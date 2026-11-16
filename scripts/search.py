"""Model search over the pod model graph, on a fixed evaluation protocol (plan F).

  plan    SPEC.json --out RUN   expand the space, prepare and cache inputs, print cost
  run     RUN [--jobs N]        score every candidate on development nights
  report  RUN                   ranking, layer scores and the pre-registered comparison
  final   RUN --candidate ID    score once on the locked holdout, log the read, write model/set.json

A candidate is one choice per node: vitals (live rows, or one replayed engine and profile),
movementModel (none, or a movement_model.py version) and stage (feature contract, extra
feature groups, model). Each node's output is cached once per (night, node version,
upstream versions) under data/cache/<node>/, so replay and movement inference run once
per version, not once per candidate.

The protocol is fixed here, not in the spec: night groups are the unit; locked holdout
nights (holdout.py) are dropped by plan, run and report; every candidate is scored on the
same development sessions; stages walk forward (refit before each test night on every
earlier night, at least MIN_TRAIN_GROUPS); movement features are cross-fitted so a test
night's Watch motion never taught them; comparisons are paired per night against the
named baseline with a night-level bootstrap and a sign test. The spec names one primary
comparison before the run; every other comparison is exploratory.
Watch signals are teachers only. Never contacts a pod, uploads or activates a model.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor
import hashlib
import importlib.util
import itertools
import json
import os
from pathlib import Path
import re
import shlex
import sqlite3
import subprocess
import sys
import time

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import canonical_json as cj
import derive_snapshot as ds
import holdout as ho
import modelset as ms
from run_history import primary_root, register_output


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fu = load('fusion', 'fusion.py')
fa, sb, mm, vr = fu.fa, fu.sb, fu.mm, fu.vr
rm, pm = fa.rm, fa.pm

ROOT = Path(__file__).resolve().parents[1]
VERSION = 'search-v1'
BOOTSTRAP = 10000
SEED = 42
METRICS = ('macroF1', 'kappa', 'wakeF1', 'lightF1', 'deepF1', 'remF1')
GROUPS = ('context', 'movement', 'arousal', 'quality')  # Fixed column order; movement-model columns always come last.
GROUP_COLUMNS = {**fa.GROUPS, 'quality': vr.QUALITY}
STAGE_MODELS = ('balanced_leaf5', 'hierarchical')
CONTRACTS = ('podNative', 'podContext')
POD = sb.POD
WAKE = pm.STAGES.index('wake')
ID = re.compile(r'^[A-Za-z0-9._-]{1,40}$')
SCRIPTS = ('search.py', 'derive_snapshot.py', 'modelset.py', 'holdout.py', 'fusion.py', 'feature-ablation.py', 'stage-backtest.py',
           'retrain-models.py', 'movement_model.py', 'vitals_reference.py', 'export-runtime-features.ts')


def sha256(path, _memo={}):
    path = Path(path).resolve()
    stat = path.stat()
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    if key not in _memo:
        _memo[key] = ds.sha256(path)
    return _memo[key]


def key_of(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def resolve(path):
    """Spec paths are absolute or relative to the working directory, like every CLI path; plan records them absolute."""
    return Path(path).expanduser().resolve()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + '.partial')
    partial.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')
    partial.replace(path)


def read_json(path):
    return json.loads(Path(path).read_text())


# ---- spec ----

def choice_id(choice, fallback):
    value = choice.get('id', fallback) if isinstance(choice, dict) else choice
    if not isinstance(value, str) or not ID.match(value):
        raise ValueError(f'Choice id {value!r}: use 1-40 letters, digits, ".", "_" or "-"')
    return value


def expand(spec):
    """Validated node choices and every candidate (one choice per node), in spec order."""
    for key in ('name', 'side', 'timezone', 'inputs', 'primary', 'space'):
        if key not in spec:
            raise ValueError(f'Spec needs {key}')
    if spec['side'] not in ('left', 'right'):
        raise ValueError('side is left or right')
    space = spec['space']
    vitals = []
    for v in space.get('vitals', [{'id': 'live', 'profile': 'live'}]):
        kinds = [k for k in ('profile', 'config') if k in v]
        if v.get('profile') == 'live':
            vitals.append(dict(id=choice_id(v, 'live'), kind='live'))
        elif v.get('engine') and v.get('profile') and 'config' not in v:
            if not (ms.SHA256.match(v['engine']) and ms.SHA256.match(v['profile'])):
                raise ValueError('vitals engine and profile are lowercase SHA-256 hashes')
            vitals.append(dict(id=choice_id(v, v['profile'][:12]), kind='replayed', engine=v['engine'], profile=v['profile'],
                               allowUngated=bool(v.get('allowUngated'))))
        elif 'config' in v:
            vitals.append(dict(id=choice_id(v, Path(v['config']).stem), kind='replayed', config=v['config'], engine=v.get('engine'),
                               allowUngated=bool(v.get('allowUngated'))))
        else:
            raise ValueError(f'vitals choice {v}: give profile "live", engine and profile hashes, or a config file ({kinds})')
    movement = []
    for m in space.get('movementModel', ['none']):
        if m == 'none':
            movement.append(dict(id='none'))
        elif isinstance(m, dict) and m.get('model'):
            movement.append(dict(id=choice_id(m, Path(m['model']).parent.name), model=m['model'], epochs=list(m.get('epochs', [])),
                                 exports={str(k): v for k, v in m.get('exports', {}).items()}, dropUncovered=bool(m.get('dropUncovered'))))
        else:
            raise ValueError(f'movementModel choice {m!r}: "none" or {{"id", "model", "epochs": [...]}}')
    stage = []
    for s in space['stage']:
        groups = list(s.get('groups', []))
        contract, model = s.get('contract', 'podNative'), s.get('model', 'balanced_leaf5')
        if contract not in CONTRACTS or model not in STAGE_MODELS or any(g not in GROUPS for g in groups):
            raise ValueError(f'stage choice {s}: contract {CONTRACTS}, model {STAGE_MODELS}, groups {GROUPS}')
        if contract == 'podContext' and set(groups) & {'context', 'movement', 'arousal'}:
            raise ValueError('podContext already has the context, movement and arousal columns')
        stage.append(dict(id=choice_id(s, f'{contract}.{model}'), contract=contract, model=model,
                          groups=[g for g in GROUPS if g in groups]))
    for name, choices in (('vitals', vitals), ('movementModel', movement), ('stage', stage)):
        ids = [c['id'] for c in choices]
        if len(set(ids)) != len(ids) or not ids:
            raise ValueError(f'{name} choice ids must be present and unique')
    candidates = [dict(id=f"{v['id']}+{m['id']}+{s['id']}", vitals=v['id'], movementModel=m['id'], stage=s['id'])
                  for v, m, s in itertools.product(vitals, movement, stage)]
    limit = spec.get('budget', {}).get('maxCandidates', 24)
    if len(candidates) > limit:
        raise ValueError(f'{len(candidates)} candidates exceed budget.maxCandidates {limit}')
    ids = {c['id'] for c in candidates}
    primary = spec['primary']
    if primary.get('metric', 'macroF1') not in METRICS:
        raise ValueError(f'primary.metric is one of {METRICS}')
    if primary.get('candidate') not in ids or primary.get('baseline') not in ids | {POD} or primary['candidate'] == primary['baseline']:
        raise ValueError(f'primary.candidate and primary.baseline must be candidates of this space (or {POD} as baseline): {sorted(ids)}')
    return dict(vitals=vitals, movementModel=movement, stage=stage), candidates


# ---- inputs and caches ----

def export_features(snapshot, watch, side, timezone, contract, cache):
    """features.json for one snapshot and contract, cached by every input export-runtime-features.ts reads."""
    sidecars = {n: sha256(Path(snapshot).parent / n) for n in ('pod-stages.json', 'acquisition.json') if (Path(snapshot).parent / n).is_file()}
    key = key_of(dict(snapshot=sha256(snapshot), sidecars=sidecars, watch=sha256(watch), side=side, timezone=timezone, contract=contract,
                      exporter=sha256(ROOT / 'scripts/export-runtime-features.ts'), vendor=sha256(ROOT / 'vendor/core-source.json')))
    path = Path(cache) / 'features' / f'{key[:24]}.json'
    if not path.exists():
        partial = path.with_name(path.stem + '.partial.json')
        partial.unlink(missing_ok=True)
        subprocess.run([str(ROOT / 'node_modules/.bin/tsx'), str(ROOT / 'scripts/export-runtime-features.ts'), str(snapshot), str(watch),
                        str(partial), side, timezone, contract], check=True, stdout=subprocess.DEVNULL)
        partial.rename(path)
    return path


def given_features(path, snapshot, watch, side, contract):
    """An existing export for live vitals, used as is when it came from exactly this snapshot and Watch file."""
    path = resolve(path)
    data = read_json(path)
    if (data['sources']['snapshot'], data['sources']['watch'], data['side'], data.get('contract', 'podNative')) != (sha256(snapshot), sha256(watch), side, contract):
        raise ValueError(f'{path} was not exported from this snapshot, Watch file, side and {contract} contract')
    return path


def profile_overrides(config):
    """Overrides from `defaults` or a profile file, in the shapes core's replay --config accepts."""
    if config == 'defaults':
        return []
    doc = read_json(resolve(config))
    if isinstance(doc, list) and len(doc) == 1 and isinstance(doc[0], dict) and 'overrides' in doc[0]:
        doc = doc[0]
    return doc['overrides'] if isinstance(doc, dict) else doc


