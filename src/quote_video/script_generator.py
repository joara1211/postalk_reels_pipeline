"""Generates the POST /reels/scripts result: per-scene captions, an
Instagram caption, and hashtags, from store/menu info
(docs/reels-api-spec.md section 3).

The LLM provider isn't confirmed with the team yet (CLAUDE.md: "LLM 제공자
(환경변수로 교체 가능하게)"), so the actual call goes through the `LlmClient`
protocol. `generate_script()` takes one in; callers that don't pass one get
`AnthropicLlmClient`, built lazily (only when actually needed) straight
from `ANTHROPIC_API_KEY`/`LLM_MODEL` env vars — so importing or unit-testing
this module never needs an API key or network access, and swapping
providers later is a new class, not a code change here.

`target` (SC-06's "장면 대상 표시") is never asked of the LLM — it's purely
structural ("가게" for hook/outro, the menu name for closeup/detail), so
it's filled in deterministically the same way whether the LLM call
succeeds or not.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol

from . import templates
from .templates import Scene

MAX_CAPTION_CHARS = 2200
MAX_HASHTAGS = 30


class LlmClient(Protocol):
    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """Return the model's raw text reply (expected to be JSON, possibly
        wrapped in a ```json fence — _parse_and_validate() strips that)."""
        ...


class AnthropicLlmClient:
    """Minimal stdlib-only client for Anthropic's Messages API — no SDK
    dependency, since the provider itself isn't confirmed yet. Reads
    ANTHROPIC_API_KEY (required) and LLM_MODEL (optional) from the
    environment so it stays swappable without a code change."""

    DEFAULT_MODEL = "claude-sonnet-5-5"
    API_URL = "https://api.anthropic.com/v1/messages"
    API_VERSION = "2023-06-01"

    def __init__(self, *, api_key: str | None = None, model: str | None = None, timeout: float = 20.0):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.model = model or os.environ.get("LLM_MODEL", self.DEFAULT_MODEL)
        self.timeout = timeout
        if not self.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        body = json.dumps(
            {
                "model": self.model,
                "max_tokens": 1024,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_prompt}],
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            self.API_URL,
            data=body,
            method="POST",
            headers={
                "content-type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": self.API_VERSION,
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return "".join(block["text"] for block in payload["content"] if block.get("type") == "text")


@dataclass(frozen=True)
class SceneScript:
    scene_index: int
    scene_type: str
    menu_index: int | None
    target: str
    text: str
    max_chars: int


@dataclass(frozen=True)
class ScriptResult:
    scenes: list[SceneScript]
    caption: str
    hashtags: list[str]
    is_fallback: bool


_PRICE_RE = re.compile(r"[\d][\d,]*\s*원")
_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _extract_price(description: str) -> str | None:
    match = _PRICE_RE.search(description)
    return match.group(0).replace(" ", "") if match else None


def _target_for_scene(scene: Scene, menus: list[dict]) -> str:
    """SC-06 "장면 대상 표시": the literal word "가게" for hook/outro, or
    the specific menu's name for closeup/detail — never LLM-generated."""
    if scene.menu_index is None:
        return "가게"
    return menus[scene.menu_index]["name"]


def _build_prompt(store: dict, menus: list[dict], scene_plan: list[Scene]) -> tuple[str, str]:
    system_prompt = (
        "너는 소상공인 광고 숏폼 영상의 장면별 자막과 인스타그램 캡션을 쓰는 카피라이터다. "
        "친근하고 간결한 한국어로 쓴다. 반드시 JSON 객체 하나만 출력한다 — 다른 설명 문장은 절대 쓰지 않는다."
    )
    scene_lines = []
    for scene in scene_plan:
        if scene.menu_index is None:
            subject = f"가게 '{store['name']}' ({store.get('description', '')})"
        else:
            menu = menus[scene.menu_index]
            subject = f"메뉴 '{menu['name']}' ({menu.get('description', '')})"
        scene_lines.append(
            f"- scene_index={scene.scene_index}, scene_type={scene.scene_type}, "
            f"대상={subject}, 최대 글자수={scene.caption_max_chars}자"
        )
    user_prompt = (
        f"가게 이름: {store['name']}\n가게 설명: {store.get('description', '')}\n\n"
        "장면 목록:\n" + "\n".join(scene_lines) + "\n\n"
        "각 장면마다 최대 글자수를 넘지 않는 자막 문장(text)을 만들어줘. "
        "detail 장면은 메뉴 설명에 가격이 있으면 자연스럽게 포함하고, 없으면 가격 언급 없이 특징만 써줘. "
        "가게 이름에 들어있지 않은 가격 입력란은 없으니 메뉴 설명 문장에서만 가격을 찾아.\n"
        "그리고 인스타그램용 caption(2,200자 이내 한 문단)과 hashtags(30개 이내, 각각 '#'으로 시작하는 배열)도 만들어줘.\n\n"
        "아래 JSON 형식 그대로만 출력해(설명 문장이나 코드펜스 없이):\n"
        '{"scenes": [{"scene_index": 0, "text": "..."}], "caption": "...", "hashtags": ["#...", "#..."]}'
    )
    return system_prompt, user_prompt


def _parse_and_validate(raw: str, scene_plan: list[Scene], menus: list[dict]) -> ScriptResult:
    cleaned = _CODE_FENCE_RE.sub("", raw.strip())
    data = json.loads(cleaned)  # ValueError (JSONDecodeError) -> caller retries/falls back

    scenes_by_index = {entry["scene_index"]: entry for entry in data["scenes"]}
    expected_indices = [scene.scene_index for scene in scene_plan]
    if sorted(scenes_by_index) != expected_indices:
        raise ValueError(f"scene_index mismatch: expected {expected_indices}, got {sorted(scenes_by_index)}")

    scenes = []
    for scene in scene_plan:
        text = str(scenes_by_index[scene.scene_index]["text"]).strip()
        if len(text) > scene.caption_max_chars:
            raise ValueError(f"scene_index {scene.scene_index}: {len(text)} chars exceeds max {scene.caption_max_chars}")
        scenes.append(
            SceneScript(
                scene_index=scene.scene_index,
                scene_type=scene.scene_type,
                menu_index=scene.menu_index,
                target=_target_for_scene(scene, menus),
                text=text,
                max_chars=scene.caption_max_chars,
            )
        )

    caption = str(data["caption"]).strip()
    if len(caption) > MAX_CAPTION_CHARS:
        raise ValueError(f"caption {len(caption)} chars exceeds max {MAX_CAPTION_CHARS}")

    hashtags = [str(tag) for tag in data["hashtags"]]
    if len(hashtags) > MAX_HASHTAGS:
        raise ValueError(f"{len(hashtags)} hashtags exceeds max {MAX_HASHTAGS}")

    return ScriptResult(scenes=scenes, caption=caption, hashtags=hashtags, is_fallback=False)


def _fallback_result(store: dict, menus: list[dict], scene_plan: list[Scene]) -> ScriptResult:
    """Used when the LLM fails twice (bad/missing key, network error,
    invalid JSON, or output that fails validation): template default
    phrasing filled in with the store/menu names, `is_fallback: true`
    (CLAUDE.md "실패 대체")."""
    scenes = []
    for scene in scene_plan:
        target = _target_for_scene(scene, menus)
        if scene.menu_index is None:
            text = store["name"] if scene.scene_type == "hook" else "지금 바로 들러주세요"
        else:
            menu = menus[scene.menu_index]
            if scene.scene_type == "detail":
                price = _extract_price(menu.get("description", ""))
                text = f"{menu['name']} {price}" if price else menu["name"]
            else:
                text = menu["name"]
        scenes.append(
            SceneScript(
                scene_index=scene.scene_index,
                scene_type=scene.scene_type,
                menu_index=scene.menu_index,
                target=target,
                text=text[: scene.caption_max_chars],
                max_chars=scene.caption_max_chars,
            )
        )

    menu_names = [menu["name"] for menu in menus]
    caption = f"{store['name']}에서 {', '.join(menu_names)}을 만나보세요."[:MAX_CAPTION_CHARS]
    hashtags = ([f"#{store['name']}"] + [f"#{name}" for name in menu_names])[:MAX_HASHTAGS]
    return ScriptResult(scenes=scenes, caption=caption, hashtags=hashtags, is_fallback=True)


def generate_script(
    store: dict,
    menus: list[dict],
    template: dict,
    *,
    llm_client: LlmClient | None = None,
) -> ScriptResult:
    """CLAUDE.md: "LLM 실패: 1회 재시도 → 템플릿 기본 문구 + 가게명·메뉴명으로
    채우고 is_fallback: true". Any failure along the way (missing API key,
    network error, malformed JSON, a scene over its char limit, too many
    hashtags, ...) counts as one failed attempt — this is deliberately a
    broad `except Exception`, since the fallback is meant to catch all of
    those the same way, not just a specific error type."""
    scene_plan = templates.build_scene_plan(template, menu_count=len(menus))
    system_prompt, user_prompt = _build_prompt(store, menus, scene_plan)

    for _attempt in range(2):
        try:
            client = llm_client or AnthropicLlmClient()
            raw = client.complete(system_prompt, user_prompt)
            return _parse_and_validate(raw, scene_plan, menus)
        except Exception:
            continue

    return _fallback_result(store, menus, scene_plan)
