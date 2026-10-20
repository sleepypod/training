"""Generate deterministic, invented inputs for a pipeline smoke test; never real health data."""
import argparse
from datetime import datetime, timezone
import math
from pathlib import Path
import sqlite3
import xml.etree.ElementTree as ET
import zipfile


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('output', type=Path)
    args = ap.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    db = sqlite3.connect(args.output / 'biometrics.db')
    db.executescript('''
    CREATE TABLE biometric_runtime_configs (side TEXT, generation INTEGER, payload TEXT);
    CREATE TABLE calibration_profiles (side TEXT, sensor_type TEXT, quality_score REAL);
    CREATE TABLE sleep_records (id INTEGER, side TEXT, entered_bed_at INTEGER, left_bed_at INTEGER);
    CREATE TABLE vitals (side TEXT, timestamp INTEGER, heart_rate REAL, hrv REAL, breathing_rate REAL);
    CREATE TABLE movement (side TEXT, timestamp INTEGER, total_movement REAL);
    ''')
    root = ET.Element('HealthData')
    stages = ['Awake', 'AsleepCore', 'AsleepDeep', 'AsleepREM']
    for night in range(5):
        start = 1704146400 + night * 86400  # Fixed synthetic dates, 3h/night.
        db.execute('INSERT INTO sleep_records VALUES (?, ?, ?, ?)', (night, 'left', start, start + 10800))
        for minute in range(180):
            stage = (minute // 15 + night) % 4
            db.execute('INSERT INTO vitals VALUES (?, ?, ?, ?, ?)',
                       ('left', start + minute * 60, 58 + stage * 4 + math.sin(minute), 20 + stage * 5, 13 + math.cos(minute / 7)))
            db.execute('INSERT INTO movement VALUES (?, ?, ?)', ('left', start + minute * 60, 1 + stage * 3))
        for epoch in range(36):
            stage = (epoch // 3 + night) % 4
            attrs = dict(type='HKCategoryTypeIdentifierSleepAnalysis', sourceName='Synthetic Apple Watch',
                         value='HKCategoryValueSleepAnalysis' + stages[stage],
                         startDate=datetime.fromtimestamp(start + epoch * 300, timezone.utc).isoformat(),
                         endDate=datetime.fromtimestamp(start + (epoch + 1) * 300, timezone.utc).isoformat())
            ET.SubElement(root, 'Record', attrs)
    db.commit(); db.close()
    with zipfile.ZipFile(args.output / 'export.zip', 'w') as archive:
        archive.writestr('apple_health_export/export.xml', ET.tostring(root))
    print(f'Synthetic inputs created: {args.output}')


if __name__ == '__main__':
    main()
