# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Pranay Kiran
"""GD&T on features a view shows EDGE-ON (issue #6).

Run with:  python3 -m unittest discover -s tests -v

A turned part drawn as a side view and a section never shows its spigot or
its bore as a circle — they are pairs of lines — and its shoulder face is a
single line. Both still have to carry GD&T: the datum goes on the feature's
SIZE dimension (ISO 1101 / ISO 5459) and a frame on a plane face goes on
that face's edge-on line with a leader. Where neither is on the sheet, the
sheet fails with a message naming the view and what to add — it never
quietly drops to a note.
"""

import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import common  # noqa: E402
import draw  # noqa: E402
import manifest as mf  # noqa: E402
import musa_writer as mw  # noqa: E402

# A plug: a 60 dia body 20 long, a 47 dia spigot 15 long on the end, bored
# 20 through. Drawn from the side, so every diameter is a pair of lines and
# the shoulder between body and spigot is the line at x = 20.
BODY_D, SPIGOT_D, BORE_D = 60.0, 47.0, 20.0
BODY_L, SPIGOT_L = 20.0, 15.0
SHOULDER_X = BODY_L


def seg(a, b):
    return {"t": "seg", "a": list(a), "b": list(b)}


def plug_front():
    """The side view: outline, shoulder faces, bore as hidden lines."""
    br, sr, bor = BODY_D / 2, SPIGOT_D / 2, BORE_D / 2
    outline = [(0.0, -br), (0.0, br), (BODY_L, br), (BODY_L, sr),
               (BODY_L + SPIGOT_L, sr), (BODY_L + SPIGOT_L, -sr),
               (BODY_L, -sr), (BODY_L, -br), (0.0, -br)]
    vis = [seg(a, b) for a, b in zip(outline, outline[1:])]
    hid = [seg((0.0, y), (BODY_L + SPIGOT_L, y)) for y in (-bor, bor)]
    return {"visible": vis, "hidden": hid,
            "bounds": [[0.0, -br], [BODY_L + SPIGOT_L, br]]}


def plug_section():
    """SECTION B-B: the cut, hatched either side of the bore."""
    br, sr, bor = BODY_D / 2, SPIGOT_D / 2, BORE_D / 2
    groups, vis = [], []
    for sign in (1, -1):
        loop = [[0.0, sign * bor], [BODY_L, sign * bor],
                [BODY_L, sign * sr], [BODY_L + SPIGOT_L, sign * sr],
                [BODY_L + SPIGOT_L, sign * bor], [0.0, sign * bor]]
        loop = [[0.0, sign * bor], [BODY_L + SPIGOT_L, sign * bor],
                [BODY_L + SPIGOT_L, sign * sr], [BODY_L, sign * sr],
                [BODY_L, sign * br], [0.0, sign * br]]
        groups.append({"outer": loop, "holes": []})
        vis += [seg(a, b) for a, b in zip(loop, loop[1:] + loop[:1])]
    return {"visible": vis, "hidden": [], "hatch_groups": groups,
            "bounds": [[0.0, -br], [BODY_L + SPIGOT_L, br]]}


def plug_manifest(tmp: Path):
    """The manifest the issue describes: datum A on the spigot axis, runout
    to A on the body diameter, perpendicularity to A on the shoulder."""
    analysis = {
        "model_dir": str(tmp), "freecad_version": "1.1.1",
        "assembly": {"file": "", "document": "", "workbench": "none",
                     "instances": [], "joints": [], "issues": [],
                     "part_usage": {}, "bbox_min": None, "bbox_max": None},
        "parts": [{"file": str(tmp / "plug.FCStd"), "document": "plug",
                   "sketches": [], "issues": [],
                   "bodies": [{"label": "Body", "object": "Body",
                               "type": "PartDesign::Body", "solids": 1,
                               "faces": 12, "volume_mm3": 40000.0,
                               "area_mm2": 0.0,
                               "bbox_min": [0, -30, -30],
                               "bbox_max": [35, 30, 30],
                               "bbox_size": [35.0, 60.0, 60.0],
                               "material": "EN8"}]}]}
    apath = tmp / "analysis.json"
    common.write_json(apath, analysis)
    part = {
        "part_id": "01", "file": str(tmp / "plug.FCStd"),
        "description": "PLUG - GD&T FIXTURE", "drg_no": "FX-01",
        "material": "EN8", "qty": 1, "front_view": "+X", "scale": "1:1",
        # the section's letter is NOT A: the datum is A, and one sheet
        # cannot call two different things by the same letter
        "section": {"enabled": True, "projection": "below", "label": "B",
                    "offset_frac": 0.5},
        "front_extent_dims": True,
        "callouts": [
            # `at`-anchored, as a side view must be — but still carrying
            # circle_r, which is what the datum and the frames key on
            {"view": "front", "at": [BODY_L + SPIGOT_L / 2, SPIGOT_D / 2],
             "circle_r": SPIGOT_D / 2, "text": "%%c47 f7",
             "suppress_dia_dim": True},
            {"view": "front", "at": [BODY_L / 2, BODY_D / 2],
             "circle_r": BODY_D / 2, "text": "%%c60",
             "suppress_dia_dim": True},
        ],
        "gdt": {
            "datums": [{"label": "A", "view": "front",
                        "target": f"spigot_r{SPIGOT_D / 2:g}"}],
            "frames": [
                {"symbol": "runout", "view": "front",
                 "target": f"od_r{BODY_D / 2:g}", "tol": "0.05",
                 "refs": ["A"]},
                {"symbol": "perpendicularity", "view": "front",
                 "target": f"face_x{SHOULDER_X:g}", "tol": "0.05",
                 "refs": ["A"]},
            ]},
    }
    m = {"locked": True, "project": "FIXTURE", "drg_no_prefix": "FX",
         "drawn_by": "Er. P. PRANAY", "rev": "00", "model_dir": str(tmp),
         "assembly_file": "", "parts": [part],
         "assembly_sheet": {"enabled": False},
         "_analysis_path": str(apath)}
    return m, part


