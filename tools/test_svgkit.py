"""Unit tests for svgkit. Run: python3 -m unittest discover -s tools"""
import re
import unittest
import xml.etree.ElementTree as ET

from svgkit import (
    Canvas,
    Choreographer,
    FrameStrip,
    Grid,
    display_width,
    esc,
    fmt,
    svg_document,
)

KEYFRAME = re.compile(r"([\d.]+)%\{([^}]*)\}")


def offsets(css: str):
    return [float(pct) for pct, _ in KEYFRAME.findall(css)]


class FormattingTest(unittest.TestCase):
    def test_fmt_trims_trailing_zeros_and_negative_zero(self):
        self.assertEqual(fmt(12.0), "12")
        self.assertEqual(fmt(8.40), "8.4")
        self.assertEqual(fmt(-0.0001), "0")
        self.assertEqual(fmt(1 / 3), "0.333")

    def test_esc_escapes_xml_special_characters(self):
        self.assertEqual(esc("<a & \"b\">'"), "&lt;a &amp; &quot;b&quot;&gt;&#39;")


class DisplayWidthTest(unittest.TestCase):
    def test_ascii_box_drawing_and_blocks_are_single_width(self):
        self.assertEqual(display_width("ab█╗·■"), 6)

    def test_cjk_is_double_width(self):
        self.assertEqual(display_width("白菜"), 4)


class GridTest(unittest.TestCase):
    def setUp(self):
        self.grid = Grid(x0=10, y0=20, line_h=16, char_w=8)

    def test_row_pins_text_length_to_the_monospace_grid(self):
        el = ET.fromstring(self.grid.row(1, "abc", cls="cy"))
        self.assertEqual(el.get("x"), "10")
        self.assertEqual(el.get("y"), "36")
        self.assertEqual(el.get("textLength"), "24")
        self.assertEqual(el.get("lengthAdjust"), "spacing")
        self.assertEqual(el.get("class"), "cy")

    def test_row_moves_leading_spaces_into_x_and_drops_trailing_spaces(self):
        el = ET.fromstring(self.grid.row(0, "  ab  "))
        self.assertEqual(el.get("x"), "26")
        self.assertEqual(el.get("textLength"), "16")
        self.assertEqual(el.text, "ab")

    def test_blank_row_renders_nothing(self):
        self.assertEqual(self.grid.row(0, "    "), "")
        self.assertEqual(self.grid.runs(0, [("cy", ""), ("", "  ")]), "")

    def test_row_escapes_markup(self):
        self.assertIn("&lt;|eos|&gt;", self.grid.row(0, "<|eos|>"))

    def test_runs_keep_spans_adjacent_so_no_stray_whitespace_renders(self):
        out = self.grid.runs(0, [("a", "ab"), ("b", "cd")])
        self.assertNotIn("> <", out)
        el = ET.fromstring(out)
        self.assertEqual("".join(el.itertext()), "abcd")
        self.assertEqual(el.get("textLength"), "32")

    def test_runs_trim_outer_spaces_across_span_boundaries(self):
        el = ET.fromstring(self.grid.runs(0, [("a", "  "), ("b", " xy"), ("", "  ")]))
        self.assertEqual(el.get("x"), "34")
        self.assertEqual("".join(el.itertext()), "xy")

    def test_cjk_counts_two_columns_in_text_length(self):
        el = ET.fromstring(self.grid.row(0, "a白"))
        self.assertEqual(el.get("textLength"), "24")


class CanvasTest(unittest.TestCase):
    def test_later_writes_win_and_rows_render_in_order(self):
        canvas = Canvas()
        canvas.text(0, 1, "abc", "mu")
        canvas.put(1, 1, "X", "cy")
        canvas.put(4, 0, "z")
        out = canvas.render(Grid(x0=0, y0=0, line_h=10, char_w=10))
        root = ET.fromstring(f"<g>{out}</g>")
        rows = [("".join(t.itertext()), t.get("y")) for t in root]
        self.assertEqual(rows, [("z", "0"), ("aXc", "10")])

    def test_rejects_wide_or_multi_character_cells(self):
        canvas = Canvas()
        with self.assertRaises(ValueError):
            canvas.put(0, 0, "白")
        with self.assertRaises(ValueError):
            canvas.put(0, 0, "ab")
        with self.assertRaises(ValueError):
            canvas.put(-1, 0, "a")


