# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Pranay Kiran
"""Assembly joint reading (issue #1) and the judge warning that rests on it.

Run with:  python3 -m unittest discover -s tests -v

The fast tests feed fake FreeCAD objects to the resolver, so they cover
every reference layout cheaply. The slow one builds real FreeCAD 1.1
assemblies with the Assembly workbench (tests/make_fixtures.py) and runs
the real fc_analyse.py inside headless FreeCAD; it is skipped when the
FreeCAD AppImage is not installed.
"""

import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

# fc_analyse runs inside FreeCAD; a stub lets the pure resolver be imported
# and tested here. It is only ever used at import time.
sys.modules.setdefault("FreeCAD", types.ModuleType("FreeCAD"))

import fc_analyse  # noqa: E402
import fc_tree  # noqa: E402
import judge  # noqa: E402


class Obj:
    """Stand-in for a FreeCAD document object."""

    def __init__(self, name, type_id, label=None, out_list=(), **props):
        self.Name = name
        self.TypeId = type_id
        self.Label = label or name
        self.OutList = list(out_list)
        for k, v in props.items():
            setattr(self, k, v)


def link(name, label=None):
    return Obj(name, "App::Link", label=label)


def assembly():
    return Obj("Assembly", "Assembly::AssemblyObject")


def node(name, label=None, children=(), path=None, kind="part"):
    """A tree node as fc_tree.walk builds them."""
    return {"name": name, "label": label or name, "path": path or name,
            "kind": kind, "children": list(children), "broken": False}


class TestRefTargets(unittest.TestCase):
    """fc_tree.resolve_ref_path over every layout seen in the wild."""

    def setUp(self):
        self.plate = link("PLATE")
        self.block = link("Body002", label="BLOCK")
        self.nodes = [node("PLATE"), node("Body002", label="BLOCK")]

    def targets(self, ref):
        return fc_tree.resolve_ref_path(ref, self.nodes)

    def test_freecad_11_reference_through_the_assembly(self):
        # the layout of issue #1: the reference object is the Assembly and
        # the instance is the head of the subname path
        ref = (assembly(), ["PLATE.Vertex1", "PLATE.Vertex1"])
        self.assertEqual(self.targets(ref), ["PLATE"])

    def test_freecad_10_reference_straight_at_the_link(self):
        ref = (self.plate, ["Vertex1", "Vertex1"])
        self.assertEqual(self.targets(ref), ["PLATE"])

    def test_nested_subname_under_a_link_reference(self):
        # a link into a sub-assembly, but this assembly has no children
        # under PLATE: the walk stops at the link, as it does on an element
        ref = (self.plate, ["Child_0.Face1", "Child_0.Vertex1"])
        self.assertEqual(self.targets(ref), ["PLATE"])

    def test_subname_head_may_be_a_label(self):
        ref = (assembly(), ["BLOCK.Vertex1"])
        self.assertEqual(self.targets(ref), ["Body002"])

    def test_unresolved_element_marker_is_stripped(self):
        ref = (assembly(), ["?PLATE.Vertex15"])
        self.assertEqual(self.targets(ref), ["PLATE"])

    def test_unknown_head_is_ignored(self):
        ref = (assembly(), ["GHOST.Vertex1", "PLATE.Vertex1"])
        self.assertEqual(self.targets(ref), ["PLATE"])

    def test_empty_and_missing_references(self):
        self.assertEqual(self.targets(None), [])
        self.assertEqual(self.targets(()), [])
        self.assertEqual(self.targets((None, ["PLATE.Vertex1"])), [])
        self.assertEqual(self.targets((assembly(), [])), [])
        self.assertEqual(self.targets((assembly(), None)), [])


