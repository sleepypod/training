"""Derived biometrics snapshot: replayed vitals from one pinned engine and profile (plan C5).

  ingest  REPLAY_DIR             cache core's replay readings by (night, engine, profile)
  derive  SOURCE.db --out NEW.db copy the database and replace the side's `vitals` and
                                 `vitals_quality` rows for each replayed night with readings

Readings come from core: `pnpm biometrics:local-fit replay --archive DEST/POD_ID ... OUT`
writes OUT/replay.json and OUT/readings/<night>-<profile12>.json (`--save-readings` format).
The cache is data/cache/vitals/<night>-<engine12>-<profile12>.json, the readings file
verbatim, with a `.source.json` beside it. A night whose replay failed core's stored-row
parity gate (`--compare-stored`) is never derived: its replay is not used for training. Every other table is copied unchanged, so
export-runtime-features.ts and the stage scripts run on the derived snapshot as on any
other. A `derived_snapshot` table records, per night, the source database SHA-256, engine,
profile, readings SHA-256 and replay.json SHA-256. Local files only; never contacts a pod.
"""
import argparse
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_history import primary_root

READINGS_KEYS = ('side', 'timestampMs', 'heartRate', 'hrvIndex', 'breathingRate', 'qualityScore', 'flags', 'hrRaw', 'configHash')
SIDECARS = ('pod-stages.json',)  # The pod's live stages: unchanged by replayed vitals.
TABLE = '''CREATE TABLE derived_snapshot (night TEXT PRIMARY KEY, side TEXT NOT NULL, source_sha256 TEXT NOT NULL,
    engine TEXT NOT NULL, profile TEXT NOT NULL, readings_sha256 TEXT NOT NULL, replay_sha256 TEXT,
    start INTEGER NOT NULL, end INTEGER NOT NULL, removed_vitals INTEGER NOT NULL, readings INTEGER NOT NULL,
    script_sha256 TEXT NOT NULL)'''


def default_cache():
    return primary_root() / 'data/cache/vitals'


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def read_readings(path):
    """The one reader of core's readings JSON ({cohortId, configHash, side, readings: [...]})."""
    doc = json.loads(Path(path).read_text())
    rows = doc.get('readings')
    if not isinstance(rows, list) or not isinstance(doc.get('configHash'), str) or doc.get('side') not in ('left', 'right'):
        raise ValueError(f'{path}: not a core replay readings file')
    for row in rows:
        missing = [k for k in READINGS_KEYS if k not in row]
        if missing:
            raise ValueError(f'{path}: reading without {", ".join(missing)}')
    return dict(night=doc.get('cohortId'), profile=doc['configHash'], side=doc['side'],
                rows=[{k: row[k] for k in READINGS_KEYS} for row in rows])


def read_replay(directory):
    """core's replay.json: engine and profile, side, timezone, and each night's window and readings file."""
    path = Path(directory) / 'replay.json'
    doc = json.loads(path.read_text())
    if doc.get('version') != 'biometrics-local-fit-replay-v1':
        raise ValueError(f'{path}: expected biometrics-local-fit-replay-v1')
    return dict(engine=doc['engine']['manifestHash'], profile=doc['config']['configHash'], overrides=doc['config'].get('overrides'),
                side=doc['side'], timezone=doc['timezone'], sha256=sha256(path), archive=doc.get('archive'),
                nights=[dict(night=n['night'], startMs=n['scoreStartMs'], endMs=n['scoreEndMs'], readings=Path(directory) / n['readingsFile'],
                             parity=(n.get('parity') or {}).get('gate')) for n in doc['nights']],
                skipped=doc.get('skipped', []))


def cache_name(night, engine, profile):
    return f'{night}-{engine[:12]}-{profile[:12]}'


