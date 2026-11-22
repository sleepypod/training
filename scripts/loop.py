"""Freeze-then-score training loop (docs/plans/training-loop.md).

  replay FEATURES --out DIR [--cadence DAYS ...] [--frozen LABEL=MODEL_DIR ...]
      Simulate the loop on past nights. For each cadence, refit with the fixed nested
      protocol of retrain-models.py every DAYS days on the night groups before the refit
      date, and score each later night group with the newest refit. Frozen models are
      scored on the night groups after their training cutoff. Every past night has
      already shaped earlier decisions, so this compares training mechanisms, not
      untouched models.
  freeze MODEL_DIR [--label LABEL]
      Register a trained candidate (candidate.json and candidate.joblib) before the
      nights it will be scored on.
  score FEATURES
      Score every registered model on development night groups after its training
      cutoff. A night is prospective for a model when every session in it began after
      the model was trained.

Comparisons are paired per night group with a night-level bootstrap and a sign test.
Locked holdout nights (holdout.py) are dropped first. Local only: never contacts a pod,
uploads or activates a model.
"""
import argparse
from datetime import date, datetime, timedelta, timezone
import hashlib
import importlib.util
import itertools
import json
from pathlib import Path
import sys

import joblib
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import holdout as ho
from canonical_json import stage_sha256
from run_history import primary_root, register_output

spec = importlib.util.spec_from_file_location('stage_backtest', Path(__file__).with_name('stage-backtest.py'))
sb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sb)
rm = sb.rm

MIN_TRAIN_GROUPS = sb.MIN_TRAIN_GROUPS
RESAMPLES = 10_000
METRICS = ('macroF1', 'kappa', 'wakeF1')


def development(data):
    """Development sessions and their sorted night groups; locked nights never enter the loop."""
    nights = ho.drop_locked(data['nights'])
    return nights, sorted({n['group'] for n in nights})


def by_group(nights):
    groups = {}
    for night in nights:
        groups.setdefault(night['group'], []).append(night)
    return groups


def refits(groups, cadence, min_train=None):
    """(refit date, test groups) pairs: a refit sees groups before its date and scores groups until the next one."""
    min_train = MIN_TRAIN_GROUPS if min_train is None else min_train
    if len(groups) <= min_train:
        raise ValueError(f'Need more than {min_train} development night groups')
    day = date.fromisoformat(groups[min_train])
    last = date.fromisoformat(groups[-1])
    plan = []
    while day <= last:
        until = day + timedelta(days=cadence)
        tests = [g for g in groups if day.isoformat() <= g < until.isoformat()]
        if tests:
            plan.append((day.isoformat(), tests))
        day = until
    return plan


def columns(model, features):
    """Indexes of a model's feature contract in a features file; refuses a missing column."""
    missing = [c for c in model['featureContract'] if c not in features]
    if missing:
        raise ValueError(f"{model['version']} needs features this file lacks: {', '.join(missing)}")
    return [features.index(c) for c in model['featureContract']]


def subset(nights, index):
    return [{**n, 'rows': [{**r, 'x': [r['x'][i] for i in index]} for r in n['rows']]} for n in nights]


def load_frozen(model_dir):
    """A trained candidate: its JSON, fitted model, training cutoff and SHA-256 as the pod computes it."""
    model_dir = Path(model_dir)
    path = model_dir / 'candidate.json'
    model = json.loads(path.read_text())
    cutoff, source = sb.training_cutoff(model, path)
    if not cutoff:
        raise ValueError(f'{model_dir}: no training cutoff recorded, so no night is out of sample for it')
    return dict(model=model, clf=joblib.load(model_dir / 'candidate.joblib'), trainedThrough=cutoff, cutoffSource=source,
                sha256=stage_sha256(model), version=model['version'], trainedAt=model.get('trainedAt'))


def score_frozen(frozen, groups, data):
    """Per night group scores of a frozen model on the groups after its cutoff."""
    index = columns(frozen['model'], data['features'])
    return {g: sb.night_scores(*rm.predict(frozen['clf'], subset(nights, index), data['ruleset']))
            for g, nights in groups.items() if g > frozen['trainedThrough']}


