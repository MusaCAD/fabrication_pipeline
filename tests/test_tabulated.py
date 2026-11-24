# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Pranay Kiran
"""Tabulated drawings: a letter dimension and a table of variants (#11).

Run with:  python3 -m unittest discover -s tests -v

A kit of trim discs differs only in thickness. One sheet should cover the
family: the varying size carries a LETTER and a table gives its value per
variant, with each variant's mark and quantity. A DIM entity always prints
the distance between its own def points — MusaCAD composes prefix +
measurement + suffix and nothing replaces the middle — so the modelled
variant's own 5 mm would print and read as the thickness of every disc.

The fixture is the real kit: ⌀40 x ⌀6.4 bore, 5 / 2 / 1 / 0.5 thick.
"""

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

OD, BORE, THICK = 40.0, 6.4, 5.0
ROWS = [["A", "5", "1"], ["B", "2", "3"], ["C", "1", "1"], ["D", "0.5", "2"]]


def seg(a, b):
    return {"t": "seg", "a": list(a), "b": list(b)}


def disc_front():
    return {"visible": [{"t": "circle", "c": [0.0, 0.0], "r": OD / 2},
                        {"t": "circle", "c": [0.0, 0.0], "r": BORE / 2}],
            "hidden": [], "bounds": [[-OD / 2, -OD / 2], [OD / 2, OD / 2]]}


def disc_section():
    """The disc on edge: the thickness runs along x, the diameter up y."""
    groups, vis = [], []
    for sign in (1, -1):
        loop = [[0.0, sign * BORE / 2], [THICK, sign * BORE / 2],
                [THICK, sign * OD / 2], [0.0, sign * OD / 2]]
        groups.append({"outer": loop, "holes": []})
        vis += [seg(a, b) for a, b in zip(loop, loop[1:] + loop[:1])]
    return {"visible": vis, "hidden": [], "hatch_groups": groups,
            "bounds": [[0.0, -OD / 2], [THICK, OD / 2]]}


def variants(**kw):
    spec = {"letter": "T", "title": "TRIM DISC KIT %%c40",
            "dim": {"view": "section", "orient": "h", "side": "bottom",
                    "a": [0.0, -OD / 2], "b": [THICK, -OD / 2]},
            "columns": ["MARK", "T", "QTY"], "rows": [list(r) for r in ROWS]}
    spec.update(kw)
    return spec


def kit_manifest(tmp: Path, **kw):
    analysis = {
        "model_dir": str(tmp), "freecad_version": "1.1.1",
        "assembly": {"file": "", "document": "", "workbench": "none",
                     "instances": [], "joints": [], "issues": [],
                     "part_usage": {}, "bbox_min": None, "bbox_max": None},
        "parts": [{"file": str(tmp / "disc.FCStd"), "document": "disc",
                   "sketches": [], "issues": [],
                   "bodies": [{"label": "Body", "object": "Body",
                               "type": "PartDesign::Body", "solids": 1,
                               "faces": 6, "volume_mm3": 6100.0,
                               "area_mm2": 0.0,
                               "bbox_min": [0, -20, -20],
                               "bbox_max": [5, 20, 20],
                               "bbox_size": [THICK, OD, OD],
                               "material": "SS 316"}]}]}
    apath = tmp / "analysis.json"
    common.write_json(apath, analysis)
    part = {"part_id": "13", "file": str(tmp / "disc.FCStd"),
            "description": "TRIM DISC KIT %%c40", "drg_no": "FX-13",
            "material": "SS 316", "qty": 7, "front_view": "-X",
            "scale": "2:1",
            "section": {"enabled": True, "projection": "right",
                        "label": "A", "offset_frac": 0.5},
            "front_extent_dims": False, "section_extent_dims": False,
            "callouts": [{"view": "front", "text": "%%c40",
                          "circle_r": OD / 2, "suppress_dia_dim": True}],
            "variants": variants(**kw)}
    m = {"locked": True, "project": "FIXTURE", "drg_no_prefix": "FX",
         "drawn_by": "Er. P. PRANAY", "rev": "00", "model_dir": str(tmp),
         "assembly_file": "", "parts": [part],
         "assembly_sheet": {"enabled": False}, "_analysis_path": str(apath)}
    return m, part


