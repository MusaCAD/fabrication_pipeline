# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Pranay Kiran
"""The assembly sheet's overall dimensions, and the balloons that have to
live beside them (issue #30).

Run with:  python3 -m unittest discover -s tests -v

The real sheet: an A3-landscape assembly at 1:5, a side view with the
section below it, 26 balloons in two columns, the shaded pictorial top
right. The overall LENGTH dimension went above the side view and the
height dimension to its left — which is where both balloon columns are —
so seven balloon leaders crossed a dimension line, three crossed the
cutting plane's sight arrows, and three more passed UNDER the pictorial,
whose stamped image is opaque: those parts of them are not printed at
all. Every finding read "the leader of 'balloon' crosses a dimension
line", so a waiver could not name the one crossing a reviewer accepted,
and `front_extent_dims: false` — dropping the dimensions a test facility
asks for — was the only lever the manifest had.

The numbers in these fixtures are measured off that sheet.
"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import draw  # noqa: E402
import layout_check as lc  # noqa: E402
import manifest as mf  # noqa: E402
import musa_writer as mw  # noqa: E402
import verify  # noqa: E402
from test_balloon_columns import (N, LEN, asm_manifest,  # noqa: E402
                                  long_assembly, leaders)

A = mw.P_ANNO
BIG = (0.0, 0.0, 3000.0, 2000.0)


def sheet(*recs):
    return "MUSACAD 14\n" + "\n".join(recs) + "\nEND\n"


def of_kind(found, kind):
    return [f for f in found if f["kind"] == kind]


# ------------------------------------------- what the manifest may say

class ExtentDimsSpecTest(unittest.TestCase):
    """`extent_dims` on the assembly sheet: which view carries the overall
    dimensions, and on which side of it."""

    def spec(self, **asm):
        return draw.extent_dims_spec(asm)

    def test_the_default_is_the_old_sheet_exactly(self):
        got = self.spec()
        self.assertEqual(got, {"view": "front", "which": ("w", "h"),
                               "width_side": "top", "height_side": "left"})

    def test_the_section_takes_the_outer_side_by_default(self):
        """The section is drawn below the front view, so the gap above it
        is the front view's and the room below it is free."""
        self.assertEqual(self.spec(extent_dims="section"),
                         {"view": "section", "which": ("w", "h"),
                          "width_side": "bottom", "height_side": "left"})

    def test_none_means_none(self):
        self.assertIsNone(self.spec(extent_dims="none"))

    def test_the_old_switch_still_turns_them_off(self):
        self.assertIsNone(self.spec(front_extent_dims=False))
        self.assertIsNone(self.spec(front_extent_dims=False,
                                    extent_dims="section"))

    def test_the_long_form_says_which_and_where(self):
        self.assertEqual(
            self.spec(extent_dims={"view": "section", "which": ["w"],
                                   "width_side": "top"}),
            {"view": "section", "which": ("w",), "width_side": "top",
             "height_side": "left"})

    def test_a_side_may_be_named_for_the_front_view_too(self):
        self.assertEqual(
            self.spec(extent_dims={"height_side": "right"}),
            {"view": "front", "which": ("w", "h"), "width_side": "top",
             "height_side": "right"})


