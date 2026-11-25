# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Pranay Kiran
"""A size leader that takes the long way round its own feature, and a
label dropped into somebody else's column (issue #33).

Run with:  python3 -m unittest discover -s tests -v

The real sheets: a 1:2 tube on A3 landscape and a 1:1 short tube on A4,
each with its outside diameter anchored on the TOP edge of the feature
and its callout column on the right. The arrow stayed where the `at` put
it and the leader ran diagonally down across the whole view — through
the centre line, through the hidden bore lines — to a knee at the height
of the view's lower half. Anchored on the BOTTOM edge at the end nearest
the column, which is the same circle, it crosses nothing at all. The
engine never asked: extension lines, arrowheads and other leaders were
tested, the view's own ink was not.

On a third sheet every other circle was a callout with
`suppress_dia_dim`, so the outside diameter fell back to a diameter
LEADER whose label landed between two callouts at the bare 0.8 mm the
engine keeps between claims — and printed as the middle line of their
note.
"""

import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import draw  # noqa: E402
import layout_check as lc  # noqa: E402
import musa_writer as mw  # noqa: E402
from test_leader_rules import (BODY_R, X0, X1, X2, build,  # noqa
                               nose_front, seg)

A = mw.P_ANNO
AREA = (0.0, 0.0, 400.0, 400.0)


def sheet(*recs):
    return "MUSACAD 14\n" + "\n".join(recs) + "\nEND\n"


def of_kind(found, kind):
    return [f for f in found if f["kind"] == kind]


def tube(cx=90.0, cy=200.0, half=15.0, length=60.0, bore=8.0):
    """A short tube as a side view draws it: two silhouettes, a centre
    line and the two hidden bore lines between them — the -08 sheet,
    whose view is 67 x 30 mm."""
    x0, x1 = cx - length / 2, cx + length / 2
    return [((x0, cy + half), (x1, cy + half)),
            ((x0, cy - half), (x1, cy - half)),
            ((x0, cy + half), (x0, cy - half)),
            ((x1, cy + half), (x1, cy - half)),
            ((x0, cy), (x1, cy)),
            ((x0, cy + bore), (x1, cy + bore)),
            ((x0, cy - bore), (x1, cy - bore))]


TUBE_R, BORE_R = 15.0, 8.0
TX0, TX1 = 500.0, 560.0           # a 60 mm tube, drawn as a side view


def tube_front():
    """The -08 sheet's own front view: a short tube seen from the side —
    two silhouettes, two ends, the centre line and the two hidden bore
    lines a leader across it has to cross."""
    box = [(TX0, TUBE_R), (TX1, TUBE_R), (TX1, -TUBE_R), (TX0, -TUBE_R),
           (TX0, TUBE_R)]
    return {"visible": [seg(a, b) for a, b in zip(box, box[1:])],
            "hidden": [seg((TX0, BORE_R), (TX1, BORE_R)),
                       seg((TX0, -BORE_R), (TX1, -BORE_R))],
            "bounds": [[TX0, -TUBE_R], [TX1, TUBE_R]]}


def tube_section():
    walls = []
    for lo, hi in ((BORE_R, TUBE_R), (-TUBE_R, -BORE_R)):
        loop = [(TX0, lo), (TX1, lo), (TX1, hi), (TX0, hi)]
        walls.append(loop)
    vis = [seg(a, b) for loop in walls
           for a, b in zip(loop, loop[1:] + loop[:1])]
    return {"visible": vis, "hidden": [],
            "hatch_groups": [{"outer": [list(p) for p in loop], "holes": []}
                             for loop in walls],
            "bounds": [[TX0, -TUBE_R], [TX1, TUBE_R]]}


def tube_sheet(callouts):
    """A part sheet of that tube, through the real composer."""
    import tempfile
    from test_leader_rules import nose_manifest
    with tempfile.TemporaryDirectory() as tmp:
        m, part = nose_manifest(Path(tmp), callouts)
        proj = {"05:front": tube_front(), "05:section": tube_section()}
        ctx, _replaced, lay = draw.build_part_sheet(m, part, proj)
        return ctx, lay


# ---------------------------------------------- what the engine counts