def resolve_config(choice, vcache):
    """(engine, profile) of a vitals choice given as a profile file, from cached replays of the same overrides."""
    overrides = cj.canonical_json(profile_overrides(choice['config']))
    found = set()
    for source in Path(vcache).glob('*.source.json'):
        record = read_json(source)
        if record.get('overrides') is not None and cj.canonical_json(record['overrides']) == overrides and choice.get('engine') in (None, record['engine']):
            found.add((record['engine'], record['profile']))
    if len(found) > 1:
        raise ValueError(f"vitals {choice['id']}: cached replays of {choice['config']} under several engines; name the engine")
    return found.pop() if found else (choice.get('engine'), None)


def movement_cache(choice, cache):
    """Ingest a movement choice's epochs files into data/cache/movementModel/<model12>/<session>.json; return {session: entry}."""
    model = resolve(choice['model'])
    model_sha = sha256(model)
    directory = Path(cache) / 'movementModel' / model_sha[:12]
    directory.mkdir(parents=True, exist_ok=True)
    teaching = set()
    for path in choice['epochs']:
        doc = read_json(resolve(path))
        if doc.get('version') != 'movement-epochs-v2':
            raise ValueError(f'{path}: movement epochs are not cross-fitted per held-out night; rerun movement_model.py epochs')
        if doc['modelSha256'] != model_sha:
            raise ValueError(f"{path}: computed with another model than {choice['model']}")
        teaching.update(doc['teachingNights'])
        for session, entry in doc['sessions'].items():
            target = directory / f'{session}.json'
            text = json.dumps(entry, sort_keys=True)
            if target.exists() and target.read_text() != text:
                raise ValueError(f'{target} is cached with different epochs for the same model and session')
            target.write_text(text)
    return model_sha, sorted(teaching), {p.stem: read_json(p) for p in directory.glob('*.json')}


def covers(entry, night):
    starts = {r['start'] for r in entry['rows']}
    return entry['group'] == night['group'] and all(r['start'] in starts for r in night['rows'])


def replayed_snapshot(source, engine, profile, side, timezone, nights, cache, allow_ungated):
    """A derived snapshot for these replayed nights, built once under data/cache/snapshots/."""
    vcache = Path(cache) / 'vitals'
    entries = sorted((n, ds.cached(vcache, n, engine, profile)[1]['readingsSha256']) for n in nights)
    key = key_of(dict(source=sha256(source), engine=engine, profile=profile, side=side, timezone=timezone, nights=entries,
                      allowUngated=allow_ungated, script=sha256(ds.__file__)))
    out = Path(cache) / 'snapshots' / key[:24] / 'biometrics.db'
    if not out.exists():
        ds.derive(source, out, engine, profile, side, timezone, [n for n, _ in entries], vcache, allow_ungated)
    return out


