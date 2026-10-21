---
type: "Training workflow"
title: "Runnable offline training pipeline"
description: "Standalone training, ignored local data, and reproducibility checks."
status: "draft"
generated: {"by": "codex/gpt-6", "at": "2026-10-21T05:21:52.943153+00:00"}
sources: [{"resource": "sleepypod/training: scripts/, vendor/core-source.json, uv.lock, package-lock.json; initial pipeline migration on 2026-10-20"}, {"resource": "/references/training-run.md"}]
---

# Runnable offline training pipeline

The repository README is the executable setup and usage guide. `scripts/train.py` accepts either a consistent SQLite snapshot with local Watch labels or `--pod <ip>` to acquire completed sessions and imported Watch labels through read-only LAN HTTP. It requires a pod side, discovers timezone from the pod (or accepts an override), and creates a new output directory. It checks pinned production sources, extracts labels and features, runs nested grouped evaluation, exports the candidate, and verifies predictions with the TypeScript runtime. It performs no upload or activation.

`data/.gitkeep` creates a local working folder in fresh checkouts. Every other file under `data/` is ignored, including raw exports, features, models, and reports. The placeholder carries no personal data. Do not force-add those contents or upload them as CI artifacts.

Production TypeScript and the Python portable-export helper are copied byte-for-byte from the source revision in `vendor/core-source.json`; checksums detect edits. This is an explicit pinned snapshot, not a second independently maintained feature implementation. The active pipeline uses production TypeScript feature extraction, not the helper’s legacy CSV feature code. CoreML export and automatic pod upload are outside this pipeline.

Validation includes behavioral tests, TypeScript checking, vendor checks, and an end-to-end synthetic ZIP/SQLite training run with schema, embedded-vector, and per-row prediction parity. Initial migration also compared the exported real-data feature rows against the original 11-session research dataset: features, labels, night groups, rules, and coverage matched. Private data stayed in ignored storage.

Terminal progress records phase, elapsed time, model fits, and completed folds in `progress.json`. Optional `--dashboard` serves a read-only loopback viewer; every run saves a self-contained interactive `index.html`. See [pod acquisition and progress](pod-acquisition.md).

The pipeline currently evaluates one participant with at least five independent night groups. It does not implement whole-person holdouts. Small or class-deficient datasets can be unsuitable for fitting. Synthetic test scores establish software operation only; new model quality requires the [evaluation protocol](../evaluation/validation-protocol.md).
