# Overnight motion, snoring, and piezo comparison — implementation plan

Status: proposed implementation plan, 2026-10-21; revised the same day to gate on piezo bandwidth, fix storage, clock-sync, control and validation protocols, and defer Watch motion. Capture and model experiments are not implemented. Keep this work separate from the existing core chart/model PR, which remains awaiting feedback.

Companion specification: [capture and training contract](overnight-capture.md).

## Objective

Determine whether snoring events detected locally by the iPhone coincide with distinctive vibrations in the pod's left and right raw piezo channels. Build synchronized capture, comparison, training, and eventually validated pod-only detection. Side attribution is a separate hypothesis to evaluate, not an assumed capability.

## Sleep-staging learnings that shape this plan

From [light-sleep confusion, 2026-10-21](../knowledge/evaluation/light-sleep-confusion-2026-10-21.md). One participant; descriptive, not demonstrated improvements.

- The staging gap is light versus REM, then light versus deep. Held out, about half of Watch-REM and Watch-deep epochs are predicted light, and a quarter of light is predicted REM or deep. No current feature separates light from REM by more than about half a pooled standard deviation.
- REM may be distinguished by short-timescale signals: breathing irregularity over 1–2 minutes and very low movement with variable heart rate (a hypothesis in the finding, not demonstrated). The current 15-minute breathing feature barely separates REM. Raw 500 Hz piezo capture (section 3) is therefore also the input for staging research, not only snoring; record that as a secondary objective.
- Mean heart rate barely differs between sleep stages in current features. Capture should record per-epoch signal coverage and quality so missing or fallback values can be told apart from physiology.
- Watch sleep stages are derived partly from the Watch's own accelerometer. Using Watch motion as a stage-model feature while Watch stages are the label risks learning the Watch algorithm rather than sleep. Treat Watch motion as a reference or diagnostic signal for staging unless an independent label source exists.
- Snoring frequency is reported to differ by sleep stage in the literature. Whether pod-derived snore features help staging is a hypothesis to test under section 8, on the same held-out nights, reporting the light/REM and light/deep confusion cells, not only κ.
- Sequence decoding, context features and partial class weighting need no new capture. Run them on existing data in parallel with this plan so capture-derived features are compared against the strongest pod-only baseline.

## 0. Piezo bandwidth gate

Run this before building iPhone capture or the dashboard. The pod samples each piezo channel at 500 Hz (`SAMPLE_RATE` in core `modules/piezo-processor/main.py`), so the 16–30 Hz and 60–100 Hz bands sit below the 250 Hz Nyquist limit. Front-end filtering, ADC resolution and mattress coupling may still remove them; sampling rate alone does not establish bandwidth.

- Tool: `scripts/piezo_bandwidth.py` (`session`, `analyze`, `night`), reading raw files through `GET /api/export/archive`.
- Dump both raw channels for one full occupied night through the existing raw-data path, plus a short scripted session on the same bed.
- Scripted session, each step 60 s and timestamped by the `session` command: empty bed with pump off (or at its quietest), empty bed with pump running, occupant lying still, occupant humming low and sustained with head on the pillow, occupant imitating snoring, three knocks on the bed frame at the head and at the foot.
- Compute Welch spectra per channel and step, dropping the first 10 s and last 5 s of each step. Report band power in 16–30 Hz and 60–100 Hz, the noise-floor slope above 100 Hz, pump RPM and lost-sample counts. Empty-bed, pump-off is the sensor noise floor; lying still is the reference for the pass bar, because heartbeat and breathing harmonics already put occupied energy into 16–30 Hz.
- Pass bar, fixed in `PASS_BAR` before the session: hum or imitated snoring raises band power at least 6 dB above lying still on the occupant's channel. Every step used for scoring needs 80% of its expected samples, after shifting step windows by the measured pod clock offset; otherwise the result is incomplete. A band that fails is treated as empty.
- Knocks are scored separately: each of the two knock steps needs at least three events above 8 robust standard deviations of the lying-still signal above 20 Hz, on every channel present. A knock failure blocks the section 1 knock clock sync until a louder or closer knock passes; it does not change the band outcome.
- Use the overnight dump to measure the real compression ratio and pod write load for section 3. A one-hour development export on 2026-10-21 showed 2 channels (`left1`, `right1`) at 500 Hz, 100% second coverage, about 2% of seconds with lost samples (mostly 1–2 samples), and a 0.63 archive gzip ratio.

