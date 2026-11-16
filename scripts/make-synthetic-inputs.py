"""Generate deterministic, invented inputs for a pipeline smoke test; never real health data."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import xml.etree.ElementTree as ET
import zipfile


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('output', type=Path)
    ap.add_argument('--nights', type=int, default=5, help='Synthetic nights, one 3 h session each')
    ap.add_argument('--start', default='2024-01-01', help='Date of the first synthetic night (sessions start 22:00 UTC)')
    ap.add_argument('--watch-vitals', action='store_true', help='Also add Watch heart rate, respiratory rate and SDNN samples')
    ap.add_argument('--replay', type=Path, help='Also write a synthetic core local-fit replay output here (replay.json + readings/)')
    args = ap.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    db = sqlite3.connect(args.output / 'biometrics.db')
    db.executescript('''
    CREATE TABLE biometric_runtime_configs (side TEXT, generation INTEGER, payload TEXT);
    CREATE TABLE calibration_profiles (side TEXT, sensor_type TEXT, quality_score REAL);
    CREATE TABLE sleep_records (id INTEGER, side TEXT, entered_bed_at INTEGER, left_bed_at INTEGER);
    CREATE TABLE vitals (side TEXT, timestamp INTEGER, heart_rate REAL, hrv REAL, breathing_rate REAL);
    CREATE TABLE vitals_quality (vitals_id INTEGER, side TEXT, timestamp INTEGER, quality_score REAL, flags TEXT, hr_raw REAL, created_at INTEGER);
    CREATE TABLE movement (side TEXT, timestamp INTEGER, total_movement REAL);
    ''')
    first = int(datetime.fromisoformat(args.start).replace(hour=22, tzinfo=timezone.utc).timestamp())
    root = ET.Element('HealthData')
    stages = ['Awake', 'AsleepCore', 'AsleepDeep', 'AsleepREM']
    for night in range(args.nights):
        start = first + night * 86400  # Fixed synthetic dates, 3h/night.
        db.execute('INSERT INTO sleep_records VALUES (?, ?, ?, ?)', (night, 'left', start, start + 10800))
        for minute in range(180):
            stage = (minute // 15 + night) % 4
            row = db.execute('INSERT INTO vitals VALUES (?, ?, ?, ?, ?)',
                             ('left', start + minute * 60, 58 + stage * 4 + math.sin(minute), 20 + stage * 5, 13 + math.cos(minute / 7)))
            db.execute('INSERT INTO vitals_quality VALUES (?, ?, ?, ?, ?, ?, ?)', (row.lastrowid, 'left', start + minute * 60, 0.9, None, None, start + minute * 60))
            db.execute('INSERT INTO movement VALUES (?, ?, ?)', ('left', start + minute * 60, 1 + stage * 3))
        for epoch in range(36):
            stage = (epoch // 3 + night) % 4
            attrs = dict(type='HKCategoryTypeIdentifierSleepAnalysis', sourceName='Synthetic Apple Watch',
                         value='HKCategoryValueSleepAnalysis' + stages[stage],
                         startDate=datetime.fromtimestamp(start + epoch * 300, timezone.utc).isoformat(),
                         endDate=datetime.fromtimestamp(start + (epoch + 1) * 300, timezone.utc).isoformat())
            ET.SubElement(root, 'Record', attrs)
            if args.watch_vitals:
                at = datetime.fromtimestamp(start + epoch * 300 + 120, timezone.utc).isoformat()
                for kind, value in (('HeartRate', 57 + stage * 4.2), ('RespiratoryRate', 12.4 + stage * 0.3), ('HeartRateVariabilitySDNN', 35 + stage * 6)):
                    ET.SubElement(root, 'Record', dict(type='HKQuantityTypeIdentifier' + kind, sourceName='Synthetic Apple Watch',
                                                       value=f'{value + 0.1 * (epoch % 3):.1f}', startDate=at, endDate=at))
    db.commit(); db.close()
    with zipfile.ZipFile(args.output / 'export.zip', 'w') as archive:
        archive.writestr('apple_health_export/export.xml', ET.tostring(root))
    if args.replay:
        write_replay(args.replay, args.nights, first)
    print(f'Synthetic inputs created: {args.output}')


def write_replay(output, nights, first):
    """Invented readings in core's `biometrics:local-fit replay` layout, for derive_snapshot.py."""
    engine, profile, other = (hashlib.sha256(f'synthetic-{k}'.encode()).hexdigest() for k in ('engine', 'profile', 'defaults'))
    (output / 'readings').mkdir(parents=True, exist_ok=False)
    rows = []
    for night in range(nights):
        start = first + night * 86400
        key = datetime.fromtimestamp(start, timezone.utc).date().isoformat()
        readings = []
        for minute in range(180):
            stage = (minute // 15 + night) % 4
            for side, config in (('left', profile), ('right', other)):
                readings.append(dict(side=side, timestampMs=(start + minute * 60) * 1000 + 500, heartRate=57 + stage * 4.5 + math.sin(minute / 3),
                                     hrvIndex=22 + stage * 5, hrvMetric='window_ibi_variability_v1', breathingRate=12.5 + stage * 0.2,
                                     qualityScore=0.8, flags=['low_signal'] if minute % 50 == 0 else None, hrRaw=None,
                                     sourceSide=side, configHash=config, limitations=[]))
        name = f'readings/{key}-{profile[:12]}.json'
        (output / name).write_text(json.dumps(dict(cohortId=key, configHash=profile, side='left', readings=readings)) + '\n')
        # As if replayed with --compare-stored and passed by core's training gate.
        rows.append(dict(night=key, source='archive', scoreStartMs=start * 1000, scoreEndMs=(start + 10800) * 1000, readingsFile=name, readings=len(readings),
                         parity=dict(trainingGate='pass', comparedMinutes=180, heartRateCoverage=1.0, meanDifference=0.1,
                                     medianAbsDifference=0.3, shareWithinOneBpm=0.9)))
    (output / 'replay.json').write_text(json.dumps(dict(version='biometrics-local-fit-replay-v1', label='exploratory-local', side='left', timezone='UTC',
        engine=dict(manifestHash=engine), config=dict(source='synthetic', overrides=[], configHash=profile), otherSideConfigHash=other,
        nights=rows, skipped=[], parity=dict(training=dict(passedNights=[r['night'] for r in rows], failedNights=[]))), indent=2) + '\n')


if __name__ == '__main__':
    main()
