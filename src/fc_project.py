# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Pranay Kiran
"""FreeCAD-side 2D view generation. Runs INSIDE headless FreeCAD.

    FABPIPE_ARGS='["<request.json>", "<out.json>"]' AppImage -c fc_project.py

Request (all geometry in real mm):
{
  "jobs": [{
    "id": "base_empty:front",
    "file": "/abs/part.FCStd",            # or "assembly": true + file
    "view_dir": [0,0,-1],                  # camera looks along this vector
    "up": [0,1,0],                         # sheet +Y
    "section": null | {                    # full section: material in front of
        "plane_point": [x,y,z],            # the plane (toward viewer) removed
        "plane_normal": [x,y,z]
    }
  }]
}

Response per job: visible/hidden edge lists (segments, circles, arcs,
polylines) in 2D view coordinates, plus hatch loops for section cut faces
and per-instance 2D bbox/centroid for assemblies (balloon anchors).
Read-only: documents are never saved.
"""

import json
import math
import os
import sys

import FreeCAD
import Part
import TechDraw

EPS = 1e-6


def p(*a):
    print(*a, flush=True)


def view_basis(view_dir, up):
    d = FreeCAD.Vector(*view_dir)
    d.normalize()
    u = FreeCAD.Vector(*up)
    u = u - d * u.dot(d)
    if u.Length < EPS:
        raise ValueError("up parallel to view_dir")
    u.normalize()
    # Screen right = sight x up (d points INTO the screen). Sanity: plan view
    # d=(0,0,-1), u=(0,1,0) -> r=(1,0,0) = east, as expected.
    r = d.cross(u)
    return r, u, d


def to_view_matrix(view_dir, up):
    """World -> view: x=right, y=up, z=depth (toward viewer negative)."""
    r, u, d = view_basis(view_dir, up)
    m = FreeCAD.Matrix(r.x, r.y, r.z, 0,
                       u.x, u.y, u.z, 0,
                       -d.x, -d.y, -d.z, 0,
                       0, 0, 0, 1)
    return m


def classify_edge(e):
    """Return a serializable 2D primitive for an edge already in view space
    (z flattened). Falls back to a discretized polyline."""
    c = e.Curve
    try:
        if isinstance(c, Part.Line) or c.TypeId == "Part::GeomLineSegment":
            a, b = e.Vertexes[0].Point, e.Vertexes[-1].Point
            if (a - b).Length < EPS:
                return None
            return {"t": "seg", "a": [a.x, a.y], "b": [b.x, b.y]}
    except Exception:
        pass
    try:
        if isinstance(c, Part.Circle):
            ctr = c.Center
            if e.Closed:
                return {"t": "circle", "c": [ctr.x, ctr.y], "r": c.Radius}
            a0 = e.FirstParameter
            a1 = e.LastParameter
            # circle axis may be -Z in view space -> reverse sweep
            if c.Axis.z < 0:
                a0, a1 = -a1, -a0
            return {"t": "arc", "c": [ctr.x, ctr.y], "r": c.Radius,
                    "a0": a0, "a1": a1}
    except Exception:
        pass
    try:
        pts = e.discretize(Deflection=0.02)
        pl = [[q.x, q.y] for q in pts]
        circ = _fit_closed_circle(pl)
        if circ is not None:
            return circ
        return {"t": "poly", "pts": pl}
    except Exception:
        return None


