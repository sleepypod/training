"""Night lineage (plan A1b): which version of each model-graph node produced a night's stored outputs.

  vitals  the identity core gives each vitals row (sleepypod/core#930): its processing run
          (vitals.processing_run_id → biometric_processing_runs, kind 'live', same side), engine =
          the run's bundle manifestHash, profile = its config_hash. A restart under the same
          config is a new run with the same identity. A night whose window holds more than one
          identity, or rows with and without one, is `mixed`; one with no recorded run is
          `unknown`. Runtime receipts and configs can't stand in: they don't name the running
          engine, and the default profile (generation 0) has neither.
  build   core commit from the first archive pull after the night (pulls/*.json manifest
          `build`): the build serving the export, an upper bound on the one that recorded it.
  stage   the pod restages on read, so stored stages follow the selection at read time,
          from an optional saved GET /api/sleep-stages/models?side= response.

Reads local files only: the archive's biometrics.db and pull records.
"""
import argparse
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3
import sys
from zoneinfo import ZoneInfo

# Core's vitals identity (src/biometrics/profiles/vital-summary-provenance.ts, sleepypod/core#930):
# `<manifestHash>:<configHash>` of the live same-side run that produced a vitals row, else NULL.
MANIFEST = "CASE WHEN json_valid(r.bundle) THEN json_extract(r.bundle, '$.manifestHash') END"
LIVE = "(r.kind = 'live' AND length(r.config_hash) = 64 AND r.config_hash NOT GLOB '*[^a-f0-9]*')"
CONFIGURATION = (f"CASE WHEN {LIVE} AND typeof({MANIFEST}) = 'text' AND length({MANIFEST}) = 64 "
                 f"AND {MANIFEST} NOT GLOB '*[^a-f0-9]*' THEN {MANIFEST} || ':' || r.config_hash END")


def connect(path):
    return sqlite3.connect(f'file:{Path(path).resolve()}?mode=ro', uri=True)


def tables(db):
    return {row[0] for row in db.execute("select name from sqlite_master where type='table'")}


def columns(db, table):
    return {row[1] for row in db.execute(f'pragma table_info("{table}")')}


def night_of(entered_s, time_zone):
    """Noon-to-noon local date, as export-runtime-features.ts groups sessions."""
    return (datetime.fromtimestamp(entered_s, ZoneInfo(time_zone)) - timedelta(hours=12)).date().isoformat()


def summary(groups):
    """Core's nightVitalsIdentity over {identity or None: rows}: one identity, `mixed`, `unknown`, or None without vitals."""
    if not groups:
        identity = None
    elif len(groups) > 1:
        identity = 'mixed'  # A change mid-night, or a night only partly recorded.
    else:
        only = next(iter(groups))
        identity = 'unknown' if only is None else dict(zip(('engine', 'profile'), only.split(':')))
    versions = [dict(zip(('engine', 'profile'), k.split(':')), minutes=n) for k, n in sorted(groups.items(), key=lambda kv: -kv[1]) if k]
    one = identity if isinstance(identity, dict) else {}
    return dict(method='processing-runs', identity=identity, engine=one.get('engine'), profile=one.get('profile'),
                mixed=None if identity is None else identity == 'mixed', versions=versions, unknownMinutes=groups.get(None, 0))


class Lineage:
    """Per-night vitals identity over one archive database; rows without a recorded live run are unknown."""

    def __init__(self, db):
        self.db = db
        names = tables(db)
        self.has_vitals = 'vitals' in names
        self.has_runs = self.has_vitals and 'biometric_processing_runs' in names and 'processing_run_id' in columns(db, 'vitals')

    def vitals(self, side, start_s, end_s):
        """Identity of the side's vitals rows in [start_s, end_s], inclusive like core."""
        if not self.has_vitals:
            return summary({})
        if self.has_runs:
            rows = self.db.execute(f'select {CONFIGURATION}, count(*) from vitals v left join biometric_processing_runs r '
                                   'on v.processing_run_id = r.id and v.side = r.side '
                                   'where v.side = ? and v.timestamp >= ? and v.timestamp <= ? group by 1', (side, start_s, end_s)).fetchall()
        else:
            rows = self.db.execute('select null, count(*) from vitals where side = ? and timestamp >= ? and timestamp <= ? having count(*) > 0',
                                   (side, start_s, end_s)).fetchall()
        return summary(dict(rows))


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
    identity = v['identity']
    if identity is None:
        return 'no vitals'
    if identity == 'mixed':
        return f"mixed ({len(v['versions'])}{' + unrecorded' if v['unknownMinutes'] else ''})"
    if identity == 'unknown':
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
    print(f"{'night':10} {'side':5} {'vitals engine/profile':24} {'build':8} stage")
    for r in rows:
        print(f"{r['night']:10} {r['side']:5} {vitals_label(r['vitals']):24} {short(r['build']['commit']):8} "
              f"{r['stage']['version'] or '-'}")
    versions = {(x['engine'], x['profile']) for r in rows for x in r['vitals']['versions']}
    print(f"{len(rows)} nights, {sum(1 for r in rows if r['vitals']['mixed'])} mixed, {len(versions)} vitals versions")


if __name__ == '__main__':
    sys.exit(main())