class ViewInkCrossingsTest(unittest.TestCase):

    def setUp(self):
        self.ctx = mw.SheetContext(1, 1)
        self.segs = tube()

    def test_a_leader_from_the_far_edge_crosses_the_view(self):
        """From the top edge at the far end, down to a knee beyond the
        near end: both bore lines, the centre line and the end."""
        n = draw.view_ink_crossings(self.ctx, self.segs, (70.0, 215.0),
                                    (150.0, 180.0))
        self.assertGreaterEqual(n, 3)       # a bore line, the centre
                                            # line, the end of the tube

    def test_the_near_end_of_the_same_edge_crosses_nothing(self):
        self.assertEqual(draw.view_ink_crossings(self.ctx, self.segs,
                                                 (118.0, 185.0),
                                                 (150.0, 175.0)), 0)

    def test_the_feature_s_own_ink_at_the_arrow_is_not_counted(self):
        """The arrow SITS on the edge it names, and the ink around that
        spot — a chamfer line a millimetre away, the hatch boundary — is
        the feature's own. A leader leaving it crosses that on its way
        out of every feature there is, so it says nothing."""
        tip, knee = (100.0, 215.0), (150.0, 205.0)
        chamfer = [((101.0, 218.0), (101.5, 212.0))]
        self.assertLess(draw._seg_pt_dist(*chamfer[0], tip),
                        self.ctx.mm(draw.VIEW_INK_OWN_MM))
        self.assertEqual(draw.view_ink_crossings(self.ctx, chamfer, tip,
                                                 knee), 0)
        # ... and the same line, from an arrow further along the edge, is
        self.assertEqual(draw.view_ink_crossings(self.ctx, chamfer,
                                                 (90.0, 215.0),
                                                 (150.0, 205.0)), 1)

    def test_ink_further_than_that_is(self):
        far = draw.view_ink_crossings(self.ctx, self.segs, (118.0, 222.0),
                                      (118.0, 178.0))
        self.assertGreaterEqual(far, 5)

    def test_a_view_with_no_ink_crosses_nothing(self):
        self.assertEqual(draw.view_ink_crossings(self.ctx, [], (0.0, 0.0),
                                                 (10.0, 10.0)), 0)


# ------------------------------------------ what the engine then draws