class ExtentDimsLockTest(unittest.TestCase):
    """What /finalise refuses to lock."""

    def problems(self, extent_dims, section=True):
        asm = {"enabled": True, "description": "ASSY", "drg_no": "X-00",
               "front_view": "+Y", "scale": "1:2", "balloons": True,
               "extent_dims": extent_dims,
               "section": {"enabled": section, "projection": "below",
                           "label": "A"}}
        return mf.assembly_sheet_problems({"parts": [{"part_id": "01"}],
                                           "assembly_sheet": asm})

    def test_the_three_names_are_allowed(self):
        for v in ("front", "section", "none"):
            self.assertEqual(self.problems(v), [], v)

    def test_a_view_the_sheet_has_not_got(self):
        out = self.problems("left_view")
        self.assertEqual(len(out), 1)
        self.assertIn("extent_dims.view", out[0])

    def test_the_section_has_to_be_drawn(self):
        out = self.problems("section", section=False)
        self.assertEqual(len(out), 1)
        self.assertIn("does not draw", out[0])

    def test_which_takes_only_w_and_h(self):
        out = self.problems({"view": "front", "which": ["w", "d"]})
        self.assertEqual(len(out), 1)
        self.assertIn("extent_dims.which", out[0])

    def test_the_sides_are_checked(self):
        self.assertIn("width_side",
                      " ".join(self.problems({"width_side": "left"})))
        self.assertIn("height_side",
                      " ".join(self.problems({"height_side": "top"})))

    def test_the_long_form_passes(self):
        self.assertEqual(
            self.problems({"view": "section", "which": ["w"],
                           "width_side": "bottom"}), [])


class ExtentDimsOnTheSheetTest(unittest.TestCase):
    """Drawn: the overall dimension measures the view the manifest names.

    The fixture is the balloon tests' own long assembly — the shape of the
    real float — with its section below it."""

    @classmethod
    def build(cls, extent_dims):
        tmp = tempfile.TemporaryDirectory(prefix="fabpipe-extent-")
        m = asm_manifest(Path(tmp.name))
        asm = m["assembly_sheet"]
        del asm["front_extent_dims"]
        asm["extent_dims"] = extent_dims
        asm["section"] = {"enabled": True, "projection": "below",
                          "label": "A", "offset_frac": 0.5}
        front = long_assembly()
        section = long_assembly()
        built = draw.build_assembly_sheet(
            m, {"assembly:front": front, "assembly:section": section})
        ctx, _replaced, lay, _meta = built[0]
        tmp.cleanup()
        return ctx, lay

    def dims(self, text):
        """(a, b, line_pt) of every linear DIM on the sheet."""
        out = []
        for line in text.splitlines():
            t = line.split()
            if t and t[0] == "DIM" and t[1] == "0":
                out.append(((float(t[2]), float(t[3])),
                            (float(t[4]), float(t[5])),
                            (float(t[6]), float(t[7]))))
        return out

    def test_the_length_dim_goes_where_the_manifest_says(self):
        front_ctx, _l = self.build({"view": "front", "which": ["w"]})
        sec_ctx, _l2 = self.build({"view": "section", "which": ["w"],
                                   "width_side": "bottom"})
        fd = self.dims(front_ctx.render())
        sd = self.dims(sec_ctx.render())
        self.assertEqual(len(fd), 1, fd)
        self.assertEqual(len(sd), 1, sd)
        # same measured length, different view: the section is lower down
        self.assertAlmostEqual(fd[0][1][0] - fd[0][0][0],
                               sd[0][1][0] - sd[0][0][0], places=6)
        self.assertLess(sd[0][0][1], fd[0][0][1],
                        "the section's dim should be below the front's")

    def test_the_side_is_the_manifest_s_too(self):
        top, _l = self.build({"view": "front", "which": ["w"],
                              "width_side": "top"})
        bottom, _l2 = self.build({"view": "front", "which": ["w"],
                                  "width_side": "bottom"})
        td, bd = self.dims(top.render()), self.dims(bottom.render())
        self.assertGreater(td[0][2][1], td[0][0][1], "top: above its feet")
        self.assertLess(bd[0][2][1], bd[0][0][1], "bottom: below them")

    def test_none_draws_no_overall_dimension(self):
        ctx, _l = self.build("none")
        self.assertEqual(self.dims(ctx.render()), [])

    def test_a_section_asked_for_and_not_drawn_fails_the_sheet(self):
        tmp = tempfile.TemporaryDirectory(prefix="fabpipe-extent2-")
        m = asm_manifest(Path(tmp.name))
        del m["assembly_sheet"]["front_extent_dims"]
        m["assembly_sheet"]["extent_dims"] = "section"
        _ctx, _r, lay, _meta = draw.build_assembly_sheet(
            m, {"assembly:front": long_assembly()})[0]
        tmp.cleanup()
        self.assertTrue([u for u in lay["unresolved"]
                         if "extent_dims" in u], lay["unresolved"])


