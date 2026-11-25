# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Pranay Kiran
"""A leader keeps off the extension lines, the other arrowheads and its
neighbours' words (issue #29).

Run with:  python3 -m unittest discover -s tests -v

The real failures, all on sheets drawn with v0.14.0 that passed every
check the pipeline had:

* a diameter callout anchored on the TOP edge of a cylinder, with its
  column on the right, crossing the extension lines of the length
  dimensions above the view. Anchored on the BOTTOM edge instead — the
  same circle — its leader crosses nothing, and the project had to find
  that by hand, sheet after sheet. The crossing was a warning, so
  nothing stopped it either;
* two balloon arrows landing 0.30 mm apart on an assembly sheet, where
  the reader cannot tell which part number names which part, and a
  leader through the tip of another leader's arrowhead — a crossing AT
  the other leader's end point, which leader-crossing deliberately does
  not look at;
* a dimension's extension line running through a callout's own words,
  between "(" and "SPIGOT";
* a diameter dim's label dropped into the callout column, between two
  callouts and touching both.

The numbers in these fixtures are measured off those sheets.
"""

import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import draw  # noqa: E402
import layout_check as lc  # noqa: E402
import musa_writer as mw  # noqa: E402
import verify  # noqa: E402

A = mw.P_ANNO
AREA = (0.0, 0.0, 400.0, 400.0)
BIG = (0.0, 0.0, 3000.0, 2000.0)


def sheet(*recs):
    return "MUSACAD 14\n" + "\n".join(recs) + "\nEND\n"


def kinds(found):
    return [f["kind"] for f in found]


def of_kind(found, kind):
    return [f for f in found if f["kind"] == kind]


def seg(a, b):
    return {"t": "seg", "a": list(a), "b": list(b)}


# ------------------------------------------------------- the arrowhead

class ArrowheadGeometryTest(unittest.TestCase):
    """The triangle MusaCAD fills at a leader's tip, as both sides of the
    pipeline model it: scene_snapshot.cpp hands (knee - tip) and the
    style's arrow size to append_arrowhead."""

    def test_the_apex_is_on_the_tip_and_the_base_is_along_the_leader(self):
        tri = lc.arrowhead((10.0, 10.0), (40.0, 10.0), 2.5)
        self.assertEqual(tri[0], (10.0, 10.0))
        for p in tri[1:]:
            self.assertAlmostEqual(p[0], 12.5)
        self.assertAlmostEqual(abs(tri[1][1] - tri[2][1]), 2 * 0.18 * 2.5)

    def test_the_engine_and_the_check_draw_the_same_triangle(self):
        a, b = (100.0, 50.0), (160.0, 80.0)
        self.assertEqual(
            [tuple(round(v, 9) for v in p)
             for p in draw.dim_arrow_tri(a, b, 2.5)],
            [tuple(round(v, 9) for v in p) for p in lc.arrowhead(a, b, 2.5)])

    def test_a_zero_length_leader_has_no_arrowhead(self):
        self.assertEqual(lc.arrowhead((5.0, 5.0), (5.0, 5.0), 2.5), [])
        self.assertEqual(draw.dim_arrow_tri((5.0, 5.0), (5.0, 5.0), 2.5), [])

    def test_the_arrow_size_is_read_off_the_sheet(self):
        text = sheet(mw.dimstyle(3.0, 2.5), mw.dimstyle(6.0, 5.0),
                     mw.dimunits(1, 0.5))
        arrows, lfacs, kinds = lc.dim_styles(text)
        self.assertEqual(arrows, [2.5, 5.0])
        self.assertEqual(lfacs, {1: 0.5})
        self.assertEqual(kinds, [0, 0])

    def test_a_sheet_with_no_dimstyle_falls_back(self):
        self.assertEqual(lc.dim_styles(sheet(mw.line((0, 0), (1, 1)))),
                         ([], {}, []))

    def test_a_dot_style_is_read_as_one(self):
        """A balloon that ends inside its part's own cut area ends in a
        dot, and the check models the dot rather than a triangle
        (issue #32)."""
        text = sheet(mw.dimstyle(3.0, 2.5),
                     mw.dimstyle(3.0, 2.5, name="Dot", arrow_type=3))
        self.assertEqual(lc.dim_styles(text)[2], [0, 3])
        dot = lc.arrowhead((10.0, 10.0), (20.0, 10.0), 2.5, 3)
        self.assertEqual(len(dot), 4)
        self.assertTrue(all(abs(math.dist(p, (10.0, 10.0)) - 0.875) < 1e-9
                            for p in dot), dot)
        tri = lc.arrowhead((10.0, 10.0), (20.0, 10.0), 2.5)
        self.assertEqual(len(tri), 3)


