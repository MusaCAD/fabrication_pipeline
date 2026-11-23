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
import judge
from common import (MANIFESTS_DIR, REPORTS_DIR, STANDARD_SCALES, die,
                    read_json, write_json)
import musa_writer as mw

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
    # how many of each real part the assembly uses, counted through every
    # sub-assembly (fc_analyse's part_usage); older analyses, written before
    # sub-assemblies were followed, only have top-level instances
    usage = a["assembly"].get("part_usage")
    if usage:
        qty: dict[str, int] = {f: u["qty"] for f, u in usage.items()}
    else:
        qty = {}
        for i in a["assembly"]["instances"]:
            if i.get("target_file"):
                qty[i["target_file"]] = qty.get(i["target_file"], 0) + 1

    parts = []
    for idx, part in enumerate(a["parts"], start=1):
        b = part["bodies"][0]
        stem = Path(part["file"]).stem
        v = verdicts.get(part["file"], {})
        entry = {
            "part_id": f"{idx:02d}",
            "file": part["file"],
            "description": stem.replace("_", " ").upper(),
            "drg_no": f"TBD-{idx:02d}",          # decided with user
            "material": b["material"] if b["material"] not in
                        (None, "Default", "None") else "TBD",
            "qty": qty.get(part["file"], 1),
            "sheet": True,           # false = bought-out: parts table only
            "front_view": default_front(b["bbox_size"]),
            "section": {"enabled": True, "projection": "below",
                        "offset_frac": 0.5, "label": "A"},
            "scale": v.get("suggested_scale") or "1:1",
            "extra_notes": [],
            "waivers": [f["message"] for f in v.get("findings", [])
                        if f["severity"] == "WARN"],
        }
        flat = part.get("flat")
        if flat:
            # sheet metal: the shop cuts a blank and bends it, so the second
            # view is the developed blank. A section through 2 mm plate shows
            # nothing, so it is off by default.
            entry["section"] = {"enabled": False}
            entry["flat_pattern"] = {
                "enabled": True,
                "object": flat["object"],
                "view": default_front(flat["bbox_size"]),
                "thickness_mm": flat["thickness_mm"],
                "blank_mm": flat["blank_mm"],
                "bends": part.get("bends", []),
            }
        parts.append(entry)

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

DATUM_EDGE_TARGETS = ("bottom_face", "short_edge", "long_edge")


def parse_scale(spec) -> tuple[int, int] | None:
    parts = str(spec).split(":")
    if len(parts) != 2 or not all(x.strip().isdigit() for x in parts):
        return None
    num, den = (int(x) for x in parts)
    return (num, den) if num and den else None


def datum_target_problem(target) -> str | None:
    """Datum targets: a view edge, or `<kind>_r<radius>` for a feature of
    size (a bore or a spigot, whose datum is its axis)."""
    t = str(target or "")
    if t in DATUM_EDGE_TARGETS:
        return None
    if "_r" in t:
        try:
            if float(t.rsplit("_r", 1)[1]) > 0:
                return None
        except ValueError:
            pass
        return f"bad datum target {t!r} (want <kind>_r<radius>, e.g. bore_r12.5)"
    return (f"bad datum target {t!r} (want one of "
            f"{', '.join(DATUM_EDGE_TARGETS)} or <kind>_r<radius>)")


def frame_target_problem(target) -> str | None:
    """Frame targets: a feature of size (`<kind>_r<radius>`), datum A's own
    face, or a plane face seen EDGE-ON in the view (issue #6)."""
    # draw.py is imported here, not at the top: it imports this module for
    # VIEW_DIRS, and by the time any lock runs, this one is fully loaded
    import draw
    t = str(target or "")
    if t == "datum_A_face" or draw.face_target(t) is not None:
        return None
    if "_r" in t:
        try:
            if float(t.rsplit("_r", 1)[1]) > 0:
                return None
        except ValueError:
            pass
    return (f"bad frame target {t!r} (want <kind>_r<radius>, datum_A_face, "
            f"face_x<coord> / face_y<coord>, or "
            f"{', '.join(sorted(draw.FACE_EDGES))})")


