"""Model set manifests and the canonical-JSON port, pinned to vectors core computed.

The vectors below came from scripts/canonical-vectors.ts run with `pnpm exec tsx` inside
core checkouts (sleepypod-core-archive fc40624c and core PR #930, stage-model-set, at 1fd2a784);
both printed identical results. Rerun it after core changes canonical-json.ts.
"""
import json
from pathlib import Path
import tempfile
import unittest

import modelset as m

FIXTURES = json.loads(Path(__file__).with_name('canonical-fixtures.json').read_text())
# configHash and dataHash agree for every fixture (all are within both budgets).
CORE_HASHES = [
    '1474d779659a22c3e599661d0aa6165074a0def4ff4442f97cc70becddea499f',
    'd28d4a49db30d04613eb2f3ece805e4df4cc35ba0c1e6bd1d84bc6a070cd9c0d',
    'ae121c2c6286b9921938ce4dcbe0627edfb0c7217a8729cb8c6c422be79d87cb',
    'd1f00cbdc70bcab2b940adf6a91973c75f1a4dc9fad211a2cb3ce295b95bd070',
    '4f869236f08f531bdc59e08cef360da030ec7234cf4674b048aea6b12a83d6ff',
]
CORE_CANONICAL = {
    0: '{"":"","B":[],"a":[true,false,null],"aa":{},"b":1}',
    2: '["\\u0000\\u001f\\"\\\\\\n\\t\\b\\f\\r/","  ","\U0001F600é\u007f","<&>"]',
    3: '{"Z":5,"a":7,"a\\u0000":6,"z":4,"é":1,"éa":8,"\U0001F600":2,"￿":3}',
}
# stageModelSha256(stageModelSchema.parse(model)): the schema defaults epochSeconds to 300 first.
CORE_STAGE = 'b9e1271fb47277b820f78ad5bb33aa28fdf7e96cf3dc66308a0da750c358be4c'
CORE_LARGE_DATA = '11a8cf9cdb08d65622d7ddd012b7b0eede7a46555bb76383183cb2752b04a08a'
VITALS = dict(engine='a' * 64, profile='b' * 64)


def nested(depth):
    value = 1
    for _ in range(depth):
        value = [value]
    return value


def model():
    return json.loads(FIXTURES['fixtures'][FIXTURES['stageModel']])


class CanonicalTests(unittest.TestCase):
    def test_matches_core_vectors(self):
        for i, text in enumerate(FIXTURES['fixtures']):
            value = json.loads(text)
            self.assertEqual(m.config_hash(value), CORE_HASHES[i], i)
            self.assertEqual(m.data_hash(value), CORE_HASHES[i], i)
            if i in CORE_CANONICAL:
                self.assertEqual(m.canonical_json(value), CORE_CANONICAL[i])

    def test_numbers_expand_exactly(self):
        self.assertEqual(m.number_token(0.1), '0.1000000000000000055511151231257827021181583404541015625')
        self.assertEqual(m.number_token(-0.0), '0')
        self.assertEqual(m.number_token(100.0), '100')
        self.assertEqual(m.number_token(9007199254740993), '9007199254740992')
        self.assertEqual(m.number_token(1e21), '1000000000000000000000')
        with self.assertRaises(ValueError):
            m.canonical_json([float('nan')])
        with self.assertRaises(ValueError):
            m.canonical_json([10 ** 400])

    def test_keys_sort_by_utf16_code_units(self):
        # U+1F600 is the surrogate pair D83D DE00, so it sorts before U+FFFF in core.
        self.assertEqual(m.canonical_json({'￿': 1, '\U0001F600': 2}), '{"\U0001F600":2,"￿":1}')

    def test_limits_match_core(self):
        m.config_hash(nested(32))
        for bad in (lambda: m.config_hash(nested(33)), lambda: m.config_hash([0] * 20000),
                    lambda: m.config_hash(['\ud800']), lambda: m.config_hash(['x' * 65537])):
            with self.assertRaises(ValueError):
                bad()
        m.config_hash([0] * 19999)
        m.config_hash(['x' * 65536])
        m.data_hash([0] * 20000)
        self.assertEqual(m.data_hash([{'i': i, 'x': i / 7} for i in range(50000)]), CORE_LARGE_DATA)

    def test_matches_core_golden_forest(self):
        # src/lib/tests/stage-model-set.test.ts in core PR #930: the upload validator's own golden value.
        forest = dict(version='test-forest', kind='forest', classes=['wake', 'light', 'deep', 'rem'],
                      featureContract=['hrZ', 'hrStdZ', 'motionZ', 'nightFraction'],
                      trees=[dict(f=[0, -1, -1], t=[-0.5, 0, 0], l=[1, -1, -1], r=[2, -1, -1], v=[[0, 0, 0, 0], [0, 1, 9, 0], [1, 8, 1, 0]])],
                      testVectors=[dict(inputs=dict(hrZ=-1, hrStdZ=0, motionZ=0, nightFraction=0.5), expected=2),
                                   dict(inputs=dict(hrZ=1, hrStdZ=0, motionZ=0, nightFraction=0.5), expected=1)],
                      metrics=dict(kappa=0.1, note='\u00e9'))
        self.assertEqual(m.stage_sha256(forest), '6fe42238f142207ca4016e4fe243a439e63e362043938fa15d4f09bdcf2c7906')

    def test_stage_hash_is_the_stored_model(self):
        self.assertEqual(m.stage_sha256(model()), CORE_STAGE)
        self.assertEqual(m.stage_sha256({**model(), 'epochSeconds': 300}), CORE_STAGE)


