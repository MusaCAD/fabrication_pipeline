# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Pranay Kiran
"""Shared paths, constants and process runners for the fabrication pipeline.

Coordinate/scale model (see CLAUDE.md "Scale model"):
  - Sheets are authored in millimetre coordinates ("sheet space").
  - Part geometry is placed FULL SIZE (1 unit = 1 real mm) so DIM entities
    measure true values.
  - The template is uniformly scaled by t = S0 * den/num into sheet space,
    where num:den is the sheet's stated scale (e.g. 1:2 -> den=2).
  - The sheet is plotted with `musa_plot --scale num:den`, so the template
    always prints at its natural size on A4 portrait while the part prints
    at exactly num:den. Aspect ratio is never touched (uniform factors only).
"""

from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = PROJECT_ROOT / "templates" / "template.musa"
REPORTS_DIR = PROJECT_ROOT / "reports"
MANIFESTS_DIR = PROJECT_ROOT / "manifests"
OUTPUT_MUSA_DIR = PROJECT_ROOT / "output" / "musa"
OUTPUT_PDF_DIR = PROJECT_ROOT / "output" / "pdf"
SCRATCH_DIR = PROJECT_ROOT / "output" / "work"

FREECAD_APPIMAGE = Path("/home/pranay/programs/FreeCAD_1.1.1-Linux-x86_64-py311.AppImage")
MUSA_PLOT = PROJECT_ROOT / "tools" / "bin" / "musa_plot"

# Template geometry (measured in Phase 0 from templates/template.musa).
TEMPLATE_EXT_MIN = (19.134764420108695, 60.998650524366894)
TEMPLATE_EXT_MAX = (1083.8608307856775, 1669.9783134411991)
# Fit factor template-units -> paper mm on A4 portrait at 300 dpi
# (paintable 202.78 x 289.90 mm; height-limited). Verified by musa_plot.
S0 = 0.180175
# Usable drawing area between title block (top edge y=460.083) and revision
# table (bottom edge y=1516.995), inside the side borders — template units.
DRAWING_AREA = (58.544651860946026, 460.0832555520592,
                1044.425593134758, 1516.9949415656047)

# What the harness can actually paint on, per paper and orientation, in mm
# at 300 dpi — musa_plot's OWN numbers (its summary line prints
# "paintable=WxH"), not paper size minus a guessed margin. A sheet's fit
# factor is min(paintable / template extents), so this is what decides how
# big one template unit prints.
PAPER_PAINTABLE = {
    ("A4", "portrait"): (202.78, 289.90),
    ("A4", "landscape"): (289.90, 202.78),
    ("A3", "portrait"): (289.90, 413.09),
    ("A3", "landscape"): (413.09, 289.90),
    ("A2", "portrait"): (413.09, 586.99),
    ("A2", "landscape"): (586.99, 413.09),
}
DEFAULT_PAPER = ("A4", "portrait")


def paper_key(paper: str, orientation: str) -> str:
    """"A3" + "landscape" -> "A3L" — how a manifest names a template for a
    paper in its `templates` map."""
    return f"{str(paper).upper()}{str(orientation)[:1].upper()}"


def fit_factor(ext_w: float, ext_h: float, paper="A4",
               orientation="portrait") -> float:
    """Template units -> printed mm for a template of this size on this
    paper: the largest uniform factor that still fits (the scale model
    allows no other kind). The default A4-portrait sheet keeps the
    measured S0 to the digit, so every sheet drawn before paper sizes
    existed redraws byte for byte — the computed value agrees with it to
    2e-6."""
    pw, ph = PAPER_PAINTABLE[(str(paper).upper(), str(orientation))]
    return min(pw / ext_w, ph / ext_h)


def sheet_geom(ext_min=None, ext_max=None, drawing_area=None, paper="A4",
               orientation="portrait", s0=None, widen=(0.0, 0.0)) -> dict:
    """One sheet's geometry in TEMPLATE UNITS plus its paper.

    `widen` is how much bigger than the default template this one is: the
    sheet grows to the right and the top, and the furniture that hugs
    those edges (the title block, the parts table, the revision table)
    travels with them, which is exactly how an A3-landscape variant of an
    A4-portrait title block is built. Everything else — the row pitch, the
    text sizes, the left border — is untouched, so both papers print the
    same title block at the same size.
    """
    dx, dy = widen
    ext_min = tuple(ext_min or TEMPLATE_EXT_MIN)
    ext_max = tuple(ext_max or (TEMPLATE_EXT_MAX[0] + dx,
                                TEMPLATE_EXT_MAX[1] + dy))
    da = tuple(drawing_area or (DRAWING_AREA[0], DRAWING_AREA[1],
                                DRAWING_AREA[2] + dx, DRAWING_AREA[3] + dy))
    paper, orientation = str(paper).upper(), str(orientation)
    if s0 is None:
        s0 = (S0 if (paper, orientation) == DEFAULT_PAPER and not dx and not dy
              else fit_factor(ext_max[0] - ext_min[0],
                              ext_max[1] - ext_min[1], paper, orientation))
    return {"paper": paper, "orientation": orientation, "s0": s0,
            "ext_min": ext_min, "ext_max": ext_max, "drawing_area": da,
            "widen": (dx, dy)}


SHEET_GEOM_A4P = sheet_geom()

# Standard drawing scales to choose from, as (num, den): paper mm : real mm.
STANDARD_SCALES = [(1, 1), (1, 2), (1, 5), (1, 10), (2, 1), (5, 1)]

