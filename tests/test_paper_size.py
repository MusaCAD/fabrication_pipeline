# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Pranay Kiran
"""One A3 sheet in an A4 set (issue #10).

Run with:  python3 -m unittest discover -s tests -v

A long assembly at 1:5 is wider than the A4 drawing area, and a long
thin-walled part needs 1:5 on A4 where its walls print under a millimetre.
Both fit A3 landscape. A sheet therefore states its own paper, and the set
mixes them: the title block prints at the same size on every sheet, the
scale is still exactly what the SCALE field says, and only the drawing
area grows.

The A3 title block is the A4 one WIDENED (tools/widen_template.py), so the
pipeline measures the widening from the file and needs nothing restated.
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import common  # noqa: E402
import draw  # noqa: E402
import judge  # noqa: E402
import manifest as mf  # noqa: E402
import musa_writer as mw  # noqa: E402

from test_gdt_placement import tube_front, tube_manifest, tube_section  # noqa: E402

sys.path.insert(0, str(ROOT / "tools"))
import widen_template  # noqa: E402


def widened(tmp: Path, paper="A3", orientation="landscape") -> Path:
    dst = tmp / f"template_{common.paper_key(paper, orientation).lower()}.musa"
    widen_template.widen(common.TEMPLATE, dst, paper, orientation)
    return dst


class PaperTableTest(unittest.TestCase):

    def test_the_default_sheet_keeps_the_measured_fit_factor(self):
        geom = common.sheet_geom()
        self.assertEqual(geom["s0"], common.S0)
        self.assertEqual(geom["paper"], "A4")
        self.assertEqual(geom["orientation"], "portrait")

    def test_the_paper_follows_from_the_template_s_own_size(self):
        # the default template prints 191.8 x 289.9: A4 portrait and
        # nothing smaller
        self.assertEqual(mw.paper_for(191.84, 289.90), ("A4", "portrait"))
        self.assertEqual(mw.paper_for(401.84, 289.90), ("A3", "landscape"))
        self.assertEqual(mw.paper_for(289.90, 400.0), ("A3", "portrait"))
        self.assertIsNone(mw.paper_for(900.0, 900.0))

    def test_paper_keys(self):
        self.assertEqual(common.paper_key("A3", "landscape"), "A3L")
        self.assertEqual(common.paper_key("a4", "portrait"), "A4P")


class WidenedTemplateTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="fabpipe-paper-")
        cls.dir = Path(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_each_paper_round_trips(self):
        for paper, orient in (("A3", "landscape"), ("A3", "portrait"),
                              ("A2", "landscape")):
            path = widened(self.dir, paper, orient)
            geom = mw.template_geom(path)
            self.assertEqual((geom["paper"], geom["orientation"]),
                             (paper, orient), path.name)
            # it fills that paper at the SAME unit size as the A4 sheet
            self.assertEqual(geom["s0"], common.S0)
            pw, ph = common.PAPER_PAINTABLE[(paper, orient)]
            w = (geom["ext_max"][0] - geom["ext_min"][0]) * geom["s0"]
            h = (geom["ext_max"][1] - geom["ext_min"][1]) * geom["s0"]
            self.assertAlmostEqual(w, pw, places=1)
            self.assertAlmostEqual(h, ph, places=1)

    def test_the_left_edge_and_the_title_block_rows_do_not_move(self):
        geom = mw.template_geom(widened(self.dir))
        self.assertEqual(geom["drawing_area"][0], common.DRAWING_AREA[0])
        self.assertEqual(geom["ext_min"], tuple(common.TEMPLATE_EXT_MIN))
        ctx = mw.SheetContext(1, 1, geom=geom)
        # the parts table travels with the right edge
        self.assertAlmostEqual(ctx.fx(mw.PARTS_COLS[0][1]),
                               mw.PARTS_COLS[0][1] + geom["widen"][0])

    def test_a_template_that_is_not_a_widened_copy_is_refused(self):
        bad = self.dir / "shifted.musa"
        # a frame that starts somewhere else: the title block would land
        # in the wrong place on every sheet drawn with it
        text = common.TEMPLATE.read_text().replace(
            "END\n", "LINE -50.0 -50.0 -40.0 -40.0 0 7 255 255 255 0 25\nEND\n")
        bad.write_text(text)
        with self.assertRaises(ValueError) as e:
            mw.template_geom(bad)
        self.assertIn("same sheet widened", str(e.exception))


class SheetOnA3Test(unittest.TestCase):
    """A part sheet that states A3: the whole path, to the PDF."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="fabpipe-a3-")
        cls.dir = Path(cls.tmp.name)
        cls.tpl = widened(cls.dir)
        cls.proj = {"01:front": tube_front(), "01:section": tube_section()}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def sheet(self, paper=None, scale="1:5"):
        m, part = tube_manifest(self.dir, {"datums": [], "frames": []})
        m["templates"] = {"A3L": str(self.tpl)}
        part = dict(part, scale=scale)
        if paper:
            part["paper"] = paper
        return m, part

    def test_an_a4_sheet_is_untouched_by_the_feature(self):
        m, part = self.sheet()
        a4 = draw.build_part_sheet(m, part, self.proj)[0].render()
        m2, part2 = self.sheet()
        del m2["templates"]
        self.assertEqual(a4, draw.build_part_sheet(m2, part2,
                                                   self.proj)[0].render())

    def test_the_a3_sheet_is_wider_and_says_so(self):
        m, part = self.sheet(paper="A3L", scale="1:2")
        ctx, _, lay = draw.build_part_sheet(m, part, self.proj)
        self.assertEqual((ctx.paper, ctx.orientation), ("A3", "landscape"))
        self.assertEqual(lay["unresolved"], [])
        a4 = mw.SheetContext(1, 2)
        self.assertGreater(ctx.window[2], a4.window[2] * 1.9)
        self.assertAlmostEqual(ctx.window[3], a4.window[3], places=6)
        self.assertIn("SCALE : 1:2", ctx.render())

    def test_the_same_part_does_not_fit_a4_at_that_scale(self):
        m, part = self.sheet(scale="1:2")
        with self.assertRaises(SystemExit):
            draw.build_part_sheet(m, part, self.proj)

    def test_the_drawing_area_grows_but_the_table_does_not(self):
        m, part = self.sheet(paper="A3L", scale="1:2")
        ctx, _, _ = draw.build_part_sheet(m, part, self.proj)
        band = draw.table_band(ctx, mw.PARTS_ROW_H * ctx.t)
        self.assertGreater(band[0], ctx.draw_area[0],
                           "the table should not span the whole A3 sheet")
        self.assertAlmostEqual(band[2], ctx.draw_area[2], places=6)

    @unittest.skipUnless(common.MUSA_PLOT.exists(), "musa_plot not built")
    def test_it_plots_on_a3_landscape_at_the_stated_scale(self):
        m, part = self.sheet(paper="A3L", scale="1:2")
        ctx, _, _ = draw.build_part_sheet(m, part, self.proj)
        musa = self.dir / "a3.musa"
        musa.write_text(ctx.render())
        info = common.run_musa_plot(musa, self.dir / "a3.pdf", scale=(1, 2),
                                    window=ctx.window, paper=ctx.paper,
                                    orientation=ctx.orientation)
        self.assertEqual(info["paper"], "A3")
        self.assertIn("landscape", info["raw"])
        self.assertAlmostEqual(float(info["mm_per_unit"]), 0.5, places=6)
        out = subprocess.run(["pdfinfo", str(self.dir / "a3.pdf")],
                             capture_output=True, text=True)
        self.assertIn("1191 x 842", out.stdout)


