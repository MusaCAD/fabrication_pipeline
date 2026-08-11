# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Pranay Kiran
"""MusaCAD native-format (v14) writer + template composition.

Record grammar reverse-engineered from musa_cad/src/core/io/native_format.cpp
(Phase 0). The parser is strict: exact token counts, mandatory font lines after
TEXT/MTEXT content, file must start `MUSACAD 14` and end `END`.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from common import (DRAWING_AREA, S0, TEMPLATE, TEMPLATE_EXT_MAX,
                    TEMPLATE_EXT_MIN)

P = "0 7 255 255 255 0 25"  # default 7-int property tail: layer 0, ByLayer
# annotation tail: explicit MAGENTA (flags 6 = linetype+lineweight ByLayer,
# colour explicit) — dims/leaders/GD&T per the round-2 color scheme
P_ANNO = "0 6 255 0 255 0 25"


def n(v: float) -> str:
    """Format a double the way std::from_chars accepts (locale-free)."""
    s = repr(float(v))
    return s


def esc(s: str) -> str:
    """MTEXT/MLEADER/HATCH/INSERT content escaping: only backslash + newline."""
    return s.replace("\\", "\\\\").replace("\n", "\\n")


# ---------------------------------------------------------------- entities

def line(a, b, props: str = P) -> str:
    return f"LINE {n(a[0])} {n(a[1])} {n(b[0])} {n(b[1])} {props}"


def circle(c, r: float, props: str = P) -> str:
    return f"CIRCLE {n(c[0])} {n(c[1])} {n(r)} {props}"


def arc(c, r: float, a0: float, a1: float, props: str = P) -> str:
    return f"ARC {n(c[0])} {n(c[1])} {n(r)} {n(a0)} {n(a1)} {props}"


def polyline(pts, closed: bool = False, props: str = P) -> str:
    coords = " ".join(f"{n(x)} {n(y)}" for x, y in pts)
    return f"POLYLINE {1 if closed else 0} {len(pts)} {coords} {props}"


def mtext(pos, text: str, height: float, *, width: float = 0.0,
          rotation: float = 0.0, width_factor: float = 1.0,
          line_spacing: float = 1.0, attach: int = 0, font: str = "",
          props: str = P) -> str:
    """attach: 0=TL 1=TC 2=TR 3=ML 4=MC 5=MR 6=BL 7=BC 8=BR."""
    rec = (f"MTEXT {n(pos[0])} {n(pos[1])} {n(width)} {n(height)} "
           f"{n(rotation)} {n(width_factor)} {n(line_spacing)} {attach} {props}")
    return f"{rec}\n{esc(text)}\n{font}"


def text(pos, s: str, height: float, *, rotation: float = 0.0,
         justify: int = 0, font: str = "", props: str = P) -> str:
    """justify: 0=Left 1=Center 2=Right. Content is RAW (no newlines)."""
    assert "\n" not in s
    return (f"TEXT {n(pos[0])} {n(pos[1])} {n(height)} {n(rotation)} "
            f"{justify} {props}\n{s}\n{font}")


# DimOverrides mask bits (core/properties.hpp)
DIM_OV_TEXT_H, DIM_OV_ARROW_SZ, DIM_OV_ARROW_T, DIM_OV_PREC = 1, 2, 4, 8
ARROW_FILLED, ARROW_TICK, ARROW_OPEN, ARROW_DOT = 0, 1, 2, 3


def dim(dtype: int, a, b, line_pt, style: int = 0, props: str = P,
        precision: int | None = None, text_height: float | None = None,
        arrow_type: int | None = None,
        arrow_size: float | None = None) -> str:
    """dtype: 0=Linear 1=Aligned 2=Radius 3=Diameter 4=Angular.
    Measured value is always computed from the def points (never faked).
    Per-dim overrides (else the DIMSTYLE's values): decimal precision, text
    height, arrow type/size — the last three are how ISO 129-1 handles a
    dimension too narrow for full-size text and filled arrowheads."""
    rec = (f"DIM {dtype} {n(a[0])} {n(a[1])} {n(b[0])} {n(b[1])} "
           f"{n(line_pt[0])} {n(line_pt[1])} {style} {props}")
    mask = ((DIM_OV_PREC if precision is not None else 0)
            | (DIM_OV_TEXT_H if text_height is not None else 0)
            | (DIM_OV_ARROW_T if arrow_type is not None else 0)
            | (DIM_OV_ARROW_SZ if arrow_size is not None else 0))
    if mask:
        # 15-field override block: mask, arrow_type, precision, text_above,
        # text_height, arrow_size, then dim/ext/text RGB. Fields whose mask
        # bit is clear are present but ignored.
        rec += (f" {mask} {arrow_type or 0} {precision or 0} 1 "
                f"{n(text_height) if text_height is not None else '2.5'} "
                f"{n(arrow_size) if arrow_size is not None else '2.5'} "
                "0 0 0 0 0 0 0 0 0")
    return rec


def leader(tip, knee, content: str, text_height: float, style: int = 0,
           props: str = P, font: str = "") -> str:
    """Arrow at tip, elbow at knee, text after the knee. Content RAW."""
    assert "\n" not in content
    return (f"LEADER {n(tip[0])} {n(tip[1])} {n(knee[0])} {n(knee[1])} "
            f"{n(text_height)} {style} {props}\n{content}\n{font}")


def hatch(loops, pattern: str = "ANSI31", *, scale: float = 1.0,
          angle: float = 0.0, origin=(0.0, 0.0), props: str = P) -> str:
    """loops: list of point lists; loop 0 = outer boundary, rest = islands."""
    parts = [f"HATCH {len(loops)}"]
    for lp in loops:
        parts.append(str(len(lp)))
        parts.extend(f"{n(x)} {n(y)}" for x, y in lp)
    parts.append(f"{n(scale)} {n(angle)} {n(origin[0])} {n(origin[1])} {props}")
    return " ".join(parts) + f"\n{esc(pattern)}"


def dimstyle(text_height: float, arrow_size: float, *, precision: int = 0,
             name: str = "Standard", color=None,
             ext_offset: float | None = None,
             ext_extension: float | None = None) -> str:
    """Full 26-token DIMSTYLE (shorter forms silently lose fields).
    color=(r,g,b) sets ALL four color slots explicitly (dim/ext/text/arrow);
    None keeps ByLayer. Both extension-line fields of core/dimension.cpp:189
    (`seg(ext, def + n*ext_offset, foot + n*ext_extension)`) are settable:
    `ext_offset` is the gap from the def point to where the extension line
    STARTS (raise it for an ISO 129-1 interrupted extension line) and
    `ext_extension` its overshoot past the dim line (cut it back when a
    value too wide for its own span would otherwise be printed through)."""
    if color is None:
        cols = "1 255 255 255 " * 4
    else:
        r, g, b = color
        cols = f"0 {r} {g} {b} " * 4
    eo = 0.6 * text_height / 2.5 if ext_offset is None else ext_offset
    ee = 1.25 * text_height / 2.5 if ext_extension is None else ext_extension
    return (f"DIMSTYLE {n(text_height)} {n(arrow_size)} 0 "
            f"{n(eo)} {n(ee)} "
            f"{precision} 1 25 {cols}{name}")


# ------------------------------------------------------- template handling

_NUM = r"[-+0-9.eE]+"


@dataclass
class TemplateRecord:
    kind: str          # LINE | CIRCLE | POLYLINE | MTEXT | ARC
    nums: list[float]  # coordinate/param fields (see transform())
    props: str
    content: str | None = None   # MTEXT only (unescaped)
    font: str = ""
    extra: dict = field(default_factory=dict)


def parse_template(path: Path = TEMPLATE) -> list[TemplateRecord]:
    """Minimal .musa reader for the records our template actually contains."""
    lines = path.read_text().splitlines()
    recs: list[TemplateRecord] = []
    i = 0
    while i < len(lines):
        raw = lines[i].rstrip("\r")
        tok = raw.split()
        i += 1
        if not tok:
            continue
        key = tok[0]
        if key in ("MUSACAD", "UNITS", "CURRENT", "LTSCALE", "LAYER",
                   "DIMSTYLE", "END"):
            continue
        if key == "LINE":
            recs.append(TemplateRecord("LINE", [float(v) for v in tok[1:5]],
                                       " ".join(tok[5:12])))
        elif key == "CIRCLE":
            recs.append(TemplateRecord("CIRCLE", [float(v) for v in tok[1:4]],
                                       " ".join(tok[4:11])))
        elif key == "ARC":
            recs.append(TemplateRecord("ARC", [float(v) for v in tok[1:6]],
                                       " ".join(tok[6:13])))
        elif key == "POLYLINE":
            closed, cnt = int(tok[1]), int(tok[2])
            coords = [float(v) for v in tok[3:3 + 2 * cnt]]
            recs.append(TemplateRecord(
                "POLYLINE", coords, " ".join(tok[3 + 2 * cnt:3 + 2 * cnt + 7]),
                extra={"closed": closed}))
        elif key == "MTEXT":
            nums = [float(v) for v in tok[1:8]]  # px py width height rot wf ls
            attach = int(tok[8])
            props = " ".join(tok[9:16])
            content = lines[i].rstrip("\r") if i < len(lines) else ""
            font = lines[i + 1].rstrip("\r") if i + 1 < len(lines) else ""
            i += 2
            recs.append(TemplateRecord("MTEXT", nums, props,
                                       content=content, font=font,
                                       extra={"attach": attach}))
        else:
            raise ValueError(f"template contains unhandled record {key!r}")
    return recs


class SheetContext:
    """One drawing sheet: template scaled by t = S0*den/num into mm space,
    part geometry placed full-size (real mm), plotted at --scale num:den."""

    def __init__(self, scale_num: float, scale_den: float):
        self.num = scale_num
        self.den = scale_den
        self.t = S0 * scale_den / scale_num
        ox, oy = TEMPLATE_EXT_MIN
        self._off = (ox, oy)
        w = TEMPLATE_EXT_MAX[0] - TEMPLATE_EXT_MIN[0]
        h = TEMPLATE_EXT_MAX[1] - TEMPLATE_EXT_MIN[1]
        self.window = (0.0, 0.0, w * self.t, h * self.t)
        da = DRAWING_AREA
        self.draw_area = (self.tx(da[0]), self.ty(da[1]),
                          self.tx(da[2]), self.ty(da[3]))
        self.entities: list[str] = []
        self.dimstyles: list[str] = []

    # template-units -> sheet mm
    def tx(self, x: float) -> float:
        return (x - self._off[0]) * self.t

    def ty(self, y: float) -> float:
        return (y - self._off[1]) * self.t

    def tpt(self, p) -> tuple[float, float]:
        return (self.tx(p[0]), self.ty(p[1]))

    # printed-mm -> sheet units (for annotation sizes that must print at a
    # constant paper size regardless of sheet scale)
    def mm(self, v: float) -> float:
        return v * self.den / self.num

    def add(self, *records: str) -> None:
        self.entities.extend(records)

    def add_template(self, replacements: dict[str, str],
                     additions: list[str] | None = None,
                     positional: list | None = None) -> list[str]:
        """Emit the template scaled into sheet space. `replacements` maps an
        MTEXT's exact original content -> new content ('' keeps a field's
        original text). Returns the list of replaced keys for QA."""
        used: list[str] = []
        for r in parse_template():
            pr = r.props
            if r.kind == "LINE":
                a = (self.tx(r.nums[0]), self.ty(r.nums[1]))
                b = (self.tx(r.nums[2]), self.ty(r.nums[3]))
                self.add(line(a, b, pr))
            elif r.kind == "CIRCLE":
                self.add(circle((self.tx(r.nums[0]), self.ty(r.nums[1])),
                                r.nums[2] * self.t, pr))
            elif r.kind == "ARC":
                self.add(arc((self.tx(r.nums[0]), self.ty(r.nums[1])),
                             r.nums[2] * self.t, r.nums[3], r.nums[4], pr))
            elif r.kind == "POLYLINE":
                pts = [(self.tx(r.nums[j]), self.ty(r.nums[j + 1]))
                       for j in range(0, len(r.nums), 2)]
                self.add(polyline(pts, bool(r.extra["closed"]), pr))
            elif r.kind == "MTEXT":
                content = r.content or ""
                # match on the UNESCAPED template text
                plain = content.replace("\\n", "\n").replace("\\\\", "\\")
                new = replacements.get(plain)
                if new is not None:
                    used.append(plain)
                    if new != "":
                        plain = new
                for prep in (positional or []):
                    if plain == prep["content"] and \
                            abs(r.nums[0] - prep["x"]) < 2.0 and \
                            abs(r.nums[1] - prep["y"]) < 2.0:
                        plain = prep["new"]
                        used.append(f"pos:{prep['new'][:16]}")
                        break
                px, py = self.tx(r.nums[0]), self.ty(r.nums[1])
                self.add(mtext((px, py), plain, r.nums[3] * self.t,
                               width=r.nums[2] * self.t, rotation=r.nums[4],
                               width_factor=r.nums[5], line_spacing=r.nums[6],
                               attach=r.extra["attach"], font=r.font, props=pr))
        if additions:
            self.entities.extend(additions)
        return used

    def render(self, units: str = "mm") -> str:
        # LTSCALE scaled so dash patterns print at CONSTANT paper size on
        # every sheet scale (Hidden 2.5-unit dash -> 2 mm printed): without
        # this, short hidden edges (< one dash) render as solid lines
        lts = 0.8 * self.den / self.num
        head = ["MUSACAD 14", f"UNITS {units}", "CURRENT 0",
                f"LTSCALE {lts}",
                "LAYER 255 255 255 0 25 1 0 0 0"]
        styles = self.dimstyles or [dimstyle(self.mm(3.0), self.mm(2.5))]
        return "\n".join(head + styles + self.entities + ["END"]) + "\n"


# --------------------------------------------- title-block field geometry
# All in template units (from Phase 0 template survey). The parts-list entry
# row sits directly ABOVE the title block top edge (y=460.083), as in the
# reference drawing; add_parts_rows() draws its grid + cell text.

TB = {
    "scale_text": "SCALE : 1:1",              # replace -> "SCALE : n:d"
    "sheet_text": "SHEET 9",                  # replace -> "SHEET i OF n"
    "rev_text": "00",                         # big REV box value
    "descr_text": "DESCRIPTION: \nCONNECTING CHAMBER",
    "drgno_text": "DRG. NO.     \nRES-GB-350T-CC-01",
}

# parts-table column x-bounds (left, right) in template units, in order.
PARTS_COLS = [
    ("pno", 58.544651860946026, 127.85208203250659),
    ("description", 127.85208203250659, 428.5314744562784),
    ("qty", 428.5314744562784, 507.32418277721445),
    ("drg_no", 507.32418277721445, 671.4463542880985),
    ("material", 671.4463542880985, 761.358696132258),
    ("size", 761.358696132258, 851.271037976403),
    ("weight", 851.271037976403, 941.1833798205771),
    ("remarks", 941.1833798205771, 1044.425593134758),
]
PARTS_ROW_BASE_Y = 460.0832555520592   # title block top edge
PARTS_ROW_H = 29.97078061472913        # matches title-block row pitch
# name/date grid rows (y of the row label baselines) and the date column
TB_ROWS_Y = {"designed": 389.89349406305337, "drawn": 359.89219468638476}
TB_DATE_X = 725.0
TB_TEXT_H = 9.365868942101082


def parts_row_records(ctx: SheetContext, row_idx: int,
                      values: dict[str, str]) -> list[str]:
    """Grid + text for one parts-list entry row above the title block."""
    y0 = PARTS_ROW_BASE_Y + row_idx * PARTS_ROW_H
    y1 = y0 + PARTS_ROW_H
    out = [line(ctx.tpt((PARTS_COLS[0][1], y1)),
                ctx.tpt((PARTS_COLS[-1][2], y1)))]
    xs = [PARTS_COLS[0][1]] + [c[2] for c in PARTS_COLS]
    for x in xs:
        out.append(line(ctx.tpt((x, y0)), ctx.tpt((x, y1))))
    for key, xl, xr in PARTS_COLS:
        v = values.get(key, "-")
        out.append(mtext(ctx.tpt(((xl + xr) / 2, (y0 + y1) / 2)), v,
                         TB_TEXT_H * ctx.t, attach=4))
    return out


def date_records(ctx: SheetContext, date_str: str) -> list[str]:
    return [mtext(ctx.tpt((TB_DATE_X, y)), date_str, TB_TEXT_H * ctx.t,
                  attach=0)
            for y in TB_ROWS_Y.values()]


# ------------------------------------------------------------- GD&T + text

def text_w(s: str, h: float) -> float:
    """Stroke-font width estimate for layout/collision purposes."""
    return 0.62 * h * len(s)


_CODE_RE = re.compile(r"%%[cdp%]", re.IGNORECASE)


def glyph_w(s: str, h: float) -> float:
    """EXACT rendered advance width of a stroke-font string: %%-codes collapse
    to ONE glyph (text_codes.cpp) and a space advances kSpace=0.45h, not
    kAdvance=0.62h (text/stroke_font.cpp). Use where the ink rect must be
    right (boxed basic dims); text_w's over-estimate stays fine as a
    conservative reservation elsewhere."""
    plain = _CODE_RE.sub("⌀", s)
    spaces = plain.count(" ") + plain.count("\t")
    return h * (0.62 * (len(plain) - spaces) + 0.45 * spaces)


def glyph_ink_w(s: str, h: float) -> float:
    """Width of the drawn INK: the advance minus the last glyph's trailing
    side bearing (kAdvance 0.62 - kGlyphW 0.52 of the height)."""
    return max(0.0, glyph_w(s, h) - 0.1 * h)


# Every glyph in the stroke font (all 77 ASCII entries plus the built-in
# degree/plus-minus/diameter shapes) is drawn inside grid columns gx 1..5 of
# 6, i.e. x in [1/6, 5/6] * kGlyphW = [0.0867, 0.4333] of the height
# (core/text/stroke_font.cpp: cell(gx, gy) = (gx/6*kGlyphW, (gy-2)/6)).
GLYPH_INK_X0, GLYPH_INK_X1 = 0.52 / 6.0, 0.52 * 5.0 / 6.0


# core/text/stroke_font.cpp: cell(gx, gy) = (gx/6*kGlyphW, (gy-2)/6), so the
# baseline is gy 2 and the lowercase descenders (g j p q y, which drop to
# gy 0) reach 2/6 = 0.333 h BELOW it. Every ink box has to allow for that or
# an annotation parked under a callout lands on its descender.
GLYPH_DESCENT = 2.0 / 6.0
_DESCENDERS = set("gjpqy")


def glyph_descent(s: str, h: float) -> float:
    """How far this string's ink drops below the baseline."""
    return GLYPH_DESCENT * h if any(c in _DESCENDERS for c in s) else 0.0


