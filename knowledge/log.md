# Knowledge update log

## 2026-11-22

- **Creation**: Captured the [Apple Watch validation literature](references/apple-watch-validation.md): 12 papers cited by a public brand comparison, each read and checked; sleep staging rests on one Oura-funded PSG study (κ 0.60, wake sensitivity 52%), and no cited study measured vitals during sleep. Linked from the [validation protocol](evaluation/validation-protocol.md). No model activation.

## 2026-11-16

- **Creation**: Captured [E2, replayed vs live vitals](evaluation/e2-replayed-vitals-2026-11-16.md): a tie on staging, better breathing agreement. No model export or activation.

## 2026-11-15

- **Creation**: Set the [locked holdout](evaluation/locked-holdout.md) from the model-graph plan (decision Q4): freeze 2026-11-14, every fourth night group locked, trial and final reads only, each logged in `data/holdout-access.jsonl`. Stage scripts drop locked nights; dashboards hide their scores. No model activation.
- **Creation**: Documented the [movement model contract](data/movement-raw-v1.md) `movementRaw-v1` for the pod port: window features, the pod-score minute rule, strict threshold, float32 and missing-value routing, hash and test vectors, and the exported size. No model upload or activation.
- **Creation**: Captured the [cross-fitted fusion rerun](evaluation/fusion-cross-fitted-2026-11-15.md) after fixing the movement-teacher leak; conclusions unchanged. No model activation.

## 2026-11-04

- **Creation**: Captured [reference devices](data/reference-devices.md): wearable measurements are optional, compared per device and never model features.

## 2026-10-22

- **Update**: Added the 13-night rerun to [sequence decoding](evaluation/sequence-decoding-2026-10-21.md); the 10-night decoder gains did not hold.
- **Creation**: Captured the [piezo bandwidth gate](evaluation/piezo-bandwidth-2026-10-22.md) result: both snore bands passed in one scripted session with imitated snoring; cross-mattress transfer and knock over-counting noted; full-night coverage and archive rate recorded. No model activation.

## 2026-10-21

- **Update**: Captured [light-sleep confusion](evaluation/light-sleep-confusion-2026-10-21.md): held-out confusion, feature separation, Watch transition structure and ranked experiments. Linked from [research priorities](operations/research-priorities.md) and the overnight capture plan. No model activation.
- **Creation**: Captured [sequence decoding](evaluation/sequence-decoding-2026-10-21.md): HMM/Viterbi decoder ablation on fixed forests; dropping isolated-epoch smoothing gained about 0.02 macro F1, Viterbi a similar amount on the balanced forest, all weak evidence. No model activation.

## 2026-10-20

- **Creation**: Established the OKF v0.2 bundle and captured [retraining findings](evaluation/findings-2026-10-20.md) as drafts with aggregate evidence. No model activation or publication.
- **Update**: Added the [runnable offline training pipeline](operations/runnable-pipeline.md), pinned core runtime sources, dependency locks, ignored data placeholder, and synthetic CI checks.
- **Update**: Added [pod acquisition and visual progress](operations/pod-acquisition.md), with read-only HTTP input, terminal progress, a loopback dashboard, and saved offline reports.
- **Update**: Expanded visual training telemetry with data throughput, feature statistics, fit timings, tree/node counts, held-out night scores, and a complete structured event log.
- **Update**: Added aligned pod/model/Watch sleep timelines with captured API stages, separate held-out and in-sample views, and explicit gaps and disagreement counts.

- 2026-10-21: Added the training workspace, persistent local run catalog, educational explanations, sleep architecture, baseline traces and descriptive diagnostics. Mock design release thresholds are not promotion policy.
