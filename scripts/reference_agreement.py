"""Compare the pod with a wearable's measurements, per metric and per device. Reads only.

Descriptive: agreement with a wearable is not clinical accuracy, and pod HRV is a 5-minute
variability index rather than RMSSD or SDNN, so its level is not expected to match either.
"""
import argparse
import json
from pathlib import Path
import sqlite3

import numpy as np
from scipy import stats

from reference_devices import METRICS, AppleHealthExport, NormalizedFile

# The pod value each metric is compared with. Others have no pod counterpart and report coverage only.
POD_COLUMN = {'breathing_rate': 'breathing_rate', 'hrv_sdnn': 'hrv', 'hrv_rmssd': 'hrv'}
SAME_QUANTITY = {'breathing_rate'}
MIN_PAIRS = 10
TOLERANCE_MS = 150000  # Half the pod's 5-minute reading interval, for spot readings.


def snapshot_measurements(db):
    """Readings fetched from the pod; empty for a snapshot taken before it stored them."""
    if not db.execute("select 1 from sqlite_master where name = 'reference_samples'").fetchone():
        return []
    return [{'metric': m, 'value': v, 'startMs': round(t * 1000), 'endMs': None if e is None else round(e * 1000), 'device': d, 'algorithm': a}
            for m, t, e, v, d, a in db.execute('select metric, timestamp, end_timestamp, value, device, algorithm from reference_samples')]


def pod_value(times, values, row):
    """Mean of pod readings across a window; for a spot reading, the nearest within tolerance."""
    if row['endMs'] is not None:
        inside = values[(times >= row['startMs']) & (times <= row['endMs'])]
        return float(inside.mean()) if len(inside) else None
    if not len(times):
        return None
    nearest = int(np.abs(times - row['startMs']).argmin())
    return float(values[nearest]) if abs(times[nearest] - row['startMs']) <= TOLERANCE_MS else None


def correlation(pairs):
    if len(pairs) < MIN_PAIRS:
        return None
    pod, reference = np.array(pairs).T
    if pod.std() == 0 or reference.std() == 0:
        return None
    return round(float(stats.spearmanr(pod, reference).statistic), 3)


def summarize(rows, sessions, vitals):
    """`sessions` is [(startMs, endMs)]; `vitals` maps a pod column to sorted (times, values) arrays."""
    groups = {}
    for row in rows:
        night = next((i for i, (a, b) in enumerate(sessions) if a <= row['startMs'] <= b), None)
        if night is not None:
            groups.setdefault((row['metric'], row['device'], row['algorithm']), []).append((night, row))
    result = []
    for (metric, device, algorithm), entries in sorted(groups.items(), key=lambda g: tuple(str(k) for k in g[0])):
        unit = METRICS[metric][0]
        entry = {'metric': metric, 'device': device, 'algorithm': algorithm, 'unit': unit, 'readings': len(entries),
                 'nights': len({night for night, _ in entries}), 'windowed': sum(r['endMs'] is not None for _, r in entries),
                 'referenceMedian': round(float(np.median([r['value'] for _, r in entries])), 2)}
        column = POD_COLUMN.get(metric)
        if column:
            times, values = vitals[column]
            paired = [(night, pod_value(times, values, r), r['value']) for night, r in entries]
            paired = [p for p in paired if p[1] is not None]
            pairs = [(pod, reference) for _, pod, reference in paired]
            nightly = {}
            for night, pod, reference in paired:
                nightly.setdefault(night, []).append((pod, reference))
            means = [tuple(np.mean(v, axis=0)) for v in nightly.values()]
            entry.update(pairs=len(pairs), spearman=correlation(pairs), nightlySpearman=correlation(means), comparison='same quantity' if metric in SAME_QUANTITY else 'different quantity: trend only')
            if pairs:
                pod, reference = np.array(pairs).T
                entry['podToReferenceRatio'] = round(float(np.median(pod / reference)), 2)
                if metric in SAME_QUANTITY:
                    entry['medianAbsoluteDifference'] = round(float(np.median(np.abs(pod - reference))), 2)
        result.append(entry)
    return result


def compare(snapshot, side, rows):
    with sqlite3.connect(f'file:{snapshot}?mode=ro', uri=True) as db:
        sessions = [(round(a * 1000), round(b * 1000)) for a, b in db.execute(
            'select entered_bed_at, left_bed_at from sleep_records where side = ? and left_bed_at is not null order by entered_bed_at', (side,))]
        vitals = {}
        for column in sorted(set(POD_COLUMN.values())):
            found = db.execute(f'select timestamp, {column} from vitals where side = ? and {column} is not null order by timestamp', (side,)).fetchall()
            data = np.array(found, dtype=float).reshape(-1, 2)
            vitals[column] = (data[:, 0] * 1000, data[:, 1])
        rows = rows + snapshot_measurements(db)
    unique = {(r['metric'], r['startMs'], r['endMs']): r for r in reversed(rows)}
    return {'side': side, 'sessions': len(sessions), 'metrics': summarize(list(unique.values()), sessions, vitals),
            'note': 'Descriptive, one sleeper. Agreement with a wearable is not clinical accuracy.'}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--snapshot', type=Path, required=True)
    ap.add_argument('--side', choices=['left', 'right'], required=True)
    ap.add_argument('--export', type=Path, help='Apple Health ZIP or XML')
    ap.add_argument('--measurements', type=Path, help='Any device as normalized JSON; see reference_devices.NormalizedFile')
    ap.add_argument('--source', default='Watch')
    ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args()
    rows = AppleHealthExport(args.source).measurements(args.export) if args.export else []
    if args.measurements:
        rows += NormalizedFile().measurements(args.measurements)
    report = compare(args.snapshot, args.side, rows)
    args.out.write_text(json.dumps(report, indent=2))
    print(f'Compared {sum(m["readings"] for m in report["metrics"])} readings across {len(report["metrics"])} metric and device groups')


if __name__ == '__main__':
    main()
