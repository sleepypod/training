import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
const script = readFileSync(
  new URL('./ui/dashboard.js', import.meta.url),
  'utf8',
).split("document.addEventListener('click'")[0];
const context = vm.createContext({
  document: {},
  location: { protocol: 'http:' },
  embedded: {},
});
vm.runInContext(
  script +
    ';globalThis.testAPI={metrics,sumCM,sharedEpochs,duration,isoWeek,weeklyNights,weeklySummary,tabs}',
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
