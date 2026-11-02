import math
import subprocess
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from src.quote_video.video_composer import (
    PipelineTimeoutError,
    SceneClip,
    _fit_duration_filter,
    _remaining_seconds,
    _run,
    _xfade_chain,
    build_ffmpeg_command,
    build_thumbnail_command,
    compose_video,
    compute_bgm_gain_db,
    generate_thumbnail,
)


class TestFitDurationFilter(unittest.TestCase):
    """The per-scene duration-fit rule (CLAUDE.md "바꿀 것"): trim the middle
    if the clip is long enough, slow down just enough if it's a bit short,
    slow 0.5x + repeat if it's very short."""

    def test_long_clip_trims_the_middle_not_the_start(self):
        stmts = _fit_duration_filter("0:v", "v0", src_duration=5.0, target_duration=2.0)
        combined = ";".join(stmts)
        self.assertIn("trim=start=1.500000:duration=2.000000", combined)
        self.assertNotIn("setpts=2", combined)

    def test_exact_length_clip_is_not_sped_up_or_slowed(self):
        stmts = _fit_duration_filter("0:v", "v0", src_duration=2.0, target_duration=2.0)
        combined = ";".join(stmts)
        self.assertIn("trim=start=0.000000:duration=2.000000", combined)

    def test_moderately_short_clip_is_slowed_by_the_minimum_needed(self):
        # 3s clip stretched to 4.5s -> speed factor 1.5 (within the 1x-2x
        # band, so no looping needed).
        stmts = _fit_duration_filter("0:v", "v0", src_duration=3.0, target_duration=4.5)
        combined = ";".join(stmts)
        self.assertIn("setpts=1.500000*PTS", combined)
        self.assertIn("trim=duration=4.500000", combined)
        self.assertNotIn("split", combined)

    def test_very_short_clip_is_slowed_half_speed_then_repeated(self):
        # 1s clip, 0.5x slow -> 2s; needs to cover 4.5s -> repeated 3x then
        # trimmed down to 4.5s.
        stmts = _fit_duration_filter("0:v", "v0", src_duration=1.0, target_duration=4.5)
        combined = ";".join(stmts)
        self.assertIn("setpts=2.0*PTS", combined)
        self.assertIn("split=3", combined)
        self.assertIn("concat=n=3:v=1:a=0", combined)
        self.assertIn("trim=duration=4.500000", combined)

    def test_output_label_is_used(self):
        stmts = _fit_duration_filter("2:v", "scene9", src_duration=5.0, target_duration=2.0)
        self.assertTrue(stmts[-1].endswith("[scene9]"))


