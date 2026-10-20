---
type: "Data contract"
title: "Feature contracts"
description: "Training and inference must share feature semantics and order."
status: "draft"
generated: {"by": "codex/gpt-6", "at": "2026-10-20T23:20:05.045039+00:00"}
sources: [{"resource": "/references/training-run.md", "title": "Training run provenance"}]
---

# Feature contracts

The production source is `src/lib/personal-stage-model.ts` at the [recorded source revision](../references/training-run.md).

- `challenger`: `hrZ`, `hrStdZ`, `motionZ`, `nightFraction`.
- `podNative`: those four, then `hrvRel`, `hrv15Rel`, `hr15StdRel`, `br15Std`, `motion15`, `minutesSinceBed`.

Epochs are 300 seconds. Use production outlier filtering, real bed-entry/exit boundaries, and independent movement data. Run2 exports production TypeScript features instead of reimplementing their definitions in Python. Assign midpoint Watch labels after features are computed, excluding conflicting labels. Training retained sessions of 3–14 hours with at least 24 labeled epochs.

Whole-night normalization and centered windows use future context. A live classifier needs a separately defined causal contract and separate evaluation. Exporting trees alone does not reproduce a classifier: feature units, missing-data behavior, column order, class order, smoothing, and transition rules must match.

Core remains the upstream owner of feature semantics. This repository now includes a byte-for-byte pinned copy under `vendor/core/`, with a commit and SHA-256 manifest. The standalone training exporter imports that copy directly. Update it from a reviewed core revision and rerun checks; do not independently edit the formulas. See [runnable pipeline](../operations/runnable-pipeline.md).
