# Changelog

All notable changes to this project. Versions follow
[semantic versioning](https://semver.org/) loosely: the minor number moves
when the drawing output or the manifest format gains something, the patch
number for fixes.

## v0.10.0 — 2026-11-24

Both open layout limits, and a regression of my own from v0.8.0:
[#13](https://github.com/MusaCAD/fabrication_pipeline/issues/13),
balloons on both sides of an assembly view, and
[#15](https://github.com/MusaCAD/fabrication_pipeline/issues/15).

### Fixed

- **A side-by-side section's own callouts had nowhere to land** (#13).
  With `"projection": "right"` (or `"left"`) and leaders on BOTH views,
  the front view's callouts opened the inter-view gap and nothing was
  reserved outside the group, so a callout on the section came out as
  `callout-<text>` in `unresolved` — and when the gap was capped the
  sheet died with "views exceed drawing area; pick a smaller scale",
  though the views fitted. Each view now gets its OWN column: the inner
  view's in the gap and the outer view's outside the group, each sized
  to its own callouts (`annotation_band_width(views=...)`), both
  reserved by `place_views` (`band_gap_w` beside `band_w`), with the
  group centred on both. The inner column wraps to the gap rather than
  to the sheet edge. When the columns genuinely do not fit, the message
  names them and their widths — the fix is a shorter callout, the other
  view, or a bigger sheet.
- **The logo printed off a widened sheet** (#15, a regression from
  v0.8.0). `tools/widen_template.py` wrote the new template's sidecar
  with the logo box already moved by the widening, and
  `SheetContext.logo_stamp` moved it again with `fx()`: on A3 landscape
  made from the A4 template the stamp landed at x ≈ 570 mm on a 420 mm
  sheet, so the sheet went out with the vector mark in the logo box and
  no logo image, and nothing noticed. The box is now copied UNMOVED, in
  the A4 template's own units, and the pipeline moves it exactly once.

### Added

- **Balloons on both sides of an assembly view.** One knee column holds
  about twenty balloons on a 190 mm drawing area; 26 part numbers need
  around 360 mm and failed one `balloon-<pid>` at a time. The fan now
  splits into two columns, one each side of the view
  (`draw.balloon_sides`), each balloon on the side its own ARROW is
  nearer — probed through `balloon_landing` itself — with the ones
  nearest the centre line moving over when a side is full
  (`_rebalance`), since a part out at the edge cannot change sides
  without its leader crossing the view. One balloon per P.NO. as before.

  Keeping the leaders from crossing took three things beyond the split: a
  split fan FIXES each landing where the fan was ordered from (it
  otherwise slides along the part's own edge as the knee moves), orders
  by the angle each landing subtends at the fan's MIDDLE (measured at the
  top, two landings of similar height order on a rounding), and then
  settles by exchanging any neighbours that would still cross
  (`_uncross`). A split fan also starts at the top of the drawing area
  rather than level with the view, and packs at a denser pitch — 2.5 mm
  between balloons instead of 4.

  The parts table gives way to the fan: the rows it cannot keep go to the
  continuation sheet, the fallback that was already reported. A missing
  balloon fails the sheet, while a table row has somewhere else to go. If
  two columns still cannot hold them, the sheet says so with the counts
  rather than dropping numbers.
- **The title block is checked against its own sheet** (#15).
  `mw.template_elements_off_sheet` checks every field and the logo box
  against the template's extents, and where the logo really stamps — so
  the lock catches a box given in the wrong units, with a message that
  names the fix. A field whose text is wider than the box the template
  drew for it is recorded per sheet (`SheetContext.field_overflow`), and
  `/export` refuses it — as it now refuses any stamp that lands outside
  the sheet window, which is the one failure neither the export nor the
  value check could see before, because a stamp off the paper is drawn
  nowhere at all.

### Upgrade steps

1. `git -C <your pipeline clone> fetch --tags && git checkout v0.10.0`
2. **If you worked around #15 by entering the A3 logo box in the A4
   template's units, that IS the convention now — but regenerate the
   template so the two agree:**
   `python3 tools/widen_template.py <a4>.musa <a3l>.musa --paper A3
   --landscape`, which copies the sidecar unmoved. If you hand-edited
   the sidecar, leave it in A4 units; the lock will tell you if it is
   wrong, and `/export` will stop before a logo prints off the sheet.
3. Nothing to change for #13 or the balloons: both are in the placement
   engine. Re-run `/draw`. An assembly sheet with more part numbers than
   one column can hold will now report `columns: 2` in sheets.json and
   may move more table rows to the continuation sheet — that is the fan
   taking the room it needs.

### Compatibility

Every sheet of four real sets — eleven sslxbt part sheets, the TPE
assembly and its parts, amogha RES-41-LH-01 and
`samples/out/RES-SAMPLE-01` — redraws byte for byte, and the value check
(#14) is clean across the live set. A sheet whose balloons fit one column
keeps the single fan it has always had, and a sheet with leaders on only
one view of a side-by-side pair keeps its single column.

## v0.9.0 — 2026-11-24

Two drawing conventions a real set needed, and the check that should have
caught what went out without them:
[#11](https://github.com/MusaCAD/fabrication_pipeline/issues/11),
[#12](https://github.com/MusaCAD/fabrication_pipeline/issues/12) and
[#14](https://github.com/MusaCAD/fabrication_pipeline/issues/14).

### Added

- **Tabulated drawings** (#11). A family that differs in ONE size is drawn
  once: the varying size carries a LETTER and a table gives its value per
  variant, with each one's mark and quantity. New part key:

  ```json
  "variants": {
    "letter": "T", "title": "TRIM DISC KIT %%c40",
    "dim": {"view": "section", "orient": "h", "side": "bottom",
            "a": [0.0, -20.0], "b": [5.0, -20.0]},
    "columns": ["MARK", "T", "QTY"],
    "rows": [["A", "5", "1"], ["B", "2", "3"],
             ["C", "1", "1"], ["D", "0.5", "2"]]
  }
  ```

  A DIM entity always prints the distance between its own def points —
  `core/dimension.cpp` composes prefix + measurement + suffix, and nothing
  replaces the middle — so the modelled variant's own 5 mm printed and
  read as the thickness of every disc in the kit. The workaround was to
  suppress the dimension and leave a leader note pointing at a paragraph.
  `draw.place_letter_dim` draws the dimension itself instead: the same
  extension lines, dimension line and filled arrowheads MusaCAD would
  draw for that style (`dim_arrowhead` matching `append_arrowhead`'s own
  proportions, so two kinds of arrow never appear on one sheet), with the
  letter where the value goes, placed and collision-checked through the
  same candidate ladder as any location dimension. The table goes in free
  space like the bend table — both now through `draw.place_table` — and
  fails the sheet rather than shrinking below the legibility floor. The
  lock checks the table has a column for the letter and every row is
  filled (`manifest.variants_problems`).
- **ISO 6410 for a tapped hole** (#12). `threads` drew the external
  convention only: a root line at the minor diameter inside the modelled
  crest. A tapped hole is modelled as its tap drill, so the solid's own
  edge is the minor diameter and the thread lived in the callout text
  alone — a sheet with an M6 x 13 deep on a ⌀5 x 16 drill showed only the
  drill. `"kind": "internal"` now draws it: a thin continuous line each
  side at the MAJOR diameter over the threaded span, the thread-end line
  where the usable thread stops short of the drill depth (on by default
  for an internal thread, since nothing else marks it), and a
  three-quarter thin arc at the major diameter — open in the upper-right
  quadrant — in a view that shows the hole as a circle
  (`"circles": [{"view": "front", "center": [0, 0]}]`). The section hatch
  already stops at the drill wall, where the solid ends, so the thread
  lines simply overlay it. The lock checks the thread is wider than its
  tap drill and that its span is a real length inside the part
  (`manifest.thread_problems`).
- **The value check** (#14). What the manifest STATES must be what the
  sheet PRINTS, and now the pipeline compares them. Nothing did before:
  the layout engine only guarantees that what it places does not overlap,
  and the qa-checker reads the PDF against the template rules — so a
  dropped tolerance and a model coordinate printed as a dimension both
  shipped while the draftsman's report said "every number kept".

  `src/verify.py` reads the stated values (callout and note text, GD&T
  tolerances and datum letters, thread designations, the variants table,
  the title block, and the value each location dimension must measure)
  and the printed ones (every MTEXT / TEXT / LEADER content in the .musa,
  plus every DIM label as MusaCAD itself composes it through
  `musa_plot --dump-dims`, so DIMLFAC and precision are already applied).
  Findings are of four kinds: `missing`, `changed` — which names the
  words that are not there, "the sheet is missing: f7" — `extra`, a
  printed VALUE the manifest does not account for, and `impossible`, a
  dimension bigger than the part it dimensions, which is what a model
  coordinate printed as a dimension looks like. Text wrapped across lines
  still matches, because the sheet is read as one running string both
  ways round (the notes block draws its lines bottom-up).

  /draw prints the findings and records them in sheets.json; /export
  REFUSES to run on any that is not waived by an explicit
  `"value_waivers"` entry in the manifest, named like every other waiver
  here. Over the eleven part sheets of a live set it reports nothing on
  the nine that place everything, and on the two that do not it names
  exactly what is missing — including a ⌀71.5 f7 (SPIGOT) callout that is
  not on the sheet at all.

### Compatibility

A manifest that asks for none of this gets none of it: every sheet of
four real sets — eleven sslxbt part sheets, the TPE assembly and its
parts, amogha RES-41-LH-01 and `samples/out/RES-SAMPLE-01` — redraws byte
for byte. The value check is new output, not new ink: it adds a `values`
list per sheet in sheets.json and a line to the /draw log.

## v0.8.0 — 2026-11-24

Three faults found drawing a real set, and the paper size they ran into:
[#7](https://github.com/MusaCAD/fabrication_pipeline/issues/7),
[#8](https://github.com/MusaCAD/fabrication_pipeline/issues/8),
[#9](https://github.com/MusaCAD/fabrication_pipeline/issues/9) and
[#10](https://github.com/MusaCAD/fabrication_pipeline/issues/10).

### Fixed

- **A feature control frame could still be drawn with no leader, and not
  reported** (#7). v0.7.0 made that a loud failure, but only on the paths
  it knew about: when the slot under a callout was taken, the widened
  ladder walked away from it and landed under a NEIGHBOUR's callout,
  where the frame needed no leader because a callout had been found at
  all. A frame is now emitted only when it TOUCHES its own size
  annotation — directly under or over it, overlapping across, with no
  other annotation in the gap (`draw._frame_attached`) — or carries a
  leader to it; otherwise the sheet fails with the reason and no frame
  ink is written. On the real sheets this moved a runout frame off a
  "2 x M4 ACROSS" note and onto its own ⌀51.8 spigot callout, and gave a
  perpendicularity frame that had been sitting under a stranger's ⌀180
  dimension a leader to the boss it controls.
- **A frame on a shoulder face leadered to the far side of the view**
  (#7). A face seen edge-on on a turned part is one short line EACH SIDE
  of the axis; `face_line` took the longest, which was as often the far
  one, and the leader then ran the width of the part, along the face
  line, through the section hatch. `face_lines` returns every segment of
  the face and `place_frame_near_face` takes the one it can reach by the
  shortest clear route; a route through the hatch (`hatch_polys` /
  `crosses_polys`) is taken only when there is no clear one.
- **A datum box crowded out by the callouts stacked under its own** (#7).
  Every spot within ±12 mm of the shoulder was the next callout's text,
  and datum A came out unresolved whatever order they were in. It now has
  the far end of its own landing to hang from (ISO 1101 5.3's extension
  of the dimension line) and, where even that is taken, the sheet is
  re-composed with the slot under that callout reserved
  (`draw.gdt_reserve`, claims of kind `"slot"` that only the frame and
  datum hung on it may use, released once they are placed). The retry
  runs only when the first pass failed, so a sheet whose GD&T already
  placed keeps the layout it had.
- **/judge judged a part drawn on ANOTHER part's sheet at the wrong
  scale** (#8). One disc of a kit, drawn 2:1 on the kit's sheet and
  carrying its DRG NO. with `sheet: false`, was judged at /judge's own
  suggested 1:1 and warned about thin features no manifest scale could
  clear, while the lock skipped it altogether. Both stages now resolve
  the sheet a part is drawn on (`judge.sheet_scales`) and judge it at
  that sheet's scale; a part no sheet draws at all is not judged for
  legibility, since nothing is drawn for it (`judge.not_drawn`).
- **Wrong quantities in a grouped assembly parts table** (#9). An O-ring
  used four times inside one sub-assembly printed P.NO. "17, 17, 17, 17"
  with QTY 16, a nut used six times across two printed "18, 18" with QTY
  12, and heading rows showed the number of parts under them (21, 22)
  where the sub-assembly itself is used once. Quantities now come from
  `part_usage`, once: `group_usage` counts instance paths per
  sub-assembly, every P.NO. gets exactly one row, a part used under
  several sub-assemblies is listed once in a common section instead of
  counted in each, a heading states the sub-assembly's own quantity
  (`sub_assembly_qty`) and carries no item number, a hardware group row
  lists each number once and only exists for two or more of a kind (so no
  row reads "1 ITEMS"), and TOTAL is the sum of the rows.
- **The pictorial box could be drawn across the parts table** (#9). The
  assembly sheet placed it before claiming the table's rows and without a
  floor, so it slid down into the bottom band; part sheets had always
  claimed the rows first.

### Added

- **A target may name WHICH feature of its size it means** (#7): size
  alone does not identify a feature on a tube bored the same at both
  ends, where the last callout authored silently won. A datum or frame
  target may carry a selector — `bore_r35.75@tail` for the callout whose
  manifest `"id"` is `tail`, or `bore_r35.75@x280` for its coordinate in
  the view's own axes — and a target that still fits two annotations
  fails, at the lock (`manifest.ambiguity_problem`) as well as at /draw.
- **Per-sheet paper size** (#10). A part entry or `assembly_sheet` may
  state `"paper": "A3L"` (or `{"size": "A3", "orientation":
  "landscape"}`) and is drawn on that paper's title block, named in the
  top-level `"templates": {"A3L": "/abs/path.musa"}`. One set may mix
  papers — an A3-landscape assembly sheet among A4 part sheets — and
  pdfunite merges them. The geometry is measured, not restated: a paper
  template is the A4 one widened (same units, same row pitch, same text,
  the same printed size, the title block travelling with the right edge),
  so `musa_writer.template_geom` takes the difference between the two
  furniture bounds as the widening and picks the smallest paper it fits.
  S0 therefore never changes — the title block prints identically on
  every sheet of a set and all the extra millimetres go to the drawing
  area. `tools/widen_template.py` makes such a template from the default
  one. sheets.json records each sheet's paper, /export plots on it and
  checks the harness agrees, /judge sizes the fit with `judge.area_mm`,
  and the lock checks the paper is one the harness is measured for and
  that a template exists for it.
- `manifest.quantity_problems` fails the lock when a manifest quantity
  has drifted from the model's own usage count.

### Compatibility

An existing manifest redraws byte for byte: no paper statement means A4
portrait with the measured S0 to the digit, and the GD&T retry pass runs
only where the first pass failed. Verified on four real sets — eleven
sslxbt part sheets, the TPE assembly and its parts, amogha RES-41-LH-01
and `samples/out/RES-SAMPLE-01`. Four sheets change, each because it
carried one of the faults above: two sslxbt sheets (the runout frame and
the two shoulder leaders), the TPE insert (its ⊥ frame was one of the
silently unleadered ones) and the sample (its datum now places, which
also lets the balance pass centre the sheet).

## v0.7.0 — 2026-11-24

Two GD&T cases common on turned parts could not be drawn
([#6](https://github.com/MusaCAD/fabrication_pipeline/issues/6)).

### Fixed

- **A datum on a feature of size the views show only EDGE-ON.** A turned
  part drawn as a side view and a section shows its spigot or bore as a
  pair of lines, never as a circle, and `place_datums` demanded a circle
  before it would hang the triangle on the feature's size callout — so a
  datum on a ⌀71.5 f7 spigot failed with `datum-A-no-size-dim-r…` although
  the callout was right there on the sheet. What a datum needs is the
  feature's SIZE dimension (ISO 1101 / ISO 5459), not a circle: when a
  callout carries that size — including an `at`-anchored one, which still
  carries `circle_r` — the triangle now goes on its leader's landing. The
  circle is used only for the other case, a datum in line with a diameter
  dimension.
- When nothing on the sheet carries that size, the failure now says what
  is missing and what would fix it: `datum-A: no size dimension for r23.5
  in view 'front' — add a %%c callout for it there, or a view showing the
  feature as a circle`.

### Added

- **Feature control frames on a plane face.** A face is drawn edge-on, so
  a perpendicularity, flatness or parallelism frame attaches to that line
  with a leader ending in an arrowhead on it (ISO 1101 5.2). New frame
  targets: `face_x<coord>` / `face_y<coord>` — a face at that coordinate
  in the view's own axes, the longest drawn edge there winning — and the
  named view edges `bottom_face` / `top_face` / `left_edge` /
  `right_edge`. Such a frame is placed BESIDE the view, square to its
  face, so the leader is short; parking it in the annotation band means a
  leader across the view and through whatever callouts stand between.
- A frame that can reach nothing in its view now FAILS instead of being
  drawn without a leader, pointing at no feature at all. The message says
  whether the feature is not drawn there or its leader has no clear route.
- `/finalise` checks each frame's target form and that every datum letter
  a frame refers to is a datum the sheet actually carries
  (`manifest.gdt_problems`) — a frame referring to a datum that is not on
  the drawing cannot be measured.

### Compatibility

Existing sheets are untouched: the wider search for a frame's position
only runs where no leader is possible at all. Verified byte-identical on
`samples/out/RES-SAMPLE-01`, the TPE assembly sheet and the amogha
RES-41-LH-01 sheet (real GD&T, two frames and a datum).

## v0.6.0 — 2026-11-24

Two issues from a live project, both blocking a locked manifest from
drawing in full:
[#4](https://github.com/MusaCAD/fabrication_pipeline/issues/4) and
[#5](https://github.com/MusaCAD/fabrication_pipeline/issues/5).

### Fixed

- **Thin features are judged at the scale the sheet is drawn at** (#4).
  `/judge` measured legibility with the scale it suggested itself, so a
  0.5 mm disc that /finalise puts on a 2:1 sheet — printing at a legible
  1.0 mm — was still reported as thin at 1:1 and had to be waived at the
  lock. It now takes the part's scale from the manifest (a locked one
  first, then a draft) and falls back to its own suggestion only when
  there is no manifest; the finding says which it used. `manifest.py lock`
  re-runs the same check, so a scale chosen at /finalise that is still too
  thin does not lock without a waiver, and one raised far enough needs no
  waiver at all. The judgement records `scale_used` beside
  `suggested_scale`.

### Added

- **Assembly sheets can carry a section** (#5). `assembly_sheet.section`
  works like a part sheet's — cutting plane, arrows, "SECTION A-A" — with
  the conventions an assembly needs (ISO 128-50):
  - every cut part is hatched SEPARATELY, and parts that touch in the cut
    get a different hatch angle or spacing. The styles come from colouring
    the adjacency graph of the cut areas, so the distinction follows the
    drawing rather than the part numbering;
  - `section.not_sectioned` lists the parts a section must not cut along
    their length — shafts, rods, screws, nuts, pins, O-rings. They are
    drawn whole inside the cut (`section_exempt` in fc_project, which cuts
    each instance separately);
  - a cut region thinner than 1 mm printed is filled solid.
- **Detail views on assembly sheets**: `assembly_sheet.detail_views`, a
  list of `{label, scale, of, center, radius, balloons}` using the part
  sheets' detail machinery from v0.4.0 (so their geometry is the parent
  view's own ink enlarged), each with its circle and letter on the parent
  view. A part too small to read in the main view is ballooned in the
  detail instead.
- **One balloon per part number on the whole sheet.** Balloons used to be
  drawn per INSTANCE, so a part with qty 3 carried three identical
  balloons. Each part number is now ballooned once, in the view that shows
  it best (the manifest's `balloon_in`, else the detail that lists it,
  else the section, else the front view), and every view's balloons share
  one knee column so leaders do not cross between views. A part number no
  view shows fails the sheet.
- **A parts table grouped by sub-assembly**, built on the v0.5.0 tree:
  `assembly_sheet.parts_table` `{group_by: none|sub_assembly,
  group_hardware, total}`. Grouped, each sub-assembly gets a heading row
  carrying its own quantity, followed by its parts, with a TOTAL row at
  the end. When the rows do not fit the drawing area the fallbacks run in
  order — group bought-out hardware into one row per kind, then move the
  rest onto a continuation sheet (same DRG NO., "SHEET 2 OF 2") — and the
  one used is reported in the /draw log and in `sheets.json`
  (`assembly_report`). Text is never shrunk below the template's floor.
- `/finalise` validates all of it (`manifest.assembly_sheet_problems`): the
  section's projection and the parts it names, each detail's parent view,
  standard-and-enlarging scale and free letter, the parts named by
  `balloons` / `balloon_in` and the views they point at, and the table's
  grouping mode.

### Changed

- The assembly composer may now return more than one sheet, so
  `build_assembly_sheet` returns a list and `sheets.json` gains
  `assembly_report` (and `sheet_of` when a table continues).
- **Defaults are the old sheet exactly**: no section, no details, an
  ungrouped table with no TOTAL row. An existing flat assembly sheet
  redraws byte-identical — except that a part with several instances now
  carries one balloon instead of one per instance, which is the point of
  the change.

## v0.5.0 — 2026-11-23

### Fixed

- **Nested sub-assemblies are followed to the real parts**
  ([#3](https://github.com/MusaCAD/fabrication_pipeline/issues/3)).
  `/analyse` followed every link exactly one level, so a sub-assembly file
  was reported as if it were a part and the parts inside it were never
  analysed at all. On the 700 W enclosure model (10 sub-assemblies, 100
  real parts) the analysis listed **10 parts — every one of them an
  assembly file** — and /finalise would have proposed a part sheet for
  each. It now reports 282 instances across those 10 sub-assemblies, 100
  real part files, and no findings.

  The new `src/fc_tree.py` walks both shapes a sub-assembly takes in
  FreeCAD 1.1 — an `Assembly::AssemblyLink`, which mirrors the
  sub-assembly's parts into the top document as links to links, and a
  plain `App::Link` straight at another file's `Assembly::AssemblyObject`
  — to any depth. It is shared by `fc_analyse` and `fc_project`, so the
  report and the sheet can never disagree about what is in the model.
  Specifically:
  - mirrors are no longer counted at the top level as well (every
    sub-assembly part used to appear twice, once with the wrong target);
  - a nested instance's placement and shape are composed with their
    parents', so a sub-assembly away from the origin is analysed AND drawn
    where it really is (it used to draw at the origin, over the top
    assembly's own parts);
  - a sub-assembly's joints are read from its own file and re-addressed to
    the parent's instance paths, and a reference reaching through a
    sub-assembly (`SUB1.SPLATE.Vertex1`) resolves, so correctly joined
    parts are no longer reported as unjoined;
  - a cyclic sub-assembly link is refused with a named chain instead of
    being followed. FreeCAD 1.1.1 saves such a model and then **segfaults**
    when asked for the link's shape, so the cycle is found before any shape
    is touched.
- `/judge` walks the whole tree: an instance is joined when a live joint
  names its path or anything inside it (a joint on a part of a rigid
  sub-assembly locates that sub-assembly), never because a sibling or a
  parent is joined — so a genuinely unjoined part one level down is still
  reported, by path (`SUB1/SHIM`). Grounding is checked per assembly
  document, the top one and each sub-assembly.

### Added

- `assembly.instances` in `analysis.json` is now a tree (`path`, `kind`,
  `children`, global `position`/`bbox` per node); `assembly.part_usage`
  maps each real part file to `{qty, used_in}` and
  `assembly.sub_assembly_files` lists the sub-assembly documents. The
  markdown report prints the tree and a "Parts used" table.
- `/finalise` takes each part's QTY from `part_usage`, so the parts table
  counts a part correctly across every sub-assembly that uses it.
- Balloons resolve an instance by path, so a balloon on a sub-assembly's
  part still finds the P.NO. of the real part file.
- Fixtures and tests for two-level assemblies: three joint types through a
  sub-assembly with no false warnings, an unjoined part one level down
  that still warns, one part file used by two sub-assemblies (analysed
  once, counted twice), a plain-link sub-assembly, and a cyclic pair.

### Note

- Issue [#1](https://github.com/MusaCAD/fabrication_pipeline/issues/1)
  (FreeCAD 1.1 joint references) shipped in **v0.2.0** and is unchanged
  here beyond moving its resolver into `fc_tree` alongside the new
  through-a-sub-assembly case.

## v0.4.0 — 2026-11-20

Three drafting features turned and tubular parts needed before boards could
be drawn ([#2](https://github.com/MusaCAD/fabrication_pipeline/issues/2)).

### Added

- **Enlarged detail views.** Manifest part `"detail_view": {label, scale,
  of, center, radius}` — `of` names the view the region is taken from
  (`front`, `section`, `aux` or `flat`), `center` and `radius` are in that
  view's own coordinates. The region is clipped out of the parent view's
  projected geometry (edges, arcs and section hatch alike), redrawn at its
  own standard scale inside a thin boundary circle, captioned
  `DETAIL B (2:1)`, and marked with a matching circle and letter on the
  parent view. It is placed in free space, clear of the other views'
  dimension gutters, and collision-checked like every other annotation —
  the two-views rule is now **two views plus an optional detail**.
  **Its dimensions read true millimetres.** A DIM always displays the
  distance between its own def points, so a detail drawn at k times size
  dimensions through a DIMSTYLE carrying `DIMLFAC = 1/k` (the new
  `DIMUNITS` record): a 2:1 detail on a 1:5 sheet is drawn 10x and its
  style's factor is 0.1. Detail dims are authored in the parent view's
  true millimetres; `/draw` moves them onto the enlarged geometry.
- **Datums on a feature of size.** A datum target may now name a feature
  instead of a view edge: `"target": "bore_r12.5"` (any `<kind>_r<radius>`,
  the radius always in true millimetres). The datum is then the feature's
  AXIS, so the triangle goes on that feature's own size annotation — the
  shoulder of its callout's leader, as ISO 1101 and ASME Y14.5 draw it, or
  in line with its diameter dimension line when it is dimensioned that way.
  A feature with no size annotation fails the sheet rather than carrying a
  triangle that identifies nothing, and the box is placed clear of other
  labels so it cannot be read as theirs.
- **Coaxiality and circular runout** feature control frames, beside the
  existing flatness, position and perpendicularity. Coaxiality's tolerance
  zone is cylindrical, so its value carries the diameter sign.
- `musa_plot --dump-dims` prints every dimension on a sheet with the label
  **MusaCAD itself composes** for it (its own `compute_dim_geometry`, the
  plot's path), then exits without plotting. `common.dump_dims()` parses
  it. This is the only way to check what a sheet reads, and it is what the
  detail-view tests assert against. Rebuild the harness (`tools/build.sh`)
  to get it.
- `samples/sample_sheet.py` draws `samples/out/RES-SAMPLE-01.pdf`, a
  bushing with a 2:1 detail of its retaining groove, a datum on its bore
  and coaxiality/runout frames. It goes through the real composer but
  needs no model and no FreeCAD, so it is a quick look at the layout
  engine after a change.

### Changed

- `/finalise` (`manifest.py lock`) validates the new keys: a detail view's
  parent must be a view the sheet carries, its scale must be a standard one
  that really enlarges, its label must be a single letter that does not
  collide with the section's, and its centre and radius must be usable.
  Datum targets must be a known view edge or a parsable `_r<radius>`.
- `place_callouts` also reports each callout's leader landing, and
  `place_datums` / `place_fcfs` take the per-view linear factors, so a
  radius target means the same feature whatever view it is resolved in.

## v0.3.0 — 2026-11-19

### Changed

- **Company title blocks are project files.** The pipeline ships only its
  default `templates/template.musa`; a project keeps its own title block
  in its own folder and the manifest names it by absolute path
  (`"template": "/path/to/project/fabrication/template/template_x.musa"`).
  A bare name still resolves inside `templates/`. Company templates are
  removed from `templates/`.
  **Upgrade:** copy any company template you used from `templates/` into
  its project and set the manifest's `"template"` to that absolute path.
- `/finalise` (`manifest.py lock`) refuses a template that is missing,
  does not parse, or whose logo does not resolve.

### Added

- **Company logos on the title block.** A template may carry a raster
  logo: a sidecar `<template stem>.logo.json` next to it names a PNG
  (relative to the template's folder) and a box in template units. `/draw`
  records each sheet's logo rectangle in `sheets.json` (scaled with the
  template and fitted to the image's aspect, so it prints at the same size
  at every sheet scale) and `/export` stamps it into the PDF next to the
  shaded pictorial. Templates without a sidecar plot exactly as before.
- `common.musa_plot_cmd` builds the harness command line; `run_musa_plot`
  takes any number of stamps.
- Tests for the logo sidecar, the stamp rectangle at several scales, the
  harness command, template resolution and the lock-time template check.

## v0.2.0 — 2026-11-18

### Fixed

- **Assembly joints in FreeCAD 1.1 are read again**
  ([#1](https://github.com/MusaCAD/fabrication_pipeline/issues/1)).
  `/analyse` built each joint's connection list from the joint's `OutList`,
  keeping the `App::Link` objects. FreeCAD 1.1's Assembly workbench writes
  joint references as `Reference1 = (AssemblyObject, ["LinkName.Vertex1",
  ...])`, so the `OutList` holds only the Assembly object and every joint
  but the grounded one came back connecting nothing — `/judge` then warned
  "instances with no joint" for parts that were in fact fully joined (on
  one 14-joint assembly, 13 of 14 instances were reported loose).
  `fc_analyse` now reads `Reference1`/`Reference2` and the grounded joint's
  `ObjectToGround`, resolving the instance from the head of the subname
  path, and still falls back to the `OutList` scan, so FreeCAD 1.0 and
  older assemblies (references pointing straight at the link) read exactly
  as before. A genuinely unjoined instance is still warned about.
- A suppressed joint is now reported as `"suppressed": true` in
  `analysis.json` and no longer counts as joining the instances it names —
  it sits in the tree but constrains nothing.

### Added

- ISO 6410 thread convention: the model carries the real helix, the
  orthographic views project the thread blank (manifest
  `"project_feature"`) and draw the root line from manifest `"threads"`.
- Angular references: a labelled centre line through each angularly placed
  feature, a `0 REF` datum label and a CCW direction arc.
- Bought-out items (manifest `"sheet": false`): P.NO. row and assembly
  balloon, no sheet of their own; print weight falls back to grams below
  0.01 kg.
- Sheet-metal and plain-solid parts: the part of a document is its tip
  solid (`src/fc_shapes.py`), and a `*_Flat` twin drives a flat-pattern
  view with a bend table.
- Standalone part packs: `/analyse <dir> --no-assembly` and `--parts`.
- `templates/template_solon.musa`.
- A test suite: `python3 -m unittest discover -s tests -v` (stdlib only).
  The assembly tests build throwaway FreeCAD 1.1 assemblies with the real
  Assembly workbench and run the FreeCAD-side scripts against them; they
  skip themselves when the FreeCAD AppImage is absent, and never touch
  `reports/`, `manifests/` or `output/`.

### Changed

- `judge.assembly_findings()` is now a function of the analysis dict
  rather than inline in `main()`, so the model-level checks are testable.
- `src/fc_analyse.py` only runs its entry point when `FABPIPE_ARGS` is
  set: FreeCAD's `-c` executes a script as a module named after the file,
  never as `__main__`, so a `__main__` guard would never fire and the
  module could not be imported by the tests.
- Layout engine, `musa_writer` and shaded-pictorial refinements.

## v0.1.0 — 2026-08-01 … 2026-11-18

The pipeline up to commit `2ca1234`, released retrospectively: FreeCAD
model analysis, fabrication judgement, the locked manifest, MusaCAD `.musa`
sheet composition with the collision-driven layout engine, and A4 portrait
PDF export with the `musa_plot` harness.