Outcomes:

- Both bands pass: proceed in the delivery order below.
- Only 16–30 Hz passes: restrict section 4 band rows and section 5 features to the low band; drop 60–100 Hz claims.
- Neither passes: stop pod-side snore detection. Section 5 drops its piezo arm and section 7 is dropped; iPhone snore events remain a standalone summary. Raw piezo capture continues only for the staging research objective (breathing irregularity sits well below 16 Hz).

Acceptance: committed aggregate report (spectra summaries, not raw samples) with the pass bar written before the session; raw dumps stay under ignored data/.

## 1. Shared recording contract

- Assign session IDs, participant aliases, bed-side mappings, and device metadata.
- Preserve UTC timestamps, sensor timing, sample rates, clock-offset estimates and uncertainty, and interruptions.
- Keep snore detection confidence separate from side-attribution confidence.
- Retain fine-grained events for analysis. Aggregate onto the existing model's exact 300-second training windows anchored to each sleep record's `entered_bed_at`; do not round to wall-clock boundaries.
- Missing data remains missing, not zero movement or no snoring.
- Version schemas and feature definitions. Retain source provenance and chunk hashes.

Clock sync uses a physical event, not only network time:

- At session start and end, knock three times on the bed frame within about 30 cm of the phone, about one second apart.
- The iPhone detects knock onsets in its in-memory audio envelope and persists only the onset timestamps; no audio is kept. The pod pipeline detects the same transients in both piezo channels.
- Offset is the median of matched onset differences; drift is the change from start to end, interpolated linearly across the night. Uncertainty is the spread of the three matched pairs plus the residual after drift correction.
- This measures the real sensor-to-sensor offset including audio and piezo pipeline delay, which a network time exchange cannot. Network offset estimates are still logged as a coarse prior and as a fallback when knocks are missed.
- A session with no matched knocks at either end is flagged unsynchronized and excluded from event-level comparison.

Acceptance: synthetic boundary, unit-conversion, DST, clock-jump, gap and duplicate-transfer tests; reproduce existing training epoch starts without changing current feature values; synthetic knock streams with injected offset and drift recover both within one piezo sample.

## 2. Opt-in iPhone snoring analysis

- Add Start/Stop overnight capture, microphone permission, recording status and morning summary.
- Process microphone buffers locally with Sound Analysis/Core ML.
- Persist only candidate-snore timestamps, confidence, detector version, knock-onset timestamps and recording coverage.
- Never write or upload raw audio, transcripts or audio embeddings. No audio-clip retention option in this implementation.
- Handle screen locking, calls, alarms, audio-route changes, interruptions and process termination.
- Start with Apple's built-in classifier (`SNClassifySoundRequest(classifierIdentifier: .version1)`). On macOS 26.7.1 its 303 known classifications include `snoring`, `breathing`, `speech` and `mechanical_fan`, with a 3 s window and 0.5 overlap. Confirm the same label on the target iOS version from `knownClassifications` at runtime and record the OS version with each detector version. Search for another existing classifier only if the calibration set below shows the built-in one is inadequate.
- Loudness alone is not snore detection; classifier confidence is not automatically a calibrated probability.

Validation set:

