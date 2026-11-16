"""Walk-forward backtest of stage models: train only on earlier nights, score the next.

Two schedules, both strictly chronological by night group:
- walk-forward: before each test night, refit on every earlier night (expanding window).
- frozen: fit once on the first nights, score every later night without refitting,
  as a model shipped on that date and never retrained would.
Arms are the frozen forests from feature-ablation.py plus the stages the pod
actually recorded live. Locked holdout nights (holdout.py) are dropped first. Not
prospective; never exports, uploads or activates.

--trial scores two frozen pod models on the same new Watch nights instead: the stages
the pod shows (pod_live, the selected model) and a library model staged on the pod
by the read-only model preview. Locked nights are included and each read is logged.
Results replace earlier scores of the same night in the trial's weekly.jsonl.
"""
import argparse
from collections import defaultdict
from datetime import date, datetime, timezone
import hashlib
import importlib.util
import json
import math
from math import comb
from pathlib import Path
import sqlite3
import subprocess
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
spec = importlib.util.spec_from_file_location('feature_ablation', Path(__file__).with_name('feature-ablation.py'))
fa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fa)
rm, pm = fa.rm, fa.pm
from fetch_pod import Pod, millis
import holdout as ho
from run_history import primary_root

ROOT = Path(__file__).resolve().parents[1]
MODELS = ('base', 'all', 'hierarchical_all')
POD = 'pod_live'
PREVIEW = 'preview'
MIN_TRAIN_GROUPS = 8
TRIAL_AFTER = '2026-11-13'
RESAMPLES = 10_000


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


def compare(scores, a, b, metric, rng, resamples=5000):
    pairs = [(x[a][metric], x[b][metric]) for x in scores if x[a][metric] is not None and x[b][metric] is not None]
    d = np.array([p - q for p, q in pairs])
    if len(d) == 0:
        return None
    means = rng.choice(d, size=(resamples, len(d)), replace=True).mean(axis=1)
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


def canonical_json(value):
    """Core's finite-binary64-json-v1 (modules/common/biometrics/config.py canonical_json): keys sorted
    by UTF-16, no whitespace, each number as its exact decimal expansion, so Python and JS agree."""
    def visit(item):
        if item is None:
            return 'null'
        if isinstance(item, bool):
            return 'true' if item else 'false'
        if isinstance(item, (int, float)):
            number = float(item)
            if not math.isfinite(number):
                raise ValueError('Non-finite JSON number')
            if number == 0:
                return '0'
            numerator, denominator = abs(number).as_integer_ratio()
            places = denominator.bit_length() - 1
            digits = str(numerator * 5 ** places)
            if places:
                digits = digits.zfill(places + 1)
                digits = (digits[:-places] + '.' + digits[-places:]).rstrip('0').rstrip('.')
            return ('-' if number < 0 else '') + digits
        if isinstance(item, str):
            return json.dumps(item, ensure_ascii=False, separators=(',', ':'))
        if isinstance(item, list):
            return '[' + ','.join(visit(child) for child in item) + ']'
        if isinstance(item, dict):
            return '{' + ','.join(visit(key) + ':' + visit(item[key]) for key in sorted(item, key=lambda key: key.encode('utf-16-be'))) + '}'
        raise ValueError('Expected finite JSON data')
    return visit(value)


def model_sha256(model):
    """Core's stageModelSha256: SHA-256 of the model as the pod stores it (epochSeconds defaults to 300)."""
    return hashlib.sha256(canonical_json({'epochSeconds': 300, **model}).encode('utf-8')).hexdigest()


def watch_stages(snapshot, side):
    """Watch stages in a snapshot's reference_stages (core archive layout) as the exporter's watch.json."""
    with sqlite3.connect(f'file:{Path(snapshot).resolve()}?mode=ro', uri=True) as db:
        rows = db.execute("select start, end, stage from reference_stages where side=? and source='apple_watch' order by start", (side,)).fetchall()
    return {'stages': [[int(a * 1000), int(b * 1000), stage] for a, b, stage in rows]}


def stage_at(blocks, seconds):
    """The one stage covering an epoch midpoint, from (start ms, end ms, stage) blocks; None when none or several."""
    found = {stage for a, b, stage in blocks if a <= seconds * 1000 < b}
    return pm.STAGES.index(found.pop()) if len(found) == 1 else None


