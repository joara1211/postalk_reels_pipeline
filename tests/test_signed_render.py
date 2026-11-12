import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from src.quote_video import api, job_store
from tests.test_api import ASSETS_VIDEOS, _render_request


class TestSignedRender(unittest.TestCase):
    origin = "https://test.supabase.co"
    headers = {"x-reels-api-key": "internal-test-key"}

    def setUp(self):
        env = patch.dict(os.environ, {
            "SUPABASE_URL": self.origin,
            "REELS_INTERNAL_API_KEY": "internal-test-key",
            "NEXT_BASE_URL": "",
        })
        env.start()
        self.addCleanup(env.stop)
        jobs = patch.object(job_store, "store", job_store.JobStore())
        jobs.start()
        self.addCleanup(jobs.stop)
        self.client = TestClient(api.app)

    def body(self):
        body = _render_request()
        for item in body["media"]:
            item["storage"] = "supabase"
            item["download_url"] = f"{self.origin}/storage/v1/object/sign/uploads/{item['key']}?token=test"
        root = f"{self.origin}/storage/v1/object/upload/sign/uploads/store/submission/generated/{body['ad_id']}"
        body["uploads"] = {"video_url": f"{root}.mp4?token=test", "thumbnail_url": f"{root}.jpg?token=test"}
        return body

    def test_requires_internal_auth_and_only_accepts_the_configured_storage_origin(self):
        body = self.body()
        self.assertEqual(self.client.post("/reels/renders", json=body).status_code, 401)
        body["media"][0]["download_url"] = body["media"][0]["download_url"].replace(self.origin, "https://other.test")
        self.assertEqual(self.client.post("/reels/renders", headers=self.headers, json=body).status_code, 400)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required")
    def test_render_uploads_video_with_music_and_thumbnail_and_reuses_completed_job(self):
        outputs = {}

        def storage(request):
            if request.method == "GET":
                return httpx.Response(200, content=(ASSETS_VIDEOS / request.url.path.split("/")[-1]).read_bytes())
            self.assertEqual(request.method, "PUT")
            outputs[request.url.path.split("/")[-1]] = request.read()
            return httpx.Response(200, json={})

        transfer = httpx.Client(transport=httpx.MockTransport(storage))
        body = self.body()
        with patch.object(api.httpx, "Client", return_value=transfer):
            accepted = self.client.post("/reels/renders", headers=self.headers, json=body)
            again = self.client.post("/reels/renders", headers=self.headers, json=body)
        self.assertEqual(accepted.status_code, 202, accepted.text)
        self.assertEqual(accepted.json()["render_id"], again.json()["render_id"])
        done = self.client.get(f"/reels/renders/{accepted.json()['render_id']}", headers=self.headers).json()
        self.assertEqual(done["status"], "done", done)
        self.assertNotIn("token=", json.dumps(done))
        video = outputs[f"{body['ad_id']}.mp4"]
        thumbnail = outputs[f"{body['ad_id']}.jpg"]
        self.assertEqual(done["result"]["video_bytes"], len(video))
        self.assertEqual(done["result"]["thumbnail_bytes"], len(thumbnail))
        self.assertEqual(thumbnail[:2], b"\xff\xd8")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "reel.mp4"
            path.write_bytes(video)
            streams = subprocess.check_output(
                ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name", "-of", "json", str(path)]
            )
            self.assertIn({"codec_name": "aac", "codec_type": "audio"}, json.loads(streams)["streams"])
            audio = subprocess.run(
                ["ffmpeg", "-hide_banner", "-i", str(path), "-vn", "-af", "volumedetect", "-f", "null", "-"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
            )
            mean = next(line for line in audio.stderr.splitlines() if "mean_volume:" in line)
            self.assertGreater(float(mean.split("mean_volume:")[1].split()[0]), -55.0)
