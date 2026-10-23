"""Import watch-motion-v1 as movement labels ONLY, never sleep-stage features.

Watch stages already use wrist acceleration: using these features to predict
Watch stages leaks the label. Thresholds below are fixed before evaluation.
"""
import argparse
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

import capture_common as cc

VERSION = 'watch-movement-labels-v1'
# Preregistered heuristic, not calibrated: fraction of samples with jerk >1 g/s.
MIN_COVERAGE = .8
MOVING_FRACTION = .05
VIGOROUS_FRACTION = .20


def parse(doc):
    if doc.get('version') != 'watch-motion-v1':
        raise ValueError('Unsupported Watch motion version')
    start = cc.number(doc.get('recordedFromMs'), 'recordedFromMs', 0, True)
    end = cc.number(doc.get('recordedToMs'), 'recordedToMs', 0, True)
    if end <= start:
        raise ValueError('Empty or reversed recording')
    zone = cc.zone(doc.get('timeZone'))
    offset = cc.number(doc.get('utcOffsetSeconds'), 'utcOffsetSeconds', integer=True)
    if datetime.fromtimestamp(start / 1000, zone).utcoffset().total_seconds() != offset:
        raise ValueError('UTC offset disagrees with recorded time zone')
    cc.text(doc.get('device'), 'device')
    for key, expected in [('sampleRateHz', 50), ('binSeconds', 2), ('jerkThresholdGPerSecond', 1)]:
        if cc.number(doc.get(key), key) != expected:
            raise ValueError(f'Unsupported {key}; thresholds preregistered for {expected}')
    columns = ('startMs', 'samples', 'meanMagnitude', 'magnitudeVariance', 'maxJerk', 'movingSamples')
    bins = doc.get('bins', {})
    if any(not isinstance(bins.get(k), list) for k in columns) or len({len(bins[k]) for k in columns}) != 1:
        raise ValueError('Missing or mismatched bin columns')
    previous = None
    for t, samples, magnitude, variance, jerk, moving in zip(*(bins[k] for k in columns)):
        cc.number(t, 'bin startMs', 0, True)
        cc.number(samples, 'samples', 1, True)
        cc.number(moving, 'movingSamples', 0, True)
        for key, value in [('meanMagnitude', magnitude), ('magnitudeVariance', variance), ('maxJerk', jerk)]:
            cc.number(value, key, 0)
        # Edge bins may begin before the requested interval; clip them when labeling.
        if t % 2000 or t + 2000 <= start or t >= end or previous is not None and t <= previous:
            raise ValueError('Bins must be unique, ordered, on the 2s Unix grid, and overlap capture')
        if samples > 110 or moving >= samples or moving > 0 and jerk <= 1 or moving == 0 and jerk > 1:
            raise ValueError('Inconsistent sample counts or jerk summary')
        previous = t
    if doc.get('raw') is not None:
        previous = None
        for row in doc['raw']:
            if not isinstance(row, list) or len(row) != 4:
                raise ValueError('Raw samples require [unixMs, x, y, z] in g')
            for value in row:
                cc.number(value, 'raw sample')
            if not start <= row[0] <= end or previous is not None and row[0] < previous:
                raise ValueError('Raw times outside capture or out of order')
            previous = row[0]
    return doc


def night_key(doc):
    # Noon-to-noon local night, so split exports from a night cannot enter different folds.
    local = datetime.fromtimestamp(doc['recordedFromMs'] / 1000, cc.zone(doc['timeZone']))
    return (local - timedelta(hours=12)).date().isoformat()


def labels(doc, clock=None):
    doc = parse(doc)
    clock = clock or {'synchronized': False, 'method': 'NTP wall time; physical-event check unavailable'}
    cc.check_capture_clock(clock, doc['recordedFromMs'], doc['recordedToMs'])
    bins = doc['bins']
    out = []
    for t, samples, moving in zip(bins['startMs'], bins['samples'], bins['movingSamples']):
        if samples < 100 * MIN_COVERAGE:
            continue  # Partial bins are unknown, not still.
        fraction = moving / samples
        intensity = 'vigorous' if fraction >= VIGOROUS_FRACTION else 'light' if fraction >= MOVING_FRACTION else 'still'
        a, b = max(t, doc['recordedFromMs']), min(t + 2000, doc['recordedToMs'])
        out.append({'startMs': cc.align(a, clock), 'endMs': cc.align(b, clock),
                    'moving': intensity != 'still', 'intensity': intensity, 'movingFraction': fraction})
    return {'version': VERSION, 'iosFormatRevision': cc.IOS_FORMAT_REVISION, 'night': night_key(doc), 'timeZone': doc['timeZone'],
            'clock': clock, 'labels': out, 'labelOrigin': 'watch-motion-heuristic',
            'thresholds': {'minimumCoverage': MIN_COVERAGE, 'movingFraction': MOVING_FRACTION,
                           'vigorousFraction': VIGOROUS_FRACTION},
            'warning': 'Movement labels only. Never use Watch motion as sleep-stage features: label leakage.'}


def label_window(start, end, rows):
    """Require contiguous full coverage of a pod-clock window; gaps stay unknown."""
    cursor, weighted, duration = start * 1000, 0.0, (end - start) * 1000
    for row in rows:
        a, b = row['startMs'], row['endMs']
        if b <= cursor:
            continue
        if a > cursor + .001:
            return None
        stop = min(end * 1000, b)
        weighted += (stop - cursor) * row['movingFraction']
        cursor = stop
        if cursor >= end * 1000 - .001:
            fraction = weighted / duration
            return 'vigorous' if fraction >= VIGOROUS_FRACTION else 'light' if fraction >= MOVING_FRACTION else 'still'
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('files', nargs='+')
    ap.add_argument('--alignment', help='Matched knock/turn pairs in capture-alignment-v1 JSON (one capture)')
    ap.add_argument('--out', required=True)
    args = ap.parse_args(argv)
    if args.alignment and len(args.files) != 1:
        ap.error('Use one capture per alignment file')
    clock = cc.load_alignment(json.loads(cc.data_path(args.alignment).read_text())) if args.alignment else None
    docs, seen = [], set()
    for filename in args.files:
        path = cc.data_path(filename)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        docs.append({**labels(json.loads(path.read_text()), clock), 'sourceSha256': digest})
    out = cc.data_path(args.out)
    if out.exists():
        raise ValueError('Output already exists')
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(docs, indent=2, allow_nan=False))
    print(f'Imported {len(docs)} captures; missing bins remain unlabeled.')


if __name__ == '__main__':
    main()
