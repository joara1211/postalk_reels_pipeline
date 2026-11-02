"""Composes the final MP4 in a single ffmpeg pass: each scene's source clip
is fit to its target duration (middle-trim if long enough, slow-down if
short, slow-down + repeat for very short clips), cropped to fill the 9:16
canvas, concatenated, burned with ASS captions, faded in/out, and mixed
with BGM. Per-scene audio is never used — on-site sound is removed
entirely (CLAUDE.md: "촬영 원본 소리는 모두 제거한다").
"""

from __future__ import annotations

import math
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from . import config

_MEAN_VOLUME_RE = re.compile(r"mean_volume:\s*(-?[\d.]+)\s*dB")


class PipelineTimeoutError(TimeoutError):
    """Raised when a step would run past the pipeline's wall-clock budget.

    This does not make ffmpeg itself faster — it converts "ran silently
    past the deadline" into a clear, fast failure, since encode time
    scales with input (more/longer cuts = more work) and can't be bounded
    for arbitrary input in the general case."""


def _remaining_seconds(deadline: float | None) -> float | None:
    """Seconds left before `deadline` (a time.monotonic() timestamp), or
    None if there's no deadline. Raises immediately if already expired,
    so a doomed subprocess is never even started."""
    if deadline is None:
        return None
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise PipelineTimeoutError(
            f"Exceeded the {config.PIPELINE_TIMEOUT_SECONDS}s pipeline budget before this step could run."
        )
    return remaining


def _run(command: list[str], *, deadline: float | None = None, **kwargs) -> subprocess.CompletedProcess:
    """subprocess.run wrapper that turns a timeout into PipelineTimeoutError
    with the offending command attached, instead of a bare TimeoutExpired."""
    try:
        return subprocess.run(command, timeout=_remaining_seconds(deadline), **kwargs)
    except subprocess.TimeoutExpired as e:
        raise PipelineTimeoutError(
            f"Exceeded the {config.PIPELINE_TIMEOUT_SECONDS}s pipeline budget running: {' '.join(command[:4])} ..."
        ) from e


def _escape_filter_path(path: Path) -> str:
    """Escape a filesystem path for use inside an ffmpeg filtergraph value.

    ffmpeg's filter parser treats ':' as an option separator, which collides
    with Windows drive letters (C:\\...), so it must be backslash-escaped.
    """
    posix_path = str(Path(path).resolve()).replace("\\", "/")
    return posix_path.replace(":", "\\:")


