# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Pranay Kiran
"""Stage 2 — /judge: review the latest analysis for fabrication readiness.

    python3 src/judge.py

Reads reports/analysis.json, writes reports/judgement.md (+ .json verdict).
REPORT-ONLY: model fixes are done by the user in FreeCAD, never here.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (DRAWING_AREA, REPORTS_DIR, S0, STANDARD_SCALES, die,
                    read_json, write_json)

# Drawing-area size in printed mm (constant for every sheet scale).
AREA_W_MM = (DRAWING_AREA[2] - DRAWING_AREA[0]) * S0
AREA_H_MM = (DRAWING_AREA[3] - DRAWING_AREA[1]) * S0
MIN_FEATURE_PRINT_MM = 1.0   # smallest legible printed feature


def best_scale(w: float, h: float) -> tuple[int, int] | None:
    """Largest standard scale at which a w x h (mm) view pair fits the
    drawing area with margins (two stacked views, per drawing style)."""
    for num, den in STANDARD_SCALES:
        f = num / den
        if w * f <= AREA_W_MM * 0.85 and 2.2 * h * f <= AREA_H_MM * 0.80:
            return (num, den)
    return None


def judge_part(part: dict, generated: str = "") -> dict:
    findings, level = [], "PASS"

    def flag(sev: str, msg: str):
        nonlocal level
        findings.append({"severity": sev, "message": msg})
        order = {"PASS": 0, "WARN": 1, "FAIL": 2}
        if order[sev] > order[level]:
            level = sev

    bodies = part["bodies"]
    if not bodies:
        flag("FAIL", "no solid body — nothing to draw")
        return {"file": part["file"], "verdict": "FAIL", "findings": findings}
    if len(bodies) > 1:
        flag("WARN", f"{len(bodies)} bodies; each drawing sheet assumes one")
    b = bodies[0]
    if b["solids"] > 1:
        flag("WARN", f"body has {b['solids']} solids (disjoint geometry?)")
    if not b["material"] or b["material"] in ("Default", "None"):
        flag("WARN", "material undefined (FreeCAD 'Default') — title block "
                     "MATERIAL will need a value at /finalise")
    dims_total = sum(len(s["dimensional"]) for s in part["sketches"])
    if dims_total == 0:
        if generated:
            # A script-built model holds its design intent in the generator,
            # not in Sketcher: every coordinate comes from a named parameter,
            # and editing a sketch by hand is not how the part changes. The
            # check still runs and still reports, because a drawing taken
            # from a generated model is only as good as that generator.
            flag("WARN", "no dimensional sketch constraints — declared "
                         f"script-generated ({generated}), so the design "
                         "dimensions live in the generator, not the sketches")
        else:
            flag("FAIL", "no dimensional sketch constraints — the model "
                         "carries no design dimensions at all")
    for s in part["sketches"]:
        if not s["fully_constrained"] and not generated:
            flag("WARN", f"sketch {s['label']} not fully constrained "
                         "(geometry may drift on edit)")
        if not s["solver_ok"]:
            flag("FAIL", f"sketch {s['label']} does not solve")
    w, h, d = b["bbox_size"]
    dims_sorted = sorted((w, h, d))
    sc = best_scale(max(w, h, d), sorted((w, h, d))[1])
    if sc is None:
        flag("FAIL", f"part {w:g}x{h:g}x{d:g} mm fits no standard scale "
                     f"({', '.join(f'{n}:{dn}' for n, dn in STANDARD_SCALES)})"
                     " — needs sheet split")
    else:
        f = sc[0] / sc[1]
        if dims_sorted[0] * f < MIN_FEATURE_PRINT_MM:
            flag("WARN", f"thinnest extent {dims_sorted[0]:g} mm prints at "
                         f"{dims_sorted[0] * f:.2f} mm at {sc[0]}:{sc[1]} — "
                         "check legibility")
    for issue in part["issues"]:
        flag("FAIL", issue)
    return {"file": part["file"], "verdict": level, "findings": findings,
            "suggested_scale": sc and f"{sc[0]}:{sc[1]}"}


def assembly_findings(asm: dict) -> list:
    """Model-level findings for the assembly document.

    A standalone part pack has no assembly document, so none of these
    checks have anything to say about it.
    """
    out = []
    if not asm.get("file"):
        return out
    for i in asm["instances"]:
        if i.get("broken"):
            out.append({"severity": "FAIL",
                        "message": f"broken link {i['label']}"})
    for issue in asm["issues"]:
        out.append({"severity": "FAIL", "message": issue})
    # a suppressed joint is in the tree but constrains nothing
    live = [j for j in asm["joints"] if not j.get("suppressed")]
    if not any(j.get("grounded") for j in live):
        out.append({"severity": "WARN",
                    "message": "no grounded joint — assembly may "
                               "be under-constrained"})
    # an instance is joined if any live joint, grounded included, names it
    joined = {n for j in live for n in j.get("connects", [])}
    loose = [i["label"] for i in asm["instances"]
             if not i.get("broken") and i["name"] not in joined]
    if loose:
        out.append({"severity": "WARN",
                    "message": f"instances with no joint: "
                               f"{', '.join(loose)}"})
    return out


def main() -> None:
    # --generated <source>: these models are built by a script, named here so
    # the report says which one. It changes the sketch-constraint findings
    # and nothing else; every other finding keeps its severity.
    generated = ""
    if "--generated" in sys.argv:
        i = sys.argv.index("--generated")
        generated = sys.argv[i + 1] if len(sys.argv) > i + 1 else "generator"
    src = REPORTS_DIR / "analysis.json"
    if not src.exists():
        die("no reports/analysis.json — run /analyse first")
    a = read_json(src)
    asm = a["assembly"]
    parts = [judge_part(p, generated) for p in a["parts"]]
    asm_findings = assembly_findings(asm)
    overall = "PASS"
    for v in [p["verdict"] for p in parts] + \
             [f["severity"] for f in asm_findings]:
        if v == "FAIL":
            overall = "FAIL"
            break
        if v == "WARN":
            overall = "WARN"

    verdict = {"overall": overall, "assembly_findings": asm_findings,
               "parts": parts, "generated": generated}
    write_json(REPORTS_DIR / "judgement.json", verdict)

    lines = [f"# Judgement — overall: **{overall}**", ""]
    if generated:
        lines += [f"> Models declared script-generated: `{generated}`. "
                  "Sketch-constraint findings are reported as WARN because "
                  "the design dimensions live in that generator.", ""]
    if asm_findings:
        lines.append("## Assembly")
        lines += [f"- [{f['severity']}] {f['message']}" for f in asm_findings]
        lines.append("")
    for p in parts:
        lines.append(f"## {Path(p['file']).name} — {p['verdict']}"
                     + (f"  (suggested scale {p['suggested_scale']})"
                        if p.get("suggested_scale") else ""))
        if not p["findings"]:
            lines.append("- clean")
        lines += [f"- [{f['severity']}] {f['message']}" for f in p["findings"]]
        lines.append("")
    lines.append("_Judge is report-only: fix models in FreeCAD, then rerun "
                 "/analyse. FAIL blocks /finalise; WARN needs an explicit "
                 "waiver at /finalise._")
    md = "\n".join(lines) + "\n"
    (REPORTS_DIR / "judgement.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
