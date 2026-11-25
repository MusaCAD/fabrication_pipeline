# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Pranay Kiran
"""A widened title block keeps its A4 width (issue #28).

Run with:  python3 -m unittest discover -s tests -v

The real failure, on every A3-landscape sheet of a set: the revision
table, the parts row and the title block run from border to border on
the A4 sheet, and the widening moved only the ends at or right of
LEFT_EDGE_X. So each of those rules was STRETCHED by the whole widening
— 1228 template units, 221 printed mm — and the sheet went out with a
row of empty cells across the top and an empty box the size of a
postcard beside the title block. They hold nothing, they read as part of
the title block, and they take a band 221 mm wide at the top and the
bottom of the sheet that the drawing could have used.
"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import common  # noqa: E402
import draw  # noqa: E402
import layout_check as lc  # noqa: E402
import musa_writer as mw  # noqa: E402
import widen_template as wt  # noqa: E402

A = mw.P_ANNO
DA = common.DRAWING_AREA
FI = common.FRAME_INNER


def stretched(src: Path, dst: Path, paper="A3", orientation="landscape"):
    """The widening as it was BEFORE this fix — every point at or right
    of LEFT_EDGE_X moves and the rest stays, so a rule that starts at the
    border is stretched. This is the fixture: a template made this way is
    what the sets in the field carry."""
    dx, dy = wt.widening(paper, orientation)
    out = []
    for raw in src.read_text().split("\n"):
        tok = raw.split()
        idx = wt.point_indices(tok) if tok else None
        if idx is None:
            out.append(raw)
            continue
        for xi, yi in idx:
            if dx and float(tok[xi]) >= wt.LEFT_EDGE_X:
                tok[xi] = repr(float(tok[xi]) + dx)
            if dy and float(tok[yi]) >= wt.TOP_EDGE_Y:
                tok[yi] = repr(float(tok[yi]) + dy)
        out.append(" ".join(tok))
    dst.write_text("\n".join(out))
    return dx, dy


def lines_of(path: Path):
    out = []
    for raw in path.read_text().split("\n"):
        tok = raw.split()
        if tok and tok[0] == "LINE":
            out.append(tuple(float(v) for v in tok[1:5]))
    return out


def seg(a, b):
    return {"t": "seg", "a": list(a), "b": list(b)}


class WidenedTemplateTest(unittest.TestCase):
    """The real A4 title block, widened to A3 landscape."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="fabpipe-28-")
        d = Path(cls.tmp.name)
        cls.a3 = d / "a3l.musa"
        cls.info = wt.widen(common.TEMPLATE, cls.a3, "A3", "landscape")
        cls.old = d / "a3l_old.musa"
        stretched(common.TEMPLATE, cls.old)
        cls.dx = cls.info["dx"]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def band_rules(self, path, band):
        """Horizontal rules of one furniture band, keyed by the y they
        are drawn at: {y: (x0, x1)}."""
        y0, y1 = common.furniture_bands(DA)[band][1:]
        return {round(n[1], 6): (min(n[0], n[2]), max(n[0], n[2]))
                for n in lines_of(path)
                if abs(n[1] - n[3]) < 1e-9 and y0 - 0.1 <= n[1] <= y1 + 0.1}

    def test_it_was_the_whole_widening_that_was_stretched(self):
        """The fixture is the failure: a rule that started at the border
        used to grow by the whole widening — 221 mm of empty box."""
        grew = 0
        for band in (0, 1):
            was, now = (self.band_rules(common.TEMPLATE, band),
                        self.band_rules(self.old, band))
            for y, (a, b) in was.items():
                c, d = now[y]
                if abs(a - DA[0]) < 0.1:          # it starts at the border
                    self.assertAlmostEqual(d - c, b - a + self.dx, places=3)
                    grew += 1
        self.assertGreaterEqual(grew, 7)          # 5 revision + 2 + title

    def test_each_furniture_rule_keeps_its_a4_width(self):
        for band in (0, 1):
            was, now = (self.band_rules(common.TEMPLATE, band),
                        self.band_rules(self.a3, band))
            for y, (a, b) in was.items():
                c, d = now[y]
                self.assertAlmostEqual(d - c, b - a, places=6,
                                       msg=f"the rule at y={y:.0f} was "
                                           "stretched")

    def test_the_furniture_travels_with_the_right_edge(self):
        for band in (0, 1):
            was, now = (self.band_rules(common.TEMPLATE, band),
                        self.band_rules(self.a3, band))
            for y, (a, _b) in was.items():
                self.assertAlmostEqual(now[y][0], a + self.dx, places=6)

    def test_each_band_is_closed_on_its_new_left_edge(self):
        left = FI[0] + self.dx
        for _name, y0, y1 in common.furniture_bands(DA):
            closing = [n for n in lines_of(self.a3)
                       if abs(n[0] - left) < 0.1 and abs(n[2] - left) < 0.1
                       and min(n[1], n[3]) <= y0 + 0.1
                       and max(n[1], n[3]) >= y1 - 0.1]
            self.assertEqual(len(closing), 1,
                             f"band at y={y0:.0f} has {len(closing)} "
                             "left-hand sides")

    def test_the_border_and_the_corner_marks_stay_where_they_are(self):
        """They are not furniture: the border runs through both bands and
        a corner mark lies outside it, so neither travels."""
        src = common.TEMPLATE.read_text().split("\n")
        got = self.a3.read_text().split("\n")
        checked = 0
        for raw_src, raw_new in zip(src, got):
            tok_s, tok_n = raw_src.split(), raw_new.split()
            if not tok_s or tok_s[0] != "POLYLINE":
                continue
            pts = [(float(tok_s[xi]), float(tok_s[yi]))
                   for xi, yi in wt.point_indices(tok_s)]
            if wt.furniture_band(pts) is not None:
                continue
            for (xi, _yi), (x, _y) in zip(wt.point_indices(tok_s), pts):
                if x < wt.LEFT_EDGE_X:
                    self.assertAlmostEqual(x, float(tok_n[xi]), places=6)
                    checked += 1
        self.assertGreaterEqual(checked, 4)

    def test_the_sheet_is_the_same_size_and_the_same_paper(self):
        self.assertEqual(mw.template_bbox(self.a3), mw.template_bbox(self.old))
        g = mw.template_geom(self.a3)
        self.assertEqual((g["paper"], g["orientation"]), ("A3", "landscape"))
        self.assertAlmostEqual(g["s0"], common.S0)
        self.assertEqual(g["drawing_area"], mw.template_geom(self.old)
                         ["drawing_area"])

    def test_a_sheet_that_needs_no_widening_is_copied_as_it_is(self):
        self.assertEqual(wt.widen_text(common.TEMPLATE.read_text(), 0.0, 0.0),
                         common.TEMPLATE.read_text())


