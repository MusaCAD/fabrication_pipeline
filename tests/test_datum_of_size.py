# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Pranay Kiran
"""A datum on a FEATURE OF SIZE — a bore or a spigot (issue #2).

Run with:  python3 -m unittest discover -s tests -v

The datum of a bore is its AXIS, so the datum triangle belongs on that
bore's own size dimension: on the leader of its callout (ISO 1101 / ASME
Y14.5), or in line with its diameter dimension line when it is dimensioned
that way. A triangle parked on a view edge would call out a surface, which
is a different datum altogether.
"""

import math
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "samples"))

import common  # noqa: E402
import draw  # noqa: E402
import manifest as mf  # noqa: E402
import musa_writer as mw  # noqa: E402
import sample_sheet  # noqa: E402


def solid_triangles(text: str) -> list:
    """Every filled triangle on a sheet, as (p0, p1, p2)."""
    out, lines = [], text.splitlines()
    for i, line in enumerate(lines):
        if not line.startswith("HATCH ") or i + 1 >= len(lines):
            continue
        if lines[i + 1].strip() != "SOLID":
            continue
        t = line.split()
        if t[1] != "1" or t[2] != "3":
            continue
        v = [float(x) for x in t[3:9]]
        out.append([(v[0], v[1]), (v[2], v[3]), (v[4], v[5])])
    return out


def leaders(text: str) -> list:
    """Every LEADER as {tip, knee, text}."""
    out, lines = [], text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("LEADER "):
            t = line.split()
            out.append({"tip": (float(t[1]), float(t[2])),
                        "knee": (float(t[3]), float(t[4])),
                        "text": lines[i + 1] if i + 1 < len(lines) else ""})
    return out


class TargetTest(unittest.TestCase):
    def test_radius_targets(self):
        self.assertEqual(draw._target_radius("bore_r12.5"), 12.5)
        self.assertEqual(draw._target_radius("boss_r20"), 20.0)
        self.assertIsNone(draw._target_radius("bottom_face"))
        self.assertIsNone(draw._target_radius("long_edge"))

    def test_a_radius_is_true_mm_in_every_view(self):
        # the detail view's geometry is k times size, so the same feature
        # is filed there under k times the radius
        self.assertEqual(draw._target_key("front", 12.5), ("front", 12.5, 1.0))
        self.assertEqual(draw._target_key("detail", 12.5, {"detail": 0.5}),
                         ("detail", 25.0, 0.5))

    def test_manifest_validation(self):
        self.assertIsNone(mf.datum_target_problem("bottom_face"))
        self.assertIsNone(mf.datum_target_problem("bore_r12.5"))
        self.assertIn("bad datum target",
                      mf.datum_target_problem("bore_rX") or "")
        self.assertIn("bad datum target",
                      mf.datum_target_problem("middle_bit") or "")
        self.assertIn("bad datum target",
                      mf.datum_target_problem("bore_r0") or "")


class AttachmentTest(unittest.TestCase):
    """Where the triangle lands, on an empty sheet."""

    def setUp(self):
        self.ctx = mw.SheetContext(1, 1)
        self.ctx.dimstyles = draw.sheet_dimstyles(self.ctx)
        self.layout = draw.Layout(self.ctx)

    def test_on_a_callout_the_triangle_sits_on_the_leader(self):
        knee = (120.0, 200.0)
        recs, rect = draw.datum_on_landing(self.ctx, self.layout, "A",
                                           (knee, (124.0, 200.0)))
        self.assertIsNotNone(rect)
        tris = solid_triangles("\n".join(recs))
        self.assertEqual(len(tris), 1)
        self.assertAlmostEqual(tris[0][0][0], knee[0], places=6)
        self.assertAlmostEqual(tris[0][0][1], knee[1], places=6)
        # and the box hangs off it, not on top of it
        self.assertFalse(rect[1] <= knee[1] <= rect[3]
                         and rect[0] <= knee[0] <= rect[2])
        self.assertLess(abs((rect[0] + rect[2]) / 2 - knee[0]),
                        self.ctx.mm(14.0))

    def test_it_keeps_clear_of_a_foreign_label(self):
        # a label right under the shoulder: the box must not park beside it
        # and be read as that label's datum
        knee = (120.0, 200.0)
        foreign = (100.0, 190.0, 121.0, 198.0)
        self.layout.claim(foreign, "callout-%%c60")
        recs, rect = draw.datum_on_landing(self.ctx, self.layout, "A",
                                           (knee, (124.0, 200.0)))
        self.assertIsNotNone(rect)
        dx = max(foreign[0] - rect[2], 0.0, rect[0] - foreign[2])
        dy = max(foreign[1] - rect[3], 0.0, rect[1] - foreign[3])
        self.assertGreaterEqual(math.hypot(dx, dy), self.ctx.mm(4.0))

    def test_on_a_diameter_dim_the_triangle_is_in_line_with_it(self):
        # no callout: the dim line runs from the centre out to its label,
        # so the triangle sits where that line leaves the feature
        circle = (100.0, 200.0, 12.5)
        size_rect = (120.0, 210.0, 140.0, 214.0)
        recs, rect = draw.datum_on_feature(self.ctx, self.layout, "A",
                                           circle, size_rect)
        self.assertIsNotNone(rect)
        tip = solid_triangles("\n".join(recs))[0][0]
        self.assertAlmostEqual(math.dist(tip, (100.0, 200.0)), 12.5, places=6)
        # tip, feature centre and the size text are collinear
        mid = ((size_rect[0] + size_rect[2]) / 2,
               (size_rect[1] + size_rect[3]) / 2)
        a = math.atan2(tip[1] - 200.0, tip[0] - 100.0)
        b = math.atan2(mid[1] - 200.0, mid[0] - 100.0)
        self.assertAlmostEqual(a, b, places=6)