class ArrowsLandTogetherTest(unittest.TestCase):
    """Two balloons whose arrows land in one place (the real pair from
    the A3-landscape assembly sheet, 1:5): the sheet does not say which
    part each number names."""

    T22, K22 = (1090.99, 985.85), (1551.19, 1267.17)
    T23, K23 = (1092.49, 985.85), (1551.19, 1205.67)

    def balloon(self, tip, knee, number):
        """A balloon as draw.balloons_fan draws it: the leader, the circle
        beside its knee and the number in it."""
        return [mw.leader(tip, knee, "", 17.5, props=A),
                mw.circle((knee[0] + 24.5, knee[1]), 24.5, A),
                mw.cell_text((knee[0] + 24.5, knee[1]), number, 17.5, A)]

    def found(self, pairs):
        recs = []
        for tip, knee, num in pairs:
            recs += self.balloon(tip, knee, num)
        return lc.check_sheet(sheet(mw.dimstyle(15.0, 12.5), *recs),
                              BIG, 1 / 5)

    def test_the_pair_is_reported_once_by_their_numbers(self):
        found = self.found([(self.T22, self.K22, "22"),
                            (self.T23, self.K23, "23")])
        hit = of_kind(found, "leader-through-arrowhead")
        self.assertEqual(len(hit), 1, kinds(found))
        self.assertIn("balloon 22", hit[0]["what"])
        self.assertIn("balloon 23", hit[0]["what"])
        self.assertIn("0.30 mm apart", hit[0]["what"])

    def test_arrows_a_balloon_apart_are_fine(self):
        found = self.found([(self.T22, self.K22, "22"),
                            ((1092.49, 1085.85), self.K23, "23")])
        self.assertEqual(of_kind(found, "leader-through-arrowhead"), [])

    def test_the_finding_is_waivable(self):
        found = self.found([(self.T22, self.K22, "22"),
                            (self.T23, self.K23, "23")])
        live, done = verify.waived(found, ["the arrows of 'balloon 22'"])
        self.assertEqual(len(done), 1)
        self.assertEqual(live, [])


class LeaderThroughArrowheadTest(unittest.TestCase):
    """One leader's shaft through another's arrowhead. The geometry is
    the PROBE NOSE sheet's own (1:1, A3 landscape): its spigot and
    outside-diameter callouts, with the spigot's knee where the issue
    reports it — on its way past the other arrow."""

    OD_TIP, OD_KNEE = (167.0, 185.6), (203.5, 177.6)

    def leaders(self, spigot_tip, spigot_knee):
        return sheet(mw.dimstyle(3.0, 2.5),
                     mw.leader(self.OD_TIP, self.OD_KNEE, "%%c50.8", 3.2,
                               props=A),
                     mw.leader(spigot_tip, spigot_knee,
                               "%%c46.7 -0.1/0 (SPIGOT)", 3.2, props=A))

    def test_a_shaft_through_the_other_arrowhead_is_a_fault(self):
        # from the left, rising to the column through the tip of the
        # %%c50.8 arrowhead (which is 2.5 x 0.9 units: the crossing has
        # to be measured, not eyeballed)
        text = self.leaders((128.1, 183.0), (203.5, 188.0))
        hit = of_kind(lc.check_sheet(text, AREA), "leader-through-arrowhead")
        self.assertTrue(hit)
        self.assertIn("runs through the arrowhead of '%%c50.8'",
                      hit[0]["what"])

    def test_the_same_leader_clear_of_it_is_silent(self):
        text = self.leaders((128.1, 234.4), (203.5, 242.4))
        self.assertEqual(
            of_kind(lc.check_sheet(text, AREA), "leader-through-arrowhead"),
            [])

    def test_the_engine_turns_such_a_rung_down(self):
        ctx = mw.SheetContext(1, 1)
        ctx.dimstyles = draw.sheet_dimstyles(ctx)
        layout = draw.Layout(ctx)
        draw._claim_leader_ink(ctx, layout, self.OD_TIP, self.OD_KNEE,
                               "callout-%%c50.8")
        self.assertEqual(layout.arrow_clash((128.1, 183.0), (203.5, 188.0)),
                         "callout-%%c50.8")
        self.assertIsNone(layout.arrow_clash((128.1, 234.4),
                                             (203.5, 242.4)))

    def test_two_leaders_meeting_at_their_tips_clash_as_well(self):
        ctx = mw.SheetContext(1, 1)
        ctx.dimstyles = draw.sheet_dimstyles(ctx)
        layout = draw.Layout(ctx)
        draw._claim_leader_ink(ctx, layout, (100.0, 100.0), (150.0, 120.0),
                               "callout-a")
        self.assertEqual(layout.arrow_clash((101.0, 100.0), (150.0, 80.0)),
                         "callout-a")


