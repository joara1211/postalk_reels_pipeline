"""Dev-time generator for the christmas concept's decorative assets.

Not part of the request-time pipeline — run this by hand whenever the
christmas look needs retuning, the same way assets/bgm/*.mp3 or
assets/font/*.ttf are checked-in static design files, not generated per
render (CLAUDE.md: "생성형 영상 AI는 쓰지 않는다. 템플릿 + FFmpeg만" — this
script draws everything itself with Pillow/ffmpeg, no generative AI).

Produces:
  assets/overlay/christmas_scene.mkv       -- looping alpha overlay: pine
                                               garland + swaying ornaments +
                                               twinkling lights + bokeh +
                                               3-layer falling snow + a
                                               static snow mound at the
                                               bottom. Pointed to by
                                               templates/christmas.json's
                                               render.animated_overlay.video.
  assets/overlay/christmas_opening_card.mp4 / christmas_closing_card.mp4
                                            -- 1s opaque title cards.
                                               render.opening_card/closing_card.

Usage: python scripts/generate_christmas_assets.py
"""

from __future__ import annotations

import math
import random
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

REPO_ROOT = Path(__file__).resolve().parents[1]
OVERLAY_DIR = REPO_ROOT / "assets" / "overlay"
FONT_PATH = REPO_ROOT / "assets" / "font" / "Griun_Fromsol-Rg.ttf"

WIDTH = 1080
HEIGHT = 1920
FPS = 30
LOOP_SECONDS = 3.0
LOOP_FRAMES = round(LOOP_SECONDS * FPS)

# Four-color palette (CLAUDE.md 2026-11-03 design decision) -- every shape
# drawn in this file uses only these colors (plus their own alpha).
RED = (0xB3, 0x00, 0x1B)
GREEN = (0x0F, 0x4D, 0x2E)
GOLD = (0xF2, 0xC1, 0x4E)
CREAM = (0xFF, 0xF6, 0xE5)

SNOW_SEED = 20261103


def _rgba(color: tuple[int, int, int], alpha: int) -> tuple[int, int, int, int]:
    return (*color, alpha)


# ---------------------------------------------------------------------------
# Static decor (time-independent; drawn once per frame at a fixed position)
# ---------------------------------------------------------------------------

def draw_pine_garland(canvas: Image.Image) -> None:
    """Full-width pine garland hugging the top edge: a dark-green scalloped
    band with lighter frond triangles layered on top for texture, plus a
    thin gold rim light along its lower edge."""
    draw = ImageDraw.Draw(canvas, "RGBA")
    band_h = 150
    scallop_r = 46
    # base band, scalloped bottom edge (a row of overlapping circles reads
    # as a soft pine-needle silhouette at the small on-screen size this
    # overlay ends up at)
    draw.rectangle([0, 0, WIDTH, band_h - scallop_r], fill=_rgba(GREEN, 235))
    cx = -scallop_r
    while cx < WIDTH + scallop_r:
        draw.ellipse(
            [cx - scallop_r, band_h - 2 * scallop_r, cx + scallop_r, band_h],
            fill=_rgba(GREEN, 235),
        )
        cx += scallop_r * 1.1
    # lighter frond clusters for texture (small triangles pointing down)
    rng = random.Random(7)
    x = 10
    while x < WIDTH - 10:
        frond_h = rng.randint(34, 58)
        frond_w = rng.randint(14, 22)
        top_y = rng.randint(-6, 10)
        draw.polygon(
            [(x, top_y), (x - frond_w, top_y + frond_h), (x + frond_w, top_y + frond_h)],
            fill=_rgba((GREEN[0] + 20, GREEN[1] + 35, GREEN[2] + 20), 200),
        )
        x += rng.randint(26, 40)
    # gold rim light along the scalloped edge
    draw.line([(0, band_h - 4), (WIDTH, band_h - 4)], fill=_rgba(GOLD, 70), width=3)