def solid_triangles(text):
    out, lines = [], text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("HATCH ") and i + 1 < len(lines) \
                and lines[i + 1].strip() == "SOLID":
            t = line.split()
            if t[1] == "1" and t[2] == "3":
                v = [float(x) for x in t[3:9]]
                out.append([(v[0], v[1]), (v[2], v[3]), (v[4], v[5])])
    return out


class FaceTargetTest(unittest.TestCase):
    """Naming a face that the view draws as a line."""

    def setUp(self):
        self.job = plug_front()

    def test_parsing(self):
        self.assertEqual(draw.face_target("face_x20"), ("x", 20.0))
        self.assertEqual(draw.face_target("face_y-12.5"), ("y", -12.5))
        self.assertEqual(draw.face_target("bottom_face"), ("y", "min"))
        self.assertEqual(draw.face_target("right_edge"), ("x", "max"))
        self.assertIsNone(draw.face_target("od_r30"))
        self.assertIsNone(draw.face_target("face_zzz"))
        self.assertIsNone(draw.face_target(None))

    def test_a_coordinate_face_finds_the_drawn_edge(self):
        line = draw.face_line(self.job, (100.0, 200.0), "face_x20")
        self.assertIsNotNone(line)
        (x0, y0), (x1, y1) = line
        self.assertAlmostEqual(x0, 120.0)        # 20 + the view offset
        self.assertAlmostEqual(x1, 120.0)
        self.assertGreater(abs(y1 - y0), 0.0)    # it is a line, not a point

    def test_the_longest_edge_at_that_coordinate_wins(self):
        job = {"visible": [seg((5.0, 0.0), (5.0, 2.0)),
                           seg((5.0, -10.0), (5.0, 10.0))],
               "bounds": [[0.0, -10.0], [10.0, 10.0]]}
        (_, y0), (_, y1) = draw.face_line(job, (0.0, 0.0), "face_x5")
        self.assertAlmostEqual(abs(y1 - y0), 20.0)

    def test_a_named_edge_comes_from_the_view_bounds(self):
        line = draw.face_line(self.job, (0.0, 0.0), "bottom_face")
        self.assertAlmostEqual(line[0][1], -BODY_D / 2)
        self.assertAlmostEqual(line[1][1], -BODY_D / 2)

    def test_a_face_the_view_does_not_draw(self):
        self.assertIsNone(draw.face_line(self.job, (0.0, 0.0), "face_x999"))

    def test_a_leader_point_is_chosen_on_the_line(self):
        ctx = mw.SheetContext(1, 1)
        layout = draw.Layout(ctx)
        line = ((100.0, 200.0), (100.0, 220.0))
        pt = draw.leader_point_on_line(ctx, layout, line, (160.0, 210.0))
        self.assertIsNotNone(pt)
        self.assertAlmostEqual(pt[0], 100.0)
        self.assertTrue(200.0 <= pt[1] <= 220.0)