def gdt_problems(part: dict, who: str) -> list[str]:
    """The sheet's GD&T: every target has a form the drawing can place, and
    every frame's datum letters are datums this sheet actually carries — a
    frame referring to a datum that is not on the sheet is unmeasurable."""
    g = part.get("gdt") or {}
    out = []
    letters = set()
    for d in g.get("datums", []):
        bad = datum_target_problem(d.get("target"))
        if bad:
            out.append(f"{who}: {bad}")
        letters.add(str(d.get("label", "")).upper())
    for f in g.get("frames", []):
        bad = frame_target_problem(f.get("target"))
        if bad:
            out.append(f"{who}: {bad}")
        for ref in f.get("refs", []) or []:
            if str(ref).upper() not in letters:
                out.append(f"{who}: frame {f.get('symbol', '?')} "
                           f"{f.get('tol', '')} refers to datum {ref}, which "
                           "this sheet does not carry — add the datum or "
                           "drop the reference")
    return out


def one_detail_problems(spec: dict, who: str, sheet_scale, have: set,
                        taken: set) -> list[str]:
    """One detail view: the parent view must be on the sheet, the scale must
    be a standard one that really enlarges, and the label must be a free
    single letter (a sheet with DETAIL B and SECTION B-B says nothing)."""
    out = []
    of = spec.get("of", "front")
    if of not in have:
        out.append(f"{who}: detail_view.of {of!r} is not a view on this "
                   f"sheet (has {', '.join(sorted(have))})")
    det = parse_scale(spec.get("scale"))
    if det is None:
        out.append(f"{who}: bad detail_view.scale {spec.get('scale')!r} "
                   "(want N:D)")
    elif det not in STANDARD_SCALES:
        out.append(f"{who}: detail_view.scale {spec['scale']} is not a "
                   "standard scale")
    elif sheet_scale and det[0] / det[1] <= sheet_scale[0] / sheet_scale[1]:
        out.append(f"{who}: detail_view.scale {spec['scale']} does not "
                   f"enlarge the sheet's {sheet_scale[0]}:{sheet_scale[1]} — "
                   "a detail view exists to show a region BIGGER")
    label = str(spec.get("label", "B"))
    if len(label) != 1 or not label.isalpha():
        out.append(f"{who}: detail_view.label {label!r} must be one letter")
    elif label.upper() in taken:
        out.append(f"{who}: detail_view.label {label!r} is already used on "
                   "this sheet (a section or another detail)")
    else:
        taken.add(label.upper())
    cen = spec.get("center")
    if not (isinstance(cen, (list, tuple)) and len(cen) == 2
            and all(isinstance(v, (int, float)) for v in cen)):
        out.append(f"{who}: detail_view.center must be [x, y] in the "
                   "parent view's coordinates")
    try:
        if float(spec.get("radius", 0)) <= 0:
            raise ValueError
    except (TypeError, ValueError):
        out.append(f"{who}: detail_view.radius must be > 0")
    return out


def detail_problems(part: dict) -> list[str]:
    """An enlarged detail view on a PART sheet."""
    spec = part.get("detail_view") or {}
    if not spec or not spec.get("enabled", True):
        return []
    sec = part.get("section", {})
    have = {"front"}
    if sec.get("enabled"):
        have.add("section")
    if part.get("aux_view"):
        have.add("aux")
    if (part.get("flat_pattern") or {}).get("enabled"):
        have.add("flat")
    taken = {str(sec.get("label", "A")).upper()} if sec.get("enabled") \
        else set()
    return one_detail_problems(spec, part.get("file"),
                               parse_scale(part.get("scale")), have, taken)


def assembly_sheet_problems(m: dict) -> list[str]:
    """The assembly sheet's own decisions (issue #5): its section, its
    detail views, where each balloon goes and how the table is grouped —
    checked at the lock exactly as a part sheet's are."""
    asm = m.get("assembly_sheet") or {}
    if not asm or not asm.get("enabled", True):
        return []
    who = "assembly_sheet"
    out = []
    scale = parse_scale(asm.get("scale"))
    if scale is None:
        out.append(f"{who}: bad scale {asm.get('scale')!r} (want N:D)")
    iso = asm.get("view") == "iso"
    if not iso and asm.get("front_view") not in VIEW_DIRS:
        out.append(f"{who}: bad front_view {asm.get('front_view')!r}")
    ids = {str(p["part_id"]) for p in m.get("parts", [])}
    have = {"front"}
    sec = asm.get("section") or {}
    if sec.get("enabled"):
        if iso:
            out.append(f"{who}: a section needs an orthographic front view, "
                       "not the isometric one")
        if sec.get("projection", "below") not in ("below", "right", "left"):
            out.append(f"{who}: bad section.projection "
                       f"{sec.get('projection')!r}")
        unknown = [i for i in sec.get("not_sectioned", [])
                   if str(i) not in ids]
        if unknown:
            out.append(f"{who}: section.not_sectioned names no such part: "
                       f"{', '.join(map(str, unknown))}")
        have.add("section")
    taken = {str(sec.get("label", "A")).upper()} if sec.get("enabled") \
        else set()
    labels = []
    for spec in asm.get("detail_views") or []:
        out += one_detail_problems(spec, who, scale, have, taken)
        label = str(spec.get("label", "B"))
        labels.append(f"detail:{label}")
        unknown = [i for i in spec.get("balloons") or [] if str(i) not in ids]
        if unknown:
            out.append(f"{who}: detail {label} balloons name no such part: "
                       f"{', '.join(map(str, unknown))}")
    for pid, view in (asm.get("balloon_in") or {}).items():
        if str(pid) not in ids:
            out.append(f"{who}: balloon_in names no such part: {pid}")
        if view not in set(have) | set(labels):
            out.append(f"{who}: balloon_in[{pid}] = {view!r} is not a view "
                       f"on this sheet ({', '.join(sorted(set(have) | set(labels)))})")
    table = asm.get("parts_table") or {}
    if table.get("group_by", "none") not in ("none", "sub_assembly"):
        out.append(f"{who}: parts_table.group_by "
                   f"{table.get('group_by')!r} (want none or sub_assembly)")
    return out


