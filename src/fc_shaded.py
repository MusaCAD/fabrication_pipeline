# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Pranay Kiran
"""FreeCAD-side mesh export for shaded pictorials. Runs INSIDE FreeCAD.

    FABPIPE_ARGS='[{"file": "...", "assembly": false, "out": "x.stl"}, ...]'

Exports each job's solid (part body, or compound of assembly link shapes)
as STL for pyvista shading. Read-only.
"""

import json
import os
import sys

import FreeCAD
import Mesh
import MeshPart
import Part

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fc_shapes  # noqa: E402  (needs the path above)


def p(*a):
    print(*a, flush=True)


def shape_of(job):
    doc = FreeCAD.openDocument(job["file"])
    if job.get("assembly"):
        shapes = [o.Shape for o in doc.Objects
                  if o.TypeId == "App::Link" and o.LinkedObject is not None
                  and not o.Shape.isNull()]
        return Part.makeCompound(shapes)
    tip, _flat = fc_shapes.tips(doc)
    if tip is None:
        raise ValueError("no solid body in " + path)
    return tip.Shape


def main():
    jobs = json.loads(os.environ["FABPIPE_ARGS"])
    for job in jobs:
        shape = shape_of(job)
        mesh = MeshPart.meshFromShape(Shape=shape, LinearDeflection=0.1,
                                      AngularDeflection=0.3)
        mesh.write(job["out"])
        p("MESH_OK", job["out"])


main()
sys.stdout.flush()
os._exit(0)
