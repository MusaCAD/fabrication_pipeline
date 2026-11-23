# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Pranay Kiran
"""FreeCAD-side model interrogation. Runs INSIDE headless FreeCAD:

    FABPIPE_ARGS='["<model_dir>", "<assembly.FCStd>", "<out.json>"]' \
        FreeCAD.AppImage -c fc_analyse.py < /dev/null

Emits one JSON document describing the assembly and every linked part:
objects, solids, bounding boxes, volumes, sketch constraint status, materials,
assembly joints, and broken links. Read-only: documents are never saved.
"""

import json
import os
import sys

import FreeCAD  # noqa: F401  (import establishes the App context)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fc_shapes  # noqa: E402  (needs the path above)
import fc_tree  # noqa: E402


def p(*a):
    print(*a, flush=True)


def vec(v):
    return [round(v.x, 6), round(v.y, 6), round(v.z, 6)]


def sketch_info(obj):
    dims = []
    for c in obj.Constraints:
        if c.Type in ("Distance", "DistanceX", "DistanceY", "Radius",
                      "Diameter", "Angle"):
            dims.append({"name": c.Name or "", "type": c.Type,
                         "value": round(float(c.Value), 6)})
    try:
        solved = obj.solve()
    except Exception:
        solved = -1
    return {
        "label": obj.Label,
        "constraints": len(obj.Constraints),
        "dimensional": dims,
        "solver_ok": solved == 0,
        "fully_constrained": bool(getattr(obj, "FullyConstrained", False)),
    }


def material_of(obj):
    m = getattr(obj, "ShapeMaterial", None)
    if m is None:
        return None
    name = getattr(m, "Name", None) or str(m)
    return name


def body_info(obj):
    sh = obj.Shape
    bb = sh.BoundBox
    return {
        "label": obj.Label,
        "object": obj.Name,
        "type": obj.TypeId,
        "solids": len(sh.Solids),
        "faces": len(sh.Faces),
        "volume_mm3": round(sh.Volume, 3),
        "area_mm2": round(sh.Area, 3),
        "bbox_min": [round(bb.XMin, 6), round(bb.YMin, 6), round(bb.ZMin, 6)],
        "bbox_max": [round(bb.XMax, 6), round(bb.YMax, 6), round(bb.ZMax, 6)],
        "bbox_size": [round(bb.XLength, 6), round(bb.YLength, 6),
                      round(bb.ZLength, 6)],
        "material": material_of(obj),
    }


def analyse_part(path):
    doc = FreeCAD.openDocument(path)
    sketches, issues = [], []
    for obj in doc.Objects:
        if obj.TypeId == "Sketcher::SketchObject":
            sketches.append(sketch_info(obj))
    tip, flat = fc_shapes.tips(doc)
    bodies = [body_info(tip)] if tip is not None else []
    if tip is None:
        issues.append("no solid found in this document")
    out = {"file": path, "document": doc.Name, "bodies": bodies,
           "sketches": sketches, "issues": issues}
    if flat is not None:
        # a sheet-metal part: the vendor cuts the blank and bends it, so the
        # drawing needs the developed shape and the bends, not a section
        fi = body_info(flat)
        fi["thickness_mm"] = fc_shapes.thickness_of(flat.Shape)
        s = sorted(fi["bbox_size"])
        fi["blank_mm"] = [s[2], s[1]]
        out["flat"] = fi
        out["bends"] = fc_shapes.bend_features(doc)
        if not out["bends"]:
            issues.append("flat pattern present but no bend features found")
    return out


JOINT_REF_PROPS = ("Reference1", "Reference2")


def joint_connects(obj, nodes, prefix=""):
    """Instance PATHS a joint connects, newest layout first, in order.

    `nodes` are the instances of the assembly this joint belongs to and
    `prefix` their path prefix, so a joint inside a sub-assembly names
    "SUB1/SPLATE" and a joint in the top document naming the same part
    resolves to the same string. That shared identity is what lets /judge
    see a part as joined wherever its joint was authored (issue #3).
    """
    names = []

    def add(n):
        if n and n not in names:
            names.append(n)

    ground = getattr(obj, "ObjectToGround", None)
    if ground is not None:
        add(prefix + getattr(ground, "Name", ""))
    for prop in JOINT_REF_PROPS:
        for n in fc_tree.resolve_ref_path(getattr(obj, prop, None), nodes):
            add(n)
    for o in obj.OutList:       # pre-1.1 layouts and other workbenches
        if o.TypeId in fc_tree.LINK_TYPES:
            add(prefix + o.Name)
    return names


def is_joint(obj):
    return obj.TypeId == "App::FeaturePython" and (
        hasattr(obj, "JointType") or hasattr(obj, "ObjectToGround")
        or "Grounded" in obj.Name)


def joint_info(obj, nodes, prefix="", assembly=""):
    grounded = (hasattr(obj, "ObjectToGround")
                or "Grounded" in obj.Label
                or obj.Name.startswith("GroundedJoint"))
    jtype = getattr(obj, "JointType", None)
    return {
        "name": obj.Name, "label": obj.Label,
        "type": str(jtype) if jtype else ("Grounded" if grounded else "?"),
        "grounded": grounded,
        # a suppressed joint is present but constrains nothing
        "suppressed": bool(getattr(obj, "Suppressed", False)),
        # which assembly document authored it ("" = the top one): a
        # sub-assembly's joints constrain only its own parts
        "assembly": assembly,
        "connects": joint_connects(obj, nodes, prefix),
    }


