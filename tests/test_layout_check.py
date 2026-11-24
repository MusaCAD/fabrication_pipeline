# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Pranay Kiran
"""The layout check (issue #22).

Run with:  python3 -m unittest discover -s tests -v

The value check reads what a sheet SAYS; this one reads how it LOOKS, off
the finished .musa. Every fixture here is a real failure from one project's
sheets: leaders crossing each other, a leader running along the outline it
points at, a datum triangle sitting on the diameter sign of its own
callout. All of them passed every check the pipeline had and were found by
eye.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import layout_check as lc  # noqa: E402
import musa_writer as mw  # noqa: E402

AREA = (0.0, 0.0, 200.0, 200.0)
P = mw.P
A = mw.P_ANNO


def sheet(*records) -> str:
    return "MUSACAD 14\n" + "\n".join(records) + "\nEND\n"


def kinds(found):
    return sorted(f["kind"] for f in found)


class ParseTest(unittest.TestCase):
    """The check reads the sheet back the way musa_writer writes it."""

    def test_it_reads_every_record_kind(self):
        text = sheet(mw.line((0, 0), (10, 0)),
                     mw.circle((5, 5), 2.0, A),
                     mw.polyline([(0, 0), (1, 0), (1, 1)], True, A),
                     mw.arc((3, 3), 1.0, 0.0, 1.5),
                     mw.hatch([[(0, 0), (1, 0), (0, 1)]], "SOLID", props=A),
                     mw.mtext((2, 2), "HELLO", 3.0, props=A),
                     mw.text((4, 4), "X", 3.0, props=A),
                     mw.leader((1, 1), (9, 9), "NOTE", 3.0, props=A))
        got = [r.kind for r in lc.parse(text)]
        self.assertEqual(got, ["LINE", "CIRCLE", "POLYLINE", "ARC", "HATCH",
                               "MTEXT", "TEXT", "LEADER"])
        recs = {r.kind: r for r in lc.parse(text)}
        self.assertEqual(recs["MTEXT"].content, "HELLO")
        self.assertEqual(recs["LEADER"].content, "NOTE")
        self.assertTrue(recs["CIRCLE"].anno)
        self.assertFalse(recs["LINE"].anno)

    def test_a_hatch_keeps_its_loops(self):
        text = sheet(mw.hatch([[(0, 0), (4, 0), (0, 4)]], "SOLID", props=A))
        r = lc.parse(text)[0]
        self.assertEqual(r.nums, [[(0.0, 0.0), (4.0, 0.0), (0.0, 4.0)]])
        self.assertEqual(r.content, "SOLID")


class LeaderCrossingTest(unittest.TestCase):
    """Two leaders that cross: each appears to point at the other's
    feature. The real failure is sheet 10's ⌀6.4 and its slot note."""

    def test_crossing_leaders_are_reported(self):
        text = sheet(mw.leader((20, 20), (120, 80), "", 3.0, props=A),
                     mw.leader((20, 80), (120, 20), "", 3.0, props=A))
        found = lc.check_sheet(text, AREA)
        self.assertEqual(kinds(found), ["leader-crossing"])

    def test_leaders_that_only_fan_out_are_not(self):
        text = sheet(mw.leader((20, 80), (120, 100), "", 3.0, props=A),
                     mw.leader((20, 20), (120, 40), "", 3.0, props=A))
        self.assertEqual(lc.check_sheet(text, AREA), [])

    def test_a_touch_at_the_ends_is_not_a_crossing(self):
        # two balloons landing on the same edge of the same part
        text = sheet(mw.leader((50, 50), (120, 60), "", 3.0, props=A),
                     mw.leader((50, 50), (120, 40), "", 3.0, props=A))
        self.assertEqual(lc.check_sheet(text, AREA), [])

    def test_the_scale_does_not_change_the_rule(self):
        # the same sheet at 1:5: every tolerance is in PRINTED mm
        text = sheet(mw.leader((100, 100), (600, 400), "", 15.0, props=A),
                     mw.leader((100, 400), (600, 100), "", 15.0, props=A))
        found = lc.check_sheet(text, (0.0, 0.0, 1000.0, 1000.0), 1 / 5)
        self.assertEqual(kinds(found), ["leader-crossing"])


