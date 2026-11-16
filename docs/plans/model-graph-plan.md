# Pod model graph: plan

Drafted 2026-11-15 from the 2026-11-14/15 training session; built out the same day after
checking core `dev`, the pod and local data. Status: accepted 2026-11-15; implemented 2026-11-16 (see Implementation status). The owner took the
recommended answer to every decision (Q1–Q7), and the other sleeper consented to archiving (Q2).
Results quoted below are one participant's retrospective aggregates, not validation.

## Summary

Raw piezo and capacitance are the pod's only true source. Everything else is a model
over them: vitals (heart rate, breathing, HRV), movement, and the sleep-stage
classifier that consumes their outputs. Each layer has its own Apple Watch teacher.
The plan is to treat this as a versioned **model graph**, keep the raw data that
feeds it, and search for better models against fixed, honest evaluation.

```
                 ┌─► vitals models (HR, BR, HRV) ──────────┐
raw piezo ───────┤                                          ├─► stage classifier ─► stages
+ capacitance    └─► movement model ────────────────────────┘
```

| Layer | Pod input | Watch teacher | Today |
| --- | --- | --- | --- |
| Heart rate | raw piezo | Watch HR | Good: MAE 2.3 bpm, r 0.71 |
| Breathing rate | raw piezo | Watch respiratory rate | Weak: r 0.09; reads ~1.4/min high from spikes; half the Watch's REM signal |
| HRV | raw piezo | Watch SDNN (rank only) | Not yet assessed beyond rank |
| Movement | piezo + capacitance (+ pod score) | Watch motion | Model beats pod score: 35% vs 10% moving recall at 1% still false alarms; 7 labeled nights |
| Stage classifier | outputs above | Watch stages | podContext ties the previous model on 9 unseen nights |

The vitals rows mix nights on a tuned profile with nights on default settings (see findings),
so re-measure them after C2 before treating them as a verdict on the extractors.

Most of the plumbing already exists in core: an off-pod archiver for the analysis export and
a laptop replay of the pod's own vitals engine. The new work is mostly glue (an archive
index, replayed training inputs), a model-set contract, and a search harness with a locked
holdout. The urgent item is A1: the pod is pruning about one night of raw per day.

## Implementation status (2026-11-16)

Every item not gated by data or a pending result is implemented. Training PRs are merged;
core and iOS PRs are open for review (they change what the pod and phone run).

