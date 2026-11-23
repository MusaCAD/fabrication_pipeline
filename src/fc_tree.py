# SPDX-License-Identifier: LGPL-3.0-or-later
# Copyright (C) 2026 Pranay Kiran
"""The instance tree of a FreeCAD assembly, sub-assemblies included.

Runs inside FreeCAD, but touches no FreeCAD API by name: every object is
duck-typed (TypeId, Name, Label, Placement, Shape, Group, InList,
LinkedObject, getLinkedObject), so the walk can be tested against plain
stand-ins. fc_analyse and fc_project share it — the analysis and the
drawing must see the SAME tree or a sheet will disagree with its report.

FreeCAD 1.1 brings one assembly into another in two shapes, and both occur
in real models (issue #3):

  (a) `Assembly::AssemblyLink` (the GUI's Insert Component on an assembly
      file). It MIRRORS every part of the sub-assembly as an `App::Link` in
      the TOP document, each mirror pointing at the sub-assembly file's own
      link — a link to a link — and owns those mirrors in its `Group`. The
      mirrors sit in the top document's object list like any other link, so
      a flat scan counts them as top-level instances AND reads their target
      as the sub-assembly file. Their placements are relative to the
      AssemblyLink.
  (b) a plain `App::Link` whose `LinkedObject` is another file's
      `Assembly::AssemblyObject`. Nothing is mirrored; the children are the
      links in the linked assembly's own `Group`, in that other document,
      placed relative to it.

Either way the real part is `getLinkedObject(True)` — it follows the whole
chain — and the part FILE is that object's document. An assembly reached
through an assembly is just another level, so the walk recurses, carrying
the ancestor chain to refuse a cycle instead of following it forever.
"""

ASSEMBLY_TYPES = ("Assembly::AssemblyObject", "Assembly::AssemblyLink")
LINK_TYPES = ("App::Link", "Assembly::AssemblyLink")


def _base(path):
    return str(path).rsplit("/", 1)[-1] or str(path)


def doc_file(obj):
    """The file an object's document was loaded from ("" for unsaved)."""
    doc = getattr(obj, "Document", None)
    return getattr(doc, "FileName", None) or ""


def deep_target(obj):
    """The real object at the end of a link chain, or None.

    getLinkedObject(True) walks link -> link -> body in one call; on an
    AssemblyLink it returns the AssemblyLink itself, which is why callers
    test the RESULT's type rather than assuming a body.
    """
    get = getattr(obj, "getLinkedObject", None)
    if get is not None:
        try:
            return get(True)
        except Exception:
            pass
    return getattr(obj, "LinkedObject", None)


def is_assembly(obj):
    return obj is not None and getattr(obj, "TypeId", "") in ASSEMBLY_TYPES


def is_mirror(obj):
    """True for a link an AssemblyLink owns (shape (a) above).

    Such a link is reached through its AssemblyLink, so counting it again
    at the top level would double every sub-assembly part.
    """
    for parent in getattr(obj, "InList", []):
        if getattr(parent, "TypeId", "") == "Assembly::AssemblyLink":
            return True
    return False


def top_instances(doc):
    """The instance links of an assembly document, mirrors excluded."""
    return [o for o in getattr(doc, "Objects", [])
            if getattr(o, "TypeId", "") in LINK_TYPES and not is_mirror(o)]


def child_links(obj, deep=None):
    """The instance links one level inside a sub-assembly instance."""
    if deep is None:
        deep = deep_target(obj)
    owner = None
    if getattr(obj, "TypeId", "") == "Assembly::AssemblyLink":
        owner = obj                      # (a) it owns its mirrors
    elif getattr(deep, "TypeId", "") == "Assembly::AssemblyObject":
        owner = deep                     # (b) the linked assembly's own links
    if owner is None:
        return []
    return [g for g in getattr(owner, "Group", [])
            if getattr(g, "TypeId", "") in LINK_TYPES]


def sub_document(obj, deep=None):
    """The document whose joints constrain a sub-assembly's children."""
    if deep is None:
        deep = deep_target(obj)
    if getattr(obj, "TypeId", "") == "Assembly::AssemblyLink":
        linked = getattr(obj, "LinkedObject", None)   # the AssemblyObject
        return getattr(linked, "Document", None)
    if getattr(deep, "TypeId", "") == "Assembly::AssemblyObject":
        return getattr(deep, "Document", None)
    return None


def _global_placement(obj, parent_placement):
    plc = getattr(obj, "Placement", None)
    if plc is None or parent_placement is None:
        return plc
    return parent_placement.multiply(plc)


def _global_shape(obj, parent_placement):
    """The instance's shape in TOP-assembly coordinates.

    A mirror's own Shape is relative to its AssemblyLink (measured: a
    mirror 100 mm along X reports a bounding box at the origin), so a
    nested shape is copied and re-placed before anything reads it.
    """
    sh = getattr(obj, "Shape", None)
    if sh is None or parent_placement is None:
        return sh
    out = sh.copy()
    out.Placement = parent_placement.multiply(out.Placement)
    return out


def walk(doc, issues=None):
    """The instance tree of an assembly document.

    Returns a list of nodes, each:
      name, label, path ("SUB1/SPLATE" — the instance identity used
      everywhere, so the report and the sheet agree), kind ("part" |
      "assembly"), target_file (the real part file, or the sub-assembly's
      file), broken, children, plus two private keys the caller consumes
      and must strip before JSON: `_placement` (the GLOBAL placement) and
      `_shape` (the shape in top-assembly coordinates, or None).

    `issues` collects what the walk refuses to paper over: a broken link,
    a cyclic sub-assembly link.
    """
    # the top file seeds the ancestor chain: a sub-assembly that links
    # back to the assembly it sits in is a cycle like any other
    return _walk(top_instances(doc), [] if issues is None else issues,
                 seen_files=(getattr(doc, "FileName", "") or "",))


