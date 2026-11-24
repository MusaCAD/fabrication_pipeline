# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Pranay Kiran
"""Build throwaway FreeCAD 1.1 assembly fixtures. Runs INSIDE FreeCAD:

    FABPIPE_ARGS='["<out_dir>"]' FreeCAD.AppImage -c make_fixtures.py </dev/null

Writes, into <out_dir>:
  plate.FCStd, block.FCStd, shim.FCStd   -- three PartDesign parts
  asm_joined.FCStd    -- 3 instances, 1 grounded + 2 Fixed joints, all joined
  asm_loose.FCStd     -- 3 instances, 1 grounded + 1 Fixed joint; "shim" is
                         genuinely unjoined and must still be warned about
and, for sub-assemblies (issue #3):
  asm_sub.FCStd       -- a sub-assembly: plate + block, grounded + Fixed
  asm_sub2.FCStd      -- a second sub-assembly that uses block AGAIN, + shim
  asm_sub_loose.FCStd -- a sub-assembly whose shim nobody joined
  asm_nested.FCStd    -- top: AssemblyLink -> asm_sub, plus a direct part,
                         joined with Fixed, Revolute and Cylindrical joints
                         (FreeCAD 1.1 has no "Coincident"/"Concentric" type;
                         these are their names in this version)
  asm_nested_loose.FCStd -- top over asm_sub_loose: the unjoined part sits
                         one level down and must still be reported
  asm_shared.FCStd    -- top over asm_sub AND asm_sub2: block is ONE part
                         file used by two different sub-assemblies
  asm_plainlink.FCStd -- the other shape a sub-assembly takes: a plain
                         App::Link straight at another file's AssemblyObject
                         (what a script builds; real models use it)
  cyc_a.FCStd, cyc_b.FCStd -- each contains the other. FreeCAD saves this
                         happily and then SEGFAULTS when asked for the
                         link's Shape, so the walk has to refuse it.
and, for the VISIBILITY check (issue #23) — the real failure: a model
built by script and saved with everything hidden, which opens showing
nothing at all:
  hidden_part.FCStd   -- a PartDesign body whose body AND tip are hidden
  hidden_tip.FCStd    -- the body is shown, its tip feature is not
  hidden_asm.FCStd    -- an assembly whose every instance link is hidden
and, for assembly SHEETS (issue #5):
  rod.FCStd, screw.FCStd   -- turned parts, which a section does not cut
                         along their length (ISO 128-50)
  asm_left.FCStd, asm_right.FCStd -- two sub-assemblies that share a part
  asm_sheet.FCStd     -- top: both sub-assemblies side by side (their
                         blocks TOUCH in the cut, so the hatch has to tell
                         them apart), a rod spanning both and a small screw
                         for a detail view

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


def make_rod(out_dir, name, dia, length):
    """A turned part: a cylinder of `dia` x `length`, built along +Z. The
    assembly turns it where it wants it (a link's own rotation), the way a
    real model reuses one rod in several orientations."""
    doc = FreeCAD.newDocument(name)
    body = doc.addObject("PartDesign::Body", "Body")
    cyl = doc.addObject("PartDesign::AdditiveCylinder", "Cylinder")
    body.addObject(cyl)
    cyl.Radius, cyl.Height, cyl.Angle = dia / 2.0, length, 360.0
    doc.recompute()
    path = os.path.join(out_dir, name + ".FCStd")
    doc.saveAs(path)
    return path


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


def hide(obj):
    """Hide an object the way a script that never touched Visibility
    leaves it — or the way a user hides something and saves."""
    obj.Visibility = False
    return obj


def make_hidden_part(out_dir, name, dx, dy, dz, body=False, tip=False):
    """A part saved with its body and/or its tip feature hidden. Opened on
    its own it shows nothing, and the geometry is all there: the only hint
    is that the 3D view is empty (issue #23)."""
    doc = FreeCAD.newDocument(name)
    b = doc.addObject("PartDesign::Body", "Body")
    box = doc.addObject("PartDesign::AdditiveBox", "Box")
    b.addObject(box)
    box.Length, box.Width, box.Height = dx, dy, dz
    doc.recompute()
    if body:
        hide(b)
    if tip:
        hide(b.Tip)
    path = os.path.join(out_dir, name + ".FCStd")
    doc.saveAs(path)
    return path


def make_hidden_assembly(out_dir, name, members):
    """An assembly whose instance links are all hidden."""
    doc = FreeCAD.newDocument(name)
    asm = doc.addObject("Assembly::AssemblyObject", "Assembly")
    path = os.path.join(out_dir, name + ".FCStd")
    doc.saveAs(path)
    group = doc.addObject("App::DocumentObjectGroup", "Joints")
    asm.addObject(group)
    links = [add_link(doc, asm, os.path.join(out_dir, part + ".FCStd"),
                      label, pos) for part, label, pos in members]
    doc.recompute()
    ground(doc, group, links[0])
    for link in links:
        hide(link)
    doc.recompute()
    doc.save()
    return path


def add_link(doc, asm, part_path, label, pos, rot=None):
    part_doc = FreeCAD.openDocument(part_path)
    tip = [o for o in part_doc.Objects if o.TypeId == "PartDesign::Body"][0]
    link = doc.addObject("App::Link", label)
    link.LinkedObject = tip
    link.Label = label
    rotation = FreeCAD.Rotation(FreeCAD.Vector(*rot[0]), rot[1]) if rot \
        else FreeCAD.Rotation()
    link.Placement = FreeCAD.Placement(FreeCAD.Vector(*pos), rotation)
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


def cyclic_pair(out_dir):
    """Two assemblies that contain each other.

    A model nobody means to build, but the walk must survive one: FreeCAD
    1.1.1 accepts and saves it, and evaluating such a link's Shape takes
    the process down with a segmentation fault.
    """
    a = FreeCAD.newDocument("cyc_a")
    a.saveAs(os.path.join(out_dir, "cyc_a.FCStd"))
    aa = a.addObject("Assembly::AssemblyObject", "Assembly")
    b = FreeCAD.newDocument("cyc_b")
    b.saveAs(os.path.join(out_dir, "cyc_b.FCStd"))
    ba = b.addObject("Assembly::AssemblyObject", "Assembly")
    l1 = a.addObject("App::Link", "B_IN_A")
    l1.LinkedObject = ba
    aa.addObject(l1)
    a.recompute()
    a.save()
    l2 = b.addObject("App::Link", "A_IN_B")
    l2.LinkedObject = aa
    ba.addObject(l2)
    b.recompute()
    b.save()
    a.save()
    return os.path.join(out_dir, "cyc_a.FCStd")


def joint(doc, asm, group, name, link_a, link_b, jtype="Fixed"):
    """Any joint type between two instances, referenced the 1.1 way."""
    j = doc.addObject("App::FeaturePython", name)
    group.addObject(j)
    JointObject.Joint(j, JointObject.JointTypes.index(jtype))
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


def sub_assembly(out_dir, name, members, joints=("Fixed",), loose=()):
    """A sub-assembly document: `members` are (part file stem, label, pos),
    `joints` the types to join member 0 to the others with, and `loose` the
    labels nobody joins at all."""
    doc = FreeCAD.newDocument(name)
    path = os.path.join(out_dir, name + ".FCStd")
    doc.saveAs(path)
    asm = doc.addObject("Assembly::AssemblyObject", "Assembly")
    group = doc.addObject("Assembly::JointGroup", "Joints")
    asm.addObject(group)
    links = []
    for stem, label, pos in members:
        links.append(add_link(doc, asm, os.path.join(out_dir,
                                                     stem + ".FCStd"),
                              label, pos))
    ground(doc, group, links[0])
    for i, lk in enumerate(links[1:], start=1):
        if lk.Label in loose:
            continue
        jtype = joints[(i - 1) % len(joints)]
        joint(doc, asm, group, f"{jtype}_{lk.Name}", links[0], lk, jtype)
    doc.recompute()
    doc.save()
    return doc, asm, path


def nested(out_dir, name, subs, extra=None, extras=(), joints=()):
    """A top assembly over sub-assemblies.

    `subs` are (sub AssemblyObject, label, pos, plain) — `plain` picks the
    shape: an Assembly::AssemblyLink (the GUI's own, which mirrors the
    sub-assembly's parts into this document) or a plain App::Link straight
    at the sub-assembly object. `joints` are (type, subname1, subname2)
    triples written exactly as the workbench writes them, so the references
    reach THROUGH a sub-assembly."""
    doc = FreeCAD.newDocument(name)
    path = os.path.join(out_dir, name + ".FCStd")
    doc.saveAs(path)
    asm = doc.addObject("Assembly::AssemblyObject", "Assembly")
    group = doc.addObject("Assembly::JointGroup", "Joints")
    asm.addObject(group)
    first = None
    for sub_asm, label, pos, plain in subs:
        if plain:
            lk = doc.addObject("App::Link", label)
            lk.LinkedObject = sub_asm
        else:
            lk = doc.addObject("Assembly::AssemblyLink", label)
            lk.LinkedObject = sub_asm
        lk.Label = label
        lk.Placement = FreeCAD.Placement(FreeCAD.Vector(*pos),
                                         FreeCAD.Rotation())
        asm.addObject(lk)
        first = first or lk
    for item in ([extra] if extra else []) + list(extras):
        stem, label, pos = item[:3]
        rot = item[3] if len(item) > 3 else None
        add_link(doc, asm, os.path.join(out_dir, stem + ".FCStd"), label,
                 pos, rot)
    ground(doc, group, first)
    for jtype, s1, s2 in joints:
        j = doc.addObject("App::FeaturePython", f"{jtype}_{s1.split('.')[0]}")
        group.addObject(j)
        JointObject.Joint(j, JointObject.JointTypes.index(jtype))
        j.Reference1 = (asm, [s1, s1])
        j.Reference2 = (asm, [s2, s2])
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

    # --- sub-assemblies (issue #3) ---------------------------------
    _, sub1, _ = sub_assembly(out_dir, "asm_sub",
                              [("plate", "PLATE", (0, 0, 0)),
                               ("block", "BLOCK", (0, 0, 10))])
    # block AGAIN, in a different sub-assembly, plus a shim
    _, sub2, _ = sub_assembly(out_dir, "asm_sub2",
                              [("block", "BLOCK2", (0, 0, 0)),
                               ("shim", "SHIM2", (0, 0, 20))],
                              joints=("Revolute",))
    _, sub_loose, _ = sub_assembly(out_dir, "asm_sub_loose",
                                   [("plate", "PLATE", (0, 0, 0)),
                                    ("block", "BLOCK", (0, 0, 10)),
                                    ("shim", "SHIM", (40, 0, 0))],
                                   loose=("SHIM",))
    # two levels, three joint types, every instance joined
    nested(out_dir, "asm_nested",
           [(sub1, "SUB1", (100, 0, 0), False)],
           extra=("shim", "TSHIM", (0, 0, 60)),
           joints=[("Fixed", "SUB1.PLATE.Vertex1", "TSHIM.Vertex1"),
                   ("Revolute", "SUB1.BLOCK.Vertex1", "TSHIM.Vertex1"),
                   ("Cylindrical", "SUB1.PLATE.Vertex1",
                    "SUB1.BLOCK.Vertex1")])
    nested(out_dir, "asm_nested_loose",
           [(sub_loose, "SUB1", (100, 0, 0), False)])
    nested(out_dir, "asm_shared",
           [(sub1, "SUB1", (0, 0, 0), False),
            (sub2, "SUB2", (200, 0, 0), False)],
           joints=[("Fixed", "SUB1.PLATE.Vertex1", "SUB2.BLOCK2.Vertex1")])
    # --- assembly sheets (issue #5) --------------------------------
    make_rod(out_dir, "rod", 8.0, 120.0)
    make_rod(out_dir, "screw", 4.0, 10.0)
    _, left, _ = sub_assembly(out_dir, "asm_left",
                              [("plate", "LPLATE", (0, 0, 0)),
                               ("block", "LBLOCK", (0, 0, 10))])
    # the same block part again, in the other sub-assembly
    _, right, _ = sub_assembly(out_dir, "asm_right",
                               [("block", "RBLOCK", (0, 0, 10)),
                                ("shim", "RSHIM", (0, 0, 30))])
    nested(out_dir, "asm_sheet",
           [(left, "SUB_A", (0, 0, 0), False),
            (right, "SUB_B", (20, 0, 0), False)],
           extras=[("rod", "ROD", (-10, 10, 20), ((0, 1, 0), 90)),
                   ("screw", "SCREW", (50, 10, 14))],
           joints=[("Fixed", "SUB_A.LPLATE.Vertex1", "SUB_B.RBLOCK.Vertex1"),
                   ("Fixed", "SUB_A.LPLATE.Vertex1", "ROD.Vertex1"),
                   ("Fixed", "SUB_A.LPLATE.Vertex1", "SCREW.Vertex1")])
    # --- visibility (issue #23) ------------------------------------
    make_hidden_part(out_dir, "hidden_part", 30, 20, 5, body=True, tip=True)
    make_hidden_part(out_dir, "hidden_tip", 30, 20, 5, tip=True)
    make_hidden_assembly(out_dir, "hidden_asm",
                         [("plate", "HPLATE", (0, 0, 0)),
                          ("block", "HBLOCK", (0, 0, 10))])
    cyclic_pair(out_dir)
    nested(out_dir, "asm_plainlink",
           [(sub1, "SUB1", (100, 0, 0), True)],
           extra=("shim", "TSHIM", (0, 0, 60)),
           joints=[("Fixed", "SUB1.PLATE.Vertex1", "TSHIM.Vertex1")])
    p("FIXTURES_WRITTEN", out_dir)


main()
sys.stdout.flush()
os._exit(0)