# -------------------------------------- the feature's own other edges

class FeatureEdgeAnchorsTest(unittest.TestCase):
    """Where else a size callout's arrow may land. The ink is the LOGGER
    CRADLE's own: its fore cap's top edge is drawn in three short
    segments and the stated `at` lies on the first of them."""

    JOB = {"bounds": [[584.0, -23.3], [712.0, 23.3]],
           "visible": [seg((712.0, 23.3), (708.04, 23.3)),
                       seg((708.04, 23.3), (706.02, 23.3)),
                       seg((706.02, 23.3), (704.0, 23.3)),
                       seg((712.0, -23.3), (704.0, -23.3)),
                       seg((712.0, 23.3), (712.0, 20.18)),
                       seg((584.0, 23.3), (584.0, -23.3))]}
    CO = {"view": "front", "text": "%%c46.6 (SIZED TO THE SHELL BORE)",
          "circle_r": 23.3, "at": [709.0, 23.3]}

    def anchors(self, co=None, off=(0.0, 0.0)):
        co = co or self.CO
        anchor = draw.callout_anchor(co, self.JOB, off)
        return anchor, draw.feature_edge_anchors(co, self.JOB, off, anchor)

    def test_the_stated_point_is_the_anchor(self):
        anchor, _alts = self.anchors()
        self.assertEqual(anchor, (709.0, 23.3, 0.0))

    def test_the_other_end_of_its_own_edge_comes_first(self):
        _a, alts = self.anchors()
        self.assertTrue(alts)
        xs = [round(p[0], 2) for p in alts]
        ys = [round(p[1], 2) for p in alts]
        self.assertEqual(ys[:3], [23.3, 23.3, 23.3])
        self.assertEqual(xs[0], 711.6, "the end nearest the column first")
        self.assertTrue(all(708.0 <= x <= 712.0 for x in xs[:3]), xs)

    def test_then_the_feature_s_other_edge_two_radii_away(self):
        _a, alts = self.anchors()
        self.assertEqual([round(p[1], 2) for p in alts[3:]],
                         [-23.3, -23.3, -23.3])

    def test_a_callout_with_no_size_keeps_the_spot_it_was_given(self):
        co = dict(self.CO)
        del co["circle_r"]
        self.assertEqual(self.anchors(co)[1], [])

    def test_a_callout_with_no_at_point_has_no_other_edge(self):
        co = {"view": "front", "text": "%%c46.6", "circle_r": 23.3}
        self.assertEqual(
            draw.feature_edge_anchors(co, self.JOB, (0.0, 0.0),
                                      (709.0, 23.3, 0.0)), [])

    def test_a_point_on_no_drawn_edge_has_none_either(self):
        co = dict(self.CO, at=[650.0, 8.0])
        self.assertEqual(self.anchors(co)[1], [])

    def test_the_view_offset_is_carried_through(self):
        _a, alts = self.anchors(off=(100.0, 200.0))
        self.assertEqual([round(p[1], 2) for p in alts[:1]], [223.3])
        self.assertEqual([round(p[0], 2) for p in alts[:1]], [811.6])

    def test_a_parallel_edge_alongside_is_not_the_other_edge(self):
        """Two radii away but nowhere near the stated edge: a different
        feature's line, which the callout does not name."""
        job = {"bounds": self.JOB["bounds"],
               "visible": [seg((712.0, 23.3), (708.04, 23.3)),
                           seg((600.0, -23.3), (590.0, -23.3))]}
        anchor = draw.callout_anchor(self.CO, job, (0.0, 0.0))
        alts = draw.feature_edge_anchors(self.CO, job, (0.0, 0.0), anchor)
        self.assertTrue(all(p[1] > 0 for p in alts), alts)


# ------------------------------------ the ink that is not there yet

