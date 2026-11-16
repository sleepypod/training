"""SHA-256 of a file in fixed-size chunks, so a large snapshot or export is never read into memory at once."""
import hashlib

CHUNK = 1 << 22


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        while chunk := f.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()