class TestJointInfo(unittest.TestCase):
    """fc_analyse.joint_info / is_joint / joint_connects."""

    def setUp(self):
        self.plate = link("PLATE")
        self.block = link("BLOCK")
        self.asm = assembly()
        self.nodes = [node("PLATE"), node("BLOCK")]

    def info(self, obj):
        return fc_analyse.joint_info(obj, self.nodes)

    def test_freecad_11_fixed_joint(self):
        # OutList holds only the Assembly object -- the old scan found
        # nothing here and every instance was reported unjoined
        j = Obj("Fixed_BLOCK", "App::FeaturePython", out_list=[self.asm],
                JointType="Fixed",
                Reference1=(self.asm, ["PLATE.Vertex1", "PLATE.Vertex1"]),
                Reference2=(self.asm, ["BLOCK.Vertex1", "BLOCK.Vertex1"]))
        self.assertTrue(fc_analyse.is_joint(j))
        got = self.info(j)
        self.assertEqual(got["connects"], ["PLATE", "BLOCK"])
        self.assertEqual(got["type"], "Fixed")
        self.assertFalse(got["grounded"])
        self.assertFalse(got["suppressed"])

    def test_grounded_joint(self):
        j = Obj("GroundedJoint", "App::FeaturePython",
                out_list=[self.plate], ObjectToGround=self.plate)
        self.assertTrue(fc_analyse.is_joint(j))
        got = self.info(j)
        self.assertEqual(got["connects"], ["PLATE"])
        self.assertEqual(got["type"], "Grounded")
        self.assertTrue(got["grounded"])

    def test_pre_11_joint_with_links_in_outlist_only(self):
        # earlier FreeCAD versions, and other workbenches: no Reference
        # properties at all, just the links in the OutList
        j = Obj("Joint", "App::FeaturePython", label="Cylindrical",
                out_list=[self.plate, self.block], JointType="Cylindrical")
        self.assertEqual(self.info(j)["connects"], ["PLATE", "BLOCK"])

    def test_references_and_outlist_are_merged_without_duplicates(self):
        j = Obj("Joint001", "App::FeaturePython",
                out_list=[self.plate, self.block], JointType="Distance",
                Reference1=(self.plate, ["Edge2", "Edge2"]),
                Reference2=(self.block, ["Edge12", "Edge12"]))
        self.assertEqual(self.info(j)["connects"], ["PLATE", "BLOCK"])

    def test_suppressed_joint_is_reported_as_such(self):
        j = Obj("Fixed_BLOCK", "App::FeaturePython", out_list=[self.asm],
                JointType="Fixed", Suppressed=True,
                Reference1=(self.asm, ["PLATE.Vertex1"]),
                Reference2=(self.asm, ["BLOCK.Vertex1"]))
        self.assertTrue(self.info(j)["suppressed"])

    def test_plain_objects_are_not_joints(self):
        self.assertFalse(fc_analyse.is_joint(self.plate))
        self.assertFalse(fc_analyse.is_joint(
            Obj("Sketch", "Sketcher::SketchObject")))


def analysis(joints, instances=("PLATE", "BLOCK", "SHIM")):
    """A minimal analysis.json-shaped assembly dict."""
    return {"file": "/models/asm.FCStd", "document": "asm",
            "workbench": "builtin-assembly", "issues": [],
            "instances": [{"name": n, "label": n, "broken": False}
                          for n in instances],
            "joints": joints}


def joint(name, connects, grounded=False, suppressed=False, jtype="Fixed"):
    return {"name": name, "label": name, "type": jtype,
            "grounded": grounded, "suppressed": suppressed,
            "connects": list(connects)}


