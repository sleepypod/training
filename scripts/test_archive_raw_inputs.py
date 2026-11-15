"""Archive RAW resolver: archived .RAW.gz files read exactly like an /api/export/archive tarball."""
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import joblib
import numpy as np

import movement_model as mm
import piezo_bandwidth as pb
import raw_piezo as rp
from test_movement import START, synthetic
from test_piezo_bandwidth import cbor, outer


class ConstantForest:
    classes_ = np.array(['light', 'still', 'vigorous'])

    def predict_proba(self, x):
        p = (np.arange(len(x)) % 5) / 5
        return np.column_stack([p / 2, 1 - p, p / 2])


def raw_body(recs):
    return gzip.compress(b''.join(outer(i, cbor(rec)) for i, rec in enumerate(recs)), mtime=0)


def archive_layout(root, recs, bookkeeping=True):
    """A session's records split over two archived RAW files, a far-away third, and the same two as a tarball."""
    split = len(recs) // 2
    files = [('0000000A.RAW.gz', raw_body(recs[:split]), START + 200), ('0000000B.RAW.gz', raw_body(recs[split:]), START + 1200),
             ('0000000C.RAW.gz', raw_body(recs[:10]), START + 86_400)]
    pod = Path(root) / 'archive' / 'pod-1'
    for sub in ('raw', 'archive'):
        (pod / sub).mkdir(parents=True)
    db = sqlite3.connect(pod / 'biometrics.db')
    db.execute('CREATE TABLE movement (id INTEGER PRIMARY KEY, side TEXT NOT NULL, timestamp INTEGER NOT NULL, total_movement INTEGER NOT NULL)')
    if bookkeeping:
        db.execute('CREATE TABLE archive_raw_files (sha256 TEXT PRIMARY KEY NOT NULL, name TEXT NOT NULL, archive_name TEXT NOT NULL UNIQUE, '
                   'mtime_ms INTEGER NOT NULL, bytes INTEGER NOT NULL, pull_key TEXT NOT NULL)')
    for name, body, mtime in files:
        sha = hashlib.sha256(body).hexdigest()
        (pod / 'raw' / f'{sha}.RAW.gz').write_bytes(body)
        os.link(pod / 'raw' / f'{sha}.RAW.gz', pod / 'archive' / name)
        os.utime(pod / 'archive' / name, (mtime, mtime))
        if bookkeeping:
            db.execute('INSERT INTO archive_raw_files VALUES (?, ?, ?, ?, ?, ?)', (sha, name, name, mtime * 1000, len(body), 'pulls/x.json'))
    db.commit()
    db.close()
    export = Path(root) / 'session.tar.gz'
    with tarfile.open(export, 'w:gz') as tar:
        for name, body, _ in files[:2]:
            info = tarfile.TarInfo(f'./raw/{name}')
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
    return pod, export, files


class ResolverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.recs, self.session, self.movement = synthetic()
        self.end = START + 300

    def tearDown(self):
        self.tmp.cleanup()

    def test_archive_records_match_the_export_of_the_same_files(self):
        pod, export, _ = archive_layout(self.tmp.name, self.recs)
        self.assertEqual([name for name, _, _ in rp.archive_window(pod, START, self.end)], ['0000000A.RAW.gz', '0000000B.RAW.gz'])
        self.assertEqual(pb.load_export(pod, START, self.end), pb.load_export(export))

    def test_without_bookkeeping_the_archive_links_and_their_mtimes_are_used(self):
        pod, export, _ = archive_layout(self.tmp.name, self.recs, bookkeeping=False)
        self.assertTrue(all(sha is None for _, _, sha in rp.archive_window(pod, START, self.end)))
        self.assertEqual(pb.load_export(pod, START, self.end), pb.load_export(export))

    def test_a_tampered_archive_file_is_refused(self):
        pod, _, files = archive_layout(self.tmp.name, self.recs)
        target = pod / 'raw' / f'{hashlib.sha256(files[0][1]).hexdigest()}.RAW.gz'
        os.unlink(pod / 'archive' / files[0][0])
        target.write_bytes(raw_body(self.recs[:3]))
        with self.assertRaises(ValueError):
            pb.load_export(pod, START, self.end)

    def test_an_archive_read_needs_a_window(self):
        pod, _, _ = archive_layout(self.tmp.name, self.recs)
        with self.assertRaises(ValueError):
            list(rp.raw_files(pod))


class EpochsFromArchiveTests(unittest.TestCase):
    def test_epochs_from_the_archive_equal_epochs_from_the_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            recs, _, movement = synthetic()
            pod, export, files = archive_layout(tmp, recs)
            with sqlite3.connect(pod / 'biometrics.db') as db:
                db.executemany("INSERT INTO movement (side, timestamp, total_movement) VALUES ('left', ?, ?)", movement)
            snapshot = pod / 'biometrics.db'
            features = Path(tmp) / 'features.json'
            features.write_text(json.dumps({'side': 'left', 'sources': {'snapshot': hashlib.sha256(snapshot.read_bytes()).hexdigest()},
                                            'nights': [{'id': 7, 'group': '2027-01-15', 'start': START, 'end': START + 300, 'rows': [{'start': START}]}]}))
            teaching = {'x': np.zeros((2, 22)), 'labels': np.array(['still', 'light']), 'nights': np.array(['2099-01-01'] * 2), 't': np.zeros(2)}
            fitted = {'model': ConstantForest(), 'strictThreshold': 0.3}
            joblib.dump({'final': fitted, 'heldOut': {'2099-01-01': fitted}, 'teaching': teaching}, Path(tmp) / 'model.joblib')
            common = [str(features), '--snapshot', str(snapshot), '--model', str(Path(tmp) / 'model.joblib'), '--workers', '1']
            with patch('builtins.print'):
                mm.main(['epochs', *common, '--export', f'7={export}', '--out', str(Path(tmp) / 'from-export.json')])
                mm.main(['epochs', *common, '--archive', str(pod), '--out', str(Path(tmp) / 'from-archive.json')])
            a, b = (json.loads((Path(tmp) / f'from-{k}.json').read_text())['sessions']['7'] for k in ('export', 'archive'))
            self.assertEqual(a['rows'], b['rows'])
            self.assertGreater(a['rows'][0]['coverage'], 0.9)
            self.assertEqual(b['archiveRawSha256'], [hashlib.sha256(body).hexdigest() for _, body, _ in files[:2]])
            self.assertEqual(a['exportSha256'], hashlib.sha256(export.read_bytes()).hexdigest())

    def test_epochs_needs_a_raw_source(self):
        with patch('sys.stderr'), self.assertRaises(SystemExit):
            mm.main(['epochs', 'f.json', '--snapshot', 's.db', '--model', 'm.joblib', '--out', 'o.json'])


if __name__ == '__main__':
    unittest.main()