def texts(recs):
    """Every text content a record list draws."""
    out, lines = [], "\n".join(recs).splitlines()
    for i, line in enumerate(lines):
        if line.startswith(("MTEXT ", "TEXT ")) and i + 1 < len(lines):
            out.append(lines[i + 1])
    return out


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


class LetterDimensionTest(unittest.TestCase):

    def setUp(self):
        self.ctx = mw.SheetContext(2, 1)
        self.ctx.dimstyles = draw.sheet_dimstyles(self.ctx)
        self.layout = draw.Layout(self.ctx)
        self.views = {"front": disc_front(), "section": disc_section()}
        self.offs = {"front": (40.0, 80.0), "section": (40.0, 80.0)}

    def place(self, **kw):
        part = {"variants": variants(**kw)}
        return draw.place_letter_dim(self.ctx, self.layout, part,
                                     self.views, self.offs)

    def test_it_prints_the_letter_and_no_value(self):
        recs = self.place()
        self.assertEqual(self.layout.unresolved, [])
        self.assertIn("T", texts(recs))
        # nothing on it reads as a measurement of the modelled variant
        for t in texts(recs):
            self.assertNotIn("5", t)
        # and it is not a DIM entity, which could only print the distance
        self.assertFalse([r for r in recs if r.startswith("DIM ")])

    def test_it_is_drawn_as_a_dimension(self):
        recs = self.place()
        lines = [r for r in recs if r.startswith("LINE ")]
        # two extension lines and the dimension line
        self.assertGreaterEqual(len(lines), 3)
        arrows = solid_triangles("\n".join(recs))
        self.assertEqual(len(arrows), 2, "a dimension has two arrowheads")

    def test_the_arrowheads_match_musacad_s_own(self):
        """core/dimension.cpp append_arrowhead, ArrowType::Filled: base one
        `size` back along the line, 0.18*size each side. Two kinds of arrow
        on one sheet read as two drawings."""
        size = 3.0
        tri = draw.dim_arrowhead((10.0, 20.0), (1.0, 0.0), size)
        v = [float(x) for x in tri.splitlines()[0].split()[3:9]]
        tip, p1, p2 = (v[0], v[1]), (v[2], v[3]), (v[4], v[5])
        self.assertAlmostEqual(p1[0] - tip[0], size, places=9)
        self.assertAlmostEqual(abs(p1[1] - p2[1]), 2 * 0.18 * size, places=9)

    def test_it_spans_the_feature_it_dimensions(self):
        recs = self.place()
        xs = []
        for r in recs:
            if r.startswith("LINE "):
                t = [float(v) for v in r.split()[1:5]]
                if abs(t[1] - t[3]) < 1e-9:          # the dimension line
                    xs = sorted((t[0], t[2]))
        self.assertTrue(xs)
        self.assertAlmostEqual(xs[1] - xs[0], THICK, places=6)

    def test_no_variants_draws_nothing(self):
        self.assertEqual(draw.place_letter_dim(self.ctx, self.layout, {},
                                               self.views, self.offs), [])

    def test_a_view_it_cannot_find_fails_loudly(self):
        self.place(dim={"view": "aux", "orient": "h",
                        "a": [0.0, 0.0], "b": [5.0, 0.0]})
        self.assertIn("letter-dim-T-noview-aux", self.layout.unresolved)


