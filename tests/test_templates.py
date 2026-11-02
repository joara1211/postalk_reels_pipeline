import unittest

from src.quote_video import config, templates


class TestLoadTemplate(unittest.TestCase):
    def test_loads_simple_json(self):
        template = templates.load_template("simple")
        self.assertEqual(template["concept"], "simple")
        self.assertTrue(template["has_captions"])

    def test_missing_concept_raises(self):
        with self.assertRaises(FileNotFoundError):
            templates.load_template("does_not_exist")

    def test_render_block_holds_design_values_not_code(self):
        template = templates.load_template("simple")
        render = template["render"]
        self.assertIn("subtitle", render)
        self.assertIn("font_name", render["subtitle"])
        self.assertIn("margin_v", render["subtitle"])
        self.assertIn("fade_sec", render)


class TestBuildScenePlan(unittest.TestCase):
    def setUp(self):
        self.template = templates.load_template("simple")

    def test_one_menu_matches_spec_table(self):
        scenes = templates.build_scene_plan(self.template, menu_count=1)
        self.assertEqual([s.scene_type for s in scenes], ["hook", "closeup", "detail", "outro"])
        self.assertEqual([s.menu_index for s in scenes], [None, 0, 0, None])
        self.assertAlmostEqual(sum(s.duration_sec for s in scenes), 13.0)

    def test_two_menus_matches_spec_table(self):
        scenes = templates.build_scene_plan(self.template, menu_count=2)
        self.assertEqual(
            [s.scene_type for s in scenes],
            ["hook", "closeup", "detail", "closeup", "detail", "outro"],
        )
        self.assertEqual([s.menu_index for s in scenes], [None, 0, 0, 1, 1, None])
        self.assertAlmostEqual(sum(s.duration_sec for s in scenes), 14.0)

    def test_three_menus_matches_spec_table(self):
        scenes = templates.build_scene_plan(self.template, menu_count=3)
        self.assertEqual(len(scenes), 8)
        self.assertAlmostEqual(sum(s.duration_sec for s in scenes), 15.0)

    def test_scene_index_is_sequential_from_zero(self):
        scenes = templates.build_scene_plan(self.template, menu_count=2)
        self.assertEqual([s.scene_index for s in scenes], list(range(6)))

    def test_menu_scene_durations_are_frame_aligned(self):
        fps = self.template["render"]["canvas"]["fps"]
        scenes = templates.build_scene_plan(self.template, menu_count=3)
        for scene in scenes:
            frames = scene.duration_sec * fps
            self.assertAlmostEqual(frames, round(frames))

    def test_invalid_menu_count_raises(self):
        with self.assertRaises(ValueError):
            templates.build_scene_plan(self.template, menu_count=4)


if __name__ == "__main__":
    unittest.main()