class LateInkTest(unittest.TestCase):
    """The callouts are placed before the overall dimensions, so the
    first pass is what tells the engine where those extension lines
    are."""

    def setUp(self):
        self.ctx = mw.SheetContext(1, 1)
        self.ctx.dimstyles = draw.sheet_dimstyles(self.ctx)
        self.layout = draw.Layout(self.ctx)

    def test_a_callout_leader_across_an_extension_line_is_collected(self):
        """And the WHOLE dimension comes back with it (issue #33): its
        line and both extension lines, or a callout steered off the one
        it crossed steps onto the next one along."""
        draw._claim_dim_ink(self.ctx, self.layout, (10.0, 10.0),
                            (90.0, 10.0), (50.0, 30.0), "h", "80")
        draw._claim_leader_ink(self.ctx, self.layout, (5.0, 20.0),
                               (20.0, 20.0), "callout-%%c20")
        got = draw.crossed_dim_ink(self.layout)
        self.assertEqual(len(got), 3)
        self.assertIn(10.0, [round(a[0], 3) for a, _b in got])
        self.assertIn(90.0, [round(a[0], 3) for a, _b in got])

    def test_a_balloon_s_leader_is_not_a_callout_s(self):
        draw._claim_dim_ink(self.ctx, self.layout, (10.0, 10.0),
                            (90.0, 10.0), (50.0, 30.0), "h", "80")
        draw._claim_leader_ink(self.ctx, self.layout, (5.0, 20.0),
                               (20.0, 20.0), "balloon-7-0")
        self.assertEqual(draw.crossed_dim_ink(self.layout), [])

    def test_a_clean_sheet_seeds_nothing(self):
        draw._claim_dim_ink(self.ctx, self.layout, (10.0, 10.0),
                            (90.0, 10.0), (50.0, 30.0), "h", "80")
        draw._claim_leader_ink(self.ctx, self.layout, (20.0, 60.0),
                               (80.0, 70.0), "callout-%%c20")
        self.assertEqual(draw.crossed_dim_ink(self.layout), [])

    def test_a_better_balance_may_not_buy_itself_a_crossing(self):
        """The balance pass shifts the whole view group, so the ink the
        first pass found moves with it and a trial may meet an extension
        line the seed never named. A trial that crosses one more of them
        is turned down however much better it looks."""
        def layout_with(crossings):
            lay = draw.Layout(self.ctx)
            draw._claim_dim_ink(self.ctx, lay, (10.0, 10.0), (90.0, 10.0),
                                (50.0, 30.0), "h", "80")
            for i in range(crossings):
                draw._claim_leader_ink(self.ctx, lay, (5.0, 20.0 + i),
                                       (20.0, 20.0 + i),
                                       f"callout-{i}")
            return lay

        clean, crossing = layout_with(0), layout_with(1)
        self.assertEqual(len(draw.crossed_dim_ink(crossing)), 3)
        # better balanced (a smaller gap) but one crossing worse
        self.assertTrue(draw.balance_regresses(crossing, clean, 1.0, 9.0))
        # and the same trial with nothing new against it is taken
        self.assertFalse(draw.balance_regresses(clean, clean, 1.0, 9.0))
        # the older guards still hold
        self.assertTrue(draw.balance_regresses(clean, clean, 9.0, 9.0),
                        "no better balanced, so not better")
        worse = layout_with(0)
        worse.faults.append("a leader with nowhere clean to go")
        self.assertTrue(draw.balance_regresses(worse, clean, 1.0, 9.0))
        lost = layout_with(0)
        lost.unresolved.append("callout-%%c20")
        self.assertTrue(draw.balance_regresses(lost, clean, 1.0, 9.0))

    def test_a_better_balance_may_not_buy_a_leader_across_the_view(self):
        """The shift moves the callout's anchor with the view, so its
        knee can land somewhere that reads worse — and a sheet does not
        become better by being centred (issue #33)."""
        def layout_with(tip):
            lay = draw.Layout(self.ctx)
            lay.geom = [((40.0, 0.0), (40.0, 100.0)),
                        ((45.0, 0.0), (45.0, 100.0))]
            draw._claim_leader_ink(self.ctx, lay, tip, (80.0, 50.0),
                                   "callout-%%c20")
            return lay

        clear = layout_with((50.0, 50.0))       # outboard of both lines
        across = layout_with((10.0, 50.0))      # the far side of them
        self.assertEqual(draw.leader_quality(clear)[3], 0)
        self.assertGreater(draw.leader_quality(across)[3], 0)
        self.assertTrue(draw.balance_regresses(across, clear, 1.0, 9.0))
        self.assertFalse(draw.balance_regresses(clear, across, 1.0, 9.0))

    def test_the_seed_travels_with_the_view_group(self):
        segs = [((10.0, 10.0), (10.0, 30.0))]
        self.assertEqual(draw.shift_segs(segs, 5.0),
                         [((15.0, 10.0), (15.0, 30.0))])
        self.assertEqual(draw.shift_segs(segs, 0.0), segs)

    def test_an_arrow_already_there_sends_it_to_another_point(self):
        """Another callout already points AT this feature's stated spot,
        so every rung from it would put two arrowheads in one place —
        which says nothing at all. The engine turns all of them down and
        takes the same feature's other points instead: here the far end
        of its own drawn edge (issue #29)."""
        job = {"bounds": [[60.0, 150.0], [140.0, 210.0]],
               "visible": [seg((60.0, 204.4), (140.0, 204.4)),
                           seg((60.0, 155.6), (140.0, 155.6))]}
        co = {"view": "front", "text": "%%c48.8", "circle_r": 24.4,
              "at": [68.0, 204.4]}
        ctx = mw.SheetContext(1, 1)
        ctx.dimstyles = draw.sheet_dimstyles(ctx)
        layout = draw.Layout(ctx)
        draw._claim_leader_ink(ctx, layout, (68.0, 204.4), (150.0, 230.0),
                               "callout-other")
        draw.place_callouts(ctx, layout, [co], {"front": job},
                            {"front": (0.0, 0.0)})
        tips = [t for t, _k, lab in layout.leaders
                if lab != "callout-other"]
        self.assertEqual(len(tips), 1, layout.unresolved)
        self.assertNotEqual((round(tips[0][0], 1), round(tips[0][1], 1)),
                            (68.0, 204.4),
                            "the arrow stayed where the other one is")
        alts = [(round(p[0], 1), round(p[1], 1)) for p in
                draw.feature_edge_anchors(co, job, (0.0, 0.0),
                                          (68.0, 204.4, 0.0))]
        self.assertIn((round(tips[0][0], 1), round(tips[0][1], 1)), alts)

    def test_a_seeded_extension_line_moves_the_knee(self):
        """place_callouts with the PROBE NOSE's own spigot callout: the
        knee that was taken when nothing was there is turned down once
        the extension line is known, and the leader comes off the
        feature at another angle."""
        job = {"bounds": [[60.0, 150.0], [140.0, 210.0]],
               "visible": [seg((60.0, 204.4), (140.0, 204.4))]}
        co = {"view": "front", "text": "%%c46.7", "at": [68.0, 204.4]}
        out = []
        for ahead in ([], [((148.0, 200.0), (148.0, 230.0))]):
            ctx = mw.SheetContext(1, 1)
            ctx.dimstyles = draw.sheet_dimstyles(ctx)
            layout = draw.Layout(ctx)
            _r, _rects, _s, lands, _anc = draw.place_callouts(
                ctx, layout, [co], {"front": job}, {"front": (0.0, 0.0)},
                ext_ahead=ahead)
            out.append(layout.leaders[0][1])
        self.assertNotEqual(out[0], out[1])
        self.assertGreater(out[0][1], out[1][1],
                           "the knee should have stepped down")


