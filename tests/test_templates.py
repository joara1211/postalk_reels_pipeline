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

    def test_loads_christmas_json(self):
        template = templates.load_template("christmas")
        self.assertEqual(template["concept"], "christmas")
        self.assertTrue(template["has_captions"])
        self.assertIn("transition", template["render"])

    def test_christmas_scene_rule_matches_simple(self):
        # Scene structure (hook/closeup/detail/outro, durations) is shared
        # across concepts per CLAUDE.md — only render/design values differ.
        simple = templates.load_template("simple")
        christmas = templates.load_template("christmas")
        self.assertEqual(simple["scene_rule"], christmas["scene_rule"])


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


class TestRebalanceMenuSceneDurations(unittest.TestCase):
    def setUp(self):
        self.template = templates.load_template("simple")
        self.fps = self.template["render"]["canvas"]["fps"]

    def test_longer_footage_gets_more_time_shorter_gets_less(self):
        # 1 menu: closeup(1)=9s budget split 4.5/4.5 by default. closeup
        # filmed much longer than detail -> closeup should end up bigger.
        scenes = templates.build_scene_plan(self.template, menu_count=1)
        closeup = next(s for s in scenes if s.scene_type == "closeup")
        detail = next(s for s in scenes if s.scene_type == "detail")
        probed = {closeup.scene_index: 8.0, detail.scene_index: 2.0}

        rebalanced = templates.rebalance_menu_scene_durations(scenes, probed, self.fps)
        new_closeup = next(s for s in rebalanced if s.scene_index == closeup.scene_index)
        new_detail = next(s for s in rebalanced if s.scene_index == detail.scene_index)

        self.assertGreater(new_closeup.duration_sec, 4.5)
        self.assertLess(new_detail.duration_sec, 4.5)

    def test_menu_total_is_preserved_exactly(self):
        scenes = templates.build_scene_plan(self.template, menu_count=1)
        original_menu_total = sum(s.duration_sec for s in scenes if s.menu_index is not None)
        probed = {s.scene_index: 7.3 for s in scenes if s.menu_index is not None}

        rebalanced = templates.rebalance_menu_scene_durations(scenes, probed, self.fps)
        new_menu_total = sum(s.duration_sec for s in rebalanced if s.menu_index is not None)

        self.assertAlmostEqual(new_menu_total, original_menu_total)

    def test_overall_total_still_matches_the_spec_table(self):
        scenes = templates.build_scene_plan(self.template, menu_count=2)
        probed = {s.scene_index: 1.0 for s in scenes if s.menu_index is not None}

        rebalanced = templates.rebalance_menu_scene_durations(scenes, probed, self.fps)

        self.assertAlmostEqual(sum(s.duration_sec for s in rebalanced), 14.0)

    def test_hook_and_outro_are_never_touched(self):
        scenes = templates.build_scene_plan(self.template, menu_count=1)
        probed = {s.scene_index: 99.0 for s in scenes}  # even if "probed" (shouldn't be used)

        rebalanced = templates.rebalance_menu_scene_durations(scenes, probed, self.fps)

        hook = next(s for s in rebalanced if s.scene_type == "hook")
        outro = next(s for s in rebalanced if s.scene_type == "outro")
        self.assertEqual(hook.duration_sec, 2.0)
        self.assertEqual(outro.duration_sec, 2.0)

    def test_extreme_footage_ratio_is_clamped_not_unbounded(self):
        scenes = templates.build_scene_plan(self.template, menu_count=1)
        closeup = next(s for s in scenes if s.scene_type == "closeup")
        detail = next(s for s in scenes if s.scene_type == "detail")
        probed = {closeup.scene_index: 1000.0, detail.scene_index: 0.1}

        rebalanced = templates.rebalance_menu_scene_durations(scenes, probed, self.fps)
        new_closeup = next(s for s in rebalanced if s.scene_index == closeup.scene_index)
        new_detail = next(s for s in rebalanced if s.scene_index == detail.scene_index)

        self.assertLessEqual(new_closeup.duration_sec, 4.5 * 2.0)
        self.assertGreaterEqual(new_detail.duration_sec, 4.5 * 0.5)
        self.assertAlmostEqual(new_closeup.duration_sec + new_detail.duration_sec, 9.0)

    def test_missing_probed_duration_falls_back_to_equal_split(self):
        scenes = templates.build_scene_plan(self.template, menu_count=1)
        rebalanced = templates.rebalance_menu_scene_durations(scenes, {}, self.fps)
        for scene in rebalanced:
            if scene.menu_index is not None:
                self.assertAlmostEqual(scene.duration_sec, 4.5)

    def test_each_menu_keeps_its_own_budget_independent_of_other_menus(self):
        # menu 2's footage being very long shouldn't eat into menu 1's budget.
        scenes = templates.build_scene_plan(self.template, menu_count=2)
        menu0_scenes = [s for s in scenes if s.menu_index == 0]
        menu1_scenes = [s for s in scenes if s.menu_index == 1]
        probed = {s.scene_index: 0.1 for s in menu0_scenes}
        probed.update({s.scene_index: 100.0 for s in menu1_scenes})

        rebalanced = templates.rebalance_menu_scene_durations(scenes, probed, self.fps)
        menu0_total = sum(s.duration_sec for s in rebalanced if s.menu_index == 0)
        menu1_total = sum(s.duration_sec for s in rebalanced if s.menu_index == 1)

        self.assertAlmostEqual(menu0_total, 5.0)  # 2 menus -> 10s / 2 = 5s each
        self.assertAlmostEqual(menu1_total, 5.0)

    def test_frame_exact_across_the_whole_scene_list(self):
        fps = self.fps
        scenes = templates.build_scene_plan(self.template, menu_count=3)
        probed = {s.scene_index: (s.scene_index + 1) * 0.7 for s in scenes if s.menu_index is not None}

        rebalanced = templates.rebalance_menu_scene_durations(scenes, probed, fps)
        for scene in rebalanced:
            frames = scene.duration_sec * fps
            self.assertAlmostEqual(frames, round(frames))
        self.assertAlmostEqual(sum(s.duration_sec for s in rebalanced), 15.0)


if __name__ == "__main__":
    unittest.main()
