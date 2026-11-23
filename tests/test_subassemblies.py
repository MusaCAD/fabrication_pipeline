# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Pranay Kiran
"""Nested sub-assemblies (issue #3): following links to the real parts.

Run with:  python3 -m unittest discover -s tests -v

The fast tests walk stand-in object graphs, so every shape a sub-assembly
takes is covered cheaply. The slow ones build real FreeCAD 1.1 assemblies
two levels deep (tests/make_fixtures.py) and run the real fc_analyse and
fc_project over them; they skip when the FreeCAD AppImage is absent.
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
import fc_tree  # noqa: E402
import judge  # noqa: E402
import manifest as mf  # noqa: E402


# --------------------------------------------------------------- fakes

class Doc:
    def __init__(self, name, file_name, objects=()):
        self.Name = name
        self.FileName = file_name
        self.Objects = list(objects)


class Plc:
    """Just enough FreeCAD Placement to compose two of them."""

    def __init__(self, x=0.0):
        self.x = x

    def multiply(self, other):
        return Plc(self.x + other.x)


class Shape:
    def __init__(self, x=0.0):
        self.Placement = Plc(x)

    def copy(self):
        s = Shape()
        s.Placement = Plc(self.Placement.x)
        return s


class Obj:
    def __init__(self, name, type_id, doc=None, label=None, linked=None,
                 group=(), in_list=(), x=0.0, shape=True):
        self.Name = name
        self.TypeId = type_id
        self.Label = label or name
        self.Document = doc
        self.LinkedObject = linked
        self.Group = list(group)
        self.InList = list(in_list)
        self.Placement = Plc(x)
        if shape:
            self.Shape = Shape(x)

    def getLinkedObject(self, deep=True):
        o = self.LinkedObject
        while deep and getattr(o, "TypeId", "") == "App::Link" \
                and o.LinkedObject is not None:
            o = o.LinkedObject
        return o


def body(name, file_name):
    return Obj(name, "PartDesign::Body", doc=Doc(name, file_name))


class FakeAssemblyLinkTest(unittest.TestCase):
    """Shape (a): Assembly::AssemblyLink, which MIRRORS the sub-assembly's
    parts into the top document."""

    def setUp(self):
        self.sub_doc = Doc("asm_sub", "/m/asm_sub.FCStd")
        b1 = body("Body", "/m/plate.FCStd")
        b2 = body("Body", "/m/block.FCStd")
        # the sub-assembly file's own links
        s1 = Obj("SPLATE", "App::Link", doc=self.sub_doc, linked=b1)
        s2 = Obj("SBLOCK", "App::Link", doc=self.sub_doc, linked=b2)
        self.sub_asm = Obj("Assembly", "Assembly::AssemblyObject",
                           doc=self.sub_doc, group=[s1, s2])
        self.sub_doc.Objects = [self.sub_asm, s1, s2]
        # the top document: an AssemblyLink plus the mirrors it owns
        self.top_doc = Doc("asm_top", "/m/asm_top.FCStd")
        self.al = Obj("SUB1", "Assembly::AssemblyLink", doc=self.top_doc,
                      linked=self.sub_asm, x=100.0)
        m1 = Obj("SPLATE", "App::Link", doc=self.top_doc, linked=s1,
                 in_list=[self.al])
        m2 = Obj("SBLOCK", "App::Link", doc=self.top_doc, linked=s2,
                 in_list=[self.al], x=10.0)
        self.al.Group = [m1, m2]
        self.direct = Obj("TBLOCK", "App::Link", doc=self.top_doc, linked=b2,
                          x=5.0)
        self.top_doc.Objects = [self.al, m1, m2, self.direct]

    def test_mirrors_are_not_top_level_instances(self):
        self.assertEqual([o.Name for o in fc_tree.top_instances(self.top_doc)],
                         ["SUB1", "TBLOCK"])

    def test_the_tree_reaches_the_real_parts(self):
        nodes = fc_tree.walk(self.top_doc)
        self.assertEqual([(n["path"], n["kind"]) for n in
                          fc_tree.flatten(nodes)],
                         [("SUB1", "assembly"), ("SUB1/SPLATE", "part"),
                          ("SUB1/SBLOCK", "part"), ("TBLOCK", "part")])
        files = {n["path"]: n["target_file"] for n in fc_tree.flatten(nodes)}
        self.assertEqual(files["SUB1"], "/m/asm_sub.FCStd")
        self.assertEqual(files["SUB1/SPLATE"], "/m/plate.FCStd")
        self.assertEqual(files["SUB1/SBLOCK"], "/m/block.FCStd")

    def test_nested_placements_and_shapes_are_global(self):
        nodes = fc_tree.walk(self.top_doc)
        kids = {n["path"]: n for n in fc_tree.flatten(nodes)}
        # the mirror's own placement is relative to the AssemblyLink
        self.assertEqual(kids["SUB1/SPLATE"]["_placement"].x, 100.0)
        self.assertEqual(kids["SUB1/SBLOCK"]["_placement"].x, 110.0)
        self.assertEqual(kids["SUB1/SBLOCK"]["_shape"].Placement.x, 110.0)
        # a top-level instance is already global
        self.assertEqual(kids["TBLOCK"]["_placement"].x, 5.0)

    def test_a_part_used_twice_is_one_file_and_two_items(self):
        usage = fc_tree.part_usage(fc_tree.walk(self.top_doc))
        self.assertEqual(usage["/m/block.FCStd"]["qty"], 2)
        self.assertEqual(usage["/m/block.FCStd"]["used_in"],
                         ["SUB1/SBLOCK", "TBLOCK"])
        self.assertEqual(usage["/m/plate.FCStd"]["qty"], 1)
        self.assertNotIn("/m/asm_sub.FCStd", usage)   # not a part

    def test_a_joint_reference_through_the_sub_assembly(self):
        nodes = fc_tree.walk(self.top_doc)
        ref = (Obj("Assembly", "Assembly::AssemblyObject"),
               ["SUB1.SPLATE.Vertex1", "SUB1.SPLATE.Vertex1"])
        self.assertEqual(fc_tree.resolve_ref_path(ref, nodes),
                         ["SUB1/SPLATE"])


class FakePlainLinkTest(unittest.TestCase):
    """Shape (b): a plain App::Link straight at another file's assembly."""

    def setUp(self):
        sub_doc = Doc("asm_sub", "/m/asm_sub.FCStd")
        b1 = body("Body", "/m/plate.FCStd")
        s1 = Obj("PART_0", "App::Link", doc=sub_doc, linked=b1, x=7.0)
        sub_asm = Obj("Assembly", "Assembly::AssemblyObject", doc=sub_doc,
                      group=[s1])
        sub_doc.Objects = [sub_asm, s1]
        self.top_doc = Doc("top", "/m/top.FCStd")
        self.lk = Obj("SA1", "App::Link", doc=self.top_doc, linked=sub_asm,
                      x=100.0)
        self.top_doc.Objects = [self.lk]

    def test_children_come_from_the_linked_assembly(self):
        nodes = fc_tree.walk(self.top_doc)
        flat = fc_tree.flatten(nodes)
        self.assertEqual([(n["path"], n["kind"]) for n in flat],
                         [("SA1", "assembly"), ("SA1/PART_0", "part")])
        self.assertEqual(flat[1]["target_file"], "/m/plate.FCStd")
        self.assertEqual(flat[1]["_placement"].x, 107.0)

    def test_a_reference_into_it_resolves_to_the_part(self):
        nodes = fc_tree.walk(self.top_doc)
        # how the real models write it: the link, with the part inside
        ref = (self.lk, ["PART_0.Face1", "PART_0.Vertex1"])
        self.assertEqual(fc_tree.resolve_ref_path(ref, nodes),
                         ["SA1/PART_0"])


