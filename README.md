<p align="center"><b>fabrication_pipeline</b></p>

# Fabrication Pipeline

A repeatable, headless pipeline that takes **FreeCAD** assembly/part models,
analyses and validates them, and produces print-ready, vendor-complete
**A4 fabrication drawings (PDF)** on the RES title-block template using the
[MusaCAD](https://github.com/MusaCAD/MusaCAD) native `.musa` format and its
real plot pipeline.

Built for and driven by [Claude Code](https://claude.com/claude-code): the
pipeline stages are exposed as slash commands with an interactive,
decision-by-decision finalisation step.

## Pipeline

```
/analyse <model_dir>   headless FreeCAD extraction -> reports/analysis.json
/judge                 fabrication-readiness verdict (report-only)
        ⏸ user approval
/finalise              conversational elicitation -> locked manifests/manifest.json
/draw                  HLR views + sections + GD&T + dims -> output/musa/*.musa
/export                plot to A4 PDFs + merged print set -> output/pdf/
/pipeline <model_dir>  all stages, pausing after /judge
```

Subagents: `model-analyst` (FreeCAD interrogation), `draftsman` (.musa
composition), `qa-checker` (21-point output verification, render-and-inspect).

## What the drawings contain

- Per part: front view + full section (or an agreed alternative view),
  true-value dimensions (geometry authored full-size; the template is scaled
  uniformly — paper size and aspect ratio are never touched), feature
  location dims (pitch / edge references / TYP), depth-annotated fit
  callouts (e.g. `3 X Ø4 H7 (REAM) THRU`), GD&T datum flags + feature
  control frames, ANSI31 section hatch, fabrication notes, a shaded
  isometric pictorial, and a fully populated RES title block (scale, rev,
  weight computed from the model, parts row).
- Per assembly: NTS isometric HLR wireframe with circled balloons, parts
  table and build notes.
- A collision-driven layout engine places every annotation (dimension text
  modelled to MusaCAD's exact rendering geometry, line bands, extension
  corridors, leaders, frames, notes) with auto-nudge and a hard
  zero-overlap gate: a sheet that cannot be laid out cleanly fails loudly.

## Requirements

| Component | Notes |
|---|---|
| FreeCAD ≥ 1.1 (AppImage) | path configured in `src/common.py`; used headlessly (`TechDraw.projectEx`, `Shape.slice`, mesh export) |
| MusaCAD source + release build | `tools/musa_plot` links against its static libs (see `tools/CMakeLists.txt`); the MusaCAD tree is never modified |
| Qt 6, CMake, C++23 compiler | to build `tools/musa_plot` (`tools/build.sh`) |
| poppler-utils | `pdfinfo` / `pdftoppm` / `pdfunite` |
| Python 3.12 + pyvista/VTK + Pillow | shaded pictorials (offscreen), calibration tooling |

Build the plot harness once: `tools/build.sh` → `tools/bin/musa_plot`.

## Layout

```
src/          pipeline modules (one per stage + FreeCAD-side scripts)
tools/        musa_plot: headless .musa -> PDF harness (paper, scale,
              window, raster stamps) derived from MusaCAD's plot_check
templates/    RES title-block template (.musa v14)
.claude/      slash commands + subagents (the operator interface)
reports/ manifests/ output/    generated per-project artifacts (gitignored)
```

`CLAUDE.md` is the full project memory: toolchain invocations, the `.musa`
v14 grammar notes, the scale model, layout-engine rules, and the drawing
style contract.

## Using vs. developing

- **Using** (generating drawings for a project): run the slash commands
  only; per-project state lives in `reports/`, `manifests/`, `output/` and
  is not part of this repository.
- **Developing** (changing the software): everything under `src/`,
  `tools/`, `.claude/`, `templates/` — keep `CLAUDE.md` in sync.

## License

LGPL-3.0-or-later, matching MusaCAD — see [COPYING](COPYING) and
[COPYING.LESSER](COPYING.LESSER). Source files carry SPDX tags.
`tools/musa_plot.cpp` is derived from MusaCAD's `tools/plot_check.cpp`
(LGPL-3.0-or-later) and links against the MusaCAD libraries.

Copyright (C) 2026 Pranay Kiran