class PlaceDatumsTest(unittest.TestCase):
    """The dispatch in place_datums, with a view but no other ink."""

    def setUp(self):
        self.ctx = mw.SheetContext(1, 1)
        self.ctx.dimstyles = draw.sheet_dimstyles(self.ctx)
        self.layout = draw.Layout(self.ctx)
        self.job = {"visible": [{"t": "circle", "c": [0.0, 0.0], "r": 12.5},
                                {"t": "circle", "c": [0.0, 0.0], "r": 30.0}],
                    "hidden": [], "bounds": [[-30.0, -30.0], [30.0, 30.0]]}
        self.offs = {"front": (90.0, 200.0)}
        self.views = {"front": self.job}

    def datums(self, target):
        return {"datums": [{"label": "A", "view": "front",
                            "target": target}], "frames": []}

    def test_a_feature_of_size_without_a_size_dimension_fails_loudly(self):
        recs, pos = draw.place_datums(self.ctx, self.layout,
                                      self.datums("bore_r12.5"),
                                      self.views, self.offs)
        self.assertEqual(recs, [])
        self.assertEqual(pos, {})
        # and the failure says what is missing and how to fix it
        msg = " ".join(self.layout.unresolved)
        self.assertIn("no size dimension for r12.5", msg)
        self.assertIn("view 'front'", msg)
        self.assertIn("callout", msg)

    def test_it_attaches_to_the_callout_when_there_is_one(self):
        lands = {("front", 12.5): ((130.0, 205.0), (134.0, 205.0))}
        rects = {("front", 12.5): (134.0, 203.0, 170.0, 207.0)}
        recs, pos = draw.place_datums(self.ctx, self.layout,
                                      self.datums("bore_r12.5"),
                                      self.views, self.offs,
                                      callout_rects=rects,
                                      callout_lands=lands)
        self.assertEqual(self.layout.unresolved, [])
        self.assertIn("A", pos)
        tip = solid_triangles("\n".join(recs))[0][0]
        self.assertAlmostEqual(tip[0], 130.0)
        self.assertAlmostEqual(tip[1], 205.0)

    def test_it_falls_back_to_the_diameter_dim(self):
        rects = {("front", 12.5): (112.0, 212.0, 126.0, 216.0)}
        recs, pos = draw.place_datums(self.ctx, self.layout,
                                      self.datums("bore_r12.5"),
                                      self.views, self.offs, dim_rects=rects)
        self.assertEqual(self.layout.unresolved, [])
        tip = solid_triangles("\n".join(recs))[0][0]
        self.assertAlmostEqual(math.dist(tip, (90.0, 200.0)), 12.5, places=6)

    def test_view_edge_datums_still_work(self):
        recs, pos = draw.place_datums(self.ctx, self.layout,
                                      self.datums("bottom_face"),
                                      self.views, self.offs)
        self.assertEqual(self.layout.unresolved, [])
        self.assertIn("A", pos)


@unittest.skipUnless(common.MUSA_PLOT.exists(), "musa_plot not built")
class SampleSheetDatumTest(unittest.TestCase):
    """On the sample sheet: datum A is the bore's axis."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="fabpipe-datum-")
        cls.info = sample_sheet.build(Path(cls.tmp.name))
        cls.text = Path(cls.info["musa"]).read_text()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_the_triangle_sits_on_the_bore_callout_s_leader(self):
        bore = [l for l in leaders(self.text) if l["text"].startswith("%%c25")]
        self.assertEqual(len(bore), 1)
        knee = bore[0]["knee"]
        tips = [t[0] for t in solid_triangles(self.text)]
        # on the leader's shoulder, or further along the same landing — ISO
        # 1101 5.3 also allows the triangle on an EXTENSION of the
        # dimension line, which is where it goes when the slots beside the
        # shoulder are taken by the callout stacked under it (issue #7)
        self.assertTrue(any(abs(t[1] - knee[1]) < 1e-6 and t[0] >= knee[0]
                            for t in tips),
                        f"no datum triangle on the landing from {knee}")

    def test_the_frames_refer_to_it(self):
        self.assertEqual(self.info["layout"]["unresolved"], [])
        # the letter is drawn in its box and in both frames: 3 cells of "A"
        self.assertGreaterEqual(self.text.count("\nA\n"), 3)


if __name__ == "__main__":
    unittest.main()
