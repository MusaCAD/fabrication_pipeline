# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Pranay Kiran
"""Stage 4 — /draw: generate .musa sheets from the locked manifest.

    python3 src/draw.py [--manifest manifests/manifest.json]

Per part: ONE sheet, TWO views (front, fully dimensioned; full section below
it with cutting-plane line/arrows/labels on the front view and ANSI31 hatch).
Assembly: ONE sheet with front view, balloon leaders (P.NO.) and the filled
parts table. Layout style: reference/0982_001-1.pdf.

Writes output/musa/<drg_no>.musa per sheet + output/musa/sheets.json
(metadata consumed by /export and qa-checker).
"""

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (MANIFESTS_DIR, MUSA_PLOT, OUTPUT_MUSA_DIR,
                    SCRATCH_DIR, die, dump_dims, paper_key, png_size,
                    read_json, run_freecad, today, write_json)
import fc_tree
import layout_check
import musa_writer as mw
import verify
from manifest import VIEW_DIRS

FC_PROJECT = Path(__file__).resolve().parent / "fc_project.py"

# props tails beyond the default
P_HIDDEN = "0 5 255 255 255 3 25"   # explicit Hidden linetype, rest ByLayer
P_CUTLINE = "0 5 255 255 255 2 35"  # explicit Center linetype
P_THICK = "0 3 255 255 255 0 50"    # explicit 0.5 mm lineweight


def parse_scale(s: str) -> tuple[int, int]:
    num, den = (int(x) for x in s.split(":"))
    return num, den


# ------------------------------------------------------------ projection

def table_band(ctx, rows_h):
    """The parts-table band along the bottom of the drawing area, across
    the TABLE's own columns: on a wider sheet the title block travels with
    the right edge, so the left of the drawing area is free (issue #10)."""
    x0 = max(ctx.draw_area[0], ctx.tx(ctx.fx(mw.PARTS_COLS[0][1])))
    return (x0, ctx.draw_area[1], ctx.draw_area[2],
            ctx.draw_area[1] + rows_h)


def sheet_paper(spec: dict):
    """The paper one sheet asks for: `"paper": "A3"` (portrait unless the
    name says otherwise, e.g. "A3L"), or the long form
    `{"size": "A3", "orientation": "landscape"}`. None = the set's own
    default, whatever the manifest's template is drawn on."""
    p = (spec or {}).get("paper")
    if not p:
        return None
    if isinstance(p, dict):
        return (str(p.get("size", "A4")).upper(),
                str(p.get("orientation", "portrait")))
    s = str(p).upper().replace(" ", "")
    if s.endswith(("P", "L")) and s[:-1] in ("A4", "A3", "A2"):
        return (s[:-1], "landscape" if s.endswith("L") else "portrait")
    return (s, "portrait")


def sheet_template(m: dict, spec: dict):
    """The title block one sheet draws on: the manifest's `templates` entry
    for the paper it asks for (keyed "A3L", "A4P", ...), else the set's
    default `template`. Returns (path or None, problem or None)."""
    want = sheet_paper(spec)
    default = m.get("template")
    if want is None:
        return default, None
    key = paper_key(*want)
    table = {str(k).upper(): v for k, v in (m.get("templates") or {}).items()}
    if key in table:
        return table[key], None
    # the set's own template may already be that paper
    if default is not None:
        try:
            geom = mw.template_geom(mw.resolve_template(default))
        except Exception:                               # reported elsewhere
            geom = None
        if geom and (geom["paper"], geom["orientation"]) == want:
            return default, None
    if want == mw.DEFAULT_PAPER and default is None:
        return None, None                                # the pipeline's own
    return None, (f"no template for {key}: add it to the manifest's "
                  f"\"templates\" ({key}: /abs/path/template_{key.lower()}"
                  ".musa)")


def sheet_context(m: dict, spec: dict, num, den):
    """A SheetContext on the paper this sheet asks for, with the title
    block that belongs to it (issue #10). Fails loudly: a sheet drawn on
    the wrong template is a sheet with somebody else's frame."""
    path, problem = sheet_template(m, spec)
    if problem:
        die(f"{spec.get('drg_no', spec.get('file', '?'))}: {problem}")
    tpl = mw.resolve_template(path) if path else None
    geom = mw.template_geom(tpl) if tpl else mw.template_geom()
    return mw.SheetContext(num, den, geom=geom, template=tpl)


def sheet_parts(m: dict) -> list[dict]:
    """Parts that get their own sheet. A bought-out item ("sheet": false —
    a standard fastener, say) still occupies a P.NO. row and a balloon on the
    assembly, but nobody fabricates it, so it is not drawn."""
    return [p for p in m["parts"] if p.get("sheet", True)]


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0]]


def _parallel(a, b):
    return all(abs(x) < 1e-9 for x in _cross(a, b))


def aligned_up(front_dir, front_up, aux_dir, aux_up):
    """`up` for an aux view that keeps it feature-aligned under the front view.

    An aux view is placed BELOW the front view, so the two must share the
    front's HORIZONTAL axis. The canonical `up` from VIEW_DIRS is about the
    world, not about the view it sits under: for front "+X" (horizontal = world
    Y) with aux "+Z", the canonical up put world X horizontal and world Y
    vertical, so the end view of a 0.2 mm pad rendered as a 20 mm tall hairline
    instead of a 20 mm wide edge. Rotate the aux 90 degrees when that happens,
    and leave it alone when the canonical up already shares the axis -- the
    views that were already aligned keep the exact orientation they had.
    """
    right_f = _cross(front_up, front_dir)
    if _parallel(_cross(aux_up, aux_dir), right_f):
        return aux_up                      # already shares the axis
    fixed = _cross(aux_dir, right_f)
    if all(abs(x) < 1e-9 for x in fixed):   # aux looks along the shared axis
        return aux_up                       # nothing to align to; leave it
    return [int(x) if abs(x - round(x)) < 1e-9 else x for x in fixed]


def collect_jobs(m: dict) -> list[dict]:
    jobs = []
    for part in sheet_parts(m):
        d, u = VIEW_DIRS[part["front_view"]]
        pf = part.get("project_feature")
        jobs.append({"id": f"{part['part_id']}:front", "file": part["file"],
                     "view_dir": d, "up": u, "section": None,
                     "project_feature": pf})
        if part["section"]["enabled"]:
            jobs.append({"id": f"{part['part_id']}:section",
                         "file": part["file"],
                         "_defer_section": part, })
        elif part.get("aux_view"):
            # agreed two-view deviation (e.g. end view for a plain rod):
            # a second plain projection instead of a meaningless section
            ad, au = VIEW_DIRS[part["aux_view"]]
            au = aligned_up(d, u, ad, au)
            jobs.append({"id": f"{part['part_id']}:aux", "file": part["file"],
                         "view_dir": ad, "up": au, "section": None,
                         "project_feature": pf})
        elif (part.get("flat_pattern") or {}).get("enabled"):
            # sheet metal: the second view is the developed blank, which is
            # a separate solid in the same document. A section through 2 mm
            # plate would show a hatched sliver and tell the shop nothing.
            fp = part["flat_pattern"]
            fd, fu = VIEW_DIRS[fp["view"]]
            jobs.append({"id": f"{part['part_id']}:flat", "file": part["file"],
                         "view_dir": fd, "up": fu, "section": None,
                         "project_object": fp["object"]})
    asm = m.get("assembly_sheet")
    if asm and asm.get("enabled", True):
        if asm.get("view") == "iso":
            k = 1.0 / 3.0 ** 0.5
            d, u = [-k, -k, -k], [0, 0, 1]  # classic iso: viewer at (1,1,1)
        else:
            d, u = VIEW_DIRS[asm["front_view"]]
        blanks = {p["file"]: p["project_feature"] for p in m["parts"]
                  if p.get("project_feature")}
        jobs.append({"id": "assembly:front", "file": m["assembly_file"],
                     "assembly": True, "view_dir": d, "up": u,
                     "section": None, "link_features": blanks})
        if (asm.get("section") or {}).get("enabled"):
            jobs.append({"id": "assembly:section", "file": m["assembly_file"],
                         "assembly": True, "link_features": blanks,
                         "_defer_section": asm})
    return jobs


def cross(a, b):
    return [a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0]]


def section_basis(front_view: str, projection: str, who: str = ""):
    """(sight direction, up, plane normal) for a section of `front_view`.

    Bases are chosen so the section view stays feature-ALIGNED with the
    front view (shared sheet axis, reference style). The removed half is
    always the -sight side, so plane_normal = -sight."""
    d, u = VIEW_DIRS[front_view]
    r = cross(d, u)                          # front-view sheet right, world
    if projection == "below":                # horizontal cut line
        s, uv = u, [-x for x in d]
    elif projection == "right":              # vertical cut, view to the right
        s, uv = [-x for x in r], u
    elif projection == "left":               # vertical cut, view to the left
        s, uv = r, u
    else:
        die(f"{who}: bad section.projection {projection!r}")
    return s, uv, [-x for x in s]


def assembly_section_job(m: dict) -> dict:
    """The assembly sheet's own section job: the same geometry rules as a
    part's, plus the instances the section must NOT cut (shafts, screws,
    pins, O-rings — ISO 128-50), resolved from part ids to instance paths."""
    asm = m["assembly_sheet"]
    sec = asm["section"]
    s, uv, nrm = section_basis(asm["front_view"],
                               sec.get("projection", "below"),
                               "assembly_sheet")
    return {"id": "assembly:section", "file": m["assembly_file"],
            "assembly": True, "view_dir": s, "up": uv,
            "link_features": {p["file"]: p["project_feature"]
                              for p in m["parts"]
                              if p.get("project_feature")},
            "section_exempt": not_sectioned_paths(m),
            "section": {"plane_point": None, "plane_normal": nrm}}


def not_sectioned_paths(m: dict) -> list:
    """Instance paths of the parts the manifest says not to cut.

    Stated as part ids (`"not_sectioned": ["02", "05"]`), because that is
    how the user thinks of them; resolved here through the analysis tree,
    so every instance of such a part is left whole however deep it sits."""
    sec = (m.get("assembly_sheet") or {}).get("section") or {}
    ids = set(sec.get("not_sectioned") or [])
    if not ids:
        return []
    files = {p["file"] for p in m["parts"] if p["part_id"] in ids}
    analysis = read_json(Path(m["_analysis_path"]))
    return [n["path"] for n in fc_tree.flatten(analysis["assembly"]
                                               ["instances"])
            if n.get("target_file") in files]


def section_job(part: dict) -> dict:
    """Section basis per manifest section.projection (below|right|left).

    Bases are chosen so the section view stays feature-ALIGNED with the
    front view (shared sheet axis, reference style). Consequence: the sight
    direction is away from the placed view (third-angle arrows); if the
    user prefers first-angle arrow convention, flip `s` and the arrowheads
    in cutting_plane() together. The removed half is always the -sight side
    (material between the section viewer and the plane), so plane_normal =
    -s."""
    s, uv, nrm = section_basis(part["front_view"],
                               part["section"].get("projection", "below"),
                               part["part_id"])
    return {"id": f"{part['part_id']}:section", "file": part["file"],
            "project_feature": part.get("project_feature"),
            "view_dir": s, "up": uv,
            "section": {"plane_point": None,   # filled after bbox known
                        "plane_normal": nrm}}


def run_projections(m: dict, analysis: dict) -> dict:
    """Two fc_project passes: front views first (their bounds fix the cut
    plane point), then sections."""
    SCRATCH_DIR.mkdir(parents=True, exist_ok=True)

    def run(jobs, tag):
        req = SCRATCH_DIR / f"proj_req_{tag}.json"
        out = SCRATCH_DIR / f"proj_out_{tag}.json"
        req.write_text(json.dumps({"jobs": jobs}))
        run_freecad(FC_PROJECT, str(req), str(out), timeout=600)
        res = read_json(out)
        by_id = {}
        for j in res["jobs"]:
            if "error" in j:
                die(f"projection {j['id']} failed: {j['error']}")
            by_id[j["id"]] = j
        return by_id

    front_jobs = [j for j in collect_jobs(m) if "_defer_section" not in j]
    results = run(front_jobs, "front")

    bbox_by_file = {p["file"]: p["bodies"][0] for p in analysis["parts"]}
    sec_jobs = []
    for part in sheet_parts(m):
        if not part["section"]["enabled"]:
            continue
        job = section_job(part)
        b = bbox_by_file[part["file"]]
        lo, hi = b["bbox_min"], b["bbox_max"]
        frac = part["section"].get("offset_frac", 0.5)
        centre = [lo[i] + (hi[i] - lo[i]) * 0.5 for i in range(3)]
        nrm = job["section"]["plane_normal"]
        ax = max(range(3), key=lambda i: abs(nrm[i]))
        centre[ax] = lo[ax] + (hi[ax] - lo[ax]) * frac
        job["section"]["plane_point"] = centre
        sec_jobs.append(job)
    asm = m.get("assembly_sheet") or {}
    if asm.get("enabled", True) and (asm.get("section") or {}).get("enabled"):
        job = assembly_section_job(m)
        lo = analysis["assembly"]["bbox_min"]
        hi = analysis["assembly"]["bbox_max"]
        frac = asm["section"].get("offset_frac", 0.5)
        centre = [lo[i] + (hi[i] - lo[i]) * 0.5 for i in range(3)]
        nrm = job["section"]["plane_normal"]
        ax = max(range(3), key=lambda i: abs(nrm[i]))
        centre[ax] = lo[ax] + (hi[ax] - lo[ax]) * frac
        job["section"]["plane_point"] = centre
        sec_jobs.append(job)
    if sec_jobs:
        results.update(run(sec_jobs, "section"))
    return results


# ------------------------------------------------------------- placement

def _prim_key(pr, ox, oy):
    r3 = lambda v: round(v, 3)
    if pr["t"] == "seg":
        pts = sorted(((r3(pr["a"][0]), r3(pr["a"][1])),
                      (r3(pr["b"][0]), r3(pr["b"][1]))))
        return ("seg", tuple(pts))
    if pr["t"] == "circle":
        return ("circle", r3(pr["c"][0]), r3(pr["c"][1]), r3(pr["r"]))
    if pr["t"] == "arc":
        return ("arc", r3(pr["c"][0]), r3(pr["c"][1]), r3(pr["r"]),
                r3(pr["a0"]), r3(pr["a1"]))
    pts = [(r3(x), r3(y)) for x, y in pr["pts"]]
    rev = list(reversed(pts))
    return ("poly", tuple(min(pts, rev)))


def thread_lines(ctx, threads, view_jobs, offs):
    """ISO 6410 simplified thread convention.

    The solid carries only the CREST (major dia) — a thread blank looks like
    a plain cylinder, so a threaded length has to be drawn: a continuous ROOT
    line at the minor diameter running the full thread length, plus the
    thread-end line across the major dia where the thread stops in open
    material (omitted where a diameter step already draws that edge).

    A TAPPED hole is the other way round (`"kind": "internal"`, issue #12).
    It is modelled as its tap drill, so the solid's own edge is the MINOR
    diameter and what has to be drawn is the MAJOR one: a thin continuous
    line each side over the threaded length, the thread-end line where the
    usable thread stops — short of the drill depth — and, in a view that
    shows the hole as a circle, a three-quarter thin arc at the major
    diameter, open in the upper-right quadrant.

    Specs are in the view's own coordinates, like location_dims:
      kind   - "external" (default) or "internal" (a tapped hole)
      views  - which views to draw it in ("front", "section", "aux")
      orient - "v" if the thread axis runs up the view, "h" across it
      axis   - the axis coordinate across the thread (view x when "v")
      span   - [start, end] along the axis: the threaded length
      major / minor - crest and root diameters (M2.5 x 0.45: 2.5 / 2.013;
               a tapped M6 x 1: 6 major, 5 tap drill)
      end_line - draw the thread-end line where the usable thread STOPS
               (default false for an external thread, where a diameter step
               already puts an edge there; default TRUE for an internal
               one, whose thread ends inside the drilled hole with nothing
               else to mark it)
      end_at   - that coordinate, when the span does not read end-first.
               `span` is [thread end, mouth] by convention, so the end is
               span[0]; a span written the other way round (ascending into
               the part, which reads better) names the end here instead.
               Issue #16: the line used to be drawn at the LOWER span
               coordinate, so a hole threaded toward +x got it across the
               mouth and the place the thread really stops was unmarked.
      circles - [{"view": v, "center": [x, y]}] for an internal thread:
               where the hole is drawn as a circle, so the 3/4 arc goes on
    Threads in section keep their hatch where the solid put it — out to
    the crest for an external thread, to the drill wall for a tapped hole
    (ISO 6410 6.2) — so these lines simply overlay it.
    """
    recs = []
    for t in threads:
        rmin, rmaj = t["minor"] / 2.0, t["major"] / 2.0
        internal = str(t.get("kind", "external")) == "internal"
        # the line that is DRAWN is the one the solid does not already
        # carry: an external thread's root, a tapped hole's major dia
        rline = rmaj if internal else rmin
        a, b = sorted(t["span"])
        vert = t.get("orient", "v") == "v"
        for view in t.get("views", ["front"]):
            job = view_jobs.get(view)
            if job is None:
                continue
            ox, oy = offs["section" if view in ("section", "aux") else view]
            for s in (-1, 1):
                if vert:
                    x = t["axis"] + s * rline + ox
                    recs.append(mw.line((x, a + oy), (x, b + oy)))
                else:
                    y = t["axis"] + s * rline + oy
                    recs.append(mw.line((a + ox, y), (b + ox, y)))
            if t.get("end_line", internal):
                e = float(t["end_at"]) if t.get("end_at") is not None \
                    else float(t["span"][0])
                if vert:
                    recs.append(mw.line((t["axis"] - rmaj + ox, e + oy),
                                        (t["axis"] + rmaj + ox, e + oy),
                                        P_THICK))
                else:
                    recs.append(mw.line((e + ox, t["axis"] - rmaj + oy),
                                        (e + ox, t["axis"] + rmaj + oy),
                                        P_THICK))
        for spec in (t.get("circles") or []) if internal else ():
            view = spec.get("view", "front")
            if view not in view_jobs:
                continue
            ox, oy = offs["section" if view in ("section", "aux") else view]
            cx, cy = spec.get("center", [t["axis"], 0.0])
            # three quarters of a circle, open in the upper-right quadrant
            recs.append(mw.arc((cx + ox, cy + oy), rmaj,
                               math.pi / 2, 2.0 * math.pi))
    return recs


def emit_center_circles(ctx: mw.SheetContext, specs, view_jobs, offs,
                        view="front"):
    """Centre-linetype construction circles from the manifest, e.g. the PCD
    a hole pattern sits on:  "center_circles": [{"view": "front", "dia": 130}]
    Drawn in the same Center linetype as the cutting plane, per ISO 128."""
    recs = []
    for spec in specs or []:
        v = spec.get("view", view)
        if v not in view_jobs:
            continue
        ox, oy = offs[v]
        cx, cy = spec.get("at", [0.0, 0.0])
        recs.append(mw.circle((cx + ox, cy + oy),
                              spec["dia"] / 2.0, P_CUTLINE))
    return recs


def emit_prims(ctx: mw.SheetContext, prims, offset, props=mw.P):
    """Emit projected primitives, deduping coincident duplicates (HLR often
    projects a feature's top and bottom edges onto the SAME segment in
    opposite directions — overlapping dashed strokes phase-fill each
    other's gaps and print as solid lines)."""
    seen = set()
    deduped = []
    for pr in prims:
        k = _prim_key(pr, *offset)
        if k in seen:
            continue
        seen.add(k)
        deduped.append(pr)
    prims = deduped
    ox, oy = offset
    for pr in prims:
        if pr["t"] == "seg":
            ctx.add(mw.line((pr["a"][0] + ox, pr["a"][1] + oy),
                            (pr["b"][0] + ox, pr["b"][1] + oy), props))
        elif pr["t"] == "circle":
            ctx.add(mw.circle((pr["c"][0] + ox, pr["c"][1] + oy), pr["r"],
                              props))
        elif pr["t"] == "arc":
            ctx.add(mw.arc((pr["c"][0] + ox, pr["c"][1] + oy), pr["r"],
                           pr["a0"], pr["a1"], props))
        elif pr["t"] == "poly":
            ctx.add(mw.polyline([(x + ox, y + oy) for x, y in pr["pts"]],
                                props=props))


def round_outline(job) -> bool:
    """True if this view's outline IS a circle, so its width and height are
    the same diameter the %%c leader already states.

    CLAUDE.md: a round feature takes a circumference leader, never a linear
    width dim. Without this, a toroid pad got "45" horizontally, "45"
    vertically and a leader reading %%c45 -- the same dimension three times.
    Geometry decides it, not the manifest: a 45 x 45 SQUARE pad has the same
    bounding box and does need both linear dims.
    """
    w, h, _, _ = view_size(job)
    if w <= 0 or h <= 0 or abs(w - h) > 0.01 * max(w, h):
        return False
    for pr in job.get("visible", []):
        if pr.get("t") == "circle" and abs(2.0 * pr["r"] - w) <= 0.01 * w:
            return True
    return False


def view_size(job) -> tuple[float, float, list, list]:
    mn, mx = job["bounds"]
    return mx[0] - mn[0], mx[1] - mn[1], mn, mx


def dim_levels(specs, view, side):
    """How many dim-line columns a side needs: chained dims (touching but not
    overlapping spans) share one line, overlapping ones stack outward."""
    spans = []
    for sp in specs:
        if sp.get("view") != view or sp.get("side") != side:
            continue
        j = 1 if sp["orient"] == "v" else 0
        spans.append(sorted((sp["a"][j], sp["b"][j])))
    levels = 0
    for s in spans:                       # max overlap depth
        levels = max(levels, sum(1 for o in spans
                                 if o[0] < s[1] and s[0] < o[1]))
    return levels


def dim_gutter(ctx, part, view, side):
    """Printed width the dimension gutter on one side of a view needs: the
    first dim line stands 10 mm off, each further column 6.6 mm beyond it,
    plus the label height and a margin to the frame. place_views uses this to
    centre the view GROUP with its gutters, not just its outlines; the
    pictorial box uses it to keep out of a gutter that is still empty when
    the box is placed."""
    n = dim_levels(part.get("location_dims", []), view, side)
    ext = part.get("extents_sides", {})
    if view == "front" and part.get("front_extent_dims", True):
        if side in ("left", "right") and ext.get("height", "left") == side:
            n += 1
        elif side in ("top", "bottom") and ext.get("width", "top") == side:
            n += 1
    if view == "front" and part.get("section", {}).get("enabled"):
        # the cutting plane overshoots the view by 9 mm and parks its arrow
        # and letter there, i.e. it occupies the first dimension column on
        # the two sides its line runs out of (round-8: the 55 height dim on
        # sheet 03 was pushed off the frame by the A-A label)
        proj = part["section"].get("projection", "below")
        if (side in ("left", "right")) == (proj == "below"):
            n += 1
    if view == "section" and part.get("section", {}).get("enabled"):
        # the section repeats only the extent dim the front view does NOT
        # carry for the shared axis (see _compose_part_sheet): height on the
        # left below the front view, width underneath beside it
        proj = part["section"].get("projection", "below")
        if proj == "below" and side == "left":
            n += 1
        elif proj in ("right", "left") and side == "bottom":
            n += 1
    if view == "flat" and side in ("bottom", "left"):
        # the blank carries its own width and height: it shares neither with
        # the folded view above it
        n += 1
    return ctx.mm(10.0 + 6.6 * (n - 1) + 4.2 + 1.0) if n else 0.0


# Two views of one sheet never come closer than this (printed mm): the
# vertical packer is allowed to eat the preferred gap to make a tall part's
# dimension bands fit, but not to the point where the views read as one.
MIN_VIEW_GAP = 16.0


def place_views(ctx: mw.SheetContext, front, section, projection="below",
                right_band=False, bottom_reserve=0.0, top_reserve=0.0,
                band_w=0.0, band_inboard=False, gutters=(0.0, 0.0),
                band_gap_w=0.0):
    """Offsets placing the views in the drawing area. `below`: front upper
    band, section under it, SHARED x offset (feature alignment). right/left:
    side by side, SHARED y offset. Errors if they don't fit.

    The group is placed by DEMAND, never by the view outlines alone:
      * `gutters` (left, right) — printed width the dimension columns beside
        the group need (dim_gutter);
      * `band_w` — width of the right-hand annotation column (callouts, FCF
        stacks, balloons), and `band_inboard` — its leaders start at a view
        that is NOT the outermost one. Either that flag or a sheet too
        narrow for the column outside the group opens the inter-view gap up
        to hold it instead, so no leader has to cross the far view;
      * `band_gap_w` — width the INNER view of a side-by-side pair needs
        for its own callouts, which land in the gap. Both columns are
        reserved and the group is centred with both, so a sheet with
        leaders on both views has room for each (issue #13);
      * `top_reserve` / `bottom_reserve` — printed height the dimension band
        above the front view and the label/notes/parts-rows band below the
        last view need. A part too tall for both bands gives the inter-view
        gap back (down to MIN_VIEW_GAP) rather than losing a dimension off
        the top of the sheet."""
    ax0, ay0, ax1, ay1 = ctx.draw_area
    aw, ah = ax1 - ax0, ay1 - ay0
    fw, fh, fmn, fmx = view_size(front)
    # sections keep a wide gap (cutting-plane arrows + SECTION label live
    # there); a plain aux/end view needs less
    gap = ctx.mm(25.0 if projection != "below" or section is None
                 or "hatch_groups" in (section or {}) else 18.0)
    margin = ctx.mm(15.0)
    sw = sh = 0.0
    if section:
        sw, sh, smn, smx = view_size(section)
    lg, rg = gutters
    if right_band and band_w <= 0.0 and band_gap_w <= 0.0:
        # an unmeasured band (the assembly sheet's balloons). A part sheet
        # that measured ZERO for one of its two columns means it, and must
        # not have 32 mm invented for it (issue #13)
        band_w = ctx.mm(32.0)
    side_by_side = bool(section) and projection in ("right", "left")

    def sizes(g):
        return ((fw + g + sw, max(fh, sh)) if side_by_side
                else (max(fw, sw), fh + ((section and (g + sh)) or 0)))

    total_w, total_h = sizes(gap)
    band_in_gap = False
    want_gap = band_gap_w
    if side_by_side and band_w > 0.0 and not band_gap_w:
        # one column, no side stated: it belongs OUTSIDE the group whenever
        # the sheet is wide enough for it AND nothing is in the way; a
        # callout whose own view is not the outermost one has to land in
        # the inter-view gap instead, because its leader would otherwise
        # cross the far view
        if band_inboard or aw - lg - total_w - rg - band_w < 0.0:
            want_gap = band_w
    if side_by_side and want_gap > 0.0:
        gap = max(gap, min(want_gap, aw - lg - rg - fw - sw))
        total_w, total_h = sizes(gap)
        band_in_gap = gap >= want_gap
    need_l = lg
    # the outer column holds what the gap does not: with band_gap_w stated,
    # the two are different callouts and both are reserved (issue #13)
    need_r = rg + (band_w if band_gap_w or not band_in_gap else 0.0)
    # a band wider than the sheet can hold is capped, not honoured: the
    # callout stack wraps to the width it really gets (place_callouts)
    if need_l + total_w + need_r > aw:
        need_r = max(rg, aw - need_l - total_w - ctx.mm(2.0))

    # vertical: give the dimension bands what they need, take it out of the
    # inter-view gap first (never below MIN_VIEW_GAP), and only then squeeze
    need_top = max(ctx.mm(12.0), top_reserve)
    over = need_top + total_h + bottom_reserve - ah
    if over > 0 and section and not side_by_side:
        gap = max(ctx.mm(MIN_VIEW_GAP), gap - over)
        total_w, total_h = sizes(gap)
    # the views' OWN size, at the tightest gap they would ever be given:
    # what is left over is the annotation columns' problem, not the scale's
    views_w, views_h = sizes(ctx.mm(MIN_VIEW_GAP)) if side_by_side \
        else (total_w, total_h)
    if views_w > aw - 2 * margin or views_h > ah - 2 * margin:
        die(f"views ({views_w:.0f}x{views_h:.0f} sheet-units) exceed drawing "
            f"area ({aw:.0f}x{ah:.0f}); pick a smaller scale, a bigger "
            "sheet, or split the part across sheets")
    if total_w > aw - 2 * margin or total_h > ah - 2 * margin:
        # the views fit; the callout columns beside them do not. Say which
        # ones and by how much, because shortening a callout or moving it
        # to the other view is the fix — not a smaller scale (issue #13)
        room = aw - 2 * margin - views_w
        die(f"the callout columns do not fit beside the views: the inner "
            f"one needs {band_gap_w:.0f} and the outer one {band_w:.0f} "
            f"sheet-units, with {max(room, 0.0):.0f} left of "
            f"{aw:.0f}; shorten a callout, move it to the other view, or "
            "use a bigger sheet")
    free = ah - total_h
    centered = free / 2 - ctx.mm(6.0)
    # roomy sheets get >= 24 mm top headroom (two-tier dim chains: inner
    # feature dim + outer overall dim); a crowded one keeps exactly the
    # headroom its own dimension columns asked for
    vpad = min(max(centered, ctx.mm(24.0)), max(need_top,
                                                free - bottom_reserve))
    vpad = max(ctx.mm(14.0), min(max(vpad, need_top), free))
    top_y = ay1 - vpad
    # horizontal: centre the group WITH its gutters — the two sides are not
    # symmetric (a height-dim column on one side, an annotation band on the
    # other), and centring the outlines alone starves the narrow one
    slack = aw - need_l - total_w - need_r
    if slack >= 0:
        left_x = ax0 + need_l + slack / 2
    else:                       # over-subscribed: share the deficit
        share = need_l / (need_l + need_r) if need_l + need_r > 0 else 0.5
        left_x = min(max(ax0, ax0 + need_l + slack * share), ax1 - total_w)
    if side_by_side:
        fx = left_x if projection == "right" else left_x + sw + gap
        sx = left_x + fw + gap if projection == "right" else left_x
        f_off = (fx - fmn[0], top_y - fmx[1])
        # shared y offset keeps world features level across both views
        offs = {"front": f_off, "section": (sx - smn[0], f_off[1])}
    else:
        cx = left_x + total_w / 2
        f_off = (cx - (fmn[0] + fmx[0]) / 2, top_y - fmx[1])
        offs = {"front": f_off}
        if section:
            s_top = top_y - fh - gap
            # shared x offset keeps features vertically aligned
            offs["section"] = (f_off[0], s_top - smx[1])
    return offs


# ------------------------------------------------------------ detail views
# An enlarged DETAIL view (ISO 128-34 / ASME Y14.3): a circled region of the
# front view or the section, redrawn larger beside it. The region is CLIPPED
# out of the parent view's already-projected primitives — the detail is the
# parent's own ink, enlarged, so the two can never disagree.
#
# A DIM always displays the distance between its own def points (MusaCAD
# measures them; nothing can fake the value), so geometry drawn at k times
# size would read k times the truth. The detail's dimensions therefore run
# through a DIMSTYLE carrying DIMLFAC = 1/k (musa_writer.dimunits): the def
# points stay on the enlarged geometry and the value reads true millimetres.

# Clear ring reserved round a detail view for its OWN dimensions: the
# nearest stand-off a location dim uses (LOCDIM_STANDOFFS[0]) plus its label
# and a margin. Reserve less and the detail's dims have nowhere to go.
DETAIL_PAD_MM = 15.0
# An assembly's detail has no dimensions of its own — only balloons, which
# go to the sheet's own column — so it needs clearance, not a dimension band
DETAIL_ASSEMBLY_PAD_MM = 4.0


def detail_factor(part: dict, spec: dict) -> float:
    """Sheet units per true mm inside the detail view.

    The sheet prints at num:den and the detail must print at its own
    dnum:dden, so its geometry is drawn k = (dnum/dden) * (den/num) times
    full size: a 2:1 detail on a 1:5 sheet is 10x, with DIMLFAC 0.1."""
    num, den = parse_scale(part["scale"])
    dnum, dden = parse_scale(spec["scale"])
    return (dnum / dden) * (den / num)


