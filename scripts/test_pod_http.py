"""curl fallback for pod reads that macOS Local Network privacy refuses."""
import errno
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pod_http


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/missing':
            self.send_response(404)
            self.send_header('X-Pod', 'pod-1')
            self.end_headers()
            self.wfile.write(b'{"error":"no"}')
            return
        body = b'\r\n\r\n{"ok": true}'
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class Refusing:
    """An opener that fails the way macOS refuses an interpreter's LAN connection."""
    def open(self, url, timeout):
        raise urllib.error.URLError(OSError(errno.EHOSTUNREACH, 'No route to host'))


class PodHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f'http://127.0.0.1:{cls.server.server_address[1]}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    @unittest.skipUnless(pod_http.CURL.exists(), 'needs /usr/bin/curl')
    def test_refused_connection_is_retried_through_curl(self):
        with pod_http.open_url(Refusing(), self.base + '/ok', 5) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers.get('Content-Length'), '16')
            self.assertEqual(response.read(), b'\r\n\r\n{"ok": true}')

    @unittest.skipUnless(pod_http.CURL.exists(), 'needs /usr/bin/curl')
    def test_curl_errors_keep_http_error_semantics(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            pod_http.open_url(Refusing(), self.base + '/missing', 5)
        self.assertEqual(caught.exception.code, 404)
        self.assertEqual(caught.exception.headers.get('X-Pod'), 'pod-1')
        self.assertIsNotNone(caught.exception.headers.get('Date'))
        self.assertEqual(caught.exception.read(), b'{"error":"no"}')

    def test_other_failures_are_not_retried(self):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with patch.object(pod_http, 'curl_open') as curl:
            with self.assertRaises(urllib.error.URLError):
                pod_http.open_url(opener, 'http://127.0.0.1:1/', 2)
            curl.assert_not_called()

    def test_direct_connection_is_used_when_allowed(self):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with patch.object(pod_http, 'curl_open') as curl, pod_http.open_url(opener, self.base + '/ok', 5) as response:
            self.assertEqual(response.read(), b'\r\n\r\n{"ok": true}')
            curl.assert_not_called()

    def test_last_head_wins_after_interim_responses(self):
        status, reason, headers = pod_http.parse_head('HTTP/1.1 100 Continue\r\n\r\nHTTP/1.1 429 Too Many Requests\r\nRetry-After: 60\r\n\r\n')
        self.assertEqual((status, reason, headers.get('Retry-After')), (429, 'Too Many Requests', '60'))


if __name__ == '__main__':
    unittest.main()