class ArrowTakesTheNearEndTest(unittest.TestCase):
    """End to end on the turned part: the diameter anchored on the top
    silhouette, its column on the right."""

    @staticmethod
    def leaders(ctx):
        out = []
        for rec in ctx.entities:
            if not rec.startswith("LEADER"):
                continue
            t = rec.split("\n")[0].split()
            out.append(((float(t[1]), float(t[2])),
                        (float(t[3]), float(t[4]))))
        return out

    @staticmethod
    def drawn_ink(ctx):
        """The sheet's own black geometry, as the layout check reads it
        — the leaders are in SHEET coordinates and the fixture's job is
        in the part's, so the finished sheet is what to measure."""
        recs = lc.parse(ctx.render())
        return [sg for r in recs
                if not r.anno and r.kind in ("LINE", "POLYLINE", "ARC",
                                             "CIRCLE")
                for sg in lc.segments(r)]

    def test_a_tube_s_diameter_leaves_by_the_near_end(self):
        """The whole of issue #33 on one sheet: ⌀30 anchored on the TOP
        edge at the FAR end of a tube, its column on the right. The
        leader used to run diagonally down across the view, through the
        centre line and both hidden bore lines; it has an edge of its
        own to leave by."""
        co = [{"view": "front", "text": "%%c30", "circle_r": TUBE_R,
               "at": [TX0 + 4.0, TUBE_R]}]
        ctx, lay = tube_sheet(co)
        recs = lc.parse(ctx.render())
        ink = [sg for r in recs
               if not r.anno and r.kind in ("LINE", "POLYLINE", "ARC",
                                            "CIRCLE")
               for sg in lc.segments(r)]
        leaders = [(tuple(r.nums[:2]), tuple(r.nums[2:4]))
                   for r in recs if r.kind == "LEADER"]
        self.assertEqual(len(leaders), 1, leaders)
        tip, knee = leaders[0]
        self.assertEqual(lay["unresolved"], [])
        # the top silhouette, as the sheet draws it: the arrow stays on
        # that edge — the same feature the manifest named — and moves
        # along it to the END NEAREST the column, which is the whole
        # point of the exercise
        top = min(ink, key=lambda s: lc._seg_dist(s[0], s[1], tip))
        xa, xb = sorted((top[0][0], top[1][0]))
        self.assertLess(lc._seg_dist(top[0], top[1], tip), 0.5, top)
        self.assertAlmostEqual(top[0][1], top[1][1], places=6)
        self.assertGreater(tip[0], xa + 0.6 * (xb - xa), (tip, xa, xb))
        # ... and it crosses less of the view for it
        stated = (xa + 4.0, top[0][1])
        self.assertLess(draw.view_ink_crossings(ctx, ink, tip, knee),
                        draw.view_ink_crossings(ctx, ink, stated, knee))

    def test_a_trial_that_reads_worse_is_not_taken(self):
        """Every combination of the two late passes is DRAWN; only one
        that reads better is kept, and a tie keeps the earlier sheet."""
        co = [{"view": "front", "text": "%%c30", "circle_r": TUBE_R,
               "at": [TX0 + 4.0, TUBE_R]}]
        real = draw._compose_part_sheet

        def worse(m, part, proj, **kw):
            got = real(m, part, proj, **kw)
            if kw.get("avoid_view_ink") or kw.get("ext_ahead"):
                got[2].faults.append("fixture: this trial reads worse")
            return got

        draw._compose_part_sheet = worse
        try:
            _ctx, lay = tube_sheet(co)
        finally:
            draw._compose_part_sheet = real
        self.assertEqual(lay["faults"], [], lay["faults"])

    def test_the_leader_does_not_dive_across_its_own_view(self):
        co = [{"view": "front", "text": "%%c50.8", "circle_r": BODY_R,
               "at": [X1 + 3.0, BODY_R]}]
        ctx, lay = build(co)
        found = lc.check_sheet(ctx.render(), ctx.sheet_area,
                               ctx.num / ctx.den)
        self.assertEqual(of_kind(found, "leader-across-own-view"), [])
        self.assertEqual(of_kind(found, "leader-crossing"), [])

    def test_the_arrow_is_still_on_the_feature_it_names(self):
        """Wherever it moves to, it moves along the part's own drawn
        edge — the same circle, so the callout still names the feature
        the designer named."""
        co = [{"view": "front", "text": "%%c50.8", "circle_r": BODY_R,
               "at": [X1 + 3.0, BODY_R]}]
        ctx, _lay = build(co)
        ink = self.drawn_ink(ctx)
        tips = [t for t, _k in self.leaders(ctx)]
        self.assertTrue(tips)
        for t in tips:
            self.assertLess(min(draw._seg_pt_dist(a, b, t)
                                for a, b in ink), 0.6, t)

    def test_a_note_keeps_the_spot_it_was_given(self):
        """A note's arrow means the place it was put: no `circle_r`, no
        other point of a feature to move to. Two sheets with the stated
        point a dozen millimetres apart put the arrow a dozen
        millimetres apart."""
        def tip_for(x):
            ctx, _lay = build([{"view": "front",
                                "text": "LEAD-IN 20%%d x 2",
                                "at": [x, BODY_R]}])
            return self.leaders(ctx)[0][0]

        a, b = tip_for(X1 + 3.0), tip_for(X1 + 15.0)
        self.assertAlmostEqual(b[0] - a[0], 12.0, places=6)
        self.assertAlmostEqual(b[1] - a[1], 0.0, places=6)


# -------------------------------------- weighing one pass against another

