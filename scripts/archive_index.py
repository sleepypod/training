"""Night index over the off-pod archive (plan A1b, A3): one row per sleep record per side.

For every sleep record in data/archive/<podId>/biometrics.db: in-bed hours, raw piezo
coverage of the in-bed seconds and where that raw lives (`archive`, or
`local-export:<path>` for /api/export/archive tarballs saved before the archive
existed), Watch teachers per type (pod rows preferred, else the night's sidecar under
watch/<night>.json), vitals lineage (lineage.py), whether the night is in the locked
holdout, and whether its raw is at risk: only on the pod and within 3 days of the pod's
oldest raw, which the pruner deletes next. At-risk nights print first.

Writes <archive>/index.json. Reads local files only; never contacts the pod. Decoded
raw coverage is cached by RAW file SHA-256 under data/cache/raw-coverage/.
"""
import argparse
import bisect
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lineage as ln
import raw_piezo as rp
from run_history import primary_root

try:
    from holdout import locked
    LOCKED_RULE = 'holdout.locked'
except ImportError:  # Until feat/locked-holdout merges: the same rule (plan A5, decision Q4).
    from datetime import date
    FREEZE, EVERY = '2026-11-14', 4
    LOCKED_RULE = f'local copy: night groups on or after {FREEZE} whose day offset is a multiple of {EVERY}'

    def locked(night):
        offset = (date.fromisoformat(night) - date.fromisoformat(FREEZE)).days
        return offset >= 0 and offset % EVERY == 0

VERSION = 'archive-index-v1'
COVERAGE_VERSION = 'raw-coverage-v1'
AT_RISK_DAYS = 3
# Archive files are selected by mtime like the pod's export: a frame is gzipped ~15 min after
# it closes, so a file stamped 20 min before bed can hold in-bed seconds. The lag is generous;
# only seconds inside the window count.
MTIME_LEAD_S, MTIME_LAG_S = 20 * 60, 3 * 3600
# A pull whose RAW starts this long after its requested window began reached the pod's oldest file.
OLDEST_GAP_MS = 3600 * 1000
TEACHERS = ('stages', 'heartRate', 'breathingRate', 'hrvSdnn', 'motion')
LETTERS = dict(stages='S', heartRate='H', breathingRate='B', hrvSdnn='V', motion='M')
SIDECAR_KEYS = dict(stages='stages', heartRate='heartRate', breathingRate='respiratoryRate', hrvSdnn='hrvSdnn')


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        while chunk := f.read(1 << 22):
            digest.update(chunk)
    return digest.hexdigest()


def piezo_runs(raw):
    """[[start, end), …] runs of whole seconds holding a piezo-dual frame."""
    seconds = sorted({rec['ts'] for rec, _ in rp.records(raw) if rec and rec.get('type') == 'piezo-dual' and isinstance(rec.get('ts'), int)})
    runs = []
    for s in seconds:
        if runs and runs[-1][1] == s:
            runs[-1][1] = s + 1
        else:
            runs.append([s, s + 1])
    return runs


def merge(intervals):
    out = []
    for a, b in sorted(intervals):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def overlap(merged, start, end):
    """Seconds of [start, end) inside sorted disjoint intervals."""
    total = 0
    for a, b in merged[max(0, bisect.bisect_right([m[0] for m in merged], start) - 1):]:
        if a >= end:
            break
        total += max(0, min(b, end) - max(a, start))
    return total


def decode_stored(name, stored):
    return dict(version=COVERAGE_VERSION, sha256=hashlib.sha256(stored).hexdigest(), name=name,
                piezoSeconds=piezo_runs(rp.decompress(name, stored)))


def decode_archive_file(job):
    path, name, sha = job
    stored = Path(path).read_bytes()
    entry = decode_stored(name, stored)
    return sha, entry if entry['sha256'] == sha else None


def decode_export(path):
    """Every RAW member's SHA-256 and piezo runs; the parent caches them."""
    return [decode_stored(name, stored) for name, stored in rp.export_members(path)]


