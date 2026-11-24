# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Pranay Kiran
"""Side-by-side sections: a callout column for each view (issue #13).

Run with:  python3 -m unittest discover -s tests -v

With `"projection": "right"` and leaders on BOTH views, the front view's
callouts opened the inter-view gap and the section's then had nowhere to
land — reported as `callout-<text>` and lost. When the gap was capped the
sheet died with "views exceed drawing area; pick a smaller scale", which
is the wrong fix: the views fit, the annotation columns do not.

The fixture is the issue's own repro: a thin disc, section to the right,
a leader on each view — the usual way to point at a thickness only the
section shows.
"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import common  # noqa: E402
import draw  # noqa: E402
import musa_writer as mw  # noqa: E402

OD, BORE, T = 50.0, 6.0, 1.0
WIDE = "THICKNESS T: SEE NOTE 1"


def seg(a, b):
    return {"t": "seg", "a": list(a), "b": list(b)}


def disc_front():
    return {"visible": [{"t": "circle", "c": [0.0, 0.0], "r": OD / 2},
                        {"t": "circle", "c": [0.0, 0.0], "r": BORE / 2}],
            "hidden": [], "bounds": [[-OD / 2, -OD / 2], [OD / 2, OD / 2]]}


def disc_section():
    groups, vis = [], []
    for sgn in (1, -1):
        loop = [[-T, sgn * BORE / 2], [0.0, sgn * BORE / 2],
                [0.0, sgn * OD / 2], [-T, sgn * OD / 2]]
        groups.append({"outer": loop, "holes": []})
        vis += [seg(a, b) for a, b in zip(loop, loop[1:] + loop[:1])]
    return {"visible": vis, "hidden": [], "hatch_groups": groups,
            "bounds": [[-T, -OD / 2], [0.0, OD / 2]]}


def disc_manifest(tmp: Path, scale="1:1", section_text=WIDE,
                  projection="right"):
    apath = tmp / "analysis.json"
    common.write_json(apath, {
        "parts": [{"file": str(tmp / "d.FCStd"), "document": "d",
                   "sketches": [], "issues": [],
                   "bodies": [{"label": "Body", "object": "Body",
                               "type": "PartDesign::Body", "solids": 1,
                               "faces": 6, "volume_mm3": 1900.0,
                               "area_mm2": 0.0, "bbox_min": [0, 0, 0],
                               "bbox_max": [1, 50, 50],
                               "bbox_size": [T, OD, OD],
                               "material": "SS 316"}]}],
        "assembly": {"instances": [], "part_usage": {}, "joints": [],
                     "issues": [], "file": "", "document": "",
                     "workbench": "none", "bbox_min": None,
                     "bbox_max": None}})
    part = {"part_id": "01", "file": str(tmp / "d.FCStd"),
            "description": "THIN DISC", "drg_no": "FX-01",
            "material": "SS 316", "qty": 1, "front_view": "-X",
            "scale": scale,
            "section": {"enabled": True, "projection": projection,
                        "label": "A", "offset_frac": 0.5},
            "front_extent_dims": False, "section_extent_dims": False,
            "callouts": [
                {"view": "front", "text": "%%c50", "circle_r": OD / 2,
                 "suppress_dia_dim": True},
                {"view": "front", "circle_r": BORE / 2,
                 "suppress_dia_dim": True,
                 "lines": [{"text": "%%c6 H7 THRU"}]},
                {"view": "section", "at": [-0.5, OD / 2],
                 "lines": [{"text": section_text}]}],
            "fab_notes": ["T = 1.0 FROM 316 SHEET; DEBURR BOTH FACES."]}
    m = {"locked": True, "project": "FIXTURE", "drg_no_prefix": "FX",
         "drawn_by": "Er. P. PRANAY", "rev": "00", "model_dir": str(tmp),
         "assembly_file": "", "parts": [part],
         "assembly_sheet": {"enabled": False}, "_analysis_path": str(apath)}
    return m, part


def proj():
    return {"01:front": disc_front(), "01:section": disc_section()}


class BothViewsGetAColumnTest(unittest.TestCase):

    def build(self, **kw):
        with tempfile.TemporaryDirectory() as tmp:
            m, part = disc_manifest(Path(tmp), **kw)
            return draw.build_part_sheet(m, part, proj())

    def test_a_leader_on_each_view_places(self):
        ctx, _, lay = self.build()
        self.assertEqual(lay["unresolved"], [])

    def test_each_column_is_on_its_own_view_s_side(self):
        ctx, _, lay = self.build()
        text = ctx.render()
        # the front view's callouts land in the gap, the section's outside
        # the group: so the section's note is the rightmost annotation. A
        # one-line callout is drawn as a LEADER (its knee is the column),
        # a stacked one as MTEXTs after the first line.
        xs = {}
        lines = text.splitlines()
        for i, line in enumerate(lines):
            t = line.split()
            if t and t[0] == "MTEXT" and i + 1 < len(lines):
                xs[lines[i + 1]] = float(t[1])
            elif t and t[0] == "LEADER" and i + 1 < len(lines):
                xs[lines[i + 1]] = float(t[3])      # the knee
        inner = max(x for t, x in xs.items() if "H7" in t or t == "%%c50")
        outer = max(x for t, x in xs.items() if "THICKNESS" in t)
        self.assertGreater(outer, inner,
                           "the section's column should be outboard of "
                           "the front view's")

    def test_the_section_callout_is_really_drawn(self):
        ctx, _, _ = self.build()
        self.assertIn(WIDE, ctx.render())

    def test_it_works_the_other_way_round_too(self):
        ctx, _, lay = self.build(projection="left")
        self.assertEqual(lay["unresolved"], [])
        self.assertIn(WIDE, ctx.render())


class TheFailureSaysWhyTest(unittest.TestCase):
    """When the columns really do not fit, the message names them — the
    fix is a shorter callout or a bigger sheet, not a smaller scale."""

    def message(self, **kw):
        with tempfile.TemporaryDirectory() as tmp:
            m, part = disc_manifest(Path(tmp), **kw)
            try:
                draw.build_part_sheet(m, part, proj())
            except SystemExit:
                pass
            # common.die writes to stderr and raises; capture it instead
            return self._last

    def setUp(self):
        self._last = ""
        self.real_die = common.die

        def fake_die(msg):
            self._last = msg
            raise SystemExit(1)

        common.die = fake_die
        draw.die = fake_die

    def tearDown(self):
        common.die = self.real_die
        draw.die = self.real_die

    def test_it_names_the_callout_columns_not_the_scale(self):
        msg = self.message(scale="2:1")
        self.assertIn("callout columns do not fit", msg)
        self.assertIn("inner one needs", msg)
        self.assertIn("outer one", msg)
        self.assertNotIn("pick a smaller scale", msg)

    def test_views_that_really_are_too_big_still_say_so(self):
        msg = self.message(scale="5:1")
        self.assertIn("exceed drawing area", msg)
        self.assertIn("smaller scale", msg)


class BandWidthTest(unittest.TestCase):

    def setUp(self):
        self.ctx = mw.SheetContext(1, 1)
        self.callouts = [
            {"view": "front", "text": "%%c50", "circle_r": 25.0},
            {"view": "section", "lines": [{"text": WIDE}], "at": [0, 0]}]
        self.gdt = {"frames": []}

    def test_it_can_measure_one_view_s_column(self):
        both = draw.annotation_band_width(self.ctx, self.callouts, self.gdt)
        front = draw.annotation_band_width(self.ctx, self.callouts,
                                           self.gdt, views={"front"})
        section = draw.annotation_band_width(self.ctx, self.callouts,
                                             self.gdt, views={"section"})
        self.assertGreater(section, front, "the wide note is the section's")
        self.assertEqual(both, max(front, section))

    def test_a_view_with_no_callouts_needs_no_column(self):
        self.assertEqual(draw.annotation_band_width(
            self.ctx, self.callouts, self.gdt, views={"aux"}), 0.0)

    def test_frames_count_towards_their_own_view_s_column(self):
        gdt = {"frames": [{"symbol": "runout", "view": "section",
                           "target": "od_r25", "tol": "0.05",
                           "refs": ["A"]}]}
        self.assertEqual(draw.annotation_band_width(self.ctx, [], gdt,
                                                    views={"front"}), 0.0)
        self.assertGreater(draw.annotation_band_width(self.ctx, [], gdt,
                                                      views={"section"}),
                           0.0)


class PlaceViewsReservesBothTest(unittest.TestCase):

    def test_the_gap_holds_the_inner_column_and_the_outer_one_fits(self):
        ctx = mw.SheetContext(1, 1)
        inner, outer = ctx.mm(30.0), ctx.mm(25.0)
        offs = draw.place_views(ctx, disc_front(), disc_section(), "right",
                                right_band=True, band_w=outer,
                                band_gap_w=inner)
        fmn, fmx = disc_front()["bounds"]
        smn, smx = disc_section()["bounds"]
        f_right = fmx[0] + offs["front"][0]
        s_left = smn[0] + offs["section"][0]
        s_right = smx[0] + offs["section"][0]
        self.assertGreaterEqual(s_left - f_right, inner,
                                "the gap must hold the inner column")
        self.assertGreaterEqual(ctx.draw_area[2] - s_right, outer,
                                "the outer column must fit beside the "
                                "outer view")

    def test_one_column_still_behaves_as_it_did(self):
        ctx = mw.SheetContext(1, 1)
        band = ctx.mm(25.0)
        inboard = draw.place_views(ctx, disc_front(), disc_section(),
                                   "right", right_band=True, band_w=band,
                                   band_inboard=True)
        fmx = disc_front()["bounds"][1]
        smn = disc_section()["bounds"][0]
        self.assertGreaterEqual(
            (smn[0] + inboard["section"][0]) - (fmx[0] + inboard["front"][0]),
            band, "an inboard single column still opens the gap")


if __name__ == "__main__":
    unittest.main()
