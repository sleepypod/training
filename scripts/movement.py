"""Movement classification from raw piezo and capacitance, compared with the pod's movement score.

  session  Spoken-cue movement session: the computer says each movement aloud
           and records when it asked for it. Those cue times are the labels.
  train    Build labeled windows from one or more sessions and compare a
           classifier with the pod's per-minute movement score.

Retrospective and exploratory. Read-only toward the pod: GET /api/settings for a
clock check, GET /api/export/archive for raw data and GET /api/biometrics/movement
for the baseline. Sessions, exports and reports stay under ignored data/.
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import confusion_matrix, f1_score
from sklearn.model_selection import GroupKFold

import piezo_bandwidth as pb
import raw_piezo as rp
from fetch_pod import base_url
from run_history import register_output

FS = 500
CLASSES = ('still', 'arm', 'leg', 'turn', 'sit')
MOVING = CLASSES[1:]
# Seconds after a cue that count as the movement: reaction time plus the movement itself.
EVENT_SECONDS = {'arm': 4.0, 'leg': 4.0, 'turn': 7.0, 'sit': 9.0}
WINDOW_S, HOP_S = 2.0, 1.0
REACTION_S = 1.0  # a cue takes about a second to say; movement starts after it
GUARD_S = 3.0  # still windows must be at least this far from any movement window
# Finished pod frames reach the export up to about 45 minutes late; an hour past the session covers it.
ARCHIVE_DELAY_S = 3600
BANDS = ((0.5, 4), (4, 10), (10, 30), (30, 100), (100, 245))
SENSING = slice(0, 6)  # capSense2 values 0-5 sense; 6-7 are the reference pair

# (class, cue text, cues, seconds between cues). Still blocks have no cues.
PROTOCOL = (
    ('still', 'Lie still on your side of the bed.', 1, 60),
    ('arm', 'Move one arm, then rest.', 5, 12),
    ('leg', 'Move one leg, then rest.', 5, 12),
    ('turn', 'Turn over.', 6, 15),
    ('sit', 'Sit up, then lie back down.', 3, 20),
    ('still', 'Lie still again.', 1, 60),
)


def now_ms():
    return round(time.time() * 1000)


def speak(text, cue):
    """Start speaking without blocking, so the recorded cue time is when speech began."""
    if cue == 'say' and shutil.which('say'):
        subprocess.Popen(['say', text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        print(f'\a  ▶ {text}', flush=True)


def run_session(side, pod, cue, sleep=time.sleep, clock=now_ms, say=speak):
    """Walk the protocol and return the session document. Every cue time is recorded before speaking."""
    events, blocks = [], []
    offset = pb.optional_offset(pod)
    say('Starting the movement session. Lie on your side of the bed.', cue)
    sleep(5)
    for cls, text, count, every in PROTOCOL:
        start = clock()
        if cls == 'still':
            say(text, cue)
            sleep(every)
        else:
            say(f'{cls}: {text} Wait for each prompt.', cue)
            sleep(4)
            start = clock()
            for _ in range(count):
                cue_ms = clock()
                say(text, cue)
                events.append({'class': cls, 'cueMs': cue_ms})
                sleep(every)
        blocks.append({'class': cls, 'startMs': start, 'endMs': clock()})
    say('Done. Thank you.', cue)
    return {'version': 'movement-session-v1', 'side': side, 'protocol': [list(p) for p in PROTOCOL],
            'eventSeconds': EVENT_SECONDS, 'podClockOffsetMs': {'start': offset, 'end': pb.optional_offset(pod)},
            'recordedAt': datetime.now(timezone.utc).isoformat(), 'blocks': blocks, 'events': events}


def offset_s(session):
    return ((session.get('podClockOffsetMs') or {}).get('start') or 0) / 1000


def session_span(session):
    """Pod-clock seconds covered by the session's blocks."""
    shift = offset_s(session)
    return min(b['startMs'] for b in session['blocks']) / 1000 + shift, max(b['endMs'] for b in session['blocks']) / 1000 + shift


def event_windows(session):
    shift = offset_s(session)
    out = []
    for i, e in enumerate(session['events']):
        start = e['cueMs'] / 1000 + shift + REACTION_S
        out.append((start, start + EVENT_SECONDS[e['class']], e['class'], i))
    return out


def label_window(start, end, events):
    """Class of a window: a movement it half-covers, 'still' if clear of every movement, else None (transition)."""
    for a, b, cls, index in events:
        if min(end, b) - max(start, a) >= (end - start) / 2:
            return cls, index
    if all(end <= a - GUARD_S or start >= b + GUARD_S for a, b, _, _ in events):
        return 'still', None
    return None, None


