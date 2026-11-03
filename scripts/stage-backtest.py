"""Walk-forward backtest of stage models: train only on earlier nights, score the next.

Two schedules, both strictly chronological by night group:
- walk-forward: before each test night, refit on every earlier night (expanding window).
- frozen: fit once on the first nights, score every later night without refitting,
  as a model shipped on that date and never retrained would.
Arms are the frozen forests from feature-ablation.py plus the stages the pod
actually recorded live. Not prospective; never exports, uploads or activates.
"""
import argparse
from collections import defaultdict
import hashlib
import importlib.util
import json
from math import comb
from pathlib import Path
import sqlite3
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
spec = importlib.util.spec_from_file_location('feature_ablation', Path(__file__).with_name('feature-ablation.py'))
fa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fa)
rm, pm = fa.rm, fa.pm

MODELS = ('base', 'all', 'hierarchical_all')
POD = 'pod_live'
MIN_TRAIN_GROUPS = 8


def schedule(groups, kind, min_train=MIN_TRAIN_GROUPS):
    """(train groups, test group) pairs; training never includes the test night or later."""
    ordered = sorted(groups)
    if len(ordered) <= min_train:
        raise ValueError(f'Need more than {min_train} night groups')
    if kind == 'walk-forward':
        return [(ordered[:i], g) for i, g in enumerate(ordered) if i >= min_train]
    return [(ordered[:min_train], g) for g in ordered[min_train:]]


def pod_live(held):
    y, p = [], []
    for night in held:
        for row in night['rows']:
            if row['y'] is not None and row.get('pod') is not None:
                y.append(row['y'])
                p.append(row['pod'])
    return y, p


def sign_test(differences):
    up, down = int((differences > 0).sum()), int((differences < 0).sum())
    n = up + down
    return up, down, min(1.0, 2 * sum(comb(n, i) for i in range(min(up, down) + 1)) / 2 ** n) if n else 1.0


def compare(scores, a, b, metric, rng):
    pairs = [(x[a][metric], x[b][metric]) for x in scores if x[a][metric] is not None and x[b][metric] is not None]
    d = np.array([p - q for p, q in pairs])
    if len(d) == 0:
        return None
    means = rng.choice(d, size=(5000, len(d)), replace=True).mean(axis=1)
    up, down, p = sign_test(d)
    return dict(nights=len(d), meanDifference=float(d.mean()), bootstrap95Percent=np.quantile(means, [.025, .975]).tolist(),
                better=up, worse=down, signTestP=p)


def night_scores(y, p):
    result = rm.metrics(y, p)
    stage_f1 = {s: fa.f1(result['confusion'], i) for i, s in enumerate(pm.STAGES)}
    return dict(epochs=len(y), macroF1=result['macroF1'], kappa=result['kappa'], **{f'{s}F1': v for s, v in stage_f1.items()})


