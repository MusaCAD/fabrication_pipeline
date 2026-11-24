# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Pranay Kiran
"""Where a leader may go (issue #18).

Run with:  python3 -m unittest discover -s tests -v

The fixture is the real failure: a turned part — ⌀50.8 body, ⌀46.7 x 10
spigot, hemispherical tip — at 1:1 on a roomy sheet, with its size
callouts anchored on the silhouette. The level knee heads the ladder and
on a roomy sheet it is almost always free, so the leader came out ON the
outline it points at, where the arrow cannot be told from the part; and
in other orders one leader crossed another, so each appeared to point at
the other's feature.
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
import layout_check as lc  # noqa: E402
import musa_writer as mw  # noqa: E402

BODY_R, SPIG_R, TIP_R = 25.4, 23.35, 25.4
X0, X1, X2 = 848.6, 881.0, 906.5          # spigot, body, tip


def seg(a, b):
    return {"t": "seg", "a": list(a), "b": list(b)}


def nose_front():
    """The side view of a turned part: two long horizontal silhouettes,
    which is what a level leader lands on."""
    outline = [(X0, SPIG_R), (X1, SPIG_R), (X1, BODY_R), (X2, BODY_R),
               (X2, -BODY_R), (X1, -BODY_R), (X1, -SPIG_R), (X0, -SPIG_R),
               (X0, SPIG_R)]
    return {"visible": [seg(a, b) for a, b in zip(outline, outline[1:])],
            "hidden": [], "bounds": [[X0, -BODY_R], [X2, BODY_R]]}


def nose_section():
    r = BODY_R
    loop = [(X0, SPIG_R), (X2, r), (X2, -r), (X0, -SPIG_R)]
    return {"visible": [seg(a, b) for a, b in zip(loop, loop[1:])],
            "hidden": [], "hatch_groups": [{"outer": [list(p) for p in loop],
                                            "holes": []}],
            "bounds": [[X0, -r], [X2, r]]}


def nose_manifest(tmp: Path, callouts, **kw):
    analysis = {
        "model_dir": str(tmp), "freecad_version": "1.1.1",
        "assembly": {"file": "", "document": "", "workbench": "none",
                     "instances": [], "joints": [], "issues": [],
                     "part_usage": {}, "bbox_min": None, "bbox_max": None},
        "parts": [{"file": str(tmp / "nose.FCStd"), "document": "nose",
                   "sketches": [], "issues": [],
                   "bodies": [{"label": "Body", "object": "Body",
                               "type": "PartDesign::Body", "solids": 1,
                               "faces": 8, "volume_mm3": 90000.0,
                               "area_mm2": 0.0,
                               "bbox_min": [X0, -BODY_R, -BODY_R],
                               "bbox_max": [X2, BODY_R, BODY_R],
                               "bbox_size": [X2 - X0, 2 * BODY_R,
                                             2 * BODY_R],
                               "material": "AL 6061"}]}]}
    apath = tmp / "analysis.json"
    common.write_json(apath, analysis)
    part = {"part_id": "05", "file": str(tmp / "nose.FCStd"),
            "description": "PROBE NOSE", "drg_no": "FX-05",
            "material": "AL 6061", "qty": 1, "front_view": "+Y",
            "scale": "1:1",
            "section": {"enabled": True, "projection": "below",
                        "label": "A", "offset_frac": 0.5},
            "callouts": callouts}
    part.update(kw)
    m = {"locked": True, "project": "FIXTURE", "drg_no_prefix": "FX",
         "drawn_by": "Er. P. PRANAY", "rev": "00", "model_dir": str(tmp),
         "assembly_file": "", "parts": [part],
         "assembly_sheet": {"enabled": False}, "_analysis_path": str(apath)}
    return m, part


def build(callouts, **kw):
    with tempfile.TemporaryDirectory() as tmp:
        m, part = nose_manifest(Path(tmp), callouts, **kw)
        proj = {"05:front": nose_front(), "05:section": nose_section()}
        ctx, _replaced, lay = draw.build_part_sheet(m, part, proj)
        return ctx, lay


def check(ctx):
    return lc.check_sheet(ctx.render(), ctx.draw_area, ctx.num / ctx.den,
                          table_band=getattr(ctx, "_table_band", None))


class SegmentMathTest(unittest.TestCase):
    def test_a_crossing_away_from_both_ends_counts(self):
        self.assertTrue(draw._segs_cross((0, 0), (10, 10), (0, 10),
                                         (10, 0), 0.1))

    def test_a_touch_at_an_end_does_not(self):
        self.assertFalse(draw._segs_cross((0, 0), (10, 0), (0, 0),
                                          (0, 10), 0.1))

    def test_parallel_leaders_never_cross(self):
        self.assertFalse(draw._segs_cross((0, 0), (10, 0), (0, 1),
                                          (10, 1), 0.1))


class RunsAlongTest(unittest.TestCase):
    def setUp(self):
        self.ctx = mw.SheetContext(1, 1)

    def test_a_level_leader_off_a_silhouette_runs_along_it(self):
        geom = [((X0, BODY_R), (X2, BODY_R))]
        run = draw.leader_runs_along(self.ctx, geom, (X0 + 5, BODY_R),
                                     (X2 + 40, BODY_R))
        self.assertGreater(run, 20.0)

    def test_a_leader_that_rises_does_not(self):
        geom = [((X0, BODY_R), (X2, BODY_R))]
        run = draw.leader_runs_along(self.ctx, geom, (X0 + 5, BODY_R),
                                     (X2 + 40, BODY_R + 8))
        self.assertEqual(run, 0.0)


class FanOrderTest(unittest.TestCase):
    """Callouts are placed in the order their arrows are SEEN from the
    column they share — the order a fan comes out in."""

    def test_it_orders_by_the_angle_at_the_column(self):
        ctx = mw.SheetContext(1, 1)
        views = {"front": nose_front()}
        offs = {"front": (0.0, 0.0)}
        # seen from the column, the high one is at the greater angle and
        # takes the top knee, whatever order the manifest wrote them in
        high = {"view": "front", "at": [X0 + 1, BODY_R], "text": "HIGH"}
        low = {"view": "front", "at": [X2 - 1, -BODY_R], "text": "LOW"}
        self.assertEqual(
            [c["text"] for c in draw.fan_order(ctx, [low, high], views,
                                               offs)], ["HIGH", "LOW"])
        self.assertEqual(
            [c["text"] for c in draw.fan_order(ctx, [high, low], views,
                                               offs)], ["HIGH", "LOW"])

    def test_a_callout_no_view_places_keeps_its_place(self):
        ctx = mw.SheetContext(1, 1)
        views = {"front": nose_front()}
        offs = {"front": (0.0, 0.0)}
        orphan = {"view": "aux", "at": [0, 0], "text": "ORPHAN"}
        here = {"view": "front", "at": [X2 - 1, BODY_R], "text": "HERE"}
        got = draw.fan_order(ctx, [orphan, here], views, offs)
        self.assertEqual([c["text"] for c in got], ["HERE", "ORPHAN"])


class OneBoreLineTest(unittest.TestCase):
    """The other real failure: five notes anchored on the SAME bore line
    of a turned part, where height says nothing about which arrow should
    be served first. In the manifest's own order three pairs of leaders
    crossed; from the column they share, the arrows are at different
    angles, and taken in THAT order a fan cannot cross itself."""

    def build(self):
        from test_gdt_placement import (tube_front, tube_manifest,
                                        tube_section)
        with tempfile.TemporaryDirectory() as tmp:
            m, part = tube_manifest(Path(tmp), {"datums": [], "frames": []})
            part = dict(part, callouts=part["callouts"] + [
                {"view": "section", "at": [60.0, -35.75],
                 "text": "LEAD-IN 20%%d x 2.0, BOTH ENDS"},
                {"view": "section", "at": [240.0, -35.75],
                 "text": "N7 BOTH BORES"}])
            proj = {"01:front": tube_front(), "01:section": tube_section()}
            return draw.build_part_sheet(m, part, proj)

    def test_every_note_is_drawn_and_none_of_them_cross(self):
        ctx, _r, lay = self.build()
        text = ctx.render()
        self.assertEqual(lay["unresolved"], [])
        self.assertEqual(
            sum(1 for l in text.splitlines() if l.startswith("LEADER ")), 5)
        crossings = [f for f in check(ctx) if f["kind"] == "leader-crossing"]
        self.assertEqual(crossings, [], "two leaders still cross")


class SheetLeadersTest(unittest.TestCase):
    """The whole sheet: the issue's own repro."""

    def test_no_leader_runs_along_the_outline_it_points_at(self):
        ctx, lay = build([
            {"view": "front", "at": [881.0, BODY_R], "text": "%%c50.8"},
            {"view": "front", "at": [860.0, SPIG_R],
             "text": "%%c46.7 -0.1/0 (SPIGOT)"}])
        self.assertEqual(lay["unresolved"], [])
        self.assertEqual([f for f in check(ctx)
                          if f["kind"] == "leader-on-outline"], [])

    def test_no_two_leaders_cross(self):
        # the order that crossed: the near feature first, the far one
        # after it, so the second has to reach past the first
        ctx, lay = build([
            {"view": "front", "at": [900.0, -BODY_R], "text": "%%c50.8"},
            {"view": "front", "at": [855.0, -SPIG_R],
             "text": "%%c46.7 -0.1/0 (SPIGOT)"},
            {"view": "front", "at": [881.0, BODY_R],
             "text": "SR25.4 (TIP)"}])
        self.assertEqual(lay["unresolved"], [])
        self.assertEqual([f for f in check(ctx)
                          if f["kind"] == "leader-crossing"], [])

    def test_a_leader_with_nowhere_clean_is_drawn_and_reported(self):
        # eight notes on one bore line: the band cannot hold them all
        # cleanly, and a callout the vendor never sees is worse than one
        # whose leader crosses another
        cos = [{"view": "section", "at": [X0 + 2 + 4 * k, -SPIG_R],
                "text": f"NOTE {k} ON THE BORE LINE, 20 CHARACTERS OR SO"}
               for k in range(8)]
        ctx, lay = build(cos)
        text = ctx.render()
        for k in range(8):
            self.assertIn(f"NOTE {k} ON THE BORE", text)
        self.assertTrue(lay["faults"], "a compromised leader went unsaid")
        self.assertTrue(any("its leader" in f for f in lay["faults"]))

    def test_the_sheet_report_carries_the_faults(self):
        ctx, lay = build([
            {"view": "front", "at": [881.0, BODY_R], "text": "%%c50.8"}])
        self.assertEqual(lay["unresolved"], [])
        self.assertEqual(lay["faults"], [])


if __name__ == "__main__":
    unittest.main()
