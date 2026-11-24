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

from common import (DRAWING_AREA, PAPER_PAINTABLE, S0, TEMPLATE_EXT_MAX,
                    TEMPLATE_EXT_MIN)

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


def widen_text(text: str, dx: float, dy: float) -> str:
    """The template's records with the sheet widened by (dx, dy)."""
    out = []
    for raw in text.split("\n"):
        tok = raw.split()
        if not tok:
            out.append(raw)
            continue
        key = tok[0]
        def move(xi, yi):
            """One point: it travels with the right edge and the top edge
            if it belongs to them, and stays otherwise."""
            if dx and float(tok[xi]) >= LEFT_EDGE_X:
                tok[xi] = repr(float(tok[xi]) + dx)
            if dy and float(tok[yi]) >= TOP_EDGE_Y:
                tok[yi] = repr(float(tok[yi]) + dy)

        if key == "LINE":
            move(1, 2)
            move(3, 4)
        elif key in ("CIRCLE", "ARC", "MTEXT"):
            move(1, 2)
        elif key == "POLYLINE":
            for j in range(int(tok[2])):
                move(3 + 2 * j, 4 + 2 * j)
        else:
            out.append(raw)
            continue
        out.append(" ".join(tok))
    return "\n".join(out)


def widen(src: Path, dst: Path, paper="A3", orientation="landscape") -> dict:
    dx, dy = widening(paper, orientation)
    dst.write_text(widen_text(src.read_text(), dx, dy))
    side = src.with_name(src.stem + ".logo.json")
    if side.exists():
        lg = json.loads(side.read_text())
        x0, y0, x1, y1 = (float(v) for v in lg["box"])
        lg["box"] = [x0 + dx, y0 + dy, x1 + dx, y1 + dy]
        (dst.parent / (dst.stem + ".logo.json")).write_text(
            json.dumps(lg, indent=1) + "\n")
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
