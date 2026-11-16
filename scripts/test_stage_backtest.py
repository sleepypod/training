"""Behavioral checks for the chronological stage backtest."""
from contextlib import redirect_stdout
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import numpy as np

spec = importlib.util.spec_from_file_location('stage_backtest', Path(__file__).with_name('stage-backtest.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

GROUPS = [f'2026-10-{d:02d}' for d in range(1, 13)]


class StageBacktestTests(unittest.TestCase):
    def test_walk_forward_trains_only_on_earlier_nights(self):
        plan = m.schedule(set(GROUPS), 'walk-forward', min_train=8)
        self.assertEqual([test for _, test in plan], GROUPS[8:])
        for train, test in plan:
            self.assertTrue(all(g < test for g in train))
            self.assertEqual(train, GROUPS[:GROUPS.index(test)])

    def test_frozen_keeps_one_training_window(self):
        plan = m.schedule(set(GROUPS), 'frozen', min_train=8)
        self.assertTrue(all(train == GROUPS[:8] for train, _ in plan))
        self.assertEqual(len(plan), 4)

    def test_too_few_nights_refused(self):
        with self.assertRaises(ValueError):
            m.schedule(set(GROUPS[:8]), 'walk-forward', min_train=8)

    def test_sign_test_ignores_ties(self):
        self.assertEqual(m.sign_test(np.array([1, 1, 1, 0, -1])), (3, 1, 0.625))
        self.assertEqual(m.sign_test(np.array([0, 0])), (0, 0, 1.0))

    def test_pod_live_uses_only_labeled_epochs_with_a_recorded_stage(self):
        night = {'rows': [{'y': 1, 'pod': 1}, {'y': None, 'pod': 2}, {'y': 3, 'pod': None}, {'y': 2, 'pod': 3}]}
        self.assertEqual(m.pod_live([night]), ([1, 2], [1, 3]))


    def test_locked_nights_are_dropped_before_any_feature_or_fit(self):
        nights = [{'id': i, 'group': g, 'rows': []} for i, g in enumerate(['2026-11-12', '2026-11-13', '2026-11-14', '2026-11-15'])]
        with tempfile.TemporaryDirectory() as d:
            snapshot = Path(d) / 'biometrics.db'
            snapshot.write_bytes(b'')
            features = Path(d) / 'features.json'
            features.write_text(json.dumps({'nights': nights, 'features': [], 'ruleset': {}, 'side': 'left',
                                            'sources': {'snapshot': hashlib.sha256(b'').hexdigest()}}))
            augmented, scored = [], []
            def augment(night, *_):
                augmented.append(night['group'])
                return []
            def run(kind, nights, *_):
                scored.extend(n['group'] for n in nights)
                return dict(nights=[], pooled={}, comparisons={})
            with patch.object(m.fa, 'augment', side_effect=augment), patch.object(m, 'run', side_effect=run), \
                    patch('sys.argv', ['x', str(features), '--snapshot', str(snapshot), '--out', str(Path(d) / 'out')]), \
                    redirect_stdout(io.StringIO()):
                m.main()
            report = json.loads((Path(d) / 'out/report.json').read_text())
        self.assertNotIn('2026-11-14', augmented + scored)
        self.assertEqual(len(augmented), 3)
        self.assertEqual(report['protocol']['holdout']['lockedGroups'], ['2026-11-14'])



STAGE_NAMES = ['wake', 'light', 'deep', 'rem']
EPOCHS = 12


def iso(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat().replace('+00:00', 'Z')


def truth(k):
    return (k // 3) % 4


class SyntheticPod:
    """Invented library, records, live stages and previews on 127.0.0.1; fails any write but the preview."""

    def __init__(self, live_model, starts, staged_by_rules=(), epochs=EPOCHS):
        self.requests, pod = [], self
        records = [dict(id=100 + i, side='left', enteredBedAt=iso(s), leftBedAt=iso(s + epochs * 300)) for i, s in enumerate(starts)]
        class Handler(BaseHTTPRequestHandler):
            def reply(self, result):
                raw = json.dumps(result).encode()
                self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(raw)
            def do_GET(self):
                url = urlsplit(self.path); q = {k: v[0] for k, v in parse_qs(url.query).items()}
                pod.requests.append(('GET', url.path))
                if url.path == '/api/sleep-stages/models':
                    return self.reply([dict(id=3, version='v3', kind='forest', uploadedAt=0, selected=True, active=True),
                                       dict(id=2, version='v2', kind='forest', uploadedAt=0, selected=False, active=False)])
                if url.path == '/api/sleep-stages/model':
                    return self.reply(dict(model=live_model, uploadedAt=0, active=True))
                if url.path == '/api/biometrics/sleep-records':
                    return self.reply(records)
                if url.path == '/api/biometrics/sleep-stages':
                    record = next(r for r in records if r['id'] == int(q['sleepRecordId']))
                    start = starts[record['id'] - 100]
                    rules = record['id'] in staged_by_rules
                    return self.reply(dict(sleepRecordId=record['id'], stagedBy=dict(engine='rules', version=None) if rules else dict(engine='model', version='v3'),
                                           epochs=[dict(start=(start + k * 300) * 1000, duration=300000, stage=STAGE_NAMES[truth(k)]) for k in range(epochs)]))
                self.send_error(404)
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                pod.requests.append(('POST', self.path))
                if self.path != '/api/sleep-stages/model/preview' or body['model']['version'] != 'v2' or body['side'] != 'left':
                    return self.send_error(400)
                record = next(r for r in records if r['id'] == body['sleepRecordId'])
                start = starts[record['id'] - 100]
                self.reply(dict(version='v2', pooled=None, rulesPooled=None, nights=[dict(
                    sleepRecordId=record['id'], enteredBedAt=start * 1000, leftBedAt=(start + epochs * 300) * 1000, agreement=None, rulesAgreement=None,
                    blocks=[dict(start=start * 1000, end=(start + 6 * 300) * 1000, stage='light'),
                            dict(start=(start + 6 * 300) * 1000, end=(start + epochs * 300) * 1000, stage=STAGE_NAMES[truth(9)])])]))
            def log_message(self, *args): pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        Thread(target=self.server.serve_forever, daemon=True).start()
        self.address = f'127.0.0.1:{self.server.server_port}'

    def close(self):
        self.server.shutdown(); self.server.server_close()


class TrialTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        groups = ['2026-11-13', '2026-11-14', '2026-11-15', '2026-11-16', '2026-11-17', '2026-11-18']
        self.starts = [int(datetime.fromisoformat(g + 'T23:00:00+00:00').timestamp()) for g in groups]
        nights = [dict(id=100 + i, group=g, start=s, end=s + EPOCHS * 300, rows=[dict(start=s + k * 300, x=[0.0], y=truth(k), rule=1, pod=None) for k in range(EPOCHS)])
                  for i, (g, s) in enumerate(zip(groups, self.starts))]
        nights[-1]['start'] += 60  # 11-18 matches no pod record
        self.run = self.root / 'run'
        self.run.mkdir()
        (self.run / 'features.json').write_text(json.dumps(dict(side='left', timeZone='UTC', features=['a'], sources={}, nights=nights)))
        baseline_run = self.root / 'baseline-run'
        (baseline_run / 'model').mkdir(parents=True)
        (baseline_run / 'features.json').write_text(json.dumps(dict(nights=[dict(group='2026-10-20'), dict(group='2026-11-01')])))
        self.baseline = baseline_run / 'model/candidate.json'
        self.baseline.write_text(json.dumps({'version': 'v2', 'kind': 'forest', 'metrics': {}}, indent=2))
        self.live = {'version': 'v3', 'kind': 'forest', 'metrics': {}}
        self.log = self.root / 'holdout-access.jsonl'
        self.out = self.root / 'trial'

    def tearDown(self):
        self.directory.cleanup()

    def score(self, pod):
        argv = ['x', '--trial', '--run', str(self.run), '--pod', pod.address, '--baseline-model', str(self.baseline),
                '--trained-through', '3=2026-11-13', '--out', str(self.out)]
        with patch.object(m.ho, 'access_log', return_value=self.log), patch('sys.argv', argv), redirect_stdout(io.StringIO()) as stdout:
            m.main()
        return stdout.getvalue()

    def test_scores_both_frozen_models_on_new_nights_and_logs_locked_reads(self):
        pod = SyntheticPod(self.live, self.starts, staged_by_rules={104})
        try:
            printed = self.score(pod)
            first = m.read_weekly(self.out / 'weekly.jsonl')
            self.score(pod)
        finally:
            pod.close()
        weekly = m.read_weekly(self.out / 'weekly.jsonl')
        self.assertEqual(len(weekly), len(first))
        self.assertEqual([(e['night'], e['modelId']) for e in weekly],
                         [(g, i) for g in ('2026-11-14', '2026-11-15', '2026-11-16') for i in (2, 3)])
        self.assertNotEqual({e['run'] for e in weekly}, {e['run'] for e in first})
        live = [e for e in weekly if e['modelId'] == 3]
        base = [e for e in weekly if e['modelId'] == 2]
        self.assertTrue(all(e['metrics']['macroF1'] == 1.0 and e['metrics']['epochs'] == EPOCHS for e in live))
        self.assertTrue(all(e['metrics']['macroF1'] < 1.0 for e in base))
        self.assertEqual({e['sha256'] for e in live}, {m.model_sha256(self.live)})
        self.assertEqual({e['sha256'] for e in base}, {m.model_sha256(json.loads(self.baseline.read_text()))})
        self.assertEqual({(e['modelId'], e['trainedThrough']) for e in weekly}, {(3, '2026-11-13'), (2, '2026-11-01')})
        self.assertTrue(all(e['outOfSample'] for e in weekly))
        self.assertEqual([e['night'] for e in live if e['locked']], ['2026-11-14'])
        reads = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertEqual(len(reads), 4)
        self.assertEqual({(r['purpose'], tuple(r['nights'])) for r in reads}, {('trial', ('2026-11-14', '2026-11-18'))})
        self.assertEqual({r['modelSha256'] for r in reads}, {live[0]['sha256'], base[0]['sha256']})
        self.assertEqual({method for method, path in pod.requests if path != '/api/sleep-stages/model/preview'}, {'GET'})
        self.assertIn('Skipped 2026-11-17 session 104', printed)
        self.assertIn('Skipped 2026-11-18 session 105: no pod sleep record', printed)
        self.assertIn('better on 3, worse on 0 of 3 nights', printed)
        report = sorted((self.out / 'scored').glob('*/trial.json'))[-1]
        summary = json.loads(report.read_text())['summary']
        self.assertEqual(summary['resamples'], 10_000)
        self.assertGreater(summary['comparisons']['macroF1']['bootstrap95Percent'][0], 0)

    def test_a_week_without_new_watch_nights_stops_cleanly(self):
        data = json.loads((self.run / 'features.json').read_text())
        data['nights'] = [n for n in data['nights'] if n['group'] <= '2026-11-13']
        (self.run / 'features.json').write_text(json.dumps(data))
        pod = SyntheticPod(self.live, self.starts)
        try:
            printed = self.score(pod)
        finally:
            pod.close()
        self.assertIn('No Watch-labelled night groups after 2026-11-13 (latest labelled night group: 2026-11-13); nothing to score', printed)
        self.assertEqual(pod.requests, [])
        self.assertFalse((self.out / 'weekly.jsonl').exists())
        self.assertFalse(self.log.exists())

    def test_refuses_when_the_live_model_is_not_selected_or_the_baseline_differs(self):
        pod = SyntheticPod({**self.live, 'version': 'other'}, self.starts)
        try:
            with self.assertRaisesRegex(ValueError, 'not the selected, active model'):
                self.score(pod)
            self.baseline.write_text(json.dumps({'version': 'v1'}))
            pod.close()
            pod = SyntheticPod(self.live, self.starts)
            with self.assertRaisesRegex(ValueError, 'library id 2 is v2'):
                self.score(pod)
        finally:
            pod.close()
        self.assertFalse(self.log.exists())

    @unittest.skipUnless((m.ROOT / 'node_modules/.bin/tsx').exists(), 'needs npm ci')
    def test_archive_snapshot_nights_go_through_the_production_exporter(self):
        snapshot = self.root / 'archive/pod/biometrics.db'
        snapshot.parent.mkdir(parents=True)
        starts = self.starts[1:3]
        with sqlite3.connect(snapshot) as db:
            db.executescript('''
            CREATE TABLE biometric_runtime_configs (side TEXT, generation INTEGER, payload TEXT);
            CREATE TABLE calibration_profiles (side TEXT, sensor_type TEXT, quality_score REAL);
            CREATE TABLE sleep_records (id INTEGER, side TEXT, entered_bed_at INTEGER, left_bed_at INTEGER);
            CREATE TABLE vitals (side TEXT, timestamp INTEGER, heart_rate REAL, hrv REAL, breathing_rate REAL);
            CREATE TABLE movement (side TEXT, timestamp INTEGER, total_movement REAL);
            CREATE TABLE reference_stages (id INTEGER, side TEXT, source TEXT, start INTEGER, end INTEGER, stage TEXT);
            ''')
            for i, start in enumerate(starts):
                # Archive ids can differ from the pod's; nights are matched by bed times.
                db.execute('INSERT INTO sleep_records VALUES (?, ?, ?, ?)', (i + 1, 'left', start, start + 36 * 300))
                for minute in range(180):
                    stage = truth(minute // 5)
                    db.execute('INSERT INTO vitals VALUES (?, ?, ?, ?, ?)', ('left', start + minute * 60, 58 + stage * 4 + minute % 3, 20 + stage * 5, 13 + minute % 2))
                    db.execute('INSERT INTO movement VALUES (?, ?, ?)', ('left', start + minute * 60, 1 + stage * 3))
                db.executemany('INSERT INTO reference_stages VALUES (?, ?, ?, ?, ?, ?)',
                               [(None, 'left', 'apple_watch', start + k * 300, start + (k + 1) * 300, STAGE_NAMES[truth(k)]) for k in range(36)])
        pod = SyntheticPod(self.live, starts, epochs=36)
        argv = ['x', '--trial', '--snapshot', str(snapshot), '--timezone', 'UTC', '--pod', pod.address, '--baseline-model', str(self.baseline),
                '--trained-through', '3=2026-11-13', '--out', str(self.out)]
        try:
            with patch.object(m.ho, 'access_log', return_value=self.log), patch('sys.argv', argv), redirect_stdout(io.StringIO()):
                m.main()
        finally:
            pod.close()
        weekly = m.read_weekly(self.out / 'weekly.jsonl')
        self.assertEqual([(e['night'], e['modelId'], e['sleepRecordIds']) for e in weekly],
                         [('2026-11-14', 2, [100]), ('2026-11-14', 3, [100]), ('2026-11-15', 2, [101]), ('2026-11-15', 3, [101])])
        self.assertTrue(all(e['metrics']['epochs'] == 36 for e in weekly))
        [report] = (self.out / 'scored').glob('*/trial.json')
        self.assertEqual(json.loads(report.read_text())['protocol']['snapshotSha256'], hashlib.sha256(snapshot.read_bytes()).hexdigest())

    def test_model_hash_is_cores_stage_model_sha256(self):
        # Golden value from core src/lib/tests/stage-model-set.test.ts (core PR #930).
        model = dict(version='test-forest', kind='forest', classes=['wake', 'light', 'deep', 'rem'],
                     featureContract=['hrZ', 'hrStdZ', 'motionZ', 'nightFraction'],
                     trees=[dict(f=[0, -1, -1], t=[-0.5, 0, 0], l=[1, -1, -1], r=[2, -1, -1], v=[[0, 0, 0, 0], [0, 1, 9, 0], [1, 8, 1, 0]])],
                     testVectors=[dict(inputs=dict(hrZ=-1, hrStdZ=0, motionZ=0, nightFraction=0.5), expected=2),
                                  dict(inputs=dict(hrZ=1, hrStdZ=0, motionZ=0, nightFraction=0.5), expected=1)],
                     metrics=dict(kappa=0.1, note='é'))
        self.assertEqual(m.model_sha256(model), '6fe42238f142207ca4016e4fe243a439e63e362043938fa15d4f09bdcf2c7906')
        self.assertEqual(m.model_sha256({**model, 'epochSeconds': 300.0}), m.model_sha256(model))
        self.assertEqual(m.canonical_json({'b': 1e-7, 'a': -0.0}), '{"a":0,"b":0.0000000999999999999999954748111825886258685613938723690807819366455078125}')

    def test_in_sample_nights_are_kept_but_not_compared(self):
        entries = [dict(side='left', night='2026-11-14', modelId=i, outOfSample=i == 2, locked=True,
                        metrics=dict(macroF1=.5, kappa=.4)) for i in (2, 3)]
        summary = m.trial_summary(entries, 'left', 3, 2, np.random.default_rng(42))
        self.assertEqual(summary['nights'], [])
        self.assertEqual(summary['excluded'], [dict(night='2026-11-14', reason='in sample for id 3')])

    def test_reading_watch_stages_from_an_archive_snapshot(self):
        snapshot = self.root / 'biometrics.db'
        with sqlite3.connect(snapshot) as db:
            db.execute('create table reference_stages(id integer, side text, source text, start integer, end integer, stage text)')
            db.executemany('insert into reference_stages values (?,?,?,?,?,?)', [(1, 'left', 'apple_watch', 600, 900, 'deep'), (2, 'right', 'apple_watch', 0, 300, 'rem'),
                                                                                (3, 'left', 'apple_watch', 0, 300, 'light')])
        self.assertEqual(m.watch_stages(snapshot, 'left'), {'stages': [[0, 300000, 'light'], [600000, 900000, 'deep']]})
        self.assertEqual(m.stage_at([(0, 300000, 'light'), (300000, 600000, 'rem')], 150), 1)
        self.assertIsNone(m.stage_at([(0, 300000, 'light'), (0, 300000, 'rem')], 150))
        self.assertEqual(m.training_cutoff({'metrics': {'trainedThrough': '2026-11-13'}}), ('2026-11-13', 'model metadata'))
        self.assertEqual(m.training_cutoff({}, self.baseline)[0], '2026-11-01')
        self.assertIsNone(m.training_cutoff({})[0])


if __name__ == '__main__':
    unittest.main()
