# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Pranay Kiran
"""Assembly sheets: section, detail views, balloons, grouped table (#5).

Run with:  python3 -m unittest discover -s tests -v

The slow tests draw a real two-level fixture assembly (tests/make_fixtures
.py: two sub-assemblies that share a part, their blocks touching in the
cut, a rod spanning both and a small screw) through the real composer.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

import common  # noqa: E402
import draw  # noqa: E402
import manifest as mf  # noqa: E402
import musa_writer as mw  # noqa: E402


def rect_loop(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


class HatchTest(unittest.TestCase):
    """ISO 128-50: neighbours differ, very thin cuts go solid."""

    def setUp(self):
        self.ctx = mw.SheetContext(1, 1)

    def test_touching_parts_get_different_styles(self):
        groups = [{"outer": rect_loop(0, 0, 10, 10), "instance": "A"},
                  {"outer": rect_loop(10, 0, 20, 10), "instance": "B"},
                  {"outer": rect_loop(20, 0, 30, 10), "instance": "C"}]
        styles = draw.hatch_styles_by_instance(groups, 0.5)
        self.assertNotEqual(styles["A"], styles["B"])
        self.assertNotEqual(styles["B"], styles["C"])

    def test_parts_that_do_not_touch_may_share_a_style(self):
        groups = [{"outer": rect_loop(0, 0, 10, 10), "instance": "A"},
                  {"outer": rect_loop(500, 0, 510, 10), "instance": "B"}]
        styles = draw.hatch_styles_by_instance(groups, 0.5)
        self.assertEqual(styles["A"], styles["B"])

    def test_the_assignment_is_stable(self):
        groups = [{"outer": rect_loop(0, 0, 10, 10), "instance": "A"},
                  {"outer": rect_loop(10, 0, 20, 10), "instance": "B"}]
        self.assertEqual(draw.hatch_styles_by_instance(groups, 0.5),
                         draw.hatch_styles_by_instance(groups, 0.5))

    def test_a_thin_cut_is_filled_solid(self):
        # 0.6 mm of material at 1:1 prints at 0.6 mm: hatch lines in that
        # band are a smear, so it is filled
        job = {"hatch_groups": [
            {"outer": rect_loop(0, 0, 40, 0.6), "instance": "SHIM"},
            {"outer": rect_loop(0, 10, 40, 30), "instance": "BLOCK"}]}
        recs, report = draw.assembly_hatches(self.ctx, job, (0.0, 0.0))
        self.assertEqual(report["solid_filled"], ["SHIM"])
        patterns = [r.splitlines()[1] for r in recs]
        self.assertEqual(patterns, ["SOLID", "ANSI31"])

    def test_no_section_no_hatch(self):
        self.assertEqual(draw.assembly_hatches(self.ctx, {}, (0, 0)),
                         ([], {}))


class BalloonPlanTest(unittest.TestCase):
    """One balloon per part number, in the view where it reads best."""

    def setUp(self):
        self.m = {"parts": [{"part_id": "01"}, {"part_id": "02"},
                            {"part_id": "03"}],
                  "assembly_sheet": {}}
        self.views = {
            "front": {"instances": [{"name": "A"}, {"name": "B"}]},
            "section": {"instances": [{"name": "A"}, {"name": "C"}]},
            "detail:B": {"instances": [{"name": "C"}]}}
        self.ids = {"A": "01", "B": "02", "C": "03"}

    def test_each_part_once(self):
        plan = draw.balloon_plan(self.m, self.views, self.ids)
        self.assertEqual(sorted(plan), ["01", "02", "03"])
        self.assertEqual(plan["01"][0], "front")
        self.assertEqual(plan["02"][0], "front")
        # 03 is not in the front view at all, so the section carries it
        self.assertEqual(plan["03"][0], "section")

    def test_a_detail_claims_the_small_parts_it_was_made_for(self):
        self.m["assembly_sheet"]["detail_views"] = [
            {"label": "B", "balloons": ["03"]}]
        plan = draw.balloon_plan(self.m, self.views, self.ids)
        self.assertEqual(plan["03"][0], "detail:B")

    def test_the_manifest_can_say_outright(self):
        self.m["assembly_sheet"]["balloon_in"] = {"01": "section"}
        plan = draw.balloon_plan(self.m, self.views, self.ids)
        self.assertEqual(plan["01"][0], "section")

    def test_a_part_no_view_shows_is_reported_missing(self):
        self.m["parts"].append({"part_id": "04"})
        plan = draw.balloon_plan(self.m, self.views, self.ids)
        self.assertNotIn("04", plan)


class PartsTableTest(unittest.TestCase):
    """Grouping, the fallbacks, and what the old sheets still get."""

    def setUp(self):
        self.m = {"parts": [
            {"part_id": "01", "file": "/m/plate.FCStd", "qty": 1,
             "description": "PLATE", "material": "MS", "drg_no": "X-01"},
            {"part_id": "02", "file": "/m/block.FCStd", "qty": 2,
             "description": "BLOCK", "material": "MS", "drg_no": "X-02"},
            {"part_id": "03", "file": "/m/screw.FCStd", "qty": 8,
             "description": "M4 X 12 SCREW", "material": "A2-70",
             "drg_no": "IS 1364-3", "sheet": False},
            {"part_id": "04", "file": "/m/screw2.FCStd", "qty": 4,
             "description": "M4 X 12 SCREW", "material": "A2-70",
             "drg_no": "IS 1364-3", "sheet": False},
            {"part_id": "05", "file": "/m/nut.FCStd", "qty": 8,
             "description": "M4 NUT", "material": "A2-70",
             "drg_no": "IS 1364-3", "sheet": False}]}
        self.analysis = {"assembly": {"part_usage": {
            "/m/plate.FCStd": {"qty": 1, "used_in": ["SUB_A/PLATE"]},
            "/m/block.FCStd": {"qty": 2, "used_in": ["SUB_A/BLOCK",
                                                     "SUB_B/BLOCK"]},
            "/m/screw.FCStd": {"qty": 8, "used_in": ["SCREW"] * 8},
            "/m/screw2.FCStd": {"qty": 4, "used_in": ["SCREW2"] * 4},
            "/m/nut.FCStd": {"qty": 8, "used_in": ["NUT"] * 8}},
            "instances": [
                {"name": "SUB_A", "path": "SUB_A", "label": "LEFT HOUSING",
                 "kind": "assembly", "children": []},
                {"name": "SUB_B", "path": "SUB_B", "label": "RIGHT HOUSING",
                 "kind": "assembly", "children": []}]}}

    def plan(self, **kw):
        return draw.parts_table_plan(self.m, self.analysis,
                                     kw.pop("max_rows", 50), **kw)

    def test_the_default_is_the_old_flat_list(self):
        rows, report = self.plan()
        self.assertEqual([r["kind"] for r in rows], ["part"] * 5)
        self.assertEqual([r["part"]["part_id"] for r in rows],
                         ["01", "02", "03", "04", "05"])
        self.assertEqual(report["fallbacks"], [])
        self.assertEqual(report["sheets"], 1)

    def test_grouping_by_sub_assembly(self):
        rows, _ = self.plan(group_by="sub_assembly")
        kinds = [(r["kind"], r.get("text") or
                  (r.get("part") or {}).get("part_id") or "") for r in rows]
        self.assertIn(("head", "LEFT HOUSING"), kinds)
        heads = {r["text"]: r["qty"] for r in rows if r["kind"] == "head"}
        # a heading states the SUB-ASSEMBLY's own quantity — it is used
        # once — not the number of parts or instances under it (issue #9)
        self.assertEqual(heads["LEFT HOUSING"], 1)
        # the block is used by BOTH housings, so it is listed once, in the
        # common section: a row under each would count it twice. That
        # leaves the right housing nothing of its own to head.
        self.assertIn(("head", draw.COMMON_GROUP), kinds)
        self.assertIn(("part", "02"), kinds)
        self.assertIsNone(heads[draw.COMMON_GROUP])
        self.assertEqual([k for k in kinds if k[0] == "part"].count(
            ("part", "02")), 1)
        total = [r for r in rows if r["kind"] == "total"]
        self.assertEqual(total[0]["qty"], 23)      # 1 + 2 + 8 + 4 + 8

    def test_every_part_number_appears_once(self):
        rows, _ = self.plan(group_by="sub_assembly")
        nums = []
        for r in rows:
            if r["kind"] == "part":
                nums.append(r["part"]["part_id"])
            elif r["kind"] == "group":
                nums += [p["part_id"] for p in r["parts"]]
        self.assertEqual(sorted(nums), ["01", "02", "03", "04", "05"])

    def test_quantities_come_from_part_usage_once(self):
        rows, _ = self.plan(group_by="sub_assembly")
        qty = {r["part"]["part_id"]: r["qty"] for r in rows
               if r["kind"] == "part"}
        usage = self.analysis["assembly"]["part_usage"]
        for part in self.m["parts"]:
            self.assertEqual(qty[part["part_id"]],
                             usage[part["file"]]["qty"])

    def test_fallback_one_groups_the_hardware(self):
        # five rows do not fit four, but the two M4 screws are one kind:
        # grouped, the table is plate, block, screws, nuts
        rows, report = self.plan(max_rows=4)
        self.assertTrue(any("grouped bought-out hardware" in f
                            for f in report["fallbacks"]), report)
        groups = [r for r in rows if r["kind"] == "group"]
        # the two M4 screws are one kind and share a row; the nut is the
        # only one of its kind, so it keeps its own row rather than
        # becoming a group of one reading "1 ITEMS" (issue #9)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["qty"], 12)     # 8 + 4 of one kind
        self.assertEqual([r["part"]["part_id"] for r in rows
                          if r["kind"] == "part"], ["01", "02", "05"])
        self.assertEqual(report["sheets"], 1)

    def test_fallback_two_is_a_continuation_sheet_and_is_reported(self):
        rows, report = self.plan(max_rows=2)
        self.assertTrue(any("grouped bought-out hardware" in f
                            for f in report["fallbacks"]), report)
        self.assertEqual(report["sheets"], 2)
        self.assertTrue(any("continuation sheet" in f
                            for f in report["fallbacks"]), report)
        self.assertIn("rows of", report["fallbacks"][-1])

    def test_a_grouped_hardware_row_names_its_items(self):
        rows, _ = self.plan(max_rows=4)
        self.assertTrue(any(r["kind"] == "group" for r in rows))
        row = next(r for r in rows if r["kind"] == "group")
        cells = draw.parts_row_cells(row, {"parts": []}, self.m)
        self.assertEqual(cells["qty"], "12")       # 8 + 4 of one kind
        self.assertIn("ITEMS", cells["remarks"])
        self.assertTrue(cells["pno"])

    def test_text_is_never_shrunk_below_the_template_floor(self):
        # the floor lives in musa_writer and reports instead of shrinking
        self.assertGreater(mw.PARTS_TEXT_FLOOR, 0.5)
        ctx = mw.SheetContext(1, 1)
        overflow = []
        mw.parts_row_records(ctx, 0, {"description": "X" * 400},
                             overflow=overflow)
        self.assertTrue(overflow)


class AssemblyManifestTest(unittest.TestCase):
    """What /finalise refuses to lock on an assembly sheet."""

    def sheet(self, **over):
        asm = {"enabled": True, "description": "ASSY", "drg_no": "X-00",
               "front_view": "+Y", "scale": "1:2", "balloons": True,
               "section": {"enabled": True, "projection": "below",
                           "label": "A", "not_sectioned": ["02"]},
               "detail_views": [{"label": "B", "scale": "2:1",
                                 "of": "section", "center": [0.0, 0.0],
                                 "radius": 6.0, "balloons": ["02"]}]}
        asm.update(over)
        return {"parts": [{"part_id": "01"}, {"part_id": "02"}],
                "assembly_sheet": asm}

    def test_a_good_one_passes(self):
        self.assertEqual(mf.assembly_sheet_problems(self.sheet()), [])

    def test_no_assembly_sheet_no_problems(self):
        self.assertEqual(mf.assembly_sheet_problems({}), [])
        self.assertEqual(mf.assembly_sheet_problems(
            {"assembly_sheet": {"enabled": False}}), [])

    def test_a_section_needs_an_orthographic_view(self):
        bad = self.sheet(view="iso")
        self.assertIn("needs an orthographic front view",
                      " ".join(mf.assembly_sheet_problems(bad)))

    def test_not_sectioned_must_name_real_parts(self):
        s = self.sheet()
        s["assembly_sheet"]["section"]["not_sectioned"] = ["09"]
        self.assertIn("names no such part",
                      " ".join(mf.assembly_sheet_problems(s)))

    def test_a_detail_of_a_view_the_sheet_has_not_got(self):
        s = self.sheet()
        s["assembly_sheet"]["section"] = {"enabled": False}
        self.assertIn("is not a view on this sheet",
                      " ".join(mf.assembly_sheet_problems(s)))

    def test_two_details_may_not_share_a_letter(self):
        s = self.sheet()
        s["assembly_sheet"]["detail_views"].append(
            dict(s["assembly_sheet"]["detail_views"][0]))
        self.assertIn("already used on this sheet",
                      " ".join(mf.assembly_sheet_problems(s)))

    def test_a_detail_must_enlarge(self):
        s = self.sheet()
        s["assembly_sheet"]["detail_views"][0]["scale"] = "1:5"
        self.assertIn("does not enlarge",
                      " ".join(mf.assembly_sheet_problems(s)))

    def test_balloons_and_balloon_in_must_name_real_parts_and_views(self):
        s = self.sheet()
        s["assembly_sheet"]["detail_views"][0]["balloons"] = ["77"]
        s["assembly_sheet"]["balloon_in"] = {"01": "detail:Z"}
        msgs = " ".join(mf.assembly_sheet_problems(s))
        self.assertIn("balloons name no such part", msgs)
        self.assertIn("is not a view on this sheet", msgs)

    def test_group_by_must_be_one_we_know(self):
        s = self.sheet(parts_table={"group_by": "colour"})
        self.assertIn("parts_table.group_by",
                      " ".join(mf.assembly_sheet_problems(s)))


@unittest.skipUnless(common.FREECAD_APPIMAGE.exists(),
                     f"FreeCAD not at {common.FREECAD_APPIMAGE}")
class RealAssemblySheetTest(unittest.TestCase):
    """The whole sheet, drawn from a real two-level fixture."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="fabpipe-asmsheet-")
        d = Path(cls.tmp.name)
        cls.dir = d
        common.run_freecad(ROOT / "tests" / "make_fixtures.py", str(d),
                           timeout=900)
        ana = d / "asm_sheet.json"
        common.run_freecad(SRC / "fc_analyse.py", str(d), "asm_sheet.FCStd",
                           str(ana), timeout=900)
        cls.analysis = json.loads(ana.read_text())
        cls.m = cls.manifest(d, ana)
        draw.SCRATCH_DIR = d / "work"
        cls.proj = draw.run_projections(cls.m, cls.analysis)
        sec = cls.proj["assembly:section"]
        screw = next(i for i in sec["instances"] if i["name"] == "SCREW")
        cls.m["assembly_sheet"]["detail_views"] = [{
            "label": "B", "scale": "2:1", "of": "section",
            "center": [(screw["bbox"][0] + screw["bbox"][2]) / 2,
                       (screw["bbox"][1] + screw["bbox"][3]) / 2],
            "radius": 6.0, "balloons": ["05"]}]
        cls.built = draw.build_assembly_sheet(cls.m, cls.proj)
        cls.ctx, _, cls.layout, cls.meta = cls.built[0]
        cls.text = cls.ctx.render()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @staticmethod
    def manifest(d, ana):
        def part(pid, stem, desc, qty, **kw):
            p = {"part_id": pid, "file": str(d / f"{stem}.FCStd"),
                 "description": desc, "drg_no": f"FX-{pid}",
                 "material": "IS 2062 E250", "qty": qty, "front_view": "+Z",
                 "scale": "1:1", "section": {"enabled": False}}
            p.update(kw)
            return p
        return {"locked": True, "project": "FIXTURE", "drg_no_prefix": "FX",
                "rev": "00", "model_dir": str(d),
                "assembly_file": str(d / "asm_sheet.FCStd"),
                "_analysis_path": str(ana),
                "parts": [part("01", "plate", "BASE PLATE", 1),
                          part("02", "block", "BLOCK", 2),
                          part("03", "shim", "SHIM", 1),
                          part("04", "rod", "GUIDE ROD", 1),
                          part("05", "screw", "M4 SCREW", 1, sheet=False,
                               drg_no="IS 1364-3")],
                "assembly_sheet": {
                    "enabled": True, "description": "FIXTURE ASSEMBLY",
                    "drg_no": "FX-00", "front_view": "+Y", "scale": "1:2",
                    "balloons": True,
                    "section": {"enabled": True, "projection": "below",
                                "offset_frac": 0.625, "label": "A",
                                "not_sectioned": ["04", "05"]},
                    "parts_table": {"group_by": "sub_assembly"}}}

    def test_the_sheet_places_everything(self):
        self.assertEqual(self.layout["unresolved"], [])
        self.assertTrue(self.meta["section"])
        self.assertEqual(self.meta["details"], ["B"])
        self.assertEqual(len(self.built), 1)

    def test_each_part_is_ballooned_exactly_once(self):
        plan = self.meta["balloons"]["ballooned"]
        self.assertEqual(sorted(plan), ["01", "02", "03", "04", "05"])
        self.assertEqual(self.meta["balloons"]["not_shown"], [])
        # one balloon leader per part, whatever the quantities
        leaders = [l for l in self.text.splitlines()
                   if l.startswith("LEADER ")]
        self.assertEqual(len(leaders), 5)

    def test_the_small_screw_is_ballooned_in_the_detail(self):
        self.assertEqual(self.meta["balloons"]["ballooned"]["05"], "detail:B")

    def test_the_rod_and_the_screw_are_not_hatched(self):
        hatched = set(self.meta["hatch"]["hatch_styles"])
        self.assertNotIn("ROD", hatched)
        self.assertNotIn("SCREW", hatched)
        self.assertTrue(hatched, "nothing was hatched at all")

    def test_touching_parts_are_hatched_differently(self):
        styles = self.meta["hatch"]["hatch_styles"]
        # the two blocks meet at x = 20 in the cut
        self.assertNotEqual(styles["SUB_A/LBLOCK"], styles["SUB_B/RBLOCK"])

    def test_the_table_is_grouped_with_quantities_per_group_and_total(self):
        rows, _ = draw.parts_table_plan(self.m, self.analysis, 50,
                                        group_by="sub_assembly")
        heads = {r["text"]: r["qty"] for r in rows if r["kind"] == "head"}
        # each sub-assembly is used once, so that is what its heading says
        # (it used to say how many parts were under it — issue #9)
        self.assertEqual(heads["SUB_A"], 1)
        self.assertEqual(heads["SUB_B"], 1)
        self.assertEqual([r for r in rows if r["kind"] == "total"][0]["qty"],
                         6)
        # and the total is the sum of the rows, with nothing counted twice
        self.assertEqual(sum(r.get("qty") or 0 for r in rows
                             if r["kind"] in ("part", "group")), 6)
        # the block is one part file used by both sub-assemblies
        self.assertEqual(
            self.analysis["assembly"]["part_usage"][
                str(self.dir / "block.FCStd")]["qty"], 2)

    def test_the_section_caption_and_the_detail_caption_are_drawn(self):
        self.assertIn("SECTION A-A", self.text)
        self.assertIn("DETAIL B (2:1)", self.text)

    def test_a_table_too_long_falls_back_and_says_so(self):
        rows, report = draw.parts_table_plan(self.m, self.analysis, 3,
                                             group_by="sub_assembly")
        self.assertTrue(report["fallbacks"])
        self.assertEqual(report["sheets"], 2)

    def test_a_table_that_will_not_fit_moves_onto_a_second_sheet(self):
        # squeeze the band the table may use until the rows do not fit
        saved = draw.TABLE_AREA_FRAC
        draw.TABLE_AREA_FRAC = 0.04
        try:
            built = draw.build_assembly_sheet(self.m, self.proj)
        finally:
            draw.TABLE_AREA_FRAC = saved
        self.assertEqual(len(built), 2)
        report = built[0][3]["parts_table"]
        self.assertEqual(report["sheets"], 2)
        self.assertTrue(any("continuation sheet" in f
                            for f in report["fallbacks"]), report)
        first, second = built[0][0].render(), built[1][0].render()
        self.assertIn("SHEET 1 OF 2", first)
        self.assertIn("SHEET 2 OF 2", second)
        self.assertIn("CONTINUED FROM SHEET 1", second)
        self.assertEqual(built[1][2]["unresolved"], [])
        # every row still reaches a sheet
        rows = len([r for r in draw.parts_table_plan(
            self.m, self.analysis, 2, group_by="sub_assembly")[0]])
        self.assertGreater(rows, 0)

    def test_it_plots(self):
        if not common.MUSA_PLOT.exists():
            self.skipTest("musa_plot not built")
        out = self.dir / "asm.musa"
        out.write_text(self.text)
        info = common.run_musa_plot(out, self.dir / "asm.pdf", scale=(1, 2),
                                    window=self.ctx.window)
        self.assertAlmostEqual(float(info["mm_per_unit"]), 0.5, places=6)


    def test_a_detail_can_be_drawn_on_the_continuation_sheet(self):
        """Issue #19: sheet 1 carries the views and the section, sheet 2
        the details with their own balloons and the rest of the table."""
        m = json.loads(json.dumps(self.m))
        m["_analysis_path"] = self.m["_analysis_path"]
        m["assembly_sheet"]["detail_views"] = [
            dict(self.m["assembly_sheet"]["detail_views"][0], sheet=2)]
        built = draw.build_assembly_sheet(m, self.proj)
        self.assertEqual(len(built), 2, "no continuation sheet was made")
        one, two = built[0][0].render(), built[1][0].render()
        self.assertEqual(built[0][2]["unresolved"], [])
        self.assertEqual(built[1][2]["unresolved"], [])
        # the region is marked on the view it comes from, on sheet 1, and
        # the letter says where to look
        self.assertIn("B (SH 2)", one)
        self.assertNotIn("DETAIL B (2:1)", one)
        # the enlargement itself, captioned, is on sheet 2
        self.assertIn("DETAIL B (2:1)", two)
        self.assertEqual(built[1][3]["details"], ["B"])

    def test_its_balloons_go_with_it(self):
        m = json.loads(json.dumps(self.m))
        m["_analysis_path"] = self.m["_analysis_path"]
        m["assembly_sheet"]["detail_views"] = [
            dict(self.m["assembly_sheet"]["detail_views"][0], sheet=2)]
        built = draw.build_assembly_sheet(m, self.proj)
        one, two = built[0][0].render(), built[1][0].render()
        # the screw is ballooned in the detail, which is now on sheet 2:
        # one balloon per P.NO. on the DRAWING, not per sheet
        self.assertEqual(built[0][3]["balloons"]["ballooned"]["05"],
                         "detail:B")
        self.assertEqual(built[0][3]["balloons"]["not_shown"], [])
        n_one = sum(1 for l in one.splitlines() if l.startswith("LEADER "))
        n_two = sum(1 for l in two.splitlines() if l.startswith("LEADER "))
        self.assertEqual((n_one, n_two), (4, 1))

    def test_two_details_on_sheet_2_do_not_leader_across_each_other(self):
        """Issue #20: side by side they share one balloon column, and
        every balloon of the far one reaches it across the near one."""
        import layout_check as lc
        m = json.loads(json.dumps(self.m))
        m["_analysis_path"] = self.m["_analysis_path"]
        base = self.m["assembly_sheet"]["detail_views"][0]
        sec = self.proj["assembly:section"]
        rod = next(i for i in sec["instances"] if i["name"] == "ROD")
        m["assembly_sheet"]["detail_views"] = [
            dict(base, sheet=2),
            {"label": "C", "scale": "2:1", "of": "section", "sheet": 2,
             "center": [(rod["bbox"][0] + rod["bbox"][2]) / 2,
                        (rod["bbox"][1] + rod["bbox"][3]) / 2],
             "radius": 6.0, "balloons": ["04"]}]
        built = draw.build_assembly_sheet(m, proj=self.proj)
        self.assertEqual(len(built), 2)
        ctx, _r, lay, meta = built[1]
        self.assertEqual(lay["unresolved"], [])
        self.assertEqual(sorted(meta["details"]), ["B", "C"])
        box = getattr(ctx, "_pictorial_box", None)
        found = lc.check_sheet(ctx.render(), ctx.draw_area,
                               ctx.num / ctx.den,
                               table_band=getattr(ctx, "_table_band", None),
                               boxes=[box] if box else ())
        self.assertEqual([f for f in found
                          if f["kind"] in ("leader-across-view",
                                           "leader-crossing")], [])

    def test_the_detail_still_fails_loudly_when_it_cannot_be_drawn(self):
        m = json.loads(json.dumps(self.m))
        m["_analysis_path"] = self.m["_analysis_path"]
        m["assembly_sheet"]["detail_views"] = [
            dict(self.m["assembly_sheet"]["detail_views"][0],
                 sheet=2, radius=400.0)]
        built = draw.build_assembly_sheet(m, self.proj)
        self.assertTrue(any("detail" in u for u in built[-1][2]["unresolved"]
                            + built[0][2]["unresolved"]))


class DetailSheetNumberTest(unittest.TestCase):
    def test_it_defaults_to_the_first_sheet(self):
        self.assertEqual(draw.detail_sheet({}), 1)
        self.assertEqual(draw.detail_sheet({"sheet": 2}), 2)
        self.assertEqual(draw.detail_sheet({"sheet": "x"}), 1)
        self.assertEqual(draw.detail_sheet({"sheet": 0}), 1)


if __name__ == "__main__":
    unittest.main()
