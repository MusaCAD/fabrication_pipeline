# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Pranay Kiran
"""Stage 5 — /export: plot every sheet to PDF and build the print set.

    python3 src/export.py

Reads output/musa/sheets.json (from /draw). Each sheet is plotted at its
manifest scale on its OWN paper via tools/bin/musa_plot (A4 portrait unless
that sheet says otherwise); the stated scale is cross-checked against the
harness's effective mm_per_unit, and the paper against its own report. Merges all sheets
into output/pdf/print_set.pdf (pdfunite). Writes output/pdf/export.json for
qa-checker.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (OUTPUT_MUSA_DIR, OUTPUT_PDF_DIR, die, read_json,
                    run_musa_plot, write_json)


def sheet_pdf_name(sheet: dict, sheets_of: dict) -> str:
    """What this sheet's PDF is called (issue #24).

    A drawing of one sheet keeps the drawing number, as every set drawn
    so far does. A drawing of several gets one file per sheet —
    `<DRG>_SH1`, `<DRG>_SH2` — because they are different pages and a
    file can only hold one of them; the sheets are merged back into
    `<DRG>.pdf` for delivery, in order."""
    drg = sheet["drg_no"]
    mates = sheets_of.get(drg) or [sheet]
    if len(mates) < 2:
        return drg
    # by identity: two sheets of one drawing differ only in their file
    i = next((k for k, m in enumerate(mates) if m is sheet), 0) + 1
    of = sheet.get("sheet_of")
    if of and len(of) == 2:
        i = int(of[0])
    return f"{drg}_SH{i}"


def merge(pdfs, out: Path) -> None:
    """pdfunite, with the one-file case taken as a copy: pdfunite needs
    at least two inputs."""
    if len(pdfs) == 1:
        shutil.copy2(pdfs[0], out)
        return
    r = subprocess.run(["pdfunite"] + [str(p) for p in pdfs] + [str(out)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        die(f"pdfunite failed: {r.stderr}")


def main() -> None:
    meta_path = OUTPUT_MUSA_DIR / "sheets.json"
    if not meta_path.exists():
        die("no output/musa/sheets.json — run /draw first")
    meta = read_json(meta_path)
    OUTPUT_PDF_DIR.mkdir(parents=True, exist_ok=True)

    # the value check (issue #14): /draw compared every value the manifest
    # states with what the sheet prints. A difference nobody waived stops
    # the export — a dropped tolerance reaches the vendor otherwise.
    bad = []
    for sheet in meta["sheets"]:
        for t in sheet.get("template_problems") or []:
            bad.append(f"{sheet['drg_no']}: TITLE BLOCK: {t}")
        for f in sheet.get("values") or []:
            bad.append(f"{sheet['drg_no']}: {f['kind'].upper()}: {f['what']}"
                       + (f" — the sheet prints {f['found']!r}"
                          if f.get("found") else ""))
    if bad:
        die("the sheets do not say what the manifest states:\n  "
            + "\n  ".join(bad)
            + "\n  fix the manifest or the sheet and re-run /draw, or waive "
              "it in the manifest's \"value_waivers\"")
    # the layout check (issue #22): what the sheets LOOK like. The engine
    # only knows that the boxes it placed do not overlap; this is read off
    # the finished sheet, and every one of these faults reached a vendor
    # once because nothing was reading it.
    bad = []
    for sheet in meta["sheets"]:
        for f in sheet.get("layout_faults") or []:
            bad.append(f"{sheet['drg_no']}: {f['kind'].upper()}: "
                       f"{f['what']}")
    if bad:
        die("the sheets do not read cleanly:\n  " + "\n  ".join(bad)
            + "\n  fix the layout and re-run /draw, or waive it in the "
              "manifest's \"layout_waivers\"")
    warn = [f"{s['drg_no']}: {f['what']}" for s in meta["sheets"]
            for f in s.get("layout_warnings") or []]
    for line in warn:
        print(f"layout warning: {line}")
    lw = sum(len(s.get("layout_waived") or []) for s in meta["sheets"])
    if lw:
        print(f"layout: {lw} fault(s) waived in the manifest")
    waived = sum(len(s.get("values_waived") or []) for s in meta["sheets"])
    if waived:
        print(f"values: {waived} difference(s) waived in the manifest")

    # one PDF per SHEET (issue #24). A drawing that runs to two sheets
    # has two entries under one DRG NO., and naming both after the
    # drawing plotted the second over the first: the print set then
    # carried sheet 2 twice and sheet 1 not at all, with the page count
    # still right, so nothing looked wrong until somebody read the pages.
    sheets_of = {}
    for s in meta["sheets"]:
        sheets_of.setdefault(s["drg_no"], []).append(s)
    exported = []
    for sheet in meta["sheets"]:
        num, den = (int(x) for x in sheet["scale"].split(":"))
        pdf = OUTPUT_PDF_DIR / (sheet_pdf_name(sheet, sheets_of) + ".pdf")
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
        extra = []
        lg = sheet.get("logo")
        if lg:
            if not Path(lg["png"]).exists():
                die(f"{sheet['drg_no']}: title block logo missing: {lg['png']}")
            extra.append((lg["png"], *lg["rect"]))
        # a stamp outside the sheet's own window is drawn nowhere and
        # nothing downstream reads it, so the sheet would go out with the
        # logo or the pictorial simply missing (issue #15)
        win = sheet.get("window")
        for png, x, y, w, h in extra + ([stamp] if stamp else []):
            if win and (x < -0.01 or y < -0.01
                        or x + w > win[2] + 0.01 or y + h > win[3] + 0.01):
                die(f"{sheet['drg_no']}: the stamp {Path(png).name} lands "
                    f"at ({x:.1f}, {y:.1f}) {w:.1f}x{h:.1f}, outside the "
                    f"sheet ({win[2]:.1f}x{win[3]:.1f}) — it would print "
                    "nowhere")
        paper = sheet.get("paper", "A4")
        orient = sheet.get("orientation", "portrait")
        info = run_musa_plot(Path(sheet["file"]), pdf,
                             scale=(num, den), window=tuple(sheet["window"]),
                             stamp=stamp, stamps=extra, paper=paper,
                             orientation=orient)
        eff = float(info["mm_per_unit"])
        want = num / den
        if abs(eff - want) > 1e-6:
            die(f"{pdf.name}: effective scale {eff} != stated {want}")
        if info.get("paper") != paper or orient not in info["raw"]:
            die(f"{pdf.name}: not {paper} {orient} ({info['raw']})")
        exported.append({**sheet, "pdf": str(pdf), "paper": paper,
                         "orientation": orient,
                         "mm_per_unit": eff, "plot": info["raw"]})
        print(f"exported {pdf.name}  ({sheet['scale']}, {paper} {orient})")

    # the print set: every sheet once, in the order they were drawn
    merged = OUTPUT_PDF_DIR / "print_set.pdf"
    merge([s["pdf"] for s in exported], merged)
    print(f"print set: {merged}  ({len(exported)} sheets)")
    # and one PDF per DRAWING, holding its sheets in order: that is what
    # goes to the vendor, and a two-sheet drawing is two pages of it
    delivered = []
    for drg, mates in sheets_of.items():
        pdfs = [s["pdf"] for s in exported if s["drg_no"] == drg]
        if len(pdfs) < 2:
            delivered += pdfs
            continue
        whole = OUTPUT_PDF_DIR / f"{drg}.pdf"
        merge(pdfs, whole)
        delivered.append(str(whole))
        print(f"drawing {whole.name}: {len(pdfs)} sheets in one file")
    dest = read_json(OUTPUT_MUSA_DIR / "sheets.json").get("deliver_to")
    if dest:
        d = Path(dest)
        d.mkdir(parents=True, exist_ok=True)
        for pdf in delivered:
            shutil.copy2(pdf, d)
        shutil.copy2(merged, d)
        print(f"delivered {len(delivered)} drawing(s) "
              f"({len(exported)} sheets) + print_set -> {d}")
    write_json(OUTPUT_PDF_DIR / "export.json",
               {"sheets": exported, "print_set": str(merged)})


if __name__ == "__main__":
    main()
