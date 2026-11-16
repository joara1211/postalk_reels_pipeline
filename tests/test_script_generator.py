import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.quote_video import templates
from src.quote_video.script_generator import (
    MAX_HASHTAGS,
    AnthropicLlmClient,
    GeminiLlmClient,
    OpenAiLlmClient,
    ScriptResult,
    default_llm_client,
    _build_prompt,
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

    def complete(self, system_prompt, user_prompt, *, schema=None):
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

    def test_overlong_scene_is_retried_with_feedback_then_fixed(self):
        too_long = ["30년 전통 방식 그대로 빚는 해오름 떡집", "b", "c", "d"]
        fixed = ["30년 전통 해오름 떡집", "b", "c", "d"]
        client = FakeLlmClient([_valid_response(self.scene_plan, too_long), _valid_response(self.scene_plan, fixed)])

        result = generate_script(STORE, MENUS_1, self.template, llm_client=client)

        self.assertFalse(result.is_fallback)
        self.assertEqual(result.scenes[0].text, "30년 전통 해오름 떡집")
        self.assertEqual(len(client.calls), 2)
        self.assertIn("scene_index=0", client.calls[1][1])  # retry names the overlong scene

    def test_scene_still_too_long_after_retry_gets_default_only_for_that_scene(self):
        texts = ["way way way too long for the fourteen char hook limit", "쫀득한 창억떡", "c", "d"]
        client = FakeLlmClient([_valid_response(self.scene_plan, texts), _valid_response(self.scene_plan, texts)])

        result = generate_script(STORE, MENUS_1, self.template, llm_client=client)

        self.assertFalse(result.is_fallback)
        self.assertEqual(result.scenes[0].text, "해오름 떡집")  # default for the hook only
        self.assertEqual(result.scenes[1].text, "쫀득한 창억떡")  # LLM text kept
        self.assertTrue(all(len(s.text) <= s.max_chars for s in result.scenes))

    def test_emoji_and_symbols_are_stripped_from_subtitles(self):
        texts = ["\U0001F370 해오름 떡집 ★", '"쫀득한 창억떡"', "c", "d"]
        client = FakeLlmClient([_valid_response(self.scene_plan, texts)])

        result = generate_script(STORE, MENUS_1, self.template, llm_client=client)

        self.assertEqual(result.scenes[0].text, "해오름 떡집")
        self.assertEqual(result.scenes[1].text, "쫀득한 창억떡")

    def test_hashtags_are_normalized_and_deduplicated(self):
        body = json.loads(_valid_response(self.scene_plan, ["a", "b", "c", "d"]))
        body["hashtags"] = ["#창억떡", "창억떡", "#동네 맛집", "", "#떡집"]
        client = FakeLlmClient([json.dumps(body)])

        result = generate_script(STORE, MENUS_1, self.template, llm_client=client)

        self.assertEqual(result.hashtags, ["#창억떡", "#동네맛집", "#떡집"])

    def test_empty_hashtags_are_filled_with_defaults(self):
        scene_plan = templates.build_scene_plan(self.template, menu_count=1)
        raw = json.loads(_valid_response(scene_plan, ["해오름 떡집", "창억떡", "쫀득한 창억떡", "지금 들러보세요"]))
        raw["hashtags"] = []
        result = generate_script(STORE, MENUS_1, self.template, llm_client=FakeLlmClient([json.dumps(raw)]))
        self.assertGreater(len(result.hashtags), 0)
        self.assertTrue(all(tag.startswith("#") and " " not in tag for tag in result.hashtags))

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
        self.assertEqual(result.scenes[3].text, "해오름 떡집에서 만나요")  # outro

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
        self.assertEqual(detail_scene.text, "쫀득한 식감")  # uses the description, not the menu name again

    def test_fallback_uses_store_and_menu_descriptions_in_caption(self):
        client = FakeLlmClient(["x", "x"])

        result = generate_script(STORE, MENUS_1, self.template, llm_client=client)

        self.assertIn("30년 전통 방식으로 만드는 동네 떡집", result.caption)
        self.assertIn("쫀득한 식감, 한 팩 5,000원", result.caption)
        self.assertEqual(result.hashtags, ["#해오름떡집", "#창억떡"])

    def test_fallback_handles_empty_descriptions_and_long_names(self):
        store = {"name": "아주아주아주 긴 이름의 동네 분식집", "description": ""}
        menus = [{"menu_index": 0, "name": "떡볶이", "description": ""}]
        client = FakeLlmClient(["x", "x"])

        result = generate_script(store, menus, self.template, llm_client=client)

        self.assertTrue(all(0 < len(s.text) <= s.max_chars for s in result.scenes))

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

    def test_default_model_is_used_when_env_unset(self):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}, clear=True):
            client = AnthropicLlmClient()
        self.assertEqual(client.model, AnthropicLlmClient.DEFAULT_MODEL)

    def test_complete_sends_schema_and_fallbacks_and_joins_text_blocks(self):
        client = AnthropicLlmClient(api_key="sk-test")
        captured = {}

        def fake_create(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                stop_reason="end_turn",
                content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text='{"ok": true}')],
            )

        with patch.object(client._client.beta.messages, "create", fake_create):
            result = client.complete("system prompt", "user prompt", schema={"type": "object"})

        self.assertEqual(result, '{"ok": true}')
        self.assertEqual(captured["system"], "system prompt")
        self.assertEqual(captured["messages"], [{"role": "user", "content": "user prompt"}])
        self.assertEqual(captured["output_config"]["format"], {"type": "json_schema", "schema": {"type": "object"}})
        self.assertEqual(captured["fallbacks"], "default")
        self.assertIn(AnthropicLlmClient.FALLBACK_BETA, captured["betas"])

    def test_refusal_or_truncation_raises(self):
        client = AnthropicLlmClient(api_key="sk-test")
        for reason in ("refusal", "max_tokens"):
            response = SimpleNamespace(stop_reason=reason, content=[])
            with patch.object(client._client.beta.messages, "create", lambda **_: response):
                with self.assertRaises(RuntimeError):
                    client.complete("s", "u")