def _walk(links, issues, path="", parent_placement=None, seen_files=()):
    nodes = []
    for obj in links:
        name = getattr(obj, "Name", "?")
        label = getattr(obj, "Label", name)
        here = f"{path}{name}"
        deep = deep_target(obj)
        node = {"name": name, "label": label, "path": here}
        if deep is None:
            issues.append(f"broken link: {name} ({label})"
                          + (f" in {path.rstrip('/')}" if path else ""))
            node.update({"kind": "part", "target_file": None, "broken": True,
                         "children": []})
            nodes.append(node)
            continue
        node["broken"] = False
        gplc = _global_placement(obj, parent_placement)
        node["_placement"] = gplc
        # NB: the shape is left until the structure below this node is
        # known. Asking a cyclic link for its Shape does not raise — it
        # takes FreeCAD down with a segmentation fault (measured on 1.1.1),
        # so a cycle has to be found before any shape is touched.
        if is_assembly(deep) or child_links(obj, deep):
            sub = sub_document(obj, deep)
            target = doc_file(deep) if is_assembly(deep) else doc_file(obj)
            if getattr(obj, "TypeId", "") == "Assembly::AssemblyLink":
                target = doc_file(getattr(obj, "LinkedObject", None) or obj)
            node.update({"kind": "assembly", "target_file": target or None,
                         "sub_document": getattr(sub, "Name", None)})
            if target and target in seen_files:
                # file NAMES: the chain is what matters, and an absolute
                # path per hop makes the finding unreadable
                chain = " -> ".join(
                    _base(f) for f in list(seen_files) + [target])
                issues.append(f"cyclic sub-assembly link at {here}: {chain}")
                node["children"] = []
                node["cyclic"] = True
            else:
                node["children"] = _walk(
                    child_links(obj, deep), issues, path=here + "/",
                    parent_placement=gplc,
                    seen_files=tuple(seen_files) + ((target,) if target
                                                    else ()))
        else:
            node.update({"kind": "part", "target_file": doc_file(deep) or None,
                         # the part document's NAME, for a caller that has to
                         # reach back into it (fc_project's thread blanks)
                         "_document": getattr(
                             getattr(deep, "Document", None), "Name", None),
                         "children": []})
        node["_shape"] = (None if has_cycle(node)
                          else _global_shape(obj, parent_placement))
        nodes.append(node)
    return nodes


def has_cycle(node):
    """True if this node, or anything under it, closes a link cycle."""
    if node.get("cyclic"):
        return True
    return any(has_cycle(k) for k in node.get("children", []))


def _match(nodes, name):
    for n in nodes:
        if n["name"] == name or n["label"] == name:
            return n
    return None


def resolve_ref_path(ref, nodes):
    """The instance PATHS a joint reference points at, deepest match first
    for each of its subnames.

    One rule covers every layout seen in the wild (issues #1 and #3):
    start at the referenced object when that is itself an instance link,
    else at this assembly's own instances, then walk DOWN the subname path
    as far as it keeps naming children —

      (link, ["Vertex1"])                 -> that link            (FC 1.0)
      (Assembly, ["BLOCK.Vertex1"])       -> BLOCK                (FC 1.1)
      (Assembly, ["SUB1.SPLATE.Vertex1"]) -> SUB1/SPLATE   (AssemblyLink)
      (SubAsmLink, ["PART.Face1"])        -> SubAsmLink/PART  (linked asm)

    An element name ("Vertex1", or FreeCAD's unresolved "?Vertex15") names
    no child, so the walk simply stops there — which is why the first case
    resolves to the link itself.
    """
    if not ref:
        return []
    obj, subs = ref[0], (ref[1] or [])
    if obj is None:
        return []
    start = None
    if getattr(obj, "TypeId", "") in LINK_TYPES:
        start = _match(nodes, getattr(obj, "Name", ""))
        if start is None:
            return []
    out = []
    for sub_name in subs:
        cur = start
        pool = nodes if cur is None else cur.get("children", [])
        for part in [p.lstrip("?") for p in str(sub_name).split(".") if p]:
            nxt = _match(pool, part)
            if nxt is None:
                break
            cur = nxt
            pool = cur.get("children", [])
        if cur is not None and cur["path"] not in out:
            out.append(cur["path"])
    return out


def flatten(nodes):
    """Every node of the tree, parents before children."""
    out = []
    for n in nodes:
        out.append(n)
        out.extend(flatten(n.get("children", [])))
    return out


def leaves(nodes):
    """The real parts: nodes that are not assemblies."""
    return [n for n in flatten(nodes) if n.get("kind") != "assembly"]


def part_usage(nodes):
    """part file -> {qty, used_in}: how many times each real part appears
    in the whole tree and under which paths. A part used by two different
    sub-assemblies is ONE part file analysed once and two items on the
    sheet; a sub-assembly used twice doubles everything inside it."""
    usage = {}
    for n in leaves(nodes):
        f = n.get("target_file")
        if not f:
            continue
        u = usage.setdefault(f, {"qty": 0, "used_in": []})
        u["qty"] += 1
        u["used_in"].append(n["path"])
    return usage


def path_files(nodes):
    """instance path -> the file it is an instance of (drawing side)."""
    return {n["path"]: n.get("target_file") for n in flatten(nodes)}
