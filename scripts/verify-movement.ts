/**
 * Check a movement-model-v1 forest (movement_model.py train/export) the way a pod would:
 * schema, SHA-256 of the canonical body, and every test vector within 1e-9.
 * Traversal is personal-stage-model.ts's, plus float32 inputs and missing-value routing.
 */
import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { z } from 'zod'

const number = z.number().finite()
const tree = z.object({
  f: z.array(z.number().int().min(-1)), t: z.array(number), l: z.array(z.number().int().min(-1)), r: z.array(z.number().int().min(-1)),
  m: z.array(z.union([z.literal(0), z.literal(1)])), v: z.array(z.array(number.min(0))),
}).strict()
const schema = z.object({
  formatVersion: z.literal('movement-model-v1'), version: z.string().min(1).max(120), kind: z.literal('forest'),
  contract: z.literal('movementRaw-v1'), featureContract: z.array(z.string().min(1)).length(21),
  classes: z.tuple([z.literal('light'), z.literal('still'), z.literal('vigorous')]), movingProbability: z.string(),
  strictThreshold: number, threshold: number, thresholdRule: z.string(), windowSeconds: z.literal(2), hopSeconds: z.number().positive(),
  sampleRateHz: z.literal(500), epochSeconds: z.literal(300), epochFeatures: z.array(z.string()), missingInputs: z.array(z.string()),
  float32Inputs: z.literal(true), hyperparameters: z.record(z.string(), z.unknown()), training: z.record(z.string(), z.unknown()),
  trainedAt: z.string(), source: z.string(), trees: z.array(tree).min(1).max(1000),
  testVectors: z.array(z.object({ inputs: z.record(z.string(), number.nullable()), probabilities: z.array(number).length(3), moving: z.boolean() }).strict()).min(1).max(64),
  sha256: z.string().regex(/^[a-f0-9]{64}$/),
}).strict()
type Model = z.infer<typeof schema>

/** finite-binary64-json-v1, as core src/biometrics/algorithms/canonical-json.ts. */
function numberToken(value: number): string {
  if (value === 0) return '0'
  const view = new DataView(new ArrayBuffer(8))
  view.setFloat64(0, Math.abs(value))
  const bits = view.getBigUint64(0)
  const exponent = Number((bits >> 52n) & 2047n)
  let coefficient = bits & ((1n << 52n) - 1n)
  if (exponent !== 0) coefficient += 1n << 52n
  const power = (exponent || 1) - 1023 - 52
  let token: string
  if (power >= 0) token = (coefficient << BigInt(power)).toString()
  else {
    const places = -power
    const digits = (coefficient * 5n ** BigInt(places)).toString().padStart(places + 1, '0')
    token = `${digits.slice(0, -places)}.${digits.slice(-places)}`.replace(/0+$/, '').replace(/\.$/, '')
  }
  return value < 0 ? `-${token}` : token
}
function canonical(item: unknown): string {
  if (item === null) return 'null'
  if (typeof item === 'number') return numberToken(item)
  if (typeof item === 'boolean' || typeof item === 'string') return JSON.stringify(item)
  if (Array.isArray(item)) return `[${item.map(canonical).join(',')}]`
  const record = item as Record<string, unknown>
  return `{${Object.keys(record).sort().map(k => `${JSON.stringify(k)}:${canonical(record[k])}`).join(',')}}`
}

/** Class probabilities for one window; null inputs are missing. */
export function evaluateMovementModel(model: Model, inputs: Record<string, number | null>): number[] {
  const x = model.featureContract.map((c) => {
    const v = inputs[c]
    if (v === null && model.missingInputs.includes(c)) return Number.NaN
    if (v === undefined || v === null || !Number.isFinite(v)) throw new Error(`feature ${c} is missing`)
    return Math.fround(v)
  })
  const sum = model.classes.map(() => 0)
  for (const tree of model.trees) {
    let node = 0
    while (tree.f[node] >= 0) {
      const v = x[tree.f[node]]
      const left = Number.isNaN(v) ? tree.m[node] === 1 : v <= tree.t[node]
      node = left ? tree.l[node] : tree.r[node]
    }
    const votes = tree.v[node]
    const total = votes.reduce((a, b) => a + b, 0)
    for (let c = 0; c < sum.length; c++) sum[c] += total > 0 ? votes[c] / total : 1 / sum.length
  }
  return sum.map(s => s / model.trees.length)
}

const path = process.argv[2]
const raw = readFileSync(path, 'utf8')
const parsed = JSON.parse(raw)
const model = schema.parse(parsed)
for (const [i, tree] of model.trees.entries()) {
  const n = tree.f.length
  if ([tree.t, tree.l, tree.r, tree.m, tree.v].some(a => a.length !== n)) throw new Error(`tree ${i}: arrays differ in length`)
  tree.f.forEach((f, j) => {
    if (f >= model.featureContract.length) throw new Error(`tree ${i}: split feature outside the contract`)
    if (f >= 0 && (tree.l[j] <= j || tree.r[j] <= j || tree.l[j] >= n || tree.r[j] >= n)) throw new Error(`tree ${i}: node ${j} does not point forward`)
    if (f < 0 && tree.v[j].length !== model.classes.length) throw new Error(`tree ${i}: leaf ${j} needs one vote per class`)
  })
}
const { sha256, ...body } = parsed
if (createHash('sha256').update(canonical(body)).digest('hex') !== sha256) throw new Error('SHA-256 does not match the canonical body')
const still = model.classes.indexOf('still')
const started = performance.now()
const failing = model.testVectors.flatMap((vector, i) => {
  const p = evaluateMovementModel(model, vector.inputs)
  const close = p.every((v, c) => Math.abs(v - vector.probabilities[c]) <= 1e-9)
  return close && (1 - p[still] > model.strictThreshold) === vector.moving ? [] : [i]
})
const msPerWindow = (performance.now() - started) / model.testVectors.length
if (failing.length) throw new Error(`Test vectors fail: ${failing.join(', ')}`)
console.log(JSON.stringify({ schemaValid: true, sha256, bytes: Buffer.byteLength(raw), trees: model.trees.length,
  nodes: model.trees.reduce((a, t) => a + t.f.length, 0), testVectors: model.testVectors.length, failing: 0, msPerWindow: Number(msPerWindow.toFixed(4)) }))
