import contextlib
import gzip
import http.server
import io
import json
import threading
import struct
import tarfile
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import numpy as np

import piezo_bandwidth as pb
import raw_piezo as rp


def cbor(value):
    def head(major, n):
        if n < 24:
            return bytes([major << 5 | n])
        for ai, size in ((24, 1), (25, 2), (26, 4), (27, 8)):
            if n < 1 << (8 * size):
                return bytes([major << 5 | ai]) + n.to_bytes(size, 'big')
    if isinstance(value, bool):
        return b'\xf5' if value else b'\xf4'
    if isinstance(value, int):
        return head(0, value) if value >= 0 else head(1, -1 - value)
    if isinstance(value, float):
        return b'\xfb' + struct.pack('>d', value)
    if isinstance(value, bytes):
        return head(2, len(value)) + value
    if isinstance(value, str):
        return head(3, len(value.encode())) + value.encode()
    if isinstance(value, dict):
        return head(5, len(value)) + b''.join(cbor(k) + cbor(v) for k, v in value.items())
    if isinstance(value, list):
        return head(4, len(value)) + b''.join(map(cbor, value))
    raise TypeError(value)


def outer(seq, data):
    return b'\xa2\x63seq\x1a' + struct.pack('>I', seq) + b'\x64data' + cbor(data)


def piezo(ts, left, right):
    return {'type': 'piezo-dual', 'ts': ts, 'freq': 500, 'adc': 65, 'gain': 400,
            'left1': np.asarray(left, '<i4').tobytes(), 'right1': np.asarray(right, '<i4').tobytes()}


def synthetic_session(low=True, high=True, knocks=True, start=1_800_000_000):
    """Seven 60 s steps of noise; hum/snore add tones on the left channel, knocks add impulses to both."""
    rng = np.random.default_rng(7)
    recs, steps, t = [], [], start
    for name, _ in pb.STEPS:
        steps.append({'name': name, 'startMs': t * 1000, 'endMs': (t + 60) * 1000})
        for second in range(60):
            n = np.arange(500) / 500 + second
            base = -500_000 + 2_000 * np.sin(2 * np.pi * 1.1 * n)
            left = base + rng.normal(0, 50, 500)
            right = base + rng.normal(0, 50, 500)
            if name in ('occupied_hum', 'occupied_snore'):
                left += (400 * np.sin(2 * np.pi * 22 * n) if low else 0) + (400 * np.sin(2 * np.pi * 80 * n) if high else 0)
            if knocks and name.startswith('knock') and second in (20, 22, 24):
                left[250:256] += 20_000
                right[250:256] += 15_000
            recs.append(piezo(t + second, left.round(), right.round()))
        recs.append({'type': 'frzHealth', 'ts': t + 30, 'left': {'pump': {'rpm': 2400 if name == 'empty_pump_on' else 0}}, 'right': {'pump': {'rpm': 0}}})
        t += 60
    return recs, steps


