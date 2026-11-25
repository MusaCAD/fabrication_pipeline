# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Pranay Kiran
"""Where a balloon's arrow is allowed to land (issue #32).

Run with:  python3 -m unittest discover -s tests -v

The real sheet: an A3-landscape assembly at 1:5. A thin tube closed in on
every side — its outside against the bore of the part around it, its own
bore a tenth of a millimetre off the part inside it, both ends against
neighbours — draws no ink that is its alone at that scale, so its arrow
fell back to the instance's bounding-box CENTRE. For a tube that centre
is on its axis, inside the part running through it: the balloon pointed
at the wrong part and nothing reported it.

Thirteen of that sheet's twenty-six balloons were landing that way, and
most of them were ballooned on the OUTSIDE view, which draws nothing of
a part buried in the hull, because the plan took the first view that
held the part rather than the one that could point at it.

The fixtures are that shape: a tube inside a tube, both cut in the
section, and a part the front view does not draw at all.
"""

import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import draw  # noqa: E402
import musa_writer as mw  # noqa: E402


def seg(a, b):
    return {"t": "seg", "a": list(a), "b": list(b)}


def box(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def edges_of(x0, y0, x1, y1):
    return [seg((x0, y0), (x1, y0)), seg((x1, y0), (x1, y1)),
            seg((x1, y1), (x0, y1)), seg((x0, y1), (x0, y0))]


def nested_section():
    """A section of two tubes, one inside the other, drawn one on top of
    the other: the shell's bore line and the sleeve's outside are the
    same line on the sheet, so neither part has an edge of its own.

    The shell's wall is 10 units thick, the sleeve's 2 — the sleeve is
    the tube of the issue."""
    shell = edges_of(0.0, 10.0, 200.0, 20.0)
    sleeve = edges_of(0.0, 8.0, 200.0, 10.0)
    return {
        "visible": shell + sleeve,
        "hidden": [],
        "bounds": [[0.0, 0.0], [200.0, 20.0]],
        "instances": [
            {"name": "A/Shell", "center": [100.0, 15.0], "edges": shell},
            {"name": "A/Sleeve", "center": [100.0, 9.0], "edges": sleeve},
        ],
        "hatch_groups": [
            {"outer": box(0.0, 10.0, 200.0, 20.0), "holes": [],
             "instance": "A/Shell"},
            {"outer": box(0.0, 8.0, 200.0, 10.0), "holes": [],
             "instance": "A/Sleeve"},
        ]}


def outside_view():
    """The view from outside: the shell is drawn, the sleeve inside it is
    not — its instance is there, with edges, and none of them is ink the
    view puts on the sheet."""
    shell = edges_of(0.0, 10.0, 200.0, 20.0)
    sleeve = edges_of(0.0, 8.0, 200.0, 10.0)
    return {
        "visible": shell,
        "hidden": [],
        "bounds": [[0.0, 0.0], [200.0, 20.0]],
        "instances": [
            {"name": "A/Shell", "center": [100.0, 15.0], "edges": shell},
            {"name": "A/Sleeve", "center": [100.0, 9.0], "edges": sleeve},
        ],
        "hatch_groups": []}


class LandingKindTest(unittest.TestCase):

    def setUp(self):
        self.ctx = mw.SheetContext(1, 5)
        self.job = nested_section()
        self.knee = (260.0, 40.0)

    def test_a_part_with_ink_of_its_own_lands_on_it(self):
        """The shell's outside is 10 units from anything else — a clean
        2 printed mm at 1:5 — so its arrow sits on the drawn edge."""
        p, kind = draw.balloons_land(self.ctx, self.job, (0.0, 0.0), 0,
                                     self.knee)
        self.assertEqual(kind, "edge")
        self.assertTrue(any(abs(p[1] - y) < 1e-6 for y in (10.0, 20.0))
                        or abs(p[0] - 200.0) < 1e-6, p)

    def test_a_part_closed_in_lands_inside_its_own_cut(self):
        p, kind = draw.balloons_land(self.ctx, self.job, (0.0, 0.0), 1,
                                     self.knee)
        self.assertEqual(kind, "area")
        self.assertTrue(8.0 < p[1] < 10.0, p)      # in the sleeve's wall
        self.assertTrue(0.0 <= p[0] <= 200.0, p)

    def test_it_is_never_the_instance_centre(self):
        """What the sheet used to draw: the sleeve's centre is on its
        axis, inside the part through it."""
        p, _kind = draw.balloons_land(self.ctx, self.job, (0.0, 0.0), 1,
                                      self.knee)
        self.assertNotEqual((round(p[0], 6), round(p[1], 6)),
                            (100.0, 9.0))

    def test_the_dot_is_nearest_the_knee(self):
        near = draw.balloons_land(self.ctx, self.job, (0.0, 0.0), 1,
                                  (260.0, 40.0))[0]
        far = draw.balloons_land(self.ctx, self.job, (0.0, 0.0), 1,
                                 (-60.0, 40.0))[0]
        self.assertGreater(near[0], far[0])

    def test_a_view_that_draws_nothing_of_it_is_reported(self):
        p, kind = draw.balloons_land(self.ctx, outside_view(), (0.0, 0.0),
                                     1, self.knee)
        self.assertEqual(kind, "shared")
        self.assertTrue(any(math.dist(p, q) < 1e-6
                            for q in ((0.0, 8.0), (200.0, 8.0),
                                      (0.0, 10.0), (200.0, 10.0),
                                      (100.0, 8.0), (100.0, 10.0))), p)

    def test_an_instance_with_no_edges_at_all_is_the_centre(self):
        job = dict(outside_view())
        job["instances"] = [dict(job["instances"][0]),
                            dict(job["instances"][1], edges=[])]
        p, kind = draw.balloons_land(self.ctx, job, (0.0, 0.0), 1,
                                     self.knee)
        self.assertEqual(kind, "centre")
        self.assertEqual((round(p[0], 6), round(p[1], 6)), (100.0, 9.0))

    def test_the_offset_moves_the_landing_with_the_view(self):
        p, _k = draw.balloons_land(self.ctx, self.job, (50.0, 7.0), 1,
                                   (310.0, 47.0))
        self.assertTrue(15.0 < p[1] < 17.0, p)

    def test_an_edge_a_hair_from_its_neighbour_is_still_its_own(self):
        """The second ink pass (issue #32): a part whose own edge runs
        half a millimetre from its neighbour's draws no ink the coarse
        test will take, and an arrow on it is a great deal better than a
        shared edge — which is all this part would get otherwise."""
        mine = [seg((0.0, 10.0), (100.0, 10.0))]
        theirs = [seg((0.0, 9.5), (100.0, 9.5))]
        job = {"visible": mine + theirs, "hidden": [],
               "bounds": [[0.0, 0.0], [100.0, 20.0]],
               "instances": [{"name": "A/Mine", "center": [50.0, 10.0],
                              "edges": mine},
                             {"name": "A/Theirs", "center": [50.0, 9.5],
                              "edges": theirs}],
               "hatch_groups": []}
        ctx = mw.SheetContext(1, 1)
        knee = (150.0, 40.0)
        # the fixture is the case: the coarse test finds nothing here
        self.assertIsNone(draw.own_ink_spot(ctx, job, (0.0, 0.0), 0, knee,
                                            draw.BALLOON_INK_CELL_MM))
        self.assertIsNotNone(draw.own_ink_spot(ctx, job, (0.0, 0.0), 0,
                                               knee,
                                               draw.BALLOON_FINE_CELL_MM))
        p, kind = draw.balloons_land(ctx, job, (0.0, 0.0), 0, knee)
        self.assertEqual(kind, "edge")
        self.assertAlmostEqual(p[1], 10.0, places=6)

    def test_landing_kind_reads_the_same_from_any_knee(self):
        self.assertEqual(draw.landing_kind(self.ctx, self.job,
                                           (0.0, 0.0), 1), "area")
        self.assertEqual(draw.balloons_land(self.ctx, self.job, (0.0, 0.0),
                                            1, (-500.0, -500.0))[1], "area")


class ThinCutTest(unittest.TestCase):
    """A dot has to fit in the cut it marks — and a tube wall at 1:5 is
    thinner than the clearance the dot would like."""

    def setUp(self):
        self.ctx = mw.SheetContext(1, 5)

    def spots(self, thickness):
        job = {"hatch_groups": [{"outer": box(0.0, 0.0, 200.0, thickness),
                                 "holes": [],
                                 "instance": "A/Sleeve"}]}
        return draw.cut_area_spots(self.ctx, job, (0.0, 0.0), "A/Sleeve")

    def test_a_wide_cut_keeps_the_dot_s_own_clearance(self):
        spots = self.spots(40.0)
        self.assertTrue(spots)
        inset = self.ctx.mm(draw.BALLOON_DOT_INSET_MM)
        self.assertTrue(all(inset <= p[1] <= 40.0 - inset for p in spots))

    def test_a_thin_cut_takes_the_deepest_it_has(self):
        """1.5 units is 0.3 printed mm at 1:5: no point in it is a
        millimetre clear of a wall, and the middle of the wall is still
        the one place that is this part and nothing else."""
        spots = self.spots(1.5)
        self.assertTrue(spots)
        self.assertTrue(all(0.0 < p[1] < 1.5 for p in spots), spots)
        self.assertTrue(any(abs(p[1] - 0.75) < 0.2 for p in spots), spots)

    def test_a_corner_of_the_bounding_box_is_not_inside_the_part(self):
        """A cut area is not its own bounding box: an L-shaped one — a
        shoulder, a stepped bore — leaves a whole quadrant outside it,
        and a dot there sits on the neighbour."""
        ell = [[0.0, 0.0], [100.0, 0.0], [100.0, 40.0], [60.0, 40.0],
               [60.0, 100.0], [0.0, 100.0]]
        job = {"hatch_groups": [{"outer": ell, "holes": [],
                                 "instance": "A/Sleeve"}]}
        spots = draw.cut_area_spots(self.ctx, job, (0.0, 0.0), "A/Sleeve")
        self.assertTrue(spots)
        self.assertFalse([p for p in spots
                          if p[0] > 60.0 and p[1] > 40.0], spots)
        near = min(spots, key=lambda p: math.dist(p, (140.0, 140.0)))
        self.assertTrue(near[0] <= 60.0 or near[1] <= 40.0, near)

    def test_an_island_is_not_inside_the_part(self):
        job = {"hatch_groups": [{"outer": box(0.0, 0.0, 200.0, 40.0),
                                 "holes": [box(80.0, 10.0, 120.0, 30.0)],
                                 "instance": "A/Sleeve"}]}
        spots = draw.cut_area_spots(self.ctx, job, (0.0, 0.0), "A/Sleeve")
        self.assertTrue(spots)
        self.assertFalse([p for p in spots
                          if 80.0 < p[0] < 120.0 and 10.0 < p[1] < 30.0])

    def test_another_part_s_cut_is_not_this_part_s(self):
        job = {"hatch_groups": [{"outer": box(0.0, 0.0, 200.0, 40.0),
                                 "holes": [], "instance": "A/Shell"}]}
        self.assertEqual(draw.cut_area_spots(self.ctx, job, (0.0, 0.0),
                                             "A/Sleeve"), [])

    def test_the_answer_is_measured_once_per_view(self):
        job = {"hatch_groups": [{"outer": box(0.0, 0.0, 200.0, 40.0),
                                 "holes": [], "instance": "A/Sleeve"}]}
        first = draw.cut_area_spots(self.ctx, job, (0.0, 0.0), "A/Sleeve")
        job["hatch_groups"] = []            # the cache answers now
        self.assertEqual(draw.cut_area_spots(self.ctx, job, (0.0, 0.0),
                                             "A/Sleeve"), first)


class DotTerminatorTest(unittest.TestCase):
    """ISO 6433: a leader that ends inside the outline of the thing it
    names ends in a dot, not an arrow."""

    def setUp(self):
        self.ctx = mw.SheetContext(1, 5)
        self.ctx.dimstyles = draw.sheet_dimstyles(self.ctx)

    def test_the_style_is_made_once_and_says_dot(self):
        idx = draw.dot_style_index(self.ctx)
        self.assertEqual(idx, draw.dot_style_index(self.ctx))
        rec = self.ctx.dimstyles[idx].split()
        self.assertEqual(rec[0], "DIMSTYLE")
        self.assertEqual(int(rec[3]), 3)       # ArrowType::Dot
        self.assertIn("Dot", self.ctx.dimstyles[idx])

    def test_a_sheet_that_needs_no_dot_writes_the_styles_it_always_did(self):
        self.assertEqual(len(self.ctx.dimstyles), 2)

    def test_the_house_style_still_draws_an_arrow(self):
        self.assertEqual(int(self.ctx.dimstyles[0].split()[3]), 0)

    def test_a_leader_can_name_the_style(self):
        rec = mw.leader((0.0, 0.0), (10.0, 10.0), "", 3.5,
                        style=draw.dot_style_index(self.ctx))
        self.assertEqual(int(rec.split("\n")[0].split()[6]), 2)


# --------------------------------- what the fan draws for each landing

class FanTerminatorTest(unittest.TestCase):
    """The fan itself: the dot on a leader that ends inside a cut area,
    and the fault on one that has nowhere of its own to end."""

    def setUp(self):
        self.ctx = mw.SheetContext(1, 5)
        self.ctx.dimstyles = draw.sheet_dimstyles(self.ctx)
        self.layout = draw.Layout(self.ctx)
        area = self.ctx.draw_area
        self.x0 = area[0] + self.ctx.mm(20.0)
        self.y0 = (area[1] + area[3]) / 2.0

    def moved(self, job):
        """The fixture jobs are drawn about the origin; put them where
        the sheet's drawing area is."""
        off = (self.x0, self.y0)
        return job, off

    def fan(self, job, k):
        j, off = self.moved(job)
        recs, _next = draw.balloons_fan(
            self.ctx, self.layout, j, off, [(k, "02")],
            start_y=self.y0 + 60.0,
            knee_x=self.x0 + 300.0)
        return recs

    @staticmethod
    def leader_style(recs):
        for r in recs:
            if r.startswith("LEADER "):
                return int(r.split("\n")[0].split()[6])
        return None

    def test_a_dot_leader_names_the_dot_style(self):
        recs = self.fan(nested_section(), 1)
        self.assertEqual(self.leader_style(recs),
                         draw.dot_style_index(self.ctx))
        self.assertEqual(self.layout.faults, [])

    def test_an_ordinary_landing_keeps_the_house_arrow(self):
        recs = self.fan(nested_section(), 0)
        self.assertEqual(self.leader_style(recs), 0)
        self.assertEqual(self.layout.faults, [])

    def test_a_part_with_nothing_of_its_own_is_reported(self):
        recs = self.fan(outside_view(), 1)
        self.assertTrue(recs)
        self.assertEqual(len(self.layout.faults), 1, self.layout.faults)
        fault = self.layout.faults[0]
        self.assertTrue(fault.startswith("balloon-02:"), fault)
        self.assertIn("no ink that is this part's alone", fault)
        self.assertIn("detail view", fault)
        self.assertIn("balloon_in", fault)

    def test_a_part_with_no_edges_at_all_says_so(self):
        job = dict(outside_view())
        job["instances"] = [dict(job["instances"][0]),
                            dict(job["instances"][1], edges=[])]
        self.fan(job, 1)
        self.assertEqual(len(self.layout.faults), 1)
        self.assertIn("nothing of its own to land on", self.layout.faults[0])


# ------------------------------------- which view the balloon goes in

def id_map():
    return {"A/Shell": "01", "A/Sleeve": "02"}


def plan_for(ctx, views, offs, balloon_in=None, details=()):
    m = {"parts": [{"part_id": "01"}, {"part_id": "02"}],
         "assembly_sheet": {"balloon_in": balloon_in or {},
                            "detail_views": list(details)}}
    return draw.balloon_plan(m, views, id_map(), ctx, offs)


class BalloonPlanTest(unittest.TestCase):
    """A part is ballooned in the view that can point at it, not in the
    first view that happens to hold it (issue #32)."""

    def setUp(self):
        self.ctx = mw.SheetContext(1, 5)
        self.views = {"front": outside_view(), "section": nested_section()}
        self.offs = {"front": (0.0, 0.0), "section": (0.0, -300.0)}

    def test_a_part_the_front_view_draws_keeps_the_front_view(self):
        self.assertEqual(plan_for(self.ctx, self.views, self.offs)["01"][0],
                         "front")

    def test_a_part_it_does_not_draw_goes_where_it_can_be_pointed_at(self):
        self.assertEqual(plan_for(self.ctx, self.views, self.offs)["02"][0],
                         "section")

    def test_the_manifest_s_own_choice_is_kept_whatever_it_costs(self):
        plan = plan_for(self.ctx, self.views, self.offs,
                        balloon_in={"02": "front"})
        self.assertEqual(plan["02"][0], "front")

    def test_a_detail_that_lists_the_part_still_wins(self):
        views = dict(self.views, **{"detail:B": nested_section()})
        offs = dict(self.offs, **{"detail:B": (0.0, 500.0)})
        plan = plan_for(self.ctx, views, offs,
                        details=[{"label": "B", "balloons": ["02"]}])
        self.assertEqual(plan["02"][0], "detail:B")

    def test_without_a_context_it_takes_the_order_it_always_did(self):
        m = {"parts": [{"part_id": "02"}], "assembly_sheet": {}}
        plan = draw.balloon_plan(m, self.views, id_map())
        self.assertEqual(plan["02"][0], "front")

    def test_a_part_no_view_holds_is_not_ballooned(self):
        m = {"parts": [{"part_id": "09"}], "assembly_sheet": {}}
        self.assertEqual(draw.balloon_plan(m, self.views, id_map(),
                                           self.ctx, self.offs), {})


# ------------------------------------------ the mark of a nested detail

class DetailMarkerTest(unittest.TestCase):
    """A detail's letter has to read as ITS circle's: a small region deep
    inside a view, or one nested inside another detail's circle, walked
    its ladder out to the view's edge and landed in a row with the other
    letters, naming neither region."""

    def setUp(self):
        self.ctx = mw.SheetContext(1, 5)
        self.layout = draw.Layout(self.ctx)
        self.parent = {"visible": [], "hidden": [],
                       "bounds": [[300.0, 700.0], [700.0, 900.0]]}

    def marker(self, spec, ink=()):
        parent = dict(self.parent, visible=list(ink))
        return draw.detail_marker(self.ctx, self.layout, spec, parent,
                                  (0.0, 0.0))

    @staticmethod
    def letter(recs):
        for r in recs:
            if r.startswith("MTEXT"):
                t = r.split("\n")[0].split()
                return (float(t[1]), float(t[2]))
        return None

    @staticmethod
    def tie(recs):
        return [r for r in recs if r.startswith("LINE")]

    def near(self, spec):
        """The ring the letter has to sit in to read as this circle's."""
        r = spec["radius"]
        return r + self.ctx.mm(1.6) + max(
            self.ctx.mm(draw.DETAIL_LETTER_NEAR_MM), 0.35 * r)

    def test_a_letter_sits_beside_its_own_circle(self):
        spec = {"label": "D", "center": [500.0, 800.0], "radius": 6.0}
        recs = self.marker(spec)
        at = self.letter(recs)
        self.assertIsNotNone(at)
        self.assertLess(math.dist(at, (500.0, 800.0)), self.near(spec))
        self.assertEqual(self.tie(recs), [])

    def test_a_big_circle_s_letter_may_stand_further_off(self):
        spec = {"label": "B", "center": [500.0, 800.0], "radius": 60.0}
        recs = self.marker(spec)
        at = self.letter(recs)
        self.assertGreater(math.dist(at, (500.0, 800.0)), 60.0)
        self.assertLess(math.dist(at, (500.0, 800.0)), self.near(spec))

    def test_a_letter_with_no_room_near_is_tied_to_its_circle(self):
        """Every near spot taken — the view's own ink all round it — so
        the letter goes where it can be read, with a line to its mark."""
        # the disc stack: ink every few units all round the region, as
        # a 5:1 detail of a crowded section has
        ink = [seg((460.0, y), (540.0, y))
               for y in range(760, 846, 6)]
        ink += [seg((x, 760.0), (x, 845.0))
                for x in range(460, 546, 6)]
        self.layout.claim((380.0, 700.0, 620.0, 830.0), "assembly-view",
                          "view")
        recs = self.marker({"label": "D", "center": [500.0, 800.0],
                            "radius": 6.0}, ink=ink)
        self.assertTrue(self.tie(recs), recs)
        t = self.tie(recs)[0].split()
        start = (float(t[1]), float(t[2]))
        self.assertAlmostEqual(math.dist(start, (500.0, 800.0)), 6.0,
                               places=6)

    def test_a_letter_over_the_view_needs_no_tie(self):
        """The view's CLAIM is a box; a letter may sit in a gap in its
        ink, as a section letter does."""
        self.layout.claim((380.0, 700.0, 620.0, 830.0), "assembly-view",
                          "view")
        spec = {"label": "D", "center": [500.0, 800.0], "radius": 6.0}
        recs = self.marker(spec)
        at = self.letter(recs)
        self.assertIsNotNone(at)
        self.assertLess(math.dist(at, (500.0, 800.0)), self.near(spec))
        self.assertEqual(self.tie(recs), [])

    def test_the_circle_is_always_drawn_where_the_region_is(self):
        recs = self.marker({"label": "D", "center": [500.0, 800.0],
                            "radius": 6.0})
        head = recs[0].split()
        self.assertEqual(head[0], "CIRCLE")
        self.assertEqual((float(head[1]), float(head[2]), float(head[3])),
                         (500.0, 800.0, 6.0))

    def test_a_continuation_detail_says_which_sheet(self):
        recs = draw.detail_marker(self.ctx, self.layout,
                                  {"label": "D", "center": [500.0, 800.0],
                                   "radius": 6.0},
                                  self.parent, (0.0, 0.0), sheet=2)
        self.assertTrue(any("D (SH 2)" in r for r in recs), recs)
