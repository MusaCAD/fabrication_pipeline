# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Pranay Kiran
"""A leader keeps off the sheet's other ink (issue #25).

Run with:  python3 -m unittest discover -s tests -v

The real failures, both on sheets that passed every check:

* a part sheet at 1:1 whose diameter leader left the circle, turned, and
  ran straight through the cutting plane's sight arrow — two arrowheads
  meeting, so neither reads as pointing at anything;
* an assembly sheet whose balloon leaders ran across the overall-length
  dimension and the pictorial box on their way to the column, and whose
  callout column was narrower than its own note, which came out one word
  a line with its leader across the view to reach it.

The layout engine knew about views, leaders and text boxes. A dimension
LINE, a sight arrow and the pipeline's own pictorial frame were not
claims, so nothing was ever asked about them.
"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import draw  # noqa: E402
import layout_check as lc  # noqa: E402
import musa_writer as mw  # noqa: E402

A = mw.P_ANNO
AREA = (0.0, 0.0, 400.0, 400.0)


def seg(a, b):
    return {"t": "seg", "a": list(a), "b": list(b)}


def sheet(*recs):
    return "MUSACAD 14\n" + "\n".join(recs) + "\nEND\n"


def kinds(text, **kw):
    return [f["kind"] for f in lc.check_sheet(text, AREA, **kw)]


# ------------------------------------------------------- what the engine
#                                                          has to know of

class LayoutInkTest(unittest.TestCase):
    """The ink a leader may not cross is remembered as SEGMENTS, named
    so the fault can say what was in the way."""

    def setUp(self):
        self.ctx = mw.SheetContext(1, 1)
        self.ctx.dimstyles = draw.sheet_dimstyles(self.ctx)
        self.layout = draw.Layout(self.ctx)

    def test_a_dimension_claims_its_line_and_its_extensions(self):
        draw._claim_dim_ink(self.ctx, self.layout, (10.0, 10.0),
                            (90.0, 10.0), (50.0, 30.0), "h", "80")
        self.assertEqual(len(self.layout.dim_lines), 1)
        self.assertEqual(len(self.layout.ext_lines), 2)
        a, b, what = self.layout.dim_lines[0]
        self.assertAlmostEqual(a[1], 30.0)      # on the dim line, not the part
        self.assertAlmostEqual(b[1], 30.0)
        self.assertIn("80", what)

    def test_a_leader_across_the_dimension_line_is_named(self):
        draw._claim_dim_ink(self.ctx, self.layout, (10.0, 10.0),
                            (90.0, 10.0), (50.0, 30.0), "h", "80")
        what = self.layout.crossing_ink((50.0, 20.0), (50.0, 45.0))
        self.assertIsNotNone(what)
        self.assertIn("dimension", what)

    def test_a_leader_clear_of_it_is_not(self):
        draw._claim_dim_ink(self.ctx, self.layout, (10.0, 10.0),
                            (90.0, 10.0), (50.0, 30.0), "h", "80")
        self.assertIsNone(self.layout.crossing_ink((50.0, 35.0),
                                                   (120.0, 35.0)))

    def test_an_extension_line_is_asked_about_separately(self):
        draw._claim_dim_ink(self.ctx, self.layout, (10.0, 10.0),
                            (90.0, 10.0), (50.0, 30.0), "h", "80")
        # between the def point and the dim line, beside the left foot
        self.assertTrue(self.layout.crosses_extension((5.0, 20.0),
                                                      (20.0, 20.0)))
        self.assertIsNone(self.layout.crossing_ink((5.0, 20.0),
                                                   (20.0, 20.0)))

    def test_keep_off_carries_the_arrows_and_the_box(self):
        self.layout.keep_off.append((((40.0, 10.0)), (40.0, 60.0),
                                     "the cutting plane's arrow"))
        what = self.layout.crossing_ink((20.0, 30.0), (60.0, 30.0))
        self.assertEqual(what, "the cutting plane's arrow")

    def test_the_pictorial_box_puts_its_own_four_edges_there(self):
        """The frame is the pipeline's own furniture, so nothing else
        claims it as geometry — it has to say so itself."""
        draw.pictorial_box(self.ctx, self.layout)
        box = self.ctx._pictorial_box
        self.assertEqual(len(self.layout.keep_off), 4)
        self.assertEqual({w for _a, _b, w in self.layout.keep_off},
                         {"the pictorial box"})
        mid = (box[1] + box[3]) / 2.0
        self.assertEqual(self.layout.crossing_ink((box[0] - 10.0, mid),
                                                  (box[2] + 10.0, mid)),
                         "the pictorial box")
        self.assertIsNone(self.layout.crossing_ink((box[0] - 30.0,
                                                    box[1] - 20.0),
                                                   (box[0] - 5.0,
                                                    box[1] - 20.0)))


# -------------------------------------------- what the check reads back

class DimInkReadbackTest(unittest.TestCase):
    """Off the finished sheet, the check rebuilds the same two lines the
    engine claimed — it has only the DIM record to go on."""

    def ink(self, rec):
        return lc.dim_ink(lc.parse(sheet(rec)),
                          draw.EXT_OFFSET, draw.EXT_EXTENSION)

    def test_a_linear_dim_gives_one_line_and_two_extensions(self):
        a, b, lp = (10.0, 10.0), (90.0, 10.0), (50.0, 30.0)
        dims, exts = self.ink(mw.dim(0, a, b, lp))
        self.assertEqual(len(dims), 1)
        self.assertEqual(len(exts), 2)
        want_e, want_d = draw.dim_segments(a, b, lp, "h", draw.EXT_OFFSET,
                                           draw.EXT_EXTENSION)
        for got, wanted in zip(dims[0], want_d):
            self.assertAlmostEqual(got[0], wanted[0], places=4)
            self.assertAlmostEqual(got[1], wanted[1], places=4)
        for got, wanted in zip(sorted(exts), sorted(want_e)):
            for p, q in zip(got, wanted):
                self.assertAlmostEqual(p[0], q[0], places=4)
                self.assertAlmostEqual(p[1], q[1], places=4)

    def test_a_vertical_dim_too(self):
        dims, _e = self.ink(mw.dim(0, (10.0, 10.0), (10.0, 90.0),
                                   (30.0, 50.0)))
        self.assertAlmostEqual(dims[0][0][0], 30.0)
        self.assertAlmostEqual(dims[0][1][0], 30.0)

    def test_a_diameter_dim_is_its_own_line_with_no_extensions(self):
        dims, exts = self.ink(mw.dim(3, (0.0, -12.0), (0.0, 12.0),
                                     (20.0, 20.0)))
        self.assertEqual(len(dims), 1)
        self.assertEqual(exts, [])


class CutArrowTest(unittest.TestCase):
    """A sight arrow is the drawing's ink; a datum triangle is not."""

    def arrow(self, tip, w=4.0):
        tri = [tip, (tip[0] - w, tip[1] - w), (tip[0] + w, tip[1] - w)]
        return mw.hatch([tri], "SOLID")

    def test_the_arrow_is_read_with_the_shaft_behind_it(self):
        recs = lc.parse(sheet(self.arrow((40.0, 20.0)),
                              mw.line((40.0, 12.0), (40.0, -30.0))))
        got = lc.cut_arrows(recs)
        self.assertEqual(len(got), 1)
        tip, far = got[0]
        self.assertAlmostEqual(tip[1], 20.0)
        self.assertLess(far[1], 0.0, "the shaft was not followed back")

    def test_an_annotation_triangle_is_not_a_sight_arrow(self):
        tri = [(10.0, 10.0), (6.0, 6.0), (14.0, 6.0)]
        recs = lc.parse(sheet(mw.hatch([tri], "SOLID", props=A)))
        self.assertEqual(lc.cut_arrows(recs), [])


