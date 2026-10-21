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

## Query the pod and watch training

```sh
uv run --locked python scripts/train.py --pod 192.168.1.88 --side left --dashboard --verbose
```

Replace the example IP with your pod. The fetcher uses unauthenticated, read-only
HTTP on port 3000; `--pod hostname:port` also works. It reads the pod timezone,
completed sessions from the last 30 days, vitals, movement, imported Watch labels,
and the current stage rules. Use `--days 14` to change the window or `--timezone`
to explicitly override the pod setting. No MCP client or SSH is needed.

If Watch labels are not already imported on the pod, add
`--watch-export data/export.zip` or `--watch-json data/watch.json`. Fetching does
not import labels, modify settings, or activate a model.

Terminal progress shows each phase, completed night folds, model fits, and elapsed
time. `--verbose` keeps every event on its own terminal line. All runs also save
`events.jsonl` with the complete structured history. The dashboard shows:

- Response-body bytes, average download throughput, API calls, and unique input rows.
- Labeled/unlabeled feature epochs, class balance, and per-feature min/mean/max/std.
- Per-fit input rows (before/after oversampling), fit time, rows/s, and actual tree/node counts.
- A fit-time chart and per-night held-out macro F1 for nested selection versus the baseline.
- A scrolling activity feed with inner-validation scores and current evaluation phase.

Throughput measures wall-clock work for this run, not sensor cadence. Feature
matrix bytes describe float64 storage, not process memory. Per-night scores are
not a loss/learning curve, and partial results are not a final quality estimate.
Dashboard history is bounded (120 events, 80 fit timings); `events.jsonl` retains
all events. Feature summaries are descriptive only and do not affect fitting.
 `--dashboard` prints a loopback URL for a live browser view with progress,
model comparisons, selectable metrics, and confusion matrices. It stays open after
training until Ctrl-C. Without this flag the command exits when training finishes.
Both modes save `index.html` in the run directory; open it directly for an offline
interactive report. A failed or interrupted run retains its status and partial
files; retry into a new directory.

API reads are not an atomic database snapshot. The fetcher limits acquisition to
completed sessions, splits saturated API windows, checks side identity, detects
configuration/calibration changes, and retains response hashes in
`acquisition.json`. Concurrent historical backfills can still alter data between
reads. Use a consistent SQLite backup when atomic input consistency is required.
The current rules are applied retrospectively; they do not reconstruct historical
per-sample rule versions. Raw responses and reports remain under ignored `data/`.

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

- `watch.json` when parsing an export or fetching pod labels, plus `features.json`.
- `progress.json`, the full `events.jsonl` log, and an offline interactive `index.html` report. Pod acquisition
  also creates `biometrics.db`, `responses/`, and `acquisition.json`.
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

To also exercise pod fetching against a synthetic local HTTP server:

```sh
uv run --locked python scripts/smoke-pod.py data/smoke-inputs data/smoke-run/features.json data/pod-smoke-run
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