def glyph_ink_span(s: str, h: float):
    """(left, right) offsets of the drawn INK from a left-justified string's
    pen origin — exact, unlike glyph_ink_w's conservative box. Needed where
    the arithmetic decides contact: a value squeezed between its own
    extension lines has no room for a side-bearing fudge."""
    adv = glyph_w(s, h)
    return (GLYPH_INK_X0 * h, adv - 0.62 * h + GLYPH_INK_X1 * h)


# core/text/mtext.cpp kSingleSpacing: MTEXT baselines sit 5/3 of the cap
# height apart, so even a ONE-line block is 1.667h tall — an attach=6 (bottom)
# block therefore renders its glyphs 0.667h ABOVE the anchor, not on it.
MTEXT_LINE_SPACING = 5.0 / 3.0


def mtext_ink(pos, lines, h: float, *, attach: int = 0,
              line_spacing: float = 1.0, width_factor: float = 1.0,
              width: float = 0.0):
    """AABB of the glyph ink mtext(pos, ..., attach=attach) will render.

    Mirrors core/text/mtext.cpp layout_mtext: line pitch 5/3 h, box height
    n*pitch, the attachment point maps to (ax, ay) in a box spanning
    x 0..box_w and y -total_h..0, and line i's BASELINE is at
    -i*pitch - h. Model the ink, never the anchor — a claim built from the
    anchor alone is off by up to 1.67 h."""
    lines = list(lines) or [""]
    pitch = h * MTEXT_LINE_SPACING * (line_spacing if line_spacing > 0 else 1.0)
    total_h = len(lines) * pitch
    adv = [glyph_w(s, h) * width_factor for s in lines]
    box_w = width if width > 0 else max(adv)
    col, row = attach % 3, attach // 3
    ax = box_w * 0.5 if col == 1 else (box_w if col == 2 else 0.0)
    ay = -total_h * 0.5 if row == 1 else (-total_h if row == 2 else 0.0)
    x0 = y0 = float("inf")
    x1 = y1 = float("-inf")
    for i, s in enumerate(lines):
        w = adv[i]
        lx = (box_w - w) * 0.5 if col == 1 else (box_w - w if col == 2 else 0.0)
        base = -i * pitch - h
        ink = max(0.0, w - 0.1 * h * width_factor)
        x0, x1 = min(x0, pos[0] + lx - ax), max(x1, pos[0] + lx + ink - ax)
        y0 = min(y0, pos[1] + base - ay - glyph_descent(s, h))
        y1 = max(y1, pos[1] + base + h - ay)
    return (x0, y0, x1, y1)


