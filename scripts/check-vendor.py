"""Check pinned production sources; optionally compare against a core checkout."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--core', type=Path, help='Optional core checkout to compare byte for byte')
    args = ap.parse_args()
    manifest = json.loads((ROOT / 'vendor/core-source.json').read_text())
    for relative, expected in manifest['files'].items():
        local = ROOT / 'vendor/core' / relative
        if hashlib.sha256(local.read_bytes()).hexdigest() != expected:
            raise SystemExit(f'Vendor checksum mismatch: {relative}')
        if args.core and (args.core / relative).read_bytes() != local.read_bytes():
            raise SystemExit(f'Core checkout differs: {relative}')
    print(f'Validated {len(manifest["files"])} pinned core files at {manifest["commit"]}')


if __name__ == '__main__':
    main()
