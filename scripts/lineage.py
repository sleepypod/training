"""Night lineage (plan A1b): which version of each model-graph node produced a night's stored outputs.

  vitals  engine (biometrics manifest hash) and profile (resolved config hash) for each
          stored vitals minute. Exact from vitals.processing_run_id → biometric_processing_runs
          where the pod recorded it; otherwise the generation active at each minute from
          biometric_runtime_receipts (latest adoption at or before the minute) joined to
          biometric_runtime_configs. A night with more than one version is `mixed`.
  build   core commit from the first archive pull after the night (pulls/*.json manifest
          `build`): the build serving the export, an upper bound on the one that recorded it.
  stage   the pod restages on read, so stored stages follow the selection at read time,
          from an optional saved GET /api/sleep-stages/models?side= response.

Reads local files only: the archive's biometrics.db and pull records.
"""
import argparse
from collections import Counter
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3
import sys
from zoneinfo import ZoneInfo

VITALS_COMPONENTS = ('heart_rate', 'breathing_rate', 'hrv_index')


def connect(path):
    return sqlite3.connect(f'file:{Path(path).resolve()}?mode=ro', uri=True)


def tables(db):
    return {row[0] for row in db.execute("select name from sqlite_master where type='table'")}


def columns(db, table):
    return {row[1] for row in db.execute(f'pragma table_info("{table}")')}


def night_of(entered_s, time_zone):
    """Noon-to-noon local date, as export-runtime-features.ts groups sessions."""
    return (datetime.fromtimestamp(entered_s, ZoneInfo(time_zone)) - timedelta(hours=12)).date().isoformat()


def _json(value):
    try:
        return json.loads(value) if isinstance(value, (str, bytes)) else value
    except ValueError:
        return None


def _summary(minutes, method):
    """versions: [(engine, profile, generation), minutes] with None for an unknown part."""
    known = [(k, n) for k, n in minutes.items() if k[0] is not None or k[1] is not None]
    unknown = sum(n for k, n in minutes.items() if k[0] is None and k[1] is None)
    versions = [dict(engine=e, profile=p, generation=g, minutes=n) for (e, p, g), n in sorted(known, key=lambda kv: -kv[1])]
    distinct = {(v['engine'], v['profile']) for v in versions}
    one = versions[0] if len(distinct) == 1 else None
    return dict(method=method, engine=one and one['engine'], profile=one and one['profile'],
                generation=one and one['generation'], mixed=len(distinct) > 1 if versions else None,
                versions=versions, unknownMinutes=unknown)


class Lineage:
    """Per-night lineage over one archive database; tolerant of snapshots without the provenance tables."""

    def __init__(self, db):
        self.db = db
        names = tables(db)
        self.has_vitals = 'vitals' in names
        self.has_runs = self.has_vitals and 'biometric_processing_runs' in names and 'processing_run_id' in columns(db, 'vitals')
        self.has_receipts = {'biometric_runtime_receipts', 'biometric_runtime_configs'} <= names
        self.runs, self.configs, self.adoptions = {}, {}, {}
        if self.has_runs:
            for run_id, bundle, config_hash in db.execute('select id, bundle, config_hash from biometric_processing_runs'):
                bundle = _json(bundle) or {}
                self.runs[run_id] = (bundle.get('manifestHash'), config_hash, bundle.get('generation'))
        if self.has_receipts and {'resolved_hash', 'payload'} <= columns(db, 'biometric_runtime_configs'):
            for side, generation, resolved_hash, payload in db.execute(
                    'select side, generation, resolved_hash, payload from biometric_runtime_configs'):
                self.configs[(side, generation)] = ((_json(payload) or {}).get('manifestHash'), resolved_hash)
            for side, component, generation, effective in db.execute(
                    "select side, component, generation, effective_sample_time_ms from biometric_runtime_receipts "
                    "where state = 'applied' and effective_sample_time_ms is not null order by effective_sample_time_ms"):
                if component in VITALS_COMPONENTS:
                    self.adoptions.setdefault((side, component), []).append((effective, generation))

    def vitals(self, side, start_s, end_s):
        if not self.has_vitals:
            return _summary({}, 'unavailable')
        if self.has_runs:
            rows = self.db.execute('select processing_run_id, count(*) from vitals where side=? and timestamp>=? and timestamp<? '
                                   'group by processing_run_id', (side, start_s, end_s)).fetchall()
            if any(run_id is not None for run_id, _ in rows):
                minutes = Counter()
                for run_id, n in rows:
                    minutes[self.runs.get(run_id, (None, None, None)) if run_id else (None, None, None)] += n
                return _summary(minutes, 'processing-runs')
        if self.adoptions.get((side, 'heart_rate')):
            stamps = [t for (t,) in self.db.execute('select timestamp from vitals where side=? and timestamp>=? and timestamp<?',
                                                     (side, start_s, end_s))]
            minutes = Counter()
            for t in stamps:
                versions = set()
                for component in VITALS_COMPONENTS:
                    adopted = [g for effective, g in self.adoptions.get((side, component), []) if effective <= t * 1000]
                    generation = adopted[-1] if adopted else 0
                    engine, profile = self.configs.get((side, generation), (None, 'defaults' if generation == 0 else None))
                    versions.add((engine, profile, generation))
                # Components on different generations in the same minute are a version of their own.
                key = versions.pop() if len(versions) == 1 else (None, 'components-disagree', None)
                minutes[key] += 1
            return _summary(minutes, 'runtime-receipts')
        return _summary({}, 'unavailable')


