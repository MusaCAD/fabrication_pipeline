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


def selector_problem(target) -> str | None:
    """A feature-of-size target may name WHICH feature of that size it
    means (issue #7): `@<callout id>`, `@x<coord>` or `@y<coord>`. Only the
    form is checked here; whether it names anything is gdt_problems' job."""
    t = str(target or "")
    if "@" not in t:
        return None
    sel = t.partition("@")[2].strip()
    if not sel:
        return (f"empty selector in {t!r} (want @<callout id>, @x<coord> or "
                "@y<coord>)")
    if sel[:1].lower() in ("x", "y") and len(sel) > 1:
        try:
            float(sel[1:])
        except ValueError:
            return (f"bad coordinate selector @{sel} in {t!r} — @x/@y take a "
                    "number in the view's own axes")
    return None


def datum_target_problem(target) -> str | None:
    """Datum targets: a view edge, or `<kind>_r<radius>` for a feature of
    size (a bore or a spigot, whose datum is its axis), optionally with a
    selector naming which feature of that size (`bore_r35.75@tail`)."""
    t = str(target or "")
    bad = selector_problem(t)
    if bad:
        return f"bad datum target: {bad}"
    base = t.partition("@")[0]
    if base in DATUM_EDGE_TARGETS:
        return None
    if "_r" in base:
        try:
            if float(base.rsplit("_r", 1)[1]) > 0:
                return None
        except ValueError:
            pass
        return f"bad datum target {t!r} (want <kind>_r<radius>, e.g. bore_r12.5)"
    return (f"bad datum target {t!r} (want one of "
            f"{', '.join(DATUM_EDGE_TARGETS)} or <kind>_r<radius>)")


def frame_target_problem(target) -> str | None:
    """Frame targets: a feature of size (`<kind>_r<radius>`, with an
    optional selector), datum A's own face, or a plane face seen EDGE-ON in
    the view (issue #6)."""
    # draw.py is imported here, not at the top: it imports this module for
    # VIEW_DIRS, and by the time any lock runs, this one is fully loaded
    import draw
    t = str(target or "")
    bad = selector_problem(t)
    if bad:
        return f"bad frame target: {bad}"
    base = t.partition("@")[0]
    if base == "datum_A_face" or draw.face_target(base) is not None:
        return None
    if "_r" in base:
        try:
            if float(base.rsplit("_r", 1)[1]) > 0:
                return None
        except ValueError:
            pass
    return (f"bad frame target {t!r} (want <kind>_r<radius>, datum_A_face, "
            f"face_x<coord> / face_y<coord>, or "
            f"{', '.join(sorted(draw.FACE_EDGES))})")


def size_annotations(part: dict, view: str, rad: float) -> list[dict]:
    """The callouts in `view` that carry the size of a feature of radius
    `rad` (true millimetres) — what a feature-of-size target attaches to.
    A detail view draws its geometry k times size, so its circle_r is k
    times the true radius; the comparison follows it."""
    import draw
    k = 1.0
    if view == "detail":
        spec = part.get("detail_view") or {}
        if spec:
            k = draw.detail_factor(part, spec)
    return [co for co in part.get("callouts") or []
            if co.get("view", "front") == view and "circle_r" in co
            and abs(float(co["circle_r"]) - rad * k) < 0.0015]


def ambiguity_problem(part: dict, item: dict, kind: str) -> str | None:
    """Does this datum/frame target name exactly ONE size annotation?

    Two features of the same size in one view (a tube's two end bores) are
    told apart by the target's selector; without one the drawing would
    attach to whichever callout came last, which is a sheet that says
    something the designer did not (issue #7)."""
    import draw
    rad, sel = draw._target_parts(item.get("target"))
    if rad is None:
        return None
    view = item.get("view", "front")
    cos = size_annotations(part, view, rad)
    tgt = item.get("target")
    if sel is None:
        if len(cos) > 1:
            named = [co.get("id") or f"at {co.get('at')}" for co in cos]
            return (f"{kind} target {tgt!r} fits {len(cos)} callouts in view "
                    f"{view!r} ({', '.join(map(str, named))}) — say which "
                    f"with @<callout id> or @x<coord>/@y<coord>")
        return None
    hit = [co for co in cos
           if draw._anchor_matches(
               draw.feature_anchor(view, co["circle_r"], (0, 0, 0, 0),
                                   cid=co.get("id"),
                                   at=co.get("at")), sel)]
    if not hit:
        if not cos:
            return (f"{kind} target {tgt!r}: view {view!r} has no callout of "
                    f"that size for @{sel} to name")
        return (f"{kind} target {tgt!r}: no callout in view {view!r} matches "
                f"@{sel} — give the one you mean an \"id\", or use "
                f"@x<coord>/@y<coord>")
    if len(hit) > 1:
        return (f"{kind} target {tgt!r}: @{sel} matches {len(hit)} callouts "
                f"in view {view!r} — ids must be unique per view")
    return None