def _span_in_circle(a, b, c, r):
    """(t0, t1) of the segment a->b that lies inside circle (c, r), or None."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    fx, fy = a[0] - c[0], a[1] - c[1]
    A = dx * dx + dy * dy
    if A <= 1e-12:                       # degenerate: a point
        return (0.0, 1.0) if math.hypot(fx, fy) <= r else None
    B = 2.0 * (fx * dx + fy * dy)
    C = fx * fx + fy * fy - r * r
    disc = B * B - 4.0 * A * C
    if disc <= 0.0:
        return None
    sq = math.sqrt(disc)
    t0 = max(0.0, (-B - sq) / (2.0 * A))
    t1 = min(1.0, (-B + sq) / (2.0 * A))
    return (t0, t1) if t1 - t0 > 1e-9 else None


def clip_seg(a, b, c, r):
    """The part of segment a->b inside circle (c, r), or None."""
    sp = _span_in_circle(a, b, c, r)
    if sp is None:
        return None
    t0, t1 = sp
    return ((a[0] + (b[0] - a[0]) * t0, a[1] + (b[1] - a[1]) * t0),
            (a[0] + (b[0] - a[0]) * t1, a[1] + (b[1] - a[1]) * t1))


def _circle_span_in_circle(cc, rr, c, r):
    """Angle span of the circle (cc, rr) inside circle (c, r), or None.

    Three degenerate cases come first: wholly inside, wholly outside, and
    the clip circle swallowed by the feature circle (whose circumference
    then never enters it)."""
    d = math.hypot(c[0] - cc[0], c[1] - cc[1])
    if d + rr <= r:
        return "all"
    if d >= r + rr or d + r <= rr:
        return None
    cosv = (d * d + rr * rr - r * r) / (2.0 * d * rr)
    half = math.acos(max(-1.0, min(1.0, cosv)))
    base = math.atan2(c[1] - cc[1], c[0] - cc[0])
    return (base - half, base + half)


def clip_arc(cc, rr, a0, a1, c, r):
    """Pieces (a0, a1) of the CCW arc a0->a1 on circle (cc, rr) inside the
    clip circle (c, r). A full circle is a0 == a1 (or a 2 pi sweep)."""
    span = _circle_span_in_circle(cc, rr, c, r)
    if span is None:
        return []
    w = (a1 - a0) % (2.0 * math.pi)
    if w <= 1e-12:
        w = 2.0 * math.pi
    if span == "all":
        return [(a0, a0 + w)]
    lo_s, hi_s = span
    out = []
    for k in (-1, 0, 1, 2):              # the span, turned into the arc's range
        lo = max(a0, lo_s + 2.0 * math.pi * k)
        hi = min(a0 + w, hi_s + 2.0 * math.pi * k)
        if hi - lo > 1e-9:
            out.append((lo, hi))
    return out


def clip_prims(prims, c, r):
    """Projected primitives clipped to the circle (c, r). Polylines come
    back as their clipped legs: emit_prims draws a poly open, so its legs
    carry exactly the same ink."""
    out = []
    for pr in prims:
        t = pr["t"]
        if t == "seg":
            sg = clip_seg(pr["a"], pr["b"], c, r)
            if sg:
                out.append({"t": "seg", "a": list(sg[0]), "b": list(sg[1])})
        elif t == "poly":
            pts = pr["pts"]
            for q0, q1 in zip(pts, pts[1:]):
                sg = clip_seg(q0, q1, c, r)
                if sg:
                    out.append({"t": "seg", "a": list(sg[0]),
                                "b": list(sg[1])})
        elif t in ("circle", "arc"):
            cc, rr = pr["c"], pr["r"]
            a0 = pr.get("a0", 0.0)
            a1 = pr.get("a1", 0.0) if t == "arc" else 0.0
            pieces = clip_arc(cc, rr, a0, a1, c, r)
            if t == "circle" and pieces == [(a0, a0 + 2.0 * math.pi)]:
                out.append({"t": "circle", "c": list(cc), "r": rr})
                continue
            for lo, hi in pieces:
                out.append({"t": "arc", "c": list(cc), "r": rr,
                            "a0": lo, "a1": hi})
    return out


def _circle_polygon(c, r, n=72):
    return [(c[0] + r * math.cos(2.0 * math.pi * i / n),
             c[1] + r * math.sin(2.0 * math.pi * i / n)) for i in range(n)]


def clip_loop_to_circle(loop, c, r, n=72):
    """Sutherland-Hodgman clip of a hatch loop against the detail circle
    (approximated by a regular n-gon, which is convex — the algorithm's one
    requirement). Returns [] when nothing of the loop is inside."""
    poly = list(loop)
    for i in range(n):
        if not poly:
            return []
        e0 = (c[0] + r * math.cos(2.0 * math.pi * i / n),
              c[1] + r * math.sin(2.0 * math.pi * i / n))
        e1 = (c[0] + r * math.cos(2.0 * math.pi * (i + 1) / n),
              c[1] + r * math.sin(2.0 * math.pi * (i + 1) / n))
        ex, ey = e1[0] - e0[0], e1[1] - e0[1]

        def inside(p):
            return (ex * (p[1] - e0[1]) - ey * (p[0] - e0[0])) >= -1e-12

        def cut(p, q):
            px, py = q[0] - p[0], q[1] - p[1]
            den = ex * py - ey * px
            if abs(den) < 1e-15:
                return p
            t = (ex * (p[1] - e0[1]) - ey * (p[0] - e0[0])) / den
            return (p[0] - px * t, p[1] - py * t)

        nxt = []
        for k, cur in enumerate(poly):
            prv = poly[k - 1]
            if inside(cur):
                if not inside(prv):
                    nxt.append(cut(prv, cur))
                nxt.append(cur)
            elif inside(prv):
                nxt.append(cut(prv, cur))
        poly = nxt
    return poly


def detail_job(parent: dict, spec: dict, k: float) -> dict:
    """The detail view as a projected job: the parent's primitives clipped
    to the detail circle, moved so the region centre is the origin, and
    scaled by k. Its bounds ARE the circle (radius r*k) — the boundary is
    drawn, so the view occupies that square whatever ink falls inside."""
    cen = [float(spec["center"][0]), float(spec["center"][1])]
    r = float(spec["radius"])

    def xf(prims):
        out = []
        for pr in clip_prims(prims, cen, r):
            q = dict(pr)
            if pr["t"] == "seg":
                q["a"] = [(pr["a"][0] - cen[0]) * k, (pr["a"][1] - cen[1]) * k]
                q["b"] = [(pr["b"][0] - cen[0]) * k, (pr["b"][1] - cen[1]) * k]
            else:
                q["c"] = [(pr["c"][0] - cen[0]) * k, (pr["c"][1] - cen[1]) * k]
                q["r"] = pr["r"] * k
            out.append(q)
        return out

    groups = []
    for g in parent.get("hatch_groups", []):
        outer = clip_loop_to_circle(g["outer"], cen, r)
        if len(outer) < 3:
            continue
        sc = lambda lp: [[(x - cen[0]) * k, (y - cen[1]) * k] for x, y in lp]
        holes = [clip_loop_to_circle(h, cen, r) for h in g.get("holes", [])]
        groups.append({"outer": sc(outer),
                       "holes": [sc(h) for h in holes if len(h) >= 3]})
    rk = r * k
    return {"visible": xf(parent.get("visible", [])),
            "hidden": xf(parent.get("hidden", [])),
            "hatch_groups": groups,
            "bounds": [[-rk, -rk], [rk, rk]], "detail": True}


def _free_spot(ctx, layout, w, h, zones=(), prefer_top=True):
    """Free rectangle of w x h in the drawing area, clear of every claim AND
    of the views' dimension gutters (`zones`, as pictorial_box uses them —
    the gutters are still empty at this point and a block parked in one
    takes a dimension's only spot). Scans the upper sheet first and from the
    right, where the reference drawings put a detail; the bottom band is the
    last resort."""
    ax0, ay0, ax1, ay1 = ctx.draw_area
    pad, step = ctx.mm(3.0), ctx.mm(4.0)
    ny = max(0, int((ay1 - pad - h - (ay0 + pad)) / step))
    nx = max(0, int((ax1 - pad - w - (ax0 + pad)) / step))
    rows = range(ny, -1, -1) if prefer_top else range(ny + 1)
    for j in rows:
        yy = ay0 + pad + j * step
        for i in range(nx, -1, -1):
            xx = ax0 + pad + i * step
            r = (xx, yy, xx + w, yy + h)
            if not layout.inside(r) or layout.collides(r):
                continue
            if any(Layout._overlap(r, z, 0.0) for z in zones):
                continue
            return r
    return None


def place_detail_view(ctx, layout, part, spec, parent_job, parent_off,
                      zones=(), pad_mm=None):
    """Reserve and draw an enlarged detail view.

    `pad_mm` is the clear ring reserved round it, DETAIL_PAD_MM by default
    — enough for the detail's own dimensions. An ASSEMBLY detail carries no
    dimensions (its balloons go to the sheet's own column), so it asks for
    much less and fits where a dimensioned one would not.

    Returns (records, job, offset) or (records, None, None) when the sheet
    has no room for it — which fails the sheet, like any other annotation
    that cannot be placed."""
    k = detail_factor(part, spec)
    job = detail_job(parent_job, spec, k)
    rk = float(spec["radius"]) * k
    label = str(spec.get("label", "B"))
    if not job["visible"]:
        # the region caught no geometry: the centre is off the view, or the
        # radius is too small. An empty circle on the sheet tells the vendor
        # nothing, so fail loudly rather than ship it.
        layout.unresolved.append(f"detail-{label}-no-geometry")
        return [], None, None
    pad = ctx.mm(DETAIL_PAD_MM if pad_mm is None else pad_mm)
    cap = detail_caption(spec.get("scale", "1:1"), label)
    cap_th = ctx.mm(4.0)
    cap_w = mw.text_w(cap, cap_th)
    cap_h = cap_th + ctx.mm(8.0)           # caption height + its stand-off
    side = 2.0 * rk
    # the island has to hold the CAPTION too: it is wider than the circle
    # on a small detail, and a band nobody reserved is a band the callouts
    # fill, leaving the caption nowhere to go (it then failed the sheet)
    spot = _free_spot(ctx, layout, max(side + 2 * pad, cap_w + ctx.mm(2.0)),
                      side + 2 * pad + cap_h, zones)
    if spot is None:
        layout.unresolved.append(f"detail-{label}-noroom")
        return [], None, None
    # the island: geometry square centred in it, caption band underneath
    cx = (spot[0] + spot[2]) / 2.0
    cy = spot[1] + cap_h + pad + rk
    off = (cx, cy)                 # the detail's own origin is its centre
    layout.claim((cx - rk, cy - rk, cx + rk, cy + rk),
                 f"detail-{label}-view", "view")
    # the caption's own room, claimed with the view it names: one
    # stand-off under it, where _view_label would have put it
    cap_rect = (cx - cap_w / 2.0, cy - rk - cap_h,
                cx + cap_w / 2.0, cy - rk - cap_h + cap_th)
    # kind "caption": a reservation that YIELDS. A callout with nowhere
    # else to go may take it, and the caption then walks its own ladder
    # like any other view label — the band it reserved is the one spot it
    # is sure of, not the only one it can use
    layout.claim(cap_rect, f"label-{cap}", "caption")
    job["caption"] = (cap, cap_rect)
    recs = []
    # boundary: the detail is bounded by the same circle that marks it on
    # the parent view (ISO 128-34 continuous thin line)
    recs.append(mw.circle((cx, cy), rk))
    return recs, job, off


def detail_sheet(spec) -> int:
    """Which sheet of the set a detail view is drawn on (issue #19).

    An assembly's details and the rest of its parts table can go on the
    CONTINUATION sheet: sheet 1 then carries the views and the section,
    sheet 2 the enlargements and their balloons. The region is still
    marked on the view it comes from, on sheet 1, and the letter there
    says which sheet to turn to."""
    try:
        return max(1, int(spec.get("sheet", 1)))
    except (TypeError, ValueError):
        return 1


def detail_marker(ctx, layout, spec, parent_job, parent_off, sheet=None):
    """The circle round the region on the PARENT view, with its letter.

    The circle is ink laid over the view BY DESIGN (it encloses features),
    so it is not collision-tested; the letter is, like every other label."""
    cen = spec["center"]
    r = float(spec["radius"])
    label = str(spec.get("label", "B"))
    if sheet and sheet > 1:
        label = f"{label} (SH {sheet})"
    cx = cen[0] + parent_off[0]
    cy = cen[1] + parent_off[1]
    recs = [mw.circle((cx, cy), r)]
    h = ctx.mm(4.0)
    tw = mw.text_w(label, h)
    gap = ctx.mm(1.6)
    # upper right of the circle first, then round it: the letter sits
    # OUTSIDE the boundary so it never lands on the detailed features
    cands = []
    for d in (0.0, 2.0, 5.0):
        rr = r + gap + ctx.mm(d)
        for ang in (45, 135, 225, 315, 90, 0, 180, 270):
            a = math.radians(ang)
            px = cx + rr * math.cos(a)
            py = cy + rr * math.sin(a)
            cands.append((px - tw / 2 if abs(math.cos(a)) < 0.3 else
                          (px if math.cos(a) > 0 else px - tw),
                          py - h / 2 if abs(math.sin(a)) < 0.3 else
                          (py if math.sin(a) > 0 else py - h)))
    rect = layout.place(cands, tw, h, f"detail-letter-{label}")
    if rect is None:
        return recs                       # already recorded as unresolved
    recs.append(mw.mtext((rect[0], rect[1]), label, h, attach=3,
                         props=mw.P_ANNO))
    return recs


def detail_specs(specs, dspec, k):
    """Location dims for a detail view, moved into its enlarged frame.

    A detail's dims are AUTHORED in the parent view's coordinates — true
    millimetres, the same frame as detail_view.center and .radius — so the
    manifest never has to do the k arithmetic. Here they are moved onto the
    enlarged geometry they actually dimension; the value stays true because
    the style carries DIMLFAC = 1/k."""
    cen = dspec["center"]
    out = []
    for sp in specs:
        if sp.get("view") != "detail":
            out.append(sp)
            continue
        q = dict(sp)
        for key in ("a", "b"):
            q[key] = [(sp[key][0] - cen[0]) * k, (sp[key][1] - cen[1]) * k]
        out.append(q)
    return out


def detail_caption_records(ctx, layout, job, off):
    """The caption of a detail view: in the band place_detail_view kept
    for it, or — if a callout with nowhere else to go took that band —
    wherever the view-label ladder can put it."""
    cap, rect = job.get("caption", (None, None))
    if not cap:
        return []
    layout.unclaim(rect, f"label-{cap}")
    if not layout.collides(rect):
        layout.claim(rect, f"label-{cap}")
        return [mw.mtext(((rect[0] + rect[2]) / 2.0, rect[3]), cap,
                         ctx.mm(4.0), attach=1)]
    # the band it was kept was taken, so: a shorter stand-off first (a
    # detail's caption may sit closer than a view's), then the ladder,
    # and at the end a caption shifted along under its own circle rather
    # than no caption at all
    mark = len(layout.unresolved)
    recs = _view_label(ctx, layout, job, off, cap,
                       standoffs=(5.0, 8.0, 12.0, 16.0, 20.0, 24.0),
                       shifts=(0.0, 0.25, -0.25, 0.45, -0.45))
    if len(layout.unresolved) > mark:
        del layout.unresolved[mark:]
        layout.unresolved.append(
            f"label-{cap}: the detail's caption has nowhere to go — the "
            "band under it is full of leader notes. Move the detail "
            "(`detail_view.center`), shorten or move a callout, or draw "
            "this part on a bigger sheet")
    return recs


def detail_caption(scale: str, label: str) -> str:
    """"DETAIL B (2:1)" — the caption every detail view carries, naming its
    own scale because it differs from the sheet's."""
    return f"DETAIL {label} ({scale})"


# ---------------------------------------------------- layout / annotation

class Layout:
    """Bounding-box claim registry for one sheet's drawing area.

    Everything an annotation places goes through claim()/place(); place()
    walks candidate positions and takes the first that is inside the
    drawing area and clear of prior claims (auto-nudge). Anything that
    cannot be placed is recorded in `unresolved` and fails the sheet."""

    def __init__(self, ctx: mw.SheetContext):
        self.ctx = ctx
        self.area = ctx.draw_area
        self.claims: list[tuple] = []   # (x0,y0,x1,y1,label,kind)
        # every leader drawn so far, as (tip, knee, label): a bounding box
        # cannot tell a leader crossing another from one running beside it,
        # and two leaders that cross read as pointing at each other's
        # features (issue #18)
        self.leaders: list[tuple] = []
        # the dimension ink, as segments: a leader may not cross a
        # dimension LINE (it reads as part of the dimension), and
        # crossing an extension line is a warning (issue #25)
        self.dim_lines: list[tuple] = []
        self.ext_lines: list[tuple] = []
        # the cutting plane's sight arrows and the pictorial box: ink a
        # leader has no business crossing either
        self.keep_off: list[tuple] = []
        self.unresolved: list[str] = []
        # placed, but not as it should be: a leader with nowhere clean to
        # go is still DRAWN — a dropped callout tells the vendor nothing,
        # while a crossing one tells them something imperfectly — and the
        # fault is recorded here, reported per sheet and found again by
        # the layout check on the finished sheet, which stops /export
        # (issue #18)
        self.faults: list[str] = []
        self.flips: list[str] = []      # dims moved to the view's near edge
        self.nudges = 0

    @staticmethod
    def _overlap(a, b, pad):
        return not (a[2] + pad <= b[0] or b[2] + pad <= a[0]
                    or a[3] + pad <= b[1] or b[3] + pad <= a[1])

    def inside(self, r) -> bool:
        ax0, ay0, ax1, ay1 = self.area
        return (r[0] >= ax0 and r[1] >= ay0 and r[2] <= ax1 and r[3] <= ay1)

    def collides(self, r, ignore=()) -> bool:
        pad = self.ctx.mm(0.8)
        return any(self._overlap(r, c[:4], pad) for c in self.claims
                   if c[5] not in ignore)

    def hits(self, r, kinds=("line",)) -> bool:
        """Touch test against one class of claims only (no clearance pad) —
        used to keep leaders from running ALONG dimension ink."""
        return any(self._overlap(r, c[:4], 0.0) for c in self.claims
                   if c[5] in kinds)

    def claim(self, r, label, kind="anno"):
        self.claims.append((r[0], r[1], r[2], r[3], label, kind))

    def snapshot(self):
        """Everything this layout has decided so far, to put back when a
        TRIAL placement is not the one that is kept (issue #20: the
        balloons are placed once to see which of them would have to cross
        a view, and then again with those moved to the view that shows
        them)."""
        return (list(self.claims), list(self.unresolved),
                list(self.leaders), list(self.flips), list(self.faults),
                self.nudges)

    def crossing_ink(self, tip, knee):
        """What this leader would cross that belongs to something else:
        a dimension line, a sight arrow, the pictorial box (issue #25).
        Extension lines are not here — crossing one is a warning, and
        `crosses_extension` asks about those."""
        for a, b, what in list(self.dim_lines) + list(self.keep_off):
            if _segs_cross(tip, knee, a, b, 0.0):
                return what
        return None

    def crosses_extension(self, tip, knee) -> bool:
        return any(_segs_cross(tip, knee, a, b, 0.0)
                   for a, b, _w in self.ext_lines)

    def restore(self, snap) -> None:
        (claims, unresolved, leaders, flips, faults, nudges) = snap
        self.claims = list(claims)
        self.unresolved = list(unresolved)
        self.leaders = list(leaders)
        self.flips = list(flips)
        self.faults = list(faults)
        self.nudges = nudges

    def release(self, kind) -> int:
        """Drop every claim of one kind. The GD&T slots (place_callouts'
        `reserve`) exist to keep a callout's column free while the frames
        and datums find their places; once those are down, what they did
        not use belongs to the notes block like any other free space."""
        n = len(self.claims)
        self.claims = [c for c in self.claims if c[5] != kind]
        return n - len(self.claims)

    def unclaim(self, r, label) -> bool:
        """Give back a claim this label took: a candidate that was free but
        failed a LATER test (a frame that fits under a callout but does not
        read as that callout's, issue #7). Only the exact rect goes."""
        if r is None:
            return False
        for i in range(len(self.claims) - 1, -1, -1):
            c = self.claims[i]
            if c[4] == label and c[:4] == tuple(r):
                del self.claims[i]
                return True
        return False

    def place(self, cands, w, h, label, kind="anno", ignore=()):
        for i, (x, y) in enumerate(cands):
            r = (x, y, x + w, y + h)
            if self.inside(r) and not self.collides(r, ignore):
                if i > 0:
                    self.nudges += 1
                self.claim(r, label, kind)
                return r
        self.unresolved.append(label)
        return None

    def leader_crossing(self, tip, knee):
        """The label of the first leader this one would cross, or None."""
        for t, k, label in self.leaders:
            if _segs_cross(tip, knee, t, k, self.ctx.mm(0.5)):
                return label
        return None

    def leader_crossings(self, tip, knee) -> list:
        """Every leader this one would cross. A leader that has to cross
        one is still better than one that crosses five, so the last-resort
        knee is chosen by this count (issue #18)."""
        return [label for t, k, label in self.leaders
                if _segs_cross(tip, knee, t, k, self.ctx.mm(0.5))]

    def anno_clearance(self, r, ignore=None) -> float:
        """Distance from `r` to the nearest TEXT claim. A symbol that must
        read as belonging to one annotation (a datum box on its callout's
        shoulder) has to stand clear of every other one, or the reader
        pairs it with the wrong label — "%%c60 [A]" when A is the bore's.

        `ignore` is a rect whose claim does not count: the annotation the
        symbol BELONGS to, so the distance measured is to the strangers
        (issue #27)."""
        best = float("inf")
        for c in self.claims:
            if c[5] != "anno":
                continue
            if ignore is not None and all(
                    abs(c[i] - ignore[i]) < 1e-6 for i in range(4)):
                continue
            best = min(best, rect_gap(r, c[:4]))
        return best

    def report(self):
        return {"nudges": self.nudges, "unresolved": self.unresolved,
                "faults": self.faults,
                "flips": self.flips, "claims": len(self.claims)}




# ------------------------------------------------- linear-dim label geometry
# MusaCAD centers a linear dim's label on the dim-line MIDPOINT, baseline
# 0.4h clear of the line, glyphs growing along the TEXT's own up-vector
# (core/dimension.cpp compute_dim_geometry + Justify::Center). For a vertical
# dim the label reads bottom-to-top, so its up-vector is -x: the value always
# sits to the LEFT of the dim line, whichever side of the view the dim is on.
# Model the ink exactly — a boxed (basic) dim has to be framed where the
# glyphs really are.

def rect_gap(a, b) -> float:
    """Shortest distance between two rects (0 when they touch)."""
    dx = max(b[0] - a[2], 0.0, a[0] - b[2])
    dy = max(b[1] - a[3], 0.0, a[1] - b[3])
    return math.hypot(dx, dy)


def dim_label_ink(label, h):
    """Width of a dim label's INK: the pen advance minus the last glyph's
    trailing gap (kAdvance 0.62 vs kGlyphW 0.52 of the height)."""
    return mw.glyph_w(label, h) - 0.1 * h


def dim_label_rect(a, b, line_pt, orient, label, h):
    """Ink rect of the label DIM(a, b, line_pt) will render."""
    w = mw.glyph_w(label, h)
    ink = dim_label_ink(label, h)
    if orient == "h":
        mid = (a[0] + b[0]) / 2.0
        y0 = line_pt[1] + 0.4 * h
        return (mid - w / 2.0, y0, mid - w / 2.0 + ink, y0 + h)
    mid = (a[1] + b[1]) / 2.0
    x1 = line_pt[0] - 0.4 * h
    return (x1 - h, mid - w / 2.0, x1, mid - w / 2.0 + ink)


# ISO 3098 character heights a cramped value may drop to, largest first.
# 1.8 mm is the smallest size in the series and the hard floor: below it the
# value stops being reliably legible on a printed A4 sheet, and the answer
# is then a DRAWING decision (re-dimension from a datum, split the view, or
# waive), never a smaller glyph.
CRAMPED_TEXT_HEIGHTS = (2.5, 1.8)
CRAMPED_TICK_SIZES = (1.6, 1.2, 0.9, 0.7, 0.6, 0.5)   # oblique strokes, mm
CRAMPED_DIMSTYLE = 1        # index of the cut-back-extension style (sheet_dimstyles)
INK_CLEAR = 0.35            # required WHITE gap, printed mm
DIM_PEN = 0.25              # dimension-line pen width, printed mm (lw 25)
# a thresholded raster counts antialiased edge pixels as ink, so a measured
# clearance runs ~0.05 mm tighter than the geometric one (checked at 1200 dpi)
RASTER_AA = 0.05


# DIMSTYLE defaults, printed mm (musa_writer.dimstyle's own derivation)
EXT_OFFSET = 0.6 * 3.0 / 2.5        # gap from the def point to the ext line
EXT_EXTENSION = 1.25 * 3.0 / 2.5    # overshoot past the dim line
EXT_EXTENSION_CRAMPED = 0.15


def sheet_dimstyles(ctx):
    """The dim styles a sheet starts with: index 0 the house ISO style,
    index 1 (CRAMPED_DIMSTYLE) the same with the extension-line overshoot cut
    back to a hairline, for values too wide to sit between their own
    extension lines. dim_style_index() appends more on demand (extension
    lines that have to start further out)."""
    ctx.dimunits = []
    ctx._dimstyle_index = {(EXT_OFFSET, EXT_EXTENSION, 1.0): 0,
                           (EXT_OFFSET, EXT_EXTENSION_CRAMPED, 1.0): 1}
    return [mw.dimstyle(ctx.mm(3.0), ctx.mm(2.5), color=(255, 0, 255)),
            mw.dimstyle(ctx.mm(3.0), ctx.mm(2.5), color=(255, 0, 255),
                        name="Cramped",
                        ext_extension=ctx.mm(EXT_EXTENSION_CRAMPED))]


def dim_style_index(ctx, ext_offset=EXT_OFFSET, ext_extension=EXT_EXTENSION,
                    lfac=1.0):
    """Index of a DIMSTYLE with this (ext_offset, ext_extension, lfac), in
    printed mm; created on first use. ext_offset is how far from the def
    point the extension line STARTS — the ISO 129-1 interrupted extension
    line, used when a full-length one would run along drawn edges (a
    cutting-plane line's overshoot, say) that the dim itself cannot move
    away from. `lfac` is DIMLFAC, the factor every measured length is
    multiplied by before it is written: 1/k inside a detail view drawn at k
    times size, so its dimensions read true millimetres."""
    key = (round(ext_offset, 3), round(ext_extension, 3), round(lfac, 6))
    idx = ctx._dimstyle_index.get(key)
    if idx is None:
        idx = len(ctx.dimstyles)
        ctx.dimstyles.append(
            mw.dimstyle(ctx.mm(3.0), ctx.mm(2.5), color=(255, 0, 255),
                        name=f"Ext{idx}", ext_offset=ctx.mm(ext_offset),
                        ext_extension=ctx.mm(ext_extension)))
        if abs(lfac - 1.0) > 1e-9:
            ctx.dimunits.append(mw.dimunits(idx, lfac))
        ctx._dimstyle_index[key] = idx
    return idx


# ------------------------------------------- dimension ink vs view geometry
# A dimension's extension lines are INK, drawn by the renderer from the def
# points to the dim line. Nothing in the placer used to look at them, so a
# dim line parked far from its feature dragged 60-120 mm of magenta across
# the views — along outlines, through hatch, through other features (round-8
# QA). Reconstruct exactly what dimension.cpp will draw and test THAT.

COLLINEAR_ANGLE = 0.026     # sin of ~1.5 deg: "running along", not crossing
COLLINEAR_GAP = 0.45        # printed mm: closer than this reads as one line
COLLINEAR_OK = 1.0          # printed mm of incidental overlap tolerated


def dim_segments(a, b, line_pt, orient, ext_offset, ext_extension):
    """(extension segments, dim-line segment) exactly as core/dimension.cpp
    computes them: feet are the def points projected onto the dim line, the
    extension line runs def+n*ext_offset -> foot+n*ext_extension."""
    d = (1.0, 0.0) if orient == "h" else (0.0, 1.0)

    def foot(p):
        t = (p[0] - line_pt[0]) * d[0] + (p[1] - line_pt[1]) * d[1]
        return (line_pt[0] + d[0] * t, line_pt[1] + d[1] * t)

    exts = []
    for p in (a, b):
        f = foot(p)
        vx, vy = f[0] - p[0], f[1] - p[1]
        ln = math.hypot(vx, vy)
        if ln < 1e-9:
            continue
        nx, ny = vx / ln, vy / ln
        exts.append(((p[0] + nx * ext_offset, p[1] + ny * ext_offset),
                     (f[0] + nx * ext_extension, f[1] + ny * ext_extension)))
    return exts, (foot(a), foot(b))


def _collinear_overlap(p0, p1, q0, q1, gap):
    """Length of q that runs ALONG p (near-parallel and within `gap`)."""
    ux, uy = p1[0] - p0[0], p1[1] - p0[1]
    L = math.hypot(ux, uy)
    vx, vy = q1[0] - q0[0], q1[1] - q0[1]
    M = math.hypot(vx, vy)
    if L < 1e-9 or M < 1e-9:
        return 0.0
    ux, uy, vx, vy = ux / L, uy / L, vx / M, vy / M
    if abs(ux * vy - uy * vx) > COLLINEAR_ANGLE:
        return 0.0
    for q in (q0, q1):
        if abs((q[0] - p0[0]) * -uy + (q[1] - p0[1]) * ux) > gap:
            return 0.0
    t0, t1 = sorted(((q0[0] - p0[0]) * ux + (q0[1] - p0[1]) * uy,
                     (q1[0] - p1[0]) * ux + (q1[1] - p1[1]) * uy + L))
    return max(0.0, min(L, t1) - max(0.0, t0))


def _proper_crossing(p0, p1, q0, q1, skip):
    """True if the segments cross transversely, away from p's endpoints by
    more than `skip` (an extension line legitimately starts on an outline)."""
    rx, ry = p1[0] - p0[0], p1[1] - p0[1]
    sx, sy = q1[0] - q0[0], q1[1] - q0[1]
    den = rx * sy - ry * sx
    if abs(den) < 1e-12:
        return False
    qpx, qpy = q0[0] - p0[0], q0[1] - p0[1]
    t = (qpx * sy - qpy * sx) / den
    u = (qpx * ry - qpy * rx) / den
    if not (0.0 <= u <= 1.0 and 0.0 <= t <= 1.0):
        return False
    L = math.hypot(rx, ry)
    return skip / L < t < 1.0 - 1e-9 if L > 0 else False


def dim_ink_conflict(ctx, geom, segs, soft=(), skip=1.2):
    """(worst collinear overlap in printed mm, transverse crossings) between
    a dimension's own segments and the drawn geometry.

    `geom` is part geometry and the cutting-plane line: dimension ink may
    neither run along it nor cross it. `soft` is annotation ink a dimension
    is allowed to CROSS transversely but not run along (the cutting-plane
    arrow strokes: a section arrow stands in every dimension gutter on its
    side, and a 0.5 mm crossing reads as normal drafting)."""
    gap = ctx.mm(COLLINEAR_GAP)
    sk = ctx.mm(skip)
    worst, crossings = 0.0, 0
    for p0, p1 in segs:
        lo = min(p0[0], p1[0]) - gap, min(p0[1], p1[1]) - gap
        hi = max(p0[0], p1[0]) + gap, max(p0[1], p1[1]) + gap
        for q0, q1, hard in [(q[0], q[1], True) for q in geom] + \
                            [(q[0], q[1], False) for q in soft]:
            if (max(q0[0], q1[0]) < lo[0] or min(q0[0], q1[0]) > hi[0]
                    or max(q0[1], q1[1]) < lo[1] or min(q0[1], q1[1]) > hi[1]):
                continue
            ov = _collinear_overlap(p0, p1, q0, q1, gap)
            if ov > worst:
                worst = ov
            if hard and ov == 0.0 and _proper_crossing(p0, p1, q0, q1, sk):
                crossings += 1
    return worst / ctx.mm(1.0), crossings


def ext_clear_offset(ctx, geom, a, b, line_pt, orient):
    """How far from the def points the extension lines must START so they no
    longer run along drawn geometry (0 = the house offset is fine). Used for
    the one conflict a dimension cannot dodge: a def point sitting ON another
    line the drawing must keep (the cutting-plane line through the slots on
    sheet 03 shares the slot centreline the 50 dim references)."""
    gap = ctx.mm(COLLINEAR_GAP)
    need = 0.0
    exts, _ = dim_segments(a, b, line_pt, orient, 0.0, ctx.mm(EXT_EXTENSION))
    for p0, p1 in exts:
        ux, uy = p1[0] - p0[0], p1[1] - p0[1]
        L = math.hypot(ux, uy)
        if L < 1e-9:
            continue
        ux, uy = ux / L, uy / L
        for q0, q1 in geom:
            if _collinear_overlap(p0, p1, q0, q1, gap) <= ctx.mm(COLLINEAR_OK):
                continue
            for q in (q0, q1):
                t = (q[0] - p0[0]) * ux + (q[1] - p0[1]) * uy
                need = max(need, min(t, L))
    return need / ctx.mm(1.0) + (1.0 if need > 0 else 0.0)


def view_geometry(ctx, view_jobs, offs, extra=()):
    """Every black line the sheet draws for its views, as segments: visible
    and hidden edges, section hatch boundaries, plus `extra` (the
    cutting-plane line). This is what dimension ink must not run along."""
    segs = list(extra)
    for name, job in view_jobs.items():
        off = offs.get(name, offs.get("section"))
        segs += prim_segments(job.get("visible", []), off)
        segs += prim_segments(job.get("hidden", []), off)
        for g in job.get("hatch_groups", []):
            for loop in [g["outer"]] + list(g.get("holes", [])):
                pts = [(x + off[0], y + off[1]) for x, y in loop]
                segs += list(zip(pts, pts[1:] + pts[:1]))
    return segs


def _seg_pt_dist(a, b, p):
    vx, vy = b[0] - a[0], b[1] - a[1]
    L2 = vx * vx + vy * vy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * vx
                                               + (p[1] - a[1]) * vy) / L2))
    return math.hypot(p[0] - (a[0] + t * vx), p[1] - (a[1] + t * vy))


def _seg_rect_dist(a, b, r):
    """Distance between a segment and an axis-aligned rect (0 if they meet).
    Exact for convex shapes: the minimum is attained at a vertex of one
    against the other."""
    def pt_rect(p):
        dx = max(r[0] - p[0], 0.0, p[0] - r[2])
        dy = max(r[1] - p[1], 0.0, p[1] - r[3])
        return math.hypot(dx, dy)
    corners = ((r[0], r[1]), (r[2], r[1]), (r[2], r[3]), (r[0], r[3]))
    return min(pt_rect(a), pt_rect(b),
               min(_seg_pt_dist(a, b, c) for c in corners))


def cramped_dim_style(ctx, label, span, h):
    """ISO 129-1 narrow-dimension treatment, sized by real glyph ink.

    A linear dim's value is ALWAYS centred on the midpoint of the feet
    (core/dimension.cpp:216, unconditional) and its arrowheads always point
    inwards, so a short span has exactly one lever: character height. Step
    down the ISO 3098 series until the drawn ink clears both extension lines
    by INK_CLEAR of white — measuring ink to ink, i.e. allowing for the
    extension line's own 0.25 mm pen — and replace the filled arrowheads
    with oblique strokes short enough to stay clear of the value too.
    Returns (text_height, dim kwargs)."""
    # white + half the extension line's pen + the raster allowance
    need = ctx.mm(INK_CLEAR + DIM_PEN / 2.0 + RASTER_AA)

    def gaps(hh):
        """(left, right) ink-to-extension-line clearances at height hh."""
        adv = mw.glyph_w(label, hh)
        l, r = mw.glyph_ink_span(label, hh)
        return (span / 2.0 - (adv / 2.0 - l), span / 2.0 - (r - adv / 2.0))

    if min(gaps(h)) >= need and span >= ctx.mm(6.0):
        return h, {}
    hh = h
    for cand in CRAMPED_TEXT_HEIGHTS:
        if ctx.mm(cand) < h:
            hh = ctx.mm(cand)
            if min(gaps(hh)) >= need:
                break
    # oblique strokes sit ON the feet at 45 deg and reach up TOWARDS the
    # value, so size them to keep their ink — pen width AND round cap, which
    # extend past the stroke's end point — clear of the value's whole ink
    # BOX. Measuring to its lower-left corner alone is only right while the
    # value fits between its feet: a value WIDER than its own span (2.55 in
    # a 2.55 mm gap, sheet 04) sits over both ticks, and then the vertical
    # gap to the digits' underside is what decides (round-8 QA).
    adv = mw.glyph_w(label, hh)
    l, r = mw.glyph_ink_span(label, hh)
    ink_box = (-adv / 2.0 + l, 0.4 * hh, -adv / 2.0 + r, 1.4 * hh)
    # both pens (the tick's and the glyph's) plus the raster allowance stand
    # between the two centre lines and the white the reader sees
    need_tick = ctx.mm(INK_CLEAR + DIM_PEN + RASTER_AA)
    asz = ctx.mm(CRAMPED_TICK_SIZES[-1])
    for cand in CRAMPED_TICK_SIZES:
        a = ctx.mm(cand)
        # core/dimension.cpp append_arrowhead Tick: d = (u + perp)*size*0.5,
        # i.e. HALF the arrow size along each axis (total length size*sqrt2)
        d = 0.5 * a
        if _seg_rect_dist((-span / 2 - d, -d), (-span / 2 + d, d),
                          ink_box) >= need_tick:
            asz = a
            break
    # the caller resolves the DIMSTYLE (dim_style_index): a cramped value
    # needs the extension lines to STOP at the dim line, because a value
    # wider than its own span (2.55 in a 2.55 mm gap, sheet 04) is centred
    # over its feet by the renderer and cannot dodge them, so the 0.5 h ISO
    # overshoot would print through the digits.
    kw = {"arrow_type": mw.ARROW_TICK, "arrow_size": asz}
    if hh < h:
        kw["text_height"] = hh
    return hh, kw


def dim_line_band(ctx, a, b, line_pt, orient):
    """The dim line's own ink band. Arrowheads are drawn INSIDE the span
    (dimension.cpp append_arrowhead: base = tip + u*size), so the band stops
    at the feet + a hairline margin; claiming an arrow's length BEYOND each
    end used to make neighbouring dims of one chain reject each other."""
    bandw, endw = ctx.mm(1.6), ctx.mm(0.5)
    if orient == "h":
        return (min(a[0], b[0]) - endw, line_pt[1] - bandw,
                max(a[0], b[0]) + endw, line_pt[1] + bandw)
    return (line_pt[0] - bandw, min(a[1], b[1]) - endw,
            line_pt[0] + bandw, max(a[1], b[1]) + endw)


