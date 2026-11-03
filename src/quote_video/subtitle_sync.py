"""Builds the timed .ass subtitle file burned into the final MP4.

The real pipeline (pipeline.py) builds `CaptionTiming`s directly from the
API-driven scene plan (templates.build_scene_plan() + the request's
per-scene text) and calls `write_ass()` — no speech recognition, no parsing
of a script file.

`parse_caption_script()`/`compute_timings()`/`build_srt()`/`generate_ass()`
below are the *old* quote-video pipeline's hand-written-markdown-script
parser (one caption line = a fixed `caption_duration` seconds, timestamps
purely from line order). They're kept only for their existing tests and
are not called by the real pipeline anymore. Two script formats are
accepted:

    ## [제1문장]                                  (older format)
    * **1컷:** some text
    * **2컷:** more text

    ## [비디오 컷 1] (00:00 ~ 00:02)                (current format)
    * **[00:00 ~ 00:01] 자막 1:** some text
    * **[00:01 ~ 00:02] 자막 2:** more text

In both, only the number immediately before the final `:**` is read (as
the caption's 1-based sequence number); any `[bracketed timestamp]` or
`자막`/`컷` label text around it is decorative and ignored.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import config

# Matches "**1컷:** text" and "**[00:00 ~ 00:01] 자막 1:** text" alike:
# `[^*]*?` lazily eats any label/bracket text so the digits captured are
# always the ones immediately before the closing `:**`.
_CAPTION_LINE_RE = re.compile(r"\*\*[^*]*?(\d+)(?:컷)?:\*\*\s*(.+?)\s*$")

# Caption text itself can carry inline emphasis (e.g. "상호명 **명품대게**를
# 꼭 방문해 보세요!") that isn't meant to reach the rendered subtitle.
_MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_MD_UNDERSCORE_BOLD_RE = re.compile(r"__(.+?)__")
_MD_BACKTICK_RE = re.compile(r"`(.+?)`")
_MD_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
_MD_LEFTOVER_RE = re.compile(r"[*_`]")


def _strip_markdown(text: str) -> str:
    """Drop markdown emphasis markup, keeping only the plain caption text."""
    text = _MD_BOLD_RE.sub(r"\1", text)
    text = _MD_UNDERSCORE_BOLD_RE.sub(r"\1", text)
    text = _MD_BACKTICK_RE.sub(r"\1", text)
    text = _MD_ITALIC_RE.sub(r"\1", text)
    text = _MD_LEFTOVER_RE.sub("", text)
    return text.strip()


@dataclass(frozen=True)
class CaptionLine:
    sequence_number: int  # 1-based caption order, e.g. "자막 N" / "N컷" — not the video cut number
    text: str


@dataclass(frozen=True)
class CaptionTiming:
    line: CaptionLine
    start: float  # seconds
    end: float  # seconds


def parse_caption_script(path: Path) -> list[CaptionLine]:
    """Extract ordered caption lines from a `**...N...:** text` markdown
    script (see module docstring for the two accepted label formats)."""
    lines: list[CaptionLine] = []
    for raw_line in Path(path).read_text(encoding="utf-8").splitlines():
        match = _CAPTION_LINE_RE.search(raw_line)
        if not match:
            continue
        sequence_number, text = match.groups()
        lines.append(CaptionLine(sequence_number=int(sequence_number), text=_strip_markdown(text)))

    if not lines:
        raise ValueError(f"No caption lines found in {path}")

    expected = list(range(1, len(lines) + 1))
    actual = [line.sequence_number for line in lines]
    if actual != expected:
        raise ValueError(
            f"Caption sequence numbers are not sequential from 1 in {path}: got {actual}"
        )
    return lines


def compute_timings(lines: list[CaptionLine], caption_duration: float = 1.0) -> list[CaptionTiming]:
    """Assign each caption line a fixed-length [start, end) window, in file
    order. This is the old markdown-script timing rule (one caption line =
    `caption_duration` seconds) — kept only for the legacy fixture-based
    tests (see module docstring); the real pipeline derives per-scene
    windows from the scene plan instead (see pipeline.py), since each
    scene's duration now varies with menu count rather than being a fixed
    2s cut."""
    return [
        CaptionTiming(line=line, start=i * caption_duration, end=(i + 1) * caption_duration)
        for i, line in enumerate(lines)
    ]


def _format_ass_timestamp(seconds: float) -> str:
    total_cs = round(seconds * 100)
    hours, remainder_cs = divmod(total_cs, 360_000)
    minutes, remainder_cs = divmod(remainder_cs, 6_000)
    secs, centis = divmod(remainder_cs, 100)
    return f"{hours:d}:{minutes:02d}:{secs:02d}.{centis:02d}"


def _escape_ass_text(text: str) -> str:
    # `{`/`}` open/close ASS override tags; a stray one in caption text
    # would otherwise be interpreted as (broken) styling markup.
    return text.replace("{", "(").replace("}", ")")


def _colorize_alternating(text: str, color_a: str, color_b: str) -> str:
    """Wrap each character of an (already brace-escaped) caption line with
    an ASS inline `\\c` override, alternating between `color_a`/`color_b`
    (each a bare `BBGGRR` hex string, no `&H`/`&` — added here) — used for
    a festive two-tone letter-by-letter look instead of one flat
    PrimaryColour for the whole line. `\\c` only overrides the color, not
    alpha, so it layers on top of the Style's own (opaque) alpha fine."""
    return "".join(f"{{\\c&H{color_a if i % 2 == 0 else color_b}&}}{ch}" for i, ch in enumerate(text))


