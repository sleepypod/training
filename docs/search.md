# Model search specs

`scripts/search.py` searches the pod model graph (plan F, `docs/plans/model-graph-plan.md`)
on a fixed evaluation protocol. A spec names the inputs, the choices for each node and one
primary comparison. The protocol itself is fixed in code, not in the spec. Search never
contacts a pod, uploads or activates anything.

```sh
uv run --locked python scripts/search.py plan SPEC.json --out data/runs/search-NAME   # expand, prepare inputs, check caches, print cost
uv run --locked python scripts/search.py run data/runs/search-NAME --jobs 4           # score every candidate on development nights
uv run --locked python scripts/search.py report data/runs/search-NAME                 # ranking, layers, the pre-registered comparison
uv run --locked python scripts/search.py final data/runs/search-NAME --candidate ID    # one locked-holdout read; writes model/set.json
```

## Spec

```json
{
  "name": "br-and-wake-2026-12",
  "side": "left",
  "timezone": "America/Los_Angeles",
  "inputs": {
    "snapshot": "data/archive/POD_ID/biometrics.db",
    "watch": "data/watch/watch.json",
    "watchVitals": "data/watch/watch-vitals.json",
    "archive": "data/archive/POD_ID",
    "core": "../sleepypod-core",
    "exports": { "2026-10-18": "data/raw/…/session-1184.tar.gz" },
    "features": { "podNative": "data/runs/RUN/features.json" },
    "nights": ["2026-10-18", "…"]
  },
  "primary": { "candidate": "replayed+none+podContext", "baseline": "live+none+podContext", "metric": "macroF1" },
  "space": {
    "vitals": [
      { "id": "live", "profile": "live" },
      { "id": "c2", "engine": "<manifest sha256>", "profile": "<resolved config sha256>" },
      { "id": "br", "config": "data/profiles/br.json" }
    ],
    "movementModel": [
      "none",
      { "id": "movementRaw-v1", "model": "data/runs/movement-model/model.joblib", "epochs": ["data/runs/movement-epochs.json"] }
    ],
    "stage": [
      { "id": "podContext", "contract": "podContext", "model": "balanced_leaf5" },
      { "id": "wakeFirst", "contract": "podNative", "groups": ["context", "movement", "arousal", "quality"], "model": "hierarchical" }
    ]
  },
  "budget": { "maxCandidates": 24 }
}
```

Paths are absolute or relative to the working directory; `plan` records them absolute.

- **inputs.** `snapshot` is the live database, usually the archive's `biometrics.db`.
  `watch` is `parse-watch-export.py` output, which holds the stage labels. `watchVitals` is
  `parse-watch-export.py --vitals` output and is optional; with it, the vitals layer gets
  scored. `archive`, `core` and `exports` (pre-archive nights, `NIGHT=tarball`) only fill
  in the printed replay command. `features` reuses an existing export for live vitals; it
  must have come from this snapshot and Watch file. `nights` restricts the night groups.