def _place_dim(ctx, layout, cands, label, grow=0.0):
    """Try (line_pt, text_ink_rect, line_band) candidates: accept the first
    whose TEXT rect — grown by `grow` when the value is boxed (basic) — fits
    AND whose dim-line band does not cross existing text/annotation claims.
    Line-vs-line crossings are normal drafting and ignored.
    Returns (line_pt, ink_rect, claimed_rect)."""
    for i, (lp, rect, band) in enumerate(cands):
        claim = (rect[0] - grow, rect[1] - grow, rect[2] + grow, rect[3] + grow)
        if not layout.inside(claim) or layout.collides(claim):
            continue
        if layout.collides(band, ignore=("line", "view", "reserved")):
            continue
        if i > 0:
            layout.nudges += 1
        layout.claim(claim, label)
        return lp, rect, claim
    layout.unresolved.append(label)
    return None, None, None


def _claim_dim_ink(ctx, layout, a, b, line_pt, orient, label):
    """Claim the dim line band and the two extension-line corridors so later
    annotations cannot be overprinted by dim ink — and remember both as
    SEGMENTS, which is what a leader has to be kept off (issue #25)."""
    exts, dline = dim_segments(a, b, line_pt, orient,
                               ctx.mm(EXT_OFFSET), ctx.mm(EXT_EXTENSION))
    layout.dim_lines.append((dline[0], dline[1], f"the dimension {label}"))
    for e in exts:
        layout.ext_lines.append((e[0], e[1], f"an extension line of {label}"))
    band = ctx.mm(1.6)
    # rendered extension lines OVERSHOOT the dim line by ext_extension
    # (1.5 sheet units for our dimstyle) — claim past it
    over = ctx.mm(2.2)
    layout.claim(dim_line_band(ctx, a, b, line_pt, orient),
                 f"dimline-{label}", "line")
    if orient == "h":
        y = line_pt[1]
        for px, py in (a, b):
            yy0, yy1 = sorted((py, y))
            layout.claim((px - band / 4, yy0 - (over if yy0 == y else 0),
                          px + band / 4, yy1 + (over if yy1 == y else 0)),
                         f"dimext-{label}", "line")
    else:
        x = line_pt[0]
        for px, py in (a, b):
            xx0, xx1 = sorted((px, x))
            layout.claim((xx0 - (over if xx0 == x else 0), py - band / 4,
                          xx1 + (over if xx1 == x else 0), py + band / 4),
                         f"dimext-{label}", "line")


def _leader_path_clear(ctx, layout, tip, knee, max_frac=0.25):
    """A leader may CROSS dimension/extension ink — that is normal drafting —
    but must never run ALONG it (a level leader out of a dimensioned hole row
    lands exactly on that row's extension line and the two merge into one
    stroke), and must never cross TEXT at all. Sample the tip->knee segment:
    reject on any text hit, and on spending more than `max_frac` of the
    length inside claimed line ink."""
    length = math.hypot(knee[0] - tip[0], knee[1] - tip[1])
    steps = max(4, round(length / ctx.mm(2.0)))
    tol = ctx.mm(0.4)           # ink-overlap tolerance (running ALONG)
    clr = ctx.mm(0.8)           # text clearance — the registry's own pad
    hit = 0
    for i in range(steps + 1):
        f = i / steps
        px = tip[0] + (knee[0] - tip[0]) * f
        py = tip[1] + (knee[1] - tip[1]) * f
        if layout.hits((px - clr, py - clr, px + clr, py + clr), ("anno",)):
            return False
        if layout.hits((px - tol, py - tol, px + tol, py + tol)):
            hit += 1
    return hit <= max_frac * (steps + 1)


def _segs_cross(a0, a1, b0, b1, eps) -> bool:
    """Do two segments cross TRANSVERSELY, away from both their ends?

    A leader legitimately starts on the outline it points at and ends in a
    column of knees beside its neighbours, so a touch at an endpoint is
    not a crossing; `eps` is how far from either end the intersection has
    to be before it counts."""
    rx, ry = a1[0] - a0[0], a1[1] - a0[1]
    sx, sy = b1[0] - b0[0], b1[1] - b0[1]
    den = rx * sy - ry * sx
    if abs(den) < 1e-12:
        return False                      # parallel: never a crossing
    qpx, qpy = b0[0] - a0[0], b0[1] - a0[1]
    t = (qpx * sy - qpy * sx) / den
    u = (qpx * ry - qpy * rx) / den
    la = math.hypot(rx, ry) or 1.0
    lb = math.hypot(sx, sy) or 1.0
    return (eps / la < t < 1.0 - eps / la
            and eps / lb < u < 1.0 - eps / lb)


def _crossing_other(layout, tip, knee, label):
    """The label of a leader this one crosses, other than its own."""
    for t, k, lab in layout.leaders:
        if lab != label and _segs_cross(tip, knee, t, k,
                                        layout.ctx.mm(0.5)):
            return lab
    return None


def leader_runs_along(ctx, geom, tip, knee) -> float:
    """Printed millimetres this leader would run ALONG drawn geometry.

    A leader that lies on the outline it points at cannot be told from it
    (ISO 128-22): the arrow stops being an arrow and becomes part of the
    part. Crossing geometry transversely is ordinary drafting and is not
    measured here — only the collinear run (issue #18)."""
    worst, _ = dim_ink_conflict(ctx, geom, [(tuple(tip), tuple(knee))])
    return worst


def _claim_leader_ink(ctx, layout, tip, knee, label):
    """Claim a thin corridor of small rects along a leader's tip->knee
    segment (sampled — a diagonal's single bbox would over-claim) so later
    annotations (FCF stacks, datum boxes, note blocks) cannot sit on
    leader ink. Mirrors _claim_dim_ink; kind "line" keeps line-vs-line
    crossings legal."""
    band = ctx.mm(0.9)
    steps = max(1, round(math.hypot(knee[0] - tip[0], knee[1] - tip[1])
                         / ctx.mm(4.0)))
    for i in range(steps + 1):
        f = i / steps
        px = tip[0] + (knee[0] - tip[0]) * f
        py = tip[1] + (knee[1] - tip[1]) * f
        layout.claim((px - band, py - band, px + band, py + band),
                     f"leader-{label}", "line")
    layout.leaders.append((tuple(tip), tuple(knee), label))


def fmt_val(v: float) -> str:
    return str(round(v)) if abs(v - round(v)) < 0.005 else f"{v:.2f}"


# A dimension's def points are placed full size, so the measured length
# is a difference of two large floats and its last bits depend on where
# the view happens to sit. A value exactly halfway between two printed
# ones then rounds one way on A4 and the other on A3 (issue #26): the
# same 362.625 tube printed "362.63" and "362.62". These snap the def
# points so the measurement is the value and nothing else, and carry a
# tie away from zero, which is how a drawing is read.
TIE_WINDOW = 1e-6           # of the printed step: inside this, a value
                            # IS the tie and the float noise is noise
TIE_NUDGE = 1e-3            # of the printed step: invisible, decisive


def snap_value(v: float, precision: int):
    """The value a dimension on a tie should measure, or None when it is
    not on one.

    Only a tie is touched. Anywhere else the float error (about 1e-10 mm
    on a 362 mm length) cannot reach the rounding boundary, so the value
    prints the same wherever the view sits and the sheet is left exactly
    as it was."""
    step = 10.0 ** -precision
    half = abs(v) / step % 1.0 - 0.5
    if abs(half) > TIE_WINDOW:
        return None
    # exactly halfway: round half AWAY FROM ZERO, whatever the C library's
    # own rounding mode would have made of it
    tie = (round(abs(v) / step - 0.5) + 0.5 + TIE_NUDGE) * step
    return tie if v >= 0 else -tie


def snap_def_points(a, b, orient, precision, lfac=1.0):
    """`b` moved so the dimension measures `snap_value` exactly.

    `orient` is the axis the value is measured along ("h" or "v"), or
    None for a length measured along the line itself (an aligned or
    diameter dim). The move is at most a micrometre of sheet, which no
    paper and no plot can show."""
    if not lfac:
        return b
    axis = {"h": 0, "v": 1}.get(orient)
    if axis is None:
        dx, dy = b[0] - a[0], b[1] - a[1]
        ln = math.hypot(dx, dy)
        snapped = snap_value(ln * lfac, precision) if ln > 1e-12 else None
        if snapped is None:
            return b
        want = snapped / lfac
        return (a[0] + dx * want / ln, a[1] + dy * want / ln)
    d = b[axis] - a[axis]
    snapped = snap_value(abs(d) * lfac, precision)
    if snapped is None:
        return b
    want = snapped / lfac
    want = want if d >= 0 else -want
    return (a[0] + want, b[1]) if axis == 0 else (b[0], a[1] + want)


def prec_for(v: float) -> int:
    """Decimal places a linear DIM needs to print its TRUE measured value.

    The DIMSTYLE default is 0 places, which silently rounded every non-integer
    dimension: three graded clamp shims (4.5, 5.5, 6.5) all printed as one
    integer and a 332.80 blank printed 333. A drawing that states a value it
    does not mean is worse than no drawing, so match fmt_val exactly -- the
    layout already reserves space for that string.
    """
    return 0 if abs(v - round(v)) < 0.005 else 2


def _outboard(steps, floor_mm):
    """Keep only stand-offs clear of an inner (feature) dim line: an overall
    dim always sits OUTSIDE the feature dims on the same side of the view."""
    out = tuple(d for d in steps if d >= floor_mm + 3.0)
    return out or steps


def dim_extents(ctx, layout, job, off, *, width_side="top",
                height_side="left", which=("w", "h"), floors=None, geom=(),
                soft=()):
    """Overall width/height linear dims, auto-nudged offsets. `which`
    restricts the set — a shared-axis section view must not repeat the
    front view's dim for the shared direction. `floors` maps
    "width"/"height" -> the outermost stand-off (printed mm) already taken
    by a feature dim on that side, so the overall dim starts beyond it.
    `geom` is the sheet's drawn view geometry: a candidate whose extension
    or dimension line would run along it (a stacked view's outline, say) is
    rejected the same way a feature dim's is."""
    mn, mx = job["bounds"]
    ox, oy = off
    x0, y0, x1, y1 = mn[0] + ox, mn[1] + oy, mx[0] + ox, mx[1] + oy
    floors = floors or {}
    h = ctx.mm(3.0)
    recs = []
    # coarse reference offsets first (existing sheets keep their spot), then
    # a fine 0.5 mm fallback sweep — a crowded gutter (an inner feature dim
    # whose rotated label eats the next slot) can leave only sub-step gaps
    steps = (8.0, 11.0, 14.0, 17.0, 18.5, 20.0, 24.0, 28.0, 32.0, 36.0)
    steps += tuple(d for d in (8.0 + 0.5 * k for k in range(57))
                   if d not in steps)

    def build(a, b, orient, lps, label):
        cands = []
        for lp in lps:
            exts, dline = dim_segments(a, b, lp, orient, ctx.mm(EXT_OFFSET),
                                       ctx.mm(EXT_EXTENSION))
            worst, cross = dim_ink_conflict(ctx, geom, exts + [dline], soft)
            if worst > COLLINEAR_OK or cross:
                continue
            cands.append((lp, dim_label_rect(a, b, lp, orient, label, h),
                          dim_line_band(ctx, a, b, lp, orient)))
        return cands

    if "w" in which:
        label = fmt_val(x1 - x0)
        sign = 1 if width_side == "top" else -1
        ybase = y1 if width_side == "top" else y0
        a, b = (x0, ybase), (x1, ybase)
        lps = [((x0 + x1) / 2, ybase + sign * ctx.mm(d))
               for d in _outboard(steps, floors.get("width", 0.0))]
        lp, _, _ = _place_dim(ctx, layout, build(a, b, "h", lps, label),
                              f"dim-w-{label}")
        if lp is not None:
            prec = prec_for(x1 - x0)
            recs.append(mw.dim(0, a, snap_def_points(a, b, "h", prec), lp,
                               precision=prec))
            _claim_dim_ink(ctx, layout, a, b, lp, "h", label)
    if "h" in which:
        label = fmt_val(y1 - y0)
        sign = -1 if height_side == "left" else 1
        xbase = x0 if height_side == "left" else x1
        a, b = (xbase, y0), (xbase, y1)
        lps = [(xbase + sign * ctx.mm(d), (y0 + y1) / 2)
               for d in _outboard(steps, floors.get("height", 0.0))]
        lp, _, _ = _place_dim(ctx, layout, build(a, b, "v", lps, label),
                              f"dim-h-{label}")
        if lp is not None:
            prec = prec_for(y1 - y0)
            recs.append(mw.dim(0, a, snap_def_points(a, b, "v", prec), lp,
                               precision=prec))
            _claim_dim_ink(ctx, layout, a, b, lp, "v", label)
    return recs


def dim_circles(ctx, layout, job, off, suppress=(), limit=4, lfac=1.0,
                geom=()):
    """Diameter dims for distinct hole radii; angle candidates dodge
    collisions; radii in `suppress` are handled by callouts instead.
    `lfac` is the view's DIMLFAC (1/k in a detail view), so the label reads
    the true diameter of geometry drawn at k times size.
    Returns (records, {radius_key: text_rect}) — the rects anchor FCFs."""
    ox, oy = off
    h = ctx.mm(3.0)
    mnb, mxb = job["bounds"]
    vrect = (mnb[0] + ox, mnb[1] + oy, mxb[0] + ox, mxb[1] + oy)
    seen, recs, rects = set(), [], {}
    circles = sorted((pr for pr in job["visible"] if pr["t"] == "circle"),
                     key=lambda c: -c["r"])
    for c in circles:
        key = round(c["r"], 3)
        if key in seen or len(recs) >= limit:
            continue
        if any(abs(c["r"] - s) < 0.05 for s in suppress):
            continue
        seen.add(key)
        # every failed attempt below records itself as unresolved; the ones
        # that were only a rejected FIRST CHOICE get rolled back once this
        # radius lands somewhere, so the sheet is not failed for a dim it
        # actually carries (a ring too narrow for its own label fell into
        # the leader fallback, succeeded, and still failed the sheet)
        mark = len(layout.unresolved)
        cx, cy = c["c"][0] + ox, c["c"][1] + oy
        dia = 2.0 * c["r"]
        shown = dia * lfac            # what the reader sees (detail: dia/k)
        label = "%%c" + fmt_val(shown)
        tw = mw.text_w(label, h)
        placed = None
        for deg in (45, 135, 315, 225):
            ang = math.radians(deg)
            ex = cx + c["r"] * math.cos(ang)
            ey = cy + c["r"] * math.sin(ang)
            # MusaCAD renders the label LEFT-JUSTIFIED at edge + 0.4h
            # outward (dimension.cpp) — model that exact rect
            tx = ex + math.cos(ang) * 0.4 * h
            ty = ey + math.sin(ang) * 0.4 * h
            rect = (tx, ty, tx + tw, ty + 1.4 * h)
            # text must sit fully inside or fully outside the view box,
            # never straddling its outline (glyphs clipped by edges)
            fully_in = (rect[0] >= vrect[0] and rect[1] >= vrect[1]
                        and rect[2] <= vrect[2] and rect[3] <= vrect[3])
            fully_out = (rect[2] < vrect[0] or rect[0] > vrect[2]
                         or rect[3] < vrect[1] or rect[1] > vrect[3])
            if not (fully_in or fully_out):
                continue
            # ...and keep clear of the arc being dimensioned: a horizontal
            # label grazing its own circle reads badly (round-4 QA). Fully
            # inside or fully outside the arc, with clearance.
            clr = ctx.mm(1.0)
            near_x = min(max(cx, rect[0]), rect[2])
            near_y = min(max(cy, rect[1]), rect[3])
            far = max(math.hypot(px - cx, py - cy)
                      for px in (rect[0], rect[2])
                      for py in (rect[1], rect[3]))
            if not (far < c["r"] - clr
                    or math.hypot(near_x - cx, near_y - cy) > c["r"] + clr):
                continue
            r = layout.place([(tx, ty)], tw, 1.4 * h, f"dim-{label}",
                             ignore=("view",))
            if r is not None:
                prec = None if abs(shown - round(shown)) < 0.005 else 2
                # line_pt ON the circle = MusaCAD's edge-label layout (the
                # rect modelled above); a line_pt off the circle is taken as
                # the label position itself (dimension.cpp), so never (0, 0)
                # a diameter measures twice the radius vector, so the
                # vector carries half the snapped value (issue #26)
                e2 = snap_def_points((cx, cy), (ex, ey), None,
                                     prec if prec is not None else 0,
                                     2.0 * lfac)
                recs.append(mw.dim(3, (cx, cy), e2, e2,
                                   style=dim_style_index(
                                       ctx, lfac=lfac) if lfac != 1.0 else 0,
                                   precision=prec))
                rects[key] = r
                placed = r
                break
        if placed is None:
            # edge feature: no angle clears the outline — standard leader
            # callout into the right band instead of a diameter dim
            # angled leaders first — a 0-deg leader rides the feature's
            # own centerline mark — and the knee steps further out when
            # the near column is taken, as a callout's does (issue #18:
            # one column of knees is not enough once a leader may not
            # cross another)
            for kdx, dy in ((kdx, dy)
                            for kdx in (8.0, 16.0, 24.0)
                            for dy in (8.0, -8.0, 14.0, -14.0, 20.0, -20.0,
                                       26.0, -26.0, 0.0)):
                kx = vrect[2] + ctx.mm(kdx)
                ky = cy + ctx.mm(dy)
                # the LEADER's label sits with its BASELINE on the knee
                ink = mw.leader_text_ink((kx, ky), label, h, ctx.mm(2.5))
                ang = math.atan2(ky - cy, kx - cx)
                tip = (cx + c["r"] * math.cos(ang),
                       cy + c["r"] * math.sin(ang))
                if geom and leader_runs_along(ctx, geom, tip,
                                              (kx, ky)) > COLLINEAR_OK:
                    continue             # along an outline (issue #18)
                if layout.leader_crossing(tip, (kx, ky)):
                    continue
                if layout.crossing_ink(tip, (kx, ky)):
                    continue             # a dim line, a sight arrow, the
                                         # pictorial box (issue #25)
                r = layout.place([(ink[0], ink[1])], ink[2] - ink[0],
                                 ink[3] - ink[1], f"dim-{label}")
                if r is not None:
                    recs.append(mw.leader(tip, (kx, ky), label, h,
                                     props=mw.P_ANNO))
                    _claim_leader_ink(ctx, layout, tip, (kx, ky),
                                      f"dim-{label}")
                    rects[key] = r
                    placed = r
                    break
        if placed is not None:
            del layout.unresolved[mark:]
        else:
            del layout.unresolved[mark:]
            layout.unresolved.append(f"dim-{label}")
    return recs, rects


# Knee stand-offs a callout may use, printed mm out from the view edge. The
# LAST one sizes the annotation band (annotation_band_width): every extra
# step widens the band and eats the opposite gutter, where the height dim
# lives, so keep the ladder short.
CALLOUT_KNEE_STEPS = (10.0, 18.0, 26.0)
KNEE_BESIDE_MM = 16.0       # how far past its own view a callout's knee
                            # may step before it is in another view's column


def annotation_band_width(ctx, callouts, gdt, views=None):
    """Width (sheet units) an annotation column needs: the widest callout
    stack / feature-control-frame stack that lands there, plus the leader
    knee stand-off and a clearance. Stacked views share one column, so by
    default every view's callouts are counted; `views` narrows it to one
    side of a side-by-side pair, which gets its OWN column (issue #13).
    Mirrors the sizing inside place_callouts()/place_fcfs() — keep the
    three in step."""
    h_co, h_f = ctx.mm(3.2), ctx.mm(3.0)
    need = 0.0
    if views is not None:
        callouts = [co for co in callouts
                    if co.get("view", "front") in views]
        gdt = {"frames": [f for f in gdt.get("frames", [])
                          if f.get("view", "front") in views]}
    for co in callouts:
        lines = co.get("lines") or [{"text": co["text"]}]
        s, gap = 1.2 * h_co, 0.45 * h_co
        # the same INK metric place_callouts claims with — text_w's
        # over-estimate would inflate the band by a couple of glyphs and
        # steal that width from the opposite gutter
        need = max(need, max((s + gap if ln.get("sym") else 0.0)
                             + mw.glyph_ink_w(ln["text"], h_co)
                             for ln in lines))
    for f in gdt.get("frames", []):
        if f["target"] == "datum_A_face":
            continue
        need = max(need, mw.fcf((0, 0), f["symbol"], f["tol"],
                                f.get("refs", []), h_f)[1])
    # the column has to hold the widest stack AT THE OUTERMOST knee step
    # (place_callouts walks its knee past the view's own dim column and tag
    # tags), plus the knee->text offset and breathing space
    return need + ctx.mm(CALLOUT_KNEE_STEPS[-1] + 1.5 + 3.0) if need else 0.0


def fit_lines(s, h, avail):
    """Break one callout string on its spaces so every piece's INK fits
    `avail` — a note is re-flowed to the column it gets, never truncated and
    never shrunk (ASME Y14.5 hole notes routinely run two or three lines).
    A single word wider than the column is kept whole."""
    if avail <= 0 or mw.glyph_ink_w(s, h) <= avail:
        return [s]
    out, cur = [], ""
    for w in s.split(" "):
        trial = f"{cur} {w}" if cur else w
        if cur and mw.glyph_ink_w(trial, h) > avail:
            out.append(cur)
            cur = w
        else:
            cur = trial
    if cur:
        out.append(cur)
    return out or [s]


def size_callouts(part, view, rad, target=None):
    """The callouts in `view` carrying the size of a feature of radius
    `rad` (TRUE millimetres), narrowed by the target's selector when it has
    one. A detail view draws its geometry k times size, so its circle_r is
    k times the true radius and the comparison follows it."""
    k = 1.0
    if view == "detail":
        spec = part.get("detail_view") or {}
        if spec:
            k = detail_factor(part, spec)
    out = [co for co in part.get("callouts") or []
           if co.get("view", "front") == view and "circle_r" in co
           and abs(float(co["circle_r"]) - rad * k) < 0.0015]
    sel = _target_parts(target)[1] if target else None
    if sel is not None:
        out = [co for co in out
               if _anchor_matches(feature_anchor(view, co["circle_r"],
                                                 (0, 0, 0, 0),
                                                 cid=co.get("id"),
                                                 at=co.get("at")), sel)]
    return out


def gdt_reserve(ctx, part) -> dict:
    """Room each size callout must keep BELOW its text for the GD&T hung on
    it: the frame stack that has to touch it, and the datum box on its
    shoulder, as {(view, circle_r): height}.

    A column of `at` callouts stacks 8 mm apart, which leaves no slot for
    either (issue #7). Reserving it makes the room the drawing needs, so
    this runs on a RETRY pass: a sheet whose GD&T already placed keeps the
    layout it had."""
    g = part.get("gdt") or {}
    h, gap = ctx.mm(3.0), ctx.mm(1.2)
    need: dict = {}
    frames_by: dict = {}
    for f in g.get("frames", []):
        tgt = f.get("target", "")
        rad = _target_radius(tgt)
        if rad is None:
            continue
        view = f.get("view", "front")
        for co in size_callouts(part, view, rad, tgt):
            frames_by.setdefault((view, round(co["circle_r"], 3)),
                                 []).append(f)
    for key, frames in frames_by.items():
        need[key] = need.get(key, 0.0) + (len(frames) * 1.8 * h
                                          + (len(frames) - 1) * gap
                                          + ctx.mm(3.5))
    for d in g.get("datums", []):
        tgt = d.get("target", "")
        rad = _target_radius(tgt)
        if rad is None:
            continue
        view = d.get("view", "front")
        for co in size_callouts(part, view, rad, tgt):
            key = (view, round(co["circle_r"], 3))
            need[key] = need.get(key, 0.0) + 1.7 * ctx.mm(3.2) + ctx.mm(3.5)
    return need


def callout_anchor(co, job, off):
    """(x, y, feature radius) this callout's leader points at, in sheet
    coordinates — an `at` point as given, else the circle of that radius
    in the topmost row and, within it, the one NEAREST the right-hand band
    (the shortest leader crosses the least other annotation). None when
    the view draws no such circle."""
    ox, oy = off
    if "at" in co:
        return (co["at"][0] + ox, co["at"][1] + oy, 0.0)
    matches = [p for p in job["visible"] if p["t"] == "circle"
               and abs(p["r"] - co["circle_r"]) < 0.05]
    if not matches:
        return None
    top = max(p["c"][1] for p in matches)
    row = [p for p in matches if abs(p["c"][1] - top) < 0.05]
    c = max(row, key=lambda p: p["c"][0])
    return (c["c"][0] + ox, c["c"][1] + oy, c["r"])


def fan_order(ctx, callouts, view_jobs, offs, by_view=True):
    """The callouts of a sheet in the order their arrows appear FROM THE
    COLUMN their knees share (issue #18).

    Knees are handed out from each callout's own level outward, so when
    two callouts are placed in an order that disagrees with the order
    their arrows are seen in, the second reaches past the first and the
    two leaders cross. Seen from the middle of the column, the arrows lie
    at different ANGLES, and taking them in that order makes a fan, which
    cannot cross itself. The angle is what matters rather than the height:
    the callouts of a turned part all sit on one bore line, where height
    says nothing and the arrow nearest the column must still be served
    last. (The balloon fan is ordered the same way, for the same reason.)

    The manifest's own order is the tie-break and the fallback for
    anything the views do not place, so this is a REORDERING, never a
    dropping."""
    anchors, keys = {}, {}
    for i, co in enumerate(callouts):
        view = co.get("view", "front")
        job = view_jobs.get(view)
        if job is None:
            continue
        anc = callout_anchor(co, job, offs[view])
        if anc is not None:
            anchors[i] = (view, anc)
    for view in {v for v, _ in anchors.values()}:
        job = view_jobs[view]
        mine = [(i, a) for i, (v, a) in anchors.items() if v == view]
        kx = job["bounds"][1][0] + offs[view][0] \
            + ctx.mm(CALLOUT_KNEE_STEPS[0])
        py = sum(a[1] for _, a in mine) / len(mine)
        for i, a in mine:
            keys[i] = -math.atan2(a[1] - py, -(a[0] - kx) or 1e-9)
    # by VIEW first: each view's callouts share a column of their own, so
    # an angle measured at one of them says nothing about the other
    rank = {}
    for i, co in enumerate(callouts):
        view = co.get("view", "front")
        if view in view_jobs:
            rank.setdefault(view, len(rank))
    unplaced = len(rank) + 1
    order = sorted(range(len(callouts)),
                   key=lambda i: ((rank.get(callouts[i].get("view",
                                                             "front"),
                                            unplaced),)
                                  if by_view else ()) +
                   (i not in keys, keys.get(i, 0.0), i))
    return [callouts[i] for i in order]


def place_callouts(ctx, layout, callouts, view_jobs, offs, reserve=None,
                   limits=None, geom=()):
    """Leader callouts (fit specs, hole callouts) landing in the right-hand
    band. A callout carries either a single "text" or a "lines" stack
    (ASME Y14.5 hole note: optional per-line "sym" prefix glyph, cbore /
    depth); the leader knee meets the FIRST line, the rest stack below,
    left-aligned. The claimed rect covers the whole stack incl. glyphs.
    `limits` caps a view's text column at an x of its own: the INNER
    view of a side-by-side pair wraps to the inter-view gap, not to the
    sheet edge, so its note stays in its own column (issue #13).

    `geom` is the drawn view geometry: a rung whose leader would run ALONG
    an outline, or cross a leader already on the sheet, is passed over for
    the next one (issue #18). The level knee is tried first as it always
    was, so a leader that was already clear keeps what it had; it is the
    ones that rode the silhouette of a turned part — on a roomy sheet the
    level spot is nearly always free — that now step up or down until they
    meet the feature at an angle. When NO rung is clear the least bad one
    is drawn and the fault recorded: a callout the vendor never sees is
    worse than one whose leader crosses another, and the layout check
    reports it on the finished sheet either way.

    Returns (records, {(view, circle_r): full_rect}, suppress_radii,
    {(view, circle_r): (knee, text_left, arrow)}, anchors) — the landing is
    where a datum on that feature of size attaches, carrying the leader's
    own ARROW point so the datum triangle can be set back ALONG the leader
    and clear the text (issue #17), and `anchors` lists EVERY callout as
    its own annotation, so two features of one size stay distinguishable
    (issue #7; the dicts keep only the last of them)."""
    h = ctx.mm(3.2)
    recs, rects, suppress, lands = [], {}, {}, {}
    anchors: list = []
    for co in callouts:
        spec_lines = co.get("lines") or [{"text": co["text"]}]
        lines = spec_lines
        first = lines[0]["text"]
        label = f"callout-{first[:18]}"
        view = co["view"]
        job = view_jobs.get(view)
        if job is None:
            layout.unresolved.append(f"callout-view-{view}")
            continue
        anchor = callout_anchor(co, job, offs[view])
        if anchor is None:
            layout.unresolved.append(f"callout-nocircle-{first[:18]}")
            continue
        ox, oy = offs[view]
        cx, cy, crad = anchor
        mnb, mxb = job["bounds"]
        s = 1.2 * h                      # prefix glyph cell side
        gap = 0.45 * h                   # glyph-to-text gap
        lh = 1.8 * h                     # line pitch (glyph cells breathe)
        arrow = ctx.mm(2.5)              # dimstyle arrow (leader text offset)
        first_sym = lines[0].get("sym")

        def stack_ink(lines_, x1, knee):
            """Ink of everything this callout draws right of the knee: the
            LEADER's own label (baseline AT the knee, 0.4*arrow clear of it)
            or a first-line glyph cell, plus the attach=3 stacked lines.
            Takes its lines, so a rung can be MEASURED without taking it."""
            ky = knee[1]
            sym0 = lines_[0].get("sym")
            boxes = []
            if not sym0:
                boxes.append(mw.leader_text_ink(knee, lines_[0]["text"], h,
                                                arrow))
            for k, ln in enumerate(lines_):
                if k == 0 and not sym0:
                    continue
                cy_l = ky - k * lh
                tx = x1
                if ln.get("sym"):
                    boxes.append((x1, cy_l - s / 2, x1 + s, cy_l + s / 2))
                    tx = x1 + s + gap
                boxes.append(mw.mtext_ink((tx, cy_l), [ln["text"]], h,
                                          attach=3))
            return (min(b[0] for b in boxes), min(b[1] for b in boxes),
                    max(b[2] for b in boxes), max(b[3] for b in boxes))

        state = {}

        def rung(dy, kdx):
            """Everything a rung determines: where the knee goes, where the
            arrow lands, how the note wraps to the column that knee leaves,
            and the ink it would claim."""
            knee_x = mxb[0] + ox + ctx.mm(kdx)
            ky = cy + ctx.mm(dy)         # knee = first-line centre
            x1 = knee_x + ctx.mm(1.5)    # left edge of every line
            # re-flow to the column this knee actually leaves: a band too
            # narrow for the note makes it two or three lines, never a
            # dropped callout (round-8: the 3X %%c27 bore note on sheet 03)
            avail = (limits or {}).get(view, ctx.draw_area[2]) \
                - x1 - ctx.mm(1.0)
            lines_ = []
            for ln in spec_lines:
                room = avail - ((s + gap) if ln.get("sym") else 0.0)
                parts = fit_lines(ln["text"], h, room)
                lines_.append({**ln, "text": parts[0]})
                lines_ += [{"text": p} for p in parts[1:]]
            ang = math.atan2(ky - cy, knee_x - cx)
            tip = (cx + crad * math.cos(ang), cy + crad * math.sin(ang))
            return (knee_x, ky), x1, tip, lines_

        def harm_of(knee, tip, ky):
            """How bad this rung's LEADER is, smallest first: clear, then
            crossing another leader or running along an outline, then
            parked beside another view altogether (issue #18)."""
            beside = (mnb[1] + oy - ctx.mm(KNEE_BESIDE_MM) <= ky
                      <= mxb[1] + oy + ctx.mm(KNEE_BESIDE_MM))
            along = leader_runs_along(ctx, geom, tip, knee) if geom else 0.0
            crossed = layout.leader_crossings(tip, knee)
            # a dimension line, a sight arrow or the pictorial box is ink
            # that belongs to something else: crossing it is as bad as
            # crossing another leader (issue #25). An extension line only
            # ranks the rung — a crowded sheet sometimes has no way round
            over = layout.crossing_ink(tip, knee)
            ext = layout.crosses_extension(tip, knee)
            bad = along > COLLINEAR_OK or crossed or over
            return ((0 if not bad else 1) if beside else 2,
                    len(crossed) + (1 if over else 0),
                    round(along / ctx.mm(1.0), 1), 1 if ext else 0), \
                along, (crossed[0] if crossed else
                        (f"over:{over}" if over else None))

        def fits(ink, over_caption):
            r = (ink[0], ink[1], ink[2], ink[3])
            return layout.inside(r) and not layout.collides(
                r, ignore=("caption",) if over_caption else ())

        def try_rung(dy, kdx, last_resort=False, over_caption=False):
            """Place this callout with its knee at (kdx, dy) from the
            feature, unless its leader would be unreadable there."""
            nonlocal lines, first, first_sym
            knee, x1, tip, lines_ = rung(dy, kdx)
            if not _leader_path_clear(ctx, layout, tip, knee):
                return None              # would run along existing dim ink
            if not last_resort and harm_of(knee, tip, knee[1])[0][0] > 0:
                return None
            lines = lines_
            first = lines[0]["text"]
            first_sym = lines[0].get("sym")
            ink = stack_ink(lines_, x1, knee)
            r = layout.place([(ink[0], ink[1])], ink[2] - ink[0],
                             ink[3] - ink[1], label,
                             ignore=("caption",) if over_caption else ())
            if r is None:
                layout.unresolved.pop()
                return None
            # a first-line glyph would sit under the leader's own text,
            # so render such a line ourselves (empty leader content)
            recs.append(mw.leader(tip, knee, "" if first_sym else first, h,
                                  props=mw.P_ANNO))
            _claim_leader_ink(ctx, layout, tip, knee, label)
            for k, ln in enumerate(lines):
                if k == 0 and not first_sym:
                    continue
                cy_l = knee[1] - k * lh
                tx = x1
                if ln.get("sym"):
                    recs.extend(mw.callout_sym(ln["sym"],
                                               (x1, cy_l - s / 2), s,
                                               props=mw.P_ANNO))
                    tx = x1 + s + gap
                recs.append(mw.mtext((tx, cy_l), ln["text"], h, attach=3,
                                     props=mw.P_ANNO))
            state["knee"] = knee
            state["x1"] = x1
            state["tip"] = tip
            return r

        placed = None
        # step OUTWARD before stepping along: the near column of the band
        # belongs to the view's own location dims (placed first), so a
        # callout parks its knee past them and keeps its short level leader
        rungs = [(dy, kdx)
                 for dy in (0.0, 8.0, -8.0, -16.0, -24.0, -32.0,
                            -40.0, -48.0, -56.0, 16.0, 24.0, 32.0,
                            -64.0, -72.0, -80.0, 40.0, 48.0)
                 for kdx in CALLOUT_KNEE_STEPS]
        for dy, kdx in rungs:
            placed = try_rung(dy, kdx)
            if placed is not None:
                break
        if placed is None:
            # nowhere left: a detail view's caption band is a reservation
            # that yields — a caption has a ladder of its own to fall back
            # on, a leader note has not
            for dy, kdx in rungs:
                placed = try_rung(dy, kdx, over_caption=True)
                if placed is not None:
                    break
        if placed is None:
            # no rung leaves this leader clear. Take the LEAST BAD of the
            # ones that fit and say what is wrong with it: a callout the
            # vendor never sees is worse than one whose leader crosses
            # another, and the layout check reports it on the finished
            # sheet either way (issue #18)
            best = None
            for dy, kdx in rungs:
                knee, x1, tip, lines_ = rung(dy, kdx)
                if not _leader_path_clear(ctx, layout, tip, knee):
                    continue
                if not fits(stack_ink(lines_, x1, knee), True):
                    continue
                score_, along, crosses = harm_of(knee, tip, knee[1])
                if best is None or score_ < best[0]:
                    best = (score_, dy, kdx, along, crosses)
            if best is not None:
                _, dy, kdx, along, crosses = best
                placed = try_rung(dy, kdx, last_resort=True,
                                  over_caption=True)
            if placed is not None:
                why = (crosses[5:] + " — ink that is not its own"
                       if crosses and crosses.startswith("over:")
                       else f"crosses the leader of {crosses[8:]!r}"
                       if crosses
                       else f"runs {along:.0f} mm along an outline"
                       if along > COLLINEAR_OK
                       else "lands beside another view, so its leader "
                            "crosses that view's own column")
                layout.faults.append(
                    f"{label}: its leader {why} — no knee on this sheet "
                    "avoids it. Move the callout's arrow (`at`), put it on "
                    "the other view, or make room beside this one")
        if placed is not None:
            knee_x, ky = state["knee"]
            x1 = state["x1"]
            # a column narrower than the note's own words wraps it to a
            # word a line, and its leader then has to cross the view to
            # reach it. The fix is not another knee: it is a bigger
            # sheet, so the sheet says so (issue #25).
            run = 0
            for ln in lines:
                run = run + 1 if len(ln["text"].split()) == 1 else 0
                if run >= 3:
                    layout.faults.append(
                        f"{label}: the column beside the views is "
                        "narrower than this note's own words, so it comes "
                        "out one word a line ("
                        + " / ".join(x["text"] for x in lines[:4])
                        + "). Shorten the note or draw this part on a "
                          "bigger sheet")
                    break
            if "circle_r" in co:
                key = (view, round(co["circle_r"], 3))
                rects[key] = placed
                lands[key] = ((knee_x, ky), (x1, ky), state["tip"])
                anchors.append(feature_anchor(
                    view, co["circle_r"], placed, lands[key],
                    cid=co.get("id"),
                    at=co.get("at") or (cx - ox, cy - oy)))
                extra = (reserve or {}).get(key)
                if extra:
                    # keep the slot under this callout free for the datum or
                    # the frame hung on it: kind "slot", which only those two
                    # may place into (issue #7)
                    layout.claim((knee_x - ctx.mm(8.0), placed[1] - extra,
                                  placed[2], placed[1]),
                                 f"slot-{key[0]}-{key[1]:g}", "slot")
            if "suppress_radii" in co:
                suppress.setdefault(view, []).extend(co["suppress_radii"])
            elif co.get("suppress_dia_dim") and "circle_r" in co:
                suppress.setdefault(view, []).append(co["circle_r"])
        else:
            layout.unresolved.append(label)
    return recs, rects, suppress, lands, anchors


