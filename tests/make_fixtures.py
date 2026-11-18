# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Pranay Kiran
"""Build throwaway FreeCAD 1.1 assembly fixtures. Runs INSIDE FreeCAD:

    FABPIPE_ARGS='["<out_dir>"]' FreeCAD.AppImage -c make_fixtures.py </dev/null

Writes, into <out_dir>:
  plate.FCStd, block.FCStd, shim.FCStd   -- three PartDesign parts
  asm_joined.FCStd    -- 3 instances, 1 grounded + 2 Fixed joints, all joined
  asm_loose.FCStd     -- 3 instances, 1 grounded + 1 Fixed joint; "shim" is
                         genuinely unjoined and must still be warned about

The joints are made with the real Assembly workbench (JointObject), so the
references land in whatever layout this FreeCAD version actually writes --
on 1.1 that is (AssemblyObject, ["LinkName.Vertex1", ...]), the layout that
issue #1 was about. Nothing here is a hand-written stand-in for it.
"""

import json
import os
import sys

import FreeCAD
import JointObject


def p(*a):
    print(*a, flush=True)


def make_part(out_dir, name, dx, dy, dz):
    doc = FreeCAD.newDocument(name)
    body = doc.addObject("PartDesign::Body", "Body")
    box = doc.addObject("PartDesign::AdditiveBox", "Box")
    body.addObject(box)
    box.Length, box.Width, box.Height = dx, dy, dz
    doc.recompute()
    path = os.path.join(out_dir, name + ".FCStd")
    doc.saveAs(path)
    return path


def add_link(doc, asm, part_path, label, pos):
    part_doc = FreeCAD.openDocument(part_path)
    tip = [o for o in part_doc.Objects if o.TypeId == "PartDesign::Body"][0]
    link = doc.addObject("App::Link", label)
    link.LinkedObject = tip
    link.Label = label
    link.Placement = FreeCAD.Placement(FreeCAD.Vector(*pos),
                                       FreeCAD.Rotation())
    asm.addObject(link)
    return link


def ground(doc, group, link):
    j = doc.addObject("App::FeaturePython", "GroundedJoint")
    group.addObject(j)
    JointObject.GroundedJoint(j, link)
    return j


def fixed(doc, asm, group, name, link_a, link_b):
    """A Fixed joint between two instances, made the way the workbench does.

    JointObject.Joint sets up the Reference properties; the references are
    then filled in exactly as the GUI fills them on this version -- see
    UtilsAssembly.getObjectInPart: the reference object is the assembly and
    the subname path starts with the instance name.
    """
    j = doc.addObject("App::FeaturePython", name)
    # the proxy looks up its owning assembly as it is constructed, so the
    # joint has to sit in the assembly's Joints group first
    group.addObject(j)
    JointObject.Joint(j, JointObject.JointTypes.index("Fixed"))
    j.Reference1 = (asm, [f"{link_a.Name}.Vertex1", f"{link_a.Name}.Vertex1"])
    j.Reference2 = (asm, [f"{link_b.Name}.Vertex1", f"{link_b.Name}.Vertex1"])
    return j


def build(out_dir, name, loose):
    doc = FreeCAD.newDocument(name)
    # an App::Link into another document is an XLink: FreeCAD refuses to
    # make one until the owning document has a file of its own
    path = os.path.join(out_dir, name + ".FCStd")
    doc.saveAs(path)
    asm = doc.addObject("Assembly::AssemblyObject", "Assembly")
    plate = add_link(doc, asm, os.path.join(out_dir, "plate.FCStd"),
                     "PLATE", (0, 0, 0))
    block = add_link(doc, asm, os.path.join(out_dir, "block.FCStd"),
                     "BLOCK", (0, 0, 10))
    shim = add_link(doc, asm, os.path.join(out_dir, "shim.FCStd"),
                    "SHIM", (40, 0, 0))
    group = doc.addObject("Assembly::JointGroup", "Joints")
    asm.addObject(group)
    ground(doc, group, plate)
    fixed(doc, asm, group, "Fixed_BLOCK", plate, block)
    if not loose:
        fixed(doc, asm, group, "Fixed_SHIM", plate, shim)
    doc.recompute()
    doc.save()
    return path


def main():
    out_dir = json.loads(os.environ["FABPIPE_ARGS"])[0]
    os.makedirs(out_dir, exist_ok=True)
    make_part(out_dir, "plate", 60, 40, 10)
    make_part(out_dir, "block", 20, 20, 20)
    make_part(out_dir, "shim", 20, 20, 2)
    build(out_dir, "asm_joined", loose=False)
    build(out_dir, "asm_loose", loose=True)
    p("FIXTURES_WRITTEN", out_dir)


main()
sys.stdout.flush()
os._exit(0)