class LeaderOnOutlineTest(unittest.TestCase):
    """A leader lying on the outline it points at cannot be told from the
    part (ISO 128-22). The real failure is sheet 07's ⌀46.8, which ran
    76.8 mm along the silhouette of a turned part."""

    def test_a_leader_along_an_outline_is_reported(self):
        text = sheet(mw.line((10, 50), (90, 50)),
                     mw.leader((20, 50), (120, 50), "%%c46.8", 3.0, props=A))
        found = lc.check_sheet(text, AREA)
        self.assertEqual(kinds(found), ["leader-on-outline"])
        self.assertIn("46.8", found[0]["what"])

    def test_a_leader_that_leaves_the_edge_at_once_is_not(self):
        text = sheet(mw.line((10, 50), (90, 50)),
                     mw.leader((20, 50), (120, 90), "%%c46.8", 3.0, props=A))
        self.assertEqual(lc.check_sheet(text, AREA), [])

    def test_crossing_an_outline_is_ordinary_drafting(self):
        text = sheet(mw.line((50, 10), (50, 90)),
                     mw.leader((20, 50), (120, 60), "", 3.0, props=A))
        self.assertEqual(lc.check_sheet(text, AREA), [])

    def test_a_graze_of_a_millimetre_is_not_a_fault(self):
        text = sheet(mw.line((20, 50), (22, 50)),
                     mw.leader((20, 50), (120, 50.3), "", 3.0, props=A))
        self.assertEqual(lc.check_sheet(text, AREA), [])

    def test_the_annotation_s_own_ink_is_not_an_outline(self):
        # a leader may run beside another annotation's line; only DRAWN
        # geometry counts as an outline
        text = sheet(mw.line((10, 50), (90, 50), A),
                     mw.leader((20, 50), (120, 50), "", 3.0, props=A))
        self.assertEqual(lc.check_sheet(text, AREA), [])


class InkOnTextTest(unittest.TestCase):
    """The real failure: the datum triangle's tip sat on the knee, where
    the callout's own label starts, and blacked out the ⌀ of "⌀71.5 f7"."""

    def test_a_filled_triangle_over_a_label_is_reported(self):
        h = 3.2
        ink = mw.leader_text_ink((50, 50), "%%c71.5 f7", h, 2.5)
        tri = [(ink[0] + 0.5, 50.0), (ink[0] + 3.0, 48.0),
               (ink[0] + 3.0, 52.0)]
        text = sheet(mw.leader((20, 40), (50, 50), "%%c71.5 f7", h, props=A),
                     mw.hatch([tri], "SOLID", props=A))
        found = lc.check_sheet(text, AREA)
        self.assertIn("ink-on-text", kinds(found))

    def test_a_triangle_set_back_along_the_leader_is_not(self):
        h = 3.2
        tri = [(44.0, 50.0), (47.0, 48.5), (47.0, 51.5)]
        text = sheet(mw.leader((20, 40), (50, 50), "%%c71.5 f7", h, props=A),
                     mw.hatch([tri], "SOLID", props=A))
        self.assertEqual(lc.check_sheet(text, AREA), [])

    def test_a_balloon_keeps_its_own_number(self):
        # the number sits INSIDE the circle by design: it is the stroke
        # that must not cross text, not the disc
        text = sheet(mw.circle((50, 50), 4.9, A),
                     mw.cell_text((50, 50), "12", 3.5, A))
        self.assertEqual(lc.check_sheet(text, AREA), [])

    def test_a_balloon_over_a_neighbour_s_text_is_reported(self):
        text = sheet(mw.circle((50, 50), 4.9, A),
                     mw.cell_text((50, 50), "12", 3.5, A),
                     mw.mtext((46, 46), "SECTION A-A", 3.0, props=A))
        self.assertIn("ink-on-text", kinds(lc.check_sheet(text, AREA)))

    def test_a_frame_keeps_its_own_cells(self):
        recs, _w, _h = mw.fcf((40.0, 40.0), "position", "%%c0.1", ["A"],
                              3.2, props=A)
        self.assertEqual(lc.check_sheet(sheet(*recs), AREA), [])


class TextOffSheetTest(unittest.TestCase):
    def test_text_outside_the_drawing_area_is_reported(self):
        text = sheet(mw.mtext((190, 100), "THICKNESS T: SEE NOTE 1", 3.0,
                              props=A))
        self.assertEqual(kinds(lc.check_sheet(text, AREA)),
                         ["text-off-sheet"])

    def test_the_title_block_s_own_text_is_not_the_drawing_s(self):
        # white text below the drawing area is the template's
        text = sheet(mw.mtext((10, -20), "DRAWN", 3.0))
        self.assertEqual(lc.check_sheet(text, AREA), [])


