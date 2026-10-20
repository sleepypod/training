---
type: "Proposed data policy"
title: "Paired participant data"
description: "Requirements for additional Watch and pod training data."
status: "draft"
generated: {"by": "codex/gpt-6", "at": "2026-10-20T23:09:59.032076+00:00"}
sources: [{"resource": "/references/training-run.md", "title": "Training run provenance"}]
---

# Paired participant data

Other people's paired Watch and pod recordings can support population training. Watch-only records cannot teach the relationship between pod inputs and stage labels without corresponding pod observations.

Proposed intake record: pseudonymous participant ID, consent and permitted-use record, session ID, device/side mapping, timestamps with timezone and clock-offset handling, hardware and firmware versions, processing/source revisions, feature contract, reference device/software and label provenance, quality flags, and missingness. Keep identity mapping separate from analytical data. Consent must cover the intended training use; agree retention and withdrawal handling before intake.

Store raw exports, biometric databases, per-epoch rows, fitted artifacts, and identity mappings in access-controlled storage outside Git. Ignore rules are only an accidental-commit guard, not access control. Review aggregates before any public release. No data collection, upload, or public release is configured by this knowledge repository.

Use whole-person holdouts for generalization and later-night holdouts for personalization. Watch stages remain imperfect reference labels. See [validation](../evaluation/validation-protocol.md).
