# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Pranay Kiran
"""Enlarged detail views (issue #2) — and above all: do they read TRUE?

Run with:  python3 -m unittest discover -s tests -v

A DIM always displays the distance between its own def points, so a detail
drawn at k times size can only read true millimetres through a DIMSTYLE
carrying DIMLFAC = 1/k. The tests that matter here do not take our word for
that: they ask the renderer, through `musa_plot --dump-dims`, which composes
each label exactly as the plot does (they skip when the harness is not
built).
"""

import math
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "samples"))

import common  # noqa: E402
import draw  # noqa: E402
import manifest as mf  # noqa: E402
import musa_writer as mw  # noqa: E402
import sample_sheet  # noqa: E402


def seg(a, b):
    return {"t": "seg", "a": list(a), "b": list(b)}


class ScaleTest(unittest.TestCase):
    """k = (detail scale) / (sheet scale), the whole arithmetic."""

    def factor(self, sheet, detail):
        return draw.detail_factor({"scale": sheet}, {"scale": detail})

    def test_issue_example(self):
        # the issue's own case: 2:1 detail on a 1:5 sheet -> 10x, DIMLFAC 0.1
        self.assertAlmostEqual(self.factor("1:5", "2:1"), 10.0)

    def test_more_scales(self):
        self.assertAlmostEqual(self.factor("1:1", "2:1"), 2.0)
        self.assertAlmostEqual(self.factor("1:2", "5:1"), 10.0)
        self.assertAlmostEqual(self.factor("1:10", "1:2"), 5.0)

    def test_caption_names_its_own_scale(self):
        self.assertEqual(draw.detail_caption("2:1", "B"), "DETAIL B (2:1)")


class ClipTest(unittest.TestCase):
    """The region is cut out of the parent view's own primitives."""

    def test_segment_crossing_the_boundary(self):
        got = draw.clip_seg((-10.0, 0.0), (10.0, 0.0), (0.0, 0.0), 4.0)
        self.assertAlmostEqual(got[0][0], -4.0)
        self.assertAlmostEqual(got[1][0], 4.0)

    def test_segment_wholly_inside_is_untouched(self):
        got = draw.clip_seg((-1.0, 0.0), (1.0, 0.0), (0.0, 0.0), 4.0)
        self.assertAlmostEqual(got[0][0], -1.0)
        self.assertAlmostEqual(got[1][0], 1.0)

    def test_segment_outside_is_dropped(self):
        self.assertIsNone(draw.clip_seg((5.0, 5.0), (9.0, 9.0),
                                        (0.0, 0.0), 4.0))
        # a chord that only grazes the circle is not ink worth keeping
        self.assertIsNone(draw.clip_seg((-10.0, 4.0), (10.0, 4.0),
                                        (0.0, 0.0), 4.0))

    def test_circle_wholly_inside_stays_a_circle(self):
        prims = draw.clip_prims([{"t": "circle", "c": [0.0, 0.0], "r": 1.0}],
                                [0.0, 0.0], 4.0)
        self.assertEqual([p["t"] for p in prims], ["circle"])

    def test_circle_crossing_the_boundary_becomes_an_arc(self):
        # a circle centred ON the boundary: half of it is inside
        prims = draw.clip_prims([{"t": "circle", "c": [4.0, 0.0], "r": 2.0}],
                                [0.0, 0.0], 4.0)
        self.assertEqual([p["t"] for p in prims], ["arc"])
        a0, a1 = prims[0]["a0"], prims[0]["a1"]
        self.assertGreater(a1 - a0, 0.0)
        self.assertLess(a1 - a0, 2.0 * math.pi)
        # the surviving arc faces the centre of the detail circle
        mid = (a0 + a1) / 2.0
        self.assertAlmostEqual(math.cos(mid), -1.0, places=6)

    def test_circle_swallowing_the_detail_leaves_nothing(self):
        # the clip circle sits well inside a big bore: its circumference
        # never enters the region, so no ink belongs to the detail
        self.assertEqual(draw.clip_prims(
            [{"t": "circle", "c": [0.0, 0.0], "r": 50.0}],
            [0.0, 0.0], 4.0), [])

    def test_polyline_is_clipped_leg_by_leg(self):
        poly = {"t": "poly", "pts": [[-10.0, 0.0], [10.0, 0.0], [10.0, 10.0]]}
        prims = draw.clip_prims([poly], [0.0, 0.0], 4.0)
        self.assertEqual([p["t"] for p in prims], ["seg"])
        self.assertAlmostEqual(prims[0]["a"][0], -4.0)

    def test_hatch_loop_is_clipped_to_the_circle(self):
        # a square swallowing the circle clips back to (about) the circle
        sq = [(-20.0, -20.0), (20.0, -20.0), (20.0, 20.0), (-20.0, 20.0)]
        poly = draw.clip_loop_to_circle(sq, (0.0, 0.0), 5.0)
        self.assertGreater(len(poly), 8)
        rr = [math.hypot(x, y) for x, y in poly]
        self.assertAlmostEqual(max(rr), 5.0, places=2)
        self.assertGreater(min(rr), 4.9)

    def test_hatch_loop_outside_is_dropped(self):
        sq = [(30.0, 30.0), (40.0, 30.0), (40.0, 40.0), (30.0, 40.0)]
        self.assertEqual(draw.clip_loop_to_circle(sq, (0.0, 0.0), 5.0), [])


