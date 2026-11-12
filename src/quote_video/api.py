"""Background script generation and FFmpeg rendering for POSTALK."""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import replace
from hmac import compare_digest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal, Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from . import config, job_store
from .errors import NoUsableMediaError, PipelineError
from .pipeline import PipelineResult, run_pipeline
from .render_request import RenderRequest, build_render_plan
from .script_generator import generate_script
from .storage import download_clip, signed_object_key, upload_result
from .templates import build_scene_plan, load_template
from .video_composer import PipelineTimeoutError, generate_thumbnail

logger = logging.getLogger(__name__)

app = FastAPI(title="POSTALK 릴스 API")

STORAGE_ROOT = Path(os.environ.get("STORAGE_ROOT", config.REPO_ROOT / "storage"))


def require_internal_auth(x_reels_api_key: str = Header(default="")) -> None:
    key = os.environ.get("REELS_INTERNAL_API_KEY", "").strip()
    if os.environ.get("SUPABASE_URL") and not key:
        raise HTTPException(503, "Reels service authentication is not configured")
    if key and not compare_digest(x_reels_api_key.encode(), key.encode()):
        raise HTTPException(401, "Invalid reels service credentials")


router = APIRouter(prefix="/reels", dependencies=[Depends(require_internal_auth)])


# ---------------------------------------------------------------------------
# Request/response models (docs/reels-api-spec.md sections 3-4). Parsed here
# at the API boundary, then handed to the existing business logic as plain
# dicts (`.model_dump()`) — render_request.py/script_generator.py don't need
# to know about pydantic.
# ---------------------------------------------------------------------------


class StoreIn(BaseModel):
    name: str
    description: str = ""


class MenuIn(BaseModel):
    menu_index: int
    name: str
    description: str = ""


class ScriptRequestIn(BaseModel):
    user_id: int
    concept: str
    store: StoreIn
    menus: list[MenuIn]


class MediaIn(BaseModel):
    scene_index: int
    menu_index: Optional[int]
    key: str
    storage: Literal["s3", "supabase"] = "s3"
    download_url: str | None = None


class UploadTargets(BaseModel):
    video_url: str
    thumbnail_url: str


class SceneTextIn(BaseModel):
    scene_index: int
    text: str


class RenderRequestIn(BaseModel):
    user_id: int
    ad_id: str
    concept: str
    store: StoreIn
    menus: list[MenuIn]
    media: list[MediaIn]
    scenes: list[SceneTextIn] = []
    caption: str = ""
    hashtags: list[str] = []
    uploads: UploadTargets | None = None


def _load_template_or_400(concept: str) -> dict:
    if not re.fullmatch(r"[a-z0-9_-]+", concept):
        raise HTTPException(400, {"code": "INVALID_CONCEPT", "message": "Invalid concept"})
    try:
        return load_template(concept)
    except FileNotFoundError as e:
        raise HTTPException(400, {"code": "INVALID_CONCEPT", "message": str(e)}) from e


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@router.get("/templates")
def get_templates() -> dict:
    """The "render" key is internal-only (design values, not part of the
    public contract) — stripped before returning."""
    templates_out = []
    for path in sorted(config.TEMPLATE_DIR.glob("*.json")):
        template = load_template(path.stem)
        templates_out.append({k: v for k, v in template.items() if k != "render"})
    return {"templates": templates_out}


# ---------------------------------------------------------------------------
# POST /reels/scripts + GET /reels/scripts/{script_id}
# ---------------------------------------------------------------------------


def _process_script(script_id: str, store: dict, menus: list[dict], template: dict) -> None:
    try:
        result = generate_script(store, menus, template)
        job_store.store.update_script(
            script_id,
            status="done",
            is_fallback=result.is_fallback,
            scenes=[
                {
                    "scene_index": s.scene_index,
                    "scene_type": s.scene_type,
                    "menu_index": s.menu_index,
                    "target": s.target,
                    "text": s.text,
                    "max_chars": s.max_chars,
                }
                for s in result.scenes
            ],
            caption=result.caption,
            hashtags=result.hashtags,
            error=None,
        )
    except Exception as e:  # input-shape errors that slipped past the sync check, etc.
        logger.exception("Script generation crashed for script_id=%s", script_id)
        job_store.store.update_script(
            script_id, status="failed", error={"code": "SCRIPT_GENERATION_FAILED", "message": str(e)}
        )


