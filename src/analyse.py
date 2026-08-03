# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Pranay Kiran
"""Stage 1 — /analyse: headless FreeCAD extraction of a model directory.

    python3 src/analyse.py <model_dir> [assembly_file.FCStd]

Writes reports/analysis.json (canonical latest) plus a timestamped copy and
a human-readable reports/analysis.md.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import REPORTS_DIR, die, read_json, run_freecad, write_json

FC_SCRIPT = Path(__file__).resolve().parent / "fc_analyse.py"


def find_assembly(model_dir: Path) -> Path:
    cands = [f for f in model_dir.glob("*.FCStd")
             if f.name.lower().startswith(("assembly", "asm"))]
    if len(cands) != 1:
        die(f"cannot auto-pick assembly in {model_dir} "
            f"(candidates: {[c.name for c in cands]}); pass it explicitly")
    return cands[0]


def to_markdown(a: dict) -> str:
    asm = a["assembly"]
    lines = [f"# Analysis — {Path(asm['file']).name}",
             f"- model dir: `{a['model_dir']}`",
             f"- FreeCAD: {a['freecad_version']}  |  workbench: {asm['workbench']}",
             f"- instances: {len(asm['instances'])}  |  joints: {len(asm['joints'])}",
             "", "## Instances"]
    for i in asm["instances"]:
        tgt = Path(i["target_file"]).name if i.get("target_file") else "BROKEN"
        lines.append(f"- {i['label']} ({i['name']}) -> {tgt}"
                     + (" **BROKEN LINK**" if i.get("broken") else ""))
    lines += ["", "## Joints"]
    for j in asm["joints"]:
        lines.append(f"- {j['label']}: {j['type']} on {', '.join(j['connects'])}")
    lines += ["", "## Parts"]
    for part in a["parts"]:
        lines.append(f"### {Path(part['file']).name}")
        for b in part["bodies"]:
            s = b["bbox_size"]
            lines.append(
                f"- body **{b['label']}**: {s[0]:g} x {s[1]:g} x {s[2]:g} mm, "
                f"{b['solids']} solid(s), vol {b['volume_mm3']:.0f} mm3, "
                f"material: {b['material'] or 'none'}")
        for sk in part["sketches"]:
            flag = "" if sk["fully_constrained"] else "  (NOT fully constrained)"
            lines.append(f"  - sketch {sk['label']}: {sk['constraints']} "
                         f"constraints, {len(sk['dimensional'])} dimensional"
                         + flag)
        for issue in part["issues"]:
            lines.append(f"  - ISSUE: {issue}")
    if asm["issues"]:
        lines += ["", "## Assembly issues"]
        lines += [f"- {i}" for i in asm["issues"]]
    return "\n".join(lines) + "\n"


def main() -> None:
    if len(sys.argv) < 2:
        die("usage: analyse.py <model_dir> [assembly.FCStd]")
    model_dir = Path(sys.argv[1]).resolve()
    if not model_dir.is_dir():
        die(f"not a directory: {model_dir}")
    asm = (model_dir / sys.argv[2]) if len(sys.argv) > 2 \
        else find_assembly(model_dir)

    REPORTS_DIR.mkdir(exist_ok=True)
    out_json = REPORTS_DIR / "analysis.json"
    run_freecad(FC_SCRIPT, str(model_dir), asm.name, str(out_json),
                timeout=600)
    data = read_json(out_json)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    write_json(REPORTS_DIR / f"analysis_{stamp}.json", data)
    md = to_markdown(data)
    (REPORTS_DIR / "analysis.md").write_text(md)
    print(md)
    print(f"written: {out_json} and reports/analysis_{stamp}.json")


if __name__ == "__main__":
    main()
