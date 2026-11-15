"""Archive index, lineage, Watch sidecars and the archive agent installer on a synthetic archive layout."""
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import archive_index as ai
import lineage as ln
import watch_sidecar as ws
from test_piezo_bandwidth import cbor, outer

SCRIPT = Path(__file__).with_name('install-archive-agent.sh')
SCHEMA = '''
CREATE TABLE sleep_records (id INTEGER PRIMARY KEY, side TEXT NOT NULL, entered_bed_at INTEGER NOT NULL, left_bed_at INTEGER NOT NULL,
  sleep_duration_seconds INTEGER NOT NULL DEFAULT 0);
CREATE TABLE vitals (id INTEGER PRIMARY KEY, side TEXT NOT NULL, timestamp INTEGER NOT NULL, heart_rate REAL, hrv REAL,
  breathing_rate REAL, processing_run_id TEXT);
CREATE TABLE biometric_processing_runs (id TEXT PRIMARY KEY, side TEXT NOT NULL, kind TEXT NOT NULL, bundle TEXT NOT NULL,
  config_hash TEXT NOT NULL, calibration_hash TEXT, input_identity TEXT NOT NULL, started_at_ms INTEGER NOT NULL, ended_at_ms INTEGER);
CREATE TABLE biometric_runtime_configs (side TEXT NOT NULL, generation INTEGER NOT NULL, resolved_hash TEXT NOT NULL, payload TEXT NOT NULL,
  boundary TEXT NOT NULL, created_at_ms INTEGER NOT NULL);
CREATE TABLE biometric_runtime_receipts (side TEXT NOT NULL, component TEXT NOT NULL, generation INTEGER NOT NULL, applied_hash TEXT NOT NULL,
  effective_sample_time_ms INTEGER, state TEXT NOT NULL, error TEXT, heartbeat_at_ms INTEGER NOT NULL);
CREATE TABLE reference_imports (id TEXT PRIMARY KEY, deleted_at_ms INTEGER);
CREATE TABLE reference_stages (id INTEGER PRIMARY KEY, side TEXT NOT NULL, source TEXT NOT NULL, start INTEGER NOT NULL, "end" INTEGER NOT NULL,
  stage TEXT NOT NULL, created_at INTEGER NOT NULL DEFAULT 0, import_id TEXT);
CREATE TABLE reference_vitals (id INTEGER PRIMARY KEY, side TEXT NOT NULL, source TEXT NOT NULL, timestamp INTEGER NOT NULL,
  heart_rate REAL NOT NULL, created_at INTEGER NOT NULL DEFAULT 0, import_id TEXT);
CREATE TABLE reference_samples (id INTEGER PRIMARY KEY, side TEXT NOT NULL, source TEXT NOT NULL, metric TEXT NOT NULL, timestamp INTEGER NOT NULL,
  end_timestamp INTEGER, value REAL NOT NULL, created_at INTEGER NOT NULL DEFAULT 0, import_id TEXT);
CREATE TABLE watch_motion_captures (id INTEGER PRIMARY KEY, side TEXT NOT NULL, version TEXT NOT NULL, recorded_from INTEGER NOT NULL,
  recorded_to INTEGER NOT NULL, bins INTEGER NOT NULL, trimmed_bins INTEGER NOT NULL, sha256 TEXT NOT NULL, file TEXT NOT NULL);
CREATE TABLE archive_raw_files (sha256 TEXT PRIMARY KEY NOT NULL, name TEXT NOT NULL, archive_name TEXT NOT NULL UNIQUE,
  mtime_ms INTEGER NOT NULL, bytes INTEGER NOT NULL, pull_key TEXT NOT NULL);
'''
HOUR = 3600


def ts(value):
    return int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp())


# Five synthetic nights in UTC: local export, pruned, archived (locked), at risk, recent.
NIGHTS = {'2026-11-10': ts('2026-11-10T22:00'), '2026-11-12': ts('2026-11-12T22:00'), '2026-11-14': ts('2026-11-14T22:00'),
          '2026-11-15': ts('2026-11-15T22:00'), '2026-11-20': ts('2026-11-20T22:00')}


