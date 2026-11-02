"""Orchestrates the reels pipeline end to end:

    POST /reels/renders body -> scene plan (templates) -> validated render
    plan (render_request) -> .ass captions (subtitle_sync) -> composed MP4
    (video_composer)

No image-generation stage exists; every scene's media must already be real
footage (see docs/reels-api-spec.md section 2 for the scene rule and
section 4 for the request body this takes).
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import config
from .errors import NoUsableMediaError
from .render_request import RenderRequest, build_render_plan
from .subtitle_sync import CaptionLine, CaptionTiming, write_ass
from .templates import load_template
from .video_composer import compose_video


@dataclass(frozen=True)
class PipelineResult:
    output_path: Path
    duration_sec: float


def run_pipeline(
    request: RenderRequest | dict,
    media_root: Path,
    output_path: Path,
    *,
    template_dir: Path = config.TEMPLATE_DIR,
    build_dir: Path | None = None,
    timeout_seconds: float | None = config.PIPELINE_TIMEOUT_SECONDS,
    on_stage: Callable[[str], None] | None = None,
) -> PipelineResult:
    """Run the full pipeline for one render request.

    `timeout_seconds` is a wall-clock budget for the whole call, enforced
    via subprocess timeouts (`PipelineTimeoutError`) — pass None to
    disable. Per docs/reels-api-spec.md: hard ceiling 60s, target 30s; the
    60s is what's actually enforced here, 30s is a goal to hit well inside
    it, not a second timeout.

    `on_stage`, when given, is called with "preparing" right away and
    "composing" right before the ffmpeg compose step — api.py uses this to
    drive GET /reels/renders' progress.stage. There's no separate stage in
    between: validation (scene/caption checks) already happened
    synchronously before this function is even called (see api.py), and
    subtitle-file writing is pure Python and fast enough not to need its
    own stage."""
    if isinstance(request, dict):
        request = RenderRequest.from_dict(request)
    media_root = Path(media_root)
    output_path = Path(output_path)
    build_dir = Path(build_dir) if build_dir else output_path.parent / "_build"
    deadline = time.monotonic() + timeout_seconds if timeout_seconds else None

    if on_stage:
        on_stage("preparing")

    template = load_template(request.concept, template_dir=template_dir)
    render_scenes = build_render_plan(request, template)

    scene_specs: list[tuple[Path, float]] = []
    timings: list[CaptionTiming] = []
    start = 0.0
    for rs in render_scenes:
        media_path = media_root / rs.media_key
        if not media_path.exists():
            raise NoUsableMediaError(f"scene_index {rs.scene.scene_index}: no such file {media_path}")
        duration = rs.scene.duration_sec
        scene_specs.append((media_path, duration))
        timings.append(
            CaptionTiming(
                line=CaptionLine(sequence_number=rs.scene.scene_index, text=rs.text),
                start=start,
                end=start + duration,
            )
        )
        start += duration

    render_cfg = template["render"]
    canvas = render_cfg["canvas"]
    subtitle_cfg = render_cfg["subtitle"]

    subtitle_path = build_dir / f"{output_path.stem}.ass"
    write_ass(
        timings,
        subtitle_path,
        width=canvas["width"],
        height=canvas["height"],
        font_name=subtitle_cfg["font_name"],
        font_size=subtitle_cfg["font_size"],
        primary_color=subtitle_cfg["primary_color"],
        back_color=subtitle_cfg["back_color"],
        border_style=subtitle_cfg["border_style"],
        outline=subtitle_cfg["outline"],
        margin_v=subtitle_cfg["margin_v"],
    )

    bgm_path = config.REPO_ROOT / template["audio"]["bgm"]
    transition_cfg = render_cfg["transition"]

    if on_stage:
        on_stage("composing")

    output_path = compose_video(
        scene_specs,
        subtitle_path=subtitle_path,
        bgm_path=bgm_path,
        output_path=output_path,
        bgm_volume=template["audio"]["bgm_volume"],
        fade_duration=render_cfg["fade_sec"],
        font_name=subtitle_cfg["font_name"],
        margin_v=subtitle_cfg["margin_v"],
        transition_type=transition_cfg["type"],
        transition_duration=transition_cfg["duration_sec"],
        font_dir=config.FONT_DIR,
        width=canvas["width"],
        height=canvas["height"],
        fps=canvas["fps"],
        preset=render_cfg.get("ffmpeg_preset", config.FFMPEG_PRESET),
        deadline=deadline,
    )
    return PipelineResult(output_path=output_path, duration_sec=sum(d for _, d in scene_specs))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the reels pipeline for one render request.")
    parser.add_argument("--request", type=Path, required=True, help="POST /reels/renders JSON body.")
    parser.add_argument("--media-root", type=Path, required=True, help="Base dir media[].key is resolved against.")
    parser.add_argument("--output", type=Path, required=True, help="Output MP4 path.")
    parser.add_argument(
        "--timeout", type=float, default=config.PIPELINE_TIMEOUT_SECONDS,
        help=f"Wall-clock budget in seconds, 0 to disable (default: {config.PIPELINE_TIMEOUT_SECONDS}).",
    )
    args = parser.parse_args()

    request = RenderRequest.from_dict(json.loads(args.request.read_text(encoding="utf-8")))
    result = run_pipeline(
        request, args.media_root, args.output,
        timeout_seconds=args.timeout or None,
    )
    print(f"Wrote {result.output_path} ({result.duration_sec:.1f}s)")


if __name__ == "__main__":
    main()
