# Changelog

All notable changes to this project. Versions follow
[semantic versioning](https://semver.org/) loosely: the minor number moves
when the drawing output or the manifest format gains something, the patch
number for fixes.

## v0.14.0 — 2026-11-25

The empty half of a widened sheet:
[#28](https://github.com/MusaCAD/fabrication_pipeline/issues/28).

### Fixed

- **A widened title block was stretched, not moved** (#28). The revision
  table, the parts row and the title block run from border to border on
  the A4 sheet, and `tools/widen_template.py` moved only the ends at or
  right of `LEFT_EDGE_X`. So each of those rules was stretched by the
  whole widening — 1228 template units, 221 printed mm on A3 landscape —
  and every A3 sheet went out with a row of empty cells across the top
  and an empty box the size of a postcard beside the title block. They
  hold nothing, they read as part of the title block, and they took a
  band 221 mm wide at the top and the bottom of the sheet that the
  drawing could have used. Both ends of those rules travel now and each
  band is CLOSED on its new left edge (`furniture_band` /
  `closing_rules`): the border runs through both bands and a corner mark
  lies outside it, so neither is furniture and neither moves, and a band
  that already carries its own left-hand side — the revision table does
  — is not given a second one.

### Added

- **The room the furniture leaves is drawing area** (#28).
  `common.sheet_geom` now returns `sheet_area` — the whole sheet inside
  the border — and `blocks`, the title block and the revision table
  reserved in it, beside the `drawing_area` it always returned, which
  stays the band between them. On A3 landscape that is a strip 221 mm
  wide over the full height of the sheet, 25% more drawing area, and it
  is beside the title block where a notes block or a table belongs.
  - The blocks are MEASURED off the template
    (`musa_writer.template_furniture`), never assumed: a variant widened
    before this fix has its rules stretched to the border, so its block
    spans the sheet, is absorbed into the area's own edge, and the sheet
    is the plain rect it always was. An A4 sheet likewise.
  - `draw.Layout` takes `sheet_area` as its area, claims each block and
    refuses any rect that overlaps one. So an annotation may use the
    freed band, and nothing may land on the title block.
  - `ctx.areas` is the band first and the whole sheet second, and that is
    the order every free-space search follows (`_free_spot`,
    `_lowest_free`, `place_notes`, `pictorial_box`). A sheet that fits as
    it always did is drawn as it always was; the new room is for the one
    that does not.
  - The views, the parts rows and the balloon columns stay in the band
    between the furniture. A view group centred in an L-shaped area has
    no meaning, and the strip adds height beside the title block, not
    width beside the drawing.
- **The layout check knows the template's own furniture** (#28): the
  title block's and the revision table's rules and text are furniture
  rather than the drawing's ink, now that they are inside the area, and
  an annotation that lands on them is reported as `text-off-sheet` —
  which the old area test used to catch for free.
- **A logo box belongs in the title block**:
  `template_elements_off_sheet` measures it against that block, because
  one in the drawing area would print under the views.

### Upgrade steps

1. `git -C <your pipeline clone> fetch --tags && git checkout v0.14.0`
2. **Nothing changes until you regenerate your paper templates.** A
   template widened by an older version keeps the sheet it had, and every
   sheet of every set redraws byte for byte.
3. To take the fix, regenerate each variant from your A4 title block and
   redraw:
   `python3 tools/widen_template.py <project>/fabrication/template/template_<co>.musa <same>_a3l.musa --paper A3 --landscape`
   then `/draw` and `/export`. The title block and the revision table
   come out at A4 width, closed on the left, and the drawing itself does
   not move — on the sslxbt set the three A3-landscape part sheets change
   in nothing but the template's own rules.
4. A sheet that was failing for want of room — a notes block or a bend
   table that would not fit — may now place it beside the title block.
   That is the new band being used; the layout check reads it as drawing
   area like any other.

### Compatibility

Every sheet of the four real sets — eleven sslxbt part sheets, the TPE
assembly and its parts, amogha RES-41-LH-01 and
`samples/out/RES-SAMPLE-01` — redraws byte for byte, the three A3
sheets among them included, because their template was widened by the
old script. Regenerated, those three change in their title block's own
rules and in nothing else: every dimension, note, leader and view line
is identical. The suite is 562 tests.

## v0.13.0 — 2026-11-25

Four faults a finished sheet carried and nothing asked about:
[#24](https://github.com/MusaCAD/fabrication_pipeline/issues/24),
[#25](https://github.com/MusaCAD/fabrication_pipeline/issues/25),
[#26](https://github.com/MusaCAD/fabrication_pipeline/issues/26) and
[#27](https://github.com/MusaCAD/fabrication_pipeline/issues/27).

### Fixed

- **/export plotted sheet 2 over sheet 1** (#24). A drawing that runs to
  two sheets — a continuation sheet for the parts table, or details on
  `"sheet": 2` — has two entries in sheets.json under ONE drawing number,
  and each was named after the drawing, so the second plot overwrote the
  first and the print set was built from that list of paths: it carried
  SHEET 2 OF 2 twice and SHEET 1 OF 2 not at all. The page count was
  right and every check before the plot passed, because each sheet's
  .musa was correct; only the PDF written for it was lost. Each sheet now
  gets its own file — `<DRG>_SH1.pdf`, `<DRG>_SH2.pdf`
  (`export.sheet_pdf_name`, by the sheet's own `sheet_of` when it states
  one, else by the order they were drawn) — and the drawing's `<DRG>.pdf`
  is those merged in sheet order. The print set carries every SHEET once;
  delivery takes the DRAWINGS and the print set, not the loose sheets. A
  one-sheet drawing keeps its plain `<DRG>.pdf`, byte for byte.
- **A tie rounded one way on A4 and the other way on A3** (#26). A
  362.625 mm tube printed `362.63` on A4 portrait and `362.62` on A3
  landscape, from one model and one manifest, and `--dump-dims` measured
  362.625 both times. Def points are placed full size at offsets that
  differ per paper, so the last bits of the subtraction land either side
  of the rounding boundary depending on where the view sits — and glibc's
  `%.2f` rounds an exact tie half-to-EVEN, which is neither of the two
  rules a drawing office uses. `draw.snap_value` / `snap_def_points` now
  move the far def point by a micrometre on a TIE ONLY, so the
  measurement is a hair past the boundary and rounds half AWAY FROM ZERO
  wherever the sheet puts it. A detail view's DIMLFAC is applied before
  the test, so what is snapped is the value the reader sees. Every other
  dimension is untouched, down to the byte: in the four real sets exactly
  two def points move, both on a tie, and both labels are the ones those
  sheets already printed on A4 — it is the A3 redraw that used to
  disagree.
- **A datum box sat beside the wrong callout** (#27). On a turned part at
  1:1 the triangle was on its own callout's leader, but the BOX one line
  below — 1.4 mm from `2 x M4 THRU WALL, 180%%d APART` and 4.0 mm from
  its own `%%c46.7 (SPIGOT)`. A box reads as belonging to the nearest
  label, so as printed the sheet named the M4 holes as datum A. A spot is
  legal now only when the datum's OWN callout is the nearest text to it
  (`datum_on_landing`'s `mine`, measuring with the new `rect_gap`
  against `Layout.anno_clearance(ignore=stack)`), and when no spot is,
  the sheet fails and says why — a datum on the wrong feature is worse
  than no datum at all.
- **A leader could run through the sheet's other ink** (#25). The engine
  knew about views, leaders and text boxes; a dimension LINE, the cutting
  plane's sight arrows and the pipeline's own pictorial frame were not
  claims, so nothing was ever asked about them — and a diameter leader
  left its circle, turned, and ran straight through a sight arrow, two
  arrowheads meeting so that neither reads as pointing at anything.
  `Layout.dim_lines` / `ext_lines` (recorded by `_claim_dim_ink` from
  `dim_segments`, which is MusaCAD's own dimension geometry) and
  `Layout.keep_off` (the sight arrows, the pictorial box) carry that ink
  as SEGMENTS; `Layout.crossing_ink` names what a rung of the ladder
  would cross, and `crosses_extension` asks about the extension lines,
  which only rank a rung — a crowded sheet sometimes has no way round
  one. Balloons are ORDERED against that ink rather than stepped round it
  (`draw.other_ink` + `_order_against_ink`): out of its fan slot a
  balloon crosses its neighbours, which says something false about the
  part, so an ink crossing costs 1 and a leader crossing 10.

### Added

- **The layout check reads all four** (#25, #27), off the finished .musa
  as the rest of it does:
  - `leader-across-dimension` — a leader across a dimension line, a
    sight arrow or the pictorial box. `dim_ink` rebuilds the dimension
    line and its two extension lines from the DIM records (the feet are
    the def points projected onto the dim line, as `core/dimension.cpp`
    computes them), and `cut_arrows` reads the filled triangle that is in
    the DRAWING's ink rather than the annotation's, with the shaft behind
    it;
  - `leader-across-extension-line` — a WARNING, for the same reason the
    engine only ranks it;
  - `callout-column-narrow` — a note coming out one word a line, which
    says the column beside the views is narrower than the note's own
    longest word and its leader has to cross the view to reach it. The
    fix is a shorter note or a bigger sheet, not another knee, so the
    fault names the words. A column is only read as a callout when it
    hangs off a LEADER's knee: a variants table's cells line up the same
    way and are nobody's note;
  - `datum-beside-wrong-callout` — a box nearer another callout's text
    than its own, with the text it would be read against. `datum_boxes`
    takes the triangle's tip to be the vertex FARTHEST from the box,
    which is the end that touches the feature.
- A crossing leader is reported per PAIR now, with coordinates when a
  leader has no label: every balloon's leader carries the same empty
  label, so a fan of sixteen crossing pairs used to report as one.

### Upgrade steps

1. `git -C <your pipeline clone> fetch --tags && git checkout v0.13.0`
2. **Re-run `/draw`, then `/export`.** Nothing in the manifest format
   changed; all four fixes are in the engine, the checks and the plot.
3. **Expect new layout findings on an assembly sheet that carries both
   dimensions and a big balloon fan.** They are real: a balloon leader
   really does cross the overall-length dimension when the fan spans the
   sheet above the view. The sheet's options are a shorter dimension set
   (`assembly_sheet.front_extent_dims: false` — the parts table carries
   the sizes), a bigger paper, or a `layout_waivers` entry naming the
   finding. The engine orders the fan to take as few as it can; it will
   not trade a leader crossing for one of these.
4. **Check the file names your delivery folder gets** (#24): a two-sheet
   drawing now delivers one `<DRG>.pdf` of two pages, and
   `<DRG>_SH1.pdf` / `<DRG>_SH2.pdf` stay in `output/pdf/` as the working
   copies. An older run's single-page `<DRG>.pdf` in the delivery folder
   is overwritten by the two-page one.
5. **Nothing to re-issue for #26.** Two def points move by a micrometre
   in the four real sets, and both dimensions print the label they
   printed before — they were already rounding away from zero on A4
   portrait. What changes is that they do so on A3 as well, where one of
   them came out a hundredth lower.

### Compatibility

Every sheet of the four real sets — eleven sslxbt part sheets, the TPE
assembly and its parts, amogha RES-41-LH-01 and
`samples/out/RES-SAMPLE-01` — redraws byte for byte except the four that
carried a fault: two sslxbt part sheets whose length was exactly on the
rounding boundary (#26), one whose datum box moves to its own callout
(#27), and one whose diameter leader now goes round the sight arrow
instead of through it (#25). The suite is 524 tests.

## v0.12.0 — 2026-11-24

What the checks were still missing, and what a model file shows when you
open it:
[#20](https://github.com/MusaCAD/fabrication_pipeline/issues/20),
[#21](https://github.com/MusaCAD/fabrication_pipeline/issues/21) and
[#23](https://github.com/MusaCAD/fabrication_pipeline/issues/23).

### Fixed

- **The value check read the parts table as unexplained values** (#21).
  `stated_assembly_values` counted only each part's description and
  drawing number as stated, so the MATERIAL cells — `SS 316`, `NBR 70`,
  `PA66`: numbers in their names — came out as `EXTRA`, along with the
  continuation sheet's own `PARTS TABLE CONTINUED FROM SHEET 1.` The only
  way through was a waiver for the drawing's own words. The table is now
  read AS PLANNED, cell by cell (`draw.parts_row_cells`, carried in the
  sheet's meta), which is what the sheet really prints: grouping
  bought-out hardware replaces several parts' rows with one of its own
  and a continuation sheet carries the rest. Material, size, remarks and
  the quantity from `part_usage` are stated values like the rest, so a
  material the table does NOT print now reads as MISSING, and the
  continuation note is furniture. On the real two-sheet assembly the
  value check goes from eleven findings to none.
- **A balloon leader could cross another view to reach its part** (#20).
  A split fan starts at the top of the drawing area and runs down past
  the section, so the balloons at the bottom of the column reached their
  parts in the FRONT view straight across the section's outline and
  hatch — pointing at a part through a view that says nothing about it.
  `_uncross` kept the leaders off each other; nothing kept them off
  another view. Every knee is now tested against the other views' ink
  (`draw.crosses_other_view`), and a balloon the section was in the way
  of is RE-PLANNED into the section when the section shows that part —
  the whole fan is then placed again, because every knee below it moves
  up. A `balloon_in` the manifest states is kept whatever it costs, and
  the layout check reports what is left. On the real 26-balloon sheet
  five part numbers move into the section, every number is still
  ballooned, and no leader crosses a view again.
- **Two details on a continuation sheet sat side by side**, sharing one
  balloon column, so every balloon of the far one reached it across the
  near one. They stack one above the other now, and the sheet's own
  balloons are kept off the detail outlines as the part sheets' leaders
  are.

### Added

- **The layout check reads leader-versus-view** (#20): `leader-across-view`
  reports a leader that reaches its feature through ink that is not its
  own view's. The views are found by clustering the sheet's own geometry
  — a sheet does not say which ink is which view and does not have to,
  since the views are drawn at least MIN_VIEW_GAP apart — and the
  parts-table band and the pictorial box are the pipeline's furniture
  rather than the drawing's ink.
- **/analyse reports what each FILE shows when it is opened** (#23), and
  /judge fails one that shows nothing. A model built by script can be
  saved with everything hidden: the geometry is all there, every check
  passes, and the only hint that something is wrong is an empty 3D view —
  the user has to expand each body to its last feature and unhide it by
  hand before they can see the part. `analysis["visibility"]` now carries
  one entry per file:
  - a PART file: each Body visible, and the Tip feature it ends on (the
    features and sketches behind the tip are hidden in the ordinary way
    and are not a finding);
  - an ASSEMBLY or sub-assembly: every instance link visible, with each
    sub-assembly read in its own document too — that is how its parts are
    seen when somebody opens that file.

  `Visibility` is an App property, so it reads the same headless as it
  does in the GUI, and `fc_tree` records it per instance. A file that
  opens with NOTHING visible is a FAIL at /judge; anything else hidden is
  a WARN, since the rest of the file is there to look at.

### Upgrade steps

1. `git -C <your pipeline clone> fetch --tags && git checkout v0.12.0`
2. **Re-run `/analyse` before `/judge`**: the visibility report comes from
   the FreeCAD side, so a judgement run against an older analysis.json
   simply says nothing about it (no false findings, no check either).
3. **Expect the visibility FAIL on script-built models.** A body whose
   tip feature is hidden shows nothing: unhide it (select in the tree,
   Space) and save, or set `Visibility` on the body and its tip in the
   generator before it saves.
4. **Drop any `value_waivers` you added for parts-table materials or the
   continuation note** (#21) — they are stated values and furniture now,
   and a stale waiver would hide a real difference.
5. Nothing to change for #20: it is in the placement engine. Re-run
   `/draw`; an assembly whose balloons had to cross a view will report
   different `ballooned` views in sheets.json, with the moved part
   numbers now in the view that shows them.

### Compatibility

Every sheet of the four real sets — eleven sslxbt part sheets, the TPE
assembly and its parts, amogha RES-41-LH-01 and
`samples/out/RES-SAMPLE-01` — redraws byte for byte. The sslxbt assembly
sheet changes only where it had the fault: five balloons move into the
section, and with both details on sheet 2 the set now passes both checks
with nothing left over.

## v0.11.0 — 2026-11-24

Three faults a real set carried past every check the pipeline had, the
check that would have caught them, and details on a second sheet:
[#16](https://github.com/MusaCAD/fabrication_pipeline/issues/16),
[#17](https://github.com/MusaCAD/fabrication_pipeline/issues/17),
[#18](https://github.com/MusaCAD/fabrication_pipeline/issues/18),
[#22](https://github.com/MusaCAD/fabrication_pipeline/issues/22) and
[#19](https://github.com/MusaCAD/fabrication_pipeline/issues/19).

### Fixed

- **A tapped hole's thread-end line was drawn at its mouth** (#16).
  `thread_lines` sorted the span and drew the line at the LOWER
  coordinate, though it documented `span[0]`. A blind hole drilled from a
  pocket floor at x = 888.6 and threaded 10 deep to x = 898.6 therefore
  got the thick line across the mouth it opens from, and the place where
  the thread really stops was left unmarked; only holes that happened to
  run toward lower coordinates came out right. The line now goes where
  the usable thread stops — `span[0]` as documented, or the new
  `"end_at"` when the span reads the other way round, which it usually
  does — and the lock refuses an `end_at` outside the threaded span.
- **The datum triangle covered the diameter sign of its own callout**
  (#17). Its tip sat exactly on the knee, where the leader's own label
  starts 0.4 x arrow further on, so the filled triangle blacked out the
  lower left of the first glyph: on a size callout that glyph is the ⌀,
  which a vendor cannot afford to read wrong. It showed on three of
  three such sheets in one set. The triangle now walks BACK along its
  leader — where ISO 1101 puts it anyway; it is the knee it no longer
  sits on — until its ink stands 0.6 mm clear of that text, and it never
  reaches back over the letter in its own box. The box keeps the spot it
  had, so a datum that already read correctly is untouched, and the
  triangle's ink is claimed like every other annotation's.
- **A leader could lie on the outline it points at, or cross another
  leader** (#18). The level knee heads the ladder, and on a roomy sheet
  that spot is almost always free, so a callout anchored on the
  silhouette of a turned part drew its leader ON that silhouette — one
  ran 76.8 mm along it — where the arrow can no longer be told from the
  part (ISO 128-22). In other orders one leader crossed another, so each
  appeared to point at the other's feature. Every rung is now tested
  against the view geometry and against the leaders already down, in
  printed millimetres; a knee also stays beside its own view rather than
  stepping into the next view's column. When the manifest's order is
  what makes two leaders cross, the sheet is re-composed with the
  callouts in FAN ORDER — the order their arrows are seen in from the
  column they share — and the better pass is kept: a fan cannot cross
  itself. When no rung is clean at all, the least bad one is drawn and
  the fault reported, because a callout the vendor never sees is worse
  than one whose leader crosses another. Balloons are ordered as ONE fan
  per side across every view that shares the column, and a fan now
  settles any crossing pair, not only neighbouring ones.
- **A detail view's caption could be crowded out by the notes under it.**
  The island `place_detail_view` reserved included a caption band, but
  nothing claimed it, so a leader note landed there and the caption had
  nowhere to go. The band is claimed with the view now — a reservation
  that yields, since a note has no ladder of alternatives and a caption
  has — and when it does yield the caption falls back to a shorter
  stand-off before it fails. A caption that cannot be placed at all says
  why.

### Added

- **The layout check** (#22), stage 4c, run inside /draw. The value check
  reads what a sheet SAYS; this one reads how it LOOKS, off the finished
  .musa, because the layout engine only ever tests the boxes it placed
  against each other — a leader is a line, not a box, and every fault
  above passed every check the pipeline had and was found by eye. It
  reports:
  - `leader-crossing` — two leaders cross, so each points at the other's
    feature;
  - `leader-on-outline` — a leader runs along drawn geometry, within
    0.5 mm for more than 3 printed mm;
  - `ink-on-text` — a symbol, datum triangle, frame or balloon lies on
    text (a balloon's own number, inside its circle, is not that);
  - `text-off-sheet` — annotation text outside the drawing area;
  - `leaders-parallel` — two leaders running together closer than 2 mm,
    a WARNING only: it reads badly but says nothing false.

  Every tolerance is in printed millimetres at any scale, and the parts
  table's rules and cells are the template's furniture rather than the
  drawing's ink. /draw prints the findings and records them per sheet in
  sheets.json (`layout_faults`, `layout_warnings`); /export refuses
  everything but the warning unless a manifest `"layout_waivers"` entry
  names it, exactly as `"value_waivers"` works for the value check.
- **Detail views on an assembly's continuation sheet** (#19). An entry of
  `assembly_sheet.detail_views` may say `"sheet": 2`: sheet 1 then keeps
  the views and the section, and sheet 2 carries the enlargements, THEIR
  BALLOONS and the rest of the parts table. The region is still circled
  on the view it comes from, on sheet 1, and the letter there reads
  "B (SH 2)" so the reader knows where to look. One balloon per part
  number still means one on the whole DRAWING, not one per sheet: a part
  shown best in a detail is ballooned with that detail, wherever it is.
  A continuation sheet is made for the details even when the table fits.

### Upgrade steps

1. `git -C <your pipeline clone> fetch --tags && git checkout v0.11.0`
2. **Tapped threads (#16): check every `end_line` you turned off.** The
   convention is `span = [thread end, mouth]`; where the span reads into
   the part instead, add `"end_at": <the thread end>` and drop
   `"end_line": false`. The lock will tell you if the coordinate is not
   on the thread.
3. **Datum callouts (#17): take the leading spaces out.** If you padded a
   callout's text to push its ⌀ clear of the triangle, remove the
   padding — the triangle moves itself now, and the padding will push the
   text further right than it needs to go.
4. **Leaders (#18): re-run `/draw` and read the two new reports.** Hand
   -chosen `at` points that worked around a leader riding an outline can
   stay, but most of them are no longer needed. A sheet whose band is
   genuinely full now says so instead of quietly crossing two leaders.
5. **The layout check (#22) can stop an export that used to pass.** It
   reports a real fault, so the fix is the sheet, not the check: move the
   callout's arrow, put the note on the other view, or give the part a
   bigger sheet. `"layout_waivers": ["<words from the finding>"]` in the
   manifest waives one deliberately, as `"value_waivers"` does.
6. **An assembly with more details than fit (#19):** add `"sheet": 2` to
   the ones that do not, and re-run. Nothing else changes; without it,
   every detail stays on sheet 1 as before.

### Compatibility

Four real sets redraw sheet for sheet: the TPE assembly and its parts and
amogha RES-41-LH-01 are byte-identical, and of thirteen sslxbt sheets the
two that carried none of these faults are byte-identical too. The ten
that changed each carried one — a datum triangle on a ⌀, a leader on an
outline, two leaders crossing — and the layout check counts 23 faults on
that set before and 5 after, all five on the one sheet whose annotation
band is genuinely full (it now says so). The value check is clean across
the set.

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
