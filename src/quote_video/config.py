"""Shared configuration for the POSTALK reels pipeline.

Pipeline: POST /reels/renders body (store+menus+media+per-scene captions)
-> scene plan expanded from templates/<concept>.json by menu count ->
each scene's clip fit to its target duration and cropped to 9:16 -> single
ffmpeg pass (burn ASS captions, mix BGM, fade in/out) -> final MP4.

Only infra constants live here (paths, canvas, encode speed, timeout).
Everything design-related (font, colors, margins, fade length, BGM
track/volume, transition) lives in templates/<concept>.json's "render" key
instead — design isn't finalized yet (CLAUDE.md), so the team needs to be
able to retune it without a code change/redeploy. See templates.py.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

ASSETS_DIR = REPO_ROOT / "assets"
FONT_DIR = ASSETS_DIR / "font"
BGM_DIR = ASSETS_DIR / "bgm"
VIDEO_DIR = ASSETS_DIR / "videos"
CAPTION_DIR = ASSETS_DIR / "caption"  # legacy markdown caption scripts, test-only (see subtitle_sync.py)

TEMPLATE_DIR = REPO_ROOT / "templates"

# Video spec (docs/reels-api-spec.md: "결과물은 1080x1920 MP4"). Portrait,
# matching the real source clips in assets/videos/ — confirm with the user
# before reverting to landscape.
VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
FPS = 30

# libx264 encoding speed/quality tradeoff. "medium" (libx264's own default)
# is noticeably slower than "veryfast" for barely-perceptible quality gain
# at these short reels durations/bitrates — needed headroom to keep the
# whole pipeline under PIPELINE_TIMEOUT_SECONDS.
FFMPEG_PRESET = "veryfast"

# Wall-clock budget (seconds) for one run_pipeline() call — every
# ffprobe/ffmpeg call shares this one deadline. Enforced as a hard
# subprocess timeout, not a performance promise: encode time still scales
# with total output duration, so this can't guarantee every possible input
# finishes in time — what it guarantees is that the pipeline fails fast
# with a clear PipelineTimeoutError instead of silently running past the
# deadline. docs/reels-api-spec.md: hard ceiling 60s (error code TIMEOUT),
# target 30s (a goal to land well inside this, not a second timeout).
PIPELINE_TIMEOUT_SECONDS = 60.0
