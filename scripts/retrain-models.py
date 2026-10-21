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
import time
import resource
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from progress import update
import timelines

FIT_COUNT = 0

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
    global FIT_COUNT
    rows = [(r['x'], r['y']) for n in nights for r in n['rows'] if r['y'] is not None]
    original_rows = len(rows)
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
    update('Training', f'Fitting {name}: {len(x):,} rows × {x.shape[1]} features ({original_rows:,} before resampling)',
           completedFits=FIT_COUNT, currentFit={'model': name, 'originalRows': original_rows, 'rows': len(x), 'features': x.shape[1], 'matrixBytes': x.nbytes})
    started = time.perf_counter()
    cpu_started = time.process_time()
    fitted = clf.fit(x, y)
    seconds = time.perf_counter() - started
    FIT_COUNT += 1
    trees = len(getattr(clf, 'estimators_', []))
    result = {'fit': FIT_COUNT, 'model': name, 'seconds': round(seconds, 4), 'rows': len(x), 'originalRows': original_rows,
              'rowsPerSecond': round(len(x) / max(seconds, 1e-9), 1), 'trees': trees,
              'nodes': sum(t.tree_.node_count for t in getattr(clf, 'estimators_', [])),
              'cpuPercent': round((time.process_time() - cpu_started) / max(seconds, 1e-9) * 100, 1),
              'peakRssBytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == 'darwin' else 1024)}
    update('Training', f'{name}: {seconds:.3f}s · {result["rowsPerSecond"]:,.0f} rows/s · {trees} trees · {result["nodes"]:,} nodes', completedFits=FIT_COUNT, fitResult=result)
    return fitted


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
        kappa_text = 'n/a' if scores[name]['kappa'] is None else f"{scores[name]['kappa']:.3f}"
        update('Training', f'Inner validation {name}: macro F1 {scores[name]["macroF1"]:.3f} · κ {kappa_text}', innerResult={'model': name, **scores[name]})
    # Fixed priority: equal-weight four-class F1, then kappa. No held-out labels.
    winner = max(CANDIDATES, key=lambda k: (scores[k]['macroF1'], scores[k]['kappa'] or 0))
    return winner, scores


def permutation_drops(clf, nights, ruleset, feature_names, repeats=3):
    """Inspect selected models on outer held-out nights; never affects selection.

    Shuffle within each session, preserve labels and row order for postprocessing.
    Epochs are correlated: this is a descriptive sensitivity check, not causation.
    """
    baseline = metrics(*predict(clf, nights, ruleset))['macroF1']
    rng = np.random.default_rng(42)
    drops = {}
    for column, name in enumerate(feature_names):
        values = []
        for _ in range(repeats):
            shuffled = []
            for night in nights:
                x = np.asarray([r['x'] for r in night['rows']], dtype=float).copy()
                x[:, column] = rng.permutation(x[:, column])
                shuffled.append({**night, 'rows': [{**row, 'x': vector.tolist()} for row, vector in zip(night['rows'], x)]})
            values.append(baseline - metrics(*predict(clf, shuffled, ruleset))['macroF1'])
        drops[name] = values
    return drops


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
    traces = timelines.initial(data)
    timelines.save(traces, args.out)
    outputs = {name: ([], []) for name in (*CANDIDATES, 'nested', 'rules', 'always_light')}
    update("Training", "Starting nested whole-night evaluation", completedFits=0, totalFits=len(groups) * 16 + 27, completedFolds=0, totalFolds=len(groups))
    folds = []
    for group in groups:
        fold_started = time.perf_counter()
        update('Training', f'Outer night {len(folds) + 1}/{len(groups)}: selecting on training nights only', evaluationStage='nested selection', outerFold=len(folds) + 1)
        train = [n for n in nights if n['group'] != group]
        held = [n for n in nights if n['group'] == group]
        winner, inner = inner_choice(train, ruleset)
        fold = dict(group=group, trainingGroups=sorted({n['group'] for n in train}), selected=winner, innerScores=inner, scores={})
        update('Training', f'Outer evaluation: {winner} selected without held-out labels', evaluationStage='held-out evaluation')
        for name in CANDIDATES:
            fitted = fit(name, train)
            y, p = predict(fitted, held, ruleset)
            outputs[name][0].extend(y)
            outputs[name][1].extend(p)
            fold['scores'][name] = metrics(y, p)
            if name == 'existing_depth8':
                timelines.add_predictions(traces, held, fitted, name, 'baseline', ruleset, postprocess)
                timelines.save(traces, args.out)
            if name == winner:
                update('Training', 'Measuring held-out feature sensitivity', evaluationStage='permutation diagnostics')
                fold['permutationDrops'] = permutation_drops(fitted, held, ruleset, data['features'])
                timelines.add_predictions(traces, held, fitted, name, 'heldOut', ruleset, postprocess)
                timelines.save(traces, args.out)
                outputs['nested'][0].extend(y)
                outputs['nested'][1].extend(p)
        labeled = [r for n in held for r in n['rows'] if r['y'] is not None]
        y = [r['y'] for r in labeled]
        outputs['always_light'][0].extend(y)
        outputs['always_light'][1].extend([1] * len(y))
        rule_rows = [r for r in labeled if r['rule'] is not None]
        outputs['rules'][0].extend(r['y'] for r in rule_rows)
        outputs['rules'][1].extend(r['rule'] for r in rule_rows)
        fold['seconds'] = time.perf_counter() - fold_started
        folds.append(fold)
        update('Training', 'Completed fold details', foldResult=fold)
        update("Training", f"Held-out night {len(folds)}/{len(groups)} complete: {winner} · macro F1 {fold['scores'][winner]['macroF1']:.3f}", completedFolds=len(folds), heldOutResult={'fold': len(folds), 'selected': winner, 'macroF1': fold['scores'][winner]['macroF1'], 'kappa': fold['scores'][winner]['kappa'], 'epochs': fold['scores'][winner]['epochs'], 'baselineMacroF1': fold['scores']['existing_depth8']['macroF1']})
    scores = {name: metrics(y, p) for name, (y, p) in outputs.items()}
    # A chronological audit, also retrospective: last two groups were already
    # inspected in earlier work and are not advertised as untouched holdouts.
    early = [n for n in nights if n['group'] not in groups[-2:]]
    recent = [n for n in nights if n['group'] in groups[-2:]]
    update('Training', 'Selecting chronological audit model', evaluationStage='chronological audit')
    temporal_name, _ = inner_choice(early, ruleset)
    update('Training', 'Chronological audit', totalFits=len(groups) * 16 + 25 + len(set([temporal_name, 'existing_depth8'])))
    temporal = {'selected': temporal_name, 'groups': groups[-2:], 'scores': {}}
    for name in dict.fromkeys([temporal_name, 'existing_depth8']):
        temporal['scores'][name] = metrics(*predict(fit(name, early), recent, ruleset))
    differences = np.array([f['scores'][f['selected']]['macroF1'] - f['scores']['existing_depth8']['macroF1'] for f in folds])
    rng = np.random.default_rng(42)
    means = rng.choice(differences, size=(2000, len(differences)), replace=True).mean(axis=1)
    uncertainty = {'meanNightMacroF1Difference': float(differences.mean()),
        'nightBootstrap95Percent': np.quantile(means, [.025, .975]).tolist(),
        'note': 'Descriptive only: overlapping CV training sets make this approximate.'}
    update('Training', 'Selecting final candidate on all groups', evaluationStage='final selection')
    final_name, final_inner = inner_choice(nights, ruleset)
    update('Training', f'Final fit: {final_name}', evaluationStage='final fit')
    clf = fit(final_name, nights)
    timelines.add_predictions(traces, nights, clf, final_name, 'final', ruleset, postprocess)
    timelines.save(traces, args.out)
    update('Export', 'Verifying portable export against sklearn')
    artifact = export(clf, final_name, data, args.out, scores['nested'])
    importance = {'method': 'Held-out permutation importance · Δ macro F1',
        'values': {name: float(np.mean([drop for fold in folds for drop in fold['permutationDrops'][name]])) for name in data['features']},
        'std': {name: float(np.std([drop for fold in folds for drop in fold['permutationDrops'][name]])) for name in data['features']},
        'repeats': 3, 'note': 'Three shuffles per feature within each held-out session; mean score drop across night groups and repeats. Spread is one standard deviation across those drops, not a confidence interval. Correlated inputs can share importance; descriptive, never used for selection.'}
    report = dict(featureImportance=importance, protocol=protocol, groups=len(groups), sessions=len(nights),
        classCounts=dict(Counter(outputs['nested'][0])),
        watchEpochs=sum(n['watchEpochs'] for n in nights), folds=folds, scores=scores,
        finalSelected=final_name, finalInnerScores=final_inner, temporalAudit=temporal, uncertainty=uncertainty, artifact=artifact,
        limitations=['Same-person retrospective reuse; a future night is required for prospective validation.',
            'Whole-night normalization and centered windows are retrospective, not a live classifier.',
            'Sparse awake labels; wake recall is not established.',
            'Per-candidate outer scores are exploratory; nested score evaluates the selection procedure.',
            'No upload or activation performed.'])
    (args.out / 'report.json').write_text(json.dumps(report, indent=2))
    print(f'Evaluation complete: selected {final_name}; nested macro F1 {scores["nested"]["macroF1"]:.3f}', flush=True)


if __name__ == '__main__':
    main()
