"""Dashboard jobs: allow-listed training and evaluation scripts, run one at a time.

Each job writes data/web-jobs/<id>/command.json (action, validated parameters, the exact
argv and working directory, status) and log.txt, and is registered in the run catalog.
Parameters are checked against fixed schemas; paths must resolve inside data/, and argv
is never passed through a shell. No action uploads, selects or activates a model, or
writes to the pod (AGENTS.md): those steps stay manual.
"""
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import signal
import subprocess
from threading import Lock, Thread, Timer
import time
from zoneinfo import ZoneInfo

from run_history import ROOT, primary_root, run_kind

JOB_ID = re.compile(r'\d{8}T\d{6}Z-[0-9a-f]{6}')
SESSION = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}')
TIMEZONE = re.compile(r'[A-Za-z][A-Za-z0-9_+-]*(/[A-Za-z0-9_+-]+){0,2}')
AGENT = ROOT / 'scripts/install-archive-agent.sh'
LOG_TAIL = 64 * 1024
LOG_CHUNK = 256 * 1024


class JobError(Exception):
    def __init__(self, status, message, **extra):
        super().__init__(message)
        self.status, self.extra = status, extra


def archive_pull_command(data):
    """argv, cwd and PATH of the pull scripts/install-archive-agent.sh schedules (plan A1/B3), resolved by that script."""
    try:
        done = subprocess.run(['bash', str(AGENT), '--print-command', '--settings', str(data / 'workspace-settings.json'),
                               '--dest', str(data / 'archive')], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise JobError(400, f'Could not resolve the archive pull: {error}') from None
    if done.returncode:
        message = (done.stderr.strip().splitlines() or ['the archive pull is not configured'])[-1]
        raise JobError(400, message.removeprefix('install-archive-agent: '))
    cwd, path, *argv = done.stdout.split('\0')[:-1]
    return argv, Path(cwd), {'PATH': path}


def field(name, kind, label, flag=None, **spec):
    return {'name': name, 'type': kind, 'label': label, 'flag': flag, **spec}


FEATURES = field('features', 'file', 'Features', placeholder='data/runs/RUN/features.json', required=True)
SNAPSHOT = field('snapshot', 'file', 'Snapshot', '--snapshot', placeholder='data/biometrics.db', required=True)


def out_dir(prefix):
    return field('out', 'out-dir', 'Output folder (new)', '--out', prefix=prefix, placeholder=f'data/runs/{prefix}-<time>')


def script(path, *subcommand):
    return ['uv', 'run', '--locked', 'python', path, *subcommand]


ACTIONS = {
    'train': {
        'label': 'Train a sleep-stage model',
        'description': 'scripts/train.py on a local snapshot and Watch labels: features, nested evaluation, final candidate and TypeScript parity. Saves model/candidate.json in the run folder; never uploads or activates it.',
        'argv': script('scripts/train.py'), 'oneOf': [('watchExport', 'watchJson')],
        'fields': [SNAPSHOT,
                   field('watchExport', 'file', 'Apple Health export', '--watch-export', placeholder='data/export.zip'),
                   field('watchJson', 'file', 'Or extracted Watch stages', '--watch-json', placeholder='data/watch.json'),
                   field('side', 'choice', 'Side', '--side', options=['left', 'right'], required=True),
                   field('timezone', 'timezone', 'Timezone', '--timezone', placeholder='America/Los_Angeles', required=True),
                   field('contract', 'choice', 'Feature contract', '--contract', options=['podNative', 'podContext'], default='podNative'),
                   field('name', 'text', 'Name', '--name', maxLength=120),
                   field('notes', 'text', 'Notes', '--notes', maxLength=2000, multiline=True),
                   field('out', 'out-dir', 'Output folder (new)', '--out', prefix='train', placeholder='data/runs/train-<time>')],
    },
    'stage-backtest': {
        'label': 'Chronological stage backtest',
        'description': 'scripts/stage-backtest.py: walk-forward and frozen refits of the feature sets, scored against the stages the pod recorded live. Compares only; exports nothing.',
        'argv': script('scripts/stage-backtest.py'), 'fields': [FEATURES, SNAPSHOT, out_dir('backtest')],
    },
    'fusion': {
        'label': 'Fusion arms',
        'description': 'scripts/fusion.py: the frozen ablation forest with HR quality and movement-model features, leave-one-night-out and chronologically. Compares only; exports nothing.',
        'argv': script('scripts/fusion.py'),
        'fields': [FEATURES, SNAPSHOT,
                   field('movementEpochs', 'file', 'Movement epochs', '--movement-epochs', placeholder='data/runs/movement-epochs.json', required=True),
                   field('podModelTrainedThrough', 'date', 'Pod model trained through', '--pod-model-trained-through', placeholder='2026-11-03'),
                   out_dir('fusion')],
    },
    'vitals-reference': {
        'label': 'Pod vitals against Watch',
        'description': 'scripts/vitals_reference.py: pod-versus-Watch bias, error and correlation for heart rate, breathing rate and HRV, epoch by epoch.',
        'argv': script('scripts/vitals_reference.py'),
        'fields': [FEATURES, SNAPSHOT,
                   field('watchVitals', 'file', 'Watch vitals', '--watch-vitals', placeholder='data/watch-vitals.json', required=True),
                   out_dir('vitals')],
    },
    'movement-model-train': {
        'label': 'Train the movement model',
        'description': 'scripts/movement_model.py train: leave-one-night-out comparison of the pod movement score, the classifier and the model, labeled by Watch motion.',
        'argv': script('scripts/movement_model.py', 'train'),
        'fields': [field('windows', 'files', 'Windows (one per line)', '--windows', placeholder='data/runs/movement-001/windows.json', required=True, maxItems=20),
                   out_dir('movement-model')],
    },
    'movement-model-epochs': {
        'label': 'Movement-model epoch features',
        'description': 'scripts/movement_model.py epochs: cross-fitted per-epoch movement features from raw exports already on this computer.',
        'argv': script('scripts/movement_model.py', 'epochs'),
        'fields': [FEATURES, SNAPSHOT,
                   field('model', 'file', 'Movement model', '--model', placeholder='data/runs/movement-model/model.joblib', required=True),
                   field('exports', 'exports', 'Raw exports (SESSION_ID=path, one per line)', '--export', placeholder='12=data/raw/session.tar.gz', required=True, maxItems=60),
                   field('workers', 'int', 'Workers', '--workers', min=1, max=16, default=4),
                   field('out', 'out-file', 'Output file (new)', '--out', prefix='movement-epochs', placeholder='data/runs/movement-epochs-<time>.json')],
    },
    'archive-pull': {
        'label': 'Pull the pod archive now',
        'description': 'Core’s read-only biometrics archive pull into data/archive, the command the nightly agent from scripts/install-archive-agent.sh runs. Pod and core checkout come from data/workspace-settings.json.',
        'build': archive_pull_command, 'output': lambda data: data / 'archive', 'fields': [],
    },
}


def stamp():
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


class JobRunner:
    def __init__(self, catalog, data=None, root=ROOT, actions=None):
        self.catalog = catalog
        self.data = Path(data or primary_root() / 'data').resolve()
        self.jobs = self.data / 'web-jobs'
        self.root = Path(root)
        self.actions = actions or ACTIONS
        self.lock = Lock()
        self.active = None

    def schemas(self):
        return [{'id': key, 'label': a['label'], 'description': a['description'], 'oneOf': a.get('oneOf', []),
                 'fields': [{k: v for k, v in f.items() if k != 'flag'} for f in a['fields']]}
                for key, a in self.actions.items()]

    def path(self, value, label):
        if not isinstance(value, str) or not value.strip() or '\0' in value or len(value) > 1024:
            raise JobError(400, f'{label}: enter a path under data/')
        path = Path(value.strip()).expanduser()
        path = (path if path.is_absolute() else self.data.parent / path).resolve()
        if self.data not in path.parents:
            raise JobError(400, f'{label} must be inside {self.data}')
        return path

    def value(self, spec, raw):
        kind, label = spec['type'], spec['label']
        if kind == 'file':
            path = self.path(raw, label)
            if not path.is_file():
                raise JobError(400, f'{label}: {path} is not a file')
            return str(path)
        if kind == 'files':
            items = raw if isinstance(raw, list) else None
            if not items or len(items) > spec.get('maxItems', 20):
                raise JobError(400, f'{label}: between 1 and {spec.get("maxItems", 20)} files')
            return [self.value({**spec, 'type': 'file'}, item) for item in items]
        if kind == 'exports':
            items = raw if isinstance(raw, list) else None
            if not items or len(items) > spec.get('maxItems', 60):
                raise JobError(400, f'{label}: between 1 and {spec.get("maxItems", 60)} exports')
            pairs = []
            for item in items:
                session, sep, path = item.partition('=') if isinstance(item, str) else ('', '', '')
                if not sep or not SESSION.fullmatch(session):
                    raise JobError(400, f'{label}: write each export as SESSION_ID=path')
                pairs.append(session + '=' + self.value({**spec, 'type': 'file'}, path))
            return pairs
        if kind in ('out-dir', 'out-file'):
            path = self.path(raw, label)
            if path.exists() or path == self.jobs or self.jobs in path.parents:
                raise JobError(400, f'{label}: {path} already exists or is reserved; choose a new path')
            if kind == 'out-file' and path.suffix != '.json':
                raise JobError(400, f'{label}: must end in .json')
            return str(path)
        if kind == 'choice':
            if raw not in spec['options']:
                raise JobError(400, f'{label}: one of {", ".join(spec["options"])}')
            return raw
        if kind == 'int':
            if not isinstance(raw, int) or isinstance(raw, bool) or not spec['min'] <= raw <= spec['max']:
                raise JobError(400, f'{label}: a whole number from {spec["min"]} to {spec["max"]}')
            return raw
        if kind == 'date':
            try:
                if not isinstance(raw, str) or len(raw) != 10:
                    raise ValueError
                return date.fromisoformat(raw).isoformat()
            except ValueError:
                raise JobError(400, f'{label}: a date as YYYY-MM-DD') from None
        if kind == 'timezone':
            try:
                if not isinstance(raw, str) or not TIMEZONE.fullmatch(raw):
                    raise ValueError
                ZoneInfo(raw)
                return raw
            except (ValueError, KeyError, OSError):
                raise JobError(400, f'{label}: an IANA timezone such as America/Los_Angeles') from None
        if kind == 'text':
            allowed = '\n\t' if spec.get('multiline') else ''
            if not isinstance(raw, str) or len(raw) > spec['maxLength'] or any(ord(c) < 32 and c not in allowed for c in raw):
                raise JobError(400, f'{label}: at most {spec["maxLength"]} characters of plain text')
            return raw
        raise JobError(500, f'Unknown field type {kind}')

    def command(self, action, params):
        """Validated (argv, cwd, params, output, env) for an allow-listed action; raises JobError."""
        spec = self.actions.get(action) if isinstance(action, str) else None
        if spec is None:
            raise JobError(400, 'Unknown action')
        if not isinstance(params, dict):
            raise JobError(400, 'params must be an object')
        known = {f['name']: f for f in spec['fields']}
        extra = set(params) - set(known)
        if extra:
            raise JobError(400, 'Unknown parameters: ' + ', '.join(sorted(map(str, extra))))
        if 'build' in spec:
            argv, cwd, env = spec['build'](self.data)
            return argv, cwd, {}, str(spec['output'](self.data)), env
        for group in spec.get('oneOf', []):
            if sum(params.get(name) not in (None, '') for name in group) != 1:
                raise JobError(400, 'Give exactly one of: ' + ', '.join(known[name]['label'] for name in group))
        values = {}
        for name, f in known.items():
            raw = params.get(name)
            if raw in (None, '', []):
                if f['type'] in ('out-dir', 'out-file'):
                    raw = f'data/runs/{f["prefix"]}-{stamp()}' + ('.json' if f['type'] == 'out-file' else '')
                elif 'default' in f:
                    raw = f['default']
                elif f.get('required'):
                    raise JobError(400, f'{f["label"]} is required')
                else:
                    continue
            values[name] = self.value(f, raw)
        argv = list(spec['argv'])
        for f in spec['fields']:
            if f['name'] not in values:
                continue
            value = values[f['name']]
            for item in value if isinstance(value, list) else [value]:
                # --flag=value keeps a value that starts with '-' from being read as an option.
                argv.append(str(item) if f['flag'] is None else f'{f["flag"]}={item}')
        output = next((values[f['name']] for f in spec['fields'] if f['type'] in ('out-dir', 'out-file')), None)
        return argv, self.root.resolve(), values, output, {}

    def busy(self):
        if self.active and self.active['process'].poll() is None:
            return self.active['record']['id']
        return next((j['id'] for j in self.catalog.jobs() if j['status'] == 'running'), None)

    def start(self, body):
        if not isinstance(body, dict) or set(body) - {'action', 'params'}:
            raise JobError(400, 'Send {"action": ..., "params": {...}}')
        with self.lock:
            running = self.busy()
            if running:
                raise JobError(409, f'Job {running} is still running; one job runs at a time', active=running)
            action = body.get('action')
            argv, cwd, values, output, env = self.command(action, body.get('params') or {})
            key = f'{stamp()}-{secrets.token_hex(3)}'
            folder = self.jobs / key
            folder.mkdir(parents=True)
            record = {'kind': 'web-job', 'id': key, 'action': action, 'label': self.actions[action]['label'],
                      'params': values, 'argv': argv, 'cwd': str(cwd), 'env': env,
                      'command': shlex.join([f'{k}={v}' for k, v in env.items()] + argv), 'output': output,
                      'status': 'running', 'createdAt': time.time(), 'startedAt': None, 'finishedAt': None,
                      'exitCode': None, 'pid': None, 'run': None, 'error': None}
            log = (folder / 'log.txt').open('ab')
            try:
                process = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                           start_new_session=True, env={**os.environ, 'PYTHONUNBUFFERED': '1', **env})
            except OSError as error:
                log.write(f'Could not start {argv[0]}: {error}\n'.encode())
                record.update(status='failed', error=str(error), finishedAt=time.time())
                self.write(folder, record)
                self.catalog.register(folder)
                return record
            finally:
                log.close()
            record.update(pid=process.pid, startedAt=time.time())
            self.write(folder, record)
            self.catalog.register(folder)
            self.active = {'record': record, 'process': process, 'folder': folder}
            Thread(target=self.wait, args=(self.active,), daemon=True).start()
            return record

    def wait(self, job):
        code = job['process'].wait()
        with self.lock:
            record = job['record']
            status = 'complete' if code == 0 else 'cancelled' if record.get('cancelRequested') else 'failed'
            record.update(status=status, exitCode=code, finishedAt=time.time())
            self.link_output(record)
            self.write(job['folder'], record)
            if self.active is job:
                self.active = None

    def link_output(self, record):
        """Register the job's output in the catalog once it is a run the workspace can show."""
        output = Path(record['output']) if record.get('output') else None
        if record.get('run') or output is None or not output.is_dir() or not run_kind(output):
            return
        record['run'] = self.catalog.register(output)

    def cancel(self, key):
        with self.lock:
            job = self.active
            if not job or job['record']['id'] != key or job['process'].poll() is not None:
                raise JobError(409, 'That job is not running from this dashboard')
            job['record']['cancelRequested'] = True
            self.write(job['folder'], job['record'])
            self.signal(job['process'], signal.SIGINT)
            # Training records a cancelled run on Ctrl-C; force it if a step ignores the interrupt.
            timer = Timer(15, self.signal, (job['process'], signal.SIGTERM))
            timer.daemon = True
            timer.start()
            return job['record']

    @staticmethod
    def signal(process, number):
        if process.poll() is None:
            try:
                os.killpg(process.pid, number)
            except (ProcessLookupError, PermissionError):
                pass

    def stop(self):
        job = self.active
        if job and job['process'].poll() is None:
            try:
                self.cancel(job['record']['id'])
            except JobError:
                return
            try:
                job['process'].wait(20)
            except subprocess.TimeoutExpired:
                self.signal(job['process'], signal.SIGKILL)
            # Record the outcome before the server exits; the watcher thread is a daemon.
            self.wait(job)

    def list(self):
        with self.lock:
            if self.active:
                self.link_output(self.active['record'])
            jobs = self.catalog.jobs()
            if self.active:
                current = self.active['record']
                jobs = [{**j, **current} if j['id'] == current['id'] else j for j in jobs]
            return {'actions': self.schemas(), 'jobs': jobs, 'active': self.busy()}

    def log(self, key, offset=None):
        if not JOB_ID.fullmatch(key or '') or not (self.jobs / key / 'log.txt').is_file():
            raise JobError(404, 'No such job')
        path = self.jobs / key / 'log.txt'
        size = path.stat().st_size
        start = max(0, size - LOG_TAIL) if offset is None or not 0 <= offset <= size else offset
        with path.open('rb') as f:
            f.seek(start)
            chunk = f.read(LOG_CHUNK)
        return {'id': key, 'start': start, 'offset': start + len(chunk), 'size': size,
                'text': chunk.decode('utf-8', 'replace')}

    @staticmethod
    def write(folder, record):
        temp = folder / 'command.json.tmp'
        temp.write_text(json.dumps(record, indent=2))
        temp.replace(folder / 'command.json')
