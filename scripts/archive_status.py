"""Workspace view of the off-pod archive (plan B1, B2).

`overview` gathers what the Data view shows for each data/archive/<podId>: its index.json
(scripts/archive_index.py), the newest pull record, which core writes only for a pull that
completed, and whether core's pull lock is held. `AutoSync` pulls when the workspace opens
and every 6 h while it stays open, as a dashboard job. It never makes the first pull: that
waits for the other sleeper's consent (decision Q2), so it starts only once an archive exists.
"""
from pathlib import Path
from threading import Event, Thread
import time

from run_history import iso_seconds, read_json
from web_jobs import JobError, archive_lock

INDEX_VERSION = 'archive-index-v1'
STALE_HOURS = 72
SYNC_SECONDS = 6 * 3600
RETRY_SECONDS = 60
PULL_FIELDS = ('pulledAt', 'podHost', 'podId', 'cursor', 'raw', 'rows', 'warnings', 'schema')


def last_pull(pod_dir):
    """The newest readable pulls/*.json, without its manifest."""
    for path in sorted((Path(pod_dir) / 'pulls').glob('*.json'), reverse=True):
        record = read_json(path)
        if isinstance(record, dict) and iso_seconds(record.get('pulledAt')) is not None:
            return {'file': path.name, **{k: record.get(k) for k in PULL_FIELDS}}
    return None


def archived(dest):
    root = Path(dest)
    return sorted(p for p in root.iterdir() if p.is_dir() and (p / 'biometrics.db').is_file()) if root.is_dir() else []


def overview(data, sync=None, now=None):
    now = time.time() if now is None else now
    dest = Path(data) / 'archive'
    pods = []
    for pod in archived(dest):
        index = read_json(pod / 'index.json')
        pull = last_pull(pod)
        pulled = iso_seconds(pull['pulledAt']) if pull else None
        age = None if pulled is None else (now - pulled) / 3600
        generated = iso_seconds(index.get('generatedAt')) if isinstance(index, dict) else None
        problem = (None if isinstance(index, dict) and index.get('version') == INDEX_VERSION
                   else 'missing' if index is None else 'unsupported')
        pods.append({'podId': pod.name, 'path': f'data/archive/{pod.name}', 'index': None if problem else index,
                     'indexProblem': problem, 'lastPull': pull, 'pullAgeHours': age,
                     'pullOverdue': age is None or age > STALE_HOURS,
                     'indexBehind': bool(pulled and generated and generated < pulled)})
    return {'pods': pods, 'lock': archive_lock(dest), 'staleAfterHours': STALE_HOURS,
            'sync': sync.status() if sync else None}


class AutoSync:
    """Starts the archive-pull job on open and every `interval` seconds; retries while another job runs."""

    def __init__(self, runner, interval=SYNC_SECONDS, retry=RETRY_SECONDS, clock=time.time):
        self.runner, self.interval, self.retry, self.clock = runner, interval, retry, clock
        self.next_at = clock()
        self.last = None
        self.stopped = Event()

    def status(self):
        return {'intervalHours': self.interval / 3600, 'nextAt': self.next_at, 'last': self.last}

    def tick(self):
        now = self.clock()
        if now < self.next_at:
            return None
        dest = self.runner.data / 'archive'
        if not archived(dest):
            return self.done(now, 'skipped', 'No archive yet; run the first pull yourself once the other sleeper agrees')
        if archive_lock(dest) is not None:
            return self.done(now, 'skipped', 'Another pull holds the archive lock')
        try:
            job = self.runner.start({'action': 'archive-pull'})
        except JobError as error:
            if error.status == 409:
                self.next_at = now + self.retry
                self.last = {'at': now, 'outcome': 'waiting', 'detail': 'Waiting for the running job to finish'}
                return self.last
            return self.done(now, 'skipped', str(error))
        return self.done(now, 'started', job['id'])

    def done(self, now, outcome, detail):
        self.next_at = now + self.interval
        self.last = {'at': now, 'outcome': outcome, 'detail': detail}
        return self.last

    def start(self, every=30):
        def loop():
            while not self.stopped.is_set():
                try:
                    self.tick()
                except Exception as error:  # A failed check must not end the loop for the rest of the session.
                    self.done(self.clock(), 'skipped', f'Sync check failed: {error}')
                self.stopped.wait(every)
        Thread(target=loop, daemon=True).start()
        return self

    def stop(self):
        self.stopped.set()

