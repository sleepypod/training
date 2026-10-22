---
type: "Experimental finding"
title: "Sequence decoding: 2026-10-21"
description: "HMM/Viterbi decoding over forest probabilities does not beat production smoothing; it shifts errors between light and REM/deep."
status: "draft"
generated: {"by": "claude/opus-5.5", "at": "2026-10-22T01:50:00+00:00"}
sources: [{"resource": "Local run seqdecode-20261022T014842Z: report.json and protocol.json under ignored data/runs/, from scripts/sequence-decoding.py", "title": "Decoder ablation report"}, {"resource": "Local training workspace run 20261021T055014537710Z: features.json under ignored data/runs/", "title": "Labeled feature epochs"}, {"resource": "/references/training-run.md", "title": "Training run provenance"}]
---

# Sequence decoding: 2026-10-21

Replacing the production isolated-epoch smoothing with HMM/Viterbi decoding did not improve held-out macro F1 for either fixed forest. It moved errors between light and REM/deep rather than removing them. One participant; descriptive.

## Measured: decoder ablation

`scripts/sequence-decoding.py`, 10 night groups, 693 labeled epochs, leave-one-night-group-out. Each outer fold fits each forest once; every decoder scores that same forest. Viterbi transitions come only from consecutive labeled training-fold Watch epochs. Smoothing α, emission weight and prior division were chosen by inner 3-fold GroupKFold on training nights from a frozen 12-point grid. Rows are Watch labels; cells count epochs.

| Forest / decoder | macro F1 | κ | light | deep | REM | light→REM | light→deep | REM→light | deep→light | Δ macro F1 per night (95% night bootstrap) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| existing_depth8 / production | 0.408 | 0.294 | 0.76 | 0.43 | 0.44 | 60 | 42 | 87 | 40 | — |
| existing_depth8 / argmax | 0.422 | 0.331 | 0.73 | 0.45 | 0.52 | 64 | 49 | 71 | 37 | +0.021 (+0.006, +0.037) |
| existing_depth8 / Viterbi | 0.404 | 0.279 | 0.62 | 0.55 | 0.56 | 105 | 60 | 64 | 32 | −0.008 (−0.051, +0.027) |
| existing_depth8 / Viterbi, no deep↔REM | 0.397 | 0.261 | 0.59 | 0.53 | 0.58 | 119 | 57 | 63 | 33 | −0.019 (−0.063, +0.018) |
| balanced_leaf10 / production | 0.411 | 0.304 | 0.55 | 0.65 | 0.63 | 117 | 73 | 44 | 23 | — |
| balanced_leaf10 / argmax | 0.407 | 0.302 | 0.55 | 0.62 | 0.63 | 115 | 75 | 42 | 24 | −0.002 (−0.022, +0.016) |
| balanced_leaf10 / Viterbi | 0.399 | 0.277 | 0.63 | 0.45 | 0.58 | 110 | 48 | 62 | 36 | −0.033 (−0.056, −0.003) |
| balanced_leaf10 / Viterbi, no deep↔REM | 0.399 | 0.277 | 0.63 | 0.45 | 0.58 | 110 | 48 | 62 | 36 | −0.033 (−0.055, −0.006) |

Wake recall is 0 throughout (14 wake epochs). The production baseline reproduces the existing_depth8 confusion cells reported for the 2026-10-21 light-sleep confusion analysis.

## Observations

- Production postprocessing already rewrites deep→REM and REM→deep through light, so it shows zero direct deep↔REM transitions. Forbidding them in the HMM adds nothing beyond that.
- Viterbi on the oversampled forest behaves like class balancing: deep and REM recall rise, light recall falls, net macro F1 unchanged. On the balanced forest it pulls back toward light. The transition prior mostly re-weights classes rather than adding temporal information the forest's centered-window features lack.
- Inner selection was unstable for existing_depth8 (different grid points across folds). For balanced_leaf10 it picked emission weight 2.0, the grid edge favoring emissions over transitions, in 10 of 10 folds.
- Removing isolated-epoch smoothing (argmax) on existing_depth8 is the only interval excluding zero, +0.021. Eight decoder/forest comparisons on ten nights make this weak evidence; it is not a recommendation to change production smoothing.

## Hypotheses

- Features already use centered 15-minute windows, so neighboring-epoch information is partly inside the emissions; a first-order HMM double-counts it. Context features with explicit lags may be the better test.
- Duration-aware decoding (semi-Markov with Watch run-length priors) was not tested.

## Limitations

One participant, ten nights, already inspected. Watch labels are not polysomnography. Forest probabilities are uncalibrated. Night bootstrap intervals are approximate because outer training sets overlap. Not prospective; no export or activation.

See [validation protocol](validation-protocol.md), [metrics](metrics.md) and [earlier findings](findings-2026-10-20.md).
