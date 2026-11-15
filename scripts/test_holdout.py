"""The locked-holdout rule and its access log."""
from pathlib import Path
import sys
import tempfile
import unittest

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


if __name__ == '__main__':
    unittest.main()
