"""Read-only LAN API acquisition; materialize inputs for the existing exporter."""
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
from pathlib import Path
import sqlite3
import time
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

from progress import update


def base_url(address):
    if '://' not in address:
        try:
            ip = ipaddress.ip_address(address)
            address = f'[{ip}]' if ip.version == 6 else str(ip)
        except ValueError:
            pass
        address = 'http://' + address
    u = urllib.parse.urlsplit(address)
    if u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password or u.path not in ('', '/') or u.query or u.fragment:
        raise ValueError('Expected pod IP/hostname, optionally with port or http(s) scheme')
    host = f'[{u.hostname}]' if ':' in u.hostname else u.hostname
    return f'{u.scheme}://{host}:{u.port or 3000}'


def millis(value):
    d = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if d.tzinfo is None:
        raise ValueError('Pod timestamps must have timezone offsets')
    return round(d.timestamp() * 1000)


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(timespec='milliseconds')


class Pod:
    def __init__(self, address, output):
        self.base = base_url(address)
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=False)
        self.requests = []
        self.started = time.perf_counter()
        self.bytes = 0
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def get(self, path, **params):
        url = self.base + '/api/' + path + ('?' + urllib.parse.urlencode(params) if params else '')
        with self.opener.open(url, timeout=30) as response:
            raw = response.read()
        self.bytes += len(raw)
        elapsed = time.perf_counter() - self.started
        data = json.loads(raw)
        count = len(data) if isinstance(data, list) else None
        update('Fetching', f'GET {path}: {len(raw):,} bytes' + (f' · {count:,} rows returned' if count is not None else ''),
               transfer={'requests': len(self.requests) + 1, 'bytes': self.bytes, 'bytesPerSecond': round(self.bytes / max(elapsed, 1e-9)), 'seconds': round(elapsed, 2)})
        name = f'{len(self.requests):05d}.json'
        (self.output / name).write_bytes(raw)
        self.requests.append({'path': path, 'params': params, 'file': name, 'sha256': hashlib.sha256(raw).hexdigest(), 'fetchedAt': iso(datetime.now(timezone.utc).timestamp() * 1000)})
        return data

    def range(self, path, side, start, end, limit):
        """Bisect inclusive time windows on saturation; never silently drop a full page."""
        rows = self.get(path, side=side, startDate=iso(start), endDate=iso(end), limit=limit)
        if not isinstance(rows, list) or any(not isinstance(r, dict) or r.get('side') != side or 'id' not in r for r in rows):
            raise ValueError(f'Unexpected {path} response or wrong side')
        if len(rows) >= limit:
            if end - start < 1:
                raise ValueError(f'{path}: API limit reached at one timestamp; cannot fetch complete data')
            mid = (start + end) // 2
            rows = self.range(path, side, start, mid, limit) + self.range(path, side, mid + 1, end, limit)
        unique = {}
        for row in rows:
            if row['id'] in unique and unique[row['id']] != row:
                raise ValueError(f'{path} changed during acquisition; retry with a new output directory')
            unique[row['id']] = row
        return list(unique.values())


