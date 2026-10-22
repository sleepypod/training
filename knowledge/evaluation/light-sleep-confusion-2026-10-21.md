---
type: "Experimental finding"
title: "Light-sleep confusion: 2026-10-21"
description: "Light is the hub stage that REM and deep collapse into; current features barely separate light from REM."
status: "draft"
generated: {"by": "claude/opus-5.5", "at": "2026-10-22T02:40:00+00:00"}
sources: [{"resource": "Local training workspace run 20261021T055014537710Z: model/report.json and features.json under ignored data/runs/", "title": "Held-out confusion matrices and labeled feature epochs"}, {"resource": "vendor/core/docs/research/sleep-stage-calibration/personal-models.py (pinned core copy; removed from current core) run locally on 2026-10-21 with --contract B, the ten-feature contract, 12 nights, leave-one-night-out", "title": "Mac personal-model retrain"}, {"resource": "/references/training-run.md", "title": "Training run provenance"}]
---

# Light-sleep confusion: 2026-10-21

Light sleep is not failing on its own. It is the hub class: about a quarter of Watch-light epochs are predicted REM or deep, and about half of Watch-REM and Watch-deep epochs are predicted light. All numbers describe one participant and are descriptive.

## Measured: held-out confusion

Workspace run, 10 night groups, 693 labeled epochs (14 wake, 430 light, 77 deep, 172 REM), whole-night outer folds. Rows are Watch labels, columns are predictions, for `existing_depth8` (the closest candidate to the forest deployed on the pod):

| Watch | wake | light | deep | REM | recall |
| --- | ---: | ---: | ---: | ---: | ---: |
| wake | 0 | 2 | 1 | 11 | 0.00 |
| light | 1 | 327 | 42 | 60 | 0.76 |
| deep | 1 | 40 | 33 | 3 | 0.43 |
| REM | 6 | 87 | 3 | 76 | 0.44 |

Light precision is 327/456 = 0.72; overall κ is 0.294. This run is not the 11-group run2 in the earlier findings. Class-balanced candidates move the error rather than remove it: `balanced_leaf10` raises deep and REM recall to 0.65 and 0.63 but lowers light recall to 0.55, sending 117 light epochs to REM and 73 to deep.

## Measured: feature separation

Standardized mean difference (Cohen's d with n-weighted pooled SD; first stage minus second) across labeled epochs, pooled over nights:

| Feature | light vs REM | light vs deep | deep vs REM |
| --- | ---: | ---: | ---: |
| hrZ | −0.04 | −0.09 | +0.05 |
| hrStdZ | −0.35 | +0.42 | −0.84 |
| motionZ | +0.29 | +0.24 | +0.05 |
| nightFraction | −0.33 | +0.59 | −1.01 |
| hrvRel | −0.48 | +0.89 | −1.53 |
| hrv15Rel | −0.54 | +0.91 | −1.53 |
| hr15StdRel | −0.24 | +0.59 | −1.02 |
| br15Std | −0.17 | +0.23 | −0.55 |
| motion15 | +0.15 | +0.06 | +0.09 |
| minutesSinceBed | −0.38 | +0.63 | −1.04 |

No feature separates light from REM by more than about half a pooled standard deviation; time of night and HRV carry most of it. Light versus deep is easier, and deep versus REM easiest. Mean heart rate (`hrZ`) differs by less than 0.1 SD for all three pairs.

## Measured: Watch label structure

Between consecutive labeled epochs (exactly 300 s apart): light→REM 29, light→deep 29, deep→light 27, REM→light 27, wake→light 10, light→wake 6, REM→wake 2, deep→wake 2, and zero direct deep↔REM transitions. Deep and REM exit to light, or rarely to wake. Median run lengths are REM 6 epochs, light 4, deep 2, wake 1. These sequences hold only 31 REM runs and 31 deep runs in total.

## Measured: deployment check

A 12-night Mac retrain (contract B) scored forest leave-one-night-out κ 0.255 and logistic about 0.11, versus κ 0.25 for an earlier 11-night run of the same script: descriptively similar, with no variance estimate. The forest beat the pod rules on every held-out night. The pod's own preview of the stored forest is in-sample and not comparable to held-out scores.

## Hypotheses and proposed experiments

Ranked by expected effect on light confusion. None is demonstrated.

The flat `hrZ` is physiologically unexpected; it may reflect heart-rate signal quality or fallback values rather than physiology (experiment 3 tests this).

1. **Sequence decoding.** Replace isolated-epoch smoothing with HMM/Viterbi decoding over forest probabilities, using the learned transition matrix (deep↔REM forbidden or near zero) and duration priors. It exports as a small matrix, so pod parity is cheap.
2. **Context features.** Lagged and lead copies of HRV, heart rate and motion (±1–3 epochs), 30-minute rolling means, and cycle position (minutes since last predicted deep or REM). Fits the existing retrospective, centered-window contract.
3. **Heart-rate signal quality.** Measure per-epoch heart-rate coverage and fallback use; add a coverage feature or filter before trusting `hrZ`. Overlaps research priority 3.
4. **REM-specific signals.** Short-window breathing irregularity (breath-to-breath variability over 1–2 minutes from raw piezo) and an interaction of low motion with high heart-rate variability, the classic atonia-plus-irregularity REM signature. Requires raw piezo access beyond the current 15-minute breathing feature.
5. **Partial class weighting.** Try square-root inverse weights selected by macro F1 in the nested loop; full balancing overshoots.
6. **More nights.** The REM and deep run counts are the binding constraint; depth and boosting sweeps already showed no gain.

## Limitations

One participant. Watch labels are themselves approximate for light versus REM, so agreement with the Watch is not accuracy against polysomnography. Effect sizes pool epochs across nights and ignore autocorrelation; they describe overlap, not significance. The workspace run and the Mac retrain use different night counts and are not directly interchangeable.

See [validation protocol](validation-protocol.md), [metrics](metrics.md), [earlier findings](findings-2026-10-20.md) and [research priorities](../operations/research-priorities.md).
