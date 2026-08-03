---
description: Generate MusaCAD sheets from the locked manifest
---

Run pipeline stage 4 (drawing generation):

1. Require manifests/manifest.json with "locked": true — else stop and point
   the user to /finalise.
2. Run `python3 src/draw.py`. It:
   - projects each part headlessly in FreeCAD (front view + full section per
     the manifest; HLR visible/hidden edges; section cut faces for hatching);
   - composes one .musa sheet per part on templates/template.musa:
     front view (top band) fully dimensioned, SECTION X-X below with ANSI31
     hatch, cutting-plane line + arrows + labels on the front view;
   - composes ONE assembly sheet: front view, P.NO. balloon leaders, filled
     parts table (P.NO., DESCRIPTION, QTY, DRG NO., MATERIAL, SIZE, WEIGHT,
     REMARKS — "-" where unknown);
   - fills the title block per sheet: DESCRIPTION, DRG NO., SCALE (the true
     plotted scale), REV, SHEET i OF n, dates (DD-MM-YY), parts-row entries.
     DESIGNED/DRAWN stay "Er. P. PRANAY" from the template;
   - writes output/musa/<DRG_NO>.musa + output/musa/sheets.json.
3. TWO VIEWS PER PART is the rule. If the script reports a view/fit problem
   or the user asks for more views, discuss with the user — never add views
   silently. If a part cannot fit legibly, the fix is a smaller standard
   scale in the manifest (re-finalise), never a distorted or cropped
   template.
4. HARD RULES: template geometry is only ever scaled uniformly
   (SheetContext guarantees this — do not bypass it); part geometry is
   authored full-size so DIM values read true millimetres.
5. Report per sheet: drg_no, scale, entity/dim/hatch counts, and any parts
   the script flagged. Delegate style/layout iteration to the draftsman
   subagent when substantial rework is needed.