def load_pulls(pod_dir):
    """Pull records, oldest first; unreadable files are skipped."""
    pulls = []
    for path in sorted((Path(pod_dir) / 'pulls').glob('*.json')):
        try:
            record = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(record, dict) and isinstance(record.get('pulledAt'), str):
            record['_path'] = path.name
            pulls.append(record)
    return sorted(pulls, key=lambda r: r['pulledAt'])


def iso_ms(value):
    return round(datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp() * 1000)


def build(pulls, night_end_s):
    for record in pulls:
        if iso_ms(record['pulledAt']) >= night_end_s * 1000:
            info = (record.get('manifest') or {}).get('build') or {}
            return dict(commit=info.get('commitHash'), branch=info.get('branch'), pulledAt=record['pulledAt'], basis='first-pull-after-night')
    return dict(commit=None, branch=None, pulledAt=None, basis='no-pull-after-night')


def stage_selection(path):
    """The selected library entry from a saved GET /api/sleep-stages/models?side= response."""
    if not path:
        return dict(basis='selection-at-read', id=None, version=None, active=None, note='pass --stage-models to record the selection')
    models = json.loads(Path(path).read_text())
    chosen = next((m for m in models if m.get('selected')), None)
    if chosen is None:
        return dict(basis='selection-at-read', id=None, version='rules', active=False)
    return dict(basis='selection-at-read', id=chosen.get('id'), version=chosen.get('version'), active=bool(chosen.get('active')))


def short(value):
    return '-' if value is None else str(value)[:8]


def vitals_label(v):
    if v['mixed']:
        return f"mixed ({len({(x['engine'], x['profile']) for x in v['versions']})})"
    if v['mixed'] is None:
        return 'unknown'
    return f"{short(v['engine'])}/{short(v['profile'])}"


def night_lineage(lineage, pulls, records, time_zone, stage_models=None):
    """One entry per sleep record: (id, side, entered, left) rows."""
    stages = {side: stage_selection(path) for side, path in (stage_models or {}).items()}
    out = []
    for record_id, side, entered, left in records:
        out.append(dict(night=night_of(entered, time_zone), side=side, sleepRecordId=record_id,
                        vitals=lineage.vitals(side, entered, left), build=build(pulls, left),
                        stage=stages.get(side) or stage_selection(None)))
    return out


def sleep_records(db, side=None):
    query = 'select id, side, entered_bed_at, left_bed_at from sleep_records'
    rows = db.execute(query + (' where side=?' if side else '') + ' order by entered_bed_at, side', (side,) if side else ()).fetchall()
    return [(i, s, int(a), int(b)) for i, s, a, b in rows]


def parse_stage_models(values):
    out = {}
    for value in values or []:
        side, _, path = value.partition('=')
        if side not in ('left', 'right') or not path:
            raise SystemExit(f'--stage-models takes side=FILE, got {value}')
        out[side] = path
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('archive', help='archive pod directory: data/archive/<podId>')
    ap.add_argument('--timezone', required=True, help='IANA timezone for noon-to-noon night labels')
    ap.add_argument('--side', choices=('left', 'right'))
    ap.add_argument('--stage-models', action='append', help='side=FILE saved from GET /api/sleep-stages/models?side=; repeatable')
    ap.add_argument('--json', action='store_true', help='print JSON instead of a table')
    args = ap.parse_args(argv)
    ZoneInfo(args.timezone)
    pod_dir = Path(args.archive)
    with connect(pod_dir / 'biometrics.db') as db:
        rows = night_lineage(Lineage(db), load_pulls(pod_dir), sleep_records(db, args.side), args.timezone,
                             parse_stage_models(args.stage_models))
    if args.json:
        print(json.dumps(rows, indent=2))
        return
    print(f"{'night':10} {'side':5} {'vitals engine/profile':22} {'method':17} {'build':8} stage")
    for r in rows:
        v = r['vitals']
        print(f"{r['night']:10} {r['side']:5} {vitals_label(v):22} {v['method']:17} {short(r['build']['commit']):8} "
              f"{r['stage']['version'] or '-'}")
    versions = {(x['engine'], x['profile']) for r in rows for x in r['vitals']['versions']}
    print(f"{len(rows)} nights, {sum(1 for r in rows if r['vitals']['mixed'])} mixed, {len(versions)} vitals versions")


if __name__ == '__main__':
    sys.exit(main())