class WhatCountsAsFurnitureTest(unittest.TestCase):
    """The rule itself, which is what keeps the sheet's own frame still:
    a band's furniture lies wholly inside ONE band and inside the
    border."""

    def band(self, *pts):
        return wt.furniture_band(list(pts))

    def test_a_rule_of_the_title_block_is_furniture(self):
        self.assertEqual(self.band((DA[0], 250.0), (800.0, 250.0)), 0)

    def test_a_rule_of_the_revision_table_is_the_other_band(self):
        self.assertEqual(self.band((DA[0], 1545.0), (DA[2], 1545.0)), 1)

    def test_the_border_belongs_to_neither(self):
        self.assertIsNone(self.band((FI[0], FI[1]), (FI[0], FI[3])))

    def test_a_mark_outside_the_border_is_not_furniture(self):
        """It is at the sheet's left-hand corner and stays there, even
        where its y would put it in a band."""
        self.assertIsNone(self.band((19.1, 200.0), (33.9, 200.0)))
        self.assertIsNone(self.band((1069.1, 1600.0), (1083.9, 1600.0)))

    def test_nor_is_anything_in_the_drawing_area(self):
        self.assertIsNone(self.band((300.0, 900.0), (700.0, 900.0)))


class MeasuredBlocksTest(unittest.TestCase):
    """What the sheet geometry makes of it, measured off the file."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="fabpipe-28b-")
        d = Path(cls.tmp.name)
        cls.a3 = d / "a3l.musa"
        cls.dx = wt.widen(common.TEMPLATE, cls.a3, "A3", "landscape")["dx"]
        cls.old = d / "a3l_old.musa"
        stretched(common.TEMPLATE, cls.old)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_a4_furniture_spans_the_sheet(self):
        for b in mw.template_furniture():
            self.assertAlmostEqual(b[0], DA[0], places=3)
            self.assertAlmostEqual(b[2], DA[2], places=3)

    def test_a_widened_block_starts_where_its_rules_do(self):
        for b in mw.template_furniture(self.a3, (self.dx, 0.0)):
            self.assertAlmostEqual(b[0], FI[0] + self.dx, places=3)
            self.assertAlmostEqual(b[2], DA[2] + self.dx, places=3)

    def test_a_stretched_one_still_spans_the_sheet(self):
        """A set drawn before this fix keeps the sheet it had."""
        for b in mw.template_furniture(self.old, (self.dx, 0.0)):
            self.assertAlmostEqual(b[0], DA[0], places=3)

    def test_the_a4_sheet_is_the_rect_it_always_was(self):
        g = common.sheet_geom()
        self.assertEqual(g["sheet_area"], g["drawing_area"])
        self.assertEqual(g["blocks"], ())

    def test_a_stretched_sheet_is_the_rect_it_always_was(self):
        g = mw.template_geom(self.old)
        self.assertEqual(g["sheet_area"], g["drawing_area"])
        self.assertEqual(g["blocks"], ())

    def test_a_widened_sheet_is_the_whole_border_less_the_furniture(self):
        g = mw.template_geom(self.a3)
        self.assertEqual(g["sheet_area"],
                         (DA[0], FI[1], DA[2] + self.dx, FI[3]))
        self.assertEqual([n for n, _b in g["blocks"]],
                         ["title block", "revision table"])
        tb = dict(g["blocks"])["title block"]
        self.assertAlmostEqual(tb[0], FI[0] + self.dx, places=3)
        self.assertAlmostEqual(tb[3], DA[1], places=3)

    def test_the_strip_is_the_widening_wide_over_the_whole_height(self):
        g = mw.template_geom(self.a3)
        area, tb = g["sheet_area"], dict(g["blocks"])["title block"]
        self.assertAlmostEqual((tb[0] - area[0]) * common.S0, 221.3,
                               places=1)
        self.assertAlmostEqual((area[3] - area[1]) * common.S0, 275.7,
                               places=1)


class LayoutKnowsTheFurnitureTest(unittest.TestCase):
    """The engine may use the freed band and may not use the furniture."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="fabpipe-28c-")
        cls.a3 = Path(cls.tmp.name) / "a3l.musa"
        wt.widen(common.TEMPLATE, cls.a3, "A3", "landscape")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.ctx = mw.SheetContext(1, 1, geom=mw.template_geom(self.a3),
                                   template=self.a3)
        self.ctx.dimstyles = draw.sheet_dimstyles(self.ctx)
        self.layout = draw.Layout(self.ctx)

    def test_the_title_block_is_a_claim_of_its_own(self):
        self.assertEqual([c[4] for c in self.layout.claims],
                         ["title block", "revision table"])

    def test_a_rect_on_the_title_block_is_not_inside(self):
        _n, tb = self.ctx.blocks[0]
        r = (tb[0] + 5.0, tb[1] + 5.0, tb[0] + 25.0, tb[1] + 15.0)
        self.assertFalse(self.layout.inside(r))

    def test_a_rect_in_the_band_it_freed_is(self):
        _n, tb = self.ctx.blocks[0]
        r = (self.ctx.sheet_area[0] + 5.0, tb[1] + 5.0, tb[0] - 5.0,
             tb[1] + 15.0)
        self.assertTrue(self.layout.inside(r))
        self.assertFalse(self.layout.collides(r))

    def test_the_sheet_is_taller_than_the_band_between_the_furniture(self):
        self.assertLess(self.ctx.sheet_area[1], self.ctx.draw_area[1])
        self.assertGreater(self.ctx.sheet_area[3], self.ctx.draw_area[3])
        self.assertEqual(len(self.ctx.areas), 2)

    def test_an_a4_sheet_has_one_area_and_no_blocks(self):
        ctx = mw.SheetContext(1, 1)
        self.assertEqual(ctx.areas, (ctx.draw_area,))
        self.assertEqual(ctx.blocks, ())
        self.assertEqual(draw.Layout(ctx).claims, [])


