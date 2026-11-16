"""The pod movement model as a portable JSON forest (`movement-model-v1`, contract `movementRaw-v1`).

Trees use the parallel arrays of core's stage models (src/lib/personal-stage-model.ts):
node i splits on f[i] at t[i] (f[i] = -1 is a leaf), x <= t goes to l[i], otherwise r[i].
Two additions make the forest reproduce scikit-learn exactly: inputs are rounded to
float32 before every comparison (sklearn's tree dtype), and m[i] = 1 sends a missing
input (null, NaN) to the left child, otherwise right. Leaves carry class votes in
`classes` order and internal nodes an empty list. The export is refused unless this
evaluator reproduces sklearn's predict_proba on every training window, as given and
with the pod score missing. See knowledge/data/movement-raw-v1.md.
"""
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from canonical_json import canonical

FORMAT = 'movement-model-v1'
CONTRACT = 'movementRaw-v1'
TOLERANCE = 1e-9
MAX_VECTORS = 64
STILL = 'still'


def body_sha256(doc):
    """SHA-256 of the canonical document without its own `sha256` field."""
    return hashlib.sha256(canonical({k: v for k, v in doc.items() if k != 'sha256'}).encode()).hexdigest()


def trees(clf):
    out = []
    for est in clf.estimators_:
        t = est.tree_
        leaf = t.children_left < 0
        out.append({'f': [-1 if is_leaf else int(f) for f, is_leaf in zip(t.feature, leaf)],
                    't': [0.0 if is_leaf else float(x) for x, is_leaf in zip(t.threshold, leaf)],
                    'l': [int(x) for x in t.children_left], 'r': [int(x) for x in t.children_right],
                    'm': [0 if is_leaf else int(x) for x, is_leaf in zip(t.missing_go_to_left, leaf)],
                    'v': [[float(p) for p in t.value[i][0]] if is_leaf else [] for i, is_leaf in enumerate(leaf)]})
    return out


def evaluate(doc, inputs):
    """One window by column name in (None for missing), class probabilities out: what a pod computes."""
    x = [math.nan if inputs[c] is None else float(np.float32(inputs[c])) for c in doc['featureContract']]
    total = [0.0] * len(doc['classes'])
    for tree in doc['trees']:
        node = 0
        while tree['f'][node] >= 0:
            value = x[tree['f'][node]]
            left = tree['m'][node] == 1 if math.isnan(value) else value <= tree['t'][node]
            node = tree['l'][node] if left else tree['r'][node]
        votes = tree['v'][node]
        s = sum(votes)
        for c in range(len(total)):
            total[c] += votes[c] / s if s > 0 else 1 / len(total)
    return [p / len(doc['trees']) for p in total]


def evaluate_many(doc, x):
    """`evaluate` for a whole matrix at once (NaN for missing), for the parity gate over every training window."""
    x = np.asarray(x, float).astype(np.float32).astype(float)
    k = len(doc['classes'])
    total = np.zeros((len(x), k))
    rows = np.arange(len(x))
    for tree in doc['trees']:
        f, t, left, right = (np.asarray(tree[a]) for a in 'ftlr')
        missing_left = np.asarray(tree['m']) == 1
        votes = np.asarray([v if v else [0.0] * k for v in tree['v']], float)
        sums = votes.sum(axis=1, keepdims=True)
        proba = np.divide(votes, sums, out=np.full_like(votes, 1 / k), where=sums > 0)
        node = np.zeros(len(x), int)
        while True:
            inner = f[node] >= 0
            if not inner.any():
                break
            n = node[inner]
            value = x[rows[inner], f[n]]
            go_left = np.where(np.isnan(value), missing_left[n], value <= t[n])
            node[inner] = np.where(go_left, left[n], right[n])
        total += proba[node]
    return total / len(doc['trees'])


def moving(doc, probabilities):
    """P(moving) = 1 - P(still); a window is moving only when it is strictly above strictThreshold."""
    p = 1 - probabilities[doc['classes'].index(STILL)]
    return p, p > doc['strictThreshold']


def without_pod_score(x, columns):
    out = np.array(x, float)
    out[:, columns.index('podScore')] = np.nan
    return out