class DatumEdgeOnTest(unittest.TestCase):
    """The datum of a feature of size needs its SIZE dimension, not a
    circle (issue #6)."""

    def setUp(self):
        self.ctx = mw.SheetContext(1, 1)
        self.ctx.dimstyles = draw.sheet_dimstyles(self.ctx)
        self.layout = draw.Layout(self.ctx)
        # a side view: the spigot is two lines, there is no circle at all
        self.views = {"front": plug_front()}
        self.offs = {"front": (60.0, 150.0)}
        self.gdt = {"datums": [{"label": "A", "view": "front",
                                "target": "spigot_r23.5"}], "frames": []}

    def test_it_attaches_to_the_callout_landing_with_no_circle(self):
        lands = {("front", 23.5): ((120.0, 170.0), (124.0, 170.0))}
        rects = {("front", 23.5): (124.0, 168.0, 160.0, 172.0)}
        recs, pos = draw.place_datums(self.ctx, self.layout, self.gdt,
                                      self.views, self.offs,
                                      callout_rects=rects,
                                      callout_lands=lands)
        self.assertEqual(self.layout.unresolved, [])
        self.assertIn("A", pos)
        tip = solid_triangles("\n".join(recs))[0][0]
        self.assertAlmostEqual(tip[0], 120.0)
        self.assertAlmostEqual(tip[1], 170.0)

    def test_without_any_size_dimension_it_says_what_to_add(self):
        recs, pos = draw.place_datums(self.ctx, self.layout, self.gdt,
                                      self.views, self.offs)
        self.assertEqual(recs, [])
        msg = " ".join(self.layout.unresolved)
        self.assertIn("datum-A", msg)
        self.assertIn("no size dimension for r23.5", msg)
        self.assertIn("view 'front'", msg)
        self.assertIn("add a %%c callout", msg)

    def test_a_view_edge_datum_is_untouched(self):
        gdt = {"datums": [{"label": "B", "view": "front",
                           "target": "bottom_face"}], "frames": []}
        recs, pos = draw.place_datums(self.ctx, self.layout, gdt,
                                      self.views, self.offs)
        self.assertEqual(self.layout.unresolved, [])
        self.assertIn("B", pos)


class FrameOnFaceTest(unittest.TestCase):
    def setUp(self):
        self.ctx = mw.SheetContext(1, 1)
        self.ctx.dimstyles = draw.sheet_dimstyles(self.ctx)
        self.layout = draw.Layout(self.ctx)
        self.views = {"front": plug_front()}
        self.offs = {"front": (60.0, 150.0)}

    def frames(self, target):
        return {"frames": [{"symbol": "perpendicularity", "view": "front",
                            "target": target, "tol": "0.05",
                            "refs": ["A"]}]}

    def test_a_frame_on_an_edge_on_face_is_leadered_to_it(self):
        recs = draw.place_fcfs(self.ctx, self.layout, self.frames("face_x20"),
                               self.views, self.offs, {}, {})
        self.assertEqual(self.layout.unresolved, [])
        text = "\n".join(recs)
        # a leader line reaching the face, ending in an arrowhead on it
        self.assertTrue(solid_triangles(text), "no arrowhead on the face")
        lines = [r for r in recs if r.startswith("LINE ")]
        self.assertTrue(lines)
        touching = [r for r in lines
                    if abs(float(r.split()[1]) - 80.0) < 0.01
                    or abs(float(r.split()[3]) - 80.0) < 0.01]
        self.assertTrue(touching, "no leader reaches the shoulder at x=20")

    def test_a_face_the_view_does_not_show_fails_with_the_reason(self):
        draw.place_fcfs(self.ctx, self.layout, self.frames("face_x999"),
                        self.views, self.offs, {}, {})
        msg = " ".join(self.layout.unresolved)
        self.assertIn("face_x999", msg)
        self.assertIn("draws no edge there", msg)
        self.assertIn("add a view", msg)

    def test_a_feature_of_size_with_nothing_to_point_at_fails(self):
        # no callout, no dim, no circle in this view: a frame placed in the
        # band would point at nothing at all
        draw.place_fcfs(self.ctx, self.layout,
                        {"frames": [{"symbol": "runout", "view": "front",
                                     "target": "od_r30", "tol": "0.05",
                                     "refs": ["A"]}]},
                        self.views, self.offs, {}, {})
        msg = " ".join(self.layout.unresolved)
        self.assertIn("nothing in view 'front' to leader it to", msg)

    def test_a_frame_under_its_callout_needs_no_leader(self):
        rects = {("front", 30.0): (120.0, 180.0, 160.0, 186.0)}
        recs = draw.place_fcfs(self.ctx, self.layout,
                               {"frames": [{"symbol": "runout",
                                            "view": "front",
                                            "target": "od_r30",
                                            "tol": "0.05", "refs": ["A"]}]},
                               self.views, self.offs, rects, {})
        self.assertEqual(self.layout.unresolved, [])
        self.assertFalse(solid_triangles("\n".join(recs)))


