<p align="center"><b>fabrication_pipeline</b></p>

# Fabrication Pipeline

A repeatable, headless pipeline that takes **FreeCAD** assembly/part models,
analyses and validates them, and produces print-ready, vendor-complete
**Fabrication drawings (PDF)** on the project's own title-block template using the
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
                       (+ the value check and the layout check)
/export                plot each sheet on its paper + print set -> output/pdf/
/pipeline <model_dir>  all stages, pausing after /judge
```

Subagents: `model-analyst` (FreeCAD interrogation), `draftsman` (.musa
composition), `qa-checker` (21-point output verification, render-and-inspect).

## What the drawings contain

Sheets are A4 portrait unless one says otherwise: a part entry or the
assembly sheet may state `"paper": "A3L"`, drawn on that paper's title
block (`"templates": {"A3L": "/abs/path.musa"}`, made from the A4 one with
`tools/widen_template.py`). One set may mix them — an A3-landscape
assembly sheet among A4 part sheets — with the title block the same
printed size on every sheet and all the extra paper going to the drawing.


- Per part: front view + full section (or an agreed alternative view),
  true-value dimensions (geometry authored full-size; the template is scaled
  uniformly — paper size and aspect ratio are never touched), feature
  location dims (pitch / edge references / TYP), depth-annotated fit
  callouts (e.g. `3 X Ø4 H7 (REAM) THRU`), enlarged detail views (a
  circled region of a view redrawn at its own scale, dimensioned in true
  millimetres), ISO 6410 thread convention
  (models carry the real helix; views project the thread blank via
  `project_feature` and draw the root line; a tapped hole is drawn from
  its tap drill out to the major diameter, with the thread-end line and a
  three-quarter arc in the end view, its thread-end line where the thread
  really stops), tabulated drawings (one sheet for a
  family that differs in one size: a letter dimension and a table of
  variants), GD&T datum flags — on a face,
  or on a feature of size, where the triangle goes on that feature's own
  size callout even when the views show it only edge-on, as a turned part
  does — and feature control frames (flatness, position, perpendicularity,
  coaxiality, circular runout), including frames on a plane face, leadered
  to the nearest edge-on line of that face and never across the section
  hatch. A datum triangle is set back along its own leader far enough to
  clear the callout's text, so no glyph is ever covered. Where one view holds two features of the same size, the datum or
  frame says which it means (`bore_r35.75@tail`, naming a callout's `id`,
  or `@x280`, its coordinate in the view); a frame is drawn only when it
  touches its own size callout or carries a leader to it, and the sheet
  fails loudly otherwise. ANSI31 section hatch, fabrication notes, a shaded
  isometric pictorial, and a fully populated RES title block (scale, rev,
  weight computed from the model, parts row).
- Per assembly: NTS isometric HLR wireframe (or an orthographic view with
  a section, hatched part by part so neighbours can be told apart and
  leaving shafts, screws and pins uncut), enlarged detail views, one
  circled balloon per part number across the whole sheet — in two columns,
  one each side of the view, when one cannot hold them all, and on the
  continuation sheet with the detail that shows the part when the
  manifest puts that detail there — a parts table
  that can be grouped by sub-assembly with quantities per group and in
  total, and build notes. Sub-assemblies are followed to the real parts at
  any depth, so the table counts each part across every sub-assembly that
  uses it and the balloons find it. Every quantity comes from the model's
  own usage count, once: one row per part number, a heading row stating
  the sub-assembly's own quantity, and a part shared between
  sub-assemblies listed once in a common section rather than counted in
  each. A table too long for the sheet groups
  bought-out hardware by kind, then continues on a second sheet — and says
  which fallback it used. Bought-out items (`"sheet": false` in the manifest)
  take a P.NO. row and a balloon but no sheet of their own.
- A collision-driven layout engine places every annotation (dimension text
  modelled to MusaCAD's exact rendering geometry, line bands, extension
  corridors, leaders, frames, notes) with auto-nudge and a hard
  zero-overlap gate: a sheet that cannot be laid out cleanly fails loudly.
  A leader meets its feature at an angle and never lies on the outline it
  points at or crosses another leader; when the manifest's order would
  make two cross, the callouts are re-composed in the order their arrows
  are seen in from the column they share.

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
              window, raster stamps) derived from MusaCAD's plot_check;
              widen_template.py: an A3/A2 title block from the A4 one
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

Every sheet is checked twice before it can be exported, both times by
reading the finished sheet rather than asking the engine what it did.

The VALUE check compares every value the manifest states — callout and
note text, tolerances, datum letters, the title block, the value each
location dimension must measure — with what the sheet actually prints. A
dropped fit class or a model coordinate printed as a dimension is named,
with the sheet it is on.

The LAYOUT check reads how the sheet looks: leaders crossing each other,
a leader running along the outline it points at, a symbol or balloon
lying on text, text outside the drawing area, and — as a warning — two
leaders running together closer than 2 mm.

/export refuses to run on anything either of them finds that the manifest
does not waive (`"value_waivers"`, `"layout_waivers"`).

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