def raw_file(start, seconds):
    recs = [{'type': 'piezo-dual', 'ts': t, 'freq': 500, 'left1': b'\0' * 8} for t in range(start, start + seconds)]
    recs.insert(1, {'type': 'capSense2', 'ts': start})
    return gzip.compress(b''.join(outer(i, cbor(rec)) for i, rec in enumerate(recs)), mtime=0)


def tarball(path, files):
    with tarfile.open(path, 'w:gz') as tar:
        for name, body in files.items():
            info = tarfile.TarInfo(f'./raw/{name}')
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))


def synthetic_archive(root):
    """data/archive/<podId> as core's archiver lays it out, plus one pre-archive export."""
    pod = Path(root) / 'archive' / 'pod-1'
    (pod / 'raw').mkdir(parents=True)
    (pod / 'pulls').mkdir()
    db = sqlite3.connect(pod / 'biometrics.db')
    db.executescript(SCHEMA)
    for i, (night, start) in enumerate(NIGHTS.items(), 1):
        db.execute('insert into sleep_records (id, side, entered_bed_at, left_bed_at) values (?, ?, ?, ?)', (i, 'left', start, start + HOUR))
    # 2026-11-14 is archived in two files and spans two vitals profiles.
    archived = NIGHTS['2026-11-14']
    for name, start in (('0000000A.RAW.gz', archived - 60), ('0000000B.RAW.gz', archived + 1800)):
        body = raw_file(start, 1860 if name.endswith('A.RAW.gz') else 1800)
        sha = hashlib.sha256(body).hexdigest()
        (pod / 'raw' / f'{sha}.RAW.gz').write_bytes(body)
        db.execute('insert into archive_raw_files values (?, ?, ?, ?, ?, ?)', (sha, name, name, (start + 1800 + 900) * 1000, len(body), 'p'))
    # A daytime file far from any night is never decoded.
    db.execute('insert into archive_raw_files values (?, ?, ?, ?, ?, ?)', ('f' * 64, 'X.RAW.gz', 'X.RAW.gz', ts('2026-11-13T09:00') * 1000, 1, 'p'))
    for run, config, generation in (('run-a', 'prof-a', 1), ('run-b', 'prof-b', 2), ('run-c', 'prof-a', 1)):
        db.execute("insert into biometric_processing_runs values (?, 'left', 'live', ?, ?, null, '{}', 0, null)",
                   (run, json.dumps({'manifestHash': 'engine-1', 'generation': generation}), config))
    for minute in range(60):
        db.execute('insert into vitals (side, timestamp, heart_rate, processing_run_id) values (?, ?, ?, ?)',
                   ('left', archived + minute * 60, 60, 'run-a' if minute < 30 else 'run-b'))
        db.execute('insert into vitals (side, timestamp, heart_rate, processing_run_id) values (?, ?, ?, ?)',
                   ('left', NIGHTS['2026-11-10'] + minute * 60, 60, 'run-c'))
        db.execute('insert into vitals (side, timestamp, heart_rate) values (?, ?, ?)', ('left', NIGHTS['2026-11-15'] + minute * 60, 60))
    # 2026-11-15 has no processing runs: receipts say generation 2 took effect half way through.
    for generation, resolved in ((1, 'prof-a'), (2, 'prof-b')):
        db.execute("insert into biometric_runtime_configs values ('left', ?, ?, ?, 'immediate', 0)",
                   (generation, resolved, json.dumps({'manifestHash': 'engine-1', 'resolvedHash': resolved})))
        effective = (NIGHTS['2026-11-15'] - 86400 if generation == 1 else NIGHTS['2026-11-15'] + 1800) * 1000
        for component in ln.VITALS_COMPONENTS:
            db.execute("insert into biometric_runtime_receipts values ('left', ?, ?, ?, ?, 'applied', null, 0)", (component, generation, resolved, effective))
    db.execute("insert into reference_imports values ('live', null), ('gone', 1)")
    db.execute("insert into reference_stages (side, source, start, \"end\", stage, import_id) values ('left', 'apple_watch', ?, ?, 'light', 'live')",
               (archived, archived + 300))
    db.execute("insert into reference_stages (side, source, start, \"end\", stage, import_id) values ('left', 'apple_watch', ?, ?, 'deep', 'gone')",
               (NIGHTS['2026-11-12'], NIGHTS['2026-11-12'] + 300))
    db.execute("insert into reference_vitals (side, source, timestamp, heart_rate) values ('left', 'apple_watch', ?, 58)", (archived + 60,))
    db.execute("insert into reference_samples (side, source, metric, timestamp, value) values ('left', 'apple_watch', 'breathing_rate', ?, 14)", (archived + 60,))
    db.execute("insert into watch_motion_captures (side, version, recorded_from, recorded_to, bins, trimmed_bins, sha256, file) "
               "values ('left', 'watch-motion-v2', ?, ?, 1, 0, 'x', '{}')", ((archived - 600) * 1000, (archived + HOUR) * 1000))
    db.commit()
    db.close()
    pulled = '2026-11-16T12:30:00.000Z'
    window = {'requested': {}, 'rawSelection': {'mtimeFromMs': ts('2026-11-02T12:00') * 1000},
              'served': {'rawMtimeMinMs': ts('2026-11-11T08:00') * 1000}}
    (pod / 'pulls' / '2026-11-16T12-30-00.000Z.json').write_text(json.dumps({
        'formatVersion': 'biometrics-archive-pull-v1', 'pulledAt': pulled, 'podHost': 'pod:3000', 'cursor': {'endMs': 0, 'end': pulled},
        'manifest': {'build': {'commitHash': 'abc1234def', 'branch': 'main'}, 'window': window}}))
    export = Path(root) / 'raw' / 'session-1.tar.gz'
    export.parent.mkdir()
    tarball(export, {'00000001.RAW.gz': raw_file(NIGHTS['2026-11-10'], 1800)})
    storage = Path(root) / 'storage.json'
    storage.write_text(json.dumps({'rawHistory': {'oldest': '2026-11-13T00:00:00.000Z', 'fileCount': 9}}))
    return pod, export, storage


class ArchiveIndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.pod, self.export, self.storage = synthetic_archive(self.root)
        self.cache = self.root / 'cache'

    def tearDown(self):
        self.tmp.cleanup()

    def index(self, **kwargs):
        options = dict(exports=[self.export], storage=self.storage, cache_dir=self.cache, workers=1,
                       now_ms=ts('2026-11-16T13:00') * 1000, log=lambda line: None)
        return ai.build_index(self.pod, 'UTC', **{**options, **kwargs})

    def test_rows_carry_raw_coverage_source_teachers_lineage_lock_and_risk(self):
        nights = {n['night']: n for n in self.index()['nights']}
        self.assertEqual(sorted(nights), sorted(NIGHTS))
        local, lost, archived, risky, recent = (nights[k] for k in sorted(NIGHTS))
        self.assertEqual(local['raw']['coverage'], 0.5)
        self.assertTrue(local['raw']['source'].startswith('local-export:') and local['raw']['source'].endswith('session-1.tar.gz'))
        self.assertEqual((archived['raw']['coverage'], archived['raw']['source']), (1.0, 'archive'))
        self.assertEqual((lost['raw']['coverage'], lost['raw']['source'], lost['atRisk']), (0, 'lost', False))
        self.assertEqual((risky['raw']['source'], risky['atRisk']), ('pod-only', True))
        self.assertEqual((recent['raw']['source'], recent['atRisk']), ('pod-only', False))
        self.assertEqual([n['locked'] for n in (local, lost, archived, risky, recent)], [False, False, True, False, False])
        self.assertEqual({t: w['source'] for t, w in archived['watch'].items()},
                         dict(stages='pod', heartRate='pod', breathingRate='pod', hrvSdnn=None, motion='pod'))
        self.assertEqual(archived['watchMissing'], ['hrvSdnn'])
        self.assertIn('stages', lost['watchMissing'], 'rows of a deleted import are not a teacher')
        vitals = archived['lineage']['vitals']
        self.assertEqual((vitals['method'], vitals['mixed'], [v['minutes'] for v in vitals['versions']]), ('processing-runs', True, [30, 30]))
        self.assertEqual((local['lineage']['vitals']['profile'], local['lineage']['vitals']['mixed']), ('prof-a', False))
        receipts = risky['lineage']['vitals']
        self.assertEqual((receipts['method'], receipts['mixed']), ('runtime-receipts', True))
        self.assertEqual(archived['lineage']['build']['commit'], 'abc1234def')

    def test_at_risk_nights_print_first(self):
        lines = ai.table(self.index())
        self.assertTrue(lines[1].startswith('2026-11-15') and lines[1].rstrip().endswith('AT RISK'), lines[:3])

    def test_reruns_read_coverage_from_the_cache(self):
        first = self.index()
        self.assertTrue(any(self.cache.glob('exports/*.json')))
        with patch.object(ai, 'piezo_runs', side_effect=AssertionError('decoded again')):
            self.assertEqual(self.index()['nights'], first['nights'])

    def test_a_corrupt_archive_file_is_skipped_not_cached(self):
        for path in (self.pod / 'raw').glob('*.RAW.gz'):
            path.write_bytes(raw_file(0, 5))
        index = self.index()
        archived = next(n for n in index['nights'] if n['night'] == '2026-11-14')
        self.assertEqual(archived['raw']['source'], 'pod-only')
        self.assertEqual(index['archive']['missingRawFiles'], 2)

    def test_oldest_raw_without_storage_is_estimated_from_the_first_pull(self):
        history = self.index(storage=None)['rawHistory']
        self.assertEqual(history['basis'], 'pull-window-estimate')
        # Observed 2026-11-11T08:00 at the 11-16 12:30 pull, advanced by the 30 min since.
        self.assertEqual(history['oldest'], '2026-11-11T08:30:00+00:00')

    def test_cli_writes_the_index_beside_the_archive(self):
        with patch('builtins.print'):
            ai.main(['--archive', str(self.pod), '--timezone', 'UTC', '--local-exports-dir', str(self.root / 'raw'),
                     '--storage', str(self.storage), '--cache', str(self.cache), '--workers', '1'])
        index = json.loads((self.pod / 'index.json').read_text())
        self.assertEqual((index['version'], index['podId'], len(index['nights'])), (ai.VERSION, 'pod-1', 5))


class SidecarTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.pod, self.export, self.storage = synthetic_archive(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, data):
        path = self.root / name
        path.write_text(json.dumps(data))
        return path

    def test_import_splits_by_night_keeps_in_bed_samples_and_feeds_the_index(self):
        local = NIGHTS['2026-11-10'] * 1000
        stages = self.write('stages.json', {'sourceFilter': 'Watch', 'stages': [[local, local + 300_000, 'deep'], [ts('2026-11-12T10:00') * 1000, ts('2026-11-12T10:05') * 1000, 'light']]})
        vitals = self.write('vitals.json', {'heartRate': [[local + 60_000, local + 60_000, 55.0], [ts('2026-11-11T15:00') * 1000, ts('2026-11-11T15:00') * 1000, 90.0]],
                                            'respiratoryRate': [], 'hrvSdnn': [[local, local, 40.0]]})
        written = ws.import_sidecars(self.pod, 'left', 'UTC', stages, vitals)
        self.assertEqual(written, {'2026-11-10': {'stages': 1, 'heartRate': 1, 'hrvSdnn': 1}})
        index = ai.build_index(self.pod, 'UTC', cache_dir=self.root / 'cache', workers=1, log=lambda line: None)
        night = next(n for n in index['nights'] if n['night'] == '2026-11-10')
        self.assertTrue(night['sidecar'])
        self.assertEqual({t: w['source'] for t, w in night['watch'].items()},
                         dict(stages='sidecar', heartRate='sidecar', breathingRate=None, hrvSdnn='sidecar', motion=None))
        self.assertEqual(ai.watch_cell(night['watch']), 'sh.v.')

    def test_pod_rows_win_over_a_sidecar_and_reimport_replaces_one_type(self):
        archived = NIGHTS['2026-11-14'] * 1000
        ws.import_sidecars(self.pod, 'left', 'UTC', self.write('a.json', {'stages': [[archived, archived + 300_000, 'rem']]}),
                           self.write('v.json', {'heartRate': [[archived, archived, 50.0]]}))
        ws.import_sidecars(self.pod, 'left', 'UTC', self.write('b.json', {'stages': [[archived, archived + 600_000, 'wake']]}))
        doc = json.loads((self.pod / 'watch' / '2026-11-14.json').read_text())
        self.assertEqual(doc['sides']['left']['stages'], [[archived, archived + 600_000, 'wake']])
        self.assertEqual(doc['sides']['left']['heartRate'], [[archived, archived, 50.0]])
        index = ai.build_index(self.pod, 'UTC', cache_dir=self.root / 'cache', workers=1, log=lambda line: None)
        night = next(n for n in index['nights'] if n['night'] == '2026-11-14')
        self.assertEqual((night['watch']['stages']['source'], night['watch']['hrvSdnn']['source']), ('pod', None))
        self.assertFalse(night['sidecar'])

    def test_import_needs_parser_output(self):
        with self.assertRaises(SystemExit):
            ws.import_sidecars(self.pod, 'left', 'UTC', self.write('x.json', {'nope': []}))