class LeaderQualityTest(unittest.TestCase):
    """Keeping a callout off its own view's ink moves its arrow, and
    every callout placed after it settles around the moved one. On a
    crowded sheet that can trade one long leader for two that cross, so
    both sheets are drawn and the better one kept."""

    def layout_with(self, leaders, geom=()):
        ctx = mw.SheetContext(1, 1)
        lay = draw.Layout(ctx)
        lay.geom = list(geom)
        for i, (tip, knee) in enumerate(leaders):
            lay.leaders.append((tip, knee, f"callout-{i}"))
        return lay

    def test_fewer_crossings_wins(self):
        crossing = self.layout_with([((0.0, 0.0), (10.0, 10.0)),
                                     ((0.0, 10.0), (10.0, 0.0))])
        clear = self.layout_with([((0.0, 0.0), (10.0, 10.0)),
                                  ((0.0, 11.0), (10.0, 21.0))])
        self.assertLess(draw.leader_quality(clear),
                        draw.leader_quality(crossing))

    def test_a_crossing_outweighs_any_amount_of_ink(self):
        geom = tube()
        crossing = self.layout_with([((0.0, 0.0), (10.0, 10.0)),
                                     ((0.0, 10.0), (10.0, 0.0))], geom)
        inky = self.layout_with([((70.0, 215.0), (150.0, 180.0))], geom)
        self.assertLess(draw.leader_quality(inky),
                        draw.leader_quality(crossing))

    def test_less_ink_wins_when_nothing_else_differs(self):
        geom = tube()
        inky = self.layout_with([((70.0, 215.0), (150.0, 180.0))], geom)
        clean = self.layout_with([((118.0, 185.0), (150.0, 175.0))], geom)
        self.assertLess(draw.leader_quality(clean),
                        draw.leader_quality(inky))

    def test_the_check_s_own_count_comes_first(self):
        """What the two compositions are weighed by is the layout check:
        its answer on the finished sheet, not the engine's opinion."""
        co = [{"view": "front", "text": "%%c50.8", "circle_r": BODY_R,
               "at": [X1 + 3.0, BODY_R]}]
        import tempfile
        from test_leader_rules import nose_manifest, nose_section
        with tempfile.TemporaryDirectory() as tmp:
            m, part = nose_manifest(Path(tmp), co)
            proj = {"05:front": nose_front(), "05:section": nose_section()}
            ctx, _r, lay = draw._compose_part_sheet(m, part, proj)
        found = lc.check_sheet(ctx.render(), ctx.sheet_area,
                               ctx.num / ctx.den,
                               table_band=getattr(ctx, "_table_band", None))
        q = draw.sheet_quality(ctx, lay)
        self.assertEqual(q[0], len(lc.faults(found)))
        self.assertEqual(q[1], draw.leader_quality(lay))

    def test_a_tie_on_faults_falls_to_the_ink(self):
        co = [{"view": "front", "text": "%%c50.8", "circle_r": BODY_R,
               "at": [X1 + 3.0, BODY_R]}]
        import tempfile
        from test_leader_rules import nose_manifest, nose_section
        with tempfile.TemporaryDirectory() as tmp:
            m, part = nose_manifest(Path(tmp), co)
            proj = {"05:front": nose_front(), "05:section": nose_section()}
            ctx, _r, lay = draw._compose_part_sheet(m, part, proj)
        inky = draw.Layout(ctx)
        inky.geom = list(lay.geom)
        inky.leaders = list(lay.leaders) + [((70.0, 215.0), (150.0, 180.0),
                                             "callout-X")]
        inky.geom += tube()
        self.assertLess(draw.sheet_quality(ctx, lay),
                        draw.sheet_quality(ctx, inky))

    def test_ink_that_belongs_to_something_else_is_weighed_too(self):
        """A dimension line, a sight arrow, the pictorial box: crossing
        one is a fault the check reports, so a pass that does it is not
        better than one that crosses more of the view's own ink."""
        geom = tube()
        over = self.layout_with([((70.0, 215.0), (150.0, 215.0))], geom)
        over.dim_lines.append(((100.0, 240.0), (100.0, 190.0),
                               "the dimension 60"))
        inky = self.layout_with([((70.0, 215.0), (150.0, 180.0))], geom)
        self.assertGreater(draw.leader_quality(over)[2], 0)
        self.assertLess(draw.leader_quality(inky),
                        draw.leader_quality(over))

    def test_a_warning_is_not_a_fault(self):
        """sheet_quality counts what /export would refuse, and a warning
        — two leaders running together — is not that."""
        co = [{"view": "front", "text": "%%c30", "circle_r": TUBE_R,
               "at": [TX0 + 4.0, TUBE_R]}]
        ctx, _lay = tube_sheet(co)
        lay = draw.Layout(ctx)
        x0, y0 = ctx.draw_area[0] + 40.0, ctx.draw_area[1] + 60.0
        ctx.entities.append(mw.leader((x0, y0), (x0 + 40.0, y0), "", 3.2,
                                      props=A))
        ctx.entities.append(mw.leader((x0, y0 + 1.0), (x0 + 40.0, y0 + 1.0),
                                      "", 3.2, props=A))
        found = lc.check_sheet(
            ctx.render(), ctx.sheet_area, ctx.num / ctx.den,
            table_band=getattr(ctx, "_table_band", None),
            boxes=[b for b in (getattr(ctx, "_pictorial_box", None),) if b],
            blocks=getattr(ctx, "blocks", ()))
        self.assertTrue(lc.warnings(found))          # the two run together
        self.assertLess(len(lc.faults(found)), len(found))
        self.assertEqual(draw.sheet_quality(ctx, lay)[0],
                         len(lc.faults(found)))

    def test_a_balloon_is_not_weighed_here(self):
        """Balloons are ordered by their own fan, not by this."""
        lay = self.layout_with([])
        lay.leaders.append(((70.0, 215.0), (150.0, 180.0), "balloon-7-0"))
        lay.geom = tube()
        self.assertEqual(draw.leader_quality(lay), (0, 0, 0, 0))


