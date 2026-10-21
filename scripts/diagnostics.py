"""Descriptive diagnostics, separate from model fitting and selection."""
import numpy as np
from scipy.stats import ks_2samp, skew

STAGES = ['wake', 'light', 'deep', 'rem']


def describe(data):
    nights = data['nights']
    rows = [r for n in nights for r in n['rows']]
    x = np.asarray([r['x'] for r in rows], dtype=float)
    if not len(rows) or x.ndim != 2 or x.shape[1] != len(data['features']):
        return {'error': 'Feature matrix is empty or malformed'}
    groups = np.asarray([n.get('group', str(i)) for i, n in enumerate(nights) for r in n['rows']])
    columns = []
    for i, name in enumerate(data['features']):
        values = x[:, i]; finite = values[np.isfinite(values)]
        counts, edges = np.histogram(finite, bins=12) if len(finite) else ([], [])
        drift = []
        for group in sorted(set(groups)):
            a, b = values[(groups == group) & np.isfinite(values)], values[(groups != group) & np.isfinite(values)]
            if len(a) and len(b):
                test = ks_2samp(a, b, method='auto')
                drift.append({'group': group, 'statistic': float(test.statistic), 'pValue': float(test.pvalue) if np.isfinite(test.pvalue) else None})
        columns.append({'name': name, 'missing': int((~np.isfinite(values)).sum()),
                        'min': float(finite.min()) if len(finite) else None, 'max': float(finite.max()) if len(finite) else None,
                        'mean': float(finite.mean()) if len(finite) else None, 'std': float(finite.std()) if len(finite) else None,
                        'skew': float(skew(finite)) if len(finite) > 2 and np.ptp(finite) > 0 else None,
                        'histogram': {'counts': list(map(int, counts)), 'edges': list(map(float, edges))}, 'drift': drift})
    sessions = []
    for i, n in enumerate(nights):
        classes = {stage: sum(r['y'] == s for r in n['rows']) for s, stage in enumerate(STAGES)}
        sessions.append({'id': str(n.get('id', i)), 'group': n.get('group', str(i)), 'rows': len(n['rows']),
                         'labeled': sum(classes.values()), 'classes': classes,
                         'gaps': sum(b.get('start', 0) - a.get('start', 0) > 300 for a, b in zip(n['rows'], n['rows'][1:]))})
    return {'columns': columns, 'sessions': sessions, 'rows': len(rows), 'timeZone': data.get('timeZone'),
            'side': data.get('side'), 'features': len(columns), 'matrixBytes': x.nbytes,
            'nonfinite': int((~np.isfinite(x)).sum()), 'labeled': sum(n['labeled'] for n in sessions),
            'driftNote': 'Descriptive two-sample KS comparisons: each night versus all other nights. Adjacent epochs are correlated and many comparisons are made; p-values are unadjusted, not a pass/fail test.'}
