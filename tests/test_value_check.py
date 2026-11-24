# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Pranay Kiran
"""The value check: the manifest's values must be the sheet's (issue #14).

Run with:  python3 -m unittest discover -s tests -v

The real failure: a report said "every number kept" while values had been
dropped from the sheet and a model coordinate had been printed as a
dimension. Both passed /draw, /export and the qa-checker, because nothing
compared the locked manifest with the drawing.

The fixtures are those two: a callout that loses its fit class, and a
dimension bigger than the part it dimensions.
"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import common  # noqa: E402
import draw  # noqa: E402
import musa_writer as mw  # noqa: E402
import verify  # noqa: E402

from test_tabulated import disc_front, disc_section, kit_manifest  # noqa: E402


def sheet_of(texts, dims=()):
    """A minimal .musa carrying these strings."""
    recs = [mw.mtext((0.0, float(i)), t, 3.0) for i, t in enumerate(texts)]
    return "MUSACAD 14\n" + "\n".join(recs) + "\nEND\n"


def part_with(**kw):
    """A part stating ONLY what the test is about: the title-block fields
    are claims too, and a unit test's bare sheet does not print them."""
    part = {"part_id": "08", "file": "/m/spacer.FCStd"}
    part.update(kw)
    return part


def kinds(findings):
    return sorted({f["kind"] for f in findings})


class ReadingTheSheetTest(unittest.TestCase):

    def test_it_reads_every_kind_of_text_record(self):
        musa = "\n".join([
            "MUSACAD 14",
            mw.mtext((0.0, 0.0), "A NOTE", 3.0),
            mw.text((0.0, 0.0), "A LABEL", 3.0),
            mw.leader((0.0, 0.0), (1.0, 1.0), "%%c30 f7", 3.0),
            "END"])
        self.assertEqual(verify.printed_texts(musa),
                         ["A NOTE", "A LABEL", "%%c30 f7"])

    def test_a_wrapped_value_still_reads_as_one(self):
        """place_callouts re-flows a long note to the column it gets."""
        text = sheet_of(["4 X %%c4.5 THRU,", "180%%d APART"])
        found = verify.check_sheet(
            {}, part_with(callouts=[{"view": "front",
                                     "text": "4 X %%c4.5 THRU, 180%%d APART"}],
),
            text)
        self.assertNotIn("missing", kinds(found))
        self.assertNotIn("changed", kinds(found))

    def test_a_note_drawn_bottom_up_still_reads_as_one(self):
        """notes_block draws its lines in reverse, so document order alone
        would call every wrapped note missing."""
        text = sheet_of(["2. THE SECOND NOTE.", "FIRST NOTE CONTINUED",
                         "1. THE"])
        found = verify.check_sheet(
            {}, part_with(fab_notes=["THE FIRST NOTE CONTINUED"],
),
            text)
        self.assertEqual([f for f in found if f["kind"] != "extra"], [])


