# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Pranay Kiran
"""What a model file SHOWS when it is opened (issue #23).

Run with:  python3 -m unittest discover -s tests -v

The real failure: models built by script were saved with everything
hidden. The geometry was all there and every check passed, but opening a
file showed an empty 3D view, and the only way to see the part was to
expand each body to its last feature and unhide it by hand. /analyse now
reports, per file, what it shows; /judge fails a file that shows nothing.

The fast tests use stand-in objects; the slow one builds real FreeCAD 1.1
files (tests/make_fixtures.py: hidden_part, hidden_tip, hidden_asm) and
runs the real fc_analyse.py against them.
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

sys.modules.setdefault("FreeCAD", types.ModuleType("FreeCAD"))

import analyse  # noqa: E402
import common  # noqa: E402
import fc_analyse  # noqa: E402
import fc_tree  # noqa: E402
import judge  # noqa: E402


class Obj:
    """Stand-in for a FreeCAD document object."""

    def __init__(self, name, type_id, label=None, visible=True, **props):
        self.Name = name
        self.TypeId = type_id
        self.Label = label or name
        self.Visibility = visible
        self.OutList = []
        for k, v in props.items():
            setattr(self, k, v)


class Doc:
    def __init__(self, name, objects, file=""):
        self.Name = name
        self.Objects = list(objects)
        self.FileName = file or f"/m/{name}.FCStd"


def body(name="Body", visible=True, tip_visible=True, label=None):
    tip = Obj("Pad", "PartDesign::Pad", visible=tip_visible)
    return Obj(name, "PartDesign::Body", label=label, visible=visible,
               Tip=tip), tip


class PartVisibilityTest(unittest.TestCase):
    """A part file: each body visible, and the tip feature it ends on."""

    def test_a_part_that_opens_showing_itself_is_clean(self):
        b, _tip = body()
        v = fc_analyse.part_visibility(Doc("plate", [b]), b)
        self.assertEqual(v["hidden"], [])
        self.assertFalse(v["nothing_visible"])
        self.assertEqual(v["kind"], "part")
        self.assertEqual(v["objects"][0]["tip"], "Pad")

    def test_a_hidden_body_is_reported(self):
        b, _tip = body(visible=False)
        v = fc_analyse.part_visibility(Doc("plate", [b]), b)
        self.assertTrue(v["nothing_visible"])
        self.assertIn("body Body is hidden", v["hidden"])

    def test_a_hidden_tip_feature_is_reported(self):
        b, _tip = body(tip_visible=False)
        v = fc_analyse.part_visibility(Doc("plate", [b]), b)
        self.assertTrue(v["nothing_visible"])
        self.assertIn("tip feature", " ".join(v["hidden"]))

    def test_one_body_of_two_hidden_is_not_nothing_visible(self):
        b1, _ = body("Body", visible=False)
        b2, _ = body("Body2")
        v = fc_analyse.part_visibility(Doc("plate", [b1, b2]), b1)
        self.assertTrue(v["hidden"])
        self.assertFalse(v["nothing_visible"])

    def test_the_features_behind_the_tip_are_not_a_finding(self):
        b, _tip = body()
        sketch = Obj("Sketch", "Sketcher::SketchObject", visible=False)
        pocket = Obj("Pocket", "PartDesign::Pocket", visible=False)
        v = fc_analyse.part_visibility(Doc("plate", [b, sketch, pocket]), b)
        self.assertEqual(v["hidden"], [])

    def test_a_part_that_is_not_a_partdesign_body_uses_its_tip(self):
        solid = Obj("Bend", "Part::FeaturePython", visible=False)
        v = fc_analyse.part_visibility(Doc("bracket", [solid]), solid)
        self.assertTrue(v["nothing_visible"])
        self.assertIn("Bend is hidden", " ".join(v["hidden"]))


class AssemblyVisibilityTest(unittest.TestCase):
    """An assembly file: every instance link visible."""

    def doc(self, *visible):
        objs = [Obj(f"L{i}", "App::Link", visible=v)
                for i, v in enumerate(visible)]
        return Doc("asm", objs)

    def test_every_link_shown_is_clean(self):
        v = fc_analyse.links_visibility(self.doc(True, True), "assembly")
        self.assertEqual(v["hidden"], [])
        self.assertFalse(v["nothing_visible"])
        self.assertEqual(v["links"], 2)

    def test_one_hidden_link_is_reported(self):
        v = fc_analyse.links_visibility(self.doc(True, False), "assembly")
        self.assertEqual(len(v["hidden"]), 1)
        self.assertFalse(v["nothing_visible"])

    def test_all_hidden_is_nothing_visible(self):
        v = fc_analyse.links_visibility(self.doc(False, False), "assembly")
        self.assertTrue(v["nothing_visible"])

    def test_an_assembly_with_no_links_says_nothing(self):
        v = fc_analyse.links_visibility(Doc("asm", []), "assembly")
        self.assertFalse(v["nothing_visible"])


class AssemblyTreeVisibilityTest(unittest.TestCase):
    """The top document's entry covers every instance under it: a
    sub-assembly's parts are mirrored here as links of their own."""

    def nodes(self, *flags):
        return [{"name": f"L{i}", "label": f"L{i}", "path": f"SUB/L{i}",
                 "kind": "part", "visible": v, "children": []}
                for i, v in enumerate(flags)]

    def test_a_hidden_instance_deep_in_the_tree_is_named(self):
        link = Obj("SUB", "App::Link")
        v = fc_analyse.assembly_visibility(Doc("asm", [link]),
                                           self.nodes(True, False))
        self.assertIn("SUB/L1 is hidden", v["hidden"])
        self.assertFalse(v["nothing_visible"])
        self.assertEqual(v["instances"], 2)

    def test_a_tree_with_nothing_hidden_is_clean(self):
        link = Obj("SUB", "App::Link")
        v = fc_analyse.assembly_visibility(Doc("asm", [link]),
                                           self.nodes(True, True))
        self.assertEqual(v["hidden"], [])


