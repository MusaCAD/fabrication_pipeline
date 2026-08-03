# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Pranay Kiran
"""Shaded isometric pictorial renders (Route B of the raster investigation:
MusaCAD has no raster records, so the PNG is stamped onto the PDF page by
tools/bin/musa_plot --stamp in the same painter session).

    python3 src/shaded.py            # renders every manifest part + assembly

FreeCAD exports STL headlessly (fc_shaded.py); pyvista renders offscreen
with a consistent iso camera and white background. Output:
output/work/shaded/<part_id|assembly>.png
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import MANIFESTS_DIR, SCRATCH_DIR, die, read_json, run_freecad

FC_SHADED = Path(__file__).resolve().parent / "fc_shaded.py"
SHADED_DIR = SCRATCH_DIR / "shaded"


def render_png(stl: Path, png: Path) -> None:
    import pyvista as pv
    pv.OFF_SCREEN = True
    mesh = pv.read(str(stl))
    pl = pv.Plotter(off_screen=True, window_size=(700, 700), lighting="three lights")
    pl.add_mesh(mesh, color="#9aa4b0", smooth_shading=False,
                specular=0.4, specular_power=12,
                diffuse=0.9, ambient=0.25)
    pl.set_background("white")
    pl.camera_position = "iso"
    pl.camera.zoom(1.25)
    pl.screenshot(str(png))


def main() -> None:
    m = read_json(MANIFESTS_DIR / "manifest.json")
    SHADED_DIR.mkdir(parents=True, exist_ok=True)
    jobs = [{"file": part["file"], "assembly": False,
             "out": str(SHADED_DIR / f"{part['part_id']}.stl")}
            for part in m["parts"]]
    jobs.append({"file": m["assembly_file"], "assembly": True,
                 "out": str(SHADED_DIR / "assembly.stl")})
    out = run_freecad(FC_SHADED, *jobs, timeout=600)
    if out.count("MESH_OK") != len(jobs):
        die(f"mesh export incomplete:\n{out[-1500:]}")
    for job in jobs:
        stl = Path(job["out"])
        png = stl.with_suffix(".png")
        render_png(stl, png)
        print(f"shaded: {png.name}")
    print(f"renders in {SHADED_DIR}")


if __name__ == "__main__":
    main()
