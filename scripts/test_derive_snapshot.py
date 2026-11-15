"""Derived snapshots from replayed readings, on invented databases and readings only."""
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

import derive_snapshot as d

ROOT = Path(__file__).resolve().parents[1]
ENGINE, PROFILE, OTHER = 'e' * 64, 'f' * 64, '0' * 64
START = 1793000000  # 2026-10-26 07:33 UTC: the night of 2026-10-25 in Los Angeles.
TZ = 'America/Los_Angeles'


def pod_database(path):
    """The pod's vitals layout: ids, provenance column, a processing-runs table and the other side's rows."""
    db = sqlite3.connect(path)
    db.executescript('''
    CREATE TABLE sleep_records (id INTEGER PRIMARY KEY, side TEXT, entered_bed_at INTEGER, left_bed_at INTEGER);
    CREATE TABLE vitals (id INTEGER PRIMARY KEY AUTOINCREMENT, side TEXT NOT NULL, timestamp INTEGER NOT NULL, heart_rate REAL, hrv REAL,
                         breathing_rate REAL, processing_run_id TEXT);
    CREATE UNIQUE INDEX uq_vitals_side_timestamp ON vitals (side, timestamp);
    CREATE TABLE vitals_quality (id INTEGER PRIMARY KEY AUTOINCREMENT, vitals_id INTEGER NOT NULL, side TEXT NOT NULL, timestamp INTEGER NOT NULL,
                                 quality_score REAL NOT NULL, flags TEXT, hr_raw REAL, created_at INTEGER NOT NULL);
    CREATE TABLE biometric_processing_runs (id TEXT PRIMARY KEY, side TEXT NOT NULL, kind TEXT NOT NULL, bundle TEXT NOT NULL, config_hash TEXT NOT NULL,
                                            calibration_hash TEXT, input_identity TEXT NOT NULL, started_at_ms INTEGER NOT NULL, ended_at_ms INTEGER);
    CREATE TABLE movement (side TEXT, timestamp INTEGER, total_movement REAL);
    ''')
    db.execute('INSERT INTO sleep_records VALUES (1, "left", ?, ?)', (START, START + 600))
    db.execute('INSERT INTO sleep_records VALUES (2, "left", ?, ?)', (START + 900, START + 1200))
    for side in ('left', 'right'):
        for t in range(START - 120, START + 1320, 60):
            v = db.execute('INSERT INTO vitals (side, timestamp, heart_rate, hrv, breathing_rate) VALUES (?, ?, 60, 30, 14)', (side, t)).lastrowid
            db.execute('INSERT INTO vitals_quality (vitals_id, side, timestamp, quality_score, created_at) VALUES (?, ?, ?, 1, ?)', (v, side, t, t))
        db.execute('INSERT INTO movement VALUES (?, ?, 1)', (side, START))
    db.commit()
    db.close()


def reading(t, side='left', config=PROFILE, **changes):
    return {'side': side, 'timestampMs': t * 1000 + 250, 'heartRate': 70.5, 'hrvIndex': 40.0, 'hrvMetric': 'window_ibi_variability_v1',
            'breathingRate': 12.0, 'qualityScore': 0.7, 'flags': ['low_signal'], 'hrRaw': 71.0, 'sourceSide': side, 'configHash': config,
            'limitations': [], **changes}


def replay(directory, rows, night='2026-10-25', window=(START, START + 1200), skipped=(), parity=None):
    (directory / 'readings').mkdir(parents=True)
    name = f'readings/{night}-{PROFILE[:12]}.json'
    (directory / name).write_text(json.dumps({'cohortId': night, 'configHash': PROFILE, 'side': 'left', 'readings': rows}))
    (directory / 'replay.json').write_text(json.dumps({
        'version': 'biometrics-local-fit-replay-v1', 'side': 'left', 'timezone': TZ, 'engine': {'manifestHash': ENGINE},
        'config': {'source': 'defaults', 'overrides': [], 'configHash': PROFILE},
        'nights': [{'night': night, 'scoreStartMs': window[0] * 1000, 'scoreEndMs': window[1] * 1000, 'readingsFile': name,
                    **({'parity': {'gate': parity}} if parity else {})}],
        'skipped': [{'night': n, 'reason': 'the archive holds no RAW file written during the night'} for n in skipped]}))
    return directory


class DeriveSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / 'archive/biometrics.db'
        self.source.parent.mkdir()
        pod_database(self.source)
        (self.source.parent / 'pod-stages.json').write_text('{"sessions": {}}')
        self.cache = self.root / 'cache'

    def tearDown(self):
        self.tmp.cleanup()

    def readings(self):
        # One reading per minute of the window, one outside it, and the other side's default-profile readings.
        rows = [reading(t) for t in range(START, START + 1200, 60)] + [reading(START + 1260)]
        return rows + [reading(t, side='right', config=OTHER) for t in range(START, START + 1200, 60)]

    def derive(self, rows=None, **kwargs):
        d.ingest(replay(self.root / 'replay', self.readings() if rows is None else rows, **kwargs), self.cache)
        return d.derive(self.source, self.root / 'derived/biometrics.db', ENGINE, PROFILE, 'left', TZ, ['2026-10-25'], self.cache)

    def test_replaces_only_the_side_and_window(self):
        before = sqlite3.connect(self.source)
        result = self.derive()
        db = sqlite3.connect(result['out'])
        inside = db.execute('select heart_rate, processing_run_id from vitals where side="left" and timestamp>=? and timestamp<?', (START, START + 1200)).fetchall()
        self.assertEqual(len(inside), 20)
        self.assertEqual({r for r in inside}, {(70.5, f'derived-left-{ENGINE[:12]}-{PROFILE[:12]}')})
        outside = 'select side, timestamp, heart_rate from vitals where not (side="left" and timestamp>=? and timestamp<?) order by side, timestamp'
        self.assertEqual(db.execute(outside, (START, START + 1200)).fetchall(), before.execute(outside, (START, START + 1200)).fetchall())
        quality = db.execute('select v.timestamp, q.quality_score, q.flags, q.hr_raw from vitals_quality q join vitals v on v.id=q.vitals_id '
                             'where q.side="left" and q.timestamp>=? and q.timestamp<?', (START, START + 1200)).fetchall()
        self.assertEqual(len(quality), 20)
        self.assertTrue(all(q[1:] == (0.7, '["low_signal"]', 71.0) for q in quality))
        self.assertEqual(db.execute('select count(*) from vitals_quality').fetchone(), before.execute('select count(*) from vitals_quality').fetchone())
        row = d.replayed(db)['2026-10-25']
        self.assertEqual((row['source_sha256'], row['engine'], row['profile'], row['removed_vitals'], row['readings']),
                         (d.sha256(self.source), ENGINE, PROFILE, 20, 20))
        self.assertEqual(row['readings_sha256'], d.sha256(self.cache / f'2026-10-25-{ENGINE[:12]}-{PROFILE[:12]}.json'))
        self.assertEqual(row['replay_sha256'], d.sha256(self.root / 'replay/replay.json'))
        self.assertEqual(db.execute('select kind, config_hash from biometric_processing_runs').fetchall(), [('replay', PROFILE)])
        self.assertEqual(db.execute('select count(*) from movement').fetchone(), (2,))
        self.assertTrue((Path(result['out']).parent / 'pod-stages.json').is_file())
        self.assertEqual(d.replayed(before), {})

    def test_reader_keeps_core_fields_and_refuses_other_files(self):
        path = self.root / 'r.json'
        path.write_text(json.dumps({'cohortId': 'n', 'configHash': PROFILE, 'side': 'left', 'readings': [reading(START)]}))
        self.assertEqual(d.read_readings(path)['rows'][0]['flags'], ['low_signal'])
        bad = reading(START)
        del bad['qualityScore']
        path.write_text(json.dumps({'cohortId': 'n', 'configHash': PROFILE, 'side': 'left', 'readings': [bad]}))
        with self.assertRaises(ValueError):
            d.read_readings(path)
        path.write_text(json.dumps({'readings': []}))
        with self.assertRaises(ValueError):
            d.read_readings(path)

    def test_refuses_mixed_profiles_changed_windows_and_reruns(self):
        with self.assertRaisesRegex(ValueError, 'another profile'):
            self.derive(self.readings() + [reading(START + 30, config=OTHER)])
        self.tmp.cleanup()
        self.setUp()
        with self.assertRaisesRegex(ValueError, 'window'):
            self.derive(window=(START, START + 600))
        self.tmp.cleanup()
        self.setUp()
        result = self.derive()
        with self.assertRaises(ValueError):
            d.derive(self.source, result['out'], ENGINE, PROFILE, 'left', TZ, ['2026-10-25'], self.cache)
        with self.assertRaisesRegex(ValueError, 'already a derived'):
            d.derive(result['out'], self.root / 'again.db', ENGINE, PROFILE, 'left', TZ, ['2026-10-25'], self.cache)
        with self.assertRaisesRegex(ValueError, 'no cached readings'):
            d.derive(self.source, self.root / 'other.db', ENGINE, OTHER, 'left', TZ, ['2026-10-25'], self.cache)

    def test_a_night_failing_stored_parity_is_never_derived(self):
        with self.assertRaisesRegex(ValueError, 'parity gate'):
            self.derive(parity='fail')
        with self.assertRaisesRegex(ValueError, 'No night'):
            d.derive(self.source, self.root / 'all.db', ENGINE, PROFILE, 'left', TZ, None, self.cache)

    def test_cache_is_write_once_and_records_skipped_nights(self):
        d.ingest(replay(self.root / 'a', self.readings(), skipped=['2026-10-24']), self.cache)
        self.assertIn('no RAW', d.skipped(self.cache, '2026-10-24', ENGINE, PROFILE))
        self.assertIsNotNone(d.cached(self.cache, '2026-10-25', ENGINE, PROFILE))
        d.ingest(replay(self.root / 'b', self.readings()), self.cache)
        with self.assertRaisesRegex(ValueError, 'different readings'):
            d.ingest(replay(self.root / 'c', self.readings()[1:]), self.cache)

    def test_night_key_is_local_noon_to_noon(self):
        self.assertEqual(d.night_of(START, TZ), '2026-10-25')
        self.assertEqual(d.night_of(START + 12 * 3600, TZ), '2026-10-26')
        self.assertEqual(d.night_windows(sqlite3.connect(self.source), 'left', TZ), {'2026-10-25': (START, START + 1200)})


