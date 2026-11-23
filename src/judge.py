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
import fc_tree
from common import (DRAWING_AREA, MANIFESTS_DIR, REPORTS_DIR, S0,
                    STANDARD_SCALES, die, read_json, write_json)

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


def parse_scale(spec) -> tuple[int, int] | None:
    parts = str(spec or "").split(":")
    if len(parts) != 2 or not all(x.strip().isdigit() for x in parts):
        return None
    num, den = (int(x) for x in parts)
    return (num, den) if num and den else None


def thin_feature_finding(bbox_size, scale, source="") -> str | None:
    """The thin-feature warning for a part drawn at `scale`, or None.

    Legibility is a property of the PRINTED sheet, so the scale that
    matters is the one the sheet is drawn at — the manifest's, once
    /finalise has chosen it (issue #4). Judging a 0.5 mm disc at the
    suggested 1:1 while the sheet draws it 2:1 asks the user to waive a
    warning about a drawing that is perfectly legible.
    """
    thin = min(bbox_size)
    printed = thin * scale[0] / scale[1]
    if printed >= MIN_FEATURE_PRINT_MM:
        return None
    where = f" ({source})" if source else ""
    return (f"thinnest extent {thin:g} mm prints at {printed:.2f} mm at "
            f"{scale[0]}:{scale[1]}{where} — check legibility")


def manifest_scales(path: Path) -> dict:
    """part file -> (num, den) from a manifest draft or a locked one."""
    if not path.exists():
        return {}
    out = {}
    for part in read_json(path).get("parts", []):
        sc = parse_scale(part.get("scale"))
        if sc and part.get("file") and part.get("sheet", True):
            out[part["file"]] = sc
    return out


def judge_part(part: dict, generated: str = "", sheet_scale=None) -> dict:
    """`sheet_scale` is the scale the manifest draws this part at, when a
    manifest exists; the suggested scale is used only when it does not."""
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
    used = sheet_scale or sc
    if used is not None:
        msg = thin_feature_finding(
            b["bbox_size"], used,
            "sheet scale" if sheet_scale else "suggested scale")
        if msg:
            flag("WARN", msg)
    for issue in part["issues"]:
        flag("FAIL", issue)
    return {"file": part["file"], "verdict": level, "findings": findings,
            "suggested_scale": sc and f"{sc[0]}:{sc[1]}",
            "scale_used": used and f"{used[0]}:{used[1]}"}


def instance_id(node: dict) -> str:
    """How an instance is named in a finding: its PATH once it lives inside
    a sub-assembly ("SUB1/SPLATE" says where to look), its label at the top
    level, where the label is what the user sees in the tree."""
    path = node.get("path") or node.get("name", "?")
    return path if "/" in path else node.get("label", path)


def assembly_findings(asm: dict) -> list:
    """Model-level findings for the assembly document.

    Everything here walks the whole instance TREE: a sub-assembly's parts
    are instances of this assembly too, and each is joined by the joints of
    the assembly document it actually lives in (issue #3).

    A standalone part pack has no assembly document, so none of these
    checks have anything to say about it.
    """
    out = []
    if not asm.get("file"):
        return out
    nodes = asm.get("instances", [])
    flat = fc_tree.flatten(nodes)
    for i in flat:
        if i.get("broken"):
            out.append({"severity": "FAIL",
                        "message": f"broken link {instance_id(i)}"})
    for issue in asm["issues"]:
        out.append({"severity": "FAIL", "message": issue})
    # a suppressed joint is in the tree but constrains nothing
    live = [j for j in asm["joints"] if not j.get("suppressed")]
    # grounding is per assembly DOCUMENT: the top one, and every
    # sub-assembly, each solves its own file
    if not any(j.get("grounded") for j in live if not j.get("assembly")):
        out.append({"severity": "WARN",
                    "message": "no grounded joint — assembly may "
                               "be under-constrained"})
    for node in flat:
        if node.get("kind") != "assembly" or not node.get("children"):
            continue
        own = [j for j in live if j.get("assembly") == node.get("path")]
        if not any(j.get("grounded") for j in own):
            out.append({"severity": "WARN",
                        "message": f"{instance_id(node)}: no grounded joint "
                                   "in the sub-assembly"})
    # an instance is joined if any live joint, grounded included, names it
    # — or names something INSIDE it: a joint reaching a part of a rigid
    # sub-assembly locates that whole sub-assembly, so demanding a joint on
    # the sub-assembly itself as well would be a false warning. Ancestors
    # only: a joined part says nothing about its unjoined siblings.
    joined = set()
    for j in live:
        for n in j.get("connects", []):
            parts = n.split("/")
            for i in range(len(parts)):
                joined.add("/".join(parts[:i + 1]))
    loose = [instance_id(i) for i in flat
             if not i.get("broken")
             and (i.get("path") or i.get("name")) not in joined]
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
    # the sheet's own scale beats the suggestion: a locked manifest first,
    # then a draft under review at /finalise (issue #4)
    scales = manifest_scales(MANIFESTS_DIR / "manifest.json") \
        or manifest_scales(MANIFESTS_DIR / "manifest_draft.json")
    if scales:
        print(f"judging thin features at the manifest's sheet scales "
              f"({len(scales)} parts)")
    parts = [judge_part(p, generated, scales.get(p["file"])) for p in a["parts"]]
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