class TheBlockIsUsedWhenNeededTest(unittest.TestCase):
    """A block of annotation takes the band between the furniture first,
    and the room beside the title block only when it must."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="fabpipe-28d-")
        cls.a3 = Path(cls.tmp.name) / "a3l.musa"
        wt.widen(common.TEMPLATE, cls.a3, "A3", "landscape")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.ctx = mw.SheetContext(1, 1, geom=mw.template_geom(self.a3),
                                   template=self.a3)
        self.ctx.dimstyles = draw.sheet_dimstyles(self.ctx)
        self.layout = draw.Layout(self.ctx)

    def fill_the_band(self):
        """Everything between the furniture taken, as a crowded sheet
        takes it."""
        d = self.ctx.draw_area
        self.layout.claim((d[0], d[1], d[2], d[3]), "views", "view")

    def test_the_notes_go_in_the_band_while_it_is_free(self):
        recs = draw.place_notes(self.ctx, self.layout,
                                ["BREAK ALL SHARP EDGES"], 0.0)
        self.assertTrue(recs)
        spot = next(c for c in self.layout.claims if c[4] == "notes-block")
        self.assertGreaterEqual(spot[1], self.ctx.draw_area[1] - 1e-6)

    def test_and_beside_the_title_block_when_it_is_not(self):
        self.fill_the_band()
        recs = draw.place_notes(self.ctx, self.layout,
                                ["BREAK ALL SHARP EDGES"], 0.0)
        self.assertTrue(recs, "the notes were dropped")
        spot = next(c for c in self.layout.claims if c[4] == "notes-block")
        self.assertLess(spot[1], self.ctx.draw_area[1])
        self.assertTrue(self.layout.inside(spot[:4]))

    def test_a_table_too(self):
        self.fill_the_band()
        recs = draw.place_table(self.ctx, self.layout, "BEND TABLE",
                                ("NO.", "ANGLE"), [("1", "90%%d")], 0.0,
                                "bend-table")
        self.assertTrue(recs)
        self.assertEqual(self.layout.unresolved, [])
        spot = next(c for c in self.layout.claims if c[4] == "bend-table")
        self.assertLess(spot[1], self.ctx.draw_area[1])

    def test_on_an_a4_sheet_there_is_nowhere_else_to_go(self):
        ctx = mw.SheetContext(1, 1)
        ctx.dimstyles = draw.sheet_dimstyles(ctx)
        layout = draw.Layout(ctx)
        layout.claim(ctx.draw_area, "views", "view")
        draw.place_notes(ctx, layout, ["BREAK ALL SHARP EDGES"], 0.0)
        self.assertIn("notes-block", layout.unresolved)


class TheDrawingDoesNotMoveTest(unittest.TestCase):
    """The same part on the same paper, drawn on the stretched title
    block and on the fixed one: the drawing is the same drawing."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="fabpipe-28e-")
        d = Path(cls.tmp.name)
        cls.a3 = d / "a3l.musa"
        wt.widen(common.TEMPLATE, cls.a3, "A3", "landscape")
        cls.old = d / "a3l_old.musa"
        stretched(common.TEMPLATE, cls.old)
        cls.dir = d

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def bar_view(self):
        pts = [(0.0, 0.0), (120.0, 0.0), (120.0, 60.0), (0.0, 60.0)]
        return {"visible": [seg(a, b)
                            for a, b in zip(pts, pts[1:] + pts[:1])],
                "hidden": [], "hatch_groups": [],
                "bounds": [[0.0, 0.0], [120.0, 60.0]]}

    def sheet_on(self, template):
        analysis = {
            "model_dir": str(self.dir), "freecad_version": "1.1.1",
            "assembly": {"file": "", "document": "", "workbench": "none",
                         "instances": [], "joints": [], "issues": [],
                         "part_usage": {}, "bbox_min": None,
                         "bbox_max": None},
            "parts": [{"file": str(self.dir / "bar.FCStd"),
                       "document": "bar", "sketches": [], "issues": [],
                       "bodies": [{"label": "Body", "object": "Body",
                                   "type": "PartDesign::Body", "solids": 1,
                                   "faces": 6, "volume_mm3": 1000.0,
                                   "area_mm2": 0.0, "bbox_min": [0, 0, 0],
                                   "bbox_max": [120.0, 60.0, 60.0],
                                   "bbox_size": [120.0, 60.0, 60.0],
                                   "material": "AL 6061"}]}]}
        apath = self.dir / "analysis.json"
        common.write_json(apath, analysis)
        part = {"part_id": "01", "file": str(self.dir / "bar.FCStd"),
                "description": "BAR", "drg_no": "FX-28",
                "material": "AL 6061", "qty": 1, "front_view": "+Y",
                "scale": "1:1", "section": {"enabled": False},
                "paper": "A3L",
                "fab_notes": ["BREAK ALL SHARP EDGES 0.5 x 45%%d."]}
        m = {"locked": True, "project": "FIXTURE", "drg_no_prefix": "FX",
             "rev": "00", "model_dir": str(self.dir), "assembly_file": "",
             "parts": [part], "assembly_sheet": {"enabled": False},
             "templates": {"A3L": str(template)},
             "_analysis_path": str(apath)}
        ctx, _r, lay = draw.build_part_sheet(m, part,
                                             {"01:front": self.bar_view()})
        self.assertEqual(lay["unresolved"], [])
        return ctx

    @staticmethod
    def split(ctx, text):
        """(the drawing, the furniture's own rules): a rule drawn wholly
        below the band between the furniture, or wholly above it, is the
        title block's or the revision table's."""
        draw_, band = [], []
        for ln in text.split("\n"):
            tok = ln.split()
            ys = ()
            if ln.startswith("LINE "):
                ys = (float(tok[2]), float(tok[4]))
            elif ln.startswith("POLYLINE "):
                ys = tuple(float(tok[4 + 2 * j]) for j in range(int(tok[2])))
            if ys and (max(ys) <= ctx.draw_area[1] + 1e-6
                       or min(ys) >= ctx.draw_area[3] - 1e-6):
                band.append(ln)
                continue
            draw_.append(ln)
        return draw_, band

    def test_the_drawing_is_the_same_drawing(self):
        old_ctx, new_ctx = self.sheet_on(self.old), self.sheet_on(self.a3)
        old, old_band = self.split(old_ctx, old_ctx.render())
        new, new_band = self.split(new_ctx, new_ctx.render())
        self.assertTrue(old_band)
        self.assertEqual(old, new)          # every dim, note and view
        self.assertNotEqual(old_band, new_band)

    def test_and_the_title_block_is_the_one_difference(self):
        old = self.sheet_on(self.old).render().split("\n")
        new = self.sheet_on(self.a3).render().split("\n")
        self.assertNotEqual(old, new)
        self.assertEqual(len(new), len(old) + 1)   # the closing rule


