import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.quote_video import api, job_store
from src.quote_video.errors import NoUsableMediaError
from src.quote_video.pipeline import PipelineResult
from src.quote_video.script_generator import ScriptResult, SceneScript
from src.quote_video.video_composer import PipelineTimeoutError

ASSETS_VIDEOS = Path(__file__).parent.parent / "assets" / "videos"


def _script_request(concept="simple", menus=None):
    return {
        "user_id": 1,
        "concept": concept,
        "store": {"name": "해오름 떡집", "description": "30년 전통 방식으로 만드는 동네 떡집"},
        "menus": menus if menus is not None else [{"menu_index": 0, "name": "창억떡", "description": "쫀득한 식감"}],
    }


def _render_request(ad_id="ad_test_0001", concept="simple"):
    return {
        "user_id": 1,
        "ad_id": ad_id,
        "concept": concept,
        "store": {"name": "해오름 떡집", "description": "30년 전통 방식으로 만드는 동네 떡집"},
        "menus": [{"menu_index": 0, "name": "창억떡", "description": "쫀득한 식감, 한 팩 5,000원"}],
        "media": [
            {"scene_index": 0, "menu_index": None, "key": "1.mp4"},
            {"scene_index": 1, "menu_index": 0, "key": "2.mp4"},
            {"scene_index": 2, "menu_index": 0, "key": "3.mp4"},
            {"scene_index": 3, "menu_index": None, "key": "4.mp4"},
        ],
        "scenes": [
            {"scene_index": 0, "text": "30년 전통 해오름 떡집"},
            {"scene_index": 1, "text": "쫀득한 창억떡"},
            {"scene_index": 2, "text": "입에 착 붙는 쫀득함"},
            {"scene_index": 3, "text": "오늘 바로 들러주세요"},
        ],
        "caption": "30년 전통 방식 그대로, 해오름 떡집 창억떡을 만나보세요.",
        "hashtags": ["#창억떡", "#떡집"],
    }


class ApiTestCase(unittest.TestCase):
    """Gives every test a fresh in-memory job store, so render_id/ad_id
    state from one test can't leak into another."""

    def setUp(self):
        job_store.store = job_store.JobStore()
        self.client = TestClient(api.app)


class TestHealthzAndTemplates(ApiTestCase):
    def test_healthz(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)

    def test_templates_lists_both_concepts_without_render_key(self):
        response = self.client.get("/reels/templates")
        self.assertEqual(response.status_code, 200)
        templates = {t["concept"]: t for t in response.json()["templates"]}
        self.assertEqual(set(templates), {"simple", "christmas"})
        for template in templates.values():
            self.assertNotIn("render", template)
            self.assertIn("scene_rule", template)
            self.assertIn("audio", template)
            self.assertTrue(template["has_captions"])


class TestPostScripts(ApiTestCase):
    def test_returns_202_with_pending_status(self):
        response = self.client.post("/reels/scripts", json=_script_request())
        self.assertEqual(response.status_code, 202)
        body = response.json()
        self.assertEqual(body["status"], "pending")
        self.assertTrue(body["script_id"].startswith("rs_"))

    def test_background_generation_lands_as_done(self):
        fake_result = ScriptResult(
            scenes=[
                SceneScript(0, "hook", None, "가게", "해오름 떡집", 14),
                SceneScript(1, "closeup", 0, "창억떡", "쫀득한 창억떡", 12),
                SceneScript(2, "detail", 0, "창억떡", "창억떡", 16),
                SceneScript(3, "outro", None, "가게", "지금 바로 들러주세요", 16),
            ],
            caption="해오름 떡집에서 창억떡을 만나보세요.",
            hashtags=["#창억떡"],
            is_fallback=False,
        )
        with patch("src.quote_video.api.generate_script", return_value=fake_result):
            post_response = self.client.post("/reels/scripts", json=_script_request())
        script_id = post_response.json()["script_id"]

        get_response = self.client.get(f"/reels/scripts/{script_id}")
        body = get_response.json()
        self.assertEqual(body["status"], "done")
        self.assertFalse(body["is_fallback"])
        self.assertEqual(len(body["scenes"]), 4)
        self.assertEqual(body["scenes"][0]["target"], "가게")
        self.assertEqual(body["caption"], "해오름 떡집에서 창억떡을 만나보세요.")

    def test_without_api_key_falls_back_gracefully(self):
        # no ANTHROPIC_API_KEY in this test environment -> generate_script's
        # own fallback kicks in; the endpoint should still report "done".
        with patch.dict("os.environ", {}, clear=True):
            post_response = self.client.post("/reels/scripts", json=_script_request())
        script_id = post_response.json()["script_id"]
        body = self.client.get(f"/reels/scripts/{script_id}").json()
        self.assertEqual(body["status"], "done")
        self.assertTrue(body["is_fallback"])

    def test_unknown_concept_is_400(self):
        response = self.client.post("/reels/scripts", json=_script_request(concept="does_not_exist"))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "INVALID_CONCEPT")

    def test_four_menus_is_400(self):
        menus = [{"menu_index": i, "name": f"메뉴{i}", "description": ""} for i in range(4)]
        response = self.client.post("/reels/scripts", json=_script_request(menus=menus))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "INVALID_MENU_COUNT")

    def test_unknown_script_id_is_404(self):
        response = self.client.get("/reels/scripts/rs_doesnotexist")
        self.assertEqual(response.status_code, 404)