class LineageCliTests(unittest.TestCase):
    def test_table_counts_mixed_nights_and_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            pod, _, _ = synthetic_archive(tmp)
            models = Path(tmp) / 'models.json'
            models.write_text(json.dumps([{'id': 3, 'version': 'research-x', 'selected': True, 'active': True}, {'id': 2, 'version': 'old', 'selected': False}]))
            with patch('builtins.print') as printed:
                ln.main([str(pod), '--timezone', 'UTC', '--stage-models', f'left={models}'])
            lines = [call.args[0] for call in printed.call_args_list]
            self.assertIn('research-x', lines[1])
            self.assertEqual(lines[-1], '5 nights, 2 mixed, 2 vitals versions')


class AgentInstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.core = self.root / 'core'
        (self.core / 'scripts/biometrics-archive').mkdir(parents=True)
        (self.core / 'node_modules').mkdir()
        (self.core / 'package.json').write_text(json.dumps({'scripts': {'biometrics:archive': 'tsx scripts/biometrics-archive/cli.ts'}}))
        (self.core / 'scripts/biometrics-archive/cli.ts').write_text("else if (arg === '--curl') values.curl = true\n")
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        (self.bin / 'pnpm').write_text('#!/bin/sh\nexit 0\n')
        (self.bin / 'pnpm').chmod(0o755)
        self.settings = self.root / 'settings.json'
        self.settings.write_text(json.dumps({'pod': '192.168.1.88:3000', 'coreCheckout': str(self.core)}))

    def tearDown(self):
        self.tmp.cleanup()

    def run_script(self, *args):
        node = subprocess.run(['sh', '-c', 'command -v node'], capture_output=True, text=True).stdout.strip()
        env = {**os.environ, 'HOME': str(self.root), 'PATH': f'{self.bin}:{Path(node).parent}:/usr/bin:/bin', 'NVM_DIR': str(self.root / 'nvm')}
        return subprocess.run(['bash', str(SCRIPT), '--settings', str(self.settings), '--dest', str(self.root / 'archive'), *args],
                              capture_output=True, text=True, env=env, timeout=30)

    def test_dry_run_prints_a_daily_curl_pull_into_the_archive(self):
        result = self.run_script('--dry-run')
        self.assertEqual(result.returncode, 0, result.stderr)
        plist = result.stdout
        self.assertIn('<string>com.sleepypod.training-archive</string>', plist)
        self.assertIn(f"cd '{self.core}' &amp;&amp; pnpm -s biometrics:archive pull --pod '192.168.1.88:3000' --dest '{self.root / 'archive'}' --curl", plist)
        self.assertIn('<key>Hour</key><integer>12</integer><key>Minute</key><integer>30</integer>', plist)
        self.assertIn(f"<string>{self.root / 'archive' / 'pull.log'}</string>", plist)
        node = subprocess.run(['sh', '-c', 'command -v node'], capture_output=True, text=True).stdout.strip()
        self.assertTrue(plist.split('<key>PATH</key><string>')[1].startswith(str(Path(node).parent)))
        self.assertFalse((self.root / 'Library').exists(), 'a dry run writes nothing')

    def test_refuses_a_core_checkout_without_the_curl_transport(self):
        (self.core / 'scripts/biometrics-archive/cli.ts').write_text('pull --pod HOST\n')
        result = self.run_script('--dry-run')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('--curl', result.stderr)

    def test_needs_a_pod(self):
        self.settings.write_text(json.dumps({'coreCheckout': str(self.core)}))
        result = self.run_script('--dry-run')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('no pod', result.stderr)


if __name__ == '__main__':
    unittest.main()
