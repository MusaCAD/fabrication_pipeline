# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Pranay Kiran
"""Which object in a part document actually IS the part. Runs inside FreeCAD.

A part file is not always a PartDesign body. The pipeline now also drafts
SheetMetal parts (chains of Part::FeaturePython bend features, plus an
unfolded `*_Flat` twin) and plain Part::Feature solids (gaskets, pads, cut
plastics). All three need the same question answered: of the several solids
in this document, which one is the finished part?

The answer is the TIP: a solid that no other solid is built from. Containers
(App::Part, groups, origins) are not solids and never consume one, and the
flat pattern is excluded from consuming its folded parent — it is a twin of
the part, not a later stage of it.
"""

CONTAINERS = ("App::Part", "App::DocumentObjectGroup", "App::Origin",
              "App::Line", "App::Plane", "App::Point")


def _is_flat(obj):
    return obj.Name.endswith("_Flat") or obj.Label.endswith("_Flat")


def _solids(doc):
    out = []
    for obj in doc.Objects:
        if obj.TypeId in CONTAINERS:
            continue
        sh = getattr(obj, "Shape", None)
        if sh is None or sh.isNull() or not sh.Solids:
            continue
        out.append(obj)
    return out


def tips(doc):
    """Finished solids in the document: (folded_part, flat_pattern_or_None).

    A PartDesign body wins outright — its own tip feature is already the
    finished shape, and its internal features would otherwise look like
    rival candidates.
    """
    bodies = [o for o in doc.Objects
              if o.TypeId == "PartDesign::Body"
              and getattr(o, "Shape", None) is not None
              and not o.Shape.isNull() and o.Shape.Solids]
    cands = _solids(doc)
    flats = [o for o in cands if _is_flat(o)]
    if bodies:
        return bodies[0], (flats[0] if flats else None)
    names = {o.Name for o in cands}
    consumed = set()
    for obj in cands:
        if _is_flat(obj):
            continue            # the flat is a twin, not a later stage
        for src in obj.OutList:
            if src.Name in names:
                consumed.add(src.Name)
    live = [o for o in cands if o.Name not in consumed and not _is_flat(o)]
    if not live:
        return None, (flats[0] if flats else None)
    # several unrelated solids: the biggest is the part, the rest are stock
    live.sort(key=lambda o: -o.Shape.Volume)
    return live[0], (flats[0] if flats else None)


def bend_features(doc):
    """Every sheet-metal bend, read from the SheetMetal features themselves.

    These values are the model's own inputs, so a bend table built from them
    states what was modelled rather than what was measured off the solid.
    """
    out = []
    for obj in doc.Objects:
        if not (hasattr(obj, "angle") and hasattr(obj, "radius")):
            continue
        parents = [s.Name for s in obj.OutList
                   if getattr(s, "Shape", None) is not None
                   and not s.Shape.isNull() and s.Shape.Solids]
        out.append({
            "name": obj.Name,
            "label": obj.Label,
            "angle_deg": round(float(obj.angle), 3),
            "radius_mm": round(float(obj.radius), 3),
            "length_mm": round(float(getattr(obj, "length", 0.0)), 3),
            "invert": bool(getattr(obj, "invert", False)),
            "bend_type": str(getattr(obj, "BendType", "")),
            "parents": parents,
        })
    return out


def thickness_of(shape):
    """Sheet thickness: the smallest bounding-box extent of a flat blank."""
    bb = shape.BoundBox
    return round(min(bb.XLength, bb.YLength, bb.ZLength), 3)