def probe_duration(video_path: Path, *, deadline: float | None = None) -> float:
    result = _run(
        [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "csv=p=0",
            str(video_path),
        ],
        deadline=deadline,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return float(result.stdout.strip())


def probe_mean_volume_db(audio_path: Path, *, deadline: float | None = None) -> float:
    """Mean loudness (dBFS) of a media file's audio track, via ffmpeg's
    volumedetect filter."""
    result = _run(
        ["ffmpeg", "-i", str(audio_path), "-af", "volumedetect", "-f", "null", "-"],
        deadline=deadline,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    match = _MEAN_VOLUME_RE.search(result.stderr)
    if not match:
        raise RuntimeError(f"Could not determine mean_volume for {audio_path}")
    return float(match.group(1))


def compute_bgm_gain_db(orig_mean_db: float, bgm_mean_db: float, bgm_volume: float) -> float:
    """Gain (dB) to apply to a raw audio track so its measured loudness sits
    at `bgm_volume` (linear ratio, e.g. 0.2 = 20%) relative to another
    track's measured loudness (`orig_mean_db`) — not relative to its own
    raw level, which is what a flat `volume=bgm_volume` would do and can
    leave it effectively inaudible under a louder reference track.

    Kept for step 3 (TTS), when BGM gets calibrated relative to the
    narration track the same way the old pipeline calibrated it relative
    to on-site audio. Not used by compose_video() yet — see its docstring."""
    target_db = orig_mean_db + 20 * math.log10(bgm_volume)
    return target_db - bgm_mean_db


@dataclass(frozen=True)
class SceneClip:
    media_path: Path
    target_duration: float  # seconds, from the scene plan
    src_duration_sec: float  # seconds, probed from the actual media file


def _fit_duration_filter(label_in: str, label_out: str, src_duration: float, target_duration: float) -> list[str]:
    """Build the filtergraph statement(s) that turn a clip of `src_duration`
    seconds into exactly `target_duration` seconds, labelled `label_out`
    (video only — scene audio is never used, see module docstring):

    - source is long enough: trim the middle (avoids shaky starts/ends).
    - source is a bit short (>= half the target): slow down just enough
      (speed in (1, 2]) to stretch it to the target.
    - source is very short (< half the target): slow down 0.5x, then
      repeat it whole until it covers the target, then trim the excess.
      (CLAUDE.md "바꿀 것": "0.5배속 슬로우 → 반복 → (최후) 정지 프레임" — the
      final freeze-frame fallback isn't needed here since repeating an
      already-looping clip always reaches the target exactly.)
    """
    eps = 1e-6
    if src_duration >= target_duration - eps:
        start = max((src_duration - target_duration) / 2, 0.0)
        return [
            f"[{label_in}]trim=start={start:.6f}:duration={target_duration:.6f},"
            f"setpts=PTS-STARTPTS[{label_out}]"
        ]

    half_target = target_duration / 2
    if src_duration >= half_target - eps:
        speed = target_duration / src_duration  # in (1, 2]
        return [
            f"[{label_in}]setpts={speed:.6f}*PTS,trim=duration={target_duration:.6f},"
            f"setpts=PTS-STARTPTS[{label_out}]"
        ]

    slow_duration = src_duration * 2
    reps = math.ceil(target_duration / slow_duration)
    slowed = f"{label_out}_slow"
    split_labels = [f"{label_out}_r{i}" for i in range(reps)]
    return [
        f"[{label_in}]setpts=2.0*PTS[{slowed}]",
        f"[{slowed}]split={reps}" + "".join(f"[{l}]" for l in split_labels),
        "".join(f"[{l}]" for l in split_labels) + f"concat=n={reps}:v=1:a=0[{label_out}_cat]",
        f"[{label_out}_cat]trim=duration={target_duration:.6f},setpts=PTS-STARTPTS[{label_out}]",
    ]


def _xfade_chain(
    labels: list[str], durations: list[float], *, transition_type: str, transition_duration: float, output_label: str
) -> list[str]:
    """Chain clips `labels` (already-cropped scene streams, e.g. "v0") with
    an ffmpeg `xfade` crossfade between each consecutive pair, instead of a
    hard-cut `concat`. `durations` must be each clip's *actual* length as
    produced by its own filter chain — the caller is responsible for having
    stretched every clip but the last by `transition_duration` first (see
    build_ffmpeg_command), so that after each pair's `transition_duration`
    seconds of overlap, the combined result lands back on the original
    (pre-stretch) total duration.

    xfade only takes two inputs, so N clips chain pairwise: combine clip 0
    and 1, then that result with clip 2, and so on. Each step's `offset`
    (when the transition starts, in the already-combined stream's own
    timeline) is simply "combined duration so far minus the overlap"."""
    statements = []
    combined_duration = durations[0]
    prev_label = labels[0]
    for i in range(1, len(labels)):
        offset = combined_duration - transition_duration
        out_label = output_label if i == len(labels) - 1 else f"{output_label}_x{i}"
        statements.append(
            f"[{prev_label}][{labels[i]}]xfade=transition={transition_type}:"
            f"duration={transition_duration:.6f}:offset={offset:.6f}[{out_label}]"
        )
        combined_duration = combined_duration + durations[i] - transition_duration
        prev_label = out_label
    return statements


def build_ffmpeg_command(
    scene_clips: list[SceneClip],
    subtitle_path: Path,
    bgm_path: Path,
    output_path: Path,
    *,
    bgm_volume: float,
    fade_duration: float,
    font_name: str,
    margin_v: int,
    bgm_gain_db: float | None = None,
    transition_type: str = "fade",
    transition_duration: float = 0.0,
    font_dir: Path = config.FONT_DIR,
    width: int = config.VIDEO_WIDTH,
    height: int = config.VIDEO_HEIGHT,
    fps: int = config.FPS,
    preset: str = config.FFMPEG_PRESET,
) -> list[str]:
    """Build the ffmpeg argv for the compose step. Pure/testable: does not
    touch the filesystem or require ffmpeg to be installed.

    One input per scene clip plus one for BGM; every scene is fit to its
    target duration, cropped to fill `width`x`height`, then joined — a
    single encode instead of the old concat-then-compose two-pass approach.
    `bgm_gain_db`, when given, is the actual dB gain applied to the raw BGM
    track; if omitted, falls back to treating `bgm_volume` as a flat linear
    multiplier of BGM's own raw level (appropriate until step 3 adds TTS to
    calibrate against — see compose_video()'s docstring).

    `transition_duration > 0` joins scenes with an ffmpeg `xfade` crossfade
    instead of a hard-cut `concat`. To keep the final output at the same
    total duration as without transitions (every scene's `target_duration`
    still sums to the planned 13/14/15s), every scene but the last is first
    stretched/fit to `target_duration + transition_duration` — the extra
    `transition_duration` at its tail is exactly what gets folded into the
    next scene's crossfade, so the combined result lands back on the
    original total (see _xfade_chain())."""
    if not scene_clips:
        raise ValueError("scene_clips must not be empty")

    total_duration = sum(c.target_duration for c in scene_clips)
    fade_out_start = max(total_duration - fade_duration, 0.0)
    if bgm_gain_db is None:
        bgm_gain_db = 20 * math.log10(bgm_volume)

    use_xfade = transition_duration > 0 and len(scene_clips) > 1
    fit_durations = [c.target_duration for c in scene_clips]
    if use_xfade:
        fit_durations = [d + transition_duration for d in fit_durations[:-1]] + [fit_durations[-1]]

    filters: list[str] = []
    v_labels: list[str] = []
    for i, clip in enumerate(scene_clips):
        filters += _fit_duration_filter(f"{i}:v", f"vfit{i}", clip.src_duration_sec, fit_durations[i])
        filters.append(
            f"[vfit{i}]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},fps={fps},setsar=1[v{i}]"
        )
        v_labels.append(f"v{i}")

    if use_xfade:
        filters += _xfade_chain(
            v_labels, fit_durations,
            transition_type=transition_type, transition_duration=transition_duration,
            output_label="vconcat",
        )
    else:
        filters.append("".join(f"[{l}]" for l in v_labels) + f"concat=n={len(scene_clips)}:v=1:a=0[vconcat]")

    subtitle_arg = _escape_filter_path(subtitle_path)
    fontsdir_arg = _escape_filter_path(font_dir)
    filters.append(
        f"[vconcat]subtitles='{subtitle_arg}':fontsdir='{fontsdir_arg}':"
        f"force_style='FontName={font_name},MarginV={margin_v}',"
        f"fade=t=in:st=0:d={fade_duration},"
        f"fade=t=out:st={fade_out_start}:d={fade_duration}[vout]"
    )

    bgm_input_index = len(scene_clips)
    filters.append(
        f"[{bgm_input_index}:a]volume={bgm_gain_db}dB,aloop=loop=-1:size=2147483647,"
        f"atrim=duration={total_duration:.6f},asetpts=PTS-STARTPTS,"
        f"afade=t=out:st={fade_out_start}:d={fade_duration}[aout]"
    )

    command = ["ffmpeg", "-y"]
    for clip in scene_clips:
        command += ["-i", str(clip.media_path)]
    command += ["-i", str(bgm_path)]
    command += [
        "-filter_complex", ";".join(filters),
        "-map", "[vout]",
        "-map", "[aout]",
        "-c:v", "libx264",
        "-preset", preset,
        "-r", str(fps),
        "-c:a", "aac",
        "-t", f"{total_duration:.6f}",
        str(output_path),
    ]
    return command


def compose_video(
    scene_specs: list[tuple[Path, float]],
    subtitle_path: Path,
    bgm_path: Path,
    output_path: Path,
    *,
    bgm_volume: float,
    fade_duration: float,
    font_name: str,
    margin_v: int,
    deadline: float | None = None,
    **kwargs,
) -> Path:
    """Probe each scene's source clip, then build and run the ffmpeg
    command. `scene_specs` is `(media_path, target_duration_sec)` per
    scene, in final order.

    BGM is calibrated against its own raw level (a flat ratio of
    `bgm_volume`), not relative to another track — there's no narration or
    on-site audio in this step's output to be relative *to* yet. Step 3
    (TTS) will switch this to calibrate BGM relative to the TTS track's
    measured loudness, the way the old pipeline calibrated it relative to
    on-site audio (see compute_bgm_gain_db())."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    scene_clips = [
        SceneClip(
            media_path=media_path,
            target_duration=target_duration,
            src_duration_sec=probe_duration(media_path, deadline=deadline),
        )
        for media_path, target_duration in scene_specs
    ]

    command = build_ffmpeg_command(
        scene_clips,
        subtitle_path,
        bgm_path,
        output_path,
        bgm_volume=bgm_volume,
        fade_duration=fade_duration,
        font_name=font_name,
        margin_v=margin_v,
        **kwargs,
    )
    _run(command, deadline=deadline, check=True)
    return output_path


def build_thumbnail_command(
    video_path: Path, output_path: Path, *, at_sec: float = 0.3, quality: int = 2
) -> list[str]:
    """Build the ffmpeg argv that grabs a single JPG frame from the
    already-composed final MP4 — not the raw hook footage, since the final
    video is already cropped/letterboxed to the right 1080x1920 canvas, so
    grabbing straight from it needs no extra scaling. `at_sec` defaults to
    just past the start (not frame 0) to skip the fade-in. Pure/testable:
    does not touch the filesystem or require ffmpeg installed."""
    return [
        "ffmpeg", "-y",
        "-ss", f"{at_sec:.3f}",
        "-i", str(video_path),
        "-frames:v", "1",
        "-update", "1",
        "-q:v", str(quality),
        str(output_path),
    ]


def generate_thumbnail(
    video_path: Path, output_path: Path, *, at_sec: float = 0.3, deadline: float | None = None
) -> Path:
    """docs/reels-api-spec.md: "썸네일은 훅 장면에서 뽑은 1080×1920 JPG." The
    composed video's first ~2s *is* the hook scene (see templates.py's
    scene order), so a frame near the start always comes from it."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = build_thumbnail_command(video_path, output_path, at_sec=at_sec)
    _run(command, deadline=deadline, check=True)
    return output_path
