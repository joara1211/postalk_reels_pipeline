"""In-memory job state for POST /reels/scripts and POST /reels/renders,
plus the status callback to Next.js.

Everything here is a stand-in for pieces the team hasn't confirmed yet
(CLAUDE.md "아직 팀과 확정 안 된 것"): a real job store would be Supabase/a
queue instead of a process-local dict (lost on restart, not shared across
workers), and the callback's base URL/auth header are assumed, not
confirmed. Kept in one module, behind a small interface, so swapping
either out later doesn't touch api.py's route handlers.
"""

from __future__ import annotations

import logging
import os
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

STAGES = ["preparing", "composing", "uploading"]
STAGE_LABELS = {
    "preparing": "영상을 다듬고 있어요",
    "composing": "장면과 음악을 합치고 있어요",
    "uploading": "거의 다 됐어요",
}


def stage_progress(stage: str) -> dict:
    """docs/reels-api-spec.md section 5 "진행 단계" table. No "narrating"
    stage — TTS was dropped (2026-11-02), so stage_total is always 3."""
    index = STAGES.index(stage) + 1
    total = len(STAGES)
    return {
        "stage": stage,
        "stage_index": index,
        "stage_total": total,
        "label": STAGE_LABELS[stage],
        "percent": round(index / total * 100),
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:6]}"


@dataclass
class ScriptJob:
    script_id: str
    status: str = "pending"  # pending | done | failed
    is_fallback: bool | None = None
    scenes: list[dict] | None = None
    caption: str | None = None
    hashtags: list[str] | None = None
    error: dict | None = None

    def to_response(self) -> dict:
        return {
            "script_id": self.script_id,
            "status": self.status,
            "is_fallback": self.is_fallback,
            "scenes": self.scenes,
            "caption": self.caption,
            "hashtags": self.hashtags,
            "error": self.error,
        }


@dataclass
class RenderJob:
    render_id: str
    ad_id: str
    status: str = "queued"  # queued | rendering | done | failed
    progress: dict | None = None
    result: dict | None = None
    error: dict | None = None
    updated_at: str = field(default_factory=_now_iso)

    def to_response(self) -> dict:
        return {
            "render_id": self.render_id,
            "ad_id": self.ad_id,
            "status": self.status,
            "progress": self.progress,
            "result": self.result,
            "error": self.error,
            "updated_at": self.updated_at,
        }


class JobStore:
    """Thread-safe: FastAPI's BackgroundTasks run sync functions in a
    thread pool, so a render/script job progresses on a different thread
    than the request handlers that read it back."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._scripts: dict[str, ScriptJob] = {}
        self._renders: dict[str, RenderJob] = {}

    def create_script(self) -> ScriptJob:
        with self._lock:
            job = ScriptJob(script_id=_new_id("rs"))
            self._scripts[job.script_id] = job
            return job

    def get_script(self, script_id: str) -> ScriptJob | None:
        with self._lock:
            return self._scripts.get(script_id)

    def update_script(self, script_id: str, **fields) -> None:
        with self._lock:
            job = self._scripts[script_id]
            for key, value in fields.items():
                setattr(job, key, value)

    def create_render_if_not_active(self, ad_id: str) -> tuple[RenderJob, bool]:
        """Atomic check-and-create: if an active (queued/rendering) job
        already exists for `ad_id`, returns it with `created=False`
        instead of making a new one. Doing the "is one already running"
        check and the create as a single locked operation avoids a race
        between two concurrent POSTs for the same ad_id each seeing no
        active job and both creating one.

        docs/reels-api-spec.md: "같은 ad_id가 이미 queued나 rendering이면 409
        ALREADY_RENDERING과 기존 render_id를 돌려준다"."""
        with self._lock:
            for job in self._renders.values():
                if job.ad_id == ad_id and job.status in ("queued", "rendering"):
                    return job, False
            job = RenderJob(render_id=_new_id("rr"), ad_id=ad_id)
            self._renders[job.render_id] = job
            return job, True

    def get_render(self, render_id: str) -> RenderJob | None:
        with self._lock:
            return self._renders.get(render_id)

    def update_render(self, render_id: str, **fields) -> None:
        with self._lock:
            job = self._renders[render_id]
            for key, value in fields.items():
                setattr(job, key, value)
            job.updated_at = _now_iso()


store = JobStore()


def send_callback(render_job: RenderJob) -> None:
    """POST {NEXT_BASE_URL}/api/internal/reels/callback with the render's
    current status (docs/reels-api-spec.md section 5: "미디어 서버는 status나
    stage가 바뀔 때마다 ... 전달한다").

    NEXT_BASE_URL and the auth header are both unconfirmed with the team
    (CLAUDE.md) — header name assumed as X-Internal-Api-Key, read from
    REELS_CALLBACK_API_KEY. If NEXT_BASE_URL isn't set, the callback is
    just skipped (so this runs fine in local dev with no Next.js around).
    A failed callback is logged, never raised — the spec itself says
    Next.js falls back to polling GET /reels/renders/{render_id} when the
    callback doesn't arrive, so a delivery failure here isn't fatal to the
    render."""
    base_url = os.environ.get("NEXT_BASE_URL")
    if not base_url:
        logger.info("NEXT_BASE_URL not set; skipping callback for %s", render_job.render_id)
        return

    body = render_job.to_response()
    body["sent_at"] = _now_iso()
    headers = {}
    api_key = os.environ.get("REELS_CALLBACK_API_KEY")
    if api_key:
        headers["X-Internal-Api-Key"] = api_key

    try:
        httpx.post(f"{base_url}/api/internal/reels/callback", json=body, headers=headers, timeout=5.0)
    except httpx.HTTPError:
        logger.warning("Callback failed for render_id=%s", render_job.render_id, exc_info=True)