class TreeVisibilityTest(unittest.TestCase):
    """Every instance of the tree remembers whether it is shown."""

    def test_the_walk_records_it(self):
        part = Obj("Body", "PartDesign::Body")
        part.Document = types.SimpleNamespace(Name="plate", FileName="/p.FC")
        link = Obj("PLATE", "App::Link", visible=False)
        link.LinkedObject = part
        doc = Doc("asm", [link])
        nodes = fc_tree.walk(doc, [])
        self.assertEqual(len(nodes), 1)
        self.assertFalse(nodes[0]["visible"])


class JudgeVisibilityTest(unittest.TestCase):
    """A file that opens with nothing visible is a FAIL."""

    def analysis(self, *entries):
        return {"visibility": list(entries)}

    def entry(self, name, kind="part", nothing=False, hidden=()):
        return {"file": f"/m/{name}", "document": name.split(".")[0],
                "kind": kind, "nothing_visible": nothing,
                "hidden": list(hidden)}

    def test_nothing_visible_fails(self):
        out = judge.visibility_findings(self.analysis(
            self.entry("plug.FCStd", nothing=True,
                       hidden=["body Body is hidden"])))
        self.assertEqual([f["severity"] for f in out], ["FAIL"])
        self.assertIn("plug.FCStd", out[0]["message"])
        self.assertIn("body Body is hidden", out[0]["message"])
        self.assertIn("Space", out[0]["message"])
        self.assertIn("built by script", out[0]["message"])

    def test_something_hidden_warns(self):
        out = judge.visibility_findings(self.analysis(
            self.entry("asm.FCStd", kind="assembly",
                       hidden=["BLOCK (BLOCK) is hidden"])))
        self.assertEqual([f["severity"] for f in out], ["WARN"])

    def test_a_clean_set_says_nothing(self):
        self.assertEqual(judge.visibility_findings(self.analysis(
            self.entry("a.FCStd"), self.entry("b.FCStd"))), [])

    def test_an_analysis_from_before_the_check_says_nothing(self):
        self.assertEqual(judge.visibility_findings({}), [])

    def test_it_reaches_the_verdict(self):
        a = {"assembly": {"file": "", "instances": [], "joints": [],
                          "issues": []},
             "visibility": [self.entry("plug.FCStd", nothing=True)]}
        findings = judge.assembly_findings(a["assembly"]) \
            + judge.visibility_findings(a)
        self.assertIn("FAIL", [f["severity"] for f in findings])


