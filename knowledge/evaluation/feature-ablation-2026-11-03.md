---
type: "Experimental finding"
title: "Literature feature ablation: 2026-11-03"
description: "On 23 night groups, temporal-context and arousal features from pod data raised light F1 from 0.68 to 0.75–0.78 and κ from 0.38 to 0.46–0.48; wake stayed at zero recall, limited by 36 noisy Watch wake epochs."
status: "draft"
generated: {"by": "claude/opus-5.5", "at": "2026-11-03T16:30:00+00:00"}
sources: [{"resource": "Local run ablation-20261103: report.json and protocol.json under ignored data/runs/, from scripts/feature-ablation.py", "title": "Feature ablation report"}, {"resource": "Local run baseline-20261103 (60-day left-side pod fetch, 23 night groups): model/report.json and features.json under ignored data/runs/", "title": "Baseline nested retrain"}, {"resource": "Walch et al. 2019, SLEEP 42(12) zsz180; Fonseca et al. 2020, SLEEP 43(9) zsaa048; Radha et al. 2019, Sci Rep 9:14149; Kortelainen et al. 2010, IEEE TITB 14(3):776–785; Willemen et al. 2014, IEEE JBHI 18(2):661–669; van Hees et al. 2015, PLoS ONE 10(11):e0142533; Schyvens et al. 2025, SLEEP Advances 6(2) zpaf021", "title": "Papers the feature groups follow"}, {"resource": "/evaluation/light-sleep-confusion-2026-10-21.md", "title": "Light-sleep confusion baseline"}]
---

# Literature feature ablation: 2026-11-03

Adding temporal context and cardiorespiratory arousal features computed from pod data narrowed light-sleep confusion on held-out nights. Pod movement features alone added nothing, and no arm learned wake. One participant; descriptive, not a recommendation to change production.

## Measured: arms

`scripts/feature-ablation.py`, 23 night groups, 1,569 labeled epochs (36 wake, 951 light, 191 deep, 391 REM), leave-one-night-group-out. Every arm uses one frozen forest (200 trees, depth 8, leaf 5, balanced subsample) and production smoothing; there is no candidate selection. Δ is the mean per-night macro F1 difference against `base` with a night-bootstrap 95% interval.

| Arm | Macro F1 | κ | Light F1 | Light recall | Deep recall | REM recall | Δ macro F1 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| base (production ten) | 0.452 | 0.381 | 0.68 | 0.60 | 0.64 | 0.72 | — |
| context | 0.462 | 0.402 | 0.71 | 0.66 | 0.58 | 0.71 | +0.015 [−0.005, +0.038] |
| movement | 0.451 | 0.383 | 0.68 | 0.59 | 0.63 | 0.74 | −0.001 [−0.010, +0.008] |
| arousal | 0.484 | 0.433 | 0.72 | 0.67 | 0.61 | 0.74 | +0.026 [+0.005, +0.052] |
| movement + arousal | 0.475 | 0.438 | 0.73 | 0.68 | 0.62 | 0.73 | +0.016 [+0.000, +0.031] |
| all | 0.490 | 0.461 | 0.75 | 0.72 | 0.60 | 0.73 | +0.034 [+0.010, +0.059] |
| hierarchical (all) | 0.497 | 0.483 | 0.78 | 0.78 | 0.56 | 0.70 | +0.042 [+0.020, +0.065] |

Light→REM errors fell from 214 (base) to 140 (all) and 109 (hierarchical); light→deep from 147 to 117 and 102. Deep recall dropped by 0.04–0.08 as light absorbed more borderline epochs.

## Measured: wake

Wake recall is 0 in every arm except arousal (1 of 36). Out-of-fold wake-versus-sleep forests rank wake only weakly: AUC 0.69 (base), 0.71 (movement), 0.63 (arousal), 0.67 (all), with average precision about 0.045 against a 0.023 prevalence. Labeled wake epochs sit mostly mid-night (median about 6 hours after bed entry), not at the session edges.

## Interpretation

The groups follow these papers, translated to pod signals:

- **Context** (Radha 2019; Fonseca 2020): recurrent models gained from neighbouring epochs; here ±1 epoch and 30-minute means of heart rate, heart-rate variability and motion, plus minutes to bed exit.
- **Movement** (Kortelainen 2010; Willemen 2014; van Hees 2015): per-minute pod movement above the night floor, peak minute, distance to and length of the surrounding still bout. On the first Watch motion night (2026-11-02), pod movement per minute barely tracked wrist movement (Spearman 0.08 on the occupied side), which may explain why this group added nothing.
- **Arousal** (Willemen 2014): heart-rate rise against the preceding 15 minutes, epoch breathing-rate variability, and movement coupled with a heart-rate surge.
- **Hierarchical** (Kortelainen 2010): wake versus sleep, then staging without wake.

The wake ceiling is most plausibly labels, not features: 36 single-label 5-minute epochs, and consumer watches detect wake poorly (Apple Watch Series 8 wake specificity about 52% against polysomnography, Schyvens 2025).

## Proposed next steps

1. Rerun on the right side and after more nights before treating any arm as better; seven arms on the same nights inflate the chance of one interval excluding zero.
2. Port `all` features to core behind a challenger contract only if a second-side or later-night rerun holds; parity tests would be required.
3. Wake needs better labels: bed exits, self-reported awakenings, or an external polysomnography set (Walch 2019 PhysioNet `sleep-accel`) for pretraining a wake detector.
4. Improve pod movement against Watch motion labels first; with orientation in Watch files, label position changes rather than magnitude.

## Limitations

One participant, left side only. Watch labels are the reference, not polysomnography. Night bootstrap intervals are descriptive: overlapping training sets make them approximate. Same nights were inspected before; this is not prospective. No model was exported, uploaded or activated.

See [light-sleep confusion](light-sleep-confusion-2026-10-21.md), [sequence decoding](sequence-decoding-2026-10-21.md) and [validation protocol](validation-protocol.md).
