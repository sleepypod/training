"""Descriptive feature statistics only; never used to fit or select a model."""
import json
from pathlib import Path
import numpy as np
from progress import update


def summarize_features(path, seconds):
    data = json.loads(Path(path).read_text())
    rows = [r for night in data['nights'] for r in night['rows']]
    x = np.asarray([r['x'] for r in rows], dtype=float)
    if not rows or x.ndim != 2 or x.shape[1] != len(data['features']) or not np.isfinite(x).all():
        raise ValueError('Cannot train: feature matrix is empty, malformed, or non-finite')
    classes = {name: sum(r['y'] == i for r in rows) for i, name in enumerate(['wake', 'light', 'deep', 'rem'])}
    summary = {'rows': len(rows), 'labeled': sum(classes.values()), 'unlabeled': sum(r['y'] is None for r in rows),
               'features': x.shape[1], 'matrixBytes': x.nbytes, 'seconds': round(seconds, 3),
               'rowsPerSecond': round(len(rows) / max(seconds, 1e-9), 1), 'classes': classes,
               'columns': [{'name': name, 'min': float(x[:, i].min()), 'max': float(x[:, i].max()),
                            'mean': float(x[:, i].mean()), 'std': float(x[:, i].std())} for i, name in enumerate(data['features'])]}
    update('Features', f'{len(rows):,} epochs × {x.shape[1]} features · {summary["labeled"]:,} labeled · {summary["unlabeled"]:,} context-only · {summary["rowsPerSecond"]:,.0f} epochs/s', dataset=summary)
    return summary
