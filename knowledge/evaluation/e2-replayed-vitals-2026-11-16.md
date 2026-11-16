---
type: "Experimental finding"
title: "Replayed vs live vitals for staging (E2): 2026-11-16"
description: "Retraining podContext on vitals replayed through one pinned engine and profile ties training on the mixed-version live rows; replayed breathing tracks the Watch more closely."
status: "draft"
generated: {"by": "claude/opus-5.5", "at": "2026-11-16T01:30:00+00:00"}
sources: [{"resource": "Local run data/runs/e2-20261116/report.json (ignored)", "title": "search.py report, E2"}, {"resource": "Local replay data/replay/c2-v2-20261116/replay.json (ignored)", "title": "core biometrics-local-fit replay, profile 04f7a165"}]
---

# Replayed vs live vitals for staging (E2): 2026-11-16

Question (plan E2, pre-registered before the run): does training the stage model on vitals
from one pinned engine and profile beat training on the live rows, which came from at least
seven engine/profile pairs across these nights (archive lineage)?

Setup: `search.py` spec `e2-replayed-c2-vs-live`, primary comparison
`replayed+none+podContext` vs `live+none+podContext`, macro F1, walk-forward, forest
`balanced_leaf5`, the same 24 night groups (2026-10-18 → 11-13, left side, one participant).
Replayed vitals came from core `biometrics-local-fit replay --archive` (core #928) with engine
`8865a12b` and the left side's saved profile `04f7a165` (the profile core #927 restores after a
deploy). Core's training gate judged the 10 nights that live had run on `04f7a165`; all 10
passed. The other 18 nights ran another profile and were accepted ungated, as pre-registered.
No locked holdout night had Watch labels yet, so none was read.

| Candidate | Walk-forward macro F1 | κ |
| --- | ---: | ---: |
| live vitals | 0.524 | 0.488 |
| replayed vitals | 0.524 | 0.497 |

Primary: +0.000 macro F1, night bootstrap 95% [−0.036, +0.037], 9 of 16 test nights better,
sign test p = 0.80. Under the plan's rule this is a tie: the mixed vitals versions did not
measurably hurt staging, and nothing is exported. Replay stays the input for vitals tuning
(C4) and the search harness (F).

Vitals against the Watch on the same epochs: HR MAE 1.59 (live) vs 1.60 bpm (replayed);
breathing MAE 1.80 vs 1.11 breaths/min; HRV rank ρ 0.51 for both. The breathing gain fits
the November nights having run on defaults, which read about 2/min above the Watch.

Watch stages are the reference, not polysomnography. Retrospective; no model was exported,
uploaded or activated.
