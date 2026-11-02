# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository state

This repo (`youtube_longform_agent`) contains the actual application code, built from the plan in `quote-video-prompt/`. `quote-video-prompt/` is **its own separate git repository** (remote: `github.com/uxjoseph/quote-video-prompt`), not a submodule — it shows up as untracked in the outer repo's git status. Do not try to commit inside it as part of the outer repo's history, and do not assume `git log`/`git diff` at the top level will show its history.

**`quote-video-prompt/prompt.md` and `README.md` describe an earlier plan that no longer matches what's built here** — the user explicitly dropped the image-generation and TTS/STT stages (2026-08-20) in favor of using real footage directly. Treat those two files as historical/superseded context, not as the current spec. The real spec is this file plus the code itself.

## Actual pipeline (as built, superseding the original prompt.md plan)

```
real footage (already cut-edited, on-site audio kept) + hand-written per-cut caption script
        → generate-subtitle (rule-based timestamps, no STT, markdown stripped) → .ass
        → compose-video (ffmpeg: burn .ass, mix in BGM, fade in/out) → final MP4
```

There is **no image generation and no TTS/STT** in this system — `video_path` is always real, already-edited footage; Gemini image gen, Gemini TTS, and Whisper were all cut from the original plan. Do not reintroduce them without asking the user first.

### The caption/cut timing rule

This is the load-bearing design decision (confirmed with the user, not guessed):
- Source video cuts are a fixed `CUT_DURATION` = 2.0s each.
- Each caption line in the script is shown for a fixed `CAPTION_DURATION` = 1.0s.
- So every video cut carries exactly `CAPTIONS_PER_CUT` = 2 caption lines in sequence.
- Timestamps are computed purely from line order (line *i* → `[i·1s, (i+1)·1s)`) — no speech recognition, no manual per-line timestamp entry. `subtitle_sync.total_video_duration()` raises if the caption count doesn't divide evenly by 2, since that means it can't line up with real 2s cuts.

All three constants live in `src/quote_video/config.py` — change them there, not inline, if the rule ever changes.

### Caption script format — two accepted forms, live asset uses the current (bracketed-timestamp) one

Parsed by `subtitle_sync.parse_caption_script()` via `_CAPTION_LINE_RE`. Only the number immediately before the final `:**` on each line is read (as the caption's 1-based `sequence_number`) — everything else in that `**...**` span (a `[bracketed timestamp]`, a `자막`/`컷` label) is decorative and ignored. Numbers must be sequential from 1 across the whole file; `## [...]` section headers are for human readability only and aren't matched.

```markdown
# older format (still supported, tests/fixtures/sample_caption.md)
## [제1문장]
* **1컷:** text
* **2컷:** text

# current format — what's actually in assets/caption/*.md as of 2026-08-20
## [비디오 컷 1] (00:00 ~ 00:02)
* **[00:00 ~ 00:01] 자막 1:** text
* **[00:01 ~ 00:02] 자막 2:** text
```

**The written-in `[00:00 ~ 00:01]` timestamps are not parsed or used for anything** — actual timing still comes purely from `compute_timings()`'s `sequence_number × CAPTION_DURATION` rule. They happen to already match that rule in the current file; if someone edits them to diverge (e.g. hand-tunes one line's window), the rule still wins silently. If real per-line time control becomes a requirement, that needs a deliberate design change (parse and honor the bracket), not an assumption — ask first.

`tests/fixtures/sample_caption.md` (older format) and `tests/fixtures/sample_caption_timestamped.md` (current format, copied from `assets/caption/자막.md`) are both real 16-line examples used by the test suite. `sample_caption.md`'s cut 4 has inline `**bold**` markup inside the caption text itself (not just around the label), used to test markdown stripping.

Caption text can carry inline markdown emphasis (e.g. `상호명 **명품대게**를 꼭 방문해 보세요!`) that isn't meant to reach the rendered subtitle. `subtitle_sync._strip_markdown()` strips `**bold**`, `__bold__`, `` `code` ``, `*italic*`, and any leftover stray `*`/`_`/`` ` `` from every parsed line before it becomes a `CaptionLine`.

## Layout

