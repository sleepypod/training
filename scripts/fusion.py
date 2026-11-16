"""Multi-source stage model: pod features plus HR quality and pod movement-model features.

Arms add feature groups to the frozen feature-ablation.py forest, all on the same
nights (those with a raw export for the movement model):
- base: the ten podNative features.
- all: podNative plus context, movement-bout and arousal (the podContext columns).
- all_quality: all plus pod HR/breathing coverage and HR jitter (vitals_reference.py).
- all_movemodel: all plus per-epoch pod movement-model features (movement_model.py).
- fused: all plus both; hierarchical_fused separates wake first.

Every input is computed from pod data. Watch stages are the labels; Watch motion
trained the movement model only, cross-fitted: when night T is held out, every
night's movement features (training and test) come from movement models fitted
without T's Watch motion. Evaluated leave-one-night-out and chronologically
(walk-forward and frozen) against the stages the pod recorded live; pod and model
are compared on the same labelled epochs (those with a live pod stage). Locked
holdout nights (holdout.py) are dropped before any feature is computed.
Not prospective; never exports, uploads or activates.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fa = load('feature_ablation', 'feature-ablation.py')
sb = load('stage_backtest', 'stage-backtest.py')
import holdout as ho
import movement_model as mm
import vitals_reference as vr

rm, pm = fa.rm, fa.pm
POD = sb.POD
GROUPS = {**fa.GROUPS, 'quality': vr.QUALITY, 'movemodel': mm.EPOCH_FEATURES}
CURRENT = ('context', 'movement', 'arousal')
ARMS = {'base': (), 'all': CURRENT, 'all_quality': CURRENT + ('quality',), 'all_movemodel': CURRENT + ('movemodel',),
        'fused': CURRENT + ('quality', 'movemodel')}
HIERARCHICAL = 'hierarchical_fused'
KEYS = tuple(ARMS) + (HIERARCHICAL,)
COMPARISONS = (('fused', 'all'), ('all_movemodel', 'all'), ('all_quality', 'all'), ('fused', 'base'), ('all', 'base'),
               (HIERARCHICAL, 'all'), ('fused', POD), (HIERARCHICAL, POD), ('all', POD))


def columns(features, arm):
    return list(features) + [name for group in ARMS[arm] for name in GROUPS[group]]


def datasets(nights, features, extras):
    out = {}
    for arm in ARMS:
        names = [name for group in ARMS[arm] for name in GROUPS[group]]
        out[arm] = [{**n, 'rows': [{**r, 'x': list(r['x']) + [np.nan if e[k] is None else e[k] for k in names]} for r, e in zip(n['rows'], extra)]}
                    for n, extra in zip(nights, extras)]
    return out


def fitted(key, train):
    return fa.Hierarchical(train) if key == HIERARCHICAL else fa.fit_forest(train)


def dataset_for(data, key):
    return data['fused' if key == HIERARCHICAL else key]


def require_pod_native(data):
    """Arms add the podContext groups themselves, so `base` must be exactly podNative."""
    added = {name for group in GROUPS.values() for name in group}
    if data.get('contract', 'podNative') != 'podNative' or added & set(data['features']):
        raise ValueError('fusion.py adds the podContext columns itself; export features with the podNative contract')


def movement_rows(session, held_out):
    """Per-epoch movement features for `session` when night group `held_out` is the test night."""
    if session['group'] == held_out:
        return session['rows']
    return session['excluding'].get(held_out, session['rows'])


def arm_data(nights, features, base_extras, movement, held_out):
    """Every arm's nights, with movement-model features that never saw `held_out`'s Watch motion."""
    extras = []
    for night, base in zip(nights, base_extras):
        epochs = {r['start']: r for r in movement_rows(movement['sessions'][str(night['id'])], held_out)}
        extras.append([{**e, **epochs[r['start']]} for r, e in zip(night['rows'], base)])
    return datasets(nights, features, extras)


def predict_shared(clf, held, ruleset):
    """Full-night postprocessing, scored only on labelled epochs that also have a live pod stage."""
    y, p, labelled = [], [], 0
    for night in held:
        processed = rm.postprocess(clf.predict(np.asarray([r['x'] for r in night['rows']])), ruleset)
        for row, pred in zip(night['rows'], processed):
            if row['y'] is not None:
                labelled += 1
                if row.get('pod') is not None:
                    y.append(row['y'])
                    p.append(pred)
    return y, p, labelled


def leave_one_out(data_for, groups, ruleset, rng):
    pooled = {k: ([], []) for k in KEYS}
    per_night = []
    for group in groups:
        row = dict(group=group)
        data = data_for(group)
        for key in KEYS:
            train = [n for n in dataset_for(data, key) if n['group'] != group]
            held = [n for n in dataset_for(data, key) if n['group'] == group]
            y, p = rm.predict(fitted(key, train), held, ruleset)
            pooled[key][0].extend(y)
            pooled[key][1].extend(p)
            row[key] = sb.night_scores(y, p)
        per_night.append(row)
        print(f'leave-one-out: {group} scored', flush=True)
    return dict(nights=per_night, pooled=summary(pooled),
                comparisons={f'{a} vs {b}': {m: sb.compare(per_night, a, b, m, rng) for m in ('macroF1', 'kappa', 'wakeF1', 'lightF1')}
                             for a, b in COMPARISONS if POD not in (a, b)})


def chronological(kind, data_for, nights, ruleset, rng, unseen_after):
    pooled = {k: ([], []) for k in KEYS + (POD,)}
    per_night = []
    for train_groups, test in sb.schedule({n['group'] for n in nights}, kind):
        row = dict(group=test, trainedOn=len(train_groups))
        data = data_for(test)
        for key in KEYS:
            train = [n for n in dataset_for(data, key) if n['group'] in train_groups]
            held = [n for n in dataset_for(data, key) if n['group'] == test]
            assert max(n['group'] for n in train) < test
            y, p, labelled = predict_shared(fitted(key, train), held, ruleset)
            pooled[key][0].extend(y)
            pooled[key][1].extend(p)
            row[key] = sb.night_scores(y, p)
        y, p = sb.pod_live([n for n in nights if n['group'] == test])
        pooled[POD][0].extend(y)
        pooled[POD][1].extend(p)
        row[POD] = sb.night_scores(y, p)
        row['podCoverage'] = len(y) / labelled if labelled else None
        per_night.append(row)
        print(f'{kind}: {test} scored (trained on {len(train_groups)} nights)', flush=True)
    result = dict(nights=per_night, pooled=summary(pooled),
                  comparisons={f'{a} vs {b}': {m: sb.compare(per_night, a, b, m, rng) for m in ('macroF1', 'kappa', 'wakeF1', 'lightF1')}
                               for a, b in COMPARISONS})
    if unseen_after:
        unseen = [r for r in per_night if r['group'] > unseen_after]
        result['unseenByPodModel'] = dict(after=unseen_after, nights=len(unseen),
            meanNight={k: {m: float(np.mean([r[k][m] for r in unseen if r[k][m] is not None])) for m in ('macroF1', 'kappa')} for k in KEYS + (POD,)} if unseen else {},
            comparisons={f'{a} vs {b}': {m: sb.compare(unseen, a, b, m, rng) for m in ('macroF1', 'kappa')} for a, b in COMPARISONS if POD in (a, b)})
    return result


def summary(pooled):
    out = {}
    for key, (y, p) in pooled.items():
        result = rm.metrics(y, p)
        out[key] = dict(**result, f1={s: fa.f1(result['confusion'], i) for i, s in enumerate(pm.STAGES)})
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('features', type=Path, help='features.json from export-runtime-features.ts')
    ap.add_argument('--snapshot', type=Path, required=True, help='The biometrics snapshot those features came from')
    ap.add_argument('--movement-epochs', type=Path, required=True, help='movement_model.py epochs output')
    ap.add_argument('--pod-model-trained-through', help='Last night group the pod-live model trained on; later nights are reported separately')
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--holdout-final', action='store_true', help='Refused: fusion compares feature arms on the nights it scores')
    args = ap.parse_args()
    if args.holdout_final:
        ho.refuse_final(ap, 'fusion compares feature arms')
    data = json.loads(args.features.read_text())
    if data['sources']['snapshot'] != hashlib.sha256(args.snapshot.read_bytes()).hexdigest():
        raise ValueError('Snapshot does not match the one these features were exported from')
    movement = json.loads(args.movement_epochs.read_text())
    if movement.get('version') != 'movement-epochs-v2':
        raise ValueError('Movement epochs are not cross-fitted per held-out night; rerun movement_model.py epochs')
    if movement['inputSha256'] != hashlib.sha256(args.features.read_bytes()).hexdigest():
        raise ValueError('Movement epochs were computed for different features')
    args.out.mkdir(parents=True, exist_ok=True)
    if any(args.out.iterdir()):
        raise ValueError('Use an empty output directory; do not overwrite an evaluation or partial run')
    features, ruleset, side = data['features'], data['ruleset'], data['side']
    require_pod_native(data)
    holdout = ho.record({n['group'] for n in data['nights']})
    development = ho.drop_locked(data['nights'])
    nights = [n for n in development if str(n['id']) in movement['sessions']]
    groups = sorted({n['group'] for n in nights})
    excluded = sorted({n['group'] for n in development} - set(groups))
    base_extras = []
    with sqlite3.connect(f'file:{args.snapshot.resolve()}?mode=ro', uri=True) as db:
        for night in nights:
            vitals = vr.night_vitals(db, side, night['start'], night['end'])
            base_extras.append([{**e, **vr.quality_features(r['start'], vitals, ruleset['outliers'])}
                                for r, e in zip(night['rows'], fa.augment(night, features, db, side))])

    def data_for(held_out):
        return arm_data(nights, features, base_extras, movement, held_out)
    rng = np.random.default_rng(42)
    protocol = dict(scriptSha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        ablationSha256=hashlib.sha256(Path(fa.__file__).read_bytes()).hexdigest(),
        movementModelSha256=movement['modelSha256'], arms={arm: columns(features, arm) for arm in ARMS},
        hierarchical=f'{HIERARCHICAL}: wake/sleep forest then three-stage forest, fused features', forest=fa.FOREST,
        nightGroups=groups, excludedWithoutRawExport=excluded, minTrainGroups=sb.MIN_TRAIN_GROUPS,
        baseline=f'{POD}: stages the pod recorded live (its selected model may have trained on some nights)',
        podModelTrainedThrough=args.pod_model_trained_through, watchSignalsAsFeatures=False,
        crossFitting='movement features for every night come from models fitted without the held-out night\'s Watch motion',
        podComparisonEpochs='chronological scores use labelled epochs with a live pod stage, for every arm; podCoverage per night',
        postprocessing='production smoothing and transition rules', prospective=False, holdout=holdout,
        inputSha256=hashlib.sha256(args.features.read_bytes()).hexdigest(), sources=data['sources'])
    (args.out / 'protocol.json').write_text(json.dumps(protocol, indent=2))
    report = dict(protocol=protocol, groups=len(groups), sessions=len(nights),
        leaveOneOut=leave_one_out(data_for, groups, ruleset, rng),
        schedules={kind: chronological(kind, data_for, nights, ruleset, rng, args.pod_model_trained_through) for kind in ('walk-forward', 'frozen')},
        limitations=['One participant; nights were inspected before, so this is retrospective, not prospective.',
            'Watch stages are the reference, not polysomnography; Watch wake labels are sparse.',
            'The movement model is fitted on Watch-motion nights that can postdate a chronological test night; it never saw that night.',
            'Movement models may have been taught by Watch motion on locked holdout nights; movement_model.py does not apply the holdout yet.',
            'Raw-piezo movement features need a core port before a pod can compute them.',
            'Many arm comparisons on the same nights: read single intervals with multiplicity in mind.',
            'No export, upload or activation performed.'])
    (args.out / 'report.json').write_text(json.dumps(report, indent=2))
    print_report(report)


def print_report(report):
    print(f"\n{report['groups']} night groups · {report['sessions']} sessions · excluded without raw export: {', '.join(report['protocol']['excludedWithoutRawExport']) or 'none'}")
    sections = [('leave-one-out', report['leaveOneOut'])] + list(report['schedules'].items())
    for name, result in sections:
        print(f'\n== {name}: {len(result["nights"])} test nights')
        for key, r in result['pooled'].items():
            f = r['f1']
            print(f"{key:19} κ {r['kappa'] or 0:.3f} macroF1 {r['macroF1']:.3f}  F1 wake {f['wake'] or 0:.2f} light {f['light'] or 0:.2f} "
                  f"deep {f['deep'] or 0:.2f} rem {f['rem'] or 0:.2f}")
        comparisons = dict(result['comparisons'])
        if 'unseenByPodModel' in result:
            u = result['unseenByPodModel']
            print(f"-- {u['nights']} nights after {u['after']} (unseen by the pod-live model), mean per night:")
            for key, m in u['meanNight'].items():
                print(f"   {key:19} macroF1 {m['macroF1']:.3f} κ {m['kappa']:.3f}")
            comparisons.update({f'{k} [unseen]': v for k, v in u['comparisons'].items()})
        for label, metrics in comparisons.items():
            print(label + ': ' + ' · '.join(f"{m} {c['meanDifference']:+.3f} [{c['bootstrap95Percent'][0]:+.3f},{c['bootstrap95Percent'][1]:+.3f}] "
                                           f"{c['better']}/{c['worse']} p={c['signTestP']:.3f}" for m, c in metrics.items() if c))


if __name__ == '__main__':
    main()