class Cache:
    def __init__(self, root):
        self.root = Path(root)

    def _read(self, path):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            return None
        return data if data.get('version') == COVERAGE_VERSION else None

    def _write(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f'.tmp.{os.getpid()}')
        tmp.write_text(json.dumps(data))
        tmp.replace(path)

    def file(self, sha):
        return self._read(self.root / f'{sha}.json')

    def put_file(self, entry):
        self._write(self.root / f"{entry['sha256']}.json", entry)

    def export(self, sha):
        listing = self._read(self.root / 'exports' / f'{sha}.json')
        if listing is None:
            return None
        files = [self.file(m['sha256']) for m in listing['members']]
        return None if any(f is None for f in files) else files

    def put_export(self, sha, entries):
        for entry in entries:
            self.put_file(entry)
        self._write(self.root / 'exports' / f'{sha}.json', dict(version=COVERAGE_VERSION, sha256=sha,
                                                                 members=[dict(name=e['name'], sha256=e['sha256']) for e in entries]))


def archive_files(db, pod_dir):
    """(sha256, path, name, mtime_s) for every archived RAW file."""
    pod_dir = Path(pod_dir)
    if 'archive_raw_files' in ln.tables(db):
        rows = db.execute('select sha256, archive_name, mtime_ms from archive_raw_files').fetchall()
        out = []
        for sha, name, mtime_ms in rows:
            path = pod_dir / 'raw' / f'{sha}.RAW.gz'
            out.append((sha, path if path.exists() else pod_dir / 'archive' / name, name, mtime_ms / 1000))
        return out
    return [(p.name.split('.')[0], p, p.name, p.stat().st_mtime) for p in sorted((pod_dir / 'raw').glob('*.RAW.gz'))]


def archive_coverage(files, records, cache, workers=4, log=print):
    """Merged piezo runs of the archive files whose mtime can reach a sleep record."""
    windows = merge([[a - MTIME_LEAD_S, b + MTIME_LAG_S] for _, _, a, b in records])
    wanted = [f for f in files if overlap(windows, f[3], f[3] + 1)]
    entries, missing = {}, []
    for sha, path, name, _ in wanted:
        cached = cache.file(sha)
        if cached:
            entries[sha] = cached
        elif Path(path).exists():
            missing.append((str(path), name, sha))
    if missing:
        log(f'Decoding {len(missing)} archive RAW files (cached by SHA-256 for next time)…')
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for sha, entry in pool.map(decode_archive_file, missing):
                if entry is None:
                    log(f'warning: archive RAW {sha} does not match its SHA-256; skipped')
                    continue
                cache.put_file(entry)
                entries[sha] = entry
    absent = len(wanted) - len(entries)
    return merge([run for e in entries.values() for run in e['piezoSeconds']]), absent


def export_coverage(paths, cache, workers=4, log=print):
    """{path: merged piezo runs} for /api/export/archive tarballs."""
    hashes = {str(p): sha256_file(p) for p in paths}
    out, todo = {}, []
    for path, sha in hashes.items():
        files = cache.export(sha)
        if files is None:
            todo.append(path)
        else:
            out[path] = merge([r for f in files for r in f['piezoSeconds']])
    if todo:
        log(f'Decoding {len(todo)} local exports (cached by SHA-256 for next time)…')
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for path, files in zip(todo, pool.map(decode_export, todo)):
                cache.put_export(hashes[path], files)
                out[path] = merge([r for f in files for r in f['piezoSeconds']])
    return out


def display_path(path):
    path = Path(path).resolve()
    try:
        return str(path.relative_to(primary_root()))
    except ValueError:
        return str(path)


