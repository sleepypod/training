import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from fetch_pod import Pod, iso
from reference_agreement import compare, pod_value, summarize
from reference_devices import METRICS, AppleHealthExport, NormalizedFile, measurement
import numpy as np

T = 1767333600000
DEVICE = '&lt;&lt;HKDevice: 0x1&gt;, name:Apple Watch, manufacturer:Apple Inc., model:Watch, hardware:Watch8,2, software:27.0&gt;'


def record(kind, value, source='Apple Watch', device=DEVICE, start='2026-01-01 22:00:00 -0800', end='2026-01-01 22:05:00 -0800', algorithm=None):
    entry = f'<MetadataEntry key="HKAlgorithmVersion" value="{algorithm}"/>' if algorithm else ''
    return (f'<Record type="HKQuantityTypeIdentifier{kind}" sourceName="{source}" device="{device}" unit="ms" value="{value}" '
            f'startDate="{start}" endDate="{end}">{entry}</Record>')


class AppleHealthTests(unittest.TestCase):
    def parse(self, xml, source='Watch'):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'export.xml'
            path.write_text('<HealthData>' + xml + '</HealthData>')
            return AppleHealthExport(source).measurements(path)

    def test_keeps_the_window_hardware_and_algorithm(self):
        self.assertEqual(self.parse(record('HeartRateVariabilityRMSSD', 19, algorithm='3')), [
            {'metric': 'hrv_rmssd', 'value': 19.0, 'startMs': T, 'endMs': T + 300000, 'device': 'Watch8,2', 'algorithm': '3'}])

    def test_blood_oxygen_becomes_a_percentage_and_a_spot_reading_has_no_end(self):
        row = self.parse(record('OxygenSaturation', 0.94, end='2026-01-01 22:00:00 -0800'))[0]
        self.assertEqual((row['value'], row['endMs']), (94.0, None))

    def test_a_watch_without_a_metric_simply_has_none(self):
        self.assertEqual({r['metric'] for r in self.parse(record('HeartRateVariabilitySDNN', 40))}, {'hrv_sdnn'})

    def test_leaves_out_other_sources_writeback_unknown_types_and_implausible_values(self):
        xml = (record('RespiratoryRate', 15, source='Phone') + record('RespiratoryRate', 15, source='sleepypod Watch')
               + record('StepCount', 12) + record('RespiratoryRate', 400))
        self.assertEqual(self.parse(xml), [])

    def test_a_record_with_no_device_stays_usable(self):
        self.assertIsNone(self.parse(record('RestingHeartRate', 52, device=''))[0]['device'])


class NormalizedFileTests(unittest.TestCase):
    def read(self, data):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'ring.json'
            path.write_text(json.dumps(data))
            return NormalizedFile().measurements(path)

    def test_a_device_with_only_some_metrics_needs_no_code(self):
        rows = self.read({'device': 'Ring 4', 'measurements': [{'metric': 'hrv_rmssd', 'value': 42, 'unit': 'ms', 'startMs': T, 'endMs': T + 300000}]})
        self.assertEqual(rows[0]['device'], 'Ring 4')

    def test_refuses_the_wrong_unit_or_an_unknown_metric(self):
        with self.assertRaisesRegex(ValueError, 'expected unit'):
            self.read({'measurements': [{'metric': 'blood_oxygen', 'value': 0.97, 'unit': 'fraction', 'startMs': T}]})
        with self.assertRaises(ValueError):
            self.read({'measurements': [{'metric': 'stress', 'value': 1, 'unit': None, 'startMs': T}]})

    def test_refuses_a_file_of_another_shape(self):
        with self.assertRaisesRegex(ValueError, 'Expected'):
            self.read([1, 2])