class PaperInTheManifestTest(unittest.TestCase):

    def test_the_forms_a_sheet_may_state(self):
        self.assertEqual(draw.sheet_paper({"paper": "A3L"}),
                         ("A3", "landscape"))
        self.assertEqual(draw.sheet_paper({"paper": "A3"}), ("A3", "portrait"))
        self.assertEqual(draw.sheet_paper(
            {"paper": {"size": "A3", "orientation": "landscape"}}),
            ("A3", "landscape"))
        self.assertIsNone(draw.sheet_paper({}))

    def test_the_lock_wants_a_template_for_the_paper(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            m, part = tube_manifest(tmp, {"datums": [], "frames": []})
            part["paper"] = "A3L"
            m["parts"] = [part]
            bad = " ".join(mf.paper_problems(m))
            self.assertIn("no template for A3L", bad)
            m["templates"] = {"A3L": str(widened(tmp))}
            self.assertEqual(mf.paper_problems(m), [])

    def test_the_lock_refuses_a_paper_the_harness_cannot_plot(self):
        with tempfile.TemporaryDirectory() as tmp:
            m, part = tube_manifest(Path(tmp), {"datums": [], "frames": []})
            part["paper"] = "A9"
            m["parts"] = [part]
            bad = " ".join(mf.paper_problems(m))
            self.assertIn("not one the harness is measured for", bad)


class JudgeOnA3Test(unittest.TestCase):

    def test_the_drawing_area_is_bigger_on_a3(self):
        a4w, a4h = judge.area_mm()
        a3w, a3h = judge.area_mm("A3", "landscape")
        self.assertGreater(a3w, a4w * 1.9)
        self.assertAlmostEqual(a3h, a4h, places=6)

    def test_a_part_that_needs_1_5_on_a4_fits_1_2_on_a3(self):
        # 600 long, 75.5 across — a long thin-walled tube
        self.assertEqual(judge.best_scale(600.0, 75.5), (1, 5))
        self.assertEqual(judge.best_scale(600.0, 75.5, "A3", "landscape"),
                         (1, 2))

    def test_a_part_is_judged_on_the_paper_its_sheet_states(self):
        parts = [{"part_id": "03", "file": "/m/tube.FCStd", "scale": "1:2",
                  "drg_no": "FX-03", "paper": "A3L", "sheet": True},
                 {"part_id": "04", "file": "/m/plug.FCStd", "scale": "1:1",
                  "drg_no": "FX-04", "sheet": True}]
        papers = judge.sheet_papers(parts)
        self.assertEqual(papers["/m/tube.FCStd"], ("A3", "landscape"))
        self.assertNotIn("/m/plug.FCStd", papers)

    def test_a_part_drawn_on_another_sheet_takes_its_paper(self):
        parts = [{"part_id": "13", "file": "/m/kit.FCStd", "scale": "2:1",
                  "drg_no": "FX-13", "paper": "A3L", "sheet": True},
                 {"part_id": "25", "file": "/m/disc.FCStd", "scale": "1:1",
                  "drg_no": "FX-13", "sheet": False}]
        self.assertEqual(judge.sheet_papers(parts)["/m/disc.FCStd"],
                         ("A3", "landscape"))


class ExportPaperTest(unittest.TestCase):
    """/export plots each sheet on the paper that sheet states, and checks
    the harness agrees — a print set may mix papers."""

    def test_each_sheet_is_plotted_on_its_own_paper(self):
        import export
        calls = []

        def fake_plot(musa, pdf, *, scale, window, stamp=None, stamps=None,
                      paper="A4", orientation="portrait"):
            calls.append((Path(musa).name, paper, orientation))
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
            sheets = [{"file": str(musa / "A.musa"), "drg_no": "A",
                       "description": "A", "scale": "1:1",
                       "window": [0, 0, 10, 10], "kind": "part"},
                      {"file": str(musa / "B.musa"), "drg_no": "B",
                       "description": "B", "scale": "1:2",
                       "window": [0, 0, 20, 10], "kind": "assembly",
                       "paper": "A3", "orientation": "landscape"}]
            for s in sheets:
                Path(s["file"]).write_text("MUSACAD 14\nEND\n")
            common.write_json(musa / "sheets.json", {"sheets": sheets})
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
        self.assertEqual(calls, [("A.musa", "A4", "portrait"),
                                 ("B.musa", "A3", "landscape")])


class PlotCommandTest(unittest.TestCase):

    def test_the_harness_is_told_the_paper(self):
        cmd = common.musa_plot_cmd(Path("s.musa"), Path("s.pdf"),
                                   scale=(1, 2), window=(0, 0, 10, 10),
                                   paper="A3", orientation="landscape")
        self.assertIn("--paper", cmd)
        self.assertEqual(cmd[cmd.index("--paper") + 1], "A3")
        self.assertIn("--landscape", cmd)
        self.assertNotIn("--portrait", cmd)

    def test_a4_portrait_is_still_the_default(self):
        cmd = common.musa_plot_cmd(Path("s.musa"), Path("s.pdf"),
                                   scale=(1, 1), window=(0, 0, 10, 10))
        self.assertEqual(cmd[cmd.index("--paper") + 1], "A4")
        self.assertIn("--portrait", cmd)


if __name__ == "__main__":
    unittest.main()
