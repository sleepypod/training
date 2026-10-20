---
type: "Research note"
title: "Training and pod deployment"
description: "Portable inference is feasible; training and activation remain separate."
status: "draft"
generated: {"by": "codex/gpt-6", "at": "2026-10-20T23:09:59.032076+00:00"}
sources: [{"resource": "/references/training-run.md"}, {"resource": "Local 2026-10-20 pod benchmark of older core-fit four-feature forest; historical session record, harness not retained in this bundle"}]
---

# Training and pod deployment

Train offline using the source scripts and locked Python dependencies recorded in [provenance](../references/training-run.md). This repository currently owns research knowledge and aggregate evidence; the training implementation remains in core pending deliberate migration.

The pod can directly evaluate an exported forest as JSON tree arrays through the TypeScript runtime. It needs the matching feature contract and postprocessing, not a Python training environment. Validate artifact schema, class/feature order, parity vectors, and resource use before activation. CoreML is an alternate client artifact, not necessary for pod inference.

A historical ARM64 pod benchmark of the older 100-tree, depth-6 model measured 488,137 JSON bytes, 14.8 ms initial load, and 11.1 ms for 96 predictions, with about 780,704 bytes retained heap growth. Process RSS was about 60 MB and is not model-only memory. These measurements excluded database access and feature extraction and do not benchmark the new 200-tree challenger. Repeat end-to-end measurements for the actual candidate.

A model may eventually replace stage-scoring heuristics once validated. Hardware safety and actuator rules remain independent. Preserve a versioned fallback and rollback path. Publishing a model artifact and activating it are distinct operations; knowledge updates do neither.
