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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (MANIFESTS_DIR, OUTPUT_MUSA_DIR, SCRATCH_DIR, die,
                    read_json, run_freecad, today, write_json)
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
    asm = m["assembly_sheet"]
    if asm.get("view") == "iso":
        k = 1.0 / 3.0 ** 0.5
        d, u = [-k, -k, -k], [0, 0, 1]   # classic iso: viewer at (+1,+1,+1)
    else:
        d, u = VIEW_DIRS[asm["front_view"]]
    jobs.append({"id": "assembly:front", "file": m["assembly_file"],
                 "assembly": True, "view_dir": d, "up": u, "section": None})
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


def place_views(ctx: mw.SheetContext, front, section, projection="below",
                right_band=False, bottom_reserve=0.0):
    """Offsets placing the views in the drawing area. `below`: front upper
    band, section under it, SHARED x offset (feature alignment). right/left:
    side by side, SHARED y offset. Errors if they don't fit."""
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
    if section and projection in ("right", "left"):
        total_w, total_h = fw + gap + sw, max(fh, sh)
    else:
        total_w, total_h = max(fw, sw), fh + ((section and (gap + sh)) or 0)
    if total_w > aw - 2 * margin or total_h > ah - 2 * margin:
        die(f"views ({total_w:.0f}x{total_h:.0f} sheet-units) exceed drawing "
            f"area ({aw:.0f}x{ah:.0f}); pick a smaller scale in the manifest")
    # horizontal center, shifted left when a right-hand annotation band
    # (callouts / FCF stacks / balloons) is needed; vertical balance leaves
    # slightly more room below for labels + notes
    cx = (ax0 + ax1) / 2 - (ctx.mm(16.0) if right_band else 0.0)
    # top headroom fits the width dim; bottom keeps room for labels,
    # datum boxes and the notes/parts-rows band
    free = ah - total_h
    centered = free / 2 - ctx.mm(6.0)
    # roomy sheets get >= 22 mm top headroom (two-tier dim chains: inner
    # feature dim + outer overall dim); tight sheets keep the old floor
    vpad = max(ctx.mm(14.0),
               min(free - bottom_reserve, max(centered, ctx.mm(24.0))))
    top_y = ay1 - vpad
    if section and projection in ("right", "left"):
        left_x = cx - total_w / 2
        fx = left_x if projection == "right" else left_x + sw + gap
        sx = left_x + fw + gap if projection == "right" else left_x
        f_off = (fx - fmn[0], top_y - fmx[1])
        # shared y offset keeps world features level across both views
        offs = {"front": f_off, "section": (sx - smn[0], f_off[1])}
    else:
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
                "claims": len(self.claims)}




def _place_dim(ctx, layout, cands_with_line, w, h, label):
    """Try (text_rect_pos, line_band_rect) candidates: accept the first
    whose TEXT rect fits AND whose dim-line band (incl. arrowheads) does
    not cross existing text/annotation claims. Line-vs-line crossings are
    normal drafting and ignored."""
    for i, (pos, band) in enumerate(cands_with_line):
        rect = (pos[0], pos[1], pos[0] + w, pos[1] + h)
        if not layout.inside(rect) or layout.collides(rect):
            continue
        if layout.collides(band, ignore=("line", "view", "reserved")):
            continue
        if i > 0:
            layout.nudges += 1
        layout.claim(rect, label)
        return rect
    layout.unresolved.append(label)
    return None


def _claim_dim_ink(ctx, layout, a, b, line_pt, orient, label):
    """Claim the dim line band (incl. arrowheads) and the two extension-line
    corridors so later annotations cannot be overprinted by dim ink."""
    arrow = ctx.mm(2.5)
    band = ctx.mm(1.6)
    # rendered extension lines OVERSHOOT the dim line by ext_extension
    # (1.5 sheet units for our dimstyle) — claim past it
    over = ctx.mm(2.2)
    if orient == "h":
        x0, x1 = min(a[0], b[0]) - arrow, max(a[0], b[0]) + arrow
        y = line_pt[1]
        layout.claim((x0, y - band, x1, y + band), f"dimline-{label}", "line")
        for px, py in (a, b):
            yy0, yy1 = sorted((py, y))
            layout.claim((px - band / 4, yy0 - (over if yy0 == y else 0),
                          px + band / 4, yy1 + (over if yy1 == y else 0)),
                         f"dimext-{label}", "line")
    else:
        y0, y1 = min(a[1], b[1]) - arrow, max(a[1], b[1]) + arrow
        x = line_pt[0]
        layout.claim((x - band, y0, x + band, y1), f"dimline-{label}", "line")
        for px, py in (a, b):
            xx0, xx1 = sorted((px, x))
            layout.claim((xx0 - (over if xx0 == x else 0), py - band / 4,
                          xx1 + (over if xx1 == x else 0), py + band / 4),
                         f"dimext-{label}", "line")


