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
from common import (MANIFESTS_DIR, OUTPUT_MUSA_DIR, SCRATCH_DIR, die,
                    png_size, read_json, run_freecad, today, write_json)
import musa_writer as mw
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

def collect_jobs(m: dict) -> list[dict]:
    jobs = []
    for part in m["parts"]:
        d, u = VIEW_DIRS[part["front_view"]]
        jobs.append({"id": f"{part['part_id']}:front", "file": part["file"],
                     "view_dir": d, "up": u, "section": None})
        if part["section"]["enabled"]:
            jobs.append({"id": f"{part['part_id']}:section",
                         "file": part["file"],
                         "_defer_section": part, })
        elif part.get("aux_view"):
            # agreed two-view deviation (e.g. end view for a plain rod):
            # a second plain projection instead of a meaningless section
            ad, au = VIEW_DIRS[part["aux_view"]]
            jobs.append({"id": f"{part['part_id']}:aux", "file": part["file"],
                         "view_dir": ad, "up": au, "section": None})
    asm = m.get("assembly_sheet")
    if asm and asm.get("enabled", True):
        if asm.get("view") == "iso":
            k = 1.0 / 3.0 ** 0.5
            d, u = [-k, -k, -k], [0, 0, 1]  # classic iso: viewer at (1,1,1)
        else:
            d, u = VIEW_DIRS[asm["front_view"]]
        jobs.append({"id": "assembly:front", "file": m["assembly_file"],
                     "assembly": True, "view_dir": d, "up": u,
                     "section": None})
    return jobs


def cross(a, b):
    return [a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0]]


def section_job(part: dict) -> dict:
    """Section basis per manifest section.projection (below|right|left).

    Bases are chosen so the section view stays feature-ALIGNED with the
    front view (shared sheet axis, reference style). Consequence: the sight
    direction is away from the placed view (third-angle arrows); if the
    user prefers first-angle arrow convention, flip `s` and the arrowheads
    in cutting_plane() together. The removed half is always the -sight side
    (material between the section viewer and the plane), so plane_normal =
    -s."""
    d, u = VIEW_DIRS[part["front_view"]]
    r = cross(d, u)                          # front-view sheet right, world
    proj = part["section"].get("projection", "below")
    if proj == "below":                      # horizontal cut line
        s, uv = u, [-x for x in d]
    elif proj == "right":                    # vertical cut, view to the right
        s, uv = [-x for x in r], u
    elif proj == "left":                     # vertical cut, view to the left
        s, uv = r, u
    else:
        die(f"{part['part_id']}: bad section.projection {proj!r}")
    return {"id": f"{part['part_id']}:section", "file": part["file"],
            "view_dir": s, "up": uv,
            "section": {"plane_point": None,   # filled after bbox known
                        "plane_normal": [-x for x in s]}}


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
    for part in m["parts"]:
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
    if sec_jobs:
        results.update(run(sec_jobs, "section"))
    return results


# ------------------------------------------------------------- placement

def emit_prims(ctx: mw.SheetContext, prims, offset, props=mw.P):
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
    return ctx.mm(10.0 + 6.6 * (n - 1) + 4.2 + 1.0) if n else 0.0


# Two views of one sheet never come closer than this (printed mm): the
# vertical packer is allowed to eat the preferred gap to make a tall part's
# dimension bands fit, but not to the point where the views read as one.
MIN_VIEW_GAP = 16.0


def place_views(ctx: mw.SheetContext, front, section, projection="below",
                right_band=False, bottom_reserve=0.0, top_reserve=0.0,
                band_w=0.0, band_inboard=False, gutters=(0.0, 0.0)):
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
    if right_band and band_w <= 0.0:
        band_w = ctx.mm(32.0)      # unmeasured band (assembly balloons)
    side_by_side = bool(section) and projection in ("right", "left")

    def sizes(g):
        return ((fw + g + sw, max(fh, sh)) if side_by_side
                else (max(fw, sw), fh + ((section and (g + sh)) or 0)))

    total_w, total_h = sizes(gap)
    band_in_gap = False
    if side_by_side and band_w > 0.0:
        # the annotation column belongs OUTSIDE the group whenever the sheet
        # is wide enough for it AND nothing is in the way; a callout whose
        # own view is not the outermost one has to land in the inter-view
        # gap instead, because its leader would otherwise cross the far view
        if band_inboard or aw - lg - total_w - rg - band_w < 0.0:
            gap = max(gap, min(band_w, aw - lg - rg - fw - sw))
            total_w, total_h = sizes(gap)
            band_in_gap = gap >= band_w
    need_l = lg
    need_r = rg + (0.0 if band_in_gap else band_w)
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
    if total_w > aw - 2 * margin or total_h > ah - 2 * margin:
        die(f"views ({total_w:.0f}x{total_h:.0f} sheet-units) exceed drawing "
            f"area ({aw:.0f}x{ah:.0f}); pick a smaller scale in the manifest")
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
        self.unresolved: list[str] = []
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

    def report(self):
        return {"nudges": self.nudges, "unresolved": self.unresolved,
                "flips": self.flips, "claims": len(self.claims)}