_TARGET_R = {"holes": None, "bores": None, "bosses": None}


# A plane face is drawn EDGE-ON: in the view that shows it, it is a line,
# not an area. A frame on such a face (perpendicularity, flatness,
# parallelism) therefore attaches to that line or to an extension of it,
# with a leader — ISO 1101 5.2. The manifest names the line two ways:
#   face_x12.5 / face_y-30  a face at that coordinate in the VIEW's own
#                           axes (x: a vertical line, y: a horizontal one)
#   bottom_face / top_face / left_edge / right_edge
#                           the view's own bounding edges, the same names
#                           datums use for a surface
FACE_EDGES = {"bottom_face": ("y", "min"), "top_face": ("y", "max"),
              "left_edge": ("x", "min"), "right_edge": ("x", "max"),
              "short_edge": ("y", "min"), "long_edge": ("x", "max")}


def face_target(target):
    """(axis, coordinate or "min"/"max") for a face target, else None."""
    t = str(target or "")
    if t in FACE_EDGES:
        return FACE_EDGES[t]
    for axis in ("x", "y"):
        if t.startswith("face_" + axis):
            try:
                return (axis, float(t[len("face_" + axis):]))
            except ValueError:
                return None
    return None


def face_lines(job, off, target, tol=0.05):
    """EVERY drawn line a face target names, in sheet coordinates, longest
    first.

    One face can show as several segments: a shoulder on a turned part is
    one short line each side of the axis, and both ARE that face. The
    frame goes beside the nearest of them — a leader to the far one runs
    right across the view and through its section hatch (issue #7) — so
    the caller is given all of them and picks.
    """
    spec = face_target(target)
    if spec is None:
        return []
    axis, coord = spec
    ox, oy = off
    mn, mx = job["bounds"]
    if isinstance(coord, str):
        value = (mn if coord == "min" else mx)[0 if axis == "x" else 1]
        if axis == "x":
            return [((value + ox, mn[1] + oy), (value + ox, mx[1] + oy))]
        return [((mn[0] + ox, value + oy), (mx[0] + ox, value + oy))]
    found = []
    for (x0, y0), (x1, y1) in prim_segments(job.get("visible", [])):
        if axis == "x" and abs(x0 - coord) <= tol and abs(x1 - coord) <= tol:
            length = abs(y1 - y0)
        elif axis == "y" and abs(y0 - coord) <= tol \
                and abs(y1 - coord) <= tol:
            length = abs(x1 - x0)
        else:
            continue
        found.append((length, ((x0 + ox, y0 + oy), (x1 + ox, y1 + oy))))
    found.sort(key=lambda f: -f[0])
    return [f[1] for f in found]


def face_line(job, off, target, tol=0.05):
    """The longest line a face target names — a face reads as its longest
    line, and a short coincident edge (a chamfer, a hidden step) is not
    what the frame is about. None when the view draws no edge there."""
    lines = face_lines(job, off, target, tol)
    return lines[0] if lines else None


def hatch_polys(job, off):
    """A view's hatched cut areas (outer loops) in sheet coordinates: the
    SOLID, which a leader must not be routed through — a leader crossing a
    hatched cut reads as part of the hatch (ISO 128-50)."""
    return [[(x + off[0], y + off[1]) for x, y in g["outer"]]
            for g in job.get("hatch_groups", [])]


def _point_in_poly(poly, p) -> bool:
    inside = False
    n = len(poly)
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        if (y0 > p[1]) != (y1 > p[1]):
            xx = x0 + (p[1] - y0) * (x1 - x0) / (y1 - y0)
            if p[0] < xx:
                inside = not inside
    return inside


def crosses_polys(ctx, polys, a, b) -> bool:
    """True if segment a-b passes through any of `polys`. Sampled, and the
    ends are skipped: a leader's tip SITS on the hatch boundary."""
    if not polys:
        return False
    length = math.hypot(b[0] - a[0], b[1] - a[1])
    steps = max(4, round(length / ctx.mm(1.5)))
    for i in range(1, steps):
        f = i / steps
        p = (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)
        if any(_point_in_poly(poly, p) for poly in polys):
            return True
    return False


def leader_point_on_line(ctx, layout, line, land, samples=9, avoid=()):
    """Where on a face's edge-on line a frame's leader should land.

    Walks the line from the end nearest the frame inwards and takes the
    first point whose leader runs clear; the ends are tried first because a
    leader meeting the face near its middle crosses whatever sits beside
    it. A frame's leader may CROSS ink — that is ordinary drafting — so the
    second pass tolerates more of it; crossing TEXT is refused in both,
    which is what _leader_path_clear never relaxes. `avoid` holds areas no
    route may pass through at all (the section's own hatch, issue #7).
    """
    (ax, ay), (bx, by) = line
    near_first = math.dist((ax, ay), land) <= math.dist((bx, by), land)
    for max_frac in (0.35, 0.7):
        for i in range(samples):
            f = (i + 1) / (samples + 1)
            t = f if near_first else 1.0 - f
            pt = (ax + (bx - ax) * t, ay + (by - ay) * t)
            if _leader_path_clear(ctx, layout, pt, land, max_frac=max_frac) \
                    and not crosses_polys(ctx, avoid, pt, land):
                return pt
    return None


def _step_towards(ctx, frm, to, mm=1.5):
    """A point `mm` along frm->to: where a leader's clearance check starts
    when its tip sits on the very ink it points at."""
    dx, dy = to[0] - frm[0], to[1] - frm[1]
    ln = math.hypot(dx, dy) or 1.0
    f = min(1.0, ctx.mm(mm) / ln)
    return (frm[0] + dx * f, frm[1] + dy * f)


def _frame_edge_towards(rect, pt):
    """The midpoint of the frame edge that faces `pt` — where its leader
    leaves the frame."""
    cx, cy = (rect[0] + rect[2]) / 2.0, (rect[1] + rect[3]) / 2.0
    if abs(pt[0] - cx) >= abs(pt[1] - cy):
        return (rect[0] if pt[0] < cx else rect[2], cy)
    return (cx, rect[1] if pt[1] < cy else rect[3])


def place_frame_near_face(ctx, layout, flines, vrect, w, h, label, avoid=()):
    """Put a frame beside the face it controls, leader first.

    A frame on an edge-on face belongs NEXT to that face, square to it: the
    leader is then short and perpendicular, as ISO 1101 draws it. Parking
    the stack in the sheet's annotation band instead means a leader right
    across the view and through whatever callouts stand between — which is
    how this used to fail.

    The frame steps outward from the VIEW, not from the short line itself:
    a shoulder face is a 6 mm line in the middle of the part, and "just
    below" it is inside the view.

    `flines` holds every segment that face is drawn as (one each side of
    the axis, on a turned part): each is tried and the SHORTEST clear
    route wins, so the leader goes to the near edge and not across the
    view (issue #7). Routes through `avoid` — the section hatch — are
    taken only when there is no clear one at all.
    Returns (rect, tip) or (None, None).
    """
    if flines and isinstance(flines[0][0], (int, float)):
        flines = [flines]
    best = None
    for fline in flines:
        (ax, ay), (bx, by) = fline
        mid = ((ax + bx) / 2.0, (ay + by) / 2.0)
        vertical = abs(bx - ax) <= abs(by - ay)
        cands = []
        for d in (6.0, 10.0, 16.0, 24.0, 32.0):
            gap = ctx.mm(d)
            for step in (0.0, 6.0, -6.0, 12.0, -12.0, 20.0, -20.0):
                off = ctx.mm(step)
                if vertical:                  # the face is a vertical line
                    x = ax - w / 2.0 + off
                    cands += [(x, vrect[3] + gap), (x, vrect[1] - gap - h)]
                else:                         # a horizontal one
                    y = ay - h / 2.0 + off
                    cands += [(vrect[2] + gap, y), (vrect[0] - gap - w, y)]
        for x, y in cands:
            rect = (x, y, x + w, y + h)
            if not layout.inside(rect) or layout.collides(rect):
                continue
            # the leader leaves the frame by the edge FACING the face —
            # the same landing place_fcfs draws it to
            land = _frame_edge_towards(rect, mid)
            tip, through = None, 0
            for keep_out in (avoid, ()):
                tip = leader_point_on_line(ctx, layout, fline, land,
                                           avoid=keep_out)
                if tip is not None:
                    break
                through = 1
            if tip is None:
                continue
            score = (through, math.dist(tip, land))
            if best is None or score < best[0]:
                best = (score, rect, tip)
            break        # this line's nearest free spot is its best one
    if best is None:
        return None, None
    layout.claim(best[1], label)
    return best[1], best[2]


def _target_key(view, rad, lfacs=None):
    """(view, radius) key for a radius target, which is authored in TRUE
    millimetres whatever view it lands in. A detail view's geometry is k
    times size (k = 1/DIMLFAC), so inside it the same feature has radius
    rad * k — the key callouts and diameter dims filed it under."""
    lf = (lfacs or {}).get(view, 1.0)
    return (view, round(rad / lf, 3), lf)


def _target_parts(target: str):
    """(radius, selector) of a feature-of-size target.

    Two features of the SAME size in one view cannot be told apart by size
    alone — a tube with a bore at each end has two r35.75 callouts, and
    whichever was authored last used to win silently (issue #7). The
    target therefore carries a selector that names WHICH one:

      bore_r35.75         the only feature of that size in the view
      bore_r35.75@tail    the callout whose manifest "id" is "tail"
      bore_r35.75@y500    the feature at that coordinate in the VIEW's own
                          axes, written exactly as a callout's "at" is
    """
    t = str(target or "")
    sel = None
    if "@" in t:
        t, _, sel = t.partition("@")
        sel = sel.strip() or None
    if "_r" not in t:
        return None, sel
    try:
        return float(t.rsplit("_r", 1)[1]), sel
    except ValueError:
        return None, sel


def _target_radius(target: str):
    return _target_parts(target)[0]


def feature_anchor(view, rad, rect, land=None, cid=None, at=None,
                   source="callout"):
    """One size annotation a GD&T target may attach to: `rad` is the radius
    as the view DRAWS it (the manifest's own circle_r), `rect` its ink,
    `land` its leader landing (knee, text left) when it has one, `cid` the
    manifest "id" that names it and `at` the feature's position in the
    view's own axes."""
    return {"view": view, "r": round(rad, 3), "rect": rect, "land": land,
            "id": cid, "at": tuple(at) if at is not None else None,
            "source": source}


def legacy_anchors(callout_rects=None, dim_rects=None, callout_lands=None):
    """Anchor records from the old (view, radius) dicts — what a direct
    caller still passes when it has no anchor list of its own."""
    out = []
    for key, rect in (callout_rects or {}).items():
        out.append(feature_anchor(key[0], key[1], rect,
                                  (callout_lands or {}).get(key)))
    for key, rect in (dim_rects or {}).items():
        out.append(feature_anchor(key[0], key[1], rect, source="dim"))
    return out


def _anchor_name(a) -> str:
    if a.get("id"):
        return f"@{a['id']}"
    if a.get("at"):
        return f"at {a['at'][0]:g},{a['at'][1]:g}"
    return a["source"]


def _anchor_matches(a, sel: str) -> bool:
    if a.get("id") is not None and str(a["id"]) == sel:
        return True
    axis = sel[:1].lower()
    if axis in ("x", "y") and a.get("at") is not None:
        try:
            want = float(sel[1:])
        except ValueError:
            return False
        return abs(a["at"][0 if axis == "x" else 1] - want) <= 0.5
    return False


def resolve_feature_anchor(anchors, view, target, lfacs=None):
    """The ONE size annotation a feature-of-size target names.

    Returns (anchor, problem): the anchor, or a message when the target
    names nothing in that view or more than one thing. Ambiguity FAILS the
    sheet — a datum or a frame on the wrong one of two same-size features
    is a drawing that says something the designer did not (issue #7).
    """
    rad, sel = _target_parts(target)
    if rad is None:
        return None, None
    lf = (lfacs or {}).get(view, 1.0)
    want = round(rad / lf, 3)
    cands = [a for a in anchors
             if a["view"] == view and abs(a["r"] - want) < 0.0015]
    if sel is not None:
        named = [a for a in cands if _anchor_matches(a, sel)]
        if not named:
            how = ", ".join(_anchor_name(a) for a in cands) or "nothing"
            return None, (f"{target}: no size annotation in view {view!r} "
                          f"matches @{sel} ({how} there)")
        cands = named
    # a callout is the feature's own size note; a diameter dim is the
    # fallback, exactly as it was before there were selectors
    cands = [a for a in cands if a["source"] == "callout"] or cands
    if len(cands) > 1:
        how = ", ".join(_anchor_name(a) for a in cands)
        return None, (f"{target}: view {view!r} carries {len(cands)} "
                      f"annotations of that size ({how}) — say which with "
                      f"@<callout id> or @x<coord>/@y<coord>")
    return (cands[0] if cands else None), None


def _feature_circle(job, rad, off):
    """The circle of radius `rad` a size annotation was hung on: topmost
    row, rightmost within it — place_callouts' and dim_circles' own choice,
    so a datum lands on the SAME feature its size dimension does."""
    ox, oy = off
    matches = [pr for pr in job.get("visible", [])
               if pr["t"] == "circle" and abs(pr["r"] - rad) < 0.05]
    if not matches:
        return None
    top = max(pr["c"][1] for pr in matches)
    row = [pr for pr in matches if abs(pr["c"][1] - top) < 0.05]
    c = max(row, key=lambda pr: pr["c"][0])
    return (c["c"][0] + ox, c["c"][1] + oy, c["r"])


def _cell_ink(box_rect, letter, h):
    """The ink of the letter mw.datum_label centres in its box."""
    w = mw.glyph_ink_w(letter, h)
    cx = (box_rect[0] + box_rect[2]) / 2.0
    cy = (box_rect[1] + box_rect[3]) / 2.0
    return (cx - w / 2.0, cy - h / 2.0, cx + w / 2.0, cy + h / 2.0)


def datum_triangle_rect(box_rect, tip, h):
    """Ink rect of the filled triangle mw.datum_label draws at `tip`, for a
    stem coming from `box_rect`: base 0.9 x h back along the stem, half
    that across it (musa_writer.datum_label's own proportions)."""
    b = 0.9 * h
    cx = (box_rect[0] + box_rect[2]) / 2.0
    cy = box_rect[3] if tip[1] > box_rect[3] else box_rect[1]
    dx, dy = tip[0] - cx, tip[1] - cy
    ln = math.hypot(dx, dy) or 1.0
    ux, uy = dx / ln, dy / ln
    px, py = -uy, ux
    base = (tip[0] - ux * b, tip[1] - uy * b)
    pts = [tip, (base[0] + px * b * 0.5, base[1] + py * b * 0.5),
           (base[0] - px * b * 0.5, base[1] - py * b * 0.5)]
    return (min(q[0] for q in pts), min(q[1] for q in pts),
            max(q[0] for q in pts), max(q[1] for q in pts))


def _stem_clear(ctx, layout, box_rect, tip) -> bool:
    """Does the datum box's stem reach `tip` without crossing text? The box
    is not claimed yet, so the sampling stops short of it, as every other
    stem check here does."""
    stem = ((box_rect[0] + box_rect[2]) / 2.0,
            box_rect[3] if tip[1] > box_rect[3] else box_rect[1])
    ln = math.hypot(stem[0] - tip[0], stem[1] - tip[1]) or 1.0
    f = max(0.0, 1.0 - ctx.mm(1.5) / ln)
    near = (tip[0] + (stem[0] - tip[0]) * f, tip[1] + (stem[1] - tip[1]) * f)
    return _leader_path_clear(ctx, layout, tip, near, max_frac=1.0)


def _leader_unit(knee, arrow):
    """Unit vector from the knee back along the leader, toward its arrow
    (an older landing carries no arrow: a level leader, which is the one
    place the fault was seen), and how far back the triangle may go."""
    if arrow is not None:
        dx, dy = arrow[0] - knee[0], arrow[1] - knee[1]
        ln = math.hypot(dx, dy)
        if ln > 1e-9:
            return (dx / ln, dy / ln), 0.4 * ln
    return (-1.0, 0.0), None


TRI_SETBACKS = (0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.5, 8.0)


def datum_on_landing(ctx, layout, letter, landing, stack=None, why=None):
    """Datum symbol on a leader-directed size callout.

    When the feature of size is called out by a leader (`%%c25 H7 THRU`),
    ISO 1101 and ASME Y14.5 put the datum triangle on that leader — at its
    shoulder, where the text starts — not on a line of its own. The box
    then hangs directly off the shoulder: a 1.2 mm stem, nothing dragged
    across the sheet.

    `stack` is the callout's whole ink rect, which does two jobs. It opens
    two further placements for a CROWDED column (issue #7: several `at`
    callouts in one view stack 8 mm apart, and the next one's text sits
    exactly where the box would go, leaving the datum unresolved whatever
    order they are in): off the far end of the landing — ISO 1101 5.3
    allows the triangle on an extension of the dimension line — and under
    the whole stack, where the stem runs down the knee column and crosses
    no text at all.

    And it is what the TRIANGLE is kept clear of (issue #17). The tip used
    to sit exactly on the knee, where the leader's own label starts 0.4 x
    arrow further on, so the filled triangle covered the lower left of the
    first glyph — the diameter sign of a size callout, which is the one
    glyph a vendor cannot afford to read wrong. The triangle now walks
    BACK along the leader, which is where ISO 1101 puts it anyway, until
    its own ink stands clear of that text; the box keeps the spot it
    always had, so a datum that already read correctly is untouched.
    Returns (records, box_rect) or ([], None)."""
    (kx, ky), _, *rest = landing
    arrow = rest[0] if rest else None
    h = ctx.mm(3.2)
    sq = 1.7 * h
    gap = ctx.mm(1.2)
    (ux, uy), cap = _leader_unit((kx, ky), arrow)
    if cap is None:
        cap = ctx.mm(6.0)
    # a WHITE gap, not a touch: the triangle is a filled shape and the
    # glyph a 0.3 mm stroke, so the two have to stand visibly apart
    clr = ctx.mm(0.6)
    want = None if stack is None else (stack[0] - clr, stack[1] - clr,
                                       stack[2] + clr, stack[3] + clr)

    def tip_for(rect, tip0, dirv, lim):
        """Where this box's triangle goes: `tip0` when its ink already
        stands clear of the callout's text, else the SHORTEST step from
        there along `dirv` — back along the leader, or further out along
        the dimension line's extension — that does clear it. None when
        there is no room for it on that line at all."""
        if want is None:
            return tip0
        for d in TRI_SETBACKS:
            dd = ctx.mm(d)
            if d > 0.0 and dd > lim:
                break
            t = (tip0[0] + dirv[0] * dd, tip0[1] + dirv[1] * dd)
            tri = datum_triangle_rect(rect, t, h)
            if Layout._overlap(tri, want, 0.0):
                continue
            if Layout._overlap(tri, _cell_ink(rect, letter, h), clr):
                # the triangle reaches back into its own box, over the
                # LETTER in it: the stem is shorter than the triangle is
                # long, which is no datum at all (issue #17)
                continue
            if d > 0.0 and not _stem_clear(ctx, layout, rect, t):
                # stepping the triangle round the text only to drag the
                # STEM across it is not progress: this box cannot carry a
                # set-back triangle, so another one must
                continue
            return t
        return None

    cands = []
    for dx in (0.0, 4.0, 8.0, -4.0, 12.0):
        for below in (True, False):
            x = kx + ctx.mm(dx) - sq / 2.0
            y = ky - gap - sq if below else ky + gap
            cands.append((x, y, (kx, ky), (ux, uy), cap))
    # a datum box 1 mm from a foreign label reads as that label's, so the
    # first legal spot is not good enough: take the first one that stands
    # CLEAR of other text, else the clearest one there is
    clear_enough = ctx.mm(4.0)

    why = [] if why is None else why

    def mine(rect) -> bool:
        """Is this spot beside the callout the datum BELONGS to?

        A box reads as belonging to the nearest label, so one that ends
        up nearer the next callout's text than its own says that the
        wrong feature is the datum — "SR25.4 (TIP) [A]" when A is the
        spigot's (issue #27). Its own text has to be the nearest one."""
        if stack is None:
            return True
        return rect_gap(rect, stack) <= \
            layout.anno_clearance(rect, ignore=stack) + 1e-9

    def legal_spots(spots):
        out = []
        for bx, by, tip0, dirv, lim in spots:
            rect = (bx, by, bx + sq, by + sq)
            if not layout.inside(rect) \
                    or layout.collides(rect, ignore=("slot",)):
                continue
            if not mine(rect):
                # it would read as another callout's datum
                taken.append("another callout's text is nearer that spot "
                             "than its own")
                continue
            tip = tip_for(rect, tip0, dirv, lim)
            if tip is None:
                continue            # its triangle cannot clear the text
            clr_ = layout.anno_clearance(rect, ignore=stack)
            if clr_ >= clear_enough:
                return [(clr_, rect, tip)]
            out.append((clr_, rect, tip))
        return out

    taken: list = []
    legal = legal_spots(cands)
    if not legal and stack is not None:
        more = []
        # in line past the text: ISO 1101 5.3's extension of the dimension
        # line, so the triangle sits at the far END of the landing and
        # steps further out along it, never back toward the text
        for dx in (1.5, 4.0, 8.0):
            more.append((stack[2] + ctx.mm(dx), ky - sq / 2.0,
                         (stack[2], ky), (1.0, 0.0), ctx.mm(8.0)))
        for dy in (2.0, 6.0):                    # below the whole stack
            more.append((kx - sq / 2.0, stack[1] - ctx.mm(dy) - sq,
                         (kx, ky), (ux, uy), cap))
        for dy in (1.5, 5.0):                    # above it
            more.append((kx - sq / 2.0, stack[3] + ctx.mm(dy),
                         (kx, ky), (ux, uy), cap))
        legal = legal_spots(more)
    if not legal:
        if taken:
            why.append(
                "every spot beside its own callout is taken — "
                + taken[0] + ". Shorten or re-order the callouts in that "
                "column, or give the sheet more room")
        return [], None
    clr_, rect, tip = max(legal, key=lambda g: g[0])
    layout.claim(rect, f"datum-{letter}")
    if math.dist(((rect[0] + rect[2]) / 2.0, (rect[1] + rect[3]) / 2.0),
                 tip) > ctx.mm(2.5):
        # a box hung off the end of the landing or below the stack draws a
        # stem back to it: that stem is ink like any other
        _claim_leader_ink(ctx, layout, tip,
                          ((rect[0] + rect[2]) / 2.0,
                           (rect[1] + rect[3]) / 2.0), f"datum-{letter}")
    # the triangle is ink too (issue #17): claimed, so no later annotation
    # can land on it either
    layout.claim(datum_triangle_rect(rect, tip, h), f"datum-{letter}-tri")
    recs, _ = mw.datum_label((rect[0], rect[1]), letter, tip, h,
                             props=mw.P_ANNO)
    return recs, rect


def datum_on_feature(ctx, layout, letter, circle, size_rect):
    """Datum on a FEATURE OF SIZE — a bore or a spigot, whose datum is its
    AXIS, not a face (ISO 1101 / ASME Y14.5).

    The triangle therefore goes on that feature's own size annotation: in
    line with its diameter dimension line, or on its callout's leader. Both
    run radially from the feature to their text, so the rule is one rule —
    the triangle sits on the feature where that annotation leaves it, and
    the box stands further out along the same line. A triangle parked on a
    view edge instead would call out a surface, which is a different datum.

    `circle` is (cx, cy, r) of the feature in sheet coordinates and
    `size_rect` the ink rect of its annotation (a callout stack or a %%c dim
    label). Returns (records, box_rect) or ([], None)."""
    cx, cy, r = circle
    h = ctx.mm(3.2)
    sq = 1.7 * h
    mx = (size_rect[0] + size_rect[2]) / 2.0
    my = (size_rect[1] + size_rect[3]) / 2.0
    ang = math.atan2(my - cy, mx - cx)
    ux, uy = math.cos(ang), math.sin(ang)
    tip = (cx + r * ux, cy + r * uy)
    # how far along the ray the annotation itself reaches
    reach = max(math.hypot(px - cx, py - cy)
                for px in (size_rect[0], size_rect[2])
                for py in (size_rect[1], size_rect[3]))
    cands = []
    for d in (2.0, 5.0, 8.0, 12.0, 16.0, 20.0):
        px = cx + (reach + ctx.mm(d)) * ux
        py = cy + (reach + ctx.mm(d)) * uy
        cands.append((px - sq / 2, py - sq / 2))
    # then flanking the annotation, square to the ray (a crowded band)
    for d in (3.0, 7.0, 11.0):
        for sgn in (-1, 1):
            px = mx - uy * sgn * (ctx.mm(d) + sq)
            py = my + ux * sgn * (ctx.mm(d) + sq)
            cands.append((px - sq / 2, py - sq / 2))
    for bx, by in cands:
        rect = (bx, by, bx + sq, by + sq)
        if not layout.inside(rect) or layout.collides(rect):
            continue
        # the stem is ink: sample it short of the box, as the edge datums do
        stem = ((rect[0] + rect[2]) / 2.0, (rect[1] + rect[3]) / 2.0)
        ln = math.hypot(stem[0] - tip[0], stem[1] - tip[1]) or 1.0
        f = max(0.0, 1.0 - ctx.mm(1.5) / ln)
        near = (tip[0] + (stem[0] - tip[0]) * f,
                tip[1] + (stem[1] - tip[1]) * f)
        if not _leader_path_clear(ctx, layout, tip, near, max_frac=1.0):
            continue
        layout.claim(rect, f"datum-{letter}")
        _claim_leader_ink(ctx, layout, tip, stem, f"datum-{letter}")
        recs, _ = mw.datum_label((rect[0], rect[1]), letter, tip, h,
                                 props=mw.P_ANNO)
        return recs, rect
    return [], None


def place_datums(ctx, layout, gdt, view_jobs, offs, callout_rects=None,
                 dim_rects=None, lfacs=None, callout_lands=None,
                 anchors=None):
    """Datum boxes (A/B/C). A target naming a view edge (bottom_face,
    short_edge, long_edge) anchors to that edge; one naming a radius
    (`bore_r12.5`, `boss_r20`) is a feature of size and anchors to that
    feature's size annotation instead — see datum_on_feature. A radius
    shared by two features in one view needs a selector naming which
    (`bore_r35.75@tail`), else the target is ambiguous and fails.
    Returns (recs, pos)."""
    if anchors is None:
        anchors = legacy_anchors(callout_rects, dim_rects, callout_lands)
    h = ctx.mm(3.2)
    recs, pos = [], {}
    for d in gdt.get("datums", []):
        view = d["view"]
        job = view_jobs.get(view)
        if job is None:
            layout.unresolved.append(f"datum-{d['label']}-noview")
            continue
        ox, oy = offs[view]
        mnb, mxb = job["bounds"]
        x0, y0 = mnb[0] + ox, mnb[1] + oy
        x1, y1 = mxb[0] + ox, mxb[1] + oy
        s = 1.7 * h
        tgt = d["target"]
        rad = _target_radius(tgt)
        if rad is not None:
            # a feature of size: the datum is the bore's or spigot's axis
            lf = (lfacs or {}).get(view, 1.0)
            anc, problem = resolve_feature_anchor(anchors, view, tgt, lfacs)
            if problem is not None:
                layout.unresolved.append(f"datum-{d['label']}: {problem}")
                continue
            land = anc["land"] if anc is not None else None
            size_rect = anc["rect"] if anc is not None else None
            circle = _feature_circle(job, rad / lf, (ox, oy))
            if land is not None:
                # Called out by a leader: the triangle goes on that leader,
                # and NO circle is needed (issue #6). A turned part drawn as
                # a side view and a section shows its spigot as two lines,
                # never as a circle, but ISO 1101 / ISO 5459 ask only that
                # the datum sit on the feature's SIZE dimension — which the
                # callout is.
                why: list = []
                frecs, frect = datum_on_landing(ctx, layout, d["label"],
                                                land, stack=size_rect,
                                                why=why)
            elif size_rect is not None and circle is not None:
                # dimensioned by a diameter dim: in line with that dim
                frecs, frect = datum_on_feature(ctx, layout, d["label"],
                                                circle, size_rect)
            else:
                # Nothing on the sheet carries the size of this feature, so
                # there is nowhere a datum triangle would MEAN anything.
                # Say what is missing and what would fix it.
                layout.unresolved.append(
                    f"datum-{d['label']}: no size dimension for r{rad:g} in "
                    f"view {view!r} — add a %%c callout for it there, or a "
                    f"view showing the feature as a circle")
                continue
            if frect is None:
                layout.unresolved.append(
                    f"datum-{d['label']}"
                    + (f": {why[0]}" if why else ""))
                continue
            recs += frecs
            pos[d["label"]] = frect
            continue
        if tgt in ("bottom_face", "short_edge"):
            # outside the view corners first (clear of the centered SECTION
            # caption), then under the edge, then walking sideways along the
            # band — the caption plus a notes block can leave only a gap at
            # one end of it
            cands = []
            for dd in (8.0, 12.0, 16.0, 22.0):
                yy = y0 - ctx.mm(dd) - s
                cands += [(x0 - s - ctx.mm(2.0), yy), (x1 + ctx.mm(2.0), yy)]
                cands += [((x0 + (x1 - x0) * f) - s / 2, yy)
                          for f in (0.12, 0.88, 0.3, 0.7, 0.5)]
                cands += [(x1 + ctx.mm(2.0 + 3.0 * k), yy) for k in range(1, 8)]
                cands += [(x0 - s - ctx.mm(2.0 + 3.0 * k), yy)
                          for k in range(1, 8)]
            tip_of = lambda r: (min(max((r[0] + r[2]) / 2, x0), x1), y0)
        else:  # long_edge
            # the near column belongs to that side's location dims; a datum
            # box just walks further out (its connector line grows with it)
            cands = [(x1 + ctx.mm(dx), y0 + (y1 - y0) * f - s / 2)
                     for dx in (6.0, 3.0, 12.0, 16.0, 20.0, 24.0)
                     for f in (0.75, 0.6, 0.9, 0.45, 0.3, 0.15)]
            tip_of = lambda r: (x1, (r[1] + r[3]) / 2)
        # a datum box also draws a STEM to the feature it identifies; that
        # stem is ink like any other and must not be routed across a caption
        # (round-8 QA: datum A's stem crossed "SECTION A-A" on sheet 01), so
        # each candidate is accepted only if its stem runs clear
        label = f"datum-{d['label']}"
        placed = None
        for cx_c, cy_c in cands:
            r = (cx_c, cy_c, cx_c + s, cy_c + s)
            if not layout.inside(r) or layout.collides(r):
                continue
            tip = tip_of(r)
            stem = ((r[0] + r[2]) / 2, r[3] if tip[1] > r[3] else r[1])
            # sample the stem short of its own box (the box is not yet
            # claimed, but its corner would still read as a hit later)
            ln = math.hypot(stem[0] - tip[0], stem[1] - tip[1]) or 1.0
            f = max(0.0, 1.0 - ctx.mm(1.5) / ln)
            near = (tip[0] + (stem[0] - tip[0]) * f,
                    tip[1] + (stem[1] - tip[1]) * f)
            if not _leader_path_clear(ctx, layout, tip, near, max_frac=1.0):
                continue
            layout.claim(r, label)
            _claim_leader_ink(ctx, layout, tip, stem, label)
            placed = r
            break
        if placed is None:
            layout.unresolved.append(label)
            continue
        dl, _ = mw.datum_label((placed[0], placed[1]), d["label"],
                               tip_of(placed), h, props=mw.P_ANNO)
        recs += dl
        pos[d["label"]] = placed
    return recs, pos


FRAME_ATTACH_MM = 16.0      # printed mm: a frame further from its own size
                            # annotation than this is a separate note


def _frame_attached(ctx, layout, rect, anchor) -> bool:
    """Does a frame stack at `rect` READ as the annotation at `anchor`'s?

    Only if it sits directly under or over it, overlapping it across,
    close enough to pair with by eye, and with no OTHER annotation in the
    gap between them. A frame parked under a NEIGHBOUR's callout belongs
    to that callout as far as the reader is concerned, which is how a
    runout frame ended up on the wrong feature (issue #7)."""
    if rect is None or anchor is None:
        return False
    if min(rect[2], anchor[2]) - max(rect[0], anchor[0]) <= 0.0:
        return False
    gap = max(anchor[1] - rect[3], rect[1] - anchor[3])
    if gap > ctx.mm(FRAME_ATTACH_MM):
        return False
    if gap <= 0.0:
        return True
    lo, hi = ((rect[3], anchor[1]) if anchor[1] >= rect[3]
              else (anchor[3], rect[1]))
    band = (max(rect[0], anchor[0]), lo, min(rect[2], anchor[2]), hi)
    for c in layout.claims:
        if c[5] != "anno" or c[:4] == tuple(rect) or c[:4] == tuple(anchor):
            continue
        if Layout._overlap(band, c[:4], 0.0):
            return False
    return True


