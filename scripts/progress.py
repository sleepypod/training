"""Atomic local progress state, with a compact terminal display."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time


def update(phase, detail, **fields):
    filename = os.environ.get('TRAINING_PROGRESS')
    if not filename:
        return
    path = Path(filename)
    state = json.loads(path.read_text()) if path.exists() else {'startedAt': time.time(), 'status': 'running'}
    state.update(phase=phase, detail=detail, updatedAt=datetime.now(timezone.utc).isoformat(), **fields)
    state['elapsedSeconds'] = round(time.time() - state['startedAt'], 1)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(state, allow_nan=False))
    temp.replace(path)
    done, total = state.get('completedFits', 0), state.get('totalFits', 0)
    bar = ''
    if total and phase == 'Training':
        count = min(24, int(24 * done / total))
        bar = '[' + '#' * count + '.' * (24 - count) + f'] {done}/{total} fits '
    line = f'{phase:12} {bar}{detail} ({state["elapsedSeconds"]:.0f}s)'
    if sys.stderr.isatty():
        print('\r\033[2K' + line, end='\n' if state.get('status') in ('complete', 'failed', 'cancelled') else '', file=sys.stderr, flush=True)
    else:
        print(line, file=sys.stderr, flush=True)