def trial_nights(data, after):
    """Watch-labelled sessions of night groups after `after`, by group."""
    groups = defaultdict(list)
    for night in data['nights']:
        if night['group'] > after and any(r['y'] is not None for r in night['rows']):
            groups[night['group']].append(night)
    return dict(sorted(groups.items()))


def training_cutoff(model, path=None, explicit=None):
    """(last night group the model trained on, where that came from); None when nothing records it."""
    if explicit:
        return explicit, 'command line'
    if (model.get('metrics') or {}).get('trainedThrough'):
        return model['metrics']['trainedThrough'], 'model metadata'
    features = Path(path).resolve().parent.parent / 'features.json' if path else None
    if features and features.is_file():
        return max(n['group'] for n in json.loads(features.read_text())['nights']), f'last night group in {ho.run_name(features)}'
    return None, 'not recorded, so no night counts as out of sample'


def align(nights, pod_blocks, preview_blocks):
    """Labelled epochs where both models have a stage: (watch, pod_live, preview) lists."""
    y, a, b = [], [], []
    for night in nights:
        live, other = pod_blocks.get(str(night['id'])), preview_blocks.get(str(night['id']))
        if live is None or other is None:
            continue
        for row in night['rows']:
            mid = row['start'] + pm.EPOCH / 2
            p, q = stage_at(live, mid), stage_at(other, mid)
            if row['y'] is not None and p is not None and q is not None:
                y.append(row['y'])
                a.append(p)
                b.append(q)
    return y, a, b


def read_weekly(path):
    path = Path(path)
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def merge_weekly(path, entries):
    """Write `entries` into the weekly log, replacing earlier scores of the same (side, night, model id); atomic."""
    path = Path(path)
    keys = {(e['side'], e['night'], e['modelId']) for e in entries}
    rows = [e for e in read_weekly(path) if (e['side'], e['night'], e['modelId']) not in keys] + list(entries)
    rows.sort(key=lambda e: (e['side'], e['night'], e['modelId']))
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(''.join(json.dumps(e, sort_keys=True) + '\n' for e in rows))
    temp.replace(path)
    return rows


def trial_summary(entries, side, live_id, baseline_id, rng):
    """Paired per-night differences live − baseline on nights out of sample for both models."""
    scored = {(e['night'], e['modelId']): e for e in entries if e['side'] == side}
    paired, excluded = [], []
    for night in sorted({n for n, _ in scored}):
        a, b = scored.get((night, live_id)), scored.get((night, baseline_id))
        if not (a and b):
            excluded.append(dict(night=night, reason=f'scored for id {live_id if a else baseline_id} only'))
        elif not (a['outOfSample'] and b['outOfSample']):
            excluded.append(dict(night=night, reason='in sample for id ' + ' and '.join(str(e['modelId']) for e in (a, b) if not e['outOfSample'])))
        else:
            paired.append(dict(group=night, locked=a['locked'], live=a['metrics'], baseline=b['metrics']))
    return dict(liveId=live_id, baselineId=baseline_id, nights=paired, excluded=excluded, resamples=RESAMPLES, seed=42,
                comparisons={m: compare(paired, 'live', 'baseline', m, rng, RESAMPLES) for m in ('macroF1', 'kappa')})