class VisibilityReportTest(unittest.TestCase):
    """The analysis report says what each file shows."""

    def report(self, *entries):
        return analyse.to_markdown({
            "model_dir": "/m", "freecad_version": "1.1.1", "parts": [],
            "assembly": {"file": ""}, "visibility": list(entries)})

    def test_a_file_that_shows_nothing_is_called_out(self):
        md = self.report({"file": "/m/plug.FCStd", "kind": "part",
                          "nothing_visible": True, "hidden": []})
        self.assertIn("## Visibility", md)
        self.assertIn("plug.FCStd", md)
        self.assertIn("NOTHING visible", md)

    def test_a_clean_set_says_so_in_one_line(self):
        md = self.report({"file": "/m/a.FCStd", "kind": "part",
                          "nothing_visible": False, "hidden": []})
        self.assertIn("open showing their geometry", md)

    def test_an_analysis_without_the_section_is_unchanged(self):
        md = analyse.to_markdown({"model_dir": "/m",
                                  "freecad_version": "1.1.1", "parts": [],
                                  "assembly": {"file": ""}})
        self.assertNotIn("## Visibility", md)


@unittest.skipUnless(common.FREECAD_APPIMAGE.exists(),
                     f"FreeCAD not at {common.FREECAD_APPIMAGE}")
class RealHiddenModelTest(unittest.TestCase):
    """The real thing: FreeCAD files saved with everything hidden."""

    tmp = None
    report = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="fabpipe-vis-")
        d = cls.tmp.name
        common.run_freecad(Path(__file__).resolve().parent
                           / "make_fixtures.py", d, timeout=900)
        out = os.path.join(d, "vis.json")
        common.run_freecad(SRC / "fc_analyse.py", d, "hidden_asm.FCStd",
                           out, os.path.join(d, "hidden_part.FCStd"),
                           os.path.join(d, "hidden_tip.FCStd"),
                           timeout=900)
        with open(out) as f:
            cls.report = json.load(f)
        cls.by_doc = {v["document"]: v for v in cls.report["visibility"]}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_an_assembly_whose_links_are_all_hidden_shows_nothing(self):
        v = self.by_doc["hidden_asm"]
        self.assertTrue(v["nothing_visible"])
        self.assertEqual(len(v["hidden"]), 2)

    def test_a_part_whose_body_is_hidden_shows_nothing(self):
        v = self.by_doc["hidden_part"]
        self.assertTrue(v["nothing_visible"])
        self.assertIn("body", " ".join(v["hidden"]))

    def test_a_part_whose_tip_is_hidden_shows_nothing(self):
        v = self.by_doc["hidden_tip"]
        self.assertTrue(v["nothing_visible"])
        self.assertIn("tip feature", " ".join(v["hidden"]))

    def test_an_ordinary_part_is_clean(self):
        v = self.by_doc["plate"]
        self.assertFalse(v["nothing_visible"])
        self.assertEqual(v["hidden"], [])
        self.assertTrue(v["objects"][0]["visible"])
        self.assertTrue(v["objects"][0]["tip_visible"])

    def test_the_judge_fails_the_set(self):
        out = judge.visibility_findings(self.report)
        fails = [f for f in out if f["severity"] == "FAIL"]
        self.assertEqual(len(fails), 3)


if __name__ == "__main__":
    unittest.main()