def compare_arms(scores, rng, keep=lambda a, b, group: True):
    """Paired comparisons of every two arms on the night groups both scored (and `keep` accepts)."""
    result = {}
    for a, b in itertools.combinations(scores, 2):
        common = [g for g in sorted(set(scores[a]) & set(scores[b])) if keep(a, b, g)]
        paired = [dict(a=scores[a][g], b=scores[b][g]) for g in common]
        result[f'{a} vs {b}'] = dict(nights=common, **{m: sb.compare(paired, 'a', 'b', m, rng, RESAMPLES) for m in METRICS})
    return result


def replay(args):
    data = json.loads(args.features.read_text())
    nights, groups = development(data)
    grouped = by_group(nights)
    ruleset = data['ruleset']
    args.out.mkdir(parents=True, exist_ok=True)
    if any(args.out.iterdir()):
        raise ValueError('Use an empty output directory; do not overwrite an evaluation')
    scores, arms = {}, {}
    for cadence in args.cadence:
        name = f'refit-{cadence}d'
        scores[name], arms[name] = {}, []
        for refit_date, tests in refits(groups, cadence):
            train = [n for n in nights if n['group'] < refit_date]
            selected, _ = rm.inner_choice(train, ruleset)
            clf = rm.fit(selected, train)
            for g in tests:
                scores[name][g] = sb.night_scores(*rm.predict(clf, grouped[g], ruleset))
            arms[name].append(dict(refit=refit_date, trainedThrough=max(n['group'] for n in train), selected=selected, tests=tests))
            print(f"{name}: refit {refit_date} on {len({n['group'] for n in train})} groups, {selected}, scored {', '.join(tests)}", flush=True)
    for label, model_dir in args.frozen:
        frozen = load_frozen(model_dir)
        scores[label] = score_frozen(frozen, grouped, data)
        arms[label] = dict(path=ho.run_name(model_dir), version=frozen['version'], sha256=frozen['sha256'],
                           trainedThrough=frozen['trainedThrough'], cutoffSource=frozen['cutoffSource'])
    report = dict(protocol=dict(scriptSha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                                inputSha256=hashlib.sha256(args.features.read_bytes()).hexdigest(), sources=data['sources'],
                                candidates=list(rm.CANDIDATES), selection='3-fold GroupKFold on training groups, macro F1 then kappa',
                                minTrainGroups=MIN_TRAIN_GROUPS, cadences=args.cadence, holdout=ho.record({n['group'] for n in data['nights']}),
                                resamples=RESAMPLES, seed=42, prospective=False),
                  arms=arms, nights=scores, comparisons=compare_arms(scores, np.random.default_rng(42)))
    (args.out / 'report.json').write_text(json.dumps(report, indent=2))
    register_output(args.out)
    print_report(report, 'Retrospective replay on nights that shaped earlier decisions; Watch stages are the reference; one participant.')


def print_report(report, note):
    scores = report['nights']
    arms = list(scores)
    print(f"\n{'night':11}" + ''.join(f'{a:>16}' for a in arms) + '   (macro F1)')
    for g in sorted({g for s in scores.values() for g in s}):
        print(f'{g:11}' + ''.join(f"{scores[a][g]['macroF1']:16.3f}" if g in scores[a] else f"{'':>16}" for a in arms))
    for pair, c in report['comparisons'].items():
        for metric in METRICS:
            r = c[metric]
            if r:
                print(f"{pair} {metric}: {r['meanDifference']:+.3f}, 95% {r['bootstrap95Percent'][0]:+.3f} to {r['bootstrap95Percent'][1]:+.3f}; "
                      f"better {r['better']}, worse {r['worse']} of {r['nights']} nights; sign test p={r['signTestP']:.3f}")
    print(note)


