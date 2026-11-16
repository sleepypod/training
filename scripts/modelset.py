"""Model set manifests (`modelset-v1`): the version of every node a stage model was trained on.

Training writes one next to its models as RUN/model/set.json; core stores it with the
uploaded model. `pinned` nodes must be installed exactly; `expected` nodes (vitals) are
owned by biometrics tuning and a difference is flagged per night, not refused.
`nodes.stage.sha256` is core's `stageModelSha256`: SHA-256 of the model, as the pod
stores it, in core's canonical JSON (canonical_json.py). Never uploads anything.

  hash   MODEL.json                 print the stage node sha256 core will compute
  check  SET.json --model MODEL.json
  write  RUN --inputs live|replayed [--engine E --profile P] [--movement-version V --movement-file F]
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from canonical_json import stage_sha256
import holdout

FORMAT = 'modelset-v1'
MAX_SET_BYTES = 64 * 1024
INPUTS = ('replayed', 'live')
SHA256 = re.compile(r'^[a-f0-9]{64}$')


def file_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def training_nights(groups):
    """Development night groups a model was fitted on, and how many locked ones were kept out."""
    development, locked = holdout.split(groups)
    if not development:
        raise ValueError('No development nights')
    return dict(first=development[0], last=development[-1], count=len(development), lockedExcluded=len(locked))


def build(set_id, model, contract, inputs, nights, vitals=None, movement=None, archive=None, evaluation=None):
    """A modelset-v1 manifest. `vitals` is {engine, profile}; `movement` is {version, sha256}; `contract` may be None."""
    nodes = dict(stage=dict(version=model['version'], sha256=stage_sha256(model), **({'contract': contract} if contract else {}), binding='pinned'))
    if vitals:
        nodes['vitals'] = dict(engine=vitals['engine'], profile=vitals['profile'], binding='expected')
    if movement:
        nodes['movementModel'] = dict(version=movement['version'], sha256=movement['sha256'], binding='pinned')
    training = dict(nights=nights, inputs=inputs)
    if archive:
        training['archive'] = archive
    manifest = dict(formatVersion=FORMAT, id=set_id, nodes=nodes, training=training)
    if evaluation:
        manifest['evaluation'] = evaluation
    validate(manifest, model)
    return manifest


def problems(manifest, model=None):
    """Everything core's modelSetSchema and modelSetProblem would refuse, plus the training fields."""
    out = []

    def text(value, limit):
        return isinstance(value, str) and 1 <= len(value) <= limit

    def strict(obj, path, required, optional=()):
        if not isinstance(obj, dict):
            out.append(f'{path} must be an object')
            return False
        out.extend(f'{path}.{k} is missing' for k in required if k not in obj)
        out.extend(f'{path}.{k} is not allowed' for k in obj if k not in required and k not in optional)
        return True

    def sha(value, path):
        if not (isinstance(value, str) and SHA256.match(value)):
            out.append(f'{path} must be a lowercase hex SHA-256')

    if not strict(manifest, 'set', ('formatVersion', 'id', 'nodes'), ('training', 'evaluation')):
        return out
    if manifest.get('formatVersion') != FORMAT:
        out.append(f'formatVersion must be {FORMAT}')
    if not text(manifest.get('id'), 200):
        out.append('id must be 1-200 characters')
    nodes = manifest.get('nodes')
    if strict(nodes, 'nodes', ('stage',), ('vitals', 'movementModel')):
        stage = nodes.get('stage')
        if strict(stage, 'nodes.stage', ('version', 'sha256', 'binding'), ('contract',)):
            if not text(stage.get('version'), 120):
                out.append('nodes.stage.version must be 1-120 characters')
            sha(stage.get('sha256'), 'nodes.stage.sha256')
            if 'contract' in stage and not text(stage['contract'], 120):
                out.append('nodes.stage.contract must be 1-120 characters')
            if stage.get('binding') != 'pinned':
                out.append('nodes.stage.binding must be pinned')
            if model is not None:
                if stage.get('version') != model.get('version'):
                    out.append(f"nodes.stage.version {stage.get('version')} is not the model's {model.get('version')}")
                actual = stage_sha256(model)
                if stage.get('sha256') != actual:
                    out.append(f"nodes.stage.sha256 {stage.get('sha256')} but the model hashes to {actual}")
        if 'vitals' in nodes and strict(nodes['vitals'], 'nodes.vitals', ('engine', 'profile', 'binding')):
            sha(nodes['vitals'].get('engine'), 'nodes.vitals.engine')
            sha(nodes['vitals'].get('profile'), 'nodes.vitals.profile')
            if nodes['vitals'].get('binding') != 'expected':
                out.append('nodes.vitals.binding must be expected')
        if 'movementModel' in nodes and strict(nodes['movementModel'], 'nodes.movementModel', ('version', 'sha256', 'binding')):
            if not text(nodes['movementModel'].get('version'), 120):
                out.append('nodes.movementModel.version must be 1-120 characters')
            sha(nodes['movementModel'].get('sha256'), 'nodes.movementModel.sha256')
            if nodes['movementModel'].get('binding') != 'pinned':
                out.append('nodes.movementModel.binding must be pinned')
    for key in ('training', 'evaluation'):
        if key in manifest and not isinstance(manifest[key], dict):
            out.append(f'{key} must be an object')
    training = manifest.get('training')
    if isinstance(training, dict):
        if training.get('inputs') not in INPUTS:
            out.append(f'training.inputs must be one of {", ".join(INPUTS)}')
        if training.get('inputs') == 'replayed' and 'vitals' not in (nodes or {}):
            out.append('replayed inputs need nodes.vitals: the engine and profile every night was replayed with')
    if len(json.dumps(manifest, separators=(',', ':'), ensure_ascii=False).encode()) > MAX_SET_BYTES:
        out.append(f'a model set manifest is at most {MAX_SET_BYTES // 1024} KB')
    return out