def mtext_anchor_for_ink(rect, h: float, *, attach: int = 6,
                         line_spacing: float = 1.0):
    """Inverse of mtext_ink for the common left-justified cases: the pos that
    lands a block's ink bottom-left at rect's bottom-left."""
    pitch = h * MTEXT_LINE_SPACING * (line_spacing if line_spacing > 0 else 1.0)
    row = attach // 3
    lift = (pitch - h) if row == 2 else (-h if row == 0 else (pitch - h) / 2.0)
    return (rect[0], rect[1] - lift)


def leader_text_ink(knee, s: str, h: float, arrow_size: float):
    """Ink of a LEADER's own label: baseline AT the knee, left-justified,
    0.4*arrow_size clear of it (core/scene_snapshot.cpp emit_text_run)."""
    x = knee[0] + 0.4 * arrow_size
    return (x, knee[1] - glyph_descent(s, h), x + glyph_ink_w(s, h),
            knee[1] + h)


def _sym_flatness(x, y, s, props):
    """Parallelogram, drawn in a s x s cell with lower-left at (x, y)."""
    k = s * 0.22
    return [polyline([(x + k * 1.6, y + k), (x + s - k * 0.6, y + k),
                      (x + s - k * 1.6, y + s - k), (x + k * 0.6, y + s - k)],
                     closed=True, props=props)]


