import unittest
from unittest.mock import patch

from src.quote_video.job_store import JobStore, send_callback, stage_progress


class TestStageProgress(unittest.TestCase):
    def test_preparing_is_stage_1_of_3(self):
        progress = stage_progress("preparing")
        self.assertEqual(progress["stage"], "preparing")
        self.assertEqual(progress["stage_index"], 1)
        self.assertEqual(progress["stage_total"], 3)
        self.assertEqual(progress["label"], "영상을 다듬고 있어요")

    def test_uploading_is_stage_3_of_3_at_100_percent(self):
        progress = stage_progress("uploading")
        self.assertEqual(progress["stage_index"], 3)
        self.assertEqual(progress["percent"], 100)

    def test_no_narrating_stage(self):
        with self.assertRaises(ValueError):
            stage_progress("narrating")


class TestScriptJobs(unittest.TestCase):
    def setUp(self):
        self.store = JobStore()

    def test_create_assigns_pending_status_and_rs_prefixed_id(self):
        job = self.store.create_script()
        self.assertEqual(job.status, "pending")
        self.assertTrue(job.script_id.startswith("rs_"))

    def test_get_returns_none_for_unknown_id(self):
        self.assertIsNone(self.store.get_script("rs_doesnotexist"))

    def test_update_mutates_stored_job(self):
        job = self.store.create_script()
        self.store.update_script(job.script_id, status="done", caption="hi")
        fetched = self.store.get_script(job.script_id)
        self.assertEqual(fetched.status, "done")
        self.assertEqual(fetched.caption, "hi")

    def test_ids_are_unique(self):
        ids = {self.store.create_script().script_id for _ in range(20)}
        self.assertEqual(len(ids), 20)


class TestRenderJobs(unittest.TestCase):
    def setUp(self):
        self.store = JobStore()

    def test_create_if_not_active_creates_new_job_for_new_ad_id(self):
        job, created = self.store.create_render_if_not_active("ad_1")
        self.assertTrue(created)
        self.assertEqual(job.ad_id, "ad_1")
        self.assertEqual(job.status, "queued")
        self.assertTrue(job.render_id.startswith("rr_"))

    def test_create_if_not_active_returns_existing_when_queued(self):
        first, _ = self.store.create_render_if_not_active("ad_1")
        second, created = self.store.create_render_if_not_active("ad_1")
        self.assertFalse(created)
        self.assertEqual(second.render_id, first.render_id)

    def test_create_if_not_active_returns_existing_when_rendering(self):
        first, _ = self.store.create_render_if_not_active("ad_1")
        self.store.update_render(first.render_id, status="rendering")
        _second, created = self.store.create_render_if_not_active("ad_1")
        self.assertFalse(created)

    def test_create_if_not_active_creates_new_job_once_previous_is_done(self):
        first, _ = self.store.create_render_if_not_active("ad_1")
        self.store.update_render(first.render_id, status="done")
        second, created = self.store.create_render_if_not_active("ad_1")
        self.assertTrue(created)
        self.assertNotEqual(second.render_id, first.render_id)

    def test_create_if_not_active_creates_new_job_once_previous_failed(self):
        first, _ = self.store.create_render_if_not_active("ad_1")
        self.store.update_render(first.render_id, status="failed")
        second, created = self.store.create_render_if_not_active("ad_1")
        self.assertTrue(created)
        self.assertNotEqual(second.render_id, first.render_id)

    def test_different_ad_ids_never_collide(self):
        job_a, created_a = self.store.create_render_if_not_active("ad_a")
        job_b, created_b = self.store.create_render_if_not_active("ad_b")
        self.assertTrue(created_a)
        self.assertTrue(created_b)
        self.assertNotEqual(job_a.render_id, job_b.render_id)

    def test_update_bumps_updated_at(self):
        job, _ = self.store.create_render_if_not_active("ad_1")
        before = job.updated_at
        self.store.update_render(job.render_id, status="rendering")
        self.assertGreaterEqual(self.store.get_render(job.render_id).updated_at, before)


class TestSendCallback(unittest.TestCase):
    def setUp(self):
        store = JobStore()
        self.job, _ = store.create_render_if_not_active("ad_1")

    def test_skips_silently_when_next_base_url_not_set(self):
        with patch.dict("os.environ", {}, clear=True):
            with patch("src.quote_video.job_store.httpx.post") as mock_post:
                send_callback(self.job)
        mock_post.assert_not_called()

    def test_posts_to_callback_path_with_api_key_header(self):
        with patch.dict("os.environ", {"NEXT_BASE_URL": "https://next.example", "REELS_CALLBACK_API_KEY": "k1"}):
            with patch("src.quote_video.job_store.httpx.post") as mock_post:
                send_callback(self.job)
        mock_post.assert_called_once()
        url = mock_post.call_args.args[0]
        self.assertEqual(url, "https://next.example/api/internal/reels/callback")
        self.assertEqual(mock_post.call_args.kwargs["headers"]["X-Internal-Api-Key"], "k1")

    def test_body_includes_sent_at_and_render_fields(self):
        with patch.dict("os.environ", {"NEXT_BASE_URL": "https://next.example"}):
            with patch("src.quote_video.job_store.httpx.post") as mock_post:
                send_callback(self.job)
        body = mock_post.call_args.kwargs["json"]
        self.assertIn("sent_at", body)
        self.assertEqual(body["render_id"], self.job.render_id)

    def test_network_failure_is_swallowed_not_raised(self):
        import httpx

        with patch.dict("os.environ", {"NEXT_BASE_URL": "https://next.example"}):
            with patch("src.quote_video.job_store.httpx.post", side_effect=httpx.ConnectError("boom")):
                send_callback(self.job)  # must not raise


if __name__ == "__main__":
    unittest.main()