def cached(cache, night, engine, profile):
    """(readings path, source record) for a cached night, or None."""
    base = Path(cache) / cache_name(night, engine, profile)
    source = base.with_name(base.name + '.source.json')
    if not source.is_file():
        return None
    record = json.loads(source.read_text())
    if (record['engine'], record['profile'], record['night']) != (engine, profile, night):
        raise ValueError(f'{source}: 12-hex cache key collides with a different engine or profile')
    return base.with_name(base.name + '.json'), record


def skipped(cache, night, engine, profile):
    """Why core could not replay this night with this engine and profile, if it said so."""
    path = Path(cache) / (cache_name(night, engine, profile) + '.skipped.json')
    return json.loads(path.read_text())['reason'] if path.is_file() else None


def ingest(directory, cache=None):
    """Copy a replay's readings into the cache; identical bytes only, never overwrite."""
    cache = Path(cache or default_cache())
    cache.mkdir(parents=True, exist_ok=True)
    replay = read_replay(directory)
    out = []
    for night in replay['nights']:
        readings = read_readings(night['readings'])
        if readings['profile'] != replay['profile'] or readings['side'] != replay['side'] or readings['night'] != night['night']:
            raise ValueError(f"{night['readings']}: night, side or profile differs from replay.json")
        base = cache / cache_name(night['night'], replay['engine'], replay['profile'])
        target, digest = base.with_name(base.name + '.json'), sha256(night['readings'])
        if target.exists() and sha256(target) != digest:
            raise ValueError(f'{target} is cached with different readings: one engine and profile must replay identically')
        if not target.exists():
            shutil.copyfile(night['readings'], target)
        record = dict(night=night['night'], engine=replay['engine'], profile=replay['profile'], side=replay['side'],
                      timezone=replay['timezone'], startMs=night['startMs'], endMs=night['endMs'], readingsSha256=digest,
                      replaySha256=replay['sha256'], overrides=replay['overrides'], parity=night['parity'])
        base.with_name(base.name + '.source.json').write_text(json.dumps(record, indent=2) + '\n')
        out.append(record)
    for row in replay['skipped']:
        (cache / (cache_name(row['night'], replay['engine'], replay['profile']) + '.skipped.json')).write_text(
            json.dumps(dict(night=row['night'], reason=row['reason'], replaySha256=replay['sha256'])) + '\n')
    return replay, out


def night_of(seconds, timezone):
    """core nightOf: the local noon-to-noon night a time belongs to."""
    local = datetime.fromtimestamp(seconds, ZoneInfo(timezone))
    return (local.date() - timedelta(days=1) if local.hour < 12 else local.date()).isoformat()


def night_windows(db, side, timezone):
    """{night: (first entered_bed_at, last left_bed_at)} in Unix seconds, as core's replay windows them."""
    windows = {}
    for start, end in db.execute('select entered_bed_at, left_bed_at from sleep_records where side=? and left_bed_at > entered_bed_at', (side,)):
        night = night_of(start, timezone)
        a, b = windows.get(night, (start, end))
        windows[night] = (min(a, start), max(b, end))
    return windows


def columns(db, table):
    return {row[1]: row for row in db.execute(f'pragma table_info({table})')}


def processing_run(db, side, engine, profile, nights, start_ms):
    """A `replay` processing run for the inserted rows, when the schema has one; else None."""
    known = columns(db, 'biometric_processing_runs')
    values = dict(id=f'derived-{side}-{engine[:12]}-{profile[:12]}', side=side, kind='replay',
                  bundle=json.dumps(dict(source='training derive_snapshot.py', engine=engine)), config_hash=profile, calibration_hash=None,
                  input_identity=json.dumps(dict(readingsSha256=nights), sort_keys=True), started_at_ms=start_ms, ended_at_ms=None)
    required = {name for name, row in known.items() if row[3] and row[4] is None and not row[5]}
    if not known or not required <= set(values) or 'processing_run_id' not in columns(db, 'vitals'):
        return None
    names = [k for k in values if k in known]
    db.execute(f'insert into biometric_processing_runs ({",".join(names)}) values ({",".join("?" * len(names))})', [values[k] for k in names])
    return values['id']


