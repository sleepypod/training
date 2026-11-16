"""Chunked file hashing gives the same SHA-256 as hashing the whole file."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import hashing


class FileSha256Tests(unittest.TestCase):
    def test_matches_a_whole_file_hash_across_chunk_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'snapshot.db'
            for size in (0, 7, 64, 65, 1000):
                path.write_bytes(bytes(i % 251 for i in range(size)))
                with patch.object(hashing, 'CHUNK', 64):
                    self.assertEqual(hashing.file_sha256(path), hashlib.sha256(path.read_bytes()).hexdigest())


if __name__ == '__main__':
    unittest.main()
