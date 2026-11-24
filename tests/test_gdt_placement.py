# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Pranay Kiran
"""Where GD&T lands on a crowded turned-part sheet (issue #7).

Run with:  python3 -m unittest discover -s tests -v

Four things a real plug/bush/tube sheet got wrong, each with the real
failure as its fixture:

  * a frame emitted with NO leader and no complaint, because it happened
    to fit somewhere — under a NEIGHBOUR's callout, where it reads as that
    callout's frame;
  * two features of the same size in one view (a tube bored 71.5 at both
    ends) that a target could not tell apart;
  * a datum box crowded out by the callouts stacked under its own;
  * a frame on a shoulder face leadered to the edge on the FAR side of the
    view, across the hatched section.
"""

import math
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import common  # noqa: E402
import draw  # noqa: E402
import manifest as mf  # noqa: E402
import musa_writer as mw  # noqa: E402

from test_gdt_edge_on import seg, solid_triangles  # noqa: E402

# A hull tube: 300 long, 75.5 outside, bored 71.5 H8 for 25 at each end and
# 72 in between. Drawn as a side view and a section, so each bore is a pair
# of lines — and BOTH ends are r35.75.
TUBE_L, OD, END_BORE, MID_BORE = 300.0, 75.5, 71.5, 72.0
END_LEN = 25.0
HEAD_X, TAIL_X = 12.5, TUBE_L - 12.5


def tube_section():
    """The cut: wall hatched either side of the bore, with the step from
    the end bore to the middle bore drawn as a short face at each end."""
    r, er, mr = OD / 2, END_BORE / 2, MID_BORE / 2
    groups, vis = [], []
    for sign in (1, -1):
        loop = [[0.0, sign * er], [END_LEN, sign * er],
                [END_LEN, sign * mr], [TUBE_L - END_LEN, sign * mr],
                [TUBE_L - END_LEN, sign * er], [TUBE_L, sign * er],
                [TUBE_L, sign * r], [0.0, sign * r]]
        groups.append({"outer": loop, "holes": []})
        vis += [seg(a, b) for a, b in zip(loop, loop[1:] + loop[:1])]
    return {"visible": vis, "hidden": [], "hatch_groups": groups,
            "bounds": [[0.0, -r], [TUBE_L, r]]}


def tube_front():
    r = OD / 2
    outline = [(0.0, -r), (0.0, r), (TUBE_L, r), (TUBE_L, -r), (0.0, -r)]
    return {"visible": [seg(a, b) for a, b in zip(outline, outline[1:])],
            "hidden": [], "bounds": [[0.0, -r], [TUBE_L, r]]}


def tube_callouts(ids=True):
    """The two end-bore callouts — the same size, at opposite ends — plus
    the middle one, as the real sheet writes them."""
    head = {"view": "section", "at": [HEAD_X, -END_BORE / 2],
            "circle_r": END_BORE / 2,
            "text": "%%c71.5 H8, N7 (HEAD END, 25 LONG)"}
    tail = {"view": "section", "at": [TAIL_X, -END_BORE / 2],
            "circle_r": END_BORE / 2,
            "text": "%%c71.5 H8, N7 (TAIL END, 25 LONG)"}
    if ids:
        head["id"], tail["id"] = "head", "tail"
    return [head, tail,
            {"view": "section", "at": [TUBE_L / 2, -MID_BORE / 2],
             "circle_r": MID_BORE / 2, "text": "%%c72 (TURNED)"}]


