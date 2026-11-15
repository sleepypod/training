"""Pod HTTP GET with a /usr/bin/curl fallback.

On macOS, Local Network privacy refuses LAN connections from interpreters that lack the
permission (EHOSTUNREACH, "No route to host") although the pod is reachable; Apple's own
curl is exempt. `open_url` retries exactly that refusal once through curl and returns a
response with the same `read`, `headers` and `status`, or raises the same HTTPError.
"""
import email
import errno
import os
from pathlib import Path
import subprocess
import tempfile
import urllib.error

CURL = Path('/usr/bin/curl')


def refused_by_privacy(error):
    reason = getattr(error, 'reason', None)
    return isinstance(reason, OSError) and reason.errno == errno.EHOSTUNREACH


class CurlResponse:
    def __init__(self, status, headers, body):
        self.status, self.headers, self._body = status, headers, body

    def read(self, size=-1):
        return self._body.read(size)

    def getcode(self):
        return self.status

    def close(self):
        self._body.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def parse_head(text):
    """Status and headers of the last response head curl wrote (interim 1xx heads come first)."""
    blocks = [b for b in text.replace('\r\n', '\n').split('\n\n') if b.strip()]
    if not blocks:
        raise urllib.error.URLError('curl returned no response head')
    status_line, _, rest = blocks[-1].partition('\n')
    parts = status_line.split(maxsplit=2)
    if len(parts) < 2 or not parts[1].isdigit():
        raise urllib.error.URLError(f'curl returned no HTTP status line: {status_line!r}')
    return int(parts[1]), parts[2] if len(parts) > 2 else '', email.message_from_string(rest + '\n\n')


def curl_open(url, timeout, curl=CURL):
    # urllib's timeout is per socket operation, so stall detection, not a total limit.
    with tempfile.TemporaryDirectory() as directory:
        head, body = Path(directory) / 'head', Path(directory) / 'body'
        result = subprocess.run([str(curl), '-sS', '--connect-timeout', str(timeout), '--speed-limit', '1', '--speed-time', str(timeout),
                                 '-D', str(head), '-o', str(body), '--', url], capture_output=True, text=True)
        if result.returncode != 0:
            raise urllib.error.URLError(result.stderr.strip() or f'curl exited with {result.returncode}')
        status, reason, headers = parse_head(head.read_text('latin-1'))
        stream = body.open('rb') if body.exists() else open(os.devnull, 'rb')
    if status >= 400:
        raise urllib.error.HTTPError(url, status, reason, headers, stream)
    return CurlResponse(status, headers, stream)


def open_url(opener, url, timeout):
    """`opener.open(url, timeout=timeout)`, retried through curl when macOS refuses the LAN connection."""
    try:
        return opener.open(url, timeout=timeout)
    except urllib.error.HTTPError:
        raise
    except urllib.error.URLError as error:
        if not (refused_by_privacy(error) and CURL.exists()):
            raise
    return curl_open(url, timeout)
