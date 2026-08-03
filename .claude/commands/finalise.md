---
description: Agree per-part drawing decisions with the user, lock the manifest
---

Run pipeline stage 3 (finalise). This stage is INTERACTIVE — the user decides
per-part drawing choices in session; nothing is guessed silently.

ELICITATION PROTOCOL (standard, per user instruction): never batch-block on
a list of unanswered items. Ask ONE question at a time, conversationally:
ask → wait for the answer → confirm your reading of it → update
manifests/manifest_draft.json live → move to the next question. Propose a
concrete default with each question (with preview renders where they help),
but the user decides.

1. Run `python3 src/manifest.py propose` (requires /analyse + /judge done;
   refuses on overall FAIL). This writes manifests/manifest_draft.json with
   proposed defaults.
2. Elicit decisions one at a time, part by part. Per part:
   - **front_view** (+X/-X/+Y/-Y/+Z/-Z): the default views along the
     smallest bbox extent (most information-rich face). Confirm or change.
   - **section**: enabled by default, cut at offset_frac 0.5,
     **projection** "below" (horizontal cut, reference style) — the user may
     choose "right" or "left" (vertical cutting plane projected to the
     side) for parts whose details run vertically. Confirm position,
     projection, and necessity. Note: sections use alignment-preserving
     third-angle sight arrows; offer to flip to first-angle if the user
     prefers (draw.py documents the change).
   - For parts where a mid-section shows nothing (e.g. a plain solid
     cylinder), apply the invisible-features rule: flag it and propose an
     honest alternative (disable the section; front view + diameter
     callout, or an end view as the second view) rather than a meaningless
     hatch.
   - **drg_no**: propose from the user's numbering scheme once they state it
     (drg_no_prefix), e.g. RES-<project>-<part>-<nn>. Never invent a scheme.
   - **material**: must be a real spec (e.g. "ALUMINIUM 6061-T6"); the
     FreeCAD models currently carry none.
   - **scale**: default from /judge suggestion; confirm.
   - **waivers**: list the WARNs being accepted; the user must acknowledge.
   - Ask whether any feature is invisible in both chosen views; if the user
     names one, record it in extra_notes or adjust views — never silently add
     a third view (two views per part is the rule).
   - **GD&T** (vendor-publishable drawings): propose datum labels (A, B…)
     and feature control frames only where a fabricator can measure them —
     flatness on datum faces, position on hole patterns w.r.t. datums,
     perpendicularity where mating demands it. No decorative GD&T. Use the
     reference drawing's conventions (flatness ≤0.05 over full surface,
     chamfer 1×45°, N7 finish) as proposal defaults. Record approved
     callouts in the part's "gdt" list.
   - **Fabrication notes**: per-sheet NOTES block (deburr/chamfer, surface
     finish N-grade, heat/surface treatment, process notes e.g. weld spec)
     — reference-drawing style; record in "fab_notes". The template's
     IS 2102 general tolerance block always stays.
3. Also settle the assembly sheet: description, drg_no, front_view, whether
   a section is needed (e.g. to show the internal rods), scale.
4. Set top-level fields: project, drg_no_prefix, rev (default 00).
5. Edit manifests/manifest_draft.json to the agreed values, show the user a
   summary table, and on their confirmation run:
   `python3 src/manifest.py lock manifests/manifest_draft.json`
   which validates and freezes manifests/manifest.json.

Do not proceed to /draw in the same breath unless the user asked for the
full /pipeline run.