def piezo_slice(seconds, channel, start, end):
    """Samples for [start, end) in pod-clock seconds, or None if any of them is missing."""
    first, last = int(np.floor(start)), int(np.ceil(end))
    segs, _, _ = rp.segments(seconds, channel, first, last)
    if len(segs) != 1 or len(segs[0]) < (last - first) * FS:
        return None
    lo = int(round((start - first) * FS))
    return segs[0][lo:lo + int(round((end - start) * FS))]


def piezo_features(x):
    """Band log-energies, burst sharpness and a slow baseline shift for one channel window."""
    x = x - x.mean()
    spectrum = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    freqs = np.fft.rfftfreq(len(x), 1 / FS)
    out = [float(np.log10(spectrum[(freqs >= a) & (freqs < b)].sum() + 1)) for a, b in BANDS]
    high = np.abs(np.diff(x))
    out.append(float(np.log10(high.max() / (np.median(high) + 1) + 1)))
    quarter = len(x) // 8
    out.append(float(np.log10(abs(x[-quarter:].mean() - x[:quarter].mean()) + 1)))
    out.append(float(np.log10(x.var() + 1)))
    return out


def capsense_series(recs):
    """Per side: (times, sensing values) with the two records of each whole second spread 0.5 s apart."""
    out = {'left': ([], []), 'right': ([], [])}
    seen = {}
    for rec in recs:
        if rec.get('type') != 'capSense2' or not isinstance(rec.get('ts'), int):
            continue
        k = seen.get(rec['ts'], 0)
        seen[rec['ts']] = k + 1
        for side in out:
            values = (rec.get(side) or {}).get('values')
            if isinstance(values, list) and len(values) >= 6:
                out[side][0].append(rec['ts'] + 0.5 * min(k, 1))
                out[side][1].append(values[SENSING])
    return {side: (np.asarray(t, float), np.asarray(v, float).reshape(-1, 6)) for side, (t, v) in out.items()}


def capsense_features(series, start, end):
    times, values = series
    mask = (times >= start) & (times < end)
    if mask.sum() < 2:
        return [0.0, 0.0], False
    v = values[mask]
    return [float(np.abs(np.diff(v, axis=0)).sum()), float(np.abs(v[-1] - v[0]).sum())], True


def baseline_score(movement, center):
    """The pod's movement score for the minute row whose epoch contains `center` (rows stamp the epoch end)."""
    for stamp, score in movement:
        if stamp - 60 <= center < stamp:
            return score
    return None


FEATURE_NAMES = ([f'{who}_{name}' for who in ('occ', 'other')
                  for name in [f'band{a}-{b}' for a, b in BANDS] + ['burst', 'shift', 'logvar']]
                 + [f'{who}_cap_{name}' for who in ('occ', 'other') for name in ('motion', 'shift')])


def build_windows(recs, session, movement, name):
    """Labeled feature windows for one session. Windows with missing data are dropped and counted."""
    side = session['side']
    other = 'right' if side == 'left' else 'left'
    seconds, _ = rp.piezo_seconds(recs)
    caps = capsense_series(recs)
    events = event_windows(session)
    start, end = session_span(session)
    rows, dropped = [], 0
    t = start
    while t + WINDOW_S <= end:
        cls, index = label_window(t, t + WINDOW_S, events)
        if cls is not None:
            occ = piezo_slice(seconds, f'{side}1', t, t + WINDOW_S)
            oth = piezo_slice(seconds, f'{other}1', t, t + WINDOW_S)
            cap_occ, ok_occ = capsense_features(caps[side], t, t + WINDOW_S)
            cap_oth, ok_oth = capsense_features(caps[other], t, t + WINDOW_S)
            if occ is None or oth is None or not (ok_occ and ok_oth):
                dropped += 1
            else:
                rows.append({'session': name, 't': round(t, 3), 'label': cls,
                             'group': f'{name}/event{index}' if index is not None else f'{name}/still{int((t - start) // 10)}',
                             'x': piezo_features(occ) + piezo_features(oth) + cap_occ + cap_oth,
                             'baseline': baseline_score(movement, t + WINDOW_S / 2)})
        t += HOP_S
    return rows, dropped


