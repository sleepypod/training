"""Retrospective decoder ablation: production smoothing versus HMM/Viterbi decoding.

Fixed forest configurations (no candidate selection) are fitted once per outer
held-out night group; every decoder scores the same fitted forest. Transition
matrices and decoder hyperparameters come from training nights only. This is
not a prospective validation and never exports or activates a model.
"""
import argparse
import hashlib
import importlib.util
import itertools
import json
from pathlib import Path
import sys

import numpy as np
from sklearn.model_selection import GroupKFold

sys.path.insert(0, str(Path(__file__).resolve().parent))
spec = importlib.util.spec_from_file_location('retraining', Path(__file__).with_name('retrain-models.py'))
rm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rm)
pm = rm.pm

MODELS = ('existing_depth8', 'balanced_leaf10')
DECODERS = ('production', 'argmax', 'viterbi', 'viterbi_no_deep_rem')
DEEP, REM = pm.STAGES.index('deep'), pm.STAGES.index('rem')
EPSILON = 1e-6
# Frozen before evaluation; chosen per outer fold by inner GroupKFold only. Large weights approach argmax.
GRID = [dict(alpha=a, weight=w) for a, w in itertools.product((0.5, 2.0), (0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0))]


def transitions(nights, alpha, forbid_deep_rem=False):
    """Row-normalized label transitions from consecutive labeled epochs only."""
    counts = np.full((4, 4), float(alpha))
    for night in nights:
        for a, b in zip(night['rows'], night['rows'][1:]):
            if a['y'] is not None and b['y'] is not None and b['start'] - a['start'] == pm.EPOCH:
                counts[a['y'], b['y']] += 1
    if forbid_deep_rem:
        counts[DEEP, REM] = counts[REM, DEEP] = 0
    matrix = counts / counts.sum(axis=1, keepdims=True)
    if forbid_deep_rem:
        matrix[DEEP, REM] = matrix[REM, DEEP] = EPSILON
        matrix /= matrix.sum(axis=1, keepdims=True)
    return matrix


def prior(nights, alpha):
    counts = np.full(4, float(alpha))
    for night in nights:
        for row in night['rows']:
            if row['y'] is not None:
                counts[row['y']] += 1
    return counts / counts.sum()


def viterbi(log_emissions, log_transitions, log_initial):
    """Most likely state path; log_emissions is (epochs, states)."""
    score = log_initial + log_emissions[0]
    back = np.zeros(log_emissions.shape, dtype=int)
    for t in range(1, len(log_emissions)):
        candidates = score[:, None] + log_transitions
        back[t] = candidates.argmax(axis=0)
        score = candidates.max(axis=0) + log_emissions[t]
    path = [int(score.argmax())]
    for t in range(len(log_emissions) - 1, 0, -1):
        path.append(int(back[t, path[-1]]))
    return path[::-1]


def probabilities(clf, night):
    """Four-column class probabilities, even when a fold lacks a class."""
    raw = clf.predict_proba(np.asarray([r['x'] for r in night['rows']]))
    full = np.zeros((len(raw), 4))
    full[:, [int(c) for c in clf.classes_]] = raw
    return full


def decode(name, clf, night, ruleset, params, train):
    if name == 'production':
        return rm.postprocess(clf.predict(np.asarray([r['x'] for r in night['rows']])), ruleset)
    proba = probabilities(clf, night)
    if name == 'argmax':
        return [int(c) for c in proba.argmax(axis=1)]
    # Both forests are fitted on class-balanced data (oversampling or balanced weights), so
    # proba is already a likelihood up to a constant; dividing by the label prior would
    # rebalance a second time. The label prior is used only for the first epoch.
    emissions = params['weight'] * np.log(proba + EPSILON)
    matrix = transitions(train, params['alpha'], forbid_deep_rem=name == 'viterbi_no_deep_rem')
    return viterbi(emissions, np.log(matrix), np.log(prior(train, params['alpha'])))


def score(name, clf, nights, ruleset, params, train):
    y, p = [], []
    for night in nights:
        for row, pred in zip(night['rows'], decode(name, clf, night, ruleset, params, train)):
            if row['y'] is not None:
                y.append(row['y'])
                p.append(pred)
    return y, p


def inner_fits(model, nights):
    """Inner GroupKFold forests on training nights, shared by every decoder."""
    groups = [n['group'] for n in nights]
    fitted = []
    for train, valid in GroupKFold(n_splits=min(3, len(set(groups)))).split(nights, groups=groups):
        train_nights, valid_nights = [nights[i] for i in train], [nights[i] for i in valid]
        assert {n['group'] for n in train_nights}.isdisjoint(n['group'] for n in valid_nights)
        fitted.append((rm.fit(model, train_nights), train_nights, valid_nights))
    return fitted