def draw_snow_mound(canvas: Image.Image) -> None:
    """Static cream snow-drift silhouette along the bottom edge."""
    draw = ImageDraw.Draw(canvas, "RGBA")
    base_y = HEIGHT - 70
    amplitude = 34
    points = [(0, HEIGHT)]
    step = 36
    x = 0
    while x <= WIDTH:
        y = base_y - amplitude * (0.5 + 0.5 * math.sin(x / 140.0 + 0.6))
        points.append((x, y))
        x += step
    points.append((WIDTH, HEIGHT))
    mound_layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(mound_layer).polygon(points, fill=_rgba(CREAM, 210))
    mound_layer = mound_layer.filter(ImageFilter.GaussianBlur(3))
    canvas.alpha_composite(mound_layer)


# ---------------------------------------------------------------------------
# Animated decor (function of t, seconds into the LOOP_SECONDS loop)
# ---------------------------------------------------------------------------

_LIGHT_COUNT = 12
_LIGHT_COLORS = [GOLD, CREAM, RED]
_LIGHT_ANCHORS = [0, WIDTH / 3, 2 * WIDTH / 3, WIDTH]
_LIGHT_BASE_Y = 150
_LIGHT_SAG = 26


def _light_position(i: int) -> tuple[float, float]:
    frac = (i + 0.5) / _LIGHT_COUNT
    x = frac * WIDTH
    arc_width = WIDTH / 3
    local = (x % arc_width) / arc_width  # 0..1 within this arc segment
    y = _LIGHT_BASE_Y + _LIGHT_SAG * math.sin(math.pi * local)
    return x, y


def draw_lights(canvas: Image.Image, t: float) -> None:
    glow_layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow_layer, "RGBA")
    core_layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    core_draw = ImageDraw.Draw(core_layer, "RGBA")
    for i in range(_LIGHT_COUNT):
        x, y = _light_position(i)
        phase = i * (2 * math.pi / _LIGHT_COUNT) * 3  # stagger flicker across bulbs
        brightness = 0.55 + 0.45 * math.sin(2 * math.pi * t / LOOP_SECONDS + phase)
        color = _LIGHT_COLORS[i % len(_LIGHT_COLORS)]
        glow_alpha = int(max(0.0, brightness) * 140)
        core_alpha = int((0.5 + 0.5 * max(0.0, brightness)) * 255)
        glow_draw.ellipse([x - 16, y - 16, x + 16, y + 16], fill=_rgba(color, glow_alpha))
        core_draw.ellipse([x - 4, y - 4, x + 4, y + 4], fill=_rgba(CREAM, core_alpha))
    glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(8))
    canvas.alpha_composite(glow_layer)
    canvas.alpha_composite(core_layer)


_BOKEH_COUNT = 6


def draw_bokeh(canvas: Image.Image, t: float) -> None:
    rng = random.Random(42)
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer, "RGBA")
    for i in range(_BOKEH_COUNT):
        side_left = i % 2 == 0
        base_x = rng.uniform(40, 160) if side_left else WIDTH - rng.uniform(40, 160)
        base_y = rng.uniform(200, HEIGHT - 200)
        radius = rng.uniform(55, 95)
        alpha = rng.randint(40, 70)
        phase = rng.uniform(0, 2 * math.pi)
        drift = 40 * math.sin(2 * math.pi * t / LOOP_SECONDS + phase)
        x = base_x + drift
        y = base_y + 25 * math.sin(2 * math.pi * t / LOOP_SECONDS * 0.6 + phase)
        draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=_rgba(GOLD, alpha))
    layer = layer.filter(ImageFilter.GaussianBlur(24))
    canvas.alpha_composite(layer)


_ORNAMENT_SPECS = [
    {"x": WIDTH * 0.22, "color": RED, "phase": 0.0},
    {"x": WIDTH * 0.50, "color": GOLD, "phase": math.pi * 2 / 3},
    {"x": WIDTH * 0.78, "color": RED, "phase": math.pi * 4 / 3},
]
_ORNAMENT_STRING_LEN = 46
_ORNAMENT_RADIUS = 22
_ORNAMENT_SWING_AMPLITUDE = 10.0


