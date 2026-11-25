# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Pranay Kiran
"""A datum box belongs to its own callout (issue #27).

Run with:  python3 -m unittest discover -s tests -v

The real failure, on a turned part at 1:1: the datum of the spigot's
diameter had its triangle on that callout's leader, and its BOX one line
below, against the next callout's text — 1.4 mm from "2 x M4 THRU WALL,
180° APART" and 4.0 mm from its own "⌀46.7 (SPIGOT)". Read as printed,
the sheet names the M4 holes as datum A. A box reads as belonging to the
nearest label, so the nearest label has to be its own.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import draw  # noqa: E402
import layout_check as lc  # noqa: E402
import musa_writer as mw  # noqa: E402

A = mw.P_ANNO


def solid_triangles(text):
    out = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if not line.startswith("HATCH ") or lines[i + 1].strip() != "SOLID":
            continue
        t = line.split()
        if t[1] != "1" or t[2] != "3":
            continue
        v = [float(x) for x in t[3:9]]
        out.append([(v[0], v[1]), (v[2], v[3]), (v[4], v[5])])
    return out


class ItsOwnCalloutTest(unittest.TestCase):
    """The fixture is the shape of the real one: a short callout with a
    longer one on the line below, and the shoulder spots taken."""

    OWN = (124.0, 248.0, 150.0, 252.0)        # "%%c46.7 (SPIGOT)"
    NEXT = (124.0, 238.0, 260.0, 243.0)       # "2 x M4 THRU WALL, ..."

    def setUp(self):
        self.ctx = mw.SheetContext(1, 1)
        self.ctx.dimstyles = draw.sheet_dimstyles(self.ctx)
        self.layout = draw.Layout(self.ctx)
        self.layout.claim(self.OWN, "callout-own")
        self.layout.claim(self.NEXT, "callout-next")
        self.land = ((120.0, 250.0), (124.0, 250.0), (60.0, 250.0))

    def block_the_shoulder(self):
        """Every spot beside the knee taken, as a crowded column takes
        them — which is when the box went looking further afield."""
        for y in (252.5, 243.5):
            self.layout.claim((108.0, y, 170.0, y + 4.0), "callout-other")

    def place(self, why=None):
        return draw.datum_on_landing(self.ctx, self.layout, "A", self.land,
                                     stack=self.OWN, why=why)

    def gap(self, rect, other):
        return draw.rect_gap(rect, other)

    def test_the_box_is_nearer_its_own_callout_than_the_next(self):
        _recs, rect = self.place()
        self.assertIsNotNone(rect)
        self.assertLessEqual(self.gap(rect, self.OWN),
                             self.gap(rect, self.NEXT))

    def test_it_holds_when_the_shoulder_spots_are_taken(self):
        self.block_the_shoulder()
        _recs, rect = self.place()
        if rect is None:
            return                      # failing loudly is the other half
        self.assertLessEqual(self.gap(rect, self.OWN),
                             self.gap(rect, self.NEXT))

    def test_when_there_is_no_such_spot_the_sheet_says_so(self):
        # a wide column: this callout's own text is far from its knee,
        # another's sits right under it, and the spots in line with its
        # own text are taken. Every spot left would read as the other
        # callout's datum, so there is no datum to draw.
        self.layout = draw.Layout(self.ctx)
        far = (200.0, 248.0, 260.0, 252.0)
        self.layout.claim(far, "callout-own")
        self.layout.claim((108.0, 236.0, 140.0, 241.0), "callout-next")
        self.layout.claim((255.0, 240.0, 320.0, 262.0), "callout-third")
        why = []
        recs, rect = draw.datum_on_landing(self.ctx, self.layout, "A",
                                           self.land, stack=far, why=why)
        self.assertIsNone(rect)
        self.assertEqual(recs, [])
        self.assertTrue(why, "the sheet failed without saying why")
        self.assertIn("its own callout", why[0])
        self.assertIn("nearer", why[0])

    def test_the_clearance_is_measured_to_the_strangers(self):
        """Its own callout's text does not count as something to stand
        clear of — it is the thing it stands beside."""
        rect = (152.0, 248.0, 157.4, 253.4)
        self.assertEqual(self.layout.anno_clearance(rect, ignore=self.OWN),
                         self.layout.anno_clearance(rect,
                                                    ignore=self.OWN))
        self.assertGreater(self.layout.anno_clearance(rect,
                                                      ignore=self.OWN),
                           self.layout.anno_clearance(rect))


class CheckReadsItBackTest(unittest.TestCase):
    """And the layout check reports it off the finished sheet."""

    AREA = (0.0, 0.0, 400.0, 400.0)

    def sheet(self, box_x, box_y):
        h = 3.2
        recs = [
            mw.leader((40.0, 200.0), (120.0, 200.0),
                      "%%c46.7 -0.1/0 (SPIGOT)", h, props=A),
            mw.leader((40.0, 180.0), (120.0, 190.0),
                      "2 x M4 THRU WALL, 180%%d APART", h, props=A),
        ]
        recs += mw.datum_label((box_x, box_y), "A", (121.0, 200.0), h,
                               props=A)[0]
        return "MUSACAD 14\n" + "\n".join(recs) + "\nEND\n"

    def kinds(self, text):
        return [f["kind"] for f in lc.check_sheet(text, self.AREA)]

    def test_a_box_beside_its_own_callout_is_silent(self):
        text = self.sheet(123.0, 201.5)
        self.assertNotIn("datum-beside-wrong-callout", self.kinds(text))

    def test_a_box_that_drifted_to_the_next_label_is_reported(self):
        # down beside the second leader's label, away from its own
        text = self.sheet(123.0, 186.0)
        found = lc.check_sheet(text, self.AREA)
        hit = [f for f in found if f["kind"] == "datum-beside-wrong-callout"]
        self.assertTrue(hit, [f["kind"] for f in found])
        self.assertIn("datum A", hit[0]["what"])
        self.assertIn("M4", hit[0]["what"])

    def test_it_is_a_fault_not_a_warning(self):
        text = self.sheet(123.0, 186.0)
        self.assertTrue([f for f in lc.faults(lc.check_sheet(text, self.AREA))
                         if f["kind"] == "datum-beside-wrong-callout"])

    def test_the_triangle_s_tip_is_what_names_the_feature(self):
        text = self.sheet(123.0, 201.5)
        recs = lc.parse(text)
        texts = [(r, lc.text_ink(r)) for r in recs
                 if r.kind in lc.TEXT_KINDS and lc.text_ink(r)]
        boxes = lc.datum_boxes(recs, texts)
        self.assertEqual(len(boxes), 1)
        _box, letter, own = boxes[0]
        self.assertEqual(letter, "A")
        self.assertIsNotNone(own, "the datum's own callout was not found")


if __name__ == "__main__":
    unittest.main()
