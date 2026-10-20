---
type: "Research note"
title: "Model and rule baselines"
description: "Keep older general and newer personal models distinct."
status: "draft"
generated: {"by": "codex/gpt-6", "at": "2026-10-20T23:09:59.032076+00:00"}
sources: [{"resource": "/references/training-run.md"}, {"resource": "Local core-fit analysis/stage-model/out/comparison-20261020.json and model.joblib; earlier analysis record"}, {"resource": "Local sleepypod-core Git commits ce6a08a6 and 872776b1; observed device model metadata on 2026-10-20"}]
---

# Model and rule baselines

The older general challenger uses four features, 100 trees, and maximum depth 6. Its training used an external 31-subject Walch dataset. Earlier personal nights were used for model selection. A separate two-night comparison reported 48.9% agreement and κ 0.186 over 131 matching epochs. These historical observations came from the earlier analysis and are not independently reproduced by the retained run2 report.

The newer personal baseline uses the ten-feature contract, 200 trees, maximum depth 8, and oversampling. Run2 refits that configuration per fold as `existing_depth8`; it does not merely evaluate the previously fitted device artifact. The last observed stored device model was `container-forest-20261020T2241`; this is not a current-state assertion.

Hardcoded rules remain a useful baseline and fallback. A movement-bucket fix selects the closest movement within 30 seconds rather than the last observation in a five-minute bucket (local core commit `872776b1`). That fix and model replacement are separate changes.

See [controlled run2 comparisons](../evaluation/findings-2026-10-20.md).