# ----------------------------------------------- a balloon has a number

class BalloonNamesTest(unittest.TestCase):
    """A balloon's LEADER carries no text, so every finding on a sheet of
    them read "the leader of 'balloon'" — and one waiver string covered
    the lot. The number is in the circle beside its knee."""

    TIP, KNEE = (1090.99, 985.85), (1551.19, 1267.17)

    def balloon(self, tip, knee, number):
        return [mw.leader(tip, knee, "", 17.5, props=A),
                mw.circle((knee[0] + 24.5, knee[1]), 24.5, A),
                mw.cell_text((knee[0] + 24.5, knee[1]), number, 17.5, A)]

    def parse(self, *recs):
        parsed = lc.parse(sheet(*recs))
        texts = [(r, lc.text_ink(r)) for r in parsed
                 if r.kind in lc.TEXT_KINDS and lc.text_ink(r)]
        return parsed, texts

    def test_the_circle_beside_the_knee_gives_the_number(self):
        parsed, texts = self.parse(*self.balloon(self.TIP, self.KNEE, "22"))
        bal = lc.balloon_numbers(parsed, texts)
        self.assertEqual(len(bal), 1)
        self.assertEqual(bal[0][2], "22")
        self.assertEqual(lc.leader_name("", self.KNEE, bal), "balloon 22")

    def test_a_leader_with_its_own_label_keeps_it(self):
        self.assertEqual(lc.leader_name("%%c46.7 -0.1/0 (SPIGOT)",
                                        self.KNEE, []),
                         "%%c46.7 -0.1/0 (SP")

    def test_a_leader_near_no_balloon_has_no_name(self):
        parsed, texts = self.parse(*self.balloon(self.TIP, self.KNEE, "22"))
        bal = lc.balloon_numbers(parsed, texts)
        self.assertEqual(lc.leader_name("", (100.0, 100.0), bal), "")

    def test_a_finding_names_the_balloon(self):
        """Two balloons whose leaders cross: each is named, so a waiver
        can take one crossing and not the kind."""
        text = sheet(mw.dimstyle(15.0, 12.5),
                     *self.balloon((1100.0, 900.0), (1551.19, 1100.0),
                                   "17"),
                     *self.balloon((1100.0, 1100.0), (1551.19, 900.0),
                                   "4"))
        found = of_kind(lc.check_sheet(text, BIG, 1 / 5), "leader-crossing")
        self.assertEqual(len(found), 1, found)
        self.assertIn("balloon 17", found[0]["what"])
        self.assertIn("balloon 4", found[0]["what"])
        live, done = verify.waived(found, ["balloon 17' and 'balloon 4"])
        self.assertEqual((live, len(done)), ([], 1))

    def test_a_leader_with_no_number_is_named_by_where_it_lands(self):
        text = sheet(mw.dimstyle(15.0, 12.5),
                     mw.leader((1100.0, 900.0), (1551.0, 1100.0), "", 17.5,
                               props=A),
                     mw.leader((1100.0, 1100.0), (1551.0, 900.0), "", 17.5,
                               props=A))
        found = of_kind(lc.check_sheet(text, BIG, 1 / 5), "leader-crossing")
        self.assertEqual(len(found), 1)
        self.assertIn("landing at 220, 180 mm", found[0]["what"])


# ------------------------------------- a leader under the pictorial

