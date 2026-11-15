/** Canonical-JSON test vectors computed by core's own code, pinned in scripts/test_modelset.py.
 * Usage (inside a core checkout): pnpm exec tsx <training>/scripts/canonical-vectors.ts <training>/scripts/canonical-fixtures.json
 * Core computes configHash/dataHash (src/biometrics/algorithms/canonical.ts) and the stage
 * node hash as the pod does: stageModelSha256(stageModelSchema.parse(model)).
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

const [fixturesPath, core = process.cwd()] = process.argv.slice(2)
const { canonicalJson, configHash, dataHash } = await import(join(core, 'src/biometrics/algorithms/canonical.ts'))
const { stageModelSchema } = await import(join(core, 'src/lib/personal-stage-model.ts'))
const setModule = await import(join(core, 'src/lib/stage-model-set.ts')).catch(() => null)
const { fixtures, stageModel } = JSON.parse(readFileSync(fixturesPath, 'utf8')) as { fixtures: string[], stageModel: number }
const fails = (run: () => unknown) => {
  try {
    run()
    return null
  }
  catch (error) {
    return (error as Error).message
  }
}
const nested = (depth: number) => Array.from({ length: depth }).reduce<unknown>(inner => [inner], 1)
const model = stageModelSchema.parse(JSON.parse(fixtures[stageModel]))
console.log(JSON.stringify({
  vectors: fixtures.map((text) => {
    const value = JSON.parse(text)
    return { canonical: canonicalJson(value), configHash: configHash(value), dataHash: dataHash(value) }
  }),
  stageModel: setModule ? setModule.stageModelSha256(model) : dataHash(model),
  limits: {
    depth32: fails(() => configHash(nested(32))), depth33: fails(() => configHash(nested(33))),
    config19999: fails(() => configHash(Array(19999).fill(0))), config20000: fails(() => configHash(Array(20000).fill(0))),
    data20000: fails(() => dataHash(Array(20000).fill(0))), loneSurrogate: fails(() => configHash(['\ud800'])),
    string65536: fails(() => configHash(['x'.repeat(65536)])), string65537: fails(() => configHash(['x'.repeat(65537)])),
    largeData: dataHash(Array.from({ length: 50000 }, (_, i) => ({ i, x: i / 7 }))),
  },
}, null, 1))