class ChoreographerTest(unittest.TestCase):
    def test_reveal_is_hidden_until_t_in_then_stays_visible(self):
        choreo = Choreographer(10)
        cls = choreo.reveal(2.0)
        self.assertEqual(cls, "an k0")
        css = choreo.css()
        self.assertIn("20%{opacity:0}", css)
        self.assertIn("21.2%{opacity:1}", css)
        self.assertIn("100%{opacity:1}", css)

    def test_windowed_reveal_is_transient_unless_persisted(self):
        choreo = Choreographer(10)
        self.assertIn("tr", choreo.reveal(1, t_out=2).split())
        self.assertNotIn("tr", choreo.reveal(1, t_out=2, persist=True).split())

    def test_keyframe_offsets_are_monotonic_and_bounded(self):
        choreo = Choreographer(7)
        choreo.reveal(0, t_out=6.5, fade=0.4, rise=3)
        choreo.wipe(1, 2, distance=80, steps=10, hide_at=3)
        choreo.staircase([(1, 0), (1.5, 16), (2, 40)])
        for block in re.findall(r"@keyframes \w+\{.*?\}\}", choreo.css()):
            found = offsets(block)
            self.assertEqual(found, sorted(found), block)
            self.assertEqual(found[0], 0)
            self.assertEqual(found[-1], 100)

    def test_wipe_steps_from_zero_to_distance(self):
        choreo = Choreographer(10)
        choreo.wipe(1, 2, distance=80, steps=10)
        css = choreo.css()
        self.assertIn("translateX(0px)", css)
        self.assertIn("steps(10,end)", css)
        self.assertIn("translateX(80px)", css)

    def test_staircase_jumps_between_points(self):
        choreo = Choreographer(10)
        choreo.staircase([(1, 0), (2, 16)], axis="Y")
        css = choreo.css()
        self.assertIn("steps(1,end)", css)
        self.assertIn("translateY(16px)", css)

    def test_flicker_opens_several_short_transient_windows(self):
        choreo = Choreographer(10)
        cls = choreo.flicker([(1, 1.1), (5, 5.2)], peak=0.8)
        self.assertIn("tr", cls.split())
        css = choreo.css()
        self.assertEqual(css.count("opacity:0.8"), 4)
        with self.assertRaises(ValueError):
            Choreographer(10).flicker([(5, 5.2), (1, 1.1)])

    def test_rejects_timelines_that_cannot_fit_the_cycle(self):
        choreo = Choreographer(10)
        with self.assertRaises(ValueError):
            choreo.reveal(10.5)
        with self.assertRaises(ValueError):
            choreo.reveal(3, t_out=2)
        with self.assertRaises(ValueError):
            choreo.wipe(2, 1, distance=10, steps=2)
        with self.assertRaises(ValueError):
            choreo.wipe(1, 2, distance=10, steps=0)
        with self.assertRaises(ValueError):
            choreo.staircase([(2, 0), (1, 5)])
        with self.assertRaises(ValueError):
            Choreographer(0)


class FrameStripTest(unittest.TestCase):
    def test_each_frame_owns_one_slot_of_the_period(self):
        strip = FrameStrip("px", frames=4, period=2)
        css = strip.css()
        self.assertIn("@keyframes px{0%{opacity:1}25%{opacity:0}100%{opacity:0}}", css)
        self.assertIn(".px-0{opacity:1}", css)
        self.assertIn(".px-1{animation-delay:-1.5s}", css)
        self.assertIn(".px-3{animation-delay:-0.5s}", css)
        self.assertEqual(strip.frame_class(2), "fr px px-2")

    def test_rejects_bad_configuration(self):
        with self.assertRaises(ValueError):
            FrameStrip("px", frames=0, period=1)
        with self.assertRaises(ValueError):
            FrameStrip("bad name", frames=2, period=1)
        with self.assertRaises(ValueError):
            FrameStrip("px", frames=2, period=1).frame_class(2)


class SvgDocumentTest(unittest.TestCase):
    def test_document_is_well_formed_accessible_and_space_preserving(self):
        doc = svg_document(100, 50, "t <1>", "d & e", css="text{fill:red}", body="<g/>")
        root = ET.fromstring(doc)
        self.assertEqual(root.tag, "{http://www.w3.org/2000/svg}svg")
        self.assertEqual(root.get("role"), "img")
        self.assertEqual(root.get("{http://www.w3.org/XML/1998/namespace}space"), "preserve")
        self.assertEqual(root.find("{http://www.w3.org/2000/svg}title").text, "t <1>")


if __name__ == "__main__":
    unittest.main()
