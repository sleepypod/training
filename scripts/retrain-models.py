"""Retrospective nested whole-night evaluation; export a candidate, never upload.

Inputs come from export-runtime-features.ts (the production preprocessing).
Candidate selection is inside each outer held-out night. All known Watch nights
have already been inspected: this is NOT a prospective validation or a new
untouched holdout. Model artifacts and features are private local outputs.
"""
import argparse
import hashlib
import importlib.util
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, cohen_kappa_score, confusion_matrix, f1_score
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

spec = importlib.util.spec_from_file_location('personal_models', Path(__file__).resolve().parents[1] / 'vendor/core/docs/research/sleep-stage-calibration/personal-models.py')
pm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pm)

# Frozen before evaluation. Existing depth-8 configuration is an explicit baseline.
CANDIDATES = ('existing_depth8', 'balanced_leaf5', 'balanced_leaf10', 'balanced_logistic')


def fit(name, nights):
    rows = [(r['x'], r['y']) for n in nights for r in n['rows'] if r['y'] is not None]
    if name == 'existing_depth8':
        rows = pm.oversample(rows)
    x = np.asarray([r[0] for r in rows])
    y = np.asarray([r[1] for r in rows])
    if name == 'balanced_logistic':
        clf = make_pipeline(StandardScaler(), LogisticRegression(C=1, class_weight='balanced', max_iter=2000, solver='liblinear', random_state=42))
    else:
        clf = RandomForestClassifier(n_estimators=200, max_depth=6 if name == 'balanced_leaf10' else 8,
            min_samples_leaf={'balanced_leaf5': 5, 'balanced_leaf10': 10}.get(name, 1),
            class_weight=None if name == 'existing_depth8' else 'balanced_subsample', random_state=42, n_jobs=1)
    return clf.fit(x, y)


def postprocess(predictions, ruleset):
    """Exactly the production sequential smoothing/transition order, including gaps."""
    p = list(map(int, predictions))
    if ruleset['smoothIsolatedEpochs']:
        for i in range(1, len(p) - 1):
            if p[i - 1] == p[i + 1] and p[i] != p[i - 1]:
                p[i] = p[i - 1]
    for i in range(1, len(p)):
        for t in ruleset['transitions']:
            if pm.STAGES[p[i - 1]] == t['from'] and pm.STAGES[p[i]] == t['to']:
                p[i] = pm.STAGES.index(t['via'])
                break
    return p


def predict(clf, nights, ruleset):
    y, p = [], []
    for night in nights:
        raw = clf.predict(np.asarray([r['x'] for r in night['rows']]))
        processed = postprocess(raw, ruleset)
        for row, pred in zip(night['rows'], processed):
            if row['y'] is not None:
                y.append(row['y'])
                p.append(pred)
    return y, p


def metrics(y, p):
    cm = confusion_matrix(y, p, labels=[0, 1, 2, 3])
    kappa = cohen_kappa_score(y, p)
    return dict(epochs=len(y), accuracy=float(accuracy_score(y, p)), kappa=float(kappa) if np.isfinite(kappa) else None,
        macroF1=float(f1_score(y, p, labels=[0, 1, 2, 3], average='macro', zero_division=0)),
        confusion=cm.tolist(), recall={s: float(cm[i, i] / cm[i].sum()) if cm[i].sum() else None for i, s in enumerate(pm.STAGES)})


def inner_choice(nights, ruleset):
    groups = [n['group'] for n in nights]
    splits = list(GroupKFold(n_splits=min(3, len(set(groups)))).split(nights, groups=groups))
    scores = {}
    for name in CANDIDATES:
        truth, preds = [], []
        for train, valid in splits:
            train_nights, valid_nights = [nights[i] for i in train], [nights[i] for i in valid]
            assert {n['group'] for n in train_nights}.isdisjoint(n['group'] for n in valid_nights)
            y, p = predict(fit(name, train_nights), valid_nights, ruleset)
            truth.extend(y)
            preds.extend(p)
        scores[name] = metrics(truth, preds)
    # Fixed priority: equal-weight four-class F1, then kappa. No held-out labels.
    winner = max(CANDIDATES, key=lambda k: (scores[k]['macroF1'], scores[k]['kappa'] or 0))
    return winner, scores


