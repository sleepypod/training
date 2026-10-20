# Training knowledge guidance

Start at `knowledge/index.md`, then read relevant concepts and their source evidence.
The knowledge bundle follows OKF v0.2 pinned in `knowledge/references/okf.md`.
Edit these authored concepts directly; they are not generated core wiki files.

Preserve provenance and uncertainty. Distinguish measured results, historical
observations, hypotheses, and proposed policies. Never invent verification or
clinical validity. Do not upgrade a draft to stable just because lint passes.
Keep raw data, per-epoch records, identities, private paths, and fitted artifacts
out of Git. Review aggregate evidence before publication.

The runnable pipeline lives in scripts/. Production feature extraction and model
inference are pinned under vendor/core/ with source hashes. Do not edit vendored
formulas independently; update from a reviewed core revision and rerun parity.
Knowledge or training changes do not authorize model activation. Keep all local
inputs and outputs under ignored data/; only data/.gitkeep is tracked.

Check existing worktrees before creating one. Default new worktrees to
`<primary-repo-root>/.codex/worktrees/<unique-task-slug>`. Ignore only
`/.codex/worktrees/` in Git's local info/exclude; preserve unrelated changes.
Run the checks in README.md, including the synthetic end-to-end pipeline, for
training changes. Run `uv run --locked python scripts/validate_knowledge.py` for
knowledge edits. Never use real health data in CI.
Use conventional commit subjects.
