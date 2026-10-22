import gzip
import io
import struct
import tarfile
import tempfile
import unittest
from pathlib import Path

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


if __name__ == '__main__':
    unittest.main()
