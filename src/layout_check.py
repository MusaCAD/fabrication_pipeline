# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Pranay Kiran
"""Stage 4c — the layout check (issue #22).

The value check (#14) reads what the sheet SAYS. This one reads how it
LOOKS. The layout engine guarantees that the boxes it placed do not
overlap, which is not the same thing: a leader is a line, not a box, and
the engine's own claims are the only thing it ever tests against. So a
leader could run along the outline it points at, two leaders could cross,
a datum triangle could sit on the diameter sign of its own callout — and
every check the pipeline had would pass. In a real project all of those
reached the vendor and were found by eye.

This check reads the FINISHED .musa, the same way the value check does:
the engine's opinion of the sheet does not come into it. What it reports:

  leader-crossing   two leaders cross each other, so each appears to point
                    at the other's feature;
  leader-on-outline a leader runs ALONG drawn geometry (within 0.5 mm for
                    more than a few mm), where the arrow can no longer be
                    told from the part (ISO 128-22);
  ink-on-text       a symbol, datum triangle, frame or balloon lies on
                    text — the covered glyph is usually the one that
                    carries the meaning;
  text-off-sheet    text outside the drawing area altogether;
  leaders-parallel  two leaders closer than 2 mm and running together (a
                    WARNING: it reads badly but says nothing false).

Everything but the warning fails /export, as an unwaived value difference
does, and `layout_waivers` in the manifest waives one by name.

What it does NOT see: a DIM's label, which MusaCAD composes at plot time
(the value check reads those through --dump-dims, where the position is
not reported), and a leader crossing another VIEW, which is a question
about where a balloon was planned rather than about the ink.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import musa_writer as mw

P_ANNO = mw.P_ANNO

# printed millimetres
ALONG_GAP = 0.5         # closer than this, two near-parallel lines merge
ALONG_MM = 3.0          # ... and running together this far is "along"
PARALLEL_GAP = 2.0      # leaders closer than this read as one bundle
PARALLEL_MM = 4.0
CROSS_EPS = 0.5         # how far from its own ends a crossing has to be
INK_TOL = 0.15          # ink may touch text by this much (pen width)
ANGLE_SIN = 0.026       # sin(1.5 deg): "running along", not crossing


# How many fields each record carries BEFORE its seven-int property tail
# (musa_writer's own grammar). A POLYLINE's count depends on its points,
# and a DIM may carry a 15-field override block AFTER the tail.
_FIELDS = {
    "LINE": 4, "CIRCLE": 3, "ARC": 5,
    "POLYLINE": lambda rest: 2 + 2 * int(rest[1]),
    "MTEXT": 8, "TEXT": 5, "LEADER": 6, "DIM": 8,
}


class Rec:
    __slots__ = ("kind", "nums", "props", "content", "font")

    def __init__(self, kind, nums, props, content="", font=""):
        self.kind, self.nums, self.props = kind, nums, props
        self.content, self.font = content, font

    @property
    def anno(self) -> bool:
        return self.props == P_ANNO


TEXT_KINDS = ("MTEXT", "TEXT", "LEADER")


def parse(musa_text: str) -> list[Rec]:
    """Every record of a sheet, as numbers + property tail (+ content).

    The grammar is musa_writer's own, read back: a record is one line, and
    TEXT/MTEXT/LEADER carry a content line and a font line after it."""
    out: list[Rec] = []
    lines = musa_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        parts = line.split()
        if not parts or parts[0] in ("MUSACAD", "END"):
            continue
        kind = parts[0]
        nums, props = [], ""
        rest = parts[1:]
        if kind == "HATCH":
            # HATCH n (len pts...)* scale angle ox oy props, then pattern
            try:
                nloops = int(rest[0])
            except (ValueError, IndexError):
                continue
            j, loops = 1, []
            for _ in range(nloops):
                np_ = int(rest[j])
                j += 1
                loops.append([(float(rest[j + 2 * k]),
                               float(rest[j + 2 * k + 1]))
                              for k in range(np_)])
                j += 2 * np_
            props = " ".join(rest[j + 4:])
            r = Rec(kind, [c for lp in loops for c in lp], props,
                    content=lines[i] if i < len(lines) else "")
            r.nums = loops                   # HATCH keeps its loops
            i += 1                           # the pattern name
            out.append(r)
            continue
        # the property tail is SEVEN ints, and they are numbers like any
        # other: a record's fields have to be counted, not sniffed (its
        # first property, the layer, is "0" — read as a coordinate it
        # swallowed the whole tail and every record came out as geometry)
        n_fields = _FIELDS.get(kind)
        if n_fields is None:
            continue
        if callable(n_fields):
            n_fields = n_fields(rest)
        try:
            nums = [float(t) for t in rest[:n_fields]]
        except ValueError:
            continue
        props = " ".join(rest[n_fields:n_fields + 7])
        content = font = ""
        if kind in TEXT_KINDS:
            content = lines[i] if i < len(lines) else ""
            font = lines[i + 1] if i + 1 < len(lines) else ""
            i += 2
        out.append(Rec(kind, nums, props, content, font))
    return out


def unescape(s: str) -> str:
    return s.replace("\\n", "\n").replace("\\\\", "\\")


# ------------------------------------------------------------------ ink

def text_ink(r: Rec):
    """The AABB of the glyphs a text record really draws."""
    body = unescape(r.content)
    if not body.strip():
        return None
    if r.kind == "MTEXT":
        x, y, width, h, _rot, wf, ls, attach = r.nums[:8]
        return mw.mtext_ink((x, y), body.split("\n"), h, attach=int(attach),
                            line_spacing=ls, width_factor=wf, width=width)
    if r.kind == "TEXT":
        x, y, h, _rot, just = r.nums[:5]
        w = mw.glyph_ink_w(body, h)
        x0 = x - (w / 2 if int(just) == 1 else (w if int(just) == 2 else 0))
        return (x0, y - mw.glyph_descent(body, h), x0 + w, y + h)
    if r.kind == "LEADER":
        _tx, _ty, kx, ky, h = r.nums[:5]
        return mw.leader_text_ink((kx, ky), body, h, 2.5)
    return None


def segments(r: Rec):
    """A record's drawn strokes as line segments (arcs and circles are
    walked at 24 steps, which is finer than anything this measures)."""
    k = r.kind
    if k == "LINE":
        return [((r.nums[0], r.nums[1]), (r.nums[2], r.nums[3]))]
    if k == "POLYLINE":
        closed, n = int(r.nums[0]), int(r.nums[1])
        pts = [(r.nums[2 + 2 * i], r.nums[3 + 2 * i]) for i in range(n)]
        segs = list(zip(pts, pts[1:]))
        if closed and len(pts) > 2:
            segs.append((pts[-1], pts[0]))
        return segs
    if k in ("CIRCLE", "ARC"):
        cx, cy, rad = r.nums[0], r.nums[1], r.nums[2]
        a0, a1 = (r.nums[3], r.nums[4]) if k == "ARC" else (0.0, 2 * math.pi)
        if a1 <= a0:
            a1 += 2 * math.pi
        n = 24
        pts = [(cx + rad * math.cos(a0 + (a1 - a0) * i / n),
                cy + rad * math.sin(a0 + (a1 - a0) * i / n))
               for i in range(n + 1)]
        return list(zip(pts, pts[1:]))
    if k == "HATCH":
        segs = []
        for loop in r.nums:
            segs += list(zip(loop, loop[1:])) + [(loop[-1], loop[0])]
        return segs
    return []


def rect_of(segs):
    xs = [p[0] for s in segs for p in s]
    ys = [p[1] for s in segs for p in s]
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def overlaps(a, b, pad=0.0) -> bool:
    return not (a[2] + pad <= b[0] or b[2] + pad <= a[0]
                or a[3] + pad <= b[1] or b[3] + pad <= a[1])


def collinear_run(p0, p1, q0, q1, gap) -> float:
    """Length of q that runs ALONG p (near-parallel, within `gap`)."""
    ux, uy = p1[0] - p0[0], p1[1] - p0[1]
    L = math.hypot(ux, uy)
    vx, vy = q1[0] - q0[0], q1[1] - q0[1]
    M = math.hypot(vx, vy)
    if L < 1e-9 or M < 1e-9:
        return 0.0
    ux, uy, vx, vy = ux / L, uy / L, vx / M, vy / M
    if abs(ux * vy - uy * vx) > ANGLE_SIN:
        return 0.0
    for q in (q0, q1):
        if abs((q[0] - p0[0]) * -uy + (q[1] - p0[1]) * ux) > gap:
            return 0.0
    t0, t1 = sorted(((q0[0] - p0[0]) * ux + (q0[1] - p0[1]) * uy,
                     (q1[0] - p1[0]) * ux + (q1[1] - p1[1]) * uy + L))
    return max(0.0, min(L, t1) - max(0.0, t0))


def segs_cross(a0, a1, b0, b1, eps) -> bool:
    rx, ry = a1[0] - a0[0], a1[1] - a0[1]
    sx, sy = b1[0] - b0[0], b1[1] - b0[1]
    den = rx * sy - ry * sx
    if abs(den) < 1e-12:
        return False
    qpx, qpy = b0[0] - a0[0], b0[1] - a0[1]
    t = (qpx * sy - qpy * sx) / den
    u = (qpx * ry - qpy * rx) / den
    la = math.hypot(rx, ry) or 1.0
    lb = math.hypot(sx, sy) or 1.0
    return (eps / la < t < 1.0 - eps / la
            and eps / lb < u < 1.0 - eps / lb)


def seg_in_rect(a, b, rect, tol) -> bool:
    """Does the segment enter the rect (shrunk by `tol`)? Endpoints inside
    count; so does a crossing of any of its four edges."""
    r = (rect[0] + tol, rect[1] + tol, rect[2] - tol, rect[3] - tol)
    if r[0] >= r[2] or r[1] >= r[3]:
        return False
    for p in (a, b):
        if r[0] <= p[0] <= r[2] and r[1] <= p[1] <= r[3]:
            return True
    corners = [(r[0], r[1]), (r[2], r[1]), (r[2], r[3]), (r[0], r[3])]
    for i in range(4):
        if segs_cross(a, b, corners[i], corners[(i + 1) % 4], 0.0):
            return True
    return False


# -------------------------------------------------------------- the check

SYMBOL_KINDS = ("POLYLINE", "CIRCLE", "ARC", "HATCH")


def check_sheet(musa_text: str, draw_area, unit_mm=1.0, table_band=None,
                where="") -> list[dict]:
    """Every layout fault this sheet carries.

    `draw_area` is the sheet's own drawing area and `unit_mm` how many
    printed millimetres one sheet unit is (num/den), so every tolerance
    here is in PRINTED millimetres whatever the scale. `table_band` is the
    parts table's rows, which are the template's furniture rather than the
    drawing's ink."""
    recs = parse(musa_text)
    u = lambda mm: mm / unit_mm if unit_mm else mm        # noqa: E731

    def in_area(rect):
        return rect is not None and overlaps(rect, draw_area)

    def furniture(rect):
        return table_band is not None and rect is not None \
            and overlaps(rect, table_band)

    texts = []
    for r in recs:
        if r.kind not in TEXT_KINDS:
            continue
        ink = text_ink(r)
        if ink is None or not in_area(ink) or furniture(ink):
            continue
        texts.append((r, ink))

    leaders = [(tuple(r.nums[:2]), tuple(r.nums[2:4]),
                mw.esc(unescape(r.content))[:18] or "balloon")
               for r in recs if r.kind == "LEADER"]

    geom = []
    for r in recs:
        if r.anno or r.kind not in ("LINE", "POLYLINE", "ARC", "CIRCLE"):
            continue
        for a, b in segments(r):
            rect = (min(a[0], b[0]), min(a[1], b[1]),
                    max(a[0], b[0]), max(a[1], b[1]))
            if in_area(rect) and not furniture(rect):
                geom.append((a, b))

    out = []

    def add(kind, what, severity="fail"):
        out.append({"kind": kind, "what": (where + ": " if where else "")
                    + what, "severity": severity})

    # 1. leaders crossing each other
    seen = set()
    for i, (t1, k1, l1) in enumerate(leaders):
        for t2, k2, l2 in leaders[i + 1:]:
            if segs_cross(t1, k1, t2, k2, u(CROSS_EPS)):
                key = tuple(sorted((l1, l2)))
                if key in seen:
                    continue
                seen.add(key)
                add("leader-crossing",
                    f"the leaders of {l1!r} and {l2!r} cross each other")

    # 2. a leader running along drawn geometry
    for t, k, lab in leaders:
        worst, which = 0.0, None
        lr = (min(t[0], k[0]) - u(ALONG_GAP), min(t[1], k[1]) - u(ALONG_GAP),
              max(t[0], k[0]) + u(ALONG_GAP), max(t[1], k[1]) + u(ALONG_GAP))
        for a, b in geom:
            if not overlaps(lr, (min(a[0], b[0]), min(a[1], b[1]),
                                 max(a[0], b[0]), max(a[1], b[1]))):
                continue
            run = collinear_run(t, k, a, b, u(ALONG_GAP))
            if run > worst:
                worst, which = run, (a, b)
        if worst > u(ALONG_MM):
            add("leader-on-outline",
                f"the leader of {lab!r} runs along the outline for "
                f"{worst * unit_mm:.1f} mm — the arrow cannot be told from "
                "the part it points at")

    # 3. ink on text: a symbol, triangle, frame or balloon over a label
    for r in recs:
        if not r.anno or r.kind not in SYMBOL_KINDS:
            continue
        segs = segments(r)
        srect = rect_of(segs)
        if srect is None or not in_area(srect):
            continue
        filled = r.kind == "HATCH" and "SOLID" in r.content.upper()
        for tr, ink in texts:
            if not overlaps(srect, ink):
                continue
            if filled and _poly_rect_overlap(r.nums[0], ink, u(INK_TOL)):
                hit = True
            else:
                hit = any(seg_in_rect(a, b, ink, u(INK_TOL))
                          for a, b in segs)
            if hit:
                add("ink-on-text",
                    f"{_name(r, filled)} lies on the text "
                    f"{unescape(tr.content).strip()[:28]!r}")
                break

    # 4. text outside the drawing area
    for r, ink in ((r, text_ink(r)) for r in recs if r.kind in TEXT_KINDS):
        if ink is None or not r.anno:
            continue
        if not (ink[0] >= draw_area[0] - 1e-6 and ink[1] >= draw_area[1] - 1e-6
                and ink[2] <= draw_area[2] + 1e-6
                and ink[3] <= draw_area[3] + 1e-6):
            add("text-off-sheet",
                f"{unescape(r.content).strip()[:28]!r} is outside the "
                "drawing area")

    # 5. leaders running parallel and close (a warning: nothing is wrong,
    #    it only reads as one line with two arrows)
    for i, (t1, k1, l1) in enumerate(leaders):
        for t2, k2, l2 in leaders[i + 1:]:
            run = collinear_run(t1, k1, t2, k2, u(PARALLEL_GAP))
            if run > u(PARALLEL_MM):
                add("leaders-parallel",
                    f"the leaders of {l1!r} and {l2!r} run together for "
                    f"{run * unit_mm:.1f} mm closer than {PARALLEL_GAP} mm",
                    severity="warn")
    return out