def thin_feature_problems(m: dict) -> list[str]:
    """Re-run the thin-feature check at the scale the manifest actually
    draws each part at (issue #4).

    /judge reported it with whatever scale was known then; the lock is the
    moment the choice becomes final, so it is checked again here. A part
    still too thin at its chosen scale does not lock unless the user has
    waived it — and one whose scale was raised enough needs no waiver at
    all, because there is nothing left to warn about.
    """
    src = REPORTS_DIR / "analysis.json"
    if not src.exists():
        return []
    bodies = {p["file"]: p["bodies"][0] for p in read_json(src).get("parts", [])
              if p.get("bodies")}
    out = []
    for part in m.get("parts", []):
        if not part.get("sheet", True):
            continue
        body = bodies.get(part.get("file"))
        sc = judge.parse_scale(part.get("scale"))
        if body is None or sc is None:
            continue
        msg = judge.thin_feature_finding(body["bbox_size"], sc, "sheet scale")
        if msg and not any("thinnest extent" in str(w)
                           for w in part.get("waivers", [])):
            out.append(f"{part.get('file')}: {msg}; raise the scale or "
                       "record a waiver")
    return out


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
        if not part.get("sheet", True):
            # bought-out: a parts-table row and a balloon, never a sheet, so
            # the view/section/scale decisions simply do not apply.
            continue
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
        fp = part.get("flat_pattern") or {}
        if fp.get("enabled"):
            if sec.get("enabled") or part.get("aux_view"):
                problems.append(f"{part.get('file')}: flat_pattern excludes "
                                "section and aux_view (two views per sheet)")
            if not fp.get("object"):
                problems.append(f"{part.get('file')}: flat_pattern.object "
                                "undecided")
            if fp.get("view") not in ("+X", "-X", "+Y", "-Y", "+Z", "-Z"):
                problems.append(f"{part.get('file')}: bad flat_pattern.view")
            if not fp.get("bends"):
                problems.append(f"{part.get('file')}: flat_pattern has no "
                                "bends — a blank with no bend table is just "
                                "a flat part; use a plain two-view sheet")
        num_den = str(part.get("scale", "")).split(":")
        if len(num_den) != 2 or not all(s.strip().isdigit() for s in num_den):
            problems.append(f"{part.get('file')}: bad scale "
                            f"{part.get('scale')!r} (want N:D)")
        problems += detail_problems(part)
        problems += gdt_problems(part, str(part.get("file")))
    problems += thin_feature_problems(m)
    problems += assembly_sheet_problems(m)
    if m.get("template"):
        problems += mw.template_problems(m["template"])
    asm = m.get("assembly_sheet") or {}
    if asm and asm.get("enabled", True) and \
            str(asm.get("drg_no", "TBD")).startswith("TBD"):
        problems.append("assembly_sheet.drg_no undecided")
    if problems:
        die("cannot lock:\n  - " + "\n  - ".join(problems))
    m["locked"] = True
    m["locked_at"] = datetime.now().isoformat(timespec="seconds")
    out = MANIFESTS_DIR / "manifest.json"
    write_json(out, m)
    drawn = [p for p in m["parts"] if p.get("sheet", True)]
    bought = len(m["parts"]) - len(drawn)
    print(f"LOCKED -> {out}  ({len(drawn)} part sheets + 1 assembly sheet"
          + (f"; {bought} bought-out, parts-table only)" if bought else ")"))


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
