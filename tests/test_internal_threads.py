# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Pranay Kiran
"""ISO 6410 for a TAPPED hole (issue #12).

Run with:  python3 -m unittest discover -s tests -v

A tapped hole is modelled as its tap drill, so the solid's own edge is the
MINOR diameter and the thread lives only in the callout text. ISO 6410
draws it: a thin continuous line at the major diameter over the threaded
length, a thread-end line where the usable thread stops — short of the
drill depth — and a three-quarter thin arc at the major diameter in the
view that shows the hole as a circle.

The fixture is the real failure: the head plug's M6 x 13 deep on a ⌀5 x 16
tap drill, drawn as a side section and an end view.
"""

import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import draw  # noqa: E402
import manifest as mf  # noqa: E402
import musa_writer as mw  # noqa: E402

MAJOR, DRILL = 6.0, 5.0          # M6 x 1 on a ⌀5 tap drill
FACE_Y, THREAD_DEEP, DRILL_DEEP = 151.375, 138.375, 135.375
THREAD_LEN = FACE_Y - THREAD_DEEP

THIN = "0 7 255 255 255 0 25"     # ByLayer: the 0.25 mm thin pen
THICK = "0 3 255 255 255 0 50"    # the 0.5 mm thick pen


def seg(a, b):
    return {"t": "seg", "a": list(a), "b": list(b)}


def plug_section():
    """The cut: material either side of the drilled hole, hatched out to
    the drill wall — which is where the solid ends, the thread being cut
    after it."""
    r, dr = 18.0, DRILL / 2
    groups, vis = [], []
    for sign in (1, -1):
        loop = [[sign * dr, FACE_Y], [sign * dr, DRILL_DEEP],
                [sign * r, DRILL_DEEP], [sign * r, FACE_Y]]
        groups.append({"outer": loop, "holes": []})
        vis += [seg(a, b) for a, b in zip(loop, loop[1:] + loop[:1])]
    return {"visible": vis, "hidden": [], "hatch_groups": groups,
            "bounds": [[-r, DRILL_DEEP], [r, FACE_Y]]}


def plug_front():
    """The end view: the hole is a circle at the tap-drill size."""
    return {"visible": [{"t": "circle", "c": [0.0, 0.0], "r": 18.0},
                        {"t": "circle", "c": [0.0, 0.0], "r": DRILL / 2}],
            "hidden": [], "bounds": [[-18.0, -18.0], [18.0, 18.0]]}


def tapped(**kw):
    spec = {"kind": "internal", "views": ["section"], "orient": "v",
            "axis": 0.0, "span": [THREAD_DEEP, FACE_Y],
            "major": MAJOR, "minor": DRILL,
            "circles": [{"view": "front", "center": [0.0, 0.0]}]}
    spec.update(kw)
    return [spec]


def records(threads, offs=None):
    ctx = mw.SheetContext(1, 1)
    views = {"front": plug_front(), "section": plug_section()}
    offs = offs or {"front": (0.0, 0.0), "section": (0.0, 0.0)}
    return draw.thread_lines(ctx, threads, views, offs)


def lines(recs):
    out = []
    for r in recs:
        t = r.split()
        if t[0] == "LINE":
            out.append((tuple(float(v) for v in t[1:5]), " ".join(t[5:])))
    return out


def arcs(recs):
    out = []
    for r in recs:
        t = r.split()
        if t[0] == "ARC":
            out.append((tuple(float(v) for v in t[1:6]), " ".join(t[6:])))
    return out