def raw_oldest(storage_path, pulls, now_ms):
    """The pod's oldest RAW archive time (ms) and how it is known, from local files only."""
    if storage_path:
        oldest = (json.loads(Path(storage_path).read_text()).get('rawHistory') or {}).get('oldest')
        if oldest:
            as_of = datetime.fromtimestamp(Path(storage_path).stat().st_mtime, timezone.utc).isoformat(timespec='seconds')
            return dict(oldestMs=ln.iso_ms(oldest), basis='storage', asOf=as_of, source=display_path(storage_path))
    for record in reversed(pulls):
        oldest = ((record.get('manifest') or {}).get('rawHistory') or {}).get('oldest')
        if oldest:
            return dict(oldestMs=ln.iso_ms(oldest), basis='pull-manifest', asOf=record['pulledAt'], source=record['_path'])
    for record in reversed(pulls):
        window = (record.get('manifest') or {}).get('window') or {}
        served, selection = window.get('served') or {}, window.get('rawSelection') or {}
        first, start = served.get('rawMtimeMinMs'), selection.get('mtimeFromMs')
        if first is not None and start is not None and first - start > OLDEST_GAP_MS:
            # The pruner keeps a roughly constant span, so the oldest file advances with the clock.
            pulled = ln.iso_ms(record['pulledAt'])
            return dict(oldestMs=first + max(0, now_ms - pulled), basis='pull-window-estimate', asOf=record['pulledAt'],
                        source=record['_path'], observedOldestMs=first)
    return dict(oldestMs=None, basis='unknown', asOf=None, source=None)


def load_sidecars(pod_dir):
    """{side: {teacher: [[startMs, endMs, …], …]}} from watch/<night>.json files."""
    out = {}
    for path in sorted((Path(pod_dir) / 'watch').glob('*.json')):
        data = json.loads(path.read_text())
        for side, entry in (data.get('sides') or {}).items():
            merged = out.setdefault(side, {k: [] for k in SIDECAR_KEYS})
            for teacher, key in SIDECAR_KEYS.items():
                merged[teacher].extend(entry.get(key) or [])
    for teachers in out.values():
        for rows in teachers.values():
            rows.sort()
    return out


def sidecar_count(rows, start_s, end_s, interval):
    if interval:
        return sum(1 for r in rows if r[0] < end_s * 1000 and r[1] > start_s * 1000)
    i = bisect.bisect_left(rows, [start_s * 1000])
    j = bisect.bisect_left(rows, [end_s * 1000])
    return j - i


class Teachers:
    def __init__(self, db, sidecars):
        self.db, self.sidecars = db, sidecars
        names = ln.tables(db)
        live = ''
        if 'reference_imports' in names:
            # The archive never deletes, so rows from imports deleted on the pod are skipped here.
            live = ' and (import_id is null or import_id not in (select id from reference_imports where deleted_at_ms is not null))'
        def scoped(table):
            return live if live and 'import_id' in ln.columns(db, table) else ''
        self.queries = {}
        if 'reference_stages' in names:
            self.queries['stages'] = f'select count(*) from reference_stages where side=? and start<? and "end">?{scoped("reference_stages")}', 'overlap-s'
        if 'reference_vitals' in names:
            self.queries['heartRate'] = f'select count(*) from reference_vitals where side=? and timestamp>=? and timestamp<?{scoped("reference_vitals")}', 'range-s'
        if 'reference_samples' in names:
            for teacher, metric in (('breathingRate', 'breathing_rate'), ('hrvSdnn', 'hrv_sdnn')):
                self.queries[teacher] = (f"select count(*) from reference_samples where side=? and metric='{metric}' "
                                         f'and timestamp>=? and timestamp<?{scoped("reference_samples")}'), 'range-s'
        if 'watch_motion_captures' in names:
            self.queries['motion'] = 'select count(*) from watch_motion_captures where side=? and recorded_from<? and recorded_to>?', 'overlap-ms'

    def night(self, side, start_s, end_s):
        out = {}
        for teacher in TEACHERS:
            pod = None
            if teacher in self.queries:
                query, kind = self.queries[teacher]
                params = {'overlap-s': (side, end_s, start_s), 'range-s': (side, start_s, end_s),
                          'overlap-ms': (side, end_s * 1000, start_s * 1000)}[kind]
                pod = self.db.execute(query, params).fetchone()[0]
            if pod:
                out[teacher] = dict(source='pod', count=pod)
                continue
            rows = (self.sidecars.get(side) or {}).get(teacher)
            sidecar = sidecar_count(rows, start_s, end_s, teacher == 'stages') if rows is not None else None
            if sidecar:
                out[teacher] = dict(source='sidecar', count=sidecar)
            else:
                out[teacher] = dict(source=None, count=0 if pod is not None or sidecar is not None else None)
        return out