def place_fcfs(ctx, layout, gdt, view_jobs, offs, callout_rects, datum_pos,
               dim_rects=None, lfacs=None, anchors=None):
    """Feature control frames: stacked under the matching callout or the
    feature's own diameter-dim text when either exists, else leadered to
    the feature into the right band; flatness frames sit beside their
    datum box.

    A frame is only ever emitted ATTACHED to its own feature's size
    annotation or with a leader to the feature — never floating (issue
    #7); and a target that names two features of one size fails rather
    than taking whichever came last."""
    dim_rects = dim_rects or {}
    if anchors is None:
        anchors = legacy_anchors(callout_rects, dim_rects)
    h = ctx.mm(3.0)
    gap = ctx.mm(1.2)
    recs = []

    def emit_stack(frames, rect):
        """Emit frames top-down inside `rect`: common left edge, uniform
        cell heights and gaps (round-2 alignment requirement)."""
        y = rect[3]
        for f in frames:
            frecs, fw, fh = mw.fcf((rect[0], y - 1.8 * h), f["symbol"],
                                   f["tol"], f.get("refs", []), h,
                                   props=mw.P_ANNO)
            recs.extend(frecs)
            y -= 1.8 * h + gap

    # flatness (datum-face) frames sit beside their datum box, alone
    groups: dict = {}
    for f in gdt.get("frames", []):
        view = f.get("view", "front")
        if view not in view_jobs:
            layout.unresolved.append(f"fcf-{f['symbol']}-noview")
            continue
        if f["target"] == "datum_A_face":
            _, fw, fh = mw.fcf((0, 0), f["symbol"], f["tol"],
                               f.get("refs", []), h)
            if "A" not in datum_pos:
                layout.unresolved.append(f"fcf-{f['symbol']}-nodatum")
                continue
            a = datum_pos["A"]
            # beside the box first, then rings under/over it walking
            # sideways — leader-ink corridors (claimed since round 3) and
            # dim ink can rule every near spot out on a crowded sheet
            step = ctx.mm(6.0)
            rows = (a[1] - fh - ctx.mm(2.0),   # under the box
                    a[3] + ctx.mm(1.2),        # over it (view gaps are snug)
                    a[1] - fh - ctx.mm(8.0))   # deeper under
            cands = [(a[2] + ctx.mm(2.5), a[1]),
                     (a[0] - fw - ctx.mm(2.5), a[1])]
            cands += [(a[0] + k * step, y)
                      for y in rows for k in (0, -1, 1, -2, 2, -3, 3, 4)]
            r = layout.place(cands, fw, fh, f"fcf-{f['symbol']}")
            if r is not None:
                emit_stack([f], r)
            continue
        groups.setdefault((view, f["target"]), []).append(f)

    for (view, tgt), frames in groups.items():
        job = view_jobs[view]
        ox, oy = offs[view]
        sizes = [mw.fcf((0, 0), f["symbol"], f["tol"], f.get("refs", []),
                        h)[1] for f in frames]
        stack_w = max(sizes)
        stack_h = len(frames) * 1.8 * h + (len(frames) - 1) * gap
        rad = _target_radius(tgt)
        anc, problem = resolve_feature_anchor(anchors, view, tgt, lfacs)
        if problem is not None:
            layout.unresolved.append(f"fcf-{problem}")
            continue
        anchor = anc["rect"] if anc is not None else None
        # a face seen edge-on: the frame leaders to that line (issue #6),
        # to the NEAREST of its segments and clear of the hatch (issue #7)
        flines = face_lines(job, (ox, oy), tgt) if rad is None else []
        fline = flines[0] if flines else None
        keep_out = hatch_polys(job, (ox, oy))
        if rad is None and fline is None and face_target(tgt) is not None:
            layout.unresolved.append(
                f"fcf-{tgt}: view {view!r} draws no edge there — the face is "
                "not shown edge-on in it; add a view that shows it")
            continue
        rect = None
        attached = False
        if anchor is not None:
            # clear of the anchor's own leader ink (claimed as a corridor
            # half a leader-width around the knee) before stepping down
            cand = [(anchor[0], anchor[1] - stack_h - ctx.mm(k))
                    for k in (2.5, 8.0, 15.0)]
            rect = layout.place(cand, stack_w, stack_h, f"fcf-stack-{tgt}",
                                ignore=("slot",))
            if rect is None and layout.unresolved:
                layout.unresolved.pop()   # fall through to the band
            attached = _frame_attached(ctx, layout, rect, anchor)
            if rect is not None and not attached:
                # under another callout, not its own: give the spot back
                # and look for a placement that can carry a leader
                layout.unclaim(rect, f"fcf-stack-{tgt}")
                rect = None
        face_tip = None
        if rect is None and fline is not None:
            # a face frame goes BESIDE its face, with a short square leader
            mnb, mxb = job["bounds"]
            vrect = (mnb[0] + ox, mnb[1] + oy, mxb[0] + ox, mxb[1] + oy)
            rect, face_tip = place_frame_near_face(
                ctx, layout, flines, vrect, stack_w, stack_h,
                f"fcf-stack-{tgt}", avoid=keep_out)
        if rect is None and face_tip is None and anchor is not None \
                and not [p for p in job["visible"] if p["t"] == "circle"
                         and rad is not None and abs(p["r"] - rad) < 0.1]:
            # Nothing in this view can carry a leader — the feature is drawn
            # edge-on (issue #6) — so the frame has to TOUCH its own size
            # callout. Only then is the ladder widened: a view that does
            # show the feature keeps the placement it always had.
            wider = [(anchor[0], anchor[3] + ctx.mm(k)) for k in (1.5, 7.0)]
            wider += [(anchor[0] + ctx.mm(dx),
                       anchor[1] - stack_h - ctx.mm(k))
                      for k in (2.5, 8.0) for dx in (6.0, -6.0, 12.0)]
            wider += [(anchor[0], anchor[1] - stack_h - ctx.mm(k))
                      for k in (22.0, 30.0)]
            rect = layout.place(wider, stack_w, stack_h, f"fcf-stack-{tgt}",
                                ignore=("slot",))
            if rect is None and layout.unresolved:
                layout.unresolved.pop()
            attached = _frame_attached(ctx, layout, rect, anchor)
            if rect is not None and not attached:
                layout.unclaim(rect, f"fcf-stack-{tgt}")
                rect = None
        need_leader = rect is None or face_tip is not None or not attached
        if rect is None:
            mnb, mxb = job["bounds"]
            ax1 = layout.area[2]
            # step the column outward past the view's own dimension lines,
            # and never past the frame — a stack as wide as the band has to
            # right-align inside it instead of hanging off the sheet
            for dx in (12.0, 18.0, 26.0, 34.0):
                band_x = min(mxb[0] + ox + ctx.mm(dx),
                             ax1 - stack_w - ctx.mm(1.0))
                for dy in (0.0, -10.0, 10.0, -20.0, -30.0, -40.0, 20.0,
                           30.0):
                    cy_t = (mnb[1] + mxb[1]) / 2 + oy + ctx.mm(dy)
                    rect = layout.place([(band_x, cy_t)], stack_w, stack_h,
                                        f"fcf-stack-{tgt}")
                    if rect is not None:
                        break
                    layout.unresolved.pop()
                if rect is not None:
                    break
        if rect is None:
            layout.unresolved.append(f"fcf-stack-{tgt}")
            continue
        if not need_leader:
            emit_stack(frames, rect)
        else:
            # leader from the feature to the stack MID-LEFT (standard)
            land = (rect[0], (rect[1] + rect[3]) / 2)
            tip = face_tip
            if tip is not None:
                # placed beside its face, with the route already found: the
                # leader leaves the stack by the edge FACING the face, and
                # is NOT re-routed — the frame's own claim is in the way now
                land = _frame_edge_towards(rect, tip)
            else:
                matches = [p for p in job["visible"] if p["t"] == "circle"
                           and rad is not None and abs(p["r"] - rad) < 0.1]
                if matches:
                    c = max(matches, key=lambda p: p["c"][1])
                    cx, cy = c["c"][0] + ox, c["c"][1] + oy
                    ang = math.atan2(land[1] - cy, land[0] - cx)
                    tip = (cx + c["r"] * math.cos(ang),
                           cy + c["r"] * math.sin(ang))
                elif flines:
                    for lines_try in ((flines, keep_out), (flines, ())):
                        opts = [leader_point_on_line(ctx, layout, fl, land,
                                                     avoid=lines_try[1])
                                for fl in lines_try[0]]
                        opts = [p for p in opts if p is not None]
                        if opts:
                            tip = min(opts, key=lambda p: math.dist(p, land))
                            break
                elif anchor is not None:
                    # a feature of size the view shows EDGE-ON: its size
                    # callout is what carries it, so the frame points at
                    # that callout rather than at a circle it has not got
                    # (#6). The tip sits ON the callout's own ink, so the
                    # route is checked from just clear of it.
                    tip = _frame_edge_towards(anchor, land)
                    if not _leader_path_clear(
                            ctx, layout, _step_towards(ctx, tip, land), land,
                            max_frac=0.7):
                        tip = None
            if tip is None:
                # A frame with no leader points at nothing: the reader
                # cannot tell which feature it controls. Say what is
                # missing instead of drawing a frame that means nothing.
                why = ("the feature is not drawn there as a circle or an "
                       "edge-on face; give it a %%c callout, or add a view "
                       "that shows it")
                if fline is not None or anchor is not None:
                    why = ("no clear route for its leader — every path from "
                           "it crosses other annotation")
                layout.unresolved.append(
                    f"fcf-stack-{tgt}: nothing in view {view!r} to leader it "
                    f"to — {why}")
                layout.unclaim(rect, f"fcf-stack-{tgt}")
                continue
            emit_stack(frames, rect)
            recs.append(mw.line(tip, land, mw.P_ANNO))
            if fline is not None:
                # on a SURFACE the leader ends in an arrowhead on the face
                # (ISO 1101 5.2); on a feature of size it meets the size
                # dimension instead, which carries its own arrows
                recs.append(arrowhead(tip, (tip[0] - land[0],
                                            tip[1] - land[1]), ctx.mm(2.5)))
            _claim_leader_ink(ctx, layout, tip, land, f"fcf-{tgt}")
    return recs


