"""Tests for the README scenes. Run: python3 -m unittest discover -s tools"""
import pathlib
import re
import unittest
import xml.etree.ElementTree as ET

import build_assets as ba

SVG = "{http://www.w3.org/2000/svg}"
REPO = pathlib.Path(__file__).resolve().parents[1]


class BannerTest(unittest.TestCase):
    def test_rows_share_one_width(self):
        rows = ba.banner("WHITECCINN")
        self.assertEqual(len(rows), 6)
        self.assertEqual(len({len(row) for row in rows}), 1)

    def test_unknown_glyph_is_rejected_with_a_clear_error(self):
        with self.assertRaisesRegex(ValueError, "no banner glyph"):
            ba.banner("Q")


class RasterizeTest(unittest.TestCase):
    def assert_contiguous(self, cells, start, end):
        self.assertEqual(cells[0], start)
        self.assertEqual(cells[-1], end)
        for (c0, r0), (c1, r1) in zip(cells, cells[1:]):
            self.assertLessEqual(max(abs(c1 - c0), abs(r1 - r0)), 1)

    def test_shallow_steep_and_reverse_lines_are_contiguous(self):
        for start, end in [((0, 0), (9, 4)), ((0, 0), (3, 12)), ((9, 12), (0, 1)), ((2, 2), (2, 2))]:
            self.assert_contiguous(ba.rasterize(start, end), start, end)


class NetworkTest(unittest.TestCase):
    def setUp(self):
        self.net = ba.Network.default()

    def test_layers_are_fully_connected(self):
        sizes = [len(layer) for layer in self.net.layers]
        expected = sum(a * b for a, b in zip(sizes, sizes[1:]))
        self.assertEqual(len(self.net.edges), expected)

    def test_pulses_only_travel_on_the_mesh_or_through_nodes(self):
        centers = {cell for layer in self.net.layers for cell in layer}
        for frame in range(self.net.frames):
            for cell in self.net.pulse_heads(frame):
                self.assertTrue(cell in self.net.mesh or cell in centers, (frame, cell))

    def test_every_pulse_reaches_an_output_node(self):
        outputs = set(self.net.layers[-1])
        for path in self.net.paths:
            self.assertIn(path[-1], outputs)


class AttentionTest(unittest.TestCase):
    def test_heads_are_causal_probability_distributions(self):
        heads = ba.attention_heads()
        self.assertEqual(len(heads), 8)
        for head in heads:
            for i, row in enumerate(head.weights):
                self.assertAlmostEqual(sum(row), 1.0, places=6)
                self.assertTrue(all(w == 0 for w in row[i + 1:]), head.name)

    def test_cells_use_only_the_shading_ramp(self):
        allowed = set(ba.SHADES) | {" "}
        for head in ba.attention_heads():
            for i, row in enumerate(head.weights):
                for j, w in enumerate(row[: i + 1]):
                    self.assertIn(ba.shade(w), allowed)


class SamplerTest(unittest.TestCase):
    def test_every_token_has_three_strictly_ranked_candidates(self):
        for sentence in ba.sampler_plan():
            self.assertTrue(sentence.steps)
            for step in sentence.steps:
                probs = [p for _, p in step.candidates]
                self.assertEqual(len(probs), 3)
                self.assertEqual(step.candidates[0][0], ba.display_token(step.token))
                self.assertTrue(all(a > b for a, b in zip(probs, probs[1:])), step)
                self.assertLessEqual(sum(probs), 1.0 + 1e-9)


class StackTest(unittest.TestCase):
    def test_process_pids_are_unique_default_ports(self):
        pids = [proc.pid for proc in ba.PROCS]
        self.assertEqual(len(pids), len(set(pids)))
        self.assertEqual(dict((p.name, p.pid) for p in ba.PROCS)["redis"], 6379)

    def test_core_loads_stay_in_percent_range(self):
        for frame in range(ba.STACK_FRAMES):
            for load in ba.core_loads(frame):
                self.assertGreaterEqual(load, 0)
                self.assertLessEqual(load, 100)


