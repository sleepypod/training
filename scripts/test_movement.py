import contextlib
import http.server
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

import movement as mv
from test_piezo_bandwidth import export_tarball

START = 1_800_000_000


def synthetic(side='left', seed=0, start=START):
    """A protocol-shaped session with class-specific signatures on the occupant channel and capacitance."""
    rng = np.random.default_rng(seed)
    other = 'right' if side == 'left' else 'left'
    events, blocks, t = [], [], start
    plan = [('still', 1, 40), ('arm', 4, 12), ('leg', 4, 12), ('turn', 4, 15), ('sit', 3, 20), ('still', 1, 40)]
    for cls, count, every in plan:
        block_start = t
        for _ in range(count if cls != 'still' else 0):
            events.append({'class': cls, 'cueMs': t * 1000})
            t += every
        t = block_start + (every if cls == 'still' else count * every)
        blocks.append({'class': cls, 'startMs': block_start * 1000, 'endMs': t * 1000})
    end = t
    signature = {'arm': (25, 3000), 'leg': (6, 4000), 'turn': (2, 20000), 'sit': (1, 40000)}
    active = {}
    for e in events:
        for s in range(int(e['cueMs'] / 1000 + mv.REACTION_S), int(e['cueMs'] / 1000 + mv.REACTION_S + mv.EVENT_SECONDS[e['class']])):
            active[s] = e['class']
    recs, movement = [], []
    for s in range(start - 5, end + 5):
        n = np.arange(500) / 500 + s
        base = -500_000 + 2000 * np.sin(2 * np.pi * 1.1 * n)
        occ = base + rng.normal(0, 50, 500)
        oth = base + rng.normal(0, 50, 500)
        cls = active.get(s)
        if cls:
            freq, amp = signature[cls]
            occ = occ + amp * np.sin(2 * np.pi * freq * n) + (amp if cls in ('turn', 'sit') else 0) * (n - s)
            oth = oth + amp * 0.2 * np.sin(2 * np.pi * freq * n)
        recs.append({'type': 'piezo-dual', 'ts': s, 'freq': 500, f'{side}1': np.round(occ).astype('<i4').tobytes(),
                     f'{other}1': np.round(oth).astype('<i4').tobytes()})
        for k in range(2):
            bump = {'arm': 0.5, 'leg': 0.8, 'turn': 3.0, 'sit': 6.0}.get(cls, 0) * (k + 1)
            values = list(15 + rng.normal(0, 0.01, 8) + np.array([bump] * 6 + [0, 0]))
            recs.append({'type': 'capSense2', 'ts': s, side: {'values': values}, other: {'values': list(15 + rng.normal(0, 0.01, 8))}})
    for stamp in range(start + 60, end + 120, 60):
        busy = sum(1 for s in range(stamp - 60, stamp) if s in active)
        movement.append((stamp, 30 + 15 * busy))
    session = {'version': 'movement-session-v1', 'side': side, 'podClockOffsetMs': {'start': 0, 'end': 0},
               'blocks': blocks, 'events': events}
    return recs, session, movement


class LabelTests(unittest.TestCase):
    def test_windows_take_the_movement_they_half_cover_or_are_still_or_transition(self):
        events = [(100.0, 104.0, 'arm', 0)]
        self.assertEqual(mv.label_window(101, 103, events), ('arm', 0))
        self.assertEqual(mv.label_window(103, 105, events), ('arm', 0))
        self.assertEqual(mv.label_window(103.5, 105.5, events), (None, None))
        self.assertEqual(mv.label_window(110, 112, events), ('still', None))
        self.assertEqual(mv.label_window(94, 96, events), ('still', None))
        self.assertEqual(mv.label_window(96, 98, events), (None, None))

    def test_session_span_and_events_apply_the_clock_offset(self):
        session = {'podClockOffsetMs': {'start': 2000}, 'blocks': [{'startMs': 10_000, 'endMs': 20_000}],
                   'events': [{'class': 'turn', 'cueMs': 12_000}]}
        self.assertEqual(mv.session_span(session), (12.0, 22.0))
        self.assertEqual(mv.event_windows(session), [(15.0, 15.0 + mv.EVENT_SECONDS['turn'], 'turn', 0)])
        self.assertEqual(mv.offset_s({'podClockOffsetMs': None}), 0)

    def test_baseline_uses_the_minute_whose_epoch_contains_the_window(self):
        rows = [(1060.0, 5), (1120.0, 900)]
        self.assertEqual(mv.baseline_score(rows, 1059.9), 5)
        self.assertEqual(mv.baseline_score(rows, 1060.0), 900)
        self.assertIsNone(mv.baseline_score(rows, 2000))

    def test_threshold_maximizes_balanced_accuracy(self):
        scores = np.array([10, 20, 30, 300, 400, 500.0])
        moving = np.array([False, False, False, True, True, True])
        self.assertEqual(mv.best_threshold(scores, moving), 300)