class FakeCycleTest(unittest.TestCase):
    """Two assemblies containing each other."""

    def setUp(self):
        self.a_doc = Doc("a", "/m/a.FCStd")
        self.b_doc = Doc("b", "/m/b.FCStd")
        self.a_asm = Obj("Assembly", "Assembly::AssemblyObject", doc=self.a_doc)
        self.b_asm = Obj("Assembly", "Assembly::AssemblyObject", doc=self.b_doc)
        b_in_a = Obj("B_IN_A", "App::Link", doc=self.a_doc, linked=self.b_asm)
        a_in_b = Obj("A_IN_B", "App::Link", doc=self.b_doc, linked=self.a_asm)
        self.a_asm.Group = [b_in_a]
        self.b_asm.Group = [a_in_b]
        self.a_doc.Objects = [self.a_asm, b_in_a]
        self.b_doc.Objects = [self.b_asm, a_in_b]

    def test_it_stops_and_says_so(self):
        issues = []
        nodes = fc_tree.walk(self.a_doc, issues)
        flat = fc_tree.flatten(nodes)
        self.assertEqual([n["path"] for n in flat],
                         ["B_IN_A", "B_IN_A/A_IN_B"])
        self.assertTrue(flat[-1]["cyclic"])
        self.assertEqual(flat[-1]["children"], [])
        self.assertEqual(len(issues), 1)
        self.assertIn("cyclic sub-assembly link", issues[0])
        self.assertIn("a.FCStd -> b.FCStd -> a.FCStd", issues[0])

    def test_no_shape_is_touched_on_a_cyclic_branch(self):
        # asking a cyclic link for its Shape segfaults FreeCAD, so the walk
        # must leave every shape above and inside the cycle alone
        nodes = fc_tree.walk(self.a_doc, [])
        for n in fc_tree.flatten(nodes):
            self.assertIsNone(n["_shape"], n["path"])


