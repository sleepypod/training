"""Behavioral checks for the literature-derived feature ablation."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import unittest.mock

import numpy as np

spec = importlib.util.spec_from_file_location('feature_ablation', Path(__file__).with_name('feature-ablation.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

FEATURES = ['hrZ', 'hrStdZ', 'motionZ', 'nightFraction', 'hrvRel', 'hrv15Rel', 'hr15StdRel', 'br15Std', 'motion15', 'minutesSinceBed']


def row(start, value, y=1):
    return {'start': start, 'x': [value] * len(FEATURES), 'y': y}


class FeatureAblationTests(unittest.TestCase):
    def test_active_minutes_are_outliers_above_the_night_floor(self):
        movement = [(t * 60, 100 + t % 3) for t in range(60)]
        movement[30] = (1800, 5000)
        self.assertEqual(m.active_minutes(movement), [1800])

    def test_still_bout_and_distance_to_movement(self):
        movement = [(t * 60, 1) for t in range(60)]
        f = m.movement_features(1200, movement, [600, 2400])
        self.assertEqual(f['activeMinutes'], 0)
        self.assertAlmostEqual(f['minutesFromMove'], (1350 - 600) / 60)
        self.assertAlmostEqual(f['stillBoutMinutes'], (2400 - 600) / 60)
        self.assertEqual(f['active15'], 1)

    def test_no_movement_bout_spans_the_record(self):
        movement = [(t * 60, 1) for t in range(60)]
        self.assertAlmostEqual(m.movement_features(1200, movement, [])['stillBoutMinutes'], 59)

    def test_heart_rate_rise_is_against_the_preceding_fifteen_minutes(self):
        hr = [(t, 60) for t in range(0, 900, 60)] + [(960, 70)]
        f = m.arousal_features(900, hr, [], night_hr_std=5, night_br_median=0)
        self.assertAlmostEqual(f['hrRise'], 2)
        self.assertEqual(f['brRel'], 0)

    def test_context_uses_time_neighbours_and_falls_back_across_gaps(self):
        rows = [row(0, 1), row(300, 2), row(900, 4)]
        out = m.context_features(rows, FEATURES, end=1200)
        self.assertEqual(out[1]['hrZPrev'], 1)
        self.assertEqual(out[1]['hrZNext'], 2)
        self.assertEqual(out[2]['hrZPrev'], 4)
        self.assertAlmostEqual(out[0]['minutesToExit'], 20)

    def test_arms_only_add_pod_features(self):
        self.assertEqual(m.arm_columns(FEATURES, 'base'), FEATURES)
        everything = m.arm_columns(FEATURES, 'all')
        self.assertEqual(len(everything), len(set(everything)))
        self.assertTrue(set(m.CONTEXT + m.MOVEMENT + m.AROUSAL) <= set(everything))
        self.assertFalse(any('watch' in c.lower() for c in everything))

    def test_hierarchical_separates_wake_first(self):
        rng = np.random.default_rng(1)
        rows = [{'start': i * 300, 'x': [float(y) + rng.normal(0, .05)], 'y': y} for i, y in enumerate([0, 1, 2, 3] * 30)]
        clf = m.Hierarchical([{'group': 'g', 'rows': rows}])
        self.assertEqual(list(clf.predict([[0], [1], [2], [3]])), [0, 1, 2, 3])

    def test_refuses_a_snapshot_the_features_did_not_come_from(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / 'snapshot.db').write_bytes(b'other')
            (tmp / 'features.json').write_text(json.dumps({'sources': {'snapshot': '0' * 64}, 'nights': [], 'features': FEATURES, 'ruleset': {}, 'side': 'left'}))
            with unittest.mock.patch('sys.argv', ['x', str(tmp / 'features.json'), '--snapshot', str(tmp / 'snapshot.db'), '--out', str(tmp / 'out')]):
                with self.assertRaisesRegex(ValueError, 'Snapshot does not match'):
                    m.main()


if __name__ == '__main__':
    unittest.main()
