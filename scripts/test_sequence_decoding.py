"""Behavioral checks for the retrospective sequence-decoding ablation."""
import importlib.util
from contextlib import redirect_stdout
import io
import itertools
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

spec = importlib.util.spec_from_file_location('sequence_decoding', Path(__file__).with_name('sequence-decoding.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def session(labels, start=0, step=300):
    return {'group': str(start), 'rows': [{'start': start + i * step, 'x': [0], 'y': y} for i, y in enumerate(labels)]}


class SequenceDecodingTests(unittest.TestCase):
    def test_viterbi_matches_brute_force(self):
        rng = np.random.default_rng(7)
        emissions = np.log(rng.dirichlet(np.ones(3), size=5))
        transitions = np.log(rng.dirichlet(np.ones(3), size=3))
        initial = np.log(rng.dirichlet(np.ones(3)))
        def total(path):
            return initial[path[0]] + sum(emissions[t, s] for t, s in enumerate(path)) + sum(transitions[a, b] for a, b in zip(path, path[1:]))
        best = max(itertools.product(range(3), repeat=5), key=total)
        self.assertEqual(m.viterbi(emissions, transitions, initial), list(best))

    def test_transitions_count_only_consecutive_labeled_epochs(self):
        gapped = {'group': 'g', 'rows': [{'start': 0, 'x': [0], 'y': 2}, {'start': 900, 'x': [0], 'y': 3}]}
        matrix = m.transitions([session([1, 1, 2, 2, 1, None, 3]), gapped], alpha=1)
        self.assertAlmostEqual(matrix[1, 2], 2 / 6)
        self.assertAlmostEqual(matrix[2, 3], 1 / 6)
        np.testing.assert_allclose(matrix.sum(axis=1), 1)

    def test_decoding_never_sees_its_own_night_in_training(self):
        class Model:
            classes_ = np.array([0, 1, 2, 3])
            def predict(self, xs):
                return np.ones(len(xs), dtype=int)
            def predict_proba(self, xs):
                return np.tile([.1, .5, .2, .2], (len(xs), 1))
        nights = [{**session([1, 2, 1, 3, 1, 0], start=g * 100_000), 'group': f'2026-01-0{g}', 'id': str(g), 'end': 0, 'watchEpochs': 6}
                  for g in range(1, 7)]
        rules = {'smoothIsolatedEpochs': True, 'transitions': []}
        calls = []
        original = m.decode
        def spy(name, clf, night, ruleset, params, train):
            calls.append((name, night['group'], {n['group'] for n in train}))
            return original(name, clf, night, ruleset, params, train)
        with tempfile.TemporaryDirectory() as root:
            features = Path(root) / 'features.json'
            features.write_text(json.dumps({'nights': nights, 'ruleset': rules, 'sources': {}}))
            with patch.object(m.rm, 'fit', lambda name, train: Model()), patch.object(m, 'decode', spy), \
                    patch('sys.argv', ['x', str(features), '--out', str(Path(root) / 'out')]), redirect_stdout(io.StringIO()):
                m.main()
            report = json.loads((Path(root) / 'out/report.json').read_text())
        self.assertTrue(calls)
        self.assertTrue(all(group not in train for _, group, train in calls))
        self.assertEqual(report['groups'], 6)

    def test_forbidden_deep_rem_never_decodes_directly(self):
        train = [session([2, 3, 2, 3, 2, 3, 1, 1])]
        matrix = m.transitions(train, alpha=1, forbid_deep_rem=True)
        self.assertEqual(matrix[m.DEEP, m.REM], 0)
        self.assertEqual(matrix[m.REM, m.DEEP], 0)
        class Certain:
            classes_ = np.array([0, 1, 2, 3])
            def predict_proba(self, xs):
                return np.array([[0, 0, 1, 0], [0, 0, 0, 1]] * (len(xs) // 2))
        # Even certain, heavily weighted emissions cannot force a forbidden jump.
        forced = m.decode('viterbi_no_deep_rem', Certain(), session([None] * 6), None, {'alpha': 1, 'weight': 32.0}, train)
        self.assertEqual(m.direct_deep_rem([forced]), 0)
        emissions = np.log(np.array([[.01, .01, .97, .01], [.01, .01, .01, .97]] * 4))
        path = m.viterbi(emissions, np.log(matrix), np.log(np.full(4, .25)))
        self.assertEqual(m.direct_deep_rem([path]), 0)
        unforbidden = m.viterbi(emissions, np.log(m.transitions(train, alpha=1)), np.log(np.full(4, .25)))
        self.assertGreater(m.direct_deep_rem([unforbidden]), 0)

    def test_probabilities_fill_missing_classes(self):
        class Model:
            classes_ = np.array([1, 3])
            def predict_proba(self, xs):
                return np.array([[.4, .6]] * len(xs))
        proba = m.probabilities(Model(), session([1, None]))
        np.testing.assert_allclose(proba, [[0, .4, 0, .6]] * 2)

    def test_emissions_are_not_divided_by_the_label_prior(self):
        class Model:
            classes_ = np.array([0, 1, 2, 3])
            def predict_proba(self, xs):
                return np.tile([.05, .5, .05, .4], (len(xs), 1))
        # Dividing by this light-heavy prior would flip every epoch to REM.
        train = [session([1] * 40 + [3] * 2)]
        path = m.decode('viterbi', Model(), session([None] * 3), None, {'alpha': 1, 'weight': 32.0}, train)
        self.assertEqual(path, [1, 1, 1])
        self.assertNotIn('beta', m.GRID[0])
        self.assertEqual(max(p['weight'] for p in m.GRID), 32.0)

    def test_main_rejects_too_few_groups_and_reused_output(self):
        nights = [{**session([1, 2], start=g * 100_000), 'group': f'g{g}'} for g in range(6)]
        with tempfile.TemporaryDirectory() as root:
            features = Path(root) / 'features.json'
            features.write_text(json.dumps({'nights': nights[:4], 'ruleset': {}, 'sources': {}}))
            with patch('sys.argv', ['x', str(features), '--out', str(Path(root) / 'a')]), self.assertRaisesRegex(ValueError, 'five'):
                m.main()
            unlabeled = nights[:5] + [{**session([None, None], start=900_000), 'group': 'g9'}]
            features.write_text(json.dumps({'nights': unlabeled, 'ruleset': {}, 'sources': {}}))
            with patch('sys.argv', ['x', str(features), '--out', str(Path(root) / 'u')]), self.assertRaisesRegex(ValueError, 'g9'):
                m.main()
            self.assertFalse((Path(root) / 'u').exists())
            features.write_text(json.dumps({'nights': nights, 'ruleset': {}, 'sources': {}}))
            (Path(root) / 'b').mkdir()
            (Path(root) / 'b/old').write_text('x')
            with patch('sys.argv', ['x', str(features), '--out', str(Path(root) / 'b')]), self.assertRaisesRegex(ValueError, 'empty output'):
                m.main()


if __name__ == '__main__':
    unittest.main()
