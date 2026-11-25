# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Pranay Kiran
"""A detail view of an assembly SECTION hatches each cut part separately
(issue #31).

Run with:  python3 -m unittest discover -s tests -v

The real sheet: an A3-landscape assembly at 1:5 with a section and three
details of it on the continuation sheet — a plug against the tube it
closes, a printed cradle against the shell around it, and a stack of
three thin discs at 5:1. In the section each cut part had its own hatch
and the parts that met had different ones; in every detail all twenty
hatch groups came out at one angle and one spacing, so the joints
disappeared.

The cause was one line: `detail_job` rebuilt each group as
{"outer", "holes"} and dropped the instance it was cut from, so the
colouring saw one unnamed part.
"""

import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import draw  # noqa: E402
import musa_writer as mw  # noqa: E402


def box(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def section_job():
    """The probe station as the section draws it: the shell's wall, the
    cradle sitting inside it and the pad between them — three parts that
    meet, inside a circle a detail will cut out."""
    return {
        "visible": [{"t": "seg", "a": [0.0, 0.0], "b": [100.0, 0.0]}],
        "hidden": [],
        "bounds": [[0.0, -20.0], [100.0, 20.0]],
        "hatch_groups": [
            {"outer": box(10.0, 10.0, 90.0, 20.0), "holes": [],
             "instance": "ProbeAssembly/ProbeShell"},
            {"outer": box(20.0, 4.0, 80.0, 10.0), "holes": [],
             "instance": "ProbeAssembly/LoggerCradle"},
            {"outer": box(20.0, 2.0, 80.0, 4.0), "holes": [],
             "instance": "ProbeAssembly/SoftPad"},
        ]}


def with_skirt():
    """The same section with the skirt that sits on the shell, OUTSIDE
    the circle the detail cuts: a neighbour the detail does not see, so
    the colouring of what is left is not the colouring of the section."""
    job = section_job()
    job["hatch_groups"].append({"outer": box(10.0, 21.0, 90.0, 31.0),
                                "holes": [],
                                "instance": "SkirtAssembly/SkirtShell"})
    return job


def hatches(recs):
    """(pattern, scale, angle) of every HATCH record written."""
    out = []
    for r in recs:
        head, pattern = r.split("\n")[0], r.split("\n")[1]
        t = head.split()
        out.append((pattern, round(float(t[-11]), 4),
                    round(float(t[-10]), 4)))
    return out


# ------------------------------------------------ the clip keeps the name

class DetailJobTest(unittest.TestCase):

    def test_a_clipped_group_still_knows_whose_cut_it_is(self):
        job = draw.detail_job(section_job(),
                              {"center": [50.0, 8.0], "radius": 12.0}, 5.0)
        names = [g.get("instance") for g in job["hatch_groups"]]
        self.assertEqual(sorted(names),
                         ["ProbeAssembly/LoggerCradle",
                          "ProbeAssembly/ProbeShell",
                          "ProbeAssembly/SoftPad"])

    def test_a_part_sheet_s_section_still_has_none(self):
        """One part, no instances: the key stays absent and such a detail
        is hatched exactly as it was."""
        parent = {"visible": [], "hidden": [], "bounds": [[0, 0], [10, 10]],
                  "hatch_groups": [{"outer": box(0.0, 0.0, 10.0, 10.0),
                                    "holes": []}]}
        job = draw.detail_job(parent, {"center": [5.0, 5.0],
                                       "radius": 4.0}, 2.0)
        self.assertNotIn("instance", job["hatch_groups"][0])

    def test_the_geometry_is_the_parent_s_enlarged(self):
        job = draw.detail_job(section_job(),
                              {"center": [50.0, 8.0], "radius": 12.0}, 5.0)
        shell = [g for g in job["hatch_groups"]
                 if g["instance"].endswith("ProbeShell")][0]
        ys = [p[1] for p in shell["outer"]]
        # the wall runs y 10..20 about a centre at y 8, five times size
        self.assertAlmostEqual(max(ys), (20.0 - 8.0) * 5.0, places=6)
        self.assertAlmostEqual(min(ys), (10.0 - 8.0) * 5.0, places=6)


# ------------------------------------------- what the detail then draws

class DetailHatchTest(unittest.TestCase):

    def setUp(self):
        self.ctx = mw.SheetContext(1, 5)
        self.parent = section_job()
        self.job = draw.detail_job(self.parent,
                                   {"center": [50.0, 8.0], "radius": 12.0},
                                   5.0)

    def test_parts_that_meet_get_different_hatches(self):
        """ISO 128-50 asks that NEIGHBOURS be told apart: the cradle
        against the shell above it and the pad below it. Two parts that
        do not meet may share a hatch, and the shell and the pad do."""
        recs, _rep = draw.detail_hatches(self.ctx, self.job, (0.0, 0.0))
        got = hatches(recs)
        self.assertEqual(len(got), 3)
        shell, cradle, pad = got
        self.assertNotEqual(shell, cradle)
        self.assertNotEqual(cradle, pad)

    def test_without_the_instance_they_all_come_out_alike(self):
        """What the sheet did before (issue #31): the groups are the
        same geometry, and with no name to colour by every one of them
        takes style 0, so the joints disappear."""
        job = dict(self.job,
                   hatch_groups=[{"outer": g["outer"], "holes": g["holes"]}
                                 for g in self.job["hatch_groups"]])
        recs, _rep = draw.detail_hatches(self.ctx, job, (0.0, 0.0))
        self.assertEqual(len(set(hatches(recs))), 1)

    def test_it_is_the_section_s_own_colouring(self):
        """One part reads the same in the section and in the detail, so
        the reader can pair them: the section's styles are handed over."""
        styles = draw.hatch_styles_by_instance(self.parent["hatch_groups"],
                                               self.ctx.mm(0.5))
        sec, _ = draw.assembly_hatches(self.ctx, self.parent, (0.0, 0.0))
        det, _ = draw.detail_hatches(self.ctx, self.job, (0.0, 0.0),
                                     styles=styles)
        for name in ("ProbeShell", "LoggerCradle"):
            i = [k for k, g in enumerate(self.parent["hatch_groups"])
                 if g["instance"].endswith(name)][0]
            j = [k for k, g in enumerate(self.job["hatch_groups"])
                 if g["instance"].endswith(name)][0]
            self.assertEqual(hatches(sec)[i][1:], hatches(det)[j][1:], name)

    def test_the_detail_draws_the_styles_it_is_given(self):
        """It does not colour the circle on its own: the detail sees a
        neighbour the section does not (one outside the circle is
        clipped away), so colouring it again would hatch the same part
        one way in the section and another in the enlargement of it."""
        parent = with_skirt()
        job = draw.detail_job(parent, {"center": [50.0, 8.0],
                                       "radius": 12.0}, 5.0)
        theirs = {"ProbeAssembly/ProbeShell": 5,
                  "ProbeAssembly/LoggerCradle": 4,
                  "ProbeAssembly/SoftPad": 3,
                  "SkirtAssembly/SkirtShell": 0}
        self.assertNotEqual(
            theirs, draw.hatch_styles_by_instance(job["hatch_groups"],
                                                  self.ctx.mm(0.5)))
        recs, rep = draw.detail_hatches(self.ctx, job, (0.0, 0.0),
                                        styles=theirs)
        for k, (pattern, scale, angle) in enumerate(hatches(recs)):
            want = draw.HATCH_STYLES[theirs[
                job["hatch_groups"][k]["instance"]]]
            self.assertEqual(pattern, "ANSI31")
            self.assertAlmostEqual(angle, round(want[0], 4), places=3)
            self.assertAlmostEqual(scale, self.ctx.mm(25.4) * want[1],
                                   places=3)
        self.assertEqual(rep["hatch_styles"]["ProbeAssembly/ProbeShell"], 5)

    def test_a_plain_section_detail_is_hatched_as_before(self):
        """A part sheet's detail: one ANSI31 at the house spacing, the
        record section_hatches has always written."""
        parent = {"visible": [], "hidden": [], "bounds": [[0, 0], [10, 10]],
                  "hatch_groups": [{"outer": box(0.0, 0.0, 10.0, 10.0),
                                    "holes": []}]}
        job = draw.detail_job(parent, {"center": [5.0, 5.0],
                                       "radius": 4.0}, 2.0)
        recs, rep = draw.detail_hatches(self.ctx, job, (0.0, 0.0))
        self.assertEqual(rep, {})
        self.assertEqual(recs, draw.section_hatches(self.ctx, job,
                                                    (0.0, 0.0)))

    def test_the_thin_rule_runs_again_at_the_detail_s_own_scale(self):
        """The pad is 2 mm thick: 0.4 mm printed in the section at 1:5,
        so it is filled solid there — and 2 mm printed in a detail drawn
        at 1:1, where it is hatched, which is why the detail is there."""
        sec, srep = draw.assembly_hatches(self.ctx, self.parent, (0.0, 0.0))
        self.assertIn("ProbeAssembly/SoftPad", srep["solid_filled"])
        det, drep = draw.detail_hatches(self.ctx, self.job, (0.0, 0.0))
        self.assertEqual(drep["solid_filled"], [])
        self.assertTrue(all(p == "ANSI31" for p, _s, _a in hatches(det)))

    def test_the_angles_are_the_iso_pair(self):
        """ISO 128-50: tell neighbours apart by angle first — 45 one way
        and 45 the other, which is 0 and pi/2 on the ANSI31 pattern."""
        recs, _ = draw.detail_hatches(self.ctx, self.job, (0.0, 0.0))
        angles = {a for _p, _s, a in hatches(recs)}
        self.assertTrue(angles <= {0.0, round(math.pi / 2, 4)}, angles)