- Product capture keeps no audio, so it cannot measure detector precision in a real bedroom. Add a separate, explicitly consented calibration session outside the product capture flow.
- Script: imitated snoring at several loudness levels, normal speech, a fan, the pod pump, bedding movement, and silence, in the actual bedroom with the phone in its overnight position. Include partner snoring when a consenting partner is available.
- Audio is held only on the device running the session, labeled in segments, and deleted after labeling; keep only the labels, segment times and detector outputs. Verify deletion.
- Report precision, recall and false positives per hour of non-snore audio at the chosen threshold. Use the same session to pick that threshold before any piezo comparison.
- The calibration session covers validation and threshold choice only; its audio is deleted, so it cannot retrain a classifier. Retraining needs a separately consented, retained labeled-audio dataset and is out of scope for this plan.

Acceptance: physical-iPhone overnight testing; explicit denied/revoked permission and stop tests; verify storage, logging and network paths contain no audio; quantify battery use and capture coverage; calibration-session report with deletion verified.

## 3. Synchronized raw pod piezo

The pod already records the raw stream; this section reuses it rather than adding a capture path. In sleepypod-core:

- Firmware writes CBOR RAW frames to the `/persistent/biometrics` tmpfs (500 MB). Each `piezo-dual` record carries `ts` (whole Unix seconds), `freq`, and about 500 int32 samples per channel in `left1` and `right1`, plus `left2`/`right2` on some hardware.
- `modules/biometrics-archiver` gzips frames every 15 minutes into `/persistent/biometrics-archive/<seqno>.RAW.gz`.
- `sleepypod-biometrics-pruner` deletes the oldest archives whenever `/persistent` is above 80% used. It does not know whether a file has been retrieved.
- `GET /api/export/archive?startTs=&endTs=&include=raw` streams a tarball of `.RAW` and `.RAW.gz` files selected by file mtime; `modules/archive-push` can scp archives to a configured host instead.

New work:

- Retrieval: the training workspace pulls `/api/export/archive` for each session. A file's mtime is never before its data, so no start pad is needed. The end needs at least frame length plus archiver period (about 30–45 minutes): firmware rotates frames about every 15 minutes, finished frames wait in tmpfs `.pending/` (invisible to the export) until the next 15-minute archiver run, and each `.RAW.gz` gets its archive time as mtime. Pull at least one archiver cycle after the session ends, and check coverage against reconstructed record times, not file presence. It then filters records by reconstructed sample time and stores the files under ignored data/raw/ with SHA-256 checksums in the session manifest.
- Retention holds: before capture starts, place a hold on the session so the pruner skips archives overlapping it until the workspace confirms retrieval. Pruning resumes for held files only after acknowledgment or an explicit release. Holds are the only pod-side change needed: a small core PR to the pruner and an API to set and clear them. Core already uses "pinned" for the linker's `.pending` links, so do not reuse that name.
- Sample timing: `ts` has 1 s resolution, so reconstruct sample times from continuous sample counts across records and fit a linear clock per session; record lost-sample markers and gaps. The section 1 knocks then fix the phone/pod offset.
- Channels: preserve every channel present, including `left2`/`right2`, at native rate before any filtering. Record which channels the hardware provides in the session manifest.
- Context: record occupancy, movement, pump activity and available hardware-state signals from the same RAW stream and biometrics database to investigate interference.
- Never use transfer time as sample time. Keep raw recordings and derived private artifacts under ignored data/ directories.

Storage budget:

