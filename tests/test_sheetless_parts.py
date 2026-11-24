# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Pranay Kiran
"""A part drawn on ANOTHER part's sheet (issue #8).

Run with:  python3 -m unittest discover -s tests -v

A kit of trim discs differs only in thickness: one sheet is drawn at 2:1
and the thinner discs carry that sheet's DRG NO. with `sheet: false`. They
are drawn — on their sibling's sheet, at its scale — so that is the scale
their thin features are judged at. /judge used its own suggested 1:1 and
raised a warning no manifest scale could clear, while the lock skipped
those parts altogether; the two now resolve the scale the same way.

A bought-out item is the other case: `sheet: false` and no sheet anywhere
carries its number, so nothing is drawn for it and there is nothing to
judge for legibility.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import judge  # noqa: E402

DISC_5 = "/m/trim_disc40_5p0.FCStd"
DISC_05 = "/m/trim_disc40_0p5.FCStd"
NUT = "/m/nut_m6.FCStd"


def parts():
    return [
        # the kit's own sheet, drawn at 2:1
        {"part_id": "13", "file": DISC_5, "drg_no": "WQ-TF07A-13",
         "scale": "2:1", "sheet": True},
        # a thinner disc of the same kit: no sheet, same DRG NO.
        {"part_id": "25", "file": DISC_05, "drg_no": "WQ-TF07A-13",
         "scale": "1:1", "sheet": False},
        # bought out: no sheet and no sibling carrying its number
        {"part_id": "18", "file": NUT, "drg_no": "ISO 4032",
         "scale": "1:1", "sheet": False},
    ]


def body(thin_mm):
    return {"label": "Body", "object": "Body", "type": "PartDesign::Body",
            "solids": 1, "faces": 6, "volume_mm3": 600.0, "area_mm2": 0.0,
            "bbox_min": [0, 0, 0], "bbox_max": [40, 40, thin_mm],
            "bbox_size": [40.0, 40.0, thin_mm], "material": "POM-C"}


def analysed(file, thin_mm):
    return {"file": file, "document": Path(file).stem, "sketches": [],
            "issues": [], "bodies": [body(thin_mm)]}


class SheetScaleTest(unittest.TestCase):

    def test_a_part_takes_the_scale_of_the_sheet_it_is_drawn_on(self):
        scales = judge.sheet_scales(parts())
        self.assertEqual(scales[DISC_5], (2, 1))
        self.assertEqual(scales[DISC_05], (2, 1),
                         "the kit disc is drawn on sheet 13, at 2:1")

    def test_a_part_no_sheet_draws_has_no_scale(self):
        self.assertNotIn(NUT, judge.sheet_scales(parts()))
        self.assertEqual(judge.not_drawn(parts()), {NUT})

    def test_the_disc_is_judged_at_the_kit_sheet_s_scale(self):
        scales = judge.sheet_scales(parts())
        v = judge.judge_part(analysed(DISC_05, 0.5), "", scales.get(DISC_05))
        self.assertEqual(v["scale_used"], "2:1")
        # 0.5 mm at 2:1 prints 1.0 mm: legible, nothing to waive
        self.assertEqual([f for f in v["findings"]
                          if "thinnest extent" in f["message"]], [])

    def test_at_its_own_1_1_it_would_have_warned(self):
        v = judge.judge_part(analysed(DISC_05, 0.5), "", (1, 1))
        self.assertTrue([f for f in v["findings"]
                         if "thinnest extent" in f["message"]],
                        "0.5 mm at 1:1 is the warning this issue is about")

    def test_an_undrawn_part_is_not_judged_for_legibility(self):
        v = judge.judge_part(analysed(NUT, 0.4), "", None, drawn=False)
        self.assertIsNone(v["scale_used"])
        self.assertEqual([f for f in v["findings"]
                          if "thinnest extent" in f["message"]], [])

    def test_it_still_warns_for_a_part_with_no_manifest_at_all(self):
        # no manifest yet: the suggestion is all there is, as before
        v = judge.judge_part(analysed(DISC_05, 0.5), "", None)
        self.assertTrue([f for f in v["findings"]
                         if "thinnest extent" in f["message"]])


class LockAgreesTest(unittest.TestCase):
    """manifest.py's lock resolves the scale the same way, so a part cannot
    warn at /judge and be skipped at the lock."""

    def setUp(self):
        import manifest as mf
        self.mf = mf

    def test_the_lock_checks_the_disc_at_the_kit_sheet_s_scale(self):
        import tempfile
        import common
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            common.write_json(tmp / "analysis.json", {
                "parts": [analysed(DISC_5, 5.0), analysed(DISC_05, 0.5),
                          analysed(NUT, 0.4)]})
            old = self.mf.REPORTS_DIR
            self.mf.REPORTS_DIR = tmp
            try:
                out = self.mf.thin_feature_problems({"parts": parts()})
            finally:
                self.mf.REPORTS_DIR = old
        # 0.5 at 2:1 prints 1.0 mm and the nut is drawn nowhere: no problem
        self.assertEqual(out, [])

    def test_the_lock_still_objects_when_the_host_sheet_is_too_small(self):
        import tempfile
        import common
        small = [dict(p, scale="1:2") if p["part_id"] == "13" else p
                 for p in parts()]
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            common.write_json(tmp / "analysis.json", {
                "parts": [analysed(DISC_05, 0.5)]})
            old = self.mf.REPORTS_DIR
            self.mf.REPORTS_DIR = tmp
            try:
                out = self.mf.thin_feature_problems({"parts": small})
            finally:
                self.mf.REPORTS_DIR = old
        self.assertEqual(len(out), 1, out)
        self.assertIn("1:2", out[0])


if __name__ == "__main__":
    unittest.main()