class TappedHoleInSectionTest(unittest.TestCase):

    def setUp(self):
        self.recs = records(tapped())

    def test_the_thread_is_drawn_at_the_major_diameter(self):
        """The drill wall is already on the sheet; what is missing is the
        thread, and for a tapped hole that is the MAJOR diameter."""
        verts = [(v, pr) for v, pr in lines(self.recs)
                 if abs(v[0] - v[2]) < 1e-9
                 and abs(abs(v[3] - v[1]) - THREAD_LEN) < 1e-6]
        self.assertEqual(len(verts), 2, "want one line each side")
        xs = sorted(v[0] for v, _ in verts)
        self.assertAlmostEqual(xs[1] - xs[0], MAJOR, places=6)
        self.assertNotAlmostEqual(xs[1] - xs[0], DRILL, places=6)
        for _, pr in verts:
            self.assertEqual(pr, THIN, "the major-dia line is a THIN line")

    def test_the_thread_ends_short_of_the_drill(self):
        ends = [(v, pr) for v, pr in lines(self.recs)
                if abs(v[1] - v[3]) < 1e-9
                and abs(abs(v[2] - v[0]) - MAJOR) < 1e-6]
        self.assertEqual(len(ends), 1)
        (x0, y0, x1, _), pr = ends[0]
        self.assertAlmostEqual(y0, THREAD_DEEP, places=6)
        self.assertGreater(y0, DRILL_DEEP, "the thread stops above the drill")
        self.assertEqual(pr, THICK, "the thread-end line is a THICK line")

    def test_the_end_line_is_on_by_default_for_a_tapped_hole(self):
        """Nothing else draws it: the thread simply stops inside the hole."""
        off = records(tapped(end_line=False))
        ends = [v for v, _ in lines(off)
                if abs(v[1] - v[3]) < 1e-9
                and abs(abs(v[2] - v[0]) - MAJOR) < 1e-6]
        self.assertEqual(ends, [])

    def test_it_follows_the_view_offset(self):
        recs = records(tapped(), offs={"front": (100.0, 200.0),
                                       "section": (40.0, 10.0)})
        xs = sorted({v[0] for v, _ in lines(recs)
                     if abs(v[0] - v[2]) < 1e-9})
        self.assertAlmostEqual(xs[0], 40.0 - MAJOR / 2, places=6)
        self.assertAlmostEqual(xs[-1], 40.0 + MAJOR / 2, places=6)


class TappedHoleInTheEndViewTest(unittest.TestCase):

    def test_a_three_quarter_arc_at_the_major_diameter(self):
        got = arcs(records(tapped()))
        self.assertEqual(len(got), 1)
        (cx, cy, r, a0, a1), pr = got[0]
        self.assertAlmostEqual(r, MAJOR / 2, places=6)
        self.assertAlmostEqual(a1 - a0, 1.5 * math.pi, places=6)
        self.assertEqual(pr, THIN)
        # open in the upper-right quadrant: the arc starts at 12 o'clock
        # and sweeps the other way round
        self.assertAlmostEqual(a0, math.pi / 2, places=6)

    def test_an_external_thread_draws_no_arc(self):
        self.assertEqual(arcs(records(tapped(kind="external"))), [])

    def test_the_arc_follows_its_own_view(self):
        recs = records(tapped(), offs={"front": (100.0, 200.0),
                                       "section": (0.0, 0.0)})
        (cx, cy, _, _, _), _ = arcs(recs)[0]
        self.assertAlmostEqual(cx, 100.0, places=6)
        self.assertAlmostEqual(cy, 200.0, places=6)


class ExternalThreadsUnchangedTest(unittest.TestCase):
    """The external convention is the other way round and must stay put."""

    def spec(self, **kw):
        s = {"views": ["section"], "orient": "v", "axis": 0.0,
             "span": [0.0, 10.0], "major": 6.0, "minor": 5.0}
        s.update(kw)
        return [s]

    def test_the_root_line_is_at_the_minor_diameter(self):
        verts = [v for v, _ in lines(records(self.spec()))
                 if abs(v[0] - v[2]) < 1e-9]
        xs = sorted(v[0] for v in verts)
        self.assertAlmostEqual(xs[1] - xs[0], 5.0, places=6)

    def test_no_end_line_unless_asked(self):
        plain = [v for v, _ in lines(records(self.spec()))
                 if abs(v[1] - v[3]) < 1e-9]
        self.assertEqual(plain, [])
        asked = [v for v, _ in lines(records(self.spec(end_line=True)))
                 if abs(v[1] - v[3]) < 1e-9]
        self.assertEqual(len(asked), 1)

    def test_kind_external_is_the_default(self):
        self.assertEqual(lines(records(self.spec())),
                         lines(records(self.spec(kind="external"))))


