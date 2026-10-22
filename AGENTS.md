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

## Working with the pod

The pod serves an unauthenticated LAN API on port 3000 (`/api/...`, OpenAPI routes
from sleepypod-core) and an MCP server at `http://<pod>:3000/api/mcp`. Ask the user
for the address; it is shown in the iOS app's Settings. Never port-forward or expose it.

- Reads are fine: `scripts/fetch_pod.py` routes (`biometrics/*`, `settings`),
  `GET /api/export/archive?startTs=&endTs=&include=raw` for raw piezo, `GET /api/system/storage`,
  `GET /api/sleep-stages/model[s]?side=`. `POST /api/sleep-stages/model/preview` is read-only.
- Ask before every write, even when a previous write was approved: temperature and power
  (`/api/device/*`), Watch imports (`POST /api/biometrics/watch-upload`), model uploads,
  model selection or activation, settings, schedules and deletes.
- Raw data: `GET /api/raw/files` is shadowed by the raw-file route and returns 400. Finished
  frames reach the export about 30–45 minutes after recording. The disk pruner deletes the
  oldest archives above 80% of `/persistent`; check `GET /api/system/storage` before relying
  on old nights.
- The MCP server has no tool for stage-model upload; use the REST routes below.

## Trained models

Training writes `RUN/model/candidate.json` locally and never uploads or activates it.
Do not upload, select or activate a model unless the user asks for that step.

- Preview first: `POST /api/sleep-stages/model/preview` with `{side, nights, model}`.
  Scores on training nights are in-sample; say so when reporting them.
- Add to the library with `POST /api/sleep-stages/models` (stored, not selected), then
  select with `PUT /api/sleep-stages/models/selected {side, id, active}`.
- Do not use `PUT /api/sleep-stages/model` to try a model: on an active side it replaces
  the model staging nights immediately.
- One side at a time. Report the previous selection so the user can switch back with
  `PUT /api/sleep-stages/model/active {side, active:false}` or the same `models/selected` call.