class TestBuildFfmpegCommand(unittest.TestCase):
    def setUp(self):
        self.scene_clips = [
            SceneClip(media_path=Path("hook.mp4"), target_duration=2.0, src_duration_sec=3.3),
            SceneClip(media_path=Path("closeup.mp4"), target_duration=4.5, src_duration_sec=4.0),
        ]
        self.subtitle = Path("out.ass")
        self.bgm = Path("bgm.mp3")
        self.output = Path("final.mp4")

    def _build(self, **overrides):
        kwargs = dict(
            bgm_volume=0.3,
            fade_duration=0.5,
            font_name="Griun Fromsol",
            margin_v=440,
        )
        kwargs.update(overrides)
        return build_ffmpeg_command(self.scene_clips, self.subtitle, self.bgm, self.output, **kwargs)

    def test_rejects_empty_scene_list(self):
        with self.assertRaises(ValueError):
            build_ffmpeg_command([], self.subtitle, self.bgm, self.output, bgm_volume=0.3, fade_duration=0.5, font_name="F", margin_v=1)

    def test_one_input_per_scene_plus_bgm_in_order(self):
        cmd = self._build()
        i0 = cmd.index(str(self.scene_clips[0].media_path))
        i1 = cmd.index(str(self.scene_clips[1].media_path))
        i2 = cmd.index(str(self.bgm))
        self.assertLess(i0, i1)
        self.assertLess(i1, i2)

    def test_scenes_are_concatenated_when_no_transition(self):
        cmd = self._build()
        filter_complex = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("[v0][v1]concat=n=2:v=1:a=0[vconcat]", filter_complex)
        self.assertNotIn("xfade", filter_complex)

    def test_transition_duration_uses_xfade_instead_of_concat(self):
        cmd = self._build(transition_type="fade", transition_duration=0.3)
        filter_complex = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("xfade=transition=fade:duration=0.300000", filter_complex)
        self.assertNotIn("concat=n=", filter_complex)

    def test_transition_stretches_every_scene_but_the_last(self):
        # scene 0 target=2.0 -> fit to 2.0+0.3=2.3; scene 1 (last) stays 4.5.
        cmd = self._build(transition_type="fade", transition_duration=0.3)
        filter_complex = cmd[cmd.index("-filter_complex") + 1]
        first_stmt = filter_complex.split(";")[0]
        self.assertIn("duration=2.300000", first_stmt)

    def test_transition_keeps_total_duration_and_fade_out_unchanged(self):
        # total_duration (used for fade-out/-t) comes from nominal
        # target_duration, not the per-clip xfade-inflated fit duration.
        cmd = self._build(transition_type="fade", transition_duration=0.3)
        filter_complex = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("fade=t=out:st=6.0:d=0.5", filter_complex)
        self.assertEqual(cmd[cmd.index("-t") + 1], "6.500000")

    def test_no_scene_audio_is_mapped(self):
        cmd = self._build()
        filter_complex = cmd[cmd.index("-filter_complex") + 1]
        self.assertNotIn("[0:a]", filter_complex)
        self.assertNotIn("[1:a]", filter_complex)

    def test_crop_fills_the_canvas(self):
        cmd = self._build()
        filter_complex = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("scale=1080:1920:force_original_aspect_ratio=increase", filter_complex)
        self.assertIn("crop=1080:1920", filter_complex)

    def test_explicit_bgm_gain_db_is_used_verbatim(self):
        cmd = self._build(bgm_gain_db=-7.5)
        filter_complex = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("volume=-7.5dB", filter_complex)

    def test_bgm_gain_falls_back_to_flat_ratio_of_bgm_volume(self):
        cmd = self._build(bgm_volume=0.20)
        filter_complex = cmd[cmd.index("-filter_complex") + 1]
        expected_db = 20 * math.log10(0.20)
        self.assertIn(f"volume={expected_db}dB", filter_complex)

    def test_bgm_loops_and_is_trimmed_to_total_scene_duration(self):
        cmd = self._build()
        filter_complex = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("aloop=loop=-1:size=2147483647", filter_complex)
        self.assertIn("atrim=duration=6.500000", filter_complex)  # 2.0 + 4.5

    def test_subtitle_margin_v_and_font_applied(self):
        cmd = self._build(font_name="Jayeon Sans Medium", margin_v=220)
        filter_complex = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("FontName=Jayeon Sans Medium,MarginV=220", filter_complex)

    def test_fade_out_starts_total_duration_minus_fade(self):
        cmd = self._build(fade_duration=0.5)
        filter_complex = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("fade=t=in:st=0:d=0.5", filter_complex)
        self.assertIn("fade=t=out:st=6.0:d=0.5", filter_complex)  # 6.5 - 0.5

    def test_fps_default_applied(self):
        # output resolution comes from the per-scene crop (see
        # test_crop_fills_the_canvas), so there's no separate "-s" flag to check.
        cmd = self._build()
        self.assertEqual(cmd[cmd.index("-r") + 1], "30")

    def test_output_path_is_last_argument(self):
        cmd = self._build()
        self.assertEqual(cmd[-1], str(self.output))

    def test_preset_applied_and_defaults_to_config(self):
        from src.quote_video import config

        cmd = self._build()
        self.assertEqual(cmd[cmd.index("-preset") + 1], config.FFMPEG_PRESET)

    def test_preset_override(self):
        cmd = self._build(preset="ultrafast")
        self.assertEqual(cmd[cmd.index("-preset") + 1], "ultrafast")


class TestXfadeChain(unittest.TestCase):
    def test_two_clips_single_xfade_at_first_clips_duration_minus_overlap(self):
        stmts = _xfade_chain(["v0", "v1"], [2.3, 4.5], transition_type="fade", transition_duration=0.3, output_label="vconcat")
        self.assertEqual(len(stmts), 1)
        self.assertIn("[v0][v1]xfade=transition=fade:duration=0.300000:offset=2.000000[vconcat]", stmts[0])

    def test_three_clips_chain_through_intermediate_labels(self):
        stmts = _xfade_chain(["v0", "v1", "v2"], [2.3, 4.8, 4.8], transition_type="fade", transition_duration=0.3, output_label="vconcat")
        self.assertEqual(len(stmts), 2)
        self.assertIn("[v0][v1]xfade=", stmts[0])
        self.assertTrue(stmts[0].endswith("[vconcat_x1]"))
        self.assertIn("[vconcat_x1][v2]xfade=", stmts[1])
        self.assertTrue(stmts[1].endswith("[vconcat]"))
        # second offset = (2.3 + 4.8 - 0.3) - 0.3
        self.assertIn("offset=6.500000", stmts[1])

    def test_transition_type_is_used(self):
        stmts = _xfade_chain(["v0", "v1"], [2.0, 2.0], transition_type="wipeleft", transition_duration=0.3, output_label="vconcat")
        self.assertIn("transition=wipeleft", stmts[0])


