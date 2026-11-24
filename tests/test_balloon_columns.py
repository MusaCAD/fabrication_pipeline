# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Pranay Kiran
"""Balloons on both sides of an assembly view.

Run with:  python3 -m unittest discover -s tests -v

One knee column holds about twenty balloons on a 290 mm sheet; a long
assembly with 26 part numbers needs around 360 mm and simply failed, one
`balloon-<pid>` at a time. The fan now splits in two, one column each side
of the view, each balloon on the side its own arrow is nearer — so no
leader crosses the view to reach a column, and each side is fanned in the
order its landings are SEEN from that column, so leaders do not cross
inside one either.

The fixture is that shape: a long horizontal assembly whose parts are
spread along it, with more part numbers than one column can hold.
"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import common  # noqa: E402
import draw  # noqa: E402
import musa_writer as mw  # noqa: E402

N = 26                      # part numbers, as the real float has
LEN, DIA = 800.0, 76.0      # a long, thin assembly


def seg(a, b):
    return {"t": "seg", "a": list(a), "b": list(b)}


def long_assembly(n=N):
    """n parts along a tube, each at its own height as a real assembly's
    are (a stack of discs, a rod, screws above and below the axis): part k
    owns its own slice, so every balloon has an edge of its own to land
    on."""
    vis, insts = [], []
    step = LEN / n
    for k in range(n):
        x0, x1 = k * step, (k + 1) * step
        # a part's own band of the section, varied so the landings are not
        # all at one height
        lo = -DIA / 2 + (k % 5) * DIA / 12
        hi = lo + DIA / 6
        edges = [seg((x0, lo), (x0, hi)), seg((x0, hi), (x1, hi)),
                 seg((x0, lo), (x1, lo))]
        vis += edges
        insts.append({"name": f"P{k}", "center": [(x0 + x1) / 2,
                                                  (lo + hi) / 2],
                      "edges": edges})
    return {"visible": vis, "hidden": [], "instances": insts,
            "bounds": [[0.0, -DIA / 2], [LEN, DIA / 2]]}


def asm_manifest(tmp: Path, n=N, scale="1:10"):
    parts = [{"part_id": f"{k + 1:02d}", "file": f"/m/p{k}.FCStd",
              "description": f"PART {k + 1}", "drg_no": f"FX-{k + 1:02d}",
              "material": "MS", "qty": 1} for k in range(n)]
    apath = tmp / "analysis.json"
    common.write_json(apath, {
        "parts": [], "assembly": {
            "instances": [{"name": f"P{k}", "path": f"P{k}",
                           "label": f"P{k}", "kind": "part",
                           "target_file": f"/m/p{k}.FCStd", "children": []}
                          for k in range(n)],
            "part_usage": {f"/m/p{k}.FCStd": {"qty": 1, "used_in": [f"P{k}"]}
                           for k in range(n)},
            "joints": [], "issues": [], "file": "", "document": "",
            "workbench": "Assembly", "bbox_min": [0, 0, 0],
            "bbox_max": [800, 76, 76]}})
    m = {"locked": True, "project": "FIXTURE", "drg_no_prefix": "FX",
         "drawn_by": "Er. P. PRANAY", "rev": "00", "model_dir": str(tmp),
         "assembly_file": "", "parts": parts,
         "assembly_sheet": {"enabled": True, "drg_no": "FX-00",
                            "description": "LONG ASSEMBLY", "scale": scale,
                            "front_view": "+Y", "balloons": True,
                            "front_extent_dims": False},
         "_analysis_path": str(apath)}
    return m


def balloon_circles(text):
    """(x, y, r) of every balloon circle drawn in annotation magenta."""
    out = []
    for line in text.splitlines():
        t = line.split()
        if t and t[0] == "CIRCLE" and " ".join(t[4:]) == mw.P_ANNO:
            out.append((float(t[1]), float(t[2]), float(t[3])))
    return out


def leaders(text):
    out, lines = [], text.splitlines()
    for i, line in enumerate(lines):
        t = line.split()
        if t and t[0] == "LEADER":
            out.append(((float(t[1]), float(t[2])),
                        (float(t[3]), float(t[4]))))
    return out


def crosses(a, b):
    """Do segments a and b properly cross?"""
    def side(p, q, r):
        return ((q[0] - p[0]) * (r[1] - p[1])
                - (q[1] - p[1]) * (r[0] - p[0]))
    d1, d2 = side(a[0], a[1], b[0]), side(a[0], a[1], b[1])
    d3, d4 = side(b[0], b[1], a[0]), side(b[0], b[1], a[1])
    return d1 * d2 < 0 and d3 * d4 < 0


class CapacityTest(unittest.TestCase):

    def test_a_column_holds_what_it_has_room_for(self):
        ctx = mw.SheetContext(1, 10)
        pitch = draw.balloon_pitch(ctx)
        self.assertEqual(draw.balloon_capacity(ctx, 10 * pitch, 0.0), 10)
        self.assertEqual(draw.balloon_capacity(ctx, 0.0, 0.0), 0)

    def test_a_split_fan_packs_closer(self):
        ctx = mw.SheetContext(1, 10)
        self.assertLess(draw.balloon_pitch(ctx, dense=True),
                        draw.balloon_pitch(ctx))
        self.assertGreater(draw.balloon_capacity(ctx, 1000.0, 0.0,
                                                 dense=True),
                           draw.balloon_capacity(ctx, 1000.0, 0.0))


class SideAssignmentTest(unittest.TestCase):

    def setUp(self):
        self.ctx = mw.SheetContext(1, 10)
        self.job = long_assembly()
        self.off = (0.0, 0.0)
        self.by_view = {"front": [(k, str(k + 1)) for k in range(N)]}
        self.columns = {"left": -100.0, "right": LEN + 100.0}

    def sides(self, capacity):
        return draw.balloon_sides(self.ctx, {"front": self.job},
                                  {"front": self.off}, self.by_view,
                                  capacity, self.columns)

    def test_one_column_while_it_fits(self):
        self.assertEqual(self.sides(N), {},
                         "no split while one column holds them all")
        self.assertEqual(self.sides(N + 5), {})

    def test_it_splits_when_one_column_cannot(self):
        sides = self.sides(N // 2 + 2)
        self.assertEqual(len(sides), N)
        self.assertEqual(set(sides.values()), {"left", "right"})

    def test_each_balloon_goes_to_the_side_its_part_is_nearer(self):
        sides = self.sides(N // 2 + 2)
        # the first parts are at the left end, the last at the right
        self.assertEqual(sides[("front", 0)], "left")
        self.assertEqual(sides[("front", N - 1)], "right")

    def test_neither_side_is_given_more_than_it_can_hold(self):
        cap = N // 2 + 1
        sides = self.sides(cap)
        per = {"left": 0, "right": 0}
        for v in sides.values():
            per[v] += 1
        self.assertLessEqual(max(per.values()), cap, per)

    def test_a_lopsided_assembly_is_rebalanced(self):
        """Every part at the left end: the ones nearest the middle move,
        because a column that cannot hold them all is no use."""
        job = long_assembly()
        for inst in job["instances"]:       # squeeze them all left
            inst["center"][0] = min(inst["center"][0], LEN * 0.2)
        sides = draw.balloon_sides(self.ctx, {"front": job},
                                   {"front": self.off}, self.by_view,
                                   N // 2, self.columns)
        per = {"left": 0, "right": 0}
        for v in sides.values():
            per[v] += 1
        self.assertLessEqual(max(per.values()), N // 2, per)


class TwoColumnSheetTest(unittest.TestCase):
    """The whole sheet: 26 numbers on a long assembly."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="fabpipe-balloons-")
        cls.m = asm_manifest(Path(cls.tmp.name))
        cls.built = draw.build_assembly_sheet(
            cls.m, {"assembly:front": long_assembly()})
        cls.ctx, _, cls.lay, cls.meta = cls.built[0]
        cls.text = cls.ctx.render()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_every_part_number_is_ballooned(self):
        self.assertEqual([u for u in self.lay["unresolved"]
                          if "balloon" in u], [])
        self.assertEqual(len(balloon_circles(self.text)), N)

    def test_it_used_two_columns(self):
        self.assertEqual(self.meta["balloons"]["columns"], 2)
        xs = sorted({round(c[0], 3) for c in balloon_circles(self.text)})
        # two distinct columns, one each side of the view
        self.assertEqual(len(xs), 2, xs)

    def test_one_balloon_per_part_number(self):
        """The balloon numbers are the annotation-magenta digits: the
        title block's own figures are black."""
        nums = []
        lines = self.text.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("MTEXT ") and i + 1 < len(lines) \
                    and lines[i + 1].isdigit() \
                    and line.endswith(mw.P_ANNO):
                nums.append(lines[i + 1])
        self.assertEqual(sorted(nums, key=int),
                         sorted((str(k + 1) for k in range(N)), key=int))

    def test_no_two_leaders_cross(self):
        ls = leaders(self.text)
        self.assertEqual(len(ls), N)
        bad = [(a, b) for i, a in enumerate(ls) for b in ls[i + 1:]
               if crosses(a, b)]
        self.assertEqual(bad, [], f"{len(bad)} crossing leaders")

    def test_the_sheet_says_how_it_ballooned(self):
        rep = self.meta["balloons"]
        self.assertEqual(rep["columns"], 2)
        self.assertGreater(rep["capacity_per_column"], 0)
        self.assertEqual(rep["not_shown"], [])