class AgreementTests(unittest.TestCase):
    def test_a_window_pairs_with_the_pod_mean_inside_it(self):
        times, values = np.array([T, T + 100000.0, T + 900000.0]), np.array([10.0, 20.0, 90.0])
        self.assertEqual(pod_value(times, values, {'startMs': T, 'endMs': T + 300000}), 15.0)

    def test_a_spot_reading_needs_a_pod_reading_nearby(self):
        times, values = np.array([float(T)]), np.array([10.0])
        self.assertEqual(pod_value(times, values, {'startMs': T + 60000, 'endMs': None}), 10.0)
        self.assertIsNone(pod_value(times, values, {'startMs': T + 600000, 'endMs': None}))

    def rows(self, metric, count, device='Watch8,2', algorithm=None):
        return [measurement(metric, 10 + i, T + i * 300000, None, device, algorithm) for i in range(count)]

    def vitals(self, count):
        times = np.array([T + i * 300000.0 for i in range(count)])
        return {'breathing_rate': (times, 10.0 + np.arange(count)), 'hrv': (times, 20.0 + 2 * np.arange(count))}

    def test_devices_and_algorithms_never_pool(self):
        rows = self.rows('hrv_sdnn', 12, 'Watch6,1', '2') + self.rows('hrv_sdnn', 12, 'Watch8,2', '3')
        result = summarize(rows, [(T, T + 86400000)], self.vitals(12))
        self.assertEqual([(m['device'], m['algorithm'], m['readings']) for m in result], [('Watch6,1', '2', 12), ('Watch8,2', '3', 12)])

    def test_hrv_is_labelled_a_different_quantity_and_gets_no_error_figure(self):
        entry = summarize(self.rows('hrv_rmssd', 12), [(T, T + 86400000)], self.vitals(12))[0]
        self.assertEqual(entry['comparison'], 'different quantity: trend only')
        self.assertNotIn('medianAbsoluteDifference', entry)

    def test_breathing_gets_an_error_figure(self):
        entry = summarize(self.rows('breathing_rate', 12), [(T, T + 86400000)], self.vitals(12))[0]
        self.assertEqual((entry['spearman'], entry['medianAbsoluteDifference']), (1.0, 0.0))

    def test_too_few_pairs_report_no_correlation(self):
        self.assertIsNone(summarize(self.rows('breathing_rate', 5), [(T, T + 86400000)], self.vitals(5))[0]['spearman'])

    def test_a_metric_with_no_pod_counterpart_reports_coverage_only(self):
        entry = summarize([measurement('blood_oxygen', 96, T, None, 'Watch8,2')], [(T, T + 86400000)], self.vitals(2))[0]
        self.assertEqual(entry['readings'], 1)
        self.assertNotIn('pairs', entry)

    def test_readings_outside_a_bed_session_are_ignored(self):
        self.assertEqual(summarize(self.rows('breathing_rate', 3), [(T + 86400000, T + 90000000)], self.vitals(3)), [])

    def test_compare_reads_an_older_snapshot_without_reference_samples(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'biometrics.db'
            with sqlite3.connect(path) as db:
                db.executescript('CREATE TABLE sleep_records(id,side,entered_bed_at,left_bed_at);CREATE TABLE vitals(side,timestamp,heart_rate,hrv,breathing_rate);')
                db.execute('INSERT INTO sleep_records VALUES (1,?,?,?)', ('left', T / 1000, T / 1000 + 28800))
            report = compare(path, 'left', self.rows('breathing_rate', 3))
            self.assertEqual(report['metrics'][0]['pairs'], 0)


class FetchTests(unittest.TestCase):
    def pod(self, directory):
        return Pod('pod', Path(directory) / 'responses')

    def test_a_pod_without_the_route_answers_none(self):
        with tempfile.TemporaryDirectory() as d:
            pod = self.pod(d)
            with patch.object(pod, 'get', side_effect=HTTPError('u', 404, 'gone', {}, None)):
                self.assertIsNone(pod.optional('biometrics/reference-capabilities'))
                self.assertEqual(pod.samples('hrv_rmssd', 'left', 0, 10), [])

    def test_a_server_failure_is_not_mistaken_for_an_older_pod(self):
        with tempfile.TemporaryDirectory() as d:
            pod = self.pod(d)
            with patch.object(pod, 'get', side_effect=HTTPError('u', 500, 'broken', {}, None)), self.assertRaises(HTTPError):
                pod.optional('biometrics/reference-capabilities')

    def test_a_full_page_bisects_so_nothing_is_dropped(self):
        with tempfile.TemporaryDirectory() as d:
            pod = self.pod(d)
            rows = [{'timestamp': iso(i * 1000), 'value': 40} for i in range(9)]
            def get(path, **q):
                from fetch_pod import millis
                a, b = millis(q['startDate']), millis(q['endDate'])
                return [r for r in rows if a <= millis(r['timestamp']) <= b][:q['limit']]
            with patch.object(pod, 'get', side_effect=get):
                self.assertEqual(len(pod.samples('hrv_sdnn', 'left', 0, 8000, 3)), 9)


class ContractTests(unittest.TestCase):
    def test_every_apple_type_maps_to_a_known_metric(self):
        self.assertTrue(all(metric in METRICS for metric, _ in AppleHealthExport.TYPES.values()))

    def test_features_never_read_wearable_measurements(self):
        source = (Path(__file__).parent / 'export-runtime-features.ts').read_text()
        self.assertNotIn('reference_samples', source)


if __name__ == '__main__': unittest.main()
