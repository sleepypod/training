"""Preregistered milestone-2 exit test, overnight-capture-implementation-plan.md §4.

One score: 3s log power in the lower passing band on the occupant's primary
channel, minus the night's median occupied pump-off log power. No fitting.
Merged event peak confidences cannot stand in for detector-window confidences.
"""
import numpy as np
from sklearn.metrics import roc_auc_score

PROTOCOL = {'version': 'snore-exit-v1', 'windowSeconds': 3, 'boutGapSeconds': 30,
            'negativeRadiusSeconds': 1800, 'exclusionGuardSeconds': 10,
            'minimumNights': 5, 'minimumBoutNights': 3, 'minimumBoutsPerNight': 5,
            'passAuc': .7, 'passLower': .5, 'failUpper': .7,
            'bootstrapUnit': 'night', 'bootstrapReplicates': 2000, 'seed': 42}


def bouts(windows):
    """Adjacent same-class windows <30s apart become one unit, with a median score."""
    groups = []
    for w in sorted(windows, key=lambda w: w['startMs']):
        if not groups or w['startMs'] - groups[-1][-1]['startMs'] >= 30_000:
            groups.append([])
        groups[-1].append(w)
    return [float(np.median([w['score'] for w in group])) for group in groups]


def night_units(night):
    if not night.get('calibrationId') or not night.get('windowConfidencesAvailable'):
        return None, 'Missing calibrated per-window confidences; event peaks cannot establish guard-band negatives'
    if night.get('occupants') != 1 or not night.get('synchronized'):
        return None, 'Requires a synchronized single-occupant night'
    threshold = night['threshold']
    if not 0 < threshold <= 1:
        raise ValueError('Invalid calibrated threshold')
    windows = night['windows']
    eligible = [w for w in windows if w['covered'] and w['occupied'] and not w['excluded'] and w['score'] is not None]
    positives = [w for w in eligible if w['confidence'] >= threshold]
    negatives = [w for w in eligible if w['confidence'] < threshold / 2
                 and any(abs(w['startMs'] - p['startMs']) <= 1_800_000 for p in positives)]
    positive, negative = bouts(positives), bouts(negatives)
    if not positive or not negative:
        return None, 'No eligible positive and guard-band negative bouts'
    return (positive, negative), None


def auc(units):
    positives = [s for p, _ in units for s in p]
    negatives = [s for _, n in units for s in n]
    return float(roc_auc_score([1] * len(positives) + [0] * len(negatives), positives + negatives))


def verdict(value, lower, upper):
    if value >= .7 and lower > .5:
        return 'pass'
    if upper < .7:
        return 'fail'
    return 'inconclusive'


def evaluate(nights):
    rows, units, seen = [], [], set()
    contracts = {(n.get('calibrationId'), n.get('threshold'), tuple(n.get('bandHz', [])))
                 for n in nights if n.get('windowConfidencesAvailable')}
    if len(contracts) > 1:
        raise ValueError('Nights must use the same preregistered calibration, threshold and score band')
    for night in nights:
        if night['night'] in seen:
            raise ValueError('Duplicate night: combine captures before evaluation')
        seen.add(night['night'])
        unit, reason = night_units(night)
        rows.append({'night': night['night'], 'reason': reason, 'bouts': len(unit[0]) if unit else 0,
                     'negativeBouts': len(unit[1]) if unit else 0, 'auc': auc([unit]) if unit else None})
        if unit:
            units.append(unit)
    result = {'protocol': PROTOCOL, 'result': 'inconclusive', 'perNight': rows, 'auc': None, 'interval95': None}
    if len(units) < 5 or sum(len(p) >= 5 for p, _ in units) < 3:
        result['reason'] = 'Need ≥5 eligible nights, ≥3 with ≥5 positive bouts each'
        return result
    rng = np.random.default_rng(PROTOCOL['seed'])
    estimates = [auc([units[i] for i in rng.integers(0, len(units), len(units))])
                 for _ in range(PROTOCOL['bootstrapReplicates'])]
    lower, upper = np.percentile(estimates, [2.5, 97.5]).tolist()
    score = auc(units)
    result.update(auc=score, interval95=[lower, upper], result=verdict(score, lower, upper))
    return result
