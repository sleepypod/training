---
type: "Experimental finding"
title: "Cross-fitted fusion rerun: 2026-11-15"
description: "Removing the movement-teacher leak left the fusion conclusions unchanged: podContext ties the live pod model on unseen nights, wake-first is best but exploratory, movement-model features add nothing."
status: "draft"
generated: {"by": "claude/opus-5.5", "at": "2026-11-15T23:59:00+00:00"}
sources: [{"resource": "Local run data/runs/fusion-20261115-xfit/report.json (ignored)", "title": "Fusion report, cross-fitted"}, {"resource": "Local run data/runs/movement-model-20261115-xfit/report.json (ignored)", "title": "Movement model report"}]
---

# Cross-fitted fusion rerun: 2026-11-15

The 2026-11-14 fusion run let a stage-model training night carry movement features from a
movement model that had seen the held-out night's Watch motion. `movement_model.py epochs`
now writes `movement-epochs-v2`, and `fusion.py` builds every arm per held-out night from
movement models fitted without that night. Chronological scores now count only labelled
epochs with a live pod stage, the same epochs as `pod_live`. Live pod coverage of labelled
epochs was 100% on all 16 walk-forward test nights, so this change did not alter the
comparison population here.

Inputs: 24 night groups (25 sessions) with raw exports, 2026-10-18 → 11-13, one participant,
left side. The movement model had 7 Watch-motion nights (133,059 windows). It reproduced the
earlier result: 34% moving recall at 1% still false alarms, against 35% before the tie-safe cutoff.

Walk-forward, the 9 nights after 2026-11-03, which the live pod model never trained on
(mean per night):

| Arm | Macro F1 | κ |
| --- | ---: | ---: |
| base (podNative) | 0.489 | 0.463 |
| all (podContext columns) | 0.545 | 0.576 |
| all + movement model | 0.531 | 0.562 |
| fused | 0.533 | 0.564 |
| hierarchical_fused (wake first) | 0.564 | 0.618 |
| pod_live | 0.533 | 0.564 |

- `hierarchical_fused` vs `pod_live`: +0.030 macro F1, bootstrap 95% [+0.001, +0.055], 8 of 9
  nights better. Many arms were compared on the same nights, so treat this as promising, not
  confirmed. It needs the locked holdout (plan F).
- `all_movemodel` vs `all`: −0.006 [−0.020, +0.006]. Movement-model features still do not
  improve staging with 7 Watch-motion nights; wake F1 stays near 0.
- Leave-one-out over all 24 nights: `fused` vs `base` +0.029 [−0.002, +0.056], 19 of 24 better.

Scores on nights up to 2026-11-03 are in-sample for `pod_live` and are not compared here.
Watch stages are the reference, not polysomnography. Nothing was exported or activated.
