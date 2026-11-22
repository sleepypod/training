---
type: "Literature review"
title: "Apple Watch validation literature"
description: "What published validation studies say about Apple Watch sleep stages, heart rate, HRV and SpO2, checked against the papers, and what that means for the Watch as a teacher."
status: "draft"
generated: {"by": "claude/opus-5.5", "at": "2026-11-22T21:30:00+00:00"}
sources: [{"resource": "https://doi.org/10.3390/s24206532", "title": "Robbins et al. 2024, Accuracy of Three Commercial Wearable Devices for Sleep Tracking in Healthy Adults, Sensors 24:6532 (Oura funded)"}, {"resource": "https://doi.org/10.1016/j.sleep.2024.01.020", "title": "Svensson et al. 2024, Oura Gen3 with OSSA 2.0 vs multi-night ambulatory PSG, Sleep Medicine 115:251-263 (Oura funded)"}, {"resource": "https://doi.org/10.3390/s26102922", "title": "Kostrna et al. 2026, PPG-Based Heart Rate Accuracy in Hispanic Adults, Sensors 26:2922"}, {"resource": "https://doi.org/10.3390/s23229164", "title": "Walzel et al. 2023, Evaluation of Leading Smartwatches for the Detection of Hypoxemia, Sensors 23:9164"}, {"resource": "https://doi.org/10.3390/s24196220", "title": "O'Grady et al. 2024, Validity of Apple Watch Series 9 and Ultra 2 for Serial Measurements of HRV and Resting HR, Sensors 24:6220"}, {"resource": "https://doi.org/10.1016/j.jshs.2026.101162", "title": "Paluch et al. 2026, Daily step counts among wearable devices, network meta-analysis, J Sport Health Sci 15:101162"}, {"resource": "https://doi.org/10.1371/journal.pone.0353261", "title": "Kostrna et al. 2026, Body fat, skin tone, and smartwatch caloric expenditure, PLOS One 21:e0353261"}, {"resource": "https://doi.org/10.3390/bioengineering13101099", "title": "Yun et al. 2026, Smartwatch HR and energy expenditure during resistance exercise, Bioengineering 13:1099"}, {"resource": "https://digitalcommons.wku.edu/ijesab/vol14/iss5/102", "title": "Perez et al. 2025, Sex-dependent algorithms in wrist wearables for exercise energy expenditure (conference abstract)"}, {"resource": "https://doi.org/10.1016/j.mcpdig.2026.100357", "title": "Lambe et al. 2026, Accuracy of VO2 max Estimates From Apple Watch Series 10, Mayo Clin Proc Digit Health 4:100357"}, {"resource": "https://doi.org/10.1371/journal.pone.0323741", "title": "Lambe et al. 2025, Investigating the accuracy of Apple Watch VO2 max measurements, PLOS One 20:e0323741"}, {"resource": "https://doi.org/10.2196/59459", "title": "Caserman et al. 2024, Smartwatch-based VO2 max estimation using Apple Watch Series 7, JMIR Biomed Eng 9:e59459"}]
---

# Apple Watch validation literature

The Watch teaches every pod model here: its stages are the stage labels, and its heart rate,
HRV and motion check or teach pod signals ([reference devices](../data/reference-devices.md)).
This note records how accurate the Watch itself is, so scores against it are read with its error
in mind. It starts from a public comparison post (2026-11) that ranked seven brands; each cited
paper was read on 2026-11-22 (full text from PMC or the publisher, except where noted) and the
post's figures were checked against it. No paper here measured this participant, this pod or
5-minute epochs, so none of it is a measured property of our data.

## Sleep stages

Only one study tests the Watch against polysomnography: Robbins 2024. Apple Watch Series 8
(watchOS not reported), 35 healthy adults aged 20–50 with no sleep disorder, one inpatient night
in a scheduled 8-hour window, PSG scored in 30 s epochs by AASM rules. Six of 35 Watches recorded
nothing, so Apple results are n = 29. The Watch's 1-minute epochs were split to 30 s; Core was
scored as light (N1+N2); time outside the Watch's sleep episode counted as wake. Oura funded the
study and the first author consults for Oura.

| | Four-stage accuracy | Four-stage Cohen's κ |
| --- | ---: | ---: |
| Apple Watch | 75.0% | 0.60 |
| Oura Gen3 | 76.3% | 0.65 |
| Fitbit Sense 2 | 70.9% | 0.55 |