class RawPiezoTests(unittest.TestCase):
    def test_outer_and_inner_records_round_trip_including_empty_markers(self):
        rec = piezo(5, [1, 2, rp.LOST_SAMPLE], [4, 5, 6])
        raw = outer(1, cbor(rec) + cbor({'type': 'log', 'ts': 5, 'msg': 'x'})) + outer(2, b'') + outer(3, cbor(rec))[:-3]
        got = list(rp.records(raw))
        self.assertEqual([r['type'] for r, e in got if r], ['piezo-dual', 'log'])
        values = rp.samples(got[0][0]['left1'])
        self.assertTrue(np.isnan(values[2]))
        self.assertEqual(values[:2].tolist(), [1, 2])

    def test_indefinite_map_and_float_values_decode(self):
        payload = b'\xbf' + cbor('type') + cbor('frzHealth') + cbor('ts') + cbor(9) + cbor('x') + b'\xf9\x3c\x00' + b'\xff'
        (rec, error), = list(rp.records(outer(1, payload)))
        self.assertIsNone(error)
        self.assertEqual(rec, {'type': 'frzHealth', 'ts': 9, 'x': 1.0})

    def test_malformed_outer_record_is_reported_not_skipped_silently(self):
        (rec, error), = list(rp.records(b'\xa1junk'))
        self.assertIsNone(rec)
        self.assertIn('outer', error)

    def test_short_lost_runs_interpolate_and_long_runs_or_missing_seconds_split(self):
        seconds = {}
        for ts in range(4):
            seconds[ts] = {'left1': np.arange(500, dtype=float) + ts * 500}
        seconds[1]['left1'][10:13] = np.nan
        seconds[2]['left1'][100:120] = np.nan
        seconds[5] = {'left1': np.arange(500, dtype=float)}
        segs, interpolated, dropped = rp.segments(seconds, 'left1', 0, 6)
        self.assertEqual(interpolated, 3)
        self.assertEqual(dropped, 20)
        self.assertEqual([len(s) for s in segs], [1100, 880, 500])
        self.assertTrue(np.allclose(segs[0][510:513], [510, 511, 512]))

    def test_duplicate_seconds_keep_first_copy(self):
        seconds, duplicates = rp.piezo_seconds([piezo(1, [1] * 500, [1] * 500), piezo(1, [2] * 500, [2] * 500)])
        self.assertEqual(duplicates, 1)
        self.assertEqual(seconds[1]['left1'][0], 1)

    def test_export_tarball_reads_live_and_gzipped_files_and_skips_seqno(self):
        raw = outer(1, cbor(piezo(1, [1] * 500, [2] * 500)))
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'export.tar.gz'
            with tarfile.open(path, 'w:gz') as tar:
                for name, body in (('raw/A.RAW', raw), ('raw/B.RAW.gz', gzip.compress(raw)), ('raw/SEQNO.RAW', b'0001')):
                    info = tarfile.TarInfo(name)
                    info.size = len(body)
                    tar.addfile(info, io.BytesIO(body))
            files = list(rp.export_files(path))
        self.assertEqual([(n, r) for n, _, r in files], [('A.RAW', raw), ('B.RAW.gz', raw)])

    def test_inner_cbor_covers_every_supported_major_type(self):
        payload = (b'\xa9' + cbor('neg') + cbor(-5) + cbor('arr') + cbor([1, 'a']) + cbor('iarr') + b'\x9f\x01\x02\xff'
                   + cbor('ibytes') + b'\x5f\x42ab\x41c\xff' + cbor('itext') + b'\x7f\x61x\x61y\xff'
                   + cbor('tag') + b'\xc1\x1a\x00\x00\x00\x07' + cbor('f32') + b'\xfa\x3f\xc0\x00\x00'
                   + cbor('flags') + b'\x84\xf5\xf4\xf6' + cbor(2.5) + cbor('big') + b'\x1b' + (2 ** 40).to_bytes(8, 'big'))
        (rec, error), = list(rp.records(outer(1, payload)))
        self.assertIsNone(error)
        self.assertEqual(rec, {'neg': -5, 'arr': [1, 'a'], 'iarr': [1, 2], 'ibytes': b'abc', 'itext': 'xy',
                               'tag': 7, 'f32': 1.5, 'flags': [True, False, None, 2.5], 'big': 2 ** 40})

    def test_inner_decode_errors_are_reported(self):
        cases = {b'\xa1': 'Truncated', b'\x9f\x01': 'indefinite', b'\xf8\x20': 'simple', b'\x1c': 'length',
                 b'\x81' * 18 + b'\x01': 'deep'}
        for payload, message in cases.items():
            (rec, error), = [item for item in rp.records(outer(1, payload)) if item[1]]
            self.assertIsNone(rec)
            self.assertIn(message, error)

    def test_outer_record_contract_violations(self):
        good = outer(1, cbor({'type': 'x'}))
        for raw, message in ((b'\xa2\x63seq\x41', 'unsigned'), (good[:10] + b'\x64date' + good[15:], 'data key'),
                             (good[:15] + b'\x60', 'byte string'), (good[:15] + b'\x5a\xff\xff\xff\xff', 'large'),
                             (b'\xa2\x63seq\x1c', 'length')):
            (rec, error), = list(rp.records(raw))
            self.assertIn(message, error)
        for truncated in (b'\xa2\x63seq', b'\xa2\x63seq\x1a\x00', good[:16]):
            self.assertEqual(list(rp.records(truncated)), [])
        self.assertEqual(rp.samples(None).size, 0)
        self.assertEqual(rp.samples(b'\x01\x00\x00\x00\x02').tolist(), [1.0])

    def test_records_without_numeric_ts_and_empty_ranges_are_ignored(self):
        seconds, _ = rp.piezo_seconds([{'type': 'piezo-dual', 'ts': 'x'}, {'type': 'log', 'ts': 1}])
        self.assertEqual(seconds, {})
        self.assertEqual(rp.segments({}, 'left1', 5, 5), ([], 0, 0))
        all_lost = np.full(10, np.nan)
        self.assertTrue(np.isnan(rp.fill_short_gaps(all_lost)).all())