def validate(manifest, model=None):
    found = problems(manifest, model)
    if found:
        raise ValueError('Invalid model set: ' + '; '.join(found))
    return manifest


def write(run, manifest, model=None):
    validate(manifest, model)
    target = Path(run) / 'model' / 'set.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, indent=2) + '\n')
    return target


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='command', required=True)
    h = sub.add_parser('hash', help='stage node sha256 of a stage model JSON')
    h.add_argument('model', type=Path)
    c = sub.add_parser('check', help='validate a set.json, against its stage model when given')
    c.add_argument('set', type=Path)
    c.add_argument('--model', type=Path)
    w = sub.add_parser('write', help='write RUN/model/set.json for RUN/model/candidate.json')
    w.add_argument('run', type=Path, help='train.py run directory (features.json and model/candidate.json)')
    w.add_argument('--inputs', choices=INPUTS, required=True)
    w.add_argument('--engine', help='vitals engine: biometrics manifest hash')
    w.add_argument('--profile', help='vitals profile: resolved config hash')
    w.add_argument('--movement-version')
    w.add_argument('--movement-file', type=Path)
    w.add_argument('--id')
    args = ap.parse_args(argv)
    if args.command == 'hash':
        print(stage_sha256(json.loads(args.model.read_text())))
    elif args.command == 'check':
        found = problems(json.loads(args.set.read_text()), json.loads(args.model.read_text()) if args.model else None)
        print('\n'.join(found) if found else f'{args.set}: valid {FORMAT}')
        return 1 if found else 0
    else:
        if bool(args.engine) != bool(args.profile) or bool(args.movement_version) != bool(args.movement_file):
            ap.error('--engine and --profile go together, as do --movement-version and --movement-file')
        model = json.loads((args.run / 'model/candidate.json').read_text())
        features = json.loads((args.run / 'features.json').read_text())
        manifest = build(args.id or f"{features['side']}-{features['contract']}-{model['version']}", model, features['contract'], args.inputs,
                         training_nights(n['group'] for n in features['nights']),
                         vitals=dict(engine=args.engine, profile=args.profile) if args.engine else None,
                         movement=dict(version=args.movement_version, sha256=file_sha256(args.movement_file)) if args.movement_file else None)
        print(f'Wrote {write(args.run, manifest, model)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