def replay_command(spec, choice, nights):
    inputs = spec['inputs']
    out = primary_root() / 'data/replays' / f"{spec['name']}-{choice['id']}"
    config = (choice['config'] if choice['config'] == 'defaults' else str(resolve(choice['config']))) if choice.get('config') \
        else f"<profile JSON resolving to {choice['profile']}>"
    command = ['pnpm', 'biometrics:local-fit', 'replay', '--archive', str(resolve(inputs['archive'])) if inputs.get('archive') else '<DEST/POD_ID>',
               '--nights', 'all', '--side', spec['side'], '--timezone', spec['timezone'], '--config', config, '--compare-stored', str(out)]
    for night, path in sorted(inputs.get('exports', {}).items()):
        if night in nights:
            command[-1:-1] = ['--export', f'{night}={resolve(path)}']
    core = str(resolve(inputs['core'])) if inputs.get('core') else '<core checkout>'
    return [f'cd {shlex.quote(core)} && ' + shlex.join(command),
            shlex.join(['uv', 'run', '--locked', 'python', 'scripts/derive_snapshot.py', 'ingest', str(out)])]


def movement_command(choice, features, snapshot, sessions, name):
    exports = [f"{s}={resolve(choice['exports'][s]) if s in choice['exports'] else '<raw export for session ' + s + '>'}" for s in sessions]
    out = primary_root() / 'data/runs' / f"{name}-{choice['id']}-epochs.json"
    return [shlex.join(['uv', 'run', '--locked', 'python', 'scripts/movement_model.py', 'epochs', str(features), '--snapshot', str(snapshot),
                        '--model', str(resolve(choice['model'])), *[a for e in exports for a in ('--export', e)], '--out', str(out)])
            + f'   # then add {out} to this choice\'s "epochs"']


# ---- plan ----

def plan(spec_path, out, cache=None):
    spec = read_json(spec_path)
    choices, candidates = expand(spec)
    out = Path(out)
    if out.exists() and any(p.name not in ('plan.json', 'report.json') for p in out.iterdir()):
        raise ValueError(f'{out} holds a started run; plan into a new directory')
    if out.exists() and read_json(out / 'plan.json').get('status') == 'ready':
        raise ValueError(f'{out} is already planned; plan into a new directory')
    cache = Path(cache or primary_root() / 'data/cache')
    vcache = cache / 'vitals'
    side, tz, inputs = spec['side'], spec['timezone'], spec['inputs']
    snapshot, watch = resolve(inputs['snapshot']), resolve(inputs['watch'])
    contracts = sorted({s['contract'] for s in choices['stage']})
    live_features = {c: given_features(inputs['features'][c], snapshot, watch, side, c) if c in inputs.get('features', {})
                     else export_features(snapshot, watch, side, tz, c, cache) for c in contracts}
    sessions = read_json(live_features[contracts[0]])['nights']
    wanted = set(inputs.get('nights') or [n['group'] for n in sessions])
    sessions = [n for n in sessions if n['group'] in wanted]
    for n in sessions:
        n['night'] = ds.night_of(n['start'], tz)
    development, locked = ho.split(n['group'] for n in sessions)
    excluded, blocked, commands = {}, [], []

    def exclude(session, why):
        excluded.setdefault(str(session['id']), dict(group=session['group'], reasons=[]))['reasons'].append(why)

    resolved = {}
    for v in choices['vitals']:
        if v['kind'] == 'live':
            resolved[v['id']] = dict(kind='live', snapshot=str(snapshot), snapshotSha256=sha256(snapshot))
            continue
        engine, profile = (v['engine'], v['profile']) if v.get('profile') else resolve_config(v, vcache)
        missing = []
        for n in sessions:
            hit = engine and profile and ds.cached(vcache, n['night'], engine, profile)
            reason = engine and profile and ds.skipped(vcache, n['night'], engine, profile)
            if hit:
                # core's training gate decides; a night it rejects keeps live rows, so no candidate may score it.
                use, verdict, why = ds.training_gate(hit[1].get('parity'), v['allowUngated'])
                if not use:
                    exclude(n, f"vitals {v['id']}: {why}")
                continue
            if reason:
                exclude(n, f"vitals {v['id']}: {reason}")
            elif n['group'] in development:
                missing.append(n['night'])
            else:
                exclude(n, f"vitals {v['id']}: locked night not replayed yet, so final could not read it")
        if missing or not profile:
            blocked.append(f"vitals {v['id']}: no cached replay for {len(set(missing)) or 'any'} development nights")
            commands.extend(replay_command(spec, {**v, 'profile': profile}, set(missing)))
        resolved[v['id']] = dict(kind='replayed', engine=engine, profile=profile, config=v.get('config'), allowUngated=v['allowUngated'])
    movement = {}
    for m in choices['movementModel']:
        if m['id'] == 'none':
            continue
        model_sha, teaching, cached_sessions = movement_cache(m, cache)
        taught = [g for g in teaching if ho.locked(g)]
        if taught:
            blocked.append(f"movementModel {m['id']}: taught by Watch motion on locked nights {', '.join(taught)}; retrain it without them")
        missing = []
        for n in sessions:
            entry = cached_sessions.get(str(n['id']))
            if entry is not None and covers(entry, n):
                continue
            if n['group'] in development and not m['dropUncovered']:
                missing.append(str(n['id']))
            else:
                exclude(n, f"movementModel {m['id']}: no cross-fitted epochs" + (' (dropUncovered)' if m['dropUncovered'] else ''))
        if missing:
            blocked.append(f"movementModel {m['id']}: no epochs for {len(missing)} development sessions")
            commands.extend(movement_command(m, live_features[contracts[0]], snapshot, missing, spec['name']))
        report = resolve(m['model']).with_name('report.json')
        movement[m['id']] = dict(model=str(resolve(m['model'])), sha256=model_sha, cache=str(cache / 'movementModel' / model_sha[:12]), teachingNights=teaching,
                                 epochs=[dict(path=str(resolve(p)), sha256=sha256(resolve(p))) for p in m['epochs']],
                                 report=str(report) if report.is_file() else None)
    kept = [n for n in sessions if str(n['id']) not in excluded]
    split = dict(development=sorted({n['group'] for n in kept if n['group'] in development}),
                 locked=sorted({n['group'] for n in kept if n['group'] in locked}),
                 developmentSessions=[str(n['id']) for n in kept if n['group'] in development],
                 lockedSessions=[str(n['id']) for n in kept if n['group'] in locked], excluded=excluded)
    if len(split['development']) <= sb.MIN_TRAIN_GROUPS:
        blocked.append(f"{len(split['development'])} development night groups; walk-forward needs more than {sb.MIN_TRAIN_GROUPS}")
    features = {}
    if not blocked:
        for v in choices['vitals']:
            r = resolved[v['id']]
            if r['kind'] == 'replayed':
                nights = sorted({n['night'] for n in kept})
                r['snapshot'] = str(replayed_snapshot(snapshot, r['engine'], r['profile'], side, tz, nights, cache, r['allowUngated']))
                r['gate'] = {n: ds.training_gate(ds.cached(vcache, n, r['engine'], r['profile'])[1].get('parity'), r['allowUngated'])[1] for n in nights}
                r['snapshotSha256'] = sha256(r['snapshot'])
                r['readingsSha256'] = {n: ds.cached(vcache, n, r['engine'], r['profile'])[1]['readingsSha256'] for n in nights}
            for c in contracts:
                path = live_features[c] if r['kind'] == 'live' else export_features(r['snapshot'], watch, side, tz, c, cache)
                features[f"{v['id']}/{c}"] = dict(path=str(path), sha256=sha256(path))
    scripts = {name: sha256(Path(__file__).with_name(name)) for name in SCRIPTS}
    protocol = dict(version=VERSION, unit='night group (noon-to-noon, as export-runtime-features.ts)', holdout=ho.record(n['group'] for n in sessions),
                    stage=dict(schedule='walk-forward', minTrainGroups=sb.MIN_TRAIN_GROUPS, forest=fa.FOREST, models=list(STAGE_MODELS),
                               scoredEpochs='labelled epochs that also have a live pod stage when the snapshot has pod stages, else every labelled epoch',
                               postprocessing='production smoothing and transition rules'),
                    movementModel='cross-fitted: features for every night come from movement models never taught by the test night\'s Watch motion; '
                                  'the layer score is movement_model.py train\'s leave-one-night-out report',
                    vitals='fixed profiles, nothing fitted: per-night agreement with Watch vitals on the same epochs (vitals_reference.py rules)',
                    statistics=dict(pairing='per development test night, against the named baseline', bootstrap=BOOTSTRAP, seed=SEED,
                                    interval='95% of the mean per-night difference', signTest='two-sided, ties dropped'),
                    sameNights='every candidate is scored on the same sessions: those every node choice covers')
    status = 'blocked' if blocked else 'ready'
    doc = dict(version=VERSION, status=status, spec=spec, specSha256=sha256(spec_path), choices=choices, candidates=candidates, resolved=resolved,
               movement=movement, features=features, cache=str(cache), split=split, blocked=blocked, commands=commands, protocol=protocol,
               protocolSha256=key_of(protocol), scripts=scripts,
               inputs=dict(snapshot=dict(path=str(snapshot), sha256=sha256(snapshot)), watch=dict(path=str(watch), sha256=sha256(watch)),
                           **({'watchVitals': dict(path=str(resolve(inputs['watchVitals'])), sha256=sha256(resolve(inputs['watchVitals'])))}
                              if inputs.get('watchVitals') else {}),
                           archive=archive_identity(resolve(inputs['archive'])) if inputs.get('archive') else None),
               command=shlex.join(['uv', 'run', '--locked', 'python', 'scripts/search.py', *sys.argv[1:]]))
    if not blocked:
        doc['cost'] = estimate(doc)
    write_json(out / 'plan.json', doc)
    write_json(out / 'report.json', dict(kind='search', status=status, name=spec['name'], candidates=len(candidates),
                                         nights=dict(development=len(split['development']), lockedExcluded=len(split['locked']))))
    doc['catalogId'] = register_output(out)
    if doc['catalogId']:
        write_json(out / 'plan.json', doc)
    return doc


