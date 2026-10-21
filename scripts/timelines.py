"""Private epoch-aligned stage traces; missing labels/predictions remain gaps."""
import json
from pathlib import Path
import numpy as np


def initial(data):
    return {'version': 2, 'features': data.get('features', []), 'timeZone': data.get('timeZone', 'UTC'), 'epochSeconds': 300,
            'podSource': data.get('podStageSource', 'replayed-rules'),
            'sessions': [{'id': str(n['id']), 'group': n['group'], 'start': n['start'], 'end': n['end'],
                          'heldOutModel': None, 'finalModel': None,
                          'rows': [{'start': r['start'], 'end': min(r['start'] + 300, n['end']),
                                    'watch': r['y'], 'pod': r.get('pod') if data.get('podStageSource') == 'pod-api' else r['rule'],
                                    'heldOut': None, 'final': None, 'baseline': None, 'x': r['x']} for r in n['rows']]} for n in data['nights']]}


def add_predictions(document, nights, clf, model_name, mode, ruleset, postprocess):
    if mode not in ('heldOut', 'final', 'baseline'):
        raise ValueError('Unknown timeline prediction mode')
    by_id = {n['id']: n for n in document['sessions']}
    for night in nights:
        target = by_id[str(night['id'])]
        if [r['start'] for r in night['rows']] != [r['start'] for r in target['rows']]:
            raise ValueError('Timeline epoch alignment differs from training input')
        predictions = postprocess(clf.predict(np.asarray([r['x'] for r in night['rows']])), ruleset)
        if len(predictions) != len(target['rows']):
            raise ValueError('Prediction count differs from timeline')
        for row, prediction in zip(target['rows'], predictions):
            row[mode] = int(prediction)
        target[mode + 'Model'] = model_name


def save(document, output):
    path = Path(output) / 'timelines.json'
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(document, allow_nan=False, separators=(',', ':')))
    temp.replace(path)