@router.post("/scripts", status_code=202)
def post_scripts(body: ScriptRequestIn, background_tasks: BackgroundTasks) -> dict:
    template = _load_template_or_400(body.concept)
    menus = [m.model_dump() for m in body.menus]
    try:
        build_scene_plan(template, menu_count=len(menus))  # validates 1-3 menus before accepting
    except ValueError as e:
        raise HTTPException(400, {"code": "INVALID_MENU_COUNT", "message": str(e)}) from e

    job = job_store.store.create_script()
    background_tasks.add_task(_process_script, job.script_id, body.store.model_dump(), menus, template)
    return {"script_id": job.script_id, "status": job.status}


@router.get("/scripts/{script_id}")
def get_script(script_id: str) -> dict:
    job = job_store.store.get_script(script_id)
    if job is None:
        raise HTTPException(404, {"code": "NOT_FOUND", "message": f"No script {script_id}"})
    return job.to_response()


# ---------------------------------------------------------------------------
# POST /reels/renders + GET /reels/renders/{render_id}
# ---------------------------------------------------------------------------


def _run_render_with_one_retry(request: RenderRequest, media_root: Path, output_path: Path, on_stage) -> PipelineResult:
    """docs/reels-api-spec.md: "RENDER_FAILED | FFmpeg 실패(자동 재시도 1회
    후)". Only a generic ffmpeg/encode failure is retried — NoUsableMediaError
    (bad input, retrying won't fix it) and PipelineTimeoutError (already hit
    the wall-clock budget once) are not."""
    last_error: Exception | None = None
    for _attempt in range(2):
        try:
            return run_pipeline(request, media_root, output_path, on_stage=on_stage)
        except (NoUsableMediaError, PipelineTimeoutError):
            raise
        except Exception as e:
            last_error = e
            continue
    raise last_error


def _fail_render(render_id: str, code: str, message: str) -> None:
    job_store.store.update_render(render_id, status="failed", error={"code": code, "message": message})
    job_store.send_callback(job_store.store.get_render(render_id))


def _process_render(render_id: str, request: RenderRequest, uploads: UploadTargets | None) -> None:
    try:
        if uploads is None:
            _render_to_storage(render_id, request, STORAGE_ROOT)
            return

        with TemporaryDirectory(prefix="postalk-reel-") as temp, httpx.Client(
            timeout=15.0, follow_redirects=False, trust_env=False
        ) as client:
            root = Path(temp)
            media = []
            job_store.store.update_render(render_id, status="rendering", progress=job_store.stage_progress("preparing"))
            for item in request.media:
                filename = f"scene-{item['scene_index']}.mp4"
                download_clip(client, item["download_url"], root / filename)
                media.append({**item, "key": filename})
            _render_to_storage(render_id, replace(request, media=media), root, client, uploads)
    except NoUsableMediaError as error:
        _fail_render(render_id, "NO_USABLE_MEDIA", str(error))
    except PipelineTimeoutError:
        _fail_render(render_id, "TIMEOUT", "The reel exceeded the rendering time limit")
    except Exception as error:
        # Transfer errors can include signed credentials in their URL.
        logger.warning("Render %s failed (%s)", render_id, type(error).__name__)
        _fail_render(render_id, "RENDER_FAILED", "Unable to render or store the reel")