class VariantsTableTest(unittest.TestCase):

    def setUp(self):
        self.ctx = mw.SheetContext(2, 1)
        self.layout = draw.Layout(self.ctx)

    def table(self, **kw):
        part = {"variants": variants(**kw)}
        return draw.place_variants_table(self.ctx, self.layout, part,
                                         mw.PARTS_ROW_H * self.ctx.t)

    def test_every_variant_and_its_values_are_printed(self):
        got = texts(self.table())
        self.assertEqual(self.layout.unresolved, [])
        self.assertIn("TRIM DISC KIT %%c40", got)
        for head in ("MARK", "T", "QTY"):
            self.assertIn(head, got)
        for mark, t, qty in ROWS:
            self.assertIn(mark, got)
            self.assertIn(t, got)

    def test_it_is_a_ruled_grid(self):
        recs = self.table()
        rules = [r for r in recs if r.startswith("LINE ")]
        # one rule per row boundary plus one per column boundary
        self.assertGreaterEqual(len(rules), len(ROWS) + 2 + 4)

    def test_no_rows_no_table(self):
        self.assertEqual(self.table(rows=[]), [])

    def test_a_table_with_nowhere_to_go_fails_loudly(self):
        ax0, ay0, ax1, ay1 = self.ctx.draw_area
        self.layout.claim((ax0, ay0, ax1, ay1), "everything")
        self.assertEqual(self.table(), [])
        self.assertIn("variants-table", self.layout.unresolved)


class TabulatedSheetTest(unittest.TestCase):
    """The whole sheet, as the kit is really drawn."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="fabpipe-kit-")
        cls.m, cls.part = kit_manifest(Path(cls.tmp.name))
        cls.proj = {"13:front": disc_front(), "13:section": disc_section()}
        cls.ctx, _, cls.lay = draw.build_part_sheet(cls.m, cls.part, cls.proj)
        cls.text = cls.ctx.render()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_the_sheet_places_everything(self):
        self.assertEqual(self.lay["unresolved"], [])

    def test_the_letter_and_the_table_are_both_on_it(self):
        got = texts(self.text.splitlines())
        self.assertIn("T", got)
        self.assertIn("MARK", got)
        self.assertIn("0.5", got)

    @unittest.skipUnless(common.MUSA_PLOT.exists(), "musa_plot not built")
    def test_the_sheet_reads_no_thickness_value(self):
        """The real failure: the modelled variant's own 5 mm printed as if
        it were the thickness of every disc in the kit."""
        with tempfile.TemporaryDirectory() as tmp:
            musa = Path(tmp) / "kit.musa"
            musa.write_text(self.text)
            for d in common.dump_dims(musa):
                self.assertNotAlmostEqual(d["measure"], THICK, places=3,
                                          msg=f"{d['label']} is the "
                                              "modelled thickness")


class TabulatedLockTest(unittest.TestCase):

    def part(self, **kw):
        return {"file": "/m/disc.FCStd", "variants": variants(**kw)}

    def test_a_clean_tabulated_part_locks(self):
        self.assertEqual(mf.variants_problems(self.part(), "13"), [])

    def test_a_part_with_no_variants_is_not_tabulated(self):
        self.assertEqual(mf.variants_problems({"file": "/m/x.FCStd"}, "13"),
                         [])

    def test_the_table_must_have_a_column_for_the_letter(self):
        bad = mf.variants_problems(
            self.part(columns=["MARK", "THICK", "QTY"]), "13")
        self.assertIn("no 'T' column", " ".join(bad))

    def test_a_short_row_fails(self):
        bad = mf.variants_problems(
            self.part(rows=[["A", "5", "1"], ["B", "2"]]), "13")
        self.assertIn("row 2 has 2 values for 3 columns", " ".join(bad))

    def test_no_rows_fails(self):
        self.assertIn("rows is empty",
                      " ".join(mf.variants_problems(self.part(rows=[]), "13")))

    def test_a_letter_that_is_not_a_letter_fails(self):
        self.assertIn("must be one letter",
                      " ".join(mf.variants_problems(
                          self.part(letter="TH"), "13")))

    def test_a_missing_dimension_fails(self):
        bad = mf.variants_problems(self.part(dim=None), "13")
        self.assertIn("the dimension the letter stands on", " ".join(bad))

    def test_a_dim_without_real_points_fails(self):
        bad = mf.variants_problems(
            self.part(dim={"view": "section", "orient": "h", "a": "x",
                           "b": [1, 2]}), "13")
        self.assertIn("variants.dim.a", " ".join(bad))


if __name__ == "__main__":
    unittest.main()
