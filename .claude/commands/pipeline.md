---
description: Run all pipeline stages with an approval pause after /judge
argument-hint: <model_dir>
---

Run the full fabrication pipeline on the model directory in $ARGUMENTS
(default: /home/pranay/projects/lisocl2/extrusion_jig_x3/):

1. /analyse $ARGUMENTS — headless FreeCAD extraction; present the report.
2. /judge — present the verdict.
3. ⏸ **PAUSE — MANDATORY.** Stop and wait for the user's explicit approval
   of the judgement before continuing, regardless of PASS/WARN/FAIL. On FAIL
   the user must fix models in FreeCAD and the pipeline restarts at
   /analyse. Never continue past this point on your own.
4. /finalise — interactive per-part decisions with the user, lock manifest.
5. /draw — generate the .musa sheets.
6. /export — produce PDFs + print set, then run qa-checker.
7. Present the final PDFs (paths + a rendered preview of each sheet at low
   resolution) and the QA verdict.

Each stage runs its own command file's instructions in full (including the
qa-checker at /export). If any stage fails, stop, show the real error, and
wait for direction — do not skip stages or fabricate outputs.
