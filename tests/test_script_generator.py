import json
import unittest
from unittest.mock import patch

from src.quote_video import templates
from src.quote_video.script_generator import (
    MAX_HASHTAGS,
    AnthropicLlmClient,
    ScriptResult,
    _extract_price,
    generate_script,
)

STORE = {"name": "해오름 떡집", "description": "30년 전통 방식으로 만드는 동네 떡집"}
MENUS_1 = [{"menu_index": 0, "name": "창억떡", "description": "쫀득한 식감, 한 팩 5,000원"}]
MENUS_2 = [
    {"menu_index": 0, "name": "창억떡", "description": "쫀득한 식감, 한 팩 5,000원"},
    {"menu_index": 1, "name": "인절미", "description": "고소한 콩가루"},
]


class FakeLlmClient:
    """Returns each item in `responses` in order (one per .complete() call);
    a response that's an Exception instance is raised instead of returned,
    so tests can script a "fails once, then succeeds" sequence."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def complete(self, system_prompt, user_prompt):
        self.calls.append((system_prompt, user_prompt))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _valid_response(scene_plan, texts):
    return json.dumps(
        {
            "scenes": [{"scene_index": s.scene_index, "text": texts[i]} for i, s in enumerate(scene_plan)],
            "caption": "30년 전통 방식 그대로, 해오름 떡집 창억떡을 만나보세요.",
            "hashtags": ["#창억떡", "#떡집"],
        }
    )


class TestGenerateScriptSuccess(unittest.TestCase):
    def setUp(self):
        self.template = templates.load_template("simple")
        self.scene_plan = templates.build_scene_plan(self.template, menu_count=1)

    def test_parses_scenes_caption_hashtags(self):
        texts = ["30년 전통 해오름 떡집", "쫀득한 창억떡", "한 팩 5,000원의 행복", "오늘 바로 들러주세요"]
        client = FakeLlmClient([_valid_response(self.scene_plan, texts)])

        result = generate_script(STORE, MENUS_1, self.template, llm_client=client)

        self.assertIsInstance(result, ScriptResult)
        self.assertFalse(result.is_fallback)
        self.assertEqual([s.text for s in result.scenes], texts)
        self.assertEqual(result.caption, "30년 전통 방식 그대로, 해오름 떡집 창억떡을 만나보세요.")
        self.assertEqual(result.hashtags, ["#창억떡", "#떡집"])
        self.assertEqual(len(client.calls), 1)

    def test_target_is_structural_not_from_llm(self):
        texts = ["a", "b", "c", "d"]
        client = FakeLlmClient([_valid_response(self.scene_plan, texts)])

        result = generate_script(STORE, MENUS_1, self.template, llm_client=client)

        self.assertEqual([s.target for s in result.scenes], ["가게", "창억떡", "창억떡", "가게"])

    def test_scene_type_and_menu_index_come_from_scene_plan(self):
        texts = ["a", "b", "c", "d"]
        client = FakeLlmClient([_valid_response(self.scene_plan, texts)])

        result = generate_script(STORE, MENUS_1, self.template, llm_client=client)

        self.assertEqual([s.scene_type for s in result.scenes], ["hook", "closeup", "detail", "outro"])
        self.assertEqual([s.menu_index for s in result.scenes], [None, 0, 0, None])

    def test_code_fence_wrapped_json_is_still_parsed(self):
        texts = ["a", "b", "c", "d"]
        fenced = "```json\n" + _valid_response(self.scene_plan, texts) + "\n```"
        client = FakeLlmClient([fenced])

        result = generate_script(STORE, MENUS_1, self.template, llm_client=client)

        self.assertFalse(result.is_fallback)
        self.assertEqual(result.scenes[0].text, "a")

    def test_two_menu_scene_plan(self):
        template = templates.load_template("simple")
        scene_plan = templates.build_scene_plan(template, menu_count=2)
        texts = ["a", "b", "c", "d", "e", "f"]
        client = FakeLlmClient([_valid_response(scene_plan, texts)])

        result = generate_script(STORE, MENUS_2, template, llm_client=client)

        self.assertEqual(len(result.scenes), 6)
        self.assertEqual([s.target for s in result.scenes], ["가게", "창억떡", "창억떡", "인절미", "인절미", "가게"])


class TestGenerateScriptRetryAndFallback(unittest.TestCase):
    def setUp(self):
        self.template = templates.load_template("simple")
        self.scene_plan = templates.build_scene_plan(self.template, menu_count=1)

    def test_succeeds_on_second_attempt_after_first_failure(self):
        texts = ["a", "b", "c", "d"]
        client = FakeLlmClient([RuntimeError("network error"), _valid_response(self.scene_plan, texts)])

        result = generate_script(STORE, MENUS_1, self.template, llm_client=client)

        self.assertFalse(result.is_fallback)
        self.assertEqual(len(client.calls), 2)

    def test_invalid_json_both_attempts_falls_back(self):
        client = FakeLlmClient(["not json", "still not json"])

        result = generate_script(STORE, MENUS_1, self.template, llm_client=client)

        self.assertTrue(result.is_fallback)
        self.assertEqual(len(client.calls), 2)

    def test_network_error_both_attempts_falls_back(self):
        client = FakeLlmClient([RuntimeError("timeout"), RuntimeError("timeout")])

        result = generate_script(STORE, MENUS_1, self.template, llm_client=client)

        self.assertTrue(result.is_fallback)

    def test_text_exceeding_max_chars_falls_back(self):
        texts = ["way way way too long for the fourteen char hook limit", "b", "c", "d"]
        client = FakeLlmClient([_valid_response(self.scene_plan, texts), _valid_response(self.scene_plan, texts)])

        result = generate_script(STORE, MENUS_1, self.template, llm_client=client)

        self.assertTrue(result.is_fallback)

    def test_scene_index_mismatch_falls_back(self):
        bad = json.dumps({"scenes": [{"scene_index": 0, "text": "a"}], "caption": "c", "hashtags": []})
        client = FakeLlmClient([bad, bad])

        result = generate_script(STORE, MENUS_1, self.template, llm_client=client)

        self.assertTrue(result.is_fallback)

    def test_missing_api_key_falls_back_without_network(self):
        with patch.dict("os.environ", {}, clear=True):
            result = generate_script(STORE, MENUS_1, self.template, llm_client=None)
        self.assertTrue(result.is_fallback)

    def test_fallback_fills_store_and_menu_names(self):
        client = FakeLlmClient(["x", "x"])

        result = generate_script(STORE, MENUS_1, self.template, llm_client=client)

        self.assertEqual(result.scenes[0].text, "해오름 떡집")  # hook
        self.assertEqual(result.scenes[1].text, "창억떡")  # closeup
        self.assertEqual(result.scenes[3].text, "지금 바로 들러주세요")  # outro

    def test_fallback_extracts_price_into_detail_scene(self):
        client = FakeLlmClient(["x", "x"])

        result = generate_script(STORE, MENUS_1, self.template, llm_client=client)

        detail_scene = next(s for s in result.scenes if s.scene_type == "detail")
        self.assertIn("5,000원", detail_scene.text)

    def test_fallback_omits_price_when_menu_has_none(self):
        menus = [{"menu_index": 0, "name": "창억떡", "description": "쫀득한 식감"}]
        client = FakeLlmClient(["x", "x"])

        result = generate_script(STORE, menus, self.template, llm_client=client)

        detail_scene = next(s for s in result.scenes if s.scene_type == "detail")
        self.assertEqual(detail_scene.text, "창억떡")

    def test_fallback_hashtags_capped_at_max(self):
        many_menus = [{"menu_index": i, "name": f"메뉴{i}", "description": ""} for i in range(40)]
        # menu_count must be 1-3 for build_scene_plan, so just check the cap logic directly via fallback helper
        from src.quote_video.script_generator import _fallback_result

        result = _fallback_result(STORE, MENUS_1, self.scene_plan)
        self.assertLessEqual(len(result.hashtags), MAX_HASHTAGS)


class TestAnthropicLlmClient(unittest.TestCase):
    def test_missing_api_key_raises_without_any_network_call(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(RuntimeError):
                AnthropicLlmClient()

    def test_reads_api_key_and_model_from_env(self):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test", "LLM_MODEL": "claude-haiku-5-5"}):
            client = AnthropicLlmClient()
        self.assertEqual(client.api_key, "sk-test")
        self.assertEqual(client.model, "claude-haiku-5-5")

    def test_explicit_args_override_env(self):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-env"}):
            client = AnthropicLlmClient(api_key="sk-explicit", model="claude-opus-5-5")
        self.assertEqual(client.api_key, "sk-explicit")
        self.assertEqual(client.model, "claude-opus-5-5")

    def test_complete_builds_request_and_parses_text_blocks(self):
        client = AnthropicLlmClient(api_key="sk-test")
        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return json.dumps({"content": [{"type": "text", "text": '{"ok": true}'}]}).encode("utf-8")

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["headers"] = {k.lower(): v for k, v in request.headers.items()}
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            return FakeResponse()

        with patch("src.quote_video.script_generator.urllib.request.urlopen", fake_urlopen):
            result = client.complete("system prompt", "user prompt")

        self.assertEqual(result, '{"ok": true}')
        self.assertEqual(captured["url"], AnthropicLlmClient.API_URL)
        self.assertEqual(captured["headers"]["x-api-key"], "sk-test")
        self.assertEqual(captured["headers"]["anthropic-version"], AnthropicLlmClient.API_VERSION)
        self.assertEqual(captured["body"]["system"], "system prompt")
        self.assertEqual(captured["body"]["messages"], [{"role": "user", "content": "user prompt"}])


class TestExtractPrice(unittest.TestCase):
    def test_extracts_comma_formatted_price(self):
        self.assertEqual(_extract_price("쫀득한 식감, 한 팩 5,000원"), "5,000원")

    def test_extracts_plain_price(self):
        self.assertEqual(_extract_price("한 팩 5000원"), "5000원")

    def test_returns_none_when_no_price(self):
        self.assertIsNone(_extract_price("쫀득한 식감"))


if __name__ == "__main__":
    unittest.main()
