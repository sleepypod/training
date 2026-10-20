---
type: "Research note"
title: "Interpreting model metrics"
description: "Accuracy, \u03ba, and macro F1 answer different questions."
status: "draft"
generated: {"by": "codex/gpt-6", "at": "2026-10-20T23:09:59.032076+00:00"}
sources: [{"resource": "/references/training-run.md", "title": "Training run provenance"}]
---

# Interpreting model metrics

Cohen's κ adjusts observed agreement for agreement expected from the predicted and reference class frequencies: `κ = (observed − expected) / (1 − expected)`. Zero means chance-level agreement under that marginal-frequency model; one means perfect agreement. It is not a probability that a prediction is correct.

Accuracy can reward always predicting the dominant light-sleep class. Macro F1 averages class-specific F1 equally, so minority stages matter. Per-class recall exposes missed wake/deep/REM epochs. Always report sample counts and coverage alongside scores; comparisons across different epochs or feature contracts are not directly interchangeable.

See the [actual results](findings-2026-10-20.md) for the class imbalance and baseline comparison.