class OneColumnIsUnchangedTest(unittest.TestCase):
    """A sheet whose numbers fit one column keeps the single fan."""

    def test_a_small_assembly_uses_one_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            m = asm_manifest(Path(tmp), n=4)
            built = draw.build_assembly_sheet(
                m, {"assembly:front": long_assembly(4)})
            ctx, _, lay, meta = built[0]
            self.assertEqual(meta["balloons"]["columns"], 1)
            xs = {round(c[0], 3) for c in balloon_circles(ctx.render())}
            self.assertEqual(len(xs), 1, "one column, as before")
            self.assertEqual([u for u in lay["unresolved"]
                              if "balloon" in u], [])


class TooManyEvenForTwoTest(unittest.TestCase):
    """Fail clearly when two columns still cannot hold them."""

    def test_it_says_so_instead_of_dropping_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            # a scale that leaves almost no column: 120 numbers cannot fit
            m = asm_manifest(Path(tmp), n=120, scale="1:10")
            try:
                built = draw.build_assembly_sheet(
                    m, {"assembly:front": long_assembly(120)})
            except SystemExit:
                return                      # refused even earlier: also fine
            msg = " ".join(built[0][2]["unresolved"])
            self.assertIn("balloons:", msg)
            self.assertIn("column", msg)
            self.assertIn("room for", msg)


