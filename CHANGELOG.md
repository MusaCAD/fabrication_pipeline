# Changelog

All notable changes to this project. Versions follow
[semantic versioning](https://semver.org/) loosely: the minor number moves
when the drawing output or the manifest format gains something, the patch
number for fixes.

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