def archive_identity(directory):
    """Pod id, pull cursor and database SHA-256 of an archive destination, when it is local."""
    db = Path(directory) / 'biometrics.db'
    pulls = sorted((Path(directory) / 'pulls').glob('*.json'))
    cursors = [read_json(p).get('cursor', {}).get('endMs') for p in pulls]
    return dict(podId=Path(directory).name, cursor=max([c for c in cursors if c is not None], default=None),
                databaseSha256=sha256(db) if db.is_file() else None)


def estimate(doc):
    """Forest fits per candidate, timed with one fit on every development night."""
    tests = len(doc['split']['development']) - sb.MIN_TRAIN_GROUPS
    stage = {s['id']: s for s in doc['choices']['stage']}
    fits = sum(tests * (2 if stage[c['stage']]['model'] == 'hierarchical' else 1) for c in doc['candidates'])
    first = doc['candidates'][0]
    data = read_json(doc['features'][f"{first['vitals']}/{stage[first['stage']]['contract']}"]['path'])
    keep = set(doc['split']['developmentSessions'])
    started = time.perf_counter()
    fa.fit_forest([n for n in data['nights'] if str(n['id']) in keep])
    seconds = time.perf_counter() - started
    return dict(candidates=len(doc['candidates']), testNights=tests, forestFits=fits, secondsPerFullFit=round(seconds, 2),
                upperBoundSeconds=round(fits * seconds, 1))


# ---- scoring ----

