"""Parses and validates a POST /reels/renders body (docs/reels-api-spec.md
section 4) against a concept template's scene plan.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from . import templates
from .errors import CaptionTooLongError, SceneMismatchError


@dataclass(frozen=True)
class RenderScene:
    scene: templates.Scene
    text: str
    media_key: str


@dataclass(frozen=True)
class RenderRequest:
    user_id: int
    ad_id: str
    concept: str
    store: dict
    menus: list[dict]
    media: list[dict]
    scenes: list[dict]
    caption: str
    hashtags: list[str]

    @classmethod
    def from_dict(cls, data: dict) -> "RenderRequest":
        return cls(
            user_id=data["user_id"],
            ad_id=data["ad_id"],
            concept=data["concept"],
            store=data.get("store", {}),
            menus=data.get("menus", []),
            media=data.get("media", []),
            scenes=data.get("scenes", []),
            caption=data.get("caption", ""),
            hashtags=data.get("hashtags", []),
        )

    @classmethod
    def from_json_file(cls, path: Path) -> "RenderRequest":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def build_render_plan(request: RenderRequest, template: dict) -> list[RenderScene]:
    """Expand the template's scene plan for this request's menu count, then
    match it up against the request's `media`/`scenes` arrays by
    scene_index. Raises SceneMismatchError if the scene_index/menu_index
    set doesn't match what the template expects for this menu count, or
    CaptionTooLongError if a scene's text exceeds its caption_max_chars."""
    menu_count = len(request.menus)
    scene_plan = templates.build_scene_plan(template, menu_count)
    expected_indices = [s.scene_index for s in scene_plan]

    media_by_index = {m["scene_index"]: m for m in request.media}
    actual_media_indices = sorted(media_by_index)
    if len(request.media) != len(expected_indices) or actual_media_indices != expected_indices:
        raise SceneMismatchError(
            f"Expected scene_index {expected_indices} for {menu_count} menu(s) "
            f"in 'media', got {actual_media_indices}"
        )

    has_captions = template.get("has_captions", False)
    text_by_index = {s["scene_index"]: s["text"] for s in request.scenes}
    if has_captions:
        actual_text_indices = sorted(text_by_index)
        if len(request.scenes) != len(expected_indices) or actual_text_indices != expected_indices:
            raise SceneMismatchError(
                f"Expected scene_index {expected_indices} for {menu_count} menu(s) "
                f"in 'scenes', got {actual_text_indices}"
            )

    render_scenes = []
    for scene in scene_plan:
        media_entry = media_by_index[scene.scene_index]
        if media_entry.get("menu_index") != scene.menu_index:
            raise SceneMismatchError(
                f"scene_index {scene.scene_index}: expected menu_index {scene.menu_index}, "
                f"got {media_entry.get('menu_index')}"
            )

        text = text_by_index[scene.scene_index] if has_captions else ""
        if has_captions and len(text) > scene.caption_max_chars:
            raise CaptionTooLongError(
                f"scene_index {scene.scene_index}: {len(text)} chars exceeds "
                f"max {scene.caption_max_chars}"
            )

        render_scenes.append(RenderScene(scene=scene, text=text, media_key=media_entry["key"]))
    return render_scenes