class HiddenLeaderTest(unittest.TestCase):
    """The pictorial's image is stamped over the sheet and is opaque, so a
    leader under it is not printed at all. The box and the leaders are the
    real sheet's."""

    BOX = (1314.92, 1121.7, 1524.92, 1301.7)
    T22, K22 = (1090.99, 985.85), (1551.19, 1267.17)

    def test_how_much_of_it_is_covered(self):
        self.assertAlmostEqual(lc.inside_run((0.0, 5.0), (10.0, 5.0),
                                             (2.0, 0.0, 6.0, 10.0)), 4.0)
        self.assertEqual(lc.inside_run((0.0, 50.0), (10.0, 50.0),
                                       (2.0, 0.0, 6.0, 10.0)), 0.0)
        self.assertAlmostEqual(lc.inside_run((3.0, 5.0), (5.0, 5.0),
                                             (2.0, 0.0, 6.0, 10.0)), 2.0,
                               msg="a segment wholly inside")
        self.assertAlmostEqual(draw.inside_run((0.0, 5.0), (10.0, 5.0),
                                               (2.0, 0.0, 6.0, 10.0)), 4.0)

    def test_the_real_balloon_is_reported_with_what_it_loses(self):
        text = sheet(mw.dimstyle(15.0, 12.5),
                     mw.leader(self.T22, self.K22, "", 17.5, props=A),
                     mw.circle((self.K22[0] + 24.5, self.K22[1]), 24.5, A),
                     mw.cell_text((self.K22[0] + 24.5, self.K22[1]), "22",
                                  17.5, A))
        found = lc.check_sheet(text, BIG, 1 / 5, boxes=[self.BOX])
        hit = of_kind(found, "leader-hidden")
        self.assertTrue(hit, [f["kind"] for f in found])
        self.assertIn("balloon 22", hit[0]["what"])
        self.assertIn("49.2 mm under the pictorial", hit[0]["what"])
        self.assertEqual(hit[0].get("waivable"), False)
        # the stronger finding stands alone: no "crosses the pictorial box"
        self.assertEqual(of_kind(found, "leader-across-dimension"), [])

    def test_no_waiver_covers_it(self):
        text = sheet(mw.dimstyle(15.0, 12.5),
                     mw.leader(self.T22, self.K22, "", 17.5, props=A),
                     mw.circle((self.K22[0] + 24.5, self.K22[1]), 24.5, A),
                     mw.cell_text((self.K22[0] + 24.5, self.K22[1]), "22",
                                  17.5, A))
        found = lc.check_sheet(text, BIG, 1 / 5, boxes=[self.BOX])
        live, done = verify.waived(found, ["under the pictorial"])
        self.assertEqual(done, [])
        self.assertEqual(len(live), len(found))


class KneeSpoiltTest(unittest.TestCase):
    """What makes a knee row no good for a balloon: the engine's own
    test, in the engine's own terms."""

    def setUp(self):
        self.ctx = mw.SheetContext(1, 5)

    def test_a_leader_under_the_stamp(self):
        why = draw.knee_spoilt(self.ctx, (1090.99, 985.85),
                               (1551.19, 1267.17),
                               hide=[(1314.92, 1121.7, 1524.92, 1301.7)])
        self.assertEqual(why, "hidden under the pictorial")

    def test_a_leader_across_a_sight_arrow(self):
        """The arrows rise from the axis at both ends of the view, between
        it and the column: the real pair on that sheet."""
        arrow = ((1516.0, 1021.0), (1516.0, 966.0))
        why = draw.knee_spoilt(self.ctx, (1209.0, 966.0), (1551.0, 1021.0),
                               step_off=[arrow])
        self.assertEqual(why, "across the cutting plane's sight arrow")
        self.assertIsNone(draw.knee_spoilt(self.ctx, (1209.0, 966.0),
                                           (1551.0, 1100.0),
                                           step_off=[arrow]))

    def test_a_clear_knee_is_not_spoilt(self):
        self.assertIsNone(draw.knee_spoilt(self.ctx, (1090.99, 985.85),
                                           (1551.19, 1050.0),
                                           hide=[(1314.92, 1121.7,
                                                  1524.92, 1301.7)]))


