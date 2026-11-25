# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Pranay Kiran
"""One PDF per sheet (issue #24).

Run with:  python3 -m unittest discover -s tests -v

The real failure: an assembly drawing that runs to two sheets — a
continuation sheet for the parts table, or details on `"sheet": 2` — has
two entries in sheets.json under ONE drawing number. /export named each
PDF after the drawing, so sheet 2 was plotted over sheet 1; the print set
was built from that list of paths, so it carried SHEET 2 OF 2 twice and
SHEET 1 OF 2 not at all. The page count was still right, and every check
before the plot passed, because each sheet's .musa was correct — only the
PDF written for it was lost.
"""

import hashlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import common  # noqa: E402
import export  # noqa: E402
import musa_writer as mw  # noqa: E402


def sheets_of(*names):
    out = {}
    for n in names:
        out.setdefault(n, []).append({"drg_no": n})
    return out


class SheetFileNameTest(unittest.TestCase):
    """A drawing of one sheet keeps its name; one of several does not."""

    def test_a_single_sheet_drawing_keeps_the_drawing_number(self):
        s = {"drg_no": "FX-00"}
        self.assertEqual(export.sheet_pdf_name(s, {"FX-00": [s]}), "FX-00")

    def test_two_sheets_of_one_drawing_get_a_file_each(self):
        a, b = {"drg_no": "FX-00"}, {"drg_no": "FX-00"}
        mates = {"FX-00": [a, b]}
        self.assertEqual(export.sheet_pdf_name(a, mates), "FX-00_SH1")
        self.assertEqual(export.sheet_pdf_name(b, mates), "FX-00_SH2")

    def test_it_follows_the_sheet_s_own_numbering(self):
        a = {"drg_no": "FX-00", "sheet_of": [1, 2]}
        b = {"drg_no": "FX-00", "sheet_of": [2, 2]}
        mates = {"FX-00": [a, b]}
        self.assertEqual(export.sheet_pdf_name(b, mates), "FX-00_SH2")
        self.assertEqual(export.sheet_pdf_name(a, mates), "FX-00_SH1")

    def test_other_drawings_are_untouched_by_a_two_sheet_one(self):
        a, b = {"drg_no": "FX-00"}, {"drg_no": "FX-00"}
        c = {"drg_no": "FX-01"}
        mates = {"FX-00": [a, b], "FX-01": [c]}
        self.assertEqual(export.sheet_pdf_name(c, mates), "FX-01")


class ExportTwoSheetsTest(unittest.TestCase):
    """The whole of /export, with the plot and pdfunite stood in for."""

    def run_export(self, sheets, deliver=None):
        self.plotted, self.merges = [], []

        def fake_plot(musa, pdf, *, scale, window, stamp=None, stamps=None,
                      paper="A4", orientation="portrait"):
            self.plotted.append((Path(musa).name, Path(pdf).name))
            Path(pdf).write_text(f"%PDF-1.4 {Path(musa).stem}\n%%EOF\n")
            return {"mm_per_unit": str(scale[0] / scale[1]), "paper": paper,
                    "raw": f"[musa_plot] ok paper={paper} {orientation}"}

        def fake_run(cmd, **kw):
            # pdfunite <inputs...> <out>
            self.merges.append([Path(c).name for c in cmd[1:]])
            Path(cmd[-1]).write_text("merged\n")

            class R:
                returncode = 0
                stderr = ""
            return R()

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            musa, pdfs = tmp / "musa", tmp / "pdf"
            musa.mkdir()
            for s in sheets:
                s["file"] = str(musa / (Path(s["file"]).name))
                Path(s["file"]).write_text("MUSACAD 14\nEND\n")
            meta = {"sheets": sheets}
            if deliver:
                meta["deliver_to"] = str(tmp / "deliver")
            common.write_json(musa / "sheets.json", meta)
            saved = (export.OUTPUT_MUSA_DIR, export.OUTPUT_PDF_DIR,
                     export.run_musa_plot, export.subprocess.run)
            export.OUTPUT_MUSA_DIR, export.OUTPUT_PDF_DIR = musa, pdfs
            export.run_musa_plot = fake_plot
            export.subprocess.run = fake_run
            try:
                export.main()
                self.files = sorted(p.name for p in pdfs.iterdir())
                self.delivered = sorted(
                    p.name for p in (tmp / "deliver").iterdir()) \
                    if deliver else []
            finally:
                (export.OUTPUT_MUSA_DIR, export.OUTPUT_PDF_DIR,
                 export.run_musa_plot, export.subprocess.run) = saved

    @staticmethod
    def sheet(drg, stem, **kw):
        s = {"drg_no": drg, "file": f"{stem}.musa", "description": drg,
             "scale": "1:1", "window": [0, 0, 10, 10], "kind": "assembly"}
        s.update(kw)
        return s

    def test_each_sheet_is_plotted_to_its_own_file(self):
        self.run_export([self.sheet("FX-00", "FX-00", sheet_of=[1, 2]),
                         self.sheet("FX-00", "FX-00_2", sheet_of=[2, 2])])
        self.assertEqual([p for _m, p in self.plotted],
                         ["FX-00_SH1.pdf", "FX-00_SH2.pdf"])
        self.assertIn("FX-00_SH1.pdf", self.files)
        self.assertIn("FX-00_SH2.pdf", self.files)

    def test_the_print_set_carries_every_sheet_once_in_order(self):
        self.run_export([self.sheet("FX-01", "FX-01"),
                         self.sheet("FX-00", "FX-00", sheet_of=[1, 2]),
                         self.sheet("FX-00", "FX-00_2", sheet_of=[2, 2])])
        pages = next(m for m in self.merges if m[-1] == "print_set.pdf")
        self.assertEqual(pages, ["FX-01.pdf", "FX-00_SH1.pdf",
                                 "FX-00_SH2.pdf", "print_set.pdf"])

    def test_the_drawing_s_own_pdf_holds_both_its_sheets(self):
        self.run_export([self.sheet("FX-00", "FX-00", sheet_of=[1, 2]),
                         self.sheet("FX-00", "FX-00_2", sheet_of=[2, 2])])
        whole = next(m for m in self.merges if m[-1] == "FX-00.pdf")
        self.assertEqual(whole, ["FX-00_SH1.pdf", "FX-00_SH2.pdf",
                                 "FX-00.pdf"])

    def test_a_one_sheet_set_is_exactly_as_it_was(self):
        self.run_export([self.sheet("FX-01", "FX-01"),
                         self.sheet("FX-02", "FX-02")])
        self.assertEqual([p for _m, p in self.plotted],
                         ["FX-01.pdf", "FX-02.pdf"])
        self.assertEqual(self.merges,
                         [["FX-01.pdf", "FX-02.pdf", "print_set.pdf"]])

    def test_delivery_takes_the_drawing_not_the_loose_sheets(self):
        self.run_export([self.sheet("FX-00", "FX-00", sheet_of=[1, 2]),
                         self.sheet("FX-00", "FX-00_2", sheet_of=[2, 2]),
                         self.sheet("FX-01", "FX-01")], deliver=True)
        self.assertEqual(self.delivered,
                         ["FX-00.pdf", "FX-01.pdf", "print_set.pdf"])


