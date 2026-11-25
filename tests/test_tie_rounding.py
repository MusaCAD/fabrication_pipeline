# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Pranay Kiran
"""A tie rounds the same way on every paper (issue #26).

Run with:  python3 -m unittest discover -s tests -v

The real failure: a 362.625 mm tube length printed "362.63" on A4
portrait and "362.62" on A3 landscape, from one model and one manifest —
`--dump-dims` measured 362.625 both times. The def points are placed full
size at offsets that differ per paper, so the last bits of the float
difference, and with them the rounding of a value exactly halfway between
two printed ones, depend on where the view happens to sit. The two sheets
then disagree with each other and with any earlier revision, and the
value check passes both because it compares against the measurement.
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
import musa_writer as mw  # noqa: E402
import widen_template  # noqa: E402

LEN, WID = 362.625, 75.5          # the tube, to the half-hundredth
# a hole pitch on a tie too: a location dim states its value to three
# places (dim_precision), so the boundary is the half-thousandth
PITCH = 46.3755


def seg(a, b):
    return {"t": "seg", "a": list(a), "b": list(b)}


def bar_view(length=LEN, width=WID):
    pts = [(0.0, 0.0), (length, 0.0), (length, width), (0.0, width)]
    return {"visible": [seg(a, b) for a, b in zip(pts, pts[1:] + pts[:1])],
            "hidden": [], "hatch_groups": [],
            "bounds": [[0.0, 0.0], [length, width]]}


class SnapValueTest(unittest.TestCase):
    """Only a tie is touched, and it goes away from zero."""

    def test_a_tie_at_two_places_is_nudged_up(self):
        v = draw.snap_value(362.625, 2)
        self.assertIsNotNone(v)
        self.assertGreater(v, 362.625)
        self.assertLess(v, 362.6251)
        self.assertEqual(f"{v:.2f}", "362.63")

    def test_float_noise_around_the_tie_lands_on_the_same_value(self):
        for raw in (362.625, 362.62499999999994, 362.6250000000001):
            self.assertEqual(f"{draw.snap_value(raw, 2):.2f}", "362.63")

    def test_a_value_that_is_not_a_tie_is_left_alone(self):
        self.assertIsNone(draw.snap_value(362.624, 2))
        self.assertIsNone(draw.snap_value(362.0, 2))
        self.assertIsNone(draw.snap_value(45.0, 0))

    def test_a_tie_at_whole_millimetres_too(self):
        v = draw.snap_value(45.5, 0)
        self.assertEqual(f"{v:.0f}", "46")

    def test_a_negative_tie_rounds_away_from_zero(self):
        self.assertEqual(f"{draw.snap_value(-2.5, 0):.0f}", "-3")


class SnapDefPointsTest(unittest.TestCase):
    """The move is in the measured axis, and it is a micrometre."""

    def test_a_horizontal_dim_moves_only_x(self):
        a, b = (100.0, 50.0), (462.625, 50.0)
        c = draw.snap_def_points(a, b, "h", 2)
        self.assertEqual(c[1], b[1])
        self.assertAlmostEqual(c[0] - b[0], 1e-5, places=9)

    def test_a_vertical_dim_moves_only_y(self):
        a, b = (10.0, 0.0), (10.0, 46.375)
        c = draw.snap_def_points(a, b, "v", 2)
        self.assertEqual(c[0], b[0])
        self.assertGreater(c[1], b[1])

    def test_a_dim_that_is_not_on_a_tie_keeps_its_points(self):
        a, b = (100.0, 50.0), (462.624, 50.0)
        self.assertEqual(draw.snap_def_points(a, b, "h", 2), b)

    def test_a_detail_view_snaps_what_the_reader_sees(self):
        # drawn at 2x, DIMLFAC 0.5: the label reads 2.625, a tie
        a, b = (0.0, 0.0), (5.25, 0.0)
        c = draw.snap_def_points(a, b, "h", 2, lfac=0.5)
        self.assertAlmostEqual((c[0] - a[0]) * 0.5, 2.62501, places=7)

    def test_a_length_along_the_line_is_scaled_whole(self):
        a, b = (0.0, 0.0), (0.0, 36.3125)
        c = draw.snap_def_points(a, b, None, 2, lfac=2.0)   # a diameter
        self.assertAlmostEqual(2.0 * (c[1] - a[1]), 72.62501, places=7)


class TwoPapersTest(unittest.TestCase):
    """One part, two papers: the dimension has to say the same thing."""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.TemporaryDirectory(prefix="fabpipe-tie-")
        cls.a3 = Path(cls.tmpdir.name) / "a3l.musa"
        widen_template.widen(common.TEMPLATE, cls.a3, "A3", "landscape")

    @classmethod
    def tearDownClass(cls):
        cls.tmpdir.cleanup()

    def manifest(self, tmp: Path, paper=None):
        analysis = {
            "model_dir": str(tmp), "freecad_version": "1.1.1",
            "assembly": {"file": "", "document": "", "workbench": "none",
                         "instances": [], "joints": [], "issues": [],
                         "part_usage": {}, "bbox_min": None,
                         "bbox_max": None},
            "parts": [{"file": str(tmp / "bar.FCStd"), "document": "bar",
                       "sketches": [], "issues": [],
                       "bodies": [{"label": "Body", "object": "Body",
                                   "type": "PartDesign::Body", "solids": 1,
                                   "faces": 6, "volume_mm3": 1000.0,
                                   "area_mm2": 0.0,
                                   "bbox_min": [0, 0, 0],
                                   "bbox_max": [LEN, WID, WID],
                                   "bbox_size": [LEN, WID, WID],
                                   "material": "AL 6061"}]}]}
        apath = tmp / "analysis.json"
        common.write_json(apath, analysis)
        part = {"part_id": "01", "file": str(tmp / "bar.FCStd"),
                "description": "TIE FIXTURE", "drg_no": "FX-26",
                "material": "AL 6061", "qty": 1, "front_view": "+Y",
                "scale": "1:5", "section": {"enabled": False},
                "location_dims": [{"view": "front", "orient": "h",
                                   "side": "bottom", "a": [0.0, 0.0],
                                   "b": [PITCH, 0.0]}]}
        if paper:
            part["paper"] = paper
        m = {"locked": True, "project": "FIXTURE", "drg_no_prefix": "FX",
             "rev": "00", "model_dir": str(tmp), "assembly_file": "",
             "parts": [part], "assembly_sheet": {"enabled": False},
             "templates": {"A3L": str(self.a3)},
             "_analysis_path": str(apath)}
        return m, part

    def draw_on(self, paper, tmp):
        m, part = self.manifest(tmp, paper)
        ctx, _r, lay = draw.build_part_sheet(
            m, part, {"01:front": bar_view()})
        self.assertEqual(lay["unresolved"], [])
        return ctx

    def dims_of(self, text):
        """The DIM records, as (dtype, a, b, measured length)."""
        out = []
        for line in text.splitlines():
            if not line.startswith("DIM "):
                continue
            t = [float(v) for v in line.split()[1:8]]
            out.append((int(t[0]), (t[1], t[2]), (t[3], t[4]),
                        max(abs(t[3] - t[1]), abs(t[4] - t[2]))))
        return out

    def test_the_same_length_is_written_the_same_on_both_papers(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            a4 = self.dims_of(self.draw_on(None, tmp).render())
            a3 = self.dims_of(self.draw_on("A3L", tmp).render())
        self.assertTrue(a4 and a3)
        self.assertEqual(sorted(round(d[3], 9) for d in a4),
                         sorted(round(d[3], 9) for d in a3))

    def test_a_location_dim_on_a_tie_is_snapped_too(self):
        # the overall dims are not the only ones a manifest states: a
        # pitch the designer wrote is measured the same way
        with tempfile.TemporaryDirectory() as tmp:
            dims = self.dims_of(self.draw_on(None, Path(tmp)).render())
        lengths = [d[3] for d in dims]
        self.assertTrue(any(abs(v - 46.3755010) < 1e-6 for v in lengths),
                        f"the pitch was not snapped: {lengths}")

    def test_the_location_dim_reads_the_same_on_both_papers(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            a4 = self.dims_of(self.draw_on(None, tmp).render())
            a3 = self.dims_of(self.draw_on("A3L", tmp).render())
        pick = lambda ds: sorted(round(d[3], 9) for d in ds)   # noqa: E731
        self.assertEqual(pick(a4), pick(a3))

    def test_the_length_is_the_tie_carried_away_from_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            dims = self.dims_of(self.draw_on(None, Path(tmp)).render())
        lengths = [d[3] for d in dims]
        self.assertTrue(any(abs(v - 362.62501) < 1e-6 for v in lengths),
                        f"no snapped length among {lengths}")

    @unittest.skipUnless(common.MUSA_PLOT.exists(), "musa_plot not built")
    def test_musacad_itself_prints_the_same_label_on_both(self):
        labels = {}
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            for paper in (None, "A3L"):
                ctx = self.draw_on(paper, tmp)
                f = tmp / f"{paper or 'A4'}.musa"
                f.write_text(ctx.render())
                labels[paper or "A4"] = sorted(
                    d["label"] for d in common.dump_dims(f))
        self.assertEqual(labels["A4"], labels["A3L"])
        self.assertIn("362.63", labels["A4"])


if __name__ == "__main__":
    unittest.main()
