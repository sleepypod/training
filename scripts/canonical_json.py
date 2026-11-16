"""core's canonical JSON (finite-binary64-json-v1) and the hashes built on it; the only Python port.

Ported exactly from core src/biometrics/algorithms/canonical-json.ts and canonical.ts: keys
sorted by UTF-16 code units, no whitespace, `JSON.stringify` string escaping, each number
through float() as its exact decimal expansion (no exponent, no trailing zeros, -0 is 0;
NaN and infinities refused), and the same value, depth, string and byte budgets. Pinned to
vectors core computed in test_modelset.py (scripts/canonical-vectors.ts).
"""
from decimal import Decimal
import hashlib
import json
import math

NAME = 'finite-binary64-json-v1'
EPOCH_SECONDS = 300
CONFIG_LIMITS = dict(max_nodes=20000, max_bytes=math.inf)
DATA_LIMITS = dict(max_nodes=2000000, max_bytes=32 * 1024 * 1024)


def number_token(value):
    """Exact decimal expansion of a finite binary64; integers convert like JSON.parse would."""
    value = float(value)
    if not math.isfinite(value):
        raise ValueError('Non-finite JSON number')
    if value == 0:
        return '0'
    text = format(Decimal(abs(value)), 'f')
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return '-' + text if value < 0 else text


def utf16_units(text):
    return len(text) + sum(1 for c in text if ord(c) > 0xFFFF)


def canonical_with_limit(value, max_nodes, max_bytes=math.inf):
    """core canonicalWithLimit: sorted UTF-16 keys, the same node, depth and byte budgets."""
    state = dict(nodes=0, bytes=0)

    def token(text):
        if max_bytes == math.inf:
            return text
        state['bytes'] += len(text.encode('utf-8'))
        if state['bytes'] > max_bytes:
            raise ValueError('Canonical data byte limit')
        return text

    def visit(item, depth):
        state['nodes'] += 1
        if state['nodes'] > max_nodes or depth > 32:
            raise ValueError('JSON resource limit')
        if item is None:
            return token('null')
        if isinstance(item, bool):
            return token('true' if item else 'false')
        if isinstance(item, (int, float)):
            try:
                return token(number_token(item))
            except OverflowError:
                raise ValueError('Non-finite JSON number') from None
        if isinstance(item, str):
            if utf16_units(item) > 65536 or any(0xD800 <= ord(c) <= 0xDFFF for c in item):
                raise ValueError('Invalid JSON string')
            return token(json.dumps(item, ensure_ascii=False))
        if isinstance(item, (list, tuple)):
            if len(item) > max_nodes - state['nodes']:
                raise ValueError('JSON resource limit')
            token('[' + ',' * max(0, len(item) - 1) + ']')
            return '[' + ','.join(visit(child, depth + 1) for child in item) + ']'
        if isinstance(item, dict) and all(isinstance(k, str) for k in item):
            keys = sorted(item, key=lambda k: k.encode('utf-16-be'))
            if len(keys) * 2 > max_nodes - state['nodes']:
                raise ValueError('JSON resource limit')
            token('{' + ':' * len(keys) + ',' * max(0, len(keys) - 1) + '}')
            return '{' + ','.join(f'{visit(k, depth + 1)}:{visit(item[k], depth + 1)}' for k in keys) + '}'
        raise ValueError('Expected finite JSON data')

    return visit(value, 0)


def canonical(value):
    """The encoding with no budget, for documents core hashes without one (movement-model-v1 bodies)."""
    return canonical_with_limit(value, math.inf)


def canonical_json(value):
    """core canonicalJson: configHash's 20,000-value budget."""
    return canonical_with_limit(value, **CONFIG_LIMITS)


def config_hash(value):
    """core configHash."""
    return hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()


def data_hash(value):
    """core dataHash: same encoding, the larger budget for models and retained rows."""
    return hashlib.sha256(canonical_with_limit(value, **DATA_LIMITS).encode('utf-8')).hexdigest()


def stage_sha256(model):
    """core stageModelSha256: the model as the pod stores it (epochSeconds defaults to 300)."""
    return data_hash({'epochSeconds': EPOCH_SECONDS, **model})
