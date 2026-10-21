"""Local run catalog. References existing artifacts; never moves or overwrites a run."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time
import uuid

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
        if not ((path / 'progress.json').is_file() or (path / 'model/report.json').is_file()):
            raise ValueError('Not a training run: progress.json or model/report.json is required')
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
                if 'progress.json' in files or (path / 'model/report.json').is_file():
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
                           'notes': meta.get('notes', '')})
        return sorted(result, key=lambda r: r['startedAt'], reverse=True)


def local_data_roots():
    roots = [primary_root() / 'data']
    try:
        text = subprocess.check_output(['git', 'worktree', 'list', '--porcelain'], cwd=ROOT, text=True)
        roots.extend(Path(line[9:]) / 'data' for line in text.splitlines() if line.startswith('worktree '))
    except (OSError, subprocess.CalledProcessError):
        pass
    return list(dict.fromkeys(roots))
