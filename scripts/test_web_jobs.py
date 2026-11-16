"""Dashboard job runner: token and origin guard, schema validation, one job at a time, no pod writes."""
import http.client
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from dashboard import html, serve
from run_history import ROOT, Catalog
from web_jobs import ACTIONS, JobError, JobRunner

TOKEN = re.compile(r'"jobToken": "([A-Za-z0-9_-]+)"')


def quick(seconds=0.0, code=0):
    """A test-only action: a Python one-liner standing in for a script."""
    return {'label': 'Quick', 'description': 'test', 'argv': [sys.executable, '-c',
            f'import sys, time; print("hello", flush=True); time.sleep({seconds}); sys.exit({code})'],
            'fields': [field for field in ACTIONS['stage-backtest']['fields'] if field['name'] == 'out']}


class Fixture:
    def __init__(self, actions=None):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.data = self.root / 'data'
        self.data.mkdir()
        for name in ('features.json', 'biometrics.db', 'watch.json', 'export.zip', 'epochs.json', 'vitals.json',
                     'windows.json', 'model.joblib', 'session.tar.gz'):
            (self.data / name).write_text('synthetic')
        (self.root / 'secret.db').write_text('outside data/')
        self.catalog = Catalog(self.data / 'catalog.sqlite')
        self.runner = JobRunner(self.catalog, self.data, ROOT, actions)

    def close(self):
        self.runner.stop()
        self.temp.cleanup()


class Client:
    def __init__(self, server):
        self.port = server.server_port
        self.token = TOKEN.search(self.get('/', token=None)[1]).group(1)

    def request(self, method, path, body=None, token='', **headers):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        headers = {'Host': f'127.0.0.1:{self.port}', **headers}
        if token is not None:
            headers['X-Training-Token'] = self.token if token == '' else token
        if body is not None:
            headers.setdefault('Content-Type', 'application/json')
            body = json.dumps(body).encode()
        connection.request(method, path, body, headers)
        response = connection.getresponse()
        text = response.read().decode()
        connection.close()
        return response.status, text

    def get(self, path, **kw):
        return self.request('GET', path, **kw)

    def post(self, path, body, **kw):
        return self.request('POST', path, body, **kw)


def wait_for(client, key, status='complete'):
    for _ in range(200):
        jobs = {j['id']: j for j in json.loads(client.get('/api/jobs')[1])['jobs']}
        if jobs[key]['status'] == status:
            return jobs[key]
        time.sleep(0.05)
    raise AssertionError(f'{key} never reached {status}: {jobs[key]}')