# ------------------------------------------------- linear-dim label geometry
# MusaCAD centers a linear dim's label on the dim-line MIDPOINT, baseline
# 0.4h clear of the line, glyphs growing along the TEXT's own up-vector
# (core/dimension.cpp compute_dim_geometry + Justify::Center). For a vertical
# dim the label reads bottom-to-top, so its up-vector is -x: the value always
# sits to the LEFT of the dim line, whichever side of the view the dim is on.
# Model the ink exactly — a boxed (basic) dim has to be framed where the
# glyphs really are.

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
    ctx._dimstyle_index = {(EXT_OFFSET, EXT_EXTENSION): 0,
                           (EXT_OFFSET, EXT_EXTENSION_CRAMPED): 1}
    return [mw.dimstyle(ctx.mm(3.0), ctx.mm(2.5), color=(255, 0, 255)),
            mw.dimstyle(ctx.mm(3.0), ctx.mm(2.5), color=(255, 0, 255),
                        name="Cramped",
                        ext_extension=ctx.mm(EXT_EXTENSION_CRAMPED))]


def dim_style_index(ctx, ext_offset=EXT_OFFSET, ext_extension=EXT_EXTENSION):
    """Index of a DIMSTYLE with this (ext_offset, ext_extension), in printed
    mm; created on first use. ext_offset is how far from the def point the
    extension line STARTS — the ISO 129-1 interrupted extension line, used
    when a full-length one would run along drawn edges (a cutting-plane
    line's overshoot, say) that the dim itself cannot move away from."""
    key = (round(ext_offset, 3), round(ext_extension, 3))
    idx = ctx._dimstyle_index.get(key)
    if idx is None:
        idx = len(ctx.dimstyles)
        ctx.dimstyles.append(
            mw.dimstyle(ctx.mm(3.0), ctx.mm(2.5), color=(255, 0, 255),
                        name=f"Ext{idx}", ext_offset=ctx.mm(ext_offset),
                        ext_extension=ctx.mm(ext_extension)))
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
    annotations cannot be overprinted by dim ink."""
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


def fmt_val(v: float) -> str:
    return str(round(v)) if abs(v - round(v)) < 0.005 else f"{v:.2f}"


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
            recs.append(mw.dim(0, a, b, lp))
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
            recs.append(mw.dim(0, a, b, lp))
            _claim_dim_ink(ctx, layout, a, b, lp, "v", label)
    return recs


def dim_circles(ctx, layout, job, off, suppress=(), limit=4):
    """Diameter dims for distinct hole radii; angle candidates dodge
    collisions; radii in `suppress` are handled by callouts instead.
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
        cx, cy = c["c"][0] + ox, c["c"][1] + oy
        dia = 2.0 * c["r"]
        label = "%%c" + fmt_val(dia)
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
                prec = None if abs(dia - round(dia)) < 0.005 else 2
                recs.append(mw.dim(3, (cx, cy), (ex, ey), (0, 0),
                                   precision=prec))
                rects[key] = r
                placed = r
                break
        if placed is None:
            # edge feature: no angle clears the outline — standard leader
            # callout into the right band instead of a diameter dim
            kx = vrect[2] + ctx.mm(8.0)
            # angled leaders first — a 0-deg leader rides the feature's
            # own centerline mark
            for dy in (8.0, -8.0, 14.0, -14.0, 20.0, -20.0, 26.0, -26.0,
                       0.0):
                ky = cy + ctx.mm(dy)
                # the LEADER's label sits with its BASELINE on the knee
                ink = mw.leader_text_ink((kx, ky), label, h, ctx.mm(2.5))
                r = layout.place([(ink[0], ink[1])], ink[2] - ink[0],
                                 ink[3] - ink[1], f"dim-{label}")
                if r is not None:
                    ang = math.atan2(ky - cy, kx - cx)
                    tip = (cx + c["r"] * math.cos(ang),
                           cy + c["r"] * math.sin(ang))
                    recs.append(mw.leader(tip, (kx, ky), label, h,
                                     props=mw.P_ANNO))
                    _claim_leader_ink(ctx, layout, tip, (kx, ky),
                                      f"dim-{label}")
                    rects[key] = r
                    placed = r
                    break
                layout.unresolved.pop()
        if placed is None:
            layout.unresolved.append(f"dim-{label}")
    return recs, rects


