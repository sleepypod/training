"""The locked-holdout rule and its access log."""
from contextlib import redirect_stderr
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import holdout as ho


class HoldoutTests(unittest.TestCase):
    def test_every_fourth_night_from_the_freeze_is_locked(self):
        self.assertFalse(ho.locked('2026-11-13'))
        self.assertTrue(ho.locked('2026-11-14'))
        self.assertFalse(ho.locked('2026-11-15'))
        self.assertTrue(ho.locked('2026-11-18'))
        self.assertTrue(ho.locked('2026-11-30'))
        self.assertFalse(ho.locked('2026-12-02'))
        self.assertEqual(ho.split(['2026-11-15', '2026-11-14', '2026-11-10', '2026-11-14']), (['2026-11-10', '2026-11-15'], ['2026-11-14']))

    def test_development_runs_drop_locked_nights_unless_final(self):
        nights = [{'group': '2026-11-13'}, {'group': '2026-11-14'}]
        self.assertEqual(ho.drop_locked(nights), [{'group': '2026-11-13'}])
        self.assertEqual(ho.drop_locked(nights, holdout_final=True), nights)

    def test_access_log_appends_only_locked_nights_and_allowed_purposes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'holdout-access.jsonl'
            ho.log_access('run-1', 'abc', ['2026-11-14', '2026-11-15'], 'final', path)
            ho.log_access('trial-id3', 'def', ['2026-11-18'], 'trial', path)
            with self.assertRaises(ValueError):
                ho.log_access('run-2', 'abc', ['2026-11-14'], 'tuning', path)
            entries = ho.accesses(path)
            self.assertEqual([e['nights'] for e in entries], [['2026-11-14'], ['2026-11-18']])
            self.assertEqual(entries[0]['purpose'], 'final')
            self.assertEqual(ho.accesses(Path(directory) / 'missing.jsonl'), [])


    def test_protocol_records_the_locked_groups_and_their_use(self):
        record = ho.record(['2026-11-13', '2026-11-14', '2026-11-18', '2026-11-14'])
        self.assertEqual(record['lockedGroups'], ['2026-11-14', '2026-11-18'])
        self.assertEqual((record['freeze'], record['every']), ('2026-11-14', 4))
        self.assertIn('dropped', record['lockedUse'])
        self.assertIn('final read', ho.record([], holdout_final=True)['lockedUse'])

    def test_groups_that_are_not_dates_cannot_be_judged(self):
        with self.assertRaisesRegex(ValueError, 'not a YYYY-MM-DD'):
            ho.locked('night-3')

    def test_scripts_that_fit_or_compare_refuse_a_final_read(self):
        scripts = {'stage-backtest.py': ['f.json', '--snapshot', 'x.db', '--out', 'o'],
                   'feature-ablation.py': ['f.json', '--snapshot', 'x.db', '--out', 'o'],
                   'fusion.py': ['f.json', '--snapshot', 'x.db', '--movement-epochs', 'm.json', '--out', 'o'],
                   'sequence-decoding.py': ['f.json', '--out', 'o']}
        for name, argv in scripts.items():
            spec = importlib.util.spec_from_file_location(name[:-3].replace('-', '_'), Path(__file__).with_name(name))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            with self.subTest(name), patch('sys.argv', [name, *argv, '--holdout-final']), \
                    redirect_stderr(io.StringIO()) as stderr, self.assertRaises(SystemExit):
                module.main()
            self.assertIn('--holdout-final is refused here', stderr.getvalue())


if __name__ == '__main__':
    unittest.main()
