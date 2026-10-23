"""Invented iOS-format captures only. No personal data or captured health files."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

import capture_common as cc
import watch_motion as wm
import snore_labels as sl
import snore_exit as se
import movement as mv
import dashboard
import run_history
from test_movement import synthetic
from test_piezo_bandwidth import export_tarball

T = 1_800_000_000_000


def watch():
    return {'version': 'watch-motion-v1', 'recordedFromMs': T, 'recordedToMs': T + 10_000,
            'timeZone': 'UTC', 'utcOffsetSeconds': 0, 'sampleRateHz': 50, 'binSeconds': 2,
            'jerkThresholdGPerSecond': 1, 'device': 'Synthetic Watch',
            'bins': {'startMs': [T, T + 2000, T + 6000, T + 8000], 'samples': [100, 100, 100, 20],
                     'meanMagnitude': [1, 1.1, 1.3, 1], 'magnitudeVariance': [0, .01, .1, 0],
                     'maxJerk': [0, 2, 5, 0], 'movingSamples': [0, 5, 30, 0]},
            'raw': [[T, 0, 0, 1], [T + 20, .1, 0, 1]]}


def snore():
    return {'schemaVersion': 'sleepypod-snore-capture-v1', 'captureId': '00000000-0000-4000-8000-000000000001',
            'source': 'iphone-microphone', 'device': 'Synthetic iPhone', 'osVersion': '26', 'appVersion': '1',
            'timeZone': 'UTC', 'units': 'unix-ms', 'startMs': T, 'endMs': T + 120_000, 'stopReason': 'user',
            'sampleRate': 48000, 'channelCount': 1, 'detectorVersion': sl.DETECTOR,
            'classifier': dict(sl.CLASSIFIER), 'coverage': [{'startMs': T, 'endMs': T + 40_000}, {'startMs': T + 50_000, 'endMs': T + 120_000}],
            'interruptions': [{'startMs': T + 40_000, 'endMs': T + 50_000, 'reason': 'call'}],
            'events': [{'captureId': '00000000-0000-4000-8000-000000000001', 'startMs': T + 10_000,
                        'endMs': T + 15_000, 'eventType': 'snore', 'confidence': .8,
                        'detectorVersion': sl.DETECTOR, 'labelOrigin': 'predicted'}],
            'knocks': [T + 1000, T + 2000, T + 3000, T + 117_000, T + 118_000, T + 119_000]}


def alignment():
    stamps = snore()['knocks']
    return {'version': 'capture-alignment-v1', 'confirmation': 'energy-fit',
            'pairs': [{'sourceMs': t, 'podMs': t + 400 + (t - T) * .00002} for t in stamps]}


class WatchTests(unittest.TestCase):
    def test_roundtrip_gaps_thresholds_and_partial_bins(self):
        doc = json.loads(json.dumps(watch()))
        self.assertEqual(wm.parse(doc), doc)
        result = wm.labels(doc)
        self.assertEqual([r['intensity'] for r in result['labels']], ['still', 'light', 'vigorous'])
        rows = result['labels']
        self.assertEqual(wm.label_window(T / 1000, T / 1000 + 2, rows), 'still')
        self.assertIsNone(wm.label_window(T / 1000 + 3, T / 1000 + 5, rows))
        self.assertIsNone(wm.label_window(T / 1000 + 8, T / 1000 + 10, rows))
        self.assertIn('Never', result['warning'])

    def test_versions_units_zone_columns_and_counts_refused(self):
        for key, value in [('version', 'watch-motion-v2'), ('binSeconds', 1), ('sampleRateHz', 100),
                           ('jerkThresholdGPerSecond', 2), ('utcOffsetSeconds', 3600), ('timeZone', 'Mars'),
                           ('recordedToMs', T - 1), ('device', ''), ('sampleRateHz', float('nan'))]:
            with self.subTest(key=key):
                doc = watch(); doc[key] = value
                with self.assertRaises(ValueError): wm.parse(doc)
        for key, value in [('startMs', [T, T]), ('samples', [True] * 4), ('movingSamples', [101] * 4),
                           ('maxJerk', [-1] * 4), ('startMs', [T + 1, T + 2000, T + 6000, T + 8000])]:
            doc = watch(); doc['bins'][key] = value
            with self.assertRaises(ValueError): wm.parse(doc)

    def test_dst_offset_is_checked_at_start_and_split_exports_share_night(self):
        doc = watch(); doc['timeZone'] = 'America/Los_Angeles'
        from datetime import datetime
        from zoneinfo import ZoneInfo
        doc['utcOffsetSeconds'] = int(datetime.fromtimestamp(T / 1000, ZoneInfo(doc['timeZone'])).utcoffset().total_seconds())
        wm.parse(doc)
        second = copy.deepcopy(doc); second['recordedFromMs'] += 2000
        self.assertEqual(wm.night_key(doc), wm.night_key(second))

    def test_import_deduplicates_and_refuses_outputs_outside_data(self):
        root = Path(__file__).resolve().parents[1] / 'data'
        root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as tmp:
            source = Path(tmp) / 'watch.json'; source.write_text(json.dumps(watch()))
            out = Path(tmp) / 'labels.json'
            wm.main([str(source), str(source), '--out', str(out)])
            self.assertEqual(len(json.loads(out.read_text())), 1)
            with self.assertRaises(ValueError): wm.main([str(source), '--out', str(out)])
        with self.assertRaises(ValueError): cc.data_path('/tmp/not-data.json')

    def test_watch_windows_hold_out_whole_nights_and_keep_gaps(self):
        rows = [{'x': [i % 2], 'label': 'light' if i % 2 else 'still', 'baseline': i % 2,
                 'session': f'file{i}', 'night': 'one', 'watch': True, 'group': f'g{i}'} for i in range(8)]
        with self.assertRaises(ValueError): mv.evaluate(rows)
        for r in rows[4:]: r['night'] = 'two'
        self.assertEqual(mv.evaluate(rows)['validation'], 'leave-one-night-out')
        recs, session, baseline = synthetic()
        session['watchLabels'] = wm.labels(watch())['labels']; session['night'] = 'one'
        windows, _ = mv.build_windows(recs, session, baseline, 'watch')
        self.assertTrue(windows)
        self.assertTrue(all(r['t'] < T / 1000 + 8 for r in windows))
        self.assertFalse(any(T / 1000 + 3 <= r['t'] < T / 1000 + 6 for r in windows))


class ClockTests(unittest.TestCase):
    def test_offset_and_drift_within_one_piezo_sample(self):
        doc = alignment(); clock = cc.load_alignment(doc)
        self.assertAlmostEqual(clock['driftPpm'], 20, places=3)
        for pair in doc['pairs']:
            self.assertLess(abs(cc.align(pair['sourceMs'], clock) - pair['podMs']), 2)
        automatic = cc.match_knocks(snore()['knocks'], [p['podMs'] for p in doc['pairs']], T, T + 120_000)
        self.assertLess(abs(cc.align(T + 50_000, automatic) - (T + 50_401)), 2)

    def test_missing_ambiguous_and_clock_jumps_refused(self):
        with self.assertRaises(ValueError): cc.clock_fit([])
        doc = alignment(); doc['pairs'][-1]['podMs'] += 1000
        with self.assertRaises(ValueError): cc.load_alignment(doc)
        doc = alignment(); doc['version'] = 'v2'
        with self.assertRaises(ValueError): cc.load_alignment(doc)
        with self.assertRaises(ValueError): cc.match_knocks([], [], T, T + 120_000)
        stamps = snore()['knocks']
        with self.assertRaises(ValueError): cc.match_knocks(stamps, sorted(stamps + [t + 500 for t in stamps]), T, T + 120_000)

    def test_injected_raw_impulses_detected(self):
        x = np.zeros(500); x[100] = 10000
        hits = cc.detect_knocks({T // 1000: {'freq': 500, 'left1': x}}, 'left1', T, T + 1000)
        self.assertEqual(hits, [T + 200])


class SnoreTests(unittest.TestCase):
    def test_roundtrip_all_fields_and_unknown_versions(self):
        doc = json.loads(json.dumps(snore()))
        self.assertEqual(sl.parse(doc), doc)
        for key, value in [('schemaVersion', 'v2'), ('detectorVersion', 'unknown'), ('units', 'seconds'),
                           ('endMs', None), ('source', 'watch'), ('stopReason', 'unknown'), ('timeZone', 'Mars')]:
            bad = copy.deepcopy(doc); bad[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): sl.parse(bad)
        for key, value in [('confidence', 2), ('labelOrigin', 'human'), ('captureId', 'other'), ('startMs', T + 45_000)]:
            bad = copy.deepcopy(doc); bad['events'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): sl.parse(bad)

    def test_alignment_transforms_events_coverage_interruptions_and_knocks(self):
        doc = snore(); clock = cc.load_alignment(alignment())
        result = sl.align_session(doc, clock)
        for key in ['events', 'coverage', 'interruptions']:
            self.assertAlmostEqual(result[key][0]['startMs'], cc.align(doc[key][0]['startMs'], clock))
        self.assertEqual(result['knocks'], [cc.align(t, clock) for t in doc['knocks']])
        self.assertEqual(doc, snore())

    def test_import_without_window_confidence_is_inconclusive_and_cataloged(self):
        recs, _, _ = synthetic()
        report = sl.import_session(snore(), recs, [], 'left')
        self.assertEqual(report['kind'], 'snore-labels')
        self.assertEqual(report['exitTest']['result'], 'inconclusive')
        self.assertIn('Missing calibrated per-window confidences', report['exitTest']['perNight'][0]['reason'])
        self.assertFalse(report['clock']['synchronized'])
        self.assertAlmostEqual(report['phoneCoverage'], 110 / 120)
        self.assertEqual(report['capture']['coverage'], snore()['coverage'])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'run'; root.mkdir()
            (root / 'report.json').write_text(json.dumps(report))
            (root / 'windows.json').write_text(json.dumps(report.pop('windows')))
            catalog = run_history.Catalog(Path(tmp) / 'catalog.sqlite')
            key = catalog.register(root)
            self.assertEqual(catalog.list()[0]['task'], 'snore')
            payload = dashboard.payload(root, key)
            self.assertEqual(payload['kind'], 'snore-labels')
            self.assertEqual(payload['taskReport']['capture']['interruptions'], snore()['interruptions'])


class ExitTests(unittest.TestCase):
    def nights(self, positive=10, negative=0):
        return [{'night': str(n), 'occupants': 1, 'synchronized': True, 'calibrationId': 'synthetic-preregistered',
                 'windowConfidencesAvailable': True, 'threshold': .6,
                 'windows': [{'startMs': T + i * 60_000 + cls * 30_000, 'score': positive if cls else negative,
                              'confidence': .9 if cls else .1, 'covered': True, 'occupied': True, 'excluded': False}
                             for i in range(5) for cls in range(2)]} for n in range(5)]

    def test_pass_fail_and_inconclusive(self):
        self.assertEqual(se.evaluate(self.nights())['result'], 'pass')
        self.assertEqual(se.evaluate(self.nights(0, 10))['result'], 'fail')
        self.assertEqual(se.evaluate(self.nights()[:4])['result'], 'inconclusive')
        self.assertEqual(se.verdict(.75, .45, .9), 'inconclusive')
        self.assertEqual(se.verdict(.7, .5, .8), 'inconclusive')
        self.assertEqual(se.verdict(.6, .4, .7), 'inconclusive')

    def test_bouts_guard_band_exclusions_and_per_night_minimum(self):
        nights = self.nights()
        nights[0]['occupants'] = 2
        self.assertEqual(se.evaluate(nights)['result'], 'inconclusive')
        night = self.nights()[0]
        for w in night['windows']:
            if w['confidence'] < .6: w['confidence'] = .3  # boundary is excluded
        self.assertIsNone(se.night_units(night)[0])
        night = self.nights()[0]
        for w in night['windows']: w['excluded'] = True
        self.assertIsNone(se.night_units(night)[0])
        self.assertEqual(se.bouts([{'startMs': 0, 'score': 0}, {'startMs': 29_999, 'score': 2}, {'startMs': 60_000, 'score': 5}]), [1, 5])
        nights = self.nights()
        for night in nights[2:]: night['windows'] = night['windows'][:4]
        self.assertEqual(se.evaluate(nights)['result'], 'inconclusive')
        with self.assertRaises(ValueError): se.evaluate([nights[0], nights[0]])

class EvidenceTests(unittest.TestCase):
    def evidence(self):
        return {'version': 'snore-window-evidence-v1', 'captureId': snore()['captureId'],
                'registeredBeforeCaptureMs': T - 1000, 'calibrationId': 'synthetic', 'threshold': .6,
                'passingBands': ['16-30Hz', '60-100Hz'], 'primaryChannel': 'left1', 'occupants': 1,
                'occupied': [{'startMs': T, 'endMs': T + 120_000}],
                'pumpOff': [{'startMs': T, 'endMs': T + 120_000}], 'pumpActive': [],
                'movement': [{'startMs': T + 70_000, 'endMs': T + 71_000}],
                'windows': [{'startMs': T + i * 3000, 'endMs': T + (i + 1) * 3000,
                             'confidence': .8 if i % 3 else .1} for i in range(40)]}

    def test_scores_use_preregistered_band_and_exclude_guard_and_gaps(self):
        recs, _, _ = synthetic()
        seconds, _ = sl.rp.piezo_seconds(recs)
        result = sl.context_windows(snore(), seconds, 'left1', {'synchronized': True}, self.evidence())
        self.assertEqual(result['bandHz'], (16, 30))
        self.assertIsNotNone(result['normalizationDb'])
        self.assertFalse(result['windows'][14]['covered'])  # phone interruption
        self.assertTrue(result['windows'][20]['excluded'])  # 10 s before movement
        self.assertTrue(result['windows'][26]['excluded'])  # 10 s after movement
        self.assertFalse(result['windows'][28]['excluded'])
        evidence = self.evidence(); evidence['registeredBeforeCaptureMs'] = T
        with self.assertRaises(ValueError): sl.context_windows(snore(), seconds, 'left1', {'synchronized': True}, evidence)
        evidence = self.evidence(); evidence['windows'][0]['endMs'] -= 1
        with self.assertRaises(ValueError): sl.context_windows(snore(), seconds, 'left1', {'synchronized': True}, evidence)

    def test_import_cli_roundtrip_and_aggregate_exit(self):
        root = Path(__file__).resolve().parents[1] / 'data'; root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as tmp, patch.object(sl, 'register_output') as register:
            tmp = Path(tmp)
            source = tmp / 'snore.json'; source.write_text(json.dumps(snore()))
            raw = tmp / 'raw.tar.gz'; raw.write_bytes(export_tarball(synthetic()[0]))
            out = tmp / 'run'
            sl.main(['--session', str(source), '--export', str(raw), '--side', 'left', '--out', str(out)])
            report = json.loads((out / 'report.json').read_text())
            self.assertEqual(report['source']['decodeErrors'], 0)
            self.assertEqual(report['capture']['events'], snore()['events'])
            self.assertTrue(json.loads((out / 'windows.json').read_text()))
            register.assert_called_once_with(out)
            combined = tmp / 'exit'
            sl.main(['--reports', str(out / 'report.json'), '--out', str(combined)])
            self.assertEqual(json.loads((combined / 'report.json').read_text())['exitTest']['result'], 'inconclusive')

class WatchCommandTests(unittest.TestCase):
    def test_train_watch_nights_from_saved_exports(self):
        root = Path(__file__).resolve().parents[1] / 'data'; root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as tmp, patch.object(mv, 'register_output') as register:
            tmp = Path(tmp); argv = ['train', '--side', 'left']; baseline = []
            for index in range(2):
                shift = index * 86_400_000
                doc = watch(); doc.pop('raw')
                doc['recordedFromMs'] += shift; doc['recordedToMs'] += shift
                doc['bins']['startMs'] = [t + shift for t in doc['bins']['startMs']]
                source = tmp / f'watch{index}.json'; source.write_text(json.dumps(doc))
                recs, _, scores = synthetic(start=(T + shift) // 1000)
                recs = [r for r in recs if (T + shift) // 1000 <= r['ts'] < (T + shift) // 1000 + 10]
                export = tmp / f'raw{index}.tar.gz'; export.write_bytes(export_tarball(recs))
                baseline.extend(scores)
                argv += ['--watch-motion', f'{source}={export}']
            scores = tmp / 'scores.json'; scores.write_text(json.dumps(baseline))
            out = tmp / 'run'
            mv.main(argv + ['--movement', str(scores), '--out', str(out)])
            report = json.loads((out / 'report.json').read_text())
            self.assertEqual(report['validation'], 'leave-one-night-out')
            self.assertEqual(len(report['sources']), 2)
            self.assertFalse(report['sources'][0]['watchProvenance']['clock']['synchronized'])
            register.assert_called_once_with(out)


class CaptureBoundaryTests(unittest.TestCase):
    def test_ios_merged_event_may_span_gap_but_gap_remains_uncovered(self):
        doc = snore()
        doc['events'][0].update(startMs=T + 39_000, endMs=T + 51_000)
        self.assertEqual(sl.parse(doc)['coverage'], snore()['coverage'])
        self.assertFalse(cc.covered(T + 40_000, T + 43_000, [(r['startMs'], r['endMs']) for r in doc['coverage']]))

    def test_recovered_interruption_can_close_at_its_start(self):
        doc = snore(); doc['stopReason'] = 'app not running'
        doc['endMs'] = T + 40_000; doc['coverage'] = doc['coverage'][:1]
        doc['interruptions'][0]['endMs'] = T + 40_000
        doc['knocks'] = doc['knocks'][:3]
        sl.parse(doc)