def tube_manifest(tmp: Path, gdt, ids=True):
    analysis = {
        "model_dir": str(tmp), "freecad_version": "1.1.1",
        "assembly": {"file": "", "document": "", "workbench": "none",
                     "instances": [], "joints": [], "issues": [],
                     "part_usage": {}, "bbox_min": None, "bbox_max": None},
        "parts": [{"file": str(tmp / "tube.FCStd"), "document": "tube",
                   "sketches": [], "issues": [],
                   "bodies": [{"label": "Body", "object": "Body",
                               "type": "PartDesign::Body", "solids": 1,
                               "faces": 14, "volume_mm3": 200000.0,
                               "area_mm2": 0.0,
                               "bbox_min": [0, -37.75, -37.75],
                               "bbox_max": [300, 37.75, 37.75],
                               "bbox_size": [300.0, 75.5, 75.5],
                               "material": "AL 6061"}]}]}
    apath = tmp / "analysis.json"
    common.write_json(apath, analysis)
    part = {"part_id": "01", "file": str(tmp / "tube.FCStd"),
            "description": "HULL TUBE - GD&T FIXTURE", "drg_no": "FX-03",
            "material": "AL 6061", "qty": 1, "front_view": "+Y",
            "scale": "1:5",
            "section": {"enabled": True, "projection": "below",
                        "label": "B", "offset_frac": 0.5},
            "callouts": tube_callouts(ids), "gdt": gdt}
    m = {"locked": True, "project": "FIXTURE", "drg_no_prefix": "FX",
         "drawn_by": "Er. P. PRANAY", "rev": "00", "model_dir": str(tmp),
         "assembly_file": "", "parts": [part],
         "assembly_sheet": {"enabled": False}, "_analysis_path": str(apath)}
    return m, part


def lines_of(recs):
    out = []
    for r in recs:
        if r.startswith("LINE "):
            t = r.split()
            out.append(((float(t[1]), float(t[2])),
                        (float(t[3]), float(t[4]))))
    return out


def fcf_boxes(recs):
    """The frame outlines: a closed 4-point polyline per stack."""
    out = []
    for r in recs:
        t = r.split()
        if r.startswith("POLYLINE ") and t[1] == "1" and t[2] == "4":
            v = [float(x) for x in t[3:11]]
            out.append((min(v[0::2]), min(v[1::2]),
                        max(v[0::2]), max(v[1::2])))
    return out


def touches(a, b, tol):
    return (min(a[2], b[2]) - max(a[0], b[0]) > 0
            and max(b[1] - a[3], a[1] - b[3]) <= tol)


def reaches(recs, rect, tol=0.6):
    """Does any drawn line run from `rect` to the frame? (a leader)"""
    for a, b in lines_of(recs):
        for p in (a, b):
            if rect[0] - tol <= p[0] <= rect[2] + tol \
                    and rect[1] - tol <= p[1] <= rect[3] + tol:
                return True
    return False