def choose(decoder, fitted, ruleset):
    """Decoder hyperparameters by inner validation: macro F1, then kappa."""
    if decoder in ('production', 'argmax'):
        return None
    best = None
    for params in GRID:
        truth, preds = [], []
        for clf, train_nights, valid_nights in fitted:
            y, p = score(decoder, clf, valid_nights, ruleset, params, train_nights)
            truth.extend(y)
            preds.extend(p)
        result = rm.metrics(truth, preds)
        key = (result['macroF1'], result['kappa'] or 0)
        if best is None or key > best[0]:
            best = (key, params)
    return best[1]


def cells(confusion):
    return {'lightAsRem': confusion[1][3], 'lightAsDeep': confusion[1][2], 'remAsLight': confusion[3][1], 'deepAsLight': confusion[2][1]}


def direct_deep_rem(decoded):
    return sum(1 for path in decoded for a, b in zip(path, path[1:]) if {a, b} == {DEEP, REM})


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
        retrainingSha256=hashlib.sha256(Path(rm.__file__).read_bytes()).hexdigest(),
        models=list(MODELS), decoders=list(DECODERS), grid=GRID, outer='leave-one-night-group-out',
        inner='3-fold GroupKFold for decoder hyperparameters only', objective='macro F1 across all four classes, then kappa',
        selection='none: fixed forest configurations; this is a decoder ablation, not candidate selection',
        gaps='decoded in row order across gaps with one transition step, as production postprocessing does; transitions counted only between consecutive labeled epochs',
        emissions='weight × log predict_proba; no prior division because both forests train on class-balanced data',
        prospective=False, inputSha256=hashlib.sha256(args.features.read_bytes()).hexdigest(), sources=data['sources'])
    (args.out / 'protocol.json').write_text(json.dumps(protocol, indent=2))
    outputs = {(m, d): ([], []) for m in MODELS for d in DECODERS}
    night_scores = {(m, d): [] for m in MODELS for d in DECODERS}
    jumps = {(m, d): 0 for m in MODELS for d in DECODERS}
    folds = []
    for group in groups:
        train = [n for n in nights if n['group'] != group]
        held = [n for n in nights if n['group'] == group]
        fold = dict(group=group, params={}, scores={})
        for model in MODELS:
            clf = rm.fit(model, train)
            inner = inner_fits(model, train)
            for decoder in DECODERS:
                params = choose(decoder, inner, ruleset)
                y, p = score(decoder, clf, held, ruleset, params, train)
                outputs[model, decoder][0].extend(y)
                outputs[model, decoder][1].extend(p)
                result = rm.metrics(y, p)
                night_scores[model, decoder].append(result['macroF1'])
                jumps[model, decoder] += direct_deep_rem([decode(decoder, clf, n, ruleset, params, train) for n in held])
                fold['params'][f'{model}/{decoder}'] = params
                fold['scores'][f'{model}/{decoder}'] = result
        folds.append(fold)
        print(f'Held-out group {len(folds)}/{len(groups)} complete', flush=True)
    rng = np.random.default_rng(42)
    results = {}
    for (model, decoder), (y, p) in outputs.items():
        result = rm.metrics(y, p)
        differences = np.array(night_scores[model, decoder]) - np.array(night_scores[model, 'production'])
        means = rng.choice(differences, size=(2000, len(differences)), replace=True).mean(axis=1)
        results[f'{model}/{decoder}'] = dict(**result, cells=cells(result['confusion']), directDeepRemTransitions=jumps[model, decoder],
            versusProduction={'meanNightMacroF1Difference': float(differences.mean()),
                'nightBootstrap95Percent': np.quantile(means, [.025, .975]).tolist()})
    chosen = {key: {json.dumps(p, sort_keys=True): sum(1 for f in folds if f['params'][key] == p)
                    for p in GRID if any(f['params'][key] == p for f in folds)}
              for key in folds[0]['params'] if folds[0]['params'][key] is not None}
    report = dict(protocol=protocol, groups=len(groups), sessions=len(nights), folds=folds, scores=results, selectedParams=chosen,
        limitations=['Same-person retrospective reuse of already-inspected nights; not prospective.',
            'Watch labels are the reference, not polysomnography.',
            'Night bootstrap intervals are descriptive: overlapping training sets make them approximate.',
            'No export, upload or activation performed.'])
    (args.out / 'report.json').write_text(json.dumps(report, indent=2))
    for key, r in results.items():
        v = r['versusProduction']
        print(f"{key:36} F1 {r['macroF1']:.3f} κ {r['kappa'] or 0:.3f} Δ {v['meanNightMacroF1Difference']:+.3f} "
              f"[{v['nightBootstrap95Percent'][0]:+.3f}, {v['nightBootstrap95Percent'][1]:+.3f}] {r['cells']}", flush=True)


if __name__ == '__main__':
    main()
