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


class VitalsAgreementTests(unittest.TestCase):
    def test_constant_inputs_give_null_correlations(self):
        a = vr.agreement([(60, 55), (60, 58), (60, 61)])
        self.assertIsNone(a['pearson'])
        self.assertIsNone(a['spearman'])
        self.assertEqual(a['bias'], 2.0)

    def test_hrv_reports_rank_agreement_only(self):
        self.assertEqual(vr.rank_agreement([(20, 40), (40, 80), (60, 120)]), {'epochs': 3, 'spearman': 1.0})
        self.assertIsNone(vr.rank_agreement([(20, 40), (40, 80)]))


class FakeForest:
    classes_ = np.array(['light', 'still', 'vigorous'])

    def __init__(self, moving):
        self.moving = moving

    def predict_proba(self, x):
        p = np.asarray(self.moving[:len(x)], float)
        return np.column_stack([p / 2, 1 - p, p / 2])


class MovementModelTests(unittest.TestCase):
    def test_strict_cutoff_never_flags_ties_at_zero(self):
        windows = [(0, np.zeros((4, 21)), 4)]
        rows = mm.epoch_features({'model': FakeForest([0, 0, 0, 0]), 'strictThreshold': 0.0}, windows)
        self.assertEqual(rows[0]['mmActive'], 0.0)
        rows = mm.epoch_features({'model': FakeForest([0, 0, .5, 1]), 'strictThreshold': 0.0}, windows)
        self.assertEqual(rows[0]['mmActive'], 0.5)

    def test_session_excludes_watch_nights_whose_windows_fall_inside_it(self):
        teaching = {'nights': np.array(['2026-11-01', '2026-11-01', '2026-11-02', '2026-11-03']),
                    't': np.array([100.0, 200.0, 500.0, 9000.0])}
        # A session grouped under 11-01 that runs past noon into Watch night 11-02.
        night = {'group': '2026-11-01', 'start': 0, 'end': 1000}
        self.assertEqual(mm.own_exclusions(night, teaching), frozenset({'2026-11-01', '2026-11-02'}))
        self.assertEqual(mm.own_exclusions({'group': '2026-10-20', 'start': 2000, 'end': 3000}, teaching), frozenset())

    def test_cross_fitted_refits_without_every_excluded_night(self):
        rng = np.random.default_rng(0)
        x = rng.normal(size=(120, 21))
        labels = np.array(['still', 'light'] * 60)
        nights = np.array(['a'] * 40 + ['b'] * 40 + ['c'] * 40)
        bundle = {'final': 'final', 'heldOut': {'a': 'no-a'}, 'teaching': {'x': x, 'labels': labels, 'nights': nights, 't': np.arange(120.0)}}
        models = mm.CrossFitted(bundle)
        self.assertEqual(models.get(frozenset()), 'final')
        self.assertEqual(models.get(frozenset({'a'})), 'no-a')
        fitted = models.get(frozenset({'a', 'b'}))
        self.assertEqual(fitted['model'].n_features_in_, 21)
        self.assertIs(models.get(frozenset({'b', 'a'})), fitted)

    def test_cross_fitting_refuses_a_fold_without_still_windows(self):
        bundle = {'final': None, 'heldOut': {}, 'teaching': {'x': np.zeros((4, 21)), 'labels': np.array(['still', 'still', 'light', 'light']),
                                                            'nights': np.array(['a', 'a', 'b', 'b']), 't': np.arange(4.0)}}
        with self.assertRaises(SystemExit):
            mm.CrossFitted(bundle).get(frozenset({'a'}))

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

    def test_held_out_night_selects_movement_features_that_never_saw_it(self):
        session = {'group': 'b', 'rows': ['own'], 'excluding': {'a': ['without-a'], 'c': ['without-c']}}
        self.assertEqual(fu.movement_rows(session, 'a'), ['without-a'])
        self.assertEqual(fu.movement_rows(session, 'b'), ['own'])
        self.assertEqual(fu.movement_rows(session, 'z'), ['own'])

    def test_pod_and_model_are_scored_on_the_same_epochs(self):
        class Constant:
            def predict(self, x):
                return np.ones(len(x), int)
        ruleset = {'smoothingWindowEpochs': 1}
        held = [{'rows': [{'x': [0], 'y': 1, 'pod': 1}, {'x': [0], 'y': 2, 'pod': None}, {'x': [0], 'y': None, 'pod': 3}]}]
        original = fu.rm.postprocess
        fu.rm.postprocess = lambda raw, rules: raw
        try:
            y, p, labelled = fu.predict_shared(Constant(), held, ruleset)
        finally:
            fu.rm.postprocess = original
        self.assertEqual((y, p, labelled), ([1], [1], 2))
        self.assertEqual(fu.sb.pod_live(held), ([1], [1]))

    def test_refuses_features_that_already_carry_podcontext_columns(self):
        with self.assertRaises(ValueError):
            fu.require_pod_native({'contract': 'podContext', 'features': ['hrZ']})
        with self.assertRaises(ValueError):
            fu.require_pod_native({'features': ['hrZ', 'activeMinutes']})
        fu.require_pod_native({'contract': 'podNative', 'features': ['hrZ']})

    def test_no_watch_signal_is_a_feature(self):
        names = {n for g in fu.GROUPS.values() for n in g}
        self.assertFalse(any(n.lower().startswith('watch') for n in names))


if __name__ == '__main__':
    unittest.main()
