# Overnight motion and audio capture — proposed contract

Status: design draft, 2026-10-21. Capture, ingestion and device validation are not implemented. No model feature contract or production model is changed by this proposal.

## Training alignment

The current exporter (`scripts/export-runtime-features.ts`) uses 300-second epochs anchored to each pod sleep record's start. Its row `start` is Unix seconds. Capture timestamps must use explicitly named Unix-millisecond fields; convert once at ingestion. Never infer units from magnitude.

Preserve original timestamps, not just averaged values. Join samples to half-open intervals `[row.start * 1000, (row.start + 300) * 1000)`. Do not round recordings onto wall-clock five-minute boundaries. Optional 30-second summaries are for exploration; reconstruct training features from raw timestamps or sufficient statistics at the exact training boundaries. Do not average averages without sample weights. Retain gaps, counts, coverage and clipping flags; missing is not zero movement or zero snoring.

Each capture manifest must contain: schemaVersion, random captureId, pseudonymous participantId, deviceId, source kind, app/OS/device versions, UTC start/end milliseconds, device time zone, units, nominal sample rate, actual sample count, chunk IDs and hashes, and clock synchronization observations. Associate pod side and sleep record explicitly during pairing; wrist side does not imply bed side. Never put real identifiers or captured data in Git.

Use sensor-provided timestamps. Preserve monotonic timestamps and their measured wall-clock anchor when available, plus phone/pod offset estimates and uncertainty. Measure the phone/pod offset with bed-frame knocks at session start and end: matched transient onsets in the microphone envelope and both piezo channels give offset, drift and uncertainty including pipeline delay. Network time exchanges are a coarse prior only. Record knock onsets as manifest clock observations. Do not use file transfer time as measurement time. Record clock jumps, interruption intervals and reasons. Deduplicate on capture/chunk/sample identity. Reject invalid/non-finite values and inconsistent metadata; report out-of-window samples rather than silently shifting them.

## Watch motion

Deferred until after the snoring model experiments; see the [implementation plan](overnight-capture-implementation-plan.md). Watch stages are derived partly from Watch acceleration, so Watch motion is a reference signal for staging, not a stage-model feature, while Watch stages are the label.

Prototype CMSensorRecorder, checking isAccelerometerRecordingAvailable and Motion authorization on the actual Watch. Apple's recording API specifies 50 Hz and at most 12 hours per request. Verify capture with screen off, app suspended, and morning retrieval before promising unattended recording. The checked iOS project currently has no Watch companion target.

Raw samples: captureId, sequence, timestampMs, xG, yG, zG (accelerometer axes including gravity, units g). Preserve wear wrist/orientation metadata when known. Proposed separate research features include gravity-adjusted acceleration magnitude, variability, movement fraction and coverage; define/version their exact formulas before training. Never substitute these values for the existing pod motionZ or motion15 features.

Transfer closed chunks Watch → iPhone → local pod/training storage, with durable acknowledgments and retries. Keep raw data under ignored data/. Start with a user-started overnight recording and morning sync; automatic starts and live streaming require separate feasibility tests.

## Optional snoring audio

Prefer an opt-in iPhone bedside capture prototype. Use the recording audio-session category, microphone permission and background audio capability; handle calls, route changes, alarms, interruptions, low storage and process termination. Verify overnight behavior and battery use on hardware. Do not start the microphone merely because motion capture is enabled.

Process audio on-device in temporary memory buffers and retain only event metadata and capture coverage. Never write or upload raw audio, transcripts or audio embeddings. There is no research-clip retention option in this implementation. Retraining or validating the audio classifier requires a separate, consented labeled-audio dataset: a scripted calibration session (imitated snoring, speech, fan, pod pump, bedding movement, silence) held only on the recording device, labeled, then deleted, keeping only labels, segment times and detector outputs. It is not part of the product capture flow. Provide visible recording state, stop and delete controls. No cloud upload is needed.

Audio capture metadata: sample rate, channel count, encoding, route, sample-frame time origin, valid-frame intervals, clipping/noise flags. Event rows: captureId, startMs, endMs, eventType, confidence, detectorVersion, and labelOrigin (predicted or human-reviewed). Confidence is not a validated probability unless calibrated. Events spanning training windows contribute only their overlapping duration. Export observed audio coverage alongside candidate-snore duration/count; distinguish no detection from no recording.

Start from Apple's built-in Sound Analysis classifier, whose label set includes `snoring`; confirm it from `knownClassifications` on the target OS. Do not call simple sound-level thresholding snore detection. Validate a classifier against reviewed examples including speech, fans, pod pumps, bedding movement and partner snoring. Audio alone does not establish who snored; default attribution is room/unknown. Predicted snoring is an auxiliary signal, not a sleep-stage ground-truth label or apnea diagnosis.

## Raw piezo storage

Two int32 channels at 500 Hz are 14.4 MB/hour, about 115 MB per 8-hour night uncompressed. Gate capture on a measured pod free-space floor; cap pod-side retention at two nights; compress losslessly in closed chunks; delete from the pod only after checksum-acknowledged retrieval to the training workspace's ignored data/raw/. Verify the 16–30 Hz and 60–100 Hz bands survive the sensor front end before building on them.

## Model and evaluation boundaries

Keep pod-only models deployable without Watch or microphone inputs. Extra sensor data can be research/reference signals; a multimodal model using them as features must declare a new contract and require those inputs at inference, with explicitly evaluated missing-sensor behavior. Hold out whole nights and, for generalization, whole participants. Keep chunks from the same night together across train/test splits. Compare pod-only, motion-added and audio-added variants on the same held-out cohort; do not tune on held-out outcomes.

## Acceptance checks before implementation is considered complete

- Synthetic boundary cases: exact epoch boundary, fractional timestamps, DST, midnight, interrupted capture, duplicate transfer, clock jump and missing sensor.
- Reproduce current exporter row starts exactly without changing existing feature values or labels.
- Round-trip chunk checksums and retry/idempotency checks.
- Piezo bandwidth gate, empty-bed pump and occupied non-snoring control nights, and synthetic knock-sync recovery of injected offset and drift.
- Real Watch overnight capture (once undeferred) and phone locked-screen audio tests, measuring battery loss, coverage, storage and synchronization error.
- Explicit permission denial/revocation, stop/delete and no-audio-retention checks.
- No production activation or claim of snoring accuracy until capture and evaluation evidence exists.

## Sources

- [Current feature contract](../knowledge/data/feature-contracts.md)
- [Apple accelerometer recording](https://developer.apple.com/documentation/coremotion/cmsensorrecorder/recordaccelerometer(forduration:))
- [Apple recording audio category](https://developer.apple.com/documentation/avfaudio/avaudiosession/category-swift.struct/record)
- [Apple extended runtime sessions](https://developer.apple.com/documentation/watchkit/using-extended-runtime-sessions)

Implementation sequence and acceptance checks: [implementation plan](overnight-capture-implementation-plan.md).