def fmt_val(v: float) -> str:
    return str(round(v)) if abs(v - round(v)) < 0.005 else f"{v:.2f}"


def dim_extents(ctx, layout, job, off, *, width_side="top",
                height_side="left", which=("w", "h")):
    """Overall width/height linear dims, auto-nudged offsets. `which`
    restricts the set — a shared-axis section view must not repeat the
    front view's dim for the shared direction."""
    mn, mx = job["bounds"]
    ox, oy = off
    x0, y0, x1, y1 = mn[0] + ox, mn[1] + oy, mx[0] + ox, mx[1] + oy
    h = ctx.mm(3.0)
    recs = []
    if "w" in which:
        label = fmt_val(x1 - x0)
        tw = mw.text_w(label, h)
        sign = 1 if width_side == "top" else -1
        ybase = y1 if width_side == "top" else y0
        arrow, bandw = ctx.mm(2.5), ctx.mm(1.6)
        cands = []
        for d in (8.0, 11.0, 14.0, 17.0, 18.5, 20.0, 24.0, 28.0,
                  32.0, 36.0):
            yl = ybase + sign * ctx.mm(d)
            # MusaCAD renders linear-dim text LEFT-JUSTIFIED at
            # midpoint + 0.4h, 0.4h above the line (dimension.cpp)
            tx = (x0 + x1) / 2 + 0.4 * h
            ty = yl + 0.4 * h
            band = (x0 - arrow, yl - bandw, x1 + arrow, yl + bandw)
            cands.append(((tx, ty), band))
        r = _place_dim(ctx, layout, cands, tw, 1.2 * h, f"dim-w-{label}")
        if r is not None:
            yline = r[1] - 0.4 * h
            recs.append(mw.dim(0, (x0, ybase), (x1, ybase),
                               ((x0 + x1) / 2, yline)))
            _claim_dim_ink(ctx, layout, (x0, ybase), (x1, ybase),
                           ((x0 + x1) / 2, yline), "h", label)
    if "h" in which:
        label = fmt_val(y1 - y0)
        tw = mw.text_w(label, h)
        sign = -1 if height_side == "left" else 1
        xbase = x0 if height_side == "left" else x1
        arrow, bandw = ctx.mm(2.5), ctx.mm(1.6)
        cands = []
        for d in (8.0, 11.0, 14.0, 17.0, 18.5, 20.0, 24.0, 28.0,
                  32.0, 36.0):
            xl = xbase + sign * ctx.mm(d)
            # rotated text reads bottom-to-top: its 0.4h "above" offset is
            # LEFT of the line regardless of which side the dim sits on
            cands.append(((xl - 1.2 * h, (y0 + y1) / 2 + 0.4 * h),
                          (xl - bandw, y0 - arrow, xl + bandw, y1 + arrow)))
        r = _place_dim(ctx, layout, cands, 1.2 * h, tw, f"dim-h-{label}")
        if r is not None:
            xline = (r[2] + 0.4 * h) if sign < 0 else (r[0] - 0.4 * h)
            recs.append(mw.dim(0, (xbase, y0), (xbase, y1),
                               (xline, (y0 + y1) / 2)))
            _claim_dim_ink(ctx, layout, (xbase, y0), (xbase, y1),
                           (xline, (y0 + y1) / 2), "v", label)
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
            for dy in (0.0, 8.0, -8.0, 14.0):
                ky = cy + ctx.mm(dy)
                r = layout.place([(kx + ctx.mm(1.5), ky - 0.7 * h)],
                                 tw, 1.4 * h, f"dim-{label}")
                if r is not None:
                    ang = math.atan2(ky - cy, kx - cx)
                    tip = (cx + c["r"] * math.cos(ang),
                           cy + c["r"] * math.sin(ang))
                    recs.append(mw.leader(tip, (kx, ky), label, h,
                                     props=mw.P_ANNO))
                    rects[key] = r
                    placed = r
                    break
                layout.unresolved.pop()
        if placed is None:
            layout.unresolved.append(f"dim-{label}")
    return recs, rects


