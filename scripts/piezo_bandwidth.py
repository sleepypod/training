"""Piezo bandwidth gate: does the pod's raw piezo preserve 16–30 Hz and 60–100 Hz?

Section 0 of docs/overnight-capture-implementation-plan.md. Three commands:

  session  Guided scripted session; records each step's UTC window to steps.json.
  analyze  Fetch (or read) the raw export for a session and score the fixed pass bar.
  night    Storage, coverage and hourly band power for one overnight export.

Read-only: the only pod request is GET /api/export/archive. Exports and reports
stay under ignored data/. Reports hold spectra summaries, never raw samples.
"""
import argparse
import hashlib
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import numpy as np
from scipy.signal import butter, sosfiltfilt, welch

import raw_piezo
from fetch_pod import base_url

FS = 500
NPERSEG = 1000  # 2 s windows, 0.5 Hz resolution
BANDS = {'16-30Hz': (16.0, 30.0), '60-100Hz': (60.0, 100.0)}
CONTEXT_BANDS = {'1-10Hz': (1.0, 10.0), '100-245Hz': (100.0, 245.0)}
TRIM_START_S, TRIM_END_S = 10, 5

# Pass bar, fixed before any session is recorded. Do not tune on results.
PASS_BAR = {
    'reference': 'occupied_still',
    'stimuli': ['occupied_hum', 'occupied_snore'],
    'minRiseDb': 6.0,
    'knockSteps': ['knock_head', 'knock_foot'],
    'knockHighpassHz': 20.0,
    'knockSigma': 8.0,
    'knockMinEvents': 3,
    'knockMinSeparationS': 0.3,
}

STEP_SECONDS = 60
STEPS = [
    ('empty_pump_off', 'Bed empty. Turn both sides off in the app and wait until the pump is quiet.'),
    ('empty_pump_on', 'Bed empty. Set one side to its coldest setting and wait until you hear the pump running.'),
    ('occupied_still', 'Turn both sides off again. Lie on your side of the bed, still, breathing normally.'),
    ('occupied_hum', 'Stay lying down with your head on the pillow. Hum low and steady, pausing only to breathe.'),
    ('occupied_snore', 'Stay lying down. Imitate snoring on every breath.'),
    ('knock_head', 'Stay still. Knock three times on the bed frame at the head, about one second apart, then stay still.'),
    ('knock_foot', 'Stay still. Knock three times on the bed frame at the foot, about one second apart, then stay still.'),
]


def now_ms():
    return round(time.time() * 1000)