DATE_FMT = "%d-%m-%y"  # per reference drawing: 10-01-26


def today() -> str:
    return date.today().strftime(DATE_FMT)


def run_freecad(script: Path, *args: str, timeout: int = 300) -> str:
    """Run a Python script inside headless FreeCAD; return stdout.

    The script must end with sys.stdout.flush(); os._exit(0) (FreeCAD's -c
    console otherwise hangs or loses buffered output). Extra args arrive in
    the script via the FABPIPE_ARGS environment variable (JSON list).
    """
    env = dict(os.environ)
    env["FABPIPE_ARGS"] = json.dumps(list(args))
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    proc = subprocess.run(
        [str(FREECAD_APPIMAGE), "-c", str(script)],
        stdin=subprocess.DEVNULL, capture_output=True, text=True,
        timeout=timeout, env=env,
    )
    out = proc.stdout
    if proc.returncode not in (0,):  # AppImage exits 0 via os._exit(0)
        raise RuntimeError(
            f"FreeCAD failed ({proc.returncode}) for {script.name}:\n"
            f"{proc.stderr[-2000:]}\n{out[-2000:]}")
    return out


def musa_plot_cmd(musa_file: Path, pdf_out: Path, *, scale: tuple[float, float],
                  window: tuple[float, float, float, float],
                  stamps: list | None = None, paper: str = "A4",
                  orientation: str = "portrait") -> list[str]:
    """The harness command line. stamps: (png_path, x, y, w, h) tuples in
    sheet units — the shaded pictorial and the title block's logo."""
    cmd = [str(MUSA_PLOT), "--file", str(musa_file), str(pdf_out),
           "--paper", str(paper).upper(),
           "--landscape" if str(orientation) == "landscape" else "--portrait",
           "--scale", f"{scale[0]}:{scale[1]}",
           "--window", ",".join(f"{v:.6f}" for v in window)]
    for st in stamps or []:
        cmd += ["--stamp", str(st[0]) + "," +
                ",".join(f"{v:.6f}" for v in st[1:])]
    # floor hairline strokes (stroke-font text plots at width 0 upstream —
    # vendor print legibility; see musacad-gap-issues)
    cmd += ["--min-lw", "0.30"]
    return cmd


def run_musa_plot(musa_file: Path, pdf_out: Path, *, scale: tuple[float, float],
                  window: tuple[float, float, float, float],
                  stamp: tuple | None = None,
                  stamps: list | None = None, paper: str = "A4",
                  orientation: str = "portrait") -> dict:
    """Plot a sheet .musa to PDF on its own paper at a fixed scale; parse
    the summary line into a dict (paper, mm_per_unit, scale, ...) for QA.
    stamp = (png_path, x, y, w, h) in sheet units (shaded pictorial);
    stamps = several of them (the pictorial and the title block's logo)."""
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    all_stamps = ([stamp] if stamp is not None else []) + list(stamps or [])
    cmd = musa_plot_cmd(musa_file, pdf_out, scale=scale, window=window,
                        stamps=all_stamps, paper=paper,
                        orientation=orientation)
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    line = next((l for l in proc.stdout.splitlines()
                 if l.startswith("[musa_plot] ok")), None)
    if proc.returncode != 0 or line is None:
        raise RuntimeError(f"musa_plot failed:\n{proc.stdout}\n{proc.stderr}")
    info: dict = {"raw": line}
    for tok in line.split():
        if "=" in tok:
            k, _, v = tok.partition("=")
            info[k] = v
    return info


def dump_dims(musa_file: Path) -> list[dict]:
    """Every dimension on a sheet as the RENDERER reads it: its def points,
    the raw measured distance, and the label MusaCAD composes for it — the
    style's DIMLFAC, precision, zero suppression and decoration all applied
    (musa_plot --dump-dims, which calls compute_dim_geometry exactly as the
    plot path does).

    A DIM always displays the distance between its own def points, so this
    is the only way to check what a sheet really reads: in an enlarged
    DETAIL view the def points are k times the true size and the value is
    true only because the style carries DIMLFAC = 1/k."""
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    proc = subprocess.run([str(MUSA_PLOT), "--file", str(musa_file),
                           os.devnull, "--dump-dims"],
                          capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        raise RuntimeError(f"musa_plot --dump-dims failed:\n{proc.stdout}\n"
                           f"{proc.stderr}")
    dims = []
    for line in proc.stdout.splitlines():
        if not line.startswith("[musa_plot] dim "):
            continue
        body = line[len("[musa_plot] dim "):]
        head, _, label = body.partition("label=")
        toks = head.split()
        d: dict = {"index": int(toks[0]), "label": label}
        for tok in toks[1:]:
            k, _, v = tok.partition("=")
            d[k] = ([float(x) for x in v.split(",")] if "," in v
                    else float(v) if k == "measure" else int(v))
        dims.append(d)
    return dims


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n")


def read_json(path: Path):
    return json.loads(path.read_text())


def png_size(path: Path) -> tuple[int, int] | None:
    """(width, height) straight out of a PNG's IHDR — stdlib only. The stamp
    rect must letterbox the REAL image; never assume it is square."""
    try:
        head = path.read_bytes()[:24]
    except OSError:
        return None
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n" \
            or head[12:16] != b"IHDR":
        return None
    w, h = struct.unpack(">II", head[16:24])
    return (w, h) if w and h else None


def latest(path_glob: str, base: Path) -> Path | None:
    files = sorted(base.glob(path_glob))
    return files[-1] if files else None


def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(1)
