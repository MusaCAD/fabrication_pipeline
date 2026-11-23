<p align="center"><b>fabrication_pipeline</b></p>

# Fabrication Pipeline

A repeatable, headless pipeline that takes **FreeCAD** assembly/part models,
analyses and validates them, and produces print-ready, vendor-complete
**A4 fabrication drawings (PDF)** on the project's own title-block template using the
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
  callouts (e.g. `3 X Ø4 H7 (REAM) THRU`), enlarged detail views (a
  circled region of a view redrawn at its own scale, dimensioned in true
  millimetres), ISO 6410 thread convention
  (models carry the real helix; views project the thread blank via
  `project_feature` and draw the root line), GD&T datum flags — on a face
  or on a feature of size, where the triangle goes on the bore's own size
  callout — and feature control frames (flatness, position,
  perpendicularity, coaxiality, circular runout), ANSI31 section hatch, fabrication notes, a shaded
  isometric pictorial, and a fully populated RES title block (scale, rev,
  weight computed from the model, parts row).
- Per assembly: NTS isometric HLR wireframe with circled balloons, parts
  table and build notes. Sub-assemblies are followed to the real parts at
  any depth, so the table counts each part across every sub-assembly that
  uses it and the balloons find it. Bought-out items (`"sheet": false` in the manifest)
  take a P.NO. row and a balloon but no sheet of their own.
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
tests/        unittest suite (stdlib only; builds throwaway FreeCAD
              fixtures for the assembly checks)
samples/      sample_sheet.py: a sample sheet through the real composer
              (no model, no FreeCAD) + the PDF it produces
tools/        musa_plot: headless .musa -> PDF harness (paper, scale,
              window, raster stamps) derived from MusaCAD's plot_check
templates/    default title-block template (.musa v14); company
              title blocks and logos live in each project's folder
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

### Tests

```
python3 -m unittest discover -s tests -v
```

Standard library only, no venv. `samples/sample_sheet.py` redraws
`samples/out/RES-SAMPLE-01.pdf`, a bushing carrying a 2:1 detail view, a
datum on its bore and coaxiality/runout frames — run it after touching the
layout engine and look at the sheet.

The assembly-joint tests build throwaway
FreeCAD 1.1 assemblies in a temporary directory with the real Assembly
workbench and run the FreeCAD-side scripts against them; they skip
themselves if the FreeCAD AppImage is not installed. Nothing under
`reports/`, `manifests/` or `output/` is touched, so the suite is safe to
run in the middle of a drawing project.

## License

LGPL-3.0-or-later, matching MusaCAD — see [COPYING](COPYING) and
[COPYING.LESSER](COPYING.LESSER). Source files carry SPDX tags.
`tools/musa_plot.cpp` is derived from MusaCAD's `tools/plot_check.cpp`
(LGPL-3.0-or-later) and links against the MusaCAD libraries.

Copyright (C) 2026 Pranay Kiran