def gdt_problems(part: dict, who: str) -> list[str]:
    """The sheet's GD&T: every target has a form the drawing can place and
    names exactly one feature, and every frame's datum letters are datums
    this sheet actually carries — a frame referring to a datum that is not
    on the sheet is unmeasurable."""
    g = part.get("gdt") or {}
    out = []
    letters = set()
    for d in g.get("datums", []):
        bad = datum_target_problem(d.get("target"))
        if bad:
            out.append(f"{who}: {bad}")
        else:
            bad = ambiguity_problem(part, d, "datum")
            if bad:
                out.append(f"{who}: {bad}")
        letters.add(str(d.get("label", "")).upper())
    for f in g.get("frames", []):
        bad = frame_target_problem(f.get("target"))
        if bad:
            out.append(f"{who}: {bad}")
        else:
            bad = ambiguity_problem(part, f, "frame")
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


def variants_problems(part: dict, who: str) -> list[str]:
    """A tabulated drawing the sheet can draw (issue #11): one letter, one
    dimension carrying it, a column for it in the table, and every row
    filled. A letter nobody can look up, or a row missing its value, is a
    sheet the vendor cannot build from."""
    spec = part.get("variants") or {}
    if not spec:
        return []
    out = []
    letter = str(spec.get("letter", ""))
    if len(letter) != 1 or not letter.isalpha():
        out.append(f"{who}: variants.letter {letter!r} must be one letter")
    dim = spec.get("dim")
    if not isinstance(dim, dict):
        out.append(f"{who}: variants needs a dim {{view, orient, a, b}} — "
                   "the dimension the letter stands on")
    else:
        for key in ("a", "b"):
            pt = dim.get(key)
            if not (isinstance(pt, (list, tuple)) and len(pt) == 2
                    and all(isinstance(v, (int, float)) for v in pt)):
                out.append(f"{who}: variants.dim.{key} must be [x, y] in "
                           "the view's own coordinates")
        if dim.get("orient", "h") not in ("h", "v"):
            out.append(f"{who}: variants.dim.orient "
                       f"{dim.get('orient')!r} (want h or v)")
        if dim.get("side") is not None and \
                dim["side"] not in ("top", "bottom", "left", "right"):
            out.append(f"{who}: variants.dim.side {dim['side']!r}")
    cols = [str(c) for c in spec.get("columns") or []]
    rows = spec.get("rows") or []
    if not cols:
        out.append(f"{who}: variants.columns is empty — the table has to "
                   f"say what its values are (e.g. MARK, {letter or 'T'}, "
                   "QTY)")
    elif letter and letter not in cols:
        out.append(f"{who}: the table has no {letter!r} column, so nothing "
                   f"gives {letter!r} a value (columns: {', '.join(cols)})")
    if not rows:
        out.append(f"{who}: variants.rows is empty — a tabulated drawing "
                   "with no variants is a plain sheet")
    for i, row in enumerate(rows, start=1):
        if not isinstance(row, (list, tuple)) or len(row) != len(cols):
            out.append(f"{who}: variants row {i} has {len(row)} values for "
                       f"{len(cols)} columns")
    return out


