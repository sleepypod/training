"""Movement and snore research runs in the catalog, workspace payloads and outputs."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.request

import numpy as np

import dashboard
import movement as mv
import piezo_bandwidth as pb
import run_history
from run_history import Catalog, register_output, run_kind, task_entry
from test_movement import synthetic
from test_piezo_bandwidth import export_tarball, synthetic_session


def write_run(path, report, windows=None):
    path.mkdir(parents=True)
    (path / 'report.json').write_text(json.dumps(report))
    if windows is not None:
        (path / 'windows.json').write_text(json.dumps(windows))
    return path


SESSION_REPORT = {
    'kind': 'snore-session', 'generatedAt': '2026-10-22T10:00:00+00:00', 'outcome': 'both-bands',
    'source': {'url': 'http://192.168.1.88:3000/api/export/archive?startTs=1'}, 'occupantChannels': ['left1'],
    'steps': {'occupied_still': {'channels': {'left1': {'bandDb': {'16-30Hz': 84.8, '60-100Hz': 71.3}}}},
              'occupied_snore': {'channels': {'left1': {'bandDb': {'16-30Hz': 94.4, '60-100Hz': 98.8}}}}},
}


class CatalogKindTests(unittest.TestCase):
    def test_kinds_are_recognized_and_listed_with_summaries(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'data'
            write_run(root / 'move', {'kind': 'movement', 'generatedAt': '2026-10-22T09:00:00Z', 'windows': 40,
                                      'classifier': {'balancedAccuracy': 0.91}})
            write_run(root / 'gate', SESSION_REPORT)
            write_run(root / 'night', {'kind': 'snore-night', 'coverage': 1.0})
            write_run(root / 'old-gate', {'outcome': 'neither'})
            (root / 'sleep').mkdir()
            (root / 'sleep' / 'progress.json').write_text('{"status": "complete"}')
            catalog = Catalog(Path(d) / 'catalog.sqlite')
            self.assertEqual(catalog.discover([root, Path(d) / 'missing']), 4)
            rows = {r['name']: r for r in catalog.list()}
            self.assertEqual(set(rows), {'move', 'gate', 'night', 'sleep'})
            self.assertEqual(rows['move']['task'], 'movement')
            self.assertIn('balanced accuracy 0.91', rows['move']['summary'])
            self.assertEqual(rows['gate']['summary'], 'Bandwidth gate · both-bands')
            self.assertEqual(rows['night']['summary'], 'Night · 100% coverage')
            self.assertEqual(rows['sleep']['kind'], 'sleep')
            self.assertIsNone(run_kind(root / 'old-gate'))
            with self.assertRaises(ValueError):
                catalog.register(root / 'old-gate')

    def test_summaries_tolerate_missing_scores(self):
        with tempfile.TemporaryDirectory() as d:
            move = write_run(Path(d) / 'm', {'kind': 'movement', 'classifier': {}})
            night = write_run(Path(d) / 'n', {'kind': 'snore-night'})
            self.assertIn('balanced accuracy —', task_entry('k', move, 'movement', 1)['summary'])
            self.assertEqual(task_entry('k', night, 'snore-night', 1)['summary'], 'Night · coverage —')
            self.assertEqual(task_entry('k', night, 'snore-night', 1)['startedAt'], (night / 'report.json').stat().st_mtime)

    def test_register_output_only_inside_local_data_and_never_raises(self):
        with tempfile.TemporaryDirectory() as d:
            data = Path(d) / 'data'
            run = write_run(data / 'move', {'kind': 'movement'})
            outside = write_run(Path(d) / 'elsewhere', {'kind': 'movement'})
            with patch.object(run_history, 'local_data_roots', return_value=[data, Path(d) / 'missing']), \
                    patch.object(run_history, 'Catalog', lambda: Catalog(Path(d) / 'c.sqlite')):
                self.assertIsNotNone(register_output(run))
                self.assertIsNone(register_output(outside))
                self.assertIsNone(register_output(Path(d) / 'nope'))
            self.assertEqual(len(Catalog(Path(d) / 'c.sqlite').list()), 1)


class PayloadTests(unittest.TestCase):
    def test_movement_payload_strips_features_and_keeps_predictions(self):
        with tempfile.TemporaryDirectory() as d:
            run = write_run(Path(d) / 'm', {'kind': 'movement'},
                            [{'session': 's', 't': 1, 'label': 'arm', 'predicted': 'arm', 'baselineMoving': True,
                              'baseline': 300, 'x': [1, 2, 3], 'group': 'g'}])
            doc = dashboard.payload(run, 'key')
            self.assertEqual(doc['kind'], 'movement')
            self.assertEqual(doc['windows'], [{'session': 's', 't': 1, 'label': 'arm', 'predicted': 'arm',
                                               'baselineMoving': True, 'baseline': 300}])
            self.assertIsNone(doc['reference'])

    def test_night_reference_comes_from_the_latest_session_on_the_same_pod(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            catalog = Catalog(root / 'c.sqlite')
            catalog.register(write_run(root / 'gate-old', {**SESSION_REPORT, 'generatedAt': '2026-10-01T00:00:00Z', 'outcome': 'neither'}))
            catalog.register(write_run(root / 'gate', SESSION_REPORT))
            catalog.register(write_run(root / 'other-pod', {**SESSION_REPORT, 'source': {'url': 'http://10.0.0.2:3000/x'},
                                                            'generatedAt': '2026-10-30T00:00:00Z'}))
            catalog.register(write_run(root / 'no-steps', {**SESSION_REPORT, 'steps': {}, 'generatedAt': '2026-10-29T00:00:00Z'}))
            night = write_run(root / 'night', {'kind': 'snore-night', 'pod': 'http://192.168.1.88:3000'}, [])
            catalog.register(night)
            ref = dashboard.payload(night, 'k', catalog)['reference']
            self.assertEqual(ref['run'], 'gate')
            self.assertEqual(ref['still']['60-100Hz'], 71.3)
            self.assertEqual(ref['channel'], 'left1')
            unknown = write_run(root / 'night2', {'kind': 'snore-night'}, [])
            self.assertIsNone(dashboard.payload(unknown, 'k', catalog)['reference'])
            self.assertIsNone(dashboard.snore_reference({'pod': '192.168.1.88'}, None))
            self.assertEqual(dashboard.pod_host({'pod': '192.168.1.88'}), '192.168.1.88:3000')

    def test_workspace_serves_task_runs_and_their_offline_report(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            catalog = Catalog(root / 'c.sqlite')
            key = catalog.register(write_run(root / 'gate', SESSION_REPORT))
            server = dashboard.serve(None, catalog)
            try:
                base = f'http://127.0.0.1:{server.server_port}'
                runs = json.load(urllib.request.urlopen(base + '/api/runs'))
                self.assertEqual(runs[0]['task'], 'snore')
                doc = json.load(urllib.request.urlopen(base + '/api/runs/' + key))
                self.assertEqual(doc['kind'], 'snore-session')
                self.assertEqual(doc['state']['status'], 'complete')
                html = urllib.request.urlopen(base + '/api/runs/' + key + '/report').read().decode()
                self.assertIn('"kind": "snore-session"', html)
            finally:
                server.shutdown()
                server.server_close()
            dashboard.write_report(root / 'gate')
            self.assertIn('task-switch', (root / 'gate' / 'index.html').read_text())


class OutputTests(unittest.TestCase):
    def test_night_saves_30_second_windows_with_pump_and_registers(self):
        recs, steps = synthetic_session()
        start = steps[0]['startMs'] // 1000
        with tempfile.TemporaryDirectory() as d, patch.object(pb, 'register_output') as register:
            export = Path(d) / 'e.tar.gz'
            export.write_bytes(export_tarball(recs))
            with contextlib.redirect_stdout(io.StringIO()):
                pb.main(['night', '--start', str(start), '--end', str(start + 420), '--export', str(export),
                         '--pod', '192.168.1.88', '--out', str(Path(d) / 'n')])
            report = json.loads((Path(d) / 'n/report.json').read_text())
            windows = json.loads((Path(d) / 'n/windows.json').read_text())
            register.assert_called_once()
        self.assertEqual(report['kind'], 'snore-night')
        self.assertEqual(report['pod'], 'http://192.168.1.88:3000')
        self.assertNotIn('windows', report)
        self.assertEqual(len(windows), 14)
        self.assertEqual(windows[1]['t'] - windows[0]['t'], 30)
        self.assertEqual(set(windows[0]['bands']['left1']), {'16-30Hz', '60-100Hz', '1-10Hz'})
        pumped = [w for w in windows if w['pump']]
        self.assertTrue(pumped and all(w['pump'] == 2400 for w in pumped))

    def test_night_windows_record_missing_energy_as_null(self):
        recs, steps = synthetic_session()
        start = steps[0]['startMs'] // 1000
        report = pb.analyze_night([r for r in recs if r['ts'] < start + 60], [], start, start + 120)
        self.assertIsNone(report['windows'][-1]['bands']['left1']['60-100Hz'])
        self.assertIsNotNone(report['windows'][0]['bands']['left1']['60-100Hz'])

    def test_zero_energy_windows_are_missing_not_minus_infinity(self):
        recs, steps = synthetic_session()
        start = steps[0]['startMs'] // 1000
        flat = [{**r, 'left1': bytes(2000)} if r.get('type') == 'piezo-dual' and r['ts'] < start + 30 else r for r in recs]
        report = pb.analyze_night(flat, [], start, start + 3600)
        first = report['windows'][0]['bands']['left1']
        self.assertEqual(set(first.values()), {None})
        self.assertIsNotNone(report['windows'][1]['bands']['left1']['60-100Hz'])
        percentiles = report['hours'][0]['channels']['left1']['bandDbPercentiles']['60-100Hz']
        self.assertTrue(all(np.isfinite(percentiles)))
        json.dumps(report, allow_nan=False)

    def test_analyze_marks_snore_sessions(self):
        recs, steps = synthetic_session()
        with tempfile.TemporaryDirectory() as d, patch.object(pb, 'register_output') as register:
            export = Path(d) / 'e.tar.gz'
            export.write_bytes(export_tarball(recs))
            path = Path(d) / 's.json'
            path.write_text(json.dumps({'side': 'left', 'steps': steps}))
            with contextlib.redirect_stdout(io.StringIO()):
                pb.main(['analyze', '--steps', str(path), '--export', str(export), '--out', str(Path(d) / 'a')])
            report = json.loads((Path(d) / 'a/report.json').read_text())
            register.assert_called_once()
        self.assertEqual(report['kind'], 'snore-session')
        self.assertIsNone(report['pod'])

    def test_movement_windows_carry_out_of_fold_predictions(self):
        recs, session, movement = synthetic()
        rows, _ = mv.build_windows(recs, session, movement, 's1')
        mv.evaluate(rows)
        scored = [r for r in rows if r['baseline'] is not None]
        self.assertTrue(all(r['predicted'] in mv.CLASSES for r in scored))
        self.assertTrue(all(isinstance(r['baselineMoving'], bool) for r in scored))


if __name__ == '__main__':
    unittest.main()