class TestGeminiLlmClient(unittest.TestCase):
    def test_missing_api_key_raises(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(RuntimeError):
                GeminiLlmClient()

    def test_reads_key_and_default_model(self):
        with patch.dict("os.environ", {"GEMINI_API_KEY": "g-test"}, clear=True):
            client = GeminiLlmClient()
        self.assertEqual(client.api_key, "g-test")
        self.assertEqual(client.model, GeminiLlmClient.DEFAULT_MODEL)

    def test_complete_sends_system_prompt_and_json_schema(self):
        client = GeminiLlmClient(api_key="g-test")
        captured = {}

        def fake_generate(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(text='{"ok": true}', candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name="STOP"))])

        with patch.object(client._client.models, "generate_content", fake_generate):
            result = client.complete("system prompt", "user prompt", schema={"type": "object"})

        self.assertEqual(result, '{"ok": true}')
        self.assertEqual(captured["contents"], "user prompt")
        self.assertEqual(captured["config"].system_instruction, "system prompt")
        self.assertEqual(captured["config"].response_mime_type, "application/json")
        self.assertEqual(captured["config"].response_json_schema, {"type": "object"})

    def test_truncated_or_blocked_response_raises(self):
        client = GeminiLlmClient(api_key="g-test")
        response = SimpleNamespace(text="", candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name="MAX_TOKENS"))])
        with patch.object(client._client.models, "generate_content", lambda **_: response):
            with self.assertRaises(RuntimeError):
                client.complete("s", "u")


class _FakeHttpResponse:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self._payload).encode("utf-8")


class TestOpenAiLlmClient(unittest.TestCase):
    def test_missing_api_key_raises_without_any_network_call(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(RuntimeError):
                OpenAiLlmClient()

    def test_reads_key_and_model_from_env(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test", "OPENAI_MODEL": "gpt-4.1"}, clear=True):
            client = OpenAiLlmClient()
        self.assertEqual(client.api_key, "sk-test")
        self.assertEqual(client.model, "gpt-4.1")

    def test_default_model_when_env_unset(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}, clear=True):
            self.assertEqual(OpenAiLlmClient().model, OpenAiLlmClient.DEFAULT_MODEL)

    def test_complete_sends_strict_schema_and_reads_output_items(self):
        client = OpenAiLlmClient(api_key="sk-test")
        captured = {}
        payload = {
            "status": "completed",
            "output": [
                {"type": "reasoning", "summary": []},
                {"type": "message", "content": [{"type": "output_text", "text": '{"ok": true}'}]},
            ],
        }

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["headers"] = {k.lower(): v for k, v in request.header_items()}
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return _FakeHttpResponse(payload)

        schema = {"type": "object"}
        with patch("urllib.request.urlopen", fake_urlopen):
            result = client.complete("system prompt", "user prompt", schema=schema)

        self.assertEqual(result, '{"ok": true}')
        self.assertEqual(captured["url"], OpenAiLlmClient.API_URL)
        self.assertEqual(captured["headers"]["authorization"], "Bearer sk-test")
        self.assertEqual(captured["body"]["instructions"], "system prompt")
        self.assertEqual(captured["body"]["input"], "user prompt")
        fmt = captured["body"]["text"]["format"]
        self.assertEqual((fmt["type"], fmt["strict"], fmt["schema"]), ("json_schema", True, schema))

    def test_incomplete_or_refused_or_empty_response_raises(self):
        client = OpenAiLlmClient(api_key="sk-test")
        bad_payloads = [
            {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}, "output": []},
            {"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal", "refusal": "no"}]}]},
            {"status": "completed", "output": []},
        ]
        for payload in bad_payloads:
            with patch("urllib.request.urlopen", lambda request, timeout, p=payload: _FakeHttpResponse(p)):
                with self.assertRaises(RuntimeError):
                    client.complete("s", "u")