def export(clf, name, dataset, output, score):
    kind = 'logistic' if name == 'balanced_logistic' else 'forest'
    xs = np.array([r['x'] for n in dataset['nights'] for r in n['rows']])
    expected = clf.predict(xs)
    model = pm.portable(clf, kind, dataset['features'])
    actual = np.array([pm.evaluate_portable(model, x) for x in xs])
    if not np.array_equal(actual, expected):
        raise ValueError('Portable artifact differs from sklearn; refusing export')
    indices = np.linspace(0, len(xs) - 1, min(64, len(xs)), dtype=int)
    doc = dict(version=f'research-{name}-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}',
        classes=list(pm.STAGES), featureContract=dataset['features'], epochSeconds=300,
        trainedAt=datetime.now(timezone.utc).isoformat(), source='runtime-feature nested night retraining; not prospectively validated',
        metrics={'nestedSelection': score},
        testVectors=[dict(inputs=dict(zip(dataset['features'], map(float, xs[i]))), expected=int(expected[i])) for i in indices], **model)
    (output / 'candidate.json').write_text(json.dumps(doc, separators=(',', ':')))
    joblib.dump(clf, output / 'candidate.joblib')
    (output / 'parity-inputs.json').write_text(json.dumps({'features': dataset['features'], 'rows': xs.tolist(), 'expected': expected.tolist()}))
    return dict(parityRows=len(xs), modelBytes=(output / 'candidate.json').stat().st_size,
                sha256=hashlib.sha256((output / 'candidate.json').read_bytes()).hexdigest())


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('features', type=Path)
    ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args()
    data = json.loads(args.features.read_text())
    nights, ruleset = data['nights'], data['ruleset']
    groups = sorted({n['group'] for n in nights})
    if len(groups) < 5:
        raise ValueError('Need at least five independent night groups')
    args.out.mkdir(parents=True, exist_ok=True)
    if any(args.out.iterdir()):
        raise ValueError('Use an empty output directory; do not overwrite an evaluation or partial run')
    protocol = dict(scriptSha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        trainingHelpersSha256=hashlib.sha256(Path(pm.__file__).read_bytes()).hexdigest(),
        candidates=list(CANDIDATES), outer='leave-one-night-group-out', inner='3-fold GroupKFold',
        objective='macro F1 across all four classes, then kappa', preprocessing='production TypeScript',
        prospective=False, inputSha256=hashlib.sha256(args.features.read_bytes()).hexdigest(), sources=data['sources'])
    (args.out / 'protocol.json').write_text(json.dumps(protocol, indent=2))
    outputs = {name: ([], []) for name in (*CANDIDATES, 'nested', 'rules', 'always_light')}
    folds = []
    for group in groups:
        train = [n for n in nights if n['group'] != group]
        held = [n for n in nights if n['group'] == group]
        winner, inner = inner_choice(train, ruleset)
        fold = dict(group=group, trainingGroups=sorted({n['group'] for n in train}), selected=winner, innerScores=inner, scores={})
        for name in CANDIDATES:
            y, p = predict(fit(name, train), held, ruleset)
            outputs[name][0].extend(y)
            outputs[name][1].extend(p)
            fold['scores'][name] = metrics(y, p)
            if name == winner:
                outputs['nested'][0].extend(y)
                outputs['nested'][1].extend(p)
        labeled = [r for n in held for r in n['rows'] if r['y'] is not None]
        y = [r['y'] for r in labeled]
        outputs['always_light'][0].extend(y)
        outputs['always_light'][1].extend([1] * len(y))
        rule_rows = [r for r in labeled if r['rule'] is not None]
        outputs['rules'][0].extend(r['y'] for r in rule_rows)
        outputs['rules'][1].extend(r['rule'] for r in rule_rows)
        folds.append(fold)
        print(f'{group}: selected {winner}; outer {fold["scores"][winner]}', flush=True)
    scores = {name: metrics(y, p) for name, (y, p) in outputs.items()}
    # A chronological audit, also retrospective: last two groups were already
    # inspected in earlier work and are not advertised as untouched holdouts.
    early = [n for n in nights if n['group'] not in groups[-2:]]
    recent = [n for n in nights if n['group'] in groups[-2:]]
    temporal_name, _ = inner_choice(early, ruleset)
    temporal = {'selected': temporal_name, 'groups': groups[-2:], 'scores': {}}
    for name in dict.fromkeys([temporal_name, 'existing_depth8']):
        temporal['scores'][name] = metrics(*predict(fit(name, early), recent, ruleset))
    differences = np.array([f['scores'][f['selected']]['macroF1'] - f['scores']['existing_depth8']['macroF1'] for f in folds])
    rng = np.random.default_rng(42)
    means = rng.choice(differences, size=(2000, len(differences)), replace=True).mean(axis=1)
    uncertainty = {'meanNightMacroF1Difference': float(differences.mean()),
        'nightBootstrap95Percent': np.quantile(means, [.025, .975]).tolist(),
        'note': 'Descriptive only: overlapping CV training sets make this approximate.'}
    final_name, final_inner = inner_choice(nights, ruleset)
    clf = fit(final_name, nights)
    artifact = export(clf, final_name, data, args.out, scores['nested'])
    report = dict(protocol=protocol, groups=len(groups), sessions=len(nights),
        classCounts=dict(Counter(outputs['nested'][0])),
        watchEpochs=sum(n['watchEpochs'] for n in nights), folds=folds, scores=scores,
        finalSelected=final_name, finalInnerScores=final_inner, temporalAudit=temporal, uncertainty=uncertainty, artifact=artifact,
        limitations=['Same-person retrospective reuse; a future night is required for prospective validation.',
            'Whole-night normalization and centered windows are retrospective, not a live classifier.',
            'Sparse awake labels; wake recall is not established.',
            'Per-candidate outer scores are exploratory; nested score evaluates the selection procedure.',
            'No upload or activation performed.'])
    (args.out / 'report.json').write_text(json.dumps(report, indent=2))
    print(json.dumps({'scores': scores, 'finalSelected': final_name, 'artifact': artifact}, indent=2), flush=True)


if __name__ == '__main__':
    main()