class CheckFindsItTest(unittest.TestCase):
    """Each of the three is a fault; an extension line is a warning."""

    DIM = mw.dim(0, (10.0, 100.0), (90.0, 100.0), (50.0, 130.0))

    def leader(self, tip, knee, text="%%c20 H7"):
        return mw.leader(tip, knee, text, 3.2, props=A)

    def test_a_leader_across_a_dimension_line(self):
        text = sheet(self.DIM, self.leader((50.0, 120.0), (50.0, 150.0)))
        found = lc.check_sheet(text, AREA)
        hit = [f for f in found if f["kind"] == "leader-across-dimension"]
        self.assertTrue(hit, [f["kind"] for f in found])
        self.assertIn("dimension line", hit[0]["what"])
        self.assertTrue([f for f in lc.faults(found)
                         if f["kind"] == "leader-across-dimension"])

    def test_a_leader_clear_of_it_is_silent(self):
        text = sheet(self.DIM, self.leader((50.0, 140.0), (120.0, 145.0)))
        self.assertNotIn("leader-across-dimension", kinds(text))

    def test_a_leader_through_the_sight_arrow(self):
        tri = [(40.0, 20.0), (36.0, 16.0), (44.0, 16.0)]
        text = sheet(mw.hatch([tri], "SOLID"),
                     mw.line((40.0, 16.0), (40.0, -30.0)),
                     self.leader((20.0, 0.0), (80.0, 0.0)))
        hit = [f for f in lc.check_sheet(text, AREA)
               if f["kind"] == "leader-across-dimension"]
        self.assertTrue(hit)
        self.assertIn("arrow", hit[0]["what"])

    def test_a_leader_that_only_clips_the_pictorial_box(self):
        """Across the corner, so almost none of it is under the stamp:
        the box is ink that belongs to something else."""
        box = (200.0, 200.0, 300.0, 300.0)
        text = sheet(self.leader((199.8, 190.0), (210.0, 200.2)))
        hit = [f for f in lc.check_sheet(text, AREA, boxes=[box])
               if f["kind"] == "leader-across-dimension"]
        self.assertTrue(hit)
        self.assertIn("pictorial", hit[0]["what"])
        # and the box is only ink when the sheet really has one
        self.assertNotIn("leader-across-dimension", kinds(text))

    def test_a_leader_under_the_pictorial_is_hidden_instead(self):
        """Through the middle: the stamped image is opaque, so that much
        of the leader is not printed at all (issue #30). It is the
        stronger finding and no waiver covers it, so the crossing is not
        reported as well."""
        box = (200.0, 200.0, 300.0, 300.0)
        text = sheet(self.leader((180.0, 250.0), (320.0, 250.0)))
        found = lc.check_sheet(text, AREA, boxes=[box])
        hit = [f for f in found if f["kind"] == "leader-hidden"]
        self.assertTrue(hit, [f["kind"] for f in found])
        self.assertIn("100.0 mm under the pictorial", hit[0]["what"])
        self.assertEqual(hit[0].get("waivable"), False)
        self.assertEqual(lc.unwaivable(found), hit)
        self.assertNotIn("leader-across-dimension",
                         [f["kind"] for f in found])

    def test_an_extension_line_is_a_fault_that_names_the_dimension(self):
        """It was a warning until v0.15.0 (issue #29): a crossing the
        engine now tries the feature's other edge to avoid, so one that
        is left is a fault — and it says WHICH dimension, so a waiver can
        name one crossing instead of a whole kind."""
        # beside the left foot, between the def point and the dim line
        text = sheet(self.DIM, self.leader((4.0, 115.0), (30.0, 115.0)))
        found = lc.check_sheet(text, AREA)
        hit = [f for f in found
               if f["kind"] == "leader-across-extension-line"]
        self.assertTrue(hit, [f["kind"] for f in found])
        self.assertIn("the 80 mm dimension", hit[0]["what"])
        self.assertEqual(lc.faults(found), hit)
        self.assertEqual(lc.warnings(found), [])

    def test_both_are_reported_when_a_leader_crosses_both(self):
        """Every kind of ink a leader crosses is named, not the first one
        found: a balloon that crossed a dimension on its way into the
        pictorial was reported for the dimension alone (issue #30)."""
        text = sheet(self.DIM, self.leader((4.0, 115.0), (50.0, 150.0)))
        got = [f["kind"] for f in lc.check_sheet(text, AREA)]
        self.assertIn("leader-across-dimension", got)
        self.assertIn("leader-across-extension-line", got)

    def test_tolerances_are_printed_millimetres_at_any_scale(self):
        # the same sheet drawn 1:2 — the leader still crosses the dim
        text = sheet(self.DIM, self.leader((50.0, 120.0), (50.0, 150.0)))
        self.assertIn("leader-across-dimension", kinds(text, unit_mm=0.5))