class CheckReadsTheFurnitureTest(unittest.TestCase):
    """And the layout check knows which ink is the template's."""

    AREA = (0.0, 0.0, 400.0, 300.0)
    BLOCK = ("title block", (250.0, 0.0, 400.0, 60.0))

    def sheet(self, *recs):
        return "MUSACAD 14\n" + "\n".join(recs) + "\nEND\n"

    def test_the_title_block_s_own_text_is_not_the_drawing_s(self):
        text = self.sheet(mw.mtext((300.0, 30.0), "DRG NO.", 3.0),
                          mw.line((250.0, 20.0), (400.0, 20.0)))
        self.assertEqual(lc.check_sheet(text, self.AREA,
                                        blocks=[self.BLOCK]), [])

    def test_a_leader_is_not_said_to_run_along_a_title_block_rule(self):
        """The rules are furniture, not the part's outline: without that,
        every leader beside the title block runs along one of them."""
        text = self.sheet(mw.line((250.0, 20.0), (400.0, 20.0)),
                          mw.leader((395.0, 20.0), (245.0, 20.0), "X",
                                    3.0, props=A))
        self.assertNotIn("leader-on-outline",
                         [f["kind"] for f in
                          lc.check_sheet(text, self.AREA,
                                         blocks=[self.BLOCK])])

    def test_nor_is_a_symbol_said_to_lie_on_its_text(self):
        tri = [(298.0, 28.0), (294.0, 24.0), (302.0, 24.0)]
        text = self.sheet(mw.mtext((295.0, 25.0), "DRG NO.", 3.0),
                          mw.hatch([tri], "SOLID", props=A))
        self.assertNotIn("ink-on-text",
                         [f["kind"] for f in
                          lc.check_sheet(text, self.AREA,
                                         blocks=[self.BLOCK])])

    def test_but_both_are_read_where_the_drawing_is(self):
        """The same two sheets with nothing reserved: this is what the
        check would say if the furniture were the drawing's ink."""
        along = self.sheet(mw.line((250.0, 20.0), (400.0, 20.0)),
                           mw.leader((395.0, 20.0), (245.0, 20.0), "X",
                                     3.0, props=A))
        tri = [(298.0, 28.0), (294.0, 24.0), (302.0, 24.0)]
        on = self.sheet(mw.mtext((295.0, 25.0), "DRG NO.", 3.0),
                        mw.hatch([tri], "SOLID", props=A))
        self.assertIn("leader-on-outline",
                      [f["kind"] for f in lc.check_sheet(along, self.AREA)])
        self.assertIn("ink-on-text",
                      [f["kind"] for f in lc.check_sheet(on, self.AREA)])

    def test_an_annotation_on_it_is_reported(self):
        text = self.sheet(mw.mtext((300.0, 30.0), "%%c20 H7", 3.0, props=A))
        hit = [f for f in lc.check_sheet(text, self.AREA,
                                         blocks=[self.BLOCK])
               if f["kind"] == "text-off-sheet"]
        self.assertTrue(hit)
        self.assertIn("title block", hit[0]["what"])

    def test_one_in_the_band_beside_it_is_not(self):
        text = self.sheet(mw.mtext((100.0, 30.0), "%%c20 H7", 3.0, props=A))
        self.assertEqual(lc.check_sheet(text, self.AREA,
                                        blocks=[self.BLOCK]), [])