# ------------------------------------------ a dimension's ink on a label

class DimensionInkOnTextTest(unittest.TestCase):
    """An extension line through a callout's words. The label is composed
    at plot time and is not on the sheet, so the only ink here is what
    dimension.cpp draws from the record — which is what the reader
    sees."""

    def test_an_extension_line_through_a_note_is_reported(self):
        text = sheet(mw.dim(0, (10.0, 100.0), (90.0, 100.0),
                            (50.0, 160.0)),
                     mw.mtext((5.0, 130.0), "%%c46.7 (SPIGOT)", 3.2,
                              props=A))
        hit = of_kind(lc.check_sheet(text, AREA), "ink-on-text")
        self.assertTrue(hit)
        self.assertIn("an extension line", hit[0]["what"])
        self.assertIn("the 80 mm dimension", hit[0]["what"])
        self.assertIn("SPIGOT", hit[0]["what"])

    def test_a_dimension_line_through_one_is_too(self):
        text = sheet(mw.dim(0, (10.0, 100.0), (90.0, 100.0),
                            (50.0, 160.0)),
                     mw.mtext((40.0, 161.6), "DEBURR ALL OVER", 3.2,
                              props=A))
        hit = of_kind(lc.check_sheet(text, AREA), "ink-on-text")
        self.assertTrue(hit)
        self.assertIn("a dimension line", hit[0]["what"])

    # the SKIRT SHELL sheet at 1:2: the overall diameter's own extension
    # line runs out level with the cutting-plane label A, 0.3 mm from the
    # top of its glyph box
    GRAZE_DIM = mw.dim(0, (142.95, 446.74), (142.95, 295.74),
                       (111.17, 371.24))
    BIG_AREA = (0.0, 0.0, 1000.0, 1000.0)

    def test_a_line_grazing_the_top_of_a_letter_is_not(self):
        """The real one. The line is INSIDE the glyph box — the letter is
        8 units tall and the line is 0.6 from its top — and it covers no
        glyph: an "A" has nothing up there but its apex. A check that
        reports this reports every sheet."""
        text = sheet(self.GRAZE_DIM,
                     mw.mtext((126.07, 433.99), "A", 8.0, attach=7))
        self.assertEqual(of_kind(lc.check_sheet(text, self.BIG_AREA, 0.5),
                                 "ink-on-text"), [])

    def test_the_same_line_through_the_body_of_the_letter_is(self):
        """Lower the label by half its height and the line runs through
        the middle of the glyph."""
        text = sheet(self.GRAZE_DIM,
                     mw.mtext((126.07, 437.4), "A", 8.0, attach=7))
        hit = of_kind(lc.check_sheet(text, self.BIG_AREA, 0.5),
                      "ink-on-text")
        self.assertTrue(hit)
        self.assertIn("an extension line", hit[0]["what"])

    def test_one_dimension_is_named_once_per_text(self):
        """Both extension lines of one dim through one note is one
        finding, not two identical ones."""
        text = sheet(mw.dim(0, (40.0, 100.0), (60.0, 100.0),
                            (50.0, 160.0)),
                     mw.mtext((30.0, 128.0), "A LONG NOTE ACROSS BOTH",
                              3.2, props=A))
        self.assertEqual(len(of_kind(lc.check_sheet(text, AREA),
                                     "ink-on-text")), 1)

    def test_a_detail_view_s_dimension_is_named_as_it_reads(self):
        """DIMLFAC is applied: geometry at twice size, style at lfac 0.5,
        so a 40-unit span is the 20 mm dimension the reader sees."""
        text = sheet(mw.dimstyle(3.0, 2.5), mw.dimstyle(3.0, 2.5),
                     mw.dimunits(1, 0.5),
                     mw.dim(0, (10.0, 100.0), (50.0, 100.0), (30.0, 160.0),
                            style=1),
                     mw.mtext((5.0, 130.0), "%%c46.7 (SPIGOT)", 3.2,
                              props=A))
        hit = of_kind(lc.check_sheet(text, AREA), "ink-on-text")
        self.assertTrue(hit)
        self.assertIn("the 20 mm dimension", hit[0]["what"])