def place_callouts(ctx, layout, callouts, view_jobs, offs):
    """Leader callouts (fit specs etc.) landing in the right-hand band.
    Returns (records, {(view, circle_r): text_rect}, suppress_radii)."""
    h = ctx.mm(3.2)
    recs, rects, suppress = [], {}, {}
    for co in callouts:
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
                layout.unresolved.append(
                    f"callout-nocircle-{co['text'][:18]}")
                continue
            c = max(matches, key=lambda p: p["c"][1])   # topmost hole
            cx, cy = c["c"][0] + ox, c["c"][1] + oy
            crad = c["r"]
        mnb, mxb = job["bounds"]
        knee_x = mxb[0] + ox + ctx.mm(10.0)
        tw = mw.text_w(co["text"], h)
        placed = None
        for dy in (0.0, 8.0, -8.0, -16.0, -24.0, -32.0, -40.0, -48.0, -56.0,
                   16.0, 24.0, 32.0):
            ky = cy + ctx.mm(dy)
            r = layout.place([(knee_x + ctx.mm(1.5), ky - 0.7 * h)],
                             tw, 1.4 * h, f"callout-{co['text'][:18]}")
            if r is None:
                layout.unresolved.pop()
            if r is not None:
                ang = math.atan2(ky - cy, knee_x - cx)
                tip = (cx + crad * math.cos(ang),
                       cy + crad * math.sin(ang))
                recs.append(mw.leader(tip, (knee_x, ky), co["text"], h,
                                          props=mw.P_ANNO))
                placed = r
                break
        if placed is not None:
            if "circle_r" in co:
                rects[(view, round(co["circle_r"], 3))] = placed
            if co.get("suppress_dia_dim") and "circle_r" in co:
                suppress.setdefault(view, []).append(co["circle_r"])
        else:
            layout.unresolved.append(f"callout-{co['text'][:18]}")
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
            # outside the view corners first (clear of the centered
            # SECTION label), then under the edge
            cands = [(x0 - s - ctx.mm(2.0), y0 - ctx.mm(8.0) - s),
                     (x1 + ctx.mm(2.0), y0 - ctx.mm(8.0) - s)] + \
                    [((x0 + (x1 - x0) * f) - s / 2, y0 - ctx.mm(d) - s)
                     for d in (10.0, 20.0)
                     for f in (0.12, 0.88, 0.3, 0.7, 0.5)]
            tip_of = lambda r: (min(max((r[0] + r[2]) / 2, x0), x1), y0)
        else:  # long_edge
            cands = [(x1 + ctx.mm(dx), y0 + (y1 - y0) * f - s / 2)
                     for dx in (6.0, 3.0, 12.0)
                     for f in (0.75, 0.6, 0.9, 0.45, 0.3, 0.15)]
            tip_of = lambda r: (x1, (r[1] + r[3]) / 2)
        r = layout.place(cands, s, s, f"datum-{d['label']}")
        if r is None:
            continue
        dl, _ = mw.datum_label((r[0], r[1]), d["label"], tip_of(r), h,
                               props=mw.P_ANNO)
        recs += dl
        pos[d["label"]] = r
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
            r = layout.place([(a[2] + ctx.mm(2.5), a[1]),
                              (a[0] - fw - ctx.mm(2.5), a[1]),
                              (a[0], a[1] - fh - ctx.mm(2.0))],
                             fw, fh, f"fcf-{f['symbol']}")
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
            cand = [(anchor[0], anchor[1] - stack_h - ctx.mm(k))
                    for k in (1.5, 8.0, 15.0)]
            rect = layout.place(cand, stack_w, stack_h, f"fcf-stack-{tgt}")
            if rect is None and layout.unresolved:
                layout.unresolved.pop()   # fall through to the band
        need_leader = rect is None
        if rect is None:
            mnb, mxb = job["bounds"]
            band_x = mxb[0] + ox + ctx.mm(12.0)
            for dy in (0.0, -10.0, 10.0, -20.0, -30.0, -40.0, 20.0, 30.0):
                cy_t = (mnb[1] + mxb[1]) / 2 + oy + ctx.mm(dy)
                rect = layout.place([(band_x, cy_t)], stack_w, stack_h,
                                    f"fcf-stack-{tgt}")
                if rect is not None:
                    break
                layout.unresolved.pop()
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



