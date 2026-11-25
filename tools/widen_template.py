# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Pranay Kiran
"""Derive a bigger-paper title block from an A4-portrait one.

    python3 tools/widen_template.py <src.musa> <dst.musa> [--paper A3]
                                    [--landscape | --portrait]

A sheet of another paper is the SAME title block on a bigger piece of
paper: same units, same row pitch, same text, same printed size — the
drawing area is what grows. So the trim frame and border stretch, the
title block, parts table and revision table travel with the right edge and
the top, and the left-hand corner marks stay where they are. That is all
this does, to the template's own coordinates.

The furniture travels WHOLE (issue #28). The revision table, the parts
row and the title block run from border to border on the A4 sheet, so
moving only their right-hand ends stretched each of them by the whole
widening — about 221 mm on A3 landscape — and left a row of empty cells
across the top of the sheet and an empty box beside the title block,
holding nothing and reading as part of the title block. Both ends of
those rules move now, each band is CLOSED on its new left edge, and the
space they leave is drawing area (common.sheet_geom's `sheet_area` and
`blocks`, measured back off the file by musa_writer.template_furniture).

The result needs no sidecar: src/musa_writer.template_geom measures the
widening from the file and works out which paper it is. A logo sidecar
(`<stem>.logo.json`) is rewritten alongside, its box moved with the title
block.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from common import (BLOCK_TOL, DRAWING_AREA, FRAME_INNER, PAPER_PAINTABLE,
                    S0, TEMPLATE_EXT_MAX, TEMPLATE_EXT_MIN, furniture_bands)

# x below this belongs to the sheet's LEFT edge: the trim frame's left side
# and the left-hand corner marks, which stay put while the sheet widens.
LEFT_EDGE_X = 60.0
# y at or above this belongs to the sheet's TOP: the drawing area's top
# edge, the revision table above it and the top frame, which travel up
# when the sheet grows taller. The title block stays on the bottom edge.
TOP_EDGE_Y = DRAWING_AREA[3] - 0.5
MIN_WIDEN = 1.0          # units; a fifth of a millimetre is not a widening


def widening(paper: str, orientation: str) -> tuple[float, float]:
    """(dx, dy) in template units that fills this paper at the same unit
    size: the title block keeps its printed size, the drawing area gets
    every extra millimetre."""
    pw, ph = PAPER_PAINTABLE[(paper.upper(), orientation)]
    w = TEMPLATE_EXT_MAX[0] - TEMPLATE_EXT_MIN[0]
    h = TEMPLATE_EXT_MAX[1] - TEMPLATE_EXT_MIN[1]
    dx, dy = pw / S0 - w, ph / S0 - h
    return (dx if dx >= MIN_WIDEN else 0.0, dy if dy >= MIN_WIDEN else 0.0)


def point_indices(tok) -> list | None:
    """The (x index, y index) of every point a record carries, or None for
    a record that holds no coordinates."""
    key = tok[0]
    if key == "LINE":
        return [(1, 2), (3, 4)]
    if key in ("CIRCLE", "ARC", "MTEXT"):
        return [(1, 2)]
    if key == "POLYLINE":
        return [(3 + 2 * j, 4 + 2 * j) for j in range(int(tok[2]))]
    return None


def furniture_band(pts) -> int | None:
    """Which furniture band this record belongs to, or None.

    A band's own rules lie wholly inside one band and inside the border.
    The border itself runs through BOTH bands and the corner marks lie
    outside it, so neither is furniture and neither travels with it."""
    for i, (_name, y0, y1) in enumerate(furniture_bands(DRAWING_AREA)):
        if all(y0 - BLOCK_TOL <= y <= y1 + BLOCK_TOL for _x, y in pts) \
                and all(FRAME_INNER[0] - BLOCK_TOL <= x
                        <= FRAME_INNER[2] + BLOCK_TOL for x, _y in pts):
            return i
    return None


def closing_rules(text: str, dx: float) -> list[str]:
    """The vertical rule each furniture band needs on its new left edge.

    On the A4 sheet the border closes these bands; once they move right
    each one needs a left-hand side of its own, or the title block and
    the revision table print as open boxes. A band that already carries
    that rule — the revision table does — travels with it and needs
    nothing, so nothing is drawn twice.
    """
    if not dx:
        return []
    left = FRAME_INNER[0] + dx
    bands = furniture_bands(DRAWING_AREA)
    rules = []
    for raw in text.split("\n"):
        tok = raw.split()
        if not tok or tok[0] != "LINE":
            continue
        nums = [float(v) for v in tok[1:5]]
        band = furniture_band([(nums[0], nums[1]), (nums[2], nums[3])])
        if band is not None:
            rules.append((band, nums, " ".join(tok[5:12])))
    out = []
    for band, (_name, y0, y1) in enumerate(bands):
        mine = [(nums, props) for b, nums, props in rules if b == band]
        if not mine:
            continue                   # the band draws nothing to close
        if any(abs(nums[0] - FRAME_INNER[0]) < BLOCK_TOL
               and abs(nums[2] - FRAME_INNER[0]) < BLOCK_TOL
               and min(nums[1], nums[3]) <= y0 + BLOCK_TOL
               and max(nums[1], nums[3]) >= y1 - BLOCK_TOL
               for nums, _p in mine):
            continue                   # it has a left-hand side already
        out.append(f"LINE {left!r} {y0!r} {left!r} {y1!r} {mine[0][1]}")
    return out


def widen_text(text: str, dx: float, dy: float) -> str:
    """The template's records with the sheet widened by (dx, dy)."""
    out = []
    for raw in text.split("\n"):
        tok = raw.split()
        if not tok:
            out.append(raw)
            continue
        idx = point_indices(tok)
        if idx is None:
            out.append(raw)
            continue
        band = furniture_band([(float(tok[xi]), float(tok[yi]))
                               for xi, yi in idx])

        def move(xi, yi):
            """One point: it travels with the right edge and the top edge
            if it belongs to them, and stays otherwise. Furniture travels
            WHOLE, both ends of every rule, so the title block keeps its
            A4 width instead of being stretched (issue #28)."""
            if dx and (band is not None or float(tok[xi]) >= LEFT_EDGE_X):
                tok[xi] = repr(float(tok[xi]) + dx)
            if dy and float(tok[yi]) >= TOP_EDGE_Y:
                tok[yi] = repr(float(tok[yi]) + dy)

        for xi, yi in idx:
            move(xi, yi)
        out.append(" ".join(tok))
    # the bands are closed on their new left edge, before END
    rules = closing_rules(text, dx)
    if rules:
        for i, raw in enumerate(out):
            if raw.strip() == "END":
                out[i:i] = rules
                break
        else:
            out += rules
    return "\n".join(out)