def best_threshold(scores, moving):
    """Pod-score threshold maximizing balanced accuracy on training windows."""
    best = (-1, None)
    for threshold in sorted(set(scores)):
        predicted = scores >= threshold
        tpr = predicted[moving].mean() if moving.any() else 0
        tnr = (~predicted[~moving]).mean() if (~moving).any() else 0
        best = max(best, ((tpr + tnr) / 2, threshold), key=lambda b: b[0])
    return best[1]


def binary_metrics(truth_moving, predicted_moving, labels):
    still = ~truth_moving
    recall = float(predicted_moving[truth_moving].mean()) if truth_moving.any() else None
    fpr = float(predicted_moving[still].mean()) if still.any() else None
    by_class = {c: float(predicted_moving[labels == c].mean()) for c in CLASSES if (labels == c).any()}
    balanced = None if recall is None or fpr is None else (recall + 1 - fpr) / 2
    return {'windows': int(len(labels)), 'movingRecall': recall, 'stillFalseAlarm': fpr, 'balancedAccuracy': balanced,
            'flaggedMovingByClass': by_class}


def evaluate(rows, folds=5, seed=42):
    """Grouped cross-validation: a movement's windows stay together; sessions stay together when there are several."""
    usable = [r for r in rows if r['baseline'] is not None]
    x = np.asarray([r['x'] for r in usable])
    labels = np.asarray([r['label'] for r in usable])
    scores = np.asarray([r['baseline'] for r in usable], float)
    sessions = sorted({r['session'] for r in usable})
    groups = np.asarray([r['session'] if len(sessions) > 1 else r['group'] for r in usable])
    n_folds = min(len(set(groups)), folds if len(sessions) == 1 else len(sessions))
    if n_folds < 2 or len(set(labels)) < 2:
        raise ValueError('Need at least two groups and two classes to evaluate')
    predicted = np.empty(len(usable), dtype=object)
    baseline_moving = np.zeros(len(usable), bool)
    thresholds = []
    for train, test in GroupKFold(n_splits=n_folds).split(x, labels, groups):
        threshold = best_threshold(scores[train], labels[train] != 'still')
        thresholds.append(float(threshold))
        baseline_moving[test] = scores[test] >= threshold
        clf = RandomForestClassifier(n_estimators=300, min_samples_leaf=2, class_weight='balanced', random_state=seed, n_jobs=1)
        clf.fit(x[train], labels[train])
        predicted[test] = clf.predict(x[test])
    for row, label, moving in zip(usable, predicted, baseline_moving):
        row['predicted'], row['baselineMoving'] = str(label), bool(moving)  # out-of-fold, for timelines
    truth_moving = labels != 'still'
    present = [c for c in CLASSES if (labels == c).any()]
    cm = confusion_matrix(labels, predicted.astype(str), labels=present)
    return {
        'validation': 'leave-one-session-out' if len(sessions) > 1 else f'{n_folds}-fold grouped by movement within one session (optimistic)',
        'sessions': sessions, 'windows': int(len(usable)), 'windowsWithoutBaseline': len(rows) - len(usable),
        'classCounts': {c: int((labels == c).sum()) for c in present},
        'baseline': {'rule': 'pod movement score ≥ threshold chosen on training folds', 'thresholds': thresholds,
                     **binary_metrics(truth_moving, baseline_moving, labels)},
        'classifier': {'model': 'random forest, 300 trees, balanced classes', 'macroF1': float(f1_score(labels, predicted.astype(str), labels=present, average='macro', zero_division=0)),
                       'classes': present, 'confusion': cm.tolist(),
                       'recall': {c: float(cm[i, i] / cm[i].sum()) for i, c in enumerate(present)},
                       **binary_metrics(truth_moving, predicted != 'still', labels)},
    }


def fetch_movement(pod, side, start, end):
    """Pod movement rows (epoch-end Unix seconds, score) around the session."""
    query = urllib.parse.urlencode({'side': side, 'limit': 1000,  # the pod's maximum; a session spans minutes
                                    'startDate': datetime.fromtimestamp(start - 120, timezone.utc).isoformat(),
                                    'endDate': datetime.fromtimestamp(end + 120, timezone.utc).isoformat()})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f'{base_url(pod)}/api/biometrics/movement?{query}', timeout=30) as response:
        rows = json.loads(response.read())
    return sorted((datetime.fromisoformat(r['timestamp'].replace('Z', '+00:00')).timestamp(), r['totalMovement']) for r in rows)