@unittest.skipUnless((ROOT / 'node_modules/.bin/tsx').exists(), 'needs npm ci')
class SyntheticPipelineTests(unittest.TestCase):
    def test_features_and_backtest_run_unchanged_on_a_derived_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            def run(*args):
                subprocess.run(args, cwd=ROOT, check=True, capture_output=True, text=True)
            run(sys.executable, 'scripts/make-synthetic-inputs.py', str(t / 'in'), '--nights', '10', '--replay', str(t / 'replay'))
            run(sys.executable, 'scripts/derive_snapshot.py', 'derive', str(t / 'in/biometrics.db'), '--replay', str(t / 'replay'),
                '--cache', str(t / 'cache'), '--out', str(t / 'derived/biometrics.db'))
            run(sys.executable, 'scripts/parse-watch-export.py', str(t / 'in/export.zip'), str(t / 'watch.json'))
            for name, db in (('live', t / 'in/biometrics.db'), ('replayed', t / 'derived/biometrics.db')):
                run(str(ROOT / 'node_modules/.bin/tsx'), 'scripts/export-runtime-features.ts', str(db), str(t / 'watch.json'), str(t / f'{name}.json'), 'left', 'UTC', 'podContext')
            live, replayed = (json.loads((t / f'{n}.json').read_text()) for n in ('live', 'replayed'))
            self.assertEqual([n['group'] for n in live['nights']], [n['group'] for n in replayed['nights']])
            self.assertNotEqual(live['nights'][0]['rows'][0]['x'], replayed['nights'][0]['rows'][0]['x'])
            run(sys.executable, 'scripts/stage-backtest.py', str(t / 'replayed.json'), '--snapshot', str(t / 'derived/biometrics.db'), '--out', str(t / 'backtest'))
            report = json.loads((t / 'backtest/report.json').read_text())
            self.assertEqual(report['protocol']['sources']['snapshot'], d.sha256(t / 'derived/biometrics.db'))
            self.assertEqual(len(report['schedules']['walk-forward']['nights']), 2)


if __name__ == '__main__':
    unittest.main()