| Item | State | Where |
| --- | --- | --- |
| A1 + B3 archive | Done: first pull 2,477 RAW, 1.1M rows; nightly launchd agent at 12:30 | core #925 (`--curl`), training #25 |
| A1b, A3 index, lineage, Watch coverage | Done; lineage from processing runs (core #930 identity) | training #25, #30 |
| A2 nightly Watch motion | Code done; 7-night on-device check pending | iOS #109 |
| A5 locked holdout | Done | training #26, #38 |
| A6 archive inputs | Done | training #28, #31 |
| B1/B2/B4/B5 dashboard | Done | training #32, #35, #37 |
| C1 profile rebase (Q1: auto-activate) | PR open | core #927 |
| C2 re-pin | Likely unneeded: #927 restores the saved overrides on the next deploy | — |
| C3 robust breathing display | PR open; floor may read ~0.7/min low once #927 deploys | core #926 |
| C4 breathing ranking | PR open | core #929 |
| C5 replay from archive + derived snapshot | Core PR open; training merged | core #928, training #27 |
| D1 movement export | Done | training #24 |
| D2–D4, E3 | Gated: ~20 Watch-motion nights | sleepypod-core-132/133/139, training-4 |
| E1 weekly trial | Tooling done; first scorable night 11-15; Q5 on 11-28 | training #29, training-18 |
| E2 replayed vs live | Run 2026-11-16: tie (+0.000 [−0.036, +0.037]); no export | knowledge/evaluation/e2-replayed-vitals-2026-11-16.md |
| E4 two-step format | Gated on an F final confirming it | sleepypod-core-138 |
| F search harness | Done; reproduces the cross-fitted fusion run exactly | training #34 |
| Model set + Q6 (`inputMismatch`) | PR open | core #930 |

Found while implementing: macOS Local Network privacy blocks node and Python (not `curl`) on
this machine, hence the curl transports (core #925, training #22); per-minute replay parity
cannot reach 0.1 bpm (C5 gate above); the stage-model evaluator compares float64 inputs where
sklearn uses float32 (sleepypod-core-140).

## Where things stand

### Code changes

| Change | Where | State |
| --- | --- | --- |
| Watch vitals parser, vitals reference, movement model, fusion evaluation | training PR #20 | Open |
| Calibration profile header layout fix | core PR #924 | Merged to `dev`; not on the pod (pod runs `main`) |
| podContext features on the phone | iOS PR #108 | Merged to `dev`; reaches the next TestFlight build |

### Already in core that this plan reuses

| Tool | Path (core `dev`) | What it gives the plan |
| --- | --- | --- |
| Analysis export | `GET /api/biometrics/analysis-export?start&end&raw=1` (`analysis-export-v1`) | Full `biometrics.db` snapshot plus RAW for a ≤14-day window. Served by the pod's build (checked 2026-11-15: `main@9246bbc3`, HTTP 200) |
| Off-pod archiver | `scripts/biometrics-archive/`, `pnpm biometrics:archive pull` | Verified, resumable pulls: content-addressed `raw/<sha256>.RAW.gz`, merged `biometrics.db` that never deletes rows, `pulls/` cursor, lock, 413 halving, 429 retry |
| Laptop vitals replay | `scripts/biometrics-local-fit/` (`local_replay.py` → unchanged `common.biometrics.replay_worker.run()`) | Pod-identical HR/BR/HRV from raw for any profile override set; `fit`, `sweep`, `parity` (±0.1 bpm vs pod) |

### Live on the pod (left side)

- Active stage model: library id **3**, `research-balanced_leaf5-20261115T014128`
  (podContext, 33 nights through 2026-11-13). Trial; compare new nights with the Watch.
- Revert: `PUT /api/sleep-stages/models/selected {"side":"left","id":2,"active":true}`.
- The pod restages every night on read, so past nights now show id 3.
- From 2026-11-14, the pod's live stages are in-sample for every night up to 2026-11-13.

### Data on hand (2026-11-15)

| Source | Nights | Notes |
| --- | --- | --- |
| Pod raw | 2026-10-20 → today | `GET /api/system/storage`: 26.9 days, 2,477 files, 6.73 GB raw, 75.8% of `/persistent` (pruner at 80%). Oldest was 10-19 a day earlier |
| Local raw exports | 24 nights, 2026-10-18 → 11-13 | `data/raw/sleep-20261114/` (26 sessions, 2.2 GB) and `data/raw/watch-2026110{2,3,4,6}.tar.gz`. 10-18 and 10-19 now exist only here |
| No raw anywhere | 9 nights, 2026-10-08 → 10-17 | Database rows only (`fusion-20261114/protocol.json` `excludedWithoutRawExport`); usable for podContext on live vitals, never for replay |
| Watch motion | 8 night keys (11-01 → 11-08); 7 labeled in the movement-model run | Labels for the movement model (`training-1`) |
| Archive | none | No `biometrics-archive` destination exists yet |

Raw runs about 250 MB per night (6.73 GB / 26.9 days), about 7.5 GB a month off the pod.

### Findings that shape the plan

- **Stage model:** podContext ≈ the previous model on nights it never saw (macro F1 0.545 vs 0.533,
  κ 0.576 vs 0.564, walk-forward). The wake-first fused model was best on those nights
  (+0.033 macro F1, 8 of 9 nights better), but many arms were compared; treat as promising.
- **Movement features did not improve staging** on 24 nights; wake F1 fell to 0. Wake epochs are too few.
- **Breathing:** the Watch sits at the pod's lower envelope; the pod's excess is upward spikes in
  unstable minutes. A robust floor fixes the level but not the stage signal.
- **Vitals profile:** left side has been on default vitals settings since 2026-11-01
  (`PROFILE_INCOMPATIBLE`). Deploys dropped it to defaults four times (10-19, 10-21, 10-30, 11-01).
- **Mixed upstream versions:** stage training nights 10-08…11-13 span at least six vitals-profile
  changes, so the classifier learned from inputs produced by different vitals models.
- **Raw retention:** nothing syncs raw off the pod automatically, and the oldest night goes each
  day. Replay-based training (C5, E2) starts with the 24 nights that have raw, not 33.

## Principles

1. **Watch signals teach; they never feed a deployed model.** Every inference input comes from the pod.
   Watch motion is never a stage feature (Watch stages derive partly from it). A movement model
   taught by Watch motion is still a proxy for it, so a stage gain from movement-model features
   may be agreement with how the Watch stages rather than better staging; report it with that caveat.
2. **Every node is versioned; every consumer pins its upstream versions.** A model set activates
   and rolls back as a unit. A missing upstream falls back to rules, never to silence.
3. **Train from raw, replayed through pinned versions**, not from database rows produced by
   whatever profile was live that night.
4. **Labels are scarce; evaluation is fixed before searching.** Locked holdout, walk-forward,
   every candidate logged.
5. **One public codebase.** The training code stays in this repo and runs as plain CLI scripts;
   any UI is a front end over them. Data stays in ignored `data/`.
6. **Reuse core's tools; don't fork them.** Archive pulls and vitals replay run core's code from
   a core checkout. Training adds indexes and adapters around their outputs. A gap in a core
   tool is fixed in core.

## The model graph

### Nodes

| Node | Implementation | Version identity | Runs on | Output |
| --- | --- | --- | --- | --- |
| `raw` | firmware `piezo-dual` + `capSense2` frames | RAW file SHA-256 | pod | `archive/*.RAW.gz` |
| `vitals` | piezo-processor engine + the side's biometrics profile | `engine` = biometrics manifest hash (`modules/common/biometrics/manifest.v1.json`); `profile` = resolved config hash of the side's runtime config generation | pod live; laptop via `replay_worker` | per-minute HR, BR, HRV + quality (`vitals`, `vitals_quality`) |
| `movementScore` | pod `total_movement` rule | core build commit | pod | per-minute `movement` |
| `movementModel` | `movementRaw-v1` forest (D1) | exported `version` + SHA-256 | laptop now; pod after D2 | per-minute active fraction and mean P(moving) |
| `stage` | stage model JSON (`personal-stage-model.ts` schema) or rules | model `version` + SHA-256; `featureContract` name | pod and phone | 5-minute stages |

Edges are declared by the consumer: a node lists the upstream versions it was trained on.
A stage model on `podContext` reads `vitals` and `movementScore`; a stage model on
`podContextMovement-v1` also reads `movementModel`.

### Model set manifest (`modelset-v1`)

Training writes one per exported set next to its models (`RUN/model/set.json`); core stores it
when the set is uploaded (`sleepypod-core-133`). Proposed shape:

```json
{
  "formatVersion": "modelset-v1",
  "id": "left-podContextMovement-20261201T0300",
  "nodes": {
    "vitals": { "engine": "<manifest sha256>", "profile": "<config sha256>", "binding": "expected" },
    "movementModel": { "version": "movementRaw-v1-20261201", "sha256": "<file sha256>", "binding": "pinned" },
    "stage": { "version": "research-…", "sha256": "<core dataHash of the stored model>", "contract": "podContextMovement-v1", "binding": "pinned" }
  },
  "training": {
    "nights": { "first": "2026-10-18", "last": "2026-11-30", "count": 38, "lockedExcluded": 4 },
    "inputs": "replayed",
    "archive": { "podId": "<id>", "cursor": "<pull cursor end>", "databaseSha256": "<sha256>" }
  },
  "evaluation": { "run": "<run catalog id>", "protocolSha256": "<sha256>", "holdoutScored": true }
}
```

- `pinned`: the pod refuses to activate the set unless that exact SHA-256 is installed, and
  activates and rolls back pinned nodes together. A stage model's SHA-256 is core's `dataHash`
  of the model as the pod stores it (canonical JSON, `epochSeconds` defaulted to 300), not the
  file's bytes (core #930; `scripts/modelset.py` reproduces it).
- `expected`: vitals stay owned by biometrics tuning, not by the set, because a profile change
  must not need a stage-model upload. The pod records a mismatch instead of refusing (Q6).
- `inputs` is `replayed` (C5, one pinned engine and profile for every night) or `live`
  (database rows, mixed versions). A `live` set says so in Settings.

### Night lineage

For every archived night, `scripts/lineage.py` (new, A1b) records which version of each node
produced the stored outputs:

- `vitals.engine` and `vitals.profile` from each vitals row's processing run
  (`vitals.processing_run_id` → `biometric_processing_runs`: bundle manifest hash and config hash),
  the same identity core uses for `inputMismatch`; a night with more than one pair is `mixed`, one
  with no recorded run `unknown`. Runtime receipts are only a fallback: they miss a drop to
  defaults (generation 0 writes none);
- core build from the pull record's verbatim manifest (`build.commitHash`);
- stage output version: the selection at read time, since the pod restages on read.

The lineage is what lets C5 decide which nights need replay. It also lets F report "trained
on N nights from M vitals versions", and it turns the mixed-versions finding into a measured
count instead of an estimate.

### Fallbacks

| Failure | Result |
| --- | --- |
| No raw for a minute | vitals and movement-model output `null` for that minute, never 0 (existing contract) |
| Movement model missing or its test vectors fail | movement display uses `total_movement`; a set needing `movementModel` stages that night by rules, and `stagedBy` names why |
| Stage model missing or contract unknown | rules (existing) |
| Live vitals version ≠ set's `expected` | Q6 (recommended: stage normally and flag `inputMismatch`) |

## Evaluation protocol

Fixed before any search; F and every stage script use it.

**Units.** A night group (calendar night, as in `stage-backtest.py`) is the unit for every split.
Epochs of one night never sit on both sides of a split.

**Splits.**

- *Locked holdout* (A5, Q4): from 2026-11-14, every fourth night. Locked nights are excluded
  from training, tuning, feature selection and dashboards' metric views. Two kinds of read only:
  frozen models already deployed (E1), and one final score per search run (F). Every read is
  appended to `data/holdout-access.jsonl` (run id, model SHA-256, nights, UTC time).
- *Walk-forward*: expanding window, refit before each test night, at least 8 training night
  groups (`MIN_TRAIN_GROUPS`). Primary for the stage layer.
- *Leave-one-night-out*: for the movement and vitals layers, where order matters less and
  labelled nights are few.
- *Cross-fitting for stacked layers*: an upstream model feeding a held-out night was never trained
  on that night's teacher (the `fusion.py` / `movement_model.py epochs` rule).

**Metrics.**

| Layer | Primary | Also reported |
| --- | --- | --- |
| Heart rate | per-minute MAE vs Watch, common support | r, coverage of Watch minutes, bias |
| Breathing rate | per-minute MAE vs Watch | r, bias, REM−light difference in pooled SD vs the Watch's |
| HRV | Spearman ρ vs Watch SDNN (different measures; rank only) | coverage |
| Movement | moving recall at 1% still false alarms (`STRICT_FALSE_ALARM`) | 5-minute epoch agreement |
| Stages | macro F1 | κ, per-class F1 (wake first), light↔REM and light↔deep confusion cells, TST and WASO error in minutes |

**Statistics.** Per-night paired differences against the named baseline; 95% interval from a
night-level bootstrap (10,000 resamples, fixed seed); sign-test count of nights better. Each run
names one primary comparison before it starts. Every other comparison is exploratory, and the
report gives the number of candidates compared next to the winner.

**Provenance.** Every run records its CLI command, script SHA-256s, input hashes (archive cursor
and database SHA-256, or snapshot SHA-256), night lists per split, and protocol. It is registered
in the run catalog (`run_history.py`, `data/run-history.sqlite`). `pod_live` counts only on
nights after its model's training cutoff.

## Workstreams

### A. Data (start now)

| # | Work | Tracking | Depends on |
| --- | --- | --- | --- |
| A1 | Archive with core's CLI: `pnpm biometrics:archive pull --pod 192.168.1.88 --dest <training>/data/archive`; first run backfills from 2026-10-20 | new | Q2 |
| A1b | `scripts/archive_index.py` + `scripts/lineage.py`: night index over the archive with lineage, Watch coverage and pruning risk; folds in the local pre-archive exports | new | A1 |
| A2 | Nightly Watch motion capture by default in the iOS app | `training-1` | — |
| A3 | Watch teachers per night: confirm each night's Watch data reached the pod and so the archive; sidecar import of an Apple Health export for nights that didn't | new | A1b |
| A4 | Second participant: the other side wears a Watch, for the first generalization test | decision Q3 | — |
| A5 | Locked holdout: freeze date plus the rule for which later nights are locked; holdout access log | decision Q4 | — |
| A6 | Point training scripts at the archive: `--snapshot data/archive/<podId>/biometrics.db`; raw resolver so `movement_model.py epochs` and `raw_piezo.py` read archive `.RAW.gz` files as well as `/api/export/archive` tarballs | new | A1b |

**A1.** No new pull code: core's archiver already verifies `Content-Length` and manifest hashes,
stages before committing, keeps raw content-addressed, merges each full-database snapshot into
one `biometrics.db`, and resumes from its cursor. Training needs a core checkout (path in
`data/workspace-settings.json`) at or after the pod's build. A snapshot from a newer pod build
is refused with "update the checkout".

Acceptance:
- first pull archives 2026-10-20 → now in two 14-day windows; the 2026-10-20 night is present
  before the pod prunes it
- a second pull the same day reports `newRaw: 0`
- a killed pull leaves the cursor unchanged and the next run completes it
- `data/archive/` stays untracked (`data/` is ignored); `git status` clean after a pull
- the nightly schedule (B3) is installed in the same change

**A1b.** One row per sleep record per side, written to `data/archive/<podId>/index.json` and
printed as a table:

| Column | Source |
| --- | --- |
| night, side, in-bed hours | `sleep_records` |
| raw coverage % of in-bed seconds | `archive_raw_files` + RAW frame timestamps |
| raw source | `archive` or `local-export:<path>` (the 24 pre-archive nights) |
| Watch teachers | `reference_stages`, `reference_vitals`/`reference_samples` (HR, BR, SDNN), `watch_motion_captures` |
| vitals engine, profile, `mixed` | lineage |
| locked | A5 rule |
| at risk | raw only on the pod and the night is within 3 days of `rawHistory.oldest` |

Acceptance: the index lists 2026-10-18 and 10-19 with `local-export` raw; 2026-10-08 → 10-17
with raw coverage 0; at-risk nights are printed first; the script reads only local files.

**A3.** The export already carries the Watch tables for every night the phone uploaded
(`POST /api/biometrics/watch-upload`). A3 lists nights where a teacher is missing and imports
`parse-watch-export.py` output as `data/archive/<podId>/watch/<night>.json`. It never writes
to the pod. The index prefers the pod's rows and marks sidecar nights.

**A5.** Costs nothing to set, and every model comparison made before it spends unseen nights,
so it does not wait for the search harness. Frozen models (E1) may be scored on locked nights.
Acceptance: `scripts/holdout.py` exposes `locked(night)` and `log_access(...)`; every stage
script drops locked nights unless passed `--holdout-final`; the rule and freeze date are
recorded in `knowledge/` with this plan's date.

**Q2 before the first pull.** Every export contains both sides' full database and raw
waveforms, so the other sleeper's consent is needed before A1 runs at all.

### B. Local training app (extends `scripts/dashboard.py`)

| # | Work | Depends on |
| --- | --- | --- |
| B1 | Sync on open and every 6 h while open: spawn the A1 command in the background; skip when the archive lock is held | A1 |
| B2 | Data view from `index.json`: archived nights, Watch coverage per teacher, lineage, locked nights, nights at risk, age of the last successful pull | A1b, A3 |
| B3 | Nightly background sync: launchd agent at 12:30 running the A1 command (core README's plist), installed by `scripts/install-archive-agent.sh`; one click in the dashboard runs it | A1 |
| B4 | Train/Evaluate actions running existing scripts, one job at a time, with logs; each run records its CLI command | — |
| B5 | Model-search jobs and comparison view | F |

Localhost only. No hosted version and no training on the pod. B1 only protects nights while
the dashboard is open; B3 is what stops raw from being pruned unarchived, so it ships with A1.

The dashboard is read-only HTTP today (`/api/runs`). B4 adds `POST /api/jobs` bound to
127.0.0.1 with a per-launch token in the page, runs one subprocess at a time, and writes
`data/web-jobs/<id>/{command.json,log.txt}`. Jobs are registered in the run catalog like CLI
runs. A job that would upload or activate a model doesn't exist: those stay manual
(AGENTS.md). B2 alerts when the last successful pull is more than 3 days old, which is
how a broken nightly agent gets noticed before raw is lost.

### C. Vitals layer

| # | Work | Tracking | Depends on |
| --- | --- | --- | --- |
| C1 | Rebase saved profiles onto a new engine instead of silently falling back to defaults | `sleepypod-core-134` (P1) | decision Q1 |
| C2 | Re-pin the left side's profile on the current engine, then backtest it on the pod | — | C1 or manual |
| C3 | Robust breathing rate for display (floor or steady-minute mean) | new (core) | — |
| C4 | Better breathing extraction, tuned against Watch respiratory rate with `biometrics-local-fit sweep` | new (core + training) | A1b, C5, F |
| C5 | Vitals replay from raw with one pinned engine and profile for training inputs | new (core + training) | A1b |

**C2.** Pod writes need the user's approval at the time (AGENTS.md). Record the profile
revision and config hash in `knowledge/`; it becomes the `vitals.profile` that C5 replays
and that new sets expect.

**C3.** Display only: the stored per-minute value and the stage features are unchanged, so
no model retrains. Acceptance: the chart's breathing line on the 24 local-raw nights sits
within 0.5/min of the Watch median, versus about +1.4/min now.

**C5.** local-fit replays from a frozen calibration dataset (`/api/biometric-tuning/datasets/*/export`),
not from the archive. Add the archive as a second input in core instead of rebuilding the
worker request in Python:

1. core: `pnpm biometrics:local-fit replay --archive <dest>/<podId> --nights <list> --config <profile.json> out/`
   builds retained directories from `archive/*.RAW.gz` and the recorded context from the archive
   database, then writes `out/readings/<night>-<config>.json` (`--save-readings` format).
   Pre-archive nights replay from their local exports through the same path.
2. training: `scripts/derive_snapshot.py` copies the archive database and replaces `vitals` and
   `vitals_quality` rows for the replayed nights. `export-runtime-features.ts` and every stage
   script then run on the derived snapshot unchanged. The derived snapshot records its source
   database SHA-256, engine, profile and readings hashes in a `derived_snapshot` table.

Acceptance:
- training gate (core #928 `parity.trainingGate`), judged on nights whose stored rows all ran the
  replayed config, inside the night's sleep sessions where replay had RAW: ≥60 compared minutes,
  replayed/stored HR minutes 0.90–1.20, |trimmed bias| ≤0.25 bpm (minutes within 5 bpm), median
  |Δ| ≤0.6 bpm, ≥70% of minutes with both HRs within 1 bpm, ≤10% jumps over 5 bpm; a failing
  night keeps its stored rows. Recalibrated on 17 archived nights (16 pass with defaults). The originally planned 0.1 bpm on ≥95% of minutes cannot
  pass: live and replay average windows offset by up to 30 s each minute (2026-10-19: 13% within
  0.1, 75% within 1, bias −0.11). Thresholds rest on two nights; confirm on the first five gated
  archived nights. The 0.1 bpm check stays for pod-vs-laptop backtest parity (`local-fit parity`)
- all 24 raw nights replay with one engine and profile; the derived snapshot passes
  `export-runtime-features.ts` and `stage-backtest.py` on synthetic and real inputs
- replay outputs are cached by (night, engine, profile) under `data/cache/vitals/`

**C4.** A breathing sweep over `breathing_rate.*` overrides on the replayed nights.
local-fit ranks by HR error today, so core needs a `--rank breathing` key: BR MAE vs Watch
among candidates that keep ≥75% of the defaults' coverage. The REM−light separation goes
next to it in the report. Profiles that win go to the pod only as a candidate for C2-style
review.

### D. Movement layer

| # | Work | Tracking | Depends on |
| --- | --- | --- | --- |
| D1 | Export the movement model as a portable JSON forest with test vectors | `training-3` | — |
| D2 | Pod computes movement-model output in `sleep-detector` from raw piezo + capacitance; parity tests | `sleepypod-core-132` | D1 |
| D3 | Movement model library API, Settings section, pinning, pair activation | `sleepypod-core-133` | D2 |
| D4 | Movement chart uses model output, falling back to `total_movement` | new (core/iOS) | D2 |

Ship gate: about 20 Watch-motion nights (A2) before D2 goes live. D1 can ship now. The
retrain at 20 nights reruns `movement_model.py train` on the archive (A6) and must keep moving
recall at 1% false alarms above the pod score's by the same LONO protocol. D2 must also
record CPU per minute on the pod (`sleepypod-core-132`), since the pruner and the replay
worker already compete for it.

### E. Stage classifier

| # | Work | Tracking | Depends on |
| --- | --- | --- | --- |
| E1 | Keep podContext id 3 as a trial; weekly, score id 3 (live) and id 2 (`model/preview`) against the Watch on the same new nights, both out of sample | — | A5 |
| E2 | Retrain podContext on the derived snapshot (C5, one engine and profile); compare with the id 3 recipe on the same nights only | new | C5 |
| E3 | Train on movement-model outputs and export as a model set (movement pinned by version + hash) | `training-4` | A2, D1 |
| E4 | Wake-first two-step model: needs a two-step format in core and on the phone | new | F result |

**E1.** `scripts/stage-backtest.py` gains a `--trial` mode that reads nights after 2026-11-13
from the archive, scores `pod_live` (id 3) and `model/preview` of id 2 on the same nights, and
appends to `data/runs/trial-id3/weekly.jsonl`. Locked nights are included and logged (frozen
models). Q5 decides on 2026-11-28 from this file.

**E2.** The primary comparison is named in advance: macro F1, walk-forward, replayed inputs vs
live inputs, same nights, same forest (`balanced_leaf5`). A win means the mixed-versions
finding mattered. A tie means it didn't, and replay is still kept for C4 and F. Export only
if the bootstrap interval excludes zero; the set's `inputs` is then `replayed`.

Export gate for E3: walk-forward macro F1 gain over podContext with a bootstrap interval excluding
zero, for one arm named before the run (the interval is approximate and does not survive picking
the best of several arms).

**E4.** Core today evaluates one forest or logistic model per contract. The two-step format adds
`kind: "twoStep"`: a binary wake gate (forest, threshold) and a three-class sleep model. It
carries test vectors for both steps and the composed output. It needs parity tests in core
(`personal-stage-model.ts`) and on the phone (iOS #108 path) before any upload. Only if F
confirms it on the locked holdout.

### F. Model search harness

`scripts/search.py`, plain CLI, registered in the run catalog as kind `search`.

```
search.py plan   SPEC.json --out RUN     # expand the space, check nights and caches, print cost
search.py run    RUN [--jobs N]          # score every candidate on development nights
search.py report RUN                     # ranking, layer scores, primary comparison
search.py final  RUN --candidate ID      # score once on the locked holdout; logs access; exports set.json
```

Spec:

```json
{
  "name": "br-and-wake-2026-12",
  "primary": { "candidate": "replayed-twoStep", "baseline": "live-podContext", "metric": "macroF1" },
  "space": {
    "vitals": [ { "profile": "live" }, { "profile": "c2" }, { "profile": "c2", "overrides": "grids/br.json" } ],
    "movementModel": [ "none", "movementRaw-v1" ],
    "stage": [ { "contract": "podContext", "model": "balanced_leaf5" }, { "contract": "podContext", "model": "twoStep" } ]
  },
  "budget": { "maxCandidates": 24 }
}
```

- A candidate is one choice per node. Each node's output is computed once per (night, node
  version, upstream versions) and cached under `data/cache/<node>/`, so vitals replay (the
  slow step) runs once per profile, not once per candidate.
- Scored per layer against its Watch teacher and end to end against Watch stages (the protocol
  above), walk-forward and leave-one-night-out, cross-fitted for stacked layers.
- `final` refuses a second final on the same run, and refuses a candidate not in the run.
  The report states how many candidates were compared and how many holdout reads the
  holdout has had in total.
- `final` writes `model/set.json` (`modelset-v1`) and the models. It never uploads.
- Later: end-to-end raw-to-stages models, once enough nights exist (rough bar: 60 labelled
  nights, two participants).

Acceptance for the first version: runs on the synthetic pipeline in CI; reproduces the
`fusion-20261114` arms' macro F1 within 0.005 when given the same nights and inputs; a 24-candidate
spec on 24 nights finishes overnight on the laptop with vitals cached.

### G. Phone

- podContext on the phone: done (iOS #108). Verify on TestFlight that Settings → Pod model
  shows id 3 and Sleep matches the pod's stages.
- Model sets (D3/E3) need the phone to fetch both models and compute movement features,
  or to read the pod's movement output. Recommended: read the pod's per-minute movement-model
  output (D2 stores it next to `total_movement`), so the phone never runs the movement forest
  and phone/pod parity is limited to the stage model. Decide when D2 lands.
- E4 needs the phone to evaluate `twoStep`; same test vectors as core.

## Epic and dependencies

`training-2` (epic: pod model set) depends on `sleepypod-core-133` and `training-4`.

```
training-3 ─► sleepypod-core-132 ─► sleepypod-core-133 ─┐
training-1 ─┐                                            ├─► training-2
training-3 ─┴─► training-4 ──────────────────────────────┘
sleepypod-core-134 (independent, P1)
```

Proposed: restructure `training-2` as the model-graph epic (vitals nodes included):

```
A1 ─► A1b ─┬─► A3 ─► B2
           ├─► A6 ─► training-4 (retrain on archive)
           └─► C5 (core replay --archive) ─► derive_snapshot ─┬─► E2
                                                              └─► F ─┬─► C4
A5 ─► E1                                                             └─► E4 (core twoStep + iOS)
A5 ─────────────────────────────────────────────────────────────────► F final
sleepypod-core-134 ─► C2 ─► C5 profile
```

## Sequence

**Now (this week)**
1. Q2 consent, then A1 + B3: first pull today; the 2026-10-20 night is next to be pruned.
2. A5 holdout rule, after decision Q4, before any further model comparison.
3. Merge training PR #20.
4. C1 profile rebase fix, after decision Q1; then C2.
5. A2 nightly Watch motion capture.

**Next (≈2 weeks)**
6. A1b index and lineage, A6 archive inputs, A3 Watch coverage, B1, B2.
7. E1 weekly trial scoring; Q5 on 2026-11-28.
8. D1 movement export; C3 robust breathing display.
9. C5 replay from the archive (core `replay --archive`, training `derive_snapshot.py`); E2.

**Later**
10. B4 training actions; F search harness, final pick scored on the A5 holdout; C4 breathing sweep.
11. D2–D4 and E3 once about 20 Watch-motion nights exist (12 more after 11-08; early December
    if A2 starts this week and some nights miss coverage).
12. E4 two-step format if F confirms it.

## Decisions

Decided 2026-11-15: each recommendation below was accepted as written.

| # | Decision | Recommendation (accepted) |
| --- | --- | --- |
| Q1 | Rebased vitals profile: activate automatically, or save as a candidate to confirm | Auto-activate compatible overrides; keep the warning for dropped ones |
| Q2 | Archive the other side's data | The export always contains both sides, so the choice is consent, not scope: ask the other sleeper before the first archive run |
| Q3 | Second participant wears a Watch | Yes, if willing |
| Q4 | Holdout freeze date and rule | Freeze at 2026-11-14, the first night no model has trained on; lock every fourth night after it so training data keeps growing |
| Q5 | Keep podContext id 3 after the trial | After two weeks of new nights, keep it unless id 2 beats it on those nights (E1) |
| Q6 | Stage model when live vitals differ from the set's `expected` version | Stage normally and flag `inputMismatch` in `stagedBy` and Settings; falling back to rules on every profile change would punish tuning |
| Q7 | Archive location | `data/archive/` in the training checkout (ignored), with an optional second copy on a NAS by running the same CLI with another `--dest`; not in iCloud or any synced folder |

## Risks

| Risk | Effect | Mitigation |
| --- | --- | --- |
| Nightly pull stops silently (laptop asleep, core checkout older than the pod's schema) | Raw pruned unarchived after ~27 days | Cursor resumes after gaps; B2 alerts at 3 days; update the core checkout with each pod deploy |
| Archive growth, ~7.5 GB a month | Laptop disk | Raw is content-addressed; a NAS `--dest` when the laptop passes 100 GB |
| Holdout leaks through dashboards or ad hoc runs | Final scores no longer unseen | Stage scripts drop locked nights by default; dashboards hide their metrics; access log |
| Replay differs from the pod (platform wheels) | Training inputs don't match inference | C5 parity gate per night; replayed sets also scored on live inputs in E1 |
| Watch is the teacher for every layer | Models learn Watch behaviour, not physiology | Principle 1 caveats in every report; second participant (A4); no clinical claims |
| One participant, few wake epochs | Gains don't generalize; wake F1 unstable | Per-class reporting; bootstrap by night; A4 before any default-model change |
| Pod CPU (D2 per-minute forest, replay worker, pruner) | Live vitals delayed | D2 CPU measurement gate; replay stays on the laptop |

## Not doing

- **Swift/Core ML training.** The pod cannot run Core ML; Python keeps parity with core's vitals
  code; Create ML cannot express the evaluation; a Swift app limits sharing to Mac users.
- **Hosted training app.** It would need health data and the unauthenticated pod API off the LAN.
- **Training on the pod.** Too little CPU and disk; the pruner is near its threshold.
- **A second archiver in Python.** Core's archiver already covers verification, resume and
  merge; a parallel one would drift from the export format.
- **Automatic upload or activation.** Search and export end at `set.json`; uploads stay manual.

## Tickets to file after review

Terse bodies follow the ticket structure in CLAUDE.md when filed; priorities 0–4.

| Plan | Repo | Title | Kind | P | Depends on |
| --- | --- | --- | --- | --- | --- |
| A1+B3 | training | archive pod nightly with core biometrics-archive into data/archive | task | 0 | Q2 |
| A1b | training | archive night index with lineage, Watch coverage and pruning risk | feature | 1 | A1 |
| A3 | training | report missing Watch teachers per night; sidecar Apple Health import | feature | 2 | A1b |
| A5 | training | locked holdout rule and access log in stage scripts | task | 1 | Q4 |
| A6 | training | read archive snapshot and RAW.gz in training scripts | feature | 2 | A1b |
| B1/B2 | training | dashboard sync on open and archive data view | feature | 2 | A1b |
| B4 | training | dashboard job runner for existing training scripts | feature | 3 | — |
| C3 | core | robust breathing rate for display | feature | 2 | — |
| C5 core | core | biometrics-local-fit replay from off-pod archive | feature | 2 | A1 |
| C5 training | training | derived snapshot from replayed vitals | feature | 2 | C5 core |
| C4 | core | local-fit breathing ranking key | feature | 3 | C5 core |
| E1 | training | weekly trial scoring of pod model id 3 vs id 2 | task | 1 | A5 |
| E2 | training | retrain podContext on replayed vitals | task | 2 | C5 training |
| E4 | core, iOS | two-step stage model format with parity | feature | 3 | F |
| F | training | model search harness with holdout final | feature | 2 | A5, C5 training |
| D4 | core, iOS | movement chart from movement-model output | feature | 3 | `sleepypod-core-132` |
| — | training | restructure `training-2` as model-graph epic over the above | epic | 1 | — |
