"""Extract Watch sleep intervals and vitals from Apple Health XML/ZIP, without uploading data."""
import argparse
from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile

STAGES = {
    'HKCategoryValueSleepAnalysisAwake': 'wake',
    'HKCategoryValueSleepAnalysisAsleepCore': 'light',
    'HKCategoryValueSleepAnalysisAsleepDeep': 'deep',
    'HKCategoryValueSleepAnalysisAsleepREM': 'rem',
}

# Watch vitals are reference signals for checking pod vitals, never stage-model features.
VITALS = {
    'HKQuantityTypeIdentifierHeartRate': 'heartRate',
    'HKQuantityTypeIdentifierHeartRateVariabilitySDNN': 'hrvSdnn',
    'HKQuantityTypeIdentifierRespiratoryRate': 'respiratoryRate',
}


@contextmanager
def export_stream(path):
    path = Path(path)
    if path.is_dir():
        path = path / 'export.xml'
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            candidates = [n for n in archive.namelist() if n == 'export.xml' or n.endswith('/export.xml')]
            if len(candidates) != 1:
                raise ValueError('ZIP must contain exactly one export.xml')
            with archive.open(candidates[0]) as stream:
                yield stream
    else:
        with path.open('rb') as stream:
            yield stream


def parse(path, source='Watch'):
    intervals = set()
    if not source.strip():
        raise ValueError('Source filter must not be empty')
    with export_stream(path) as stream:
        context = ET.iterparse(stream, events=('start', 'end'))
        _, root = next(context)
        for event, element in context:
            if event != 'end':
                continue
            if element.tag == 'Record':
                attrs = element.attrib
                name = attrs.get('sourceName', '')
                stage = STAGES.get(attrs.get('value'))
                if (attrs.get('type') == 'HKCategoryTypeIdentifierSleepAnalysis'
                        and stage and source.casefold() in name.casefold()
                        and 'sleepypod' not in name.casefold()):
                    start, end = interval_ms(attrs)
                    if end <= start:
                        raise ValueError('Watch interval has nonpositive duration')
                    intervals.add((start, end, stage))
            element.clear()
            root.clear()
    if not intervals:
        raise ValueError('No matching Watch sleep stages; check the source filter and export')
    return {'stages': sorted(intervals), 'sourceFilter': source,
            'reference': 'Apple Health Watch sleep stages; not clinical ground truth'}


def interval_ms(attrs):
    dates = [datetime.fromisoformat(attrs[k]) for k in ('startDate', 'endDate')]
    if any(d.tzinfo is None for d in dates):
        raise ValueError('Watch timestamps must include timezone offsets')
    return [round(d.timestamp() * 1000) for d in dates]


def parse_vitals(path, source='Watch'):
    """Watch heart rate, HRV (SDNN) and respiratory rate samples as [startMs, endMs, value]."""
    if not source.strip():
        raise ValueError('Source filter must not be empty')
    samples = {name: set() for name in VITALS.values()}
    with export_stream(path) as stream:
        context = ET.iterparse(stream, events=('start', 'end'))
        _, root = next(context)
        for event, element in context:
            if event != 'end':
                continue
            if element.tag == 'Record':
                attrs = element.attrib
                name = attrs.get('sourceName', '')
                kind = VITALS.get(attrs.get('type'))
                if kind and source.casefold() in name.casefold() and 'sleepypod' not in name.casefold():
                    start, end = interval_ms(attrs)
                    value = float(attrs['value'])
                    if end < start or not value > 0:
                        raise ValueError('Invalid Watch vitals sample')
                    samples[kind].add((start, end, value))
            element.clear()
            root.clear()
    if not samples['heartRate']:
        raise ValueError('No matching Watch heart rate; check the source filter and export')
    return {**{k: sorted(v) for k, v in samples.items()}, 'sourceFilter': source,
            'reference': 'Apple Health Watch vitals; reference for pod vitals, never stage-model features'}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('export', type=Path)
    ap.add_argument('output', type=Path)
    ap.add_argument('--source', default='Watch', help='Case-insensitive sourceName substring; SleepyPod is always excluded')
    ap.add_argument('--vitals', action='store_true', help='Extract Watch heart rate, HRV and respiratory rate instead of stages')
    args = ap.parse_args()
    if args.vitals:
        result = parse_vitals(args.export, args.source)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x') as f:
            json.dump(result, f)
        print('Extracted ' + ', '.join(f'{len(result[k])} {k}' for k in VITALS.values()))
        return
    result = parse(args.export, args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as f:
        json.dump(result, f)
    print(f'Extracted {len(result["stages"])} reference intervals')


if __name__ == '__main__':
    main()
