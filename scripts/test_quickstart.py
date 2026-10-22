"""Behavioral checks for scripts/quickstart.sh with stub tools and a stub pod."""
import contextlib
import http.server
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest

SCRIPT = Path(__file__).with_name('quickstart.sh')
WANT_NODE = (Path(__file__).parents[1] / '.node-version').read_text().strip().split('.')[0]


@contextlib.contextmanager
def stub_pod(status=200):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(status if self.path == '/api/settings' else 404)
            self.end_headers()

        def log_message(self, *args):
            pass
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f'127.0.0.1:{server.server_address[1]}'
    finally:
        server.shutdown()
        server.server_close()


class QuickstartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.bin = Path(self.tmp.name) / 'bin'
        self.bin.mkdir()
        self.tool('node', f'echo v{WANT_NODE}.3.0')
        self.tool('npm', 'exit 0')
        self.tool('uv', 'echo "uv 0.9.0"')

    def tearDown(self):
        self.tmp.cleanup()

    def tool(self, name, body):
        path = self.bin / name
        path.write_text(f'#!/bin/sh\n{body}\n')
        path.chmod(0o755)

    def run_script(self, *args, stdin=''):
        env = {**os.environ, 'PATH': f'{self.bin}:/usr/bin:/bin', 'HOME': self.tmp.name}
        env.pop('POD', None)
        return subprocess.run(['bash', str(SCRIPT), *args], input=stdin, capture_output=True, text=True, env=env, timeout=30)

    def test_dry_run_prints_the_training_command_with_every_choice(self):
        export = Path(self.tmp.name) / 'export.zip'
        export.write_bytes(b'zip')
        with stub_pod() as pod:
            result = self.run_script('--dry-run', '--pod', pod, '--side', 'right', '--watch-export', str(export))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('would run: npm ci && uv sync --locked', result.stdout)
        self.assertIn(f'scripts/train.py --pod {pod} --side right --dashboard --out data/runs/', result.stdout)
        self.assertIn(f'--watch-export {export}', result.stdout)
        self.assertIn('Pod reachable', result.stdout)

    def test_prompts_for_pod_and_side_and_allows_no_export(self):
        with stub_pod() as pod:
            result = self.run_script('--dry-run', stdin=f'{pod}\nleft\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f'--pod {pod} --side left --dashboard', result.stdout)
        self.assertNotIn('--watch-export', result.stdout)

    def test_full_run_installs_then_trains_with_the_chosen_export(self):
        log = Path(self.tmp.name) / 'calls.log'
        self.tool('npm', f'echo "npm $*" >> {log}')
        self.tool('uv', f'[ "$1" = "--version" ] && echo "uv 0.9.0" || echo "uv $*" >> {log}')
        export = Path(self.tmp.name) / 'export.zip'
        export.write_bytes(b'zip')
        with stub_pod() as pod:
            result = self.run_script('--pod', pod, '--side', 'left', stdin='~/export.zip\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = log.read_text().splitlines()
        self.assertEqual(calls[0], 'npm ci --no-audit --no-fund')
        self.assertEqual(calls[1], 'uv sync --locked')
        self.assertTrue(calls[2].startswith(f'uv run --locked python scripts/train.py --pod {pod} --side left --dashboard --out data/runs/'))
        self.assertTrue(calls[2].endswith(f'--watch-export {export}'))

    def test_relative_export_path_resolves_from_the_starting_directory(self):
        elsewhere = Path(self.tmp.name) / 'downloads'
        elsewhere.mkdir()
        (elsewhere / 'export.zip').write_bytes(b'zip')
        env = {**os.environ, 'PATH': f'{self.bin}:/usr/bin:/bin', 'HOME': self.tmp.name}
        env.pop('POD', None)
        with stub_pod() as pod:
            result = subprocess.run(['bash', str(SCRIPT), '--dry-run', '--pod', pod, '--side', 'left', '--watch-export', 'export.zip'],
                                    cwd=elsewhere, capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f'--watch-export {elsewhere / "export.zip"}', result.stdout)

    def test_rejects_wrong_node_major(self):
        self.tool('node', 'echo v22.1.0')
        result = self.run_script('--dry-run', '--pod', '127.0.0.1', '--side', 'left')
        self.assertEqual(result.returncode, 1)
        self.assertIn(f'Node.js {WANT_NODE} is required', result.stderr)

    def test_missing_node_or_npm_explains_how_to_install(self):
        (self.bin / 'npm').unlink()
        self.assertIn('npm is missing', self.run_script('--dry-run').stderr)
        (self.bin / 'node').unlink()
        self.assertIn(f'brew install node@{WANT_NODE}', self.run_script('--dry-run').stderr)

    def test_missing_uv_is_not_installed_without_consent(self):
        (self.bin / 'uv').unlink()
        result = self.run_script('--dry-run', stdin='n\n')
        self.assertEqual(result.returncode, 1)
        self.assertIn('uv is required', result.stderr)

    def test_unreachable_pod_bad_side_and_missing_export_fail_clearly(self):
        self.assertIn('cannot reach the pod', self.run_script('--dry-run', '--pod', '127.0.0.1:9').stderr)
        self.assertIn('a pod address is required', self.run_script('--dry-run', stdin='\n').stderr)
        with stub_pod() as pod:
            self.assertIn('side must be left or right', self.run_script('--dry-run', '--pod', pod, '--side', 'middle').stderr)
            self.assertIn('no file at', self.run_script('--dry-run', '--pod', pod, '--side', 'left', '--watch-export', '/nope.zip').stderr)
        with stub_pod(status=500) as pod:
            self.assertIn('cannot reach the pod', self.run_script('--dry-run', '--pod', pod).stderr)

    def test_help_and_unknown_options(self):
        result = self.run_script('--help')
        self.assertEqual(result.returncode, 0)
        self.assertIn('--watch-export FILE', result.stdout)
        self.assertIn('Read-only toward the pod', result.stdout)
        bad = self.run_script('--bogus')
        self.assertEqual(bad.returncode, 1)
        self.assertIn('unknown option: --bogus', bad.stderr)


if __name__ == '__main__':
    unittest.main()