class DroppedValueTest(unittest.TestCase):
    """The real failure: a callout loses its fit class."""

    def check(self, printed, stated):
        return verify.check_sheet(
            {}, part_with(callouts=[{"view": "front", "text": stated}],
),
            sheet_of([printed]))

    def test_a_dropped_fit_is_reported_and_named(self):
        found = self.check("%%c30", "%%c30 f7")
        self.assertEqual(len(found), 1, found)
        self.assertEqual(found[0]["kind"], "changed")
        self.assertIn("f7", found[0]["what"])
        self.assertEqual(found[0]["found"], "%%c30")

    def test_a_callout_that_is_not_there_at_all_is_missing(self):
        found = self.check("SOMETHING ELSE ENTIRELY", "%%c30 f7 (SPIGOT)")
        self.assertEqual([f["kind"] for f in found], ["missing"])

    def test_the_value_it_does_print_is_quoted(self):
        found = self.check("%%c30 h7", "%%c30 f7")
        self.assertEqual(found[0]["found"], "%%c30 h7")

    def test_a_sheet_that_says_what_the_manifest_says_is_silent(self):
        self.assertEqual(self.check("%%c30 f7", "%%c30 f7"), [])

    def test_a_dropped_tolerance_in_a_frame_is_reported(self):
        part = part_with(gdt={"datums": [{"label": "A", "view": "front",
                                          "target": "bore_r15"}],
                              "frames": [{"symbol": "runout", "view": "front",
                                          "tol": "0.05", "refs": ["A"],
                                          "target": "od_r15"}]})
        found = verify.check_sheet({}, part, sheet_of(["A"]))
        self.assertIn("tolerance '0.05'",
                      " ".join(f["what"] for f in found))

    def test_a_dropped_location_dim_is_reported(self):
        part = part_with(location_dims=[{"view": "front", "orient": "h",
                                         "side": "top", "a": [0.0, 0.0],
                                         "b": [12.5, 0.0]}])
        found = verify.check_sheet({}, part, sheet_of([]),
                                   dims=[{"label": "30"}])
        # with only a value to go on there is no telling a dropped dim
        # from a changed one, so it is reported as missing and named
        self.assertEqual([f["kind"] for f in found], ["missing"])
        self.assertIn("location dim 12.50", found[0]["what"])

    def test_a_location_dim_that_is_drawn_is_silent(self):
        part = part_with(location_dims=[{"view": "front", "orient": "h",
                                         "side": "top", "a": [0.0, 0.0],
                                         "b": [12.5, 0.0]}])
        self.assertEqual(verify.check_sheet({}, part, sheet_of([]),
                                            dims=[{"label": "12.5"}]), [])


class ModelCoordinateTest(unittest.TestCase):
    """The other real failure: a model coordinate printed as a dimension."""

    def test_a_dimension_bigger_than_the_part_is_impossible(self):
        found = verify.check_sheet(
            {}, part_with(), sheet_of([]), dims=[{"label": "49"}, {"label": "906.51"}],
            bbox=[49.0, 30.0, 30.0])
        self.assertEqual([f["kind"] for f in found], ["impossible"])
        self.assertIn("906.51", found[0]["what"])
        self.assertIn("model coordinate", found[0]["what"])

    def test_every_dimension_within_the_part_is_fine(self):
        found = verify.check_sheet(
            {}, part_with(), sheet_of([]), dims=[{"label": "49"}, {"label": "%%c30"}],
            bbox=[49.0, 30.0, 30.0])
        self.assertEqual(found, [])

    def test_without_a_bounding_box_it_says_nothing(self):
        found = verify.check_sheet(
            {}, part_with(), sheet_of([]), dims=[{"label": "906.51"}])
        self.assertEqual(found, [])


class ExtraValueTest(unittest.TestCase):

    def part(self):
        return part_with(callouts=[{"view": "front",
                                    "text": "%%c30 f7"}])

    def test_a_value_the_manifest_never_stated_is_reported(self):
        found = verify.check_sheet({}, self.part(),
                                   sheet_of(["%%c30 f7", "M8 x 1.25 DEEP"]))
        self.assertEqual([f["kind"] for f in found], ["extra"])
        self.assertIn("M8 x 1.25 DEEP", found[0]["what"])

    def test_the_pipeline_s_own_furniture_is_not_extra(self):
        found = verify.check_sheet(
            {}, self.part(),
            sheet_of(["%%c30 f7", "SECTION A-A", "24-11-26",
                      "SHEET 1 OF 1", "DETAIL B (2:1)", "0.12 kg", "17",
                      "A", "%%c30", "3 ITEMS", "TOTAL"]))
        self.assertEqual(found, [])

    def test_the_template_s_own_text_is_not_extra(self):
        found = verify.check_sheet(
            {}, self.part(), sheet_of(["%%c30 f7", "GENERAL TOLERANCES "
                                       "ARE AS PER IS 2102"]),
            template_texts=["GENERAL TOLERANCES ARE AS PER IS 2102"])
        self.assertEqual(found, [])

    def test_words_without_a_number_are_not_values(self):
        found = verify.check_sheet({}, self.part(),
                                   sheet_of(["%%c30 f7", "DEBURR ALL EDGES"]))
        self.assertEqual(found, [])


