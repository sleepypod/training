# Training knowledge guidance

Start at `knowledge/index.md`, then read relevant concepts and their source evidence.
The knowledge bundle follows OKF v0.2 pinned in `knowledge/references/okf.md`.
Edit these authored concepts directly; they are not generated core wiki files.

Preserve provenance and uncertainty. Distinguish measured results, historical
observations, hypotheses, and proposed policies. Never invent verification or
clinical validity. Do not upgrade a draft to stable just because lint passes.
Keep raw data, per-epoch records, identities, private paths, and fitted artifacts
out of Git. Review aggregate evidence before publication.

Training implementation remains in core. Do not fork feature extraction formulas;
link source revisions and explicitly plan any migration. Knowledge edits do not
authorize model activation or external publishing.

Check existing worktrees before creating one. Default new worktrees to
`<primary-repo-root>/.codex/worktrees/<unique-task-slug>`. Ignore only
`/.codex/worktrees/` in Git's local info/exclude; preserve unrelated changes.
Run `uv run --locked python scripts/validate_knowledge.py` before committing.
Use conventional commit subjects.
