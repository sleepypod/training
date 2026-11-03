"""Retrospective feature ablation for literature-derived pod features.

Feature groups added to the production ten-feature contract, each from pod data only:
- context: neighbouring-epoch and 30-minute values (temporal context, Radha 2019;
  Fonseca 2020) and minutes until the bed exit (retrospective, like production).
- movement: active minutes, peak minute and still-bout geometry from per-minute pod
  movement (Kortelainen 2010 wake from movement; Willemen 2014; van Hees 2015
  sustained inactivity).
- arousal: heart-rate rise against the preceding 15 minutes and breathing-rate
  variability, alone and coupled with movement (Willemen 2014 cardiorespiratory).
Watch motion is never a feature: Watch stages are the labels.

One forest configuration, frozen before evaluation, is fitted per arm per outer
held-out night group. The hierarchical arm separates wake from sleep first and
stages sleep second (Kortelainen 2010). Not prospective; never exports a model.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys

import numpy as np
from sklearn.ensemble import RandomForestClassifier

sys.path.insert(0, str(Path(__file__).resolve().parent))
spec = importlib.util.spec_from_file_location('retraining', Path(__file__).with_name('retrain-models.py'))
rm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rm)
pm = rm.pm

EPOCH = 300
WAKE = pm.STAGES.index('wake')
CONTEXT_BASE = ('hrZ', 'hrStdZ', 'motionZ', 'hrvRel')
CONTEXT = tuple(f'{f}{s}' for f in CONTEXT_BASE for s in ('Prev', 'Next', 'Mean30')) + ('minutesToExit',)
MOVEMENT = ('activeMinutes', 'peakMinuteZ', 'minutesFromMove', 'stillBoutMinutes', 'active15')
AROUSAL = ('hrRise', 'hrMaxZ', 'brStdEpoch', 'brRel', 'moveHrCoupling')
GROUPS = {'context': CONTEXT, 'movement': MOVEMENT, 'arousal': AROUSAL}
ARMS = {'base': (), 'context': ('context',), 'movement': ('movement',), 'arousal': ('arousal',),
        'movement_arousal': ('movement', 'arousal'), 'all': ('context', 'movement', 'arousal')}
HIERARCHICAL = 'hierarchical_all'
# Frozen before evaluation: the balanced_leaf5 forest from retrain-models.py.
FOREST = dict(n_estimators=200, max_depth=8, min_samples_leaf=5, class_weight='balanced_subsample', random_state=42, n_jobs=1)


def robust_z(values):
    values = np.asarray(values, dtype=float)
    if len(values) == 0:
        return values
    mad = np.median(np.abs(values - np.median(values))) * 1.4826
    return (values - np.median(values)) / mad if mad > 0 else np.zeros_like(values)


def active_minutes(movement):
    """Minutes clearly above the night's own movement floor (median + 3 MAD of log movement)."""
    if not movement:
        return []
    z = robust_z([np.log1p(v) for _, v in movement])
    return [t for (t, _), score in zip(movement, z) if score > 3]


def movement_features(start, movement, active):
    mid = start + EPOCH / 2
    inside = [v for t, v in movement if start <= t < start + EPOCH]
    before = [t for t in active if t <= mid]
    after = [t for t in active if t > mid]
    last, following = (before[-1] if before else None), (after[0] if after else None)
    distances = [abs(mid - t) for t in (last, following) if t is not None]
    # Sustained inactivity: the still bout around the epoch, bounded by active minutes or the record edges.
    bout_start = last if last is not None else (movement[0][0] if movement else start)
    bout_end = following if following is not None else (movement[-1][0] if movement else start + EPOCH)
    return dict(activeMinutes=sum(1 for t in active if start <= t < start + EPOCH),
        peakMinute=np.log1p(max(inside)) if inside else 0.0,
        minutesFromMove=min(distances) / 60 if distances else 720.0,
        stillBoutMinutes=max(bout_end - bout_start, 0) / 60,
        active15=sum(1 for t in active if abs(t - mid) <= 900))


def arousal_features(start, hr, br, night_hr_std, night_br_median):
    inside = [v for t, v in hr if start <= t < start + EPOCH]
    prior = [v for t, v in hr if start - 900 <= t < start]
    breaths = [v for t, v in br if start <= t < start + EPOCH]
    rise = (max(inside) - np.median(prior)) / night_hr_std if inside and prior and night_hr_std > 0 else 0.0
    return dict(hrRise=float(rise), hrMax=max(inside) if inside else None,
        brStdEpoch=float(np.std(breaths)) if len(breaths) > 1 else 0.0,
        brRel=float(np.mean(breaths) / night_br_median) if breaths and night_br_median > 0 else 0.0)