class ParallelLeadersTest(unittest.TestCase):
    def test_two_leaders_running_together_are_a_warning_only(self):
        text = sheet(mw.leader((20, 50), (120, 50), "", 3.0, props=A),
                     mw.leader((20, 51), (120, 51), "", 3.0, props=A))
        found = lc.check_sheet(text, AREA)
        self.assertEqual(kinds(found), ["leaders-parallel"])
        self.assertEqual(lc.faults(found), [])
        self.assertEqual(len(lc.warnings(found)), 1)

    def test_leaders_a_centimetre_apart_are_not(self):
        text = sheet(mw.leader((20, 50), (120, 50), "", 3.0, props=A),
                     mw.leader((20, 60), (120, 60), "", 3.0, props=A))
        self.assertEqual(lc.check_sheet(text, AREA), [])


class TableBandTest(unittest.TestCase):
    """The parts table is the template's furniture, not the drawing's."""

    def test_its_rules_are_not_outlines_and_its_cells_not_text(self):
        band = (0.0, 0.0, 200.0, 20.0)
        text = sheet(mw.line((0, 10), (200, 10)),
                     mw.leader((20, 10), (120, 10), "", 3.0, props=A))
        self.assertEqual(kinds(lc.check_sheet(text, AREA)),
                         ["leader-on-outline"])
        self.assertEqual(lc.check_sheet(text, AREA, table_band=band), [])


class ExportGateTest(unittest.TestCase):
    """/draw reports the faults; /export refuses to run on them — the
    warning alone goes through."""

    def run_export(self, faults=(), waived=(), warnings_=()):
        import tempfile

        import common
        import export
        plotted = []

        def fake_plot(musa, pdf, *, scale, window, stamp=None, stamps=None,
                      paper="A4", orientation="portrait"):
            plotted.append(Path(musa).name)
            Path(pdf).write_bytes(b"%PDF-1.4\n%%EOF\n")
            return {"mm_per_unit": str(scale[0] / scale[1]), "paper": paper,
                    "raw": f"[musa_plot] ok paper={paper} {orientation}"}

        def fake_run(cmd, **kw):
            class R:
                returncode = 0
                stderr = ""
            return R()

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            musa, pdfs = tmp / "musa", tmp / "pdf"
            musa.mkdir()
            s = {"file": str(musa / "A.musa"), "drg_no": "A",
                 "description": "A", "scale": "1:1",
                 "window": [0, 0, 10, 10], "kind": "part",
                 "layout_faults": list(faults),
                 "layout_waived": list(waived),
                 "layout_warnings": list(warnings_)}
            Path(s["file"]).write_text("MUSACAD 14\nEND\n")
            common.write_json(musa / "sheets.json", {"sheets": [s]})
            saved = (export.OUTPUT_MUSA_DIR, export.OUTPUT_PDF_DIR,
                     export.run_musa_plot, export.subprocess.run)
            export.OUTPUT_MUSA_DIR, export.OUTPUT_PDF_DIR = musa, pdfs
            export.run_musa_plot = fake_plot
            export.subprocess.run = fake_run
            try:
                export.main()
            finally:
                (export.OUTPUT_MUSA_DIR, export.OUTPUT_PDF_DIR,
                 export.run_musa_plot, export.subprocess.run) = saved
        return plotted

    def test_a_clean_sheet_exports(self):
        self.assertEqual(self.run_export(), ["A.musa"])

    def test_a_fault_stops_the_export(self):
        with self.assertRaises(SystemExit):
            self.run_export([{"kind": "leader-crossing",
                              "what": "the leaders of 'a' and 'b' cross"}])

    def test_a_waived_fault_exports(self):
        self.assertEqual(
            self.run_export(waived=[{"kind": "leader-crossing",
                                     "what": "the leaders cross"}]),
            ["A.musa"])

    def test_a_warning_alone_exports(self):
        self.assertEqual(
            self.run_export(warnings_=[{"kind": "leaders-parallel",
                                        "what": "they run together",
                                        "severity": "warn"}]),
            ["A.musa"])


if __name__ == "__main__":
    unittest.main()