class JudgeTreeTest(unittest.TestCase):
    """What /judge says about a tree."""

    def tree(self, loose=False):
        sub_kids = [{"name": "PLATE", "label": "PLATE", "path": "SUB1/PLATE",
                     "kind": "part", "children": [], "broken": False},
                    {"name": "SHIM", "label": "SHIM", "path": "SUB1/SHIM",
                     "kind": "part", "children": [], "broken": False}]
        asm = {"file": "/m/top.FCStd", "issues": [],
               "instances": [{"name": "SUB1", "label": "SUB1",
                              "path": "SUB1", "kind": "assembly",
                              "children": sub_kids, "broken": False}],
               "joints": [
                   {"name": "GroundedJoint", "label": "g", "type": "Grounded",
                    "grounded": True, "suppressed": False, "assembly": "",
                    "connects": ["SUB1"]},
                   {"name": "GroundedJoint", "label": "g2",
                    "type": "Grounded", "grounded": True, "suppressed": False,
                    "assembly": "SUB1", "connects": ["SUB1/PLATE"]}]}
        if not loose:
            asm["joints"].append(
                {"name": "Fixed_SHIM", "label": "f", "type": "Fixed",
                 "grounded": False, "suppressed": False, "assembly": "SUB1",
                 "connects": ["SUB1/PLATE", "SUB1/SHIM"]})
        return asm

    def msgs(self, asm):
        return [f["message"] for f in judge.assembly_findings(asm)]

    def test_a_fully_joined_two_level_assembly_is_quiet(self):
        self.assertEqual(judge.assembly_findings(self.tree()), [])

    def test_an_unjoined_part_inside_a_sub_assembly_still_warns(self):
        msgs = self.msgs(self.tree(loose=True))
        self.assertIn("instances with no joint: SUB1/SHIM", msgs)
        self.assertEqual(len(msgs), 1)

    def test_a_joint_on_an_inner_part_pins_the_sub_assembly(self):
        # the top joint reaches SUB1/PLATE, never SUB1 itself; a rigid
        # sub-assembly is located by that, so demanding its own joint would
        # be the false warning issue #3 is about
        asm = self.tree()
        asm["joints"][0]["connects"] = ["SUB1/PLATE"]
        self.assertEqual(judge.assembly_findings(asm), [])

    def test_a_sub_assembly_with_no_ground_of_its_own(self):
        asm = self.tree()
        asm["joints"] = [j for j in asm["joints"]
                         if j.get("assembly") != "SUB1" or not j["grounded"]]
        self.assertIn("SUB1: no grounded joint in the sub-assembly",
                      self.msgs(asm))

    def test_a_broken_link_inside_a_sub_assembly_is_named_by_path(self):
        asm = self.tree()
        asm["instances"][0]["children"][1]["broken"] = True
        f = judge.assembly_findings(asm)
        self.assertEqual(f[0]["severity"], "FAIL")
        self.assertIn("broken link SUB1/SHIM", f[0]["message"])


class ManifestQtyTest(unittest.TestCase):
    """The parts table counts instances, not files."""

    def test_qty_comes_from_part_usage(self):
        usage = {"/m/block.FCStd": {"qty": 3, "used_in": ["a", "b", "c"]}}
        self.assertEqual({f: u["qty"] for f, u in usage.items()},
                         {"/m/block.FCStd": 3})

    def test_lock_still_accepts_a_nested_manifest(self):
        # nothing about sub-assemblies changes what /finalise demands
        part = {"part_id": "01", "file": "/m/block.FCStd",
                "description": "BLOCK", "drg_no": "X-01", "material": "MS",
                "qty": 3, "front_view": "+Z", "scale": "1:1",
                "section": {"enabled": True, "projection": "below"}}
        self.assertEqual(mf.detail_problems(part), [])


@unittest.skipUnless(common.FREECAD_APPIMAGE.exists(),
                     f"FreeCAD not at {common.FREECAD_APPIMAGE}")
