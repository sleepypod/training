"""Behavioral checks for the offline whole-night model evaluation."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np

spec = importlib.util.spec_from_file_location('retraining', Path(__file__).with_name('retrain-models.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
RULES = {'smoothIsolatedEpochs': True, 'transitions': [{'from': 'wake', 'to': 'deep', 'via': 'light'}]}


class RetrainingTests(unittest.TestCase):
    def test_unlabeled_epochs_remain_in_prediction_context(self):
        class Model:
            def predict(self, xs):
                return np.array([2, 1, 2])
        night = {'rows': [{'x': [0], 'y': None}, {'x': [1], 'y': 2}, {'x': [2], 'y': None}]}
        self.assertEqual(m.predict(Model(), [night], RULES), ([2], [2]))

    def test_postprocessing_order_and_input_immutability(self):
        raw = [0, 2, 0]
        self.assertEqual(m.postprocess(raw, RULES), [0, 0, 0])
        self.assertEqual(raw, [0, 2, 0])
        self.assertEqual(m.postprocess([0, 2], RULES), [0, 1])

    def test_inner_selection_keeps_fragmented_nights_together(self):
        nights = [{'group': str(g), 'rows': [{'x': [g], 'y': g % 4}]} for g in range(6)]
        nights.append({'group': '0', 'rows': [{'x': [0], 'y': 0}]})
        checks = []
        def fake_fit(name, train):
            return {n['group'] for n in train}
        def fake_predict(train_groups, valid, rules):
            self.assertTrue(train_groups.isdisjoint(n['group'] for n in valid))
            checks.append(len(valid))
            y = [n['rows'][0]['y'] for n in valid]
            return y, y
        with patch.object(m, 'fit', fake_fit), patch.object(m, 'predict', fake_predict):
            winner, scores = m.inner_choice(nights, RULES)
        self.assertEqual(winner, m.CANDIDATES[0])
        self.assertEqual(len(checks), len(m.CANDIDATES) * 3)
        self.assertTrue(all(s['epochs'] == len(nights) for s in scores.values()))

    def test_missing_awake_class_is_not_reported_as_zero_recall(self):
        score = m.metrics([1, 1, 2, 3], [1, 2, 2, 3])
        self.assertIsNone(score['recall']['wake'])
        self.assertEqual(score['recall']['deep'], 1)

    def test_permutation_diagnostics_preserve_inputs_and_are_repeatable(self):
        class Model:
            def predict(self, x): return np.asarray(x)[:, 0].astype(int)
        nights = [{'rows': [{'x': [float(i % 4), 1.0], 'y': i % 4} for i in range(40)]}]
        import copy
        original = copy.deepcopy(nights)
        rules = {'smoothIsolatedEpochs': False, 'transitions': []}
        drops = m.permutation_drops(Model(), nights, rules, ['signal', 'constant'])
        self.assertEqual(nights, original)
        self.assertEqual(drops, m.permutation_drops(Model(), nights, rules, ['signal', 'constant']))
        self.assertEqual(drops['constant'], [0.0, 0.0, 0.0])
        self.assertTrue(all(v > 0 for v in drops['signal']))

    def test_exported_forest_preserves_class_identity_when_wake_is_absent(self):
        nights = [{'rows': [{'x': [float(i), float(i % 3)], 'y': 1 + i % 3} for i in range(60)]}]
        clf = m.fit('balanced_leaf5', nights)
        doc = m.pm.portable(clf, 'forest', ['a', 'b'])
        xs = np.asarray([r['x'] for r in nights[0]['rows']])
        actual = [m.pm.evaluate_portable(doc, x) for x in xs]
        self.assertEqual(actual, clf.predict(xs).tolist())
        self.assertTrue(set(actual).issubset({1, 2, 3}))


if __name__ == '__main__':
    unittest.main()
