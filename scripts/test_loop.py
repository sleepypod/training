"""Behavioral checks for the freeze-then-score training loop."""
from contextlib import redirect_stdout
from datetime import datetime, timezone
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier

spec = importlib.util.spec_from_file_location('loop', Path(__file__).with_name('loop.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

FEATURES = ['a', 'b']
GROUPS = [f'2026-11-{d:02d}' for d in range(1, 13)]


def night(i, group, rng):
    rows = [{'x': [k % 4 + rng.normal(0, .3), rng.normal()], 'y': k % 4, 'rule': None} for k in range(40)]
    start = int(datetime.fromisoformat(group + 'T23:00:00+00:00').timestamp())
    return {'id': i, 'group': group, 'start': start, 'end': start + 40 * 300, 'watchEpochs': 40, 'rows': rows}


def features(groups, rng):
    ruleset = {'smoothIsolatedEpochs': False, 'transitions': []}
    return {'features': FEATURES, 'ruleset': ruleset, 'sources': {}, 'side': 'left',
            'nights': [night(i, g, rng) for i, g in enumerate(groups)]}


def save_model(directory, trained_through, trained_at, contract=FEATURES):
    directory.mkdir(parents=True)
    rng = np.random.default_rng(0)
    x = rng.normal(size=(40, len(contract)))
    clf = RandomForestClassifier(n_estimators=5, random_state=0).fit(x, np.arange(40) % 4)
    joblib.dump(clf, directory / 'candidate.joblib')
    model = {'version': f'test-{trained_through}', 'featureContract': contract, 'trainedAt': trained_at,
             'metrics': {'trainedThrough': trained_through}}
    (directory / 'candidate.json').write_text(json.dumps(model))
    return directory


class LoopTests(unittest.TestCase):
    def test_refits_see_only_earlier_groups_and_cover_each_test_group_once(self):
        for cadence in (1, 3, 7):
            plan = m.refits(GROUPS, cadence, min_train=4)
            tested = [g for _, tests in plan for g in tests]
            self.assertEqual(tested, GROUPS[4:])
            for refit_date, tests in plan:
                self.assertTrue(all(g >= refit_date for g in tests))
                self.assertTrue(all(g < refit_date for g in GROUPS[:GROUPS.index(tests[0])]))
        self.assertEqual(len(m.refits(GROUPS, 1, min_train=4)), 8)
        with self.assertRaises(ValueError):
            m.refits(GROUPS[:4], 1, min_train=4)

    def test_locked_nights_never_enter_the_loop(self):
        data = {'nights': [{'group': g} for g in ('2026-11-13', '2026-11-14', '2026-11-15', '2026-11-18')]}
        nights, groups = m.development(data)
        self.assertEqual(groups, ['2026-11-13', '2026-11-15'])

    def test_columns_follow_the_model_contract_and_refuse_missing_ones(self):
        self.assertEqual(m.columns({'version': 'v', 'featureContract': ['b', 'a']}, ['a', 'b', 'c']), [1, 0])
        with self.assertRaises(ValueError):
            m.columns({'version': 'v', 'featureContract': ['z']}, ['a'])

    def test_frozen_models_score_only_after_their_cutoff(self):
        rng = np.random.default_rng(1)
        data = features(GROUPS, rng)
        with tempfile.TemporaryDirectory() as d:
            frozen = m.load_frozen(save_model(Path(d) / 'model', '2026-11-08', '2026-11-09T12:00:00+00:00'))
            scores = m.score_frozen(frozen, m.by_group(data['nights']), data)
        self.assertEqual(sorted(scores), GROUPS[8:])

    def test_prospective_needs_every_session_after_training(self):
        trained = '2026-11-10T12:00:00+00:00'
        after = int(datetime(2026, 11, 10, 23, tzinfo=timezone.utc).timestamp())
        self.assertTrue(m.prospective([{'start': after}], trained))
        self.assertFalse(m.prospective([{'start': after}, {'start': after - 86400}], trained))
        self.assertFalse(m.prospective([{'start': after}], None))

    def test_comparisons_pair_only_shared_nights(self):
        s = lambda f: {'macroF1': f, 'kappa': f, 'wakeF1': None}
        scores = {'x': {'n1': s(.5), 'n2': s(.6), 'n3': s(.7)}, 'y': {'n2': s(.4), 'n3': s(.5)}}
        result = m.compare_arms(scores, np.random.default_rng(0))['x vs y']
        self.assertEqual(result['nights'], ['n2', 'n3'])
        self.assertAlmostEqual(result['macroF1']['meanDifference'], .2)
        self.assertIsNone(result['wakeF1'])
        kept = m.compare_arms(scores, np.random.default_rng(0), lambda a, b, g: g == 'n3')['x vs y']
        self.assertEqual(kept['nights'], ['n3'])

    def test_replay_and_registry_end_to_end(self):
        rng = np.random.default_rng(2)
        data = features(GROUPS, rng)
        with tempfile.TemporaryDirectory() as d, patch.object(m, 'MIN_TRAIN_GROUPS', 6), patch.object(m, 'register_output'):
            root = Path(d)
            path = root / 'features.json'
            path.write_text(json.dumps(data))
            model = save_model(root / 'frozen', '2026-11-09', '2026-11-10T00:00:00+00:00')
            args = type('Args', (), dict(features=path, out=root / 'replay', cadence=[1, 7], frozen=[('old', model)]))
            with redirect_stdout(io.StringIO()):
                m.replay(args)
            report = json.loads((root / 'replay' / 'report.json').read_text())
            self.assertEqual(set(report['nights']), {'refit-1d', 'refit-7d', 'old'})
            self.assertEqual(sorted(report['nights']['old']), GROUPS[9:])
            self.assertEqual(report['comparisons']['refit-1d vs old']['nights'], GROUPS[9:])
            for arm in report['arms']['refit-1d']:
                self.assertTrue(arm['trainedThrough'] < arm['refit'])
            with self.assertRaises(ValueError):
                m.replay(args)

            registry = root / 'loop' / 'frozen.jsonl'
            freeze = type('Args', (), dict(model_dir=model, label='old', registry=registry))
            with patch.object(m.ho, 'run_name', lambda p: str(Path(p).resolve())), redirect_stdout(io.StringIO()):
                m.freeze(freeze)
                with self.assertRaises(ValueError):
                    m.freeze(freeze)
                m.score(type('Args', (), dict(features=path, registry=registry)))
            scored = json.loads(registry.with_name('scores.json').read_text())
            self.assertEqual(sorted(scored['nights']['old']), GROUPS[9:])
            self.assertTrue(all(scored['prospective']['old'].values()))


if __name__ == '__main__':
    unittest.main()
