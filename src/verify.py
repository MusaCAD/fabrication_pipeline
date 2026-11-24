# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Pranay Kiran
"""Stage 4b — the value check (issue #14).

What the manifest STATES must be what the sheet PRINTS. The layout engine
only guarantees that what it places does not overlap; the qa-checker reads
the PDF against the template rules. Neither compares the drawing with the
locked statement of what it is supposed to say, so a dropped tolerance or
a model coordinate printed as a dimension reached the vendor while the
report said "every number kept".

The manifest is machine-readable and so is the sheet, so this is a
comparison, not a reading:

  stated   - callout and note text, GD&T tolerances and datum letters,
             thread designations, the variants table, the title block, and
             the VALUE each location dimension must measure;
  printed  - every MTEXT / TEXT / LEADER content in the .musa, plus every
             DIM label as MusaCAD itself composes it (musa_plot
             --dump-dims, so DIMLFAC, precision and zero suppression are
             already applied and a detail view reads as it prints).

Findings are of four kinds:
  missing     a stated value is nowhere on the sheet;
  changed     it is there but different (the same callout, another number);
  extra       the sheet prints a value the manifest does not account for;
  impossible  a dimension bigger than the part it dimensions — which is
              what a model coordinate printed as a dimension looks like.

/draw records them; /export refuses to run on any that is not waived.
"""

from __future__ import annotations

import difflib
import re

TEXT_RECORDS = ("MTEXT", "TEXT", "LEADER")

# What the pipeline itself writes that is not a manifest value: view
# captions, the date, sheet numbering, single letters (datum boxes, section
# and detail letters, variant marks), bare numbers (dimension values,
# balloons, P.NO.), and the table words.
FURNITURE = tuple(re.compile(p) for p in (
    r"^\d{2}-\d{2}-\d{2}$",                 # DD-MM-YY
    r"^SHEET \d+ OF \d+$",
    r"^SECTION [A-Z]-[A-Z]$",
    r"^DETAIL [A-Z] \(\d+:\d+\)$",
    r"^[A-Z]$",
    r"^-?[\d.]+$",
    r"^(\d+\s*,\s*)+\d+$",                  # a P.NO. list in one cell
    r"^\d+ ITEMS$",
    r"^(NOTES:|BEND TABLE|TOTAL|SUB-ASSEMBLY|TYP|REF|NTS|ISOMETRIC VIEW)$",
    r"^\d+%%d REF$",
    r"^%%c[\d.]+$",                         # a bare diameter dim value
    r"^[\d.]+ (kg|g)$",                      # the weight, computed here
))


def unescape(s: str) -> str:
    return s.replace("\\n", "\n").replace("\\\\", "\\")


def norm(s) -> str:
    """One space between words, so a value wrapped across lines still
    matches the single string the manifest states."""
    return " ".join(str(s).split())


def printed_texts(musa_text: str) -> list[str]:
    """Every string the sheet prints, in document order. MTEXT, TEXT and
    LEADER all carry their content on the line after the record and their
    font on the one after that."""
    out = []
    lines = musa_text.splitlines()
    i = 0
    while i < len(lines):
        if lines[i].split(" ", 1)[0] in TEXT_RECORDS and i + 2 < len(lines):
            out.append(unescape(lines[i + 1]))
            i += 3
        else:
            i += 1
    return out


def fmt_value(v: float) -> str:
    """A dimension value as draw.py prints it."""
    return str(round(v)) if abs(v - round(v)) < 0.005 else f"{v:.2f}"