class TestPostRendersValidation(ApiTestCase):
    def test_unknown_concept_is_400(self):
        response = self.client.post("/reels/renders", json=_render_request(concept="does_not_exist"))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "INVALID_CONCEPT")

    def test_caption_too_long_is_400(self):
        body = _render_request()
        body["scenes"][0]["text"] = "이건 분명히 열네 글자를 훌쩍 넘기는 아주 긴 훅 자막입니다"
        response = self.client.post("/reels/renders", json=body)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "CAPTION_TOO_LONG")

    def test_scene_mismatch_is_400(self):
        body = _render_request()
        body["media"].pop()
        response = self.client.post("/reels/renders", json=body)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "SCENE_MISMATCH")

    def test_unknown_render_id_is_404(self):
        response = self.client.get("/reels/renders/rr_doesnotexist")
        self.assertEqual(response.status_code, 404)


class TestPostRendersAlreadyRendering(ApiTestCase):
    def test_active_job_for_same_ad_id_returns_409_with_existing_id(self):
        existing, _ = job_store.store.create_render_if_not_active("ad_test_0001")
        response = self.client.post("/reels/renders", json=_render_request(ad_id="ad_test_0001"))
        self.assertEqual(response.status_code, 409)
        body = response.json()["detail"]
        self.assertEqual(body["code"], "ALREADY_RENDERING")
        self.assertEqual(body["render_id"], existing.render_id)


