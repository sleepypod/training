"""Import iPhone snore exports and align them to raw piezo; provisional labels, never diagnoses."""
import argparse
import hashlib
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from uuid import UUID

import numpy as np

import capture_common as cc
import piezo_bandwidth as pb
import raw_piezo as rp
from movement import piezo_slice, ARCHIVE_DELAY_S
from run_history import register_output
import snore_exit

DETECTOR = 'apple-sound-classifier-v1+hysteresis-0.6-0.4'
CLASSIFIER = {'identifier': 'SNClassifierIdentifier.version1', 'label': 'snoring',
              'windowSeconds': 3, 'overlapFactor': .5, 'onThreshold': .6, 'offThreshold': .4}


def parse(doc):
    if doc.get('schemaVersion') != 'sleepypod-snore-capture-v1':
        raise ValueError('Unknown snore schema version')
    if doc.get('units') != 'unix-ms' or doc.get('source') != 'iphone-microphone':
        raise ValueError('Unsupported source or timestamp units')
    if doc.get('detectorVersion') != DETECTOR or doc.get('classifier') != CLASSIFIER:
        raise ValueError('Unknown detector version or classifier parameters')
    UUID(cc.text(doc.get('captureId'), 'captureId'))
    for key in ['device', 'osVersion', 'appVersion']:
        cc.text(doc.get(key), key)
    cc.zone(doc.get('timeZone'))
    start = cc.number(doc.get('startMs'), 'startMs', 0, True)
    # Open exports are not final labels: require a stopped capture.
    end = cc.number(doc.get('endMs'), 'endMs (export a stopped session)', 0, True)
    if end < start:
        raise ValueError('Reversed session')
    reason = cc.text(doc.get('stopReason'), 'stopReason')
    if reason not in ('user', 'app terminated', 'app not running') and not reason.startswith('failed: '):
        raise ValueError('Unknown stopReason')
    for key in ['sampleRate', 'channelCount']:
        if doc.get(key) is not None:
            cc.number(doc[key], key, 1, key == 'channelCount')
    coverage = cc.spans(doc.get('coverage'), start, end)
    if coverage and (doc.get('sampleRate') is None or doc.get('channelCount') is None):
        raise ValueError('Recording coverage requires microphone format')
    interruptions = []
    for row in doc.get('interruptions', []):
        cc.text(row.get('reason'), 'interruption reason')
        a, b = cc.interval(row, start, end)
        if any(min(b, d) > max(a, c) for c, d in coverage):
            raise ValueError('Interruption overlaps recording coverage')
        interruptions.append((a, b))
    if interruptions != sorted(interruptions):
        raise ValueError('Interruptions out of order')
    previous = start
    for row in doc.get('events', []):
        a, b = cc.interval(row, start, end)
        confidence = cc.number(row.get('confidence'), 'confidence', 0)
        if confidence > 1 or confidence < .6:
            raise ValueError('Event confidence inconsistent with opening threshold')
        if row.get('captureId') != doc['captureId'] or row.get('eventType') != 'snore' or row.get('labelOrigin') != 'predicted' or row.get('detectorVersion') != DETECTOR:
            raise ValueError('Unknown event provenance')
        if a < previous or not cc.covered(a, b, coverage):
            raise ValueError('Events must be ordered, nonoverlapping and recorded')
        previous = b
    previous = -1
    for t in doc.get('knocks', []):
        cc.number(t, 'knock Unix ms', start, True)
        if t > end or t <= previous or not (t <= start + 60_000 or t >= end - 60_000):
            raise ValueError('Invalid or duplicate knock')
        previous = t
    for key in ['interruptions', 'events', 'knocks']:
        if not isinstance(doc.get(key), list):
            raise ValueError(f'Missing {key} array')
    return doc


def align_session(doc, clock):
    result = dict(doc)
    for key in ['startMs', 'endMs']:
        result[key] = cc.align(doc[key], clock)
    for key in ['events', 'coverage', 'interruptions']:
        result[key] = [{**r, 'startMs': cc.align(r['startMs'], clock), 'endMs': cc.align(r['endMs'], clock)} for r in doc[key]]
    result['knocks'] = [cc.align(t, clock) for t in doc['knocks']]
    return result


