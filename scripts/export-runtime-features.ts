/** Export local training inputs using exactly the pod's preprocessing and bed windows.
 * Usage: tsx export-runtime-features.ts snapshot.db watch.json output.json [left|right] [timezone]
 * Output contains private biometrics. Keep it outside version control.
 */
import Database from 'better-sqlite3'
import { z } from 'zod'
import { dirname } from 'node:path'
import { createHash } from 'node:crypto'
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs'
import { filterModelOutliers, stageModelFeatures, POD_NATIVE_CONTRACT } from '../vendor/core/src/lib/personal-stage-model'
import { DEFAULT_STAGE_RULESET } from '../vendor/core/src/lib/sleep-stage-rules'
import { runStageClassifier } from '../vendor/core/src/lib/sleep-stages'

const [snapshot, watchPath, output, side = 'left', timeZone = 'America/Los_Angeles'] = process.argv.slice(2)
if (!snapshot || !watchPath || !output || !['left', 'right'].includes(side)) throw new Error('Expected snapshot.db watch.json output.json [left|right] [timezone]')
new Intl.DateTimeFormat('en', { timeZone }).format(new Date())
const db = new Database(snapshot, { readonly: true, fileMustExist: true })
const watch = z.object({ stages: z.array(z.tuple([z.number().int().nonnegative(), z.number().int().positive(), z.enum(['wake', 'light', 'deep', 'rem'])]).refine(([start, end]) => end > start)).min(1) }).parse(JSON.parse(readFileSync(watchPath, 'utf8')))
const stages = ['wake', 'light', 'deep', 'rem']
const reference = watch.stages.filter(r => stages.includes(r[2]))
const config = db.prepare('select payload from biometric_runtime_configs where side = ? order by generation desc limit 1').get(side) as { payload: string } | undefined
const ruleset = config ? JSON.parse(config.payload).resolved.sleep_stages.ruleset : DEFAULT_STAGE_RULESET
const quality = (db.prepare('select quality_score q from calibration_profiles where side=? and sensor_type=\'piezo\' limit 1').get(side) as { q: number } | undefined)?.q ?? 0
const records = db.prepare('select id, entered_bed_at s, left_bed_at e from sleep_records where side=? and left_bed_at-entered_bed_at between 10800 and 50400 order by entered_bed_at').all(side) as { id: number, s: number, e: number }[]
const nights = []
for (const record of records) {
  const labelsAt = (seconds: number) => [...new Set(reference.filter(([a, b]) => a <= seconds * 1000 && seconds * 1000 < b).map(r => stages.indexOf(r[2])))]
  const refs = Array.from({ length: Math.floor((record.e - record.s) / 300) }, (_, i) => labelsAt(record.s + i * 300 + 150))
  if (!refs.some(r => r.length === 1)) continue
  const vitals = (db.prepare('select timestamp,heart_rate,hrv,breathing_rate from vitals where side=? and timestamp>=? and timestamp<? order by timestamp').all(side, record.s, record.e) as { timestamp: number, heart_rate: number | null, hrv: number | null, breathing_rate: number | null }[])
    .map(r => ({ timestamp: new Date(r.timestamp * 1000), heartRate: r.heart_rate, hrv: r.hrv, breathingRate: r.breathing_rate }))
  const movement = (db.prepare('select timestamp,total_movement from movement where side=? and timestamp>=? and timestamp<? order by timestamp').all(side, record.s, record.e) as { timestamp: number, total_movement: number }[])
    .map(r => ({ timestamp: new Date(r.timestamp * 1000), totalMovement: r.total_movement }))
  const epochs = stageModelFeatures('podNative', new Date(record.s * 1000), new Date(record.e * 1000), filterModelOutliers(vitals, ruleset.outliers), movement)
  const rules = runStageClassifier(vitals, movement, quality, ruleset).epochs
  const rows = epochs.map((epoch) => {
    const mid = epoch.start + 150
    const reference = labelsAt(mid)
    const rule = rules.find(r => r.start <= mid * 1000 && mid * 1000 < r.start + r.duration)
    return { start: epoch.start, x: POD_NATIVE_CONTRACT.map(c => epoch.values[c]), y: reference.length === 1 ? reference[0] : null, rule: rule ? stages.indexOf(rule.stage) : null }
  })
  if (rows.filter(r => r.y !== null).length < 24) continue
  // Noon-to-noon grouping keeps fragmented sessions of one local night together.
  const group = new Date((record.s - 12 * 3600) * 1000).toLocaleDateString('en-CA', { timeZone })
  nights.push({ id: record.id, group, start: record.s, end: record.e, watchEpochs: refs.filter(r => r.length === 1).length, rows })
}
const hash = (path: string | URL) => createHash('sha256').update(readFileSync(path)).digest('hex')
mkdirSync(dirname(output), { recursive: true })
writeFileSync(output, JSON.stringify({ version: 1, side, timeZone, features: POD_NATIVE_CONTRACT, ruleset, sources: { coreCommit: JSON.parse(readFileSync(new URL('../vendor/core-source.json', import.meta.url), 'utf8')).commit, vendorManifest: hash(new URL('../vendor/core-source.json', import.meta.url)), snapshot: hash(snapshot), watch: hash(watchPath), featureCode: hash(new URL('../vendor/core/src/lib/personal-stage-model.ts', import.meta.url)) }, nights }), { flag: 'wx' })
db.close()
console.log(`${nights.length} sessions, ${new Set(nights.map(n => n.group)).size} night groups, ${nights.reduce((s, n) => s + n.rows.filter(r => r.y !== null).length, 0)} labeled epochs`)