def node_geometry(node):
    """Fill a tree node's placement/bbox fields from the private handles
    fc_tree left on it, then drop them (they hold FreeCAD objects)."""
    plc = node.pop("_placement", None)
    shape = node.pop("_shape", None)
    node.pop("_document", None)        # a FreeCAD handle, not report data
    if plc is not None:
        node["position"] = vec(plc.Base)
        node["rotation_axis"] = vec(plc.Rotation.Axis)
        node["rotation_deg"] = round(plc.Rotation.Angle * 180.0
                                     / 3.141592653589793, 6)
    try:
        bb = shape.BoundBox
        node["bbox_min"] = [round(bb.XMin, 6), round(bb.YMin, 6),
                            round(bb.ZMin, 6)]
        node["bbox_max"] = [round(bb.XMax, 6), round(bb.YMax, 6),
                            round(bb.ZMax, 6)]
    except Exception:
        pass
    for kid in node.get("children", []):
        node_geometry(kid)
    return node


def assembly_joints(doc, nodes, prefix="", assembly=""):
    """The joints of one assembly document, resolved against ITS instances."""
    return [joint_info(obj, nodes, prefix, assembly)
            for obj in doc.Objects if is_joint(obj)]


def collect_joints(doc, nodes, seen=None):
    """Joints of this assembly and of every sub-assembly under it.

    A sub-assembly's joints live in ITS OWN file and constrain its own
    parts, so they are read there and re-addressed to the parent's paths
    (issue #3). One sub-assembly document is read once per instance path:
    the same file used twice holds twice as many jointed parts.
    """
    seen = set() if seen is None else seen
    joints = list(assembly_joints(doc, nodes))
    for node in fc_tree.flatten(nodes):
        if node.get("kind") != "assembly" or node.get("cyclic"):
            continue
        sub_name = node.get("sub_document")
        sub = FreeCAD.getDocument(sub_name) if sub_name else None
        if sub is None:
            continue
        key = node["path"]
        if key in seen:
            continue
        seen.add(key)
        joints += assembly_joints(sub, node.get("children", []),
                                  prefix=node["path"] + "/",
                                  assembly=node["path"])
    return joints


def analyse_assembly(path):
    doc = FreeCAD.openDocument(path)
    issues = []
    # the instance TREE first: sub-assemblies are followed to the real
    # parts, mirrors are not double-counted, cycles are refused (fc_tree)
    nodes = fc_tree.walk(doc, issues)
    for node in nodes:
        node_geometry(node)
    joints = collect_joints(doc, nodes)
    wb = ("builtin-assembly"
          if any(o.TypeId == "Assembly::AssemblyObject" for o in doc.Objects)
          else "unknown")
    # overall assembly bbox from every instance shape, at any depth
    mn = [1e300] * 3
    mx = [-1e300] * 3
    for i in fc_tree.flatten(nodes):
        if "bbox_min" in i:
            mn = [min(a, b) for a, b in zip(mn, i["bbox_min"])]
            mx = [max(a, b) for a, b in zip(mx, i["bbox_max"])]
    usage = fc_tree.part_usage(nodes)
    return {"file": path, "document": doc.Name, "workbench": wb,
            "instances": nodes, "joints": joints, "issues": issues,
            # the real parts: analysed once, counted per instance
            "part_usage": {f: u for f, u in sorted(usage.items())},
            "sub_assembly_files": sorted(
                {n["target_file"] for n in fc_tree.flatten(nodes)
                 if n.get("kind") == "assembly" and n.get("target_file")}),
            "bbox_min": mn, "bbox_max": mx}


def main():
    args = json.loads(os.environ["FABPIPE_ARGS"])
    model_dir, assembly_name, out_path = args[:3]
    extra_parts = args[3:]          # standalone parts (not in the assembly)
    if assembly_name:
        asm = analyse_assembly(os.path.join(model_dir, assembly_name))
    else:
        asm = {"file": "", "document": "", "workbench": "none",
               "instances": [], "joints": [], "issues": [],
               "part_usage": {}, "sub_assembly_files": [],
               "bbox_min": None, "bbox_max": None}
    result = {"model_dir": model_dir, "assembly": asm}
    # the real parts, each analysed ONCE however many instances use it —
    # and never a sub-assembly file, which is not a part
    part_files = sorted(asm.get("part_usage", {}))
    part_files += [f for f in extra_parts if f not in part_files]
    result["parts"] = [analyse_part(f) for f in part_files]
    fcv = FreeCAD.Version()
    result["freecad_version"] = ".".join(fcv[:3])
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)
    p("ANALYSIS_WRITTEN", out_path)


# FreeCAD's `-c` executes this file as a module named after the file, not as
# __main__, so a __main__ guard would never fire. The harness always sets
# FABPIPE_ARGS; its absence means we are being imported (by the tests).
if "FABPIPE_ARGS" in os.environ:
    main()
    sys.stdout.flush()
    os._exit(0)