def _wrap(textstr, limit=46):
    """Word-wrap one note into numbered + continuation lines."""
    words = textstr.split()
    lines, cur = [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > limit:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines



LOCDIM_STANDOFFS = (10.0, 13.0, 16.0, 19.0, 22.0, 26.0, 30.0, 34.0,
                    38.0, 42.0, 46.0)


def _locdim_metrics(ctx, sp, view_jobs, offs, lfac=1.0):
    """(label ink span, extension-line coords) of one location dim, both on
    the axis the dim measures along — the axis its value reads along too.
    `lfac` is the view's DIMLFAC: the span is geometry, the label is what
    the reader sees, and in a detail view the two differ by k."""
    ox, oy = offs[sp["view"]]
    a = (sp["a"][0] + ox, sp["a"][1] + oy)
    b = (sp["b"][0] + ox, sp["b"][1] + oy)
    j = 1 if sp["orient"] == "v" else 0
    val = abs(b[j] - a[j])
    shown = val * lfac
    prec = sp.get("precision")
    if prec is None and abs(shown - round(shown)) > 0.005:
        prec = 2
    label = f"{shown:.{prec}f}" if prec else fmt_val(shown)
    h, _ = cramped_dim_style(ctx, label, val, ctx.mm(3.0))
    w = mw.glyph_w(label, h)
    mid = (a[j] + b[j]) / 2.0
    lo = mid - w / 2.0
    return (lo, lo + dim_label_ink(label, h)), (a[j], b[j])


def order_location_dims(ctx, specs, view_jobs, offs, lfacs=None):
    """Order the dims of each (view, side) so that a dim whose EXTENSION
    line would run through another dim's value is drawn INSIDE it — its
    extension line then stops at its own dim line, short of that value.

    Extension lines are fixed to the def points, so a value straddling one
    cannot be nudged away from it: the only cure is the drawing order, and
    without it a pitch dim's extension line ran straight through the slot
    width value on sheet 03 (round-8 QA)."""
    n = len(specs)
    info = []
    for sp in specs:
        info.append(_locdim_metrics(ctx, sp, view_jobs, offs,
                                    (lfacs or {}).get(sp.get("view"), 1.0))
                    if sp.get("view") in view_jobs else None)
    clr = ctx.mm(0.5)
    before: dict[int, set] = {i: set() for i in range(n)}
    for i in range(n):
        for k in range(n):
            if i == k or info[i] is None or info[k] is None:
                continue
            if (specs[i]["view"], specs[i]["side"]) != \
                    (specs[k]["view"], specs[k]["side"]):
                continue
            def hits(x, y):          # y's extension lines cross x's value
                return any(x[0][0] - clr <= e <= x[0][1] + clr for e in y[1])
            if hits(info[i], info[k]) and not hits(info[k], info[i]):
                before[i].add(k)
    order, done = [], set()
    while len(order) < n:
        for i in range(n):
            if i not in done and before[i] <= done:
                order.append(i)
                done.add(i)
                break
        else:                        # mutual crossing: keep manifest order
            nxt = next(i for i in range(n) if i not in done)
            order.append(nxt)
            done.add(nxt)
    return [specs[i] for i in order]


def dim_precision(val, forced=None):
    """Fewest decimals that still state the value exactly (17.5, not 17.50 —
    MusaCAD renders %.*f of the measured length)."""
    if forced is not None:
        return forced
    for p in (0, 1, 2, 3):
        if abs(val - round(val, p)) < 5e-4:
            return p
    return 3


def place_location_dims(ctx, layout, specs, view_jobs, offs, geom=(),
                        soft=(), lfacs=None):
    """Manifest-driven feature-location dims (pitch, edge references,
    depths): explicit def points in view coords + measuring orientation +
    placement side. Optional per-spec flags:
      "typ"   — TYP tag beside the value (magenta, claimed);
      "basic" — ASME Y14.5 BASIC dimension: the value is boxed, because a
                position tolerance (the feature's FCF) controls it. The box
                is part of the claimed rect, so nothing can crowd it.

    The def points are PROJECTED onto the view boundary the dim is placed
    against before anything is drawn. Only the measuring axis carries the
    value, so this is exact — and it is what a draughtsman does: a feature's
    position is projected to the side of the view and dimensioned there,
    instead of dragging extension lines across the view from an interior
    point (round-8 QA: 60-120 mm of extension line along outlines, through
    hatch and through other features).

    `lfacs` maps a view name to its DIMLFAC (a detail view drawn at k
    times size has 1/k): the def points and everything geometric stay in
    the view's own units, while the VALUE — the label laid out here and the
    one MusaCAD writes — is the measurement times that factor.

    Returns (records, floors): floors maps (view, side) -> the outermost
    stand-off (printed mm) used, so the overall extent dims stay outboard."""
    h0 = ctx.mm(3.0)
    # basic-box padding around the value ink: the frame is ink too, so it
    # keeps a full INK_CLEAR white gap off the digits (plus its own pen)
    box_pad = ctx.mm(0.55)
    recs, floors = [], {}
    for sp in order_location_dims(ctx, specs, view_jobs, offs, lfacs):
        view = sp["view"]
        if view not in view_jobs:
            layout.unresolved.append(f"locdim-noview-{view}")
            continue
        lfac = (lfacs or {}).get(view, 1.0)
        ox, oy = offs[view]
        a = (sp["a"][0] + ox, sp["a"][1] + oy)
        b = (sp["b"][0] + ox, sp["b"][1] + oy)
        vmn, vmx = view_jobs[view]["bounds"]
        vx0, vy0 = vmn[0] + ox, vmn[1] + oy
        vx1, vy1 = vmx[0] + ox, vmx[1] + oy
        orient = sp["orient"]
        val = abs(b[1] - a[1]) if orient == "v" else abs(b[0] - a[0])
        # `val` is geometry (it sizes the layout); `shown` is what the
        # reader sees, which in a detail view is val/k
        shown = val * lfac
        prec = dim_precision(shown, sp.get("precision"))
        label = f"{shown:.{prec}f}" if prec else fmt_val(shown)
        basic = bool(sp.get("basic"))
        # a value that will not fit between its own extension lines drops to
        # the next text size and oblique arrows (ISO 129-1)
        h, dim_kw = cramped_dim_style(ctx, label, val, h0)
        # --- side sanity + extension-line origin projection ----------------
        # k is the axis the extension lines run along (the one the value does
        # NOT measure); the dim is placed against one of the view's two
        # edges on that axis.
        k = 0 if orient == "v" else 1
        lo_edge = vx0 if k == 0 else vy0
        hi_edge = vx1 if k == 0 else vy1
        side = sp["side"]
        want_hi = side in ("right", "top")
        pos = (a[k] + b[k]) / 2.0
        # a requested side that would put the dim at the FAR end of the view
        # while the other edge is right next to the feature is not a
        # placement, it is a 100 mm extension line: use the near edge
        flipped = None
        if abs((hi_edge if want_hi else lo_edge) - pos) > \
                abs((lo_edge if want_hi else hi_edge) - pos) + ctx.mm(20.0):
            want_hi = not want_hi
            side = ("right" if want_hi else "left") if k == 0 \
                else ("top" if want_hi else "bottom")
            flipped = f"{sp['side']}->{side}"
            layout.flips.append(f"locdim-{label}: {flipped}")
        edge = hi_edge if want_hi else lo_edge
        a = (edge, a[1]) if k == 0 else (a[0], edge)
        b = (edge, b[1]) if k == 0 else (b[0], edge)
        sign = 1 if want_hi else -1
        base = edge
        # extension lines that would still run ALONG drawn geometry (a def
        # point sitting on the cutting-plane line) start beyond it instead
        probe = (base + sign * ctx.mm(LOCDIM_STANDOFFS[0]),
                 (a[1] + b[1]) / 2) if orient == "v" \
            else ((a[0] + b[0]) / 2, base + sign * ctx.mm(LOCDIM_STANDOFFS[0]))
        ext_off = max(EXT_OFFSET,
                      ext_clear_offset(ctx, geom, a, b, probe, orient))
        ext_ext = EXT_EXTENSION_CRAMPED if dim_kw else EXT_EXTENSION
        dim_kw["style"] = dim_style_index(ctx, ext_off, ext_ext, lfac)
        cands = []
        for d in LOCDIM_STANDOFFS:          # nearest legal placement wins
            off = base + sign * ctx.mm(d)
            lp = (off, (a[1] + b[1]) / 2) if orient == "v" \
                else ((a[0] + b[0]) / 2, off)
            exts, dline = dim_segments(a, b, lp, orient, ctx.mm(ext_off),
                                       ctx.mm(ext_ext))
            worst, cross = dim_ink_conflict(ctx, geom, exts + [dline], soft)
            if worst > COLLINEAR_OK or cross:
                continue                    # would draw along/through a view
            cands.append((lp, dim_label_rect(a, b, lp, orient, label, h),
                          dim_line_band(ctx, a, b, lp, orient)))
        if not cands:
            layout.unresolved.append(f"locdim-{label}-noclearplace")
            continue
        lp, _, placed = _place_dim(ctx, layout, cands, f"locdim-{label}",
                                     grow=box_pad if basic else 0.0)
        if lp is None:
            continue   # _place_dim already recorded the unresolved label
        recs.append(mw.dim(0, a, snap_def_points(a, b, orient, prec, lfac),
                           lp, precision=prec, **dim_kw))
        _claim_dim_ink(ctx, layout, a, b, lp, orient, label)
        used = abs((lp[0] if orient == "v" else lp[1]) - base) / ctx.mm(1.0)
        key = (view, side)
        floors[key] = max(floors.get(key, 0.0), used)
        if basic:
            # rectangular frame around the value = theoretically exact
            recs.append(mw.polyline([(placed[0], placed[1]),
                                     (placed[2], placed[1]),
                                     (placed[2], placed[3]),
                                     (placed[0], placed[3])],
                                    closed=True, props=mw.P_ANNO))
        if sp.get("typ"):
            # candidates are INK rects (what the reader sees), not anchors:
            # an attach=6 block renders 0.667 h above its anchor. A tag has
            # to clear the dim's OWN extension lines too — they run between
            # the value and the feature, so "just past the value" can land
            # straight on one (round-5 QA D1).
            ttw = mw.glyph_ink_w("TYP", h)
            # clear the extension-line corridor (half-width band/4) and the
            # registry's own clearance pad, else the tag lands a hair inside
            gap = ctx.mm(1.6)
            endw = ctx.mm(0.5)               # dim_line_band end margin
            if orient == "v":
                # OUTBOARD of the dim line first, level with its own value:
                # the corridor past the top of a vertical dim is the natural
                # leader route into the annotation band, and a tag parked
                # there sends every callout on a long detour. Then above,
                # below, and finally inboard of the value.
                cx_t = (placed[0] + placed[2]) / 2 - ttw / 2
                band = ctx.mm(1.6)
                out = (lp[0] + band + gap) if sign > 0 \
                    else (lp[0] - band - gap - ttw)
                hi = max(placed[3], max(a[1], b[1]) + endw) + gap
                lo = min(placed[1], min(a[1], b[1]) - endw) - h - gap
                cands = [(out, placed[1]), (cx_t, hi), (cx_t, lo),
                         (placed[0] - ttw - gap, placed[1])]
            else:
                right = max(placed[2], max(a[0], b[0]) + endw) + gap
                left = min(placed[0], min(a[0], b[0]) - endw) - ttw - gap
                cands = [(right, placed[1]), (left, placed[1]),
                         (placed[0], placed[3] + gap),
                         (placed[0], lp[1] - ctx.mm(1.6) - gap - h)]
            r = layout.place(cands, ttw, h, "typ-tag")
            if r is None:
                layout.unresolved.pop()
                # last resort: ride beside the value at the value's own
                # stand-off, i.e. inside its dim's line corridor
                r = layout.place(cands, ttw, h, "typ-tag", ignore=("line",))
            if r is not None:
                recs.append(mw.mtext(mw.mtext_anchor_for_ink(r, h, attach=6),
                                     "TYP", h, attach=6, props=mw.P_ANNO))
    return recs, floors


# Column widths (characters) a note block may wrap to, and the ISO 3098
# character heights it may drop to. The house width comes first, then WIDER
# (the reference sheet runs its notes as a broad, few-line band across the
# bottom — widening shortens the block, and height is what usually
# collides), then narrower columns for a side band. 2.5 mm is a standard
# note height on the reference drawing and the floor here: below it a note
# stops being reliably legible on A4 and the answer is a drawing decision.
NOTES_LIMITS = (46, 54, 66, 80, 34, 26, 20, 16)
NOTES_HEIGHTS = (3.0, 2.5)


def dim_arrowhead(tip, along, size):
    """A filled dimension arrowhead with MUSACAD's own proportions
    (core/dimension.cpp append_arrowhead, ArrowType::Filled: base one
    `size` back along the line, 0.18*size each side).

    A lettered dimension draws its own arrowheads, so they have to match
    the DIM entities beside it exactly — a drawing with two kinds of arrow
    on one sheet reads as two drawings."""
    ux, uy = along
    ln = math.hypot(ux, uy) or 1.0
    ux, uy = ux / ln, uy / ln
    px, py = -uy, ux
    base = (tip[0] + ux * size, tip[1] + uy * size)
    return mw.hatch([[tip,
                      (base[0] + px * size * 0.18, base[1] + py * size * 0.18),
                      (base[0] - px * size * 0.18, base[1] - py * size * 0.18)]],
                    "SOLID", props=mw.P_ANNO)


def place_letter_dim(ctx, layout, part, view_jobs, offs, geom=(), soft=()):
    """The LETTER dimension of a tabulated drawing (issue #11).

    A family that differs in one size is drawn once, with that size shown
    as a letter and its values listed in the variants table. A DIM entity
    always prints the distance between its own def points — MusaCAD
    composes prefix + measurement + suffix, with nothing that replaces the
    middle — so the modelled variant's own thickness would print as a
    number and read as the size of every variant. This draws the
    dimension itself instead: the same extension lines, dimension line and
    filled arrowheads MusaCAD would draw for that style, with the letter
    where the value goes. It is placed and collision-checked exactly like
    any other dimension, through the same candidate ladder.
    """
    spec = (part.get("variants") or {}).get("dim")
    if not spec:
        return []
    letter = str((part.get("variants") or {}).get("letter", "T"))
    view = spec.get("view", "front")
    if view not in view_jobs:
        layout.unresolved.append(f"letter-dim-{letter}-noview-{view}")
        return []
    ox, oy = offs[view if view in offs else "section"]
    a = (spec["a"][0] + ox, spec["a"][1] + oy)
    b = (spec["b"][0] + ox, spec["b"][1] + oy)
    orient = spec.get("orient", "h")
    h = ctx.mm(3.0)
    vmn, vmx = view_jobs[view]["bounds"]
    k = 0 if orient == "v" else 1
    lo_edge = (vmn[0] + ox) if k == 0 else (vmn[1] + oy)
    hi_edge = (vmx[0] + ox) if k == 0 else (vmx[1] + oy)
    side = spec.get("side", "right" if k == 0 else "top")
    want_hi = side in ("right", "top")
    edge = hi_edge if want_hi else lo_edge
    # the def points are projected onto the view edge the dim is placed
    # against, as every location dim is
    a = (edge, a[1]) if k == 0 else (a[0], edge)
    b = (edge, b[1]) if k == 0 else (b[0], edge)
    sign = 1 if want_hi else -1
    cands = []
    for d in LOCDIM_STANDOFFS:
        off = edge + sign * ctx.mm(d)
        lp = (off, (a[1] + b[1]) / 2) if orient == "v" \
            else ((a[0] + b[0]) / 2, off)
        exts, dline = dim_segments(a, b, lp, orient, ctx.mm(EXT_OFFSET),
                                   ctx.mm(EXT_EXTENSION))
        worst, cross = dim_ink_conflict(ctx, geom, exts + [dline], soft)
        if worst > COLLINEAR_OK or cross:
            continue
        cands.append((lp, dim_label_rect(a, b, lp, orient, letter, h),
                      dim_line_band(ctx, a, b, lp, orient)))
    if not cands:
        layout.unresolved.append(f"letter-dim-{letter}-noclearplace")
        return []
    lp, _, placed = _place_dim(ctx, layout, cands, f"letter-dim-{letter}")
    if lp is None:
        return []
    exts, (fa, fb) = dim_segments(a, b, lp, orient, ctx.mm(EXT_OFFSET),
                                  ctx.mm(EXT_EXTENSION))
    recs = [mw.line(e0, e1, mw.P_ANNO) for e0, e1 in exts]
    recs.append(mw.line(fa, fb, mw.P_ANNO))
    arrow = ctx.mm(2.5)                      # the house dimstyle's arrow
    recs.append(dim_arrowhead(fa, (fb[0] - fa[0], fb[1] - fa[1]), arrow))
    recs.append(dim_arrowhead(fb, (fa[0] - fb[0], fa[1] - fb[1]), arrow))
    recs.append(mw.cell_text(((placed[0] + placed[2]) / 2.0,
                              (placed[1] + placed[3]) / 2.0), letter, h,
                             props=mw.P_ANNO))
    _claim_dim_ink(ctx, layout, a, b, lp, orient, letter)
    return recs


def place_notes(ctx, layout, notes, rows_h):
    """Numbered NOTES block (word-wrapped). Nothing here may ever be dropped
    — the block is placed by searching the drawing area for a free rectangle
    that holds it: lowest first, then left to right, over every wrap width
    and (only if no width fits at full size) the smaller ISO note height.
    A fixed candidate ladder used to fail on a tall part whose bottom band is
    eaten by the section caption and whose side bands are eaten by the
    dimension columns (round-8 QA, sheet 01)."""
    if not notes:
        return []
    # manifest notes may arrive pre-numbered ("1. BREAK ...") — strip any
    # leading ordinal so this block's own numbering is the single source
    notes = [re.sub(r"^\d+\.\s*", "", t) for t in notes]
    ax0, ay0, ax1, ay1 = ctx.draw_area
    y_floor = ay0 + rows_h + ctx.mm(2.0)
    step = ctx.mm(4.0)
    best = None
    for hi, hmm in enumerate(NOTES_HEIGHTS):
        h = ctx.mm(hmm)
        lh = 1.55 * h
        for li, limit in enumerate(NOTES_LIMITS):
            lines = []
            for i, t in enumerate(notes, start=1):
                wrapped = _wrap(t, limit)
                lines.append(f"{i}. {wrapped[0]}")
                lines += [f"   {c}" for c in wrapped[1:]]
            rows = list(reversed(lines)) + ["NOTES:"]   # bottom-up, as emitted
            height = (len(rows) - 1) * lh + h
            width = max(mw.glyph_ink_w(t, h) for t in rows)
            if width > ax1 - ax0 - ctx.mm(6.0) or height > ay1 - y_floor:
                continue
            ny = int((ay1 - ctx.mm(3.0) - height - y_floor) / step)
            nx = int((ax1 - ctx.mm(3.0) - width - ax0 - ctx.mm(3.0)) / step)
            spot = None
            for j in range(max(0, ny) + 1):        # lowest band first
                yy = y_floor + j * step
                # frame-anchored spots first (house style), then a sweep
                xs = [ax0 + ctx.mm(3.0), ax1 - width - ctx.mm(3.0)]
                xs += [ax0 + ctx.mm(3.0) + k * step
                       for k in range(1, max(0, nx) + 1)]
                for xx in xs:
                    r = (xx, yy, xx + width, yy + height)
                    if layout.inside(r) and not layout.collides(r):
                        spot = r
                        break
                if spot:
                    break
            if spot is None:
                continue
            # the block belongs in the LOWEST free band of the sheet; only
            # inside one band does a bigger character height, then the house
            # column width, decide. Choosing the first fit instead sent a
            # sheet's notes up a side band as a narrow tall column while the
            # bottom band sat empty (round-8 QA, sheet 03).
            key = (round((spot[1] - y_floor) / ctx.mm(8.0)), hi, li)
            if best is None or key < best[0]:
                best = (key, spot, rows, h, lh)
    if best is None:
        layout.unresolved.append("notes-block")
        return []
    _, spot, rows, h, lh = best
    layout.claim(spot, "notes-block")
    # the block is one attach=6 MTEXT per row at pitch lh: its INK runs from
    # the bottom row's baseline to the top row's cap, and every row renders
    # 0.667 h above its own anchor (mtext.cpp kSingleSpacing)
    lift = (mw.MTEXT_LINE_SPACING - 1.0) * h
    base = (spot[0], spot[1] - lift)
    return [mw.mtext((base[0], base[1] + k * lh), t, h, attach=6)
            for k, t in enumerate(rows)]


BEND_HEADERS = ("NO.", "ANGLE", "IN. R", "DIR", "FLANGE")
# a bend table may drop a step smaller than the notes before it gives up:
# it is a handful of numbers, and losing it loses the bend spec entirely
BEND_TABLE_HEIGHTS = (3.0, 2.5, 2.0, 1.8)


def bend_rows(part):
    """One row per modelled bend, in the order the part is formed."""
    fp = part.get("flat_pattern") or {}
    rows = []
    for i, b in enumerate(fp.get("bends", []), start=1):
        rows.append((str(i),
                     f"{b['angle_deg']:g}%%d",
                     f"R{b['radius_mm']:g}",
                     "DOWN" if b.get("invert") else "UP",
                     f"{b['length_mm']:g}"))
    return rows


def place_table(ctx, layout, title, headers, rows, rows_h, label,
                heights=BEND_TABLE_HEIGHTS):
    """A ruled table in the sheet's leftover space.

    Placed by the same lowest-free-band search the notes block uses,
    because the two blocks compete for the same leftover sheet, and shrunk
    through `heights` until it fits — never below the smallest, which is
    the legibility floor, so a table that cannot fit FAILS the sheet
    instead of printing text nobody can read.
    """
    if not rows:
        return []
    ax0, ay0, ax1, ay1 = ctx.draw_area
    y_floor = ay0 + rows_h + ctx.mm(2.0)
    ncol = len(headers)
    for hmm in heights:
        h = ctx.mm(hmm)
        pad = 0.5 * h
        body = [headers] + rows
        cols = [max(mw.glyph_ink_w(r[c], h) for r in body) + 2 * pad
                for c in range(ncol)]
        width = max(sum(cols), mw.glyph_ink_w(title, h) + 2 * pad)
        rh = h + 2 * pad
        cols[-1] += width - sum(cols)
        height = rh * (len(rows) + 1) + 1.7 * h      # grid + title line
        if width > ax1 - ax0 - ctx.mm(6.0) or height > ay1 - y_floor:
            continue
        spot = _lowest_free(ctx, layout, width, height, y_floor)
        if spot is None:
            continue
        layout.claim(spot, label)
        x0, y0 = spot[0], spot[1]
        out = [mw.mtext((x0, y0 + height - 1.7 * h), title, h, attach=6)]
        top = y0 + rh * (len(rows) + 1)
        for k in range(len(rows) + 2):               # horizontal rules
            yy = top - k * rh
            out.append(mw.line((x0, yy), (x0 + width, yy)))
        xx = x0
        for c, w in enumerate(cols):                 # vertical rules
            out.append(mw.line((xx, y0), (xx, top)))
            xx += w
        out.append(mw.line((xx, y0), (xx, top)))
        for r, row in enumerate(body):
            cy = top - (r + 0.5) * rh
            xx = x0
            for c, w in enumerate(cols):
                out.append(mw.cell_text((xx + w / 2, cy), row[c], h))
                xx += w
        return out
    layout.unresolved.append(label)
    return []


def place_bend_table(ctx, layout, part, rows_h):
    """BEND TABLE for a sheet-metal sheet, as a real ruled grid.

    The numbers are the model's own bend inputs, not measurements off the
    folded solid, so the table states what was designed.
    """
    # the blank size is dimensioned on the view and the thickness is in the
    # title block, so the caption stays short and the table stays narrow
    return place_table(ctx, layout, "BEND TABLE", BEND_HEADERS,
                       bend_rows(part), rows_h, "bend-table")


def variant_rows(part) -> tuple:
    """(title, headers, rows) of a tabulated drawing's variants table."""
    spec = part.get("variants") or {}
    cols = [str(c) for c in spec.get("columns") or []]
    rows = [[str(v) for v in row] for row in spec.get("rows") or []]
    return (str(spec.get("title", "VARIANTS")), tuple(cols), rows)


def place_variants_table(ctx, layout, part, rows_h):
    """The table of a TABULATED drawing (issue #11): one row per variant,
    giving the letter dimension's value for each, with its own mark and
    quantity. One sheet then covers a family that differs in one size."""
    title, headers, rows = variant_rows(part)
    if not rows:
        return []
    return place_table(ctx, layout, title, headers, rows, rows_h,
                       "variants-table")


def _lowest_free(ctx, layout, width, height, y_floor):
    """Lowest, then leftmost, free rectangle of this size in the drawing
    area — the same search rule the notes block uses."""
    ax0, ay0, ax1, ay1 = ctx.draw_area
    step = ctx.mm(4.0)
    ny = int((ay1 - ctx.mm(3.0) - height - y_floor) / step)
    nx = int((ax1 - ctx.mm(3.0) - width - ax0 - ctx.mm(3.0)) / step)
    for j in range(max(0, ny) + 1):
        yy = y_floor + j * step
        xs = [ax0 + ctx.mm(3.0), ax1 - width - ctx.mm(3.0)]
        xs += [ax0 + ctx.mm(3.0) + k * step for k in range(1, max(0, nx) + 1)]
        for xx in xs:
            r = (xx, yy, xx + width, yy + height)
            if layout.inside(r) and not layout.collides(r):
                return r
    return None


def _poly_seg_clear(poly, a, b, clr):
    """True if segment a-b stays at least `clr` away from convex polygon."""
    def inside(p):
        sgn = 0
        for i in range(len(poly)):
            q0, q1 = poly[i], poly[(i + 1) % len(poly)]
            c = (q1[0] - q0[0]) * (p[1] - q0[1]) - (q1[1] - q0[1]) * (p[0] - q0[0])
            s = 1 if c > 0 else -1 if c < 0 else 0
            if s and sgn and s != sgn:
                return False
            sgn = sgn or s
        return True
    if inside(a) or inside(b):
        return False
    edges = list(zip(poly, poly[1:] + poly[:1]))
    for q0, q1 in edges:
        if min(_seg_pt_dist(a, b, q0), _seg_pt_dist(q0, q1, a),
               _seg_pt_dist(q0, q1, b)) < clr:
            return False
        # proper crossing
        d1 = (b[0] - a[0]) * (q0[1] - a[1]) - (b[1] - a[1]) * (q0[0] - a[0])
        d2 = (b[0] - a[0]) * (q1[1] - a[1]) - (b[1] - a[1]) * (q1[0] - a[0])
        d3 = (q1[0] - q0[0]) * (a[1] - q0[1]) - (q1[1] - q0[1]) * (a[0] - q0[0])
        d4 = (q1[0] - q0[0]) * (b[1] - q0[1]) - (q1[1] - q0[1]) * (b[0] - q0[0])
        if d1 * d2 < 0 and d3 * d4 < 0:
            return False
    return True


def place_angle_refs(ctx, layout, spec, view_jobs, offs, geom):
    """Angular reference for a less technical vendor (manifest "angle_refs"):
      "rays":  [{"angle": deg, "r0": .., "r1": .., "text": "30%%d"}] -- a
               centre line from r0 to r1 through each feature, labelled at
               its outer end ALONG the ray; the label walks outward until it
               clears the drawn geometry and every claimed annotation;
      "zero":  {"at": [x, y], "text": "0%%d REF"} -- the datum direction;
      "ccw":   {"r": .., "a0": deg, "a1": deg, "at": [x, y],
                "lines": ["ANGLES CCW", ...]} -- direction arc + arrowhead.
    All coordinates are the view's model coordinates (mm, full size)."""
    if not spec:
        return []
    view = spec.get("view", "front")
    if view not in view_jobs:
        layout.unresolved.append(f"angle-refs-noview-{view}")
        return []
    ox, oy = offs[view]
    h = ctx.mm(2.5)
    clr = ctx.mm(INK_CLEAR + 0.4)
    gap = ctx.mm(1.2)
    recs = []

    def free(poly, ignore=("view",)):
        xs = [p[0] for p in poly]; ys = [p[1] for p in poly]
        bb = (min(xs), min(ys), max(xs), max(ys))
        if not layout.inside(bb) or layout.collides(bb, ignore=ignore):
            return None
        if all(_poly_seg_clear(poly, a, b, clr) for a, b in geom):
            return bb
        return None

    for ray in spec.get("rays", []):
        th = math.radians(ray["angle"])
        u = (math.cos(th), math.sin(th))
        flip = math.cos(th) < -1e-9 or (abs(math.cos(th)) <= 1e-9 and u[1] < 0)
        d = (-u[0], -u[1]) if flip else u          # reading direction
        n = (-d[1], d[0])                           # reading "up"
        s = ray["text"]
        w = mw.text_w(s, h)
        placed = None
        r_t = ray["r1"]
        while r_t <= ray["r1"] + 60.0:
            p_in = (ox + u[0] * r_t, oy + u[1] * r_t)
            p_out = (p_in[0] + u[0] * (gap + w), p_in[1] + u[1] * (gap + w))
            a = (p_in[0] + u[0] * gap, p_in[1] + u[1] * gap)
            start = p_out if flip else a
            base = (start[0] - n[0] * h * 0.5, start[1] - n[1] * h * 0.5)
            poly = [base, (base[0] + d[0] * w, base[1] + d[1] * w),
                    (base[0] + d[0] * w + n[0] * h, base[1] + d[1] * w + n[1] * h),
                    (base[0] + n[0] * h, base[1] + n[1] * h)]
            bb = free(poly)
            if bb is not None:
                placed = (r_t, base)
                layout.claim(bb, f"angle-{s}")
                break
            r_t += 2.0
        if placed is None:
            layout.unresolved.append(f"angle-ray-{s}")
            continue
        r_t, base = placed
        recs.append(mw.line((ox + u[0] * ray["r0"], oy + u[1] * ray["r0"]),
                            (ox + u[0] * r_t, oy + u[1] * r_t), P_CUTLINE))
        recs.append(mw.text(base, s, h,
                            rotation=math.atan2(d[1], d[0]), props=mw.P_ANNO))

    z = spec.get("zero")
    if z:
        s = z["text"]
        w = mw.text_w(s, h)
        base = (ox + z["at"][0], oy + z["at"][1])
        poly = [base, (base[0] + w, base[1]), (base[0] + w, base[1] + h),
                (base[0], base[1] + h)]
        # the datum label rides beside the cutting-plane line on purpose:
        # its coarse claim box is skipped, the exact ink clearance still holds
        bb = free(poly, ignore=("view", "line"))
        if bb is None:
            layout.unresolved.append("angle-zero-ref")
        else:
            layout.claim(bb, "angle-zero-ref")
            recs.append(mw.text(base, s, h, props=mw.P_ANNO))

    c = spec.get("ccw")
    if c:
        a0, a1 = math.radians(c["a0"]), math.radians(c["a1"])
        recs.append(mw.arc((ox, oy), c["r"], a0, a1, mw.P_ANNO))
        tip = (ox + c["r"] * math.cos(a1), oy + c["r"] * math.sin(a1))
        hatch = arrowhead(tip, (-math.sin(a1), math.cos(a1)), ctx.mm(2.5))
        recs.append(hatch.replace(mw.P, mw.P_ANNO) if mw.P in hatch else hatch)
        lines = c.get("lines", [])
        lh = 1.55 * h
        x0, y0 = ox + c["at"][0], oy + c["at"][1]
        ws = max(mw.text_w(t, h) for t in lines) if lines else 0
        poly = [(x0 - ws / 2, y0 - lh * (len(lines) - 1)), (x0 + ws / 2, y0 - lh * (len(lines) - 1)),
                (x0 + ws / 2, y0 + h), (x0 - ws / 2, y0 + h)]
        bb = free(poly)
        if bb is None:
            layout.unresolved.append("angle-ccw-note")
        else:
            layout.claim(bb, "angle-ccw-note")
            for i, t in enumerate(lines):
                recs.append(mw.text((x0, y0 - i * lh), t, h, justify=1,
                                    props=mw.P_ANNO))
    return recs


def arrowhead(tip, direction, size):
    """Filled triangle as a SOLID hatch (plot arrows for the cutting plane)."""
    dx, dy = direction
    ln = math.hypot(dx, dy) or 1.0
    dx, dy = dx / ln, dy / ln
    px, py = -dy, dx
    b = (tip[0] - dx * size, tip[1] - dy * size)
    p1 = (b[0] + px * size * 0.35, b[1] + py * size * 0.35)
    p2 = (b[0] - px * size * 0.35, b[1] - py * size * 0.35)
    return mw.hatch([[tip, p1, p2]], "SOLID")


def cutting_plane(ctx, layout, front_job, f_off, label, projection="below",
                  frac=0.5):
    """ISO-style cutting plane: Center-linetype line across the view with
    THICK end strokes along the line (both ends OUTSIDE the view outline),
    a thick arrow at each end pointing in the viewing direction, and the
    letter beside each arrowhead. Sight matches section_job (third-angle:
    away from the placed section view); flip both together for first-angle.

    Every stroke is claimed: the arrow/label zones as "anno", and the cut
    LINE ITSELF as "line" ink — it is the heaviest line on the sheet and a
    leader routed along it disappears underneath it (round-5 QA, sheet 02).
    Returns (records, segments): the segments join the sheet's view geometry
    so no dimension's extension line may be drawn along the cut line."""
    mn, mx = front_job["bounds"]
    ox, oy = f_off
    ext = ctx.mm(9.0)        # line overshoot beyond the view outline
    end = ctx.mm(6.0)        # thick end-stroke length along the line
    shaft = ctx.mm(7.0)      # arrow shaft length
    asz = ctx.mm(4.0)        # solid arrowhead length
    h = ctx.mm(4.0)          # letter height
    band = ctx.mm(1.0)       # half-width claimed around the cut line
    recs = []
    if projection == "below":                # horizontal line, sight up
        y = mn[1] + (mx[1] - mn[1]) * frac + oy
        x0, x1 = mn[0] + ox - ext, mx[0] + ox + ext
        recs = [mw.line((x0, y), (x1, y), P_CUTLINE),
                mw.line((x0, y), (x0 + end, y), P_THICK),
                mw.line((x1 - end, y), (x1, y), P_THICK)]
        layout.claim((x0, y - band, x1, y + band), f"cutline-{label}", "line")
        segs, arrows = [((x0, y), (x1, y))], []
        for xa in (x0, x1):
            tpos = (xa + mw.MC_DX * h, y + shaft + asz + ctx.mm(1.5))
            recs += [mw.line((xa, y), (xa, y + shaft), P_THICK),
                     arrowhead((xa, y + shaft + asz), (0, 1), asz),
                     mw.mtext(tpos, label, h, attach=7)]
            arrows.append(((xa, y), (xa, y + shaft + asz)))
            layout.keep_off.append(((xa, y), (xa, y + shaft + asz),
                                    "the cutting plane's sight arrow"))
            layout.claim((xa - band, y, xa + band, y + shaft + asz),
                         f"cutarrow-{label}", "line")
            ink = mw.mtext_ink(tpos, [label], h, attach=7)
            layout.claim((min(ink[0], xa - asz / 2), y - ctx.mm(1.0),
                          max(ink[2], xa + asz / 2), ink[3]),
                         f"cutlabel-{label}")
    else:                                    # vertical line, sight sideways
        adx = -1 if projection == "right" else 1
        x = mn[0] + (mx[0] - mn[0]) * frac + ox
        y0, y1 = mn[1] + oy - ext, mx[1] + oy + ext
        recs = [mw.line((x, y0), (x, y1), P_CUTLINE),
                mw.line((x, y0), (x, y0 + end), P_THICK),
                mw.line((x, y1 - end), (x, y1), P_THICK)]
        layout.claim((x - band, y0, x + band, y1), f"cutline-{label}", "line")
        segs, arrows = [((x, y0), (x, y1))], []
        for ya, side in ((y0, -1), (y1, 1)):
            tpos = (x + adx * shaft, ya + side * ctx.mm(2.5))
            attach = 1 if side < 0 else 7
            recs += [mw.line((x, ya), (x + adx * shaft, ya), P_THICK),
                     arrowhead((x + adx * (shaft + asz), ya), (adx, 0), asz),
                     mw.mtext(tpos, label, h, attach=attach)]
            arrows.append(((x, ya), (x + adx * (shaft + asz), ya)))
            layout.keep_off.append(((x, ya),
                                    (x + adx * (shaft + asz), ya),
                                    "the cutting plane's sight arrow"))
            xr = sorted((x, x + adx * (shaft + asz)))
            layout.claim((xr[0], ya - band, xr[1], ya + band),
                         f"cutarrow-{label}", "line")
            ink = mw.mtext_ink(tpos, [label], h, attach=attach)
            layout.claim((min(xr[0], ink[0]), min(ink[1], ya - asz / 2),
                          max(xr[1], ink[2]), max(ink[3], ya + asz / 2)),
                         f"cutlabel-{label}")
    return recs, segs, arrows


def section_hatches(ctx, sec_job, s_off):
    """One HATCH per material region: outer loop + hole islands, ANSI31.
    Nesting is resolved in fc_project (Shape.slice + containment depth)."""
    ox, oy = s_off
    recs = []
    for g in sec_job.get("hatch_groups", []):
        shifted = [[(x + ox, y + oy) for x, y in g["outer"]]]
        shifted += [[(x + ox, y + oy) for x, y in hole]
                    for hole in g["holes"]]
        # ANSI31 spacing is 0.125 units (inch-based pattern): x25.4 gives
        # 3.175 mm printed spacing at any sheet scale
        recs.append(mw.hatch(shifted, "ANSI31", scale=ctx.mm(25.4)))
    return recs


def prim_segments(prims, off=(0.0, 0.0)):
    """Projected primitives -> flat list of world-space segments."""
    ox, oy = off
    segs = []
    for pr in prims:
        t = pr["t"]
        if t == "seg":
            segs.append(((pr["a"][0] + ox, pr["a"][1] + oy),
                         (pr["b"][0] + ox, pr["b"][1] + oy)))
        elif t == "poly":
            pts = [(x + ox, y + oy) for x, y in pr["pts"]]
            segs += list(zip(pts, pts[1:]))
        elif t in ("circle", "arc"):
            a0, a1 = (0.0, 2 * math.pi) if t == "circle" \
                else (pr["a0"], pr["a1"])
            n = max(8, int(abs(a1 - a0) * pr["r"] / 2.0))
            pts = [(pr["c"][0] + ox + pr["r"] * math.cos(a0 + (a1 - a0) * i / n),
                    pr["c"][1] + oy + pr["r"] * math.sin(a0 + (a1 - a0) * i / n))
                   for i in range(n + 1)]
            segs += list(zip(pts, pts[1:]))
    return segs


def _cells_of(segs, cell):
    """Grid cells any of these segments passes through (walked, not bboxed)."""
    out = set()
    for a, b in segs:
        n = max(1, int(math.hypot(b[0] - a[0], b[1] - a[1]) / (cell * 0.5)))
        for i in range(n + 1):
            f = i / n
            out.add((int((a[0] + (b[0] - a[0]) * f) // cell),
                     int((a[1] + (b[1] - a[1]) * f) // cell)))
    return out


def balloon_landing(ctx, asm_job, off, k, knee):
    """Where balloon k's arrow should land: a point on ASSEMBLY-VISIBLE ink
    that lies on instance k's own projected edges and on NO other instance's
    — so the terminator sits on an edge that belongs to that part alone —
    snapped exactly onto that edge, and chosen nearest the knee so the
    leader is short. Falls back to the instance centre if the parts share
    every visible edge. (Round-7 DEF-C: landing on the projected bbox centre
    left both arrows floating in the bore region, reading as transposed.)"""
    insts = asm_job.get("instances", [])
    own = prim_segments(insts[k].get("edges", []), off)
    if not own:
        c = insts[k]["center"]
        return (c[0] + off[0], c[1] + off[1])
    cell = ctx.mm(0.8)
    own_cells = _cells_of(own, cell)
    other_cells = set()
    for j, inst in enumerate(insts):
        if j != k:
            other_cells |= _cells_of(prim_segments(inst.get("edges", []),
                                                   off), cell)
    best, bestd = None, None
    for a, b in prim_segments(asm_job["visible"], off):
        n = max(1, int(math.hypot(b[0] - a[0], b[1] - a[1]) / cell))
        for i in range(n + 1):
            f = i / n
            p = (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)
            c = (int(p[0] // cell), int(p[1] // cell))
            if c not in own_cells:
                continue
            if any((c[0] + dx, c[1] + dy) in other_cells
                   for dx in (-1, 0, 1) for dy in (-1, 0, 1)):
                continue                    # shared/coincident edge
            d = math.hypot(p[0] - knee[0], p[1] - knee[1])
            if bestd is None or d < bestd:
                best, bestd = p, d
    if best is None:
        c = insts[k]["center"]
        return (c[0] + off[0], c[1] + off[1])
    # snap exactly onto the owning edge
    snap, sd = best, None
    for a, b in own:
        vx, vy = b[0] - a[0], b[1] - a[1]
        L2 = vx * vx + vy * vy
        t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((best[0] - a[0]) * vx
                                                   + (best[1] - a[1]) * vy) / L2))
        q = (a[0] + t * vx, a[1] + t * vy)
        d = math.hypot(q[0] - best[0], q[1] - best[1])
        if sd is None or d < sd:
            snap, sd = q, d
    return snap


BALLOON_TEXT_MM = 3.5            # P.NO. height; the circle is 1.4 x it


def balloon_pitch(ctx, dense=False):
    """Centre-to-centre of two balloons in one column. A SPLIT fan packs
    them closer — 2.5 mm apart instead of 4 — which is ordinary practice
    on a crowded assembly and is what lets two columns hold a set that one
    cannot."""
    brad = 1.4 * ctx.mm(BALLOON_TEXT_MM)
    return 2 * brad + ctx.mm(2.5 if dense else 4.0)


def balloon_capacity(ctx, top, floor, dense=False):
    """How many balloons one column can hold between `top` and `floor`.

    The first sits AT `top` and each one after it a pitch lower, so the
    last one's circle has to clear the floor by its own radius."""
    brad = 1.4 * ctx.mm(BALLOON_TEXT_MM)
    room = top - floor - brad
    if room < 0:
        return 0
    return int(room / balloon_pitch(ctx, dense)) + 1


def _rebalance(sides, order, capacity):
    """Move the balloons nearest the view's CENTRE to the other column
    when their own side is full. A part out at the edge keeps the side it
    is on — its leader would otherwise cross the view — while one near the
    middle reads as well either way, so that is what gives."""
    per = {"left": [], "right": []}
    for key in sides:
        per[sides[key]].append(key)
    for full, other in (("left", "right"), ("right", "left")):
        while len(per[full]) > capacity and len(per[other]) < capacity:
            # the one whose part sits closest to the centre line
            key = min(per[full], key=lambda k: abs(order[k]))
            per[full].remove(key)
            per[other].append(key)
            sides[key] = other
    return sides


def balloon_sides(ctx, views, offs, by_view, capacity, columns):
    """{(view, instance): "left"|"right"} when ONE column cannot hold them
    all, else {} (one column, as a sheet with few part numbers has always
    had).

    26 part numbers need about 360 mm of column and A3 landscape has 290,
    so the fan splits in two, one each side of the view. Each balloon goes
    on the side ITS OWN PART sits on, so its leader never crosses the view
    to reach a column — and since each side is then fanned top-down by
    where its arrow lands, leaders cannot cross inside a column either.
    """
    total = sum(len(v) for v in by_view.values())
    if total <= capacity:
        return {}
    sides, offset = {}, {}
    for view, items in by_view.items():
        job, off = views[view], offs[view]
        top = job["bounds"][1][1] + off[1]
        for k, _pid in items:
            # where the arrow would really land from each column — through
            # the same function that will place it — and how far it would
            # then have to reach
            reach = {sd: abs(balloon_landing(ctx, job, off, k,
                                             (kx, top))[0] - kx)
                     for sd, kx in columns.items()}
            sides[(view, k)] = min(reach, key=lambda sd: reach[sd])
            offset[(view, k)] = reach["left"] - reach["right"]
    return _rebalance(sides, offset, capacity)


def _uncross(order, tip_of, kx, start, pitch, rounds=8):
    """Exchange neighbours in a fan whose leaders would cross.

    The knee of position i is fixed — `start - i * pitch` — so a crossing
    between two neighbours is removed by swapping which balloon sits
    where, and repeated passes settle the whole column. Ordering by the
    angle from the fan's middle gets almost every pair right; this is
    what settles the ones where a landing's height and its distance from
    the column disagree."""
    n = len(order)
    for _ in range(rounds):
        swapped = False
        # EVERY pair, not only neighbours: two leaders that cross can sit
        # several knees apart with no crossing pair between them, and a
        # neighbour sweep never reaches them (issue #18)
        for i in range(n - 1):
            for j in range(i + 1, n):
                a, b = order[i], order[j]
                ka = (kx, start - i * pitch)
                kb = (kx, start - j * pitch)
                if _proper_crossing(ka, tip_of[a], kb, tip_of[b], 0.0):
                    order[i], order[j] = b, a
                    swapped = True
        if not swapped:
            break
    return order


def view_ink(views):
    """[(name, job, segments)] for the views a leader must not cross —
    their drawn ink in sheet coordinates, measured once."""
    return [(name, job, prim_segments(job["visible"], off))
            for name, job, off in views]


def other_ink(layout):
    """The sheet's OTHER ink, in the shape view_ink returns: the
    dimension lines, the cutting plane's sight arrows and the pictorial
    box. A balloon leader has no more business crossing these than
    crossing another view (issue #25); each gets its own sentinel owner,
    so nothing can mistake it for the view a balloon points into."""
    groups: dict = {}
    for a, b, what in list(layout.dim_lines) + list(layout.keep_off):
        groups.setdefault(what if "dimension" not in what
                          else "a dimension line", []).append((a, b))
    return [(what, object(), segs) for what, segs in groups.items()]


def crosses_other_view(tip, knee, inks, own_job):
    """The name of a view this leader would cross on its way, other than
    the one it points into (issue #20).

    A balloon that reaches its part across the section points at it
    through a view that says nothing about it, and the reader has to
    follow a line over the hatch to see where it lands."""
    lo = (min(tip[0], knee[0]), min(tip[1], knee[1]),
          max(tip[0], knee[0]), max(tip[1], knee[1]))
    for name, job, segs in inks:
        if job is own_job:
            continue
        for a, b in segs:
            if max(a[0], b[0]) < lo[0] or min(a[0], b[0]) > lo[2] \
                    or max(a[1], b[1]) < lo[1] or min(a[1], b[1]) > lo[3]:
                continue
            if _segs_cross(tip, knee, a, b, 0.0):
                return name
    return None


def _order_against_ink(order, tip_of, kx, start, pitch, job_of, inks,
                       rounds=6):
    """The fan's order, settled against the sheet's OTHER ink as well as
    against itself (issue #25).

    Each balloon's knee is its place in the fan, so which balloon sits
    where decides both what crosses what and what runs over a dimension
    line. The two cannot be decided one after the other — stepping round
    a dimension moves a balloon out of the fan, and out of the fan it
    crosses its neighbours. So: tabulate, for every balloon and every
    knee, whether the leader would cross ink that is not its own, then
    improve the order by exchanging pairs while that helps. A crossing
    leader counts double: it says something false about the part, where
    a leader over a dimension line is only hard to read."""
    n = len(order)
    knees = [(kx, start - i * pitch) for i in range(n)]
    over = [[bool(crosses_other_view(tip_of[b], knees[s], inks, job_of[b]))
             for s in range(n)] for b in order]
    idx = {b: i for i, b in enumerate(order)}

    def cost(seq):
        c = sum(1 for s, b in enumerate(seq) if over[idx[b]][s])
        for i in range(n - 1):
            for j in range(i + 1, n):
                if _proper_crossing(knees[i], tip_of[seq[i]],
                                    knees[j], tip_of[seq[j]], 0.0):
                    c += 10
        return c

    best, best_c = list(order), cost(order)
    for _ in range(rounds):
        moved = False
        for i in range(n - 1):
            for j in range(i + 1, n):
                trial = list(best)
                trial[i], trial[j] = trial[j], trial[i]
                c = cost(trial)
                if c < best_c:
                    best, best_c, moved = trial, c, True
        if not moved:
            break
    return best


def balloons_fan(ctx, layout, asm_job, off, items, start_y=None,
                 knee_x=None, side="right", dense=False, inks=(),
                 other=(), in_the_way=None):
    """P.NO. leader callouts fanned into a band through the collision
    layout. Each arrow lands on an edge that belongs to that instance
    alone (balloon_landing).

    `items` is [(instance index, P.NO. as drawn)] for ONE view, or
    [(job, off, instance index, P.NO.)] for several — because every
    view's balloons share one column, and two views ordered one after the
    other hand out their knees in two runs, which is where the leaders
    cross between them (issue #18). Ordered as ONE fan they cannot.
    The caller decides WHICH instance carries each part number, because a
    part number is ballooned once on the whole sheet, in the view where it
    reads best (balloon_plan). `start_y` continues a fan begun elsewhere
    and `knee_x` pins the column. `side` puts that column left of the
    view instead, for the other half of a split fan (balloon_sides) — the
    balloon then sits left of its knee, so the leader still meets it from
    the view.

    `inks` is every view's drawn ink (view_ink): a knee whose leader would
    cross a view the balloon does not point into is passed over for the
    next one down the column, and when none is left the balloon says
    which view is in the way and records it in `in_the_way`, so the
    caller can balloon that part in THAT view instead (issue #20).

    `other` is the sheet's remaining ink — the dimension lines, the
    cutting plane's arrows, the pictorial box. A balloon does not step
    down the column for those: stepping moves it out of the fan, and out
    of the fan it crosses the leaders it was ordered against. They are
    settled in the ORDER instead (issue #25)."""
    in_the_way = {} if in_the_way is None else in_the_way
    if asm_job is None:
        ents = [(j, o, k, pid) for j, o, k, pid in items]
    else:
        ents = [(asm_job, off, k, pid) for k, pid in items]
    if not ents:
        return [], start_y
    recs = []
    h = ctx.mm(BALLOON_TEXT_MM)
    brad = 1.4 * h                       # balloon circle radius
    sgn = -1.0 if side == "left" else 1.0
    lo_x = min(j["bounds"][0][0] + o[0] for j, o, _k, _p in ents)
    hi_x = max(j["bounds"][1][0] + o[0] for j, o, _k, _p in ents)
    hi_y = max(j["bounds"][1][1] + o[1] for j, o, _k, _p in ents)
    if knee_x is not None:
        kx = knee_x
    elif sgn > 0:
        kx = hi_x + ctx.mm(16.0)
    else:
        kx = lo_x - ctx.mm(16.0)
    # rank by where each arrow will LAND, not by instance centre: knees are
    # handed out top-down, so any disagreement between the two orders makes
    # the leaders cross. Probe the landings from one common reference knee
    # to break the knee<->landing circularity.
    start = start_y if start_y is not None else hi_y
    # the fan radiates from the MIDDLE of the column it will occupy, not
    # from its top: angles measured at the top make the order of two
    # landings at similar heights turn on a rounding, and the leaders
    # then cross
    probe = (kx, start - (len(ents) - 1)
             * balloon_pitch(ctx, dense) / 2.0)

    def _fan_key(i):
        """Where this arrow lands, as SEEN from the column.

        Knees are handed out top-down, so the order has to be the order
        the landings appear from the column — a radial fan, which cannot
        cross itself. Ranking by the landing's height alone says nothing
        when the parts are spread ALONG the assembly instead of up it,
        which is exactly when the fan has to be split (two columns), so
        that is where the angle is used."""
        job, o, k, _pid = ents[i]
        p = balloon_landing(ctx, job, o, k, probe)
        if not dense:
            return -p[1]
        # the x term points INWARD, away from the column, for both sides:
        # a right-hand column looks left and a left-hand one looks right
        return -math.atan2(p[1] - probe[1], -sgn * (p[0] - kx) or 1e-9)

    ys = sorted(range(len(ents)), key=_fan_key)
    # a split fan FIXES each arrow where the fan was ordered from: the
    # landing is otherwise recomputed for every candidate knee and slides
    # along the part's own edge, so two leaders ordered apart end up
    # crossing after all
    fixed = {i: balloon_landing(ctx, ents[i][0], ents[i][1], ents[i][2],
                                probe) for i in ys} if dense else {}
    if dense:
        ys = _uncross(ys, fixed, kx, start, balloon_pitch(ctx, dense))
        if inks or other:
            # and settled against the sheet's other ink: a fan ordered
            # only against itself sends its balloons over the dimension
            # lines and the pictorial, and one that steps round those as
            # it places them ends up crossing the leaders it was ordered
            # against. Both are decided here, before a knee is taken
            # (issue #25).
            ys = _order_against_ink(ys, fixed, kx, start,
                                    balloon_pitch(ctx, dense),
                                    [ents[i][0] for i in range(len(ents))],
                                    list(inks) + list(other))
    next_y = hi_y if start_y is None else start_y
    for rank, i in enumerate(ys):
        job, o, k, pid = ents[i]
        placed = None
        blocked = None
        for dy in [4.0 * step for step in range(0, 25)]:
            ky = next_y - ctx.mm(dy)
            tip = fixed.get(i) or balloon_landing(ctx, job, o, k, (kx, ky))
            across = crosses_other_view(tip, (kx, ky), inks, job) \
                if inks else None
            if across:
                # this knee would reach the part across another VIEW: step
                # down the column, and when the column runs out say which
                # view is in the way, so the caller can balloon the part
                # there instead (issue #20). The sheet's other ink is not
                # stepped round — it is settled in the fan's order
                blocked = across
                continue
            if inks and leader_runs_along(
                    ctx, [sg for _n, _j, sgs in inks for sg in sgs],
                    tip, (kx, ky)) > COLLINEAR_OK:
                # ... or lie along the outline it lands on, where the
                # arrow cannot be told from the part (issue #18)
                continue
            r = layout.place([(kx if sgn > 0 else kx - 2 * brad,
                               ky - brad)], 2 * brad, 2 * brad,
                             f"balloon-{pid}-{rank}")
            if r is None:
                layout.unresolved.pop()
            if r is not None:
                # leader with empty text (arrow + line), then the circled
                # item number — standard balloon style
                recs.append(mw.leader(tip, (kx, ky), "", h, props=mw.P_ANNO))
                _claim_leader_ink(ctx, layout, tip, (kx, ky),
                                  f"balloon-{pid}-{rank}")
                recs.append(mw.circle((kx + sgn * brad, ky), brad,
                                      mw.P_ANNO))
                recs.append(mw.cell_text((kx + sgn * brad, ky), pid, h,
                                         mw.P_ANNO))
                next_y = ky - 2 * brad - ctx.mm(2.5 if dense else 4.0)
                placed = r
                break
        if placed is None:
            layout.unresolved.append(
                f"balloon-{pid}-{rank}" + (
                    f": every knee left on this column would cross the "
                    f"{blocked} view to reach the part (issue #20) — "
                    "balloon it in that view instead (`balloon_in`), or "
                    "give the sheet a column its leader can reach"
                    if blocked else ""))
            if blocked:
                in_the_way[pid] = blocked
    return recs, next_y


# ------------------------------------------------- assembly section hatch
# ISO 128-50: in an assembly section every cut part is hatched separately,
# and parts that touch must be told apart — by hatch ANGLE first, then by
# SPACING. Six styles are enough for any part that touches five others; the
# assignment is a graph colouring over the parts that actually meet in the
# cut, so the choice is about the drawing, not about part numbering.
HATCH_STYLES = ((0.0, 1.0), (math.pi / 2, 1.0), (0.0, 1.6),
                (math.pi / 2, 1.6), (0.0, 2.4), (math.pi / 2, 2.4))
# A cut region thinner than this (printed) is filled solid instead of
# hatched: hatch lines in a 0.8 mm band read as a smear (ISO 128-50 3.4).
THIN_SOLID_MM = 1.0


def _loop_rect(loop, off=(0.0, 0.0)):
    xs = [q[0] + off[0] for q in loop]
    ys = [q[1] + off[1] for q in loop]
    return (min(xs), min(ys), max(xs), max(ys))


def hatch_styles_by_instance(groups, clearance):
    """A hatch style index per instance, so no two parts that MEET in the
    section share one. Greedy colouring, most-constrained part first, by
    name on ties — the same model always draws the same way."""
    rects = {}
    for g in groups:
        rects.setdefault(g.get("instance", ""), []).append(
            _loop_rect(g["outer"]))
    names = sorted(rects)
    adj = {n: set() for n in names}
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if any(not (ra[2] + clearance < rb[0] or rb[2] + clearance < ra[0]
                        or ra[3] + clearance < rb[1]
                        or rb[3] + clearance < ra[1])
                   for ra in rects[a] for rb in rects[b]):
                adj[a].add(b)
                adj[b].add(a)
    colour = {}
    for n in sorted(names, key=lambda x: (-len(adj[x]), x)):
        used = {colour[o] for o in adj[n] if o in colour}
        colour[n] = next(k for k in range(len(HATCH_STYLES) * 2)
                         if k % len(HATCH_STYLES) not in used
                         or k >= len(HATCH_STYLES)) % len(HATCH_STYLES)
    return colour


def assembly_hatches(ctx, sec_job, s_off):
    """Section hatch for an ASSEMBLY: one hatch per cut part, neighbours
    distinguished, very thin cuts filled solid. Returns (records, report)."""
    groups = sec_job.get("hatch_groups", [])
    if not groups:
        return [], {}
    ox, oy = s_off
    styles = hatch_styles_by_instance(groups, ctx.mm(0.5))
    thin = ctx.mm(THIN_SOLID_MM)
    recs, solid = [], []
    for g in groups:
        loops = [[(x + ox, y + oy) for x, y in g["outer"]]]
        loops += [[(x + ox, y + oy) for x, y in hole]
                  for hole in g.get("holes", [])]
        r = _loop_rect(g["outer"])
        if min(r[2] - r[0], r[3] - r[1]) < thin:
            recs.append(mw.hatch(loops, "SOLID"))
            solid.append(g.get("instance", "?"))
            continue
        ang, spacing = HATCH_STYLES[styles.get(g.get("instance", ""), 0)]
        # ANSI31 spacing is 0.125 units (inch pattern): x25.4 prints at
        # 3.175 mm whatever the sheet scale
        recs.append(mw.hatch(loops, "ANSI31", scale=ctx.mm(25.4) * spacing,
                             angle=ang))
    return recs, {"hatch_styles": {k: v for k, v in sorted(styles.items())},
                  "solid_filled": sorted(set(solid))}


# ------------------------------------------------------ assembly balloons

def balloon_plan(m, views, id_by_path):
    """Which view balloons each part number — one balloon per P.NO. on the
    whole sheet (issue #5).

    A part is ballooned in the view where it reads best: the manifest's
    `balloon_in` if it says, else the detail view that lists it (small
    parts are put in a detail precisely so they can be ballooned there),
    else the section when the part is cut there, else the main view.
    """
    asm = m.get("assembly_sheet") or {}
    forced = {str(k): v for k, v in (asm.get("balloon_in") or {}).items()}
    in_detail = {}
    for d in asm.get("detail_views") or []:
        for pid in d.get("balloons") or []:
            in_detail[str(pid)] = f"detail:{d.get('label', 'B')}"
    plan = {}
    for part in m["parts"]:
        pid = str(part["part_id"])
        want = forced.get(pid) or in_detail.get(pid)
        order = ([want] if want else []) + ["front", "section"]
        for view in order:
            k = instance_index(views.get(view), part["part_id"], id_by_path)
            if k is not None:
                plan.setdefault(pid, (view, k))
                break
    return plan


def instance_index(job, part_id, id_by_path):
    """Which instance of a view is this part number, or None."""
    if job is None:
        return None
    for k, inst in enumerate(job.get("instances", [])):
        if id_by_path.get(inst["name"]) == part_id:
            return k
    return None


# -------------------------------------------------------- parts table
# The table grows upward from the title block in rows of PARTS_ROW_H. It
# shares the drawing area with the views, so a long one has to give way —
# in the order the user asked for: group the small bought-out hardware into
# one row per kind, then continue onto a second sheet. Text is NEVER shrunk
# below the template's own floor (musa_writer.PARTS_TEXT_FLOOR).
TABLE_AREA_FRAC = 0.55      # most of the drawing area a table may occupy


COMMON_GROUP = "COMMON TO SUB-ASSEMBLIES"
TOP_GROUP = "TOP LEVEL"


def _top_labels(analysis) -> dict:
    # an analysis written before v0.5.0 has names, not paths
    return {(n.get("path") or n.get("name", "")):
            n.get("label") or n.get("path") or n.get("name", "")
            for n in fc_tree.flatten(analysis["assembly"].get("instances")
                                     or [])}


def group_usage(part, analysis) -> dict:
    """{sub-assembly label: how many of this part are used under it}.

    Counted once per INSTANCE PATH, so a part used four times inside one
    sub-assembly counts four there (not once in four groups, which is how
    an O-ring with qty 4 came out as qty 16 — issue #9), and the totals
    add up to part_usage's own qty. "" is the top level."""
    usage = analysis["assembly"].get("part_usage") or {}
    paths = (usage.get(part["file"]) or {}).get("used_in") or []
    if not paths:
        return {}
    labels = _top_labels(analysis)
    out: dict = {}
    for path in paths:
        head = path.split("/")[0]
        g = "" if head == path else labels.get(head, head)
        out[g] = out.get(g, 0) + 1
    return out


def sub_assembly_qty(analysis) -> dict:
    """{label: how many times that top-level sub-assembly is used} — the
    quantity a heading row states. It is the sub-assembly's OWN count, not
    the number of parts or instances under it (issue #9)."""
    out: dict = {}
    for n in fc_tree.flatten(analysis["assembly"]["instances"]):
        path = n.get("path") or n.get("name", "")
        if "/" in path or n.get("kind") != "assembly":
            continue
        label = n.get("label", path)
        out[label] = out.get(label, 0) + 1
    return out


def _group_of(part, analysis, m):
    """The sub-assembly labels a part is used under (one entry per use)."""
    counts = group_usage(part, analysis)
    return [g for g, n in counts.items() if g for _ in range(n)]


def hardware_kind(part):
    """What makes two bought-out items 'the same kind' for one table row."""
    return (part.get("description", ""), part.get("material", ""),
            part.get("drg_no", ""))


def parts_table_plan(m, analysis, max_rows, group_by="none",
                     group_hardware=None, total=None):
    """Rows for the parts table, with the fallbacks the sheet may need.

    Returns (rows, report). Each row is a dict: kind "head" (a sub-assembly
    heading), "part" (one part), "group" (several bought-out items of one
    kind) or "total". `report` names the fallback actually used, so /draw
    can say so rather than quietly dropping something.

    Everything here is OPT-IN: with the defaults the table is exactly what
    it has always been, one row per manifest part in manifest order, so an
    existing sheet redraws byte for byte.
    """
    parts = list(m["parts"])
    report = {"fallbacks": [], "rows": 0, "sheets": 1}
    head_qty = sub_assembly_qty(analysis) if group_by == "sub_assembly" \
        else {}

    def qty_of(part):
        """How many of this part the ASSEMBLY uses — once, from part_usage,
        whatever grouping the table ends up with (issue #9). The manifest's
        own count stands in when the analysis predates part_usage."""
        counts = group_usage(part, analysis)
        return sum(counts.values()) if counts else part.get("qty", 1)

    def home_of(part):
        """The ONE section this part's row belongs in. A part used under
        several sub-assemblies is listed once, in a common section: a row
        per (part, sub-assembly) would count it twice over."""
        if group_by != "sub_assembly":
            return ""
        named = [g for g in group_usage(part, analysis) if g]
        if not named:
            return TOP_GROUP
        if len(named) == 1 and len(group_usage(part, analysis)) == 1:
            return named[0]
        return COMMON_GROUP

    def build(group_hw):
        rows = []
        groups: dict = {}
        for part in parts:
            groups.setdefault(home_of(part), []).append(part)
        # named sub-assemblies first, then the parts no single one owns;
        # every row sits under a heading that says what it belongs to
        order = sorted(g for g in groups if g not in ("", COMMON_GROUP,
                                                      TOP_GROUP))
        order += [g for g in (COMMON_GROUP, TOP_GROUP, "") if g in groups]
        for name in order:
            members = groups[name]
            if name and (len(order) > 1 or name not in (COMMON_GROUP,
                                                        TOP_GROUP)):
                rows.append({"kind": "head", "text": name,
                             "qty": head_qty.get(name)})
            hw, rest = [], []
            for part in members:
                (hw if group_hw and not part.get("sheet", True)
                 else rest).append(part)
            for part in rest:
                rows.append({"kind": "part", "part": part,
                             "qty": qty_of(part)})
            kinds: dict = {}
            for part in hw:
                kinds.setdefault(hardware_kind(part), []).append(part)
            for kind, members_hw in kinds.items():
                if len(members_hw) == 1:
                    # one P.NO. of a kind is just that part's own row —
                    # "1 ITEMS" against a single number says nothing
                    rows.append({"kind": "part", "part": members_hw[0],
                                 "qty": qty_of(members_hw[0])})
                    continue
                rows.append({"kind": "group", "parts": members_hw,
                             "qty": sum(qty_of(p) for p in members_hw)})
        return rows

    rows = build(bool(group_hardware))
    if len(rows) > max_rows and group_hardware is None:
        # fallback 1: one row per kind of bought-out hardware
        grouped = build(True)
        if len(grouped) < len(rows):
            rows = grouped
            report["fallbacks"].append("grouped bought-out hardware by kind")
    # a TOTAL row belongs to a GROUPED table ("quantities per group and in
    # total"); an ungrouped one is the plain list it has always been
    if total or (total is None and any(r["kind"] == "head" for r in rows)):
        # the sum of the rows, which is the sum of part_usage: counting the
        # manifest again here is how a grouped table and its TOTAL could
        # disagree (issue #9)
        rows.append({"kind": "total",
                     "qty": sum(r.get("qty") or 0 for r in rows
                                if r["kind"] in ("part", "group"))})
    if len(rows) > max_rows:
        # fallback 2: the rest goes on a continuation sheet
        report["fallbacks"].append(
            f"continuation sheet ({len(rows) - max_rows} rows of "
            f"{len(rows)} moved to sheet 2)")
        report["sheets"] = 2
    report["rows"] = len(rows)
    return rows, report


def parts_row_cells(row, analysis, m):
    """The eight title-block cells for one planned row."""
    if row["kind"] == "head":
        # a heading carries no item number, and its QTY is the
        # SUB-ASSEMBLY's own quantity — blank where it has none, as the
        # common and top-level sections do (issue #9)
        qty = row.get("qty")
        return {"pno": "", "description": row["text"],
                "qty": "" if qty is None else str(qty),
                "drg_no": "", "material": "", "size": "", "weight": "",
                "remarks": "SUB-ASSEMBLY" if qty is not None else ""}
    if row["kind"] == "total":
        return {"pno": "", "description": "TOTAL", "qty": str(row["qty"]),
                "drg_no": "", "material": "", "size": "", "weight": "",
                "remarks": ""}
    if row["kind"] == "group":
        # one entry per P.NO., however many of each the assembly uses:
        # repeating a number once per instance printed "17, 17, 17, 17"
        # against a quantity counted twice over (issue #9)
        seen, nums = set(), []
        for p in row["parts"]:
            n = disp_no(p["part_id"])
            if n not in seen:
                seen.add(n)
                nums.append(n)
        first = row["parts"][0]
        return {"pno": ", ".join(nums),
                "description": first["description"], "qty": str(row["qty"]),
                "drg_no": first["drg_no"], "material": first["material"],
                "size": "-", "weight": "-",
                "remarks": f"{len(nums)} ITEMS"}
    part = row["part"]
    wkg = part_weight_kg(analysis, part)
    qty = row.get("qty")
    return {"pno": disp_no(part["part_id"]),
            "description": part["description"],
            "qty": str(part["qty"] if qty is None else qty),
            "drg_no": part["drg_no"], "material": part["material"],
            "size": "-", "weight": weight_label(wkg), "remarks": "-"}


# ----------------------------------------------------------- title block

def titleblock_replacements(m, sheet):
    # Each DRG NO. is an independent document: SHEET 1 OF 1 unless a part is
    # split across sheets under one DRG NO. (manifest "sheet_of": [i, n]).
    # NEVER numbered across the print set.
    num, den = parse_scale(sheet["scale"])
    label = sheet.get("scale_label") or f"{num}:{den}"
    si, sn = sheet.get("sheet_of", [1, 1])
    return {
        "SCALE : 1:1": f"SCALE : {label}",
        "SHEET 9": f"SHEET {si} OF {sn}",
        "00": sheet.get("rev", m.get("rev", "00")),
        "DESCRIPTION: \nCONNECTING CHAMBER":
            f"DESCRIPTION: \n{sheet['description']}",
        "DRG. NO.     \nRES-GB-350T-CC-01":
            f"DRG. NO.     \n{sheet['drg_no']}",
    }


# ---------------------------------------------------------------- sheets

def disp_no(pid) -> str:
    """P.NO. display form: no leading zeros (stroke font slashes zeros,
    "02" would read as a diameter callout)."""
    try:
        return str(int(str(pid)))
    except ValueError:
        return str(pid)



# revision-table cell columns (REV, DESCRIPTION, REVISED BY, APPROVED BY,
# DATE) and the three row baselines of the template's "-" placeholders
REV_COLS = (80.72697303959285, 281.445892556294, 557.502500627168,
            646.231785341799, 734.961070056459)
REV_ROW_YS = (1587.9783693373156, 1559.5849982286345, 1531.1916271199552)


def rev_row_positional(m=None, sheet=None, ctx=None):
    """Fill revision-table rows from the sheet's "revisions" (falling back
    to the manifest-level list); default: one INITIAL RELEASE row. The
    revision table hugs the sheet's right edge, so on a wider paper its
    columns travel with it (SheetContext.fx)."""
    from common import today
    fx = ctx.fx if ctx is not None else (lambda x: x)
    revs = ((sheet or {}).get("revisions")
            or (m or {}).get("revisions")) or [
        {"rev": "00", "desc": "INITIAL RELEASE", "by": "Er. P. PRANAY",
         "date": None}]
    out = []
    for row_y, rv in zip(REV_ROW_YS, revs[:len(REV_ROW_YS)]):
        vals = (rv.get("rev", "-"), rv.get("desc", "-"),
                rv.get("by", "Er. P. PRANAY"), rv.get("approved", "-"),
                rv.get("date") or today())
        for x, val in zip(REV_COLS, vals):
            out.append({"content": "-", "x": fx(x), "y": row_y, "new": val})
    return out


# Densities in g/cm3, matched WORD-WISE against the manifest MATERIAL string
# (longest key wins, so "MILD STEEL" beats "STEEL" and "STAINLESS" beats "SS").
# A part may override outright with "density_g_cm3".
DENSITY_G_CM3 = {
    "ACRYLIC": 1.19, "PMMA": 1.19, "PERSPEX": 1.19,
    "MILD STEEL": 7.85, "STEEL": 7.85, "MS": 7.85, "EN8": 7.85, "EN24": 7.85,
    "STAINLESS": 7.90, "SS": 7.90, "SS304": 7.90, "SS316": 8.00,
    "CAST IRON": 7.20,
    "ALUMINIUM": 2.70, "ALUMINUM": 2.70, "AL": 2.70,
    "BRASS": 8.50, "BRONZE": 8.80, "COPPER": 8.96,
    "NYLON": 1.14, "PA6": 1.14, "PTFE": 2.20, "TEFLON": 2.20,
    "POLYPROPYLENE": 0.90, "PP": 0.90, "HDPE": 0.95, "UHMWPE": 0.93,
    "POLYCARBONATE": 1.20, "PC": 1.20, "DELRIN": 1.41, "POM": 1.41,
    "PVC": 1.40, "PVDF": 1.78, "EPOXY": 1.20, "RUBBER": 1.20,
}
DEFAULT_DENSITY_G_CM3 = 7.85    # mild steel — the pipeline's original default


def material_density(material: str | None) -> float:
    """g/cm3 for a title-block MATERIAL string; mild steel if unrecognised."""
    s = (material or "").upper()
    best = None
    for key, rho in DENSITY_G_CM3.items():
        if re.search(r"\b" + re.escape(key) + r"\b", s):
            if best is None or len(key) > len(best[0]):
                best = (key, rho)
    return best[1] if best else DEFAULT_DENSITY_G_CM3


def part_weight_kg(analysis, part):
    """Solid volume x the density implied by the part's MATERIAL. Steel was
    hardcoded until acrylic showed up and a 0.80 kg lid printed as 5.26 kg."""
    if part.get("density_g_cm3") == 0:
        # explicit "unknown": a bought-out item whose real mass we do not
        # know. Printing a figure derived from a schematic model would be
        # fabricated data — the WEIGHT cell shows "-" instead.
        return None
    for a in analysis["parts"]:
        if a["file"] == part["file"]:
            rho = part.get("density_g_cm3") or \
                material_density(part.get("material"))
            return a["bodies"][0]["volume_mm3"] * rho * 1e-6
    return None


def weight_label(wkg) -> str:
    """WEIGHT cell text. Small hardware rounds to 0.00 kg, which reads as
    "not weighed" — drop to grams rather than print a zero."""
    if not wkg:
        return "-"
    if wkg < 0.01:
        return f"{wkg * 1000.0:.2f} g".replace(".00 g", " g")
    return f"{wkg:.2f} kg"


def _claim_view(layout, job, off, name):
    mn, mx = job["bounds"]
    layout.claim((mn[0] + off[0], mn[1] + off[1],
                  mx[0] + off[0], mx[1] + off[1]), name, "view")


def _view_label(ctx, layout, job, off, textstr, standoffs=None,
                shifts=(0.0,)):
    """View caption, centered under the view. Placed AFTER that view's dims
    (reference style: the caption sits below the dimension band), so its
    stand-off ladder has to reach past a couple of dim rows."""
    mn, mx = job["bounds"]
    h = ctx.mm(4.0)
    tw = mw.text_w(textstr, h)
    cx = (mn[0] + mx[0]) / 2 + off[0]
    cands = [(cx - tw / 2 + tw * f, mn[1] + off[1] - ctx.mm(d) - h)
             for d in (standoffs
                       or (8.0, 12.0, 16.0, 20.0, 24.0, 28.0, 32.0))
             for f in shifts]
    # fallback: beside the view (tight sheets where the below-band is full)
    cands += [(mx[0] + off[0] + ctx.mm(d),
               (mn[1] + mx[1]) / 2 + off[1] - h / 2)
              for d in (8.0, 14.0)]
    cands += [(mn[0] + off[0] - tw - ctx.mm(d),
               (mn[1] + mx[1]) / 2 + off[1] - h / 2)
              for d in (8.0, 14.0)]
    r = layout.place(cands, tw, h, f"label-{textstr}")
    if r is None:
        return []
    return [mw.mtext((r[0] + tw / 2, r[3]), textstr, h, attach=1)]



def shaded_png(stem: str) -> Path:
    """Where /shaded puts a sheet's pictorial (export.py stamps the same
    path). Read for its pixel aspect at draw time."""
    return SCRATCH_DIR / "shaded" / f"{stem}.png"


PICTORIAL_SIZES = ((42.0, 36.0), (36.0, 31.0), (30.0, 26.0))


def view_zone(ctx, part, view, job, off):
    """A view's rect grown by the dimension gutter each of its sides needs.
    The gutters are still EMPTY when the pictorial box is placed (dims come
    later), so anything reserving space up front has to respect them or it
    takes a dimension's only spot — that is how a slot-height dim and a
    depth dim went missing under the pictorial (round-8 QA)."""
    mn, mx = job["bounds"]
    return (mn[0] + off[0] - dim_gutter(ctx, part, view, "left"),
            mn[1] + off[1] - dim_gutter(ctx, part, view, "bottom"),
            mx[0] + off[0] + dim_gutter(ctx, part, view, "right"),
            mx[1] + off[1] + dim_gutter(ctx, part, view, "top"))


def pictorial_box(ctx, layout, png=None, zones=(), floor=None):
    """Reserved box for the shaded pictorial: top-right of the drawing area
    by preference, sliding left along the top band (then the bottom band)
    and shrinking through PICTORIAL_SIZES until it is clear of every view's
    outline AND dimension gutters (`zones`). The box frame is drawn; the
    interior stays EMPTY in the .musa (no raster records exist) — export.py
    stamps the PNG into it via musa_plot --stamp.

    The stamp rect LETTERBOXES: the largest rect of the image's own aspect
    (read from the PNG's IHDR, never assumed) that fits the box interior,
    centred in it. Stretching the image to the box would be a non-uniform
    scale, which this pipeline does not do — anywhere.
    Returns (records, stamp_rect)."""
    ax0, ay0, ax1, ay1 = ctx.draw_area
    pad, step = ctx.mm(2.0), ctx.mm(3.0)
    base_y = ay0 + pad if floor is None else floor + pad
    spot = None
    for w_mm, h_mm in PICTORIAL_SIZES:
        w, hh = ctx.mm(w_mm), ctx.mm(h_mm)
        rows = [ay1 - pad - hh, base_y]          # top band first, then bottom
        n = max(0, int((ax1 - pad - w - ax0 - pad) / step))
        for yy in rows:
            for k in range(n + 1):               # slide right -> left
                xx = ax1 - pad - w - k * step
                r = (xx - ctx.mm(1.0), yy - ctx.mm(1.0),
                     xx + w + ctx.mm(1.0), yy + hh + ctx.mm(1.0))
                if not layout.inside(r) or any(Layout._overlap(r, z, 0.0)
                                               for z in zones):
                    continue
                spot = (xx, yy, w, hh)
                break
            if spot:
                break
        if spot:
            break
    if spot is None:      # nowhere clear: keep the house corner, smallest box
        w, hh = ctx.mm(PICTORIAL_SIZES[-1][0]), ctx.mm(PICTORIAL_SIZES[-1][1])
        spot = (ax1 - pad - w, ay1 - pad - hh, w, hh)
    x0, y0, w, hh = spot
    x1, y1 = x0 + w, y0 + hh
    layout.claim((x0 - ctx.mm(1.0), y0 - ctx.mm(1.0),
                  x1 + ctx.mm(1.0), y1 + ctx.mm(1.0)),
                 "pictorial-box", "reserved")
    # the check reads the finished sheet and would otherwise take this
    # frame for a view of its own (issue #20)
    ctx._pictorial_box = (x0, y0, x1, y1)
    pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    for e0, e1 in zip(pts, pts[1:] + pts[:1]):
        layout.keep_off.append((e0, e1, "the pictorial box"))
    recs = [mw.polyline(pts, closed=True)]
    inset = ctx.mm(1.0)
    iw, ih = w - 2 * inset, hh - 2 * inset
    size = png_size(png) if png else None
    aspect = (size[0] / size[1]) if size else 1.0
    # box wider than the image -> height is the limit, and vice versa
    sw, sh = (ih * aspect, ih) if iw / ih > aspect else (iw, iw / aspect)
    return recs, (x0 + inset + (iw - sw) / 2, y0 + inset + (ih - sh) / 2,
                  sw, sh)


def content_balance(layout):
    """(left_clearance, right_clearance) of the DRAWN content inside the
    drawing area. The reserved boxes are excluded — they hug the frame by
    design. The notes block counts only when it sits in the sheet's
    ANNOTATION column (right of centre, where the callouts and FCF stacks
    live): there it is part of that column's mass, and ignoring it reported
    a sheet as centred while its margins were 23 mm and 2 mm (round-8 QA,
    sheet 04). A block banded across the bottom or hugging the opposite
    frame is placed there by design and would only drag the views off
    centre."""
    mid = (layout.area[0] + layout.area[2]) / 2.0
    xs = [(c[0], c[2]) for c in layout.claims
          if c[5] != "reserved"
          and (not c[4].startswith("notes-block")
               or (c[0] > mid and c[2] - c[0] > (layout.area[2]
                                                 - layout.area[0]) * 0.35))]
    if not xs:
        return 0.0, 0.0
    ax0, _, ax1, _ = layout.area
    return min(x[0] for x in xs) - ax0, ax1 - max(x[1] for x in xs)


def build_part_sheet(m, part, proj):
    """Compose the sheet, then re-compose it with the view group shifted so
    the content sits centred: the dimension/callout gutters are NOT
    symmetric (a left-hand height dim column vs a section on the right), so
    centring the view boxes alone leaves the sheet visibly lopsided
    (round-5 QA D3). Iterates on the measured imbalance and keeps the best
    fully-resolved pass."""
    reserve, co_order = None, None

    def score(p):
        u = p[2].unresolved
        # an annotation the sheet does not carry is the worst, and a
        # GD&T frame is the one with its own retry to come; after that,
        # an annotation the sheet carries BADLY (a leader with nowhere
        # clean to go, issue #18)
        return (len(u),
                sum(1 for x in u if x.startswith(("datum-", "fcf-"))),
                len(p[2].faults))

    best = _compose_part_sheet(m, part, proj)
    if any(u.startswith("callout-") for u in best[2].unresolved) \
            or best[2].faults:
        # a callout found no knee whose leader stays clear of the outlines
        # and of the leaders already down. Place them in the order their
        # arrows appear instead — a fan cannot cross itself (issue #18) —
        # one view at a time, since each view's knees are its own column,
        # and then across the sheet, which is the order to take when the
        # views' own leaders are what meet. Either pass is kept only if it
        # does better; a sheet whose callouts all placed keeps the order
        # the manifest gave them.
        for order in ("fan", "fan-across"):
            retry = _compose_part_sheet(m, part, proj, co_order=order)
            if score(retry) < score(best):
                best, co_order = retry, order
    if best[2].unresolved and any(u.startswith(("datum-", "fcf-"))
                                  for u in best[2].unresolved):  # noqa
        # A datum or a frame found no room on its own callout: give it one
        # by reserving the slot under that callout and re-composing, which
        # restacks the column around it (issue #7). Only then — a sheet
        # whose GD&T already placed keeps the layout it had.
        trial_reserve = gdt_reserve(best[0], part)
        retry = _compose_part_sheet(m, part, proj, reserve=trial_reserve,
                                    co_order=co_order)

        if score(retry) < score(best):
            # fewer failures wins; on a tie the pass that placed more of the
            # GD&T does, so the message names what is really in the way
            best, reserve = retry, trial_reserve
    if best[2].unresolved:
        return best[0], best[1], best[2].report()
    ctx = best[0]
    dx, tol = 0.0, ctx.mm(0.5)
    # place_views has already centred the group WITH its gutters and its
    # annotation band; this loop only trims what is left over. Letting it
    # run free lets a frame-anchored notes block drag a view group halfway
    # across the sheet (round-8: the rod on sheet 02 ended against the left
    # frame), so the total correction is capped.
    limit = ctx.mm(8.0)
    for _ in range(3):
        lo, hi = content_balance(best[2])
        shift = (hi - lo) / 2.0
        shift = max(-limit - dx, min(limit - dx, shift))
        if abs(shift) <= tol:
            break
        trial = _compose_part_sheet(m, part, proj, dx=dx + shift,
                                    reserve=reserve, co_order=co_order)
        tlo, thi = content_balance(trial[2])
        if trial[2].unresolved or abs(thi - tlo) >= abs(hi - lo) \
                or len(trial[2].faults) > len(best[2].faults):
            # a better-balanced sheet that has to cross a leader to get
            # there is not better (issue #18)
            break
        dx += shift
        best = trial
    return best[0], best[1], best[2].report()


def _compose_part_sheet(m, part, proj, dx=0.0, reserve=None,
                        co_order=None):
    """One placement pass; `dx` shifts the whole view group (and therefore
    everything anchored to it) horizontally, and `reserve` keeps a slot
    under named size callouts for the GD&T hung on them (gdt_reserve).
    Returns (ctx, replaced, layout) — the live Layout, so the caller can
    measure the balance."""
    num, den = parse_scale(part["scale"])
    ctx = sheet_context(m, part, num, den)
    front = proj[f"{part['part_id']}:front"]
    section = proj.get(f"{part['part_id']}:section") \
        if part["section"]["enabled"] else None
    aux = proj.get(f"{part['part_id']}:aux") if not section else None
    flat = proj.get(f"{part['part_id']}:flat") if not (section or aux) else None
    projection = part["section"].get("projection", "below")
    if aux or flat:
        projection = "below"
    gdt = part.get("gdt", {"datums": [], "frames": []})
    callouts = part.get("callouts", [])
    right_band = bool(callouts) or bool(gdt.get("frames"))
    rows_h_pre = mw.PARTS_ROW_H * ctx.t
    second = "section" if section else ("aux" if aux else "flat")
    # stacked views share the sheet's left/right gutters, so each side has to
    # hold the wider of the two views' dimension columns
    lg = dim_gutter(ctx, part, "front", "left")
    rg = dim_gutter(ctx, part, "front", "right")
    if projection == "below":
        lg = max(lg, dim_gutter(ctx, part, second, "left"))
        rg = max(rg, dim_gutter(ctx, part, second, "right"))
    else:
        rg = dim_gutter(ctx, part, second, "right")
    # which view sits outermost right? anything leadering out of the other
    # one has to keep its band in the inter-view gap
    outer = second if projection == "right" else "front"
    inner = "front" if projection == "right" else second
    inboard = projection in ("right", "left") and any(
        co.get("view", "front") != outer for co in callouts)
    band_w = annotation_band_width(ctx, callouts, gdt)
    band_gap_w = 0.0
    if projection in ("right", "left") and inboard:
        # BOTH views carry leaders: each gets its own column, the inner
        # view's in the gap and the outer view's outside the group, each
        # sized to its own callouts (issue #13). A detail view's callouts
        # go with the outer column, which is where a detail is placed.
        band_gap_w = annotation_band_width(ctx, callouts, gdt,
                                           views={inner})
        band_w = annotation_band_width(
            ctx, callouts, gdt,
            views={v for v in ("front", "section", "aux", "flat", "detail")
                   if v != inner})
    offs = place_views(ctx, front, section or aux or flat, projection,
                       right_band=right_band,
                       bottom_reserve=rows_h_pre + ctx.mm(16.0),
                       top_reserve=dim_gutter(ctx, part, "front", "top"),
                       band_w=band_w, band_gap_w=band_gap_w,
                       band_inboard=inboard, gutters=(lg, rg))
    if dx:
        offs = {k: (v[0] + dx, v[1]) for k, v in offs.items()}
    second_name = ("section" if section else
                   "aux" if aux else "flat" if flat else None)
    second_label = None
    cut_segs: list = []
    cut_arrows: list = []
    view_jobs = {"front": front}
    if section:
        view_jobs["section"] = section
    if aux:
        view_jobs["aux"] = aux
        offs["aux"] = offs["section"]
    if flat:
        view_jobs["flat"] = flat
        offs["flat"] = offs["section"]

    ctx.dimstyles = sheet_dimstyles(ctx)
    layout = Layout(ctx)
    rows_h = mw.PARTS_ROW_H * ctx.t
    ax0, ay0, ax1, _ = ctx.draw_area
    ctx._table_band = table_band(ctx, rows_h)
    layout.claim(ctx._table_band, "parts-rows", "reserved")
    zones = [view_zone(ctx, part, v, j,
                       offs[v if v in offs else "section"])
             for v, j in view_jobs.items()]
    box_recs, stamp_rect = pictorial_box(ctx, layout,
                                         shaded_png(part["part_id"]),
                                         zones=zones, floor=ay0 + rows_h)
    ctx.entities += box_recs
    ctx._stamp_rect = stamp_rect
    _claim_view(layout, front, offs["front"], "front-view")
    if second_name:
        _claim_view(layout, view_jobs[second_name], offs["section"],
                    f"{second_name}-view")

    # an enlarged DETAIL view, when the manifest asks for one: reserved
    # before any dimension is placed (so dims route around it) and kept out
    # of the views' own dimension gutters, exactly as the pictorial box is.
    # Its dimensions read true millimetres through DIMLFAC — lfacs carries
    # the factor to every placer that writes a value.
    lfacs: dict = {}
    dspec = part.get("detail_view") or {}
    det_recs, det_job, det_off = [], None, None
    if dspec and dspec.get("enabled", True):
        of = dspec.get("of", "front")
        if of not in view_jobs:
            layout.unresolved.append(f"detail-of-{of}-missing")
        else:
            # a detail view PREFERS to sit clear of the callout band:
            # parked in it, it takes a note's only landing, and a note
            # has no ladder of alternatives the way a detail does. When
            # the band is the only room there is, it goes there (issue
            # #18 — on a crowded sheet this is what decides whether
            # everything fits).
            det_zones = list(zones)
            if band_w > 0.0:
                bx = max(j["bounds"][1][0]
                         + offs[v if v in offs else "section"][0]
                         for v, j in view_jobs.items()) + ctx.mm(2.0)
                det_zones.append((bx, ctx.draw_area[1], ctx.draw_area[2],
                                  ctx.draw_area[3]))
            mark = len(layout.unresolved)
            det_recs, det_job, det_off = place_detail_view(
                ctx, layout, part, dspec, view_jobs[of],
                offs[of if of in offs else "section"], zones=det_zones)
            if det_job is None:
                del layout.unresolved[mark:]
                det_recs, det_job, det_off = place_detail_view(
                    ctx, layout, part, dspec, view_jobs[of],
                    offs[of if of in offs else "section"], zones=zones)
            if det_job is not None:
                view_jobs["detail"] = det_job
                offs["detail"] = det_off
                lfacs["detail"] = 1.0 / detail_factor(part, dspec)

    replaced = ctx.add_template(titleblock_replacements(m, part),
                                positional=rev_row_positional(m, part,
                                                              ctx))
    wkg = part_weight_kg(read_json(Path(m["_analysis_path"])), part)
    ctx.entities += mw.parts_row_records(ctx, 0, {
        "pno": disp_no(part["part_id"]), "description": part["description"],
        "qty": str(part["qty"]), "drg_no": part["drg_no"],
        "material": part["material"], "size": "-",
        "weight": weight_label(wkg),
        "remarks": "-"}, overflow=layout.unresolved)
    ctx.entities += mw.date_records(ctx, today())

    emit_prims(ctx, front["visible"], offs["front"])
    emit_prims(ctx, front["hidden"], offs["front"], P_HIDDEN)
    view_jobs_cc = {"front": front} | ({"section": section} if section else {})
    ctx.entities += emit_center_circles(ctx, part.get("center_circles"),
                                        view_jobs_cc, offs)

    if section:
        label = part["section"].get("label", "A")
        frac = part["section"].get("offset_frac", 0.5)
        # world-axis frac -> view frac: flip when the shared axis vector
        # points along a negative world direction
        d, u = VIEW_DIRS[part["front_view"]]
        axis_vec = u if projection == "below" else cross(d, u)
        if sum(axis_vec) < 0:
            frac = 1.0 - frac
        cut_recs, cut_segs, cut_arrows = cutting_plane(
            ctx, layout, front, offs["front"], label, projection, frac)
        ctx.entities += cut_recs
        emit_prims(ctx, section["visible"], offs["section"])
        ctx.entities += section_hatches(ctx, section, offs["section"])
        second_label = f"SECTION {label}-{label}"
    if aux:
        emit_prims(ctx, aux["visible"], offs["section"])
        emit_prims(ctx, aux["hidden"], offs["section"], P_HIDDEN)
        second_label = "END VIEW"
    if flat:
        # the blank as cut, before bending: outline and holes only. Hidden
        # lines are suppressed because in a flat slab they are the opposite
        # face of every edge, which doubles every line for no information.
        emit_prims(ctx, flat["visible"], offs["section"])
        second_label = "FLAT PATTERN"

    if det_job is not None:
        of = dspec.get("of", "front")
        ctx.entities += det_recs                  # the boundary circle
        emit_prims(ctx, det_job["visible"], det_off)
        emit_prims(ctx, det_job["hidden"], det_off, P_HIDDEN)
        ctx.entities += section_hatches(ctx, det_job, det_off)
        # and the circle + letter marking the region on the parent view
        ctx.entities += detail_marker(ctx, layout, dspec, view_jobs[of],
                                      offs[of if of in offs else "section"])

    ctx.entities += thread_lines(ctx, part.get("threads", []), view_jobs, offs)

    # every black line the views draw, as segments: dimension ink is tested
    # against THIS, not just against other annotations' bounding boxes
    geom = view_geometry(ctx, view_jobs, offs, cut_segs)
    # annotations through the collision layout: dims and callouts first
    # (they anchor FCF stacks), then datums, frames, notes.
    # Feature-location dims go FIRST of all: standard practice puts the
    # dimensions in the column nearest the view and pushes leader callouts
    # outboard of them, never the other way round.
    loc_specs = part.get("location_dims", [])
    if det_job is not None:
        loc_specs = detail_specs(loc_specs, dspec,
                                 detail_factor(part, dspec))
    loc_recs, loc_floors = place_location_dims(
        ctx, layout, loc_specs, view_jobs, offs, geom,
        cut_arrows, lfacs=lfacs)
    ctx.entities += loc_recs
    # a tabulated drawing's letter dimension goes down with the location
    # dims: it is a dimension, and it competes for the same gutters
    ctx.entities += place_letter_dim(ctx, layout, part, view_jobs, offs,
                                     geom, cut_arrows)
    # angular references sit INSIDE the view, so they claim their labels
    # before the leader callouts route around them
    ctx.entities += place_angle_refs(ctx, layout, part.get("angle_refs"),
                                     view_jobs, offs, geom)
    co_limits = {}
    if projection in ("right", "left") and inner in view_jobs:
        # the inner view's column ends where the outer view starts
        omn, omx = view_jobs[outer]["bounds"]
        oox = offs[outer if outer in offs else "section"][0]
        co_limits[inner] = omn[0] + oox - ctx.mm(2.0)
    cos = callouts if co_order is None else fan_order(
        ctx, callouts, view_jobs, offs, by_view=co_order == "fan")
    co_recs, co_rects, suppress, co_lands, co_anchors = place_callouts(
        ctx, layout, cos, view_jobs, offs, reserve=reserve,
        limits=co_limits, geom=geom)
    ctx.entities += co_recs
    dim_rects: dict = {}

    def floors_for(view, wside, hside):
        return {"width": loc_floors.get((view, wside), 0.0),
                "height": loc_floors.get((view, hside), 0.0)}

    if part.get("front_extent_dims", True) and not round_outline(front):
        # manifest opt-out: a round part's extents are fully covered by its
        # diameter callouts — the front w/h dims would be redundant
        ext_sides = part.get("extents_sides", {})
        w_side = ext_sides.get("width", "top")
        h_side = ext_sides.get("height", "left")
        ctx.entities += dim_extents(ctx, layout, front, offs["front"],
                                    width_side=w_side, height_side=h_side,
                                    floors=floors_for("front", w_side,
                                                      h_side), geom=geom,
                                    soft=cut_arrows)
    recs, rr = dim_circles(ctx, layout, front, offs["front"],
                           suppress=suppress.get("front", ()), geom=geom)
    ctx.entities += recs
    dim_rects.update({("front", k): v for k, v in rr.items()})
    if section and part.get("section_extent_dims", True) \
            and not round_outline(section):
        # shared-axis views don't repeat the shared extent dim: below shares
        # width with the front view, right/left share height
        # (opt-out: a circular section fully covered by its dia leader)
        sec_h_side = "left" if projection != "right" else "right"
        ctx.entities += dim_extents(ctx, layout, section, offs["section"],
                                    width_side="bottom",
                                    height_side=sec_h_side,
                                    which=("h",) if projection == "below"
                                    else ("w",),
                                    floors=floors_for("section", "bottom",
                                                      sec_h_side), geom=geom,
                                    soft=cut_arrows)
        recs, rr = dim_circles(ctx, layout, section, offs["section"],
                               suppress=suppress.get("section", ()), geom=geom)
        ctx.entities += recs
        dim_rects.update({("section", k): v for k, v in rr.items()})
    if aux:
        recs, rr = dim_circles(ctx, layout, aux, offs["section"],
                               suppress=suppress.get("aux", ()), geom=geom)
        ctx.entities += recs
        dim_rects.update({("aux", k): v for k, v in rr.items()})
    if flat:
        # the blank's own width and height are the shop's cutting size, and
        # the folded part above shares neither of them, so both are drawn
        ctx.entities += dim_extents(ctx, layout, flat, offs["section"],
                                    width_side="bottom", height_side="left",
                                    floors=floors_for("flat", "bottom",
                                                      "left"), geom=geom)
        recs, rr = dim_circles(ctx, layout, flat, offs["section"],
                               suppress=suppress.get("flat", ()), geom=geom)
        ctx.entities += recs
        dim_rects.update({("flat", k): v for k, v in rr.items()})
    if det_job is not None:
        recs, rr = dim_circles(ctx, layout, det_job, det_off,
                               suppress=suppress.get("detail", ()),
                               lfac=lfacs["detail"], geom=geom)
        ctx.entities += recs
        dim_rects.update({("detail", k): v for k, v in rr.items()})
    if second_label:
        # caption last of the view's own annotations: it belongs BELOW the
        # dimension band, not between the view and its dims
        ctx.entities += _view_label(ctx, layout, view_jobs[second_name],
                                    offs["section"], second_label)
    # datums/FCFs before the notes: every one of them is ANCHORED to a view
    # edge or a callout and has a handful of legal spots, while the notes
    # block searches the whole sheet for a free rectangle. Placing the block
    # first buried the section's datum-A zone under it (round-8 QA sheet 01).
    # FCF stacks that belong UNDER a callout go before the datum boxes: the
    # stack is a wide, rigidly-anchored block, a datum box is a small square
    # with a dozen candidate spots along its edge
    if det_job is not None:
        ctx.entities += detail_caption_records(ctx, layout, det_job,
                                               det_off)
    frames = gdt.get("frames", [])
    anchored = [f for f in frames if f["target"] != "datum_A_face"]
    on_datum = [f for f in frames if f["target"] == "datum_A_face"]
    # every callout is its own anchor (issue #7), with the diameter dims
    # behind them as the fallback they have always been
    anchors = co_anchors + legacy_anchors(dim_rects=dim_rects)
    ctx.entities += place_fcfs(ctx, layout, {"frames": anchored}, view_jobs,
                               offs, co_rects, {}, dim_rects, lfacs,
                               anchors=anchors)
    dat_recs, datum_pos = place_datums(ctx, layout, gdt, view_jobs, offs,
                                       co_rects, dim_rects, lfacs, co_lands,
                                       anchors=anchors)
    ctx.entities += dat_recs
    ctx.entities += place_fcfs(ctx, layout, {"frames": on_datum}, view_jobs,
                               offs, co_rects, datum_pos, dim_rects, lfacs,
                               anchors=anchors)
    layout.release("slot")       # the GD&T is placed; free what it left
    if flat:
        ctx.entities += place_bend_table(ctx, layout, part, rows_h)
    ctx.entities += place_variants_table(ctx, layout, part, rows_h)
    notes = part.get("fab_notes", []) + part.get("extra_notes", [])
    ctx.entities += place_notes(ctx, layout, notes, rows_h)
    return ctx, replaced, layout


def scale_job(job, f):
    """Uniformly scale a projected view's geometry (NTS pictorials only —
    never a dimensioned orthographic view)."""
    import copy
    j = copy.deepcopy(job)

    def sp(pr):
        if pr["t"] == "seg":
            pr["a"] = [v * f for v in pr["a"]]
            pr["b"] = [v * f for v in pr["b"]]
        elif pr["t"] in ("circle", "arc"):
            pr["c"] = [v * f for v in pr["c"]]
            pr["r"] *= f
        elif pr["t"] == "poly":
            pr["pts"] = [[x * f, y * f] for x, y in pr["pts"]]
    for bucket in ("visible", "hidden"):
        for pr in j.get(bucket, []):
            sp(pr)
    j["bounds"] = [[v * f for v in j["bounds"][0]],
                   [v * f for v in j["bounds"][1]]]
    for inst in j.get("instances", []):
        inst["center"] = [v * f for v in inst["center"]]
        inst["bbox"] = [v * f for v in inst["bbox"]]
        # per-instance ownership edges live in the same space as visible/
        # hidden ink and must scale with it, else balloon_landing compares
        # scaled ink against a 1/f phantom and lands on the wrong part
        for pr in inst.get("edges", []):
            sp(pr)
    return j


def detail_instances(parent_job, spec, k):
    """The parent view's per-instance anchors, moved into a detail view.

    A balloon in a detail needs the same per-instance edges the main view
    has, clipped to the detail circle and enlarged with it — otherwise it
    lands on ink that is not drawn there."""
    cen = [float(spec["center"][0]), float(spec["center"][1])]
    r = float(spec["radius"])
    out = []
    for inst in parent_job.get("instances", []):
        edges = clip_prims(inst.get("edges", []), cen, r)
        if not edges:
            continue                    # this part is not in the region
        moved = []
        for pr in edges:
            q = dict(pr)
            if pr["t"] == "seg":
                q["a"] = [(pr["a"][0] - cen[0]) * k, (pr["a"][1] - cen[1]) * k]
                q["b"] = [(pr["b"][0] - cen[0]) * k, (pr["b"][1] - cen[1]) * k]
            else:
                q["c"] = [(pr["c"][0] - cen[0]) * k, (pr["c"][1] - cen[1]) * k]
                q["r"] = pr["r"] * k
            moved.append(q)
        mn, mx = bounds_of_prims(moved)
        out.append({"name": inst["name"], "label": inst.get("label", ""),
                    "center": [(mn[0] + mx[0]) / 2, (mn[1] + mx[1]) / 2],
                    "bbox": [mn[0], mn[1], mx[0], mx[1]], "edges": moved})
    return out


def bounds_of_prims(prims):
    mn = [1e300, 1e300]
    mx = [-1e300, -1e300]
    for pr in prims:
        if pr["t"] == "seg":
            pts = [pr["a"], pr["b"]]
        elif pr["t"] in ("circle", "arc"):
            c, r = pr["c"], pr["r"]
            pts = [[c[0] - r, c[1] - r], [c[0] + r, c[1] + r]]
        else:
            pts = pr["pts"]
        for x, y in pts:
            mn = [min(mn[0], x), min(mn[1], y)]
            mx = [max(mx[0], x), max(mx[1], y)]
    return mn, mx


def continuation_sheet(m, asm, rows, analysis, sheet_of, details=(),
                       balloons=None):
    """The second sheet of an assembly drawing.

    Same DRG NO., "SHEET 2 OF 2" (CLAUDE.md: i OF n only within one DRG
    NO.), title block and table. It carries the rest of a parts table that
    does not fit — and, when the manifest asks for it, the DETAIL views
    with their own balloons (issue #19): sheet 1 then has the views and
    the section, sheet 2 the enlargements and the remaining rows. The
    regions are marked on sheet 1, where the views are, and each letter
    there says which sheet to turn to."""
    num, den = parse_scale(asm["scale"])
    ctx = sheet_context(m, asm, num, den)
    ctx.dimstyles = sheet_dimstyles(ctx)
    layout = Layout(ctx)
    sheet = dict(asm, sheet_of=sheet_of)
    replaced = ctx.add_template(titleblock_replacements(m, sheet),
                                positional=rev_row_positional(m, asm, ctx))
    overflow = []
    for idx, row in enumerate(rows):
        ctx.entities += mw.parts_row_records(
            ctx, idx, parts_row_cells(row, analysis, m), overflow=overflow)
    ctx.entities += mw.date_records(ctx, today())
    rows_h = mw.PARTS_ROW_H * ctx.t * len(rows)
    ctx._table_band = table_band(ctx, rows_h)
    layout.claim(ctx._table_band, "parts-rows", "reserved")

    ball = balloons or {}
    # the fan's column, and the strip it needs: left of the parts table,
    # which on a widened sheet travels with the title block, so the
    # details keep to the left of BOTH
    brad = 1.4 * ctx.mm(BALLOON_TEXT_MM)
    fan_w = 2 * brad + ctx.mm(16.0)
    # on a WIDENED paper the parts table travels with the title block and
    # takes the right-hand column, so the fan goes left of it; on A4 the
    # table runs the full width along the bottom and the column is the
    # sheet's own right edge
    band = ctx._table_band
    right = band[0] if band[0] > (ctx.draw_area[0] + ctx.draw_area[2]) / 2 \
        else ctx.draw_area[2]
    knee_x = right - fan_w
    fan_zone = [(knee_x - ctx.mm(2.0), ctx.draw_area[1],
                 ctx.draw_area[2], ctx.draw_area[3])]

    placed_details = []
    for spec, _of, parent, job in details:
        label = str(spec.get("label", "B"))
        # the balloon column of this sheet belongs to the fan, as on
        # sheet 1: the detail islands keep to the left of it
        recs, djob, off = place_detail_view(
            ctx, layout, asm, spec, parent, (0.0, 0.0),
            zones=fan_zone, pad_mm=DETAIL_ASSEMBLY_PAD_MM)
        if djob is None:
            continue
        djob["instances"] = job.get("instances", [])
        # the next detail goes ABOVE or BELOW this one, not beside it:
        # two details side by side share one balloon column, and every
        # balloon of the far one would reach it across the near one
        # (issue #20)
        rk = (djob["bounds"][1][1] - djob["bounds"][0][1]) / 2.0
        pad = ctx.mm(DETAIL_ASSEMBLY_PAD_MM)
        fan_zone.append((ctx.draw_area[0], off[1] - rk - pad,
                         ctx.draw_area[2], off[1] + rk + pad))
        ctx.entities += recs
        emit_prims(ctx, djob["visible"], off)
        emit_prims(ctx, djob["hidden"], off, P_HIDDEN)
        ctx.entities += section_hatches(ctx, djob, off)
        ctx.entities += detail_caption_records(ctx, layout, djob, off)
        placed_details.append((label, djob, off))

    items = [(djob, off, k, pid) for label, djob, off in placed_details
             for (k, pid) in (ball.get(f"detail:{label}") or [])]
    if items:
        recs, _ = balloons_fan(ctx, layout, None, None, items,
                               start_y=ctx.draw_area[3] - brad,
                               knee_x=knee_x, dense=True,
                               inks=view_ink([(f"detail {label}", dj, off)
                                              for label, dj, off
                                              in placed_details]),
                               other=other_ink(layout))
        ctx.entities += recs
    if rows:
        ctx.entities += place_notes(
            ctx, layout,
            [f"PARTS TABLE CONTINUED FROM SHEET {sheet_of[0] - 1}."], rows_h)
    layout.unresolved.extend(overflow)
    ctx._stamp_rect = None
    return ctx, replaced, layout.report()


def build_assembly_sheet(m, proj):
    """The assembly sheet(s): front view, an optional section, any number of
    detail views, one balloon per part number across them all, and the
    parts table grouped by sub-assembly.

    Returns [(ctx, replaced, layout report, meta)] — more than one sheet
    only when the table needs a continuation."""
    asm = m["assembly_sheet"]
    num, den = parse_scale(asm["scale"])
    ctx = sheet_context(m, asm, num, den)
    analysis = read_json(Path(m["_analysis_path"]))
    front = proj["assembly:front"]
    sec_spec = asm.get("section") or {}
    section = proj.get("assembly:section") if sec_spec.get("enabled") else None
    iso = asm.get("view") == "iso"
    if iso and section is None:
        # NTS pictorial: shrink to leave room for balloons + notes
        vw = front["bounds"][1][0] - front["bounds"][0][0]
        vh = front["bounds"][1][1] - front["bounds"][0][1]
        f = min(1.0, ctx.mm(100.0) / vw, ctx.mm(95.0) / vh)
        if f < 1.0:
            front = scale_job(front, f)

    # the parts table is planned BEFORE the views are placed: how many rows
    # it needs is how much of the sheet the views do not get
    table = asm.get("parts_table") or {}
    row_h = mw.PARTS_ROW_H * ctx.t
    area_h = ctx.draw_area[3] - ctx.draw_area[1]
    max_rows = max(1, int(area_h * TABLE_AREA_FRAC / row_h))
    if asm.get("balloons", True) and m.get("parts"):
        # The balloons need column height, and on this sheet they come
        # first: a part number with no balloon fails the sheet, while a
        # table row has somewhere else to go. Two columns hold half the
        # numbers each, so the table keeps what is left over and the rows
        # it cannot keep move to the continuation sheet — the fallback
        # that is already reported.
        # what one column of a split fan needs: half the numbers at the
        # dense pitch, plus the half-balloon the top one hangs above its
        # own centre. balloon_capacity measures the same span back.
        # one spare slot per column: a balloon that has to step past the
        # view's own dimension text eats into the fan's budget, and a
        # column with no slack loses the last number
        need = ((math.ceil(len(m["parts"]) / 2.0) + 1)
                * balloon_pitch(ctx, True)
                + 1.4 * ctx.mm(BALLOON_TEXT_MM))
        max_rows = max(1, min(max_rows,
                              int((area_h - need) / row_h)))
    rows, table_report = parts_table_plan(
        m, analysis, max_rows, group_by=table.get("group_by", "none"),
        group_hardware=table.get("group_hardware"),
        total=table.get("total"))
    here_rows = rows[:max_rows] if table_report["sheets"] > 1 else rows
    rest_rows = rows[max_rows:] if table_report["sheets"] > 1 else []
    rows_h = row_h * len(here_rows)

    projection = sec_spec.get("projection", "below")
    # will the balloon fan need BOTH sides? Decided before the views are
    # placed, because a left-hand column has to be reserved like the
    # right-hand one — the views give it the room.
    brad = 1.4 * ctx.mm(BALLOON_TEXT_MM)
    # the column sits OUTBOARD of the view's own left-hand dimension
    # gutter: the assembly sheet dimensions its overall width and height
    # there, and a balloon parked on a dimension is not a balloon
    left_dim = dim_gutter(ctx, asm, "front", "left")
    left_band = 2 * brad + ctx.mm(18.0) + left_dim
    split_fan = bool(asm.get("balloons", True)) and len(
        m.get("parts") or []) > balloon_capacity(
            ctx, ctx.draw_area[3] - brad, ctx.draw_area[1] + rows_h)
    offs = place_views(ctx, front, section, projection,
                       right_band=asm.get("balloons", True),
                       gutters=(left_band if split_fan else 0.0, 0.0),
                       bottom_reserve=rows_h + ctx.mm(36.0))
    ctx.dimstyles = sheet_dimstyles(ctx)
    layout = Layout(ctx)
    views = {"front": front}
    if section is not None:
        views["section"] = section
    zones = [(j["bounds"][0][0] + offs[v][0], j["bounds"][0][1] + offs[v][1],
              j["bounds"][1][0] + offs[v][0], j["bounds"][1][1] + offs[v][1])
             for v, j in views.items()]
    # the balloon column: every view's balloons share it (one fan, no
    # crossing leaders), so nothing else may be parked there
    band_x = max(z[2] for z in zones) + ctx.mm(16.0)
    if asm.get("balloons", True):
        # a SPLIT fan runs the whole column, from the top of the drawing
        # area down to the table, so its band is reserved over that whole
        # height — the pictorial box would otherwise take the top of it
        band_lo = (ctx.draw_area[1] + rows_h) if split_fan \
            else min(z[1] for z in zones)
        band_hi = ctx.draw_area[3] if split_fan \
            else max(z[3] for z in zones)
        zones.append((band_x - ctx.mm(2.0), band_lo,
                      ctx.draw_area[2], band_hi))
        # the fan may need the other side too; its column is reserved
        # here as well, or the pictorial box would take it first
        if split_fan:
            # only the column itself, not every millimetre left of the
            # views: the rest of that slack is where a detail view goes
            lx = min(z[0] for z in zones) - ctx.mm(16.0) - left_dim
            zones.append((lx - 2 * brad - ctx.mm(2.0), band_lo,
                          lx + ctx.mm(2.0), band_hi))
    # the parts table is claimed BEFORE the pictorial box is placed, and
    # the box is given its floor: otherwise the box slid down the bottom
    # band and its frame was drawn straight across the table's upper rows
    # (issue #9), since nothing had claimed them yet
    ax0, ay0, ax1, _ = ctx.draw_area
    ctx._table_band = table_band(ctx, rows_h)
    layout.claim(ctx._table_band, "parts-rows", "reserved")
    box_recs, stamp_rect = pictorial_box(ctx, layout, shaded_png("assembly"),
                                         zones=zones, floor=ay0 + rows_h)
    ctx.entities += box_recs
    ctx._stamp_rect = stamp_rect
    _claim_view(layout, front, offs["front"], "assembly-view")
    if section is not None:
        _claim_view(layout, section, offs["section"], "assembly-section")

    # detail views: as many as the manifest asks for, each the parent's own
    # ink enlarged (the part sheets' machinery, issue #2)
    detail_specs_list = asm.get("detail_views") or []
    detail_recs, detail_jobs, detail_offs = [], {}, {}
    later: list = []            # details the manifest puts on sheet 2
    for spec in detail_specs_list:
        label = str(spec.get("label", "B"))
        of = spec.get("of", "front")
        if of not in views:
            layout.unresolved.append(f"detail-of-{of}-missing")
            continue
        key = f"detail:{label}"
        k = detail_factor(asm, spec)
        if detail_sheet(spec) > 1:
            # a detail may be drawn on the CONTINUATION sheet, with its own
            # balloons: sheet 1 carries the views and the section, sheet 2
            # the details and the rest of the table. Its job is built here
            # all the same, because the balloon plan has to know what it
            # shows before either sheet is drawn.
            job = detail_job(views[of], spec, k)
            job["instances"] = detail_instances(views[of], spec, k)
            detail_jobs[key] = job
            later.append((spec, of, views[of], job))
            continue
        recs, job, off = place_detail_view(ctx, layout, asm, spec, views[of],
                                           offs[of], zones=zones,
                                           pad_mm=DETAIL_ASSEMBLY_PAD_MM)
        if job is None:
            continue
        job["instances"] = detail_instances(views[of], spec, k)
        detail_jobs[key] = job
        detail_offs[key] = off
        detail_recs.append((recs, job, off, spec, of))

    replaced = ctx.add_template(titleblock_replacements(
        m, dict(asm, sheet_of=asm.get("sheet_of",
                                      [1, table_report["sheets"]]))),
        positional=rev_row_positional(m, asm, ctx))
    overflow = []
    for idx, row in enumerate(here_rows):
        ctx.entities += mw.parts_row_records(
            ctx, idx, parts_row_cells(row, analysis, m), overflow=overflow)
    ctx.entities += mw.date_records(ctx, today())

    emit_prims(ctx, front["visible"], offs["front"])
    emit_prims(ctx, front["hidden"], offs["front"], P_HIDDEN)
    cut_segs: list = []
    cut_arrows: list = []
    hatch_report = {}
    if section is not None:
        label = sec_spec.get("label", "A")
        frac = sec_spec.get("offset_frac", 0.5)
        d, u = VIEW_DIRS[asm["front_view"]]
        axis_vec = u if projection == "below" else cross(d, u)
        if sum(axis_vec) < 0:
            frac = 1.0 - frac
        cut_recs, cut_segs, cut_arrows = cutting_plane(
            ctx, layout, front, offs["front"], label, projection, frac)
        ctx.entities += cut_recs
        emit_prims(ctx, section["visible"], offs["section"])
        hrecs, hatch_report = assembly_hatches(ctx, section, offs["section"])
        ctx.entities += hrecs
    for recs, job, off, spec, of in detail_recs:
        ctx.entities += recs
        emit_prims(ctx, job["visible"], off)
        emit_prims(ctx, job["hidden"], off, P_HIDDEN)
        ctx.entities += section_hatches(ctx, job, off)
        ctx.entities += detail_marker(ctx, layout, spec, views[of], offs[of])
    for spec, of, _parent, _job in later:
        # the region is marked where it IS, on this sheet's view; the
        # letter says which sheet to turn to
        ctx.entities += detail_marker(ctx, layout, spec, views[of], offs[of],
                                      sheet=detail_sheet(spec))

    ctx.entities += thread_lines(ctx, asm.get("threads", []), views, offs)
    if iso and section is None:
        ctx.entities += _view_label(ctx, layout, front, offs["front"],
                                    "ISOMETRIC VIEW")
    elif asm.get("front_extent_dims", True) and not iso:
        ctx.entities += dim_extents(ctx, layout, front, offs["front"])
    if section is not None:
        ctx.entities += _view_label(
            ctx, layout, section, offs["section"],
            f"SECTION {sec_spec.get('label', 'A')}-{sec_spec.get('label', 'A')}")
    for _recs, job, off, spec, _of in detail_recs:
        ctx.entities += detail_caption_records(ctx, layout, job, off)

    balloon_report = {}
    later_balloons: dict = {}
    if asm.get("balloons", True):
        id_by_file = {p["file"]: p["part_id"] for p in m["parts"]}
        # an analysis written before v0.5.0 has no paths, only names
        id_by_path = {(n.get("path") or n["name"]):
                      id_by_file.get(n.get("target_file"))
                      for n in fc_tree.flatten(
                          analysis["assembly"]["instances"])}
        all_views = dict(views, **detail_jobs)
        all_offs = dict(offs, **detail_offs)
        plan = balloon_plan(m, all_views, id_by_path)
        # a balloon on a detail that is drawn on sheet 2 is drawn THERE
        elsewhere = {f"detail:{str(sp.get('label', 'B'))}"
                     for sp, _of, _p, _j in later}
        # ONE column while it holds them all; otherwise one each side of
        # the view, every balloon on the side its own part sits on. 26
        # numbers need about 360 mm of column and A3 landscape has 290.
        # a fan beside the views may use the whole drawing area's height,
        # not only the part of it the views happen to occupy
        top = ctx.draw_area[3] - 1.4 * ctx.mm(BALLOON_TEXT_MM)
        floor = ctx.draw_area[1] + rows_h
        cap = balloon_capacity(ctx, top, floor, dense=True)
        # the knee of the left column: the balloon itself sits further
        # left again, so the view has to have left room for both
        left_x = min(j["bounds"][0][0] + offs[v][0]
                     for v, j in views.items()) - ctx.mm(16.0) - left_dim
        columns = {"right": band_x, "left": left_x}
        inks = view_ink([(v, j, offs[v]) for v, j in views.items()])
        other = other_ink(layout)

        def run_fans(plan):
            """Place every balloon of this plan and say which of them had
            to be left out because a view was in the way (issue #20)."""
            by_view: dict = {}
            for pid, (view, k) in plan.items():
                if view in elsewhere:
                    continue
                by_view.setdefault(view, []).append((k, disp_no(pid)))
            sides = balloon_sides(ctx, all_views, all_offs, by_view,
                                  balloon_capacity(ctx, top, floor),
                                  columns)
            if sides:                   # a split fan packs closer
                sides = balloon_sides(ctx, all_views, all_offs, by_view,
                                      cap, columns)
            full = None
            if sides:
                per = {"left": 0, "right": 0}
                for sd in sides.values():
                    per[sd] += 1
                if max(per.values()) > cap:
                    full = (max(per, key=lambda kk: per[kk]), per)
            # a single column keeps starting level with the view (every
            # sheet drawn so far); a split one starts at the top of the
            # area, where the room it needs is
            next_y = {"left": top, "right": top} if sides \
                else {"left": None, "right": None}
            recs, blocked = [], {}
            for sd in ("right", "left"):
                # every view's balloons on this side are ONE fan: ordered
                # in two runs, one view after the other, their leaders
                # cross between the views (issue #18)
                items = [(all_views[view], all_offs[view], k, pid)
                         for view in sorted(by_view,
                                            key=lambda v: (v != "front", v))
                         for (k, pid) in by_view[view]
                         if sides.get((view, k), "right") == sd]
                if not items:
                    continue
                got, next_y[sd] = balloons_fan(
                    ctx, layout, None, None, items, start_y=next_y[sd],
                    knee_x=columns[sd], side=sd, dense=bool(sides),
                    inks=inks, other=other, in_the_way=blocked)
                recs += got
            return recs, blocked, sides, full

        snap = layout.snapshot()
        recs, blocked, sides, full = run_fans(plan)
        # A balloon the section was in the way of belongs in the section,
        # when the section shows that part at all: moving it is better
        # than a leader over the hatch, and better than no balloon
        # (issue #20). The whole fan is placed again, because every knee
        # below it moves up.
        pid_of = {disp_no(pid): pid for pid in plan}
        forced = {str(k) for k in (asm.get("balloon_in") or {})}
        moved = {}
        for shown, view in blocked.items():
            pid = pid_of.get(shown)
            if pid in forced:
                continue        # the manifest said where this one goes
            part = next((p for p in m["parts"]
                         if str(p["part_id"]) == pid), None)
            if part is None or view not in all_views:
                continue
            k = instance_index(all_views[view], part["part_id"], id_by_path)
            if k is not None:
                moved[pid] = (view, k)
        if moved:
            layout.restore(snap)
            trial_plan = dict(plan, **moved)
            trial = run_fans(trial_plan)
            if len(trial[1]) < len(blocked):
                plan = trial_plan
                recs, blocked, sides, full = trial
            else:
                layout.restore(snap)
                recs, blocked, sides, full = run_fans(plan)
        ctx.entities += recs
        if full:
            side_name, per = full
            layout.unresolved.append(
                f"balloons: the {side_name} column has {per[side_name]} "
                f"numbers and room for {cap} — even with both sides in "
                f"use {sum(per.values())} balloons need more column than "
                "this sheet has; use a bigger sheet or balloon fewer "
                "part numbers per view")
        later_balloons.update({v: [] for v in elsewhere})
        for pid, (view, k) in plan.items():
            if view in elsewhere:
                later_balloons[view].append((k, disp_no(pid)))
        missing = [p["part_id"] for p in m["parts"]
                   if str(p["part_id"]) not in plan]
        balloon_report = {"ballooned": {k: v[0] for k, v in plan.items()},
                          "columns": 2 if sides else 1,
                          "capacity_per_column": cap,
                          "not_shown": missing}
        for pid in missing:
            # a part number with nowhere to point is a parts table that does
            # not match the drawing: say so rather than ship it
            layout.unresolved.append(f"balloon-{pid}-no-view-shows-it")
    ctx.entities += place_notes(ctx, layout, asm.get("fab_notes", []), rows_h)
    layout.unresolved.extend(overflow)
    # what the table was planned to print, cell by cell: the value check
    # reads the printed table against THIS, since grouping and the
    # continuation sheet decide which rows a part's own values reach
    table_report["cells"] = [parts_row_cells(r, analysis, m) for r in rows]
    meta = {"parts_table": table_report, "hatch": hatch_report,
            "balloons": balloon_report,
            "details": [str(s.get("label", "B")) for s in detail_specs_list],
            "section": section is not None}
    sheets = [(ctx, replaced, layout.report(), meta)]
    if rest_rows or later:
        n_sheets = max(2, table_report["sheets"])
        sheets.append(continuation_sheet(
            m, asm, rest_rows, analysis, [2, n_sheets],
            details=later, balloons=later_balloons)
            + ({"parts_table": {"continuation_of": asm["drg_no"]},
                "details": [str(sp.get("label", "B"))
                            for sp, _o, _p, _j in later]},))
    return sheets


def sheet_layout_check(ctx, path: Path, waivers=None):
    """Read a finished sheet back and report what it LOOKS like (issue
    #22). Returns (live faults, waived, warnings)."""
    box = getattr(ctx, "_pictorial_box", None)
    found = layout_check.check_sheet(
        path.read_text(), ctx.draw_area, ctx.num / ctx.den,
        table_band=getattr(ctx, "_table_band", None),
        boxes=[box] if box else ())
    live, waived_ = verify.waived(layout_check.faults(found), waivers)
    return live, waived_, layout_check.warnings(found)


def main() -> None:
    man_path = MANIFESTS_DIR / "manifest.json"
    if "--manifest" in sys.argv:
        man_path = Path(sys.argv[sys.argv.index("--manifest") + 1])
    if not man_path.exists():
        die(f"no {man_path} — run /finalise first")
    m = read_json(man_path)
    if not m.get("locked"):
        die("manifest is not locked — /finalise must lock it")
    if m.get("template"):
        # per-project title block: same sheet geometry, different company
        mw.use_template(mw.resolve_template(m["template"]))
        print(f"template: {m['template']}")
    analysis_path = Path(__file__).resolve().parent.parent / "reports" / \
        "analysis.json"
    m["_analysis_path"] = str(analysis_path)
    analysis = read_json(analysis_path)

    proj = run_projections(m, analysis)
    OUTPUT_MUSA_DIR.mkdir(parents=True, exist_ok=True)

    has_asm = bool(m.get("assembly_sheet") and m["assembly_sheet"].get("enabled", True))
    drawn = sheet_parts(m)
    total = len(drawn) + (1 if has_asm else 0)
    sheets = []
    problems = []
    stamp_rects = {}
    for part in m["parts"]:
        if not part.get("sheet", True):
            print(f"bought-out: {part['description']} "
                  f"({part['drg_no']}) — parts-table row only, no sheet")
    bboxes = {p["file"]: p["bodies"][0]["bbox_size"]
              for p in analysis.get("parts", []) if p.get("bodies")}
    tpl_texts = [r.content.replace("\\n", "\n")
                 for r in mw.parse_template() if r.kind == "MTEXT"]
    value_findings = []
    layout_findings: list = []
    layout_warnings: list = []
    for idx, part in enumerate(drawn, start=1):
        ctx, replaced, lay = build_part_sheet(m, part, proj)
        stamp_rects[part["drg_no"]] = ctx._stamp_rect
        out = OUTPUT_MUSA_DIR / f"{part['drg_no']}.musa"
        out.write_text(ctx.render())
        # the value check (issue #14): what the manifest states must be
        # what the sheet prints. /export refuses to run on a difference
        # that is not waived.
        values = verify.check_sheet(
            m, part, out.read_text(),
            dump_dims(out) if MUSA_PLOT.exists() else [],
            bboxes.get(part["file"]), tpl_texts)
        live, done = verify.waived(values, part.get("value_waivers"))
        value_findings += [f"{part['drg_no']}: {ln}"
                           for ln in verify.report(live)]
        # the layout check (issue #22): what the sheet LOOKS like, read
        # back off the finished sheet the way the value check reads what
        # it says
        lay_live, lay_waived, lay_warn = sheet_layout_check(
            ctx, out, part.get("layout_waivers") or m.get("layout_waivers"))
        layout_findings += [f"{part['drg_no']}: {ln}"
                            for ln in layout_check.report(lay_live)]
        layout_warnings += [f"{part['drg_no']}: {ln}"
                            for ln in layout_check.report(lay_warn)]
        sheets.append({"values": live, "values_waived": done,
                       "layout_faults": lay_live,
                       "layout_waived": lay_waived,
                       "layout_warnings": lay_warn,
                       "template_problems": list(
                           getattr(ctx, "field_overflow", [])),
                       "file": str(out), "drg_no": part["drg_no"],
                       "description": part["description"],
                       "scale": part["scale"], "window": ctx.window,
                       "paper": ctx.paper, "orientation": ctx.orientation,
                       "kind": "part", "section": part["section"]["enabled"],
                       "projection": part["section"].get("projection",
                                                         "below"),
                       "replaced_fields": replaced, "layout": lay,
                       "stamp": {"png": f"output/work/shaded/"
                                        f"{part['part_id']}.png",
                                 "rect": list(stamp_rects[part['drg_no']])},
                       "logo": ctx.logo_stamp()})
        if lay["unresolved"]:
            problems.append(f"{part['drg_no']}: {lay['unresolved']}")
        print(f"sheet {idx}/{total}: {out.name}  scale {part['scale']}  "
              + (f"{ctx.paper} {ctx.orientation}  "
                 if (ctx.paper, ctx.orientation) != mw.DEFAULT_PAPER else "")
              + f"(nudges={lay['nudges']}, unresolved="
                f"{len(lay['unresolved'])})")
    if m.get("assembly_sheet") and m["assembly_sheet"].get("enabled", True):
        asm = m["assembly_sheet"]
        built = build_assembly_sheet(m, proj)
        for k, (ctx, replaced, lay, meta) in enumerate(built, start=1):
            suffix = "" if k == 1 else f"_{k}"
            drg = asm["drg_no"]
            stamp_rects[drg + suffix] = ctx._stamp_rect
            asm_out = OUTPUT_MUSA_DIR / f"{drg}{suffix}.musa"
            asm_out.write_text(ctx.render())
            lay_live, lay_waived, lay_warn = sheet_layout_check(
                ctx, asm_out,
                asm.get("layout_waivers") or m.get("layout_waivers"))
            layout_findings += [f"{drg}{suffix}: {ln}"
                                for ln in layout_check.report(lay_live)]
            layout_warnings += [f"{drg}{suffix}: {ln}"
                                for ln in layout_check.report(lay_warn)]
            entry = {"file": str(asm_out), "drg_no": drg,
                     "layout_faults": lay_live,
                     "layout_waived": lay_waived,
                     "layout_warnings": lay_warn,
                     "template_problems": list(
                         getattr(ctx, "field_overflow", [])),
                     "description": asm["description"],
                     "scale": asm["scale"], "window": ctx.window,
                     "paper": ctx.paper, "orientation": ctx.orientation,
                     "kind": "assembly",
                     "section": bool(meta.get("section")),
                     "replaced_fields": replaced, "layout": lay,
                     "assembly_report": meta,
                     "logo": ctx.logo_stamp()}
            if ctx._stamp_rect is not None:
                entry["stamp"] = {"png": "output/work/shaded/assembly.png",
                                  "rect": list(ctx._stamp_rect)}
            if len(built) > 1:
                entry["sheet_of"] = [k, len(built)]
            sheets.append(entry)
            if lay["unresolved"]:
                problems.append(f"{drg}{suffix}: {lay['unresolved']}")
            print(f"sheet {total}/{total}: {asm_out.name}  scale "
                  f"{asm['scale']}"
                  + (f"  (nudges={lay['nudges']})" if k == 1 else ""))
        report = built[0][3]
        if report.get("section"):
            h = report.get("hatch") or {}
            print(f"  section: {len(h.get('hatch_styles') or {})} parts "
                  f"hatched"
                  + (f", {len(h['solid_filled'])} filled solid (thin)"
                     if h.get("solid_filled") else ""))
        if report.get("details"):
            print(f"  details: {', '.join(report['details'])}")
        tbl = report.get("parts_table") or {}
        if tbl.get("fallbacks"):
            # the table did not fit as asked: say which fallback was used
            print(f"  parts table ({tbl['rows']} rows): "
                  + "; ".join(tbl["fallbacks"]))
    asm_sheets = [s for s in sheets if s["kind"] == "assembly"]
    if asm_sheets:
        asm = m["assembly_sheet"]
        text = "\n".join(Path(s["file"]).read_text() for s in asm_sheets)
        dims = []
        if MUSA_PLOT.exists():
            for s in asm_sheets:
                dims += dump_dims(Path(s["file"]))
        bb = analysis["assembly"].get("bbox_max")
        lo = analysis["assembly"].get("bbox_min")
        size = [hi - l for hi, l in zip(bb, lo)] if bb and lo else None
        cells = ((asm_sheets[0].get("assembly_report") or {})
                 .get("parts_table") or {}).get("cells")
        values = verify.check_assembly(m, asm, text, dims, size, tpl_texts,
                                       rows=cells)
        live, done = verify.waived(values, asm.get("value_waivers"))
        asm_sheets[0]["values"] = live
        asm_sheets[0]["values_waived"] = done
        value_findings += [f"{asm['drg_no']}: {ln}"
                           for ln in verify.report(live)]
    if value_findings:
        print(f"values: {len(value_findings)} difference(s) between the "
              "manifest and the sheets:")
        for line in value_findings:
            print(f"  {line}")
        print("  (/export will refuse these; waive one in the manifest's "
              '"value_waivers" if it is deliberate)')
    else:
        print("values: every value the manifest states is on its sheet")
    if layout_findings:
        print(f"layout: {len(layout_findings)} fault(s) on the finished "
              "sheets:")
        for line in layout_findings:
            print(f"  {line}")
        print("  (/export will refuse these; waive one in the manifest's "
              '"layout_waivers" if it is deliberate)')
    else:
        print("layout: no crossing leaders, no ink on text, nothing off "
              "the sheet")
    for line in layout_warnings:
        print(f"  warning: {line}")
    write_json(OUTPUT_MUSA_DIR / "sheets.json",
               {"manifest": str(man_path), "sheets": sheets,
                "values_ok": not value_findings,
                "layout_ok": not layout_findings,
                "deliver_to": m.get("deliver_to")})
    print(f"wrote {len(sheets)} sheets + output/musa/sheets.json")
    if problems:
        die("layout unresolved (never ship an overlapping sheet):\n  "
            + "\n  ".join(problems))


if __name__ == "__main__":
    main()
