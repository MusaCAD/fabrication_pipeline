# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Pranay Kiran
"""Stage 3 — /finalise: propose and lock the drawing manifest.

    python3 src/manifest.py propose            # defaults from analysis
    python3 src/manifest.py lock <draft.json>  # validate + lock

The manifest records the per-part decisions taken IN SESSION with the user
(front view, section placement, DRG NO., material, scale). `propose` only
suggests defaults; nothing is hardcoded — /finalise edits the draft with the
user before `lock` freezes it to manifests/manifest.json.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import MANIFESTS_DIR, REPORTS_DIR, die, read_json, write_json

VIEW_DIRS = {   # named front-view choices: (view_dir, up) in world coords
    "+X": ([1, 0, 0], [0, 0, 1]), "-X": ([-1, 0, 0], [0, 0, 1]),
    "+Y": ([0, 1, 0], [0, 0, 1]), "-Y": ([0, -1, 0], [0, 0, 1]),
    "+Z": ([0, 0, -1], [0, 1, 0]), "-Z": ([0, 0, 1], [0, 1, 0]),
}
# "+Z" means: viewer above, looking down (plan view), sheet up = world +Y.


def default_front(bbox_size) -> str:
    """Propose viewing along the SMALLEST extent: shows the two largest
    dimensions, i.e. the most information-rich face."""
    axes = ["X", "Y", "Z"]
    smallest = axes[bbox_size.index(min(bbox_size))]
    return "+" + smallest


def propose() -> None:
    src = REPORTS_DIR / "analysis.json"
    if not src.exists():
        die("no reports/analysis.json — run /analyse first")
    judgement = REPORTS_DIR / "judgement.json"
    if not judgement.exists():
        die("no reports/judgement.json — run /judge first")
    a = read_json(src)
    j = read_json(judgement)
    if j["overall"] == "FAIL":
        die("judgement is FAIL — fix models and rerun /analyse + /judge")

    verdicts = {p["file"]: p for p in j["parts"]}
    instances = a["assembly"]["instances"]
    qty: dict[str, int] = {}
    for i in instances:
        if i.get("target_file"):
            qty[i["target_file"]] = qty.get(i["target_file"], 0) + 1

    parts = []
    for idx, part in enumerate(a["parts"], start=1):
        b = part["bodies"][0]
        stem = Path(part["file"]).stem
        v = verdicts.get(part["file"], {})
        parts.append({
            "part_id": f"{idx:02d}",
            "file": part["file"],
            "description": stem.replace("_", " ").upper(),
            "drg_no": f"TBD-{idx:02d}",          # decided with user
            "material": b["material"] if b["material"] not in
                        (None, "Default", "None") else "TBD",
            "qty": qty.get(part["file"], 1),
            "front_view": default_front(b["bbox_size"]),
            "section": {"enabled": True, "projection": "below",
                        "offset_frac": 0.5, "label": "A"},
            "scale": v.get("suggested_scale") or "1:1",
            "extra_notes": [],
            "waivers": [f["message"] for f in v.get("findings", [])
                        if f["severity"] == "WARN"],
        })

    manifest = {
        "locked": False,
        "created": datetime.now().isoformat(timespec="seconds"),
        "model_dir": a["model_dir"],
        "assembly_file": a["assembly"]["file"],
        "project": "TBD",                        # title block DESCRIPTION line
        "drg_no_prefix": "TBD",                  # numbering scheme from user
        "drawn_by": "Er. P. PRANAY",
        "rev": "00",
        "parts": parts,
        "assembly_sheet": {
            "description": "ASSEMBLY - " + Path(a["model_dir"]).name
            .replace("_", " ").upper(),
            "drg_no": "TBD-00",
            "front_view": "+Y",
            "section": {"enabled": False},
            "scale": "1:1",
            "balloons": True,
        },
    }
    out = MANIFESTS_DIR / "manifest_draft.json"
    write_json(out, manifest)
    print(f"draft written: {out}")
    print("Review each part with the user (/finalise), then: "
          "python3 src/manifest.py lock manifests/manifest_draft.json")


REQUIRED_PART_KEYS = {"part_id", "file", "description", "drg_no", "material",
                      "qty", "front_view", "section", "scale"}


def lock(draft_path: Path) -> None:
    m = read_json(draft_path)
    problems = []
    if m.get("drg_no_prefix") in (None, "", "TBD"):
        problems.append("drg_no_prefix undecided")
    if m.get("project") in (None, "", "TBD"):
        problems.append("project undecided")
    for part in m.get("parts", []):
        missing = REQUIRED_PART_KEYS - set(part)
        if missing:
            problems.append(f"{part.get('file')}: missing {sorted(missing)}")
        for key in ("drg_no", "material"):
            if str(part.get(key, "")).startswith("TBD"):
                problems.append(f"{part.get('file')}: {key} undecided")
        if part.get("front_view") not in ("+X", "-X", "+Y", "-Y", "+Z", "-Z"):
            problems.append(f"{part.get('file')}: bad front_view")
        sec = part.get("section", {})
        if sec.get("enabled") and sec.get("projection", "below") not in \
                ("below", "right", "left"):
            problems.append(f"{part.get('file')}: bad section.projection "
                            f"{sec.get('projection')!r}")
        if part.get("aux_view") is not None:
            if sec.get("enabled"):
                problems.append(f"{part.get('file')}: aux_view and section "
                                "are mutually exclusive")
            if part["aux_view"] not in ("+X", "-X", "+Y", "-Y", "+Z", "-Z"):
                problems.append(f"{part.get('file')}: bad aux_view")
        num_den = str(part.get("scale", "")).split(":")
        if len(num_den) != 2 or not all(s.strip().isdigit() for s in num_den):
            problems.append(f"{part.get('file')}: bad scale "
                            f"{part.get('scale')!r} (want N:D)")
    asm = m.get("assembly_sheet", {})
    if str(asm.get("drg_no", "TBD")).startswith("TBD"):
        problems.append("assembly_sheet.drg_no undecided")
    if problems:
        die("cannot lock:\n  - " + "\n  - ".join(problems))
    m["locked"] = True
    m["locked_at"] = datetime.now().isoformat(timespec="seconds")
    out = MANIFESTS_DIR / "manifest.json"
    write_json(out, m)
    print(f"LOCKED -> {out}  ({len(m['parts'])} part sheets + 1 assembly "
          "sheet)")


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in ("propose", "lock"):
        die("usage: manifest.py propose | lock <draft.json>")
    if sys.argv[1] == "propose":
        propose()
    else:
        if len(sys.argv) < 3:
            die("usage: manifest.py lock <draft.json>")
        lock(Path(sys.argv[2]))


if __name__ == "__main__":
    main()