class NarrowColumnTest(unittest.TestCase):
    """A note coming out one word a line is the column, not the note."""

    def column(self, words, y=200.0, knee_x=100.0):
        recs = [mw.leader((60.0, y), (knee_x, y), words[0], 3.2, props=A)]
        for i, w in enumerate(words[1:]):
            recs.append(mw.mtext((knee_x + 1.0, y - 3.0 * (i + 1)), w,
                                 2.0, props=A))
        return recs

    def columns_of(self, recs):
        parsed = lc.parse(sheet(*recs))
        texts = [(r, lc.text_ink(r)) for r in parsed
                 if r.kind in lc.TEXT_KINDS and lc.text_ink(r)]
        return lc.narrow_columns(parsed, texts, 1.0)

    def test_three_one_word_lines_under_a_knee_are_reported(self):
        got = self.columns_of(self.column(["DEBURR", "ALL", "SHARP",
                                           "EDGES"]))
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0][0], "DEBURR")

    def test_a_note_that_wraps_sensibly_is_not(self):
        got = self.columns_of(self.column(["2 x M4 THRU WALL,",
                                           "180%%d APART",
                                           "TAP BOTH SIDES"]))
        self.assertEqual(got, [])

    def test_a_table_column_is_nobody_s_note(self):
        # the same stack of single words, with no leader behind it: a
        # variants table's cells line up exactly like this
        recs = [mw.mtext((101.0, 200.0 - 3.0 * i), w, 2.0, props=A)
                for i, w in enumerate(["A", "B", "C", "D"])]
        self.assertEqual(self.columns_of(recs), [])

    def test_the_check_reports_it_with_the_words(self):
        text = sheet(*self.column(["DEBURR", "ALL", "SHARP", "EDGES"]))
        hit = [f for f in lc.check_sheet(text, AREA)
               if f["kind"] == "callout-column-narrow"]
        self.assertTrue(hit)
        self.assertIn("DEBURR", hit[0]["what"])
        self.assertIn("bigger paper", hit[0]["what"])


