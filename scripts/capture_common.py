"""Strict capture contracts and physical-event clock alignment (all public times Unix ms)."""
import math
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

IOS_FORMAT_REVISION = '7a9696b9945945bae24409ff26768b47a0ecc24a'

import numpy as np
from scipy.signal import find_peaks



def number(value, name, minimum=None, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{name}: expected finite number')
    if integer and (not isinstance(value, int)):
        raise ValueError(f'{name}: expected integer')
    if minimum is not None and value < minimum:
        raise ValueError(f'{name}: below {minimum}')
    return value


def text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{name}: expected nonempty string')
    return value


def zone(value):
    try:
        return ZoneInfo(text(value, 'timeZone'))
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError('Unknown IANA timeZone') from error


def interval(row, lower=0, upper=math.inf):
    a = number(row.get('startMs'), 'startMs', 0, True)
    b = number(row.get('endMs'), 'endMs', 0, True)
    if not lower <= a < b <= upper:
        raise ValueError('Invalid or out-of-bounds interval')
    return a, b


def spans(rows, lower, upper):
    if not isinstance(rows, list):
        raise ValueError('Expected interval array')
    result = [interval(r, lower, upper) for r in rows]
    if any(a < previous for (_, previous), (a, _) in zip(result, result[1:])):
        raise ValueError('Intervals must be ordered and nonoverlapping')
    return result


def covered(start, end, coverage):
    return any(a <= start and end <= b for a, b in coverage)


def data_path(path):
    """Capture inputs and outputs may never escape the workspace's ignored data/."""
    path = Path(path).resolve()
    root = Path(__file__).resolve().parents[1] / 'data'
    if not path.is_relative_to(root.resolve()):
        raise ValueError('Keep capture inputs and outputs under ignored data/')
    return path


def clock_fit(pairs):
    """Fit matched physical events; ending knocks anchor offset, both ends determine drift.

    Pairs must be independently matched (schedule/energy confirmation), not arbitrary
    nearest peaks from the whole night. At least three pairs at each end are required.
    """
    if len(pairs) < 6:
        raise ValueError('Need at least three matched events at both ends')
    a = np.asarray([number(p['sourceMs'], 'sourceMs', 0) for p in pairs])
    b = np.asarray([number(p['podMs'], 'podMs', 0) for p in pairs])
    order = np.argsort(a)
    a, b = a[order], b[order]
    if np.any(np.diff(a) <= 0) or np.any(np.diff(b) <= 0) or a[-3] - a[2] < 60_000:
        raise ValueError('Matches must be unique, ordered, and span both ends')
    anchor = float(np.median(a[-3:]))
    slope, offset = np.polyfit(a - anchor, b - a, 1)
    residual = b - a - (offset + slope * (a - anchor))
    if abs(slope) > .001 or max(abs(residual)) > 100:
        raise ValueError('Clock jump or ambiguous matches; alignment refused')
    return {'synchronized': True, 'anchorMs': anchor, 'offsetMs': float(offset),
            'driftPpm': float(slope * 1e6), 'uncertaintyMs': float(max(abs(residual))),
            'sourceStartMs': float(a[0]), 'openingEndMs': float(a[2]),
            'closingStartMs': float(a[-3]), 'sourceEndMs': float(a[-1]),
            'matches': len(a), 'method': 'end-anchored linear physical-event fit'}


def align(ms, clock):
    return ms + clock.get('offsetMs', 0) + (ms - clock.get('anchorMs', ms)) * clock.get('driftPpm', 0) / 1e6


def unalign(ms, clock):
    """Return source wall time for a pod timestamp, including offset and drift."""
    drift = clock.get('driftPpm', 0) / 1e6
    anchor = clock.get('anchorMs')
    if anchor is None:
        return ms - clock.get('offsetMs', 0)
    return (ms - clock.get('offsetMs', 0) + anchor * drift) / (1 + drift)


def load_alignment(doc):
    if doc.get('version') != 'capture-alignment-v1':
        raise ValueError('Unknown alignment version')
    if doc.get('confirmation') not in ('schedule', 'energy-fit'):
        raise ValueError('Confirm matched events with schedule or energy-fit')
    result = clock_fit(doc['pairs'])
    result['confirmation'] = doc['confirmation']
    return result


def detect_knocks(seconds, channel, start_ms, end_ms):
    """Sharp derivative peaks in each uninterrupted second, with a 150ms refractory period."""
    peaks = []
    for t in sorted(seconds):
        if not start_ms / 1000 - 1 <= t <= end_ms / 1000 + 1:
            continue
        record = seconds[t]
        if record.get('freq') != 500 or channel not in record:
            continue
        x = np.asarray(record[channel])
        if len(x) != 500:
            continue
        energy = np.abs(np.diff(x.astype(float), prepend=x[0]))
        floor = np.median(energy)
        threshold = max(1, floor + 12 * np.median(abs(energy - floor)))
        indices, _ = find_peaks(energy, height=threshold, distance=75, prominence=threshold / 2)
        peaks.extend(t * 1000 + int(i) * 2 for i in indices)
    return [p for p in peaks if start_ms <= p <= end_ms]


def match_knocks(source, pod, start, end, tolerance_ms=5000):
    """Match each three-knock schedule as a group, ending first (sharper in the 2026-10-22 session).

    Reject ambiguous schedules instead of claiming sync from arbitrary high energy.
    A common offset is scored by residual timing; both ends must independently match.
    """
    pairs = []
    for lo, hi in [(end - 60_000, end), (start, start + 60_000)]:
        group = [s for s in source if lo <= s <= hi]
        candidates = [p for p in pod if lo - tolerance_ms <= p <= hi + tolerance_ms]
        if len(group) < 3:
            raise ValueError('Missing opening or closing knock schedule')
        fits = []
        for p in candidates:
            offset = p - group[0]
            if abs(offset) > tolerance_ms:
                continue
            matched = [min(candidates, key=lambda v: abs(v - s - offset)) for s in group]
            errors = np.asarray(matched) - np.asarray(group)
            residual = max(abs(errors - np.median(errors)))
            if len(set(matched)) == len(group) and residual <= 20:
                fits.append((residual, matched))
        fits.sort(key=lambda f: f[0])
        if not fits or (len(fits) > 1 and fits[1][0] - fits[0][0] < 2):
            raise ValueError('Ambiguous or absent knock schedule')
        pairs.extend({'sourceMs': s, 'podMs': p} for s, p in zip(group, fits[0][1]))
    result = clock_fit(pairs)
    result['confirmation'] = 'energy-fit'
    return result


def check_capture_clock(clock, start, end):
    if clock.get('synchronized') and 'sourceStartMs' in clock:
        if not (start <= clock['sourceStartMs'] <= clock['openingEndMs'] <= start + 60_000
                and end - 60_000 <= clock['closingStartMs'] <= clock['sourceEndMs'] <= end):
            raise ValueError('Matched events must belong to the opening and closing minute of this capture')