def raw_status(coverage, best, entered, left, oldest_ms):
    if coverage > 0:
        return best, False
    if oldest_ms is None:
        return 'pod-only?', None
    if left * 1000 < oldest_ms:
        return 'lost', False
    return 'pod-only', entered * 1000 < oldest_ms + AT_RISK_DAYS * 86_400_000


def build_index(pod_dir, time_zone, side=None, exports=(), storage=None, stage_models=None, cache_dir=None,
                workers=4, now_ms=None, log=None):
    pod_dir, log = Path(pod_dir), log or print
    now_ms = now_ms if now_ms is not None else round(datetime.now(timezone.utc).timestamp() * 1000)
    cache = Cache(cache_dir or primary_root() / 'data/cache/raw-coverage')
    pulls = ln.load_pulls(pod_dir)
    with ln.connect(pod_dir / 'biometrics.db') as db:
        records = ln.sleep_records(db, side)
        archived, absent = archive_coverage(archive_files(db, pod_dir), records, cache, workers, log)
        local = export_coverage([Path(p) for p in exports], cache, workers, log)
        teachers = Teachers(db, load_sidecars(pod_dir))
        lineage = {r['sleepRecordId']: r for r in ln.night_lineage(ln.Lineage(db), pulls, records, time_zone, stage_models)}
        oldest = raw_oldest(storage, pulls, now_ms)
        sources = {'archive': archived, **{f'local-export:{display_path(p)}': runs for p, runs in local.items()}}
        every = merge([r for runs in sources.values() for r in runs])
        nights = []
        for record_id, record_side, entered, left in records:
            seconds = max(1, left - entered)
            per_source = {k: v for k, runs in sources.items() if (v := overlap(runs, entered, left))}
            union = overlap(every, entered, left) if per_source else 0
            best = max(per_source, key=lambda k: (per_source[k], k == 'archive')) if per_source else None
            source, at_risk = raw_status(union, best, entered, left, oldest['oldestMs'])
            watch = teachers.night(record_side, entered, left)
            night = lineage[record_id]['night']
            nights.append(dict(
                night=night, side=record_side, sleepRecordId=record_id, enteredBedAt=entered, leftBedAt=left,
                inBedHours=round((left - entered) / 3600, 2),
                raw=dict(coverage=round(union / seconds, 4), seconds=union, source=source,
                         sources={k: round(v / seconds, 4) for k, v in per_source.items()}),
                watch=watch, watchMissing=[t for t in TEACHERS if watch[t]['source'] is None],
                sidecar=any(w['source'] == 'sidecar' for w in watch.values()),
                lineage={k: lineage[record_id][k] for k in ('vitals', 'build', 'stage')},
                locked=locked(night), atRisk=at_risk))
    archive_db = pod_dir / 'biometrics.db'
    return dict(version=VERSION, generatedAt=datetime.fromtimestamp(now_ms / 1000, timezone.utc).isoformat(timespec='seconds'),
                podId=pod_dir.name, timezone=time_zone, nightGrouping='noon-to-noon local date of entered_bed_at',
                archive=dict(path=display_path(pod_dir), databaseSha256=sha256_file(archive_db), pulls=len(pulls),
                             cursor=pulls[-1]['cursor']['end'] if pulls and pulls[-1].get('cursor') else None,
                             lastPullAt=pulls[-1]['pulledAt'] if pulls else None, missingRawFiles=absent),
                localExports=[display_path(p) for p in exports],
                rawHistory=dict(oldest=None if oldest['oldestMs'] is None else datetime.fromtimestamp(oldest['oldestMs'] / 1000, timezone.utc).isoformat(timespec='seconds'),
                                **{k: v for k, v in oldest.items() if k != 'oldestMs'}),
                atRiskDays=AT_RISK_DAYS, lockedRule=LOCKED_RULE, teachers=list(TEACHERS), nights=nights)


def ordered(nights):
    return sorted(nights, key=lambda n: (not n['atRisk'], n['enteredBedAt'], n['side']))