def _render_to_storage(
    render_id: str, request: RenderRequest, root: Path,
    client: httpx.Client | None = None, uploads: UploadTargets | None = None,
) -> None:
    result_key_dir = f"results/{request.user_id}/{request.ad_id}"
    output_path = root / result_key_dir / "reel.mp4"
    thumbnail_path = root / result_key_dir / "thumb.jpg"

    def on_stage(stage: str) -> None:
        # run_pipeline() calls this with "preparing" as its very first
        # step, so the queued -> rendering transition rides along with it
        # instead of needing a separate callback before run_pipeline starts.
        job_store.store.update_render(render_id, status="rendering", progress=job_store.stage_progress(stage))
        job_store.send_callback(job_store.store.get_render(render_id))

    start = time.monotonic()
    pipeline_result = _run_render_with_one_retry(request, root, output_path, on_stage)

    job_store.store.update_render(render_id, progress=job_store.stage_progress("uploading"))
    job_store.send_callback(job_store.store.get_render(render_id))

    result_files = {"video_key": f"{result_key_dir}/reel.mp4", "thumbnail_key": f"{result_key_dir}/thumb.jpg"}
    if uploads and client:
        generate_thumbnail(pipeline_result.output_path, thumbnail_path)
        result_files = {
            "video_key": signed_object_key(uploads.video_url, upload=True),
            "thumbnail_key": signed_object_key(uploads.thumbnail_url, upload=True),
            "video_bytes": upload_result(client, uploads.video_url, pipeline_result.output_path, "video/mp4"),
            "thumbnail_bytes": upload_result(client, uploads.thumbnail_url, thumbnail_path, "image/jpeg"),
        }
    else:
        try:
            generate_thumbnail(pipeline_result.output_path, thumbnail_path)
        except Exception:
            logger.exception("Thumbnail generation failed for render_id=%s (continuing without one)", render_id)

    render_ms = round((time.monotonic() - start) * 1000)
    job_store.store.update_render(
        render_id,
        status="done",
        progress=job_store.stage_progress("uploading"),
        result={
            **result_files,
            "duration_sec": pipeline_result.duration_sec,
            "caption": request.caption,
            "hashtags": request.hashtags,
            "render_ms": render_ms,
            "fallbacks": [],  # per-scene media substitution isn't implemented yet (CLAUDE.md 실패 대체)
        },
        error=None,
    )
    job_store.send_callback(job_store.store.get_render(render_id))


@router.post("/renders", status_code=202)
def post_renders(body: RenderRequestIn, background_tasks: BackgroundTasks) -> dict:
    template = _load_template_or_400(body.concept)
    request = RenderRequest.from_dict(body.model_dump())
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", request.ad_id):
        raise HTTPException(400, {"code": "INVALID_AD_ID", "message": "Invalid ad identifier"})
    try:
        build_render_plan(request, template)  # raises CAPTION_TOO_LONG/SCENE_MISMATCH synchronously
    except PipelineError as e:
        raise HTTPException(400, {"code": e.code, "message": str(e)}) from e
    except ValueError as e:
        raise HTTPException(400, {"code": "INVALID_MENU_COUNT", "message": str(e)}) from e

    try:
        if body.uploads:
            for item in body.media:
                if item.storage != "supabase" or signed_object_key(item.download_url or "") != item.key:
                    raise ValueError("Invalid signed source")
            video = signed_object_key(body.uploads.video_url, upload=True)
            thumbnail = signed_object_key(body.uploads.thumbnail_url, upload=True)
            if not video.endswith(f"/generated/{request.ad_id}.mp4") or thumbnail != video[:-4] + ".jpg":
                raise ValueError("Invalid render destination")
        elif os.environ.get("SUPABASE_URL") or any(
            "\\" in item.key or ":" in item.key or any(part in ("", ".", "..") for part in item.key.split("/"))
            for item in body.media
        ):
            raise ValueError("Invalid render storage configuration")
    except ValueError as e:
        raise HTTPException(400, {"code": "INVALID_RENDER_INPUT", "message": str(e)}) from e

    # Validation passes *before* the ad_id/active-job check so a 400 never
    # leaves an orphaned "queued" job behind with nothing to process it.
    job, created = job_store.store.create_render_if_not_active(body.ad_id, reuse_completed=body.uploads is not None)
    if not created:
        if job.status == "done":
            return {"render_id": job.render_id, "ad_id": job.ad_id, "status": job.status}
        raise HTTPException(409, {"code": "ALREADY_RENDERING", "render_id": job.render_id})

    background_tasks.add_task(_process_render, job.render_id, request, body.uploads)
    return {"render_id": job.render_id, "ad_id": job.ad_id, "status": job.status}


@router.get("/renders/{render_id}")
def get_render(render_id: str) -> dict:
    job = job_store.store.get_render(render_id)
    if job is None:
        raise HTTPException(404, {"code": "NOT_FOUND", "message": f"No render {render_id}"})
    return job.to_response()


app.include_router(router)
