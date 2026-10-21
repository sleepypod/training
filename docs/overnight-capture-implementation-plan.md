# Overnight motion, snoring, and piezo comparison — implementation plan

Status: proposed implementation plan, 2026-10-21. Capture and model experiments are not implemented. Keep this work separate from the existing core chart/model PR, which remains awaiting feedback.

Companion specification: [capture and training contract](overnight-capture.md).

## Objective

Determine whether snoring events detected locally by the iPhone coincide with distinctive vibrations in the pod's left and right raw piezo channels. Build synchronized capture, comparison, training, and eventually validated pod-only detection. Side attribution is a separate hypothesis to evaluate, not an assumed capability.

## 1. Shared recording contract

- Assign session IDs, participant aliases, bed-side mappings, and device metadata.
- Preserve UTC timestamps, sensor timing, sample rates, clock-offset estimates and uncertainty, and interruptions.
- Keep snore detection confidence separate from side-attribution confidence.
- Retain fine-grained events for analysis. Aggregate onto the existing model's exact 300-second, session-anchored training windows; do not round to wall-clock boundaries.
- Missing data remains missing, not zero movement or no snoring.
- Version schemas and feature definitions. Retain source provenance and chunk hashes.

Acceptance: synthetic boundary, unit-conversion, DST, clock-jump, gap and duplicate-transfer tests; reproduce existing training epoch starts without changing current feature values.

## 2. Opt-in iPhone snoring analysis

- Add Start/Stop overnight capture, microphone permission, recording status and morning summary.
- Process microphone buffers locally with Sound Analysis/Core ML.
- Persist only candidate-snore timestamps, confidence, detector version and recording coverage.
- Never write or upload raw audio, transcripts or audio embeddings. No audio-clip retention option in this implementation.
- Handle screen locking, calls, alarms, audio-route changes, interruptions and process termination.
- Evaluate available classifiers before selecting a baseline. Loudness alone is not snore detection; classifier confidence is not automatically a calibrated probability.

Acceptance: physical-iPhone overnight testing; explicit denied/revoked permission and stop tests; verify storage, logging and network paths contain no audio; quantify battery use and capture coverage.

## 3. Synchronized raw pod piezo

- Reuse existing raw-data/archive infrastructure where suitable.
- Preserve both channels at their native sampling rate before heart-rate or breathing filtering.
- Add bounded sessions, storage limits, checksums, durable acknowledgments and resumable retrieval.
- Record occupancy, movement, pump activity and available hardware-state signals to investigate interference.
- Measure phone/pod clock alignment and retain uncertainty; never use transfer time as sample time.
- Keep raw recordings and derived private artifacts under ignored data/ directories.

Acceptance: recover from transfer interruption without duplication or silent loss; verify channel identities, sample timing, capacity limits and preservation of existing sensor processing.

## 4. Watch motion capture

- Create a watchOS companion target; none was found in the inspected iOS project.
- Prototype CMSensorRecorder on physical hardware before committing to unattended overnight behavior.
- Add a user-started recording, Motion permission handling, and morning transfer through the iPhone.
- Preserve timestamped acceleration, units, sensor orientation metadata where available and coverage.
- Derive separately versioned motion features; never substitute wrist acceleration for existing pod movement features.

Acceptance: measure overnight availability and battery use; test app suspension, incomplete transfers and recovery. Automatic starts and live streaming are separate follow-up feasibility work.

## 5. Comparison workspace

Extend the training dashboard with an aligned timeline containing:

- iPhone candidate-snore events and confidence.
- Left/right piezo waveforms and spectrograms.
- Energy in research-inspired frequency bands, including 16–30 Hz and 60–100 Hz.
- Watch motion, pod movement and sleep stages.
- Capture gaps and synchronization uncertainty.

Support zooming into individual events and comparing periods with and without detected snoring. Add review labels for likely snore, movement interference, equipment noise and uncertain. Preserve label origin and reviewer metadata. Without retained audio, retrospective audio listening is unavailable; waveform review alone is not ground-truth verification.

Acceptance: synthetic synchronized events line up across all rows; gaps remain visible; exported windows reproduce the viewed intervals; no private data enters public screenshots or CI.

## 6. Snoring model experiments

- Begin with a spectral-feature baseline inspired by the Emfit research, then compare a small spectrogram-based neural network.
- First verify that pod hardware preserves useful signal in the proposed frequency bands; sampling rate alone does not establish sensor bandwidth or signal quality.
- Evaluate piezo-only, audio-only and combined-sensor approaches separately.
- Treat iPhone predictions as provisional labels. Measure disagreement and label quality; independent validation is needed before interpreting agreement as accuracy.
- Hold out whole nights and participants. Keep both sides of one recording in the same split.
- Report precision, recall, false positives per hour, coverage and performance during movement/pump activity. Report metrics against provisional labels as such.
- Retraining the audio classifier requires an independently consented, labeled audio dataset, outside this no-audio-retention product capture flow.

Acceptance: reproducible datasets/manifests and runs, leakage checks, held-out evaluation and an explicit account of label limitations. Do not transfer published Emfit accuracy claims to sleepypod.

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

1. Contract and iPhone/pod capture.
2. Synchronized comparison dashboard — first usable milestone.
3. Watch motion capture and dashboard integration.
4. Reproducible snoring model experiments.
5. Side-attribution experiments.
6. Validated model packaging and product integration.

The first milestone lets a user record a night and inspect microphone-derived snore events against both raw piezo channels while keeping audio ephemeral on the iPhone. Device validation gates claims of reliable overnight operation.

## Research references

- [Emfit snoring classification using SVM/CNN](https://jperezmacias.github.io/papers/EMBC17_perezmacias.pdf)
- [Detection and assessment of sleep-disordered breathing with Emfit](https://research.aalto.fi/en/publications/detection-and-assessment-of-sleep-disordered-breathing-with-emfit/)
- [Apple Sound Analysis](https://developer.apple.com/documentation/soundanalysis)
- [Apple sensor recorder](https://developer.apple.com/documentation/coremotion/cmsensorrecorder)
- [Apple audio recording category](https://developer.apple.com/documentation/avfaudio/avaudiosession/category-swift.struct/record)