class FeatureTests(unittest.TestCase):
    def test_piezo_slice_handles_sub_second_bounds_and_gaps(self):
        seconds = {s: {'left1': np.arange(500, dtype=float) + s * 500} for s in range(10, 14)}
        x = mv.piezo_slice(seconds, 'left1', 10.5, 12.5)
        self.assertEqual(len(x), 1000)
        self.assertEqual(x[0], 10 * 500 + 250)
        del seconds[12]
        self.assertIsNone(mv.piezo_slice(seconds, 'left1', 10.5, 12.5))

    def test_features_separate_a_burst_from_quiet(self):
        rng = np.random.default_rng(1)
        quiet = rng.normal(0, 50, 1000)
        burst = quiet + 3000 * np.sin(2 * np.pi * 25 * np.arange(1000) / 500)
        q, b = mv.piezo_features(quiet), mv.piezo_features(burst)
        self.assertEqual(len(q), len(mv.BANDS) + 3)
        self.assertGreater(b[2], q[2] + 2)  # 10-30 Hz band

    def test_capsense_series_spreads_two_records_per_second_and_skips_bad_ones(self):
        recs = [{'type': 'capSense2', 'ts': 5, 'left': {'values': [1] * 8}, 'right': {'values': [2] * 8}},
                {'type': 'capSense2', 'ts': 5, 'left': {'values': [3] * 8}, 'right': {'values': [2] * 8}},
                {'type': 'capSense2', 'ts': 'bad'}, {'type': 'capSense2', 'ts': 6, 'left': {'values': [1]}},
                {'type': 'log', 'ts': 6}]
        series = mv.capsense_series(recs)
        self.assertEqual(series['left'][0].tolist(), [5.0, 5.5])
        self.assertEqual(series['left'][1].shape, (2, 6))
        self.assertEqual(mv.capsense_features(series['left'], 5, 6), ([12.0, 12.0], True))
        self.assertEqual(mv.capsense_features(series['left'], 7, 8), ([0.0, 0.0], False))


class EvaluationTests(unittest.TestCase):
    def test_one_session_classifier_beats_the_minute_baseline_on_synthetic_movements(self):
        recs, session, movement = synthetic()
        rows, dropped = mv.build_windows(recs, session, movement, 's1')
        self.assertEqual(dropped, 0)
        self.assertEqual(len(rows[0]['x']), len(mv.FEATURE_NAMES))
        report = mv.evaluate(rows)
        self.assertIn('within one session', report['validation'])
        self.assertEqual(set(report['classCounts']), set(mv.CLASSES))
        self.assertGreater(report['classifier']['balancedAccuracy'], report['baseline']['balancedAccuracy'])
        self.assertGreater(report['classifier']['macroF1'], 0.5)

    def test_several_sessions_hold_out_whole_sessions_and_mirror_sides(self):
        rows = []
        for i, side in enumerate(('left', 'right', 'left')):
            recs, session, movement = synthetic(side=side, seed=i, start=START + i * 10_000)
            rows += mv.build_windows(recs, session, movement, f's{i}')[0]
        report = mv.evaluate(rows)
        self.assertEqual(report['validation'], 'leave-one-session-out')
        self.assertEqual(report['sessions'], ['s0', 's1', 's2'])
        self.assertEqual(len(report['baseline']['thresholds']), 3)
        self.assertGreater(report['classifier']['balancedAccuracy'], report['baseline']['balancedAccuracy'])

    def test_missing_data_and_baseline_are_counted_not_guessed(self):
        recs, session, movement = synthetic()
        holey = [r for r in recs if not (r['type'] == 'piezo-dual' and START + 50 <= r['ts'] < START + 60)]
        rows, dropped = mv.build_windows(holey, session, movement[2:], 's1')
        self.assertGreater(dropped, 0)
        report = mv.evaluate(rows)
        self.assertGreater(report['windowsWithoutBaseline'], 0)
        no_caps = [r for r in recs if r['type'] != 'capSense2']
        self.assertEqual(mv.build_windows(no_caps, session, movement, 's1')[0], [])

    def test_evaluation_refuses_a_single_class(self):
        rows = [{'session': 's', 'group': f'g{i}', 'label': 'still', 'x': [0.0], 'baseline': 1} for i in range(4)]
        with self.assertRaises(ValueError):
            mv.evaluate(rows)
        self.assertIsNone(mv.binary_metrics(np.array([False]), np.array([False]), np.array(['still']))['movingRecall'])


@contextlib.contextmanager
def stub_pod(tarball, movement_rows):
    requests = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            if self.path.startswith('/api/export/archive'):
                body = tarball
            elif self.path.startswith('/api/biometrics/movement'):
                body = json.dumps(movement_rows).encode()
            else:
                body = b'{}'
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f'127.0.0.1:{server.server_address[1]}', requests
    finally:
        server.shutdown()
        server.server_close()