# ---------------------------------------------- a crowded callout column

class CrowdedColumnTest(unittest.TestCase):
    """Two annotations closer than the clearance the engine itself keeps
    read as one block. A diameter dim's label that fell into the callout
    column lands between two callouts and touches both."""

    def test_a_label_touching_the_next_callout_is_reported(self):
        text = sheet(mw.leader((60.0, 200.0), (100.0, 200.0), "%%c75.50",
                               3.2, props=A),
                     mw.mtext((102.0, 199.6), "%%c58 (LIP)", 3.2, props=A))
        hit = of_kind(lc.check_sheet(text, AREA), "annotation-crowded")
        self.assertTrue(hit, kinds(lc.check_sheet(text, AREA)))
        self.assertIn("%%c75.50", hit[0]["what"])
        self.assertIn("%%c58 (LIP)", hit[0]["what"])

    def test_the_same_two_two_millimetres_apart_are_not(self):
        """Clear of the 1.28 mm (0.4 of a 3.2 mm height) two annotations
        need to read apart (issue #33)."""
        text = sheet(mw.leader((60.0, 200.0), (100.0, 200.0), "%%c75.50",
                               3.2, props=A),
                     mw.mtext((102.0, 197.6), "%%c58 (LIP)", 3.2, props=A))
        self.assertEqual(of_kind(lc.check_sheet(text, AREA),
                                 "annotation-crowded"), [])

    def test_a_callout_s_own_stacked_lines_are_not_crowding(self):
        """The ASME hole note: its lines hang off its own knee at the
        pitch place_callouts gives them."""
        knee = (100.0, 200.0)
        text = sheet(mw.leader((60.0, 200.0), knee, "4 x %%c4.5 THRU",
                               3.2, props=A),
                     mw.mtext((101.5, 200.0 - 5.76), "SPOTFACE %%c9",
                              3.2, attach=3, props=A),
                     mw.mtext((101.5, 200.0 - 11.52), "FROM THE FAR SIDE",
                              3.2, attach=3, props=A))
        self.assertEqual(of_kind(lc.check_sheet(text, AREA),
                                 "annotation-crowded"), [])

    def test_a_notes_block_is_not_crowding_either(self):
        """The real leading of the fab-notes block (0.65 mm between
        lines at 1:1): a block of text is one annotation, and it has no
        leader."""
        recs = [mw.mtext((20.0, 100.0 - i * 3.85),
                         f"{i + 1}. PRINT FROM WQ-SSLXBT-TF07A-10", 3.2,
                         props=A) for i in range(4)]
        self.assertEqual(of_kind(lc.check_sheet(sheet(*recs), AREA),
                                 "annotation-crowded"), [])

    def test_a_frame_s_cells_belong_to_the_frame(self):
        """A feature control frame hangs UNDER its callout, by 0.4 mm
        where the column is tight — closer than two annotations may be,
        except that a frame's cells are drawn with their boxes and are
        that frame's, not somebody else's crowding. A frame lying ON
        another label is ink-on-text, which is check 3's business."""
        recs = [mw.leader((60.0, 200.0), (100.0, 200.0), "%%c46.7", 3.2,
                          props=A)]
        for (x0, x1), body in (((100.0, 108.0), "%%v"),
                               ((108.0, 126.0), "0.05")):
            recs.append(mw.polyline([(x0, 196.0), (x1, 196.0),
                                     (x1, 199.6), (x0, 199.6)], True, A))
            recs.append(mw.mtext((x0 + 1.0, 199.6), body, 3.2, props=A))
        found = lc.check_sheet(sheet(*recs), AREA)
        self.assertEqual(of_kind(found, "annotation-crowded"), [])
        # ... and the same text outside any box IS crowding
        loose = recs[:1] + [mw.mtext((102.0, 199.6), "0.05", 3.2, props=A)]
        self.assertTrue(of_kind(lc.check_sheet(sheet(*loose), AREA),
                                "annotation-crowded"))

    def test_it_is_printed_millimetres_at_any_scale(self):
        """The floor is PRINTED millimetres: the same two labels a sheet
        unit apart are 1 mm apart at 1:1 and 0.2 mm apart at 1:5."""
        text = sheet(mw.leader((60.0, 200.0), (100.0, 200.0), "%%c75.50",
                               2.0, props=A),
                     mw.mtext((102.0, 199.0), "%%c58 (LIP)", 2.0,
                              props=A))
        self.assertEqual(of_kind(lc.check_sheet(text, BIG, 1.0),
                                 "annotation-crowded"), [])
        self.assertTrue(of_kind(lc.check_sheet(text, BIG, 1 / 5),
                                "annotation-crowded"))