def place_location_dims(ctx, layout, specs, view_jobs, offs):
    """Manifest-driven feature-location dims (pitch, edge references,
    depths): explicit def points in view coords + measuring orientation +
    placement side; optional TYP tag (magenta, claimed)."""
    h = ctx.mm(3.0)
    recs = []
    for sp in specs:
        view = sp["view"]
        if view not in view_jobs:
            layout.unresolved.append(f"locdim-noview-{view}")
            continue
        ox, oy = offs[view]
        ax, ay = sp["a"][0] + ox, sp["a"][1] + oy
        bx, by = sp["b"][0] + ox, sp["b"][1] + oy
        vmn, vmx = view_jobs[view]["bounds"]
        vx0, vy0 = vmn[0] + ox, vmn[1] + oy
        vx1, vy1 = vmx[0] + ox, vmx[1] + oy
        val = abs(by - ay) if sp["orient"] == "v" else abs(bx - ax)
        prec = sp.get("precision")
        if prec is None and abs(val - round(val)) > 0.005:
            prec = 2   # never let the precision-0 style round 17.5 to 18
        label = f"{val:.{prec}f}" if prec else fmt_val(val)
        tw = mw.text_w(label, h)
        placed = None
        if sp["orient"] == "v":
            sign = -1 if sp["side"] == "left" else 1
            # offset from the VIEW edge (interior features must clear the
            # whole view), never from the def points alone
            xb = max(ax, bx, vx1) if sign > 0 else min(ax, bx, vx0)
            arrow, bandw = ctx.mm(2.5), ctx.mm(1.6)
            cands = []
            for d in (10.0, 13.0, 16.0, 19.0, 22.0, 26.0, 30.0, 34.0,
                      38.0, 42.0, 46.0):
                xl = xb + sign * ctx.mm(d)
                cands.append(((xl - 1.2 * h, (ay + by) / 2 + 0.4 * h),
                              (xl - bandw, min(ay, by) - arrow,
                               xl + bandw, max(ay, by) + arrow)))
            r = _place_dim(ctx, layout, cands, 1.2 * h, tw,
                           f"locdim-{label}")
            if r is not None:
                xline = r[2] + 0.4 * h if sign < 0 else r[0] - 0.4 * h
                recs.append(mw.dim(0, (ax, ay), (bx, by),
                                   (xline, (ay + by) / 2),
                                   precision=prec))
                _claim_dim_ink(ctx, layout, (ax, ay), (bx, by),
                               (xline, (ay + by) / 2), "v", label)
                placed = r
        else:
            sign = -1 if sp["side"] == "bottom" else 1
            yb = max(ay, by, vy1) if sign > 0 else min(ay, by, vy0)
            arrow, bandw = ctx.mm(2.5), ctx.mm(1.6)
            cands = []
            for d in (10.0, 13.0, 16.0, 19.0, 22.0, 26.0, 30.0, 34.0,
                      38.0, 42.0, 46.0):
                yl = yb + sign * ctx.mm(d)
                band = (min(ax, bx) - arrow, yl - bandw,
                        max(ax, bx) + arrow, yl + bandw)
                cands.append((((ax + bx) / 2 + 0.4 * h, yl + 0.4 * h),
                              band))
            r = _place_dim(ctx, layout, cands, tw, 1.4 * h,
                           f"locdim-{label}")
            if r is not None:
                yline = r[1] - 0.4 * h if sign > 0 else r[3] + 0.4 * h
                recs.append(mw.dim(0, (ax, ay), (bx, by),
                                   ((ax + bx) / 2, yline),
                                   precision=prec))
                _claim_dim_ink(ctx, layout, (ax, ay), (bx, by),
                               ((ax + bx) / 2, yline), "h", label)
                placed = r
        if placed is None:
            continue   # _place_dim already recorded the unresolved label
        if sp.get("typ"):
            ttw = mw.text_w("TYP", h)
            cands = [(placed[2] + ctx.mm(1.0), placed[1]),
                     (placed[0] - ttw - ctx.mm(1.0), placed[1]),
                     (placed[0], placed[1] - 1.4 * h - ctx.mm(0.8)),
                     (placed[0], placed[3] + ctx.mm(0.8))]
            # a TYP tag rides beside its dim text, same stand-off from the
            # dim line as the text itself -> line-kind claims don't apply
            r = layout.place(cands, ttw, 1.4 * h, "typ-tag",
                             ignore=("line",))
            if r is not None:
                recs.append(mw.mtext((r[0], r[1]), "TYP", h, attach=6,
                                     props=mw.P_ANNO))
    return recs


