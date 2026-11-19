# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Pranay Kiran
"""Company logo images on per-project title blocks.

A company title block is the project's own .musa (the manifest's
"template", an absolute path). It may carry a sidecar `<stem>.logo.json`
naming a PNG and a box in template units; draw.py turns it into a per-sheet stamp rect and export.py
passes it to the harness with --stamp, next to the shaded pictorial.

Run with:  python3 -m unittest discover -s tests -v
"""

import json
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import common  # noqa: E402
import musa_writer as mw  # noqa: E402


def write_png(path: Path, w: int, h: int) -> None:
    """A minimal valid greyscale PNG of w x h pixels."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data)))
    raw = b"".join(b"\x00" + b"\xff" * w for _ in range(h))
    path.write_bytes(b"\x89PNG\r\n\x1a\n"
                     + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw))
                     + chunk(b"IEND", b""))


class TemplateLogoTest(unittest.TestCase):
    def setUp(self):
        self.saved = mw.ACTIVE_TEMPLATE
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.tpl = self.dir / "template_x.musa"
        self.tpl.write_text(mw.TEMPLATE.read_text())

    def tearDown(self):
        mw.ACTIVE_TEMPLATE = self.saved
        self.tmp.cleanup()

    def sidecar(self, box, png="logos/x.png", size=(200, 100)):
        (self.dir / "logos").mkdir(exist_ok=True)
        write_png(self.dir / "logos" / "x.png", *size)
        (self.dir / "template_x.logo.json").write_text(
            json.dumps({"png": png, "box": box}))

    def test_png_size(self):
        p = self.dir / "a.png"
        write_png(p, 37, 11)
        self.assertEqual(mw.png_size(p), (37, 11))
        (self.dir / "b.png").write_bytes(b"GIF89a" + b"\0" * 30)
        with self.assertRaises(ValueError):
            mw.png_size(self.dir / "b.png")

    def test_no_sidecar_no_logo(self):
        mw.use_template(self.tpl)
        self.assertIsNone(mw.template_logo())
        self.assertIsNone(mw.SheetContext(1, 2).logo_stamp())

    def test_default_template_has_no_logo(self):
        self.assertIsNone(mw.template_logo(mw.TEMPLATE))

    def test_missing_png_is_an_error(self):
        self.sidecar([0, 0, 10, 10], png="logos/none.png")
        with self.assertRaises(FileNotFoundError):
            mw.template_logo(self.tpl)

    def test_empty_box_is_an_error(self):
        self.sidecar([10, 0, 10, 10])
        with self.assertRaises(ValueError):
            mw.template_logo(self.tpl)

    def test_stamp_is_letterboxed_and_scaled(self):
        # a 2:1 image in a 100 x 100 unit box: full width, half height, centred
        self.sidecar([900, 340, 1000, 440], size=(200, 100))
        mw.use_template(self.tpl)
        for num, den in [(1, 1), (1, 2), (2, 1)]:
            ctx = mw.SheetContext(num, den)
            st = ctx.logo_stamp()
            x, y, w, h = st["rect"]
            self.assertAlmostEqual(w, 100 * ctx.t)
            self.assertAlmostEqual(h, 50 * ctx.t)
            self.assertAlmostEqual(x, ctx.tx(900))
            self.assertAlmostEqual(y + h / 2, ctx.ty(390))
            self.assertTrue(Path(st["png"]).is_absolute())
        # the logo prints at a constant paper size whatever the sheet scale
        a = mw.SheetContext(1, 1).logo_stamp()["rect"][2] * 1 / 1
        b = mw.SheetContext(1, 5).logo_stamp()["rect"][2] * 1 / 5
        self.assertAlmostEqual(a, b)

    def test_plot_cmd_takes_several_stamps(self):
        cmd = common.musa_plot_cmd(Path("s.musa"), Path("s.pdf"),
                                   scale=(1, 2), window=(0, 0, 10, 20),
                                   stamps=[("pic.png", 1, 2, 3, 4),
                                           ("logo.png", 5, 6, 7, 8)])
        st = [cmd[i + 1] for i, a in enumerate(cmd) if a == "--stamp"]
        self.assertEqual(len(st), 2)
        self.assertTrue(st[0].startswith("pic.png,1.000000,2.000000"))
        self.assertTrue(st[1].startswith("logo.png,5.000000"))
        self.assertNotIn("--stamp", common.musa_plot_cmd(
            Path("s.musa"), Path("s.pdf"), scale=(1, 1), window=(0, 0, 1, 1)))


class ProjectTemplateTest(unittest.TestCase):
    """Company title blocks belong to the project, not the pipeline: the
    manifest names the project's own .musa by absolute path."""

    def test_pipeline_ships_only_the_default(self):
        files = sorted(f.name for f in (ROOT / "templates").rglob("*")
                       if f.is_file())
        self.assertEqual(files, ["template.musa"])
        self.assertIsNone(mw.template_logo(mw.TEMPLATE))

    def test_resolve(self):
        self.assertEqual(mw.resolve_template("/x/y/t.musa"), Path("/x/y/t.musa"))
        self.assertEqual(mw.resolve_template("template.musa"), mw.TEMPLATE)

    def test_problems(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            tpl = d / "template_co.musa"
            self.assertTrue(mw.template_problems(tpl)[0].startswith(
                "template not found"))
            tpl.write_text(mw.TEMPLATE.read_text())
            self.assertEqual(mw.template_problems(tpl), [])
            (d / "template_co.logo.json").write_text(
                json.dumps({"png": "co.png", "box": [850, 346, 912, 408]}))
            self.assertIn("co.png", mw.template_problems(tpl)[0])
            write_png(d / "co.png", 180, 180)
            self.assertEqual(mw.template_problems(tpl), [])
            tpl.write_text("MUSACAD 14\nBOGUS 1\nEND\n")
            self.assertEqual(len(mw.template_problems(tpl)), 1)
        self.assertEqual(mw.template_problems("template.musa"), [])


if __name__ == "__main__":
    unittest.main()