def fetch(address, side, days, output, time_zone=None, fetch_watch=True, now=None):
    output = Path(output)
    pod = Pod(address, output / 'responses')
    end = round((now or datetime.now(timezone.utc)).timestamp() * 1000)
    start = end - days * 86400 * 1000
    update('Fetching', 'Reading pod timezone and stage configuration')
    settings = pod.get('settings')
    time_zone = time_zone or settings['device']['timezone']
    ZoneInfo(time_zone)
    overview = pod.get('biometric-tuning/profiles/overview', side=side)
    view = overview['stageView']
    rules = view['classifier']['ruleset']
    calibration = next((r for r in overview['sensorCalibration'] if r['sensorType'] == 'piezo'), None)
    quality = (calibration or {}).get('qualityScore') or 0
    records = pod.range('biometrics/sleep-records', side, start, end, 100)
    records = sorted([r for r in records if r['leftBedAt'] and millis(r['leftBedAt']) <= end
                      and 10800000 <= millis(r['leftBedAt']) - millis(r['enteredBedAt']) <= 50400000], key=lambda r: r['enteredBedAt'])
    if not records:
        raise ValueError('No completed 3–14 hour sessions in the requested range')
    vitals, movement, refs = {}, {}, {}
    pod_stages = {}
    for i, record in enumerate(records):
        a, b = millis(record['enteredBedAt']), millis(record['leftBedAt'])
        update('Fetching', f'Session {i + 1}/{len(records)}: vitals, movement' + (', Watch labels' if fetch_watch else ''), fetchedSessions=i, totalSessions=len(records))
        trace = pod.get('biometrics/sleep-stages', side=side, sleepRecordId=record['id'])
        if trace.get('sleepRecordId') != record['id'] or not isinstance(trace.get('epochs'), list):
            raise ValueError('Pod stage trace does not match the requested sleep record')
        if trace.get('stageView') is not None and trace['stageView'] != view:
            raise ValueError('Pod stage configuration changed while fetching trace')
        for epoch in trace['epochs']:
            if epoch.get('stage') not in ('wake', 'light', 'deep', 'rem') or not isinstance(epoch.get('start'), (int, float)) or not isinstance(epoch.get('duration'), (int, float)) or epoch['duration'] <= 0:
                raise ValueError('Invalid pod stage epoch')
        pod_stages[str(record['id'])] = trace['epochs']
        targets = [('biometrics/vitals', 100000, vitals), ('biometrics/movement', 1000, movement)]
        if fetch_watch:
            targets.append(('biometrics/reference-stages', 20000, refs))
        for path, limit, dest in targets:
            for row in pod.range(path, side, a, b, limit):
                if row['id'] in dest and dest[row['id']] != row:
                    raise ValueError(f'{path} changed during acquisition')
                dest[row['id']] = row
        update('Fetching', f'Session {i + 1}/{len(records)} received · {len(vitals):,} unique vitals · {len(movement):,} movement rows', fetchedSessions=i + 1, dataCounts={'sessions': i + 1, 'vitals': len(vitals), 'movement': len(movement), 'referenceStages': len(refs)})
    after = pod.get('biometric-tuning/profiles/overview', side=side)
    after_quality = next((r.get('qualityScore') or 0 for r in after['sensorCalibration'] if r['sensorType'] == 'piezo'), 0)
    if after['stageView'] != view or after_quality != quality:
        raise ValueError('Stage configuration/calibration changed during fetch; retry')
    if fetch_watch and not refs:
        raise ValueError('No imported Watch labels on this pod; pass --watch-export or --watch-json')
    (output / 'pod-stages.json').write_text(json.dumps({'source': 'pod-api', 'stageView': view, 'sessions': pod_stages}))
    snapshot = output / 'biometrics.db'
    with sqlite3.connect(snapshot) as db:
        db.executescript('''
        CREATE TABLE biometric_runtime_configs(side TEXT,generation INTEGER,payload TEXT);
        CREATE TABLE calibration_profiles(side TEXT,sensor_type TEXT,quality_score REAL);
        CREATE TABLE sleep_records(id INTEGER,side TEXT,entered_bed_at INTEGER,left_bed_at INTEGER);
        CREATE TABLE vitals(side TEXT,timestamp INTEGER,heart_rate REAL,hrv REAL,breathing_rate REAL);
        CREATE TABLE movement(side TEXT,timestamp INTEGER,total_movement REAL);
        ''')
        db.execute('INSERT INTO biometric_runtime_configs VALUES (?,?,?)', (side, 1, json.dumps({'resolved': {'sleep_stages': {'ruleset': rules}}})))
        db.execute('INSERT INTO calibration_profiles VALUES (?,?,?)', (side, 'piezo', quality))
        db.executemany('INSERT INTO sleep_records VALUES (?,?,?,?)', [(r['id'], side, millis(r['enteredBedAt']) / 1000, millis(r['leftBedAt']) / 1000) for r in records])
        db.executemany('INSERT INTO vitals VALUES (?,?,?,?,?)', [(side, millis(r['timestamp']) / 1000, r['heartRate'], r['hrv'], r['breathingRate']) for r in vitals.values()])
        db.executemany('INSERT INTO movement VALUES (?,?,?)', [(side, millis(r['timestamp']) / 1000, r['totalMovement']) for r in movement.values()])
    watch = output / 'watch.json'
    if fetch_watch:
        if any(r.get('source') != 'apple_watch' for r in refs.values()):
            raise ValueError('Unexpected reference source; expected apple_watch')
        watch.write_text(json.dumps({'stages': [[millis(r['start']), millis(r['end']), r['stage']] for r in refs.values()]}))
    manifest = {'baseUrl': pod.base, 'side': side, 'timezone': time_zone, 'range': [iso(start), iso(end)], 'stageView': view,
                'quality': quality, 'requests': pod.requests, 'counts': {'sessions': len(records), 'vitals': len(vitals), 'movement': len(movement), 'referenceStages': len(refs)},
                'consistency': 'Completed sessions fetched over multiple HTTP reads; not an atomic database snapshot.'}
    (output / 'acquisition.json').write_text(json.dumps(manifest, indent=2))
    update('Fetching', f'Downloaded {len(records)} completed sessions', fetchedSessions=len(records))
    return snapshot, watch if fetch_watch else None, time_zone