class DetailJobTest(unittest.TestCase):
    def setUp(self):
        self.parent = {
            "visible": [seg((0.0, 0.0), (40.0, 0.0)),
                        {"t": "circle", "c": [30.0, 0.0], "r": 1.0}],
            "hidden": [],
            "hatch_groups": [{"outer": [[20.0, -5.0], [40.0, -5.0],
                                        [40.0, 5.0], [20.0, 5.0]],
                              "holes": []}],
            "bounds": [[0.0, -5.0], [40.0, 5.0]]}
        self.spec = {"label": "B", "scale": "2:1", "of": "front",
                     "center": [30.0, 0.0], "radius": 5.0}

    def test_bounds_are_the_circle(self):
        job = draw.detail_job(self.parent, self.spec, 2.0)
        self.assertEqual(job["bounds"], [[-10.0, -10.0], [10.0, 10.0]])

    def test_geometry_is_centred_and_enlarged(self):
        job = draw.detail_job(self.parent, self.spec, 2.0)
        circles = [p for p in job["visible"] if p["t"] == "circle"]
        self.assertEqual(len(circles), 1)
        # the feature at the region's centre lands on the origin, 2x bigger
        self.assertEqual(circles[0]["c"], [0.0, 0.0])
        self.assertEqual(circles[0]["r"], 2.0)
        edge = [p for p in job["visible"] if p["t"] == "seg"][0]
        self.assertAlmostEqual(edge["a"][0], -10.0)   # clipped at the circle
        self.assertAlmostEqual(edge["b"][0], 10.0)

    def test_hatch_follows(self):
        job = draw.detail_job(self.parent, self.spec, 2.0)
        self.assertEqual(len(job["hatch_groups"]), 1)
        pts = job["hatch_groups"][0]["outer"]
        self.assertTrue(all(math.hypot(x, y) <= 10.001 for x, y in pts))

    def test_a_region_that_caught_nothing(self):
        spec = dict(self.spec, center=[500.0, 500.0])
        self.assertEqual(draw.detail_job(self.parent, spec, 2.0)["visible"],
                         [])

    def test_dims_are_authored_in_the_parent_s_own_coordinates(self):
        specs = [{"view": "detail", "orient": "h", "a": [28.0, 0.0],
                  "b": [30.0, 0.0]},
                 {"view": "front", "orient": "h", "a": [0.0, 0.0],
                  "b": [9.0, 0.0]}]
        got = draw.detail_specs(specs, self.spec, 2.0)
        # the detail's dim moves into the enlarged frame...
        self.assertEqual(got[0]["a"], [-4.0, 0.0])
        self.assertEqual(got[0]["b"], [0.0, 0.0])
        # ...every other view's is left exactly as the manifest wrote it
        self.assertEqual(got[1]["a"], [0.0, 0.0])
        self.assertEqual(got[1]["b"], [9.0, 0.0])