@unittest.skipUnless(shutil.which("pdfunite") and shutil.which("pdftoppm")
                     and common.MUSA_PLOT.exists(),
                     "needs musa_plot, pdfunite and pdftoppm")
class RealTwoSheetPrintSetTest(unittest.TestCase):
    """The real thing: two sheets through the real plot and the real
    pdfunite, read back page by page."""

    def test_the_two_pages_are_not_the_same_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            musa, pdfs = tmp / "musa", tmp / "pdf"
            musa.mkdir()
            sheets = []
            for i in (1, 2):
                ctx = mw.SheetContext(1, 1)
                ctx.add_template({"SHEET 9": f"SHEET {i} OF 2"})
                f = musa / ("FX-00" + ("" if i == 1 else "_2") + ".musa")
                f.write_text(ctx.render())
                sheets.append({"drg_no": "FX-00", "file": str(f),
                               "description": "TWO SHEET DRAWING",
                               "scale": "1:1", "window": list(ctx.window),
                               "kind": "assembly", "sheet_of": [i, 2]})
            common.write_json(musa / "sheets.json", {"sheets": sheets})
            saved = (export.OUTPUT_MUSA_DIR, export.OUTPUT_PDF_DIR)
            export.OUTPUT_MUSA_DIR, export.OUTPUT_PDF_DIR = musa, pdfs
            try:
                export.main()
            finally:
                (export.OUTPUT_MUSA_DIR, export.OUTPUT_PDF_DIR) = saved
            ps = pdfs / "print_set.pdf"
            self.assertTrue(ps.exists())
            # the sheet's text is drawn as strokes, not glyphs, so the
            # pages are compared as the issue compared them: rendered
            pages = []
            for n in (1, 2):
                subprocess.run(
                    ["pdftoppm", "-r", "40", "-png", "-singlefile",
                     "-f", str(n), "-l", str(n), str(ps),
                     str(tmp / f"page{n}")], check=True)
                pages.append(hashlib.sha256(
                    (tmp / f"page{n}.png").read_bytes()).hexdigest())
            self.assertEqual(len(pages), 2)
            self.assertNotEqual(pages[0], pages[1],
                                "the print set carries one sheet twice")
            # and each sheet has its own file beside the merged ones
            self.assertTrue((pdfs / "FX-00_SH1.pdf").exists())
            self.assertTrue((pdfs / "FX-00_SH2.pdf").exists())
            self.assertTrue((pdfs / "FX-00.pdf").exists())


if __name__ == "__main__":
    unittest.main()
