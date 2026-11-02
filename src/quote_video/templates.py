"""Loads concept templates (templates/<concept>.json) and expands them into
a concrete scene plan for a given menu count.

Everything design-related — font, colors, margins, fade length, BGM track,
transition style — lives in the template's "render" key, not in Python, so
the team can retune the look without touching code (see docs/reels-api-spec.md
section 2 for the "scene_rule"/"audio" shape, which mirrors GET /reels/templates).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from . import config


@dataclass(frozen=True)
class Scene:
    scene_index: int
    scene_type: str  # "hook" | "closeup" | "detail" | "outro"
    menu_index: int | None
    duration_sec: float
    caption_max_chars: int


def load_template(concept: str, *, template_dir: Path = config.TEMPLATE_DIR) -> dict:
    path = Path(template_dir) / f"{concept}.json"
    if not path.exists():
        raise FileNotFoundError(f"No template for concept '{concept}' at {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def build_scene_plan(template: dict, menu_count: int) -> list[Scene]:
    """Expand a template's scene_rule into an ordered scene list for a
    render with `menu_count` menus (1-3, per docs/reels-api-spec.md).

    Scene order is hook -> menu 1 (per_menu scenes) -> ... -> menu n -> outro,
    and that order's 0-based position is each scene's scene_index.
    """
    scene_rule = template["scene_rule"]
    common = scene_rule["common"]
    per_menu = scene_rule["per_menu"]
    menu_block_sec_map = scene_rule["menu_block_sec"]

    block_key = str(menu_count)
    if block_key not in menu_block_sec_map:
        raise ValueError(
            f"menu_count {menu_count} has no menu_block_sec entry "
            f"(allowed: {sorted(menu_block_sec_map)})"
        )

    hook = next(s for s in common if s["position"] == "first")
    outro = next(s for s in common if s["position"] == "last")

    fps = template["render"]["canvas"]["fps"]
    menu_block_sec = menu_block_sec_map[block_key]
    per_menu_scene_count = len(per_menu) * menu_count
    total_frames = round(menu_block_sec * fps)
    if total_frames % per_menu_scene_count != 0:
        raise ValueError(
            f"menu_block_sec={menu_block_sec} ({total_frames} frames at {fps}fps) "
            f"does not divide evenly across {per_menu_scene_count} menu scenes"
        )
    frames_per_scene = total_frames // per_menu_scene_count
    scene_duration_sec = frames_per_scene / fps

    scenes: list[Scene] = []
    scene_index = 0

    scenes.append(
        Scene(
            scene_index=scene_index,
            scene_type=hook["scene_type"],
            menu_index=None,
            duration_sec=hook["duration_sec"],
            caption_max_chars=hook["caption_max_chars"],
        )
    )
    scene_index += 1

    for menu_index in range(menu_count):
        for scene_def in per_menu:
            scenes.append(
                Scene(
                    scene_index=scene_index,
                    scene_type=scene_def["scene_type"],
                    menu_index=menu_index,
                    duration_sec=scene_duration_sec,
                    caption_max_chars=scene_def["caption_max_chars"],
                )
            )
            scene_index += 1

    scenes.append(
        Scene(
            scene_index=scene_index,
            scene_type=outro["scene_type"],
            menu_index=None,
            duration_sec=outro["duration_sec"],
            caption_max_chars=outro["caption_max_chars"],
        )
    )
    return scenes
