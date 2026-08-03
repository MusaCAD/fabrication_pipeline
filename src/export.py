# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Pranay Kiran
"""Stage 5 — /export: plot every sheet to PDF and build the print set.

    python3 src/export.py

Reads output/musa/sheets.json (from /draw). Each sheet is plotted A4
portrait at its manifest scale via tools/bin/musa_plot; the stated scale is
cross-checked against the harness's effective mm_per_unit. Merges all sheets
into output/pdf/print_set.pdf (pdfunite). Writes output/pdf/export.json for
qa-checker.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (OUTPUT_MUSA_DIR, OUTPUT_PDF_DIR, die, read_json,
                    run_musa_plot, write_json)


def main() -> None:
    meta_path = OUTPUT_MUSA_DIR / "sheets.json"
    if not meta_path.exists():
        die("no output/musa/sheets.json — run /draw first")
    meta = read_json(meta_path)
    OUTPUT_PDF_DIR.mkdir(parents=True, exist_ok=True)

    exported = []
    for sheet in meta["sheets"]:
        num, den = (int(x) for x in sheet["scale"].split(":"))
        pdf = OUTPUT_PDF_DIR / (sheet["drg_no"] + ".pdf")
        stamp = None
        st = sheet.get("stamp")
        if st:
            png = Path(st["png"])
            if not png.is_absolute():
                png = OUTPUT_MUSA_DIR.parent.parent / png
            if png.exists():
                stamp = (str(png), *st["rect"])
            else:
                die(f"{sheet['drg_no']}: shaded pictorial missing: {png} "
                    "(run: python3 src/shaded.py)")
        info = run_musa_plot(Path(sheet["file"]), pdf,
                             scale=(num, den), window=tuple(sheet["window"]),
                             stamp=stamp)
        eff = float(info["mm_per_unit"])
        want = num / den
        if abs(eff - want) > 1e-6:
            die(f"{pdf.name}: effective scale {eff} != stated {want}")
        if info.get("paper") != "A4" or "portrait" not in info["raw"]:
            die(f"{pdf.name}: not A4 portrait ({info['raw']})")
        exported.append({**sheet, "pdf": str(pdf),
                         "mm_per_unit": eff, "plot": info["raw"]})
        print(f"exported {pdf.name}  ({sheet['scale']}, A4 portrait)")

    merged = OUTPUT_PDF_DIR / "print_set.pdf"
    cmd = ["pdfunite"] + [s["pdf"] for s in exported] + [str(merged)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        die(f"pdfunite failed: {r.stderr}")
    print(f"print set: {merged}  ({len(exported)} sheets)")
    write_json(OUTPUT_PDF_DIR / "export.json",
               {"sheets": exported, "print_set": str(merged)})


if __name__ == "__main__":
    main()