def context_windows(doc, seconds, channel, clock, evidence):
    """Optional calibration export with actual detector windows and pod-clock context.

    iOS v1 exports cannot populate this contract: absence of a merged event is
    not a confidence below threshold/2. Never synthesize negative confidences.
    """
    local = datetime.fromtimestamp(doc['startMs'] / 1000, cc.zone(doc['timeZone']))
    night = {'night': (local - timedelta(hours=12)).date().isoformat(), 'occupants': None,
             'synchronized': clock['synchronized'], 'windowConfidencesAvailable': False, 'windows': []}
    if evidence is None:
        return night
    if evidence.get('version') != 'snore-window-evidence-v1' or evidence.get('captureId') != doc['captureId']:
        raise ValueError('Unknown/mismatched window evidence')
    registered = cc.number(evidence.get('registeredBeforeCaptureMs'), 'registeredBeforeCaptureMs', 0, True)
    if registered >= doc['startMs']:
        raise ValueError('Threshold and band must be registered before capture')
    threshold = cc.number(evidence.get('threshold'), 'threshold', 0)
    if not 0 < threshold <= 1:
        raise ValueError('Invalid threshold')
    cc.text(evidence.get('calibrationId'), 'calibrationId')
    bands = evidence.get('passingBands')
    if bands not in (['16-30Hz'], ['60-100Hz'], ['16-30Hz', '60-100Hz']):
        raise ValueError('Supply the preregistered passing bands')
    band = (16, 30) if '16-30Hz' in bands else (60, 100)
    if evidence.get('primaryChannel') != channel:
        raise ValueError('Primary channel must match preregistered occupant channel')
    aligned = align_session(doc, clock)
    lo, hi = aligned['startMs'], aligned['endMs']
    occupied = cc.spans(evidence['occupied'], lo, hi)
    pump_off = cc.spans(evidence['pumpOff'], lo, hi)
    movement = cc.spans(evidence['movement'], lo, hi)
    pump = cc.spans(evidence['pumpActive'], lo, hi)
    if any(min(b, d) > max(a, c) for a, b in pump_off for c, d in pump):
        raise ValueError('Pump off and active context overlap')
    coverage = [(r['startMs'], r['endMs']) for r in aligned['coverage']]
    rows, normalization = [], []
    previous = -1
    for row in evidence['windows']:
        a, b = cc.interval(row, doc['startMs'], doc['endMs'])
        if b - a != 3000 or a <= previous:
            raise ValueError('Expected unique ordered 3s detector windows')
        previous = a
        confidence = cc.number(row['confidence'], 'window confidence', 0)
        if confidence > 1:
            raise ValueError('Confidence exceeds one')
        a, b = cc.align(a, clock), cc.align(b, clock)
        x = piezo_slice(seconds, channel, a / 1000, b / 1000)
        score = None
        if x is not None:
            f, p, _ = pb.psd([x])
            score = pb.finite_or_none(pb.band_db(f, p, band))
        occ = cc.covered(a, b, occupied)
        off = cc.covered(a, b, pump_off)
        if score is not None and occ and off:
            normalization.append(score)
        excluded = not off or any(a < d + 10_000 and b > c - 10_000 for c, d in movement + pump)
        rows.append({'startMs': a, 'endMs': b, 'confidence': confidence, 'score': score,
                     'occupied': occ, 'excluded': excluded, 'covered': x is not None and cc.covered(a, b, coverage)})
    median = float(np.median(normalization)) if normalization else None
    for row in rows:
        row['score'] = row['score'] - median if median is not None and row['score'] is not None else None
    night.update(occupants=cc.number(evidence['occupants'], 'occupants', 0, True), threshold=threshold,
                 calibrationId=evidence['calibrationId'], windowConfidencesAvailable=True,
                 windows=rows, bandHz=band, primaryChannel=channel, normalizationDb=median)
    return night


