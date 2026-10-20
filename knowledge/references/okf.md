---
type: "Research note"
title: "Open Knowledge Format"
description: "Pinned upstream format and local authoring rules."
status: "draft"
generated: {"by": "codex/gpt-6", "at": "2026-10-20T23:09:59.032076+00:00"}
sources: [{"resource": "https://github.com/GoogleCloudPlatform/open-knowledge-format/blob/ad30107c31c06aec8a7d5636e0d1058118604e6f/SPEC.md", "title": "Official OKF specification v0.2"}]
---

# Open Knowledge Format

This bundle uses OKF v0.2. The specification is pinned to revision `ad30107c31c06aec8a7d5636e0d1058118604e6f`; upgrades require a deliberate review.

Concepts use YAML frontmatter and ordinary Markdown links. Reserved index and log files have their own structure. `generated` records authorship, not correctness. No initial concept has a `verified` attestation: structural lint does not verify scientific claims. Initial concepts are drafts pending review.

The local validator adds stricter repository checks for links and metadata; these are not extra requirements of the OKF standard. This repository is a local bundle, with no Google service or cloud ingestion configured.