def _sym_position(x, y, s, props):
    c = (x + s / 2, y + s / 2)
    r = s * 0.28
    return [circle(c, r, props),
            line((c[0] - s * 0.42, c[1]), (c[0] + s * 0.42, c[1]), props),
            line((c[0], c[1] - s * 0.42), (c[0], c[1] + s * 0.42), props)]


def _sym_perpendicularity(x, y, s, props):
    k = s * 0.2
    return [line((x + k, y + k), (x + s - k, y + k), props),
            line((x + s / 2, y + k), (x + s / 2, y + s - k), props)]


_FCF_SYMBOLS = {"flatness": _sym_flatness, "position": _sym_position,
                "perpendicularity": _sym_perpendicularity}


def _sym_cbore(x, y, s, props):
    """Counterbore (ASME Y14.5 ⌴): open-top U — two verticals joined by a
    bottom bar, drawn in an s x s cell with lower-left at (x, y)."""
    k = s * 0.18
    return [polyline([(x + k, y + s - k), (x + k, y + k),
                      (x + s - k, y + k), (x + s - k, y + s - k)],
                     props=props)]


def _sym_depth(x, y, s, props):
    """Depth (ASME Y14.5 ↧): downward arrow whose tip touches a horizontal
    bar at the cell bottom."""
    k = s * 0.18
    cx = x + s / 2
    a = s * 0.22
    return [line((x + k, y + k), (x + s - k, y + k), props),
            line((cx, y + s - k), (cx, y + k), props),
            line((cx - a, y + k + a), (cx, y + k), props),
            line((cx + a, y + k + a), (cx, y + k), props)]