class TestPostRendersProcessing(ApiTestCase):
    """Mocks run_pipeline/generate_thumbnail so these don't need real ffmpeg
    or media files — just the API/job-store wiring. See
    TestRenderRealFfmpeg below for one real end-to-end render."""

    def test_successful_render_reaches_done_with_result(self):
        fake = PipelineResult(output_path=Path("fake_output.mp4"), duration_sec=13.0)
        with patch("src.quote_video.api.run_pipeline", return_value=fake):
            with patch("src.quote_video.api.generate_thumbnail") as mock_thumb:
                post_response = self.client.post("/reels/renders", json=_render_request())
                render_id = post_response.json()["render_id"]
        mock_thumb.assert_called_once()

        body = self.client.get(f"/reels/renders/{render_id}").json()
        self.assertEqual(body["status"], "done")
        self.assertEqual(body["progress"]["stage"], "uploading")
        self.assertEqual(body["progress"]["percent"], 100)
        self.assertEqual(body["result"]["duration_sec"], 13.0)
        self.assertEqual(body["result"]["caption"], "30년 전통 방식 그대로, 해오름 떡집 창억떡을 만나보세요.")
        self.assertEqual(body["result"]["video_key"], "results/1/ad_test_0001/reel.mp4")
        self.assertEqual(body["result"]["thumbnail_key"], "results/1/ad_test_0001/thumb.jpg")
        self.assertEqual(body["result"]["fallbacks"], [])
        self.assertIsNone(body["error"])

    def test_no_usable_media_sets_failed_with_code(self):
        with patch("src.quote_video.api.run_pipeline", side_effect=NoUsableMediaError("missing clip")):
            post_response = self.client.post("/reels/renders", json=_render_request())
        render_id = post_response.json()["render_id"]

        body = self.client.get(f"/reels/renders/{render_id}").json()
        self.assertEqual(body["status"], "failed")
        self.assertEqual(body["error"]["code"], "NO_USABLE_MEDIA")

    def test_timeout_sets_failed_with_code(self):
        with patch("src.quote_video.api.run_pipeline", side_effect=PipelineTimeoutError("too slow")):
            post_response = self.client.post("/reels/renders", json=_render_request())
        render_id = post_response.json()["render_id"]

        body = self.client.get(f"/reels/renders/{render_id}").json()
        self.assertEqual(body["status"], "failed")
        self.assertEqual(body["error"]["code"], "TIMEOUT")

    def test_no_usable_media_and_timeout_are_not_retried(self):
        with patch("src.quote_video.api.run_pipeline", side_effect=NoUsableMediaError("x")) as mock_run:
            self.client.post("/reels/renders", json=_render_request())
        self.assertEqual(mock_run.call_count, 1)

    def test_generic_ffmpeg_failure_retries_once_then_fails(self):
        with patch("src.quote_video.api.run_pipeline", side_effect=RuntimeError("ffmpeg crashed")) as mock_run:
            post_response = self.client.post("/reels/renders", json=_render_request())
        render_id = post_response.json()["render_id"]
        self.assertEqual(mock_run.call_count, 2)

        body = self.client.get(f"/reels/renders/{render_id}").json()
        self.assertEqual(body["status"], "failed")
        self.assertEqual(body["error"]["code"], "RENDER_FAILED")

    def test_succeeds_on_second_attempt_after_first_failure(self):
        fake = PipelineResult(output_path=Path("fake_output.mp4"), duration_sec=13.0)
        with patch("src.quote_video.api.run_pipeline", side_effect=[RuntimeError("boom"), fake]) as mock_run:
            with patch("src.quote_video.api.generate_thumbnail"):
                post_response = self.client.post("/reels/renders", json=_render_request())
        render_id = post_response.json()["render_id"]
        self.assertEqual(mock_run.call_count, 2)

        body = self.client.get(f"/reels/renders/{render_id}").json()
        self.assertEqual(body["status"], "done")

    def test_callback_fires_for_each_stage_transition(self):
        # send_callback receives the *same* mutable RenderJob instance each
        # time (job_store.get_render() doesn't copy), so a plain Mock's
        # call_args_list would end up holding N references to one object
        # that's already in its final state by the time the test inspects
        # it. Snapshotting via .to_response() at call time avoids that.
        fake = PipelineResult(output_path=Path("fake_output.mp4"), duration_sec=13.0)

        def fake_run_pipeline(request, media_root, output_path, *, on_stage=None, **kwargs):
            if on_stage:
                on_stage("preparing")
                on_stage("composing")
            return fake

        snapshots = []

        def record_callback(render_job):
            snapshots.append(render_job.to_response())

        with patch("src.quote_video.api.run_pipeline", side_effect=fake_run_pipeline):
            with patch("src.quote_video.api.generate_thumbnail"):
                with patch("src.quote_video.job_store.send_callback", side_effect=record_callback):
                    self.client.post("/reels/renders", json=_render_request())

        # rendering/preparing, composing, uploading, done = 4 callback sends
        self.assertEqual(len(snapshots), 4)
        stages = [s["progress"]["stage"] for s in snapshots[:-1]]
        self.assertEqual(stages, ["preparing", "composing", "uploading"])
        self.assertEqual(snapshots[-1]["status"], "done")

    def test_thumbnail_at_sec_passed_through_from_pipeline_result(self):
        fake = PipelineResult(output_path=Path("fake_output.mp4"), duration_sec=15.0, thumbnail_at_sec=1.3)
        with patch("src.quote_video.api.run_pipeline", return_value=fake):
            with patch("src.quote_video.api.generate_thumbnail") as mock_thumb:
                self.client.post("/reels/renders", json=_render_request())
        self.assertEqual(mock_thumb.call_args.kwargs["at_sec"], 1.3)

    def test_thumbnail_failure_does_not_fail_the_render(self):
        fake = PipelineResult(output_path=Path("fake_output.mp4"), duration_sec=13.0)
        with patch("src.quote_video.api.run_pipeline", return_value=fake):
            with patch("src.quote_video.api.generate_thumbnail", side_effect=RuntimeError("no frame")):
                post_response = self.client.post("/reels/renders", json=_render_request())
        render_id = post_response.json()["render_id"]

        body = self.client.get(f"/reels/renders/{render_id}").json()
        self.assertEqual(body["status"], "done")


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not on PATH")
class TestRenderRealFfmpeg(ApiTestCase):
    """One real end-to-end render through the HTTP API: real footage, real
    ffmpeg, real thumbnail grab. Everything else in this file mocks
    run_pipeline/generate_thumbnail to stay fast and ffmpeg-independent."""

    def test_real_render_produces_a_done_result_with_files_on_disk(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            storage_root = Path(tmp)
            upload_dir = storage_root / "uploads"
            upload_dir.mkdir()
            for name in ("1.mp4", "2.mp4", "3.mp4", "4.mp4"):
                shutil.copy(ASSETS_VIDEOS / name, upload_dir / name)

            body = _render_request()
            for entry in body["media"]:
                entry["key"] = f"uploads/{entry['key']}"

            with patch("src.quote_video.api.STORAGE_ROOT", storage_root):
                post_response = self.client.post("/reels/renders", json=body)
                self.assertEqual(post_response.status_code, 202)
                render_id = post_response.json()["render_id"]

                result = self.client.get(f"/reels/renders/{render_id}").json()

            self.assertEqual(result["status"], "done", result.get("error"))
            self.assertAlmostEqual(result["result"]["duration_sec"], 13.0)
            video_path = storage_root / result["result"]["video_key"]
            thumb_path = storage_root / result["result"]["thumbnail_key"]
            self.assertTrue(video_path.exists())
            self.assertTrue(thumb_path.exists())


if __name__ == "__main__":
    unittest.main()
