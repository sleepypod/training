"""Behavioral checks for the chronological stage backtest."""
import importlib.util
from pathlib import Path
import unittest

import numpy as np

spec = importlib.util.spec_from_file_location('stage_backtest', Path(__file__).with_name('stage-backtest.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

GROUPS = [f'2026-10-{d:02d}' for d in range(1, 13)]


class StageBacktestTests(unittest.TestCase):
    def test_walk_forward_trains_only_on_earlier_nights(self):
        plan = m.schedule(set(GROUPS), 'walk-forward', min_train=8)
        self.assertEqual([test for _, test in plan], GROUPS[8:])
        for train, test in plan:
            self.assertTrue(all(g < test for g in train))
            self.assertEqual(train, GROUPS[:GROUPS.index(test)])

    def test_frozen_keeps_one_training_window(self):
        plan = m.schedule(set(GROUPS), 'frozen', min_train=8)
        self.assertTrue(all(train == GROUPS[:8] for train, _ in plan))
        self.assertEqual(len(plan), 4)

    def test_too_few_nights_refused(self):
        with self.assertRaises(ValueError):
            m.schedule(set(GROUPS[:8]), 'walk-forward', min_train=8)

    def test_sign_test_ignores_ties(self):
        self.assertEqual(m.sign_test(np.array([1, 1, 1, 0, -1])), (3, 1, 0.625))
        self.assertEqual(m.sign_test(np.array([0, 0])), (0, 0, 1.0))

    def test_pod_live_uses_only_labeled_epochs_with_a_recorded_stage(self):
        night = {'rows': [{'y': 1, 'pod': 1}, {'y': None, 'pod': 2}, {'y': 3, 'pod': None}, {'y': 2, 'pod': 3}]}
        self.assertEqual(m.pod_live([night]), ([1, 2], [1, 3]))


if __name__ == '__main__':
    unittest.main()
