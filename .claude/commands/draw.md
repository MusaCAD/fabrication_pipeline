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

The run ends with TWO CHECKS, both read off the finished sheets:

- the VALUE CHECK (src/verify.py): every value the manifest states is
  compared with what the sheet prints;
- the LAYOUT CHECK (src/layout_check.py): how the sheet looks — leaders
  crossing each other, a leader running along the outline it points at,
  through another view, or across a dimension line, a sight arrow or the
  pictorial box, a symbol or balloon lying on text, a datum box nearer
  another callout than its own, a callout column so narrow its note comes
  out one word a line, text outside the drawing area, and (as warnings
  only) two leaders running together closer than 2 mm and a leader across
  an extension line.

Both are printed and recorded in sheets.json. /export will refuse
anything but the warning, so fix the manifest or the sheet — or waive one
deliberately in "value_waivers" / "layout_waivers" — and re-run. A
leader fault usually means the callout's arrow (`at`) wants moving, the
note belongs on the other view, or the sheet needs more room.
