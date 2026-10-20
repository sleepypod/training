"""Extract Watch sleep intervals from Apple Health XML/ZIP, without uploading data."""
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
                    dates = [datetime.fromisoformat(attrs[k]) for k in ('startDate', 'endDate')]
                    if any(d.tzinfo is None for d in dates):
                        raise ValueError('Watch timestamps must include timezone offsets')
                    start, end = [round(d.timestamp() * 1000) for d in dates]
                    if end <= start:
                        raise ValueError('Watch interval has nonpositive duration')
                    intervals.add((start, end, stage))
            element.clear()
            root.clear()
    if not intervals:
        raise ValueError('No matching Watch sleep stages; check the source filter and export')
    return {'stages': sorted(intervals), 'sourceFilter': source,
            'reference': 'Apple Health Watch sleep stages; not clinical ground truth'}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('export', type=Path)
    ap.add_argument('output', type=Path)
    ap.add_argument('--source', default='Watch', help='Case-insensitive sourceName substring; SleepyPod is always excluded')
    args = ap.parse_args()
    result = parse(args.export, args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as f:
        json.dump(result, f)
    print(f'Extracted {len(result["stages"])} reference intervals')


if __name__ == '__main__':
    main()