# ------------------------------------------------- and the engine avoids

class EngineKeepsOffTest(unittest.TestCase):
    """Given the ink, the ladder steps round it."""

    def setUp(self):
        self.ctx = mw.SheetContext(1, 1)
        self.ctx.dimstyles = draw.sheet_dimstyles(self.ctx)
        self.layout = draw.Layout(self.ctx)
        self.job = {"visible": [seg((40.0, 150.0), (90.0, 150.0)),
                                seg((90.0, 150.0), (90.0, 210.0)),
                                seg((90.0, 210.0), (40.0, 210.0)),
                                seg((40.0, 210.0), (40.0, 150.0))],
                    "hidden": [],
                    "bounds": [[40.0, 150.0], [90.0, 210.0]]}

    def place(self, callouts):
        out = draw.place_callouts(self.ctx, self.layout, callouts,
                                  {"front": self.job},
                                  {"front": (0.0, 0.0)})
        return out[0]

    def test_a_leader_steps_round_a_dimension_line(self):
        # a dim line level with the feature, right across the knee's way
        draw._claim_dim_ink(self.ctx, self.layout, (90.0, 150.0),
                            (90.0, 210.0), (100.0, 180.0), "v", "60")
        self.place([{"view": "front", "at": [90.0, 180.0],
                     "text": "%%c20 H7"}])
        self.assertTrue(self.layout.leaders)
        for tip, knee, _lab in self.layout.leaders:
            self.assertIsNone(self.layout.crossing_ink(tip, knee),
                              f"{tip} -> {knee} crosses the dimension")

    def test_the_level_knee_is_still_kept_when_nothing_is_there(self):
        self.place([{"view": "front", "at": [90.0, 180.0],
                     "text": "%%c20 H7"}])
        tip, knee, _lab = self.layout.leaders[0]
        self.assertAlmostEqual(tip[1], knee[1], places=6)


