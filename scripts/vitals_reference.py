"""Check pod vitals against Watch vitals, epoch by epoch, and derive pod-only HR quality features.

Watch heart rate, respiratory rate and HRV are reference signals: they measure how
far pod vitals are from a second device and which pod-side quality signals predict
that error. They are never stage-model features. The quality features below use
pod vitals only, so a pod can compute them at inference.

Retrospective and descriptive. Watch HR is itself an estimate, not ECG.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys

import numpy as np
from scipy.stats import spearmanr

sys.path.insert(0, str(Path(__file__).resolve().parent))
spec = importlib.util.spec_from_file_location('feature_ablation', Path(__file__).with_name('feature-ablation.py'))
fa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fa)
pm = fa.pm

EPOCH = fa.EPOCH
QUALITY = ('hrCoverage', 'hrJitter', 'brCoverage')


def night_vitals(db, side, start, end):
    return db.execute('select timestamp, heart_rate, breathing_rate, hrv from vitals where side=? and timestamp>=? and timestamp<? order by timestamp',
                      (side, start, end)).fetchall()


def valid(value, bounds):
    return value is not None and np.isfinite(value) and bounds['min'] <= value <= bounds['max']


def quality_features(start, vitals, outliers):
    """Pod-only: share of minutes with an in-range HR or breathing rate, and minute-to-minute HR jitter."""
    inside = [v for v in vitals if start <= v[0] < start + EPOCH]
    hr = [v[1] for v in inside if valid(v[1], outliers['heartRate'])]
    br = [v[2] for v in inside if valid(v[2], outliers['breathingRate'])]
    minutes = EPOCH / 60
    return dict(hrCoverage=min(len(hr) / minutes, 1.0), brCoverage=min(len(br) / minutes, 1.0),
                hrJitter=float(np.median(np.abs(np.diff(hr)))) if len(hr) > 1 else 0.0)


def watch_mean(samples, start, end):
    """Mean of Watch samples whose midpoint falls in [start, end) Unix seconds."""
    values = [v for a, b, v in samples if start <= (a + b) / 2000 < end]
    return float(np.mean(values)) if values else None


def agreement(pairs):
    if len(pairs) < 3:
        return None
    pod, watch = np.asarray(pairs, float).T
    error = pod - watch
    return dict(epochs=len(pairs), bias=float(error.mean()), mae=float(np.abs(error).mean()),
                pearson=float(np.corrcoef(pod, watch)[0, 1]), spearman=float(spearmanr(pod, watch).statistic))


def stage_means(rows, key):
    """Mean within-night z-score of `key` per Watch stage: does the signal move with stage at all?"""
    out = {s: [] for s in pm.STAGES}
    for night in rows:
        values = np.asarray([r[key] for r in night if r[key] is not None], float)
        if len(values) < 5 or values.std() == 0:
            continue
        mean, std = values.mean(), values.std()
        for r in night:
            if r[key] is not None and r['y'] is not None:
                out[pm.STAGES[r['y']]].append((r[key] - mean) / std)
    return {s: (float(np.mean(v)) if v else None) for s, v in out.items()}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('features', type=Path, help='features.json from export-runtime-features.ts')
    ap.add_argument('--snapshot', type=Path, required=True, help='The biometrics snapshot those features came from')
    ap.add_argument('--watch-vitals', type=Path, required=True, help='parse-watch-export.py --vitals output')
    ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args()
    data = json.loads(args.features.read_text())
    if data['sources']['snapshot'] != hashlib.sha256(args.snapshot.read_bytes()).hexdigest():
        raise ValueError('Snapshot does not match the one these features were exported from')
    args.out.mkdir(parents=True, exist_ok=True)
    if any(args.out.iterdir()):
        raise ValueError('Use an empty output directory; do not overwrite an evaluation or partial run')
    watch = json.loads(args.watch_vitals.read_text())
    outliers = data['ruleset']['outliers']
    nights = []
    with sqlite3.connect(f'file:{args.snapshot.resolve()}?mode=ro', uri=True) as db:
        for night in data['nights']:
            vitals = night_vitals(db, data['side'], night['start'], night['end'])
            rows = []
            for row in night['rows']:
                a, b = row['start'], row['start'] + EPOCH
                inside = [v for v in vitals if a <= v[0] < b]
                hr = [v[1] for v in inside if valid(v[1], outliers['heartRate'])]
                br = [v[2] for v in inside if valid(v[2], outliers['breathingRate'])]
                hrv = [v[3] for v in inside if valid(v[3], outliers['hrv'])]
                rows.append(dict(y=row['y'], **quality_features(a, vitals, outliers),
                                 podHr=float(np.mean(hr)) if hr else None, podBr=float(np.mean(br)) if br else None,
                                 podHrv=float(np.mean(hrv)) if hrv else None,
                                 watchHr=watch_mean(watch['heartRate'], a, b), watchBr=watch_mean(watch['respiratoryRate'], a, b),
                                 watchHrv=watch_mean(watch['hrvSdnn'], a, b)))
            nights.append(rows)
    flat = [r for night in nights for r in night]
    hr_pairs = [r for r in flat if r['podHr'] is not None and r['watchHr'] is not None]
    by_coverage = {}
    for name, keep in (('full', lambda r: r['hrCoverage'] >= 1), ('partial', lambda r: 0 < r['hrCoverage'] < 1)):
        by_coverage[name] = agreement([(r['podHr'], r['watchHr']) for r in hr_pairs if keep(r)])
    error = np.abs([r['podHr'] - r['watchHr'] for r in hr_pairs])
    predictors = {q: float(spearmanr([r[q] for r in hr_pairs], error).statistic) for q in QUALITY} if len(hr_pairs) > 2 else {}
    report = dict(
        protocol=dict(scriptSha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), epochSeconds=EPOCH,
                      watchSampleRule='Watch samples whose midpoint falls in the epoch', podOutliers=outliers,
                      inputSha256=hashlib.sha256(args.features.read_bytes()).hexdigest(),
                      watchVitalsSha256=hashlib.sha256(args.watch_vitals.read_bytes()).hexdigest(), sources=data['sources']),
        nights=len(nights), epochs=len(flat),
        coverage={k: float(np.mean([r[k] is not None for r in flat])) for k in ('podHr', 'watchHr', 'podBr', 'watchBr', 'podHrv', 'watchHrv')},
        heartRate=dict(all=agreement([(r['podHr'], r['watchHr']) for r in hr_pairs]), byPodCoverage=by_coverage,
                       byWatchStage={s: agreement([(r['podHr'], r['watchHr']) for r in hr_pairs if r['y'] == i]) for i, s in enumerate(pm.STAGES)},
                       absErrorSpearmanWithQuality=predictors),
        breathingRate=agreement([(r['podBr'], r['watchBr']) for r in flat if r['podBr'] is not None and r['watchBr'] is not None]),
        hrv=dict(note='Pod HRV and Watch SDNN are different statistics; only rank agreement is meaningful.',
                 **(agreement([(r['podHrv'], r['watchHrv']) for r in flat if r['podHrv'] is not None and r['watchHrv'] is not None]) or {})),
        stageZ={k: stage_means(nights, k) for k in ('podHr', 'watchHr', 'podBr', 'watchBr')},
        limitations=['One participant; Watch vitals are device estimates, not ECG or capnography.',
                     'Watch heart rate is sparse during sleep; epochs without a Watch sample are excluded from agreement.',
                     'Watch vitals are reference signals only, never stage-model features.'])
    (args.out / 'report.json').write_text(json.dumps(report, indent=2))
    hr = report['heartRate']
    print(f"{report['nights']} nights · {report['epochs']} epochs · coverage " + ', '.join(f'{k} {v:.0%}' for k, v in report['coverage'].items()))
    for name, a in [('HR all', hr['all']), *((f'HR pod coverage {k}', v) for k, v in by_coverage.items()), ('BR', report['breathingRate'])]:
        if a:
            print(f"{name:24} n={a['epochs']} bias {a['bias']:+.1f} MAE {a['mae']:.1f} r {a['pearson']:.2f} ρ {a['spearman']:.2f}")
    print('|HR error| Spearman with ' + ', '.join(f'{k} {v:+.2f}' for k, v in predictors.items()))
    for key, means in report['stageZ'].items():
        print(f'{key:8} z by Watch stage: ' + ' '.join(f'{s} {v:+.2f}' for s, v in means.items() if v is not None))


if __name__ == '__main__':
    main()
