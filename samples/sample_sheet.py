# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Pranay Kiran
"""A sample fabrication sheet for the drawing features of issue #2.

    python3 samples/sample_sheet.py [outdir]        # default: samples/out

Draws RES-SAMPLE-01, a bushing, carrying every feature the issue added:

  * an enlarged DETAIL view (2:1 on a 1:1 sheet) of the retaining groove in
    the section, whose dimensions read TRUE millimetres through DIMLFAC;
  * a datum (A) on a FEATURE OF SIZE — the %%c25 bore — with the datum
    triangle on that bore's own size callout, so the datum is its axis;
  * coaxiality and circular-runout feature control frames referring to it.

The part is synthesised here rather than projected from a .FCStd: the sheet
goes through the real composer (draw.build_part_sheet), so the sample and
the pipeline can never drift apart, but it needs no model and no FreeCAD.
Run it after changing the layout engine and look at the PDF.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import common                      # noqa: E402
import draw                        # noqa: E402
import musa_writer as mw           # noqa: E402

DRG_NO = "RES-SAMPLE-01"

# --- the part ---------------------------------------------------------
# A bushing: 60 outside, 25 bore, 30 long, four M6 clearance holes on a 46
# PCD, a 2 x 45 deg chamfer at the bore mouth and a 4 wide x 2 deep
# retaining groove round the outside, 8 down from the top face.
OD, BORE, LEN = 60.0, 25.0, 30.0
PCD, HOLE = 46.0, 6.0
GRV_Y0, GRV_Y1, GRV_D = 18.0, 22.0, 2.0     # groove: y span, depth
CHAMFER = 2.0


def seg(a, b):
    return {"t": "seg", "a": list(a), "b": list(b)}


def front_view() -> dict:
    """The face: outside circle, bore, and the four holes."""
    prims = [{"t": "circle", "c": [0.0, 0.0], "r": OD / 2.0},
             {"t": "circle", "c": [0.0, 0.0], "r": BORE / 2.0}]
    for deg in (45, 135, 225, 315):
        a = math.radians(deg)
        prims.append({"t": "circle",
                      "c": [PCD / 2.0 * math.cos(a), PCD / 2.0 * math.sin(a)],
                      "r": HOLE / 2.0})
    return {"visible": prims, "hidden": [],
            "bounds": [[-OD / 2.0, -OD / 2.0], [OD / 2.0, OD / 2.0]]}


def _band(x_in: float, x_out: float) -> list:
    """One side of the cut: the material between bore and outside, with the
    groove notched into the outer face and the chamfer at the bore mouth.
    Returned as a closed loop."""
    sgn = 1.0 if x_out > 0 else -1.0
    g = x_out - sgn * GRV_D                      # groove bottom
    return [(x_in, 0.0), (x_out, 0.0),
            (x_out, GRV_Y0), (g, GRV_Y0), (g, GRV_Y1), (x_out, GRV_Y1),
            (x_out, LEN), (x_in + sgn * CHAMFER, LEN),
            (x_in, LEN - CHAMFER)]


def section_view() -> dict:
    """SECTION A-A: the two material bands, hatched, bore between them."""
    prims, groups = [], []
    for x_in, x_out in ((-BORE / 2.0, -OD / 2.0), (BORE / 2.0, OD / 2.0)):
        loop = _band(x_in, x_out)
        for a, b in zip(loop, loop[1:] + loop[:1]):
            prims.append(seg(a, b))
        groups.append({"outer": [list(p) for p in loop], "holes": []})
    return {"visible": prims, "hidden": [], "hatch_groups": groups,
            "bounds": [[-OD / 2.0, 0.0], [OD / 2.0, LEN]]}


def manifest(analysis_path: Path) -> dict:
    """The locked manifest this sheet is drawn from — the real schema."""
    part = {
        "part_id": "01",
        "file": "/samples/bushing.FCStd",
        "description": "BUSHING - SAMPLE",
        "drg_no": DRG_NO,
        "material": "IS 2062 E250",
        "qty": 1,
        "front_view": "+Z",
        "scale": "1:1",
        "section": {"enabled": True, "projection": "below",
                    "offset_frac": 0.5, "label": "A"},
        "front_extent_dims": False,          # a round face: the %%c dims say it
        "callouts": [
            {"view": "front", "circle_r": BORE / 2.0,
             "text": "%%c25 H7 (REAM) THRU", "suppress_dia_dim": True},
            {"view": "front", "circle_r": HOLE / 2.0,
             "text": "4 X %%c6 THRU, EQUI-SPACED ON %%c46 PCD",
             "suppress_dia_dim": True},
        ],
        "center_circles": [{"view": "front", "dia": PCD}],
        # the feature the issue is about: a datum on the bore (a feature of
        # size), plus the two new frames referring to that axis
        "gdt": {
            "datums": [{"label": "A", "view": "front",
                        "target": f"bore_r{BORE / 2.0:g}"}],
            "frames": [
                {"symbol": "coaxiality", "view": "front",
                 "target": f"od_r{OD / 2.0:g}", "tol": "%%c0.1",
                 "refs": ["A"]},
                {"symbol": "runout", "view": "front",
                 "target": f"od_r{OD / 2.0:g}", "tol": "0.05",
                 "refs": ["A"]},
            ],
        },
        # the enlarged detail: the groove in the section, at 2:1
        "detail_view": {"enabled": True, "label": "B", "scale": "2:1",
                        "of": "section",
                        "center": [OD / 2.0, (GRV_Y0 + GRV_Y1) / 2.0],
                        "radius": 7.0},
        # authored in the PARENT view's true millimetres, like the circle
        "location_dims": [
            {"view": "detail", "orient": "v", "side": "right",
             "a": [OD / 2.0 - GRV_D, GRV_Y0],
             "b": [OD / 2.0 - GRV_D, GRV_Y1]},
            {"view": "detail", "orient": "h", "side": "top",
             "a": [OD / 2.0 - GRV_D, GRV_Y1], "b": [OD / 2.0, GRV_Y1]},
            {"view": "section", "orient": "v", "side": "left",
             "a": [-OD / 2.0, GRV_Y1], "b": [-OD / 2.0, LEN]},
        ],
        "fab_notes": [
            "GROOVE PROFILE PER DETAIL B.",
            "DEBURR ALL EDGES, 0.3 MAX.",
            "SURFACE FINISH N7 ON BORE.",
        ],
    }
    return {
        "locked": True,
        "project": "PIPELINE SAMPLE",
        "drg_no_prefix": "RES-SAMPLE",
        "drawn_by": "Er. P. PRANAY",
        "rev": "00",
        "model_dir": "/samples",
        "assembly_file": "",
        "parts": [part],
        "assembly_sheet": {"enabled": False},
        "_analysis_path": str(analysis_path),
    }


def analysis() -> dict:
    """Just enough analysis for the title block's WEIGHT cell."""
    vol = (math.pi / 4.0 * (OD ** 2 - BORE ** 2)) * LEN
    return {"model_dir": "/samples", "freecad_version": "1.1.1",
            "assembly": {"file": "", "document": "", "workbench": "none",
                         "instances": [], "joints": [], "issues": [],
                         "bbox_min": None, "bbox_max": None},
            "parts": [{"file": "/samples/bushing.FCStd",
                       "document": "bushing", "sketches": [], "issues": [],
                       "bodies": [{"label": "Body", "object": "Body",
                                   "type": "PartDesign::Body", "solids": 1,
                                   "faces": 20, "volume_mm3": round(vol, 3),
                                   "area_mm2": 0.0,
                                   "bbox_min": [-30.0, -30.0, 0.0],
                                   "bbox_max": [30.0, 30.0, 30.0],
                                   "bbox_size": [60.0, 60.0, 30.0],
                                   "material": None}]}]}