# ---------------------------------------- what the finished sheet says

class LongWayRoundTest(unittest.TestCase):
    """Read off the sheet: a size leader that crosses its own view where
    the near end of the same drawn edge would not."""

    def view(self):
        """The tube, with its top silhouette drawn as the POLYLINE a
        projected edge really is — one edge, several segments, so the
        end of it nearest a knee is a question with an answer."""
        segs = tube()
        out = [mw.polyline([(60.0, 215.0), (90.0, 215.0), (120.0, 215.0)])]
        return out + [mw.line(a, b) for a, b in segs[1:]]

    def test_the_far_edge_is_reported(self):
        text = sheet(*self.view(),
                     mw.leader((70.0, 215.0), (150.0, 180.0), "%%c30", 3.2,
                               props=A))
        hit = of_kind(lc.check_sheet(text, AREA), "leader-across-own-view")
        self.assertTrue(hit, lc.check_sheet(text, AREA))
        self.assertIn("%%c30", hit[0]["what"])
        self.assertIn("`at`", hit[0]["what"])

    def test_the_near_end_is_not(self):
        text = sheet(*self.view(),
                     mw.leader((118.0, 185.0), (150.0, 175.0), "%%c30",
                               3.2, props=A))
        self.assertEqual(of_kind(lc.check_sheet(text, AREA),
                                 "leader-across-own-view"), [])

    def test_a_note_is_not_asked(self):
        text = sheet(*self.view(),
                     mw.leader((70.0, 215.0), (150.0, 180.0),
                               "LEAD-IN 20%%d x 2", 3.2, props=A))
        self.assertEqual(of_kind(lc.check_sheet(text, AREA),
                                 "leader-across-own-view"), [])

    def test_a_bore_with_no_better_way_out_is_not(self):
        """An internal feature has to cross the wall around it, and no
        other point of its own edge crosses less."""
        text = sheet(*self.view(),
                     mw.leader((118.0, 208.0), (150.0, 225.0), "%%c16",
                               3.2, props=A))
        self.assertEqual(of_kind(lc.check_sheet(text, AREA),
                                 "leader-across-own-view"), [])

    def walls(self):
        """Three walls between a feature and the column: whichever end
        of the feature the arrow sits on, the leader crosses them."""
        return [mw.line((140.0, 160.0), (140.0, 240.0)),
                mw.line((146.0, 160.0), (146.0, 240.0)),
                mw.line((152.0, 160.0), (152.0, 240.0))]

    def test_a_feature_whose_other_end_saves_nothing_is_not_reported(self):
        """The saving is what makes it worth saying: a leader that has
        to cross those walls from either end of its own feature is told
        nothing by being asked to move (issue #33)."""
        feature = mw.polyline([(90.0, 215.0), (105.0, 215.0),
                               (120.0, 215.0)])
        text = sheet(feature, *self.walls(),
                     mw.leader((90.0, 215.0), (200.0, 215.0), "%%c30", 3.2,
                               props=A))
        found = lc.check_sheet(text, AREA)
        self.assertEqual(of_kind(found, "leader-across-own-view"), [],
                         found)

    def test_the_ink_at_the_arrow_is_not_counted_against_it(self):
        """A chamfer line or a hatch boundary beside the arrow is the
        feature's own, and a leader leaving ANY feature crosses it. With
        it counted, this leader would look two crossings worse than the
        alternative and be reported; it is one, and is not."""
        feature = mw.polyline([(60.0, 215.0), (90.0, 215.0),
                               (120.0, 215.0)])
        def at(x):
            return [mw.line((x, 218.0), (x + 0.5, 210.0)),
                    mw.line((x + 1.5, 218.0), (x + 2.0, 210.0))]

        lead = mw.leader((60.0, 215.0), (200.0, 165.0), "%%c30", 3.2,
                         props=A)
        beside = sheet(feature, *at(61.0), *self.walls(), lead)
        self.assertEqual(of_kind(lc.check_sheet(beside, AREA),
                                 "leader-across-own-view"), [])
        # ... and the same two lines further along the view ARE its ink
        along = sheet(feature, *at(67.0), *self.walls(), lead)
        hit = of_kind(lc.check_sheet(along, AREA), "leader-across-own-view")
        self.assertTrue(hit, lc.check_sheet(along, AREA))
        self.assertIn("crosses its own view 5 times", hit[0]["what"])

    def test_an_arrow_a_hair_from_the_near_end_is_not_asked_to_move(self):
        """The real case the guard is for (sheet -07): the arrow sits
        1.8 mm from the end of its own edge, and the ink just beyond it
        — the feature's own, discounted at the arrow but not at a point
        two millimetres along — makes the move look like a saving. It is
        not an instruction worth printing, so it is not printed."""
        edge = mw.polyline([(60.0, 215.0), (90.0, 215.0), (120.0, 215.0)])
        tip, knee = (118.5, 215.0), (200.0, 180.0)
        ux, uy = 81.5, -35.0
        ln = math.hypot(ux, uy)
        ticks = []
        for d in (2.2, 2.5, 2.8):
            px, py = tip[0] + ux / ln * d, tip[1] + uy / ln * d
            ticks.append(mw.line((px - 0.4, py - 1.0), (px + 0.4, py + 1.0)))
        text = sheet(edge, *ticks,
                     mw.leader(tip, knee, "%%c30", 3.2, props=A))
        self.assertEqual(of_kind(lc.check_sheet(text, AREA),
                                 "leader-across-own-view"), [])

    def test_an_arrow_already_at_the_near_end_is_not_asked_to_move(self):
        """It has nowhere to move to: the end of its own edge nearest
        the knee is where it already is, whatever it crosses from
        there."""
        feature = mw.polyline([(90.0, 215.0), (105.0, 215.0),
                               (120.0, 215.0)])
        text = sheet(feature, *self.walls(),
                     mw.leader((120.0, 215.0), (200.0, 215.0), "%%c30",
                               3.2, props=A))
        self.assertEqual(of_kind(lc.check_sheet(text, AREA),
                                 "leader-across-own-view"), [])

    def test_the_edge_is_the_one_the_arrow_sits_on(self):
        """A centre mark crossing the silhouette at the arrow is not the
        feature: the edge is the drawn one the arrow lies along."""
        feature = mw.polyline([(60.0, 215.0), (90.0, 215.0),
                               (120.0, 215.0)])
        tick = mw.line((70.0, 213.0), (70.0, 217.0))
        text = sheet(feature, tick, *[mw.line(a, b) for a, b in tube()[1:]],
                     mw.leader((70.0, 215.0), (150.0, 180.0), "%%c30", 3.2,
                               props=A))
        hit = of_kind(lc.check_sheet(text, AREA), "leader-across-own-view")
        self.assertTrue(hit, lc.check_sheet(text, AREA))
        self.assertIn("50 mm away", hit[0]["what"])

    def test_it_names_what_moving_the_arrow_would_save(self):
        text = sheet(*self.view(),
                     mw.leader((70.0, 215.0), (150.0, 180.0), "%%c30", 3.2,
                               props=A))
        what = of_kind(lc.check_sheet(text, AREA),
                       "leader-across-own-view")[0]["what"]
        self.assertRegex(what, r"crosses its own view \d+ times")
        self.assertRegex(what, r"crosses it \d+")

    def test_it_is_printed_millimetres_at_any_scale(self):
        """The same leader at 1:5 moves its arrow a fifth as far in
        print, which is below the threshold for asking."""
        text = sheet(*[mw.line(a, b) for a, b in tube(cx=62.0, cy=200.0,
                                                      half=2.0,
                                                      length=4.0,
                                                      bore=1.0)],
                     mw.leader((60.5, 202.0), (80.0, 196.0), "%%c30", 3.2,
                               props=A))
        self.assertEqual(of_kind(lc.check_sheet(text, AREA, 1 / 5),
                                 "leader-across-own-view"), [])


