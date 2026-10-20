import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile

spec = importlib.util.spec_from_file_location('watch', Path(__file__).with_name('parse-watch-export.py'))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


def record(source='Apple Watch', stage='AsleepCore', start='2026-01-01 22:00:00 -0800', end='2026-01-01 22:05:00 -0800'):
    return f'<Record type="HKCategoryTypeIdentifierSleepAnalysis" sourceName="{source}" value="HKCategoryValueSleepAnalysis{stage}" startDate="{start}" endDate="{end}"/>'


class ParserTests(unittest.TestCase):
    def parse_xml(self, xml):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'export.xml'; p.write_text('<HealthData>' + xml + '</HealthData>')
            return m.parse(p)

    def test_watch_only_deduplicates_and_excludes_writeback(self):
        result = self.parse_xml(record() * 2 + record('SleepyPod Watch') + record('Phone', 'Awake') + record(stage='InBed'))
        self.assertEqual(len(result['stages']), 1)
        self.assertEqual(result['stages'][0], (1767333600000, 1767333900000, 'light'))

    def test_conflicting_stages_retained_for_exporter_to_exclude(self):
        self.assertEqual(len(self.parse_xml(record() + record(stage='Awake'))['stages']), 2)

    def test_rejects_missing_timezone_and_invalid_intervals(self):
        for row in [record(start='2026-01-01T22:00:00'), record(end='2026-01-01 21:00:00 -0800')]:
            with self.assertRaises(ValueError): self.parse_xml(row)

    def test_zip_is_parsed_without_extracting_archive(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'export.zip'
            with zipfile.ZipFile(p, 'w') as z:
                z.writestr('apple_health_export/export.xml', '<HealthData>' + record() + '</HealthData>')
                z.writestr('../unrelated', 'ignored')
            self.assertEqual(len(m.parse(p)['stages']), 1)
            self.assertEqual(list(Path(d).iterdir()), [p])

    def test_no_matching_stages_fails(self):
        with self.assertRaises(ValueError): self.parse_xml(record('Phone'))


if __name__ == '__main__': unittest.main()
