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


def run_musa_plot(musa_file: Path, pdf_out: Path, *, scale: tuple[float, float],
                  window: tuple[float, float, float, float],
                  stamp: tuple | None = None) -> dict:
    """Plot a sheet .musa to A4 portrait PDF at a fixed scale; parse the
    summary line into a dict (paper, mm_per_unit, scale, ...) for QA.
    stamp = (png_path, x, y, w, h) in sheet units (shaded pictorial)."""
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    cmd = [str(MUSA_PLOT), "--file", str(musa_file), str(pdf_out),
           "--paper", "A4", "--portrait",
           "--scale", f"{scale[0]}:{scale[1]}",
           "--window", ",".join(f"{v:.6f}" for v in window)]
    if stamp is not None:
        cmd += ["--stamp", stamp[0] + "," +
                ",".join(f"{v:.6f}" for v in stamp[1:])]
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