class ModelSetTests(unittest.TestCase):
    def manifest(self, **kwargs):
        nights = m.training_nights(['2026-11-12', '2026-11-13', '2026-11-14', '2026-11-15'])
        return m.build('left-podContext-test', model(), 'podContext', kwargs.pop('inputs', 'replayed'), nights, **{'vitals': VITALS, **kwargs})

    def test_builds_the_plan_shape(self):
        doc = self.manifest(movement=dict(version='movementRaw-v1-20261201', sha256='c' * 64),
                            evaluation=dict(run='run-id', protocolSha256='d' * 64, holdoutScored=True))
        self.assertEqual(doc['nodes']['stage'], dict(version=model()['version'], sha256=CORE_STAGE, contract='podContext', binding='pinned'))
        self.assertEqual(doc['nodes']['vitals'], {**VITALS, 'binding': 'expected'})
        self.assertEqual(doc['nodes']['movementModel']['binding'], 'pinned')
        # 2026-11-14 is the freeze night, so it is locked.
        self.assertEqual(doc['training']['nights'], dict(first='2026-11-12', last='2026-11-15', count=3, lockedExcluded=1))

    def test_refuses_what_core_refuses(self):
        doc = self.manifest()
        self.assertEqual(m.problems(doc, model()), [])
        wrong = json.loads(json.dumps(doc))
        wrong['nodes']['stage']['sha256'] = 'e' * 64
        self.assertTrue(any('hashes to' in p for p in m.problems(wrong, model())))
        extra = json.loads(json.dumps(doc))
        extra['nodes']['vitals']['note'] = 'x'
        self.assertIn('nodes.vitals.note is not allowed', m.problems(extra))
        expected = json.loads(json.dumps(doc))
        expected['nodes']['vitals']['binding'] = 'pinned'
        self.assertIn('nodes.vitals.binding must be expected', m.problems(expected))
        with self.assertRaises(ValueError):
            m.build('x', model(), 'podContext', 'replayed', doc['training']['nights'])
        with self.assertRaises(ValueError):
            self.manifest(inputs='mixed')

    def test_live_sets_may_leave_vitals_out_and_write_set_json(self):
        doc = self.manifest(inputs='live', vitals=None)
        self.assertNotIn('vitals', doc['nodes'])
        self.assertNotIn('contract', m.build('x', model(), None, 'live', doc['training']['nights'])['nodes']['stage'])
        with tempfile.TemporaryDirectory() as d:
            path = m.write(d, doc, model())
            self.assertEqual(path, Path(d) / 'model/set.json')
            self.assertEqual(json.loads(path.read_text()), doc)


if __name__ == '__main__':
    unittest.main()
