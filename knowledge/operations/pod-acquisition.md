---
type: "Training workflow"
title: "Pod acquisition and visual progress"
description: "Read-only LAN fetching, live progress, and local reports."
status: "draft"
generated: {"by": "codex/gpt-6", "at": "2026-10-21T05:58:40.712440+00:00"}
sources: [{"resource": "sleepypod/training: scripts/fetch_pod.py, scripts/train.py, scripts/dashboard.py, scripts/progress.py"}, {"resource": "sleepypod/core: src/server/routers/biometrics.ts and src/biometrics/profiles/overview.ts; inspected API contract on 2026-10-20"}]
---

# Pod acquisition and visual progress

Pass `--pod <ip> --side left` to the training CLI. It uses unauthenticated LAN HTTP, normally port 3000. No MCP client or SSH credentials are required. By default it fetches the last 30 days of completed sessions and uses the pod timezone; `--days` and `--timezone` override these choices. Imported Watch reference stages are used unless a local Watch export/JSON is supplied.

The fetcher reads settings, the current stage view and piezo calibration quality, session records, vitals, movement, and reference stages. It recursively splits saturated API time windows and deduplicates by row ID. Ambiguous saturation at a single timestamp fails instead of silently dropping rows. Wrong-side responses and detected configuration/calibration changes fail acquisition. Raw responses and hashes are retained locally with an acquisition manifest.

Acquisition consists of multiple HTTP requests and is not an atomic backup. Historical backfills can race these reads. Active sessions are excluded; current stage rules are applied retrospectively, not reconstructed historically. A consistent SQLite snapshot remains available when atomicity matters.

Terminal progress shows current phase, model fits, completed held-out folds, and elapsed time. The optional loopback dashboard polls progress and displays selectable model metrics and confusion matrices. It stays open until Ctrl-C and can only read the progress/report files; it cannot modify the pod or activate a model. Every run also saves an offline interactive HTML report. Interrupted and failed runs preserve their state and must be retried in a fresh directory.

Validation includes synthetic API pagination and side-isolation tests, a full synthetic HTTP-pod pipeline, and browser checks for live polling, selectors, offline reporting, and mobile layout. A read-only live pod check fetched 15 completed sessions, retained 10 labeled night groups, and passed portable parity on all 888 feature rows. This validates operation, not prospective model performance; no model activation occurred. Personal outputs remain in ignored storage.

Detailed telemetry now includes response-body download bytes/rate, unique input row counts, descriptive feature statistics and class balance, per-fit rows before/after oversampling, measured fit times and rows/s, actual forest tree/node counts, inner-validation scores, and completed held-out night scores. These measurements do not alter training or selection. Fit timing is not a loss curve; held-out points correspond to different nights and remain incomplete until evaluation finishes. Feature matrix size is float64 storage, not process RAM.

Every update is appended to ignored `events.jsonl`. The dashboard keeps a bounded recent history (120 events and 80 fits) and a scrolling log; `--verbose` retains terminal log lines instead of replacing the current status line. Saved reports embed the bounded telemetry so they remain useful offline.