def thread_problems(part: dict, who: str, bbox=None) -> list[str]:
    """A thread the sheet can draw (issue #12): the crest must be bigger
    than the root — for a tapped hole that means the thread is wider than
    its tap drill — and the threaded length must be a real span inside the
    part, not a stray coordinate."""
    out = []
    for t in part.get("threads") or []:
        kind = str(t.get("kind", "external"))
        if kind not in ("external", "internal"):
            out.append(f"{who}: thread kind {kind!r} (want external or "
                       "internal)")
        try:
            major, minor = float(t["major"]), float(t["minor"])
            a, b = sorted(float(v) for v in t["span"])
        except (KeyError, TypeError, ValueError):
            out.append(f"{who}: a thread needs major, minor and "
                       "span [start, end]")
            continue
        if not minor > 0 or not major > minor:
            what = ("tap drill" if kind == "internal" else "root")
            out.append(f"{who}: thread major {major:g} must be bigger than "
                       f"its {what} {minor:g}")
        if b - a <= 0:
            out.append(f"{who}: thread span {t['span']} has no length")
        elif bbox is not None:
            # the span is along the axis in the view's own coordinates, so
            # it can only be as long as the part is
            longest = max(bbox)
            if b - a > longest + 0.001:
                out.append(f"{who}: thread span {t['span']} is longer "
                           f"({b - a:g}) than the part ({longest:g})")
        if kind == "internal":
            for spec in t.get("circles") or []:
                cen = spec.get("center")
                if not (isinstance(cen, (list, tuple)) and len(cen) == 2):
                    out.append(f"{who}: thread circles need a "
                               "center [x, y] in the view's coordinates")
    return out


def paper_problems(m: dict) -> list[str]:
    """Per-sheet paper (issue #10): the paper must be one the harness is
    measured for, and the manifest must name a template for it — a sheet
    drawn on another paper's title block is a sheet with the wrong frame.
    """
    import draw
    out = []
    for key, path in (m.get("templates") or {}).items():
        out += [f"templates.{key}: {p}" for p in mw.template_problems(path)]
    sheets = [(p.get("file") or p.get("part_id"), p) for p in m.get("parts", [])
              if p.get("sheet", True)]
    asm = m.get("assembly_sheet") or {}
    if asm and asm.get("enabled", True):
        sheets.append(("assembly_sheet", asm))
    for who, spec in sheets:
        want = draw.sheet_paper(spec)
        if want is None:
            continue
        if want not in mw.PAPER_PAINTABLE:
            out.append(f"{who}: paper {spec.get('paper')!r} is not one the "
                       "harness is measured for (A4/A3/A2, portrait or "
                       "landscape)")
            continue
        _, problem = draw.sheet_template(m, spec)
        if problem:
            out.append(f"{who}: {problem}")
    return out


def quantity_problems(m: dict) -> list[str]:
    """Every part's QTY is what the model uses it (issue #9).

    The parts table counts from `part_usage`, so a manifest quantity that
    has drifted from the model would state one number in the balloon's row
    and another everywhere else. Reported at the lock, where it can still
    be put right."""
    src = REPORTS_DIR / "analysis.json"
    if not src.exists():
        return []
    usage = (read_json(src).get("assembly") or {}).get("part_usage") or {}
    if not usage:
        return []
    out = []
    for part in m.get("parts", []):
        used = (usage.get(part.get("file")) or {}).get("qty")
        if used is not None and part.get("qty") != used:
            out.append(f"{part.get('file')}: qty {part.get('qty')} but the "
                       f"assembly uses it {used} time"
                       f"{'s' if used != 1 else ''}")
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
    # the scale of the sheet each part is DRAWN on — its own, or, for a
    # part with no sheet of its own, that of the sibling sheet whose DRG
    # NO. it carries. /judge resolves it the same way (issue #8).
    scales = judge.sheet_scales(m.get("parts", []))
    for part in m.get("parts", []):
        body = bodies.get(part.get("file"))
        sc = scales.get(part.get("file"))
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
    src = REPORTS_DIR / "analysis.json"
    bboxes = {p["file"]: p["bodies"][0]["bbox_size"]
              for p in (read_json(src).get("parts", []) if src.exists()
                        else []) if p.get("bodies")}
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
        problems += thread_problems(part, str(part.get("file")),
                                    bboxes.get(part.get("file")))
        problems += variants_problems(part, str(part.get("file")))
    problems += thin_feature_problems(m)
    problems += quantity_problems(m)
    problems += paper_problems(m)
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
