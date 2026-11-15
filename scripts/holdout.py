"""Locked holdout for stage-model evaluation (plan A5, decision Q4).

From the freeze night on, every fourth night group is locked: it is excluded from
training, tuning, feature selection and dashboard metrics. Only two kinds of read may
score a locked night: a frozen model that is already deployed (a trial, plan E1), and
one final score per search run (plan F). Every read is appended to
`data/holdout-access.jsonl` in the primary checkout, so the number of times the holdout
has been looked at is always known.
"""
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path

from run_history import primary_root

FREEZE = '2026-11-14'
EVERY = 4
RULE = f'night groups on or after {FREEZE} whose offset in days from it is a multiple of {EVERY}'
PURPOSES = ('trial', 'final')


def locked(night):
    """True for a locked night group ('YYYY-MM-DD')."""
    offset = (date.fromisoformat(night) - date.fromisoformat(FREEZE)).days
    return offset >= 0 and offset % EVERY == 0


def split(groups):
    """(development, locked) night groups, each sorted."""
    ordered = sorted(set(groups))
    return [g for g in ordered if not locked(g)], [g for g in ordered if locked(g)]


def drop_locked(nights, holdout_final=False, key=lambda night: night['group']):
    """Nights a development run may use; `holdout_final` keeps them for a logged final read."""
    return list(nights) if holdout_final else [n for n in nights if not locked(key(n))]


def access_log(path=None):
    return Path(path) if path else primary_root() / 'data/holdout-access.jsonl'


def log_access(run, model_sha256, nights, purpose, path=None):
    """Record one read of locked nights before scoring them; returns the entry."""
    if purpose not in PURPOSES:
        raise ValueError(f'Locked nights are read only for {" or ".join(PURPOSES)}, not {purpose!r}')
    read = sorted(g for g in set(nights) if locked(g))
    entry = dict(run=str(run), modelSha256=model_sha256, nights=read, purpose=purpose, rule=RULE,
                 utc=datetime.now(timezone.utc).isoformat(timespec='seconds'))
    target = access_log(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('a') as stream:
        stream.write(json.dumps(entry, sort_keys=True) + '\n')
    return entry


def accesses(path=None):
    target = access_log(path)
    if not target.exists():
        return []
    return [json.loads(line) for line in target.read_text().splitlines() if line.strip()]


def file_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def protocol():
    """The rule as recorded in every run's protocol."""
    return dict(freeze=FREEZE, every=EVERY, rule=RULE, accessLog='data/holdout-access.jsonl')