def build_ass(
    timings: list[CaptionTiming],
    *,
    width: int = config.VIDEO_WIDTH,
    height: int = config.VIDEO_HEIGHT,
    font_name: str = "Griun Fromsol",
    font_size: int = 64,
    primary_color: str = "&H00FFFFFF",
    back_color: str = "&H80000000",
    border_style: int = 1,
    outline: int = 2,
    margin_v: int = 440,
    alternating_colors: tuple[str, str] | None = None,
) -> str:
    """Render timed captions as a minimal .ass script with an explicit
    PlayResX/PlayResY matching the real output canvas.

    Burning in a plain .srt via ffmpeg's `subtitles` filter has libass
    silently assume the legacy 384x288 default script resolution and scale
    MarginV (and other numeric style values) against *that* instead of the
    actual frame size — on a 1080x1920 canvas that inflates a MarginV of
    220 to ~1467px, pushing captions almost to the top of the frame. An
    .ass file with a correct PlayResX/PlayResY header sidesteps the
    guesswork entirely, so positions/margins apply at the intended pixel
    scale.

    All style params default to the pre-templates look (outline-only text,
    no box) purely so legacy/test callers don't need to pass them — the
    real pipeline always passes these explicitly from
    templates/<concept>.json's "render.subtitle" block (design isn't
    finalized yet, so it must stay editable there, not hardcoded here).
    `border_style=3` + a non-transparent `back_color` draws a solid caption
    box instead of an outline, per CLAUDE.md's "자막은 우선 전부 ASS로
    만든다(BorderStyle=3으로 박스형 가능)"."""
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {width}\n"
        f"PlayResY: {height}\n"
        "WrapStyle: 0\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{font_name},{font_size},{primary_color},&H000000FF,&H00000000,{back_color},"
        f"0,0,0,0,100,100,0,0,{border_style},{outline},0,2,40,40,{margin_v},1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    def _render_text(t: CaptionTiming) -> str:
        escaped = _escape_ass_text(t.line.text)
        if alternating_colors is None:
            return escaped
        return _colorize_alternating(escaped, *alternating_colors)

    events = "\n".join(
        f"Dialogue: 0,{_format_ass_timestamp(t.start)},{_format_ass_timestamp(t.end)},"
        f"Default,,0,0,0,,{_render_text(t)}"
        for t in timings
    )
    return header + events + "\n"


def generate_ass(
    caption_script_path: Path,
    output_ass_path: Path,
    *,
    caption_duration: float = 1.0,
    **kwargs,
) -> Path:
    """Legacy markdown-script path (see module docstring) — the real
    pipeline builds CaptionTiming directly from the scene plan instead
    (see pipeline.py) and calls write_ass()."""
    lines = parse_caption_script(caption_script_path)
    timings = compute_timings(lines, caption_duration=caption_duration)
    return write_ass(timings, output_ass_path, **kwargs)


def write_ass(timings: list[CaptionTiming], output_ass_path: Path, **kwargs) -> Path:
    ass_content = build_ass(timings, **kwargs)
    output_ass_path = Path(output_ass_path)
    output_ass_path.parent.mkdir(parents=True, exist_ok=True)
    output_ass_path.write_text(ass_content, encoding="utf-8")
    return output_ass_path


def _format_srt_timestamp(seconds: float) -> str:
    total_ms = round(seconds * 1000)
    hours, remainder_ms = divmod(total_ms, 3_600_000)
    minutes, remainder_ms = divmod(remainder_ms, 60_000)
    secs, millis = divmod(remainder_ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def build_srt(timings: list[CaptionTiming]) -> str:
    blocks = []
    for i, timing in enumerate(timings, start=1):
        blocks.append(
            f"{i}\n"
            f"{_format_srt_timestamp(timing.start)} --> {_format_srt_timestamp(timing.end)}\n"
            f"{timing.line.text}\n"
        )
    return "\n".join(blocks) + "\n"


def generate_srt(caption_script_path: Path, output_srt_path: Path) -> Path:
    lines = parse_caption_script(caption_script_path)
    timings = compute_timings(lines)
    srt_content = build_srt(timings)
    output_srt_path = Path(output_srt_path)
    output_srt_path.parent.mkdir(parents=True, exist_ok=True)
    output_srt_path.write_text(srt_content, encoding="utf-8")
    return output_srt_path
