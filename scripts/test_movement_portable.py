"""Behavioral checks for the portable movement forest (movement-model-v1) on synthetic windows."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier

sys.path.insert(0, str(Path(__file__).resolve().parent))
import movement as mv
import movement_model as mm
import movement_portable as mp

ROOT = Path(__file__).resolve().parents[1]
TSX = ROOT / 'node_modules/.bin/tsx'
COLUMNS = mv.FEATURE_NAMES + ['podScore']


def windows(nights=('2026-11-01', '2026-11-02'), per_night=150, seed=0):
    """Invented windows: moving ones have more energy in the occupant bands and a higher pod score."""
    rng = np.random.default_rng(seed)
    rows = []
    for night in nights:
        for k in range(per_night):
            label = ('still', 'still', 'light', 'vigorous')[k % 4]
            lift = {'still': 0.0, 'light': 1.0, 'vigorous': 2.0}[label]
            x = rng.normal(size=len(mv.FEATURE_NAMES)) + np.r_[np.full(5, lift), np.zeros(len(mv.FEATURE_NAMES) - 5)]
            rows.append({'night': night, 't': 1_800_000_000.0 + 2 * k, 'label': label, 'watch': True,
                         'x': [float(v) for v in x], 'baseline': float(30 + 20 * lift + rng.normal(0, 10))})
    return rows


def fitted(rows):
    x, labels = mm.matrix(rows), np.asarray([r['label'] for r in rows])
    return mm.fit_matrix(x, labels), x, labels, np.asarray([r['night'] for r in rows])


class PortableMovementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = windows()
        cls.fit, cls.x, cls.labels, cls.nights = fitted(cls.rows)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.tmp.name) / 'movement.json'
        cls.result = mp.export(cls.fit, cls.x, cls.labels, mm.export_meta(cls.nights, cls.labels, 'test'), cls.path)
        cls.doc = json.loads(cls.path.read_text())

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_document_carries_the_contract_threshold_and_inference_cadence(self):
        doc = self.doc
        self.assertEqual((doc['formatVersion'], doc['contract'], doc['kind']), ('movement-model-v1', 'movementRaw-v1', 'forest'))
        self.assertEqual(doc['featureContract'], COLUMNS)
        self.assertEqual(len(doc['featureContract']), 21)
        self.assertEqual(doc['classes'], ['light', 'still', 'vigorous'])
        self.assertEqual(doc['strictThreshold'], self.fit['strictThreshold'])
        self.assertEqual(doc['threshold'], self.fit['threshold'])
        self.assertIn('P(moving) > strictThreshold', doc['thresholdRule'])
        self.assertEqual((doc['windowSeconds'], doc['hopSeconds'], doc['sampleRateHz']), (2.0, 2.0, 500))
        self.assertTrue(doc['version'].startswith('movementRaw-v1-'))
        self.assertEqual(len(doc['trees']), mm.MODEL['n_estimators'])
        self.assertEqual(doc['sha256'], mp.body_sha256(doc))

    def test_sha256_covers_the_body(self):
        tampered = json.loads(json.dumps(self.doc))
        tampered['strictThreshold'] += 1e-12
        self.assertNotEqual(mp.body_sha256(tampered), self.doc['sha256'])

    def test_vectors_cover_each_class_missing_pod_scores_and_both_sides_of_the_threshold(self):
        vectors = self.doc['testVectors']
        self.assertLessEqual(len(vectors), 64)
        self.assertTrue(any(v['inputs']['podScore'] is None for v in vectors))
        self.assertTrue(any(v['moving'] for v in vectors) and not all(v['moving'] for v in vectors))
        self.assertEqual({self.doc['classes'][int(np.argmax(v['probabilities']))] for v in vectors}, set(mm.CLASSES))
        strict = self.doc['strictThreshold']
        gaps = sorted(abs(1 - v['probabilities'][1] - strict) for v in vectors)
        self.assertGreater(gaps[0], mp.TOLERANCE)
        p = 1 - self.fit['model'].predict_proba(self.x)[:, 1]
        self.assertLessEqual(gaps[0], np.sort(np.abs(p - strict))[16])

    def test_evaluators_reproduce_sklearn_with_and_without_the_pod_score(self):
        clf = self.fit['model']
        for x in (self.x[:40], mp.without_pod_score(self.x[:40], COLUMNS)):
            expected = clf.predict_proba(x)
            np.testing.assert_allclose(mp.evaluate_many(self.doc, x), expected, rtol=0, atol=1e-12)
            for row, want in zip(x[:5], expected[:5]):
                inputs = {c: None if np.isnan(v) else float(v) for c, v in zip(COLUMNS, row)}
                np.testing.assert_allclose(mp.evaluate(self.doc, inputs), want, rtol=0, atol=1e-12)
        self.assertEqual(self.result['parityWindows'], len(self.x))
        self.assertLessEqual(max(self.result['maxAbsDifference'].values()), mp.TOLERANCE)

    def test_inputs_round_to_float32_before_comparing(self):
        clf = RandomForestClassifier(n_estimators=1, bootstrap=False, random_state=0).fit([[1.0], [2.0]], ['still', 'light'])
        doc = {'featureContract': ['a'], 'classes': ['light', 'still'], 'trees': mp.trees(clf)}
        x = 1.5 + 1e-9  # float32(x) == 1.5, the split; a float64 comparison would go right
        self.assertEqual(clf.estimators_[0].tree_.threshold[0], 1.5)
        self.assertEqual(mp.evaluate(doc, {'a': x}), list(clf.predict_proba([[x]])[0]))
        self.assertEqual(mp.evaluate(doc, {'a': x}), [0.0, 1.0])

    def test_export_is_refused_when_the_portable_forest_differs(self):
        trees = mp.trees

        def always_right(clf):
            out = trees(clf)
            for tree in out:
                tree['m'] = [0] * len(tree['m'])
            return out
        with tempfile.TemporaryDirectory() as tmp, patch.object(mp, 'trees', always_right):
            path = Path(tmp) / 'movement.json'
            with self.assertRaisesRegex(SystemExit, 'pod score missing'):
                mp.export(self.fit, self.x, self.labels, mm.export_meta(self.nights, self.labels, 'test'), path)
            self.assertFalse(path.exists())

    def test_canonical_json_matches_core_exact_decimals(self):
        self.assertEqual(mp.canonical(0.1), '0.1000000000000000055511151231257827021181583404541015625')
        self.assertEqual(mp.canonical({'b': [2.0, -0.0, True, None], 'a': 'x"y'}), '{"a":"x\\"y","b":[2,0,true,null]}')
        self.assertEqual(mp.canonical(1e21), '1000000000000000000000')
        with self.assertRaises(ValueError):
            mp.canonical(float('nan'))

    @unittest.skipUnless(TSX.exists(), 'npm ci not run')
    def test_typescript_reproduces_the_hash_and_every_vector(self):
        out = subprocess.run([str(TSX), str(ROOT / 'scripts/verify-movement.ts'), str(self.path)], capture_output=True, text=True, check=True)
        summary = json.loads(out.stdout)
        self.assertEqual((summary['sha256'], summary['failing'], summary['testVectors']), (self.doc['sha256'], 0, len(self.doc['testVectors'])))
        bad = json.loads(self.path.read_text())
        bad['testVectors'][0]['probabilities'][0] += 1e-6
        bad['sha256'] = mp.body_sha256(bad)
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'bad.json').write_text(json.dumps(bad))
            failed = subprocess.run([str(TSX), str(ROOT / 'scripts/verify-movement.ts'), str(Path(tmp) / 'bad.json')], capture_output=True, text=True)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn('Test vectors fail: 0', failed.stderr)


class MovementModelExportTests(unittest.TestCase):
    def test_train_writes_the_portable_forest(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'windows.json').write_text(json.dumps(windows(per_night=80)))
            with contextlib.redirect_stdout(io.StringIO()):
                mm.main(['train', '--windows', str(Path(tmp) / 'windows.json'), '--out', str(Path(tmp) / 'run')])
            doc = json.loads((Path(tmp) / 'run/model/movement.json').read_text())
            report = json.loads((Path(tmp) / 'run/report.json').read_text())
        self.assertEqual(doc['featureContract'], COLUMNS)
        self.assertEqual(report['portable']['sha256'], doc['sha256'])
        self.assertEqual(doc['training']['nights'], ['2026-11-01', '2026-11-02'])

    def test_export_reads_teaching_windows_or_the_windows_it_was_trained_on(self):
        rows = windows(per_night=60, seed=1)
        fit, x, labels, nights = fitted(rows)
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            joblib.dump({'final': fit, 'teaching': {'x': x, 'labels': labels, 'nights': nights}}, tmp / 'with.joblib')
            joblib.dump({'final': fit}, tmp / 'without.joblib')
            (tmp / 'windows.json').write_text(json.dumps(rows))
            with contextlib.redirect_stdout(io.StringIO()):
                mm.main(['export', str(tmp / 'with.joblib'), '--out', str(tmp / 'a.json')])
                mm.main(['export', str(tmp / 'without.joblib'), '--windows', str(tmp / 'windows.json'), '--out', str(tmp / 'b.json')])
            a, b = (json.loads((tmp / n).read_text()) for n in ('a.json', 'b.json'))
            self.assertEqual(a['trees'], b['trees'])
            # sklearn sums its trees across threads, so expected probabilities can differ in the last bit between runs.
            self.assertEqual([v['inputs'] for v in a['testVectors']], [v['inputs'] for v in b['testVectors']])
            np.testing.assert_allclose([v['probabilities'] for v in a['testVectors']], [v['probabilities'] for v in b['testVectors']], rtol=0, atol=1e-12)
            with self.assertRaisesRegex(SystemExit, 'exists'):
                mm.main(['export', str(tmp / 'with.joblib'), '--out', str(tmp / 'a.json')])
            with self.assertRaisesRegex(SystemExit, 'teaching windows'):
                mm.main(['export', str(tmp / 'without.joblib'), '--out', str(tmp / 'c.json')])


if __name__ == '__main__':
    unittest.main()