def pod_clock_offset_ms(pod):
    """Pod HTTP Date minus local time; 1 s resolution, a coarse check only."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    before = now_ms()
    with opener.open(base_url(pod) + '/api/raw/files', timeout=10) as response:
        header = response.headers.get('Date')
    after = now_ms()
    if not header:
        return None
    return round(parsedate_to_datetime(header).timestamp() * 1000) - (before + after) // 2


def fetch_export(pod, start_s, end_s, out):
    """Download raw files whose mtime ≥ start_s; archives are always written after their data."""
    url = f'{base_url(pod)}/api/export/archive?startTs={int(start_s)}&endTs={int(end_s)}&include=raw'
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    out.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with opener.open(url, timeout=600) as response, open(out, 'wb') as f:
        while chunk := response.read(1 << 20):
            f.write(chunk)
            digest.update(chunk)
    return {'url': url, 'bytes': out.stat().st_size, 'sha256': digest.hexdigest()}


def load_export(path):
    recs, errors, files = [], [], []
    for name, compressed, raw in raw_piezo.export_files(path):
        count = 0
        for rec, error in raw_piezo.records(raw):
            if error:
                errors.append({'file': name, 'error': error})
            else:
                recs.append(rec)
                count += 1
        files.append({'name': name, 'compressedBytes': compressed, 'rawBytes': len(raw), 'records': count})
    return recs, errors, files


def channels_present(seconds):
    return [c for c in raw_piezo.CHANNELS if any(c in entry for entry in seconds.values())]


def psd(segments):
    """Window-weighted mean Welch PSD over segments of at least one window."""
    total, windows, freqs = None, 0, None
    for segment in segments:
        n = (len(segment) - NPERSEG) // (NPERSEG // 2) + 1
        if len(segment) < NPERSEG or n < 1:
            continue
        # numpy 2.x on macOS Accelerate raises spurious FP warnings from any matmul; check the result instead.
        with np.errstate(divide='ignore', over='ignore', invalid='ignore'):
            freqs, p = welch(segment, fs=FS, nperseg=NPERSEG, detrend='linear')
        if not np.isfinite(p).all():
            raise ValueError('Non-finite PSD')
        total = p * n if total is None else total + p * n
        windows += n
    return (freqs, total / windows, windows) if windows else (None, None, 0)


def band_db(freqs, p, band):
    mask = (freqs >= band[0]) & (freqs < band[1])
    return float(10 * np.log10(np.trapezoid(p[mask], freqs[mask])))


def floor_slope(freqs, p, lo=100.0, hi=245.0):
    """PSD slope in dB per decade between lo and hi; flat suggests a noise floor, steep a filter edge."""
    mask = (freqs >= lo) & (freqs < hi)
    return float(np.polyfit(np.log10(freqs[mask]), 10 * np.log10(p[mask]), 1)[0])


def highpass(segment, cutoff):
    return sosfiltfilt(butter(4, cutoff, btype='highpass', fs=FS, output='sos'), segment)


def robust_sigma(values):
    return float(1.4826 * np.median(np.abs(values - np.median(values))))


def transient_events(segments, threshold, separation_s):
    """Count threshold crossings of |high-passed signal| at least separation_s apart."""
    events = 0
    for segment in segments:
        above = np.flatnonzero(np.abs(segment) > threshold)
        last = None
        for index in above:
            if last is None or index - last > separation_s * FS:
                events += 1
            last = index
    return events


def pump_rpm(recs, start_s, end_s):
    values = [max(r.get(s, {}).get('pump', {}).get('rpm') or 0 for s in ('left', 'right'))
              for r in recs if r.get('type') == 'frzHealth' and start_s <= r.get('ts', -1) < end_s]
    return {'samples': len(values), 'maxRpm': max(values) if values else None,
            'medianRpm': float(np.median(values)) if values else None}


def step_window(step):
    return step['startMs'] // 1000 + TRIM_START_S, step['endMs'] // 1000 - TRIM_END_S


def analyze_steps(recs, steps, side):
    seconds, duplicates = raw_piezo.piezo_seconds(recs)
    present = channels_present(seconds)
    by_name = {s['name']: s for s in steps}
    spectra, report_steps = {}, {}
    for step in steps:
        start_s, end_s = step_window(step)
        entry = {'analyzedStartS': start_s, 'analyzedEndS': end_s, 'pump': pump_rpm(recs, start_s, end_s), 'channels': {}}
        for channel in present:
            segs, interpolated, dropped = raw_piezo.segments(seconds, channel, start_s, end_s)
            freqs, p, windows = psd(segs)
            stats = {'samples': int(sum(map(len, segs))), 'expectedSamples': (end_s - start_s) * FS,
                     'interpolatedSamples': interpolated, 'droppedSamples': dropped, 'welchWindows': windows}
            if windows:
                spectra[(step['name'], channel)] = (freqs, p, segs)
                stats['bandDb'] = {k: band_db(freqs, p, b) for k, b in {**BANDS, **CONTEXT_BANDS}.items()}
                stats['floorSlopeDbPerDecade'] = floor_slope(freqs, p)
                stats['psdDb'] = {str(f): float(10 * np.log10(p[np.argmin(np.abs(freqs - f))]))
                                  for f in (1, 5, 10, 20, 30, 40, 50, 60, 80, 100, 120, 150, 200, 240)}
            entry['channels'][channel] = stats
        report_steps[step['name']] = entry

    reference = PASS_BAR['reference']
    occupant = [c for c in present if c.startswith(side)]
    bands = {}
    for band in BANDS:
        rises = {}
        for stimulus in PASS_BAR['stimuli']:
            for channel in occupant:
                ref = report_steps.get(reference, {}).get('channels', {}).get(channel, {}).get('bandDb', {}).get(band)
                got = report_steps.get(stimulus, {}).get('channels', {}).get(channel, {}).get('bandDb', {}).get(band)
                if ref is not None and got is not None:
                    rises[f'{stimulus}/{channel}'] = got - ref
        best = max(rises.values()) if rises else None
        bands[band] = {'risesDb': rises, 'bestRiseDb': best,
                       'passed': best is not None and best >= PASS_BAR['minRiseDb'],
                       'evaluated': bool(rises)}

    knocks = {}
    for name in PASS_BAR['knockSteps']:
        if name not in by_name:
            continue
        knocks[name] = {}
        for channel in present:
            ref = spectra.get((reference, channel))
            got = spectra.get((name, channel))
            if not ref or not got:
                knocks[name][channel] = None
                continue
            ref_hp = np.concatenate([highpass(s, PASS_BAR['knockHighpassHz']) for s in ref[2]])
            threshold = PASS_BAR['knockSigma'] * robust_sigma(ref_hp)
            hp = [highpass(s, PASS_BAR['knockHighpassHz']) for s in got[2]]
            knocks[name][channel] = transient_events(hp, threshold, PASS_BAR['knockMinSeparationS'])
    knock_pass = any(all((n or 0) >= PASS_BAR['knockMinEvents'] for n in per.values()) and per
                     for per in knocks.values())

    passed = [b for b, r in bands.items() if r['passed']]
    if not all(r['evaluated'] for r in bands.values()):
        outcome = 'incomplete'
    elif len(passed) == len(BANDS):
        outcome = 'both-bands'
    elif passed == ['16-30Hz']:
        outcome = 'low-band-only'
    elif passed:
        outcome = 'high-band-only'
    else:
        outcome = 'neither'
    return {'passBar': PASS_BAR, 'side': side, 'channels': present, 'occupantChannels': occupant,
            'duplicateSeconds': duplicates, 'steps': report_steps, 'bands': bands,
            'knockEvents': knocks, 'knockPassed': bool(knock_pass), 'outcome': outcome}


def analyze_night(recs, files, start_s, end_s):
    seconds, duplicates = raw_piezo.piezo_seconds(recs)
    present = channels_present(seconds)
    in_window = [t for t in seconds if start_s <= t < end_s]
    hours = []
    for hour_start in range(int(start_s), int(end_s), 3600):
        hour_end = min(hour_start + 3600, int(end_s))
        row = {'startS': hour_start, 'piezoSeconds': sum(1 for t in in_window if hour_start <= t < hour_end),
               'pump': pump_rpm(recs, hour_start, hour_end), 'channels': {}}
        for channel in present:
            medians = {k: [] for k in {**BANDS, **CONTEXT_BANDS}}
            lost = 0
            for window_start in range(hour_start, hour_end, 30):
                segs, interpolated, dropped = raw_piezo.segments(seconds, channel, window_start, min(window_start + 30, hour_end))
                lost += interpolated + dropped
                freqs, p, windows = psd(segs)
                if windows:
                    for k, b in {**BANDS, **CONTEXT_BANDS}.items():
                        medians[k].append(band_db(freqs, p, b))
            row['channels'][channel] = {'lostSamples': lost, 'bandDbPercentiles': {
                k: (np.percentile(v, [10, 50, 90]).tolist() if v else None) for k, v in medians.items()}}
        hours.append(row)
    compressed = sum(f['compressedBytes'] for f in files)
    raw = sum(f['rawBytes'] for f in files)
    archived = [f for f in files if f['name'].lower().endswith('.gz')]
    archived_raw = sum(f['rawBytes'] for f in archived)
    covered = len(in_window)
    return {'channels': present, 'freq': sorted({e.get('freq') for e in seconds.values()} - {None}),
            'duplicateSeconds': duplicates, 'windowSeconds': int(end_s - start_s), 'piezoSeconds': covered,
            'coverage': covered / max(1, end_s - start_s),
            'storage': {'files': len(files), 'exportCompressedBytes': compressed, 'exportRawBytes': raw,
                        'compressionRatio': compressed / raw if raw else None,
                        'archiveGzipRatio': sum(f['compressedBytes'] for f in archived) / archived_raw if archived_raw else None,
                        'archivedFiles': len(archived),
                        'piezoSampleBytesPerHour': len(present) * FS * 4 * 3600,
                        'note': 'Export bytes include every RAW record type and any files outside the window.'},
            'hours': hours}


def summary_lines(report):
    lines = [f"Outcome: {report['outcome']}  (channels {', '.join(report['channels'])}; occupant {', '.join(report['occupantChannels'])})"]
    for band, r in report['bands'].items():
        best = 'n/a' if r['bestRiseDb'] is None else f"{r['bestRiseDb']:+.1f} dB"
        lines.append(f"  {band}: best rise {best} vs {PASS_BAR['reference']} → {'PASS' if r['passed'] else 'fail'}")
    lines.append(f"  knocks: {report['knockEvents']} → {'PASS' if report['knockPassed'] else 'fail'}")
    for name, step in report['steps'].items():
        pump = step['pump']['maxRpm']
        cells = '  '.join(f"{c} " + (' '.join(f"{k}={v:.1f}" for k, v in s.get('bandDb', {}).items()) or 'no data')
                          for c, s in step['channels'].items())
        lines.append(f"  {name:15} pump≤{pump}  {cells}")
    return lines


def cmd_session(args):
    out = Path(args.out)
    if out.exists():
        raise SystemExit(f'{out} exists; choose a new path')
    offset = pod_clock_offset_ms(args.pod) if args.pod else None
    print(f'Side: {args.side}. {len(STEPS)} steps × {args.step_seconds} s. Press Enter to start each step.')
    if offset is not None:
        print(f'Pod clock offset ≈ {offset / 1000:+.1f} s (HTTP Date, 1 s resolution).')
    steps = []
    for index, (name, instruction) in enumerate(STEPS, 1):
        input(f'\n[{index}/{len(STEPS)}] {name}: {instruction}\n  Press Enter when ready… ')
        start = now_ms()
        print('\a  Recording', end='', flush=True)
        while (remaining := args.step_seconds - (now_ms() - start) / 1000) > 0:
            print(f'\r  Recording {name}: {remaining:4.0f} s left ', end='', flush=True)
            time.sleep(min(1, remaining))
        steps.append({'name': name, 'instruction': instruction, 'startMs': start, 'endMs': now_ms()})
        print(f'\r\a  Done: {name}.                         ')
    doc = {'version': 'piezo-bandwidth-session-v1', 'side': args.side, 'stepSeconds': args.step_seconds,
           'podClockOffsetMs': {'start': offset, 'end': pod_clock_offset_ms(args.pod) if args.pod else None},
           'recordedAt': datetime.now(timezone.utc).isoformat(), 'steps': steps}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2))
    print(f'\nSaved {out}. Next: piezo_bandwidth.py analyze --steps {out} --pod {args.pod or "<pod>"} --out <dir>')


def prepare_output(path):
    out = Path(path)
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f'{out} is not empty; use a fresh directory')
    out.mkdir(parents=True, exist_ok=True)
    return out


def obtain_export(args, out, start_s, end_s):
    if args.export:
        path = Path(args.export)
        return path, {'path': str(path), 'bytes': path.stat().st_size, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    if not args.pod:
        raise SystemExit('Pass --pod to fetch or --export to read a saved export')
    path = out / 'export.tar.gz'
    print(f'Fetching raw export from {args.pod}…', flush=True)
    return path, fetch_export(args.pod, start_s, end_s, path)


def cmd_analyze(args):
    session = json.loads(Path(args.steps).read_text())
    out = prepare_output(args.out)
    start_s = min(s['startMs'] for s in session['steps']) // 1000 - 60
    path, source = obtain_export(args, out, start_s, time.time())
    recs, errors, files = load_export(path)
    report = analyze_steps(recs, session['steps'], args.side or session['side'])
    report.update(version='piezo-bandwidth-report-v1', generatedAt=datetime.now(timezone.utc).isoformat(),
                  source=source, files=files, decodeErrors=errors,
                  scriptSha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  sessionSha256=hashlib.sha256(Path(args.steps).read_bytes()).hexdigest())
    (out / 'report.json').write_text(json.dumps(report, indent=2))
    print('\n'.join(summary_lines(report)))
    print(f'Report: {out / "report.json"}')


def parse_time(value):
    if value.isdigit():
        return int(value)
    d = datetime.fromisoformat(value)
    if d.tzinfo is None:
        raise SystemExit('Times need a UTC offset, for example 2026-10-22T23:00-07:00')
    return int(d.timestamp())


def cmd_night(args):
    out = prepare_output(args.out)
    start_s, end_s = parse_time(args.start), parse_time(args.end)
    if end_s <= start_s:
        raise SystemExit('--end must be after --start')
    path, source = obtain_export(args, out, start_s, time.time())
    recs, errors, files = load_export(path)
    report = analyze_night(recs, files, start_s, end_s)
    report.update(version='piezo-night-report-v1', generatedAt=datetime.now(timezone.utc).isoformat(),
                  source=source, decodeErrors=errors, scriptSha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    (out / 'report.json').write_text(json.dumps(report, indent=2))
    s = report['storage']
    print(f"Channels {report['channels']} at {report['freq']} Hz · coverage {report['coverage']:.1%} of {report['windowSeconds'] / 3600:.1f} h")
    ratio = 'n/a' if s['archiveGzipRatio'] is None else f"{s['archiveGzipRatio']:.2f}"
    print(f"Export {s['exportCompressedBytes'] / 1e6:.0f} MB as fetched / {s['exportRawBytes'] / 1e6:.0f} MB decompressed (archive gzip ratio {ratio}); "
          f"piezo samples alone {s['piezoSampleBytesPerHour'] * report['windowSeconds'] / 3600 / 1e6:.0f} MB")
    print(f'Report: {out / "report.json"}')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='command', required=True)
    s = sub.add_parser('session', help='guided scripted session')
    s.add_argument('--side', choices=('left', 'right'), required=True)
    s.add_argument('--pod', help='pod address, for a coarse clock-offset check')
    s.add_argument('--out', required=True, help='steps.json path (new)')
    s.add_argument('--step-seconds', type=int, default=STEP_SECONDS)
    a = sub.add_parser('analyze', help='score a recorded session')
    a.add_argument('--steps', required=True)
    a.add_argument('--side', choices=('left', 'right'))
    a.add_argument('--pod')
    a.add_argument('--export', help='saved /api/export/archive tarball instead of fetching')
    a.add_argument('--out', required=True)
    n = sub.add_parser('night', help='storage, coverage and hourly band power for one night')
    n.add_argument('--start', required=True, help='Unix seconds or ISO time with offset')
    n.add_argument('--end', required=True)
    n.add_argument('--pod')
    n.add_argument('--export')
    n.add_argument('--out', required=True)
    args = ap.parse_args(argv)
    {'session': cmd_session, 'analyze': cmd_analyze, 'night': cmd_night}[args.command](args)


if __name__ == '__main__':
    sys.exit(main())