class Inputs:
    """Everything one candidate is scored from, loaded once per process."""

    def __init__(self, doc, candidate, sessions):
        self.doc, self.candidate = doc, candidate
        self.vitals = doc['resolved'][candidate['vitals']]
        self.stage = next(s for s in doc['choices']['stage'] if s['id'] == candidate['stage'])
        data = read_json(doc['features'][f"{candidate['vitals']}/{self.stage['contract']}"]['path'])
        self.data = data
        keep = set(sessions)
        self.nights = [n for n in data['nights'] if str(n['id']) in keep]
        if {str(n['id']) for n in self.nights} != keep:
            raise ValueError(f"{candidate['id']}: features lack sessions {sorted(keep - {str(n['id']) for n in self.nights})}")
        self.ruleset, self.features = data['ruleset'], data['features']
        self.shared = data.get('podStageSource') == 'pod-api'
        names = [name for g in self.stage['groups'] for name in GROUP_COLUMNS[g]]
        with sqlite3.connect(f"file:{Path(self.vitals['snapshot']).resolve()}?mode=ro", uri=True) as db:
            self.extras = []
            for night in self.nights:
                extra = fa.augment(night, self.features, db, data['side']) if set(self.stage['groups']) - {'quality'} else [{} for _ in night['rows']]
                if 'quality' in self.stage['groups']:
                    vitals = vr.night_vitals(db, data['side'], night['start'], night['end'])
                    extra = [{**e, **vr.quality_features(r['start'], vitals, self.ruleset['outliers'])} for r, e in zip(night['rows'], extra)]
                self.extras.append([[np.nan if e[k] is None else e[k] for k in names] for e in extra])
        self.movement = None
        if candidate['movementModel'] != 'none':
            directory = Path(doc['movement'][candidate['movementModel']]['cache'])
            self.movement = {str(n['id']): read_json(directory / f"{n['id']}.json") for n in self.nights}
        self.columns = list(self.features) + names + (list(mm.EPOCH_FEATURES) if self.movement else [])

    def dataset(self, held_out):
        """Every night's rows; movement-model columns come from models never taught by `held_out`'s Watch motion."""
        out = []
        for night, extra in zip(self.nights, self.extras):
            if self.movement:
                epochs = {r['start']: r for r in fu.movement_rows(self.movement[str(night['id'])], held_out)}
                tail = [[np.nan if epochs[r['start']][k] is None else epochs[r['start']][k] for k in mm.EPOCH_FEATURES] for r in night['rows']]
            else:
                tail = [[] for _ in night['rows']]
            out.append({**night, 'rows': [{**r, 'x': list(r['x']) + e + t} for r, e, t in zip(night['rows'], extra, tail)]})
        return out

    def fit(self, nights):
        return fa.Hierarchical(nights) if self.stage['model'] == 'hierarchical' else fa.fit_forest(nights)

    def predict(self, clf, held):
        if self.shared:
            y, p, _ = fu.predict_shared(clf, held, self.ruleset)
            return y, p
        return rm.predict(clf, held, self.ruleset)


def waso(sequence):
    sleep = [i for i, s in enumerate(sequence) if s != WAKE]
    return sum(1 for s in sequence[sleep[0]:sleep[-1]] if s == WAKE) if sleep else 0


def night_scores(y, p):
    """stage-backtest.py's per-night scores plus total sleep time and WASO error on the scored epochs."""
    scores = sb.night_scores(y, p)
    minutes = fa.EPOCH / 60
    scores['tstErrorMinutes'] = float((sum(s != WAKE for s in p) - sum(s != WAKE for s in y)) * minutes)
    scores['wasoErrorMinutes'] = float((waso(p) - waso(y)) * minutes)
    return scores


def pooled(y, p):
    result = rm.metrics(y, p)
    return dict(**result, f1={s: fa.f1(result['confusion'], i) for i, s in enumerate(pm.STAGES)})


def score(job):
    """Walk-forward scores of one candidate on the development sessions; runs in a worker process."""
    run, candidate_id = job
    doc = read_json(Path(run) / 'plan.json')
    candidate = next(c for c in doc['candidates'] if c['id'] == candidate_id)
    started = time.perf_counter()
    inputs = Inputs(doc, candidate, doc['split']['developmentSessions'])
    ys, ps, per_night = [], [], []
    for train_groups, test in sb.schedule({n['group'] for n in inputs.nights}, 'walk-forward'):
        data = inputs.dataset(test)
        train = [n for n in data if n['group'] in train_groups]
        held = [n for n in data if n['group'] == test]
        assert max(n['group'] for n in train) < test and not ho.locked(test)
        y, p = inputs.predict(inputs.fit(train), held)
        ys.extend(y)
        ps.extend(p)
        per_night.append(dict(group=test, trainedOn=len(train_groups), **night_scores(y, p)))
    result = dict(id=candidate_id, columns=inputs.columns, scoredEpochs='shared with pod' if inputs.shared else 'labelled',
                  nights=per_night, pooled=pooled(ys, ps), seconds=round(time.perf_counter() - started, 2))
    write_json(Path(run) / 'candidates' / f'{candidate_id}.json', result)
    return candidate_id, result['seconds'], result['pooled']['macroF1']


def ready(run):
    doc = read_json(Path(run) / 'plan.json')
    if doc['status'] != 'ready':
        raise SystemExit('This run is blocked; produce the inputs below, then plan again into a new directory:\n  '
                         + '\n  '.join(doc['blocked'] + doc['commands']))
    for item in [*doc['features'].values(), doc['inputs']['snapshot'], doc['inputs']['watch']]:
        if sha256(item['path']) != item['sha256']:
            raise SystemExit(f"{item['path']} changed since plan; plan again into a new directory")
    return doc


def run(run_dir, jobs=1):
    doc = ready(run_dir)
    todo = [c['id'] for c in doc['candidates'] if not (Path(run_dir) / 'candidates' / f"{c['id']}.json").exists()]
    print(f"{len(doc['candidates']) - len(todo)} of {len(doc['candidates'])} candidates already scored; scoring {len(todo)} on "
          f"{len(doc['split']['development'])} development nights with {jobs} jobs", flush=True)
    started = time.perf_counter()
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        for i, (cid, seconds, f1) in enumerate(pool.map(score, [(str(run_dir), c) for c in todo]), 1):
            print(f'[{i}/{len(todo)}] {cid}: walk-forward macro F1 {f1:.3f} ({seconds:.1f}s)', flush=True)
    if todo:
        print(f'Scored {len(todo)} candidates in {time.perf_counter() - started:.1f}s wall', flush=True)
    return report(run_dir)


# ---- comparisons and layers ----

def compare(a, b, metric):
    """Paired per-night difference a - b: mean, 95% night-bootstrap interval (fixed seed), sign test."""
    by_night = {r['group']: r[metric] for r in b}
    pairs = [(r[metric], by_night[r['group']]) for r in a if r['group'] in by_night and r[metric] is not None and by_night[r['group']] is not None]
    if not pairs:
        return None
    d = np.array([x - y for x, y in pairs])
    rng = np.random.default_rng(SEED)
    means = d[rng.integers(0, len(d), size=(BOOTSTRAP, len(d)))].mean(axis=1)
    up, down, p = sb.sign_test(d)
    return dict(nights=len(d), meanDifference=float(d.mean()), bootstrap95Percent=np.quantile(means, [.025, .975]).tolist(),
                better=up, worse=down, signTestP=p)