def _name(r: Rec, filled: bool) -> str:
    if filled:
        return "a filled symbol (an arrowhead or a datum triangle)"
    if r.kind == "CIRCLE":
        return "a circle (a balloon or a detail boundary)"
    if r.kind == "POLYLINE":
        return "a boxed symbol (a frame or a datum box)"
    return "an arc"


def _poly_rect_overlap(poly, rect, tol) -> bool:
    """Does a filled polygon cover part of a text's ink?"""
    r = (rect[0] + tol, rect[1] + tol, rect[2] - tol, rect[3] - tol)
    if r[0] >= r[2] or r[1] >= r[3]:
        return False
    for p in poly:                       # a corner of the fill inside
        if r[0] <= p[0] <= r[2] and r[1] <= p[1] <= r[3]:
            return True
    corners = [(r[0], r[1]), (r[2], r[1]), (r[2], r[3]), (r[0], r[3])]
    for c in corners:                    # ... or the text inside the fill
        if _point_in_poly(poly, c):
            return True
    edges = list(zip(poly, poly[1:])) + [(poly[-1], poly[0])]
    return any(segs_cross(a, b, corners[i], corners[(i + 1) % 4], 0.0)
               for a, b in edges for i in range(4))


def _point_in_poly(poly, p) -> bool:
    inside = False
    n = len(poly)
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        if (y0 > p[1]) != (y1 > p[1]):
            xx = x0 + (p[1] - y0) * (x1 - x0) / ((y1 - y0) or 1e-12)
            if p[0] < xx:
                inside = not inside
    return inside


def faults(findings) -> list[dict]:
    return [f for f in findings if f.get("severity") != "warn"]


def warnings(findings) -> list[dict]:
    return [f for f in findings if f.get("severity") == "warn"]


def report(findings) -> list[str]:
    return [f"{f['kind'].upper()}: {f['what']}" for f in findings]
