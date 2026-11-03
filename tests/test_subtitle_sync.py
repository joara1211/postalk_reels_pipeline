import unittest
from pathlib import Path

from src.quote_video.subtitle_sync import (
    _strip_markdown,
    build_ass,
    build_srt,
    compute_timings,
    parse_caption_script,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sample_caption.md"
TIMESTAMPED_FIXTURE = Path(__file__).parent / "fixtures" / "sample_caption_timestamped.md"


class TestParseCaptionScript(unittest.TestCase):
    def test_parses_all_16_cuts_in_order(self):
        lines = parse_caption_script(FIXTURE)
        self.assertEqual(len(lines), 16)
        self.assertEqual([line.sequence_number for line in lines], list(range(1, 17)))
        self.assertEqual(lines[0].text, "포항중앙수산시장에 오시면")
        self.assertEqual(lines[-1].text, "지금 바로 입안 가득 바다를 즐겨보세요!")

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            parse_caption_script(Path("does/not/exist.md"))

    def test_inline_bold_markup_inside_cut_text_is_stripped(self):
        # cut 4 in the fixture: "* **4컷:** 상호명 **명품대게**를 꼭 방문해 보세요!"
        lines = parse_caption_script(FIXTURE)
        cut_4 = next(line for line in lines if line.sequence_number == 4)
        self.assertEqual(cut_4.text, "상호명 명품대게를 꼭 방문해 보세요!")
        self.assertNotIn("*", cut_4.text)

    def test_current_bracketed_timestamp_format_is_also_accepted(self):
        # "* **[00:00 ~ 00:01] 자막 1:** text" — the format actually in
        # assets/caption/ now (2026-08-20), distinct from the older
        # "**N컷:**" format above. The bracketed timestamp is decorative;
        # only the "자막 N" sequence number is read.
        lines = parse_caption_script(TIMESTAMPED_FIXTURE)
        self.assertEqual(len(lines), 16)
        self.assertEqual([line.sequence_number for line in lines], list(range(1, 17)))
        self.assertEqual(lines[0].text, "포항 여행의 완벽한 마침표")
        self.assertEqual(lines[-1].text, "놓치지 말고 지금 바로 즐겨보세요!")
        # the "## [비디오 컷 N]" section headers must not be mistaken for caption lines
        self.assertNotIn("비디오", " ".join(line.text for line in lines))


class TestStripMarkdown(unittest.TestCase):
    def test_strips_bold(self):
        self.assertEqual(_strip_markdown("상호명 **명품대게**를 꼭"), "상호명 명품대게를 꼭")

    def test_strips_italic_without_touching_bold(self):
        self.assertEqual(_strip_markdown("**bold** and *italic*"), "bold and italic")

    def test_strips_underscore_bold_and_backticks(self):
        self.assertEqual(_strip_markdown("__bold__ and `code`"), "bold and code")

    def test_strips_stray_leftover_markers(self):
        self.assertEqual(_strip_markdown("odd * marker"), "odd  marker")

    def test_plain_text_is_unchanged(self):
        self.assertEqual(_strip_markdown("포항중앙수산시장에 오시면"), "포항중앙수산시장에 오시면")


class TestComputeTimings(unittest.TestCase):
    def test_each_line_gets_a_one_second_window(self):
        lines = parse_caption_script(FIXTURE)
        timings = compute_timings(lines, caption_duration=1.0)

        self.assertEqual(timings[0].start, 0.0)
        self.assertEqual(timings[0].end, 1.0)
        self.assertEqual(timings[1].start, 1.0)
        self.assertEqual(timings[1].end, 2.0)
        self.assertEqual(timings[-1].start, 15.0)
        self.assertEqual(timings[-1].end, 16.0)


class TestBuildSrt(unittest.TestCase):
    def test_srt_format_and_timestamps(self):
        lines = parse_caption_script(FIXTURE)
        timings = compute_timings(lines)
        srt = build_srt(timings)

        self.assertTrue(srt.startswith("1\n00:00:00,000 --> 00:00:01,000\n"))
        self.assertIn("2\n00:00:01,000 --> 00:00:02,000\n", srt)
        self.assertIn("16\n00:00:15,000 --> 00:00:16,000\n", srt)
        self.assertEqual(srt.count("-->"), 16)


class TestBuildAss(unittest.TestCase):
    def test_play_res_matches_requested_canvas(self):
        lines = parse_caption_script(FIXTURE)
        timings = compute_timings(lines)
        ass = build_ass(timings, width=1080, height=1920)

        self.assertIn("PlayResX: 1080", ass)
        self.assertIn("PlayResY: 1920", ass)

    def test_margin_v_and_font_name_land_in_the_style_line(self):
        lines = parse_caption_script(FIXTURE)
        timings = compute_timings(lines)
        ass = build_ass(timings, font_name="Jayeon Sans Medium", margin_v=220)

        style_line = next(line for line in ass.splitlines() if line.startswith("Style: Default,"))
        self.assertIn("Jayeon Sans Medium", style_line)
        self.assertIn(",220,1", style_line)  # ...,MarginV,Encoding

    def test_dialogue_timestamps_and_text(self):
        lines = parse_caption_script(FIXTURE)
        timings = compute_timings(lines)
        ass = build_ass(timings)

        self.assertIn("Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,포항중앙수산시장에 오시면", ass)
        self.assertIn("Dialogue: 0,0:00:15.00,0:00:16.00,Default,,0,0,0,,지금 바로 입안 가득 바다를 즐겨보세요!", ass)
        self.assertEqual(ass.count("Dialogue:"), 16)

    def test_curly_braces_in_text_are_escaped(self):
        from src.quote_video.subtitle_sync import CaptionLine, CaptionTiming

        timing = CaptionTiming(line=CaptionLine(sequence_number=1, text="weird {tag} text"), start=0.0, end=1.0)
        ass = build_ass([timing])
        self.assertNotIn("{tag}", ass)
        self.assertIn("(tag)", ass)

    def test_box_style_params_from_template_land_in_the_style_line(self):
        # templates/<concept>.json's render.subtitle block drives these —
        # BorderStyle=3 + an opaque-ish BackColour draws a solid box
        # instead of a text outline (CLAUDE.md: "BorderStyle=3으로 박스형 가능").
        from src.quote_video.subtitle_sync import CaptionLine, CaptionTiming

        timing = CaptionTiming(line=CaptionLine(sequence_number=1, text="box"), start=0.0, end=1.0)
        ass = build_ass(
            [timing],
            font_size=48,
            primary_color="&H00112233",
            back_color="&H60445566",
            border_style=3,
            outline=0,
        )
        style_line = next(line for line in ass.splitlines() if line.startswith("Style: Default,"))
        fields = style_line.split(",")
        self.assertEqual(fields[2], "48")  # Fontsize
        self.assertEqual(fields[3], "&H00112233")  # PrimaryColour
        self.assertEqual(fields[6], "&H60445566")  # BackColour
        self.assertEqual(fields[15], "3")  # BorderStyle
        self.assertEqual(fields[16], "0")  # Outline

    def test_alternating_colors_wraps_each_character_with_an_inline_color_tag(self):
        from src.quote_video.subtitle_sync import CaptionLine, CaptionTiming

        timing = CaptionTiming(line=CaptionLine(sequence_number=1, text="가나다"), start=0.0, end=1.0)
        ass = build_ass([timing], alternating_colors=("46AA00", "1E1EDC"))

        dialogue_line = next(line for line in ass.splitlines() if line.startswith("Dialogue:"))
        self.assertIn(r"{\c&H46AA00&}가{\c&H1E1EDC&}나{\c&H46AA00&}다", dialogue_line)

    def test_alternating_colors_none_keeps_plain_text(self):
        from src.quote_video.subtitle_sync import CaptionLine, CaptionTiming

        timing = CaptionTiming(line=CaptionLine(sequence_number=1, text="가나다"), start=0.0, end=1.0)
        ass = build_ass([timing], alternating_colors=None)

        self.assertIn(",,가나다", ass)
        self.assertNotIn(r"\c&H", ass)

    def test_alternating_colors_applied_after_brace_escaping(self):
        from src.quote_video.subtitle_sync import CaptionLine, CaptionTiming

        timing = CaptionTiming(line=CaptionLine(sequence_number=1, text="a{b}c"), start=0.0, end=1.0)
        ass = build_ass([timing], alternating_colors=("46AA00", "1E1EDC"))

        self.assertNotIn("{b}", ass)
        self.assertIn(r"{\c&H46AA00&}a{\c&H1E1EDC&}(", ass)


if __name__ == "__main__":
    unittest.main()