def pod_live(doc):
    """The stages the pod recorded live, on the same development test nights."""
    data = read_json(doc['features'][next(iter(doc['features']))]['path'])
    if data.get('podStageSource') != 'pod-api':
        return dict(id=POD, nights=[], pooled=None)
    keep = set(doc['split']['developmentSessions'])
    nights = [n for n in data['nights'] if str(n['id']) in keep]
    ys, ps, per_night = [], [], []
    for _, test in sb.schedule({n['group'] for n in nights}, 'walk-forward'):
        y, p = sb.pod_live([n for n in nights if n['group'] == test])
        ys.extend(y)
        ps.extend(p)
        per_night.append(dict(group=test, **night_scores(y, p)))
    return dict(id=POD, nights=per_night, pooled=pooled(ys, ps) if ys else None)


def vitals_layer(doc):
    """Each vitals choice against Watch HR, respiratory rate and SDNN on the development epochs, per night."""
    if 'watchVitals' not in doc['inputs']:
        return None
    watch = read_json(doc['inputs']['watchVitals']['path'])
    keep = set(doc['split']['developmentSessions'])
    out = {}
    for vid, r in doc['resolved'].items():
        data = read_json(doc['features'][next(k for k in doc['features'] if k.startswith(vid + '/'))]['path'])
        outliers = data['ruleset']['outliers']
        per_night, flat = [], {k: [] for k in ('hr', 'br', 'hrv')}
        with sqlite3.connect(f"file:{Path(r['snapshot']).resolve()}?mode=ro", uri=True) as db:
            for night in (n for n in data['nights'] if str(n['id']) in keep):
                vitals = vr.night_vitals(db, data['side'], night['start'], night['end'])
                pairs = {k: [] for k in flat}
                for row in night['rows']:
                    a, b = row['start'], row['start'] + fa.EPOCH
                    inside = [v for v in vitals if a <= v[0] < b]
                    for key, column, bound, teacher in (('hr', 1, 'heartRate', 'heartRate'), ('br', 2, 'breathingRate', 'respiratoryRate'),
                                                        ('hrv', 3, 'hrv', 'hrvSdnn')):
                        values = [v[column] for v in inside if vr.valid(v[column], outliers[bound])]
                        reference = vr.watch_mean(watch.get(teacher, []), a, b)
                        if values and reference is not None:
                            pairs[key].append((float(np.mean(values)), reference))
                for key in flat:
                    flat[key].extend(pairs[key])
                hr, br, hrv = vr.agreement(pairs['hr']), vr.agreement(pairs['br']), vr.rank_agreement(pairs['hrv'])
                per_night.append(dict(group=night['group'], session=str(night['id']), hrMae=hr and hr['mae'], brMae=br and br['mae'],
                                      hrvSpearman=hrv and hrv['spearman']))
        out[vid] = dict(kind=r['kind'], engine=r.get('engine'), profile=r.get('profile'), nights=per_night,
                        heartRate=vr.agreement(flat['hr']), breathingRate=vr.agreement(flat['br']), hrv=vr.rank_agreement(flat['hrv']))
    base = next(iter(out))
    for vid, layer in out.items():
        if vid != base:
            # Baseline minus candidate, so a positive difference and "better" both mean less error than the baseline.
            layer['versus'] = dict(baseline=base, **{f'{m}Reduction': compare(out[base]['nights'], layer['nights'], m) for m in ('hrMae', 'brMae')})
    return out


def movement_layer(doc):
    """movement_model.py train's own leave-one-night-out scores: strict moving recall at 1% still false alarms."""
    out = {}
    for mid, m in doc['movement'].items():
        report = read_json(m['report']) if m['report'] else None
        if not report:
            out[mid] = dict(sha256=m['sha256'], note='no report.json beside the model')
            continue
        window = report['windowLevel']
        out[mid] = dict(sha256=m['sha256'], validation=report['validation'], nights=report['nights'],
                        lockedTeachingNights=[n for n in report['nights'] if ho.locked(n)], strictFalseAlarm=report['strictFalseAlarm'],
                        modelStrict={k: window['modelStrict'][k] for k in ('movingRecall', 'stillFalseAlarm', 'balancedAccuracy')},
                        podScore={k: window['podScore'][k] for k in ('movingRecall', 'stillFalseAlarm', 'balancedAccuracy')})
    return out


def report(run_dir):
    run_dir = Path(run_dir)
    doc = ready(run_dir)
    scored = {c['id']: read_json(run_dir / 'candidates' / f"{c['id']}.json") for c in doc['candidates']
              if (run_dir / 'candidates' / f"{c['id']}.json").exists()}
    if len(scored) < len(doc['candidates']):
        raise SystemExit(f"{len(doc['candidates']) - len(scored)} candidates not scored yet; search.py run {run_dir}")
    primary = doc['spec']['primary']
    metric = primary.get('metric', 'macroF1')
    pod = pod_live(doc)
    table = dict(scored)
    if pod['pooled']:
        table[POD] = pod
    baseline = table[primary['baseline']]
    ranking = sorted(scored, key=lambda k: -(pooled_metric(scored[k]['pooled'], metric) or 0))
    exploratory = {cid: {m: compare(c['nights'], baseline['nights'], m) for m in ('macroF1', 'kappa', 'wakeF1')}
                   for cid, c in table.items() if cid not in (primary['baseline'], primary['candidate'])}
    seconds = [c['seconds'] for c in scored.values()]
    final = run_dir / 'final.json'
    accesses = ho.accesses(read_json(final).get('accessLog') if final.exists() else None)
    result = dict(kind='search', status='final' if final.exists() else 'scored', name=doc['spec']['name'], version=VERSION,
                  catalogId=doc.get('catalogId'), candidates=len(scored), protocolSha256=doc['protocolSha256'],
                  nights=dict(development=doc['split']['development'], lockedExcluded=len(doc['split']['locked']),
                              excludedSessions=doc['split']['excluded']),
                  pooled={cid: dict(c['pooled'], meanNight={m: mean([r[m] for r in c['nights']]) for m in METRICS})
                          for cid, c in table.items() if c['pooled']},
                  ranking=ranking, winner=dict(id=ranking[0], candidatesCompared=len(scored), metric=metric,
                                               note='exploratory: the best of many candidates; its interval does not survive the selection'),
                  primary=dict(**primary, preregistered=True, result=compare(table[primary['candidate']]['nights'], baseline['nights'], metric)),
                  exploratory=exploratory, layers=dict(vitals=vitals_layer(doc), movementModel=movement_layer(doc)),
                  timing=dict(meanSecondsPerCandidate=float(np.mean(seconds)), maxSecondsPerCandidate=float(np.max(seconds)),
                              totalCandidateSeconds=float(np.sum(seconds))),
                  holdout=dict(rule=ho.RULE, readsSoFar=len(accesses), finalRead=final.exists()),
                  limitations=['One participant; retrospective reuse of inspected development nights.',
                               'Watch stages, motion and vitals are teachers and references, not polysomnography or ECG.',
                               'Movement-model features carry Watch-motion information into staging by design; a stage gain from them may be '
                               'agreement with how the Watch stages (principle 1).',
                               'Only the primary comparison was named before the run; the winner and every other comparison are exploratory.',
                               'Locked-holdout scores live in final.json and are never written here.'])
    write_json(run_dir / 'report.json', result)
    print_report(result, doc, run_dir)
    return result