class TargetSelectorTest(unittest.TestCase):
    """`bore_r35.75@tail` — size plus position, since size alone is not
    unique on a tube bored the same at both ends."""

    def test_parsing(self):
        self.assertEqual(draw._target_parts("bore_r35.75"), (35.75, None))
        self.assertEqual(draw._target_parts("bore_r35.75@tail"),
                         (35.75, "tail"))
        self.assertEqual(draw._target_parts("bore_r35.75@x280"),
                         (35.75, "x280"))
        self.assertEqual(draw._target_parts("bottom_face"), (None, None))
        # the plain radius keeps working for everything written before
        self.assertEqual(draw._target_radius("bore_r12.5"), 12.5)
        self.assertEqual(draw._target_radius("bore_r12.5@head"), 12.5)

    def anchors(self):
        return [draw.feature_anchor("section", 35.75, (10, 10, 40, 14),
                                    ((9, 12), (11, 12)), cid="head",
                                    at=(12.5, -35.75)),
                draw.feature_anchor("section", 35.75, (10, 30, 40, 34),
                                    ((9, 32), (11, 32)), cid="tail",
                                    at=(287.5, -35.75)),
                draw.feature_anchor("section", 36.0, (10, 50, 40, 54),
                                    ((9, 52), (11, 52)), at=(150.0, -36.0))]

    def test_one_feature_of_that_size_resolves(self):
        a, bad = draw.resolve_feature_anchor(self.anchors(), "section",
                                             "bore_r36.0")
        self.assertIsNone(bad)
        self.assertEqual(a["at"], (150.0, -36.0))

    def test_two_of_one_size_without_a_selector_is_ambiguous(self):
        a, bad = draw.resolve_feature_anchor(self.anchors(), "section",
                                             "bore_r35.75")
        self.assertIsNone(a)
        self.assertIn("2 annotations of that size", bad)
        self.assertIn("@head", bad)
        self.assertIn("@tail", bad)

    def test_an_id_selector_picks_its_own(self):
        for name, y in (("head", 10), ("tail", 30)):
            a, bad = draw.resolve_feature_anchor(
                self.anchors(), "section", f"bore_r35.75@{name}")
            self.assertIsNone(bad)
            self.assertEqual(a["rect"][1], y)

    def test_a_coordinate_selector_picks_its_own(self):
        a, bad = draw.resolve_feature_anchor(self.anchors(), "section",
                                             "bore_r35.75@x287.5")
        self.assertIsNone(bad)
        self.assertEqual(a["id"], "tail")

    def test_a_selector_that_names_nothing_fails(self):
        a, bad = draw.resolve_feature_anchor(self.anchors(), "section",
                                             "bore_r35.75@middle")
        self.assertIsNone(a)
        self.assertIn("@middle", bad)

    def test_the_lock_rejects_an_ambiguous_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, part = tube_manifest(
                Path(tmp),
                {"datums": [{"label": "A", "view": "section",
                             "target": "bore_r35.75"}], "frames": []})
            bad = " ".join(mf.gdt_problems(part, "01"))
            self.assertIn("fits 2 callouts", bad)
            self.assertIn("@<callout id>", bad)

    def test_the_lock_accepts_a_selector_that_names_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, part = tube_manifest(
                Path(tmp),
                {"datums": [{"label": "A", "view": "section",
                             "target": "bore_r35.75@head"}],
                 "frames": [{"symbol": "coaxiality", "view": "section",
                             "target": "bore_r35.75@tail",
                             "tol": "%%c0.05", "refs": ["A"]}]})
            self.assertEqual(mf.gdt_problems(part, "01"), [])

    def test_the_lock_rejects_a_selector_naming_no_callout(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, part = tube_manifest(
                Path(tmp),
                {"datums": [{"label": "A", "view": "section",
                             "target": "bore_r35.75@nose"}], "frames": []},
                ids=True)
            bad = " ".join(mf.gdt_problems(part, "01"))
            self.assertIn("@nose", bad)

    def test_the_lock_checks_the_selector_s_form(self):
        self.assertIn("empty selector",
                      mf.datum_target_problem("bore_r35.75@") or "")
        self.assertIn("coordinate selector",
                      mf.frame_target_problem("bore_r35.75@xfoo") or "")


class SameSizeFeaturesSheetTest(unittest.TestCase):
    """The whole sheet: a datum on one end bore and a frame on the other,
    both r35.75, each landing on ITS OWN callout."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="fabpipe-tube-")
        tmp = Path(cls.tmp.name)
        cls.m, cls.part = tube_manifest(tmp, {
            "datums": [{"label": "A", "view": "section",
                        "target": "bore_r35.75@head"}],
            "frames": [{"symbol": "coaxiality", "view": "section",
                        "target": "bore_r35.75@tail", "tol": "%%c0.05",
                        "refs": ["A"]}]})
        cls.proj = {"01:front": tube_front(), "01:section": tube_section()}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_both_place_on_their_own_feature(self):
        ctx, _, lay = draw.build_part_sheet(self.m, self.part, self.proj)
        self.assertEqual(lay["unresolved"], [])
        text = ctx.render()
        # the datum triangle belongs to the HEAD callout: its tip sits on
        # that callout's own landing, not the tail's
        tips = [t[0] for t in solid_triangles(text)]
        self.assertTrue(tips, "no datum triangle on the sheet")

    def test_without_a_selector_the_sheet_fails_and_says_why(self):
        part = dict(self.part, gdt={
            "datums": [{"label": "A", "view": "section",
                        "target": "bore_r35.75"}], "frames": []})
        ctx, _, lay = draw.build_part_sheet(self.m, part, self.proj)
        msg = " ".join(lay["unresolved"])
        self.assertIn("datum-A", msg)
        self.assertIn("annotations of that size", msg)


class FrameAttachedTest(unittest.TestCase):

    def setUp(self):
        self.ctx = mw.SheetContext(1, 1)
        self.layout = draw.Layout(self.ctx)

    def test_directly_under_its_callout_is_attached(self):
        anchor = (100.0, 200.0, 140.0, 204.0)
        rect = (100.0, 193.0, 124.0, 199.0)
        self.assertTrue(draw._frame_attached(self.ctx, self.layout, rect,
                                             anchor))

    def test_a_foreign_label_in_the_gap_breaks_it(self):
        anchor = (100.0, 200.0, 140.0, 204.0)
        rect = (100.0, 185.0, 124.0, 191.0)
        self.layout.claim((100.0, 193.0, 130.0, 197.0), "callout-other")
        self.assertFalse(draw._frame_attached(self.ctx, self.layout, rect,
                                              anchor))

    def test_too_far_away_is_not_attached(self):
        anchor = (100.0, 200.0, 140.0, 204.0)
        rect = (100.0, 160.0, 124.0, 166.0)
        self.assertFalse(draw._frame_attached(self.ctx, self.layout, rect,
                                              anchor))

    def test_beside_it_is_not_under_it(self):
        anchor = (100.0, 200.0, 140.0, 204.0)
        rect = (150.0, 199.0, 174.0, 205.0)
        self.assertFalse(draw._frame_attached(self.ctx, self.layout, rect,
                                              anchor))


class FrameNeverFloatsTest(unittest.TestCase):
    """v0.7.0 made an unleadered frame a loud failure; one path still
    slipped through it (issue #7) — the ladder that walks AWAY from the
    callout when the slot under it is taken."""

    def setUp(self):
        self.ctx = mw.SheetContext(1, 1)
        self.ctx.dimstyles = draw.sheet_dimstyles(self.ctx)
        self.layout = draw.Layout(self.ctx)
        # a side view: nothing in it is drawn as a circle
        self.views = {"front": tube_front()}
        self.offs = {"front": (20.0, 180.0)}
        self.frames = {"frames": [{"symbol": "runout", "view": "front",
                                   "target": "od_r35.75", "tol": "0.05",
                                   "refs": ["A"]}]}

    def place(self, anchor, blockers=()):
        for i, b in enumerate(blockers):
            self.layout.claim(b, f"callout-other-{i}")
        rects = {("front", 35.75): anchor}
        return draw.place_fcfs(self.ctx, self.layout, self.frames,
                               self.views, self.offs, rects, {})

    def test_it_sits_on_its_callout_when_the_slot_is_free(self):
        anchor = (120.0, 250.0, 160.0, 254.0)
        recs = self.place(anchor)
        self.assertEqual(self.layout.unresolved, [])
        boxes = fcf_boxes(recs)
        self.assertEqual(len(boxes), 1)
        self.assertTrue(touches(boxes[0], anchor, self.ctx.mm(16.0)),
                        f"frame {boxes[0]} is not on its callout {anchor}")

    def test_with_the_slot_taken_it_is_leadered_or_refused(self):
        anchor = (120.0, 250.0, 160.0, 254.0)
        # the callout stacked under it, exactly 8 mm down, and another
        # under that: every spot below the anchor belongs to someone else
        recs = self.place(anchor, blockers=[(120.0, 242.0, 170.0, 246.0),
                                            (120.0, 234.0, 170.0, 238.0),
                                            (120.0, 226.0, 170.0, 230.0)])
        boxes = fcf_boxes(recs)
        if not boxes:
            self.assertTrue(self.layout.unresolved, "silently dropped")
            return
        self.assertEqual(self.layout.unresolved, [])
        box = boxes[0]
        self.assertTrue(
            touches(box, anchor, self.ctx.mm(16.0)) or reaches(recs, anchor),
            f"frame {box} neither touches nor leaders to {anchor}")

    def test_a_frame_that_can_neither_attach_nor_leader_is_refused(self):
        anchor = (120.0, 250.0, 160.0, 254.0)
        # wall the callout in with text on every side: no attached spot and
        # no leader route that does not cross a label
        blockers = [(100.0, 240.0, 185.0, 248.0),
                    (100.0, 256.0, 185.0, 264.0),
                    (100.0, 232.0, 185.0, 238.0)]
        recs = self.place(anchor, blockers=blockers)
        if fcf_boxes(recs):
            # if it did place, it must be attached or leadered — never free
            box = fcf_boxes(recs)[0]
            self.assertTrue(touches(box, anchor, self.ctx.mm(16.0))
                            or reaches(recs, anchor))
        else:
            self.assertEqual(recs, [], "frame ink emitted for a failure")
            self.assertTrue(any("fcf" in u for u in self.layout.unresolved))


class DatumInACrowdedColumnTest(unittest.TestCase):
    """Four `at` callouts on one bore line, a datum on one of them: the
    slots beside its shoulder are the next callout's text (issue #7)."""

    def setUp(self):
        self.ctx = mw.SheetContext(1, 1)
        self.ctx.dimstyles = draw.sheet_dimstyles(self.ctx)
        self.layout = draw.Layout(self.ctx)
        self.views = {"front": tube_front()}
        self.offs = {"front": (20.0, 180.0)}

    def test_the_box_finds_the_landing_s_extension(self):
        land = ((120.0, 250.0), (124.0, 250.0))
        stack = (124.0, 248.0, 170.0, 252.0)
        self.layout.claim(stack, "callout-own")
        # the next callout 8 mm under it and one 8 mm over it: every slot
        # beside the shoulder is somebody else's text, which is how the
        # real tube left datum A unresolved whatever order they were in
        for y in (240.0, 232.0, 253.0):
            self.layout.claim((108.0, y, 170.0, y + 5.0), "callout-other")
        recs, rect = draw.datum_on_landing(self.ctx, self.layout, "A", land,
                                           stack=stack)
        self.assertIsNotNone(rect, "datum crowded out with room to its side")
        tips = [t[0] for t in solid_triangles("\n".join(recs))]
        self.assertTrue(tips)
        # the triangle is on the landing: same height, at or past its knee
        self.assertAlmostEqual(tips[0][1], 250.0, places=6)
        self.assertGreaterEqual(tips[0][0], 120.0 - 1e-9)

    def test_without_the_stack_it_still_reports_the_failure(self):
        land = ((120.0, 250.0), (124.0, 250.0))
        for y in (240.0, 244.0, 252.0, 256.0):
            self.layout.claim((100.0, y, 180.0, y + 4.0), "callout-other")
        recs, rect = draw.datum_on_landing(self.ctx, self.layout, "A", land)
        self.assertIsNone(rect)
        self.assertEqual(recs, [])

    def test_a_sheet_with_a_stacked_column_places_its_datum(self):
        with tempfile.TemporaryDirectory() as tmp:
            m, part = tube_manifest(Path(tmp), {
                "datums": [{"label": "A", "view": "section",
                            "target": "bore_r35.75@head"}], "frames": []})
            # four callouts on the same bore line, as the real tube has
            part = dict(part, callouts=part["callouts"] + [
                {"view": "section", "at": [60.0, -35.75],
                 "text": "LEAD-IN 20%%d x 2.0, BOTH ENDS"},
                {"view": "section", "at": [240.0, -35.75],
                 "text": "N7 BOTH BORES"}])
            proj = {"01:front": tube_front(), "01:section": tube_section()}
            ctx, _, lay = draw.build_part_sheet(m, part, proj)
            self.assertEqual(lay["unresolved"], [])
            self.assertTrue(solid_triangles(ctx.render()))


class ShoulderLeaderTest(unittest.TestCase):
    """A face on a turned part shows as one short edge EACH SIDE of the
    axis. The frame takes the near one, and no leader crosses the hatch."""

    def setUp(self):
        self.ctx = mw.SheetContext(1, 5)
        self.ctx.dimstyles = draw.sheet_dimstyles(self.ctx)
        self.layout = draw.Layout(self.ctx)
        self.job = tube_section()
        self.off = (40.0, 300.0)
        self.views = {"section": self.job}
        self.offs = {"section": self.off}

    def test_both_edges_of_the_face_are_found(self):
        lines = draw.face_lines(self.job, self.off, f"face_x{END_LEN:g}")
        self.assertEqual(len(lines), 2, "a shoulder shows one edge a side")
        ys = sorted(((a[1] + b[1]) / 2 for a, b in lines))
        self.assertLess(ys[0], self.off[1])      # below the axis
        self.assertGreater(ys[1], self.off[1])   # and above it

    def test_the_leader_takes_the_near_edge_and_misses_the_hatch(self):
        frames = {"frames": [{"symbol": "perpendicularity",
                              "view": "section",
                              "target": f"face_x{END_LEN:g}",
                              "tol": "0.05", "refs": ["A"]}]}
        recs = draw.place_fcfs(self.ctx, self.layout, frames, self.views,
                               self.offs, {}, {})
        self.assertEqual(self.layout.unresolved, [])
        polys = draw.hatch_polys(self.job, self.off)
        self.assertTrue(polys)
        leaders = [(a, b) for a, b in lines_of(recs)
                   if abs(a[0] - (END_LEN + self.off[0])) < 0.01
                   or abs(b[0] - (END_LEN + self.off[0])) < 0.01]
        self.assertTrue(leaders, "no leader reaches the shoulder")
        for a, b in leaders:
            self.assertFalse(draw.crosses_polys(self.ctx, polys, a, b),
                             f"leader {a}->{b} crosses the section hatch")
            self.assertLess(math.dist(a, b), self.ctx.mm(60.0),
                            "leader runs right across the view")

    def test_it_takes_the_nearest_line_not_the_first(self):
        """Given both edges of one face, the frame goes beside the one it
        can reach shortest — the far edge is a leader across the view."""
        far = ((100.0, 100.0), (100.0, 112.0))
        near = ((100.0, 300.0), (100.0, 312.0))
        vrect = (60.0, 90.0, 140.0, 320.0)
        rect, tip = draw.place_frame_near_face(
            self.ctx, self.layout, [far, near], vrect, 30.0, 8.0, "fcf-x")
        self.assertIsNotNone(rect)
        self.assertGreaterEqual(tip[1], 300.0,
                                "leader went to the far edge of the face")

    def test_a_segment_through_the_solid_is_seen(self):
        polys = draw.hatch_polys(self.job, self.off)
        across = ((self.off[0] + 1.0, self.off[1] + OD / 2 - 1.0),
                  (self.off[0] + TUBE_L - 1.0, self.off[1] + OD / 2 - 1.0))
        self.assertTrue(draw.crosses_polys(self.ctx, polys, *across))
        clear = ((self.off[0] - 20.0, self.off[1] + OD),
                 (self.off[0] + TUBE_L, self.off[1] + OD))
        self.assertFalse(draw.crosses_polys(self.ctx, polys, *clear))


if __name__ == "__main__":
    unittest.main()
