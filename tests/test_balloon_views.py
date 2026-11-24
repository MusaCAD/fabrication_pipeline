# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Pranay Kiran
"""A balloon leader must not cross another view (issue #20).

Run with:  python3 -m unittest discover -s tests -v

The real failure: an assembly about 800 mm long at 1:5 on A3 landscape,
front view with section A-A below it, 26 part numbers in two columns. A
split fan starts at the top of the drawing area and runs down past the
section, so the leaders from the balloons at the bottom of the column
reached their parts in the FRONT view straight across the section's
outline and hatch — pointing at a part through a view that says nothing
about it. `_uncross` kept the leaders off each other; nothing kept them
off another view.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "tools"))

import common  # noqa: E402
import draw  # noqa: E402
import layout_check as lc  # noqa: E402
import musa_writer as mw  # noqa: E402

P = mw.P
A = mw.P_ANNO


def seg(a, b):
    return {"t": "seg", "a": list(a), "b": list(b)}


def box_prims(x0, y0, x1, y1):
    pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    return [seg(a, b) for a, b in zip(pts, pts[1:] + pts[:1])]


def instance(name, x0, y0, x1, y1):
    edges = box_prims(x0, y0, x1, y1)
    return {"name": name, "label": name, "edges": edges,
            "bbox": [x0, y0, x1, y1],
            "center": [(x0 + x1) / 2, (y0 + y1) / 2]}


def view(instances, bounds):
    vis = [pr for i in instances for pr in i["edges"]]
    return {"visible": vis, "hidden": [], "instances": instances,
            "bounds": bounds, "hatch_groups": []}


class CrossesOtherViewTest(unittest.TestCase):
    """The question a knee has to answer before it is taken."""

    def setUp(self):
        self.front = view([instance("A", 0, 100, 80, 140)],
                          [[0, 100], [80, 140]])
        self.section = view([instance("B", 0, 0, 80, 40)], [[0, 0], [80, 40]])
        self.inks = draw.view_ink([("front", self.front, (0.0, 0.0)),
                                   ("section", self.section, (0.0, 0.0))])

    def test_a_leader_through_the_section_is_named(self):
        # from a part in the front view down to a knee below the section
        self.assertEqual(
            draw.crosses_other_view((40.0, 120.0), (100.0, -40.0),
                                    self.inks, self.front), "section")

    def test_a_leader_that_stays_clear_is_not(self):
        self.assertIsNone(
            draw.crosses_other_view((40.0, 120.0), (200.0, 130.0),
                                    self.inks, self.front))

    def test_a_leader_may_cross_its_OWN_view(self):
        # it has to: the arrow lands on a part inside the view's outline
        self.assertIsNone(
            draw.crosses_other_view((40.0, 120.0), (200.0, 120.0),
                                    self.inks, self.front))

    def test_the_rule_cuts_both_ways(self):
        # a balloon on the section, reaching up past the front view
        self.assertEqual(
            draw.crosses_other_view((40.0, 20.0), (120.0, 400.0),
                                    self.inks, self.section), "front")


class FanSkipsTheBlockedKneeTest(unittest.TestCase):
    """The fan steps past a knee whose leader would cross another view,
    and says which view when none is left."""

    def setUp(self):
        self.ctx = mw.SheetContext(1, 1)
        self.ctx.dimstyles = draw.sheet_dimstyles(self.ctx)
        self.layout = draw.Layout(self.ctx)
        area = self.ctx.draw_area
        self.y0 = (area[1] + area[3]) / 2.0
        self.x0 = area[0] + self.ctx.mm(20.0)
        self.front = view([instance("A", self.x0, self.y0 + 40,
                                    self.x0 + 60, self.y0 + 70)],
                          [[self.x0, self.y0 + 40],
                           [self.x0 + 60, self.y0 + 70]])
        self.section = view([instance("B", self.x0, self.y0 - 40,
                                      self.x0 + 200, self.y0 - 10)],
                            [[self.x0, self.y0 - 40],
                             [self.x0 + 200, self.y0 - 10]])
        self.inks = draw.view_ink([("front", self.front, (0.0, 0.0)),
                                   ("section", self.section, (0.0, 0.0))])
        self.kx = self.x0 + 120

    def fan(self, start_y, inks):
        blocked = {}
        recs, _next = draw.balloons_fan(
            self.ctx, self.layout, None, None,
            [(self.front, (0.0, 0.0), 0, "1")], start_y=start_y,
            knee_x=self.kx, dense=True, inks=inks, in_the_way=blocked)
        return recs, blocked

    def leader_knee(self, recs):
        for r in recs:
            if r.startswith("LEADER "):
                t = r.split()
                return (float(t[3]), float(t[4]))
        return None

    def test_without_the_rule_the_knee_below_the_section_is_taken(self):
        recs, blocked = self.fan(self.y0 - 60, ())
        self.assertEqual(blocked, {})
        self.assertLess(self.leader_knee(recs)[1], self.y0 - 40)

    def test_with_it_that_knee_is_refused(self):
        # knees are handed out DOWN the column, so below the section
        # there is nothing left to step to: the balloon says so, and
        # draws nothing rather than drawing a leader over the hatch
        recs, blocked = self.fan(self.y0 - 60, self.inks)
        self.assertEqual(blocked, {"1": "section"})
        self.assertTrue(any("section view" in u
                            for u in self.layout.unresolved))
        self.assertEqual([r for r in recs if r.startswith("LEADER ")], [])

    def test_a_knee_beside_its_own_view_is_taken_as_before(self):
        recs, blocked = self.fan(self.y0 + 80, self.inks)
        self.assertEqual(blocked, {})
        for r in recs:
            if r.startswith("LEADER "):
                t = [float(v) for v in r.split()[1:5]]
                self.assertIsNone(draw.crosses_other_view(
                    (t[0], t[1]), (t[2], t[3]), self.inks, self.front))


class WholeSheetTest(unittest.TestCase):
    """The real shape of it: a long assembly, a section below the front
    view and more part numbers than one column holds, so the fan runs
    down the sheet past the section."""

    N = 24
    LEN = 800.0

    def jobs(self):
        """Both views show every part: a row of blocks along a bar."""
        step = self.LEN / self.N
        front, section = [], []
        for i in range(self.N):
            x = i * step
            front.append(instance(f"P{i:02d}", x + 1, 0.0, x + step - 1,
                                  30.0))
            section.append(instance(f"P{i:02d}", x + 1, 0.0, x + step - 1,
                                    24.0))
        return (view(front, [[0.0, 0.0], [self.LEN, 30.0]]),
                view(section, [[0.0, 0.0], [self.LEN, 24.0]]))

    def manifest(self, tmp: Path, **asm_kw):
        front, section = self.jobs()
        parts, instances, usage = [], [], {}
        for i in range(self.N):
            f = str(tmp / f"p{i:02d}.FCStd")
            parts.append({"part_id": f"{i + 1:02d}", "file": f,
                          "description": f"PART {i:02d}",
                          "drg_no": f"FX-{i:02d}", "material": "AL 6061",
                          "qty": 1, "front_view": "+Y", "scale": "1:5",
                          "section": {"enabled": False}})
            instances.append({"name": f"P{i:02d}", "label": f"P{i:02d}",
                              "path": f"P{i:02d}", "kind": "part",
                              "target_file": f, "broken": False,
                              "children": []})
            usage[f] = {"qty": 1, "used_in": [f"P{i:02d}"]}
        analysis = {
            "model_dir": str(tmp), "freecad_version": "1.1.1",
            "assembly": {"file": str(tmp / "asm.FCStd"), "document": "asm",
                         "workbench": "builtin-assembly",
                         "instances": instances, "joints": [], "issues": [],
                         "part_usage": usage, "sub_assembly_files": [],
                         "bbox_min": [0, 0, 0],
                         "bbox_max": [self.LEN, 60, 30]},
            "parts": [{"file": p["file"], "document": "p",
                       "sketches": [], "issues": [],
                       "bodies": [{"label": "Body", "object": "Body",
                                   "type": "PartDesign::Body", "solids": 1,
                                   "faces": 6, "volume_mm3": 1000.0,
                                   "area_mm2": 0.0,
                                   "bbox_min": [0, 0, 0],
                                   "bbox_max": [30, 30, 10],
                                   "bbox_size": [30.0, 30.0, 10.0],
                                   "material": "AL 6061"}]}
                      for p in parts]}
        apath = tmp / "analysis.json"
        common.write_json(apath, analysis)
        asm = {"enabled": True, "description": "FIXTURE", "drg_no": "FX-00",
               "front_view": "+Y", "scale": "1:5", "paper": "A3L",
               "balloons": True,
               "section": {"enabled": True, "projection": "below",
                           "offset_frac": 0.5, "label": "A"},
               "parts_table": {"group_by": "none"}}
        asm.update(asm_kw)
        m = {"locked": True, "project": "FIXTURE", "drg_no_prefix": "FX",
             "rev": "00", "model_dir": str(tmp),
             "assembly_file": str(tmp / "asm.FCStd"),
             "_analysis_path": str(apath), "parts": parts,
             "templates": {"A3L": str(self.a3)}, "assembly_sheet": asm}
        proj = {"assembly:front": front, "assembly:section": section}
        return m, proj

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.TemporaryDirectory(prefix="fabpipe-bviews-")
        cls.a3 = Path(cls.tmpdir.name) / "a3l.musa"
        import widen_template
        widen_template.widen(common.TEMPLATE, cls.a3, "A3", "landscape")

    @classmethod
    def tearDownClass(cls):
        cls.tmpdir.cleanup()

    def build(self, **asm_kw):
        with tempfile.TemporaryDirectory() as tmp:
            m, proj = self.manifest(Path(tmp), **asm_kw)
            built = draw.build_assembly_sheet(m, proj)
            return built

    def test_no_balloon_leader_crosses_the_other_view(self):
        built = self.build()
        ctx, _r, lay, meta = built[0]
        self.assertEqual([u for u in lay["unresolved"]
                          if u.startswith("balloon")], [])
        box = getattr(ctx, "_pictorial_box", None)
        found = lc.check_sheet(ctx.render(), ctx.draw_area,
                               ctx.num / ctx.den,
                               table_band=getattr(ctx, "_table_band", None),
                               boxes=[box] if box else ())
        self.assertEqual([f for f in found
                          if f["kind"] == "leader-across-view"], [])

    def test_the_ones_the_section_was_in_the_way_of_moved_into_it(self):
        built = self.build()
        plan = built[0][3]["balloons"]["ballooned"]
        self.assertEqual(len(plan), self.N)
        self.assertTrue(any(v == "section" for v in plan.values()),
                        "nothing moved: the fixture no longer reproduces it")
        self.assertTrue(any(v == "front" for v in plan.values()))
        self.assertEqual(built[0][3]["balloons"]["not_shown"], [])

    def test_the_manifest_s_own_choice_is_kept(self):
        # whichever part the engine moved, `balloon_in` pins it back —
        # and the sheet then says it could not place that balloon
        plan = self.build()[0][3]["balloons"]["ballooned"]
        moved = next(p for p, v in plan.items() if v == "section")
        built = self.build(balloon_in={moved: "front"})
        self.assertEqual(built[0][3]["balloons"]["ballooned"][moved],
                         "front")
        self.assertTrue([u for u in built[0][2]["unresolved"]
                         if u.startswith(f"balloon-{draw.disp_no(moved)}")])


class LayoutCheckAcrossViewTest(unittest.TestCase):
    """And the check reads it back off the finished sheet."""

    AREA = (0.0, 0.0, 400.0, 400.0)

    def sheet(self, *records):
        return "MUSACAD 14\n" + "\n".join(records) + "\nEND\n"

    def views(self):
        out = []
        for y0 in (40.0, 240.0):
            pts = [(40.0, y0), (340.0, y0), (340.0, y0 + 80),
                   (40.0, y0 + 80)]
            out += [mw.line(a, b) for a, b in zip(pts, pts[1:] + pts[:1])]
            out.append(mw.line((60.0, y0 + 10), (320.0, y0 + 10)))
            out.append(mw.line((60.0, y0 + 20), (320.0, y0 + 20)))
        return out

    def test_two_views_are_two_clusters(self):
        recs = lc.parse(self.sheet(*self.views()))
        geom = [s for r in recs for s in lc.segments(r)]
        views = lc.view_clusters(geom, lc.VIEW_GAP, lc.VIEW_MIN)
        self.assertEqual(len(views), 2)

    def test_a_single_frame_line_is_not_a_view(self):
        recs = lc.parse(self.sheet(mw.line((0.0, 0.0), (0.0, 400.0))))
        geom = [s for r in recs for s in lc.segments(r)]
        self.assertEqual(lc.view_clusters(geom, lc.VIEW_GAP, lc.VIEW_MIN),
                         [])

    def test_a_leader_through_the_other_view_is_reported(self):
        text = self.sheet(*self.views(),
                          mw.leader((120.0, 280.0), (380.0, 60.0), "",
                                    3.0, props=A))
        found = lc.check_sheet(text, self.AREA)
        self.assertIn("leader-across-view", [f["kind"] for f in found])

    def test_a_leader_that_keeps_to_its_own_view_is_not(self):
        text = self.sheet(*self.views(),
                          mw.leader((120.0, 280.0), (380.0, 300.0), "",
                                    3.0, props=A))
        self.assertEqual([f for f in lc.check_sheet(text, self.AREA)
                          if f["kind"] == "leader-across-view"], [])

    def test_it_fails_the_export(self):
        text = self.sheet(*self.views(),
                          mw.leader((120.0, 280.0), (380.0, 60.0), "",
                                    3.0, props=A))
        found = lc.check_sheet(text, self.AREA)
        self.assertTrue([f for f in lc.faults(found)
                         if f["kind"] == "leader-across-view"])


if __name__ == "__main__":
    unittest.main()
