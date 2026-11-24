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
from common import (DEFAULT_PAPER, DRAWING_AREA, MANIFESTS_DIR,
                    PAPER_PAINTABLE, REPORTS_DIR, S0, STANDARD_SCALES, die,
                    read_json, write_json)

# Drawing-area size in printed mm (constant for every sheet scale).
AREA_W_MM = (DRAWING_AREA[2] - DRAWING_AREA[0]) * S0
AREA_H_MM = (DRAWING_AREA[3] - DRAWING_AREA[1]) * S0
MIN_FEATURE_PRINT_MM = 1.0   # smallest legible printed feature


def area_mm(paper="A4", orientation="portrait") -> tuple[float, float]:
    """The drawing area in printed mm on this paper. A bigger sheet keeps
    the title block's own size and gives the extra millimetres to the
    drawing, so the area grows by exactly the paper's growth (issue #10).
    """
    if (str(paper).upper(), str(orientation)) == DEFAULT_PAPER:
        return AREA_W_MM, AREA_H_MM
    pw, ph = PAPER_PAINTABLE[(str(paper).upper(), str(orientation))]
    base_w, base_h = PAPER_PAINTABLE[DEFAULT_PAPER]
    return AREA_W_MM + (pw - base_w), AREA_H_MM + (ph - base_h)


def best_scale(w: float, h: float, paper="A4",
               orientation="portrait") -> tuple[int, int] | None:
    """Largest standard scale at which a w x h (mm) view pair fits the
    drawing area with margins (two stacked views, per drawing style)."""
    area_w, area_h = area_mm(paper, orientation)
    for num, den in STANDARD_SCALES:
        f = num / den
        if w * f <= area_w * 0.85 and 2.2 * h * f <= area_h * 0.80:
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


def sheet_scales(parts: list) -> dict:
    """part file -> (num, den) of the sheet that part is DRAWN on.

    A part with `sheet: false` has no sheet of its own. It is either drawn
    on a sibling's sheet — one of a kit of discs shown on the kit's sheet,
    carrying that sheet's DRG NO. — in which case it is judged at THAT
    sheet's scale, or it is bought out and nothing is drawn for it at all,
    in which case there is no scale to judge it at and it is left out
    (issue #8). manifest.py's lock resolves it the same way, so /judge and
    the lock never disagree about a part's scale.
    """
    own = {}
    for part in parts:
        sc = parse_scale(part.get("scale"))
        if sc and part.get("file") and part.get("sheet", True):
            own[part["file"]] = sc
    by_drg = {}
    for part in parts:
        if part.get("sheet", True) and part.get("drg_no"):
            by_drg.setdefault(str(part["drg_no"]),
                              parse_scale(part.get("scale")))
    out = dict(own)
    for part in parts:
        if part.get("sheet", True) or not part.get("file"):
            continue
        host = by_drg.get(str(part.get("drg_no") or ""))
        if host:
            out[part["file"]] = host
    return out


def sheet_papers(parts: list) -> dict:
    """part file -> (paper, orientation) of the sheet it is drawn on, for
    the parts whose manifest entry asks for one that is not the default
    (issue #10). A part drawn on a sibling's sheet is judged on THAT
    sheet's paper, exactly as it is judged at its scale."""
    def paper_of(part):
        p = part.get("paper")
        if not p:
            return None
        if isinstance(p, dict):
            return (str(p.get("size", "A4")).upper(),
                    str(p.get("orientation", "portrait")))
        s = str(p).upper().replace(" ", "")
        if s.endswith(("P", "L")) and s[:-1] in ("A4", "A3", "A2"):
            return (s[:-1], "landscape" if s.endswith("L") else "portrait")
        return (s, "portrait")

    out, by_drg = {}, {}
    for part in parts:
        pp = paper_of(part)
        if part.get("sheet", True) and part.get("drg_no"):
            by_drg.setdefault(str(part["drg_no"]), pp)
        if pp and part.get("file") and part.get("sheet", True):
            out[part["file"]] = pp
    for part in parts:
        if part.get("sheet", True) or not part.get("file"):
            continue
        host = by_drg.get(str(part.get("drg_no") or ""))
        if host:
            out[part["file"]] = host
    return out