Apple Watch per PSG stage (Tables 2 and 3: sensitivity = share of PSG epochs the Watch labelled
correctly; precision = share of the Watch's label that PSG agreed with):

| PSG stage | Sensitivity | Where the misses went | Precision |
| --- | ---: | --- | ---: |
| Wake | 52.4% | 37.2% Core, 9.4% REM | 61.9% |
| Light | 86.1% | — | 72.7% |
| Deep | 50.5% | 48.3% Core | 87.8% |
| REM | 82.6% | 15.8% Core | 77.7% |

Nightly totals (Table 4, Watch vs PSG): total sleep +8 min (not significant), WASO −10 min
(p = 0.02), deep −43 min, light +45 min, REM not significantly different. Across people, total
sleep time agreed well (ICC 0.85) and nightly deep (0.13) and REM (0.37) minutes agreed poorly.
The authors note that healthy sleepers in a fixed window overstate agreement, because wearables
score quiet wakefulness as sleep.

Svensson 2024 (96 Japanese adults, up to three ambulatory PSG nights in a hotel, Oura funded)
tests Oura only. Only its abstract could be read; the post's Oura REM figure of 72.6% is not in it.

## Heart rate, HRV and SpO2

None of these studies measured during sleep.

- **Heart rate.** Cycling in the lab (Kostrna 2026, Series 8, Polar H10 reference, n = 35 for
  Apple): MAE 2.51 bpm, bias +0.65, lowest of four brands. Error rose with BMI and body fat, and
  percentage error was highest at low heart rates. Lying still on waking (O'Grady 2024, Series 9
  and Ultra 2, n = 39): bias about 0, MAE 3.73 bpm, MAPE 5.9%.
- **HRV (SDNN).** O'Grady 2024 tested deliberate 5-minute Breathe-app readings on waking, not the
  Watch's background readings: bias −8.3 ms, MAE 20.5 ms, MAPE 28.9%, limits of agreement −54 to
  +37 ms; equivalence within ±10 ms was not shown. Its Table 1 means (Apple 93.3 vs reference
  85 ms) contradict the reported negative bias. Background and overnight SDNN are unvalidated here.
- **SpO2.** Seated, still, induced hypoxia (Walzel 2023, Watch 8, 18 young Caucasian adults,
  Masimo finger reference): bias −0.1%, A_rms 2.2%, limits of agreement −4.4 to +4.2; SpO2 below
  90% detected with sensitivity 0.91 and specificity 0.95. The FDA limit for reflectance oximeters
  is A_rms ≤ 3.5% (4.0% is ISO 80601-2-61).
- **Breathing rate.** None of the cited papers measured it.

## Not used by training

These don't affect any pod model and are recorded only because the post cited them.

- **Steps** (Paluch 2026, 59 studies): Apple counted 7% more steps than a hip ActiGraph (ratio
  1.07, 95% CI 0.90–1.26), mostly from indirect comparisons. This is distance from a reference
  device, not error against true steps.
- **Calories**: every brand was far from indirect calorimetry, and rankings flipped by activity.
  Apple MAPE was 36% cycling (Kostrna 2026), 112% resistance training (Yun 2026) and 26–27% on a
  maximal test (Perez 2025, conference abstract). The post's 58% is an unweighted mean of these
  three non-comparable studies.
- **VO2 max**: all three studies found the Watch read low on average, by 4.51 (Caserman 2024,
  cycle test without VO2 max criteria), 6.07 (Lambe 2025) and 6.25 mL/kg/min (Lambe 2026), with
  limits of agreement about 24 points wide. Larger gaps for fitter people and for women come from
  small post hoc subgroups; in Lambe 2026 sex and fitness were confounded and neither was
  independently significant.

## Where the post was wrong

| Claim | Papers |
| --- | --- |
| Apple sleep agreement 0.60 | Four-stage Cohen's κ, not accuracy (accuracy 75.0%) |
| Oura caught 73% of awake time | 68.6% in Robbins; 73.0–74.6% is Oura sleep/wake specificity in Svensson, another study and metric |
| Oura REM 72.6% (Svensson) | Not in the abstract; unverified |
| Apple best of 7 brands for workout HR | Four brands tested |
| FDA allows 4 SpO2 points | FDA ≤ 3.5% A_rms for wrist sensors; 4.0% is ISO |
| Apple overcounts steps by 7% | Relative to a hip ActiGraph, not true steps; CI includes 1.0 |
| HRV 29% off; can't be ranked against RMSSD brands | 28.9% MAPE matches; only Apple was tested, no ranking |

## What this means here

- **The Watch sets a ceiling.** It agrees with PSG at four-stage κ 0.60 on healthy sleepers. A pod
  model scored against the Watch can't show more than Watch agreement, and gains near that level
  may be learning the Watch's errors. Small macro F1 differences between models are within the
  teacher's own disagreement with PSG.
- **Watch wake labels miss about half of wake.** In Robbins, 37% of PSG wake became Core and a third
  of the Watch's wake epochs were PSG light. Wake is 2% of our labelled five-minute epochs
  (63 of 2,945 in run 2026-11-22) and wake recall has stayed near 0
  ([fusion rerun](../evaluation/fusion-cross-fitted-2026-11-15.md)). The missed labels are one
  plausible reason; it is not measured on our data. Evidence that does not come from the Watch
  stages — pod bed entry and exit, heart-rate rises — is the way to test it. Watch motion is not
  independent of Watch staging, since the Watch stages partly from it.
- **Watch Core is a mixed class and Watch Deep is a precise but sparse one.** Half of PSG deep was
  labelled Core, while 88% of the Watch's deep epochs were PSG deep. Our models collapse deep and
  REM into light ([light-sleep confusion](../evaluation/light-sleep-confusion-2026-10-21.md)); some
  of that may come from the labels.
- **Watch REM is the strongest label** (83% sensitivity, 78% precision). REM had the highest recall
  of any stage for the candidate scored on the 2026-11-18 holdout night (17 of 17 epochs).
- **Vitals comparisons are loose.** Pod-versus-Watch heart-rate differences of a few bpm at rest are
  within the Watch's own error; Watch SDNN is a trend check only, as
  [reference devices](../data/reference-devices.md) already says, and its overnight accuracy is
  unpublished in these sources. SpO2 and breathing rate have no overnight Watch evidence here.
- **Limits of this evidence.** One sleep study, one night per person, healthy young adults, Series 8
  with unreported software and an Oura-funded design. Our Watch hardware changed on 2026-11-04 and
  watchOS versions are not recorded per night.