def run(kind, nights, features, extras, ruleset, rng):
    datasets = {m: fa.build(nights, features, extras, 'all' if m == 'hierarchical_all' else m)[0] for m in MODELS}
    pooled = {k: ([], []) for k in MODELS + (POD,)}
    per_night = []
    fitted = {}
    for train_groups, test in schedule({n['group'] for n in nights}, kind):
        row = dict(group=test, trainedOn=len(train_groups))
        for model in MODELS:
            data = datasets[model]
            train = [n for n in data if n['group'] in train_groups]
            held = [n for n in data if n['group'] == test]
            assert max(n['group'] for n in train) < test
            key = (model, tuple(train_groups))
            if key not in fitted:
                fitted = {k: v for k, v in fitted.items() if k[0] != model}
                fitted[key] = fa.Hierarchical(train) if model == 'hierarchical_all' else fa.fit_forest(train)
            y, p = rm.predict(fitted[key], held, ruleset)
            pooled[model][0].extend(y)
            pooled[model][1].extend(p)
            row[model] = night_scores(y, p)
        y, p = pod_live([n for n in nights if n['group'] == test])
        pooled[POD][0].extend(y)
        pooled[POD][1].extend(p)
        row[POD] = night_scores(y, p)
        per_night.append(row)
        print(f'{kind}: {test} scored (trained on {len(train_groups)} nights)', flush=True)
    summary = {}
    for key, (y, p) in pooled.items():
        result = rm.metrics(y, p)
        summary[key] = dict(**result, f1={s: fa.f1(result['confusion'], i) for i, s in enumerate(pm.STAGES)})
    comparisons = {f'{a} vs {b}': {m: compare(per_night, a, b, m, rng) for m in ('macroF1', 'lightF1', 'deepF1', 'remF1')}
                   for a, b in (('all', 'base'), ('hierarchical_all', 'base'), ('hierarchical_all', POD), ('base', POD))}
    return dict(nights=per_night, pooled=summary, comparisons=comparisons)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('features', type=Path, help='features.json from export-runtime-features.ts')
    ap.add_argument('--snapshot', type=Path, required=True, help='The biometrics snapshot those features came from')
    ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args()
    data = json.loads(args.features.read_text())
    if data['sources']['snapshot'] != hashlib.sha256(args.snapshot.read_bytes()).hexdigest():
        raise ValueError('Snapshot does not match the one these features were exported from')
    args.out.mkdir(parents=True, exist_ok=True)
    if any(args.out.iterdir()):
        raise ValueError('Use an empty output directory; do not overwrite an evaluation or partial run')
    nights, features, ruleset = data['nights'], data['features'], data['ruleset']
    with sqlite3.connect(f'file:{args.snapshot.resolve()}?mode=ro', uri=True) as db:
        extras = [fa.augment(n, features, db, data['side']) for n in nights]
    rng = np.random.default_rng(42)
    protocol = dict(scriptSha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        ablationSha256=hashlib.sha256(Path(fa.__file__).read_bytes()).hexdigest(), models=list(MODELS),
        baseline=f'{POD}: stages the pod recorded live for each night (its selected model may have trained on some of these nights)',
        minTrainGroups=MIN_TRAIN_GROUPS, forest=fa.FOREST, order='chronological by noon-to-noon night group',
        postprocessing='production smoothing and transition rules', prospective=False,
        inputSha256=hashlib.sha256(args.features.read_bytes()).hexdigest(), sources=data['sources'])
    report = dict(protocol=protocol, schedules={kind: run(kind, nights, features, extras, ruleset, rng) for kind in ('walk-forward', 'frozen')},
        limitations=['One participant; nights were inspected before, so this is retrospective, not prospective.',
            'Watch stages are the reference, not polysomnography.',
            'The pod-live baseline may be in-sample for nights its selected model was trained on.',
            'Bootstrap intervals and sign tests are descriptive across a handful of nights.',
            'No export, upload or activation performed.'])
    (args.out / 'report.json').write_text(json.dumps(report, indent=2))
    for kind, result in report['schedules'].items():
        print(f'\n== {kind}: {len(result["nights"])} test nights')
        for key, r in result['pooled'].items():
            f = r['f1']
            print(f"{key:17} κ {r['kappa'] or 0:.3f} macroF1 {r['macroF1']:.3f}  F1 wake {f['wake'] or 0:.2f} light {f['light'] or 0:.2f} "
                  f"deep {f['deep'] or 0:.2f} rem {f['rem'] or 0:.2f}")
        for name, metrics in result['comparisons'].items():
            print(name + ': ' + ' · '.join(f"{m} {c['meanDifference']:+.3f} [{c['bootstrap95Percent'][0]:+.3f},{c['bootstrap95Percent'][1]:+.3f}] "
                                          f"{c['better']}/{c['worse']} p={c['signTestP']:.3f}" for m, c in metrics.items() if c))


if __name__ == '__main__':
    main()
