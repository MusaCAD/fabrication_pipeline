---
description: Run headless FreeCAD extraction on a model directory
argument-hint: <model_dir> [assembly.FCStd]
---

Run pipeline stage 1 (analysis) on the model directory given in $ARGUMENTS
(default if empty: /home/pranay/projects/lisocl2/extrusion_jig_x3/).

1. Run: `python3 src/analyse.py <model_dir> [assembly.FCStd]`
   (from the project root; the script drives headless FreeCAD itself and
   writes reports/analysis.json, a timestamped copy, and reports/analysis.md).
2. If it fails, diagnose using CLAUDE.md "FreeCAD headless" notes (stdin must
   be closed; scripts must flush and os._exit) — do not hand-roll a new
   extraction script; fix src/ if genuinely broken.
3. Present reports/analysis.md to the user, highlighting: part count,
   assembly workbench, per-part bounding boxes, material status, any
   issues list entries, and the VISIBILITY section — what each file
   shows when it is opened (issue #23). A file that opens with nothing
   visible is a FAIL at /judge: the geometry is there, the file just
   shows none of it.

Models are READ-ONLY: never save or modify anything under the model
directory. For deeper one-off interrogation, delegate to the model-analyst
subagent.
