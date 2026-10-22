---
type: "Experimental finding"
title: "Sequence decoding: 2026-10-21"
description: "Dropping isolated-epoch smoothing helps the deployed-style forest; Viterbi adds a small gain mainly on the balanced forest. Neither is demonstrated."
status: "draft"
generated: {"by": "claude/opus-5.5", "at": "2026-10-22T02:15:00+00:00"}
sources: [{"resource": "Local runs seqdecode-20261022T020904Z (final) and seqdecode-20261022T014842Z (superseded): report.json and protocol.json under ignored data/runs/, from scripts/sequence-decoding.py", "title": "Decoder ablation report"}, {"resource": "Local training workspace run 20261021T055014537710Z: features.json under ignored data/runs/", "title": "Labeled feature epochs"}, {"resource": "/references/training-run.md", "title": "Training run provenance"}]
---

# Sequence decoding: 2026-10-21

On held-out nights, replacing the production isolated-epoch smoothing with plain argmax or HMM/Viterbi decoding raised macro F1 by about 0.02 for the oversampled depth-8 forest. Viterbi's extra transition structure added little there; on the class-balanced forest Viterbi gained about 0.02 where argmax gained nothing, with an interval touching zero. One participant; descriptive, not a recommendation to change production.

## Measured: decoder ablation

`scripts/sequence-decoding.py`, 10 night groups, 693 labeled epochs, leave-one-night-group-out. Each outer fold fits each forest once; every decoder scores that same forest. Emissions are emission weight × log forest probability, without prior division, because both forests are fitted on class-balanced data. Transitions come only from consecutive labeled training-fold Watch epochs; the training label prior sets the first epoch only. Smoothing α ∈ {0.5, 2} and emission weight ∈ {0.5, 1, 2, 4, 8, 16, 32} were chosen by inner 3-fold GroupKFold on training nights. Rows are Watch labels; cells count epochs.

| Forest / decoder | macro F1 | κ | light | deep | REM | light→REM | light→deep | REM→light | deep→light | direct deep↔REM | Δ macro F1 per night (95% night bootstrap) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| existing_depth8 / production | 0.408 | 0.294 | 0.76 | 0.43 | 0.44 | 60 | 42 | 87 | 40 | 0 | — |
| existing_depth8 / argmax | 0.422 | 0.331 | 0.73 | 0.45 | 0.52 | 64 | 49 | 71 | 37 | 3 | +0.021 (+0.006, +0.037) |
| existing_depth8 / Viterbi | 0.426 | 0.336 | 0.77 | 0.45 | 0.49 | 56 | 44 | 78 | 37 | 1 | +0.024 (+0.005, +0.041) |
| existing_depth8 / Viterbi, no deep↔REM | 0.423 | 0.332 | 0.77 | 0.44 | 0.49 | 56 | 44 | 78 | 38 | 0 | +0.022 (+0.001, +0.040) |
| balanced_leaf10 / production | 0.411 | 0.304 | 0.55 | 0.65 | 0.63 | 117 | 73 | 44 | 23 | 0 | — |
| balanced_leaf10 / argmax | 0.407 | 0.302 | 0.55 | 0.62 | 0.63 | 115 | 75 | 42 | 24 | 12 | −0.002 (−0.022, +0.016) |
| balanced_leaf10 / Viterbi | 0.436 | 0.359 | 0.60 | 0.64 | 0.69 | 106 | 66 | 38 | 24 | 2 | +0.018 (−0.002, +0.044) |
| balanced_leaf10 / Viterbi, no deep↔REM | 0.436 | 0.358 | 0.60 | 0.62 | 0.68 | 105 | 64 | 40 | 25 | 0 | +0.019 (−0.000, +0.044) |

Wake recall is 0 throughout (14 wake epochs). The production baseline reproduces the existing_depth8 confusion cells reported in the light-sleep confusion analysis of the same date.

## Measured: hyperparameter selection

Inner selection chose emission weights from 4 to 32 across folds for both forests, with no single setting in more than 4 of 10 folds. Large weights approach argmax, so for existing_depth8 the selected Viterbi is close to argmax and its predictions differ little.

## Measured: revisions

The first run divided probabilities by the training label prior as a grid option and stopped emission weight at 2; review noted that this rebalanced already-balanced forests a second time and that selection sat on the grid edge. In that run Viterbi did not beat production (existing_depth8 −0.008, balanced_leaf10 −0.033). An intermediate run without prior division and weights up to 8 still selected the edge (weight 8 in every fold) and gave existing_depth8 +0.029 and balanced_leaf10 +0.016. The grid above was extended once, before this final run, to move selection off the edge. Results changed with the grid, which is itself a sign of how small and noisy these differences are.

## Observations

- Most of the existing_depth8 gain comes from removing isolated-epoch smoothing: argmax and Viterbi intervals overlap almost entirely.
- On balanced_leaf10, argmax does not help but Viterbi does, mostly by reducing light→REM and light→deep errors and raising light and REM recall.
- Unconstrained Viterbi already produced 1–2 direct deep↔REM transitions, so forbidding them changed little.
- Eight decoder/forest comparisons on ten nights, several of them overlapping, make every interval here weak evidence.

## Hypotheses

- Features already use centered 15-minute windows, so neighboring-epoch information is partly inside the emissions; a first-order HMM partly double-counts it. Context features with explicit lags may be the better test.
- The isolated-epoch smoothing may remove correct short REM and deep runs, which are short (median deep run 2 epochs) in Watch labels.
- Duration-aware decoding (semi-Markov with Watch run-length priors) was not tested.

## Limitations

One participant, ten nights, already inspected; the grid was revised after seeing results from the earlier runs. Watch labels are not polysomnography. Forest probabilities are uncalibrated. The decoder applies a single transition step across recording gaps (seven gaps of 10–15 minutes and one of 160 minutes in this data). Night bootstrap intervals are approximate because outer training sets overlap. Not prospective; no export or activation.

See [validation protocol](validation-protocol.md), [metrics](metrics.md) and [earlier findings](findings-2026-10-20.md).