class BandwidthGateTests(unittest.TestCase):
    def test_tones_and_knocks_pass_both_bands(self):
        recs, steps = synthetic_session()
        report = pb.analyze_steps(recs, steps, 'left')
        self.assertEqual(report['outcome'], 'both-bands')
        self.assertTrue(report['knockPassed'])
        self.assertGreater(report['bands']['16-30Hz']['bestRiseDb'], 20)
        self.assertEqual(report['steps']['empty_pump_on']['pump']['maxRpm'], 2400)
        self.assertEqual(report['knockEvents']['knock_head'], {'left1': 3, 'right1': 3})

    def test_missing_high_band_reports_low_band_only(self):
        report = pb.analyze_steps(*synthetic_session(high=False), 'left')
        self.assertEqual(report['outcome'], 'low-band-only')
        self.assertLess(report['bands']['60-100Hz']['bestRiseDb'], pb.PASS_BAR['minRiseDb'])

    def test_wrong_side_and_no_knocks_fail(self):
        report = pb.analyze_steps(*synthetic_session(knocks=False), 'right')
        self.assertEqual(report['outcome'], 'neither')
        self.assertFalse(report['knockPassed'])

    def test_high_band_alone_and_missing_knock_step(self):
        recs, steps = synthetic_session(low=False)
        report = pb.analyze_steps(recs, [s for s in steps if s['name'] != 'knock_foot'], 'left')
        self.assertEqual(report['outcome'], 'high-band-only')
        self.assertEqual(set(report['knockEvents']), {'knock_head'})

    def test_short_or_missing_data_yields_no_spectrum_and_bad_spectra_raise(self):
        self.assertEqual(pb.psd([np.zeros(pb.NPERSEG - 1)]), (None, None, 0))
        recs, steps = synthetic_session()
        report = pb.analyze_steps([r for r in recs if r['ts'] < steps[1]['startMs'] // 1000], steps, 'left')
        self.assertEqual(report['steps']['occupied_hum']['channels']['left1']['welchWindows'], 0)
        self.assertEqual(report['outcome'], 'incomplete')
        with patch.object(pb, 'welch', return_value=(np.arange(3), np.array([1, np.nan, 1]))), self.assertRaisesRegex(ValueError, 'Non-finite'):
            pb.psd([np.zeros(pb.NPERSEG)])

    def test_corrupt_files_are_listed_as_decode_errors(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'export.tar.gz'
            path.write_bytes(export_tarball([{'type': 'log', 'ts': 1}]))
            with patch.object(pb.raw_piezo, 'records', return_value=iter([(None, 'Expected data key')])):
                recs, errors, files = pb.load_export(path)
        self.assertEqual(recs, [])
        self.assertEqual(errors, [{'file': '00000001.RAW.gz', 'error': 'Expected data key'}])
        self.assertEqual(files[0]['records'], 0)

    def test_clock_offset_without_date_header_is_unknown(self):
        class Response:
            headers = {}
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
        class Opener:
            def open(self, url, timeout):
                return Response()
        with patch.object(pb.urllib.request, 'build_opener', return_value=Opener()):
            self.assertIsNone(pb.pod_clock_offset_ms('127.0.0.1'))
        self.assertAlmostEqual(pb.now_ms() / 1000, pb.time.time(), delta=1)

    def test_missing_reference_step_is_incomplete_not_failed(self):
        recs, steps = synthetic_session()
        report = pb.analyze_steps(recs, [s for s in steps if s['name'] != 'occupied_still'], 'left')
        self.assertEqual(report['outcome'], 'incomplete')

    def test_night_reports_coverage_and_storage(self):
        recs, steps = synthetic_session()
        start = steps[0]['startMs'] // 1000
        report = pb.analyze_night(recs, [{'name': 'a.RAW.gz', 'compressedBytes': 60, 'rawBytes': 100}], start, start + 7200)
        self.assertAlmostEqual(report['coverage'], 420 / 7200)
        self.assertEqual(report['storage']['archiveGzipRatio'], 0.6)
        self.assertEqual(report['hours'][0]['piezoSeconds'], 420)
        self.assertEqual(report['hours'][1]['piezoSeconds'], 0)


def export_tarball(recs):
    """An /api/export/archive-shaped tarball holding one gzipped RAW file."""
    raw = b''.join(outer(i, cbor(rec)) for i, rec in enumerate(recs))
    body = gzip.compress(raw)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as tar:
        info = tarfile.TarInfo('./raw/00000001.RAW.gz')
        info.size = len(body)
        tar.addfile(info, io.BytesIO(body))
    return buffer.getvalue()


@contextlib.contextmanager
def fake_pod(tarball):
    """Local HTTP server answering the two GET routes the gate uses."""
    requests = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            body = tarball if self.path.startswith('/api/export/archive') else b'[]'
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'127.0.0.1:{server.server_address[1]}', requests
    finally:
        server.shutdown()
        server.server_close()


class CommandTests(unittest.TestCase):
    def run_main(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            pb.main(list(argv))
        return out.getvalue()

    def test_session_records_every_step_window_and_refuses_to_overwrite(self):
        clock = iter(range(0, 10_000_000, 30_000))
        with tempfile.TemporaryDirectory() as d, fake_pod(b'') as (pod, _), \
                patch('builtins.input', return_value=''), patch.object(pb.time, 'sleep'), \
                patch.object(pb, 'now_ms', side_effect=lambda: next(clock)):
            path = Path(d) / 'steps.json'
            text = self.run_main('session', '--side', 'left', '--pod', pod, '--out', str(path), '--step-seconds', '60')
            doc = json.loads(path.read_text())
            with self.assertRaises(SystemExit):
                self.run_main('session', '--side', 'left', '--out', str(path))
        self.assertEqual([s['name'] for s in doc['steps']], [name for name, _ in pb.STEPS])
        self.assertTrue(all(s['endMs'] - s['startMs'] >= 60_000 for s in doc['steps']))
        self.assertIsNotNone(doc['podClockOffsetMs']['start'])
        self.assertIn('Saved', text)

    def test_analyze_fetches_from_pod_and_reanalyses_a_saved_export(self):
        recs, steps = synthetic_session()
        tarball = export_tarball(recs)
        with tempfile.TemporaryDirectory() as d, fake_pod(tarball) as (pod, requests):
            steps_path = Path(d) / 'steps.json'
            steps_path.write_text(json.dumps({'side': 'left', 'steps': steps}))
            text = self.run_main('analyze', '--steps', str(steps_path), '--pod', pod, '--out', str(Path(d) / 'a'))
            report = json.loads((Path(d) / 'a/report.json').read_text())
            again = self.run_main('analyze', '--steps', str(steps_path), '--export', str(Path(d) / 'a/export.tar.gz'),
                                  '--out', str(Path(d) / 'b'))
            with self.assertRaises(SystemExit):
                self.run_main('analyze', '--steps', str(steps_path), '--out', str(Path(d) / 'c'))
            with self.assertRaises(SystemExit):
                self.run_main('analyze', '--steps', str(steps_path), '--export', str(Path(d) / 'a/export.tar.gz'), '--out', str(Path(d) / 'a'))
        self.assertIn('startTs=', requests[0])
        self.assertIn('include=raw', requests[0])
        self.assertEqual(report['outcome'], 'both-bands')
        self.assertEqual(report['source']['bytes'], len(tarball))
        self.assertIn('Outcome: both-bands', text)
        self.assertIn('Outcome: both-bands', again)

    def test_night_command_and_time_parsing(self):
        recs, steps = synthetic_session()
        start = steps[0]['startMs'] // 1000
        with tempfile.TemporaryDirectory() as d:
            export = Path(d) / 'export.tar.gz'
            export.write_bytes(export_tarball(recs))
            text = self.run_main('night', '--start', str(start), '--end', str(start + 420), '--export', str(export), '--out', str(Path(d) / 'n'))
            report = json.loads((Path(d) / 'n/report.json').read_text())
            with self.assertRaises(SystemExit):
                self.run_main('night', '--start', str(start), '--end', str(start), '--export', str(export), '--out', str(Path(d) / 'm'))
        self.assertEqual(report['coverage'], 1.0)
        self.assertIn('archive gzip ratio', text)
        self.assertEqual(pb.parse_time('2026-10-22T23:00-07:00'), int(datetime(2026, 10, 23, 6, tzinfo=timezone.utc).timestamp()))
        self.assertEqual(pb.parse_time('1793253600'), 1793253600)
        with self.assertRaises(SystemExit):
            pb.parse_time('2026-10-22T23:00')


if __name__ == '__main__':
    unittest.main()
