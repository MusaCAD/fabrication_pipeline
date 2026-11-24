# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Pranay Kiran
"""Quantities in a grouped assembly parts table (issue #9).

Run with:  python3 -m unittest discover -s tests -v

The real failure: 26 part numbers in five rigid sub-assemblies, grouped by
sub-assembly with the hardware fallback. An O-ring used 4 times inside one
sub-assembly printed P.NO. "17, 17, 17, 17" with QTY 16; a nut used 6
times across two printed "18, 18" with QTY 12; and each heading row showed
21 and 22 — the number of rows under it — where the sub-assembly itself is
used once. Quantities come from part_usage, once, per group and in total.

The fixture is that shape: a part used four times inside one sub-assembly,
and another used six times across two.
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

HULL, SLED = "HullAssembly", "SledAssembly"


def manifest():
    return {"locked": True, "project": "FIXTURE", "rev": "00",
            "parts": [
                {"part_id": "02", "file": "/m/plug.FCStd", "qty": 1,
                 "description": "HEAD PLUG", "material": "POM-C",
                 "drg_no": "FX-02"},
                {"part_id": "17", "file": "/m/oring.FCStd", "qty": 4,
                 "description": "O-RING 2-146 NBR 70", "material": "NBR",
                 "drg_no": "AS568 2-146", "sheet": False},
                {"part_id": "18", "file": "/m/nut.FCStd", "qty": 6,
                 "description": "M6 NUT", "material": "A2-70",
                 "drg_no": "ISO 4032", "sheet": False},
                {"part_id": "15", "file": "/m/csk10.FCStd", "qty": 2,
                 "description": "M4 X 10 CSK SCREW", "material": "A2-70",
                 "drg_no": "ISO 10642", "sheet": False},
                {"part_id": "16", "file": "/m/csk25.FCStd", "qty": 2,
                 "description": "M4 X 10 CSK SCREW", "material": "A2-70",
                 "drg_no": "ISO 10642", "sheet": False}],
            "assembly_sheet": {"enabled": True, "drg_no": "FX-00",
                               "description": "FIXTURE ASSEMBLY",
                               "scale": "1:2", "front_view": "+Y",
                               "balloons": False,
                               "parts_table": {"group_by": "sub_assembly"}}}


def analysis():
    return {"assembly": {
        "part_usage": {
            "/m/plug.FCStd": {"qty": 1, "used_in": [f"{HULL}/HeadPlug"]},
            # four of one kind, all inside ONE sub-assembly
            "/m/oring.FCStd": {"qty": 4,
                               "used_in": [f"{HULL}/ORing{i}"
                                           for i in range(1, 5)]},
            # six across TWO sub-assemblies
            "/m/nut.FCStd": {"qty": 6,
                             "used_in": [f"{HULL}/Nut{i}" for i in (1, 2)]
                             + [f"{SLED}/Nut{i}" for i in (1, 2, 3, 4)]},
            "/m/csk10.FCStd": {"qty": 2,
                               "used_in": [f"{SLED}/Csk10{i}"
                                           for i in (1, 2)]},
            "/m/csk25.FCStd": {"qty": 2,
                               "used_in": [f"{SLED}/Csk25{i}"
                                           for i in (1, 2)]}},
        "instances": [
            {"name": HULL, "path": HULL, "label": HULL, "kind": "assembly",
             "children": []},
            {"name": SLED, "path": SLED, "label": SLED, "kind": "assembly",
             "children": []}],
        "file": "", "document": "", "workbench": "Assembly", "joints": [],
        "issues": [], "bbox_min": [0, 0, 0], "bbox_max": [100, 60, 40]},
        "parts": []}


def rows_of(**kw):
    kw.setdefault("group_by", "sub_assembly")
    kw.setdefault("max_rows", 50)
    return draw.parts_table_plan(manifest(), analysis(),
                                 kw.pop("max_rows"), **kw)


def cells(rows):
    return [draw.parts_row_cells(r, analysis(), manifest()) for r in rows]


class GroupedQuantitiesTest(unittest.TestCase):

    def test_a_heading_states_the_sub_assembly_s_own_quantity(self):
        rows, _ = rows_of()
        heads = {r["text"]: r["qty"] for r in rows if r["kind"] == "head"}
        self.assertEqual(heads[HULL], 1)
        self.assertEqual(heads[SLED], 1)

    def test_a_heading_carries_no_item_number(self):
        rows, _ = rows_of()
        for c in cells([r for r in rows if r["kind"] == "head"]):
            self.assertEqual(c["pno"], "")

    def test_four_of_one_kind_in_one_sub_assembly_is_one_row_of_four(self):
        rows, _ = rows_of()
        oring = [r for r in rows if r["kind"] == "part"
                 and r["part"]["part_id"] == "17"]
        self.assertEqual(len(oring), 1, "one row per P.NO.")
        self.assertEqual(oring[0]["qty"], 4)
        self.assertEqual(draw.parts_row_cells(oring[0], analysis(),
                                              manifest())["pno"], "17")

    def test_six_across_two_sub_assemblies_is_one_row_of_six(self):
        rows, _ = rows_of()
        nut = [r for r in rows if r["kind"] == "part"
               and r["part"]["part_id"] == "18"]
        self.assertEqual(len(nut), 1)
        self.assertEqual(nut[0]["qty"], 6)
        # and it is not claimed by either sub-assembly on its own
        heads = [r["text"] for r in rows if r["kind"] == "head"]
        self.assertIn(draw.COMMON_GROUP, heads)

    def test_every_part_number_appears_exactly_once(self):
        rows, _ = rows_of()
        nums = []
        for r in rows:
            if r["kind"] == "part":
                nums.append(r["part"]["part_id"])
            elif r["kind"] == "group":
                nums += [p["part_id"] for p in r["parts"]]
        self.assertEqual(sorted(nums), ["02", "15", "16", "17", "18"])

    def test_the_total_is_the_sum_of_part_usage(self):
        rows, _ = rows_of()
        total = [r for r in rows if r["kind"] == "total"][0]
        self.assertEqual(total["qty"], 1 + 4 + 6 + 2 + 2)

    def test_the_hardware_fallback_does_not_multiply_anything(self):
        rows, _ = rows_of(group_hardware=True)
        per = {}
        for r in rows:
            if r["kind"] == "part":
                per[r["part"]["part_id"]] = r["qty"]
            elif r["kind"] == "group":
                for p in r["parts"]:
                    per.setdefault(p["part_id"], 0)
        self.assertEqual(per["17"], 4)
        self.assertEqual(per["18"], 6)
        total = [r for r in rows if r["kind"] == "total"][0]
        self.assertEqual(total["qty"], 15)
        # the two csk screws are one kind: one row, both numbers, qty 4
        groups = [r for r in rows if r["kind"] == "group"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["qty"], 4)
        c = draw.parts_row_cells(groups[0], analysis(), manifest())
        self.assertEqual(c["pno"], "15, 16")
        self.assertEqual(c["remarks"], "2 ITEMS")

    def test_no_row_ever_reads_one_items(self):
        for kw in ({}, {"group_hardware": True}):
            rows, _ = rows_of(**kw)
            for c in cells(rows):
                self.assertNotEqual(c["remarks"], "1 ITEMS")
                # and no P.NO. is ever repeated inside one cell
                nums = [n.strip() for n in c["pno"].split(",") if n.strip()]
                self.assertEqual(len(nums), len(set(nums)), c)

    def test_a_group_row_lists_each_number_once_whatever_it_holds(self):
        """Belt and braces for the cell itself: even handed a row that
        names one part per instance, the P.NO. cell reads "17", not
        "17, 17, 17, 17" (issue #9)."""
        parts = [p for p in manifest()["parts"]
                 if p["part_id"] in ("17", "18")]
        row = {"kind": "group", "parts": parts * 3, "qty": 10}
        c = draw.parts_row_cells(row, analysis(), manifest())
        self.assertEqual(c["pno"], "17, 18")
        self.assertEqual(c["remarks"], "2 ITEMS")
        self.assertEqual(c["qty"], "10")

    def test_an_analysis_without_part_usage_keeps_the_manifest_count(self):
        old = {"assembly": {"part_usage": {}, "instances": []}, "parts": []}
        rows, _ = draw.parts_table_plan(manifest(), old, 50)
        qty = {r["part"]["part_id"]: r["qty"] for r in rows
               if r["kind"] == "part"}
        self.assertEqual(qty["17"], 4)
        self.assertEqual(qty["18"], 6)


class LockChecksQuantitiesTest(unittest.TestCase):
    """The table counts from part_usage, so a manifest quantity that has
    drifted from the model is caught at the lock, not printed."""

    def problems(self, m):
        import manifest as mf
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            common.write_json(tmp / "analysis.json", analysis())
            old = mf.REPORTS_DIR
            mf.REPORTS_DIR = tmp
            try:
                return mf.quantity_problems(m)
            finally:
                mf.REPORTS_DIR = old

    def test_a_manifest_that_matches_the_model_locks(self):
        self.assertEqual(self.problems(manifest()), [])

    def test_a_stale_quantity_is_reported(self):
        m = manifest()
        m["parts"][1]["qty"] = 2          # the O-ring is used 4 times
        out = self.problems(m)
        self.assertEqual(len(out), 1, out)
        self.assertIn("uses it 4 times", out[0])


class StampClearsTheTableTest(unittest.TestCase):
    """The pictorial box was placed before the table's rows were claimed,
    so its frame could be drawn straight across them (issue #9)."""

    def job(self):
        pts = [(0.0, 0.0), (120.0, 0.0), (120.0, 60.0), (0.0, 60.0)]
        return {"visible": [{"t": "seg", "a": list(a), "b": list(b)}
                            for a, b in zip(pts, pts[1:] + pts[:1])],
                "hidden": [], "bounds": [[0.0, 0.0], [120.0, 60.0]]}

    def test_the_rows_are_claimed_before_the_box_is_placed(self):
        """The order IS the bug: with the rows unclaimed and no floor, the
        box slides into the bottom band, which is the table."""
        seen = {}
        real = draw.pictorial_box

        def spy(ctx, layout, png=None, zones=(), floor=None):
            seen["floor"] = floor
            seen["claimed"] = [c[4] for c in layout.claims]
            return real(ctx, layout, png, zones, floor)

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            apath = tmp / "analysis.json"
            common.write_json(apath, analysis())
            m = manifest()
            m["_analysis_path"] = str(apath)
            draw.pictorial_box = spy
            try:
                built = draw.build_assembly_sheet(
                    m, {"assembly:front": self.job()})
            finally:
                draw.pictorial_box = real
        self.assertIn("parts-rows", seen["claimed"],
                      "the table was not claimed before the box was placed")
        rows = len(rows_of()[0])
        ctx2 = mw.SheetContext(1, 2)
        self.assertAlmostEqual(
            seen["floor"],
            ctx2.draw_area[1] + mw.PARTS_ROW_H * ctx2.t * rows, places=6)
        self.assertEqual(built[0][2]["unresolved"], [])

    def test_the_box_keeps_off_the_table_when_the_top_band_is_full(self):
        ctx = mw.SheetContext(1, 2)
        layout = draw.Layout(ctx)
        ax0, ay0, ax1, ay1 = ctx.draw_area
        rows_h = mw.PARTS_ROW_H * ctx.t * 9
        floor = ay0 + rows_h
        layout.claim((ax0, ay0, ax1, floor), "parts-rows", "reserved")
        # a view filling everything above the table: the box has to come
        # down, and the only clear band left is the table's
        zones = [(ax0, floor + ctx.mm(2.0), ax1, ay1)]
        recs, stamp = draw.pictorial_box(ctx, layout, None, zones=zones,
                                         floor=floor)
        self.assertGreaterEqual(stamp[1], floor,
                                "the pictorial box sits on the table")
        for r in recs:
            ys = [float(v) for v in r.split()[3:11][1::2]]
            self.assertGreaterEqual(min(ys), floor,
                                    "the box FRAME crosses the table")


if __name__ == "__main__":
    unittest.main()
