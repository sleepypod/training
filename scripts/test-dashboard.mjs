import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
const script = readFileSync(
  new URL('./ui/dashboard.js', import.meta.url),
  'utf8',
).split("document.addEventListener('click'")[0];
function element(tag) {
  return { tag, children: [], attrs: {}, append(...items) { this.children.push(...items); },
    setAttribute(k, v) { this.attrs[k] = v; }, textContent: '' };
}
const context = vm.createContext({
  document: { createElement: element, createElementNS: (_, tag) => element(tag) },
  location: { protocol: 'http:' },
  embedded: {},
});
vm.runInContext(
  script +
    ';globalThis.testAPI={metrics,sumCM,sharedEpochs,duration,isoWeek,weeklyNights,weeklySummary,tabs,runTask,movementRows,segments,snoreRiseRows,nightRuns,extent,taskOf,phoneTimeline,phoneOverlay,snoreLabelSummary}',
  context,
);
const {
  metrics,
  sumCM,
  sharedEpochs,
  duration,
  isoWeek,
  weeklyNights,
  weeklySummary,
  tabs,
  runTask,
  movementRows,
  segments,
  snoreRiseRows,
  nightRuns,
  extent,
  taskOf,
} = context.testAPI;
test('pooled macro F1 weights stages equally, including absent stages', () => {
  const result = metrics([
    [0, 0, 0, 0],
    [0, 90, 0, 0],
    [0, 10, 0, 0],
    [0, 0, 0, 0],
  ]);
  assert.equal(result.accuracy, 0.9);
  assert.ok(Math.abs(result.macroF1 - 180 / 190 / 4) < 1e-12);
  assert.equal(result.epochs, 100);
});
test('single-class perfect agreement has undefined kappa', () => {
  assert.equal(
    metrics([
      [0, 0, 0, 0],
      [0, 10, 0, 0],
      [0, 0, 0, 0],
      [0, 0, 0, 0],
    ]).kappa,
    null,
  );
});
test('architecture uses identical matched intervals, preserving wake zero', () => {
  const rows = [
    { watch: 0, pod: 0, heldOut: 1, baseline: 0 },
    { watch: 1, pod: null, heldOut: 1, baseline: 1 },
    { watch: null, pod: 2, heldOut: 2, baseline: 2 },
    { watch: 3, pod: 3, heldOut: 3, baseline: null },
  ];
  const common = sharedEpochs(rows, ['watch', 'pod', 'heldOut', 'baseline']);
  assert.equal(common.length, 1);
  assert.equal(common[0], rows[0]);
  assert.equal(sharedEpochs(rows, ['watch', 'pod', 'heldOut']).length, 2);
});
test('pooled confusion sums counts rather than averaging percentages', () => {
  const result = metrics(
    sumCM([
      [
        [1, 0, 0, 0],
        [0, 0, 0, 0],
        [0, 0, 0, 0],
        [0, 0, 0, 0],
      ],
      [
        [0, 0, 0, 0],
        [9, 0, 0, 0],
        [0, 0, 0, 0],
        [0, 0, 0, 0],
      ],
    ]),
  );
  assert.equal(result.epochs, 10);
  assert.equal(result.accuracy, 0.1);
});
test('elapsed time carries rounded seconds into minutes', () => {
  assert.equal(duration(779.7), '13m 0s');
  assert.equal(duration(null), '—');
});
const plain = (v) => JSON.parse(JSON.stringify(v));
const row = (start, watch, pod, heldOut, baseline) => ({
  start,
  end: start + 300,
  watch,
  pod,
  heldOut,
  baseline,
});
test('ISO weeks follow the night date, including year boundaries', () => {
  assert.deepEqual(plain(isoWeek('2026-10-21')), {
    key: '2026-W43',
    monday: '2026-10-19',
    weekday: 2,
  });
  assert.equal(isoWeek('2026-10-25').key, '2026-W43');
  assert.equal(isoWeek('2026-10-26').key, '2026-W44');
  assert.equal(isoWeek('2026-12-31').key, '2026-W53');
  assert.equal(isoWeek('2027-01-03').key, '2026-W53');
  assert.equal(isoWeek('2027-01-04').key, '2027-W01');
  assert.equal(isoWeek('2024-12-30').key, '2025-W01');
  assert.equal(isoWeek('2026-01-01').monday, '2025-12-29');
});
test('weekly nights merge sessions of one night and share a denominator', () => {
  const keys = ['watch', 'pod', 'heldOut'];
  const nights = weeklyNights(
    [
      {
        group: '2026-10-21',
        rows: [row(0, 1, 1, 2, 1), row(300, 2, null, 2, 2)],
      },
      { group: '2026-10-21', rows: [row(900, 3, 3, 3, 3)] },
      { group: '2026-10-20', rows: [row(5000, 1, 2, 1, null)] },
      { rows: [row(9000, 1, 1, 1, 1)] },
      { group: 'night-3', rows: [row(9300, 1, 1, 1, 1)] },
      { group: '2024-02-31', rows: [row(9600, 1, 1, 1, 1)] },
      { group: '2026-13-01', rows: [row(9900, 1, 1, 1, 1)] },
    ],
    keys,
  );
  assert.deepEqual(plain(nights.map((night) => night.group)), [
    '2026-10-20',
    '2026-10-21',
  ]);
  const [first, second] = nights;
  assert.equal(first.epochs, 1);
  assert.deepEqual(plain(first.minutes.pod), [0, 0, 5, 0]);
  assert.equal(second.epochs, 2);
  assert.deepEqual(plain(second.minutes.watch), [0, 5, 0, 5]);
  assert.deepEqual(plain(second.minutes.heldOut), [0, 0, 5, 5]);
  assert.equal(second.week.key, '2026-W43');
});
test('a night with no shared interval is a gap, never zero minutes', () => {
  const [night] = weeklyNights(
    [
      {
        group: '2026-10-22',
        rows: [row(0, 1, null, 1, 1), row(300, null, 2, 2, 2)],
      },
    ],
    ['watch', 'pod'],
  );
  assert.equal(night.gap, true);
  assert.equal(night.epochs, 0);
  assert.equal(night.minutes, null);
});
test('weekly averages skip gaps and report differences versus Watch', () => {
  const keys = ['watch', 'heldOut'];
  const nights = weeklyNights(
    [
      { group: '2026-10-19', rows: [row(0, 1, null, 1), row(300, 2, null, 1)] },
      { group: '2026-10-20', rows: [row(0, 1, null, 3), row(300, 1, null, 3)] },
      { group: '2026-10-21', rows: [row(0, null, null, 1)] },
      { group: '2026-10-27', rows: [row(0, null, null, 2)] },
    ],
    keys,
  );
  const [w43, w44] = weeklySummary(nights, keys);
  assert.equal(w43.week.key, '2026-W43');
  assert.equal(w43.nightsRecorded, 3);
  assert.equal(w43.nightsWithData, 2);
  assert.deepEqual(plain(w43.average.watch), [0, 7.5, 2.5, 0]);
  assert.deepEqual(plain(w43.average.heldOut), [0, 5, 0, 5]);
  assert.deepEqual(plain(w43.versusWatch.heldOut), [0, -2.5, -2.5, 5]);
  assert.equal(w43.versusWatch.watch, undefined);
  assert.equal(w44.nightsWithData, 0);
  assert.equal(w44.average.watch, null);
  assert.equal(w44.versusWatch.heldOut, null);
});
test('tab order includes Weeks between Nights and Data', () => {
  assert.deepEqual(plain(tabs), ['overview', 'nights', 'weeks', 'data', 'log']);
});
test('runs map to sleep, movement or snore tasks; unknown kinds stay sleep', () => {
  assert.equal(runTask({}), 'sleep');
  assert.equal(runTask({ kind: 'movement' }), 'movement');
  assert.equal(runTask({ kind: 'snore-session' }), 'snore');
  assert.equal(runTask({ kind: 'snore-night' }), 'snore');
  assert.equal(runTask({ kind: 'mystery' }), 'sleep');
  assert.equal(runTask(null), 'sleep');
  assert.deepEqual(plain(Object.values(taskOf)), [
    'sleep',
    'movement',
    'snore',
    'snore',
    'snore',
  ]);
});
test('movement rows compare the pod score and classifier, including per-class rates', () => {
  const rows = movementRows({
    baseline: {
      movingRecall: 0.5,
      stillFalseAlarm: 0.2,
      balancedAccuracy: 0.65,
      flaggedMovingByClass: { still: 0.2, turn: 0.9 },
    },
    classifier: {
      movingRecall: 0.9,
      stillFalseAlarm: 0.05,
      balancedAccuracy: 0.925,
      flaggedMovingByClass: { still: 0.05, arm: 0.8, turn: 1 },
    },
  });
  assert.deepEqual(plain(rows.slice(0, 3).map((r) => r[3])), [
    'movingRecall',
    'stillFalseAlarm',
    'balancedAccuracy',
  ]);
  assert.deepEqual(plain(rows.slice(3).map((r) => r[0])), [
    'Flagged as moving · still',
    'Flagged as moving · arm',
    'Flagged as moving · turn',
  ]);
  assert.equal(rows[4][1], undefined);
  assert.equal(rows[4][2], 0.8);
  assert.equal(movementRows({}).length, 3);
});
test('segments merge contiguous equal windows and break at gaps and missing values', () => {
  const windows = [
    { t: 3, label: 'arm' },
    { t: 0, label: 'still' },
    { t: 1, label: 'still' },
    { t: 2, label: 'arm' },
    { t: 4, label: null },
    { t: 7, label: 'arm' },
  ];
  assert.deepEqual(plain(segments(windows, 'label', 1)), [
    { start: 0, end: 2, value: 'still' },
    { start: 2, end: 4, value: 'arm' },
    { start: 7, end: 8, value: 'arm' },
  ]);
  assert.deepEqual(plain(segments([], 'label', 1)), []);
});
test('snore rises apply the pass bar per band, step and channel', () => {
  const rows = snoreRiseRows({
    passBar: { minRiseDb: 6 },
    bands: {
      '16-30Hz': {
        risesDb: { 'occupied_hum/left1': 1.6, 'occupied_snore/left1': 9.6 },
      },
      '60-100Hz': { risesDb: { 'occupied_snore/left1': 6 } },
    },
  });
  assert.deepEqual(
    plain(rows.map((r) => [r.band, r.stimulus, r.channel, r.pass])),
    [
      ['16-30Hz', 'occupied_hum', 'left1', false],
      ['16-30Hz', 'occupied_snore', 'left1', true],
      ['60-100Hz', 'occupied_snore', 'left1', true],
    ],
  );
  assert.equal(
    snoreRiseRows({ bands: { x: { risesDb: { 'a/b': 5.9 } } } })[0].pass,
    false,
  );
  assert.deepEqual(plain(snoreRiseRows({})), []);
});
test('night runs split at missing energy and time gaps, never bridging them', () => {
  const w = (t, v) => ({ t, bands: { left1: { '60-100Hz': v } } });
  const runs = nightRuns(
    [w(60, 3), w(0, 1), w(30, 2), w(90, null), w(120, 4), w(300, 5), w(330, 6)],
    'left1',
    '60-100Hz',
    45,
  );
  assert.deepEqual(plain(runs.map((r) => r.map((p) => p.v))), [
    [1, 2, 3],
    [4],
    [5, 6],
  ]);
  assert.deepEqual(plain(nightRuns([w(0, 1)], 'right1', '60-100Hz', 45)), []);
});
test('extent ignores missing values and pads', () => {
  assert.deepEqual(plain(extent([null, 3, 1, NaN, 2], 1)), [0, 4]);
  assert.equal(extent([null, undefined]), null);
});

