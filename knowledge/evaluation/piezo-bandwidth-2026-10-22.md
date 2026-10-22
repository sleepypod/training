---
type: "Experimental finding"
title: "Piezo bandwidth gate: 2026-10-22"
description: "In one scripted session on one pod, imitated snoring raised raw piezo energy in both snore bands well above the pass bar; real snoring is untested."
status: "draft"
generated: {"by": "claude/opus-5.5", "at": "2026-10-22T09:40:00+00:00"}
sources: [{"resource": "Local scripted session data/bandwidth/steps.json and analyze report data/bandwidth/session/report.json (identical to the earlier preview) under ignored data/, from scripts/piezo_bandwidth.py", "title": "Bandwidth gate session and report"}, {"resource": "/evaluation/light-sleep-confusion-2026-10-21.md", "title": "Why raw piezo also matters for staging"}]
---

# Piezo bandwidth gate: 2026-10-22

The pod's raw piezo channels carry energy in both proposed snore bands. Imitated snoring raised 16–30 Hz by 9.6 dB and 60–100 Hz by 27.5 dB over lying still on the occupant's channel, against a pre-committed bar of 6 dB. Outcome: `both-bands`, so section 0 of the overnight capture plan (`docs/overnight-capture-implementation-plan.md`) passes and the plan proceeds in its delivery order. One participant, one pod, one session, imitated rather than real snoring.

## Measured: session

`scripts/piezo_bandwidth.py`, seven 60 s steps on 2026-10-22 between 02:11 and 02:26 local time (America/Los_Angeles). Steps were timed remotely by chat command instead of the interactive `session` prompt; windows were shifted by the measured pod clock offset (−0.5 s). Two channels (`left1`, `right1`) at 500 Hz. Every analyzed window had at least 99.9% sample coverage, no decode errors and at most 27 lost samples. The occupant lay on the left side. The other sleeper was out of the bed for the whole session. Pump speed confirmed the protocol: up to 1,944 rpm in the pump-on step and 0 in every other step.

Band power in dB (arbitrary ADC units, Welch, 2 s windows, first 10 s and last 5 s of each step dropped):

| Step | left1 16–30 | left1 60–100 | right1 16–30 | right1 60–100 |
| --- | ---: | ---: | ---: | ---: |
| empty, pump off | 67.2 | 65.1 | 64.1 | 51.1 |
| empty, pump on | 68.2 | 65.1 | 66.4 | 55.4 |
| lying still | 84.8 | 71.3 | 73.8 | 59.6 |
| humming | 86.4 | 80.8 | 75.8 | 59.9 |
| imitated snoring | 94.4 | 98.8 | 86.8 | 84.1 |
| knocks at head | 91.4 | 72.9 | 86.6 | 66.5 |
| knocks at foot | 93.4 | 79.1 | 96.1 | 79.6 |

Rises over lying still on the occupant channel (`left1`): humming +1.6 dB (16–30 Hz) and +9.5 dB (60–100 Hz); imitated snoring +9.6 dB and +27.5 dB. Both knock steps passed the knock rule on both channels.

## Observations

- Lying still already raised `left1` 16–30 Hz by about 18 dB over the empty bed, as expected from heartbeat and breathing; this is why the pass bar compares against lying still, not the empty bed.
- The unoccupied channel also rose during imitated snoring: `right1` 60–100 Hz went up 24.5 dB over lying still. Vibration carries across the mattress, so channel strength alone is unlikely to say which sleeper snored.
- Humming barely moved 16–30 Hz; imitated snoring moved both bands.
- The empty-bed `left1` floor above 60 Hz sits about 14 dB above `right1`, matching an earlier development export; the cause (sensor, wiring or interference) is unknown.
- The knock detector counted 50–56 events on `left1` in each knock step and 5 on `right1`. Three knocks were made. The occupant channel picks up the body movement of reaching to knock, so the counts pass the rule but do not isolate knocks.

## Hypotheses

- Real snoring, which is quieter and less voluntary than imitation, may raise the bands less. The milestone-2 exit test with phone-detected snoring is the real check.
- Part of the imitated-snoring rise may come from body and head movement rather than airway vibration.
- A knock detector that requires an isolated, short transient (for example, energy above 20 Hz rising and falling within about 50 ms) should count close to three per step and is needed before knocks are used for clock sync.

## Limitations

One participant on one pod with two channels; four-channel hardware is untested. One session, imitated snoring and humming only. A cat lay still on the bed during the head-knock step. Band power is in uncalibrated ADC units, so only differences within this session are meaningful. The pass bar was fixed in `PASS_BAR` before the session, but the step timing was driven by chat rather than the scripted prompt.

See section 0 of `docs/overnight-capture-implementation-plan.md` and [light-sleep confusion](light-sleep-confusion-2026-10-21.md).
