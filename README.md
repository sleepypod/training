# Training

`sleepypod/training` — offline sleep-stage model training and an
[OKF knowledge base](knowledge/index.md). Hosted privately at
[sleepypod/training](https://github.com/sleepypod/training).

## Setup

Install Node.js 24 and `uv`, then from this repository:

```sh
npm ci
uv sync --locked
```

`package-lock.json` pins the TypeScript tooling and SQLite dependency. `uv.lock`
pins Python 3.11 numerical dependencies. Production features, rules, and portable
inference are [vendored from a pinned core revision](vendor/README.md), with checksums;
no sibling core checkout or unpublished commit is needed to run training.

## Train with your data

Put local inputs in `data/`. Only `data/.gitkeep` is tracked; all other contents,
including subfolders and generated outputs, are ignored. Raw data and fitted
artifacts must not be force-added. Git ignore rules do not encrypt local files.

Required inputs:

- A consistent `biometrics.db` SQLite backup containing sleep records, vitals,
  movement, runtime configuration, and calibration tables. See [snapshot instructions](docs/input-data.md).
- An Apple Health export ZIP, `export.xml`, or directory containing `export.xml`.
  The parser selects Watch sleep stages and always excludes SleepyPod writeback.
- The Watch wearer's pod side and the pod's IANA timezone.
- At least five independent night groups. Each retained session must last 3–14
  hours and have at least 24 labeled five-minute epochs. Training folds need enough
  class diversity to fit the classifiers. This workflow currently evaluates one
  participant; do not mix people into its night-only split scheme.

```sh
uv run --locked python scripts/train.py \
  --snapshot data/biometrics.db \
  --watch-export data/export.zip \
  --side left --timezone America/Los_Angeles \
  --out data/runs/experiment-001
```

The output directory must not already exist. Use `--source 'Watch'` to choose a
case-insensitive Health source-name substring. To reuse extracted labels, replace
`--watch-export` with `--watch-json data/watch.json` (schema in [input data](docs/input-data.md)).

The command checks the vendor snapshot, parses labels, exports production runtime
features, performs nested whole-night model selection, writes the candidate, and
checks all exported predictions against the TypeScript evaluator. It never uploads
or activates the model. It produces:

- `watch.json` when parsing an export, plus `features.json`.
- `model/report.json` and `model/protocol.json` with evaluation and provenance.
- `model/candidate.json` (pod-compatible model), `model/candidate.joblib` (Python fit),
  and `model/parity-inputs.json` (private verification rows).

Do not use these scores as prospective validation. Features use whole-night and
centered-window context, and this is a retrospective classifier. See the
[validation protocol](knowledge/evaluation/validation-protocol.md) and
[latest historical findings](knowledge/evaluation/findings-2026-10-20.md).

## Individual steps

```sh
uv run --locked python scripts/parse-watch-export.py data/export.zip data/watch.json
npm run features -- data/biometrics.db data/watch.json data/features.json left America/Los_Angeles
uv run --locked python scripts/retrain-models.py data/features.json --out data/runs/manual-model
npm run parity -- data/runs/manual-model
```

## Validate without personal data

```sh
uv run --locked python scripts/validate_knowledge.py
uv run --locked python scripts/check-vendor.py
uv run --locked python -m unittest discover -s scripts -p 'test_*.py'
npm run typecheck
uv run --locked python scripts/make-synthetic-inputs.py data/smoke-inputs
uv run --locked python scripts/train.py \
  --snapshot data/smoke-inputs/biometrics.db --watch-export data/smoke-inputs/export.zip \
  --side left --timezone UTC --out data/smoke-run
```

Synthetic fixtures are invented and deterministic. Use fresh directories to repeat
the smoke run. Their scores demonstrate plumbing only, not model quality. CI runs
these checks with synthetic inputs.

## Knowledge updates

The `knowledge/` bundle follows Google's [OKF v0.2 specification](https://github.com/GoogleCloudPlatform/open-knowledge-format/blob/ad30107c31c06aec8a7d5636e0d1058118604e6f/SPEC.md).
Concepts are agent-authored drafts unless actually reviewed; structure validation
does not verify scientific claims. Update source provenance, `generated` metadata,
directory indexes, and `knowledge/log.md` when changing concepts. Add `verified`
only after a real review, naming the reviewer and scope.

Aggregate evidence still describes personal data. Review it before public release.