class LogoBelongsInTheTitleBlockTest(unittest.TestCase):
    """A logo box is given in the A4 title block's own units; one in the
    drawing area would print under the views."""

    def template_with_box(self, d: Path, box):
        tpl = d / "t.musa"
        tpl.write_text(common.TEMPLATE.read_text())
        png = d / "logo.png"
        png.write_bytes(bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000100000001008060000001ff3ff"
            "61"))
        (d / "t.logo.json").write_text(
            '{"png": "logo.png", "box": %s}' % list(box))
        return tpl

    def test_a_logo_in_the_title_block_is_fine(self):
        with tempfile.TemporaryDirectory() as d:
            tpl = self.template_with_box(Path(d), (800.9, 335.0, 1044.4,
                                                   430.0))
            self.assertEqual(mw.template_elements_off_sheet(tpl), [])

    def test_a_logo_in_the_drawing_area_is_reported(self):
        with tempfile.TemporaryDirectory() as d:
            tpl = self.template_with_box(Path(d), (400.0, 700.0, 600.0,
                                                   800.0))
            bad = mw.template_elements_off_sheet(tpl)
            self.assertTrue(bad)
            self.assertIn("title block", bad[0])
            self.assertIn("over the drawing", bad[0])


if __name__ == "__main__":
    unittest.main()