def stated_values(m: dict, part: dict) -> list[dict]:
    """Every value this part's sheet is supposed to carry."""
    out = []

    def add(kind, text, where=""):
        t = norm(text)
        if t and t != "None":
            out.append({"kind": kind, "text": t, "where": where})

    for co in part.get("callouts") or []:
        view = co.get("view", "front")
        for ln in (co.get("lines") or [{"text": co.get("text", "")}]):
            add("callout", ln.get("text", ""), view)
    for note in list(part.get("fab_notes") or []) + \
            list(part.get("extra_notes") or []):
        add("note", note)
    gdt = part.get("gdt") or {}
    for d in gdt.get("datums") or []:
        add("datum", d.get("label"), d.get("view", "front"))
    for f in gdt.get("frames") or []:
        add("tolerance", f.get("tol"), f.get("view", "front"))
        for ref in f.get("refs") or []:
            add("datum reference", ref, f.get("view", "front"))
    for t in part.get("threads") or []:
        add("thread", t.get("designation", ""))
    var = part.get("variants") or {}
    if var:
        add("letter dimension", var.get("letter"))
        add("variants title", var.get("title", "VARIANTS"))
        for col in var.get("columns") or []:
            add("variants column", col)
        for row in var.get("rows") or []:
            for cell in row:
                add("variants value", cell)
    for key in ("description", "drg_no", "material"):
        add("title block", part.get(key), key)
    add("title block", part.get("qty"), "qty")
    if part.get("scale"):
        add("title block", f"SCALE : {part['scale']}", "scale")
    if m.get("drawn_by"):
        add("title block", m["drawn_by"], "drawn_by")
    for sp in part.get("location_dims") or []:
        try:
            a, b = sp["a"], sp["b"]
            val = abs(b[1] - a[1]) if sp.get("orient") == "v" \
                else abs(b[0] - a[0])
        except (KeyError, IndexError, TypeError):
            continue
        add("location dim", fmt_value(val), sp.get("view", "front"))
    return out


def stated_assembly_values(m: dict, asm: dict) -> list[dict]:
    """Every value the assembly sheet is supposed to carry: its own notes
    and title block, and the row each part number brings to the parts
    table. A dropped row is a part the drawing no longer orders."""
    out = []

    def add(kind, text, where=""):
        t = norm(text)
        if t and t != "None":
            out.append({"kind": kind, "text": t, "where": where})

    for note in asm.get("fab_notes") or []:
        add("note", note)
    add("title block", asm.get("description"), "description")
    add("title block", asm.get("drg_no"), "drg_no")
    if asm.get("scale"):
        add("title block", f"SCALE : {asm['scale']}", "scale")
    if m.get("drawn_by"):
        add("title block", m["drawn_by"], "drawn_by")
    for part in m.get("parts") or []:
        for key in ("description", "drg_no"):
            add("parts table", part.get(key), f"P.NO. {part.get('part_id')}")
    return out


def _value_tokens(s: str) -> list[str]:
    return re.findall(r"-?\d+(?:\.\d+)?", s)


def _closest(text: str, printed: list[str]):
    """The printed string most like a stated one, when it is close enough
    to be the same annotation with a different value."""
    best, score = None, 0.0
    for p in printed:
        r = difflib.SequenceMatcher(None, text, p).ratio()
        if r > score:
            best, score = p, r
    return (best, score) if score >= 0.6 else (None, score)


NUMBERED = re.compile(r"^\d+\.\s+")


def _blobs(printed: list[str]) -> tuple[str, str]:
    """The sheet's text as two running strings: in document order, and
    reversed. A value wrapped across lines is still one string in one of
    them — the notes block draws its lines bottom-up, everything else
    top-down — so a claim that survived wrapping matches exactly, without
    giving up on word ORDER the way a bag of words would."""
    return " ".join(printed), " ".join(reversed(printed))


def check_assembly(m: dict, asm: dict, musa_text: str, dims=(), bbox=None,
                   template_texts=()) -> list[dict]:
    """Compare the assembly sheet (or its sheets, concatenated, when the
    table runs onto a continuation) with what the manifest states."""
    return check_sheet(m, {}, musa_text, dims, bbox, template_texts,
                       claims=stated_assembly_values(m, asm))