def derive(source, out, engine, profile, side, timezone, nights=None, cache=None):
    """Write `out`: `source` with each listed night's vitals replaced by its cached readings."""
    source, out, cache = Path(source), Path(out), Path(cache or default_cache())
    if out.exists():
        raise ValueError(f'{out} exists; choose a new path')
    source_sha = sha256(source)
    with sqlite3.connect(f'file:{source.resolve()}?mode=ro', uri=True) as db:
        if db.execute("select 1 from sqlite_master where name='derived_snapshot'").fetchone():
            raise ValueError(f'{source} is already a derived snapshot; derive from the archive database')
        windows = night_windows(db, side, timezone)
    wanted = sorted(windows) if nights is None else sorted(set(nights))
    plan, live = [], []
    for night in wanted:
        hit = cached(cache, night, engine, profile)
        if hit is None:
            if nights is not None:
                raise ValueError(f'{night}: no cached readings for engine {engine[:12]} profile {profile[:12]}; run core replay, then ingest')
            live.append(night)
            continue
        if night not in windows:
            raise ValueError(f'{night}: no {side} sleep record in {source}')
        path, record = hit
        if record.get('parity') == 'fail':
            if nights is not None:
                raise ValueError(f'{night}: the replay failed the stored-row parity gate, so it is not used for training')
            live.append(night)
            continue
        start, end = windows[night]
        if (record['startMs'], record['endMs']) != (start * 1000, end * 1000) or record['side'] != side:
            raise ValueError(f'{night}: replay window or side differs from this database; sleep records changed since the replay')
        if sha256(path) != record['readingsSha256']:
            raise ValueError(f'{path}: cached readings changed')
        plan.append((night, start, end, path, record))
    if not plan:
        raise ValueError('No night has cached readings for this engine and profile')
    out.parent.mkdir(parents=True, exist_ok=True)
    partial = out.with_name(out.name + '.partial')
    partial.unlink(missing_ok=True)
    src, dst = sqlite3.connect(f'file:{source.resolve()}?mode=ro', uri=True), sqlite3.connect(partial)
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()
    script = sha256(__file__)
    db = sqlite3.connect(partial)
    try:
        with db:
            quality = columns(db, 'vitals_quality')
            run_id = processing_run(db, side, engine, profile, {n: r['readingsSha256'] for n, *_, r in plan}, plan[0][1] * 1000)
            db.execute(TABLE)
            for night, start, end, path, record in plan:
                readings = read_readings(path)
                rows = [r for r in readings['rows'] if r['side'] == side and start <= r['timestampMs'] // 1000 < end]
                if any(r['configHash'] != profile for r in rows):
                    raise ValueError(f'{path}: a {side} reading was computed with another profile')
                stamps = [r['timestampMs'] // 1000 for r in rows]
                if len(set(stamps)) != len(stamps):
                    raise ValueError(f'{path}: two readings in one second')
                ids = [i for i, in db.execute('select rowid from vitals where side=? and timestamp>=? and timestamp<?', (side, start, end))]
                if quality:
                    db.execute('delete from vitals_quality where side=? and timestamp>=? and timestamp<?', (side, start, end))
                    db.executemany('delete from vitals_quality where vitals_id=?', [(i,) for i in ids])
                db.execute('delete from vitals where side=? and timestamp>=? and timestamp<?', (side, start, end))
                for r, stamp in zip(rows, stamps):
                    values = dict(side=side, timestamp=stamp, heart_rate=r['heartRate'], hrv=r['hrvIndex'], breathing_rate=r['breathingRate'])
                    if run_id:
                        values['processing_run_id'] = run_id
                    vitals_id = db.execute(f'insert into vitals ({",".join(values)}) values ({",".join("?" * len(values))})', list(values.values())).lastrowid
                    if quality:
                        # The pod writes flags as JSON only when there are any; created_at is the reading time so the copy is deterministic.
                        q = dict(vitals_id=vitals_id, side=side, timestamp=stamp, quality_score=r['qualityScore'],
                                 flags=json.dumps(r['flags']) if r['flags'] else None, hr_raw=r['hrRaw'], created_at=stamp)
                        q = {k: v for k, v in q.items() if k in quality}
                        db.execute(f'insert into vitals_quality ({",".join(q)}) values ({",".join("?" * len(q))})', list(q.values()))
                db.execute('insert into derived_snapshot values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                           (night, side, source_sha, engine, profile, record['readingsSha256'], record.get('replaySha256'), start, end, len(ids), len(rows), script))
    finally:
        db.close()
    partial.rename(out)
    for name in SIDECARS:
        if (source.parent / name).is_file() and not (out.parent / name).exists():
            shutil.copyfile(source.parent / name, out.parent / name)
    return dict(out=str(out), sha256=sha256(out), source=source_sha, engine=engine, profile=profile, side=side,
                replayed=[n for n, *_ in plan], live=live)


def replayed(db):
    """{night: derived_snapshot row} of a derived snapshot; {} for any other database."""
    if not db.execute("select 1 from sqlite_master where name='derived_snapshot'").fetchone():
        return {}
    db.row_factory = sqlite3.Row
    try:
        return {row['night']: dict(row) for row in db.execute('select * from derived_snapshot')}
    finally:
        db.row_factory = None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='command', required=True)
    i = sub.add_parser('ingest', help='cache a core replay output directory')
    i.add_argument('replay', type=Path, help='OUTPUT_DIR of pnpm biometrics:local-fit replay')
    i.add_argument('--cache', type=Path, help='default: data/cache/vitals in the primary checkout')
    d = sub.add_parser('derive', help='write a derived snapshot from cached readings')
    d.add_argument('source', type=Path, help='archive biometrics.db (or any snapshot with the pod schema)')
    d.add_argument('--out', type=Path, required=True, help='new derived database path')
    d.add_argument('--replay', type=Path, help='ingest this replay and derive its nights, engine, profile, side and timezone')
    d.add_argument('--engine', help='biometrics manifest hash, with --profile, to derive from the cache alone')
    d.add_argument('--profile', help='resolved profile config hash')
    d.add_argument('--side', choices=['left', 'right'])
    d.add_argument('--timezone', help='IANA timezone of the night key')
    d.add_argument('--nights', help='comma-separated nights; default every night of the side with cached readings')
    d.add_argument('--cache', type=Path, help='default: data/cache/vitals in the primary checkout')
    args = ap.parse_args(argv)
    if args.command == 'ingest':
        replay, rows = ingest(args.replay, args.cache)
        print(f"Cached {len(rows)} nights for engine {replay['engine'][:12]} profile {replay['profile'][:12]}"
              + (f"; core skipped {len(replay['skipped'])}" if replay['skipped'] else ''))
        return 0
    if args.replay:
        if args.engine or args.profile:
            ap.error('--replay names its own engine and profile')
        replay, _ = ingest(args.replay, args.cache)
        engine, profile, side, timezone = replay['engine'], replay['profile'], replay['side'], replay['timezone']
        nights = [n['night'] for n in replay['nights']]
    else:
        if not (args.engine and args.profile and args.side and args.timezone):
            ap.error('without --replay, give --engine, --profile, --side and --timezone')
        engine, profile, side, timezone = args.engine, args.profile, args.side, args.timezone
        nights = args.nights.split(',') if args.nights else None
    result = derive(args.source, args.out, engine, profile, side, timezone, nights, args.cache)
    print(f"Derived {result['out']} ({result['sha256'][:12]}): {len(result['replayed'])} nights replayed with engine {engine[:12]} profile {profile[:12]}")
    if result['live']:
        print(f"Still live vitals (no cached readings): {', '.join(result['live'])}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