def not_drawn(parts: list) -> set:
    """The part files a manifest draws NOWHERE: `sheet: false`, and no
    other part's sheet carries their DRG NO. (issue #8)."""
    have = sheet_scales(parts)
    return {p["file"] for p in parts
            if p.get("file") and not p.get("sheet", True)
            and p["file"] not in have}


def manifest_parts(path: Path) -> list:
    """The parts of a manifest draft or a locked one ([] if there is none)."""
    return read_json(path).get("parts", []) if path.exists() else []


def manifest_scales(path: Path) -> dict:
    """part file -> (num, den) from a manifest draft or a locked one."""
    return sheet_scales(manifest_parts(path))


def judge_part(part: dict, generated: str = "", sheet_scale=None,
               drawn: bool = True, paper=DEFAULT_PAPER) -> dict:
    """`sheet_scale` is the scale the manifest draws this part at, when a
    manifest exists; the suggested scale is used only when it does not.
    `drawn` is False for a part the manifest draws NOWHERE (a bought-out
    item): legibility is a property of a drawing, and a part that has none
    gets no thin-feature warning, since no scale could ever clear it
    (issue #8)."""
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
    sc = best_scale(max(w, h, d), sorted((w, h, d))[1], *paper)
    if sc is None:
        flag("FAIL", f"part {w:g}x{h:g}x{d:g} mm fits no standard scale "
                     f"({', '.join(f'{n}:{dn}' for n, dn in STANDARD_SCALES)})"
                     f" on {paper[0]} {paper[1]} — needs a bigger sheet or a "
                     "sheet split")
    used = sheet_scale or (sc if drawn else None)
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


def visibility_findings(analysis: dict) -> list:
    """What each file SHOWS when it is opened (issue #23).

    A model built by script can be saved with everything hidden: the user
    then has to expand each body to its last feature and unhide it before
    they can see the part at all, and a drawing run gives no hint that
    anything is wrong — the geometry is there, it is only invisible. A
    file that opens with nothing visible is a FAIL; one with something
    hidden that should not be is a WARN, since the rest of it is there to
    look at.
    """
    out = []
    for v in analysis.get("visibility") or []:
        where = Path(v.get("file") or "").name or v.get("document") or "?"
        kind = v.get("kind", "file")
        if v.get("nothing_visible"):
            what = "; ".join(v.get("hidden") or []) or (
                "every body in it is hidden" if kind == "part"
                else "every link in it is hidden")
            out.append({"severity": "FAIL",
                        "message": f"{where} opens with nothing visible — "
                                   f"{what}. Unhide it (select in the tree, "
                                   "Space) and save; a model built by "
                                   "script sets Visibility on the body and "
                                   "its tip feature before it saves"})
        elif v.get("hidden"):
            out.append({"severity": "WARN",
                        "message": f"{where} ({kind}): "
                                   + "; ".join(v["hidden"])})
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
    mparts = manifest_parts(MANIFESTS_DIR / "manifest.json") \
        or manifest_parts(MANIFESTS_DIR / "manifest_draft.json")
    scales = sheet_scales(mparts)
    undrawn = not_drawn(mparts)
    if scales:
        print(f"judging thin features at the manifest's sheet scales "
              f"({len(scales)} parts"
              + (f", {len(undrawn)} drawn on no sheet" if undrawn else "")
              + ")")
    papers = sheet_papers(mparts)
    parts = [judge_part(p, generated, scales.get(p["file"]),
                        drawn=p["file"] not in undrawn,
                        paper=papers.get(p["file"], DEFAULT_PAPER))
             for p in a["parts"]]
    asm_findings = assembly_findings(asm) + visibility_findings(a)
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
