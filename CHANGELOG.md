# Changelog

All notable changes to this project. Versions follow
[semantic versioning](https://semver.org/) loosely: the minor number moves
when the drawing output or the manifest format gains something, the patch
number for fixes.

## v0.4.0 — 2026-11-20

Three drafting features turned and tubular parts needed before boards could
be drawn ([#2](https://github.com/MusaCAD/fabrication_pipeline/issues/2)).

### Added

- **Enlarged detail views.** Manifest part `"detail_view": {label, scale,
  of, center, radius}` — `of` names the view the region is taken from
  (`front`, `section`, `aux` or `flat`), `center` and `radius` are in that
  view's own coordinates. The region is clipped out of the parent view's
  projected geometry (edges, arcs and section hatch alike), redrawn at its
  own standard scale inside a thin boundary circle, captioned
  `DETAIL B (2:1)`, and marked with a matching circle and letter on the
  parent view. It is placed in free space, clear of the other views'
  dimension gutters, and collision-checked like every other annotation —
  the two-views rule is now **two views plus an optional detail**.
  **Its dimensions read true millimetres.** A DIM always displays the
  distance between its own def points, so a detail drawn at k times size
  dimensions through a DIMSTYLE carrying `DIMLFAC = 1/k` (the new
  `DIMUNITS` record): a 2:1 detail on a 1:5 sheet is drawn 10x and its
  style's factor is 0.1. Detail dims are authored in the parent view's
  true millimetres; `/draw` moves them onto the enlarged geometry.
- **Datums on a feature of size.** A datum target may now name a feature
  instead of a view edge: `"target": "bore_r12.5"` (any `<kind>_r<radius>`,
  the radius always in true millimetres). The datum is then the feature's
  AXIS, so the triangle goes on that feature's own size annotation — the
  shoulder of its callout's leader, as ISO 1101 and ASME Y14.5 draw it, or
  in line with its diameter dimension line when it is dimensioned that way.
  A feature with no size annotation fails the sheet rather than carrying a
  triangle that identifies nothing, and the box is placed clear of other
  labels so it cannot be read as theirs.
- **Coaxiality and circular runout** feature control frames, beside the
  existing flatness, position and perpendicularity. Coaxiality's tolerance
  zone is cylindrical, so its value carries the diameter sign.
- `musa_plot --dump-dims` prints every dimension on a sheet with the label
  **MusaCAD itself composes** for it (its own `compute_dim_geometry`, the
  plot's path), then exits without plotting. `common.dump_dims()` parses
  it. This is the only way to check what a sheet reads, and it is what the
  detail-view tests assert against. Rebuild the harness (`tools/build.sh`)
  to get it.
- `samples/sample_sheet.py` draws `samples/out/RES-SAMPLE-01.pdf`, a
  bushing with a 2:1 detail of its retaining groove, a datum on its bore
  and coaxiality/runout frames. It goes through the real composer but
  needs no model and no FreeCAD, so it is a quick look at the layout
  engine after a change.

### Changed

- `/finalise` (`manifest.py lock`) validates the new keys: a detail view's
  parent must be a view the sheet carries, its scale must be a standard one
  that really enlarges, its label must be a single letter that does not
  collide with the section's, and its centre and radius must be usable.
  Datum targets must be a known view edge or a parsable `_r<radius>`.
- `place_callouts` also reports each callout's leader landing, and
  `place_datums` / `place_fcfs` take the per-view linear factors, so a
  radius target means the same feature whatever view it is resolved in.

## v0.3.0 — 2026-11-19

### Changed

- **Company title blocks are project files.** The pipeline ships only its
  default `templates/template.musa`; a project keeps its own title block
  in its own folder and the manifest names it by absolute path
  (`"template": "/path/to/project/fabrication/template/template_x.musa"`).
  A bare name still resolves inside `templates/`. Company templates are
  removed from `templates/`.
  **Upgrade:** copy any company template you used from `templates/` into
  its project and set the manifest's `"template"` to that absolute path.
- `/finalise` (`manifest.py lock`) refuses a template that is missing,
  does not parse, or whose logo does not resolve.

### Added

- **Company logos on the title block.** A template may carry a raster
  logo: a sidecar `<template stem>.logo.json` next to it names a PNG
  (relative to the template's folder) and a box in template units. `/draw`
  records each sheet's logo rectangle in `sheets.json` (scaled with the
  template and fitted to the image's aspect, so it prints at the same size
  at every sheet scale) and `/export` stamps it into the PDF next to the
  shaded pictorial. Templates without a sidecar plot exactly as before.
- `common.musa_plot_cmd` builds the harness command line; `run_musa_plot`
  takes any number of stamps.
- Tests for the logo sidecar, the stamp rectangle at several scales, the
  harness command, template resolution and the lock-time template check.

## v0.2.0 — 2026-11-18

### Fixed

- **Assembly joints in FreeCAD 1.1 are read again**
  ([#1](https://github.com/MusaCAD/fabrication_pipeline/issues/1)).
  `/analyse` built each joint's connection list from the joint's `OutList`,
  keeping the `App::Link` objects. FreeCAD 1.1's Assembly workbench writes
  joint references as `Reference1 = (AssemblyObject, ["LinkName.Vertex1",
  ...])`, so the `OutList` holds only the Assembly object and every joint
  but the grounded one came back connecting nothing — `/judge` then warned
  "instances with no joint" for parts that were in fact fully joined (on
  one 14-joint assembly, 13 of 14 instances were reported loose).
  `fc_analyse` now reads `Reference1`/`Reference2` and the grounded joint's
  `ObjectToGround`, resolving the instance from the head of the subname
  path, and still falls back to the `OutList` scan, so FreeCAD 1.0 and
  older assemblies (references pointing straight at the link) read exactly
  as before. A genuinely unjoined instance is still warned about.
- A suppressed joint is now reported as `"suppressed": true` in
  `analysis.json` and no longer counts as joining the instances it names —
  it sits in the tree but constrains nothing.

### Added

- ISO 6410 thread convention: the model carries the real helix, the
  orthographic views project the thread blank (manifest
  `"project_feature"`) and draw the root line from manifest `"threads"`.
- Angular references: a labelled centre line through each angularly placed
  feature, a `0 REF` datum label and a CCW direction arc.
- Bought-out items (manifest `"sheet": false`): P.NO. row and assembly
  balloon, no sheet of their own; print weight falls back to grams below
  0.01 kg.
- Sheet-metal and plain-solid parts: the part of a document is its tip
  solid (`src/fc_shapes.py`), and a `*_Flat` twin drives a flat-pattern
  view with a bend table.
- Standalone part packs: `/analyse <dir> --no-assembly` and `--parts`.
- `templates/template_solon.musa`.
- A test suite: `python3 -m unittest discover -s tests -v` (stdlib only).
  The assembly tests build throwaway FreeCAD 1.1 assemblies with the real
  Assembly workbench and run the FreeCAD-side scripts against them; they
  skip themselves when the FreeCAD AppImage is absent, and never touch
  `reports/`, `manifests/` or `output/`.

### Changed

- `judge.assembly_findings()` is now a function of the analysis dict
  rather than inline in `main()`, so the model-level checks are testable.
- `src/fc_analyse.py` only runs its entry point when `FABPIPE_ARGS` is
  set: FreeCAD's `-c` executes a script as a module named after the file,
  never as `__main__`, so a `__main__` guard would never fire and the
  module could not be imported by the tests.
- Layout engine, `musa_writer` and shaded-pictorial refinements.

## v0.1.0 — 2026-08-01 … 2026-11-18

The pipeline up to commit `2ca1234`, released retrospectively: FreeCAD
model analysis, fabrication judgement, the locked manifest, MusaCAD `.musa`
sheet composition with the collision-driven layout engine, and A4 portrait
PDF export with the `musa_plot` harness.