class DimUnitsTest(unittest.TestCase):
    """The DIMUNITS record and the styles that carry it."""

    def test_record_shape(self):
        rec = mw.dimunits(3, 0.1)
        toks = rec.split()
        self.assertEqual(len(toks), 15)          # the parser demands exactly 15
        self.assertEqual(toks[0], "DIMUNITS")
        self.assertEqual(toks[1], "3")
        self.assertEqual(float(toks[5]), 0.1)    # lfac
        self.assertEqual(toks[-2:], ["-", "-"])  # empty post / apost

    def test_a_zero_factor_is_refused(self):
        for bad in (0.0, -1.0):
            with self.assertRaises(ValueError):
                mw.dimunits(0, bad)

    def test_styles_differ_by_factor(self):
        ctx = mw.SheetContext(1, 1)
        ctx.dimstyles = draw.sheet_dimstyles(ctx)
        plain = draw.dim_style_index(ctx)
        half = draw.dim_style_index(ctx, lfac=0.5)
        self.assertNotEqual(plain, half)
        self.assertEqual(plain, draw.dim_style_index(ctx))       # cached
        self.assertEqual(half, draw.dim_style_index(ctx, lfac=0.5))
        self.assertEqual(len(ctx.dimunits), 1)
        self.assertTrue(ctx.dimunits[0].startswith(f"DIMUNITS {half} "))

    def test_render_puts_dimunits_after_the_styles(self):
        ctx = mw.SheetContext(1, 1)
        ctx.dimstyles = draw.sheet_dimstyles(ctx)
        draw.dim_style_index(ctx, lfac=0.5)
        ctx.add(mw.line((0, 0), (1, 1)))
        lines = ctx.render().splitlines()
        last_style = max(i for i, l in enumerate(lines)
                         if l.startswith("DIMSTYLE"))
        units = [i for i, l in enumerate(lines) if l.startswith("DIMUNITS")]
        first_entity = min(i for i, l in enumerate(lines)
                           if l.startswith("LINE"))
        self.assertTrue(units)
        self.assertGreater(units[0], last_style)
        self.assertLess(units[-1], first_entity)


@unittest.skipUnless(common.MUSA_PLOT.exists(), "musa_plot not built")
class LinearFactorContractTest(unittest.TestCase):
    """What MusaCAD itself writes for a dimension under DIMLFAC.

    This is the fact the whole detail view rests on, so it is checked
    against the renderer, not against our arithmetic.
    """

    def labels(self, lfac):
        body = ["MUSACAD 14", "UNITS 4", "CURRENT 0", "LTSCALE 1",
                "LAYER 255 255 255 0 25 1 0 0 0",
                mw.dimstyle(3.0, 2.5, name="Plain"),
                mw.dimstyle(3.0, 2.5, name="Detail"),
                mw.dimunits(1, lfac),
                mw.line((0, 0), (100, 0)),
                mw.dim(0, (0, 0), (100, 0), (50, 20), style=0, precision=1),
                mw.dim(0, (0, 0), (100, 0), (50, -20), style=1, precision=1),
                "END"]
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "lfac.musa"
            f.write_text("\n".join(body) + "\n")
            return common.dump_dims(f)

    def test_the_def_points_are_never_faked(self):
        dims = self.labels(0.1)
        self.assertEqual(len(dims), 2)
        for d in dims:                      # same geometry, both of them
            self.assertAlmostEqual(d["measure"], 100.0)
        self.assertEqual(dims[0]["label"], "100.0")
        self.assertEqual(dims[1]["label"], "10.0")   # DIMLFAC 0.1

    def test_a_2_to_1_detail_on_a_1_to_1_sheet(self):
        self.assertEqual(self.labels(0.5)[1]["label"], "50.0")