def registry(path=None):
    return Path(path) if path else primary_root() / 'data/runs/loop/frozen.jsonl'


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def freeze(args):
    frozen = load_frozen(args.model_dir)
    path = registry(args.registry)
    entries = read_jsonl(path)
    label = args.label or frozen['version']
    if any(e['sha256'] == frozen['sha256'] or e['label'] == label for e in entries):
        raise ValueError(f'{label} ({frozen["sha256"][:12]}) is already registered')
    entry = dict(label=label, path=ho.run_name(args.model_dir), version=frozen['version'], sha256=frozen['sha256'],
                 trainedThrough=frozen['trainedThrough'], cutoffSource=frozen['cutoffSource'], trainedAt=frozen['trainedAt'],
                 frozenAt=datetime.now(timezone.utc).isoformat(timespec='seconds'))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as stream:
        stream.write(json.dumps(entry, sort_keys=True) + '\n')
    print(f"Frozen {label}: trained through {entry['trainedThrough']} ({entry['cutoffSource']}), sha256 {entry['sha256'][:12]}")


def prospective(nights, trained_at):
    """True when every session of a night group began after the model was trained."""
    if not trained_at:
        return False
    after = datetime.fromisoformat(trained_at.replace('Z', '+00:00')).timestamp()
    return all(n['start'] > after for n in nights)


def score(args):
    data = json.loads(args.features.read_text())
    nights, _ = development(data)
    grouped = by_group(nights)
    entries = read_jsonl(registry(args.registry))
    if not entries:
        raise ValueError('No model is registered; run freeze first')
    scores, status = {}, {}
    for e in entries:
        frozen = load_frozen(primary_root() / e['path'] if not Path(e['path']).is_absolute() else e['path'])
        if frozen['sha256'] != e['sha256']:
            raise ValueError(f"{e['label']} changed on disk since it was frozen")
        scores[e['label']] = score_frozen(frozen, grouped, data)
        status[e['label']] = {g: prospective(grouped[g], e['trainedAt']) for g in scores[e['label']]}
    report = dict(models=entries, nights=scores, prospective=status,
                  comparisons=compare_arms(scores, np.random.default_rng(42)),
                  prospectiveComparisons=compare_arms(scores, np.random.default_rng(42), lambda a, b, g: status[a][g] and status[b][g]),
                  inputSha256=hashlib.sha256(args.features.read_bytes()).hexdigest(), scoredAt=datetime.now(timezone.utc).isoformat(timespec='seconds'))
    out = registry(args.registry).with_name('scores.json')
    out.write_text(json.dumps(report, indent=2))
    print_report(dict(nights=scores, comparisons=report['comparisons']), 'Out of sample after each training cutoff; Watch stages are the reference; one participant.')
    for pair, c in report['prospectiveComparisons'].items():
        print(f"{pair}: {len(c['nights'])} night groups prospective for both")
    print(f'Scores: {out}')


def frozen_arg(value):
    label, _, path = value.partition('=')
    if not label or not path:
        raise argparse.ArgumentTypeError('use LABEL=MODEL_DIR')
    return label, Path(path)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='command', required=True)
    p = sub.add_parser('replay')
    p.add_argument('features', type=Path)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--cadence', type=int, action='append', help='Days between refits; repeatable (default 1 and 7)')
    p.add_argument('--frozen', type=frozen_arg, action='append', default=[], help='LABEL=MODEL_DIR holding candidate.json and candidate.joblib')
    p = sub.add_parser('freeze')
    p.add_argument('model_dir', type=Path)
    p.add_argument('--label')
    p.add_argument('--registry', type=Path)
    p = sub.add_parser('score')
    p.add_argument('features', type=Path)
    p.add_argument('--registry', type=Path)
    args = ap.parse_args()
    if args.command == 'replay':
        args.cadence = args.cadence or [1, 7]
        if any(c < 1 for c in args.cadence):
            ap.error('--cadence must be at least 1 day')
        replay(args)
    elif args.command == 'freeze':
        freeze(args)
    else:
        score(args)


if __name__ == '__main__':
    main()