def widen(src: Path, dst: Path, paper="A3", orientation="landscape") -> dict:
    dx, dy = widening(paper, orientation)
    dst.write_text(widen_text(src.read_text(), dx, dy))
    side = src.with_name(src.stem + ".logo.json")
    if side.exists():
        # the box is copied UNMOVED, in the A4 title block's own units:
        # SheetContext.logo_stamp moves a title-block x with the right
        # edge itself, and a box written already moved is moved twice and
        # prints off the sheet (issue #15)
        (dst.parent / (dst.stem + ".logo.json")).write_text(
            json.dumps(json.loads(side.read_text()), indent=1) + "\n")
    return {"dx": dx, "dy": dy, "paper": paper, "orientation": orientation}


def main() -> None:
    args = [a for a in sys.argv[1:]]
    paper, orientation = "A3", "landscape"
    if "--paper" in args:
        i = args.index("--paper")
        paper = args[i + 1]
        del args[i:i + 2]
    for flag in ("--landscape", "--portrait"):
        if flag in args:
            orientation = flag[2:]
            args.remove(flag)
    if len(args) != 2:
        print(__doc__)
        raise SystemExit(2)
    info = widen(Path(args[0]), Path(args[1]), paper, orientation)
    print(f"{args[1]}: {info['paper']} {info['orientation']}, widened by "
          f"({info['dx']:.3f}, {info['dy']:.3f}) template units")


if __name__ == "__main__":
    main()