class TestJudgeJointFindings(unittest.TestCase):
    """judge.assembly_findings: what the user is actually told."""

    def messages(self, asm):
        return [f["message"] for f in judge.assembly_findings(asm)]

    def test_fully_joined_assembly_is_quiet(self):
        asm = analysis([joint("GroundedJoint", ["PLATE"], grounded=True,
                              jtype="Grounded"),
                        joint("Fixed_BLOCK", ["PLATE", "BLOCK"]),
                        joint("Fixed_SHIM", ["PLATE", "SHIM"])])
        self.assertEqual(judge.assembly_findings(asm), [])

    def test_genuinely_unjoined_part_still_warns(self):
        asm = analysis([joint("GroundedJoint", ["PLATE"], grounded=True,
                              jtype="Grounded"),
                        joint("Fixed_BLOCK", ["PLATE", "BLOCK"])])
        msgs = self.messages(asm)
        self.assertIn("instances with no joint: SHIM", msgs)
        self.assertEqual(len(msgs), 1)

    def test_grounded_joint_alone_joins_its_instance(self):
        asm = analysis([joint("GroundedJoint", ["PLATE"], grounded=True,
                              jtype="Grounded")], instances=("PLATE",))
        self.assertEqual(judge.assembly_findings(asm), [])

    def test_suppressed_joint_does_not_join(self):
        asm = analysis([joint("GroundedJoint", ["PLATE"], grounded=True,
                              jtype="Grounded"),
                        joint("Fixed_BLOCK", ["PLATE", "BLOCK"]),
                        joint("Fixed_SHIM", ["PLATE", "SHIM"],
                              suppressed=True)])
        self.assertIn("instances with no joint: SHIM", self.messages(asm))

    def test_missing_grounded_joint_warns(self):
        asm = analysis([joint("Fixed_BLOCK", ["PLATE", "BLOCK"]),
                        joint("Fixed_SHIM", ["PLATE", "SHIM"])])
        self.assertIn("no grounded joint — assembly may "
                      "be under-constrained", self.messages(asm))

    def test_broken_link_fails(self):
        asm = analysis([joint("GroundedJoint", ["PLATE"], grounded=True,
                              jtype="Grounded")], instances=("PLATE",))
        asm["instances"].append({"name": "GONE", "label": "GONE",
                                 "broken": True})
        f = judge.assembly_findings(asm)
        self.assertEqual([x["severity"] for x in f], ["FAIL"])
        self.assertIn("broken link GONE", f[0]["message"])

    def test_standalone_part_pack_has_no_assembly_findings(self):
        self.assertEqual(judge.assembly_findings(
            {"file": "", "instances": [], "joints": [], "issues": []}), [])


import common  # noqa: E402  (after sys.path is set up)


@unittest.skipUnless(common.FREECAD_APPIMAGE.exists(),
                     f"FreeCAD not at {common.FREECAD_APPIMAGE}")
class TestRealFreeCADAssembly(unittest.TestCase):
    """End to end through headless FreeCAD on real Assembly-workbench joints.

    One fixture pair is built once: an assembly whose instances are all
    joined, and one with a part nobody joined.
    """

    tmp = None
    reports = {}

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="fabpipe-joints-")
        d = cls.tmp.name
        common.run_freecad(Path(__file__).resolve().parent
                           / "make_fixtures.py", d, timeout=600)
        for name in ("asm_joined", "asm_loose"):
            out = os.path.join(d, name + ".json")
            common.run_freecad(SRC / "fc_analyse.py", d,
                               name + ".FCStd", out, timeout=600)
            with open(out) as f:
                cls.reports[name] = json.load(f)["assembly"]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_fixture_is_the_freecad_11_layout(self):
        # guards the fixture itself: if a later FreeCAD writes joint
        # references some other way, this is where it shows up
        asm = self.reports["asm_joined"]
        self.assertEqual(asm["workbench"], "builtin-assembly")
        self.assertEqual(len(asm["joints"]), 3)
        self.assertEqual(sorted(i["name"] for i in asm["instances"]),
                         ["BLOCK", "PLATE", "SHIM"])

    def test_every_joint_resolves_its_instances(self):
        joints = {j["name"]: j for j in self.reports["asm_joined"]["joints"]}
        self.assertEqual(joints["GroundedJoint"]["connects"], ["PLATE"])
        self.assertTrue(joints["GroundedJoint"]["grounded"])
        self.assertEqual(sorted(joints["Fixed_BLOCK"]["connects"]),
                         ["BLOCK", "PLATE"])
        self.assertEqual(sorted(joints["Fixed_SHIM"]["connects"]),
                         ["PLATE", "SHIM"])

    def test_joined_assembly_raises_nothing(self):
        self.assertEqual(
            judge.assembly_findings(self.reports["asm_joined"]), [])

    def test_unjoined_part_is_warned_about(self):
        f = judge.assembly_findings(self.reports["asm_loose"])
        self.assertEqual([x["severity"] for x in f], ["WARN"])
        self.assertIn("instances with no joint: SHIM", f[0]["message"])


if __name__ == "__main__":
    unittest.main()
