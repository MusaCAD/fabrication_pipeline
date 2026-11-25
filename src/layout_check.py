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
  leader-across-view a leader reaches its feature THROUGH another view,
                    so it points at the part across ink that says nothing
                    about it;
  leader-across-own-view
                    a SIZE leader that crosses the view it points into,
                    where the near end of the very edge its arrow sits on
                    — the same feature — would have crossed nothing
                    (issue #33);
  leader-across-dimension
                    a leader crosses a dimension LINE, or the arrows of
                    the cutting plane, or the pictorial box — ink that
                    belongs to something else entirely;
  leader-across-extension-line
                    a leader crosses an extension line, named by the
                    dimension that draws it — a FAULT (issue #29), not
                    the warning it used to be: the engine now tries the
                    feature's other edge before it accepts one;
  leader-through-arrowhead
                    a leader runs through ANOTHER leader's arrowhead,
                    where the two arrows meet and neither reads as an
                    arrow any more (issue #29). The crossing is AT the
                    other leader's end point, which is exactly where
                    leader-crossing does not look;
  leader-hidden     a leader runs UNDER the pictorial, whose stamped
                    image is opaque, so that part of it is not printed
                    at all (issue #30). Never waivable: a leader the
                    vendor cannot see points at nothing;
  callout-column-narrow
                    a callout column too narrow for its own words, so
                    the note comes out one word a line;
  annotation-crowded
                    two annotations closer than the clearance the engine
                    itself keeps between them, so they read as one block
                    — a diameter dim's label dropped into a callout
                    column, say (issue #29);
  datum-beside-wrong-callout
                    a datum box nearer another callout's text than its
                    own, or on that other callout's own LINE, so the
                    sheet names the wrong feature as the datum;
  text-off-sheet    text outside the drawing area altogether;
  leaders-parallel  two leaders closer than 2 mm and running together (a
                    WARNING: it reads badly but says nothing false).

Everything but the warning fails /export, as an unwaived value difference
does, and `layout_waivers` in the manifest waives one by name — except
leader-hidden, which no waiver covers.

A balloon is named by its own NUMBER (issue #30). A balloon's LEADER
carries no text of its own, so every finding on a sheet of them read "the
leader of 'balloon'": a reviewer could not tell which crossing they were
looking at, and one waiver string covered the lot, now and on every later
redraw.

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
VIEW_GAP = 6.0          # ink further apart than this is another view
VIEW_MIN = 5.0          # ... and a view is bigger than this each way
ALONG_GAP = 0.5         # closer than this, two near-parallel lines merge
ALONG_MM = 3.0          # ... and running together this far is "along"
PARALLEL_GAP = 2.0      # leaders closer than this read as one bundle
PARALLEL_MM = 4.0
CROSS_EPS = 0.5         # how far from its own ends a crossing has to be
INK_TOL = 0.15          # ink may touch text by this much (pen width)
ANGLE_SIN = 0.026       # sin(1.5 deg): "running along", not crossing
ARROW_MM = 2.5          # a leader's arrowhead, when the sheet states no
ARROW_HALF = 0.18       # dimstyle; half-width as a fraction of its length
CROWD_MM = 0.8          # two annotations closer than this read as one
# ... and so do two closer than this FRACTION of their height, whatever
# the sheet's text size: the lines of one callout's own stack stand 0.8
# of a height apart, so a stranger nearer than half of that is read as
# one of them (issue #33, draw.ANNO_READ_FRAC)
CROWD_FRAC = 0.4
HIDDEN_MM = 0.5         # this much of a leader under the pictorial is gone
# A size leader that could have landed on the near end of the very edge
# it points at, and crosses this much more of the drawing by not doing so
# (issue #33)
LONG_WAY_CROSS = 3      # ink crossings away from its own arrow, at least
LONG_WAY_SAVE = 2       # ... of which the near end would save this many
LONG_WAY_MOVE = 2.0     # and the arrow has to move this far to get there


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


def dim_styles(musa_text: str):
    """(arrow size per DIMSTYLE index, DIMLFAC per index, arrow TYPE per
    index), read off the sheet's own style records.

    parse() skips both — they draw nothing themselves — but a LEADER's
    arrowhead is drawn at its style's arrow size and in its shape (a
    balloon that ends inside a part's own cut area ends in a DOT, issue
    #32), and what a DIM measures is multiplied by its style's DIMLFAC
    before it is printed, which is how a detail view's dimensions read
    true (musa_writer.dimunits)."""
    arrows, lfacs, types = [], {}, []
    for line in musa_text.splitlines():
        tok = line.split()
        if not tok:
            continue
        try:
            if tok[0] == "DIMSTYLE" and len(tok) > 3:
                arrows.append(float(tok[2]))
                types.append(int(tok[3]))
            elif tok[0] == "DIMUNITS" and len(tok) > 5:
                lfacs[int(tok[1])] = float(tok[5])
        except ValueError:
            continue
    return arrows, lfacs, types


def arrowhead(tip, knee, size, kind=0):
    """The terminator MusaCAD draws at a leader's tip.

    `kind` is the style's ArrowType (core/properties.hpp). 0, the house
    style, is a filled triangle: apex ON the tip, base `size` along the
    leader towards the knee, half-width 0.18*size (scene_snapshot.cpp
    hands the leader's own dimstyle arrow size to append_arrowhead;
    core/dimension.cpp's proportions, the ones draw.dim_arrowhead models
    for a lettered dim). 3 is the DOT a balloon ends in when it lands
    inside its part's own cut area (issue #32) — a filled diamond of
    radius 0.35*size about the tip, dimension.cpp's own."""
    ux, uy = knee[0] - tip[0], knee[1] - tip[1]
    ln = math.hypot(ux, uy)
    if ln < 1e-9 or size <= 0.0:
        return []
    ux, uy = ux / ln, uy / ln
    px, py = -uy, ux
    if kind == 3:
        r = size * 0.35
        return [(tip[0] + px * r, tip[1] + py * r),
                (tip[0] + ux * r, tip[1] + uy * r),
                (tip[0] - px * r, tip[1] - py * r),
                (tip[0] - ux * r, tip[1] - uy * r)]
    bx, by = tip[0] + ux * size, tip[1] + uy * size
    return [(tip[0], tip[1]),
            (bx + px * size * ARROW_HALF, by + py * size * ARROW_HALF),
            (bx - px * size * ARROW_HALF, by - py * size * ARROW_HALF)]


def seg_meets_poly(a, b, poly) -> bool:
    """Does the segment touch a filled polygon — crossing an edge, or
    lying inside it?"""
    if not poly:
        return False
    edges = list(zip(poly, poly[1:] + poly[:1]))
    if any(segs_cross(a, b, p, q, 0.0) for p, q in edges):
        return True
    return any(_point_in_poly(poly, p) for p in (a, b))


def inside_run(a, b, rect) -> float:
    """How much of the segment lies INSIDE the rect (Liang-Barsky): what
    an opaque stamp over that rect would hide."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, a[0] - rect[0]), (dx, rect[2] - a[0]),
                 (-dy, a[1] - rect[1]), (dy, rect[3] - a[1])):
        if abs(p) < 1e-12:
            if q < 0.0:
                return 0.0
            continue
        r = q / p
        if p < 0.0:
            t0 = max(t0, r)
        else:
            t1 = min(t1, r)
    if t1 <= t0:
        return 0.0
    return (t1 - t0) * math.hypot(dx, dy)


def balloon_numbers(recs, texts):
    """[(centre, radius, number)] for every balloon on the sheet.

    draw.balloons_fan draws the circle beside the knee and the number in
    it, so the number is a short text whose middle lies inside an
    annotation circle (issue #30)."""
    out = []
    for r in recs:
        if not r.anno or r.kind != "CIRCLE":
            continue
        c, rad = (r.nums[0], r.nums[1]), r.nums[2]
        for tr, ink in texts:
            body = unescape(tr.content).strip()
            if not body or len(body) > 4:
                continue
            mid = ((ink[0] + ink[2]) / 2.0, (ink[1] + ink[3]) / 2.0)
            if math.dist(mid, c) <= rad:
                out.append((c, rad, body))
                break
    return out


def leader_name(content: str, knee, balloons) -> str:
    """What to call this leader in a finding: its own label, else the
    NUMBER of the balloon its knee carries (issue #30)."""
    lab = mw.esc(unescape(content))[:18]
    if lab:
        return lab
    for c, rad, num in balloons:
        if math.dist(knee, c) <= rad * 1.2:
            return f"balloon {num}"
    return ""


# -------------------------------------------------------------- the check

SYMBOL_KINDS = ("POLYLINE", "CIRCLE", "ARC", "HATCH")


def check_sheet(musa_text: str, draw_area, unit_mm=1.0, table_band=None,
                where="", boxes=(), blocks=()) -> list[dict]:
    """Every layout fault this sheet carries.

    `draw_area` is the sheet's own drawing area and `unit_mm` how many
    printed millimetres one sheet unit is (num/den), so every tolerance
    here is in PRINTED millimetres whatever the scale. `table_band` is the
    parts table's rows and `boxes` the pictorial box: both are the
    pipeline's own furniture rather than the drawing's ink, and a frame
    the pipeline drew is not a view of the part.

    `blocks` is the TEMPLATE's furniture inside the drawing area, as
    (name, rect): on a widened sheet the title block and the revision
    table keep their A4 width and the area is the whole sheet inside the
    border, so their own rules and text are inside it and are not the
    drawing's ink — while an annotation that lands on them is a fault
    the old area test used to catch for free (issue #28)."""
    recs = parse(musa_text)
    u = lambda mm: mm / unit_mm if unit_mm else mm        # noqa: E731

    def in_area(rect):
        return rect is not None and overlaps(rect, draw_area)

    def on_block(rect):
        """The template furniture this rect lies on, if any."""
        for name, b in blocks:
            if rect is not None and overlaps(rect, b):
                return name
        return None

    def furniture(rect):
        if rect is None:
            return False
        if table_band is not None and overlaps(rect, table_band):
            return True
        if on_block(rect):
            return True
        pad = u(1.5)
        return any(b[0] - pad <= rect[0] and rect[2] <= b[2] + pad
                   and b[1] - pad <= rect[1] and rect[3] <= b[3] + pad
                   for b in boxes)

    texts = []
    for r in recs:
        if r.kind not in TEXT_KINDS:
            continue
        ink = text_ink(r)
        if ink is None or not in_area(ink) or furniture(ink):
            continue
        texts.append((r, ink))

    balloons = balloon_numbers(recs, texts)
    arrow_mm, lfacs, arrow_type = dim_styles(musa_text)
    boxed = [rect_of(segments(r)) for r in recs
             if r.anno and r.kind == "POLYLINE" and int(r.nums[1]) == 4]

    leaders = []
    for r in recs:
        if r.kind != "LEADER":
            continue
        tip, knee = tuple(r.nums[:2]), tuple(r.nums[2:4])
        style = int(r.nums[5]) if len(r.nums) > 5 else 0
        size = arrow_mm[style] if 0 <= style < len(arrow_mm) else u(ARROW_MM)
        kind = arrow_type[style] if 0 <= style < len(arrow_type) else 0
        leaders.append((tip, knee, leader_name(r.content, knee, balloons),
                        arrowhead(tip, knee, size, kind)))
    dims, exts = dim_ink(recs, u(0.72), u(1.5), lfacs)

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

    def add(kind, what, severity="fail", waivable=True):
        f = {"kind": kind, "what": (where + ": " if where else "") + what,
             "severity": severity}
        if not waivable:
            f["waivable"] = False
        out.append(f)

    def who(lab, tip) -> str:
        """This leader, by name. A balloon carries its own number
        (issue #30); a leader with no label at all is named by where its
        arrow lands, which is all there is left to tell it by."""
        return repr(lab) if lab else (
            f"the leader landing at {tip[0] * unit_mm:.0f}, "
            f"{tip[1] * unit_mm:.0f} mm")

    # 1. leaders crossing each other
    # by the PAIR, not by their labels: every balloon's leader carries
    # the same empty label, and keying on that reported one crossing for
    # a whole fan of them (issue #25)
    for i, (t1, k1, l1, _h1) in enumerate(leaders):
        for t2, k2, l2, _h2 in leaders[i + 1:]:
            if segs_cross(t1, k1, t2, k2, u(CROSS_EPS)):
                add("leader-crossing",
                    f"the leaders of {who(l1, t1)} and {who(l2, t2)} "
                    "cross each other")

    # 2. a leader running along drawn geometry
    for t, k, lab, _h in leaders:
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
                f"the leader of {who(lab, t)} runs along the outline for "
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

    # 3b. a DIMENSION's own ink over a label (issue #29): the shared
    #     extension line of two location dims running through a callout's
    #     words. A DIM's label is composed at plot time and is not on the
    #     sheet, so the only ink here is the lines dimension.cpp draws
    #     from the record — which is exactly what the reader sees.
    seen: set = set()
    for what, segs in (("a dimension line", dims),
                       ("an extension line", exts)):
        for a, b, dname in segs:
            for tr, ink in texts:
                # into the BODY of the label, not across the corner of
                # its box: a line a quarter of a letter's height from the
                # top of an "A" grazes the box and covers no glyph
                if not seg_in_rect(a, b, ink,
                                   max(u(INK_TOL), 0.25 * _text_h(tr))):
                    continue
                body = unescape(tr.content).strip()[:28]
                if (what, dname, body) in seen:
                    continue
                seen.add((what, dname, body))
                add("ink-on-text",
                    f"{what} ({dname}) runs through the text {body!r}")

    # 4. a leader that reaches its feature through ANOTHER view
    views = view_clusters(geom, u(VIEW_GAP), u(VIEW_MIN))
    for t, k, lab, _h in leaders:
        own = _own_view(views, t, u(2.0))
        for i, (bb, segs) in enumerate(views):
            if i == own:
                continue
            if any(segs_cross(t, k, a, b, 0.0) for a, b in segs):
                add("leader-across-view",
                    f"the leader of {who(lab, t)} reaches its feature "
                    f"through the view at ({bb[0] * unit_mm:.0f}, "
                    f"{bb[1] * unit_mm:.0f}) mm, which says nothing about "
                    "the part it points at — balloon or call it out in "
                    "the view that does")
                break

    # 5. a leader across ink that belongs to something else: a dimension
    #    line, an extension line, the cutting plane's arrows, the
    #    pictorial box. EVERY kind it crosses is reported, not the first
    #    one found: a balloon that crossed a dimension line on its way
    #    into the pictorial was reported for the dimension alone, and the
    #    pictorial — which hides it altogether — went unsaid (issue #30).
    arrows = cut_arrows(recs)
    box_edges = []
    for b in boxes:
        pts = [(b[0], b[1]), (b[2], b[1]), (b[2], b[3]), (b[0], b[3])]
        box_edges += [(p, q, "") for p, q in zip(pts, pts[1:] + pts[:1])]
    for t, k, lab, _h in leaders:
        # the pictorial's image is OPAQUE, so a leader that runs under it
        # is not printed there at all: no waiver makes that readable
        hidden = max((inside_run(t, k, b) for b in boxes), default=0.0)
        if hidden > u(HIDDEN_MM):
            add("leader-hidden",
                f"the leader of {who(lab, t)} runs {hidden * unit_mm:.1f} "
                "mm under the pictorial, which is stamped over it: that "
                "much of the leader is not printed at all", waivable=False)
        for kind, what, segs in (
                ("leader-across-dimension", "a dimension line", dims),
                ("leader-across-dimension", "the cutting plane's arrow",
                 [(a, b, "") for a, b in arrows]),
                ("leader-across-dimension", "the pictorial box",
                 [] if hidden > u(HIDDEN_MM) else box_edges),
                ("leader-across-extension-line", "an extension line",
                 exts)):
            for a, b, dname in segs:
                if segs_cross(t, k, a, b, u(0.2)):
                    add(kind, f"the leader of {who(lab, t)} crosses {what}"
                        + (f" ({dname})" if dname else ""))
                    break

    # 5b. a size leader taking the long way round its own feature
    #     (issue #33): a diameter anchored on the FAR edge of the
    #     cylinder it names, so its shaft crosses the outline, the centre
    #     line and every hidden line between — where the near end of the
    #     same drawn edge is the same feature and crosses nothing. Only a
    #     leader that names a SIZE is asked: a note's arrow means the
    #     spot it was put on.
    for t, k, lab, _h in leaders:
        if "%%c" not in (lab or ""):
            continue
        home = None
        for r in recs:
            if r.anno or r.kind not in ("LINE", "POLYLINE", "ARC", "CIRCLE"):
                continue
            segs = segments(r)
            if not segs or min(_seg_dist(a, b, t) for a, b in segs) > u(0.5):
                continue
            if home is None or len(segs) > len(home):
                home = segs
        if home is None:
            continue
        alt = min((_near_pt(a, b, k) for a, b in home),
                  key=lambda p: math.dist(p, k))
        if math.dist(alt, t) < u(LONG_WAY_MOVE):
            continue                      # it IS the near end

        def crossed(p, knee=k):
            return sum(1 for a, b in geom
                       if _seg_dist(a, b, p) > u(LONG_WAY_MOVE)
                       and segs_cross(p, knee, a, b, 0.0))

        now, then = crossed(t), crossed(alt)
        if now < LONG_WAY_CROSS or now - then < LONG_WAY_SAVE:
            continue
        add("leader-across-own-view",
            f"the leader of {who(lab, t)} crosses its own view {now} "
            f"times on the way out, where the end of the same edge "
            f"{math.dist(alt, t) * unit_mm:.0f} mm away crosses it "
            f"{then} — point it at the nearer end of the feature (`at`)")

    # 6. a callout column too narrow for its own words
    for first, lines in narrow_columns(recs, texts, u(1.0)):
        add("callout-column-narrow",
            f"the callout {first[:20]!r} is coming out one word a line ("
            + " / ".join(lines[:4])
            + ") — the column beside the views is narrower than its own "
              "words, and its leader has to cross the view to reach it; "
              "this sheet wants a bigger paper")

    # 7. a datum box that reads as another callout's
    for box, letter, own in datum_boxes(recs, texts):
        if own is None:
            continue                      # not hung on a callout's leader
        # text READS ALONG A LINE, so the first question is which label
        # this box sits after: a box on the line below, just past that
        # line's words, reads as that line's however near its own callout
        # may be in plain distance (issue #29).
        h = box[3] - box[1]
        mates = [(box[0] - ink[2], unescape(r.content).strip(), ink)
                 for r, ink in texts
                 if len(unescape(r.content).strip()) > 1
                 and _line_share(box, ink) > h * 0.25
                 and ink[2] <= box[0] + u(0.05)
                 and box[0] - ink[2] <= 4.0 * h]
        if mates:
            gap, body, ink = min(mates)
            if not _same_rect(ink, own):
                add("datum-beside-wrong-callout",
                    f"datum {letter} sits on the same LINE as "
                    f"{body[:28]!r}, {gap * unit_mm:.1f} mm after its "
                    f"words, so the sheet reads \"{body[:28]} "
                    f"{letter}\" — its own callout is on another line")
            continue
        # sharing no label's line, it reads as whichever is nearest
        # (issue #27)
        mine = rect_gap(box, own)
        for r, ink in texts:
            body = unescape(r.content).strip()
            if len(body) <= 1 or _same_rect(ink, own):
                continue
            if rect_gap(box, ink) < mine - u(0.05):
                add("datum-beside-wrong-callout",
                    f"datum {letter} sits nearer {body[:28]!r} than the "
                    "callout it belongs to, so the sheet names that "
                    "feature as the datum")
                break

    # 8. text outside the drawing area — or on the template's own
    #    furniture inside it, which is not drawing area either (#28)
    for r, ink in ((r, text_ink(r)) for r in recs if r.kind in TEXT_KINDS):
        if ink is None or not r.anno:
            continue
        if not (ink[0] >= draw_area[0] - 1e-6 and ink[1] >= draw_area[1] - 1e-6
                and ink[2] <= draw_area[2] + 1e-6
                and ink[3] <= draw_area[3] + 1e-6):
            add("text-off-sheet",
                f"{unescape(r.content).strip()[:28]!r} is outside the "
                "drawing area")
        elif on_block(ink):
            add("text-off-sheet",
                f"{unescape(r.content).strip()[:28]!r} is over the "
                f"{on_block(ink)}")

    # 9. leaders running parallel and close (a warning: nothing is wrong,
    #    it only reads as one line with two arrows)
    for i, (t1, k1, l1, _h1) in enumerate(leaders):
        for t2, k2, l2, _h2 in leaders[i + 1:]:
            run = collinear_run(t1, k1, t2, k2, u(PARALLEL_GAP))
            if run > u(PARALLEL_MM):
                add("leaders-parallel",
                    f"the leaders of {who(l1, t1)} and {who(l2, t2)} run "
                    f"together for {run * unit_mm:.1f} mm closer than "
                    f"{PARALLEL_GAP} mm", severity="warn")

    # 10. a leader through ANOTHER leader's ARROWHEAD (issue #29). The
    #     meeting point is the other leader's own end, which is where
    #     check 1 deliberately does not look: a leader legitimately
    #     touches the outline it points at and the knees of its
    #     neighbours. Two arrowheads in one place say nothing at all.
    for i, (t1, k1, l1, h1) in enumerate(leaders):
        for j, (t2, _k2, l2, head) in enumerate(leaders):
            if i == j or not head or not seg_meets_poly(t1, k1, head):
                continue
            near = math.dist(t1, t2) * unit_mm
            if near <= ARROW_MM:
                # the two arrows LAND in the same place. Each points at a
                # feature of its own and the sheet cannot say which is
                # which, so it is reported once for the pair.
                if i < j:
                    add("leader-through-arrowhead",
                        f"the arrows of {who(l1, t1)} and {who(l2, t2)} "
                        f"land {near:.2f} mm apart, so the two read as one "
                        "arrow and the sheet does not say which feature "
                        "each one names — draw that region as a detail, or "
                        "point one of them somewhere clear")
                continue
            add("leader-through-arrowhead",
                f"the leader of {who(l1, t1)} runs through the arrowhead "
                f"of {who(l2, t2)}, where the two arrows meet and neither "
                "reads as an arrow any more")

    # 11. two annotations too close to read apart (issue #29): a
    #     diameter dim's label that fell into a callout column lands
    #     between two callouts and touches both. The threshold is the
    #     clearance the ENGINE keeps between its own annotation claims,
    #     so anything closer means some ink was understated — which is
    #     the whole bug. A text inside an annotation BOX belongs to that
    #     box (a frame's cells, a datum letter), and the frame's own ink
    #     over a label is check 3's business.
    crowded: set = set()
    for r in recs:
        if r.kind != "LEADER":
            continue
        label = unescape(r.content).strip()
        ink = text_ink(r)
        if not label or ink is None or not in_area(ink) or furniture(ink):
            continue
        for tr, other in texts:
            if tr is r or _same_rect(other, ink):
                continue
            body = unescape(tr.content).strip()
            if not body or _in_a_box(other, boxed, u(INK_TOL)):
                continue
            # the engine's own clearance, or — bigger on any ordinary
            # sheet — the fraction of a text height at which a stranger
            # starts reading as a line of the column it fell into
            # (issue #33): the %%c75.50 of a diameter dim dropped between
            # two callouts sat at exactly the 0.8 mm the engine keeps,
            # and printed as the middle line of their note
            gap = rect_gap(ink, other)
            if gap >= max(u(CROWD_MM), CROWD_FRAC * _text_h(r)):
                continue
            # a line of this callout's OWN stack is never this close: the
            # stack's leading is 0.8 of the text height, 2.5 printed mm
            # (place_callouts' 1.8h pitch), so the gap tells them apart.
            # Two labels that are both LEADERS' meet twice here, and it
            # is one pair crowding one spot either way round
            pair = tuple(sorted((label[:24], body[:24])))
            if pair in crowded:
                continue
            crowded.add(pair)
            add("annotation-crowded",
                f"the label {label[:24]!r} and {body[:24]!r} are "
                f"{gap * unit_mm:.2f} mm apart — closer than the "
                f"{max(CROWD_MM, CROWD_FRAC * _text_h(r) * unit_mm):.2f} mm "
                "two annotations need to read apart, so they read as one "
                "block")
    return out


def _text_h(r: Rec) -> float:
    """A text record's own glyph height (MTEXT, TEXT and LEADER each
    carry it in a field of their own)."""
    idx = {"MTEXT": 3, "TEXT": 2, "LEADER": 4}.get(r.kind)
    return r.nums[idx] if idx is not None and len(r.nums) > idx else 0.0


def _in_a_box(ink, boxed, tol) -> bool:
    """Does this text sit inside an annotation BOX — a frame's cell, a
    datum letter? Such a text belongs to the box, which is drawn with it;
    a box over somebody else's label is check 3's business."""
    return any(bx is not None and bx[0] - tol <= ink[0]
               and ink[2] <= bx[2] + tol and bx[1] - tol <= ink[1]
               and ink[3] <= bx[3] + tol for bx in boxed)


def _same_rect(a, b) -> bool:
    return a is b or (a is not None and b is not None
                      and all(abs(a[i] - b[i]) < 1e-9 for i in range(4)))


def _line_share(a, b) -> float:
    """How much of two rects' HEIGHT overlaps: how much they share a line
    of text."""
    return max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def dim_ink(recs, ext_offset=0.72, ext_extension=1.5, lfacs=None):
    """The dimension lines and extension lines a sheet's DIM records will
    draw, as MusaCAD draws them (core/dimension.cpp): the feet are the
    def points projected onto the dim line, and the extension lines run
    from just off each def point to just past its foot.

    Returns (dimension lines, extension lines), each segment as
    (a, b, name) — the name being the dimension it belongs to, so a
    finding can say WHICH one a leader crosses and a waiver can name that
    one crossing (issue #29). A linear dim is named by what it measures:
    the distance between its def points times its style's DIMLFAC, which
    is the number the reader sees (`lfacs` maps style index -> DIMLFAC,
    from dim_styles)."""
    dims, exts = [], []
    for r in recs:
        if r.kind != "DIM" or len(r.nums) < 7:
            continue
        dtype = int(r.nums[0])
        a = (r.nums[1], r.nums[2])
        b = (r.nums[3], r.nums[4])
        lp = (r.nums[5], r.nums[6])
        style = int(r.nums[7]) if len(r.nums) > 7 else 0
        lfac = (lfacs or {}).get(style, 1.0)
        name = _dim_name(dtype, a, b, lfac)
        if dtype in (2, 3):
            # radius / diameter: the dim line is the leader MusaCAD draws
            # from the centre, and it has no extension lines
            dims.append((a, b, name))
            continue
        if dtype != 0:
            dims.append((a, b, name))
            continue
        d = (1.0, 0.0) if abs(a[1] - b[1]) <= abs(a[0] - b[0]) else (0.0, 1.0)

        def foot(p):
            t = (p[0] - lp[0]) * d[0] + (p[1] - lp[1]) * d[1]
            return (lp[0] + d[0] * t, lp[1] + d[1] * t)

        fa, fb = foot(a), foot(b)
        dims.append((fa, fb, name))
        for p, f in ((a, fa), (b, fb)):
            vx, vy = f[0] - p[0], f[1] - p[1]
            ln = math.hypot(vx, vy)
            if ln < 1e-9:
                continue
            nx, ny = vx / ln, vy / ln
            exts.append(((p[0] + nx * ext_offset, p[1] + ny * ext_offset),
                         (f[0] + nx * ext_extension,
                          f[1] + ny * ext_extension), name))
    return dims, exts


def _dim_name(dtype: int, a, b, lfac: float) -> str:
    """What to call a dimension in a finding. A linear one is named by
    what it measures — the distance between its own def points, which is
    the one thing a DIM cannot misstate — and an angular or round one by
    its kind, since its value is not a length."""
    if dtype == 3:
        return "a diameter dimension"
    if dtype == 2:
        return "a radius dimension"
    if dtype == 4:
        return "an angular dimension"
    if dtype == 0 and abs(a[1] - b[1]) <= abs(a[0] - b[0]):
        val = abs(b[0] - a[0]) * lfac
    elif dtype == 0:
        val = abs(b[1] - a[1]) * lfac
    else:
        val = math.dist(a, b) * lfac
    shown = str(round(val)) if abs(val - round(val)) < 0.005 else f"{val:.2f}"
    return f"the {shown} mm dimension"


def cut_arrows(recs):
    """The cutting plane's sight arrows: a filled triangle in the
    DRAWING's own ink (the datum triangles and the dimension arrowheads
    are annotation ink), with the thick shaft that carries it."""
    heads, out = [], []
    for r in recs:
        if r.anno or r.kind != "HATCH" or len(r.nums) != 1 \
                or len(r.nums[0]) != 3 or "SOLID" not in r.content.upper():
            continue
        tri = r.nums[0]
        tip = tri[0]
        base = ((tri[1][0] + tri[2][0]) / 2.0, (tri[1][1] + tri[2][1]) / 2.0)
        heads.append((tip, base))
    shafts = [seg for r in recs if r.kind == "LINE" and not r.anno
              for seg in segments(r)]
    for tip, base in heads:
        far = base
        for a, b in shafts:
            for p, q in ((a, b), (b, a)):
                if math.dist(p, base) < math.dist(tip, base) * 1.2 \
                        and math.dist(q, base) > math.dist(far, base):
                    far = q
        out.append((tip, far))
    return out


def narrow_columns(recs, texts, tol):
    """Callout stacks that came out one word a line (issue #25).

    A column narrower than the note's own longest word wraps it to a word
    a line, and the leaders then reach across the view to get to it. The
    sheet shows it plainly: a leader's own note, stacked under its knee,
    three or more lines of one word each.

    A column of MTEXTs is only read as a callout when it hangs off a
    LEADER's knee — a table's cells line up the same way and are nobody's
    note."""
    out = []
    for r in recs:
        if r.kind != "LEADER":
            continue
        knee = (r.nums[2], r.nums[3])
        first = unescape(r.content).strip()
        lines = [(knee[1], first)] if first else []
        for t, ink in texts:
            if t.kind != "MTEXT":
                continue
            body = unescape(t.content).strip()
            if not body or not (0.0 < ink[0] - knee[0] < 4.0 * tol):
                continue
            if knee[1] - 12.0 * tol < ink[1] <= knee[1] + tol:
                lines.append((ink[1], body))
        if len(lines) < 3:
            continue
        lines.sort(reverse=True)
        run = 0
        for _y, body in lines:
            run = run + 1 if len(body.split()) == 1 else 0
            if run >= 3:
                out.append((first, [b for _y2, b in lines]))
                break
    return out


def _sample(a, b, step):
    """Points along a segment, no further apart than `step`."""
    n = max(1, int(math.dist(a, b) / step) + 1)
    return [(a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n)
            for i in range(n + 1)]


def view_clusters(geom, gap, min_side):
    """The sheet's drawn geometry grouped into VIEWS.

    A sheet does not say which ink belongs to which view, and it does not
    have to: the views are drawn apart (MIN_VIEW_GAP is 16 mm), so ink
    that is connected within `gap` is one view. Clusters too small or too
    thin to be a view — the frame's own rules, a stray centre mark — are
    left out. Returns [(bbox, segments)], biggest first."""
    cells: dict = {}
    for i, (a, b) in enumerate(geom):
        for p in _sample(a, b, gap / 2.0):
            cells.setdefault((int(p[0] // gap), int(p[1] // gap)),
                             []).append(i)
    parent = list(range(len(geom)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        a, b = find(i), find(j)
        if a != b:
            parent[a] = b

    for (cx, cy), members in cells.items():
        for i in members[1:]:
            union(members[0], i)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                near = cells.get((cx + dx, cy + dy))
                if near:
                    union(members[0], near[0])
    groups: dict = {}
    for i in range(len(geom)):
        groups.setdefault(find(i), []).append(i)
    out = []
    for idx in groups.values():
        xs = [c for i in idx for c in (geom[i][0][0], geom[i][1][0])]
        ys = [c for i in idx for c in (geom[i][0][1], geom[i][1][1])]
        bb = (min(xs), min(ys), max(xs), max(ys))
        if len(idx) < 4 or bb[2] - bb[0] < min_side \
                or bb[3] - bb[1] < min_side:
            continue
        out.append((bb, [geom[i] for i in idx]))
    return sorted(out, key=lambda v: -len(v[1]))


def _own_view(views, tip, pad):
    """Which view a leader points into: the one whose box holds its arrow
    (the smallest, where a detail sits inside another), else the nearest
    — an arrow may land just off the ink it names."""
    inside = [i for i, (bb, _s) in enumerate(views)
              if bb[0] - pad <= tip[0] <= bb[2] + pad
              and bb[1] - pad <= tip[1] <= bb[3] + pad]
    if inside:
        return min(inside, key=lambda i: ((views[i][0][2] - views[i][0][0])
                                          * (views[i][0][3]
                                             - views[i][0][1])))
    if not views:
        return None
    return min(range(len(views)),
               key=lambda i: min(_seg_dist(a, b, tip)
                                 for a, b in views[i][1]))


def _near_pt(a, b, p):
    """The point of segment a-b nearest p."""
    vx, vy = b[0] - a[0], b[1] - a[1]
    L2 = vx * vx + vy * vy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * vx
                                               + (p[1] - a[1]) * vy) / L2))
    return (a[0] + t * vx, a[1] + t * vy)


def _seg_dist(a, b, p):
    vx, vy = b[0] - a[0], b[1] - a[1]
    L2 = vx * vx + vy * vy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * vx
                                               + (p[1] - a[1]) * vy) / L2))
    return math.dist(p, (a[0] + t * vx, a[1] + t * vy))


def datum_boxes(recs, texts):
    """Every datum box on the sheet, with the triangle it hangs off and
    the leader that triangle sits on (issue #27).

    A datum box is a square drawn in annotation ink with one letter in
    it; the triangle at the end of its stem is what says WHICH feature
    is the datum, and the leader that triangle sits on is that feature's
    own callout. Returns [(box rect, letter, own label ink or None)]."""
    out = []
    squares = []
    for r in recs:
        if not r.anno or r.kind != "POLYLINE" or int(r.nums[1]) != 4:
            continue
        pts = [(r.nums[2 + 2 * i], r.nums[3 + 2 * i]) for i in range(4)]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        w, h = max(xs) - min(xs), max(ys) - min(ys)
        if w < 1e-9 or abs(w - h) > 1e-6:
            continue
        squares.append((min(xs), min(ys), max(xs), max(ys)))
    if not squares:
        return out
    tris = [r.nums[0] for r in recs
            if r.kind == "HATCH" and r.anno and len(r.nums) == 1
            and len(r.nums[0]) == 3 and "SOLID" in r.content.upper()]
    leaders = [(tuple(r.nums[:2]), tuple(r.nums[2:4]),
                unescape(r.content).strip(), text_ink(r))
               for r in recs if r.kind == "LEADER"]
    for box in squares:
        letter = None
        for r, ink in texts:
            body = unescape(r.content).strip()
            if len(body) == 1 and body.isalpha() \
                    and box[0] <= (ink[0] + ink[2]) / 2 <= box[2] \
                    and box[1] <= (ink[1] + ink[3]) / 2 <= box[3]:
                letter = body
        if letter is None:
            continue                      # a frame cell, not a datum box
        tri = min((t for t in tris),
                  key=lambda t: min(_dist(box, q) for q in t),
                  default=None)
        own = None
        if tri is not None:
            # the triangle POINTS at the feature, so its tip is the
            # vertex farthest from the box its stem comes from
            tip = max(tri, key=lambda q: _dist(box, q))
            for a, b, body, ink in leaders:
                if not body or ink is None:
                    continue
                # on the leader itself (set back along it, issue #17), or
                # in line with its landing past the text (ISO 1101 5.3)
                in_line = abs(tip[1] - b[1]) < 1e-6 and tip[0] >= b[0] - 1e-6
                if in_line or _seg_dist(a, b, tip) < 0.5:
                    own = ink
                    break
        out.append((box, letter, own))
    return out


def _dist(rect, p):
    dx = max(rect[0] - p[0], 0.0, p[0] - rect[2])
    dy = max(rect[1] - p[1], 0.0, p[1] - rect[3])
    return math.hypot(dx, dy)


def rect_gap(a, b) -> float:
    dx = max(b[0] - a[2], 0.0, a[0] - b[2])
    dy = max(b[1] - a[3], 0.0, a[1] - b[3])
    return math.hypot(dx, dy)


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


def unwaivable(findings) -> list[dict]:
    """The findings no manifest entry may waive (issue #30): a leader
    hidden under the pictorial is not printed, and a waiver cannot make
    the vendor see it."""
    return [f for f in findings if f.get("waivable") is False]


def warnings(findings) -> list[dict]:
    return [f for f in findings if f.get("severity") == "warn"]


def report(findings) -> list[str]:
    return [f"{f['kind'].upper()}: {f['what']}" for f in findings]
