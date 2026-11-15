"""Pod movement model: raw piezo, capacitance and the pod movement score, labeled by Watch motion.

  train   Leave-one-night-out comparison of the pod movement score, the movement.py
          classifier and this model, on 2 s windows and on 5-minute epochs, then fit
          one model per held-out night plus one on every night.
  epochs  Apply the model to raw exports of sleep sessions and write per-epoch
          movement features for the stage model, cross-fitted: a session gets a model
          fitted without every Watch-motion night whose windows fall inside it, plus
          one variant per other Watch-motion night T fitted without T as well, so a
          stage model tested on T never trains on features T's Watch motion taught.
  export  Write the final model of an existing model.joblib as the portable forest
          `train` writes to model/movement.json (movementRaw-v1, test vectors, SHA-256).

Inputs are pod-only, so a pod could compute them; Watch motion is the label only.
Watch stages are derived partly from Watch acceleration, so these features carry
Watch-motion information into staging by design (distillation), never at inference.
Retrospective; never uploads or activates anything.
"""
import argparse
import bisect
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
from pathlib import Path
import sys

import joblib
import numpy as np
from scipy.stats import spearmanr
from sklearn.ensemble import RandomForestClassifier

sys.path.insert(0, str(Path(__file__).resolve().parent))
import movement as mv
import movement_portable as portable
import piezo_bandwidth as pb
import raw_piezo as rp

CLASSES = ('still', 'light', 'vigorous')
EPOCH = 300
INFERENCE_HOP_S = 2.0
EPOCH_FEATURES = ('mmActive', 'mmMoving', 'mmPeak')
# Epoch features count windows strictly above a threshold: at most this share of still
# training windows (out-of-bag) is flagged, also when many probabilities tie at the cutoff. Chosen on Watch-motion epoch agreement,
# never on stage results: a balanced threshold leaves a false-alarm floor in still epochs.
STRICT_FALSE_ALARM = 0.01
# Frozen before evaluation. Shallower than movement.py's forest so probabilities are smoother and the fitted file stays small.
MODEL = dict(n_estimators=200, max_depth=12, min_samples_leaf=20, class_weight='balanced_subsample', oob_score=True, random_state=42, n_jobs=-1)
V1 = dict(n_estimators=300, min_samples_leaf=2, class_weight='balanced', random_state=42, n_jobs=-1)


def matrix(rows):
    """movement.py window features plus the pod score of the minute around the window (NaN when the pod has none)."""
    return np.asarray([r['x'] + [np.nan if r['baseline'] is None else r['baseline']] for r in rows], float)


def moving_probability(clf, x):
    return 1 - clf.predict_proba(x)[:, list(clf.classes_).index('still')]


def balanced_threshold(probability, moving):
    """Threshold on P(moving) that maximizes balanced accuracy; fitted on out-of-bag training predictions."""
    best = (-1.0, 0.5)
    for threshold in np.quantile(probability, np.linspace(0.01, 0.99, 197)):
        predicted = probability >= threshold
        score = (predicted[moving].mean() + (~predicted[~moving]).mean()) / 2
        best = max(best, (score, float(threshold)))
    return best[1]


def fit(rows):
    return fit_matrix(matrix(rows), np.asarray([r['label'] for r in rows]))


def fit_matrix(x, labels):
    clf = RandomForestClassifier(**MODEL).fit(x, labels)
    oob = 1 - clf.oob_decision_function_[:, list(clf.classes_).index('still')]
    finite = np.isfinite(oob)
    moving = labels[finite] != 'still'
    return dict(model=clf, threshold=balanced_threshold(oob[finite], moving),
                strictThreshold=float(np.quantile(oob[finite][~moving], 1 - STRICT_FALSE_ALARM)))


def export_meta(nights, labels, source):
    return dict(columns=mv.FEATURE_NAMES + ['podScore'], windowSeconds=mv.WINDOW_S, hopSeconds=INFERENCE_HOP_S, sampleRateHz=mv.FS,
                epochSeconds=EPOCH, epochFeatures=EPOCH_FEATURES, hyperparameters={k: v for k, v in MODEL.items() if k != 'n_jobs'},
                training=dict(nights=sorted(set(nights.tolist())), windows=int(len(labels)),
                              classCounts={c: int((labels == c).sum()) for c in CLASSES}), source=source)


def write_portable(fitted, x, labels, nights, source, out):
    result = portable.export(fitted, x, labels, export_meta(nights, labels, source), out)
    print(f"Portable forest: {result['path']} · {result['bytes'] / 1e6:.1f} MB · {result['nodes']:,} nodes · {result['testVectors']} test vectors · "
          f"max |Δ| vs sklearn {max(result['maxAbsDifference'].values()):.1e} on {result['parityWindows']:,} windows", flush=True)
    return result


