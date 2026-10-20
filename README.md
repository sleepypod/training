# Training

`sleepypod/training` — research knowledge and aggregate evidence for sleep-stage model development.
Start at [the knowledge index](knowledge/index.md).

The `knowledge/` directory is an Open Knowledge Format (OKF) v0.2 bundle,
using Google's [pinned specification](https://github.com/GoogleCloudPlatform/open-knowledge-format/blob/ad30107c31c06aec8a7d5636e0d1058118604e6f/SPEC.md).
It is ordinary Markdown with YAML metadata; no Google service is required.
Initial concepts are agent-authored drafts, with no human verification claimed.

## Scope

This repository captures findings, model comparisons, feature contracts,
evaluation protocols, deployment constraints, and research priorities.
Training code still lives in the source core commit identified in
[run provenance](knowledge/references/training-run.md). Reproducing it requires
authorized private inputs. This bundle does not train, upload, or activate models.

Raw health exports, per-epoch data, databases, and fitted model artifacts belong
outside Git. The retained aggregate evidence describes a single person and must
be reviewed before public release. The repository is hosted privately at [sleepypod/training](https://github.com/sleepypod/training).

## Validate

Install `uv`, then run:

```sh
uv sync --locked
uv run --locked python scripts/validate_knowledge.py
```

The validator checks local metadata and links, not the truth of scientific claims.
Its stricter authoring checks are repository policy, not additional OKF requirements.

## Update

Add or revise concepts with source provenance, update their `generated` metadata,
and record material changes in `knowledge/log.md`. Update directory indexes when
adding concepts. Keep observations dated; never treat historical device metadata
as current state. Add `verified` only after an actual review with a named actor,
timestamp, and clearly described scope. Do not infer verification from lint passing.
