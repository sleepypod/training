"""Archive Data view and sync on open (plan B1, B2), on synthetic archive folders."""
import json
import os
import socket
import unittest
import urllib.request

from archive_status import AutoSync, overview
from dashboard import serve
from run_history import iso_seconds
from test_web_jobs import Fixture, quick
from web_jobs import archive_lock

HOUR = 3600
PULLED = '2026-11-15T20:30:00.000Z'
PULLED_S = iso_seconds(PULLED)


def index(generated='2026-11-15T21:00:00+00:00', version='archive-index-v1'):
    return {'version': version, 'generatedAt': generated, 'podId': 'pod-a', 'timezone': 'UTC', 'nights': [
        {'night': '2026-11-14', 'side': 'left', 'locked': True, 'atRisk': False},
        {'night': '2026-11-15', 'side': 'left', 'locked': False, 'atRisk': True}]}


class ArchiveFixture(Fixture):
    def __init__(self, actions=None):
        super().__init__(actions)
        self.dest = self.data / 'archive'
        self.pod = self.dest / 'pod-a'

    def archive(self, index_doc=None, pulls=(PULLED,)):
        (self.pod / 'pulls').mkdir(parents=True, exist_ok=True)
        (self.pod / 'biometrics.db').write_text('synthetic')
        for pulled in pulls:
            name = pulled.replace(':', '-') + '.json'
            (self.pod / 'pulls' / name).write_text(json.dumps({'pulledAt': pulled, 'raw': {'new': 3}, 'manifest': {'big': 1}}))
        if index_doc is not None:
            (self.pod / 'index.json').write_text(json.dumps(index_doc))


class ArchiveLockTests(unittest.TestCase):
    def test_lock_follows_cores_stale_rule(self):
        fixture = Fixture()
        self.addCleanup(fixture.close)
        dest, lock = fixture.data, fixture.data / '.biometrics-archive.lock'
        self.assertIsNone(archive_lock(dest))
        here = socket.gethostname()
        lock.write_text(json.dumps({'pid': os.getpid(), 'host': here, 'startedAt': PULLED}))
        self.assertEqual(archive_lock(dest)['pid'], os.getpid())
        lock.write_text(json.dumps({'pid': 2 ** 22 + 12345, 'host': here}))
        self.assertIsNone(archive_lock(dest), 'a dead process on this host leaves a stale lock')
        lock.write_text(json.dumps({'pid': 2 ** 22 + 12345, 'host': 'nas.local'}))
        self.assertEqual(archive_lock(dest)['host'], 'nas.local')
        lock.write_text('{not json')
        self.assertEqual(archive_lock(dest), {})


class OverviewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = ArchiveFixture()
        self.addCleanup(self.fixture.close)

    def test_newest_successful_pull_age_and_overdue_alert(self):
        self.fixture.archive(index(), pulls=('2026-11-10T12:30:00.000Z', PULLED))
        (self.fixture.pod / 'pulls' / 'zzz-unreadable.json').write_text('{')
        pod = overview(self.fixture.data, now=PULLED_S + 5 * HOUR)['pods'][0]
        self.assertEqual(pod['lastPull']['pulledAt'], PULLED)
        self.assertNotIn('manifest', pod['lastPull'])
        self.assertAlmostEqual(pod['pullAgeHours'], 5)
        self.assertFalse(pod['pullOverdue'])
        self.assertFalse(pod['indexBehind'])
        self.assertEqual(len(pod['index']['nights']), 2)
        late = overview(self.fixture.data, now=PULLED_S + 73 * HOUR)['pods'][0]
        self.assertTrue(late['pullOverdue'])

    def test_missing_pull_old_or_unknown_index_are_reported(self):
        self.fixture.archive(index('2026-11-15T19:00:00+00:00'))
        pod = overview(self.fixture.data, now=PULLED_S)['pods'][0]
        self.assertTrue(pod['indexBehind'])
        self.fixture.archive(index(version='archive-index-v0'), pulls=())
        for path in (self.fixture.pod / 'pulls').iterdir():
            path.unlink()
        pod = overview(self.fixture.data, now=PULLED_S)['pods'][0]
        self.assertEqual((pod['index'], pod['indexProblem'], pod['lastPull']), (None, 'unsupported', None))
        self.assertTrue(pod['pullOverdue'])
        (self.fixture.pod / 'index.json').unlink()
        self.assertEqual(overview(self.fixture.data)['pods'][0]['indexProblem'], 'missing')

    def test_served_without_token_on_loopback(self):
        self.fixture.archive(index())
        sync = AutoSync(self.fixture.runner)
        server = serve(catalog=self.fixture.catalog, jobs=self.fixture.runner, sync=sync)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}/api/archive') as response:
            doc = json.loads(response.read())
        self.assertEqual([p['podId'] for p in doc['pods']], ['pod-a'])
        self.assertEqual(doc['sync']['intervalHours'], 6)
        self.assertIsNone(doc['lock'])


class AutoSyncTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        self.fixture = ArchiveFixture({'archive-pull': quick(2), 'other': quick(30)})
        self.addCleanup(self.fixture.close)
        self.sync = AutoSync(self.fixture.runner, clock=lambda: self.now)

    def test_never_makes_the_first_pull(self):
        self.assertEqual(self.sync.tick()['outcome'], 'skipped')
        self.assertIn('first pull yourself', self.sync.last['detail'])
        self.assertEqual(self.sync.next_at, self.now + 6 * HOUR)
        self.assertFalse((self.fixture.data / 'web-jobs').exists())

    def test_skips_while_another_pull_holds_the_lock_then_pulls_on_schedule(self):
        self.fixture.archive()
        lock = self.fixture.dest / '.biometrics-archive.lock'
        lock.write_text(json.dumps({'pid': 1, 'host': 'launchd-host'}))
        self.assertIn('lock', self.sync.tick()['detail'])
        lock.unlink()
        self.assertIsNone(self.sync.tick(), 'not due until 6 h later')
        self.now += 6 * HOUR
        started = self.sync.tick()
        self.assertEqual(started['outcome'], 'started')
        job = json.loads((self.fixture.data / 'web-jobs' / started['detail'] / 'command.json').read_text())
        self.assertEqual(job['action'], 'archive-pull')

    def test_waits_for_a_running_job_and_retries_within_a_minute(self):
        self.fixture.archive()
        self.fixture.runner.start({'action': 'other'})
        self.assertEqual(self.sync.tick()['outcome'], 'waiting')
        self.assertEqual(self.sync.next_at, self.now + 60)


if __name__ == '__main__': unittest.main()
