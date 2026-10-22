"""Decode pod RAW files into per-channel piezo segments; read-only, no pod writes.

RAW files are a sequence of outer {seq, data} CBOR maps whose data bytes hold
one or more inner firmware records (core modules/common/cbor_raw.py and
src/streaming/piezoStream.ts). piezo-dual records carry whole-second `ts`,
`freq` and little-endian int32 sample buffers in left1/right1 (left2/right2 on
some hardware). INT32_MAX marks a lost sample.
"""
import gzip
import io
import struct
import tarfile
from pathlib import Path

import numpy as np

LOST_SAMPLE = np.iinfo(np.int32).max
CHANNELS = ('left1', 'right1', 'left2', 'right2')
MAX_DATA = 1_000_000
MAX_INTERPOLATED = 5  # samples (10 ms at 500 Hz)


def _argument(read, additional):
    if additional < 24:
        return additional
    size = {24: 1, 25: 2, 26: 4, 27: 8}.get(additional)
    if size is None:
        raise ValueError('Invalid CBOR length encoding')
    raw = read(size)
    if len(raw) < size:
        raise EOFError
    return int.from_bytes(raw, 'big')


def read_outer(f):
    """Return inner bytes of the next outer record, None for an empty marker; EOFError at end."""
    head = f.read(1)
    if not head:
        raise EOFError
    if head[0] != 0xa2 or f.read(4) != b'\x63seq':
        raise ValueError('Expected outer {seq, data} map')
    seq = f.read(1)
    if not seq or seq[0] >> 5 != 0:
        raise ValueError('seq must be an unsigned int')
    _argument(f.read, seq[0] & 31)
    if f.read(5) != b'\x64data':
        raise ValueError('Expected data key')
    kind = f.read(1)
    if not kind or kind[0] >> 5 != 2:
        raise ValueError('data must be a byte string')
    length = _argument(f.read, kind[0] & 31)
    if length > MAX_DATA:
        raise ValueError('Implausibly large data field')
    data = f.read(length)
    if len(data) < length:
        raise EOFError
    return data or None


class _Inner:
    def __init__(self, payload):
        self.payload, self.position = payload, 0

    def read(self, size):
        chunk = self.payload[self.position:self.position + size]
        self.position += len(chunk)
        return chunk

    def peek_break(self):
        if self.position < len(self.payload) and self.payload[self.position] == 0xff:
            self.position += 1
            return True
        if self.position >= len(self.payload):
            raise ValueError('Truncated indefinite container')
        return False

    def value(self, depth=0):
        if depth > 16:
            raise ValueError('CBOR nesting too deep')
        head = self.read(1)
        if not head:
            raise ValueError('Truncated inner record')
        major, additional = head[0] >> 5, head[0] & 31
        if major in (0, 1):
            n = _argument(self.read, additional)
            return n if major == 0 else -1 - n
        if major in (2, 3):
            if additional == 31:
                parts = []
                while not self.peek_break():
                    parts.append(self.value(depth + 1))
                joined = b''.join(parts) if major == 2 else ''.join(parts)
                return joined
            raw = self.read(_argument(self.read, additional))
            return raw if major == 2 else raw.decode('utf-8', 'replace')
        if major == 4:
            if additional == 31:
                items = []
                while not self.peek_break():
                    items.append(self.value(depth + 1))
                return items
            return [self.value(depth + 1) for _ in range(_argument(self.read, additional))]
        if major == 5:
            out = {}
            if additional == 31:
                while not self.peek_break():
                    key = self.value(depth + 1)
                    out[key] = self.value(depth + 1)
                return out
            for _ in range(_argument(self.read, additional)):
                key = self.value(depth + 1)
                out[key] = self.value(depth + 1)
            return out
        if major == 6:
            _argument(self.read, additional)
            return self.value(depth + 1)
        if additional == 20:
            return False
        if additional == 21:
            return True
        if additional in (22, 23):
            return None
        if additional == 25:
            return float(np.frombuffer(self.read(2), dtype='>f2')[0])
        if additional == 26:
            return struct.unpack('>f', self.read(4))[0]
        if additional == 27:
            return struct.unpack('>d', self.read(8))[0]
        raise ValueError('Unsupported CBOR simple value')