def watch_cell(watch):
    return ''.join(LETTERS[t] if watch[t]['source'] == 'pod' else LETTERS[t].lower() if watch[t]['source'] == 'sidecar' else '.'
                   for t in TEACHERS)


def table(index):
    lines = [f"{'night':10} {'side':5} {'bed h':>5} {'raw':>5} {'raw source':28} {'watch':5} {'vitals':22} {'lock':4} risk"]
    for n in ordered(index['nights']):
        source = n['raw']['source'] or '-'
        if source.startswith('local-export:'):
            source = 'local-export:' + Path(source.split(':', 1)[1]).name
        lines.append(f"{n['night']:10} {n['side']:5} {n['inBedHours']:5.1f} {n['raw']['coverage']:5.0%} {source[:28]:28} "
                     f"{watch_cell(n['watch']):5} {ln.vitals_label(n['lineage']['vitals'])[:22]:22} {'yes' if n['locked'] else '':4} "
                     f"{'AT RISK' if n['atRisk'] else '?' if n['atRisk'] is None else ''}")
    history = index['rawHistory']
    lines.append(f"Watch: S stages, H heart rate, B breathing, V HRV SDNN, M motion; capital = pod rows, lower case = sidecar, . = missing.")
    lines.append(f"Pod oldest raw: {history['oldest'] or 'unknown'} ({history['basis']}{', as of ' + history['asOf'] if history['asOf'] else ''}). "
                 f"{sum(1 for n in index['nights'] if n['atRisk'])} at risk, {sum(1 for n in index['nights'] if n['raw']['source'] == 'lost')} lost, "
                 f"{sum(1 for n in index['nights'] if n['watchMissing'])} missing a Watch teacher.")
    if history['basis'] == 'unknown':
        lines.append('Save GET /api/system/storage to a file and pass --storage to flag nights at risk.')
    return lines


def default_archive():
    root = primary_root() / 'data/archive'
    pods = [p for p in root.iterdir() if (p / 'biometrics.db').is_file()] if root.is_dir() else []
    if len(pods) != 1:
        raise SystemExit(f'Pass --archive data/archive/<podId>: found {len(pods)} pod archives under {root}')
    return pods[0]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--archive', help='archive pod directory, data/archive/<podId> (default: the only one)')
    ap.add_argument('--timezone', required=True, help='IANA timezone for noon-to-noon night labels')
    ap.add_argument('--side', choices=('left', 'right'), help='one side only (default both)')
    ap.add_argument('--local-export', action='append', default=[], help='/api/export/archive tarball saved before the archive; repeatable')
    ap.add_argument('--local-exports-dir', action='append', default=[], help='directory searched recursively for *.tar.gz exports; repeatable')
    ap.add_argument('--storage', help='saved GET /api/system/storage response, for the pod\'s oldest raw')
    ap.add_argument('--stage-models', action='append', help='side=FILE saved from GET /api/sleep-stages/models?side=; repeatable')
    ap.add_argument('--cache', help='decoded coverage cache (default: <primary checkout>/data/cache/raw-coverage)')
    ap.add_argument('--out', help='index path (default: <archive>/index.json)')
    ap.add_argument('--workers', type=int, default=4)
    args = ap.parse_args(argv)
    ZoneInfo(args.timezone)
    pod_dir = Path(args.archive) if args.archive else default_archive()
    if not (pod_dir / 'biometrics.db').is_file():
        raise SystemExit(f'{pod_dir / "biometrics.db"} not found; run the archive pull first (scripts/install-archive-agent.sh)')
    exports = [Path(p) for p in args.local_export]
    for directory in args.local_exports_dir:
        exports.extend(sorted(Path(directory).rglob('*.tar.gz')))
    exports = list(dict.fromkeys(p.resolve() for p in exports))
    index = build_index(pod_dir, args.timezone, args.side, exports, args.storage, ln.parse_stage_models(args.stage_models),
                        args.cache, args.workers)
    out = Path(args.out) if args.out else pod_dir / 'index.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix('.tmp')
    tmp.write_text(json.dumps(index, indent=2))
    tmp.replace(out)
    print('\n'.join(table(index)))
    print(f'Index: {out}')


if __name__ == '__main__':
    sys.exit(main())
