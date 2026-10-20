---
type: "Evidence reference"
title: "Training run provenance"
description: "Source revisions and retained aggregate evidence."
status: "draft"
generated: {"by": "codex/gpt-6", "at": "2026-10-20T23:20:05.045039+00:00"}
sources: [{"resource": "Local sleepypod-core Git commit c9e5c5c037275d3e5a3c255efa2ff8801be13b79; stage-retraining/run2/report.json", "title": "Local training run and source code"}, {"resource": "/references/evidence/retraining-2026-10-20.json", "title": "Retained aggregate report"}]
---

# Training run provenance

The authoritative run is `stage-retraining/run2`, captured on 2026-10-20. The original training implementation is recorded in the local sleepypod-core commit `c9e5c5c037275d3e5a3c255efa2ff8801be13b79`. This commit is not assumed to be published remotely.

Source paths within that commit:

- `docs/research/sleep-stage-calibration/export-runtime-features.ts`
- `docs/research/sleep-stage-calibration/retrain-models.py`
- `docs/research/sleep-stage-calibration/retrain-models.py.lock`
- `docs/research/sleep-stage-calibration/test_retrain_models.py`
- `src/lib/personal-stage-model.ts`

The [aggregate evidence](evidence/retraining-2026-10-20.json) retains metrics, protocol, input fingerprint, source report fingerprint, artifact fingerprint, and limitations. It excludes raw samples, per-night dates, participant identifiers, and fitted model parameters. Aggregates still describe one person's data: this local repository is not automatically approved for public release.

The private source report and training inputs remain outside this repository. Reproducing the historical run requires those authorized inputs and its original pinned implementation. The repository now also includes a standalone [training pipeline](../operations/runnable-pipeline.md) derived from that source, with pinned production files; a sibling core checkout is no longer required for new runs. The knowledge bundle alone still contains no training inputs. The failed first experimental run was superseded by run2 using liblinear logistic regression.

Recorded checks: five Python behavioral tests, twelve TypeScript model tests, TypeScript/ESLint checks, and parity of 64 embedded vectors plus 970 raw predictions against the production TypeScript evaluator (zero mismatches). These are historical run checks, not checks rerun by the knowledge validator.
