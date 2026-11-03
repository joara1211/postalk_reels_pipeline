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
from .templates import load_template, rebalance_menu_scene_durations
from .video_composer import compose_video, probe_duration


@dataclass(frozen=True)
class PipelineResult:
    output_path: Path
    duration_sec: float  # actual final MP4 length, opening/closing cards included
    thumbnail_at_sec: float = 0.3  # hook scene may not start at t=0 if an opening card ran first


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

    # Each menu's own time budget (closeup/detail/...) is split by how long
    # the user actually recorded each scene, not always evenly — hook/outro
    # stay fixed (see rebalance_menu_scene_durations()). Needs each
    # per-menu scene's source clip length up front, which means probing
    # here rather than leaving it to compose_video() as usual; that probe
    # runs again inside compose_video() for the actual duration-fit
    # filter, so this is a deliberate small duplicate cost (ffprobe is
    # fast) traded for not having to thread pre-probed durations through
    # video_composer's API.
    fps = template["render"]["canvas"]["fps"]
    probed_durations: dict[int, float] = {}
    for rs in render_scenes:
        if rs.scene.menu_index is not None:
            media_path = media_root / rs.media_key
            if media_path.exists():
                probed_durations[rs.scene.scene_index] = probe_duration(media_path, deadline=deadline)
    rebalanced_by_index = {
        scene.scene_index: scene
        for scene in rebalance_menu_scene_durations([rs.scene for rs in render_scenes], probed_durations, fps)
    }

    scene_specs: list[tuple[Path, float]] = []
    timings: list[CaptionTiming] = []
    start = 0.0
    for rs in render_scenes:
        media_path = media_root / rs.media_key
        if not media_path.exists():
            raise NoUsableMediaError(f"scene_index {rs.scene.scene_index}: no such file {media_path}")
        duration = rebalanced_by_index[rs.scene.scene_index].duration_sec
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
        outline_color=subtitle_cfg.get("outline_color", "&H00000000"),
        back_color=subtitle_cfg["back_color"],
        border_style=subtitle_cfg["border_style"],
        outline=subtitle_cfg["outline"],
        bold=subtitle_cfg.get("bold", 0),
        shadow=subtitle_cfg.get("shadow", 0),
        margin_v=subtitle_cfg["margin_v"],
        pop_in=subtitle_cfg.get("pop_in", False),
    )

    bgm_path = config.REPO_ROOT / template["audio"]["bgm"]
    transition_cfg = render_cfg["transition"]
    overlay_cfg = render_cfg.get("overlay")
    overlay_path = config.REPO_ROOT / overlay_cfg["image"] if overlay_cfg else None
    animated_overlay_cfg = render_cfg.get("animated_overlay")
    animated_overlay_path = config.REPO_ROOT / animated_overlay_cfg["video"] if animated_overlay_cfg else None
    color_grade = render_cfg.get("color_grade")

    opening_cfg = render_cfg.get("opening_card")
    opening_card_path = opening_card_duration = None
    if opening_cfg and opening_cfg.get("enabled"):
        opening_card_path = config.REPO_ROOT / opening_cfg["video"]
        opening_card_duration = opening_cfg["duration_sec"]

    closing_cfg = render_cfg.get("closing_card")
    closing_card_path = closing_card_duration = None
    if closing_cfg and closing_cfg.get("enabled"):
        closing_card_path = config.REPO_ROOT / closing_cfg["video"]
        closing_card_duration = closing_cfg["duration_sec"]

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
        overlay_path=overlay_path,
        animated_overlay_path=animated_overlay_path,
        color_grade=color_grade,
        opening_card_path=opening_card_path,
        opening_card_duration=opening_card_duration or 0.0,
        closing_card_path=closing_card_path,
        closing_card_duration=closing_card_duration or 0.0,
        font_dir=config.FONT_DIR,
        width=canvas["width"],
        height=canvas["height"],
        fps=canvas["fps"],
        preset=render_cfg.get("ffmpeg_preset", config.FFMPEG_PRESET),
        deadline=deadline,
    )
    main_duration = sum(d for _, d in scene_specs)
    full_duration = main_duration + (opening_card_duration or 0.0) + (closing_card_duration or 0.0)
    thumbnail_at_sec = (opening_card_duration or 0.0) + 0.3
    return PipelineResult(output_path=output_path, duration_sec=full_duration, thumbnail_at_sec=thumbnail_at_sec)


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
