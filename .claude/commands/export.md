---
description: Plot sheets to A4 portrait PDFs and build the print set
---

Run pipeline stage 5 (export):

1. Run `python3 src/export.py`. It plots every sheet from
   output/musa/sheets.json via tools/bin/musa_plot (QT_QPA_PLATFORM=offscreen)
   at the sheet's manifest scale on A4 portrait, verifies the effective
   mm_per_unit equals the stated scale, writes one PDF per sheet to
   output/pdf/<DRG_NO>.pdf, merges them into output/pdf/print_set.pdf with
   pdfunite, and records output/pdf/export.json.
2. If tools/bin/musa_plot is missing, build it first: `tools/build.sh`.
3. After export, ALWAYS run the qa-checker subagent on output/pdf/ — it
   verifies A4 portrait paper, aspect ratio, one sheet per manifest part,
   section hatching present, title-block completeness, and that the stated
   SCALE matches the scale actually plotted.
4. Present the user: list of PDFs with drg_no + scale, the print-set path,
   and the qa-checker verdict WITH its full checklist (the user wants to
   see what was verified, not just the conclusion). Render a preview image
   of at least one sheet (pdftoppm -png -r 50) and show it.