# Knee stand-offs a callout may use, printed mm out from the view edge. The
# LAST one sizes the annotation band (annotation_band_width): every extra
# step widens the band and eats the opposite gutter, where the height dim
# lives, so keep the ladder short.
CALLOUT_KNEE_STEPS = (10.0, 18.0, 26.0)


def annotation_band_width(ctx, callouts, gdt):
    """Width (sheet units) the right-hand annotation column needs: the widest
    callout stack / feature-control-frame stack that lands there, plus the
    leader knee stand-off and a clearance. EVERY view's callouts are counted
    — stacked views share one band, and a side-by-side section's callouts
    land in the same column as the front view's. Mirrors the sizing inside
    place_callouts()/place_fcfs() — keep the three in step."""
    h_co, h_f = ctx.mm(3.2), ctx.mm(3.0)
    need = 0.0
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


def place_callouts(ctx, layout, callouts, view_jobs, offs):
    """Leader callouts (fit specs, hole callouts) landing in the right-hand
    band. A callout carries either a single "text" or a "lines" stack
    (ASME Y14.5 hole note: optional per-line "sym" prefix glyph, cbore /
    depth); the leader knee meets the FIRST line, the rest stack below,
    left-aligned. The claimed rect covers the whole stack incl. glyphs.
    Returns (records, {(view, circle_r): full_rect}, suppress_radii)."""
    h = ctx.mm(3.2)
    recs, rects, suppress = [], {}, {}
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
        ox, oy = offs[view]
        if "at" in co:
            cx, cy = co["at"][0] + ox, co["at"][1] + oy
            crad = 0.0
        else:
            matches = [p for p in job["visible"] if p["t"] == "circle"
                       and abs(p["r"] - co["circle_r"]) < 0.05]
            if not matches:
                layout.unresolved.append(f"callout-nocircle-{first[:18]}")
                continue
            # topmost row, and within it the feature NEAREST the right-hand
            # band: the shortest leader crosses the least other annotation
            top = max(p["c"][1] for p in matches)
            row = [p for p in matches if abs(p["c"][1] - top) < 0.05]
            c = max(row, key=lambda p: p["c"][0])
            cx, cy = c["c"][0] + ox, c["c"][1] + oy
            crad = c["r"]
        mnb, mxb = job["bounds"]
        s = 1.2 * h                      # prefix glyph cell side
        gap = 0.45 * h                   # glyph-to-text gap
        lh = 1.8 * h                     # line pitch (glyph cells breathe)
        arrow = ctx.mm(2.5)              # dimstyle arrow (leader text offset)
        first_sym = lines[0].get("sym")

        def stack_ink(x1, ky, knee):
            """Ink of everything this callout draws right of the knee: the
            LEADER's own label (baseline AT the knee, 0.4*arrow clear of it)
            or a first-line glyph cell, plus the attach=3 stacked lines."""
            boxes = []
            if not first_sym:
                boxes.append(mw.leader_text_ink(knee, first, h, arrow))
            for k, ln in enumerate(lines):
                if k == 0 and not first_sym:
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

        placed = None
        # step OUTWARD before stepping along: the near column of the band
        # belongs to the view's own location dims (placed first), so a
        # callout parks its knee past them and keeps its short level leader
        for dy, kdx in ((dy, kdx)
                        for dy in (0.0, 8.0, -8.0, -16.0, -24.0, -32.0,
                                   -40.0, -48.0, -56.0, 16.0, 24.0, 32.0)
                        for kdx in CALLOUT_KNEE_STEPS):
            knee_x = mxb[0] + ox + ctx.mm(kdx)
            ky = cy + ctx.mm(dy)         # knee = first-line centre
            x1 = knee_x + ctx.mm(1.5)    # left edge of every line
            # re-flow to the column this knee actually leaves: a band too
            # narrow for the note makes it two or three lines, never a
            # dropped callout (round-8: the 3X %%c27 bore note on sheet 03)
            avail = ctx.draw_area[2] - x1 - ctx.mm(1.0)
            lines = []
            for ln in spec_lines:
                room = avail - ((s + gap) if ln.get("sym") else 0.0)
                parts = fit_lines(ln["text"], h, room)
                lines.append({**ln, "text": parts[0]})
                lines += [{"text": p} for p in parts[1:]]
            first = lines[0]["text"]
            first_sym = lines[0].get("sym")
            ang = math.atan2(ky - cy, knee_x - cx)
            tip = (cx + crad * math.cos(ang), cy + crad * math.sin(ang))
            if not _leader_path_clear(ctx, layout, tip, (knee_x, ky)):
                continue                 # would run along existing dim ink
            ink = stack_ink(x1, ky, (knee_x, ky))
            r = layout.place([(ink[0], ink[1])], ink[2] - ink[0],
                             ink[3] - ink[1], label)
            if r is None:
                layout.unresolved.pop()
            if r is not None:
                # a first-line glyph would sit under the leader's own text,
                # so render such a line ourselves (empty leader content)
                recs.append(mw.leader(tip, (knee_x, ky),
                                      "" if first_sym else first, h,
                                      props=mw.P_ANNO))
                _claim_leader_ink(ctx, layout, tip, (knee_x, ky), label)
                for k, ln in enumerate(lines):
                    if k == 0 and not first_sym:
                        continue
                    cy_l = ky - k * lh
                    tx = x1
                    if ln.get("sym"):
                        recs += mw.callout_sym(ln["sym"],
                                               (x1, cy_l - s / 2), s,
                                               props=mw.P_ANNO)
                        tx = x1 + s + gap
                    recs.append(mw.mtext((tx, cy_l), ln["text"], h,
                                         attach=3, props=mw.P_ANNO))
                placed = r
                break
        if placed is not None:
            if "circle_r" in co:
                rects[(view, round(co["circle_r"], 3))] = placed
            if "suppress_radii" in co:
                suppress.setdefault(view, []).extend(co["suppress_radii"])
            elif co.get("suppress_dia_dim") and "circle_r" in co:
                suppress.setdefault(view, []).append(co["circle_r"])
        else:
            layout.unresolved.append(label)
    return recs, rects, suppress


