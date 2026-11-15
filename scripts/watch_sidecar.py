"""Watch teacher sidecars (plan A3): Apple Health data for nights whose Watch rows never reached the pod.

  import  Split parse-watch-export.py output (stages, and --vitals output) by night and
          write data/archive/<podId>/watch/<night>.json. Re-importing a teacher type for a
          night replaces that type only.

Samples are kept only inside the archive's sleep records for the side (±1 h), or inside
the imported stage intervals when the archive has none. archive_index.py prefers the
pod's own rows and uses a sidecar only where they are missing. Local files only; this
never writes to the pod (Watch uploads to the pod stay a manual, approved step).
"""
import argparse
import bisect
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lineage as ln

VERSION = 'watch-sidecar-v1'
VITALS = ('heartRate', 'respiratoryRate', 'hrvSdnn')
MARGIN_S = 3600


def windows(pod_dir, side, stages):
    """In-bed windows (seconds) that keep a sample: sleep records, else the imported stages."""
    db_path = Path(pod_dir) / 'biometrics.db'
    spans = []
    if db_path.is_file():
        with ln.connect(db_path) as db:
            if 'sleep_records' in ln.tables(db):
                spans = [(a, b) for _, _, a, b in ln.sleep_records(db, side)]
    if not spans:
        spans = [(s // 1000, e // 1000) for s, e, _ in stages]
    merged = []
    for a, b in sorted([a - MARGIN_S, b + MARGIN_S] for a, b in spans):
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return merged


def inside(spans, starts, start_ms):
    i = bisect.bisect_right(starts, start_ms / 1000) - 1
    return i >= 0 and start_ms / 1000 < spans[i][1]


def source(path, data, kind):
    return dict(file=Path(path).name, sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest(), kind=kind,
                sourceFilter=data.get('sourceFilter'), importedAt=datetime.now(timezone.utc).isoformat(timespec='seconds'))


def split(rows, spans, time_zone):
    nights, starts = {}, [a for a, _ in spans]
    for row in rows:
        if inside(spans, starts, row[0]):
            nights.setdefault(ln.night_of(row[0] / 1000, time_zone), []).append(list(row))
    return nights


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f'.tmp.{os.getpid()}')
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(path)


def import_sidecars(pod_dir, side, time_zone, stages_path=None, vitals_path=None):
    """Returns {night: {teacher: count}} for the nights written."""
    if not stages_path and not vitals_path:
        raise SystemExit('Pass --stages and/or --vitals (parse-watch-export.py output)')
    stages = json.loads(Path(stages_path).read_text()) if stages_path else None
    vitals = json.loads(Path(vitals_path).read_text()) if vitals_path else None
    if stages is not None and not isinstance(stages.get('stages'), list):
        raise SystemExit(f'{stages_path} has no stages; pass parse-watch-export.py output')
    if vitals is not None and not any(isinstance(vitals.get(k), list) for k in VITALS):
        raise SystemExit(f'{vitals_path} has no Watch vitals; pass parse-watch-export.py --vitals output')
    spans = windows(pod_dir, side, stages['stages'] if stages else [])
    if not spans:
        raise SystemExit('No sleep records in the archive for this side; pass --stages so nights can be found')
    updates = {}
    if stages is not None:
        for night, rows in split(stages['stages'], spans, time_zone).items():
            updates.setdefault(night, {})['stages'] = rows
    if vitals is not None:
        for key in VITALS:
            for night, rows in split(vitals.get(key) or [], spans, time_zone).items():
                updates.setdefault(night, {})[key] = rows
    written = {}
    for night, teachers in sorted(updates.items()):
        path = Path(pod_dir) / 'watch' / f'{night}.json'
        doc = json.loads(path.read_text()) if path.exists() else dict(version=VERSION, night=night, timezone=time_zone, sides={})
        entry = doc['sides'].setdefault(side, {'sources': {}})
        for teacher, rows in teachers.items():
            entry[teacher] = sorted(rows)
            entry['sources'][teacher] = source(stages_path if teacher == 'stages' else vitals_path,
                                               stages if teacher == 'stages' else vitals, teacher)
        doc['reference'] = 'Apple Health Watch data; a teacher for pod models, never an inference input or clinical ground truth'
        write(path, doc)
        written[night] = {t: len(r) for t, r in teachers.items()}
    return written


def cmd_import(args):
    ZoneInfo(args.timezone)
    written = import_sidecars(args.archive, args.side, args.timezone, args.stages, args.vitals)
    for night, counts in written.items():
        print(f"{night} {args.side}: " + ', '.join(f'{n} {t}' for t, n in counts.items()))
    print(f'Wrote {len(written)} sidecar nights under {Path(args.archive) / "watch"}; rerun archive_index.py to see them.')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='command', required=True)
    i = sub.add_parser('import', help='write watch/<night>.json from parse-watch-export.py output')
    i.add_argument('--archive', required=True, help='archive pod directory: data/archive/<podId>')
    i.add_argument('--side', choices=('left', 'right'), required=True, help='the side whose sleeper wore the Watch')
    i.add_argument('--timezone', required=True, help='IANA timezone for noon-to-noon night labels')
    i.add_argument('--stages', help='parse-watch-export.py output')
    i.add_argument('--vitals', help='parse-watch-export.py --vitals output')
    args = ap.parse_args(argv)
    {'import': cmd_import}[args.command](args)


if __name__ == '__main__':
    sys.exit(main())