class JobServerTests(unittest.TestCase):
    def serve(self, actions=None):
        self.fixture = Fixture(actions)
        self.server = serve(catalog=self.fixture.catalog, jobs=self.fixture.runner)
        self.client = Client(self.server)
        self.addCleanup(self.fixture.close)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        return self.client

    def test_requests_without_this_launchs_token_origin_or_host_are_refused(self):
        client = self.serve({'quick': quick()})
        port = client.port
        body = {'action': 'quick', 'params': {}}
        refused = [
            client.post('/api/jobs', body, token=None),
            client.post('/api/jobs', body, token='wrong'),
            client.post('/api/jobs', body, Host=f'attacker.example:{port}'),
            client.post('/api/jobs', body, Origin='http://attacker.example'),
            client.post('/api/jobs', body, Origin=f'http://127.0.0.1:{port + 1}'),
            client.post('/api/jobs', body, **{'Sec-Fetch-Site': 'cross-site'}),
            client.get('/api/jobs', token=None),
            client.get('/api/jobs', Host='localhost.attacker.example'),
        ]
        self.assertEqual([status for status, _ in refused], [403] * len(refused))
        self.assertEqual(client.post('/api/jobs', body, **{'Content-Type': 'text/plain'})[0], 415)
        self.assertEqual(client.request('OPTIONS', '/api/jobs')[0], 501)
        # DNS rebinding: the page carrying the token is served only to loopback host names.
        self.assertEqual(client.get('/', token=None, Host=f'attacker.example:{port}')[0], 403)
        self.assertEqual(client.get('/api/runs', token=None, Host=f'attacker.example:{port}')[0], 403)
        self.assertEqual(client.get('/', token=None, Host=f'localhost:{port}')[0], 200)
        self.assertEqual(json.loads(client.get('/api/jobs')[1])['jobs'], [])
        self.assertFalse((self.fixture.data / 'web-jobs').exists())

    def test_token_is_per_launch_and_absent_from_reports_and_read_only_viewers(self):
        first = self.serve({'quick': quick()})
        other = serve(catalog=self.fixture.catalog, jobs=JobRunner(self.fixture.catalog, self.fixture.data))
        viewer = serve(catalog=self.fixture.catalog)
        for server in (other, viewer):
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
        second = Client(other)
        self.assertNotEqual(first.token, second.token)
        self.assertEqual(second.post('/api/jobs', {'action': 'quick'}, token=first.token)[0], 403)
        connection = http.client.HTTPConnection('127.0.0.1', viewer.server_port, timeout=10)
        connection.request('GET', '/')
        self.assertIn('"jobToken": null', connection.getresponse().read().decode())
        connection.request('POST', '/api/jobs', b'{}', {'Content-Type': 'application/json'})
        self.assertEqual(connection.getresponse().status, 404)
        self.assertNotIn('jobToken": "', html({'state': {}}))

    def test_job_records_command_log_and_catalog_entry(self):
        client = self.serve({'quick': quick()})
        status, text = client.post('/api/jobs', {'action': 'quick', 'params': {}})
        self.assertEqual(status, 201, text)
        job = json.loads(text)
        done = wait_for(client, job['id'])
        folder = self.fixture.data / 'web-jobs' / job['id']
        record = json.loads((folder / 'command.json').read_text())
        self.assertEqual(record['argv'], quick()['argv'] + [f'--out={record["output"]}'])
        self.assertEqual(record['command'], shlex.join(record['argv']))
        self.assertEqual(record['cwd'], str(ROOT.resolve()))
        self.assertTrue(record['output'].startswith(str(self.fixture.data / 'runs' / 'backtest-')))
        self.assertEqual((record['status'], record['exitCode']), ('complete', 0))
        self.assertEqual((folder / 'log.txt').read_text(), 'hello\n')
        self.assertEqual(done['catalogId'], self.fixture.catalog.register(folder))
        self.assertEqual(self.fixture.catalog.list(), [], 'jobs stay out of the run list')
        log = json.loads(client.get(f'/api/jobs/{job["id"]}/log')[1])
        self.assertEqual((log['text'], log['offset']), ('hello\n', 6))
        self.assertEqual(json.loads(client.get(f'/api/jobs/{job["id"]}/log?offset=6')[1])['text'], '')
        self.assertEqual(client.get('/api/jobs/..%2F..%2Fsecret/log')[0], 404)

    def test_one_job_at_a_time_and_cancel(self):
        client = self.serve({'slow': quick(30), 'quick': quick()})
        job = json.loads(client.post('/api/jobs', {'action': 'slow'})[1])
        status, text = client.post('/api/jobs', {'action': 'quick'})
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(text)['active'], job['id'])
        self.assertEqual(json.loads(client.get('/api/jobs')[1])['active'], job['id'])
        self.assertEqual(client.post('/api/jobs/nope/cancel', {})[0], 409)
        self.assertEqual(client.post(f'/api/jobs/{job["id"]}/cancel', {})[0], 200)
        self.assertNotEqual(wait_for(client, job['id'], 'cancelled')['exitCode'], 0)
        self.assertEqual(client.post('/api/jobs', {'action': 'quick'})[0], 201)

    def test_stopping_the_workspace_records_the_running_job_as_cancelled(self):
        client = self.serve({'slow': quick(30)})
        job = json.loads(client.post('/api/jobs', {'action': 'slow'})[1])
        self.fixture.runner.stop()
        record = json.loads((self.fixture.data / 'web-jobs' / job['id'] / 'command.json').read_text())
        self.assertEqual(record['status'], 'cancelled')
        self.assertIsNone(self.fixture.runner.busy())

    def test_failed_job_is_recorded(self):
        client = self.serve({'quick': quick(code=3)})
        job = json.loads(client.post('/api/jobs', {'action': 'quick'})[1])
        self.assertEqual(wait_for(client, job['id'], 'failed')['exitCode'], 3)

    def test_finished_output_that_is_a_run_is_registered(self):
        action = quick()
        action['argv'] = [sys.executable, '-c', 'import pathlib, sys; out = pathlib.Path(sys.argv[1].split("=", 1)[1]); '
                          'out.mkdir(parents=True); (out / "report.json").write_text(\'{"kind": "movement"}\')']
        client = self.serve({'quick': action})
        job = json.loads(client.post('/api/jobs', {'action': 'quick'})[1])
        done = wait_for(client, job['id'])
        self.assertEqual([r['id'] for r in self.fixture.catalog.list()], [done['run']])


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        self.addCleanup(self.fixture.close)
        self.runner = self.fixture.runner

    def refuse(self, action, params, pattern):
        with self.assertRaisesRegex(JobError, pattern) as caught:
            self.runner.command(action, params)
        self.assertEqual(caught.exception.status, 400)

    def test_train_builds_flag_value_argv_with_absolute_paths(self):
        argv, cwd, values, output, env = self.runner.command('train', {
            'snapshot': 'data/biometrics.db', 'watchJson': str(self.fixture.data / 'watch.json'), 'side': 'left',
            'timezone': 'America/Los_Angeles', 'name': '--pod 192.168.1.88', 'notes': 'line one\nline two',
            'out': 'data/runs/first'})
        data = self.fixture.data
        self.assertEqual(argv, ['uv', 'run', '--locked', 'python', 'scripts/train.py', f'--snapshot={data}/biometrics.db',
                                f'--watch-json={data}/watch.json', '--side=left', '--timezone=America/Los_Angeles',
                                '--contract=podNative', '--name=--pod 192.168.1.88', '--notes=line one\nline two',
                                f'--out={data}/runs/first'])
        self.assertEqual(output, f'{data}/runs/first')
        self.assertEqual(cwd, ROOT.resolve())

    def test_paths_must_stay_under_data(self):
        base = {'features': 'data/features.json', 'snapshot': 'data/biometrics.db'}
        self.refuse('stage-backtest', {**base, 'snapshot': '/etc/passwd'}, 'inside')
        self.refuse('stage-backtest', {**base, 'snapshot': 'data/../secret.db'}, 'inside')
        self.refuse('stage-backtest', {**base, 'snapshot': 'secret.db'}, 'inside')
        self.refuse('stage-backtest', {**base, 'out': 'data'}, 'inside')
        self.refuse('stage-backtest', {**base, 'out': '../elsewhere'}, 'inside')
        (self.fixture.data / 'escape.db').symlink_to(self.fixture.root / 'secret.db')
        self.refuse('stage-backtest', {**base, 'snapshot': 'data/escape.db'}, 'inside')
        self.refuse('stage-backtest', {**base, 'snapshot': 'data/missing.db'}, 'not a file')
        self.refuse('stage-backtest', {**base, 'out': 'data/features.json'}, 'already exists')
        self.refuse('stage-backtest', {**base, 'out': 'data/web-jobs/x'}, 'reserved')
        self.refuse('stage-backtest', {**base, 'snapshot': ['data/biometrics.db']}, 'path')

    def test_schemas_reject_unknown_actions_fields_and_values(self):
        train = {'snapshot': 'data/biometrics.db', 'watchJson': 'data/watch.json', 'side': 'left', 'timezone': 'UTC'}
        self.refuse('shell', {}, 'Unknown action')
        self.refuse('train', {**train, 'pod': '192.168.1.88'}, 'Unknown parameters: pod')
        self.refuse('train', {**train, 'holdoutFinal': True}, 'Unknown parameters')
        self.refuse('train', {**train, 'watchExport': 'data/export.zip'}, 'exactly one')
        self.refuse('train', {**train, 'side': 'both'}, 'one of')
        self.refuse('train', {**train, 'timezone': '../../etc/passwd'}, 'IANA')
        self.refuse('train', {**train, 'timezone': 'Not/AZone'}, 'IANA')
        self.refuse('train', {**train, 'name': 'a\x1b[2Jb'}, 'plain text')
        self.refuse('train', {**train, 'name': 'x' * 121}, 'plain text')
        self.refuse('train', {k: v for k, v in train.items() if k != 'side'}, 'Side is required')
        self.refuse('fusion', {'features': 'data/features.json', 'snapshot': 'data/biometrics.db',
                               'movementEpochs': 'data/epochs.json', 'podModelTrainedThrough': '2026-13-01'}, 'YYYY-MM-DD')
        epochs = {'features': 'data/features.json', 'snapshot': 'data/biometrics.db', 'model': 'data/model.joblib',
                  'exports': ['12=data/session.tar.gz']}
        self.refuse('movement-model-epochs', {**epochs, 'workers': 99}, 'whole number')
        self.refuse('movement-model-epochs', {**epochs, 'workers': '4'}, 'whole number')
        self.refuse('movement-model-epochs', {**epochs, 'exports': ['-x=data/session.tar.gz']}, 'SESSION_ID')
        self.refuse('movement-model-epochs', {**epochs, 'exports': ['12=/etc/passwd']}, 'inside')
        self.refuse('movement-model-epochs', {**epochs, 'out': 'data/new.csv'}, '.json')
        self.refuse('movement-model-train', {'windows': []}, 'is required')
        self.refuse('movement-model-train', {'windows': ['data/windows.json'] * 21}, 'between 1 and 20')
        self.refuse('archive-index', {'archive': 'data/features.json', 'timezone': 'UTC'}, 'not a folder')
        (self.fixture.data / 'archive/pod-a').mkdir(parents=True)
        self.assertEqual(self.runner.command('archive-index', {'archive': 'data/archive/pod-a', 'timezone': 'UTC'})[0][4:],
                         ['scripts/archive_index.py', f'--archive={self.fixture.data}/archive/pod-a', '--timezone=UTC'])
        (self.fixture.data / 'runs/search-a').mkdir(parents=True)
        self.refuse('search-run', {'run': 'data/runs/search-a'}, 'no plan.json')
        (self.fixture.data / 'runs/search-a/plan.json').write_text('{}')
        argv, cwd, values, output, env = self.runner.command('search-run', {'run': 'data/runs/search-a'})
        self.assertEqual(argv[4:], ['scripts/search.py', 'run', f'{self.fixture.data}/runs/search-a', '--jobs=2'])
        self.assertEqual(output, f'{self.fixture.data}/runs/search-a', 'a finished run is linked into the run list')
        self.assertEqual(self.runner.command('search-plan', {'spec': 'data/watch.json', 'out': 'data/runs/search-b'})[0][4:],
                         ['scripts/search.py', 'plan', f'{self.fixture.data}/watch.json', f'--out={self.fixture.data}/runs/search-b'])
        argv = self.runner.command('movement-model-epochs', epochs)[0]
        self.assertEqual(argv[4:7], ['scripts/movement_model.py', 'epochs', str(self.fixture.data / 'features.json')])
        self.assertIn(f'--export=12={self.fixture.data}/session.tar.gz', argv)
        self.assertIn('--workers=4', argv)

    def test_archive_pull_is_resolved_by_the_agent_script(self):
        data, root = self.fixture.data, self.fixture.root
        bin_dir = root / 'bin'
        bin_dir.mkdir()
        (bin_dir / 'pnpm').write_text('#!/bin/sh\n')
        (bin_dir / 'pnpm').chmod(0o755)
        path = f'{os.environ["PATH"]}:{bin_dir}'
        with patch.dict(os.environ, {'PATH': path}):
            self.refuse('archive-pull', {}, 'no pod')
            core = root / 'core'
            settings = {'pod': '-rf', 'coreCheckout': str(core)}
            (data / 'workspace-settings.json').write_text(json.dumps(settings))
            self.refuse('archive-pull', {}, 'pod must be HOST')
            (data / 'workspace-settings.json').write_text(json.dumps({**settings, 'pod': '192.168.1.88'}))
            self.refuse('archive-pull', {}, 'not found')
            (core / 'scripts/biometrics-archive').mkdir(parents=True)
            (core / 'node_modules').mkdir()
            (core / 'package.json').write_text('{"scripts": {"biometrics:archive": "tsx scripts/biometrics-archive/cli.ts"}}')
            (core / 'scripts/biometrics-archive/cli.ts').write_text('old')
            self.refuse('archive-pull', {}, '--curl')
            (core / 'scripts/biometrics-archive/cli.ts').write_text("option('--curl')")
            self.refuse('archive-pull', {'pod': '10.0.0.1'}, 'Unknown parameters')
            argv, cwd, values, output, env = self.runner.command('archive-pull', {})
            plist = subprocess.run(['bash', str(ROOT / 'scripts/install-archive-agent.sh'), '--dry-run', '--settings',
                                    str(data / 'workspace-settings.json'), '--dest', str(data / 'archive')],
                                   capture_output=True, text=True, check=True).stdout
        self.assertEqual(argv, ['pnpm', '-s', 'biometrics:archive', 'pull', '--pod', '192.168.1.88',
                                '--dest', str(data / 'archive'), '--curl'])
        self.assertEqual((cwd, output), (core.resolve(), str(data / 'archive')))
        self.assertIn(env['PATH'].split(':')[0], plist)
        # The nightly agent and the dashboard run the same pull.
        self.assertIn(f"cd '{core.resolve()}' &amp;&amp; pnpm -s biometrics:archive pull --pod '192.168.1.88' "
                      f"--dest '{data / 'archive'}' --curl", plist)


