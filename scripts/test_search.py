"""Model search harness: spec expansion, statistics, cross-fitting and the synthetic pipeline end to end."""
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

import numpy as np

import modelset as ms
import search as s

ROOT = Path(__file__).resolve().parents[1]
SPEC = json.loads((ROOT / 'docs/search/synthetic.json').read_text())


def spec(**changes):
    return json.loads(json.dumps({**SPEC, **changes}))


class SpecTests(unittest.TestCase):
    def test_one_choice_per_node(self):
        choices, candidates = s.expand(SPEC)
        self.assertEqual([c['id'] for c in candidates], ['live+none+podContext', 'live+none+quality-wakeFirst',
                                                         'replayed+none+podContext', 'replayed+none+quality-wakeFirst'])
        self.assertEqual(choices['stage'][1]['groups'], ['context', 'movement', 'arousal', 'quality'])
        self.assertEqual(choices['vitals'][1], dict(id='replayed', kind='replayed', config='defaults', engine=None, allowUngated=False))

    def test_refuses_unnamed_primaries_budgets_and_duplicate_columns(self):
        with self.assertRaisesRegex(ValueError, 'primary'):
            s.expand(spec(primary={'candidate': 'replayed+none+podContext', 'baseline': 'nope'}))
        with self.assertRaisesRegex(ValueError, 'budget'):
            s.expand(spec(budget={'maxCandidates': 3}))
        with self.assertRaisesRegex(ValueError, 'podContext already'):
            s.expand(spec(space={**SPEC['space'], 'stage': [{'contract': 'podContext', 'groups': ['context']}]}))
        self.assertEqual(s.expand(spec(primary={'candidate': 'live+none+podContext', 'baseline': s.POD}))[1][0]['id'], 'live+none+podContext')


class StatisticsTests(unittest.TestCase):
    def test_paired_bootstrap_is_fixed_and_pairs_by_night(self):
        a = [dict(group=f'n{i}', macroF1=0.5 + 0.01 * i) for i in range(8)]
        b = [dict(group=f'n{i}', macroF1=0.5) for i in reversed(range(8))] + [dict(group='other', macroF1=0.0)]
        first, second = s.compare(a, b, 'macroF1'), s.compare(a, b, 'macroF1')
        self.assertEqual(first, second)
        self.assertEqual((first['nights'], first['better'], first['worse']), (8, 7, 0))
        self.assertAlmostEqual(first['meanDifference'], 0.035)
        low, high = first['bootstrap95Percent']
        self.assertTrue(0 < low < 0.035 < high)
        self.assertEqual(s.BOOTSTRAP, 10000)
        self.assertIsNone(s.compare(a, [], 'macroF1'))

    def test_waso_counts_wake_between_first_and_last_sleep(self):
        self.assertEqual(s.waso([0, 1, 0, 0, 2, 0]), 2)
        self.assertEqual(s.waso([0, 0]), 0)