def inner_records(data):
    """Decode concatenated inner CBOR records; malformed tails are reported, not guessed."""
    inner = _Inner(data)
    while inner.position < len(data):
        yield inner.value()


def records(raw_bytes):
    """Yield (record, error) pairs from one RAW file's bytes; stops at a truncated tail."""
    f = io.BytesIO(raw_bytes)
    while True:
        try:
            data = read_outer(f)
        except EOFError:
            return
        except ValueError as error:
            yield None, str(error)
            return
        if data is None:
            continue
        try:
            for rec in inner_records(data):
                if isinstance(rec, dict):
                    yield rec, None
        except ValueError as error:
            yield None, str(error)


def file_bytes(path):
    raw = Path(path).read_bytes()
    return gzip.decompress(raw) if str(path).endswith('.gz') else raw


def export_files(tar_path):
    """Yield (name, compressed_size, raw_bytes) for every RAW file in an /api/export/archive tarball."""
    with tarfile.open(tar_path, 'r:gz') as tar:
        for member in tar.getmembers():
            name = Path(member.name).name
            if not member.isfile() or not name.upper().endswith(('.RAW', '.RAW.GZ')) or name.upper() == 'SEQNO.RAW':
                continue
            raw = tar.extractfile(member).read()
            yield name, len(raw), gzip.decompress(raw) if name.lower().endswith('.gz') else raw


def samples(buffer):
    """int32 little-endian samples with lost markers as NaN; truncated tails dropped."""
    if not isinstance(buffer, (bytes, bytearray)):
        return np.empty(0)
    usable = len(buffer) - len(buffer) % 4
    values = np.frombuffer(bytes(buffer[:usable]), dtype='<i4').astype(float)
    values[values == LOST_SAMPLE] = np.nan
    return values


def piezo_seconds(recs):
    """Map piezo-dual records to {ts: {'freq', channel: samples}}, keeping the first copy of each second."""
    seconds, duplicates = {}, 0
    for rec in recs:
        if rec.get('type') != 'piezo-dual' or not isinstance(rec.get('ts'), int):
            continue
        if rec['ts'] in seconds:
            duplicates += 1
            continue
        entry = {'freq': rec.get('freq'), 'adc': rec.get('adc'), 'gain': rec.get('gain')}
        for channel in CHANNELS:
            if channel in rec:
                entry[channel] = samples(rec[channel])
        seconds[rec['ts']] = entry
    return seconds, duplicates


def fill_short_gaps(values, limit=MAX_INTERPOLATED):
    """Linearly interpolate lost-sample runs of at most `limit` samples; longer runs stay NaN."""
    values = values.copy()
    lost = np.isnan(values)
    if not lost.any() or lost.all():
        return values
    edges = np.flatnonzero(np.diff(np.concatenate(([0], lost.astype(int), [0]))))
    good = np.flatnonzero(~lost)
    for begin, end in zip(edges[::2], edges[1::2]):
        if end - begin <= limit:
            idx = np.arange(begin, end)
            values[idx] = np.interp(idx, good, values[good])
    return values


def segments(seconds, channel, start_s, end_s):
    """Contiguous sample arrays for [start_s, end_s).

    Short lost-sample runs are interpolated (as core does); a missing second or
    a longer lost run splits the segment. Returns (segments, interpolated, dropped).
    """
    joined, interpolated, dropped = [], 0, 0
    for ts in range(int(start_s), int(end_s)):
        values = seconds.get(ts, {}).get(channel)
        if values is None or values.size == 0:
            joined.append(np.array([np.nan]))
            continue
        filled = fill_short_gaps(values)
        interpolated += int(np.isnan(values).sum() - np.isnan(filled).sum())
        dropped += int(np.isnan(filled).sum())
        joined.append(filled)
    if not joined:
        return [], 0, 0
    stream = np.concatenate(joined)
    finite = np.isfinite(stream)
    edges = np.flatnonzero(np.diff(np.concatenate(([0], finite.astype(int), [0]))))
    return [stream[a:b] for a, b in zip(edges[::2], edges[1::2])], interpolated, dropped