def context_features(rows, features, end):
    """Neighbours by time, not row index: a missing epoch falls back to the epoch's own value."""
    index = {f: features.index(f) for f in CONTEXT_BASE}
    by_start = {r['start']: r for r in rows}
    out = []
    for row in rows:
        values = {}
        for name, column in index.items():
            own = row['x'][column]
            values[f'{name}Prev'] = by_start.get(row['start'] - EPOCH, row)['x'][column]
            values[f'{name}Next'] = by_start.get(row['start'] + EPOCH, row)['x'][column]
            window = [r['x'][column] for s, r in by_start.items() if abs(s - row['start']) <= 3 * EPOCH]
            values[f'{name}Mean30'] = float(np.mean(window)) if window else own
        values['minutesToExit'] = (end - row['start']) / 60
        out.append(values)
    return out


def augment(night, features, db, side):
    """Return per-row dicts of every extra feature for one session, from pod data only."""
    a, b = night['start'], night['end']
    movement = db.execute('select timestamp, total_movement from movement where side=? and timestamp>=? and timestamp<? order by timestamp', (side, a, b)).fetchall()
    vitals = db.execute('select timestamp, heart_rate, breathing_rate from vitals where side=? and timestamp>=? and timestamp<? order by timestamp', (side, a, b)).fetchall()
    hr = [(t, v) for t, v, _ in vitals if v is not None and np.isfinite(v)]
    br = [(t, v) for t, _, v in vitals if v is not None and np.isfinite(v)]
    active = active_minutes(movement)
    night_hr_std = float(np.std([v for _, v in hr])) if len(hr) > 1 else 0.0
    night_br_median = float(np.median([v for _, v in br])) if br else 0.0
    rows = night['rows']
    extra = [{**movement_features(r['start'], movement, active), **arousal_features(r['start'], hr, br, night_hr_std, night_br_median)} for r in rows]
    peak_z = robust_z([e['peakMinute'] for e in extra])
    hr_max = [e['hrMax'] for e in extra]
    known = [v for v in hr_max if v is not None]
    mean, std = (float(np.mean(known)), float(np.std(known))) if known else (0.0, 0.0)
    for e, z, context in zip(extra, peak_z, context_features(rows, features, b)):
        e['peakMinuteZ'] = float(z)
        e['hrMaxZ'] = (e['hrMax'] - mean) / std if e['hrMax'] is not None and std > 0 else 0.0
        e['moveHrCoupling'] = e['activeMinutes'] * max(e['hrRise'], 0.0)
        e.update(context)
    return extra


def arm_columns(features, arm):
    return list(features) + [name for group in ARMS[arm] for name in GROUPS[group]]


def build(nights, features, extras, arm):
    columns = arm_columns(features, arm)
    out = []
    for night, extra in zip(nights, extras):
        rows = [{**row, 'x': list(row['x']) + [e[name] for group in ARMS[arm] for name in GROUPS[group]]} for row, e in zip(night['rows'], extra)]
        out.append({**night, 'rows': rows})
    return out, columns


def fit_forest(nights, labels=None):
    rows = [(r['x'], r['y']) for n in nights for r in n['rows'] if r['y'] is not None]
    if labels:
        rows = [(x, labels(y)) for x, y in rows if labels(y) is not None]
    x, y = np.asarray([r[0] for r in rows], dtype=float), np.asarray([r[1] for r in rows])
    return RandomForestClassifier(**FOREST).fit(x, y)


class Hierarchical:
    """Wake versus sleep first, then wake-free staging; predicts in the shared four-class space."""

    def __init__(self, nights):
        self.wake = fit_forest(nights, lambda y: int(y == WAKE))
        self.sleep = fit_forest(nights, lambda y: None if y == WAKE else y)

    def predict(self, x):
        x = np.asarray(x, dtype=float)
        return np.where(self.wake.predict(x) == 1, WAKE, self.sleep.predict(x))


def scores_for(clf, held, ruleset):
    y, p = rm.predict(clf, held, ruleset)
    return y, p, rm.metrics(y, p)