class FanRowsTest(unittest.TestCase):
    """The rows a fan will use are settled BEFORE its order is, because
    the order is settled against them. Skipping a row moves every balloon
    below it down and leaves the order alone; stepping ONE balloon out of
    its row is what makes a fan cross itself."""

    BOX = (1314.92, 1121.7, 1524.92, 1301.7)
    KX, START, PITCH = 1551.19, 1287.0, 61.5

    def setUp(self):
        self.ctx = mw.SheetContext(1, 5)
        self.tips = [(1090.99, 985.85)] * 4

    def rows(self, **kw):
        return draw.fan_rows(self.ctx, self.KX, self.START, self.PITCH,
                             self.tips, **kw)

    def test_with_nothing_in_the_way_they_are_the_plain_pitch(self):
        self.assertEqual(self.rows(),
                         [self.START - i * self.PITCH for i in range(4)])

    def test_the_rows_the_pictorial_hides_are_passed_over(self):
        got = self.rows(hide=[self.BOX], floor=0.0)
        self.assertEqual(len(got), 4)
        self.assertTrue(all(y < self.BOX[1] for y in got),
                        f"{got} should start below the box at {self.BOX[1]}")
        # and they keep the pitch between them
        for a, b in zip(got, got[1:]):
            self.assertAlmostEqual(a - b, self.PITCH)

    def test_a_column_that_cannot_hold_the_skips_keeps_its_rows(self):
        """A balloon the drawing does not carry is worse than one that is
        hard to read, so the plain rows are kept and the sheet says what
        is hidden."""
        got = self.rows(hide=[self.BOX], floor=1100.0)
        self.assertEqual(got, [self.START - i * self.PITCH
                               for i in range(4)])

    def test_no_tips_no_rows(self):
        self.assertEqual(draw.fan_rows(self.ctx, self.KX, self.START,
                                       self.PITCH, [], hide=[self.BOX]), [])

    def test_the_order_passes_take_the_rows_they_are_given(self):
        """_uncross and _order_against_ink are handed the row list, so a
        skipped row is in the knees they measure."""
        tips = {1: (100.0, 260.0), 2: (100.0, 180.0)}
        jobs = {1: object(), 2: object()}
        dim = [("a dimension line", object(),
                [((80.0, 220.0), (320.0, 220.0))])]
        self.assertEqual(
            draw._order_against_ink([2, 1], tips, 300.0, [270.0, 260.0],
                                    jobs, dim), [1, 2])
        # the same two with the rows BELOW the dim line: now the straight
        # order is the one that crosses it
        self.assertEqual(
            draw._uncross([1, 2], tips, 300.0, [270.0, 260.0]), [1, 2])


class TheFanUsesItsRowsTest(unittest.TestCase):
    """A fan whose column IS long enough passes the hidden rows over and
    places on the rows its order was settled for — four balloons on the
    long assembly, with the pictorial where the top row's leader would
    have gone."""

    BOX = (1120.0, 1480.0, 1300.0, 1620.0)
    OFF = (300.0, 1500.0)

    def fan(self, **kw):
        ctx = mw.SheetContext(1, 10)
        layout = draw.Layout(ctx)
        job = long_assembly(4)
        draw.balloons_fan(ctx, layout, job, self.OFF,
                          [(k, str(k + 1)) for k in range(4)],
                          floor=800.0, **kw)
        return ctx, layout

    def hidden(self, layout, ctx):
        return [lab for t, k, lab in layout.leaders
                if draw.inside_run(t, k, self.BOX) > ctx.mm(0.5)]

    def test_without_the_box_the_top_row_is_used(self):
        ctx, lay = self.fan()
        self.assertEqual([round(k[1]) for _t, k, _l in lay.leaders],
                         [1538, 1400, 1262, 1124])
        self.assertEqual(self.hidden(lay, ctx), ["balloon-4-0"],
                         "the fixture no longer exercises this")

    def test_with_the_box_the_whole_fan_moves_down_a_row(self):
        ctx, lay = self.fan(hide=[self.BOX])
        self.assertEqual([round(k[1]) for _t, k, _l in lay.leaders],
                         [1400, 1262, 1124, 986])
        self.assertEqual(self.hidden(lay, ctx), [])
        self.assertEqual(lay.faults, [])
        self.assertEqual(lay.unresolved, [])

    def test_the_rows_are_the_ones_the_order_was_settled_for(self):
        ctx, _lay = self.fan(hide=[self.BOX])
        tips = [draw.balloon_landing(ctx, long_assembly(4), self.OFF, k,
                                     (1260.0, 1538.0)) for k in range(4)]
        self.assertEqual(
            draw.fan_rows(ctx, 1260.0, 1538.0, draw.balloon_pitch(ctx),
                          tips, hide=[self.BOX], floor=800.0),
            [1400.0, 1262.0, 1124.0, 986.0])