class RealNestedAssemblyTest(unittest.TestCase):
    """Real FreeCAD 1.1 assemblies, two levels deep."""

    reports: dict = {}

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="fabpipe-nested-")
        d = cls.tmp.name
        common.run_freecad(Path(__file__).resolve().parent
                           / "make_fixtures.py", d, timeout=900)
        for name in ("asm_nested", "asm_nested_loose", "asm_shared",
                     "asm_plainlink", "cyc_a"):
            out = os.path.join(d, name + ".json")
            common.run_freecad(SRC / "fc_analyse.py", d, name + ".FCStd",
                               out, timeout=900)
            with open(out) as f:
                cls.reports[name] = json.load(f)
        cls.dir = d

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def names(self, report):
        return [n["path"] for n in
                fc_tree.flatten(report["assembly"]["instances"])]

    def files(self, report):
        return sorted(Path(p["file"]).name for p in report["parts"])

    def test_the_real_parts_are_analysed_not_the_assembly_files(self):
        r = self.reports["asm_nested"]
        self.assertEqual(self.files(r),
                         ["block.FCStd", "plate.FCStd", "shim.FCStd"])
        self.assertNotIn("asm_sub.FCStd", self.files(r))
        self.assertEqual(self.names(r),
                         ["SUB1", "SUB1/PLATE", "SUB1/BLOCK", "TSHIM"])

    def test_two_levels_with_three_joint_types_warn_about_nothing(self):
        r = self.reports["asm_nested"]
        kinds = {j["type"] for j in r["assembly"]["joints"]}
        self.assertTrue({"Fixed", "Revolute", "Cylindrical"} <= kinds, kinds)
        self.assertEqual(judge.assembly_findings(r["assembly"]), [])

    def test_the_sub_assembly_s_own_joints_are_read_from_its_file(self):
        joints = self.reports["asm_nested"]["assembly"]["joints"]
        inner = [j for j in joints if j.get("assembly") == "SUB1"]
        self.assertTrue(inner)
        self.assertTrue(all(c.startswith("SUB1/")
                            for j in inner for c in j["connects"]))

    def test_nested_placements_are_global(self):
        flat = fc_tree.flatten(
            self.reports["asm_nested"]["assembly"]["instances"])
        pos = {n["path"]: n.get("position") for n in flat}
        # the sub-assembly sits 100 mm along X; its parts must say so
        self.assertEqual(pos["SUB1"][0], 100.0)
        self.assertEqual(pos["SUB1/PLATE"][0], 100.0)
        self.assertEqual(pos["SUB1/BLOCK"][0], 100.0)
        self.assertEqual(pos["TSHIM"][0], 0.0)

    def test_an_unjoined_part_one_level_down_still_warns(self):
        msgs = [f["message"] for f in judge.assembly_findings(
            self.reports["asm_nested_loose"]["assembly"])]
        self.assertIn("instances with no joint: SUB1/SHIM", msgs)

    def test_a_part_in_two_sub_assemblies_is_analysed_once_counted_twice(self):
        r = self.reports["asm_shared"]
        usage = r["assembly"]["part_usage"]
        block = next(u for f, u in usage.items()
                     if Path(f).name == "block.FCStd")
        self.assertEqual(block["qty"], 2)
        self.assertEqual(sorted(block["used_in"]),
                         ["SUB1/BLOCK", "SUB2/BLOCK2"])
        self.assertEqual(len([p for p in r["parts"]
                              if Path(p["file"]).name == "block.FCStd"]), 1)
        self.assertEqual(judge.assembly_findings(r["assembly"]), [])

    def test_a_plain_link_to_an_assembly_is_followed_too(self):
        r = self.reports["asm_plainlink"]
        self.assertEqual(self.names(r),
                         ["SUB1", "SUB1/PLATE", "SUB1/BLOCK", "TSHIM"])
        self.assertEqual(judge.assembly_findings(r["assembly"]), [])

    def test_a_cyclic_link_is_refused_cleanly(self):
        asm = self.reports["cyc_a"]["assembly"]
        self.assertTrue(any("cyclic sub-assembly link" in i
                            for i in asm["issues"]), asm["issues"])
        self.assertIn("cyc_a.FCStd -> cyc_b.FCStd -> cyc_a.FCStd",
                      " ".join(asm["issues"]))
        # and /judge refuses to pass the model on
        sev = [f["severity"] for f in judge.assembly_findings(asm)]
        self.assertIn("FAIL", sev)

    def test_the_projection_places_nested_parts_globally(self):
        req = Path(self.dir) / "proj_req.json"
        out = Path(self.dir) / "proj_out.json"
        req.write_text(json.dumps({"jobs": [{
            "id": "assembly:front",
            "file": os.path.join(self.dir, "asm_nested.FCStd"),
            "assembly": True, "view_dir": [0, -1, 0], "up": [0, 0, 1],
            "section": None, "link_features": {}}]}))
        common.run_freecad(SRC / "fc_project.py", str(req), str(out),
                           timeout=900)
        job = json.loads(out.read_text())["jobs"][0]
        self.assertNotIn("error", job)
        # the instances are the real parts, named by path
        names = sorted(i["name"] for i in job["instances"])
        self.assertEqual(names, ["SUB1/BLOCK", "SUB1/PLATE", "TSHIM"])
        # and the sub-assembly's parts are 100 mm from the loose one
        by = {i["name"]: i["bbox"] for i in job["instances"]}
        self.assertGreater(abs(by["SUB1/PLATE"][0] - by["TSHIM"][0]), 90.0)


if __name__ == "__main__":
    unittest.main()
