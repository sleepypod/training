---
type: "Evaluation protocol"
title: "Validation protocol"
description: "Separate exploratory tuning from honest held-out evaluation."
status: "draft"
generated: {"by": "codex/gpt-6", "at": "2026-10-20T23:09:59.032076+00:00"}
sources: [{"resource": "/references/training-run.md", "title": "Training run provenance"}]
---

# Validation protocol

Run2 uses outer leave-one-night-group-out evaluation. Each outer training partition uses three-fold GroupKFold to select among four fixed candidates by four-class macro F1, breaking ties with κ. Fit preprocessing and model parameters without held-out labels. Production TypeScript computes feature rows; runtime smoothing and transition handling are reproduced in order, preserving unlabeled context.

Group sessions by noon-to-noon device-local dates. Do not randomly split adjacent epochs across train and test. Whole-night normalization and centered windows make this a retrospective task, even when labels are held out.

For the next experiment, freeze the candidate and evaluation protocol before collecting future paired nights. Keep them untouched until the planned evaluation. For multiple participants, hold out whole people for population generalization and separately measure personal adaptation on later nights. Report coverage, per-stage recall, confusion matrix, macro F1, κ, and night/participant variability. Compare against both fixed rules and always-light.

Model promotion requires predeclared acceptance criteria, especially wake performance, feature parity, and device resource measurements. Numeric thresholds have not yet been agreed. Watch labels are a reference signal, not polysomnography ground truth.
