---
type: "Evaluation protocol"
title: "Locked holdout"
description: "From the night of 2026-11-14, every fourth night group is locked: no training, tuning, feature selection or dashboard metric uses it. Only a deployed model's trial and one final read of a finished candidate may score it, and every read is logged."
status: "draft"
generated: {"by": "claude/opus-5.5", "at": "2026-11-15T23:40:44+00:00"}
sources: [{"resource": "docs/plans/model-graph-plan.md on branch docs/model-graph-plan, accepted 2026-11-15: Evaluation protocol, workstream A5, decision Q4", "title": "Pod model graph plan"}, {"resource": "scripts/holdout.py and the stage scripts that call it", "title": "Holdout rule and access log"}, {"resource": "/evaluation/validation-protocol.md", "title": "Validation protocol"}]
---

# Locked holdout

Every model comparison spends the nights it is scored on: once a night has steered a choice, its score is no longer an estimate for unseen nights. The locked holdout keeps a growing set of nights that no choice has seen. This is a proposed policy, set in the plan dated 2026-11-15 (decision Q4); it has not been reviewed.

## Rule

- Freeze: the night group of 2026-11-14, the first night no deployed or candidate model trained on.
- Locked: night groups on or after the freeze whose offset in days from it is a multiple of 4 (2026-11-14, 11-18, 11-22, 11-26, 11-30, …). The other three nights in four stay development nights, so training data keeps growing.
- Night groups are the exporter's local noon-to-noon dates (`YYYY-MM-DD`), as in every other split. A group that is not a date cannot be judged and stops the run.

## What may read a locked night

Training, tuning, candidate selection, feature selection and feature analysis never see locked nights, and dashboard metric views show "Locked holdout" in place of their scores. Two reads only:

1. **Trial of a deployed model.** A frozen model already running on the pod is scored on new nights, locked ones included, because nothing about it can change in response (plan E1). Purpose `trial`.
2. **Final read of a finished candidate.** After training and selection on development nights, the exported candidate is scored once on the locked nights (`retrain-models.py --holdout-final`, later the search harness's `final`). Purpose `final`.

Scripts that fit, tune or compare on the nights they score (walk-forward backtest, feature ablation, fusion, decoder ablation) refuse `--holdout-final`.

## Access log

Each read appends one line to `data/holdout-access.jsonl` in the primary checkout (ignored by Git) before any locked night is scored: run, model SHA-256, the locked nights read, purpose, the rule, and UTC time. The count of lines is how many times the holdout has been looked at; a final score is only as unseen as that count allows. Every stage run also records the rule and the locked groups it dropped under `holdout` in its protocol.

## Limits

- The movement model (`movement_model.py`) does not apply the holdout yet; its Watch-motion teachers may include locked nights, which `fusion.py` lists as a limitation.
- Nights before 2026-11-14 were all inspected during earlier work; they are development nights, not a holdout. See the [validation protocol](validation-protocol.md).
- One participant and Watch labels as reference: a holdout score is still not clinical validation.