class WaiverTest(unittest.TestCase):

    def findings(self):
        return verify.check_sheet(
            {}, part_with(callouts=[{"view": "front", "text": "%%c30 f7"}]),
            sheet_of(["%%c30"]))

    def test_a_waiver_takes_a_difference_out_of_the_way(self):
        live, done = verify.waived(self.findings(), ["%%c30 f7"])
        self.assertEqual(live, [])
        self.assertEqual(len(done), 1)

    def test_an_unrelated_waiver_waives_nothing(self):
        live, done = verify.waived(self.findings(), ["%%c99"])
        self.assertEqual(len(live), 1)
        self.assertEqual(done, [])

    def test_no_waivers_at_all(self):
        live, done = verify.waived(self.findings(), None)
        self.assertEqual(len(live), 1)
        self.assertEqual(done, [])


class SheetMarkedDetailTest(unittest.TestCase):
    """A detail drawn on the continuation sheet marks its region "B
    (SH 2)" on the view it comes from (issue #19). That is the
    pipeline's own furniture, not a value the manifest should state."""

    def test_the_marker_is_not_an_extra_value(self):
        musa = ("MUSACAD 14\n"
                "MTEXT 10.0 10.0 0.0 4.0 0.0 1.0 1.0 3 0 6 255 0 255 0 25\n"
                "B (SH 2)\n\nEND\n")
        found = verify.check_sheet({}, {"description": "X"}, musa)
        self.assertEqual([f for f in found if f["kind"] == "extra"], [])


class ExportGateTest(unittest.TestCase):
    """/draw reports the differences; /export refuses to run on them."""

    def run_export(self, values, waived=()):
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
            sheet = {"file": str(musa / "A.musa"), "drg_no": "A",
                     "description": "A", "scale": "1:1",
                     "window": [0, 0, 10, 10], "kind": "part",
                     "values": values, "values_waived": list(waived)}
            Path(sheet["file"]).write_text("MUSACAD 14\nEND\n")
            common.write_json(musa / "sheets.json", {"sheets": [sheet]})
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
        self.assertEqual(self.run_export([]), ["A.musa"])

    def test_a_difference_stops_the_export(self):
        with self.assertRaises(SystemExit):
            self.run_export([{"kind": "changed",
                              "what": "callout '%%c30 f7'",
                              "found": "%%c30"}])

    def test_a_waived_difference_exports(self):
        self.assertEqual(
            self.run_export([], waived=[{"kind": "changed",
                                         "what": "callout '%%c30 f7'",
                                         "found": "%%c30"}]),
            ["A.musa"])


class WholeSheetTest(unittest.TestCase):
    """A real composed sheet: the check must be silent on a sheet whose
    manifest and ink agree."""

    def test_a_tabulated_sheet_passes_its_own_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            m, part = kit_manifest(Path(tmp))
            proj = {"13:front": disc_front(), "13:section": disc_section()}
            ctx, _, lay = draw.build_part_sheet(m, part, proj)
            self.assertEqual(lay["unresolved"], [])
            tpl = [r.content.replace("\\n", "\n")
                   for r in mw.parse_template() if r.kind == "MTEXT"]
            found = verify.check_sheet(m, part, ctx.render(), [],
                                       [5.0, 40.0, 40.0], tpl)
            self.assertEqual(verify.report(found), [])

    def test_the_variants_table_values_are_checked_too(self):
        """A dropped variant is a disc the kit no longer orders."""
        with tempfile.TemporaryDirectory() as tmp:
            m, part = kit_manifest(Path(tmp))
            proj = {"13:front": disc_front(), "13:section": disc_section()}
            ctx, _, _ = draw.build_part_sheet(m, part, proj)
            claimed = dict(part)
            claimed["variants"] = dict(part["variants"])
            claimed["variants"]["rows"] = \
                part["variants"]["rows"] + [["E", "0.25", "4"]]
            found = verify.check_sheet(m, claimed, ctx.render(), [],
                                       [5.0, 40.0, 40.0])
            self.assertIn("0.25", " ".join(f["what"] for f in found))


if __name__ == "__main__":
    unittest.main()