- `src/quote_video/` — `config.py` (all tunables), `subtitle_sync.py` (parse script → `.ass`), `video_composer.py` (ffmpeg compose + raw-clip concat), `pipeline.py` (orchestrates the two + CLI entry point). No `image_generator.py` / `tts_generator.py` — intentionally absent.
- `.claude/skills/{generate-subtitle,compose-video}/SKILL.md` — one skill per pipeline stage.
- `.claude/agents/quote-video-agent.md` (orchestrator) and `quote-writer-agent.md` (writes new caption scripts into `assets/caption/`).
- `.claude/commands/create-quote-video.md` — the `/createvideo` command.
- `assets/font/`, `assets/bgm/`, `assets/videos/`, `assets/caption/` now hold real assets (see below); `output/` (gitignored) is where rendered MP4s and the `_build/` intermediates (SRT, assembled source video) land.

### `assets/videos/` is raw numbered per-cut clips, not one pre-edited file

Real footage arrived as `1.mp4`, `2.mp4`, ... `8.mp4` — one clip per cut, each a **different resolution and an arbitrary duration** (not a clean 2.0s). `pipeline._resolve_video_path()` handles this: if `video_path` is a directory, it globs `*.mp4`, sorts numerically by filename, and calls `video_composer.assemble_cuts_video()` to normalize each clip (scale+letterbox to a common canvas, trim if longer than `CUT_DURATION`, freeze-frame-pad via `tpad` if shorter) and concatenate them into one source video before handing off to `compose_video()`. Pass a directory as `--video` to use this path; pass a single file to skip it.

### Subtitle font is WOFF, not TTF — needs conversion

`assets/font/JayeonSans-Medium.woff` is the only font supplied (the plan's `KOTRA_SONGEULSSI` was never provided). libass/fontconfig can't load `.woff` directly, so it was converted once with `fontTools` into `assets/font/JayeonSans-Medium.ttf` (family name inside the font is `Jayeon Sans Medium`, wired up in `config.SUBTITLE_FONT_NAME`/`SUBTITLE_FONT_FILE`). If new fonts show up as `.woff`/`.woff2`, convert the same way:

```python
from fontTools.ttLib import TTFont
f = TTFont("assets/font/Name.woff"); f.flavor = None; f.save("assets/font/Name.ttf")
```

### Subtitles are burned in from .ass, not .srt — libass mis-scales SRT margins

Burning in a plain `.srt` via ffmpeg's `subtitles` filter (`force_style='...MarginV=220'`) does *not* place captions 220px from the bottom of a 1080x1920 canvas — libass silently assumes the legacy default script resolution (384x288) for a bare SRT and scales `MarginV` (and any other numeric style values) against *that*, not the real frame size. On this canvas that's a ~6.67x inflation, which pushed a `MarginV=220` caption almost to the *top* of the frame instead of clearing the bottom UI (confirmed empirically by rendering solid-color test frames at several MarginV values). Confirmed via the same reproduction that a real `.ass` file with an explicit `PlayResX`/`PlayResY` header sidesteps this entirely — margins land at the intended pixel scale.

Fix: `subtitle_sync.build_ass()`/`generate_ass()` emit a minimal `.ass` script with `PlayResX`/`PlayResY` set to `config.VIDEO_WIDTH`/`VIDEO_HEIGHT` and the style (`FontName`, `MarginV`) baked in; `pipeline.py` generates `<name>.ass` (not `.srt`) and `video_composer.build_ffmpeg_command()`'s `subtitle_path` param points at it (still re-applies `FontName`/`MarginV` via `force_style` too — redundant with the baked-in style but harmless, and lets compose-time overrides work without regenerating the caption file). `build_srt()`/`generate_srt()` are still in `subtitle_sync.py` and tested, but nothing in the pipeline calls them anymore — don't reintroduce `.srt` into the compose step without re-testing this exact positioning bug.

If you need to re-verify subtitle positioning after touching this code, don't trust visual inspection of the real footage alone (its own baked-in letterboxing can make the vertical position ambiguous) — render a solid-color test clip and check pixel position directly, the way this was diagnosed.

### BGM volume is calibrated relative to the on-site audio, not to its own raw level

The first render had BGM technically mixed in but inaudible — `volume=0.2` was applied to the BGM's own raw amplitude, and the on-site market audio happened to be ~14dB louder than that, plus `amix`'s default `normalize=1` halved both tracks again on top. Fixed in `video_composer.py`: `compose_video()` now measures both tracks' mean loudness with `probe_mean_volume_db()` (parses ffmpeg's `volumedetect` output) and `compute_bgm_gain_db()` derives the actual dB gain needed so BGM's *measured loudness* sits at `BGM_VOLUME` (0.20 = 20%) relative to the *on-site audio's measured loudness* — not 20% of BGM's own level. `amix` now runs with `normalize=0` (so this calibration isn't silently undone) and an `alimiter` guards against clipping from the sum. If BGM still seems too quiet/loud after this, adjust `config.BGM_VOLUME`, not the filter internals.