# --------------------------------------------- a datum reads along a line

class DatumReadsOnItsLineTest(unittest.TestCase):
    """Text reads ALONG a line: a datum box on the line below, after that
    line's words, reads as that line's however far its own callout is."""

    def stack(self, box_y):
        """Two stacked callouts in the right-hand column and a datum box
        hung on the upper one's landing — the PROBE NOSE's own
        arrangement, with the spigot's label wrapped short by the column
        (which is how the box came to land after the line below it).

        The triangle sits on the extension of the landing past the text
        (ISO 1101 5.3, issue #17), so its tip is level with the knee: that
        is what tells the check whose datum it is."""
        box = (220.0, box_y, 225.4, box_y + 5.4)
        return sheet(
            mw.leader((128.1, 234.4), (203.5, 245.1), "%%c46.7", 3.2,
                      props=A),
            mw.leader((186.0, 229.0), (203.5, 237.0), "SR25.4", 3.2,
                      props=A),
            mw.polyline([(box[0], box[1]), (box[2], box[1]),
                         (box[2], box[3]), (box[0], box[3])], True, A),
            mw.mtext((box[0] + 1.5, box[1] + 4.4), "A", 3.2, props=A),
            mw.hatch([[(217.0, 245.1), (219.5, 244.6), (219.5, 245.6)]],
                     "SOLID", props=A))

    def test_a_box_on_the_other_callout_s_line_is_reported(self):
        found = lc.check_sheet(self.stack(box_y=236.0), AREA)
        hit = of_kind(found, "datum-beside-wrong-callout")
        self.assertTrue(hit, kinds(found))
        self.assertIn("SR25.4", hit[0]["what"])
        self.assertIn("same LINE", hit[0]["what"])

    def test_a_box_on_its_own_callout_s_line_is_not(self):
        self.assertEqual(
            of_kind(lc.check_sheet(self.stack(box_y=243.5), AREA),
                    "datum-beside-wrong-callout"), [])

    def test_a_label_too_far_along_the_line_is_read_as_nothing(self):
        """Its own callout is on the line above, 4 mm away; the only
        label on the box's OWN line ends 30 mm to its left. Nobody reads
        across a gap like that, so the box reads as its own and the line
        rule says nothing (the distance rule has the last word)."""
        box = (220.0, 236.0, 225.4, 241.4)
        text = sheet(
            mw.leader((128.1, 234.4), (203.5, 245.1), "%%c46.7", 3.2,
                      props=A),
            mw.leader((186.0, 229.0), (150.0, 237.0), "SR25.4", 3.2,
                      props=A),
            mw.polyline([(box[0], box[1]), (box[2], box[1]),
                         (box[2], box[3]), (box[0], box[3])], True, A),
            mw.mtext((box[0] + 1.5, box[1] + 4.4), "A", 3.2, props=A),
            mw.hatch([[(217.0, 245.1), (219.5, 244.6), (219.5, 245.6)]],
                     "SOLID", props=A))
        self.assertEqual(of_kind(lc.check_sheet(text, AREA),
                                 "datum-beside-wrong-callout"), [])

    def test_a_label_right_across_the_sheet_is_not_what_it_reads_as(self):
        """The real HULL TUBE sheet (1:5): a frame's 0.05 cell shares the
        datum box's line but sits 143 mm away, while the box's own
        callout is 4.8 mm from it on the line above. Nobody reads across
        the sheet."""
        box = (1419.0, 676.0, 1446.2, 703.2)
        text = sheet(
            mw.leader((300.0, 689.6), (1095.5, 697.6),
                      "%%c71.5 H8, N7 (HEAD END, 25 LONG)", 16.0, props=A),
            mw.mtext((665.5, 676.6), "0.05", 15.0, props=A),
            mw.polyline([(box[0], box[1]), (box[2], box[1]),
                         (box[2], box[3]), (box[0], box[3])], True, A),
            mw.mtext((box[0] + 7.0, box[1] + 6.0), "A", 15.0, props=A),
            mw.hatch([[(1404.0, 689.6), (1413.9, 676.9), (1420.1, 689.9)]],
                     "SOLID", props=A))
        self.assertEqual(
            of_kind(lc.check_sheet(text, BIG, 1 / 5),
                    "datum-beside-wrong-callout"), [])