_TARGET_R = {"holes": None, "bores": None, "bosses": None}


def _target_radius(target: str):
    if "_r" in target:
        try:
            return float(target.rsplit("_r", 1)[1])
        except ValueError:
            return None
    return None


def place_datums(ctx, layout, gdt, view_jobs, offs):
    """Datum boxes (A/B/C) anchored to view edges. Returns (recs, pos)."""
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


def place_fcfs(ctx, layout, gdt, view_jobs, offs, callout_rects, datum_pos,
               dim_rects=None):
    """Feature control frames: stacked under the matching callout or the
    feature's own diameter-dim text when either exists, else leadered to
    the feature into the right band; flatness frames sit beside their
    datum box."""
    dim_rects = dim_rects or {}
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
        key = (view, round(rad, 3)) if rad is not None else None
        anchor = (callout_rects.get(key) or dim_rects.get(key)) \
            if key else None
        rect = None
        if anchor is not None:
            # clear of the anchor's own leader ink (claimed as a corridor
            # half a leader-width around the knee) before stepping down
            cand = [(anchor[0], anchor[1] - stack_h - ctx.mm(k))
                    for k in (2.5, 8.0, 15.0)]
            rect = layout.place(cand, stack_w, stack_h, f"fcf-stack-{tgt}")
            if rect is None and layout.unresolved:
                layout.unresolved.pop()   # fall through to the band
        need_leader = rect is None
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
        emit_stack(frames, rect)
        if need_leader:
            # leader from the feature to the stack MID-LEFT (standard)
            matches = [p for p in job["visible"] if p["t"] == "circle"
                       and rad is not None and abs(p["r"] - rad) < 0.1]
            if matches:
                c = max(matches, key=lambda p: p["c"][1])
                cx, cy = c["c"][0] + ox, c["c"][1] + oy
                land = (rect[0], (rect[1] + rect[3]) / 2)
                ang = math.atan2(land[1] - cy, land[0] - cx)
                tip = (cx + c["r"] * math.cos(ang),
                       cy + c["r"] * math.sin(ang))
                recs.append(mw.line(tip, land, mw.P_ANNO))
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


def _locdim_metrics(ctx, sp, view_jobs, offs):
    """(label ink span, extension-line coords) of one location dim, both on
    the axis the dim measures along — the axis its value reads along too."""
    ox, oy = offs[sp["view"]]
    a = (sp["a"][0] + ox, sp["a"][1] + oy)
    b = (sp["b"][0] + ox, sp["b"][1] + oy)
    j = 1 if sp["orient"] == "v" else 0
    val = abs(b[j] - a[j])
    prec = sp.get("precision")
    if prec is None and abs(val - round(val)) > 0.005:
        prec = 2
    label = f"{val:.{prec}f}" if prec else fmt_val(val)
    h, _ = cramped_dim_style(ctx, label, val, ctx.mm(3.0))
    w = mw.glyph_w(label, h)
    mid = (a[j] + b[j]) / 2.0
    lo = mid - w / 2.0
    return (lo, lo + dim_label_ink(label, h)), (a[j], b[j])