def draw_ornaments(canvas: Image.Image, t: float) -> None:
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer, "RGBA")
    for spec in _ORNAMENT_SPECS:
        attach_x = spec["x"]
        attach_y = 118.0
        swing = _ORNAMENT_SWING_AMPLITUDE * math.sin(2 * math.pi * t / LOOP_SECONDS + spec["phase"])
        bx = attach_x + swing
        by = attach_y + _ORNAMENT_STRING_LEN
        draw.line([(attach_x, attach_y), (bx, by)], fill=_rgba(CREAM, 180), width=2)
        r = _ORNAMENT_RADIUS
        draw.ellipse([bx - r, by - r, bx + r, by + r], fill=_rgba(spec["color"], 255))
        draw.ellipse([bx - r * 0.4, by - r * 0.5, bx + r * 0.1, by - r * 0.1], fill=_rgba(CREAM, 120))
        draw.rectangle([bx - 4, by - r - 6, bx + 4, by - r + 2], fill=_rgba(GOLD, 255))
    canvas.alpha_composite(layer)


class _SnowLayer:
    def __init__(self, count: int, size_range: tuple[float, float], blur: float, alpha_range: tuple[int, int], seed: int):
        rng = random.Random(seed)
        self.blur = blur
        self.flakes = []
        for _ in range(count):
            self.flakes.append(
                {
                    "x0": rng.uniform(0, WIDTH),
                    "y0": rng.uniform(0, HEIGHT),
                    "size": rng.uniform(*size_range),
                    "alpha": rng.randint(*alpha_range),
                    "phase": rng.uniform(0, 2 * math.pi),
                    "sway_amp": rng.uniform(8, 26),
                    "fall_cycles": rng.choice([1, 2, 3]),
                    "sway_cycles": rng.choice([1, 2]),
                }
            )

    def draw(self, canvas: Image.Image, t: float) -> None:
        layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer, "RGBA")
        frac = t / LOOP_SECONDS
        for f in self.flakes:
            y = (f["y0"] + f["fall_cycles"] * HEIGHT * frac) % HEIGHT
            x = f["x0"] + f["sway_amp"] * math.sin(2 * math.pi * f["sway_cycles"] * frac + f["phase"])
            r = f["size"]
            draw.ellipse([x - r, y - r, x + r, y + r], fill=_rgba(CREAM, f["alpha"]))
        if self.blur:
            layer = layer.filter(ImageFilter.GaussianBlur(self.blur))
        canvas.alpha_composite(layer)


_SNOW_FAR = _SnowLayer(80, (1.5, 2.5), blur=0.4, alpha_range=(60, 110), seed=SNOW_SEED + 1)
_SNOW_MID = _SnowLayer(40, (3.0, 4.5), blur=0.0, alpha_range=(110, 170), seed=SNOW_SEED + 2)
_SNOW_NEAR = _SnowLayer(12, (6.0, 9.0), blur=1.6, alpha_range=(150, 210), seed=SNOW_SEED + 3)


def compose_frame(t: float) -> Image.Image:
    canvas = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    _SNOW_FAR.draw(canvas, t)
    draw_bokeh(canvas, t)
    draw_pine_garland(canvas)
    draw_snow_mound(canvas)
    draw_ornaments(canvas, t)
    draw_lights(canvas, t)
    _SNOW_MID.draw(canvas, t)
    _SNOW_NEAR.draw(canvas, t)
    return canvas


