import unittest

from fastapi.testclient import TestClient

from src.quote_video.api import app

client = TestClient(app)


class TestHealthz(unittest.TestCase):
    def test_ok(self):
        resp = client.get("/healthz")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok"})


class TestGenerateValidation(unittest.TestCase):
    def _body(self, **overrides):
        body = {
            "video_url": "https://example.com/source.mp4",
            "caption_markdown": "## [1]\n* **1:** hi\n",
            "bgm_url": "https://example.com/bgm.mp3",
        }
        body.update(overrides)
        return body

    def test_rejects_neither_video_field(self):
        body = self._body()
        del body["video_url"]
        resp = client.post("/generate", json=body)
        self.assertEqual(resp.status_code, 422)

    def test_rejects_both_video_fields(self):
        body = self._body(video_urls=["https://example.com/1.mp4"])
        resp = client.post("/generate", json=body)
        self.assertEqual(resp.status_code, 422)

    def test_rejects_missing_required_field(self):
        body = self._body()
        del body["bgm_url"]
        resp = client.post("/generate", json=body)
        self.assertEqual(resp.status_code, 422)


class TestApiKey(unittest.TestCase):
    def test_rejects_wrong_key_when_configured(self):
        import src.quote_video.api as api_module

        original = api_module._API_KEY
        api_module._API_KEY = "secret"
        try:
            resp = client.get("/healthz")  # healthz has no auth check by design
            self.assertEqual(resp.status_code, 200)

            body = {
                "video_url": "https://example.com/source.mp4",
                "caption_markdown": "## [1]\n* **1:** hi\n",
                "bgm_url": "https://example.com/bgm.mp3",
            }
            resp = client.post("/generate", json=body, headers={"x-api-key": "wrong"})
            self.assertEqual(resp.status_code, 401)
        finally:
            api_module._API_KEY = original


if __name__ == "__main__":
    unittest.main()