def print_trial(summary, models):
    live, base = models[POD], models[PREVIEW]
    print(f"\nTrial: id {live['id']} ({POD}, {live['version']}) vs id {base['id']} (model preview, {base['version']})")
    for m in (live, base):
        print(f"  id {m['id']} trained through {m['trainedThrough'] or 'unknown'} ({m['cutoffSource']}); out of sample after it")
    print(f"{'night':11} {'locked':6} {'epochs':>6}  F1 id{live['id']}  F1 id{base['id']}     ΔF1   κ id{live['id']}   κ id{base['id']}      Δκ")
    for n in summary['nights']:
        a, b = n['live'], n['baseline']
        dk = a['kappa'] - b['kappa'] if a['kappa'] is not None and b['kappa'] is not None else None
        print(f"{n['group']:11} {'yes' if n['locked'] else '':6} {a['epochs']:6}  {a['macroF1']:7.3f}  {b['macroF1']:7.3f}  {a['macroF1'] - b['macroF1']:+7.3f}  "
              f"{a['kappa'] if a['kappa'] is not None else float('nan'):6.3f}  {b['kappa'] if b['kappa'] is not None else float('nan'):6.3f}  "
              f"{dk if dk is not None else float('nan'):+7.3f}")
    for metric, c in summary['comparisons'].items():
        if c:
            print(f"{metric} id {live['id']} − id {base['id']}: mean {c['meanDifference']:+.3f}, 95% {c['bootstrap95Percent'][0]:+.3f} to "
                  f"{c['bootstrap95Percent'][1]:+.3f} ({summary['resamples']:,} night resamples, seed {summary['seed']}); "
                  f"id {live['id']} better on {c['better']}, worse on {c['worse']} of {c['nights']} nights; sign test p={c['signTestP']:.3f}")
        else:
            print(f'{metric}: no night is out of sample for both models yet')
    for e in summary['excluded']:
        print(f"Not compared: {e['night']} ({e['reason']})")
    print('Q5 (decide 2026-11-28): keep id 3 unless id 2 beats it on these nights. Watch stages are the reference, one participant.')