class EngineSaysTheColumnIsNarrowTest(unittest.TestCase):
    """And the sheet says it while it is being drawn, not only when the
    finished sheet is read back: the fix is a bigger sheet, which is a
    decision, so it belongs in the /draw log."""

    def setUp(self):
        self.ctx = mw.SheetContext(1, 1)
        self.ctx.dimstyles = draw.sheet_dimstyles(self.ctx)
        self.job = {"visible": [seg((40.0, 150.0), (90.0, 150.0)),
                                seg((90.0, 150.0), (90.0, 210.0)),
                                seg((90.0, 210.0), (40.0, 210.0)),
                                seg((40.0, 210.0), (40.0, 150.0))],
                    "hidden": [],
                    "bounds": [[40.0, 150.0], [90.0, 210.0]]}

    def place(self, limit):
        layout = draw.Layout(self.ctx)
        draw.place_callouts(
            self.ctx, layout,
            [{"view": "front", "at": [90.0, 180.0],
              "text": "DEBURR ALL SHARP EDGES BOTH FACES"}],
            {"front": self.job}, {"front": (0.0, 0.0)},
            limits={"front": limit})
        return layout

    def test_a_column_narrower_than_the_note_is_a_fault(self):
        layout = self.place(112.0)
        self.assertEqual(layout.unresolved, [])     # it is drawn, and said
        self.assertTrue(layout.faults)
        why = layout.faults[0]
        self.assertIn("one word a line", why)
        self.assertIn("DEBURR", why)
        self.assertIn("bigger sheet", why)

    def test_a_column_the_note_fits_is_silent(self):
        self.assertEqual(self.place(130.0).faults, [])


class DiameterLeaderTest(unittest.TestCase):
    """The real part-sheet failure: a bore whose label would not fit
    beside its own circle falls back to a leader into the band, and that
    leader went straight through the cutting plane's sight arrow."""

    ARROW = ((75.0, 184.0), (110.0, 184.0), "the cutting plane's arrow")

    def setUp(self):
        self.ctx = mw.SheetContext(1, 1)
        self.ctx.dimstyles = draw.sheet_dimstyles(self.ctx)
        edges = [seg((40.0, 150.0), (90.0, 150.0)),
                 seg((90.0, 150.0), (90.0, 210.0)),
                 seg((90.0, 210.0), (40.0, 210.0)),
                 seg((40.0, 210.0), (40.0, 150.0))]
        self.job = {"visible": edges + [{"t": "circle", "c": [65.0, 180.0],
                                         "r": 5.0}],
                    "hidden": [],
                    "bounds": [[40.0, 150.0], [90.0, 210.0]]}

    def dim(self, arrow):
        layout = draw.Layout(self.ctx)
        # every diagonal label spot beside the circle taken, which is what
        # drops this diameter into the leader fallback
        layout.claim((50.0, 165.0, 90.0, 195.0), "blocker")
        if arrow:
            layout.keep_off.append(self.ARROW)
        _recs, rects = draw.dim_circles(self.ctx, layout, self.job,
                                        (0.0, 0.0))
        self.assertIn(5.0, rects)               # the dim is on the sheet
        self.assertEqual(layout.unresolved, [])
        self.assertEqual(len(layout.leaders), 1)
        return layout, layout.leaders[0]

    def test_without_the_arrow_the_level_rung_is_kept(self):
        _layout, (_tip, knee, _lab) = self.dim(False)
        self.assertAlmostEqual(knee[1], 188.0)

    def test_with_the_arrow_in_the_way_it_steps_past_it(self):
        layout, (tip, knee, _lab) = self.dim(True)
        self.assertIsNone(layout.crossing_ink(tip, knee),
                          f"{tip} -> {knee} runs through the sight arrow")
        self.assertNotAlmostEqual(knee[1], 188.0)