def pooled_metric(p, metric):
    return p[metric] if metric in ('macroF1', 'kappa') else p['f1'][metric[:-2]]


def mean(values):
    values = [v for v in values if v is not None]
    return float(np.mean(values)) if values else None


def fmt(c):
    return (f"{c['meanDifference']:+.3f} [{c['bootstrap95Percent'][0]:+.3f}, {c['bootstrap95Percent'][1]:+.3f}] "
            f"{c['better']}/{c['worse']} nights better/worse, sign p={c['signTestP']:.3f}") if c else 'n/a'


def print_report(r, doc, run_dir):
    print(f"\n{r['name']}: {r['candidates']} candidates · {len(r['nights']['development'])} development nights · "
          f"{r['nights']['lockedExcluded']} locked nights excluded · {len(r['nights']['excludedSessions'])} sessions excluded")
    print('walk-forward pooled (development nights):')
    for cid in r['ranking'] + ([POD] if POD in r['pooled'] else []):
        p = r['pooled'][cid]
        print(f"  {cid:42} macroF1 {p['macroF1']:.3f} κ {p['kappa'] or 0:.3f} · F1 wake {p['f1']['wake'] or 0:.2f} light {p['f1']['light'] or 0:.2f} "
              f"deep {p['f1']['deep'] or 0:.2f} rem {p['f1']['rem'] or 0:.2f}")
    q = r['primary']
    print(f"PRIMARY (pre-registered) {q['candidate']} vs {q['baseline']}, {q.get('metric', 'macroF1')}: {fmt(q['result'])}")
    print(f"winner (exploratory): {r['winner']['id']} of {r['winner']['candidatesCompared']} candidates compared")
    for cid, metrics in r['exploratory'].items():
        print(f"  exploratory {cid} vs {q['baseline']}: macroF1 {fmt(metrics['macroF1'])}")
    for vid, layer in (r['layers']['vitals'] or {}).items():
        hr, br, hrv = layer['heartRate'], layer['breathingRate'], layer['hrv']
        print(f"  vitals {vid}: HR MAE {hr['mae'] if hr else float('nan'):.2f} · BR MAE {br['mae'] if br else float('nan'):.2f} · "
              f"HRV ρ {(hrv or {}).get('spearman') or float('nan'):.2f}")
    for mid, layer in r['layers']['movementModel'].items():
        if 'modelStrict' in layer:
            print(f"  movementModel {mid}: strict moving recall {layer['modelStrict']['movingRecall']:.2f} at "
                  f"{layer['modelStrict']['stillFalseAlarm']:.3f} still false alarms (pod score {layer['podScore']['movingRecall']:.2f}), LONO")
    t = r['timing']
    print(f"time per candidate: mean {t['meanSecondsPerCandidate']:.1f}s, max {t['maxSecondsPerCandidate']:.1f}s")
    print(f"holdout reads so far (all runs): {r['holdout']['readsSoFar']}")
    if (run_dir / 'final.json').exists():
        f = read_json(run_dir / 'final.json')
        print(f"FINAL {f['candidate']} on {len(f['nights'])} locked nights: macroF1 {f['pooled']['macroF1']:.3f} κ {f['pooled']['kappa'] or 0:.3f}"
              + (f" · {POD} {f[POD]['macroF1']:.3f}" if f.get(POD) else ''))


# ---- final ----

