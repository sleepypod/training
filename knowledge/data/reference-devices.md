---
type: "Data contract"
title: "Reference devices"
description: "Wearables differ, so their measurements are optional and never become model features."
status: "draft"
generated: {"by": "claude-code", "at": "2026-11-05T01:50:22.099867+00:00"}
sources: [{"resource": "/references/training-run.md", "title": "Training run provenance"}]
---

# Reference devices

A wearable contributes two things. Its sleep stages are training labels. Everything else it measures is compared with the pod and stays out of the model, because a [feature contract](feature-contracts.md) has to be computable on the pod alone, on nights with no wearable.

Devices differ in which metrics they report, how they define them and how often, and one device changes between hardware and algorithm versions. On 2026-11-04 a new Apple Watch reported HRV as RMSSD alongside SDNN in 5-minute windows, where the previous Watch gave SDNN in 1-minute readings. Comparisons therefore stay separate per metric, device and algorithm version, and a missing metric stays missing.

Pod HRV is a 5-minute variability index, not RMSSD or SDNN. Comparing it with either shows trend only, not accuracy.

Apple Health exports do not record which hardware scored a sleep stage, so stage labels cannot yet be separated by device.
