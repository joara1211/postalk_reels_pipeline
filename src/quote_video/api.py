"""HTTP wrapper around run_pipeline() for the web app to call over the
network, since the pipeline itself needs ffmpeg + a real filesystem
(Python/asset files) that the web app's serverless/edge runtime doesn't
have.

The web app is the only thing that talks to its own Storage: it uploads
source clips/BGM and gets URLs for them, calls this service with those
URLs plus the caption script text, and gets the finished MP4 bytes back
in the response to upload itself. This service holds no storage
credentials and is otherwise stateless.

Deploy as a container (see Dockerfile) behind an API key — see
QUOTE_VIDEO_API_KEY below.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import httpx
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, model_validator

from . import config
from .pipeline import run_pipeline
from .video_composer import PipelineTimeoutError

app = FastAPI(title="quote-video pipeline API")

# Set in the deployment environment. If unset, the API is unauthenticated —
# fine for local testing, never for a public deployment.
_API_KEY = os.environ.get("QUOTE_VIDEO_API_KEY")

_DOWNLOAD_TIMEOUT_SECONDS = 30.0


class GenerateRequest(BaseModel):
    # Exactly one of these two: a single pre-edited source video, or an
    # ordered list of raw per-cut clips (mirrors pipeline.py's --video
    # file-vs-directory distinction).
    video_url: str | None = None
    video_urls: list[str] | None = None

    caption_markdown: str
    bgm_url: str

    # Wall-clock budget for the ffmpeg stage only (see config.PIPELINE_TIMEOUT_SECONDS);
    # does not cover the time spent downloading inputs below.
    timeout_seconds: float | None = config.PIPELINE_TIMEOUT_SECONDS

    @model_validator(mode="after")
    def _check_video_source(self) -> "GenerateRequest":
        if bool(self.video_url) == bool(self.video_urls):
            raise ValueError("Provide exactly one of video_url or video_urls.")
        return self


def _check_api_key(x_api_key: str | None) -> None:
    if _API_KEY and x_api_key != _API_KEY:
        raise HTTPException(status_code=401, detail="invalid or missing API key")


async def _download(client: httpx.AsyncClient, url: str, dest: Path) -> None:
    try:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with open(dest, "wb") as f:
                async for chunk in resp.aiter_bytes():
                    f.write(chunk)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"failed to download {url}: {exc}") from exc


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.post("/generate")
async def generate(req: GenerateRequest, x_api_key: str | None = Header(default=None)) -> Response:
    _check_api_key(x_api_key)

    with tempfile.TemporaryDirectory(prefix="quote_video_") as tmp:
        tmp_path = Path(tmp)
        caption_path = tmp_path / "caption.md"
        caption_path.write_text(req.caption_markdown, encoding="utf-8")
        bgm_path = tmp_path / "bgm.mp3"
        output_path = tmp_path / "output.mp4"

        async with httpx.AsyncClient(timeout=_DOWNLOAD_TIMEOUT_SECONDS) as client:
            await _download(client, req.bgm_url, bgm_path)

            if req.video_url:
                video_path = tmp_path / "source.mp4"
                await _download(client, req.video_url, video_path)
            else:
                video_dir = tmp_path / "clips"
                video_dir.mkdir()
                for i, url in enumerate(req.video_urls, start=1):
                    await _download(client, url, video_dir / f"{i}.mp4")
                video_path = video_dir

        try:
            run_pipeline(
                video_path,
                caption_path,
                bgm_path,
                output_path,
                build_dir=tmp_path / "_build",
                timeout_seconds=req.timeout_seconds,
            )
        except PipelineTimeoutError as exc:
            raise HTTPException(status_code=504, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        video_bytes = output_path.read_bytes()

    return Response(content=video_bytes, media_type="video/mp4")
