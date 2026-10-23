"""Local run catalog. References existing artifacts; never moves or overwrites a run."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time
import uuid
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]


def primary_root():
    try:
        common = subprocess.check_output(['git', 'rev-parse', '--git-common-dir'], cwd=ROOT, text=True).strip()
        return (ROOT / common).resolve().parent
    except (OSError, subprocess.CalledProcessError):
        return ROOT


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


# Research runs that are not sleep-stage training write report.json with one of these kinds.
TASK_KINDS = {'movement': 'movement', 'snore-session': 'snore', 'snore-night': 'snore', 'snore-labels': 'snore'}


def run_kind(path):
    """'sleep' for training runs, a TASK_KINDS key for movement/snore runs, else None."""
    path = Path(path)
    if (path / 'progress.json').is_file() or (path / 'model/report.json').is_file():
        return 'sleep'
    kind = read_json(path / 'report.json', {}).get('kind') if (path / 'report.json').is_file() else None
    return kind if kind in TASK_KINDS else None


def iso_seconds(value):
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except (AttributeError, ValueError):
        return None


class Catalog:
    def __init__(self, path=None):
        self.path = Path(path or primary_root() / 'data/run-history.sqlite')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL, added REAL NOT NULL)')

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def register(self, path):
        path = Path(path).resolve(strict=True)
        if run_kind(path) is None:
            raise ValueError('Not a training run: progress.json, model/report.json or a movement/snore report.json is required')
        key = uuid.uuid5(uuid.NAMESPACE_URL, path.as_uri()).hex
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO runs VALUES (?, ?, ?)', (key, str(path), time.time()))
        return key

    def discover(self, roots):
        count = 0
        for root in roots:
            root = Path(root)
            if not root.is_dir():
                continue
            for directory, dirs, files in os.walk(root, followlinks=False):
                dirs[:] = [d for d in dirs if d not in ('responses', 'node_modules', '.venv') and not (Path(directory) / d).is_symlink()]
                path = Path(directory)
                if run_kind(path):
                    self.register(path)
                    count += 1
                    dirs[:] = []
        return count

    def resolve(self, key):
        with self.connect() as db:
            row = db.execute('SELECT path FROM runs WHERE id=?', (key,)).fetchone()
        return Path(row[0]) if row else None

    def list(self):
        with self.connect() as db:
            rows = db.execute('SELECT id, path, added FROM runs').fetchall()
        result = []
        for key, path, added in rows:
            root = Path(path)
            kind = run_kind(root) if root.is_dir() else None
            if kind and kind != 'sleep':
                result.append(task_entry(key, root, kind, added))
                continue
            state = read_json(root / 'progress.json', {})
            report = read_json(root / 'model/report.json', {})
            meta = read_json(root / 'run.json', {})
            status = state.get('status', 'archived' if report else 'unavailable')
            # A dead process or stale legacy progress is not an actively training run.
            if status == 'running':
                alive = False
                if meta.get('pid'):
                    try:
                        os.kill(meta['pid'], 0)
                        alive = True
                    except (ProcessLookupError, PermissionError):
                        pass
                if not alive and time.time() - (root / 'progress.json').stat().st_mtime > 120:
                    status = 'interrupted'
            result.append({'id': key, 'name': meta.get('name') or root.name, 'status': status,
                           'startedAt': state.get('startedAt', (root / 'model/report.json').stat().st_mtime if (root / 'model/report.json').is_file() else added), 'updatedAt': state.get('updatedAt'),
                           'phase': state.get('phase'), 'fits': state.get('completedFits'),
                           'groups': report.get('groups', state.get('totalFolds')),
                           'macroF1': report.get('scores', {}).get('nested', {}).get('macroF1'),
                           'candidate': report.get('finalSelected'), 'available': root.is_dir(),
                           'notes': meta.get('notes', ''), 'kind': 'sleep', 'task': 'sleep'})
        return sorted(result, key=lambda r: r['startedAt'], reverse=True)


def task_entry(key, root, kind, added):
    """Sidebar row for a movement or snore run: finished artifacts, so always complete."""
    report = read_json(root / 'report.json', {})
    meta = read_json(root / 'run.json', {})
    if kind == 'movement':
        accuracy = report.get('classifier', {}).get('balancedAccuracy')
        summary = f"{report.get('windows', '—')} windows · balanced accuracy " + (f'{accuracy:.2f}' if accuracy is not None else '—')
    elif kind == 'snore-labels':
        summary = 'Phone labels · exit test ' + report.get('exitTest', {}).get('result', 'inconclusive')
    elif kind == 'snore-session':
        summary = 'Bandwidth gate · ' + str(report.get('outcome', '—'))
    else:
        coverage = report.get('coverage')
        summary = 'Night · ' + (f'{coverage:.0%} coverage' if coverage is not None else 'coverage —')
    return {'id': key, 'name': meta.get('name') or root.name, 'status': 'complete',
            'startedAt': iso_seconds(report.get('generatedAt')) or (root / 'report.json').stat().st_mtime,
            'updatedAt': None, 'phase': None, 'fits': None, 'groups': None, 'macroF1': None,
            'candidate': None, 'available': True, 'notes': meta.get('notes', '') or summary,
            'kind': kind, 'task': TASK_KINDS[kind], 'summary': summary}


def register_output(path):
    """Best effort: add a research output under a local data/ folder to the catalog; never raises."""
    try:
        path = Path(path).resolve(strict=True)
        if not any(root.resolve() in path.parents for root in local_data_roots() if root.exists()):
            return None
        return Catalog().register(path)
    except Exception:
        return None


def local_data_roots():
    roots = [primary_root() / 'data']
    try:
        text = subprocess.check_output(['git', 'worktree', 'list', '--porcelain'], cwd=ROOT, text=True)
        roots.extend(Path(line[9:]) / 'data' for line in text.splitlines() if line.startswith('worktree '))
    except (OSError, subprocess.CalledProcessError):
        pass
    return list(dict.fromkeys(roots))