def epoch_agreement(rows, moving):
    """Per 5-minute block of a night: Watch moving fraction against the predicted moving fraction."""
    blocks = {}
    for row, flag in zip(rows, moving):
        blocks.setdefault((row['night'], int(row['t'] // EPOCH)), []).append((row['label'] != 'still', flag))
    truth = np.asarray([np.mean([t for t, _ in v]) for v in blocks.values()])
    predicted = np.asarray([np.mean([p for _, p in v]) for v in blocks.values()], float)
    any_truth, any_predicted = truth > 0, predicted > 0
    return dict(epochs=len(blocks), spearman=float(spearmanr(truth, predicted).statistic), mae=float(np.abs(truth - predicted).mean()),
                movingEpochRecall=float(any_predicted[any_truth].mean()) if any_truth.any() else None,
                stillEpochFalseAlarm=float(any_predicted[~any_truth].mean()) if (~any_truth).any() else None)


def cmd_train(args):
    out = pb.prepare_output(args.out)
    rows = [r for path in args.windows for r in json.loads(Path(path).read_text()) if r.get('watch') and r['baseline'] is not None]
    nights = sorted({r['night'] for r in rows})
    if len(nights) < 2:
        raise SystemExit('Need Watch-labeled windows from at least two nights')
    if 'still' not in {r['label'] for r in rows}:
        raise SystemExit('Need still-labelled Watch windows to calibrate the moving threshold')
    labels = np.asarray([r['label'] for r in rows])
    truth = labels != 'still'
    arms = {k: np.zeros(len(rows), bool) for k in ('podScore', 'classifierV1', 'model', 'modelStrict')}
    probability = np.zeros(len(rows))
    folds = {}
    for night in nights:
        test = np.asarray([r['night'] == night for r in rows])
        train = [r for r, t in zip(rows, test) if not t]
        held = [r for r, t in zip(rows, test) if t]
        if not any(r['label'] == 'still' for r in train):
            raise SystemExit(f'Holding out {night} leaves no still-labelled windows to train on')
        scores = np.asarray([r['baseline'] for r in train], float)
        threshold = mv.best_threshold(scores, np.asarray([r['label'] != 'still' for r in train]))
        arms['podScore'][test] = np.asarray([r['baseline'] for r in held], float) >= threshold
        v1 = RandomForestClassifier(**V1).fit(np.asarray([r['x'] for r in train]), [r['label'] for r in train])
        arms['classifierV1'][test] = v1.predict(np.asarray([r['x'] for r in held])) != 'still'
        fitted = fit(train)
        probability[test] = moving_probability(fitted['model'], matrix(held))
        arms['model'][test] = probability[test] >= fitted['threshold']
        arms['modelStrict'][test] = probability[test] > fitted['strictThreshold']
        folds[night] = fitted
        print(f'Held-out night {night}: {int(test.sum())} windows', flush=True)
    final = fit(rows)
    # The teaching windows travel with the model so `epochs` can refit without any set of nights.
    teaching = dict(x=matrix(rows), labels=labels, nights=np.asarray([r['night'] for r in rows]),
                    t=np.asarray([r['t'] for r in rows], float))
    joblib.dump(dict(final=final, heldOut=folds, teaching=teaching, classes=CLASSES, features=mv.FEATURE_NAMES + ['podScore'],
                     windowSeconds=mv.WINDOW_S, epochFeatures=EPOCH_FEATURES), out / 'model.joblib', compress=3)
    exported = write_portable(final, teaching['x'], labels, teaching['nights'], 'movement_model.py train; leave-one-night-out report.json',
                              out / 'model' / 'movement.json')
    per_night = {night: {arm: mv.binary_metrics(truth[m], flags[m], labels[m])['balancedAccuracy'] for arm, flags in arms.items()}
                 for night in nights for m in [np.asarray([r['night'] == night for r in rows])]}
    report = dict(version='movement-model-report-v1', validation='leave-one-night-out', nights=nights, windows=len(rows),
                  classCounts={c: int((labels == c).sum()) for c in CLASSES},
                  windowLevel={arm: mv.binary_metrics(truth, flags, labels) for arm, flags in arms.items()},
                  epochLevel={**{arm: epoch_agreement(rows, flags) for arm, flags in arms.items()},
                              'modelProbability': epoch_agreement(rows, probability)},
                  perNightBalancedAccuracy=per_night, thresholds={n: {k: f[k] for k in ('threshold', 'strictThreshold')} for n, f in (folds | {'final': final}).items()},
                  strictFalseAlarm=STRICT_FALSE_ALARM,
                  model=MODEL, classifierV1=V1, portable=exported, featureNames=mv.FEATURE_NAMES + ['podScore'],
                  scriptSha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  inputs={str(p): hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in args.windows},
                  limitations=['Labels are preregistered Watch-motion heuristics on the wrist, not whole-body ground truth.',
                               'NTP wall-time alignment between Watch and pod is unverified.',
                               'One participant; nights share a person, bed and setup.',
                               'Raw-piezo features need a core port before a pod can compute them.'])
    (out / 'report.json').write_text(json.dumps(report, indent=2))
    for arm, m in report['windowLevel'].items():
        e = report['epochLevel'][arm]
        print(f"{arm:13} windows: moving recall {mv.pct(m['movingRecall'])}, still false alarm {mv.pct(m['stillFalseAlarm'])}, "
              f"balanced {m['balancedAccuracy']:.3f} · epochs: ρ {e['spearman']:.2f}, moving-epoch recall {mv.pct(e['movingEpochRecall'])}, "
              f"still-epoch false alarm {mv.pct(e['stillEpochFalseAlarm'])}")
    print(f"model P(moving) epochs: ρ {report['epochLevel']['modelProbability']['spearman']:.2f}")
    print(f'Report: {out / "report.json"}')


def session_windows(recs, side, rows, movement):
    """Unlabeled inference windows for each epoch row; windows with missing raw data are skipped and counted."""
    other = 'right' if side == 'left' else 'left'
    seconds, _ = rp.piezo_seconds(recs)
    caps = mv.capsense_series(recs)
    stamps = [m[0] for m in movement]
    out = []
    for row in rows:
        x, expected = [], 0
        t = row['start']
        while t + mv.WINDOW_S <= row['start'] + EPOCH:
            expected += 1
            occ = mv.piezo_slice(seconds, f'{side}1', t, t + mv.WINDOW_S)
            oth = mv.piezo_slice(seconds, f'{other}1', t, t + mv.WINDOW_S)
            cap_occ, ok_occ = mv.capsense_features(caps[side], t, t + mv.WINDOW_S)
            cap_oth, ok_oth = mv.capsense_features(caps[other], t, t + mv.WINDOW_S)
            if occ is not None and oth is not None and ok_occ and ok_oth:
                # Pod movement rows stamp the end of their minute.
                i = bisect.bisect_right(stamps, t + mv.WINDOW_S / 2)
                score = movement[i][1] if i < len(movement) and movement[i][0] - 60 <= t + mv.WINDOW_S / 2 else np.nan
                x.append(mv.piezo_features(occ) + mv.piezo_features(oth) + cap_occ + cap_oth + [score])
            t += INFERENCE_HOP_S
        out.append((row['start'], np.asarray(x, float), expected))
    return out


def epoch_features(fitted, windows):
    rows = []
    for start, x, expected in windows:
        if len(x) == 0:
            rows.append(dict(start=start, coverage=0.0, **{k: None for k in EPOCH_FEATURES}))
            continue
        proba = fitted['model'].predict_proba(x)
        classes = list(fitted['model'].classes_)
        moving = 1 - proba[:, classes.index('still')]
        rows.append(dict(start=start, coverage=len(x) / expected, mmActive=float((moving > fitted['strictThreshold']).mean()),
                         mmMoving=float(moving.mean()), mmPeak=float(moving.max())))
    return rows


def one_session(job):
    """Window features for one session; the parent process applies every model it needs."""
    export, night, side, movement = job
    recs, errors, _ = pb.load_export(export)
    return night['id'], session_windows(recs, side, night['rows'], movement), len(errors), hashlib.sha256(Path(export).read_bytes()).hexdigest()


def own_exclusions(night, teaching):
    """Watch-motion nights a session must not be scored with: its own group and any whose windows fall inside it."""
    inside = (teaching['t'] >= night['start']) & (teaching['t'] < night['end'])
    known = set(teaching['nights'].tolist())
    return frozenset(set(teaching['nights'][inside].tolist()) | ({night['group']} & known))


class CrossFitted:
    """Movement models keyed by the Watch-motion nights they exclude; fitted once each, on demand."""

    def __init__(self, bundle):
        self.teaching = bundle['teaching']
        self.models = {frozenset(): bundle['final'], **{frozenset({n}): f for n, f in bundle['heldOut'].items()}}

    def get(self, excluded):
        if excluded not in self.models:
            keep = ~np.isin(self.teaching['nights'], sorted(excluded))
            labels = self.teaching['labels'][keep]
            if 'still' not in set(labels.tolist()):
                raise SystemExit(f'Excluding {", ".join(sorted(excluded))} leaves no still-labelled windows')
            print(f'Fitting movement model without {", ".join(sorted(excluded))}', flush=True)
            self.models[excluded] = fit_matrix(self.teaching['x'][keep], labels)
        return self.models[excluded]


def label(excluded):
    return 'without ' + ', '.join(sorted(excluded)) if excluded else 'final'


def cmd_epochs(args):
    import sqlite3
    out = Path(args.out)
    if out.exists():
        raise SystemExit(f'{out} exists; choose a new path')
    data = json.loads(Path(args.features).read_text())
    if data['sources']['snapshot'] != hashlib.sha256(Path(args.snapshot).read_bytes()).hexdigest():
        raise ValueError('Snapshot does not match the one these features were exported from')
    bundle = joblib.load(args.model)
    if 'teaching' not in bundle:
        raise SystemExit(f'{args.model} predates cross-fitted epochs; retrain it with this version of movement_model.py train')
    models = CrossFitted(bundle)
    teaching_nights = sorted(set(bundle['teaching']['nights'].tolist()))
    by_id = {str(n['id']): n for n in data['nights']}
    jobs = []
    with sqlite3.connect(f'file:{Path(args.snapshot).resolve()}?mode=ro', uri=True) as db:
        for value in args.export:
            session, _, export = value.partition('=')
            night = by_id[session]
            movement = db.execute('select timestamp, total_movement from movement where side=? and timestamp>=? and timestamp<? order by timestamp',
                                  (data['side'], night['start'] - 120, night['end'] + 120)).fetchall()
            jobs.append((export, night, data['side'], movement))
    sessions = {}
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for night_id, windows, decode_errors, export_sha in pool.map(one_session, jobs):
            session = str(night_id)
            night = by_id[session]
            own = own_exclusions(night, bundle['teaching'])
            rows = epoch_features(models.get(own), windows)
            excluding = {t: epoch_features(models.get(own | {t}), windows) for t in teaching_nights if t not in own}
            sessions[session] = dict(group=night['group'], model=label(own), excludedNights=sorted(own), decodeErrors=decode_errors,
                                     exportSha256=export_sha, rows=rows, excluding=excluding)
            covered = [r['coverage'] for r in rows]
            print(f"Session {session} ({night['group']}, {label(own)}): {len(covered)} epochs, mean raw coverage {np.mean(covered):.0%}", flush=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(dict(version='movement-epochs-v2', features=list(EPOCH_FEATURES), epochSeconds=EPOCH,
                                   teachingNights=teaching_nights,
                                   crossFitting='rows: model without the session\'s own Watch-motion nights; excluding[T]: also without night T',
                                   modelSha256=hashlib.sha256(Path(args.model).read_bytes()).hexdigest(),
                                   inputSha256=hashlib.sha256(Path(args.features).read_bytes()).hexdigest(), sessions=sessions)))
    print(f'Wrote {out}')


def cmd_export(args):
    out = Path(args.out)
    if out.exists():
        raise SystemExit(f'{out} exists; choose a new path')
    bundle = joblib.load(args.model)
    if 'teaching' in bundle:
        x, labels, nights = bundle['teaching']['x'], bundle['teaching']['labels'], bundle['teaching']['nights']
    elif args.windows:
        # Models from before the teaching windows were saved: rebuild them exactly as `train` selected them.
        rows = [r for path in args.windows for r in json.loads(Path(path).read_text()) if r.get('watch') and r['baseline'] is not None]
        x, labels, nights = matrix(rows), np.asarray([r['label'] for r in rows]), np.asarray([r['night'] for r in rows])
    else:
        raise SystemExit(f'{args.model} has no teaching windows; pass the --windows it was trained on')
    digest = hashlib.sha256(Path(args.model).read_bytes()).hexdigest()
    write_portable(bundle['final'], x, labels, nights, f'movement_model.py export of model.joblib sha256 {digest}', out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='command', required=True)
    t = sub.add_parser('train', help='leave-one-night-out comparison and fitted models')
    t.add_argument('--windows', action='append', required=True, help='windows.json from movement.py train with --watch-motion; repeatable')
    t.add_argument('--out', required=True)
    e = sub.add_parser('epochs', help='per-epoch movement features for sleep sessions')
    e.add_argument('features', help='features.json from export-runtime-features.ts')
    e.add_argument('--snapshot', required=True, help='The biometrics snapshot those features came from')
    e.add_argument('--model', required=True, help='model.joblib from train')
    e.add_argument('--export', action='append', required=True, help='sessionId=raw-export.tar.gz; repeatable')
    e.add_argument('--workers', type=int, default=4)
    e.add_argument('--out', required=True, help='movement-epochs JSON path (new)')
    x = sub.add_parser('export', help='portable JSON forest from an existing model.joblib')
    x.add_argument('model', help='model.joblib from train')
    x.add_argument('--windows', action='append', default=[], help='windows.json it was trained on, for a model.joblib without teaching windows')
    x.add_argument('--out', required=True, help='movement.json path (new)')
    args = ap.parse_args(argv)
    {'train': cmd_train, 'epochs': cmd_epochs, 'export': cmd_export}[args.command](args)


if __name__ == '__main__':
    sys.exit(main())