def import_session(doc, recs, files, side, evidence=None, clock=None):
    doc = parse(doc)
    seconds, _ = rp.piezo_seconds(recs)
    channel = side + '1'
    if clock is None:
        try:
            pod_knocks = []
            for a, b in [(doc['startMs'] - 5000, doc['startMs'] + 65_000), (doc['endMs'] - 65_000, doc['endMs'] + 5000)]:
                pod_knocks.extend(cc.detect_knocks(seconds, channel, a, b))
            clock = cc.match_knocks(doc['knocks'], sorted(set(pod_knocks)), doc['startMs'], doc['endMs'])
        except ValueError as error:
            clock = {'synchronized': False, 'method': 'NTP wall time only', 'reason': str(error)}
    cc.check_capture_clock(clock, doc['startMs'], doc['endMs'])
    aligned = align_session(doc, clock)
    report = pb.analyze_night(recs, files, aligned['startMs'] / 1000, aligned['endMs'] / 1000)
    night = context_windows(doc, seconds, channel, clock, evidence)
    report.update(kind='snore-labels', version='snore-labels-v1', generatedAt=datetime.now(timezone.utc).isoformat(),
                  iosFormatRevision=cc.IOS_FORMAT_REVISION, capture=aligned, clock=clock, evaluationNight=night, exitTest=snore_exit.evaluate([night]),
                  phoneCoverage=sum(r['endMs'] - r['startMs'] for r in doc['coverage']) / max(1, doc['endMs'] - doc['startMs']),
                  limitations=['Events are phone predictions; confidence is not a calibrated probability.',
                               'Merged event peaks cannot establish detector-window guard-band negatives.',
                               'Unsynchronized sessions are excluded from event-level comparison.'])
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--session', help='Stopped iPhone export JSON')
    ap.add_argument('--export', help='Saved raw piezo tar.gz')
    ap.add_argument('--pod')
    ap.add_argument('--side', choices=['left', 'right'])
    ap.add_argument('--alignment', help='Confirmed capture-alignment-v1 physical event pairs')
    ap.add_argument('--window-evidence', help='Calibrated per-window evidence, absent from current iOS exports')
    ap.add_argument('--reports', nargs='+', help='Evaluate previously imported report.json files across nights')
    ap.add_argument('--out', required=True)
    args = ap.parse_args(argv)
    out = pb.prepare_output(cc.data_path(args.out))
    if args.reports:
        reports = [json.loads(cc.data_path(p).read_text()) for p in args.reports]
        if any(r.get('kind') != 'snore-labels' or r.get('version') != 'snore-labels-v1' for r in reports):
            raise ValueError('Unknown snore-label report version')
        nights = [r['evaluationNight'] for r in reports]
        result = {'kind': 'snore-labels', 'version': 'snore-labels-v1', 'exitTest': snore_exit.evaluate(nights)}
    else:
        if not args.session or not args.side or not (args.export or args.pod):
            ap.error('Import requires --session, --side and --export or --pod')
        source = cc.data_path(args.session)
        doc = parse(json.loads(source.read_text()))
        export = cc.data_path(args.export) if args.export else out / 'raw.tar.gz'
        if not args.export:
            pb.fetch_export(args.pod, doc['startMs'] / 1000 - 60, doc['endMs'] / 1000 + ARCHIVE_DELAY_S, export)
        recs, errors, files = pb.load_export(export)
        clock = cc.load_alignment(json.loads(cc.data_path(args.alignment).read_text())) if args.alignment else None
        evidence = json.loads(cc.data_path(args.window_evidence).read_text()) if args.window_evidence else None
        result = import_session(doc, recs, files, args.side, evidence, clock)
        result['source'] = {'captureSha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                            'exportSha256': hashlib.sha256(export.read_bytes()).hexdigest(), 'decodeErrors': len(errors)}
        (out / 'windows.json').write_text(json.dumps(result.pop('windows'), allow_nan=False))
    (out / 'report.json').write_text(json.dumps(result, indent=2, allow_nan=False))
    register_output(out)
    print(result['exitTest']['result'])


if __name__ == '__main__':
    main()
