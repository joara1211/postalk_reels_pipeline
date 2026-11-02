"""Loads concept templates (templates/<concept>.json) and expands them into
a concrete scene plan for a given menu count.

Everything design-related — font, colors, margins, fade length, BGM track,
transition style — lives in the template's "render" key, not in Python, so
the team can retune the look without touching code (see docs/reels-api-spec.md
section 2 for the "scene_rule"/"audio" shape, which mirrors GET /reels/templates).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
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


def rebalance_menu_scene_durations(
    scenes: list[Scene],
    probed_durations: dict[int, float],
    fps: int,
    *,
    min_ratio: float = 0.5,
    max_ratio: float = 2.0,
) -> list[Scene]:
    """Redistribute each menu's own time budget among its per_menu scenes
    (closeup/detail/...) by how long the user actually recorded each one,
    instead of always splitting it equally — a scene filmed long gets more
    screen time, one filmed short gets less (2026-11-02 decision).

    hook/outro scenes (`menu_index is None`) are left untouched: CLAUDE.md
    fixes those at exactly 2s each as a separate rule from the per-menu
    split, not something this flexes.

    Each menu's own total (the sum of its own scenes' durations, from
    `build_scene_plan()`'s equal split) is preserved exactly — this only
    moves time *between* a menu's own scenes, never changes a menu's share
    or the overall 13/14/15s total. Every scene is clamped to
    `[min_ratio, max_ratio]` of its equal-split baseline so one scene can't
    swallow its menu's whole budget; any remainder left by clamping/
    rounding is handed to whichever scene in that menu still has the most
    headroom (not a perfectly optimal distribution, but exact and good
    enough for the 2-3 scenes a menu actually has).

    `probed_durations` maps `scene_index -> source clip length (seconds)`;
    a scene missing from it (media not probed/found yet) falls back to the
    equal-split baseline, i.e. behaves exactly like before this function
    existed."""
    scenes_by_menu: dict[int, list[Scene]] = {}
    for scene in scenes:
        if scene.menu_index is not None:
            scenes_by_menu.setdefault(scene.menu_index, []).append(scene)

    new_duration_by_index: dict[int, float] = {}
    for menu_scenes in scenes_by_menu.values():
        n = len(menu_scenes)
        sub_budget_frames = round(sum(s.duration_sec for s in menu_scenes) * fps)
        equal_frames = sub_budget_frames / n
        min_frames = max(1, round(equal_frames * min_ratio))
        max_frames = max(min_frames, int(equal_frames * max_ratio))

        weights = [max(probed_durations.get(s.scene_index, equal_frames / fps), 0.01) for s in menu_scenes]
        total_weight = sum(weights)
        allocated = [
            min(max(round(sub_budget_frames * w / total_weight), min_frames), max_frames) for w in weights
        ]

        diff = sub_budget_frames - sum(allocated)
        while diff != 0:
            if diff > 0:
                i = max(range(n), key=lambda i: max_frames - allocated[i])
                if allocated[i] >= max_frames:
                    break
                allocated[i] += 1
                diff -= 1
            else:
                i = max(range(n), key=lambda i: allocated[i] - min_frames)
                if allocated[i] <= min_frames:
                    break
                allocated[i] -= 1
                diff += 1

        for scene, frames in zip(menu_scenes, allocated):
            new_duration_by_index[scene.scene_index] = frames / fps

    return [
        replace(scene, duration_sec=new_duration_by_index[scene.scene_index])
        if scene.scene_index in new_duration_by_index
        else scene
        for scene in scenes
    ]