def final(run_dir, candidate_id, access_log=None):
    run_dir = Path(run_dir)
    doc = ready(run_dir)
    if (run_dir / 'final.json').exists() or (run_dir / 'final.started').exists():
        raise SystemExit('This run already had its final read; a second final on the same run is refused')
    candidate = next((c for c in doc['candidates'] if c['id'] == candidate_id), None)
    if candidate is None:
        raise SystemExit(f'{candidate_id} is not a candidate of this run')
    if any(not (run_dir / 'candidates' / f"{c['id']}.json").exists() for c in doc['candidates']):
        raise SystemExit('Score every candidate (search.py run) before the final read')
    locked = doc['split']['lockedSessions']
    if not locked:
        raise SystemExit('No locked night is covered by this candidate yet; nothing to read')
    movement = doc['movement'].get(candidate['movementModel'])
    if movement and any(ho.locked(g) for g in movement['teachingNights']):
        raise SystemExit('The movement model was taught by Watch motion on locked nights; it cannot be scored on them')
    sessions = doc['split']['developmentSessions'] + locked
    inputs = Inputs(doc, candidate, sessions)
    groups = sorted({n['group'] for n in inputs.nights})
    development = [g for g in groups if not ho.locked(g)]
    locked_groups = [g for g in groups if ho.locked(g)]
    model_dir = run_dir / 'model'
    model_dir.mkdir(exist_ok=True)
    # The model is fixed before any locked night is read: fitted on development nights only.
    train = [n for n in inputs.dataset(None) if n['group'] in development]
    clf = inputs.fit(train)
    dev = read_json(run_dir / 'candidates' / f'{candidate_id}.json')['pooled']
    exported = None
    if inputs.stage['model'] == 'balanced_leaf5':
        try:
            rm.export(clf, inputs.stage['model'], dict(nights=train, features=inputs.columns), model_dir, dev)
        except ValueError as error:
            print(f'No portable model: {error}', flush=True)
        else:
            model = read_json(model_dir / 'candidate.json')
            model['metrics'] = {'walkForwardDevelopment': {k: dev[k] for k in ('macroF1', 'kappa', 'epochs')}}
            model['source'] = f'search.py final of {candidate_id}; development nights only; not prospectively validated'
            (model_dir / 'candidate.json').write_text(json.dumps(model, separators=(',', ':')))
            tsx = ROOT / 'node_modules/.bin/tsx'
            if tsx.exists():
                subprocess.run([str(tsx), str(ROOT / 'scripts/verify-portable.ts'), str(model_dir)], check=True)
            exported = model
    if exported:
        model_sha = cj.stage_sha256(exported)
    else:
        import joblib
        joblib.dump(clf, model_dir / 'candidate.joblib')
        model_sha = sha256(model_dir / 'candidate.joblib')
    (run_dir / 'final.started').write_text(candidate_id + '\n')
    entry = ho.log_access(ho.run_name(run_dir), model_sha, locked_groups, 'final', access_log)
    ys, ps, per_night, pod_y, pod_p = [], [], [], [], []
    for test in locked_groups:
        held = [n for n in inputs.dataset(test) if n['group'] == test]
        y, p = inputs.predict(clf, held)
        ys.extend(y)
        ps.extend(p)
        py, pp = sb.pod_live([n for n in inputs.nights if n['group'] == test])
        pod_y.extend(py)
        pod_p.extend(pp)
        per_night.append(dict(group=test, **night_scores(y, p)))
    deployable, why = deployability(inputs)
    result = dict(candidate=candidate_id, access=entry, accessLog=str(ho.access_log(access_log)), modelSha256=model_sha, nights=per_night, pooled=pooled(ys, ps),
                  **({POD: pooled(pod_y, pod_p)} if pod_y else {}), trainedOn=development, deployable=deployable, why=why,
                  note=f'POD baseline here is the stages the pod showed live; its model may have trained on nights before {ho.FREEZE}.')
    if exported:
        vitals = inputs.vitals
        manifest = ms.build(f"{doc['spec']['side']}-{candidate_id}-{exported['version'][len('research-'):]}"[:200], exported,
                            inputs.stage['contract'] if deployable else None, vitals['kind'], ms.training_nights(groups),
                            vitals=dict(engine=vitals['engine'], profile=vitals['profile']) if vitals['kind'] == 'replayed' else None,
                            movement=dict(version=candidate['movementModel'], sha256=movement['sha256']) if movement else None,
                            archive=doc['inputs'].get('archive'),
                            evaluation=dict(run=doc.get('catalogId') or ho.run_name(run_dir), protocolSha256=doc['protocolSha256'], holdoutScored=True))
        result['set'] = str(ms.write(run_dir, manifest, exported))
    write_json(run_dir / 'final.json', result)
    (run_dir / 'final.started').unlink()
    report(run_dir)
    print(f"Wrote {run_dir / 'final.json'}{' and ' + result['set'] if 'set' in result else ''}. Nothing uploaded or activated.")
    return result


def deployability(inputs):
    """A pod computes only core's own contracts: no Python-only groups and no movement model yet."""
    if inputs.stage['model'] != 'balanced_leaf5':
        return False, 'core has no two-step stage model format yet (plan E4)'
    if inputs.stage['groups']:
        return False, f"groups {inputs.stage['groups']} are computed in Python; a pod computes only {inputs.stage['contract']}"
    if inputs.movement:
        return False, 'movement-model features need the pod movement model (plan D2/D3)'
    return True, None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='command', required=True)
    p = sub.add_parser('plan', help='expand the space, prepare inputs, check caches, print cost')
    p.add_argument('spec', type=Path)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--cache', type=Path, help='default: data/cache in the primary checkout')
    r = sub.add_parser('run', help='score every candidate on development nights')
    r.add_argument('run', type=Path)
    r.add_argument('--jobs', type=int, default=max(1, (os.cpu_count() or 2) // 2))
    s = sub.add_parser('report', help='ranking, layer scores and the primary comparison')
    s.add_argument('run', type=Path)
    f = sub.add_parser('final', help='score one candidate once on the locked holdout and write model/set.json')
    f.add_argument('run', type=Path)
    f.add_argument('--candidate', required=True)
    f.add_argument('--access-log', type=Path, help='default: data/holdout-access.jsonl in the primary checkout; only synthetic checks use another')
    args = ap.parse_args(argv)
    if args.command == 'plan':
        doc = plan(args.spec, args.out, args.cache)
        s = doc['split']
        print(f"{len(doc['candidates'])} candidates · {len(s['development'])} development nights ({len(s['developmentSessions'])} sessions) · "
              f"{len(s['locked'])} locked nights held out · {len(s['excluded'])} sessions excluded")
        for sid, why in s['excluded'].items():
            print(f"  excluded session {sid} ({why['group']}): {'; '.join(why['reasons'])}")
        if doc['status'] == 'blocked':
            print('BLOCKED; run refuses to start until these exist:\n  ' + '\n  '.join(doc['blocked']))
            print('Produce them with:\n  ' + '\n  '.join(doc['commands']))
            return 2
        c = doc['cost']
        print(f"cost: {c['forestFits']} forest fits over {c['testNights']} walk-forward test nights; one full fit takes {c['secondsPerFullFit']}s, "
              f"so at most ~{c['upperBoundSeconds'] / 60:.0f} CPU-minutes")
        print(f'Next: uv run --locked python scripts/search.py run {args.out}')
    elif args.command == 'run':
        run(args.run, args.jobs)
    elif args.command == 'report':
        report(args.run)
    else:
        final(args.run, args.candidate, args.access_log)
    return 0


if __name__ == '__main__':
    sys.exit(main())