test('phone labels render coverage, no-detection and gaps separately on the energy timeline', () => {
  const capture = { startMs: 0, endMs: 10000, coverage: [{startMs: 0, endMs: 5000}],
    events: [{ startMs: 1000, endMs: 4000, confidence: .8 }], knocks: [500] };
  assert.equal(runTask({ kind: 'snore-labels' }), 'snore');
  assert.deepEqual(plain(context.testAPI.phoneTimeline(capture)).map(r => r.state),
    ['Recording · no detection', 'Phone snore prediction', 'Recording · no detection', 'Not recording']);
  const svg = element('svg');
  context.testAPI.phoneOverlay(svg, capture, t => t * 10, 150);
  assert.equal(svg.children.length, 5);
  assert.deepEqual(svg.children.filter(r => r.tag === 'rect').map(r => r.attrs.width), [10, 30, 10, 50]);
  assert.equal(svg.children[1].children[0].textContent, 'Phone snore prediction · confidence 0.8');
  assert.equal(svg.children[3].attrs['data-recording-state'], 'Not recording');
  assert.ok(context.testAPI.snoreLabelSummary({ exitTest: { result: 'inconclusive', reason: 'Missing window confidences' }, phoneCoverage: .5 }).includes('Phone coverage: 50.0%'));
  assert.equal(context.testAPI.snoreLabelSummary({ exitTest: { result: 'pass', auc: .8, interval95: [.6,.9] }, clock: {synchronized:true, uncertaintyMs:2} })[0], 'Milestone 2: pass');
});