class WrapTest(unittest.TestCase):
    def test_wrap_respects_width_and_keeps_every_word(self):
        text = "Implement all kafka protocols with high level and low level client apis"
        lines = ba.wrap(text, 20, max_lines=10)
        self.assertTrue(all(len(line) <= 20 for line in lines))
        self.assertEqual(" ".join(lines).split(), text.split())

    def test_wrap_ellipsizes_overflow(self):
        lines = ba.wrap("one two three four five six", 9, max_lines=2)
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[-1].endswith("…"))
        self.assertTrue(all(len(line) <= 9 for line in lines))


class GeneratedSvgTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.svgs = ba.render_all()

    def test_expected_assets_are_produced(self):
        for name in ("hero.svg", "brain.svg", "stack.svg", "stream.svg", "btn-blog.svg", "btn-mail.svg"):
            self.assertIn(name, self.svgs)
        self.assertEqual(len([n for n in self.svgs if n.startswith("repo-")]), len(ba.REPOS))

    def test_each_svg_is_well_formed_xml(self):
        for name, svg in self.svgs.items():
            with self.subTest(name=name):
                ET.fromstring(svg)

    def test_no_scripts_or_remote_resources(self):
        for name, svg in self.svgs.items():
            with self.subTest(name=name):
                lowered = svg.lower()
                for banned in ("<script", "foreignobject", "@import", "url(http", "href=\"http", "onload"):
                    self.assertNotIn(banned, lowered)

    def test_animated_assets_honour_reduced_motion(self):
        for name, svg in self.svgs.items():
            if "@keyframes" in svg:
                with self.subTest(name=name):
                    self.assertIn("prefers-reduced-motion", svg)

    def test_size_budget(self):
        for name, svg in self.svgs.items():
            with self.subTest(name=name):
                self.assertLess(len(svg.encode()), 160_000)

    def test_rendering_is_deterministic(self):
        self.assertEqual(ba.render_all(), self.svgs)

    def test_grid_text_never_overflows_the_canvas(self):
        for name, svg in self.svgs.items():
            if name == "stream.svg":  # intentionally wider: it scrolls
                continue
            root = ET.fromstring(svg)
            width = float(root.get("width"))
            for text in root.iter(f"{SVG}text"):
                if text.get("textLength") is None:
                    continue
                right = float(text.get("x")) + float(text.get("textLength"))
                with self.subTest(name=name, text="".join(text.itertext())):
                    self.assertLessEqual(right, width - 12)

    def test_ids_are_unique_within_each_svg(self):
        for name, svg in self.svgs.items():
            ids = re.findall(r'\sid="([^"]+)"', svg)
            with self.subTest(name=name):
                self.assertEqual(len(ids), len(set(ids)))

    def test_committed_assets_match_the_generator(self):
        for name, svg in self.svgs.items():
            with self.subTest(name=name):
                path = REPO / "assets" / name
                self.assertTrue(path.exists(), f"run python3 tools/build_assets.py ({name} missing)")
                self.assertEqual(path.read_text(encoding="utf-8"), svg, f"{name} is stale")


class ReadmeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.readme = (REPO / "README.md").read_text(encoding="utf-8")

    def test_every_local_asset_reference_exists(self):
        refs = re.findall(r'src="\./(assets/[^"]+)"', self.readme)
        self.assertTrue(refs)
        for ref in refs:
            self.assertTrue((REPO / ref).exists(), ref)

    def test_every_generated_asset_is_used(self):
        for name in ba.render_all():
            self.assertIn(f"./assets/{name}", self.readme)

    def test_no_dead_widgets_or_logo_icon_grids(self):
        for banned in ("github-readme-stats.vercel.app", "github-profile-trophy.vercel.app",
                       "devicons/devicon", "skillicons.dev", "vectorlogo.zone"):
            self.assertNotIn(banned, self.readme)


if __name__ == "__main__":
    unittest.main()
