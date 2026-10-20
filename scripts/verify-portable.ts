import { readFileSync } from 'node:fs'
import { stageModelSchema, evaluateStageModel, failingTestVectors } from '../vendor/core/src/lib/personal-stage-model'
const dir = process.argv[2]
const model = stageModelSchema.parse(JSON.parse(readFileSync(`${dir}/candidate.json`, 'utf8')))
const data = JSON.parse(readFileSync(`${dir}/parity-inputs.json`, 'utf8')) as { features: string[], rows: number[][], expected: number[] }
if (!data.rows.length || data.rows.length !== data.expected.length || JSON.stringify(data.features) !== JSON.stringify(model.featureContract) || data.rows.some(row => row.length !== data.features.length || row.some(x => !Number.isFinite(x)))) throw new Error('Invalid or mismatched parity inputs')
const failures = failingTestVectors(model)
let mismatches = 0
for (const [i, row] of data.rows.entries()) {
  const values = Object.fromEntries(data.features.map((key, j) => [key, row[j]]))
  if (evaluateStageModel(model, values).classIndex !== data.expected[i]) mismatches++
}
if (failures.length || mismatches) throw new Error(`Parity failed: ${failures.length} vectors, ${mismatches} rows`)
console.log(JSON.stringify({ schemaValid: true, cardVectors: model.testVectors.length, predictionRows: data.rows.length, mismatches }))
