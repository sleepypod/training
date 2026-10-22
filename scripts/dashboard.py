"""Local training workspace, persistent run history, and self-contained offline reports."""
import argparse
import base64
from functools import lru_cache
import json
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import time
from urllib.parse import urlsplit

from run_history import Catalog, local_data_roots, read_json, run_kind

UI = Path(__file__).with_name('ui')


@lru_cache(maxsize=12)
def _diagnostics(path, modified, size):
    from diagnostics import describe
    data = read_json(path)
    return describe(data) if data else None


MOVEMENT_WINDOW_FIELDS = ('session', 't', 'label', 'predicted', 'baselineMoving', 'baseline')


def pod_host(report):
    """host:port of the pod a snore report came from, if recorded."""
    for value in (report.get('pod'), (report.get('source') or {}).get('url')):
        if value:
            parts = urlsplit(value if '://' in value else 'http://' + value)
            if parts.hostname:
                return f'{parts.hostname}:{parts.port or 3000}'
    return None


def snore_reference(report, catalog):
    """Lying-still and imitated-snore levels from the latest snore session on the same pod, if any."""
    host = pod_host(report)
    if not host or catalog is None:
        return None
    best = None
    for entry in catalog.list():
        if entry.get('kind') != 'snore-session':
            continue
        root = catalog.resolve(entry['id'])
        session = read_json(root / 'report.json', {}) if root else {}
        if pod_host(session) != host or not session.get('occupantChannels'):
            continue
        channel = session['occupantChannels'][0]
        steps = session.get('steps', {})
        still = steps.get('occupied_still', {}).get('channels', {}).get(channel, {}).get('bandDb')
        snore = steps.get('occupied_snore', {}).get('channels', {}).get(channel, {}).get('bandDb')
        if still and snore and (best is None or entry['startedAt'] > best['startedAt']):
            best = {'run': entry['name'], 'id': entry['id'], 'channel': channel, 'still': still, 'snore': snore,
                    'outcome': session.get('outcome'), 'startedAt': entry['startedAt']}
    return best


def task_payload(root, key, kind, catalog=None):
    report = read_json(root / 'report.json', {})
    windows = read_json(root / 'windows.json')
    if kind == 'movement' and windows:
        windows = [{k: w.get(k) for k in MOVEMENT_WINDOW_FIELDS} for w in windows]
    return {'id': key, 'name': root.name, 'kind': kind, 'state': {'status': 'complete'},
            'config': read_json(root / 'run.json', {}), 'taskReport': report, 'windows': windows,
            'reference': snore_reference(report, catalog) if kind == 'snore-night' else None}


def payload(output, key=None, catalog=None):
    root = Path(output)
    kind = run_kind(root)
    if kind and kind != 'sleep':
        return task_payload(root, key, kind, catalog)
    features = root / 'features.json'
    diagnostics = None
    if features.is_file():
        stat = features.stat()
        try:
            diagnostics = _diagnostics(str(features), stat.st_mtime_ns, stat.st_size)
        except (ValueError, KeyError, TypeError):
            diagnostics = {'error': 'Historical feature data cannot be summarized'}
    traces = read_json(root / 'model/timelines.json')
    # Older traces did not embed inputs. Join by timestamp without synthesizing predictions.
    if traces and 'features' not in traces and features.is_file():
        data = read_json(features, {})
        traces['features'] = data.get('features', [])
        by_id = {str(n['id']): {r['start']: r['x'] for r in n['rows']} for n in data.get('nights', [])}
        for night in traces['sessions']:
            for row in night['rows']:
                row['x'] = by_id.get(night['id'], {}).get(row['start'])
    return {'id': key, 'name': root.name, 'state': read_json(root / 'progress.json', {}),
            'report': read_json(root / 'model/report.json'), 'timelines': traces,
            'diagnostics': diagnostics, 'config': read_json(root / 'run.json', {}),
            'protocol': read_json(root / 'model/protocol.json', {}), 'kind': 'sleep',
            'error': (root / 'error.txt').read_text() if (root / 'error.txt').is_file() else None}


def html(data):
    embedded = json.dumps(data, allow_nan=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    return (UI / 'dashboard.html').read_text().replace('__CSS__', (UI / 'dashboard.css').read_text()).replace('__JS__', (UI / 'dashboard.js').read_text()).replace('__LOGO__', 'data:image/png;base64,' + base64.b64encode((UI / 'sleepypod-logo.png').read_bytes()).decode('ascii')).replace('__EMBEDDED__', embedded)


def write_report(output):
    root = Path(output)
    temp = root / 'index.html.tmp'
    temp.write_text(html(payload(root)))
    temp.replace(root / 'index.html')


def serve(output=None, catalog=None, port=0):
    catalog = catalog or Catalog()
    key = catalog.register(output) if output else None
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlsplit(self.path).path
            kind = 'application/json'
            data = None
            if path in ('/', '/index.html'):
                kind = 'text/html'
                data = html({'initialRun': key}).encode()
            elif path == '/api/runs':
                data = json.dumps(catalog.list(), allow_nan=False).encode()
            elif path.startswith('/api/runs/'):
                parts = path.split('/')
                root = catalog.resolve(parts[3])
                if root and root.is_dir() and len(parts) in (4, 5):
                    if len(parts) == 4:
                        doc = payload(root, parts[3], catalog)
                        entry = next((r for r in catalog.list() if r['id'] == parts[3]), None)
                        if entry: doc['state']['status'] = entry['status']
                        data = json.dumps(doc, allow_nan=False).encode()
                    elif parts[4] == 'report':
                        kind = 'text/html'; data = html(payload(root, None, catalog)).encode()
                    elif parts[4] in ('events', 'candidate'):
                        artifact = root / ('events.jsonl' if parts[4] == 'events' else 'model/candidate.json')
                        if artifact.is_file():
                            data = artifact.read_bytes()
                            kind = 'application/x-ndjson' if parts[4] == 'events' else 'application/json'
            elif output:
                legacy = {'/progress.json': 'progress.json', '/model/report.json': 'model/report.json', '/model/timelines.json': 'model/timelines.json'}
                if path in legacy and (Path(output) / legacy[path]).is_file(): data = (Path(output) / legacy[path]).read_bytes()
            if data is None:
                self.send_error(404); return
            self.send_response(200)
            self.send_header('Content-Type', kind + '; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            try: self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError): pass
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    return server


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--run', type=Path, help='Open an existing run without retraining')
    ap.add_argument('--import-root', type=Path, action='append', default=[], help='Discover historical run folders under this directory')
    ap.add_argument('--catalog', type=Path, help='Override the shared local SQLite catalog')
    ap.add_argument('--port', type=int, default=8765)
    args = ap.parse_args()
    catalog = Catalog(args.catalog)
    catalog.discover(local_data_roots() + args.import_root)
    server = serve(args.run, catalog, args.port)
    print(f'Training workspace: http://127.0.0.1:{server.server_port}', flush=True)
    try:
        while True: time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown(); server.server_close()


if __name__ == '__main__': main()