class TwoColumnFanKeepsItsOrderTest(unittest.TestCase):
    """The whole sheet, 26 numbers on the long assembly: with the
    pictorial where the fan wants to be, the rows move and the leaders
    still do not cross."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="fabpipe-fanrows-")
        cls.m = asm_manifest(Path(cls.tmp.name))
        cls.built = draw.build_assembly_sheet(
            cls.m, {"assembly:front": long_assembly()})
        cls.ctx, _r, cls.lay, _meta = cls.built[0]
        cls.text = cls.ctx.render()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_every_balloon_is_placed(self):
        self.assertEqual([u for u in self.lay["unresolved"]
                          if u.startswith("balloon")], [])

    def test_what_is_hidden_is_named_with_its_lever(self):
        """This fixture is TIGHTER than the real A3-landscape sheet: 26
        balloons at 1:10 use the whole column, so the rows cannot be
        passed over and the pictorial does hide some leaders. The sheet
        then says which balloon and what to do about it — a balloon the
        drawing does not carry would be worse."""
        box = getattr(self.ctx, "_pictorial_box", None)
        self.assertIsNotNone(box)
        hidden = [f"{tip}->{knee}" for tip, knee in leaders(self.text)
                  if draw.inside_run(tip, knee, box) > self.ctx.mm(0.5)]
        self.assertTrue(hidden, "the fixture no longer exercises this")
        faults = [f for f in self.lay["faults"]
                  if "hidden under the pictorial" in f]
        self.assertEqual(len(faults), len(hidden), self.lay["faults"])
        self.assertIn('"pictorial": false', faults[0])

    def test_the_fan_still_does_not_cross_itself(self):
        found = lc.check_sheet(
            self.text, self.ctx.sheet_area, self.ctx.num / self.ctx.den,
            table_band=getattr(self.ctx, "_table_band", None),
            boxes=[self.ctx._pictorial_box],
            blocks=getattr(self.ctx, "blocks", ()))
        self.assertEqual(of_kind(found, "leader-crossing"), [])

    def test_without_the_pictorial_nothing_is_hidden(self):
        """The lever the fault names: the sheet says it has no room for
        one, and the fan has the whole column."""
        tmp = tempfile.TemporaryDirectory(prefix="fabpipe-nopic-")
        m = asm_manifest(Path(tmp.name))
        m["assembly_sheet"]["pictorial"] = False
        ctx, _r, lay, _meta = draw.build_assembly_sheet(
            m, {"assembly:front": long_assembly()})[0]
        tmp.cleanup()
        self.assertIsNone(ctx._stamp_rect)
        self.assertEqual([f for f in lay["faults"]
                          if "pictorial" in f], [])
        self.assertEqual([u for u in lay["unresolved"]
                          if u.startswith("balloon")], [])


if __name__ == "__main__":
    unittest.main()