def trial(ap, args):
    if args.features or args.holdout_final:
        ap.error('--trial reads --snapshot or --run, takes no features file, and includes locked nights by design')
    if bool(args.snapshot) == bool(args.run) or not args.pod or not args.baseline_model:
        ap.error('--trial needs --pod, --baseline-model and one of --snapshot or --run')
    out = (args.out or primary_root() / 'data/runs/trial-id3').resolve()
    work = out / 'scored' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    work.mkdir(parents=True, exist_ok=False)
    pod = Pod(args.pod, work / 'responses')
    if args.run:
        features = args.run / 'features.json'
    else:
        (work / 'watch.json').write_text(json.dumps(watch_stages(args.snapshot, args.side)))
        features = work / 'features.json'
        time_zone = args.timezone or pod.get('settings')['device']['timezone']
        subprocess.run([str(ROOT / 'node_modules/.bin/tsx'), str(ROOT / 'scripts/export-runtime-features.ts'), str(args.snapshot.resolve()),
                        str(work / 'watch.json'), str(features), args.side, time_zone], check=True)
    data = json.loads(features.read_text())
    if data['side'] != args.side:
        raise ValueError(f"Features are for the {data['side']} side, not {args.side}")
    groups = trial_nights(data, args.after)
    if not groups:
        # A week without a new Watch night is not an error; nothing is read from the pod's models or scored.
        last = max((n['group'] for n in data['nights']), default='none')
        print(f'No Watch-labelled night groups after {args.after} (latest labelled night group: {last}); nothing to score')
        return

    library = {m['id']: m for m in pod.get('sleep-stages/models', side=args.side)}
    if args.live_id not in library or args.baseline_id not in library:
        raise ValueError(f'The {args.side} model library has no id {args.live_id if args.live_id not in library else args.baseline_id}')
    live, base = library[args.live_id], library[args.baseline_id]
    selected = pod.get('sleep-stages/model', side=args.side)
    if not (live['selected'] and live['active'] and selected.get('active') and (selected.get('model') or {}).get('version') == live['version']):
        raise ValueError(f'Library id {args.live_id} is not the selected, active model on {args.side}, so the pod\'s stages are not its stages')
    baseline_model = json.loads(args.baseline_model.read_text())
    if baseline_model.get('version') != base['version']:
        raise ValueError(f"--baseline-model is {baseline_model.get('version')}; library id {args.baseline_id} is {base['version']}")
    if args.live_model and model_sha256(json.loads(args.live_model.read_text())) != model_sha256(selected['model']):
        raise ValueError('--live-model differs from the model the pod has selected')
    models = {}
    for key, entry, model, path, source in ((POD, live, selected['model'], args.live_model, 'GET /api/biometrics/sleep-stages: the pod restages on read with its selected model'),
                                            (PREVIEW, base, baseline_model, args.baseline_model, 'POST /api/sleep-stages/model/preview (read-only) with --baseline-model')):
        cutoff, cutoff_source = training_cutoff(model, path, args.trained_through.get(entry['id']))
        models[key] = dict(id=entry['id'], version=entry['version'], sha256=model_sha256(model), trainedThrough=cutoff, cutoffSource=cutoff_source, source=source)

    # Every read of a locked night is logged before any stage for it is fetched or scored.
    locked = [g for g in groups if ho.locked(g)]
    if locked:
        for m in models.values():
            ho.log_access(ho.run_name(work), m['sha256'], locked, 'trial')
    sessions = [n for nights in groups.values() for n in nights]
    records = {(millis(r['enteredBedAt']) // 1000, millis(r['leftBedAt']) // 1000): r
               for r in pod.range('biometrics/sleep-records', args.side, (min(n['start'] for n in sessions) - 86400) * 1000,
                                  (max(n['end'] for n in sessions) + 86400) * 1000, 100) if r.get('leftBedAt')}
    blocks, record_ids, skipped = {POD: {}, PREVIEW: {}}, {}, []
    for night in sessions:
        record = records.get((night['start'], night['end']))
        if record is None:
            skipped.append(dict(night=night['group'], session=str(night['id']), reason='no pod sleep record with the same bed times'))
            continue
        trace = pod.get('biometrics/sleep-stages', side=args.side, sleepRecordId=record['id'])
        if trace.get('sleepRecordId') != record['id'] or not isinstance(trace.get('epochs'), list):
            raise ValueError('Pod stage trace does not match the requested sleep record')
        staged = trace.get('stagedBy') or {}
        if staged.get('engine') != 'model' or staged.get('version') != live['version']:
            skipped.append(dict(night=night['group'], session=str(night['id']), reason=f'the pod staged it with {staged or "an unrecorded engine"}, not id {live["id"]}'))
            continue
        preview = pod.post('sleep-stages/model/preview', dict(side=args.side, sleepRecordId=record['id'], model=baseline_model))
        if preview.get('version') != base['version'] or [n['sleepRecordId'] for n in preview['nights']] != [record['id']]:
            raise ValueError('Model preview does not match the requested model and sleep record')
        blocks[POD][str(night['id'])] = [(e['start'], e['start'] + e['duration'], e['stage']) for e in trace['epochs']]
        blocks[PREVIEW][str(night['id'])] = [(b['start'], b['end'], b['stage']) for b in preview['nights'][0]['blocks']]
        record_ids[str(night['id'])] = record['id']

    scored_at = datetime.now(timezone.utc).isoformat(timespec='seconds')
    entries = []
    for group, nights in groups.items():
        y, a, b = align(nights, blocks[POD], blocks[PREVIEW])
        if not y:
            if not any(s['night'] == group for s in skipped):
                skipped.append(dict(night=group, reason='no labelled epoch staged by both models'))
            continue
        ids = sorted(record_ids[str(n['id'])] for n in nights if str(n['id']) in record_ids)
        for key, predictions in ((POD, a), (PREVIEW, b)):
            m = models[key]
            entries.append(dict(side=args.side, night=group, model=key, modelId=m['id'], version=m['version'], sha256=m['sha256'],
                                trainedThrough=m['trainedThrough'], outOfSample=bool(m['trainedThrough']) and group > m['trainedThrough'],
                                locked=ho.locked(group), sleepRecordIds=ids, metrics=night_scores(y, predictions), scoredAt=scored_at,
                                run=ho.run_name(work)))
    rows = merge_weekly(out / 'weekly.jsonl', entries)
    summary = trial_summary(rows, args.side, live['id'], base['id'], np.random.default_rng(42))
    protocol = dict(scriptSha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), side=args.side, after=args.after,
        nights=list(groups), inputSha256=hashlib.sha256(features.read_bytes()).hexdigest(), sources=data['sources'],
        snapshotSha256=hashlib.sha256(args.snapshot.read_bytes()).hexdigest() if args.snapshot else None,
        models=models, holdout=dict(ho.protocol(), lockedGroups=locked, lockedUse='included: both models are frozen and deployed; each read logged as trial'),
        alignment='Watch 5-minute epochs from export-runtime-features.ts; each model\'s stage at the epoch midpoint; both scored on the same labelled epochs',
        outOfSample='a night counts only after the model\'s training cutoff', prospective=True, podWrites='none: GETs and the read-only model preview')
    (work / 'trial.json').write_text(json.dumps(dict(protocol=protocol, scored=entries, skipped=skipped, summary=summary,
        limitations=['One participant; Watch stages are the reference, not polysomnography.',
            'pod_live is what the pod shows on read today, not what it showed on the night.',
            'Bootstrap intervals and sign tests are descriptive over a handful of nights.',
            'No upload, selection or activation performed.']), indent=2))
    (work / 'requests.json').write_text(json.dumps(pod.requests, indent=2))
    for s in skipped:
        print(f"Skipped {s['night']}{' session ' + s['session'] if 'session' in s else ''}: {s['reason']}")
    print(f'Scored {len(entries) // 2} night groups; {out / "weekly.jsonl"} now holds {len(rows)} entries')
    print_trial(summary, models)


