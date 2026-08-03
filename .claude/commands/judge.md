---
description: Review the latest analysis for fabrication-drawing readiness
---

Run pipeline stage 2 (judgement):

1. Run `python3 src/judge.py` (reads reports/analysis.json, writes
   reports/judgement.md and judgement.json).
2. Present the verdict to the user in a compact form: overall PASS/WARN/FAIL,
   then per-part findings. Explain each WARN/FAIL in plain language and what
   fixing it would involve **in FreeCAD** (e.g. "assign a material to the
   Body", "add the missing sketch dimension").
3. HARD RULE — judge is report-only. Never edit, "fix", or resave the FreeCAD
   models; the user does that themself. Do not soften FAILs.
4. Remind the user: FAIL blocks /finalise; WARNs can be waived per-part
   during /finalise. If the user fixes models, rerun /analyse then /judge.

Checks performed by the script: missing/Default materials, sketches not
fully constrained or not solving, bodies with no or multiple solids, parts
with zero dimensional constraints, broken assembly links, ungrounded or
unjointed instances, and parts that fit no standard scale
(1:1, 1:2, 1:5, 1:10, 2:1, 5:1) on the A4 template.