def pick_vectors(labels, p_moving, predicted, strict, threshold, classes, limit=MAX_VECTORS):
    """(window index, pod score missing) pairs: both sides of each threshold, every label and predicted class,
    missing pod scores, then evenly spread windows. None lies within TOLERANCE of the strict threshold."""
    chosen = []

    def add(i, missing=False):
        if len(chosen) < limit and (int(i), missing) not in chosen:
            chosen.append((int(i), missing))

    for cut, per_side in ((strict, 8), (threshold, 4)):
        gap = p_moving - cut
        above, below = np.flatnonzero(gap > TOLERANCE), np.flatnonzero(gap < -TOLERANCE)
        for i in above[np.argsort(gap[above], kind='stable')][:per_side]:
            add(i)
        for i in below[np.argsort(-gap[below], kind='stable')][:per_side]:
            add(i)
    for group in (labels, predicted):
        for c in classes:
            members = np.flatnonzero(group == c)
            for i in members[np.linspace(0, len(members) - 1, min(3, len(members)), dtype=int)] if len(members) else []:
                add(i)
    for i, _ in list(chosen[:6]) + list(chosen[24:30]):
        add(i, True)
    for i in np.linspace(0, len(labels) - 1, limit, dtype=int):
        add(i)
    return chosen


def export(fitted, x, labels, meta, out):
    """Write the portable forest to `out`, or refuse when it differs from sklearn on any training window."""
    clf = fitted['model']
    classes = [str(c) for c in clf.classes_]
    columns = list(meta['columns'])
    if x.shape[1] != len(columns) or clf.n_features_in_ != len(columns):
        raise SystemExit('Training windows do not match the model\'s feature columns')
    doc = dict(formatVersion=FORMAT, version=f'{CONTRACT}-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}', kind='forest',
               contract=CONTRACT, featureContract=columns, classes=classes, movingProbability=f'1 - P({STILL})',
               strictThreshold=float(fitted['strictThreshold']), threshold=float(fitted['threshold']),
               thresholdRule='moving when P(moving) > strictThreshold (strictly greater); `threshold` is the balanced '
                             'cutoff (P(moving) >= threshold) reported for evaluation only',
               windowSeconds=meta['windowSeconds'], hopSeconds=meta['hopSeconds'], sampleRateHz=meta['sampleRateHz'],
               epochSeconds=meta['epochSeconds'], epochFeatures=list(meta['epochFeatures']),
               missingInputs=['podScore'], float32Inputs=True, hyperparameters=meta['hyperparameters'],
               training=meta['training'], trainedAt=datetime.now(timezone.utc).isoformat(), source=meta['source'],
               trees=trees(clf))
    plain, missing = x, without_pod_score(x, columns)
    expected = {False: clf.predict_proba(plain), True: clf.predict_proba(missing)}
    p_moving = 1 - expected[False][:, classes.index(STILL)]
    vectors = []
    for i, gone in pick_vectors(labels, p_moving, np.asarray(classes)[expected[False].argmax(axis=1)],
                                doc['strictThreshold'], doc['threshold'], classes):
        p = [float(v) for v in expected[gone][i]]
        if abs(1 - p[classes.index(STILL)] - doc['strictThreshold']) <= TOLERANCE:
            continue
        row = missing[i] if gone else plain[i]
        vectors.append({'inputs': {c: None if math.isnan(v) else float(v) for c, v in zip(columns, row)},
                        'probabilities': p, 'moving': bool(1 - p[classes.index(STILL)] > doc['strictThreshold'])})
    doc['testVectors'] = vectors
    doc['sha256'] = body_sha256(doc)
    text = json.dumps(doc, separators=(',', ':'), allow_nan=False)
    portable = json.loads(text)
    parity = {}
    for gone, matrix in ((False, plain), (True, missing)):
        diff = np.abs(evaluate_many(portable, matrix) - expected[gone]).max()
        parity['podScoreMissing' if gone else 'asTrained'] = float(diff)
        if not diff <= TOLERANCE:
            raise SystemExit(f'Portable forest differs from sklearn by {diff:.3g} (pod score {"missing" if gone else "as trained"}); refusing export')
    for i, vector in enumerate(portable['testVectors']):
        p = evaluate(portable, vector['inputs'])
        if max(abs(a - b) for a, b in zip(p, vector['probabilities'])) > TOLERANCE or moving(portable, p)[1] != vector['moving']:
            raise SystemExit(f'Test vector {i} does not reproduce; refusing export')
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    return dict(path=str(out), bytes=len(text.encode()), sha256=doc['sha256'], version=doc['version'], trees=len(doc['trees']),
                nodes=sum(len(t['f']) for t in doc['trees']), testVectors=len(vectors), parityWindows=int(len(plain)),
                maxAbsDifference=parity)
