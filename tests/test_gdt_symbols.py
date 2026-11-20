# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Pranay Kiran
"""Feature control frame symbols (ISO 1101).

Run with:  python3 -m unittest discover -s tests -v

Every symbol draws inside its own cell and the frame parses in the real
harness (skipped when the harness is not built).
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import common  # noqa: E402
import musa_writer as mw  # noqa: E402



def coords(rec: str):
    """(x, y) pairs a LINE / CIRCLE / POLYLINE record touches (circles as
    their bounding corners)."""
    t = rec.split()
    if t[0] == "LINE":
        x0, y0, x1, y1 = map(float, t[1:5])
        return [(x0, y0), (x1, y1)]
    if t[0] == "CIRCLE":
        cx, cy, r = map(float, t[1:4])
        return [(cx - r, cy - r), (cx + r, cy + r)]
    if t[0] == "POLYLINE":
        n = int(t[2])
        v = list(map(float, t[3:3 + 2 * n]))
        return list(zip(v[::2], v[1::2]))
    return []


class SymbolTest(unittest.TestCase):
    def test_every_symbol_stays_in_its_cell(self):
        s = 10.0
        for name, fn in mw._FCF_SYMBOLS.items():
            with self.subTest(symbol=name):
                recs = fn(100.0, 200.0, s, mw.P)
                self.assertTrue(recs)
                for r in recs:
                    for x, y in coords(r):
                        self.assertTrue(100.0 <= x <= 110.0 and 200.0 <= y <= 210.0,
                                        (name, x, y))

    def test_coaxiality_is_two_concentric_circles(self):
        recs = mw._FCF_SYMBOLS["coaxiality"](0.0, 0.0, 10.0, mw.P)
        circles = [r.split() for r in recs if r.startswith("CIRCLE")]
        self.assertEqual(len(circles), 2)
        self.assertEqual(circles[0][1:3], circles[1][1:3])
        self.assertNotEqual(circles[0][3], circles[1][3])

    def test_runout_arrow_points_up_right(self):
        recs = mw._FCF_SYMBOLS["runout"](0.0, 0.0, 10.0, mw.P)
        x0, y0, x1, y1 = map(float, recs[0].split()[1:5])
        self.assertGreater(x1, x0)
        self.assertAlmostEqual(x1 - x0, y1 - y0)

    @unittest.skipUnless(common.MUSA_PLOT.exists(), "musa_plot not built")
    def test_frames_parse_in_the_harness(self):
        body = ["MUSACAD 14", "UNITS 4", "CURRENT 0", "LTSCALE 1",
                "LAYER 255 255 255 0 25 1 0 0 0", mw.dimstyle(3.0, 2.5)]
        y = 0.0
        for name in mw._FCF_SYMBOLS:
            recs, w, h = mw.fcf((0.0, y), name, "%%c0.1", ["A"], 3.0)
            body += recs
            y += h + 2.0
        body.append("END")
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "fcf.musa"
            f.write_text("\n".join(body) + "\n")
            env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
            r = subprocess.run([str(common.MUSA_PLOT), "--file", str(f), str(Path(d) / "fcf.pdf"),
                                "--paper", "A4", "--portrait", "--scale", "1:1",
                                "--window", f"-5,-5,80,{y + 5}"],
                               capture_output=True, text=True, env=env, timeout=60)
            self.assertIn("[musa_plot] ok", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