def _encode_lossless_alpha(frame_paths: list[Path], out_path: Path, *, fps: int) -> None:
    """FFV1/MKV instead of qtrle/MOV -- both are lossless and preserve
    alpha correctly (checked directly: composited a test frame over a
    solid color and confirmed transparent/opaque regions survive), but
    qtrle's byte-level RLE compresses this busy a frame (full-width
    garland, blurred bokeh/light glow, dense snow) far worse than FFV1's
    real entropy coding -- ~103MB vs ~20MB for the same content, and only
    the latter is reasonable to check into git. (VP9/WebM alpha was tried
    earlier for a different overlay and found unreliable on decode --
    deliberately not retried here; FFV1 is a different, still-lossless,
    codec, not that same risk.)"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pattern = str(frame_paths[0].parent / "frame_%04d.png")
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-framerate", str(fps),
            "-i", pattern,
            "-c:v", "ffv1",
            "-pix_fmt", "rgba",
            str(out_path),
        ],
        check=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def generate_scene_overlay(out_path: Path = OVERLAY_DIR / "christmas_scene.mkv") -> Path:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        frame_paths = []
        for i in range(LOOP_FRAMES):
            t = i / FPS
            frame = compose_frame(t)
            p = tmp_dir / f"frame_{i:04d}.png"
            frame.save(p)
            frame_paths.append(p)
        _encode_lossless_alpha(frame_paths, out_path, fps=FPS)
    return out_path


def _radial_green_background() -> Image.Image:
    """Smooth (not banded) radial lighten toward the center, computed
    per-pixel instead of as stepped concentric ellipses -- 8px ring steps
    read as a visible hard-edged band on a tall 1080x1920 canvas since the
    rings are nearly flat near the top/bottom where this gradient matters
    most for the title text's backdrop."""
    import numpy as np

    cx, cy = WIDTH / 2, HEIGHT / 2
    yy, xx = np.mgrid[0:HEIGHT, 0:WIDTH]
    dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    max_r = math.hypot(cx, cy)
    shade = np.clip(1 - dist / max_r, 0, 1)
    base = np.array(GREEN, dtype=np.float64)
    lighter = base + np.array([25, 30, 20])
    rgb = base[None, None, :] + (lighter - base)[None, None, :] * shade[..., None]
    # dither: h264 visibly bands an otherwise perfectly smooth gradient at
    # these low bitrates; a little noise breaks the banding up
    rng = np.random.default_rng(1)
    rgb = rgb + rng.uniform(-3, 3, size=rgb.shape[:2])[..., None]
    return Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8), mode="RGB")


def generate_title_card(text: str, duration_sec: float, out_path: Path) -> Path:
    frame_count = round(duration_sec * FPS)
    font = ImageFont.truetype(str(FONT_PATH), 96)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        frame_paths = []
        for i in range(frame_count):
            t = i / FPS
            bg = _radial_green_background().convert("RGBA")
            _SNOW_FAR.draw(bg, t)
            draw_bokeh(bg, t)
            _SNOW_MID.draw(bg, t)
            draw = ImageDraw.Draw(bg, "RGBA")
            bbox = draw.textbbox((0, 0), text, font=font, stroke_width=6)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            tx, ty = (WIDTH - tw) / 2 - bbox[0], (HEIGHT - th) / 2 - bbox[1]
            draw.text((tx, ty), text, font=font, fill=_rgba(GOLD, 255), stroke_width=6, stroke_fill=_rgba(RED, 255))
            p = tmp_dir / f"frame_{i:04d}.png"
            bg.convert("RGB").save(p)
            frame_paths.append(p)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        pattern = str(tmp_dir / "frame_%04d.png")
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-framerate", str(FPS),
                "-i", pattern,
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                str(out_path),
            ],
            check=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
    return out_path


def main() -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH")
    scene_path = generate_scene_overlay()
    print(f"wrote {scene_path}")
    opening_path = generate_title_card("Merry Christmas", 1.0, OVERLAY_DIR / "christmas_opening_card.mp4")
    print(f"wrote {opening_path}")
    closing_path = generate_title_card("Merry Christmas", 1.0, OVERLAY_DIR / "christmas_closing_card.mp4")
    print(f"wrote {closing_path}")


if __name__ == "__main__":
    main()