class TestDefaultLlmClient(unittest.TestCase):
    def test_openai_when_openai_key_is_set(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "o", "ANTHROPIC_API_KEY": "a", "GEMINI_API_KEY": "g"}, clear=True):
            self.assertIsInstance(default_llm_client(), OpenAiLlmClient)

    def test_unknown_provider_raises(self):
        with patch.dict("os.environ", {"LLM_PROVIDER": "nope"}, clear=True):
            with self.assertRaises(RuntimeError):
                default_llm_client()
    def test_gemini_when_only_gemini_key_is_set(self):
        with patch.dict("os.environ", {"GEMINI_API_KEY": "g"}, clear=True):
            self.assertIsInstance(default_llm_client(), GeminiLlmClient)

    def test_anthropic_when_anthropic_key_is_set(self):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "a", "GEMINI_API_KEY": "g"}, clear=True):
            self.assertIsInstance(default_llm_client(), AnthropicLlmClient)

    def test_llm_provider_overrides_key_detection(self):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "a", "GEMINI_API_KEY": "g", "LLM_PROVIDER": "gemini"}, clear=True):
            self.assertIsInstance(default_llm_client(), GeminiLlmClient)

    def test_generate_script_uses_gemini_from_env(self):
        template = templates.load_template("simple")
        scene_plan = templates.build_scene_plan(template, menu_count=1)
        fake = FakeLlmClient([_valid_response(scene_plan, ["a", "b", "c", "d"])])
        with patch("src.quote_video.script_generator.default_llm_client", return_value=fake):
            result = generate_script(STORE, MENUS_1, template)
        self.assertFalse(result.is_fallback)
        self.assertEqual(len(fake.calls), 1)


class TestPrompt(unittest.TestCase):
    def test_prompt_contains_user_input_and_per_scene_limits(self):
        template = templates.load_template("simple")
        scene_plan = templates.build_scene_plan(template, menu_count=1)

        system_prompt, user_prompt = _build_prompt(STORE, MENUS_1, scene_plan)

        self.assertIn("해오름 떡집", user_prompt)
        self.assertIn("30년 전통 방식으로 만드는 동네 떡집", user_prompt)
        self.assertIn("쫀득한 식감, 한 팩 5,000원", user_prompt)
        for scene in scene_plan:
            self.assertIn(f"scene_index={scene.scene_index}", user_prompt)
            self.assertIn(f"최대 {scene.caption_max_chars}자", user_prompt)
        self.assertIn("이모지", system_prompt)

    def test_empty_description_is_marked_not_blank(self):
        template = templates.load_template("simple")
        scene_plan = templates.build_scene_plan(template, menu_count=1)
        menus = [{"menu_index": 0, "name": "떡볶이", "description": ""}]

        _, user_prompt = _build_prompt({"name": "분식집", "description": ""}, menus, scene_plan)

        self.assertIn("(설명 없음)", user_prompt)


class TestExtractPrice(unittest.TestCase):
    def test_extracts_comma_formatted_price(self):
        self.assertEqual(_extract_price("쫀득한 식감, 한 팩 5,000원"), "5,000원")

    def test_extracts_plain_price(self):
        self.assertEqual(_extract_price("한 팩 5000원"), "5000원")

    def test_returns_none_when_no_price(self):
        self.assertIsNone(_extract_price("쫀득한 식감"))


if __name__ == "__main__":
    unittest.main()