class SearchViewTests(unittest.TestCase):
    def test_search_run_payload_never_carries_holdout_scores(self):
        fixture = Fixture()
        self.addCleanup(fixture.close)
        run = fixture.data / 'runs' / 'search-a'
        run.mkdir(parents=True)
        (run / 'report.json').write_text(json.dumps({'kind': 'search', 'status': 'final', 'name': 'a', 'candidates': 2,
                                                     'holdout': {'readsSoFar': 1, 'finalRead': True}}))
        (run / 'final.json').write_text(json.dumps({'pooled': {'macroF1': 0.123456789}}))
        key = fixture.catalog.register(run)
        server = serve(catalog=fixture.catalog)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
        for path in (f'/api/runs/{key}', f'/api/runs/{key}/report', '/api/runs'):
            connection.request('GET', path)
            text = connection.getresponse().read().decode()
            self.assertNotIn('0.123456789', text, path)
        connection.request('GET', f'/api/runs/{key}')
        doc = json.loads(connection.getresponse().read())
        self.assertEqual((doc['kind'], doc['runPath']), ('search', str(run)))


class NoPodWritesTests(unittest.TestCase):
    """AGENTS.md: uploads, model selection or activation, and pod writes stay manual."""
    SCRIPTS = {'scripts/train.py', 'scripts/stage-backtest.py', 'scripts/fusion.py', 'scripts/vitals_reference.py',
               'scripts/movement_model.py', 'scripts/archive_index.py', 'scripts/search.py'}
    WRITES = re.compile(r'sleep-stages/model|watch-upload|/api/device|/api/settings|/api/schedules|'
                        r"method\s*=\s*['\"](PUT|POST|PATCH|DELETE)|['\"]-X['\"]|--request", re.I)
    # Reads: GETs of the model library and selection (archive_index.py --stage-models help), and the preview route Pod.post is limited to.
    READS = re.compile(r"pod\.get\('sleep-stages/models?'|sleep-stages/model/preview|GET /api/sleep-stages/models\?side=")
    # fetch_pod.Pod.post sends POST only to fetch_pod.READ_ONLY_POSTS (test_pod_post_refuses_writes).
    GUARDED_POST = "method='POST')"

    def test_action_allow_list_is_fixed(self):
        self.assertEqual(set(ACTIONS), {'train', 'stage-backtest', 'fusion', 'vitals-reference', 'movement-model-train',
                                        'movement-model-epochs', 'archive-pull', 'archive-index',
                                        'search-plan', 'search-run', 'search-report'})
        for key, action in ACTIONS.items():
            self.assertNotRegex(key + action['label'], re.compile('upload|activat|select|promot|deploy', re.I))
            flags = {f['flag'] for f in action['fields']}
            self.assertFalse(flags & {'--pod', '--holdout-final', '--dashboard', '--candidate', '--access-log'}, key)
            # search.py final reads the locked holdout and exports model/set.json: never a workspace action.
            self.assertNotIn('final', action.get('argv', []), key)
            if 'build' not in action:
                self.assertEqual(action['argv'][:4], ['uv', 'run', '--locked', 'python'])
                self.assertIn(action['argv'][4], self.SCRIPTS)

    def test_action_scripts_never_write_to_the_pod(self):
        for path in self.SCRIPTS | {'scripts/fetch_pod.py', 'scripts/retrain-models.py', 'scripts/web_jobs.py',
                                     'scripts/install-archive-agent.sh'}:
            text = self.READS.sub('', (ROOT / path).read_text())
            if path == 'scripts/fetch_pod.py':
                text = text.replace(self.GUARDED_POST, ')', 1)
            matches = [m.group(0) for m in self.WRITES.finditer(text)]
            self.assertEqual(matches, [], path)

    def test_pod_post_refuses_writes(self):
        import fetch_pod
        self.assertEqual(fetch_pod.READ_ONLY_POSTS, frozenset({'sleep-stages/model/preview'}))
        pod = fetch_pod.Pod.__new__(fetch_pod.Pod)
        for route in ('sleep-stages/models', 'sleep-stages/model', 'biometrics/watch-upload', 'device/temperature'):
            with self.assertRaises(ValueError):
                pod.post(route, {})


if __name__ == '__main__': unittest.main()
