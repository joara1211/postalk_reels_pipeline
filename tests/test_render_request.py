import unittest
from pathlib import Path

from src.quote_video import templates
from src.quote_video.errors import CaptionTooLongError, SceneMismatchError
from src.quote_video.render_request import RenderRequest, build_render_plan

FIXTURE = Path(__file__).parent / "fixtures" / "render_request_1menu.json"


class TestRenderRequestFromJsonFile(unittest.TestCase):
    def test_parses_fixture(self):
        request = RenderRequest.from_json_file(FIXTURE)
        self.assertEqual(request.concept, "simple")
        self.assertEqual(len(request.menus), 1)
        self.assertEqual(len(request.media), 4)
        self.assertEqual(len(request.scenes), 4)


class TestBuildRenderPlan(unittest.TestCase):
    def setUp(self):
        self.template = templates.load_template("simple")
        self.request = RenderRequest.from_json_file(FIXTURE)

    def test_matches_media_and_text_to_scene_plan_in_order(self):
        render_scenes = build_render_plan(self.request, self.template)
        self.assertEqual([rs.scene.scene_type for rs in render_scenes], ["hook", "closeup", "detail", "outro"])
        self.assertEqual([rs.media_key for rs in render_scenes], ["1.mp4", "2.mp4", "3.mp4", "4.mp4"])
        self.assertEqual(render_scenes[0].text, "30년 전통 해오름 떡집")

    def test_caption_too_long_raises_with_code(self):
        self.request.scenes[0]["text"] = "너무 긴 문장입니다 열네글자를 훌쩍 넘어버리는 자막"
        with self.assertRaises(CaptionTooLongError) as ctx:
            build_render_plan(self.request, self.template)
        self.assertEqual(ctx.exception.code, "CAPTION_TOO_LONG")

    def test_missing_scene_in_media_raises_scene_mismatch(self):
        self.request.media.pop()
        with self.assertRaises(SceneMismatchError) as ctx:
            build_render_plan(self.request, self.template)
        self.assertEqual(ctx.exception.code, "SCENE_MISMATCH")

    def test_wrong_menu_index_raises_scene_mismatch(self):
        self.request.media[1]["menu_index"] = 1  # only menu_index 0 exists for a 1-menu request
        with self.assertRaises(SceneMismatchError):
            build_render_plan(self.request, self.template)

    def test_missing_scene_text_raises_scene_mismatch(self):
        self.request.scenes.pop()
        with self.assertRaises(SceneMismatchError):
            build_render_plan(self.request, self.template)


if __name__ == "__main__":
    unittest.main()