def f1(confusion, k):
    c = np.asarray(confusion)
    tp, predicted, actual = c[k, k], c[:, k].sum(), c[k].sum()
    return float(2 * tp / (predicted + actual)) if predicted + actual else None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('features', type=Path, help='features.json from export-runtime-features.ts')
    ap.add_argument('--snapshot', type=Path, required=True, help='The biometrics snapshot those features came from')
    ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args()
    data = json.loads(args.features.read_text())
    if data['sources']['snapshot'] != hashlib.sha256(args.snapshot.read_bytes()).hexdigest():
        raise ValueError('Snapshot does not match the one these features were exported from')
    nights, features, ruleset, side = data['nights'], data['features'], data['ruleset'], data['side']
    groups = sorted({n['group'] for n in nights})
    if len(groups) < 5:
        raise ValueError('Need at least five independent night groups')
    args.out.mkdir(parents=True, exist_ok=True)
    if any(args.out.iterdir()):
        raise ValueError('Use an empty output directory; do not overwrite an evaluation or partial run')
    with sqlite3.connect(f'file:{args.snapshot.resolve()}?mode=ro', uri=True) as db:
        extras = [augment(n, features, db, side) for n in nights]
    protocol = dict(scriptSha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        retrainingSha256=hashlib.sha256(Path(rm.__file__).read_bytes()).hexdigest(),
        arms={arm: arm_columns(features, arm) for arm in ARMS}, hierarchical=f'{HIERARCHICAL}: wake/sleep forest then three-stage forest, all features',
        forest=FOREST, outer='leave-one-night-group-out', selection='none: one frozen configuration per arm',
        postprocessing='production smoothing and transition rules', watchMotionAsFeature=False,
        prospective=False, inputSha256=hashlib.sha256(args.features.read_bytes()).hexdigest(), sources=data['sources'])
    (args.out / 'protocol.json').write_text(json.dumps(protocol, indent=2))
    datasets = {arm: build(nights, features, extras, arm)[0] for arm in ARMS}
    keys = list(ARMS) + [HIERARCHICAL]
    pooled = {k: ([], []) for k in keys}
    night_scores = {k: [] for k in keys}
    folds = []
    for group in groups:
        fold = dict(group=group, scores={})
        for key in keys:
            dataset = datasets['all' if key == HIERARCHICAL else key]
            train = [n for n in dataset if n['group'] != group]
            held = [n for n in dataset if n['group'] == group]
            assert {n['group'] for n in train}.isdisjoint(n['group'] for n in held)
            clf = Hierarchical(train) if key == HIERARCHICAL else fit_forest(train)
            y, p, result = scores_for(clf, held, ruleset)
            pooled[key][0].extend(y)
            pooled[key][1].extend(p)
            night_scores[key].append(result['macroF1'])
            fold['scores'][key] = result
        folds.append(fold)
        print(f'Held-out group {len(folds)}/{len(groups)} complete', flush=True)
    rng = np.random.default_rng(42)
    results = {}
    for key, (y, p) in pooled.items():
        result = rm.metrics(y, p)
        differences = np.array(night_scores[key]) - np.array(night_scores['base'])
        means = rng.choice(differences, size=(2000, len(differences)), replace=True).mean(axis=1)
        results[key] = dict(**result, f1={s: f1(result['confusion'], i) for i, s in enumerate(pm.STAGES)},
            versusBase={'meanNightMacroF1Difference': float(differences.mean()), 'nightBootstrap95Percent': np.quantile(means, [.025, .975]).tolist()})
    report = dict(protocol=protocol, groups=len(groups), sessions=len(nights), folds=folds, scores=results,
        limitations=['Same-person retrospective reuse of already-inspected nights; not prospective.',
            'Watch labels are the reference, not polysomnography; Watch wake labels are especially unreliable.',
            'Night bootstrap intervals are descriptive: overlapping training sets make them approximate.',
            'Seven arms on the same nights: treat any single interval excluding zero with multiplicity in mind.',
            'No export, upload or activation performed.'])
    (args.out / 'report.json').write_text(json.dumps(report, indent=2))
    for key, r in results.items():
        v = r['versusBase']
        print(f"{key:18} F1 {r['macroF1']:.3f} κ {r['kappa'] or 0:.3f} wakeF1 {r['f1']['wake'] or 0:.3f} lightF1 {r['f1']['light'] or 0:.3f} "
              f"recall {' '.join(f'{s}:{x:.2f}' for s, x in r['recall'].items() if x is not None)} "
              f"Δ {v['meanNightMacroF1Difference']:+.3f} [{v['nightBootstrap95Percent'][0]:+.3f}, {v['nightBootstrap95Percent'][1]:+.3f}]", flush=True)


if __name__ == '__main__':
    main()
