---
type: "Experimental finding"
title: "Sequence decoding: 2026-10-21"
description: "On 10 nights, dropping smoothing or Viterbi decoding gained about 0.02 macro F1; on 13 nights neither gain held. No decoder beats production smoothing."
status: "draft"
generated: {"by": "claude/opus-5.5", "at": "2026-10-23T02:50:00+00:00"}
sources: [{"resource": "Local run seqdecode-20261022T192528Z-export-1022 (13 nights, same script and grid): report.json under ignored data/runs/", "title": "13-night decoder rerun"}, {"resource": "Local runs seqdecode-20261022T021425Z (final) and seqdecode-20261022T014842Z (superseded): report.json and protocol.json under ignored data/runs/, from scripts/sequence-decoding.py", "title": "Decoder ablation report"}, {"resource": "Local training workspace run 20261021T055014537710Z: features.json under ignored data/runs/", "title": "Labeled feature epochs"}, {"resource": "/evaluation/light-sleep-confusion-2026-10-21.md", "title": "Light-sleep confusion baseline"}, {"resource": "/references/training-run.md", "title": "Training run provenance"}]
---

# Sequence decoding: 2026-10-21

Update, 13 nights: the gains reported below for 10 nights did not hold. Rerun with the same script and grid on a fresh export covering 13 night groups, no decoder improved per-night macro F1 over production smoothing; every 95% interval includes zero. The original 10-night text follows unchanged for the record. On held-out nights, replacing the production isolated-epoch smoothing with plain argmax or HMM/Viterbi decoding raised macro F1 by about 0.02 for the oversampled depth-8 forest. Viterbi's extra transition structure added little there; on the class-balanced forest Viterbi gained about 0.02 where argmax gained nothing, with an interval touching zero. One participant; descriptive, not a recommendation to change production. The title date is the local (America/Los_Angeles) analysis date; run IDs are UTC timestamps, so they read 2026-10-22.

## Measured: decoder ablation

`scripts/sequence-decoding.py`, 10 night groups, 693 labeled epochs, leave-one-night-group-out. Each outer fold fits each forest once; every decoder scores that same forest. Emissions are emission weight × log forest probability, without prior division, because both forests are fitted on class-balanced data. Transitions come only from consecutive labeled training-fold Watch epochs; the training label prior sets the first epoch only. Forbidden deep↔REM transitions have exactly zero probability. Smoothing α ∈ {0.5, 2} and emission weight ∈ {0.5, 1, 2, 4, 8, 16, 32} were chosen by inner 3-fold GroupKFold on training nights. Rows are Watch labels; cells count epochs.

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

Wake recall is 0 throughout (14 wake epochs). The production baseline reproduces the existing_depth8 confusion cells reported in [light-sleep confusion](light-sleep-confusion-2026-10-21.md).

## Measured: 13-night rerun

Same script, decoders and grid; 13 night groups, 890 labeled epochs (20 wake), from an Apple Health export taken 2026-10-22.

| Forest / decoder | macro F1 | κ | light | deep | REM | light→REM | light→deep | REM→light | deep→light | direct deep↔REM | Δ macro F1 per night (95% night bootstrap) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| existing_depth8 / production | 0.427 | 0.325 | 0.75 | 0.51 | 0.47 | 73 | 64 | 101 | 45 | 0 | — |
| existing_depth8 / argmax | 0.424 | 0.318 | 0.71 | 0.53 | 0.50 | 86 | 68 | 94 | 42 | 3 | −0.001 (−0.020, +0.018) |
| existing_depth8 / Viterbi | 0.437 | 0.346 | 0.74 | 0.52 | 0.51 | 74 | 63 | 92 | 43 | 1 | +0.012 (−0.006, +0.030) |
| existing_depth8 / Viterbi, no deep↔REM | 0.439 | 0.352 | 0.75 | 0.52 | 0.52 | 72 | 63 | 91 | 43 | 0 | +0.014 (−0.004, +0.031) |
| balanced_leaf10 / production | 0.448 | 0.333 | 0.54 | 0.69 | 0.69 | 148 | 93 | 49 | 26 | 0 | — |
| balanced_leaf10 / argmax | 0.444 | 0.313 | 0.51 | 0.67 | 0.68 | 154 | 98 | 50 | 28 | 16 | +0.005 (−0.021, +0.044) |
| balanced_leaf10 / Viterbi | 0.426 | 0.328 | 0.57 | 0.65 | 0.66 | 141 | 89 | 59 | 29 | 2 | −0.008 (−0.043, +0.019) |
| balanced_leaf10 / Viterbi, no deep↔REM | 0.431 | 0.335 | 0.58 | 0.65 | 0.66 | 139 | 85 | 59 | 29 | 0 | −0.003 (−0.040, +0.026) |

The 10-night argmax gain on existing_depth8 (+0.021, interval excluding zero) became −0.001; balanced_leaf10 Viterbi went from +0.018 to −0.008. Ten of the thirteen nights overlap the earlier run, so the change comes from three added nights and a refreshed export, which shows how unstable differences of this size are.

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

One participant, ten and then thirteen nights, already inspected; the grid was revised after seeing results from the earlier runs. Watch labels are not polysomnography. Forest probabilities are uncalibrated. The decoder applies a single transition step across recording gaps (seven gaps of 10–15 minutes and one of 160 minutes in this data). Night bootstrap intervals are approximate because outer training sets overlap. Not prospective; no export or activation.

See [light-sleep confusion](light-sleep-confusion-2026-10-21.md), [validation protocol](validation-protocol.md), [metrics](metrics.md) and [earlier findings](findings-2026-10-20.md).