class CommandTests(unittest.TestCase):
    def run_main(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            mv.main(list(argv))
        return out.getvalue()

    def test_session_records_a_cue_time_for_every_prompted_movement(self):
        clock = iter(range(0, 10_000_000, 1000))
        spoken = []
        with patch.object(mv.pb, 'optional_offset', return_value=-300):
            doc = mv.run_session('left', '127.0.0.1', 'print', sleep=lambda s: None, clock=lambda: next(clock),
                                 say=lambda text, cue: spoken.append(text))
        self.assertEqual(len(doc['events']), sum(count for cls, _, count, _ in mv.PROTOCOL if cls != 'still'))
        self.assertEqual([e['class'] for e in doc['events']][:5], ['arm'] * 5)
        self.assertEqual(doc['podClockOffsetMs'], {'start': -300, 'end': -300})
        self.assertEqual(len(doc['blocks']), len(mv.PROTOCOL))
        self.assertIn('Done. Thank you.', spoken)

    def test_session_command_writes_the_file_and_refuses_to_overwrite(self):
        with tempfile.TemporaryDirectory() as d, patch.object(mv, 'run_session', return_value={'events': [1, 2]}):
            path = Path(d) / 's.json'
            text = self.run_main('session', '--side', 'left', '--cue', 'print', '--out', str(path))
            self.assertIn('2 cued movements', text)
            with self.assertRaises(SystemExit):
                self.run_main('session', '--side', 'left', '--out', str(path))

    def test_speak_prints_when_say_is_unavailable(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), patch.object(mv.shutil, 'which', return_value=None):
            mv.speak('Turn over.', 'say')
        self.assertIn('Turn over.', out.getvalue())
        with patch.object(mv.shutil, 'which', return_value='/usr/bin/say'), patch.object(mv.subprocess, 'Popen') as popen:
            mv.speak('Turn over.', 'say')
        popen.assert_called_once()

    def test_train_fetches_export_and_movement_from_the_pod(self):
        recs, session, movement = synthetic()
        rows = [{'timestamp': mv.datetime.fromtimestamp(s, mv.timezone.utc).isoformat().replace('+00:00', 'Z'),
                 'totalMovement': m} for s, m in movement]
        with tempfile.TemporaryDirectory() as d, stub_pod(export_tarball(recs), rows) as (pod, requests):
            path = Path(d) / 's1.json'
            path.write_text(json.dumps(session))
            text = self.run_main('train', '--session', str(path), '--pod', pod, '--out', str(Path(d) / 'run'))
            report = json.loads((Path(d) / 'run/report.json').read_text())
            windows = json.loads((Path(d) / 'run/windows.json').read_text())
        self.assertTrue(any(r.startswith('/api/biometrics/movement?side=left') for r in requests))
        export_request = next(r for r in requests if r.startswith('/api/export/archive'))
        self.assertIn(f'endTs={int(mv.session_span(session)[1] + mv.ARCHIVE_DELAY_S)}', export_request)
        self.assertEqual(report['kind'], 'movement')
        self.assertEqual(report['sources'][0]['droppedWindows'], 0)
        self.assertEqual(len(windows), report['windows'])
        self.assertIn('pod score baseline', text)

    def test_train_reads_saved_exports_and_movement_without_a_pod(self):
        recs, session, movement = synthetic()
        with tempfile.TemporaryDirectory() as d:
            path, export, saved = Path(d) / 's1.json', Path(d) / 'e.tar.gz', Path(d) / 'm.json'
            path.write_text(json.dumps(session))
            export.write_bytes(export_tarball(recs))
            saved.write_text(json.dumps(movement))
            self.run_main('train', '--session', f'{path}={export}', '--movement', str(saved), '--out', str(Path(d) / 'run'))
            self.assertTrue((Path(d) / 'run/report.json').exists())
            with self.assertRaises(SystemExit):
                self.run_main('train', '--session', str(path), '--movement', str(saved), '--out', str(Path(d) / 'run2'))
            with self.assertRaisesRegex(SystemExit, '--pod, or --movement'):
                self.run_main('train', '--session', f'{path}={export}', '--out', str(Path(d) / 'run3'))
            self.assertFalse((Path(d) / 'run3').exists())

    def test_summary_handles_missing_rates(self):
        report = {'windows': 3, 'validation': 'x',
                  'baseline': {'movingRecall': None, 'stillFalseAlarm': 0.1},
                  'classifier': {'movingRecall': 0.9, 'stillFalseAlarm': None, 'macroF1': 0.5, 'recall': {'arm': 1.0}}}
        self.assertIn('n/a', '\n'.join(mv.summary_lines(report)))


if __name__ == '__main__':
    unittest.main()