def _fit_closed_circle(pl):
    """Recover full circles that HLR delivered as dense polylines (curve
    type is not preserved by projectEx). Kaasa least-squares fit — exact for
    points on a circle regardless of sampling — then a tight residual gate."""
    if len(pl) < 8:
        return None
    if math.hypot(pl[0][0] - pl[-1][0], pl[0][1] - pl[-1][1]) > 0.05:
        return None
    pts = pl[:-1] if pl[0] == pl[-1] else pl
    n = len(pts)
    sx = sy = sxx = syy = sxy = sxz = syz = sz = 0.0
    for x, y in pts:
        z = x * x + y * y
        sx += x; sy += y; sz += z
        sxx += x * x; syy += y * y; sxy += x * y
        sxz += x * z; syz += y * z
    # solve [sxx sxy sx; sxy syy sy; sx sy n] * [a b c] = [sxz syz sz]
    def det3(m):
        return (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
                - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
                + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))
    M = [[sxx, sxy, sx], [sxy, syy, sy], [sx, sy, float(n)]]
    D = det3(M)
    if abs(D) < 1e-12:
        return None
    rhs = [sxz, syz, sz]
    def col_replaced(k):
        return [[rhs[i] if j == k else M[i][j] for j in range(3)]
                for i in range(3)]
    a = det3(col_replaced(0)) / D
    b = det3(col_replaced(1)) / D
    c = det3(col_replaced(2)) / D
    cx, cy = a / 2.0, b / 2.0
    rr = c + cx * cx + cy * cy
    if rr <= 0:
        return None
    r = math.sqrt(rr)
    if r < 0.05:
        return None
    if max(abs(math.hypot(x - cx, y - cy) - r) for x, y in pts) > \
            0.01 * r + 0.01:
        return None
    return {"t": "circle", "c": [cx, cy], "r": r}


def project_shape(shape, view_dir, up):
    """HLR-project `shape`; returns (visible, hidden) 2D primitive lists."""
    m = to_view_matrix(view_dir, up)
    s = shape.transformGeometry(m)
    # projectEx returns [V, V1, VN, VO, H, H1, HN, HO]: the outline groups
    # (VO/HO) carry curved-surface silhouettes that plain project() omits
    # (e.g. bore tangent lines). First half visible, second half hidden.
    try:
        groups = TechDraw.projectEx(s, FreeCAD.Vector(0, 0, 1))
    except Exception:
        groups = TechDraw.project(s, FreeCAD.Vector(0, 0, 1))
    vis, hid = [], []
    for gi, g in enumerate(groups):
        bucket = vis if gi < len(groups) / 2 else hid
        for e in g.Edges:
            prim = classify_edge(e)
            if prim is not None:
                bucket.append(prim)
    return vis, hid


def section_cut(shape, plane_point, plane_normal):
    """Remove material on the +normal side (toward viewer) with a huge box."""
    n = FreeCAD.Vector(*plane_normal)
    n.normalize()
    pt = FreeCAD.Vector(*plane_point)
    bb = shape.BoundBox
    size = 4.0 * max(bb.XLength, bb.YLength, bb.ZLength, 1.0)
    box = Part.makeBox(size, size, size)
    box.translate(FreeCAD.Vector(-size / 2, -size / 2, 0.0))
    rot = FreeCAD.Rotation(FreeCAD.Vector(0, 0, 1), n)
    box.Placement = FreeCAD.Placement(pt, rot)
    return shape.cut(box)


def _point_in_loop(pt, loop):
    """Even-odd ray-crossing test."""
    x, y = pt
    inside = False
    j = len(loop) - 1
    for i in range(len(loop)):
        xi, yi = loop[i]
        xj, yj = loop[j]
        if (yi > y) != (yj > y) and \
                x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _loop_area(loop):
    a = 0.0
    for i in range(len(loop)):
        x0, y0 = loop[i - 1]
        x1, y1 = loop[i]
        a += x0 * y1 - x1 * y0
    return abs(a) / 2.0