def check_sheet(m: dict, part: dict, musa_text: str, dims=(), bbox=None,
                template_texts=(), claims=None) -> list[dict]:
    """Compare one sheet with what its manifest states. `claims` overrides
    the part-sheet reading (the assembly sheet states different things)."""
    printed = [norm(t) for t in printed_texts(musa_text)]
    fwd, rev = _blobs(printed)
    labels = [norm(d.get("label", "")) for d in dims]
    values = [v for lab in labels for v in _value_tokens(lab)]
    claims = stated_values(m, part) if claims is None else claims
    findings = []

    for claim in claims:
        text = claim["text"]
        if claim["kind"] == "location dim":
            if any(abs(float(v) - float(text)) < 0.005 for v in values):
                continue
            near, _ = _closest(text, labels)
            findings.append({
                "kind": "changed" if near else "missing",
                "what": f"{claim['kind']} {text}"
                        + (f" ({claim['where']} view)" if claim["where"]
                           else ""),
                "found": near or "",
            })
            continue
        if text in fwd or text in rev:
            continue
        words = set(fwd.split()) | set(rev.split())
        tokens = text.split()
        gone = [t for t in tokens if t not in words]
        what = f"{claim['kind']} {text!r}" \
            + (f" [{claim['where']}]" if claim["where"] else "")
        if gone and len(gone) < len(tokens) * 0.6:
            # most of it is there: name the part that is NOT, which is the
            # dropped tolerance or the changed number
            what += f" — the sheet is missing: {' '.join(gone)}"
            findings.append({"kind": "changed", "what": what,
                             "found": _closest(text, printed)[0] or ""})
        elif gone:
            findings.append({"kind": "missing", "what": what, "found": ""})
        else:
            # every word is on the sheet but not as this value reads: the
            # annotation it belongs to says something else
            findings.append({"kind": "changed", "what": what,
                             "found": _closest(text, printed)[0] or ""})

    # EXTRA — a printed VALUE the manifest does not account for. Text with
    # no digits in it cannot be a wrong value, and the pipeline's own
    # furniture is known, so what is left is a number the drawing states
    # and the manifest does not.
    tpl = {norm(t) for t in template_texts}
    for p in printed:
        bare = NUMBERED.sub("", p)          # the notes' own numbering
        if not bare or not re.search(r"\d", bare):
            continue
        if any(bare in t or t in bare for t in tpl):
            continue
        if any(bare in c["text"] or c["text"] in bare for c in claims):
            continue
        if any(r.match(bare) for r in FURNITURE):
            continue
        findings.append({"kind": "extra", "what": f"printed {bare!r}",
                         "found": ""})

    # IMPOSSIBLE — a dimension longer than the part can be. This is what a
    # model coordinate printed as a dimension looks like: a 906 mm value on
    # a 40 mm disc.
    if bbox:
        longest = max(bbox) * 1.02 + 0.5
        for d in dims:
            val = max((float(v) for v in _value_tokens(str(d.get("label")))),
                      default=0.0)
            if val > longest:
                findings.append({
                    "kind": "impossible",
                    "what": f"dimension {norm(d.get('label'))} is bigger "
                            f"than the part ({max(bbox):g} mm) — a model "
                            "coordinate printed as a dimension?",
                    "found": "",
                })
    return findings


def waived(findings: list[dict], waivers) -> tuple[list, list]:
    """(live, waived): a waiver is an explicit string in the manifest that
    appears in the finding, named and reasoned like every other waiver
    here."""
    live, done = [], []
    for f in findings:
        text = f"{f['kind']} {f['what']} {f.get('found', '')}"
        if any(str(w) and str(w) in text for w in (waivers or [])):
            done.append(f)
        else:
            live.append(f)
    return live, done


def report(findings: list[dict]) -> list[str]:
    """One line per finding, for the /draw log and the /export failure."""
    out = []
    for f in findings:
        line = f"{f['kind'].upper()}: {f['what']}"
        if f.get("found"):
            line += f" — the sheet prints {f['found']!r}"
        out.append(line)
    return out
