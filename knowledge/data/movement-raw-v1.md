---
type: "Data contract"
title: "Movement model contract movementRaw-v1"
description: "Inputs, features, output and file format a pod needs to run the movement model exactly as training does."
status: "draft"
generated: {"by": "claude-code", "at": "2026-11-15T23:37:02.016138+00:00"}
sources: [{"resource": "https://github.com/sleepypod/training/blob/main/scripts/movement.py", "title": "Window features (piezo_features, capsense_features, FEATURE_NAMES)"}, {"resource": "https://github.com/sleepypod/training/blob/main/scripts/movement_model.py", "title": "Model, thresholds and inference windows (session_windows, epoch_features)"}, {"resource": "https://github.com/sleepypod/training/blob/main/scripts/movement_portable.py", "title": "Portable forest export and evaluator"}, {"resource": "https://github.com/sleepypod/training/blob/main/scripts/verify-movement.ts", "title": "TypeScript evaluator and checks"}]
---

# Movement model contract movementRaw-v1

`movement_model.py train` (or `export` for an existing `model.joblib`) writes `model/movement.json`, format `movement-model-v1`. The model is a random forest trained on 2-second windows of raw piezo and capacitance plus the pod's own movement score, labelled by Watch motion. Watch motion is the label only and never an input. This page is what core needs to compute the same inputs on the pod (`sleepypod-core-132`). The Python in `scripts/movement.py` and `scripts/movement_model.py` defines the contract; where this page and the code disagree, the code wins and this page is wrong.

## Inputs

- **Piezo**: `piezo-dual` records, channel `<side>1` for the occupant and `<other>1` for the other side, int32 little-endian samples at 500 Hz. The lost-sample marker (int32 max) is missing. A run of at most 5 lost samples inside one second is filled by linear interpolation within that second; a longer run, or a missing second, is a gap. When a second arrives twice, the first copy is kept.
- **Capacitance**: `capSense2` records with an integer `ts`. Values 0–5 are the sensing channels (6–7 are the reference pair and are ignored). The first record of a whole second is stamped `ts`, every later one `ts + 0.5`. A record without at least 6 values for a side does not count for that side.
- **Pod score**: the side's `movement` rows (`total_movement`). A row stamps the **end** of its minute: the row stamped `s` covers `[s − 60, s)`.

## Windows

A window covers `[t, t + 2)` seconds (`windowSeconds` 2). Inference steps windows by `hopSeconds` (2) from the start of each 5-minute epoch, `t = start, start + 2, …` while `t + 2 ≤ start + 300`, so 150 windows per epoch. Training windows stepped by 1 s; that only changes how many windows there are, not how each one is computed.

A window is **skipped**, not scored, when either piezo channel has a gap anywhere in seconds `floor(t)` to `ceil(t + 2) − 1`, or either side has fewer than 2 capacitance records in the window. The epoch's `coverage` is scored windows ÷ 150.

## Features (21 columns, in this order)

For each piezo channel (occupant first, then other): take the window's 1,000 samples (first sample at `round((t − floor(t)) × 500)` into that run), subtract their mean, and compute:

| Column | Definition |
| --- | --- |
| `band0.5-4`, `band4-10`, `band10-30`, `band30-100`, `band100-245` | `log10(Σ P(f) + 1)` over FFT bins with `a ≤ f < b` Hz, where `P = |rfft(x · hann)|²` and `hann[n] = 0.5 − 0.5 cos(2πn / 999)` (numpy `hanning`, symmetric). Bins are 0.5 Hz apart; DC is excluded |
| `burst` | `log10(max |Δx| / (median |Δx| + 1) + 1)`, `Δx` the 999 first differences |
| `shift` | `log10(|mean(last 125) − mean(first 125)| + 1)` |
| `logvar` | `log10(var(x) + 1)`, population variance |

Columns are `occ_<name>` for the 8 occupant values, then `other_<name>`. Then capacitance, occupant side first: `occ_cap_motion`, `occ_cap_shift`, `other_cap_motion`, `other_cap_shift`. Over the records with `t ≤ time < t + 2`, in time order, `cap_motion = Σ |v[k+1] − v[k]|` over consecutive records and all 6 channels, and `cap_shift = Σ |v[last] − v[first]|` over the 6 channels. Piezo units are raw ADC counts; capacitance is the raw value.

The last column is `podScore`: the `total_movement` of the row stamped `s` with `s − 60 ≤ t + 1 < s` (the minute containing the window centre). With no such row it is **missing** (`null` in JSON). It is the only column that may be missing. Training used only windows that had a pod score, so a missing score follows the routing the forest exported for it (below), which reproduces scikit-learn.

## Output

- Per window: class probabilities in `classes` order (`light`, `still`, `vigorous`); `P(moving) = 1 − P(still)`.
- A window is moving when `P(moving) > strictThreshold`, **strictly greater**, in evaluation and inference. A window exactly at the threshold is still. `strictThreshold` is the 99th percentile of out-of-bag P(moving) over still training windows, so about 1% of still windows are flagged (`STRICT_FALSE_ALARM`). `threshold` is the balanced-accuracy cutoff (`≥`), reported for evaluation only.
- Per 5-minute epoch over its scored windows: `mmActive` (share moving), `mmMoving` (mean P(moving)), `mmPeak` (max P(moving)). An epoch with no scored window has `null` for all three and coverage 0, never 0.

## Evaluating the forest

Trees use the stage-model arrays (`personal-stage-model.ts`): node `i` splits on `f[i]` at `t[i]`; `f[i] = −1` is a leaf. Two rules are needed to match scikit-learn, and the export checks both:

1. Round every input to float32 first (`Math.fround`), then compare `x ≤ t[i]` in double precision. Without it, 12 of 133,059 training windows of the first real model differ by up to 0.0045.
2. A missing input goes left when `m[i] = 1`, otherwise right. Routing every missing pod score right instead changed every window, by up to 0.37.

A leaf's probabilities are its votes `v[i]` divided by their sum; the forest's probabilities are the mean over trees. Internal nodes have empty `v`.

## Integrity

- `sha256` is the SHA-256 of the document without its `sha256` field, serialized as core's `finite-binary64-json-v1` (`src/biometrics/algorithms/canonical-json.ts`): sorted keys and exact decimal numbers.
- Up to 64 `testVectors` hold inputs (with `null` pod scores), the expected probabilities and the moving flag. They cover both sides of each threshold, every label and predicted class, and missing pod scores. A pod must reproduce every probability within 1e-9 and every flag, or refuse the model and fall back to `total_movement`.
- `movement_model.py` refuses to write the file unless its evaluator matches scikit-learn's `predict_proba` within 1e-9 on every training window, both as trained and with the pod score removed. `scripts/verify-movement.ts` repeats the hash and vector checks in TypeScript.

## Size

The first real model (200 trees, depth 12, leaf 20; 7 Watch-motion nights, 133,059 windows; measured 2026-11-15) has 200,628 nodes. The file is 9.4 MB of JSON (3.7 MB gzipped); leaf votes are 4.8 MB of it and thresholds 2.2 MB. Core `dev` sets no byte limit on stage-model uploads, but a movement model needs its own route and storage (`sleepypod-core-133`). The hyperparameters were frozen before evaluation and stay as they are.

## Limits

One participant on the left side; labels are wrist-motion heuristics; Watch–pod clock alignment is unverified. Raw sample timing and capacitance cadence are as recorded by the current firmware; a firmware change that alters either needs a new contract version.
