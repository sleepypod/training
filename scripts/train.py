"""Run local Watch parsing, production feature extraction, nested training, and parity.

No network access to the pod, model upload, or activation is performed.
"""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def run(*command):
    subprocess.run(command, cwd=ROOT, check=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--snapshot', type=Path, required=True, help='Consistent local biometrics SQLite backup')
    inputs = ap.add_mutually_exclusive_group(required=True)
    inputs.add_argument('--watch-export', type=Path, help='Apple Health ZIP, export.xml, or directory containing it')
    inputs.add_argument('--watch-json', type=Path, help='Previously extracted {stages: [[startMs,endMs,stage],...]}')
    ap.add_argument('--source', default='Watch')
    ap.add_argument('--side', choices=['left', 'right'], required=True)
    ap.add_argument('--timezone', required=True, help='Pod IANA timezone, e.g. America/Los_Angeles')
    ap.add_argument('--out', type=Path, default=ROOT / 'data/runs' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    args = ap.parse_args()
    snapshot = args.snapshot.resolve(strict=True)
    watch = (args.watch_export or args.watch_json).resolve(strict=True)
    output = args.out.resolve()
    run(sys.executable, str(ROOT / 'scripts/check-vendor.py'))
    output.mkdir(parents=True, exist_ok=False)
    tsx = str(ROOT / 'node_modules/.bin/tsx')
    if args.watch_export:
        parsed = output / 'watch.json'
        run(sys.executable, str(ROOT / 'scripts/parse-watch-export.py'), str(watch), str(parsed), '--source', args.source)
        watch = parsed
    features = output / 'features.json'
    run(tsx, str(ROOT / 'scripts/export-runtime-features.ts'), str(snapshot), str(watch), str(features), args.side, args.timezone)
    model = output / 'model'
    run(sys.executable, str(ROOT / 'scripts/retrain-models.py'), str(features), '--out', str(model))
    run(tsx, str(ROOT / 'scripts/verify-portable.ts'), str(model))
    print(f'Completed local training and TypeScript parity: {output}')


if __name__ == '__main__':
    main()