CALLOUT_SYMBOLS = {"cbore": _sym_cbore, "depth": _sym_depth}


def callout_sym(sym: str, pos, s: float, props: str = P):
    """Line-work prefix glyph for hole callouts (the stroke font has no
    counterbore/depth glyphs), in an s x s cell with lower-left at pos."""
    return CALLOUT_SYMBOLS[sym](pos[0], pos[1], s, props)


def fcf(pos, symbol: str, tol: str, refs, h: float, props: str = P):
    """Feature control frame at pos (lower-left), text height h.
    Returns (records, width, height)."""
    cell_h = 1.8 * h
    sym_w = 1.8 * h
    tol_w = text_w(tol, h) + 0.9 * h
    ref_w = 1.6 * h
    x, y = pos
    recs = list(_FCF_SYMBOLS[symbol](x, y, cell_h, props))
    xs = [x, x + sym_w, x + sym_w + tol_w]
    recs.append(cell_text((x + sym_w + tol_w / 2, y + cell_h / 2), tol, h,
                          props))
    for i, r in enumerate(refs):
        cx = xs[2] + i * ref_w
        recs.append(cell_text((cx + ref_w / 2, y + cell_h / 2), r, h,
                              props))
        xs.append(cx + ref_w)
    w = xs[-1] - x if refs else x + sym_w + tol_w - x
    x1 = x + w
    recs.append(polyline([(x, y), (x1, y), (x1, y + cell_h), (x, y + cell_h)],
                         closed=True, props=props))
    for xv in xs[1:-1] if refs else xs[1:2]:
        recs.append(line((xv, y), (xv, y + cell_h), props))
    if refs:
        recs.append(line((xs[1], y), (xs[1], y + cell_h), props))
    return recs, w, cell_h