@unittest.skipUnless(common.MUSA_PLOT.exists(), "musa_plot not built")
class SampleSheetTest(unittest.TestCase):
    """The sample sheet, end to end through the real composer."""

    info = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="fabpipe-detail-")
        cls.info = sample_sheet.build(Path(cls.tmp.name))
        cls.text = Path(cls.info["musa"]).read_text()
        cls.dims = common.dump_dims(cls.info["musa"])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_nothing_was_left_unplaced(self):
        self.assertEqual(self.info["layout"]["unresolved"], [])

    def test_the_detail_dims_read_true_millimetres(self):
        # the groove is 4 wide and 2 deep; the detail is 2:1 on a 1:1 sheet,
        # so its def points are 8 and 4 apart and the labels still read 4
        # and 2 — MusaCAD's own composition, not ours
        by_label = {d["label"]: d for d in self.dims}
        self.assertIn("4", by_label)
        self.assertIn("2", by_label)
        self.assertAlmostEqual(by_label["4"]["measure"], 8.0)
        self.assertAlmostEqual(by_label["2"]["measure"], 4.0)
        # and the sheet's own dims are untouched: 30 long, groove 8 down
        self.assertAlmostEqual(by_label["30"]["measure"], 30.0)
        self.assertAlmostEqual(by_label["8"]["measure"], 8.0)

    def test_detail_dims_use_a_factored_style_and_the_rest_do_not(self):
        styles = {d["label"]: d["style"] for d in self.dims}
        factored = {int(r.split()[1]) for r in self.text.splitlines()
                    if r.startswith("DIMUNITS")}
        self.assertTrue(factored)
        self.assertIn(styles["4"], factored)
        self.assertIn(styles["2"], factored)
        self.assertNotIn(styles["30"], factored)

    def test_the_caption_names_the_detail_and_its_scale(self):
        self.assertIn("DETAIL B (2:1)", self.text)

    def test_it_plots(self):
        with tempfile.TemporaryDirectory() as d:
            info = sample_sheet.plot(self.info, Path(d) / "s.pdf")
            self.assertEqual(info["scale"], "1:1")
            self.assertAlmostEqual(float(info["mm_per_unit"]), 1.0, places=6)


class DetailManifestTest(unittest.TestCase):
    """What /finalise refuses to lock."""

    def part(self, **over):
        p = {"file": "/p.FCStd", "scale": "1:1",
             "section": {"enabled": True, "label": "A"},
             "detail_view": {"label": "B", "scale": "2:1", "of": "section",
                             "center": [30.0, 20.0], "radius": 7.0}}
        p["detail_view"].update(over.pop("detail_view", {}))
        p.update(over)
        return p

    def test_a_good_one_passes(self):
        self.assertEqual(mf.detail_problems(self.part()), [])

    def test_no_detail_no_problem(self):
        self.assertEqual(mf.detail_problems({"file": "/p.FCStd"}), [])

    def test_parent_view_must_exist_on_the_sheet(self):
        bad = self.part(section={"enabled": False})
        self.assertIn("is not a view on this sheet",
                      " ".join(mf.detail_problems(bad)))

    def test_the_scale_must_enlarge(self):
        self.assertIn("does not enlarge", " ".join(
            mf.detail_problems(self.part(detail_view={"scale": "1:2"}))))
        self.assertIn("does not enlarge", " ".join(
            mf.detail_problems(self.part(detail_view={"scale": "1:1"}))))

    def test_the_scale_must_be_a_standard_one(self):
        self.assertIn("not a standard scale", " ".join(
            mf.detail_problems(self.part(detail_view={"scale": "3:1"}))))
        self.assertIn("bad detail_view.scale", " ".join(
            mf.detail_problems(self.part(detail_view={"scale": "2x"}))))

    def test_the_label_is_one_letter_and_not_the_section_s(self):
        self.assertIn("must be one letter", " ".join(
            mf.detail_problems(self.part(detail_view={"label": "B1"}))))
        # "A" is the section's letter: DETAIL A and SECTION A-A on one
        # sheet name two different things the same
        self.assertIn("already used on this sheet", " ".join(
            mf.detail_problems(self.part(detail_view={"label": "A"}))))

    def test_centre_and_radius(self):
        self.assertIn("detail_view.center", " ".join(
            mf.detail_problems(self.part(detail_view={"center": [1.0]}))))
        self.assertIn("radius must be > 0", " ".join(
            mf.detail_problems(self.part(detail_view={"radius": 0}))))


if __name__ == "__main__":
    unittest.main()
