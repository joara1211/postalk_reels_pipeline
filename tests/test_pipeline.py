import time
import unittest
from pathlib import Path
from unittest.mock import patch

from src.quote_video import config
from src.quote_video.pipeline import run_pipeline
from src.quote_video.render_request import RenderRequest

FIXTURE = Path(__file__).parent / "fixtures" / "render_request_1menu.json"
MEDIA_ROOT = Path(__file__).parent.parent / "assets" / "videos"


class TestRunPipelineDeadline(unittest.TestCase):
    """run_pipeline() shouldn't touch ffmpeg/ffprobe in these tests —
    compose_video is mocked so we can check *what* gets passed to it
    without needing real media files or ffmpeg installed."""

    def setUp(self):
        patcher_compose = patch("src.quote_video.pipeline.compose_video")
        self.mock_compose = patcher_compose.start()
        self.addCleanup(patcher_compose.stop)
        self.mock_compose.return_value = Path("output/final.mp4")
        self.request = RenderRequest.from_json_file(FIXTURE)

    def test_default_timeout_produces_a_near_future_deadline(self):
        run_pipeline(self.request, MEDIA_ROOT, Path("output/final.mp4"))
        deadline = self.mock_compose.call_args.kwargs["deadline"]

        now = time.monotonic()
        self.assertLessEqual(deadline, now + config.PIPELINE_TIMEOUT_SECONDS)
        self.assertGreater(deadline, now)

    def test_timeout_seconds_none_disables_the_deadline(self):
        run_pipeline(self.request, MEDIA_ROOT, Path("output/final.mp4"), timeout_seconds=None)
        self.assertIsNone(self.mock_compose.call_args.kwargs["deadline"])

    def test_scene_specs_resolve_media_keys_under_media_root_in_scene_order(self):
        run_pipeline(self.request, MEDIA_ROOT, Path("output/final.mp4"))
        scene_specs = self.mock_compose.call_args.args[0]
        self.assertEqual(
            [str(p) for p, _ in scene_specs],
            [str(MEDIA_ROOT / f"{i}.mp4") for i in (1, 2, 3, 4)],
        )

    def test_scene_durations_match_the_one_menu_scene_plan(self):
        run_pipeline(self.request, MEDIA_ROOT, Path("output/final.mp4"))
        scene_specs = self.mock_compose.call_args.args[0]
        hook, closeup, detail, outro = (d for _, d in scene_specs)

        self.assertEqual(hook, 2.0)
        self.assertEqual(outro, 2.0)
        # closeup/detail aren't forced to a flat 4.5/4.5 split anymore —
        # they're weighted by each scene's actual recorded length
        # (2.mp4/closeup is longer than 3.mp4/detail, see
        # templates.rebalance_menu_scene_durations()), but the menu's own
        # 9s budget is still preserved exactly.
        self.assertAlmostEqual(closeup + detail, 9.0)
        self.assertGreater(closeup, detail)
        self.assertAlmostEqual(hook + closeup + detail + outro, 13.0)

    def test_bgm_and_subtitle_settings_come_from_the_template(self):
        run_pipeline(self.request, MEDIA_ROOT, Path("output/final.mp4"))
        kwargs = self.mock_compose.call_args.kwargs
        self.assertEqual(kwargs["bgm_volume"], 0.3)
        self.assertEqual(kwargs["font_name"], "Griun Fromsol")
        self.assertEqual(kwargs["margin_v"], 560)

    def test_transition_settings_come_from_the_template(self):
        run_pipeline(self.request, MEDIA_ROOT, Path("output/final.mp4"))
        kwargs = self.mock_compose.call_args.kwargs
        self.assertEqual(kwargs["transition_type"], "fade")
        self.assertEqual(kwargs["transition_duration"], 0.3)

    def test_christmas_concept_loads_its_own_template(self):
        self.request.scenes  # sanity: request fixture is 'simple'
        christmas_request = RenderRequest.from_json_file(FIXTURE)
        christmas_request = RenderRequest(**{**christmas_request.__dict__, "concept": "christmas"})
        run_pipeline(christmas_request, MEDIA_ROOT, Path("output/final.mp4"))
        kwargs = self.mock_compose.call_args.kwargs
        self.assertEqual(kwargs["transition_type"], "fade")

    def test_christmas_concept_passes_its_overlay_image(self):
        christmas_request = RenderRequest(**{**self.request.__dict__, "concept": "christmas"})
        run_pipeline(christmas_request, MEDIA_ROOT, Path("output/final.mp4"))
        kwargs = self.mock_compose.call_args.kwargs
        self.assertEqual(
            kwargs["overlay_path"], config.REPO_ROOT / "assets" / "overlay" / "christmas_border_placeholder.png"
        )

    def test_simple_concept_has_no_overlay(self):
        run_pipeline(self.request, MEDIA_ROOT, Path("output/final.mp4"))
        kwargs = self.mock_compose.call_args.kwargs
        self.assertIsNone(kwargs["overlay_path"])

    def test_accepts_a_plain_dict_request_too(self):
        import json

        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        run_pipeline(data, MEDIA_ROOT, Path("output/final.mp4"))
        self.mock_compose.assert_called_once()


class TestRunPipelineDurationRebalancing(unittest.TestCase):
    """Controls exact probed source durations (instead of relying on
    assets/videos/*.mp4's real lengths) to check the rebalanced durations
    precisely."""

    def setUp(self):
        patcher_compose = patch("src.quote_video.pipeline.compose_video")
        self.mock_compose = patcher_compose.start()
        self.addCleanup(patcher_compose.stop)
        self.mock_compose.return_value = Path("output/final.mp4")

        patcher_probe = patch("src.quote_video.pipeline.probe_duration")
        self.mock_probe = patcher_probe.start()
        self.addCleanup(patcher_probe.stop)
        # closeup (2.mp4) filmed long, detail (3.mp4) filmed short.
        self.mock_probe.side_effect = lambda path, **kwargs: {"2.mp4": 8.0, "3.mp4": 2.0}.get(path.name, 2.0)

        self.request = RenderRequest.from_json_file(FIXTURE)

    def test_closeup_gets_more_time_than_detail_from_longer_footage(self):
        run_pipeline(self.request, MEDIA_ROOT, Path("output/final.mp4"))
        scene_specs = self.mock_compose.call_args.args[0]
        _hook, closeup, detail, _outro = (d for _, d in scene_specs)

        self.assertGreater(closeup, 4.5)
        self.assertLess(detail, 4.5)
        self.assertAlmostEqual(closeup + detail, 9.0)

    def test_probing_uses_the_shared_deadline(self):
        run_pipeline(self.request, MEDIA_ROOT, Path("output/final.mp4"))
        self.assertIn("deadline", self.mock_probe.call_args.kwargs)


class TestRunPipelineMediaValidation(unittest.TestCase):
    def test_missing_media_file_raises_no_usable_media(self):
        from src.quote_video.errors import NoUsableMediaError

        request = RenderRequest.from_json_file(FIXTURE)
        with self.assertRaises(NoUsableMediaError):
            run_pipeline(request, Path("does/not/exist"), Path("output/final.mp4"))


if __name__ == "__main__":
    unittest.main()
