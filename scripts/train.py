"""Fetch or load paired data, train offline, and show progress. Never activates a model."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import os
import subprocess
import sys
import time
from zoneinfo import ZoneInfo

from dashboard import serve, write_report
from fetch_pod import fetch
from progress import update
from telemetry import summarize_features

ROOT = Path(__file__).resolve().parents[1]


def run(*command):
    subprocess.run(command, cwd=ROOT, check=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    pod = ap.add_mutually_exclusive_group(required=True)
    pod.add_argument('--snapshot', type=Path, help='Consistent local biometrics SQLite backup')
    pod.add_argument('--pod', help='Pod IP/hostname (default HTTP port 3000); read-only, no authentication')
    inputs = ap.add_mutually_exclusive_group()
    inputs.add_argument('--watch-export', type=Path, help='Apple Health ZIP/XML; overrides pod reference labels')
    inputs.add_argument('--watch-json', type=Path, help='Previously extracted Watch intervals; overrides pod labels')
    ap.add_argument('--days', type=int, default=30, help='Pod acquisition window, 1–365 days; completed sessions only')
    ap.add_argument('--source', default='Watch')
    ap.add_argument('--side', choices=['left', 'right'], required=True)
    ap.add_argument('--timezone', help='IANA timezone; default from pod, required with --snapshot')
    ap.add_argument('--verbose', action='store_true', help='Keep every progress event as a terminal log line')
    ap.add_argument('--dashboard', action='store_true', help='Serve a live loopback dashboard; stays open until Ctrl-C')
    ap.add_argument('--out', type=Path, default=ROOT / 'data/runs' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    args = ap.parse_args()
    if not 1 <= args.days <= 365: ap.error('--days must be between 1 and 365')
    if args.snapshot and not (args.watch_export or args.watch_json): ap.error('--snapshot requires --watch-export or --watch-json')
    if args.snapshot and not args.timezone: ap.error('--snapshot requires --timezone')
    if args.timezone: ZoneInfo(args.timezone)
    snapshot = args.snapshot.resolve(strict=True) if args.snapshot else None
    watch = (args.watch_export or args.watch_json).resolve(strict=True) if args.watch_export or args.watch_json else None
    output = args.out.resolve()
    run(sys.executable, str(ROOT / 'scripts/check-vendor.py'))
    output.mkdir(parents=True, exist_ok=False)
    os.environ['TRAINING_PROGRESS'] = str(output / 'progress.json')
    if args.verbose: os.environ['TRAINING_VERBOSE'] = '1'
    update('Preparing', 'Starting local training pipeline')
    write_report(output)
    server = serve(output) if args.dashboard else None
    if server: print(f'Live dashboard: http://127.0.0.1:{server.server_port} (Ctrl-C to close)', flush=True)
    try:
        if args.pod:
            snapshot, pod_watch, args.timezone = fetch(args.pod, args.side, args.days, output, args.timezone, fetch_watch=watch is None)
            watch = watch or pod_watch
        tsx = str(ROOT / 'node_modules/.bin/tsx')
        if args.watch_export:
            update('Labels', 'Extracting Watch reference stages')
            parsed = output / 'watch.json'
            run(sys.executable, str(ROOT / 'scripts/parse-watch-export.py'), str(watch), str(parsed), '--source', args.source)
            watch = parsed
        update('Features', 'Extracting production features and matching labels')
        features = output / 'features.json'
        extraction_started = time.perf_counter()
        run(tsx, str(ROOT / 'scripts/export-runtime-features.ts'), str(snapshot), str(watch), str(features), args.side, args.timezone)
        summarize_features(features, time.perf_counter() - extraction_started)
        model = output / 'model'
        run(sys.executable, str(ROOT / 'scripts/retrain-models.py'), str(features), '--out', str(model))
        update('Parity', 'Checking every exported prediction in TypeScript')
        run(tsx, str(ROOT / 'scripts/verify-portable.ts'), str(model))
        update('Complete', 'Training and parity passed. Model saved locally; no activation.', status='complete')
        write_report(output)
        print(f'Report: {output / "index.html"}', flush=True)
    except (Exception, KeyboardInterrupt) as error:
        update('Stopped', str(error) or 'Interrupted by user', status='cancelled' if isinstance(error, KeyboardInterrupt) else 'failed')
        write_report(output)
        raise
    finally:
        # Successful dashboard runs stay open; failed/interrupted runs always release the port.
        if server and sys.exc_info()[0] is not None:
            server.shutdown(); server.server_close()
    if server:
        try:
            print('Dashboard remains available. Press Ctrl-C to close; saved report works offline.', flush=True)
            while True: time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            server.shutdown(); server.server_close()


if __name__ == '__main__': main()