`subprocess.run(..., text=True)` calls in `video_composer.py` pass `encoding="utf-8", errors="replace"` explicitly — without it, ffmpeg's stderr (UTF-8) fails to decode under Windows' default cp949 console locale and crashes the probe.

### `run_pipeline()` has a 20s wall-clock budget, enforced as a hard timeout — not a performance promise

2026-08-20 feedback: "어떤 1~8.mp4와 대본.md가 와도 ... 20초 이내로 끝나도록." There is no way to *guarantee* arbitrary input finishes in a fixed time (encode cost scales with total output duration — more/longer cuts is more work, period), so this was built as two separate things instead of one impossible one:

1. **Speed**: `config.FFMPEG_PRESET = "veryfast"` (was implicitly libx264's own "medium" default) is now passed via `-preset` to both `build_ffmpeg_command()` and `build_concat_command()`. For the actual 8-clip/16s case this runs in ~4-5s wall-clock end to end (measured), comfortably under budget.
2. **A hard ceiling**: `config.PIPELINE_TIMEOUT_SECONDS = 20.0`. `pipeline.run_pipeline()` computes one `deadline = time.monotonic() + timeout_seconds` and threads it through every ffprobe/ffmpeg call in both `assemble_cuts_video()` and `compose_video()` (`video_composer._run()` wraps `subprocess.run(..., timeout=...)`). Time already spent on clip 3's probe counts against clip 4's — it's one shared budget across the whole pipeline call, not a fresh N seconds per subprocess. Exceeding it raises `video_composer.PipelineTimeoutError` (a `TimeoutError` subclass) instead of hanging or silently running long. Pass `timeout_seconds=None` (or CLI `--timeout 0`) to disable.

If a real input still blows the 20s budget (e.g. someone hands it a 5-minute source instead of ~16s of cuts), that's the intended failure mode — a fast, clear `PipelineTimeoutError`, not a slow success. Don't try to make the deadline itself adaptive to input size; that would defeat the point of a promised ceiling.

## Commands

```bash
python -m unittest discover -s tests -v   # run tests (stdlib unittest, no pytest installed)

# single pre-edited source video:
python -m src.quote_video.pipeline --video <file.mp4> --caption <path.md> [--bgm <path>] --output <path.mp4>

# directory of raw numbered per-cut clips (1.mp4, 2.mp4, ...) — auto-assembled first:
python -m src.quote_video.pipeline --video assets/videos --caption assets/caption/<name>.md --bgm "assets/bgm/<name>.mp3" --output output/<name>.mp4

# --timeout overrides the default 20s wall-clock budget (see below); 0 disables it
python -m src.quote_video.pipeline --video assets/videos --caption assets/caption/<name>.md --output output/<name>.mp4 --timeout 30
```

ffmpeg was not preinstalled in this dev environment; it was installed via `winget install --id Gyan.FFmpeg` into `%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0-full_build\bin`. A shell opened before that install needs that dir prepended to `PATH` manually (or just open a new shell — winget already updated the persistent user `PATH`).

## Fixed config values (current, in `config.py`)

- Video: **1080x1920 portrait** (30fps, 0.5s fade in/out) — changed from the original plan's 1920x1080 landscape because every real source clip in `assets/videos/` is portrait (this is reels/shorts content). Revisit only if the user says otherwise.
- Subtitle font: `Jayeon Sans Medium` (`assets/font/JayeonSans-Medium.ttf`, converted from the supplied `.woff` — see above).
- Subtitle bottom margin: `SUBTITLE_MARGIN_V` = 220px (in the `.ass` canvas' own pixel scale — see above), raised from libass's small default so captions clear Instagram Reels' bottom UI (2026-08-20 feedback).
- On-site audio volume: `ORIGINAL_VOLUME` = 0.7 (linear), turned down from the raw recording so BGM has room (2026-08-20 feedback — "기존 영상 소리 좀 더 낮춰줘"). `compose_video()` factors this into the BGM gain calibration below, so the two stay balanced whatever `ORIGINAL_VOLUME` is set to.
- BGM volume: 20% (`BGM_VOLUME`), mixed under the (now-reduced) original on-site audio via `amix`, not replacing it.
- Image style (`Notion-style minimal illustration`) and TTS voice values from the original plan are now moot — no image-gen or TTS stage exists to consume them.