class UnwaivableTest(unittest.TestCase):
    """A leader hidden under the pictorial is not printed, and no reason
    makes the vendor see it (issue #30)."""

    HIDDEN = {"kind": "leader-hidden", "what": "the leader of 'balloon 22' "
              "runs 49.2 mm under the pictorial", "severity": "fail",
              "waivable": False}
    ORDINARY = {"kind": "leader-across-dimension",
                "what": "the leader of 'balloon 22' crosses a dimension "
                        "line", "severity": "fail"}

    def test_a_waiver_naming_it_does_not_waive_it(self):
        live, done = verify.waived([self.HIDDEN],
                                   ["the leader of 'balloon 22'"])
        self.assertEqual(live, [self.HIDDEN])
        self.assertEqual(done, [])

    def test_the_ordinary_one_beside_it_still_waives(self):
        live, done = verify.waived([self.HIDDEN, self.ORDINARY],
                                   ["crosses a dimension line"])
        self.assertEqual(live, [self.HIDDEN])
        self.assertEqual(done, [self.ORDINARY])

    def test_the_check_lists_them_on_their_own(self):
        self.assertEqual(lc.unwaivable([self.HIDDEN, self.ORDINARY]),
                         [self.HIDDEN])


if __name__ == "__main__":
    unittest.main()
