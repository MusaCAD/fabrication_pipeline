# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Pranay Kiran
"""Thin features are judged at the scale the SHEET is drawn at (issue #4).

Run with:  python3 -m unittest discover -s tests -v

Legibility is a property of the printed sheet. /judge used to measure it
at the scale it suggested itself, so a 0.5 mm disc that /finalise puts on
a 2:1 sheet — printing at a legible 1.0 mm — still had to have its warning
waived at the lock.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import judge  # noqa: E402
import manifest as mf  # noqa: E402

THIN = 0.5          # mm: the part's thinnest extent


def part(file="/m/disc.FCStd", thin=THIN):
    """An analysis entry for a thin disc that is otherwise healthy."""
    return {"file": file, "document": "disc", "issues": [],
            "sketches": [{"label": "Sketch", "constraints": 4,
                          "dimensional": [{"name": "d", "type": "Diameter",
                                           "value": 40.0}],
                          "solver_ok": True, "fully_constrained": True}],
            "bodies": [{"label": "Body", "object": "Body",
                        "type": "PartDesign::Body", "solids": 1, "faces": 3,
                        "volume_mm3": 628.0, "area_mm2": 0.0,
                        "bbox_min": [0, 0, 0], "bbox_max": [40, 40, thin],
                        "bbox_size": [40.0, 40.0, thin],
                        "material": "IS 2062 E250"}]}


def warnings(verdict):
    return [f["message"] for f in verdict["findings"]
            if f["severity"] == "WARN"]


class FindingTest(unittest.TestCase):
    def test_a_thin_part_at_1_to_1(self):
        msg = judge.thin_feature_finding([40.0, 40.0, THIN], (1, 1))
        self.assertIn("thinnest extent 0.5 mm", msg)
        self.assertIn("prints at 0.50 mm at 1:1", msg)

    def test_the_same_part_at_2_to_1_is_fine(self):
        # 0.5 mm at 2:1 prints at exactly the 1.0 mm legibility floor
        self.assertIsNone(
            judge.thin_feature_finding([40.0, 40.0, THIN], (2, 1)))

    def test_a_reduced_scale_makes_it_worse(self):
        msg = judge.thin_feature_finding([40.0, 40.0, 2.0], (1, 5))
        self.assertIn("prints at 0.40 mm at 1:5", msg)

    def test_the_message_says_which_scale_it_used(self):
        self.assertIn("(sheet scale)", judge.thin_feature_finding(
            [40.0, 40.0, THIN], (1, 1), "sheet scale"))
        self.assertIn("(suggested scale)", judge.thin_feature_finding(
            [40.0, 40.0, THIN], (1, 1), "suggested scale"))

    def test_scale_parsing(self):
        self.assertEqual(judge.parse_scale("2:1"), (2, 1))
        self.assertEqual(judge.parse_scale("1:10"), (1, 10))
        for bad in (None, "", "2", "2:0", "x:1", "2:1:1"):
            self.assertIsNone(judge.parse_scale(bad), bad)


class JudgePartTest(unittest.TestCase):
    def test_with_no_manifest_it_warns_at_the_suggested_scale(self):
        v = judge.judge_part(part())
        self.assertEqual(v["verdict"], "WARN")
        self.assertEqual(v["scale_used"], v["suggested_scale"])
        self.assertTrue(any("suggested scale" in w for w in warnings(v)), v)

    def test_with_a_2_to_1_sheet_there_is_nothing_to_warn_about(self):
        v = judge.judge_part(part(), sheet_scale=(2, 1))
        self.assertEqual(warnings(v), [])
        self.assertEqual(v["verdict"], "PASS")
        self.assertEqual(v["scale_used"], "2:1")
        # the suggestion is still reported, it just does not drive the check
        self.assertEqual(v["suggested_scale"], "1:1")

    def test_a_sheet_scale_that_is_still_too_small_warns(self):
        v = judge.judge_part(part(), sheet_scale=(1, 1))
        self.assertTrue(any("sheet scale" in w for w in warnings(v)), v)

    def test_other_findings_are_untouched(self):
        p = part()
        p["sketches"][0]["dimensional"] = []
        v = judge.judge_part(p, sheet_scale=(2, 1))
        self.assertEqual(v["verdict"], "FAIL")


class ManifestScalesTest(unittest.TestCase):
    def test_it_reads_a_draft_or_a_locked_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "manifest.json"
            f.write_text(json.dumps({"parts": [
                {"file": "/m/a.FCStd", "scale": "2:1"},
                {"file": "/m/b.FCStd", "scale": "1:5"},
                {"file": "/m/bought.FCStd", "scale": "1:1", "sheet": False},
                {"file": "/m/bad.FCStd", "scale": "nonsense"}]}))
            self.assertEqual(judge.manifest_scales(f),
                             {"/m/a.FCStd": (2, 1), "/m/b.FCStd": (1, 5)})

    def test_no_manifest_no_scales(self):
        self.assertEqual(judge.manifest_scales(Path("/nope/manifest.json")),
                         {})


class LockTest(unittest.TestCase):
    """The lock is where the scale choice becomes final, so it checks again."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / "reports").mkdir()
        (self.dir / "reports" / "analysis.json").write_text(json.dumps(
            {"model_dir": "/m", "parts": [part()],
             "assembly": {"file": "", "instances": [], "joints": [],
                          "issues": []}}))
        self.saved = mf.REPORTS_DIR
        mf.REPORTS_DIR = self.dir / "reports"

    def tearDown(self):
        mf.REPORTS_DIR = self.saved
        self.tmp.cleanup()

    def manifest(self, scale, waivers=()):
        return {"parts": [{"part_id": "01", "file": "/m/disc.FCStd",
                           "description": "DISC", "drg_no": "X-01",
                           "material": "IS 2062 E250", "qty": 1,
                           "front_view": "+Z", "scale": scale,
                           "section": {"enabled": True},
                           "waivers": list(waivers)}]}

    def test_a_scale_that_is_too_small_blocks_the_lock(self):
        got = mf.thin_feature_problems(self.manifest("1:1"))
        self.assertEqual(len(got), 1)
        self.assertIn("thinnest extent", got[0])
        self.assertIn("raise the scale or record a waiver", got[0])

    def test_a_big_enough_scale_needs_no_waiver(self):
        self.assertEqual(mf.thin_feature_problems(self.manifest("2:1")), [])

    def test_a_waiver_lets_it_through(self):
        self.assertEqual(mf.thin_feature_problems(
            self.manifest("1:1", ["thinnest extent 0.5 mm prints at 0.50 mm "
                                  "at 1:1 — check legibility"])), [])

    def test_a_bought_out_part_is_not_drawn_so_not_checked(self):
        m = self.manifest("1:1")
        m["parts"][0]["sheet"] = False
        self.assertEqual(mf.thin_feature_problems(m), [])

    def test_without_an_analysis_the_check_is_skipped(self):
        mf.REPORTS_DIR = self.dir / "nothing"
        self.assertEqual(mf.thin_feature_problems(self.manifest("1:1")), [])


if __name__ == "__main__":
    unittest.main()