def place_notes(ctx, layout, notes, rows_h):
    """Numbered NOTES block (word-wrapped): bottom-left above the parts
    rows, else bottom-right, else the band beside the lower view."""
    if not notes:
        return []
    h = ctx.mm(3.0)
    ax0, ay0, ax1, ay1 = ctx.draw_area
    lh = 1.55 * h
    y = ay0 + rows_h + ctx.mm(3.0)
    # progressively narrower columns until one fits a free band
    for limit in (46, 34, 26, 20, 16):
        lines = []
        for i, t in enumerate(notes, start=1):
            wrapped = _wrap(t, limit)
            lines.append(f"{i}. {wrapped[0]}")
            lines += [f"   {c}" for c in wrapped[1:]]
        height = (len(lines) + 1) * lh
        width = max(mw.text_w(t, h) for t in lines + ["NOTES:"])
        cands = [(ax0 + ctx.mm(3.0), y), (ax1 - width - ctx.mm(3.0), y),
                 (ax0 + ctx.mm(3.0), y + ctx.mm(12.0)),
                 (ax1 - width - ctx.mm(3.0), y + ctx.mm(12.0)),
                 (ax0 + ctx.mm(3.0), y + ctx.mm(30.0)),
                 # top-left band (clear of the pictorial box) — last resort
                 (ax0 + ctx.mm(3.0), ay1 - height - ctx.mm(3.0))]
        r = layout.place(cands, width, height, "notes-block")
        if r is None:
            layout.unresolved.remove("notes-block")
            continue
        recs = [mw.mtext((r[0], r[1] + k * lh), t, h, attach=6)
                for k, t in enumerate(reversed(lines))]
        recs.append(mw.mtext((r[0], r[1] + len(lines) * lh), "NOTES:", h,
                             attach=6))
        return recs
    layout.unresolved.append("notes-block")
    return []


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
    All arrow/label zones are claimed against later annotations."""
    mn, mx = front_job["bounds"]
    ox, oy = f_off
    ext = ctx.mm(9.0)        # line overshoot beyond the view outline
    end = ctx.mm(6.0)        # thick end-stroke length along the line
    shaft = ctx.mm(7.0)      # arrow shaft length
    asz = ctx.mm(4.0)        # solid arrowhead length
    h = ctx.mm(4.0)          # letter height
    recs = []
    if projection == "below":                # horizontal line, sight up
        y = mn[1] + (mx[1] - mn[1]) * frac + oy
        x0, x1 = mn[0] + ox - ext, mx[0] + ox + ext
        recs = [mw.line((x0, y), (x1, y), P_CUTLINE),
                mw.line((x0, y), (x0 + end, y), P_THICK),
                mw.line((x1 - end, y), (x1, y), P_THICK)]
        half = max(asz / 2, mw.text_w(label, h) / 2)
        for xa in (x0, x1):
            recs += [mw.line((xa, y), (xa, y + shaft), P_THICK),
                     arrowhead((xa, y + shaft + asz), (0, 1), asz),
                     mw.mtext((xa + mw.MC_DX * h,
                               y + shaft + asz + ctx.mm(1.5)), label, h,
                              attach=7)]
            layout.claim((xa - half, y - ctx.mm(1.0), xa + half,
                          y + shaft + asz + ctx.mm(1.5) + h),
                         f"cutlabel-{label}")
    else:                                    # vertical line, sight sideways
        adx = -1 if projection == "right" else 1
        x = mn[0] + (mx[0] - mn[0]) * frac + ox
        y0, y1 = mn[1] + oy - ext, mx[1] + oy + ext
        recs = [mw.line((x, y0), (x, y1), P_CUTLINE),
                mw.line((x, y0), (x, y0 + end), P_THICK),
                mw.line((x, y1 - end), (x, y1), P_THICK)]
        for ya, side in ((y0, -1), (y1, 1)):
            recs += [mw.line((x, ya), (x + adx * shaft, ya), P_THICK),
                     arrowhead((x + adx * (shaft + asz), ya), (adx, 0), asz),
                     mw.mtext((x + adx * shaft, ya + side * ctx.mm(2.5)),
                              label, h, attach=1 if side < 0 else 7)]
            xr = sorted((x, x + adx * (shaft + asz)))
            layout.claim((xr[0], min(ya - ctx.mm(2.5) - h, ya - asz / 2)
                          if side < 0 else ya - asz / 2,
                          xr[1], max(ya + ctx.mm(2.5) + h, ya + asz / 2)
                          if side > 0 else ya + asz / 2),
                         f"cutlabel-{label}")
    return recs


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


def balloons(ctx, layout, asm_job, off, id_by_file, m):
    """P.NO. leader callouts, one per assembly instance, fanned into the
    right band through the collision layout."""
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
    ys = sorted(range(len(insts)), key=lambda k: -insts[k]["center"][1])
    next_y = mx[1] + oy
    for rank, k in enumerate(ys):
        inst = insts[k]
        pid = disp_no(id_by_file.get(file_by_name.get(inst["name"], ""),
                                     "?"))
        tip = (inst["center"][0] + ox, inst["center"][1] + oy)
        placed = None
        for dy in [4.0 * k for k in range(0, 25)]:
            ky = next_y - ctx.mm(dy)
            r = layout.place([(kx, ky - brad)], 2 * brad, 2 * brad,
                             f"balloon-{pid}-{rank}")
            if r is None:
                layout.unresolved.pop()
            if r is not None:
                # leader with empty text (arrow + line), then the circled
                # item number — standard balloon style
                recs.append(mw.leader(tip, (kx, ky), "", h, props=mw.P_ANNO))
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



REV_ROW_Y = 1587.9783693373156
REV_ROW = [  # (x, new) for the "-" placeholders of revision-table row 1
    (80.72697303959285, "00"),
    (281.445892556294, "INITIAL RELEASE"),
    (557.502500627168, "Er. P. PRANAY"),
    (646.231785341799, "-"),
    (734.961070056459, None),   # DATE cell - filled at build time
]


def rev_row_positional():
    from common import today
    out = []
    for x, val in REV_ROW:
        out.append({"content": "-", "x": x, "y": REV_ROW_Y,
                    "new": val if val is not None else today()})
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
    mn, mx = job["bounds"]
    h = ctx.mm(4.0)
    tw = mw.text_w(textstr, h)
    cx = (mn[0] + mx[0]) / 2 + off[0]
    cands = [(cx - tw / 2, mn[1] + off[1] - ctx.mm(d) - h)
             for d in (8.0, 12.0, 16.0)]
    r = layout.place(cands, tw, h, f"label-{textstr}")
    if r is None:
        return []
    return [mw.mtext((cx, r[3]), textstr, h, attach=1)]



def pictorial_box(ctx, layout):
    """Reserved corner box for the shaded pictorial: top-right of the
    drawing area, fixed printed size. The box frame is drawn; the interior
    stays EMPTY in the .musa (no raster records exist) — export.py stamps
    the PNG into it via musa_plot --stamp. Returns (records, stamp_rect)."""
    ax0, ay0, ax1, ay1 = ctx.draw_area
    w, hh = ctx.mm(42.0), ctx.mm(36.0)
    x1, y1 = ax1 - ctx.mm(2.0), ay1 - ctx.mm(2.0)
    x0, y0 = x1 - w, y1 - hh
    layout.claim((x0 - ctx.mm(1.0), y0 - ctx.mm(1.0),
                  x1 + ctx.mm(1.0), y1 + ctx.mm(1.0)),
                 "pictorial-box", "reserved")
    recs = [mw.polyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)],
                        closed=True)]
    inset = ctx.mm(1.0)
    return recs, (x0 + inset, y0 + inset, w - 2 * inset, hh - 2 * inset)


def build_part_sheet(m, part, proj):
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
    offs = place_views(ctx, front, section or aux, projection,
                       right_band=right_band,
                       bottom_reserve=rows_h_pre + ctx.mm(16.0))
    second_name = "section" if section else ("aux" if aux else None)
    view_jobs = {"front": front}
    if section:
        view_jobs["section"] = section
    if aux:
        view_jobs["aux"] = aux
        offs["aux"] = offs["section"]

    ctx.dimstyles = [mw.dimstyle(ctx.mm(3.0), ctx.mm(2.5),
                                 color=(255, 0, 255))]
    layout = Layout(ctx)
    rows_h = mw.PARTS_ROW_H * ctx.t
    ax0, ay0, ax1, _ = ctx.draw_area
    layout.claim((ax0, ay0, ax1, ay0 + rows_h), "parts-rows", "reserved")
    box_recs, stamp_rect = pictorial_box(ctx, layout)
    ctx.entities += box_recs
    ctx._stamp_rect = stamp_rect
    _claim_view(layout, front, offs["front"], "front-view")
    if second_name:
        _claim_view(layout, view_jobs[second_name], offs["section"],
                    f"{second_name}-view")

    replaced = ctx.add_template(titleblock_replacements(m, part),
                                positional=rev_row_positional())
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
        ctx.entities += cutting_plane(ctx, layout, front, offs["front"],
                                      label, projection, frac)
        emit_prims(ctx, section["visible"], offs["section"])
        ctx.entities += section_hatches(ctx, section, offs["section"])
        ctx.entities += _view_label(ctx, layout, section, offs["section"],
                                    f"SECTION {label}-{label}")
    if aux:
        emit_prims(ctx, aux["visible"], offs["section"])
        emit_prims(ctx, aux["hidden"], offs["section"], P_HIDDEN)
        ctx.entities += _view_label(ctx, layout, aux, offs["section"],
                                    "END VIEW")

    # annotations through the collision layout: callouts and dims first
    # (they anchor FCF stacks), then datums, frames, notes
    co_recs, co_rects, suppress = place_callouts(ctx, layout, callouts,
                                                 view_jobs, offs)
    ctx.entities += co_recs
    dim_rects: dict = {}
    # feature-anchored location dims first: chains then stack outward and
    # the freer extents dims walk past their claimed ink
    ctx.entities += place_location_dims(
        ctx, layout, part.get("location_dims", []), view_jobs, offs)
    ext_sides = part.get("extents_sides", {})
    ctx.entities += dim_extents(ctx, layout, front, offs["front"],
                                width_side=ext_sides.get("width", "top"),
                                height_side=ext_sides.get("height", "left"))
    recs, rr = dim_circles(ctx, layout, front, offs["front"],
                           suppress=suppress.get("front", ()))
    ctx.entities += recs
    dim_rects.update({("front", k): v for k, v in rr.items()})
    if section:
        # shared-axis views don't repeat the shared extent dim: below shares
        # width with the front view, right/left share height
        ctx.entities += dim_extents(ctx, layout, section, offs["section"],
                                    width_side="bottom",
                                    height_side="left"
                                    if projection != "right" else "right",
                                    which=("h",) if projection == "below"
                                    else ("w",))
        recs, rr = dim_circles(ctx, layout, section, offs["section"],
                               suppress=suppress.get("section", ()))
        ctx.entities += recs
        dim_rects.update({("section", k): v for k, v in rr.items()})
    if aux:
        recs, rr = dim_circles(ctx, layout, aux, offs["section"],
                               suppress=suppress.get("aux", ()))
        ctx.entities += recs
        dim_rects.update({("aux", k): v for k, v in rr.items()})
    # notes before datums/FCFs: the block is the largest, least flexible
    # annotation; small framed items dodge it far more easily
    notes = part.get("fab_notes", []) + part.get("extra_notes", [])
    ctx.entities += place_notes(ctx, layout, notes, rows_h)
    dat_recs, datum_pos = place_datums(ctx, layout, gdt, view_jobs, offs)
    ctx.entities += dat_recs
    ctx.entities += place_fcfs(ctx, layout, gdt, view_jobs, offs, co_rects,
                               datum_pos, dim_rects)
    return ctx, replaced, layout.report()



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
    ctx.dimstyles = [mw.dimstyle(ctx.mm(3.0), ctx.mm(2.5),
                                 color=(255, 0, 255))]
    layout = Layout(ctx)
    box_recs, stamp_rect = pictorial_box(ctx, layout)
    ctx.entities += box_recs
    ctx._stamp_rect = stamp_rect
    ax0, ay0, ax1, _ = ctx.draw_area
    layout.claim((ax0, ay0, ax1, ay0 + rows_h), "parts-rows", "reserved")
    _claim_view(layout, job, offs["front"], "assembly-view")
    replaced = ctx.add_template(titleblock_replacements(m, asm),
                                positional=rev_row_positional())
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

    total = len(m["parts"]) + 1
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
