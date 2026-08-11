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


SIZE = 700          # square render window; the stamp letterboxes it
FILL = 0.86         # target: longest content side / frame, leaving a margin


def _content_fill(img) -> float:
    """Longest side of the rendered silhouette / frame size. Measured from
    the pixels, so it is right for any part shape."""
    ink = img.astype(int).sum(axis=2) < 720
    ys, xs = ink.nonzero()
    if len(xs) == 0:
        return 0.0
    return max(xs.max() - xs.min() + 1, ys.max() - ys.min() + 1) / img.shape[1]


def render_png(stl: Path, png: Path) -> None:
    """Shaded iso view, framed by MEASUREMENT: the fixed zoom used to clip
    long parts against the window edge (round-7 DEF-B). Render, measure the
    silhouette, correct the zoom, repeat until the part sits inside the
    frame with a margin."""
    import pyvista as pv
    pv.OFF_SCREEN = True
    mesh = pv.read(str(stl))
    pl = pv.Plotter(off_screen=True, window_size=(SIZE, SIZE),
                    lighting="three lights")
    pl.add_mesh(mesh, color="#9aa4b0", smooth_shading=False,
                specular=0.4, specular_power=12,
                diffuse=0.9, ambient=0.25)
    pl.set_background("white")
    pl.camera_position = "iso"
    # parallel projection: a true isometric (matching the sheet caption) and,
    # for the framing loop, a zoom that is exactly linear in the silhouette
    # size, so one correction step converges
    pl.enable_parallel_projection()
    # NB: camera.zoom() does not stick through Plotter.screenshot() in
    # pyvista 0.48 — set parallel_scale (the view half-height) directly and
    # re-render. Under a parallel projection the silhouette size is exactly
    # inversely proportional to it, so one correction lands on target.
    for _ in range(3):
        fill = _content_fill(pl.screenshot(None, return_img=True))
        if fill <= 0.0 or abs(fill - FILL) <= 0.01:
            break
        pl.camera.parallel_scale = pl.camera.parallel_scale * fill / FILL
        pl.render()
    pl.screenshot(str(png))


def main() -> None:
    m = read_json(MANIFESTS_DIR / "manifest.json")
    SHADED_DIR.mkdir(parents=True, exist_ok=True)
    jobs = [{"file": part["file"], "assembly": False,
             "out": str(SHADED_DIR / f"{part['part_id']}.stl")}
            for part in m["parts"]]
    if m.get("assembly_sheet") and m["assembly_sheet"].get("enabled", True):
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
