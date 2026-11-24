# Fabrication Pipeline — Project Memory

FreeCAD assembly/part models → analysis → judgement → locked manifest →
MusaCAD (.musa) fabrication sheets → PDFs (A4 portrait, or the paper a
sheet states). Company: Renewable
Energy Systems Ltd (RES); drawings are drawn/designed by "Er. P. PRANAY".
Style target for every sheet: `reference/0982_001-1.pdf`.

License: LGPL-3.0-or-later (COPYING/COPYING.LESSER, SPDX tags per source
file, convention mirrored from MusaCAD). Org policy: NEVER add a Claude
co-author line to git commits.

## Usage vs development (session contract)
- DRAWING-GENERATION sessions (other chats): USE the pipeline via the slash
  commands only — never modify src/, tools/, .claude/, templates/ there.
  Per-project state (reports/, manifests/, output/) is gitignored and
  local to each project run.
- SOFTWARE-UPDATE sessions (this chat's lineage): modify the software,
  keep CLAUDE.md + README.md in sync, commit + push to
  git@github.com:MusaCAD/fabrication_pipeline.git (branch main).
- Per-project DELIVERY: every project states its folder (ask if not given);
  manifest "deliver_to" copies the final sheet PDFs + print_set there on
  /export (e.g. /home/pranay/projects/lisocl2/fabrication/). output/ stays
  a per-run working area only.
- MusaCAD gaps found while drafting are FILED UPSTREAM (gh issue on
  MusaCAD/MusaCAD) — see memory musacad-gap-issues; workarounds live in the
  harness (e.g. --min-lw 0.30 floors hairline text strokes, issue #19).
- tools/musacad-libs/ holds MusaCAD static libs built OUT-OF-TREE by
  tools/build.sh (musa_cad tree never modified/rebuilt in place); rebuild
  after every musa_cad pull so headers and libs never diverge.

## Fixed paths (do not guess others)
| What | Path | Access |
|---|---|---|
| This project | /home/pranay/projects/fabrication_pipeline/ | rw |
| Target models (Round 0) | /home/pranay/projects/lisocl2/extrusion_jig_x3/ | **READ-ONLY** |
| Top assembly | assembly_extrusion_jig.FCStd (in model dir) | READ-ONLY |
| MusaCAD dev source | /home/pranay/projects/musa_cad/ | **READ-ONLY** (API reference + prebuilt libs) |
| Drawing template | templates/template.musa | rw (treat as canonical) |
| Reference drawing | reference/0982_001-1.pdf | READ-ONLY (style target) |
| FreeCAD | /home/pranay/programs/FreeCAD_1.1.1-Linux-x86_64-py311.AppImage | executable |
| Plot harness | tools/bin/musa_plot (build: tools/build.sh) | built artifact |

## Toolchain facts (verified Phase 0, 2026-08-01)
### FreeCAD 1.1.1 headless
- `<AppImage> -c <script.py> </dev/null`; script must end
  `sys.stdout.flush(); os._exit(0)` (else hang / lost output).
- `-c` runs the file as a module named AFTER THE FILE, never as
  `__main__` — a `__main__` guard would never fire. fc_analyse.py therefore
  guards its entry on `"FABPIPE_ARGS" in os.environ`, so the tests can
  import it without running it.
- Args go via env var `FABPIPE_ARGS` (JSON list) — see src/common.run_freecad.
- `TechDraw.project(shape, dir)` works headlessly → 4 groups (visible
  hard/smooth, hidden hard/smooth); edges discretize to polylines.
- Assembly is FreeCAD 1.x builtin workbench (Assembly::AssemblyObject,
  App::Link instances, App::FeaturePython joints).
- SUB-ASSEMBLIES come in two shapes and both occur (issue #3):
  `Assembly::AssemblyLink` (the GUI's Insert Component on an assembly file)
  MIRRORS every part of the sub-assembly as an `App::Link` in the TOP
  document, each mirror pointing at the sub-file's own link (a link to a
  link) and owned by the AssemblyLink's `Group`; a plain `App::Link` whose
  `LinkedObject` is another file's `Assembly::AssemblyObject` mirrors
  nothing and keeps its children in that other document's `Group`. Either
  way the real part is `getLinkedObject(True)` and the part FILE is that
  object's document. src/fc_tree.py walks both, at any depth, and is shared
  by fc_analyse and fc_project so the report and the sheet see ONE tree.
  Hard-won facts: a mirror's placement and Shape are relative to its
  AssemblyLink (compose with the parent, or a sub-assembly draws at the
  origin); mirrors must be excluded from the top level or every part is
  counted twice; a sub-assembly's joints live in ITS OWN file and are read
  there; an instance is identified by PATH ("SUB1/SPLATE") everywhere —
  report, joints, balloons; and asking a CYCLIC link for its `Shape`
  SEGFAULTS FreeCAD 1.1.1, so fc_tree refuses a cycle (issue -> /judge
  FAIL) before any shape is touched.
- JOINT REFERENCES come in two layouts and both must be read (issue #1):
  1.1's Assembly workbench writes `Reference1 = (AssemblyObject,
  ["LinkName.Vertex1", ...])` — the instance is the HEAD of the subname
  path and the joint's `OutList` holds only the Assembly, so an OutList
  scan finds nothing; 1.0 and older write `(link, ["Vertex1", ...])`,
  where the referenced object IS the instance. GroundedJoint carries
  `ObjectToGround`. A reference may also reach THROUGH a sub-assembly, as
  a three-part subname `SUB1.SPLATE.Vertex1` (AssemblyLink, mirror,
  element). fc_tree.resolve_ref_path handles all of them with one rule —
  start at the referenced object when it is itself an instance, else at
  this assembly's instances, then walk down the subname while it keeps
  naming children — and joints also report `suppressed` (a suppressed
  joint is in the tree but constrains nothing) and `assembly` (which
  assembly document authored it; "" = the top one).
- Origin planes/axes have 1e100 bboxes — filter to PartDesign::Body.

### MusaCAD 0.1.0
- No scripting API, no CLI args. We author `.musa` v14 text directly
  (grammar reverse-engineered from musa_cad/src/core/io/native_format.cpp)
  and plot with our own harness `tools/bin/musa_plot` (extended copy of
  musa_cad/tools/plot_check.cpp, linked against the prebuilt release static
  libs — musa_cad tree itself is never modified or rebuilt).
- Harness: `QT_QPA_PLATFORM=offscreen tools/bin/musa_plot --file s.musa out.pdf
  --paper A4 --portrait --scale N:D --window x0,y0,x1,y1`
  → prints `[musa_plot] ok ... mm_per_unit=...` (parse this for QA);
  exit 1 + "parse FAILED" on bad .musa.
- `--dump-dims` prints one line per DIM — def points, measured distance and
  the LABEL MusaCAD composes for it (compute_dim_geometry, the plot's own
  path) — then exits without plotting. The only way to check what a sheet
  READS; common.dump_dims() parses it. Use it on every detail view.
- Parser is STRICT: exact token counts; TEXT/MTEXT need content + font lines
  (font line mandatory in v14, empty = stroke font); file starts
  `MUSACAD 14`, ends `END`; unknown record key kills the whole file.
- DIM entities always display the measured def-point distance (cannot fake).
  The ONE honest lever is `DIMUNITS idx lunit frac rnd lfac aunit adec+1 alt
  altu altd altf altrnd post apost` (15 tokens exactly, v40): its `lfac`
  (DIMLFAC) multiplies every measured length before it is written. That is
  how an enlarged DETAIL view reads true millimetres — geometry at k times
  size, style at lfac = 1/k. musa_writer.dimunits() writes it; it must come
  AFTER the DIMSTYLE records (SheetContext.dimunits renders it there).
- Angles in radians. `%%p` ±, `%%c` ⌀, `%%d` °. ANSI31 = section hatch.
- Always use src/musa_writer.py helpers, never hand-format records.

## Scale model (THE core invariant — hard rules)
- A sheet's paper and its template's aspect ratio are INVIOLABLE.
  Uniform scaling only. Non-uniform scaling of anything = bug.
- Sheets are authored in mm. Template is scaled uniformly by
  `t = S0 * den/num` (S0 = 0.180175 mm per template unit, the A4-portrait
  fit factor measured at 300 dpi); part geometry is placed FULL SIZE
  (1 unit = 1 mm) so DIM labels read true millimetres; the sheet is plotted
  `--scale num:den`. Result: template prints at constant size, part prints
  at exactly num:den, dims are truthful.
- Standard scales: 1:1, 1:2, 1:5, 1:10, 2:1, 5:1. The title block SCALE
  field must state the plotted scale exactly (export.py cross-checks
  mm_per_unit; qa-checker re-checks).
- If a part can't fit legibly: smaller standard scale, a BIGGER SHEET, or
  split across sheets — decided with the user, never by distorting the
  template.
- Template extents (units): (19.134764, 60.998651)–(1083.860831,
  1669.978313). Drawing area: x 58.545–1044.426, y 460.083–1516.995.
  Sheet window at scale num:den: (0,0)–(1064.726·t, 1608.980·t).
- PAPER PER SHEET (issue #10): a part entry or `assembly_sheet` may state
  `"paper": "A3L"` (or `{"size": "A3", "orientation": "landscape"}`);
  omitted = A4 portrait, so an existing manifest redraws unchanged. The
  sheet is then drawn on that paper's title block, named in the top-level
  `"templates": {"A3L": "/abs/path.musa"}` (the plain `"template"` stays
  the A4-portrait one). The whole set may MIX papers — one A3 assembly
  sheet among A4 part sheets — and pdfunite merges them.
  A paper template is the A4 one WIDENED, made by
  `tools/widen_template.py <src> <dst> --paper A3 --landscape`: same
  units, same row pitch, same text, same PRINTED size, with the trim
  frame stretched and the title block, parts table and revision table
  travelling with the right edge and the top. So S0 never changes, the
  title block prints identically on every sheet of a set, and all the
  extra millimetres go to the drawing area.
  musa_writer.template_geom MEASURES the widening from the file (bbox vs
  the default's) and picks the smallest paper it fits (paper_for) — a
  project states nothing but the paper it wants on the sheet. A template
  that is not such a variant fails there. common.PAPER_PAINTABLE holds
  what the harness can paint per paper, measured from musa_plot's own
  report at 300 dpi; SheetContext.fx moves a title-block x with the right
  edge; sheets.json records each sheet's paper, export.py plots and
  re-checks it, and judge.area_mm judges the fit on it.

## Drawing style rules (locked with user, from reference/0982_001-1.pdf)
- Per part: ONE sheet, TWO views. Front view (chosen per part at /finalise),
  fully dimensioned, upper band. ONE full section cut through the part
  middle: cutting-plane line (Center linetype) + solid arrowheads + "A"
  labels on the front view, ANSI31 hatch on cut faces, "SECTION A-A" label.
  Manifest `section.projection` ∈ {below, right, left} (default "below" =
  horizontal cut, reference style; right/left = vertical cutting plane
  projected to the side). Views stay feature-aligned on the shared axis;
  sight arrows are third-angle (away from the placed view) — flip in
  draw.py section_job + cutting_plane together if first-angle is requested.
  No extra views — features invisible in both views are flagged to the
  user, never silently added.
- DETAIL VIEWS (the one sanctioned third view): manifest part
  `"detail_view": {label, scale, of: "front"|"section"|"aux"|"flat",
  center: [x, y], radius}` — centre and radius are in the PARENT view's own
  coordinates. The region is CLIPPED out of that view's projected
  primitives (draw.clip_prims / clip_loop_to_circle for hatch), so the
  detail is the parent's own ink enlarged and the two can never disagree.
  It is drawn k = (detail scale)/(sheet scale) times full size, bounded by
  a thin circle, captioned "DETAIL B (2:1)", with the matching circle +
  letter on the parent view. Its dims are authored in the parent's true
  millimetres (draw.detail_specs moves them) and read true through
  DIMLFAC = 1/k. It is placed in free space clear of the views' dimension
  gutters, with a DETAIL_PAD_MM ring for its own dims, and is
  collision-checked like everything else. /finalise refuses a detail whose
  parent view is not on the sheet, whose scale is not standard or does not
  enlarge, or whose label collides with the section's.
- ANGULAR REFERENCES (for less technical vendors): manifest part "angle_refs"
  {"rays": [{angle, r0, r1, text}], "zero": {at, text}, "ccw": {r, a0, a1, at,
  lines}} draws a labelled centre line through each angularly placed feature,
  a "0%%d REF" datum label and a CCW direction arc (draw.py place_angle_refs,
  collision-checked against view ink + claims like every annotation).
- Per assembly: ONE sheet — front view, an optional SECTION, any number of
  DETAIL views, P.NO. balloons and the parts table (P.NO., DESCRIPTION,
  QTY, DRG NO., MATERIAL, SIZE, WEIGHT, REMARKS; "-" if unknown). All of it
  is manifest-stated under `assembly_sheet` and checked at the lock
  (manifest.assembly_sheet_problems), as a part sheet's decisions are:
  - `section` {enabled, projection, offset_frac, label, not_sectioned}.
    Each cut part is hatched SEPARATELY and parts that touch in the cut get
    a different hatch angle or spacing (ISO 128-50) — draw.py colours the
    adjacency graph of the cut areas, so it is about the drawing, not the
    part numbering. A cut region thinner than THIN_SOLID_MM printed is
    filled SOLID instead of hatched. `not_sectioned` lists the part ids a
    section must not cut along their length — shafts, rods, screws, nuts,
    pins, O-rings — resolved to instance paths and passed to fc_project as
    `section_exempt`, which leaves those parts whole inside the cut.
  - `detail_views`: [{label, scale, of, center, radius, balloons}] — the
    part sheets' detail machinery (DIMLFAC and all), several per sheet,
    each with its circle and letter on the parent view. An assembly detail
    reserves only DETAIL_ASSEMBLY_PAD_MM of clearance: it carries no
    dimensions of its own, only balloons.
  - BALLOONS: ONE per P.NO. on the whole sheet, never one per instance —
    a part with qty 4 is one balloon. draw.balloon_plan picks the view
    that shows it best (manifest `balloon_in`, else the detail that lists
    it, else the section, else the front view) and every view's balloons
    share ONE knee column, so the leaders cannot cross between views. A
    P.NO. no view shows fails the sheet: the table would claim a part the
    drawing does not point at.
    TWO COLUMNS when one cannot hold them: 26 numbers need about 360 mm
    of column and the A3-landscape drawing area is 190 mm, so the fan
    splits, one column each side of the view (draw.balloon_sides). Each
    balloon goes to the side its own ARROW is nearer — probed through
    balloon_landing itself — and the ones nearest the centre line move
    over when a side is full (_rebalance), because a part out at the edge
    cannot change sides without its leader crossing the view. A split fan
    starts at the top of the drawing area rather than at the view, packs
    at the dense pitch, FIXES each landing where the fan was ordered from,
    and is ordered by the angle each landing subtends at the fan's middle,
    then settled by exchanging any neighbours that would still cross
    (draw._uncross). The parts table gives way to it — the rows it cannot
    keep go to the continuation sheet, which is reported — because a
    missing balloon fails the sheet while a table row has somewhere else
    to go. If two columns still cannot hold them, the sheet says so with
    the counts.
  - `parts_table` {group_by: none|sub_assembly, group_hardware, total}.
    Grouped by sub-assembly it uses the v0.5.0 tree: a heading row per
    sub-assembly carrying its own quantity, then its parts, then a TOTAL
    row. If the rows do not fit TABLE_AREA_FRAC of the drawing area the
    fallbacks run IN ORDER — group bought-out hardware into one row per
    kind, then move the rest to a continuation sheet (same DRG NO.,
    "SHEET 2 OF 2") — and the one actually used is REPORTED, in the /draw
    log and in sheets.json `assembly_report`. Text is never shrunk below
    musa_writer.PARTS_TEXT_FLOOR; a cell that cannot fit fails the sheet.
    QUANTITIES come from `part_usage`, ONCE (issue #9): one row per P.NO.
    whatever the grouping, its QTY the part's own usage count
    (draw.group_usage counts instance PATHS, so four O-rings in one
    sub-assembly are one row of four, not four rows); a heading row states
    the SUB-ASSEMBLY's own quantity (draw.sub_assembly_qty) and carries no
    item number; a part used under more than one sub-assembly is listed
    once under COMMON_GROUP and a top-level part under TOP_GROUP, so no
    row is ever counted twice; a hardware group row lists each P.NO. once
    and only exists for two or more of a kind; and TOTAL is the sum of the
    rows. manifest.quantity_problems fails the lock when a manifest qty
    has drifted from the model's.
  - Defaults are the old sheet exactly: no section, no details, an
    ungrouped table, so an existing assembly sheet redraws unchanged.
- THREADS: model the REAL helix in the .FCStd (PartDesign::SubtractiveHelix,
  ISO 60%%d V truncated to d3 = d - 1.2269*P) so the model and the shaded
  pictorial are a true twin — but NEVER project it: a 20+ turn helix
  scribbles the view. Orthographic views project the thread BLANK via
  manifest part `"project_feature": "<feature name>"` (fc_project walks back
  to that feature; assembly links follow the same map), and the thread is
  drawn by ISO 6410 convention from manifest `"threads"` — a root line at
  the minor dia over the threaded span, `end_line` only where no diameter
  step already draws that edge. Section hatch runs out to the crest, so the
  root lines simply overlay it. At M2.5/1:1 the 0.24 mm crest-root gap is
  under the 0.30 mm stroke floor and prints as one heavier band: normal for
  small threads — the DESIGNATION carries the spec, not the line-work.
  A TAPPED HOLE is the other way round (`"kind": "internal"`, issue #12):
  it is modelled as its tap drill, so the solid's own edge is the MINOR
  dia and what has to be drawn is the MAJOR one — a thin continuous line
  each side over the threaded span, the thread-end line where the usable
  thread stops (short of the drill depth; on by default, since nothing
  else marks it), and, in a view that shows the hole as a circle, a
  three-quarter thin arc at the major dia open in the upper-right quadrant
  (manifest `"circles": [{"view", "center"}]`). The hatch stops at the
  drill wall, where the solid ends, and the thread lines overlay it. The
  lock checks the thread is wider than its tap drill and its span is a
  real length inside the part (manifest.thread_problems).
- TABULATED DRAWINGS (issue #11): a family that differs in ONE size is
  drawn once. Manifest part `"variants"` {letter, title, dim {view,
  orient, a, b, side}, columns, rows}: the varying size is dimensioned
  with the LETTER and the table gives its value per variant, with each
  one's mark and quantity. A DIM always prints the distance between its
  own def points — MusaCAD composes prefix + measurement + suffix, and
  nothing replaces the middle — so draw.place_letter_dim draws the
  dimension itself (the same extension lines, dimension line and filled
  arrowheads, dim_arrowhead matching core/dimension.cpp's own
  proportions) with the letter where the value goes, placed and
  collision-checked through the same ladder as any location dim. The
  table goes in free space like the bend table (both through
  draw.place_table) and fails the sheet rather than shrinking below the
  legibility floor. The lock checks the table has a column for the letter
  and every row is filled (manifest.variants_problems).
- BOUGHT-OUT items (standard fasteners etc.): manifest part `"sheet": false`
  — the part keeps its P.NO. row and assembly balloon but gets no sheet of
  its own, and its DRG NO. carries the standard (e.g. "IS 1364-3"). Nobody
  fabricates it, so nothing is drawn for it. WEIGHT falls back to grams
  below 0.01 kg (small hardware would otherwise print "0.00 kg").
- SHEET METAL and non-PartDesign parts: a part document's part is its TIP
  solid (fc_shapes.tips — PartDesign bodies, SheetMetal feature chains and
  plain Part::Feature solids all qualify; containers and origins never do).
  A `*_Flat` twin marks sheet metal: the vendor cuts a blank and bends it,
  so the part gets a flat-pattern view (developed blank + bend table,
  draw.py bend_rows) from manifest `"flat_pattern"` {object, view,
  thickness_mm, blank_mm, bends}. A flat pattern with no bend features is
  rejected at /finalise — a blank with no bend table is just a flat part,
  which takes a plain two-view sheet.
- STANDALONE PARTS: `/analyse <dir> --no-assembly` (and `--parts <files>`)
  analyses a part pack with no assembly document; the analysis carries an
  empty assembly stub and /judge skips every assembly-level check.
- NESTED ASSEMBLIES in the report: `assembly.instances` is a TREE (each
  node: name, label, path, kind part|assembly, target_file, children, with
  GLOBAL position/bbox), `assembly.part_usage` maps each real part file to
  {qty, used_in: [paths]} — one part analysed once however many instances
  use it, counted per instance — and `assembly.sub_assembly_files` lists
  the sub-assembly documents. /finalise takes each part's QTY from
  part_usage, so the parts table counts correctly across sub-assemblies.
  /judge joins an instance if a live joint names its path or anything
  INSIDE it (a joint on a part of a rigid sub-assembly locates that
  sub-assembly), never by what a sibling or a parent carries, so a
  genuinely unjoined part one level down is still reported — by path.
- Round 0 output: 4 part sheets + 1 assembly sheet + merged print set.
- COMPANY TITLE BLOCKS ARE PROJECT FILES, never pipeline files: the
  pipeline is a tool and holds no project or company information. A
  project keeps its title block in its own folder (e.g.
  <project>/fabrication/template/template_<company>.musa) and the manifest
  names it by absolute path: top-level `"template": "/abs/path.musa"`
  (omitted = the pipeline's default templates/template.musa; a bare name
  resolves only inside templates/). A variant keeps template.musa's
  geometry and extents exactly (the scale model rests on them) and changes
  only its text, the vector mark in the logo box (x 800.9–1044.4,
  y 335–430 template units) and an optional raster logo: sidecar
  `<stem>.logo.json` next to it, {"png": path relative to the template's
  folder, "box": [x0, y0, x1, y1] template units}. manifest.py lock checks
  the template parses and its logo resolves; draw.py writes each sheet's
  "logo" stamp (SheetContext.logo_stamp: box scaled by t, letterboxed to
  the PNG's aspect, so it prints the same size at every sheet scale) and
  export.py hands it to musa_plot as a second --stamp after the pictorial.
- Title block per sheet: DESCRIPTION, DRG NO., SCALE, REV (00), SHEET
  field, dates DD-MM-YY, DESIGNED/DRAWN = "Er. P. PRANAY" (template
  default). General notes/tolerance block (IS 2102) stays as in the
  template.
- SHEET numbering: each DRG NO. is an independent document → "SHEET 1 OF
  1". Revisions are PER SHEET: part-level "rev" + "revisions" (up to 3
  rows) override the manifest-level defaults; dia of round features uses a
  circumference LEADER with %%c (vendor style), not a linear width dim. "i OF n" ONLY when one part splits across sheets under a single DRG
  NO. (manifest "sheet_of": [i, n]). NEVER numbered across the print set.
- Front-view choice, section positions, DRG NO. numbering, GD&T callouts,
  and fabrication notes are per-part USER DECISIONS at /finalise, elicited
  ONE question at a time conversationally (never batch-blocked) —
  src/manifest.py only proposes defaults.
- THE VALUE CHECK (issue #14, src/verify.py, run inside /draw): what the
  manifest STATES must be what the sheet PRINTS. The layout engine only
  guarantees that what it places does not overlap and the qa-checker reads
  the PDF against the template rules, so neither caught a dropped
  tolerance or a model coordinate printed as a dimension — while the
  report said "every number kept". stated = callout and note text, GD&T
  tolerances and datum letters, thread designations, the variants table,
  the title block, and the VALUE each location dim must measure; printed =
  every MTEXT/TEXT/LEADER content in the .musa plus every DIM label as
  MusaCAD composes it (dump_dims, so DIMLFAC and precision are applied).
  Four kinds of finding — missing, changed (it names the words that are
  not there: "the sheet is missing: f7"), extra (a printed VALUE the
  manifest does not account for; the pipeline's own furniture and the
  template's text are known) and impossible (a dim bigger than the part,
  which is what a model coordinate looks like). Wrapped text still matches
  because the sheet is read as one running string both ways round (the
  notes block draws bottom-up). /draw prints and records them; /export
  REFUSES to run on any that is not waived by an explicit manifest
  `"value_waivers"` entry.
- SIDE-BY-SIDE SECTIONS (issue #13): with `projection: right|left` and
  leaders on BOTH views, each view gets its OWN callout column — the
  inner view's in the inter-view gap, the outer view's outside the group
  — each sized to its own callouts (annotation_band_width(views=...)),
  both reserved by place_views (`band_gap_w` + `band_w`) and the group
  centred with both. The inner column wraps to the gap, not to the sheet
  edge (place_callouts `limits`). When the columns genuinely do not fit,
  the failure names THEM and their widths: the views fit, and the fix is
  a shorter callout, the other view, or a bigger sheet — not the scale,
  which is what the old message said.
- TEMPLATE INTEGRITY (issue #15): a title block's own elements have to be
  on the sheet. mw.template_elements_off_sheet checks every field and the
  logo box against the template's extents, AND where the logo really
  stamps — on a widened paper the box travels with the right edge
  (SheetContext.fx), so a box already moved by the widening is moved
  twice and lands off the paper. tools/widen_template.py therefore copies
  the sidecar box UNMOVED, in the A4 template's own units. A field whose
  text is wider than the box the template drew for it is recorded per
  sheet (SheetContext.field_overflow) and /export refuses it, as it
  refuses a stamp that lands outside the sheet window.
- Layout quality (hard, enforced in draw.py + qa-checker): view group
  centered horizontally and vertically balanced in the drawing area; ZERO
  overlaps (dim text vs geometry, dim vs dim, leaders vs views) and ZERO
  intrusion into title block / parts table / revision table / border —
  bounding-box collision checks with standard-practice auto-nudge; if a
  collision is unresolvable, fail the sheet loudly, never ship it.

## Pipeline stages
| Stage | Command | Script | Output |
|---|---|---|---|
| 1 analyse | /analyse <dir> | src/analyse.py (+fc_analyse.py in FreeCAD) | reports/analysis.json+.md |
| 2 judge | /judge | src/judge.py | reports/judgement.json+.md |
| 3 finalise | /finalise | src/manifest.py propose/lock (interactive) | manifests/manifest.json (locked) |
| 4 draw | /draw | src/draw.py (+fc_project.py in FreeCAD) | output/musa/*.musa + sheets.json |
| 4b values | (inside /draw) | src/verify.py | sheets.json "values" per sheet |
| 5 export | /export | src/export.py → tools/bin/musa_plot, pdfunite | output/pdf/*.pdf + print_set.pdf |
| all | /pipeline <dir> | stages 1–5 with MANDATORY pause after /judge | — |

Subagents: model-analyst (FreeCAD interrogation), draftsman (.musa
composition/rework), qa-checker (verify PDFs after every /export).

## Ground rules
- READ-ONLY scope (precise): existing MODEL documents under
  /home/pranay/projects/lisocl2/extrusion_jig_x3/ and the musa_cad source
  tree — never modify them; never doc.save() an existing model. SANCTIONED
  writes inside lisocl2/ (user-directed): NEW part files we create (e.g.
  spacer_5mm/spacer_10mm/press_tool, ours to regenerate) and the
  per-project delivery folder (e.g. lisocl2/fabrication/).
- /judge reports model issues; the user fixes models in FreeCAD themself.
- THIN FEATURES are judged at the scale the SHEET is drawn at (issue #4):
  /judge uses the manifest's scale for that part (a locked manifest first,
  then a draft), and its own suggestion only when no manifest exists;
  manifest.py lock re-runs the same check (judge.thin_feature_finding), so
  a scale chosen at /finalise that is still too thin does not lock without
  a waiver, and one raised far enough needs no waiver at all. A part with
  `sheet: false` is judged at the scale of the sheet that DOES draw it —
  the sibling sheet whose DRG NO. it carries, e.g. a kit disc on the kit's
  2:1 sheet — and one no sheet draws at all (a bought-out item) is not
  judged for legibility: nothing is drawn, so no scale could clear it.
  judge.sheet_scales/not_drawn decide both, and the lock calls the same
  two, so /judge and /finalise never disagree about a part's scale
  (issue #8).
- Out of scope (Round 0): 3D-print workflows, CAM/G-code, BOM costing,
  other model folders. GD&T IS in scope (amended at /finalise, Round 0):
  drawings must be vendor-publishable — measurable datum labels + feature
  control frames (flatness / position / perpendicularity) approved per part
  during /finalise elicitation, plus per-sheet fabrication notes
  (deburr/chamfer, N-grade finish, treatments, process notes). No
  decorative GD&T; reference-drawing conventions are the defaults.
  SYMBOLS: flatness, position, perpendicularity, coaxiality (concentric
  circles; its zone is a cylinder, so the tolerance carries %%c) and
  circular runout (oblique arrow) — musa_writer._FCF_SYMBOLS.
  DATUMS: a target naming a view edge (bottom_face / short_edge /
  long_edge) marks a SURFACE. A datum on a FEATURE OF SIZE — a bore or a
  spigot, whose datum is its AXIS — uses `"target": "<kind>_r<radius>"`
  (e.g. `bore_r12.5`, radius always in TRUE mm, in any view) and the
  triangle goes on that feature's own size annotation: the shoulder of its
  callout leader (ISO 1101 / ASME Y14.5), or in line with its diameter
  dimension line when it is dimensioned that way. No size annotation for
  that feature = the sheet fails; a datum triangle on an undimensioned
  feature is not a datum.
  TWO FEATURES OF ONE SIZE (issue #7): size alone does not name a feature
  on a tube bored the same at both ends, so a target may carry a SELECTOR
  — `bore_r35.75@tail` (the callout whose manifest `"id"` is `tail`) or
  `bore_r35.75@x280` / `@y-30` (the feature's coordinate in the view's own
  axes, written as a callout's `at` is). A target that still fits two
  annotations FAILS, at the lock (manifest.ambiguity_problem) and at
  /draw (draw.resolve_feature_anchor): attaching to whichever callout came
  last is a sheet that says something the designer did not.
  A FRAME IS ITS FEATURE'S OR IT IS NOTHING (issue #7): a stack is emitted
  only when it TOUCHES its own size annotation — directly under or over
  it, with no other annotation in the gap (draw._frame_attached) — or
  carries a leader to it. Otherwise the sheet fails with the reason; no
  frame ink is written for a failure. When a crowded callout column leaves
  no room, draw.gdt_reserve re-composes the sheet with that callout's slot
  reserved (claims of kind "slot", which only the frame and datum hung on
  it may use, released once they are placed); the datum's own last resort
  is the far end of its landing — ISO 1101 5.3's extension of the
  dimension line.
  EDGE-ON FEATURES (issue #6): a turned part drawn as a side view and a
  section never shows its spigot or bore as a CIRCLE — they are pairs of
  lines — and that is fine. The datum needs the feature's SIZE dimension,
  not a circle: when a callout carries that size (an `at`-anchored one
  still carries `circle_r`), the triangle goes on its landing. A circle is
  used only for the in-line-with-the-diameter-dimension case. When neither
  is on the sheet the failure names the view and says to add a %%c callout
  there or a view showing the feature as a circle.
  FRAMES ON A PLANE FACE: a face is drawn EDGE-ON, so a perpendicularity /
  flatness / parallelism frame attaches to that line with a leader and an
  arrowhead on it (ISO 1101 5.2). Target `face_x<coord>` / `face_y<coord>`
  (the view's own axes) or a named view edge (bottom_face / top_face /
  left_edge / right_edge). One face can be drawn as SEVERAL segments — a
  shoulder on a turned part is one short line each side of the axis — so
  draw.face_lines returns them all and place_frame_near_face takes the one
  it can reach by the shortest clear route, never routing through the
  section hatch while a clear route exists (draw.hatch_polys /
  crosses_polys; issue #7). It puts such a frame BESIDE the view, square
  to the face, so the leader is short — parking it in the annotation band
  means a leader across the view and through the callouts. A frame that
  can reach nothing in its view FAILS loudly: an unleadered frame points
  at no feature at all. /finalise checks every frame target's form and
  that each frame's datum letters are datums the sheet carries
  (manifest.gdt_problems).
- reports/, manifests/, output/ are generated artifacts (gitignored).
- TESTS: `python3 -m unittest discover -s tests -v` (stdlib unittest, no
  venv). tests/make_fixtures.py builds throwaway FreeCAD assemblies in a
  temp dir with the real Assembly workbench; FreeCAD-dependent tests skip
  themselves when the AppImage is absent. The suite never writes to
  reports/, manifests/ or output/, so it is safe mid-project. Add tests
  with every fix.
- Python 3.12 stdlib only (no venv); pdfunite/pdfinfo/pdftoppm (poppler)
  for PDF ops; Qt 6.4.2 system-wide for the harness build.
