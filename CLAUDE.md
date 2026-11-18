# Fabrication Pipeline — Project Memory

FreeCAD assembly/part models → analysis → judgement → locked manifest →
MusaCAD (.musa) fabrication sheets → A4 portrait PDFs. Company: Renewable
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
- JOINT REFERENCES come in two layouts and both must be read (issue #1):
  1.1's Assembly workbench writes `Reference1 = (AssemblyObject,
  ["LinkName.Vertex1", ...])` — the instance is the HEAD of the subname
  path and the joint's `OutList` holds only the Assembly, so an OutList
  scan finds nothing; 1.0 and older write `(link, ["Vertex1", ...])`,
  where the referenced object IS the instance. GroundedJoint carries
  `ObjectToGround`. fc_analyse.ref_targets/joint_connects handle all
  three, and joints also report `suppressed` (a suppressed joint is in the
  tree but constrains nothing, so /judge does not count it as joining).
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
- Parser is STRICT: exact token counts; TEXT/MTEXT need content + font lines
  (font line mandatory in v14, empty = stroke font); file starts
  `MUSACAD 14`, ends `END`; unknown record key kills the whole file.
- DIM entities always display the measured def-point distance (cannot fake).
- Angles in radians. `%%p` ±, `%%c` ⌀, `%%d` °. ANSI31 = section hatch.
- Always use src/musa_writer.py helpers, never hand-format records.

## Scale model (THE core invariant — hard rules)
- Template paper size (A4 portrait) and aspect ratio are INVIOLABLE.
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
- If a part can't fit legibly: smaller standard scale or split across
  sheets — decided with the user, never by distorting the template.
- Template extents (units): (19.134764, 60.998651)–(1083.860831,
  1669.978313). Drawing area: x 58.545–1044.426, y 460.083–1516.995.
  Sheet window at scale num:den: (0,0)–(1064.726·t, 1608.980·t).

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
- ANGULAR REFERENCES (for less technical vendors): manifest part "angle_refs"
  {"rays": [{angle, r0, r1, text}], "zero": {at, text}, "ccw": {r, a0, a1, at,
  lines}} draws a labelled centre line through each angularly placed feature,
  a "0%%d REF" datum label and a CCW direction arc (draw.py place_angle_refs,
  collision-checked against view ink + claims like every annotation).
- Per assembly: ONE sheet — front view (section only if needed), P.NO.
  balloon leaders per instance, parts table filled (P.NO., DESCRIPTION,
  QTY, DRG NO., MATERIAL, SIZE, WEIGHT, REMARKS; "-" if unknown).
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
- Round 0 output: 4 part sheets + 1 assembly sheet + merged print set.
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
- Out of scope (Round 0): 3D-print workflows, CAM/G-code, BOM costing,
  other model folders. GD&T IS in scope (amended at /finalise, Round 0):
  drawings must be vendor-publishable — measurable datum labels + feature
  control frames (flatness / position / perpendicularity) approved per part
  during /finalise elicitation, plus per-sheet fabrication notes
  (deburr/chamfer, N-grade finish, treatments, process notes). No
  decorative GD&T; reference-drawing conventions are the defaults.
- reports/, manifests/, output/ are generated artifacts (gitignored).
- TESTS: `python3 -m unittest discover -s tests -v` (stdlib unittest, no
  venv). tests/make_fixtures.py builds throwaway FreeCAD assemblies in a
  temp dir with the real Assembly workbench; FreeCAD-dependent tests skip
  themselves when the AppImage is absent. The suite never writes to
  reports/, manifests/ or output/, so it is safe mid-project. Add tests
  with every fix.
- Python 3.12 stdlib only (no venv); pdfunite/pdfinfo/pdftoppm (poppler)
  for PDF ops; Qt 6.4.2 system-wide for the harness build.