- Rate: 500 Hz × 4 bytes per channel = 2 kB/s, 7.2 MB/hour per channel. Two channels are about 115 MB per 8-hour night before gzip; four channels about 230 MB. On this 2-channel pod the whole RAW stream including other record types measured about 17 MB/hour, and 0.63 gzip puts the archive at roughly 11 MB/hour, about 86 MB per 8-hour night; the section 0 night confirms this. Core's frank drop-in cites about 1 GB/day of RAW (about 42 MB/hour) on Pod 5 variants, so plan for roughly 2–2.5× on `left2`/`right2` hardware.
- Pod: the existing archive on `/persistent` (about 10 GB free per the export route's staging note), bounded by the 80% pruner. Holds are capped at two nights; a third fails until one is retrieved or released. Measure free space before the first hold.
- Permanent home: the Mac training workspace under ignored data/raw/, never Git and never the phone. Retention there is per-session and user-deletable; derived aggregates are what get committed after review.

Control nights:

- Empty bed with the pump running through its normal overnight schedule, at least one night.
- Occupied night with no snoring according to the iPhone detector and the sleeper, at least one night.
- Use them to describe band energy under pump and occupancy alone and to set false-positive baselines for section 5. Recompute them after hardware or firmware changes.

Acceptance: the pruner never deletes a held, unretrieved archive (test on synthetic disk pressure); retrieval recovers from interruption without duplication or silent loss and checksums round-trip; reconstructed sample times match a synthetic stream with injected gaps and lost samples; channel identities verified; existing archiver, export and sensor processing unchanged.

## 4. Comparison workspace

Extend the training dashboard with an aligned timeline containing:

- iPhone candidate-snore events and confidence.
- Left/right piezo waveforms and spectrograms.
- Energy in the frequency bands that passed section 0, among 16–30 Hz and 60–100 Hz.
- Pod movement, pump activity and sleep stages; Watch motion once section 6 exists.
- Capture gaps, knock markers and synchronization uncertainty.

Support zooming into individual events and comparing periods with and without detected snoring. Add review labels for likely snore, movement interference, equipment noise and uncertain. Preserve label origin and reviewer metadata. Without retained audio, retrospective audio listening is unavailable; waveform review alone is not ground-truth verification.

Exit test for milestone 2, fixed and committed before looking at captured nights:

- Score, fixed in advance: log band power in the lower passing section 0 band (16–30 Hz if it passed), on the occupant's primary channel (`left1` or `right1`), over each 3 s detector window, normalized by that night's median over occupied, pump-off windows. One score, no choosing among bands or channels afterwards. Nothing is fitted, so every night is evaluation data.
- Nights: single-occupant nights only, until section 7 shows side attribution works.
- Positive windows: detector windows at or above the threshold fixed by the section 2 calibration session.
- Negative windows: detector confidence below half that threshold (a guard band against near-misses), same night, within 30 minutes of a positive window, with recording coverage on both sensors.
- Exclusions applied to both classes: pod movement, pump activity, and the 10 s around each.
- Unit of evidence: snore bouts (positive windows less than 30 s apart merge into one bout; a bout's score is its median window score), not windows.
- Minimum evidence: at least 5 nights, at least 3 of them with at least 5 bouts each. Report per-night AUCs alongside the pooled AUC.
- Interval: pooled bout-level AUC with a 95% bootstrap interval resampled by night.
- Pass: AUC at least 0.7 and lower bound above 0.5 → proceed to section 5. Fail: upper bound below 0.7 → record the result and stop pod-side snore detection as in the section 0 "neither passes" outcome. Otherwise inconclusive: collect more nights; do not stop and do not proceed.

Acceptance: synthetic synchronized events line up across all rows; gaps remain visible; exported windows reproduce the viewed intervals; no private data enters public screenshots or CI; the exit test result is reported against its pre-committed bar.

## 5. Snoring model experiments

- Begin with a spectral-feature baseline inspired by the Emfit research, then compare a small spectrogram-based neural network.
- Use only the bands that passed section 0 and only after the section 4 exit test passes.
- Evaluate piezo-only, audio-only and combined-sensor approaches separately.
- Treat iPhone predictions as provisional labels. Measure disagreement and label quality; the section 2 calibration set bounds detector precision but does not make agreement accuracy.
- Hold out whole nights and participants. Keep both sides of one recording in the same split.
- Report precision, recall, false positives per hour, coverage and performance during movement/pump activity, with control-night false-positive rates alongside. Report metrics against provisional labels as such.
- Validating the audio detector uses the section 2 calibration session. Retraining it requires a separately consented, retained labeled-audio dataset, outside this no-audio-retention product flow and out of scope here.

Acceptance: reproducible datasets/manifests and runs, leakage checks, held-out evaluation and an explicit account of label limitations. Do not transfer published Emfit accuracy claims to sleepypod.

## 6. Watch motion capture (deferred)

Deferred until after section 5. For snoring, pod movement already covers interference; for staging, Watch motion leaks the label. Build it only if section 5 shows a specific need that pod movement cannot meet, or an independent stage label source appears.

- Create a watchOS companion target; none was found in the inspected iOS project.
- Prototype CMSensorRecorder on physical hardware before committing to unattended overnight behavior.
- Add a user-started recording, Motion permission handling, and morning transfer through the iPhone.
- Preserve timestamped acceleration, units, sensor orientation metadata where available and coverage.
- Derive separately versioned motion features; never substitute wrist acceleration for existing pod movement features.

Acceptance: a written justification from section 5 results before starting; measure overnight availability and battery use; test app suspension, incomplete transfers and recovery. Automatic starts and live streaming are separate follow-up feasibility work.

## 7. Side attribution

- Begin with known single-occupant recordings, then paired-occupant sessions.
- Compare channel strength, timing and breathing-related patterns.
- Include simultaneous snoring, position changes and vibration transfer across the mattress.
- Output left, right, both or uncertain; avoid forced identity assignments.
- Evaluate attribution separately from snore detection. Bed-side assignment alone is not evidence that a particular sound originated on that side.

Acceptance: report attribution errors and abstention rate on independently verified cases, including both occupants and simultaneous events.

## 8. Packaging and product integration

- Export artifacts with preprocessing, units, input requirements, thresholds, versions and parity test vectors.
- Run audio inference on iPhone; benchmark piezo inference on the pod before choosing its runtime.
- Keep snore detection separate from existing sleep staging initially.
- Add snore events and nightly summaries to iOS and the pod after validation.
- Test whether snore features improve held-out sleep staging before introducing a new stage-model feature contract.
- Multimodal models must declare their required inference inputs and evaluated missing-sensor behavior; retain a usable pod-only baseline.
- Require explicit model activation. No automatic replacement of the current model and no apnea-diagnosis claims.

Acceptance: training/inference parity, bounded resource use, missing-input behavior, model compatibility checks and end-to-end UI/API verification.

## Delivery order

0. Piezo bandwidth gate (section 0) — one night of raw dumps plus the scripted hum and knock session; its outcome can cut later steps.
1. Contract, knock clock sync, storage budget, iPhone/pod capture, control nights and the iPhone calibration session.
2. Synchronized comparison dashboard — first usable milestone, finished when the section 4 exit test is run against its pre-committed bar.
3. Reproducible snoring model experiments, if the exit test passes.
4. Watch motion capture, only if justified by step 3.
5. Side-attribution experiments.
6. Validated model packaging and product integration.

In parallel from the start: sequence decoding, context features and partial class weighting on existing staging data.

The first milestone lets a user record a night and inspect microphone-derived snore events against both raw piezo channels while keeping audio ephemeral on the iPhone. Device validation gates claims of reliable overnight operation.

## Research references

- [Emfit snoring classification using SVM/CNN](https://jperezmacias.github.io/papers/EMBC17_perezmacias.pdf)
- [Detection and assessment of sleep-disordered breathing with Emfit](https://research.aalto.fi/en/publications/detection-and-assessment-of-sleep-disordered-breathing-with-emfit/)
- [Apple Sound Analysis](https://developer.apple.com/documentation/soundanalysis)
- [Apple built-in sound classifier request](https://developer.apple.com/documentation/soundanalysis/snclassifysoundrequest)
- [Apple sensor recorder](https://developer.apple.com/documentation/coremotion/cmsensorrecorder)
- [Apple audio recording category](https://developer.apple.com/documentation/avfaudio/avaudiosession/category-swift.struct/record)