class GdtManifestTest(unittest.TestCase):
    """The lock checks the targets and the datums a frame refers to."""

    def part(self, frames, datums=(("A", "spigot_r23.5"),)):
        return {"file": "/m/plug.FCStd", "gdt": {
            "datums": [{"label": l, "view": "front", "target": t}
                       for l, t in datums],
            "frames": list(frames)}}

    def test_a_good_one_passes(self):
        p = self.part([{"symbol": "runout", "target": "od_r30",
                        "tol": "0.05", "refs": ["A"]},
                       {"symbol": "perpendicularity", "target": "face_x20",
                        "tol": "0.05", "refs": ["A"]}])
        self.assertEqual(mf.gdt_problems(p, "plug"), [])

    def test_a_frame_referring_to_a_datum_the_sheet_has_not_got(self):
        p = self.part([{"symbol": "position", "target": "od_r30",
                        "tol": "0.2", "refs": ["A", "C"]}])
        msg = " ".join(mf.gdt_problems(p, "plug"))
        self.assertIn("refers to datum C", msg)
        self.assertIn("does not carry", msg)

    def test_a_target_of_no_known_form(self):
        p = self.part([{"symbol": "flatness", "target": "somewhere",
                        "tol": "0.05"}])
        self.assertIn("bad frame target", " ".join(mf.gdt_problems(p, "plug")))

    def test_every_face_form_is_accepted(self):
        for t in ("face_x20", "face_y-12.5", "bottom_face", "top_face",
                  "left_edge", "right_edge", "datum_A_face", "bore_r17.5"):
            p = self.part([{"symbol": "flatness", "target": t, "tol": "0.05"}])
            self.assertEqual(mf.gdt_problems(p, "plug"), [], t)

    def test_a_bad_datum_target_is_still_caught(self):
        p = self.part([], datums=(("A", "nonsense"),))
        self.assertIn("bad datum target", " ".join(mf.gdt_problems(p, "plug")))


class PlugSheetTest(unittest.TestCase):
    """The whole sheet from the issue: all three drawn, nothing dropped."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="fabpipe-gdt-")
        tmp = Path(cls.tmp.name)
        cls.m, cls.part = plug_manifest(tmp)
        proj = {"01:front": plug_front(), "01:section": plug_section()}
        # the live Layout, so the test can see WHAT was placed and where
        cls.ctx, cls.replaced, cls.layout = draw._compose_part_sheet(
            cls.m, cls.part, proj)
        cls.report = cls.layout.report()
        cls.text = cls.ctx.render()

    def labels(self, prefix):
        return [c for c in self.layout.claims if c[4].startswith(prefix)]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_nothing_was_left_unplaced(self):
        self.assertEqual(self.report["unresolved"], [])

    def test_the_datum_sits_on_the_spigot_callout(self):
        # the datum box letter and its filled triangle are both drawn
        self.assertIn("\nA\n", self.text)
        self.assertTrue(solid_triangles(self.text))

    def test_both_frames_are_drawn(self):
        # the frames' tolerances appear as cell text
        self.assertGreaterEqual(self.text.count("0.05"), 2)
        self.assertTrue(self.labels("fcf-stack-od_r30"), "no runout frame")
        self.assertTrue(self.labels("fcf-stack-face_x20"),
                        "no perpendicularity frame")

    def test_the_perpendicularity_frame_is_leadered_to_the_shoulder(self):
        # the leader's ink corridor is claimed under its own name, and it
        # has to reach the shoulder: the view's x = 20, wherever the sheet
        # placed the view
        corridor = self.labels("leader-fcf-face_x20")
        self.assertTrue(corridor, "the frame has no leader at all")
        view = next(c for c in self.layout.claims if c[4] == "front-view")
        shoulder_x = view[0] + SHOULDER_X       # the view's own left edge
        self.assertTrue(any(c[0] - 1.0 <= shoulder_x <= c[2] + 1.0
                            for c in corridor),
                        f"no leader ink at the shoulder (x={shoulder_x:.1f})")

    def test_the_datum_and_the_frames_went_on_the_drawing(self):
        # nothing fell back to a note: no fab note mentions them
        notes = [c for c in self.layout.claims
                 if c[4].startswith("notes-block")]
        self.assertFalse(any("PERPENDICULAR" in n[4].upper() for n in notes))
        self.assertTrue(self.labels("datum-A"))

    def test_it_plots(self):
        if not common.MUSA_PLOT.exists():
            self.skipTest("musa_plot not built")
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "plug.musa"
            out.write_text(self.text)
            info = common.run_musa_plot(out, Path(d) / "plug.pdf",
                                        scale=(1, 1), window=self.ctx.window)
            self.assertAlmostEqual(float(info["mm_per_unit"]), 1.0, places=6)


if __name__ == "__main__":
    unittest.main()