def datum_label(box_pos, letter: str, tri_tip, h: float, props: str = P):
    """Boxed datum letter + stem + filled triangle touching the feature.
    box_pos = lower-left of the box. Returns (records, size)."""
    s = 1.7 * h
    x, y = box_pos
    cx = x + s / 2
    recs = [polyline([(x, y), (x + s, y), (x + s, y + s), (x, y + s)],
                     closed=True, props=props),
            cell_text((cx, y + s / 2), letter, h, props)]
    tx, ty = tri_tip
    recs.append(line((cx, y + s if ty > y + s else y), (tx, ty), props))
    b = 0.9 * h
    dx, dy = tx - cx, ty - (y + s if ty > y + s else y)
    ln = math.hypot(dx, dy) or 1.0
    ux, uy = dx / ln, dy / ln
    px, py = -uy, ux
    base = (tx - ux * b, ty - uy * b)
    recs.append(hatch([[(tx, ty),
                        (base[0] + px * b * 0.5, base[1] + py * b * 0.5),
                        (base[0] - px * b * 0.5, base[1] - py * b * 0.5)]],
                      "SOLID", props=props))
    return recs, s


def notes_block(pos, notes, h: float, title: str = "NOTES:"):
    """Numbered fabrication notes, growing upward from pos (lower-left).
    Returns (records, width, height)."""
    lh = 1.55 * h
    x, y = pos
    recs = []
    lines_txt = [f"{i}. {t}" for i, t in enumerate(notes, start=1)]
    for k, t in enumerate(reversed(lines_txt)):
        recs.append(mtext((x, y + k * lh), t, h, attach=6))
    recs.append(mtext((x, y + len(lines_txt) * lh), title, h, attach=6))
    w = max(text_w(t, h) for t in lines_txt + [title])
    return recs, w, (len(lines_txt) + 1) * lh


# MusaCAD's MTEXT middle-center anchor centers the line BOX (trailing
# advance + descender allowance included), so glyphs render low-left of the
# geometric anchor. Empirically calibrated compensation (center_test.musa):
MC_DX = 0.05   # x h
MC_DY = -0.33  # x h


def cell_text(center, s: str, h: float, props: str = P) -> str:
    """Text visually centered (both axes) at `center` — for FCF cells,
    datum letters, balloon numbers, arrow letters."""
    return mtext((center[0] + MC_DX * h, center[1] + MC_DY * h), s, h,
                 attach=4, props=props)