def order_location_dims(ctx, specs, view_jobs, offs):
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
        info.append(_locdim_metrics(ctx, sp, view_jobs, offs)
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
                        soft=()):
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

    Returns (records, floors): floors maps (view, side) -> the outermost
    stand-off (printed mm) used, so the overall extent dims stay outboard."""
    h0 = ctx.mm(3.0)
    # basic-box padding around the value ink: the frame is ink too, so it
    # keeps a full INK_CLEAR white gap off the digits (plus its own pen)
    box_pad = ctx.mm(0.55)
    recs, floors = [], {}
    for sp in order_location_dims(ctx, specs, view_jobs, offs):
        view = sp["view"]
        if view not in view_jobs:
            layout.unresolved.append(f"locdim-noview-{view}")
            continue
        ox, oy = offs[view]
        a = (sp["a"][0] + ox, sp["a"][1] + oy)
        b = (sp["b"][0] + ox, sp["b"][1] + oy)
        vmn, vmx = view_jobs[view]["bounds"]
        vx0, vy0 = vmn[0] + ox, vmn[1] + oy
        vx1, vy1 = vmx[0] + ox, vmx[1] + oy
        orient = sp["orient"]
        val = abs(b[1] - a[1]) if orient == "v" else abs(b[0] - a[0])
        prec = dim_precision(val, sp.get("precision"))
        label = f"{val:.{prec}f}" if prec else fmt_val(val)
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
        dim_kw["style"] = dim_style_index(ctx, ext_off, ext_ext)
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
        recs.append(mw.dim(0, a, b, lp, precision=prec, **dim_kw))
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


def balloons(ctx, layout, asm_job, off, id_by_file, m):
    """P.NO. leader callouts, one per assembly instance, fanned into the
    right band through the collision layout. Each arrow lands on an edge
    that belongs to that instance alone (balloon_landing)."""
    ox, oy = off
    mn, mx = asm_job["bounds"]
    recs = []
    insts = asm_job.get("instances", [])
    file_by_name = {}
    analysis = read_json(Path(m["_analysis_path"]))
    for i in analysis["assembly"]["instances"]:
        if i.get("target_file"):
            file_by_name[i["name"]] = i["target_file"]
    h = ctx.mm(3.5)
    brad = 1.4 * h                       # balloon circle radius
    kx = mx[0] + ox + ctx.mm(16.0)
    # rank by where each arrow will LAND, not by instance centre: knees are
    # handed out top-down, so any disagreement between the two orders makes
    # the leaders cross. Probe the landings from one common reference knee
    # (the top of the band) to break the knee<->landing circularity.
    probe = (kx, mx[1] + oy)
    ys = sorted(range(len(insts)),
                key=lambda k: -balloon_landing(ctx, asm_job, off, k,
                                               probe)[1])
    next_y = mx[1] + oy
    for rank, k in enumerate(ys):
        inst = insts[k]
        pid = disp_no(id_by_file.get(file_by_name.get(inst["name"], ""),
                                     "?"))
        placed = None
        for dy in [4.0 * k for k in range(0, 25)]:
            ky = next_y - ctx.mm(dy)
            tip = balloon_landing(ctx, asm_job, off, k, (kx, ky))
            r = layout.place([(kx, ky - brad)], 2 * brad, 2 * brad,
                             f"balloon-{pid}-{rank}")
            if r is None:
                layout.unresolved.pop()
            if r is not None:
                # leader with empty text (arrow + line), then the circled
                # item number — standard balloon style
                recs.append(mw.leader(tip, (kx, ky), "", h, props=mw.P_ANNO))
                _claim_leader_ink(ctx, layout, tip, (kx, ky),
                                  f"balloon-{pid}-{rank}")
                recs.append(mw.circle((kx + brad, ky), brad, mw.P_ANNO))
                recs.append(mw.cell_text((kx + brad, ky), pid, h,
                                         mw.P_ANNO))
                next_y = ky - 2 * brad - ctx.mm(4.0)
                placed = r
                break
        if placed is None:
            layout.unresolved.append(f"balloon-{pid}-{rank}")
    return recs


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
        "00": m.get("rev", "00"),
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


def rev_row_positional(m=None):
    """Fill revision-table rows from manifest "revisions" (chronological,
    row 1 first); default: a single INITIAL RELEASE row dated today."""
    from common import today
    revs = (m or {}).get("revisions") or [
        {"rev": "00", "desc": "INITIAL RELEASE", "by": "Er. P. PRANAY",
         "date": None}]
    out = []
    for row_y, rv in zip(REV_ROW_YS, revs[:len(REV_ROW_YS)]):
        vals = (rv.get("rev", "-"), rv.get("desc", "-"),
                rv.get("by", "Er. P. PRANAY"), rv.get("approved", "-"),
                rv.get("date") or today())
        for x, val in zip(REV_COLS, vals):
            out.append({"content": "-", "x": x, "y": row_y, "new": val})
    return out


def part_weight_kg(analysis, file):
    for part in analysis["parts"]:
        if part["file"] == file:
            v = part["bodies"][0]["volume_mm3"]
            return v * 7.85e-6   # mild steel 7.85 g/cm3
    return None


def _claim_view(layout, job, off, name):
    mn, mx = job["bounds"]
    layout.claim((mn[0] + off[0], mn[1] + off[1],
                  mx[0] + off[0], mx[1] + off[1]), name, "view")


def _view_label(ctx, layout, job, off, textstr):
    """View caption, centered under the view. Placed AFTER that view's dims
    (reference style: the caption sits below the dimension band), so its
    stand-off ladder has to reach past a couple of dim rows."""
    mn, mx = job["bounds"]
    h = ctx.mm(4.0)
    tw = mw.text_w(textstr, h)
    cx = (mn[0] + mx[0]) / 2 + off[0]
    cands = [(cx - tw / 2, mn[1] + off[1] - ctx.mm(d) - h)
             for d in (8.0, 12.0, 16.0, 20.0, 24.0, 28.0, 32.0)]
    r = layout.place(cands, tw, h, f"label-{textstr}")
    if r is None:
        return []
    return [mw.mtext((cx, r[3]), textstr, h, attach=1)]



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
    recs = [mw.polyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)],
                        closed=True)]
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
    best = _compose_part_sheet(m, part, proj)
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
        trial = _compose_part_sheet(m, part, proj, dx=dx + shift)
        tlo, thi = content_balance(trial[2])
        if trial[2].unresolved or abs(thi - tlo) >= abs(hi - lo):
            break
        dx += shift
        best = trial
    return best[0], best[1], best[2].report()


def _compose_part_sheet(m, part, proj, dx=0.0):
    """One placement pass; `dx` shifts the whole view group (and therefore
    everything anchored to it) horizontally. Returns (ctx, replaced,
    layout) — the live Layout, so the caller can measure the balance."""
    num, den = parse_scale(part["scale"])
    ctx = mw.SheetContext(num, den)
    front = proj[f"{part['part_id']}:front"]
    section = proj.get(f"{part['part_id']}:section") \
        if part["section"]["enabled"] else None
    aux = proj.get(f"{part['part_id']}:aux") if not section else None
    projection = part["section"].get("projection", "below")
    if aux:
        projection = "below"
    gdt = part.get("gdt", {"datums": [], "frames": []})
    callouts = part.get("callouts", [])
    right_band = bool(callouts) or bool(gdt.get("frames"))
    rows_h_pre = mw.PARTS_ROW_H * ctx.t
    second = "section" if section else "aux"
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
    inboard = projection in ("right", "left") and any(
        co.get("view", "front") != outer for co in callouts)
    offs = place_views(ctx, front, section or aux, projection,
                       right_band=right_band,
                       bottom_reserve=rows_h_pre + ctx.mm(16.0),
                       top_reserve=dim_gutter(ctx, part, "front", "top"),
                       band_w=annotation_band_width(ctx, callouts, gdt),
                       band_inboard=inboard, gutters=(lg, rg))
    if dx:
        offs = {k: (v[0] + dx, v[1]) for k, v in offs.items()}
    second_name = "section" if section else ("aux" if aux else None)
    second_label = None
    cut_segs: list = []
    cut_arrows: list = []
    view_jobs = {"front": front}
    if section:
        view_jobs["section"] = section
    if aux:
        view_jobs["aux"] = aux
        offs["aux"] = offs["section"]

    ctx.dimstyles = sheet_dimstyles(ctx)
    layout = Layout(ctx)
    rows_h = mw.PARTS_ROW_H * ctx.t
    ax0, ay0, ax1, _ = ctx.draw_area
    layout.claim((ax0, ay0, ax1, ay0 + rows_h), "parts-rows", "reserved")
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

    replaced = ctx.add_template(titleblock_replacements(m, part),
                                positional=rev_row_positional(m))
    wkg = part_weight_kg(read_json(Path(m["_analysis_path"])), part["file"])
    ctx.entities += mw.parts_row_records(ctx, 0, {
        "pno": disp_no(part["part_id"]), "description": part["description"],
        "qty": str(part["qty"]), "drg_no": part["drg_no"],
        "material": part["material"], "size": "-",
        "weight": f"{wkg:.2f} kg" if wkg else "-",
        "remarks": "-"})
    ctx.entities += mw.date_records(ctx, today())

    emit_prims(ctx, front["visible"], offs["front"])
    emit_prims(ctx, front["hidden"], offs["front"], P_HIDDEN)

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

    # every black line the views draw, as segments: dimension ink is tested
    # against THIS, not just against other annotations' bounding boxes
    geom = view_geometry(ctx, view_jobs, offs, cut_segs)
    # annotations through the collision layout: dims and callouts first
    # (they anchor FCF stacks), then datums, frames, notes.
    # Feature-location dims go FIRST of all: standard practice puts the
    # dimensions in the column nearest the view and pushes leader callouts
    # outboard of them, never the other way round.
    loc_recs, loc_floors = place_location_dims(
        ctx, layout, part.get("location_dims", []), view_jobs, offs, geom,
        cut_arrows)
    ctx.entities += loc_recs
    co_recs, co_rects, suppress = place_callouts(ctx, layout, callouts,
                                                 view_jobs, offs)
    ctx.entities += co_recs
    dim_rects: dict = {}

    def floors_for(view, wside, hside):
        return {"width": loc_floors.get((view, wside), 0.0),
                "height": loc_floors.get((view, hside), 0.0)}

    if part.get("front_extent_dims", True):
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
                           suppress=suppress.get("front", ()))
    ctx.entities += recs
    dim_rects.update({("front", k): v for k, v in rr.items()})
    if section:
        # shared-axis views don't repeat the shared extent dim: below shares
        # width with the front view, right/left share height
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
                               suppress=suppress.get("section", ()))
        ctx.entities += recs
        dim_rects.update({("section", k): v for k, v in rr.items()})
    if aux:
        recs, rr = dim_circles(ctx, layout, aux, offs["section"],
                               suppress=suppress.get("aux", ()))
        ctx.entities += recs
        dim_rects.update({("aux", k): v for k, v in rr.items()})
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
    frames = gdt.get("frames", [])
    anchored = [f for f in frames if f["target"] != "datum_A_face"]
    on_datum = [f for f in frames if f["target"] == "datum_A_face"]
    ctx.entities += place_fcfs(ctx, layout, {"frames": anchored}, view_jobs,
                               offs, co_rects, {}, dim_rects)
    dat_recs, datum_pos = place_datums(ctx, layout, gdt, view_jobs, offs)
    ctx.entities += dat_recs
    ctx.entities += place_fcfs(ctx, layout, {"frames": on_datum}, view_jobs,
                               offs, co_rects, datum_pos, dim_rects)
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


def build_assembly_sheet(m, proj):
    asm = m["assembly_sheet"]
    num, den = parse_scale(asm["scale"])
    ctx = mw.SheetContext(num, den)
    job = proj["assembly:front"]
    rows_h = mw.PARTS_ROW_H * ctx.t * len(m["parts"])
    if asm.get("view") == "iso":
        # NTS pictorial: shrink to leave room for balloons + notes
        vw = job["bounds"][1][0] - job["bounds"][0][0]
        vh = job["bounds"][1][1] - job["bounds"][0][1]
        f = min(1.0, ctx.mm(100.0) / vw, ctx.mm(95.0) / vh)
        if f < 1.0:
            job = scale_job(job, f)
    offs = place_views(ctx, job, None, right_band=asm.get("balloons", True),
                       bottom_reserve=rows_h + ctx.mm(36.0))
    ctx.dimstyles = sheet_dimstyles(ctx)
    layout = Layout(ctx)
    box_recs, stamp_rect = pictorial_box(ctx, layout, shaded_png("assembly"))
    ctx.entities += box_recs
    ctx._stamp_rect = stamp_rect
    ax0, ay0, ax1, _ = ctx.draw_area
    layout.claim((ax0, ay0, ax1, ay0 + rows_h), "parts-rows", "reserved")
    _claim_view(layout, job, offs["front"], "assembly-view")
    replaced = ctx.add_template(titleblock_replacements(m, asm),
                                positional=rev_row_positional(m))
    analysis = read_json(Path(m["_analysis_path"]))
    for row, part in enumerate(m["parts"]):
        wkg = part_weight_kg(analysis, part["file"])
        ctx.entities += mw.parts_row_records(ctx, row, {
            "pno": disp_no(part["part_id"]), "description": part["description"],
            "qty": str(part["qty"]), "drg_no": part["drg_no"],
            "material": part["material"], "size": "-",
            "weight": f"{wkg:.2f} kg" if wkg else "-",
            "remarks": "-"})
    ctx.entities += mw.date_records(ctx, today())
    emit_prims(ctx, job["visible"], offs["front"])
    emit_prims(ctx, job["hidden"], offs["front"], P_HIDDEN)
    if asm.get("view") == "iso":
        ctx.entities += _view_label(ctx, layout, job, offs["front"],
                                    "ISOMETRIC VIEW")
    else:
        ctx.entities += dim_extents(ctx, layout, job, offs["front"])
    if asm.get("balloons", True):
        id_by_file = {p["file"]: p["part_id"] for p in m["parts"]}
        ctx.entities += balloons(ctx, layout, job, offs["front"],
                                 id_by_file, m)
    ctx.entities += place_notes(ctx, layout, asm.get("fab_notes", []),
                                rows_h)
    return ctx, replaced, layout.report()


def main() -> None:
    man_path = MANIFESTS_DIR / "manifest.json"
    if "--manifest" in sys.argv:
        man_path = Path(sys.argv[sys.argv.index("--manifest") + 1])
    if not man_path.exists():
        die(f"no {man_path} — run /finalise first")
    m = read_json(man_path)
    if not m.get("locked"):
        die("manifest is not locked — /finalise must lock it")
    analysis_path = Path(__file__).resolve().parent.parent / "reports" / \
        "analysis.json"
    m["_analysis_path"] = str(analysis_path)
    analysis = read_json(analysis_path)

    proj = run_projections(m, analysis)
    OUTPUT_MUSA_DIR.mkdir(parents=True, exist_ok=True)

    has_asm = bool(m.get("assembly_sheet") and m["assembly_sheet"].get("enabled", True))
    total = len(m["parts"]) + (1 if has_asm else 0)
    sheets = []
    problems = []
    stamp_rects = {}
    for idx, part in enumerate(m["parts"], start=1):
        ctx, replaced, lay = build_part_sheet(m, part, proj)
        stamp_rects[part["drg_no"]] = ctx._stamp_rect
        out = OUTPUT_MUSA_DIR / f"{part['drg_no']}.musa"
        out.write_text(ctx.render())
        sheets.append({"file": str(out), "drg_no": part["drg_no"],
                       "description": part["description"],
                       "scale": part["scale"], "window": ctx.window,
                       "kind": "part", "section": part["section"]["enabled"],
                       "projection": part["section"].get("projection",
                                                         "below"),
                       "replaced_fields": replaced, "layout": lay,
                       "stamp": {"png": f"output/work/shaded/"
                                        f"{part['part_id']}.png",
                                 "rect": list(stamp_rects[part['drg_no']])}})
        if lay["unresolved"]:
            problems.append(f"{part['drg_no']}: {lay['unresolved']}")
        print(f"sheet {idx}/{total}: {out.name}  scale {part['scale']}  "
              f"(nudges={lay['nudges']}, unresolved="
              f"{len(lay['unresolved'])})")
    if m.get("assembly_sheet") and m["assembly_sheet"].get("enabled", True):
        ctx, replaced, lay = build_assembly_sheet(m, proj)
        stamp_rects[m["assembly_sheet"]["drg_no"]] = ctx._stamp_rect
        asm_out = OUTPUT_MUSA_DIR / f"{m['assembly_sheet']['drg_no']}.musa"
        asm_out.write_text(ctx.render())
        sheets.append({"file": str(asm_out),
                       "drg_no": m["assembly_sheet"]["drg_no"],
                       "description": m["assembly_sheet"]["description"],
                       "scale": m["assembly_sheet"]["scale"],
                       "window": ctx.window, "kind": "assembly",
                       "section": False, "replaced_fields": replaced,
                       "layout": lay,
                       "stamp": {"png": "output/work/shaded/assembly.png",
                                 "rect": list(stamp_rects[
                                     m["assembly_sheet"]["drg_no"]])}})
        if lay["unresolved"]:
            problems.append(f"{m['assembly_sheet']['drg_no']}: "
                            f"{lay['unresolved']}")
        print(f"sheet {total}/{total}: {asm_out.name}  scale "
              f"{m['assembly_sheet']['scale']}")
    write_json(OUTPUT_MUSA_DIR / "sheets.json",
               {"manifest": str(man_path), "sheets": sheets})
    print(f"wrote {len(sheets)} sheets + output/musa/sheets.json")
    if problems:
        die("layout unresolved (never ship an overlapping sheet):\n  "
            + "\n  ".join(problems))


if __name__ == "__main__":
    main()