def build(outdir: Path) -> dict:
    """Write the sample sheet. Returns the sheet's metadata."""
    outdir.mkdir(parents=True, exist_ok=True)
    apath = outdir / "sample_analysis.json"
    common.write_json(apath, analysis())
    m = manifest(apath)
    part = m["parts"][0]
    proj = {"01:front": front_view(), "01:section": section_view()}
    ctx, replaced, lay = draw.build_part_sheet(m, part, proj)
    musa = outdir / f"{DRG_NO}.musa"
    musa.write_text(ctx.render())
    return {"musa": musa, "window": ctx.window, "scale": part["scale"],
            "layout": lay, "replaced": replaced}


def plot(info: dict, pdf: Path) -> dict:
    """Plot the sample with the real harness — the call /export makes."""
    num, den = draw.parse_scale(info["scale"])
    return common.run_musa_plot(info["musa"], pdf, scale=(num, den),
                                window=info["window"])


def main() -> None:
    outdir = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "samples" / "out"
    info = build(outdir)
    lay = info["layout"]
    print(f"{info['musa']}  (nudges={lay['nudges']}, "
          f"unresolved={lay['unresolved']})")
    if lay["unresolved"]:
        common.die(f"sample layout unresolved: {lay['unresolved']}")
    if common.MUSA_PLOT.exists():
        pdf = outdir / f"{DRG_NO}.pdf"
        print(f"{pdf}  {plot(info, pdf)['raw']}")
    else:
        print("harness not built (tools/build.sh) — .musa only")


if __name__ == "__main__":
    main()