class TestComposeVideo(unittest.TestCase):
    def setUp(self):
        patcher_probe_dur = patch("src.quote_video.video_composer.probe_duration", return_value=3.3)
        patcher_run = patch("src.quote_video.video_composer._run")
        self.mock_probe_dur = patcher_probe_dur.start()
        self.mock_run = patcher_run.start()
        self.addCleanup(patcher_probe_dur.stop)
        self.addCleanup(patcher_run.stop)

    def test_probes_every_scene_and_runs_once(self):
        compose_video(
            [(Path("hook.mp4"), 2.0), (Path("outro.mp4"), 2.0)],
            subtitle_path=Path("out.ass"),
            bgm_path=Path("bgm.mp3"),
            output_path=Path("output/final.mp4"),
            bgm_volume=0.3,
            fade_duration=0.5,
            font_name="Griun Fromsol",
            margin_v=440,
        )
        self.assertEqual(self.mock_probe_dur.call_count, 2)
        self.mock_run.assert_called_once()

    def test_no_relative_bgm_probing_happens(self):
        # there's no narration/on-site track yet to calibrate BGM against
        # (step 3 adds that) — compose_video must not call probe_mean_volume_db.
        with patch("src.quote_video.video_composer.probe_mean_volume_db") as mock_probe_vol:
            compose_video(
                [(Path("hook.mp4"), 2.0)],
                subtitle_path=Path("out.ass"),
                bgm_path=Path("bgm.mp3"),
                output_path=Path("output/final.mp4"),
                bgm_volume=0.3,
                fade_duration=0.5,
                font_name="Griun Fromsol",
                margin_v=440,
            )
            mock_probe_vol.assert_not_called()


class TestBuildThumbnailCommand(unittest.TestCase):
    def test_grabs_a_single_frame_at_the_given_timestamp(self):
        cmd = build_thumbnail_command(Path("final.mp4"), Path("thumb.jpg"), at_sec=0.3)
        self.assertIn("-ss", cmd)
        self.assertEqual(cmd[cmd.index("-ss") + 1], "0.300")
        self.assertIn(str(Path("final.mp4")), cmd)
        self.assertEqual(cmd[-1], str(Path("thumb.jpg")))

    def test_frames_v_1_and_update_flag_force_a_single_image(self):
        # without -update, ffmpeg's image2 muxer wants a %d pattern and
        # falls back (with a warning) to overwriting the literal filename —
        # -update 1 makes "write exactly one image to this path" explicit.
        cmd = build_thumbnail_command(Path("final.mp4"), Path("thumb.jpg"))
        self.assertEqual(cmd[cmd.index("-frames:v") + 1], "1")
        self.assertEqual(cmd[cmd.index("-update") + 1], "1")

    def test_default_at_sec_skips_the_fade_in(self):
        cmd = build_thumbnail_command(Path("final.mp4"), Path("thumb.jpg"))
        self.assertGreater(float(cmd[cmd.index("-ss") + 1]), 0.0)


class TestGenerateThumbnail(unittest.TestCase):
    def test_creates_parent_dir_and_runs_ffmpeg(self):
        with patch("src.quote_video.video_composer._run") as mock_run:
            with patch("src.quote_video.video_composer.Path.mkdir") as mock_mkdir:
                generate_thumbnail(Path("final.mp4"), Path("out/thumb.jpg"))
        mock_mkdir.assert_called_once()
        mock_run.assert_called_once()
        command = mock_run.call_args.args[0]
        self.assertEqual(command[0], "ffmpeg")


class TestRemainingSeconds(unittest.TestCase):
    def test_none_deadline_means_no_limit(self):
        self.assertIsNone(_remaining_seconds(None))

    def test_future_deadline_returns_positive_remaining(self):
        remaining = _remaining_seconds(time.monotonic() + 10)
        self.assertGreater(remaining, 0)
        self.assertLessEqual(remaining, 10)

    def test_past_deadline_raises_immediately(self):
        with self.assertRaises(PipelineTimeoutError):
            _remaining_seconds(time.monotonic() - 1)


class TestRun(unittest.TestCase):
    def test_timeout_expired_becomes_pipeline_timeout_error(self):
        with patch(
            "src.quote_video.video_composer.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=5),
        ):
            with self.assertRaises(PipelineTimeoutError):
                _run(["ffmpeg", "-y", "-i", "in.mp4", "out.mp4"], deadline=time.monotonic() + 5)

    def test_no_deadline_passes_none_as_subprocess_timeout(self):
        with patch("src.quote_video.video_composer.subprocess.run") as mock_run:
            _run(["ffmpeg", "-version"], deadline=None)
            self.assertIsNone(mock_run.call_args.kwargs["timeout"])


class TestComputeBgmGainDb(unittest.TestCase):
    def test_matches_original_level_when_ratio_is_1(self):
        gain = compute_bgm_gain_db(orig_mean_db=-20.3, bgm_mean_db=-20.3, bgm_volume=1.0)
        self.assertAlmostEqual(gain, 0.0)

    def test_targets_20_percent_relative_to_louder_original(self):
        gain = compute_bgm_gain_db(orig_mean_db=-20.3, bgm_mean_db=-20.3, bgm_volume=0.20)
        self.assertAlmostEqual(gain, 20 * math.log10(0.20), places=4)

    def test_boosts_a_quiet_bgm_file_up_to_the_target(self):
        gain = compute_bgm_gain_db(orig_mean_db=-10.0, bgm_mean_db=-40.0, bgm_volume=0.20)
        self.assertGreater(gain, 0.0)


if __name__ == "__main__":
    unittest.main()