def section_loops(shape, plane_point, plane_normal, view_dir, up):
    """Material cross-section of the ORIGINAL solid at the cutting plane via
    Shape.slice(): closed wires -> view coords -> nested (outer, holes)
    groups by even-odd containment depth."""
    n = FreeCAD.Vector(*plane_normal)
    n.normalize()
    pt = FreeCAD.Vector(*plane_point)
    m = to_view_matrix(view_dir, up)
    loops = []
    for w in shape.slice(n, pt.dot(n)):
        tw = w.transformGeometry(m)   # returns a generic Shape
        wire = tw.Wires[0] if tw.Wires else None
        if wire is None:
            continue
        pts = wire.discretize(Deflection=0.02)
        loop = [[q.x, q.y] for q in pts]
        if len(loop) >= 3 and _loop_area(loop) > 1e-3:
            loops.append(loop)
    # containment depth: even = outer boundary, odd = hole
    depth = []
    for i, lp in enumerate(loops):
        d = sum(1 for k, other in enumerate(loops)
                if k != i and _point_in_loop(lp[0], other))
        depth.append(d)
    groups = []
    for i, lp in enumerate(loops):
        if depth[i] % 2 == 0:
            groups.append({"outer": lp, "holes": [], "_area": _loop_area(lp),
                           "_idx": i})
    for i, lp in enumerate(loops):
        if depth[i] % 2 == 1:
            containing = [g for g in groups
                          if _point_in_loop(lp[0], loops[g["_idx"]])]
            if containing:
                min(containing, key=lambda g: g["_area"])["holes"].append(lp)
    for g in groups:
        g.pop("_area")
        g.pop("_idx")
    return groups


def bounds_of(prims):
    mn = [1e300, 1e300]
    mx = [-1e300, -1e300]

    def upd(x, y):
        mn[0] = min(mn[0], x); mn[1] = min(mn[1], y)
        mx[0] = max(mx[0], x); mx[1] = max(mx[1], y)

    for pr in prims:
        if pr["t"] == "seg":
            upd(*pr["a"]); upd(*pr["b"])
        elif pr["t"] in ("circle", "arc"):
            upd(pr["c"][0] - pr["r"], pr["c"][1] - pr["r"])
            upd(pr["c"][0] + pr["r"], pr["c"][1] + pr["r"])
        elif pr["t"] == "poly":
            for q in pr["pts"]:
                upd(*q)
    return mn, mx


def load_shape(job):
    doc = FreeCAD.openDocument(job["file"])
    if job.get("assembly"):
        shapes, inst = [], []
        for obj in doc.Objects:
            if obj.TypeId == "App::Link" and obj.LinkedObject is not None:
                sh = obj.Shape  # placed shape
                if not sh.isNull():
                    shapes.append(sh)
                    inst.append((obj.Name, obj.Label, sh))
        comp = Part.makeCompound([s for s in shapes])
        return comp, inst
    for obj in doc.Objects:
        if obj.TypeId == "PartDesign::Body" and not obj.Shape.isNull():
            return obj.Shape, None
    raise ValueError("no solid body in " + job["file"])


def run_job(job):
    shape, instances = load_shape(job)
    view_dir = job["view_dir"]
    up = job["up"]
    out = {"id": job["id"]}
    sec = job.get("section")
    target = shape
    if sec:
        target = section_cut(shape, sec["plane_point"], sec["plane_normal"])
        out["hatch_groups"] = section_loops(
            shape, sec["plane_point"], sec["plane_normal"], view_dir, up)
    vis, hid = project_shape(target, view_dir, up)
    out["visible"] = vis
    out["hidden"] = hid
    out["bounds"] = bounds_of(vis)
    if instances is not None:
        m = to_view_matrix(view_dir, up)
        anchors = []
        for name, label, sh in instances:
            bb = sh.transformGeometry(m).BoundBox
            anchors.append({"name": name, "label": label,
                            "center": [round((bb.XMin + bb.XMax) / 2, 6),
                                       round((bb.YMin + bb.YMax) / 2, 6)],
                            "bbox": [round(bb.XMin, 6), round(bb.YMin, 6),
                                     round(bb.XMax, 6), round(bb.YMax, 6)]})
        out["instances"] = anchors
    return out


def main():
    req_path, out_path = json.loads(os.environ["FABPIPE_ARGS"])
    with open(req_path) as f:
        req = json.load(f)
    results = []
    for job in req["jobs"]:
        try:
            results.append(run_job(job))
        except Exception as e:  # report per-job, keep going
            results.append({"id": job["id"], "error": repr(e)})
    with open(out_path, "w") as f:
        json.dump({"jobs": results}, f)
    p("PROJECTION_WRITTEN", out_path)


main()
sys.stdout.flush()
os._exit(0)