def cmd_session(args):
    out = Path(args.out)
    if out.exists():
        raise SystemExit(f'{out} exists; choose a new path')
    total = sum(5 if cls != 'still' else 0 for cls, *_ in PROTOCOL) + sum(count * every for _, _, count, every in PROTOCOL)
    print(f'Movement session, {args.side} side, about {total // 60 + 1} minutes. Cues are spoken; keep the sound on.')
    doc = run_session(args.side, args.pod, args.cue)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2))
    print(f'Saved {out} with {len(doc["events"])} cued movements. Train after the pod has written the data (about 45 minutes).')


def load_session_args(values):
    """--session path[=export.tar.gz] pairs."""
    pairs = []
    for value in values:
        path, _, export = value.partition('=')
        pairs.append((Path(path), Path(export) if export else None))
    return pairs


def cmd_train(args):
    if not args.pod and not args.movement:
        raise SystemExit('Pass --pod, or --movement with saved pod movement rows')
    out = pb.prepare_output(args.out)
    rows, sources = [], []
    for path, export in load_session_args(args.session):
        session = json.loads(path.read_text())
        name = path.stem
        start, end = session_span(session)
        if export is None:
            if not args.pod:
                raise SystemExit('Pass --pod, or give each session as path=export.tar.gz')
            export = out / f'{name}.tar.gz'
            print(f'Fetching raw export for {name}…', flush=True)
            pb.fetch_export(args.pod, start - 60, end + ARCHIVE_DELAY_S, export)
        movement = (json.loads(Path(args.movement).read_text()) if args.movement else
                    fetch_movement(args.pod, session['side'], start, end))
        recs, errors, _ = pb.load_export(export)
        session_rows, dropped = build_windows(recs, session, [tuple(m) for m in movement], name)
        rows.extend(session_rows)
        sources.append({'session': name, 'sessionSha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                        'exportSha256': hashlib.sha256(export.read_bytes()).hexdigest(), 'windows': len(session_rows),
                        'droppedWindows': dropped, 'decodeErrors': len(errors), 'side': session['side']})
    report = evaluate(rows)
    report.update(version='movement-report-v1', kind='movement', generatedAt=datetime.now(timezone.utc).isoformat(),
                  sources=sources, featureNames=FEATURE_NAMES, windowSeconds=WINDOW_S, hopSeconds=HOP_S,
                  eventSeconds=EVENT_SECONDS, reactionSeconds=REACTION_S, scriptSha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  limitations=['Labels are cue times from scripted, deliberate movements, not natural sleep movements.',
                               'The pod movement score is one value per minute, so it cannot resolve a 2 s window; '
                               'the baseline is scored on the minute containing each window.',
                               'With one session, folds share a person, night and setup; results are optimistic.'])
    (out / 'report.json').write_text(json.dumps(report, indent=2))
    (out / 'windows.json').write_text(json.dumps(rows))
    register_output(out)
    for line in summary_lines(report):
        print(line)
    print(f'Report: {out / "report.json"}')


def pct(value):
    return 'n/a' if value is None else f'{value:.0%}'


def summary_lines(report):
    b, c = report['baseline'], report['classifier']
    lines = [f"{report['windows']} windows · {report['validation']}",
             f"  pod score baseline: moving recall {pct(b['movingRecall'])}, false alarms while still {pct(b['stillFalseAlarm'])}",
             f"  classifier:         moving recall {pct(c['movingRecall'])}, false alarms while still {pct(c['stillFalseAlarm'])}, macro F1 {c['macroF1']:.2f}",
             '  classifier recall by class: ' + ', '.join(f'{k} {pct(v)}' for k, v in c['recall'].items())]
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='command', required=True)
    s = sub.add_parser('session', help='spoken-cue movement session')
    s.add_argument('--side', choices=('left', 'right'), required=True)
    s.add_argument('--pod', help='pod address, for a coarse clock check')
    s.add_argument('--cue', choices=('say', 'print'), default='say', help='speak cues (macOS say) or print them')
    s.add_argument('--out', required=True, help='session JSON path (new)')
    t = sub.add_parser('train', help='compare a classifier with the pod movement score')
    t.add_argument('--session', action='append', required=True, help='session.json, or session.json=export.tar.gz; repeatable')
    t.add_argument('--pod')
    t.add_argument('--movement', help='saved pod movement rows [[epochEndSeconds, score], ...] instead of fetching')
    t.add_argument('--out', required=True)
    args = ap.parse_args(argv)
    {'session': cmd_session, 'train': cmd_train}[args.command](args)


if __name__ == '__main__':
    sys.exit(main())