def cutoff_argument(value):
    model, _, night = value.partition('=')
    date.fromisoformat(night)
    return int(model), night


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('features', type=Path, nargs='?', help='features.json from export-runtime-features.ts')
    ap.add_argument('--snapshot', type=Path, help='The biometrics snapshot those features came from; with --trial, an archive snapshot (data/archive/<podId>/biometrics.db)')
    ap.add_argument('--out', type=Path, help='Report directory; with --trial, the trial directory holding weekly.jsonl (default data/runs/trial-id3)')
    ap.add_argument('--holdout-final', action='store_true', help='Refused: walk-forward refits and compares arms on the nights it scores')
    trial_args = ap.add_argument_group('weekly trial of deployed models')
    trial_args.add_argument('--trial', action='store_true', help='Score the pod\'s live stages and a library model\'s preview against the Watch on new nights')
    trial_args.add_argument('--run', type=Path, help='A run directory whose features.json lists the nights, instead of --snapshot')
    trial_args.add_argument('--pod', help='Pod IP/hostname; read-only')
    trial_args.add_argument('--side', choices=['left', 'right'], default='left')
    trial_args.add_argument('--timezone', help='IANA timezone for night groups with --snapshot; default from the pod')
    trial_args.add_argument('--after', default=TRIAL_AFTER, type=lambda v: date.fromisoformat(v).isoformat(), help='Score night groups after this date')
    trial_args.add_argument('--live-id', type=int, default=3, help='Library id of the selected model whose live stages are scored')
    trial_args.add_argument('--baseline-id', type=int, default=2, help='Library id of the model scored by preview')
    trial_args.add_argument('--baseline-model', type=Path, help='Local copy of the baseline model JSON (the library API lists models without their JSON)')
    trial_args.add_argument('--live-model', type=Path, help='Optional local copy of the live model; checked against the pod and used for its training cutoff')
    trial_args.add_argument('--trained-through', type=cutoff_argument, action='append', default=[], metavar='ID=YYYY-MM-DD',
                            help='Last night group a library model trained on, where neither its metadata nor its run records it')
    args = ap.parse_args()
    args.trained_through = dict(args.trained_through)
    if args.trial:
        return trial(ap, args)
    if args.holdout_final:
        ho.refuse_final(ap, 'every walk-forward test night refits and compares arms')
    if not (args.features and args.snapshot and args.out):
        ap.error('the backtest needs features, --snapshot and --out')
    data = json.loads(args.features.read_text())
    if data['sources']['snapshot'] != hashlib.sha256(args.snapshot.read_bytes()).hexdigest():
        raise ValueError('Snapshot does not match the one these features were exported from')
    args.out.mkdir(parents=True, exist_ok=True)
    if any(args.out.iterdir()):
        raise ValueError('Use an empty output directory; do not overwrite an evaluation or partial run')
    holdout = ho.record({n['group'] for n in data['nights']})
    nights, features, ruleset = ho.drop_locked(data['nights']), data['features'], data['ruleset']
    with sqlite3.connect(f'file:{args.snapshot.resolve()}?mode=ro', uri=True) as db:
        extras = [fa.augment(n, features, db, data['side']) for n in nights]
    rng = np.random.default_rng(42)
    protocol = dict(scriptSha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        ablationSha256=hashlib.sha256(Path(fa.__file__).read_bytes()).hexdigest(), models=list(MODELS),
        baseline=f'{POD}: stages the pod recorded live for each night (its selected model may have trained on some of these nights)',
        minTrainGroups=MIN_TRAIN_GROUPS, forest=fa.FOREST, order='chronological by noon-to-noon night group',
        postprocessing='production smoothing and transition rules', prospective=False, holdout=holdout,
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