class UncrossTest(unittest.TestCase):
    """Settling a fan whose leaders would still cross (issue #18).

    The real failure: two balloons several knees apart crossed, with no
    crossing pair between them, so a sweep of NEIGHBOURS never reached
    them — and the sheet went out with two leaders pointing at each
    other's part."""

    # the right-hand column of the real 26-balloon sheet: where its
    # arrows land, and the fan they have to come out in. The pair that
    # crossed sat two knees apart, with a pair that did NOT cross
    # between them, so a sweep of neighbours never reached it.
    REAL_KX, REAL_START, REAL_PITCH = 1551.0, 1287.0, 61.5
    REAL_TIPS = [(1338, 966), (1339, 966), (1363, 966), (1427, 966),
                 (1433, 935), (1466, 950), (1335, 966), (1276, 966),
                 (1270, 966), (1209, 966), (1205, 966), (1092, 986),
                 (1117, 966), (1091, 986)]

    def test_it_settles_a_pair_that_are_not_neighbours(self):
        kx, start = self.REAL_KX, self.REAL_START
        pitch = self.REAL_PITCH
        tips = {i: (float(x), float(y))
                for i, (x, y) in enumerate(self.REAL_TIPS)}
        order = draw._uncross(list(tips), tips, kx, start, pitch)
        self.assertEqual(sorted(order), sorted(tips), "a balloon was lost")
        for i in range(len(order) - 1):
            for j in range(i + 1, len(order)):
                ka = (kx, start - i * pitch)
                kb = (kx, start - j * pitch)
                self.assertFalse(
                    draw._proper_crossing(ka, tips[order[i]], kb,
                                          tips[order[j]], 0.0),
                    f"{order[i]} still crosses {order[j]}")

    def test_an_order_that_does_not_cross_is_left_alone(self):
        kx, start, pitch = 100.0, 100.0, 10.0
        tips = {"a": (20.0, 90.0), "b": (20.0, 60.0), "c": (20.0, 30.0)}
        self.assertEqual(draw._uncross(["a", "b", "c"], tips, kx, start,
                                       pitch), ["a", "b", "c"])


if __name__ == "__main__":
    unittest.main()
