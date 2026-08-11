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


def analyse_part(path):
    doc = FreeCAD.openDocument(path)
    bodies, sketches, issues = [], [], []
    for obj in doc.Objects:
        if obj.TypeId == "Sketcher::SketchObject":
            sketches.append(sketch_info(obj))
        if obj.TypeId == "PartDesign::Body":
            sh = obj.Shape
            if sh.isNull() or not sh.Solids:
                issues.append(f"body {obj.Label} has no solid")
                continue
            bb = sh.BoundBox
            bodies.append({
                "label": obj.Label,
                "solids": len(sh.Solids),
                "faces": len(sh.Faces),
                "volume_mm3": round(sh.Volume, 3),
                "area_mm2": round(sh.Area, 3),
                "bbox_min": vec(bb.getPoint(4)) if False else
                            [round(bb.XMin, 6), round(bb.YMin, 6),
                             round(bb.ZMin, 6)],
                "bbox_max": [round(bb.XMax, 6), round(bb.YMax, 6),
                             round(bb.ZMax, 6)],
                "bbox_size": [round(bb.XLength, 6), round(bb.YLength, 6),
                              round(bb.ZLength, 6)],
                "material": material_of(obj),
            })
    if not bodies:
        issues.append("no PartDesign body with a solid")
    return {"file": path, "document": doc.Name, "bodies": bodies,
            "sketches": sketches, "issues": issues}


def analyse_assembly(path):
    doc = FreeCAD.openDocument(path)
    instances, joints, issues = [], [], []
    for obj in doc.Objects:
        if obj.TypeId == "App::Link":
            lo = obj.LinkedObject
            if lo is None:
                issues.append(f"broken link: {obj.Name} ({obj.Label})")
                instances.append({"name": obj.Name, "label": obj.Label,
                                  "target_file": None, "broken": True})
                continue
            pl = obj.Placement
            tgt = getattr(lo.Document, "FileName", None)
            inst = {"name": obj.Name, "label": obj.Label,
                    "target_file": tgt, "broken": False,
                    "position": vec(pl.Base),
                    "rotation_axis": vec(pl.Rotation.Axis),
                    "rotation_deg": round(pl.Rotation.Angle * 180.0
                                          / 3.141592653589793, 6)}
            try:
                bb = obj.Shape.BoundBox
                inst["bbox_min"] = [round(bb.XMin, 6), round(bb.YMin, 6),
                                    round(bb.ZMin, 6)]
                inst["bbox_max"] = [round(bb.XMax, 6), round(bb.YMax, 6),
                                    round(bb.ZMax, 6)]
            except Exception:
                pass
            instances.append(inst)
        elif obj.TypeId == "App::FeaturePython" and hasattr(obj, "JointType"):
            joints.append({
                "name": obj.Name, "label": obj.Label,
                "type": str(getattr(obj, "JointType", "?")),
                "grounded": "Grounded" in obj.Label
                            or obj.Name.startswith("GroundedJoint"),
                "connects": [o.Name for o in obj.OutList
                             if o.TypeId == "App::Link"],
            })
        elif obj.TypeId == "App::FeaturePython" and "Grounded" in obj.Name:
            joints.append({"name": obj.Name, "label": obj.Label,
                           "type": "Grounded", "grounded": True,
                           "connects": [o.Name for o in obj.OutList
                                        if o.TypeId == "App::Link"]})
    wb = ("builtin-assembly"
          if any(o.TypeId == "Assembly::AssemblyObject" for o in doc.Objects)
          else "unknown")
    # overall assembly bbox from instance shapes
    mn = [1e300] * 3
    mx = [-1e300] * 3
    for i in instances:
        if "bbox_min" in i:
            mn = [min(a, b) for a, b in zip(mn, i["bbox_min"])]
            mx = [max(a, b) for a, b in zip(mx, i["bbox_max"])]
    return {"file": path, "document": doc.Name, "workbench": wb,
            "instances": instances, "joints": joints, "issues": issues,
            "bbox_min": mn, "bbox_max": mx}


def main():
    args = json.loads(os.environ["FABPIPE_ARGS"])
    model_dir, assembly_name, out_path = args[:3]
    extra_parts = args[3:]          # standalone parts (not in the assembly)
    asm_path = os.path.join(model_dir, assembly_name)
    result = {"model_dir": model_dir, "assembly": analyse_assembly(asm_path)}
    part_files = sorted({i["target_file"] for i in
                         result["assembly"]["instances"]
                         if i.get("target_file")})
    part_files += [f for f in extra_parts if f not in part_files]
    result["parts"] = [analyse_part(f) for f in part_files]
    fcv = FreeCAD.Version()
    result["freecad_version"] = ".".join(fcv[:3])
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)
    p("ANALYSIS_WRITTEN", out_path)


main()
sys.stdout.flush()
os._exit(0)
