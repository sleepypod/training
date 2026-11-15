"""Behavioral checks for vitals reference, the pod movement model and the multi-source stage model."""
import importlib.util
from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import movement_model as mm
import vitals_reference as vr

spec = importlib.util.spec_from_file_location('fusion', Path(__file__).with_name('fusion.py'))
fu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fu)

OUTLIERS = {'heartRate': {'min': 45, 'max': 130}, 'breathingRate': {'min': 8, 'max': 25}, 'hrv': {'min': 1, 'max': 300}}


class VitalsReferenceTests(unittest.TestCase):
    def test_quality_counts_in_range_minutes_and_jitter(self):
        vitals = [(0, 60, 14, 40), (60, None, 15, 40), (120, 64, 30, 40), (180, 200, 16, 40), (240, 61, 14, 40), (300, 90, 14, 40)]
        q = vr.quality_features(0, vitals, OUTLIERS)
        self.assertAlmostEqual(q['hrCoverage'], 3 / 5)
        self.assertAlmostEqual(q['brCoverage'], 4 / 5)
        self.assertEqual(q['hrJitter'], 3.5)

    def test_empty_epoch_has_zero_quality(self):
        self.assertEqual(vr.quality_features(0, [], OUTLIERS), {'hrCoverage': 0.0, 'brCoverage': 0.0, 'hrJitter': 0.0})

    def test_watch_samples_are_assigned_by_midpoint(self):
        samples = [(280_000, 300_000, 50), (10_000, 20_000, 60), (290_000, 310_000, 70)]
        self.assertEqual(vr.watch_mean(samples, 0, 300), 55)
        self.assertEqual(vr.watch_mean(samples, 300, 600), 70)
        self.assertIsNone(vr.watch_mean(samples, 600, 900))


class MovementModelTests(unittest.TestCase):
    def test_threshold_separates_moving_from_still(self):
        probability = np.array([.1, .2, .15, .8, .9, .7])
        moving = np.array([False, False, False, True, True, True])
        threshold = mm.balanced_threshold(probability, moving)
        self.assertTrue(.2 < threshold <= .7)

    def test_epoch_agreement_bins_windows_by_night_and_five_minutes(self):
        rows = [{'night': 'a', 't': t, 'label': 'light' if t < 300 else 'still'} for t in range(0, 600, 30)]
        flags = np.array([r['label'] != 'still' for r in rows])
        result = mm.epoch_agreement(rows, flags)
        self.assertEqual(result['epochs'], 2)
        self.assertEqual(result['movingEpochRecall'], 1.0)
        self.assertEqual(result['stillEpochFalseAlarm'], 0.0)
        self.assertEqual(result['mae'], 0.0)


class FusionTests(unittest.TestCase):
    def test_arms_extend_the_production_columns_in_group_order(self):
        features = ['hrZ']
        self.assertEqual(fu.columns(features, 'base'), ['hrZ'])
        fused = fu.columns(features, 'fused')
        self.assertEqual(fused[0], 'hrZ')
        self.assertEqual(fused[-len(mm.EPOCH_FEATURES):], list(mm.EPOCH_FEATURES))
        self.assertLess(fused.index('hrJitter'), fused.index('mmMoving'))

    def test_missing_movement_model_values_become_nan_not_zero(self):
        names = [n for g in fu.ARMS['fused'] for n in fu.GROUPS[g]]
        extra = {n: 1.0 for n in names} | {'mmMoving': None}
        night = {'group': 'g', 'rows': [{'start': 0, 'x': [0.5], 'y': 1}]}
        row = fu.datasets([night], ['hrZ'], [[extra]])['fused'][0]['rows'][0]
        self.assertTrue(np.isnan(row['x'][1 + names.index('mmMoving')]))
        self.assertEqual(len(row['x']), 1 + len(names))

    def test_no_watch_signal_is_a_feature(self):
        names = {n for g in fu.GROUPS.values() for n in g}
        self.assertFalse(any(n.lower().startswith('watch') for n in names))


if __name__ == '__main__':
    unittest.main()