# ------------------------------- a stranger dropped into a note column

class CrowdedLabelTest(unittest.TestCase):

    def test_one_pair_is_one_finding(self):
        """Two LEADERS' labels crowd each other once, not once each way
        round."""
        text = sheet(mw.leader((60.0, 200.0), (100.0, 200.0), "%%c75.50",
                               3.2, props=A),
                     mw.leader((60.0, 196.0), (100.0, 196.4), "%%c58 (LIP)",
                               3.2, props=A))
        self.assertEqual(len(of_kind(lc.check_sheet(text, AREA),
                                     "annotation-crowded")), 1)

    def test_the_threshold_follows_the_text_height(self):
        """0.4 of a height: half the leading between the lines of one
        callout's own stack."""
        big = sheet(mw.leader((60.0, 200.0), (100.0, 200.0), "%%c75.50",
                              6.0, props=A),
                    mw.mtext((102.0, 198.0), "%%c58 (LIP)", 6.0, props=A))
        small = sheet(mw.leader((60.0, 200.0), (100.0, 200.0), "%%c75.50",
                                1.0, props=A),
                      mw.mtext((102.0, 198.0), "%%c58 (LIP)", 1.0,
                               props=A))
        self.assertTrue(of_kind(lc.check_sheet(big, AREA),
                                "annotation-crowded"))
        self.assertEqual(of_kind(lc.check_sheet(small, AREA),
                                 "annotation-crowded"), [])