- **vitals.**
  - `profile: "live"` uses the stored rows: mixed versions, `training.inputs: live`.
  - A replayed profile is named either by `engine` and `profile` hashes, or by `config`
    (`defaults`, or a profile JSON in any shape core's `replay --config` accepts). A
    `config` is matched to cached replays of the same overrides.
- **movementModel.** `none`, or a `movement_model.py train` model with one or more
  `movement_model.py epochs` outputs (cross-fitted, `movement-epochs-v2`).
  `"dropUncovered": true` excludes sessions without raw exports instead of blocking the
  run; plan lists them.
- **stage.** `contract` is `podNative` or `podContext` (computed by core's TypeScript).
  `groups` adds the Python feature groups `context`, `movement`, `arousal` and `quality`
  (`feature-ablation.py`, `vitals_reference.py`). `model` is `balanced_leaf5` (the frozen
  forest) or `hierarchical` (wake first). Movement-model columns are added whenever the
  candidate's movement model is not `none`.
- **Candidates** are one choice per node, named `vitals+movementModel+stage`. The space
  may not exceed `budget.maxCandidates`, which defaults to 24.
- **primary** names two candidates (or `pod_live` as the baseline) and one of `macroF1`,
  `kappa`, `wakeF1`, `lightF1`, `deepF1` or `remF1`. It is the only pre-registered
  comparison. The winner and every other comparison are reported as exploratory, next to
  the number of candidates compared.

## Protocol (fixed in code)

- Night groups are the unit. Every candidate is scored on the same sessions: those every
  node choice covers. Plan lists each excluded session with its reason.
- Locked holdout nights (`holdout.py`) are dropped by `plan`, `run` and `report`. Only
  `final` reads them, once per run. It fits the chosen candidate on development nights,
  appends the read to `data/holdout-access.jsonl` before scoring, and refuses a second
  final or a candidate not in the run. Holdout scores go to `final.json` and the terminal,
  never to `report.json`, which dashboards read. A movement model taught by Watch motion
  on a locked night is refused.
- Stages walk forward: before each test night, the model is refit on every earlier night,
  with at least `MIN_TRAIN_GROUPS` (8). Scored epochs are the labelled epochs that also
  have a live pod stage when the snapshot has pod stages, as in `fusion.py`.
- Movement features are cross-fitted (`fusion.movement_rows`): when night T is tested,
  every night's movement columns come from models never taught by T's Watch motion. The
  movement layer score is the model's own leave-one-night-out report: strict moving recall
  at 1% still false alarms, against the pod score.
- The vitals layer scores fixed profiles, with nothing fitted. Per night, it reports HR
  and breathing MAE and HRV Spearman against Watch vitals on the same epochs
  (`vitals_reference.py` rules), with paired error reductions against the first vitals
  choice.
- Statistics: paired per-night differences against the named baseline; a 95% interval
  from a night-level bootstrap (10,000 resamples, seed 42); a sign test.
- Provenance in `plan.json`: CLI command, script SHA-256s, input hashes (snapshot, Watch,
  readings per night, movement epochs and model, archive cursor), night lists per split,
  and the protocol with its SHA-256. Runs under `data/` are registered in the run catalog
  as kind `search`.

## Caches

Each node's output is computed once per (night, node version, upstream versions) under
`data/cache/` in the primary checkout (`--cache` to move it):

| Path | Key |
| --- | --- |
| `vitals/<night>-<engine12>-<profile12>.json` | core replay readings (`derive_snapshot.py ingest`) |
| `snapshots/<key>/biometrics.db` | derived snapshot: source SHA-256, engine, profile, nights and readings |
| `features/<key>.json` | snapshot, pod-stages and acquisition sidecars, Watch file, side, timezone, contract, exporter and vendor hashes |
| `movementModel/<model12>/<session>.json` | cross-fitted epochs per session for one model file |

When a profile's readings are not cached, `plan` prints the exact
`pnpm biometrics:local-fit replay --archive …` command and the `derive_snapshot.py ingest`
that follows, and `run` refuses to start. Search never runs core itself. After the replay,
plan again into a new directory.

## E2: replayed against live inputs (prepared, not run)

[`search/e2-replayed-vs-live.json`](search/e2-replayed-vs-live.json) is the pre-registered
E2 comparison:
- macro F1, walk-forward;
- podContext `balanced_leaf5`, replayed vitals (one engine, `defaults` profile) against
  live rows;
- the same nights: those with raw, so about 24.

When C2 has pinned the left side's profile, replace `defaults` with that profile's JSON
before the first run; the spec must not change after it. Running it waits for core's
`replay --archive` and a populated archive (A1). Replace `POD_ID` with the archive
directory name first.

```sh
# 1. Watch labels and vitals for the archived nights
uv run --locked python scripts/parse-watch-export.py data/export.zip data/watch/watch.json
uv run --locked python scripts/parse-watch-export.py data/export.zip data/watch/watch-vitals.json --vitals
# 2. plan prints the replay commands, because nothing is cached yet; run them
uv run --locked python scripts/search.py plan docs/search/e2-replayed-vs-live.json --out data/runs/e2-plan
(cd ../sleepypod-core && pnpm biometrics:local-fit replay --archive <training>/data/archive/POD_ID --nights all --side left \
  --timezone America/Los_Angeles --config defaults --compare-stored --export 2026-10-18=<…>/session-1184.tar.gz \
  --export 2026-10-19=<…>/session-1194.tar.gz <training>/data/replays/e2-replayed-vs-live-replayed)
uv run --locked python scripts/derive_snapshot.py ingest data/replays/e2-replayed-vs-live-replayed
# 3. plan again (nights failing core's parity gate or without RAW are listed as excluded), run, report
uv run --locked python scripts/search.py plan docs/search/e2-replayed-vs-live.json --out data/runs/e2-20261201
uv run --locked python scripts/search.py run data/runs/e2-20261201 --jobs 2
uv run --locked python scripts/search.py report data/runs/e2-20261201
# 4. Only if the primary interval excludes zero: one locked-holdout read, then set.json with inputs "replayed"
uv run --locked python scripts/search.py final data/runs/e2-20261201 --candidate replayed+none+podContext
```

A win means the mixed-versions finding mattered. A tie means it didn't, and replay is still
kept for C4 and F. `final` writes `RUN/model/candidate.json` and `RUN/model/set.json` and
stops there. Uploading stays manual (AGENTS.md).

## Reproducing fusion.py

[`search/fusion-xfit.json`](search/fusion-xfit.json) holds the `fusion.py` arms as search
candidates on the same features, snapshot and cross-fitted movement epochs:

| fusion arm | candidate |
| --- | --- |
| `base`, `all`, `all_quality` | `live+none+base`, `live+none+all`, `live+none+all_quality` |
| `all_movemodel`, `fused`, `hierarchical_fused` | `live+movementRaw-v1+all`, `live+movementRaw-v1+all_quality`, `live+movementRaw-v1+hierarchical_quality` |

Checked on 2026-11-15 against `fusion-20261115-xfit` (same features, snapshot and
movement-model hashes). On every arm, and on `pod_live`, the walk-forward pooled macro F1
matched `fusion.py`'s `schedules.walk-forward.pooled` to four decimals, on the same 1,165
scored epochs over 24 night groups. The same space widened to 24 candidates (six group
sets × two models × two movement choices) took a mean of 8 s per candidate and about 1
minute of wall time on 4 jobs (Apple M4), with vitals cached.
