import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timezone

from fetch_pod import Pod, base_url, fetch, millis, iso


class FetchTests(unittest.TestCase):
    def test_address_ports_ipv6_and_reject_credentials_or_paths(self):
        self.assertEqual(base_url('192.168.1.5'), 'http://192.168.1.5:3000')
        self.assertEqual(base_url('localhost:4321'), 'http://localhost:4321')
        self.assertEqual(base_url('::1'), 'http://[::1]:3000')
        for address in ['http://user:pass@pod', 'http://pod/path', 'file:///tmp/pod']:
            with self.assertRaises(ValueError): base_url(address)

    def test_saturated_range_recovers_rows_and_overlap_duplicates(self):
        with tempfile.TemporaryDirectory() as d:
            pod = Pod('pod', Path(d) / 'responses')
            rows = [{'id': i, 'side': 'left', 't': i} for i in range(9)]
            def get(path, **q):
                a, b = millis(q['startDate']), millis(q['endDate'])
                return [r for r in rows if a <= r['t'] <= b][:q['limit']]
            with patch.object(pod, 'get', side_effect=get):
                actual = pod.range('vitals', 'left', 0, 8, 3)
            self.assertEqual(sorted(r['id'] for r in actual), list(range(9)))
            with patch.object(pod, 'get', return_value=[{'id': 1, 'side': 'left'}]):
                with self.assertRaisesRegex(ValueError, 'one timestamp'):
                    pod.range('references', 'left', 0, 0, 1)

    def test_overlapping_reference_segments_deduplicate(self):
        with tempfile.TemporaryDirectory() as d:
            pod = Pod('pod', Path(d) / 'responses')
            long = {'id': 1, 'side': 'left'}
            def get(path, **q):
                return [long, {'id': 2, 'side': 'left'}, {'id': 3, 'side': 'left'}] if millis(q['endDate']) - millis(q['startDate']) == 10 else [long, {'id': 2 if millis(q['startDate']) == 0 else 3, 'side': 'left'}]
            with patch.object(pod, 'get', side_effect=get):
                self.assertEqual(sorted(r['id'] for r in pod.range('references', 'left', 0, 10, 3)), [1, 2, 3])

    def test_wrong_side_fails_instead_of_mixing_participants(self):
        with tempfile.TemporaryDirectory() as d:
            pod = Pod('pod', Path(d) / 'responses')
            with patch.object(pod, 'get', return_value=[{'id': 1, 'side': 'right'}]):
                with self.assertRaisesRegex(ValueError, 'wrong side'):
                    pod.range('vitals', 'left', 0, 10, 100)

    def test_fetch_materializes_only_closed_sessions_and_checks_configuration(self):
        now = datetime(2026, 1, 10, tzinfo=timezone.utc)
        start = int(now.timestamp() * 1000) - 86400000
        rules = {'outliers': {}, 'transitions': []}
        view = {'classifier': {'ruleset': rules}, 'provenance': {'generation': 1}}
        overview = {'stageView': view, 'sensorCalibration': [{'sensorType': 'piezo', 'qualityScore': .8}]}
        def get(path, **q):
            if path == 'settings': return {'device': {'timezone': 'UTC'}}
            if path.endswith('overview'): return overview
            if path.endswith('sleep-records'): return [
                {'id': 1, 'side': 'left', 'enteredBedAt': iso(start), 'leftBedAt': iso(start + 10800000)},
                {'id': 2, 'side': 'left', 'enteredBedAt': iso(start), 'leftBedAt': None}]
            if path.endswith('sleep-stages'): return {'sleepRecordId': 1, 'epochs': [{'start': start, 'duration': 300000, 'stage': 'light'}], 'stageView': view}
            if path.endswith('vitals'): return [{'id': 3, 'side': 'left', 'timestamp': iso(start), 'heartRate': 60, 'hrv': 25, 'breathingRate': 14}]
            if path.endswith('movement'): return [{'id': 4, 'side': 'left', 'timestamp': iso(start), 'totalMovement': 2}]
            if path.endswith('reference-stages'): return [{'id': 5, 'side': 'left', 'source': 'apple_watch', 'start': iso(start), 'end': iso(start + 300000), 'stage': 'light'}]
            raise AssertionError(path)
        with tempfile.TemporaryDirectory() as d, patch.object(Pod, 'get', side_effect=get):
            db, watch, tz = fetch('pod', 'left', 30, Path(d), now=now)
            import sqlite3
            with sqlite3.connect(db) as conn:
                self.assertEqual(conn.execute('select count(*) from sleep_records').fetchone()[0], 1)
                self.assertEqual(conn.execute('select heart_rate from vitals').fetchone()[0], 60)
            self.assertEqual(tz, 'UTC')
            self.assertEqual(json.loads(watch.read_text())['stages'][0][2], 'light')
            self.assertIn('not an atomic', json.loads((Path(d) / 'acquisition.json').read_text())['consistency'])
        with tempfile.TemporaryDirectory() as d:
            def wrong_record(path, **q):
                if path.endswith('sleep-stages'): return {'sleepRecordId': 999, 'epochs': []}
                return get(path, **q)
            with patch.object(Pod, 'get', side_effect=wrong_record), self.assertRaisesRegex(ValueError, 'requested sleep record'):
                fetch('pod', 'left', 30, Path(d), now=now)
        with tempfile.TemporaryDirectory() as d:
            calls = 0
            def changed(path, **q):
                nonlocal calls
                if path.endswith('overview'):
                    calls += 1
                    if calls == 2: return {**overview, 'stageView': {**view, 'provenance': {'generation': 2}}}
                return get(path, **q)
            with patch.object(Pod, 'get', side_effect=changed), self.assertRaisesRegex(ValueError, 'changed'):
                fetch('pod', 'left', 30, Path(d), now=now)


if __name__ == '__main__': unittest.main()
