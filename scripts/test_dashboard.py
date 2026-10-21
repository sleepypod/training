import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.request
import urllib.error

from dashboard import serve, write_report
from progress import update


class DashboardTests(unittest.TestCase):
    def test_saved_report_escapes_script_injection(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'progress.json').write_text(json.dumps({'detail': '</script><script>alert(1)</script>'}))
            write_report(root)
            html = (root / 'index.html').read_text()
            self.assertNotIn('</script><script>alert', html)
            self.assertIn('\\u003c/script', html)

    def test_loopback_viewer_does_not_serve_private_inputs(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root / 'features.json').write_text('private')
            write_report(root); server = serve(root)
            try:
                base = f'http://127.0.0.1:{server.server_port}'
                self.assertEqual(urllib.request.urlopen(base).status, 200)
                for path in ['/features.json', '/../features.json', '/']:
                    if path == '/':
                        request = urllib.request.Request(base + path, data=b'{}', method='POST')
                    else: request = base + path
                    with self.assertRaises(urllib.error.HTTPError): urllib.request.urlopen(request)
            finally: server.shutdown();server.server_close()

    def test_failed_progress_preserves_counters(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'TRAINING_PROGRESS': str(Path(d) / 'progress.json')}):
            update('Training', 'Fitting', completedFits=4, totalFits=12)
            update('Stopped', 'Failure', status='failed')
            state = json.loads((Path(d) / 'progress.json').read_text())
            self.assertEqual(state['completedFits'], 4)
            self.assertEqual(state['status'], 'failed')
            self.assertFalse((Path(d) / 'progress.tmp').exists())


if __name__ == '__main__': unittest.main()