class DiameterFallbackRoomTest(unittest.TestCase):
    """The diameter leader's own label asks for reading room before it
    takes the bare clearance between claims."""

    def setUp(self):
        self.ctx = mw.SheetContext(1, 1)
        self.ctx.dimstyles = draw.sheet_dimstyles(self.ctx)
        self.layout = draw.Layout(self.ctx)
        ax0, ay0, ax1, ay1 = self.ctx.draw_area
        self.cx, self.cy = ax0 + 60.0, ay0 + 120.0
        self.job = {
            "visible": [{"t": "circle", "c": [self.cx, self.cy], "r": 30.0}],
            "hidden": [],
            "bounds": [[self.cx - 30.0, self.cy - 30.0],
                       [self.cx + 30.0, self.cy + 30.0]]}

    def place(self):
        return draw.dim_circles(self.ctx, self.layout, self.job,
                                (0.0, 0.0))

    def first_rung_ink(self):
        """Where the label would land on the rung the ladder tries
        first (dim_circles' own arithmetic)."""
        h = self.ctx.mm(3.0)
        kx = self.cx + 30.0 + self.ctx.mm(8.0)
        ky = self.cy + self.ctx.mm(8.0)
        return mw.leader_text_ink((kx, ky), "%%c60", h, self.ctx.mm(2.5))

    def test_the_label_keeps_clear_of_a_callout_already_there(self):
        """A note parked a millimetre above where the first rung would
        put the label: the engine's own 0.8 mm clearance allows it, and
        at that distance the two print as one block (issue #33)."""
        h = self.ctx.mm(3.0)
        ink = self.first_rung_ink()
        self.layout.claim((ink[0], ink[3] + 1.0, ink[0] + 40.0,
                           ink[3] + 1.0 + h), "callout-X")
        recs, rects = self.place()
        self.assertTrue(recs and rects)
        got = list(rects.values())[0]
        self.assertGreaterEqual(
            self.layout.anno_clearance(got, ignore=got),
            draw.ANNO_READ_FRAC * h)
        # ... which means it did NOT take the rung it would have before
        self.assertNotAlmostEqual(got[1], ink[1], places=3)

    def test_the_fallback_keeps_its_leader_off_the_view(self):
        """The same question the callouts are asked (issue #33): a rung
        whose leader crosses the view it points into is passed over
        while a cleaner one is left."""
        ink = self.first_rung_ink()
        # a drawn edge across the first rung's own path, and nothing in
        # the way of the rungs below it
        wall = [((self.cx + 34.0, self.cy + 2.0),
                 (self.cx + 34.0, self.cy + 40.0))]
        _recs, rects = draw.dim_circles(self.ctx, self.layout, self.job,
                                        (0.0, 0.0), geom=wall)
        self.assertTrue(rects)
        got = list(rects.values())[0]
        self.assertNotAlmostEqual(got[1], ink[1], places=3)
        self.assertLess(got[1], ink[1])        # it went the other way

    def test_a_label_with_room_everywhere_takes_the_first_rung(self):
        """Nothing else in the column: the ladder's own first rung, as
        it always was."""
        ink = self.first_rung_ink()
        _recs, rects = self.place()
        self.assertTrue(rects)
        got = list(rects.values())[0]
        self.assertAlmostEqual(got[1], ink[1], places=3)