class CrossFittingTests(unittest.TestCase):
    def test_movement_columns_never_saw_the_test_nights_watch_motion(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            rows = lambda start: [dict(start=start + i * 300, x=[float(i)], y=i % 4, pod=None) for i in range(3)]
            nights = [dict(id=1, group='2026-11-01', start=0, end=900, rows=rows(0)), dict(id=2, group='2026-11-02', start=86400, end=87300, rows=rows(86400))]
            (t / 'features.json').write_text(json.dumps(dict(nights=nights, features=['hrZ'], ruleset={'smoothIsolatedEpochs': False, 'transitions': []}, side='left')))
            sqlite3.connect(t / 'db.sqlite').close()
            mmdir = t / 'mm'
            mmdir.mkdir()
            epoch = lambda r, v: dict(start=r['start'], coverage=1.0, mmActive=v, mmMoving=v, mmPeak=v)
            for n in nights:
                other = '2026-11-02' if n['id'] == 1 else '2026-11-01'
                (mmdir / f"{n['id']}.json").write_text(json.dumps(dict(group=n['group'], rows=[epoch(r, 0.1) for r in n['rows']],
                                                                        excluding={other: [epoch(r, 0.9) for r in n['rows']]})))
            doc = dict(resolved={'v': dict(kind='live', snapshot=str(t / 'db.sqlite'))}, features={'v/podNative': dict(path=str(t / 'features.json'))},
                       choices=dict(stage=[dict(id='st', contract='podNative', model='balanced_leaf5', groups=[])]), movement={'m': dict(cache=str(mmdir))})
            inputs = s.Inputs(doc, dict(id='v+m+st', vitals='v', movementModel='m', stage='st'), ['1', '2'])
            self.assertEqual(inputs.columns, ['hrZ', 'mmActive', 'mmMoving', 'mmPeak'])
            held_out_two = {n['group']: n['rows'][0]['x'][1] for n in inputs.dataset('2026-11-02')}
            # Night 1 trains with a model fitted without night 2's Watch motion; night 2 keeps its own exclusions.
            self.assertEqual(held_out_two, {'2026-11-01': 0.9, '2026-11-02': 0.1})


@unittest.skipUnless((ROOT / 'node_modules/.bin/tsx').exists(), 'needs npm ci')
class SyntheticSearchTests(unittest.TestCase):
    def test_plan_run_report_final_on_synthetic_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            def run(*args):
                subprocess.run([sys.executable, *args], cwd=ROOT, check=True, capture_output=True, text=True)
            run('scripts/make-synthetic-inputs.py', str(t / 'in'), '--nights', '14', '--start', '2026-11-03', '--watch-vitals', '--replay', str(t / 'replay'))
            run('scripts/parse-watch-export.py', str(t / 'in/export.zip'), str(t / 'watch.json'))
            run('scripts/parse-watch-export.py', str(t / 'in/export.zip'), str(t / 'watch-vitals.json'), '--vitals')
            inputs = dict(snapshot=str(t / 'in/biometrics.db'), watch=str(t / 'watch.json'), watchVitals=str(t / 'watch-vitals.json'))
            (t / 'spec.json').write_text(json.dumps(spec(inputs=inputs)))
            blocked = s.plan(t / 'spec.json', t / 'blocked', t / 'cache')
            self.assertEqual(blocked['status'], 'blocked')
            self.assertTrue(any('pnpm biometrics:local-fit replay' in c and '--config defaults' in c for c in blocked['commands']))
            with self.assertRaises(SystemExit):
                s.run(t / 'blocked')
            run('scripts/derive_snapshot.py', 'ingest', str(t / 'replay'), '--cache', str(t / 'cache/vitals'))
            doc = s.plan(t / 'spec.json', t / 'run', t / 'cache')
            self.assertEqual((doc['status'], doc['split']['locked']), ('ready', ['2026-11-14']))
            self.assertNotIn('2026-11-14', doc['split']['development'])
            self.assertIsNone(doc['catalogId'])  # Outside data/, so never registered.
            report = s.run(t / 'run', jobs=2)
            for c in doc['candidates']:
                scored = json.loads((t / 'run/candidates' / f"{c['id']}.json").read_text())
                self.assertEqual(len(scored['nights']), len(doc['split']['development']) - 8)
            self.assertEqual(report['primary']['preregistered'], True)
            self.assertEqual(report['winner']['candidatesCompared'], 4)
            self.assertEqual(set(report['layers']['vitals']), {'live', 'replayed'})
            scored_groups = report['nights']['development'] + [n['group'] for v in report['layers']['vitals'].values() for n in v['nights']]
            self.assertNotIn('2026-11-14', scored_groups)
            self.assertEqual(report['layers']['vitals']['replayed']['versus']['brMaeReduction']['better'], len(doc['split']['development']))
            with self.assertRaisesRegex(SystemExit, 'not a candidate'):
                s.final(t / 'run', 'live+none+nope', t / 'access.jsonl')
            result = s.final(t / 'run', 'replayed+none+podContext', t / 'access.jsonl')
            log = [json.loads(line) for line in (t / 'access.jsonl').read_text().splitlines()]
            self.assertEqual([(e['purpose'], e['nights'], e['modelSha256']) for e in log], [('final', ['2026-11-14'], result['modelSha256'])])
            manifest = json.loads((t / 'run/model/set.json').read_text())
            model = json.loads((t / 'run/model/candidate.json').read_text())
            self.assertEqual(ms.problems(manifest, model), [])
            self.assertEqual(manifest['training']['inputs'], 'replayed')
            self.assertEqual(manifest['nodes']['vitals']['profile'], doc['resolved']['replayed']['profile'])
            self.assertEqual(manifest['training']['nights']['lockedExcluded'], 1)
            self.assertNotIn('2026-11-14', json.dumps(model['metrics']))
            with self.assertRaisesRegex(SystemExit, 'second final'):
                s.final(t / 'run', 'replayed+none+podContext', t / 'access.jsonl')
            self.assertEqual(json.loads((t / 'run/report.json').read_text())['holdout']['readsSoFar'], 1)


if __name__ == '__main__':
    unittest.main()