class ThreadLockTest(unittest.TestCase):

    def part(self, **kw):
        return {"file": "/m/plug.FCStd", "threads": tapped(**kw)}

    def test_a_clean_thread_locks(self):
        self.assertEqual(mf.thread_problems(self.part(), "02", [40, 40, 50]),
                         [])

    def test_a_thread_no_wider_than_its_tap_drill_fails(self):
        bad = mf.thread_problems(self.part(major=5.0), "02")
        self.assertTrue(bad)
        self.assertIn("bigger than its tap drill", bad[0])

    def test_a_span_with_no_length_fails(self):
        bad = mf.thread_problems(self.part(span=[10.0, 10.0]), "02")
        self.assertIn("no length", " ".join(bad))

    def test_a_span_longer_than_the_part_fails(self):
        bad = mf.thread_problems(self.part(span=[0.0, 900.0]), "02",
                                 [40, 40, 50])
        self.assertIn("longer", " ".join(bad))

    def test_an_unknown_kind_fails(self):
        bad = mf.thread_problems(self.part(kind="helical"), "02")
        self.assertIn("thread kind", " ".join(bad))

    def test_a_circle_without_a_centre_fails(self):
        bad = mf.thread_problems(self.part(circles=[{"view": "front"}]), "02")
        self.assertIn("center", " ".join(bad))


class ThreadEndLineTest(unittest.TestCase):
    """Where the thread-end line goes (issue #16).

    The real failure: a blind hole drilled from a POCKET FLOOR at
    x = 888.6 and threaded 10 deep to x = 898.6, in a section whose x
    increases into the part. The line was drawn at the lower span
    coordinate, which is the mouth, and the place where the thread really
    stops was left unmarked."""

    MOUTH, END = 888.6, 898.6

    def thread(self, **kw):
        spec = {"kind": "internal", "views": ["section"], "orient": "h",
                "axis": 0.0, "span": [self.END, self.MOUTH],
                "major": 6.0, "minor": 5.0}
        spec.update(kw)
        return [spec]

    def ends(self, recs):
        """The thread-end lines: across the major diameter."""
        return [v for v, _pr in lines(recs)
                if abs(v[0] - v[2]) < 1e-9
                and abs(abs(v[3] - v[1]) - 6.0) < 1e-6]

    def test_the_line_is_drawn_where_the_thread_stops(self):
        got = self.ends(records(self.thread()))
        self.assertEqual(len(got), 1)
        self.assertAlmostEqual(got[0][0], self.END, places=6)

    def test_not_at_the_mouth_the_hole_opens_from(self):
        got = self.ends(records(self.thread()))
        self.assertNotAlmostEqual(got[0][0], self.MOUTH, places=6)

    def test_end_at_names_it_when_the_span_reads_the_other_way(self):
        got = self.ends(records(self.thread(span=[self.MOUTH, self.END],
                                            end_at=self.END)))
        self.assertEqual(len(got), 1)
        self.assertAlmostEqual(got[0][0], self.END, places=6)

    def test_the_thread_itself_still_covers_the_whole_span(self):
        for spec in (self.thread(),
                     self.thread(span=[self.MOUTH, self.END],
                                 end_at=self.END)):
            roots = [v for v, _pr in lines(records(spec))
                     if abs(v[1] - v[3]) < 1e-9]
            self.assertEqual(len(roots), 2)
            for v in roots:
                self.assertAlmostEqual(min(v[0], v[2]), self.MOUTH, places=6)
                self.assertAlmostEqual(max(v[0], v[2]), self.END, places=6)

    def test_a_hole_that_runs_the_other_way_is_unchanged(self):
        # span[0] is the thread end whichever way the hole runs: the
        # fixture's own M6, drilled downward, keeps the line it had
        got = self.ends(records(tapped()))
        self.assertEqual(len(got), 0)      # that one is across x, not y
        across = [v for v, _pr in lines(records(tapped()))
                  if abs(v[1] - v[3]) < 1e-9
                  and abs(abs(v[2] - v[0]) - MAJOR) < 1e-6]
        self.assertAlmostEqual(across[0][1], THREAD_DEEP, places=6)

    def test_the_lock_refuses_an_end_outside_the_thread(self):
        part = {"threads": self.thread(end_at=1000.0)}
        bad = mf.thread_problems(part, "05", [60, 60, 60])
        self.assertTrue(bad)
        self.assertIn("outside the threaded span", " ".join(bad))

    def test_an_end_inside_the_span_locks(self):
        part = {"threads": self.thread(end_at=self.END)}
        self.assertEqual(mf.thread_problems(part, "05", [60, 60, 60]), [])


if __name__ == "__main__":
    unittest.main()