class FanAgainstInkTest(unittest.TestCase):
    """Balloons are ordered against the other ink, not stepped round it:
    out of its fan slot a balloon crosses its neighbours, which is worse
    (issue #20 is about the views; this is the rest)."""

    def test_the_order_that_stays_off_the_ink_is_chosen(self):
        # two parts, one above the dim line and one below; the upper knee
        # is the upper slot, so the straight order is the clean one
        tips = {1: (100.0, 260.0), 2: (100.0, 180.0)}
        jobs = {1: object(), 2: object()}
        dim = [("a dimension line", object(),
                [((80.0, 220.0), (320.0, 220.0))])]
        got = draw._order_against_ink([2, 1], tips, 300.0,
                                      [270.0, 260.0], jobs, dim)
        self.assertEqual(got, [1, 2])

    def test_an_order_with_nothing_to_gain_is_left_alone(self):
        tips = {1: (100.0, 260.0), 2: (100.0, 250.0)}
        jobs = {1: object(), 2: object()}
        self.assertEqual(
            draw._order_against_ink([1, 2], tips, 300.0,
                                    [270.0, 260.0], jobs, []),
            [1, 2])

    def test_a_crossing_leader_is_never_traded_for_a_clean_dimension(self):
        # swapping these two keeps them off the dim line but crosses them
        tips = {1: (100.0, 200.0), 2: (100.0, 260.0)}
        jobs = {1: object(), 2: object()}
        dim = [("a dimension line", object(),
                [((80.0, 230.0), (320.0, 230.0))])]
        got = draw._order_against_ink([2, 1], tips, 300.0,
                                      [270.0, 260.0], jobs, dim)
        self.assertEqual(got, [2, 1])

    def test_two_clean_dimensions_are_given_up_before_one_crossing(self):
        """What the weighting is FOR. Straight, these two leaders cross
        each other and touch nothing else; swapped, neither crosses the
        other and both run over a dimension line. A crossing says
        something false about the part, so the fan takes the two."""
        tips = {1: (100.0, 190.0), 2: (100.0, 200.0)}
        jobs = {1: object(), 2: object()}
        ink = [("a dimension line", object(),
                [((125.0, 199.5), (125.0, 200.5)),
                 ((125.0, 189.5), (125.0, 190.5))])]
        got = draw._order_against_ink([1, 2], tips, 150.0,
                                      [200.0, 190.0], jobs, ink)
        self.assertEqual(got, [2, 1])

    def test_other_ink_is_grouped_by_what_it_is(self):
        ctx = mw.SheetContext(1, 1)
        ctx.dimstyles = draw.sheet_dimstyles(ctx)
        layout = draw.Layout(ctx)
        draw._claim_dim_ink(ctx, layout, (10.0, 10.0), (90.0, 10.0),
                            (50.0, 30.0), "h", "80")
        draw._claim_dim_ink(ctx, layout, (10.0, 10.0), (10.0, 90.0),
                            (30.0, 50.0), "v", "80")
        layout.keep_off.append(((40.0, 10.0), (40.0, 60.0),
                                "the cutting plane's arrow"))
        got = draw.other_ink(layout)
        names = sorted(w for w, _o, _s in got)
        self.assertEqual(names, ["a dimension line",
                                 "the cutting plane's arrow"])
        dims = next(s for w, _o, s in got if w == "a dimension line")
        self.assertEqual(len(dims), 2)       # both dims, one name
        # and the extension lines are NOT in it: crossing one is a warning
        self.assertEqual(len(got), 2)


if __name__ == "__main__":
    unittest.main()
