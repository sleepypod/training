# Training loop: plan

Drafted 2026-11-22. Status: proposed; `scripts/loop.py` implements steps 2–4 and the replay.
One participant, left side, Apple Watch stages as the reference. Results quoted here are
retrospective aggregates, not validation.

## Summary

Train on every development night with a fixed protocol, freeze the result before the nights
it will be judged on, score it on those nights once, then let them join training. Every night
ends up both training a model and scoring one it has never seen. The every-fourth-night
[locked holdout](../../knowledge/evaluation/locked-holdout.md) stays as the last gate, read
once per finished candidate.

Cross-validation alone overstates a model (nested macro F1 0.538 against 0.503 on the
2026-11-18 holdout night for run `archive-20261122`), because every night that has produced a
score we reacted to has shaped later choices. Moving the lockout around has the same problem:
eventually every night has been seen. A night is unseen once, so the loop uses it once while
it is unseen.

## The loop

Weekly, or whenever a few new Watch nights have arrived:

1. **Ingest.** The launchd agent pulls the archive daily (`scripts/install-archive-agent.sh`).
   Add a Health export when Watch stages did not reach the pod: on 2026-11-22 the archive had
   no stages for 11-19 and 11-21, and the export filled both.
2. **Train** with `train.py` on the archive snapshot copied into the run, same contract and
   candidates every time. Do not tune the protocol on the nights about to be scored.
3. **Freeze.** `loop.py freeze RUN/model` records the candidate's SHA-256 (core's
   `stageModelSha256`), training cutoff and `trainedAt` in `data/runs/loop/frozen.jsonl`.
4. **Score.** `loop.py score FEATURES` scores every registered model on the development night
   groups after its cutoff, and marks a night prospective for a model when every session in it
   began after the model was trained. Comparisons are paired per night with a night-level
   bootstrap (10,000 resamples, seed 42) and a sign test, for macro F1, κ and wake F1.
   `stage-backtest.py --trial` confirms the deployed models on the pod itself.
5. **Promote** only when a candidate beats the deployed model on nights prospective for both,
   with the bootstrap interval of the macro F1 difference above 0, over at least 15 nights.
   Then read the locked holdout once (`--holdout-final`) as the last check, and ask before any
   upload or selection.
6. **Fold in.** Next round, those nights train the next candidate.

Locked nights never enter `loop.py`. A candidate that is not deployed reads them only in its
one `--holdout-final`.

## How many nights a decision needs

On unseen nights, per-night macro F1 differences between two models vary by about ±0.04
(the [cross-fitted fusion rerun](../../knowledge/evaluation/fusion-cross-fitted-2026-11-15.md):
+0.030, 95% [+0.001, +0.055] over 9 nights). Resolving a 0.03 difference takes about 15–20
paired nights, about three weeks of Watch nights. Smaller gaps on fewer nights are ties.

## Replay

`loop.py replay` runs the same mechanism on past nights to choose a cadence: refit every N days
on the groups before the refit date with `retrain-models.py`'s nested selection, score the groups
until the next refit, and score frozen models on the groups after their cutoffs. It compares
mechanisms, not models, because these nights have shaped earlier choices.

### Replay 2026-11-22

Input: run `archive-20261122` features (off-pod archive snapshot plus the 2026-11-22 Health
export), 31 development night groups scored from 2026-10-17 to 11-21 after 8 training groups;
locked 11-14 and 11-18 never entered. Frozen: id 2 (`podcontext-20261103`, trained through 11-01)
and id 3 (`podcontext-export-20261114`, trained through 11-13). Paired per night:

| Comparison | Nights | Δ macro F1 [95%] | Δ κ | Better–worse |
| --- | ---: | --- | ---: | --- |
| nightly refit vs weekly refit | 31 | +0.007 [−0.009, +0.025] | +0.009 | 13–11 |
| nightly refit vs id 2 | 16 | −0.010 [−0.025, +0.006] | −0.020 | 6–9 |
| weekly refit vs id 2 | 16 | +0.003 [−0.012, +0.019] | +0.005 | 8–8 |
| nightly refit vs id 3 | 6 | −0.004 [−0.021, +0.012] | −0.005 | 3–2 |
| weekly refit vs id 3 | 6 | +0.004 [+0.000, +0.011] | +0.012 | 1–0 |
| id 2 vs id 3 | 6 | +0.006 [−0.018, +0.031] | +0.014 | 3–3 |

- Everything ties. Refitting nightly is no better than weekly, so weekly is the cadence.
- Retraining with more nights did not beat id 2, frozen after 23 groups, on the 16 nights after
  it: models refitted on up to 37 groups scored the same. Nightly-refit macro F1 rose from 0.44
  on the first 7 test nights (8–14 training groups) to 0.53 from 11-03 on, so the gain came
  before about 20 groups and has been flat since. More nights of the same features and labels
  are not what limits these models; features and labels are (see below).
- The weekly refit of 2026-11-14 saw the same nights as id 3 and selected the same candidate;
  it matched id 3 exactly on 5 of 6 nights, a check that replay reproduces the export.
- Wake F1 averaged 0.03–0.06 per night in every arm.

First live `score` the same day (`data/runs/loop/scores.json`): id 2 vs id 3 on the 6 nights
prospective for both, +0.006 macro F1 [−0.018, +0.031], 3–3. Candidate `cand-20261122`
(run `archive-20261122/final`, trained through 11-21) has its first scorable night on 11-23.

## The Watch as teacher

The Watch agrees with polysomnography at four-stage κ 0.60 on healthy sleepers and labels about
half of wake; Core absorbs a third of real wake and half of real deep
([Apple Watch validation](../../knowledge/references/apple-watch-validation.md)). So:

- Differences below about 0.03 macro F1 are within the teacher's own noise; treat them as ties.
- Wake recall near 0 is partly a label problem. Test wake on pod evidence the Watch does not
  stage from: bed entry and exit (capacitance), and heart-rate rises without movement. Watch
  motion helps flag wrist movement inside Watch Core, but the Watch stages partly from it, so it
  can't recover still-but-awake time.
- The wake-first two-step model was the only arm that clearly helped on unseen nights (+0.030,
  8 of 9), and needs core support (sleepypod-core-138) before a pod can run it.

## When more data comes

- **More of this participant's nights:** the same loop. Near 100 nights the candidate set can
  grow (sequence models, wake-first with more features), with the change frozen before the
  nights that judge it.
- **Other people:** hold out whole people for a population model, keep the later-night split
  for personal adaptation ([paired participant data](../../knowledge/data/paired-watch-pod.md)).
- **A second teacher** (for example a ring with better wake detection) gives consensus labels
  where devices agree; `reference_devices.py` already keeps teachers separate per signal.
