import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
const script = readFileSync(
  new URL('./ui/dashboard.js', import.meta.url),
  'utf8',
).split("document.addEventListener('click'")[0];
function element(tag) {
  return { tag, children: [], attrs: {}, dataset: {}, style: {}, append(...items) { this.children.push(...items); },
    prepend(...items) { this.children.unshift(...items); },
    replaceChildren(...items) { this.children = items; },
    setAttribute(k, v) { this.attrs[k] = v; }, textContent: '' };
}
const elements = {};
const context = vm.createContext({
  document: { createElement: element, createElementNS: (_, tag) => element(tag),
    createTextNode: (text) => ({ textContent: String(text), children: [] }),
    getElementById: (id) => (elements[id] ??= element('div')) },
  // Mock elements are plain objects; primitives become text nodes, as in the browser.
  Node: Object,
  location: { protocol: 'http:' },
  embedded: {},
});
vm.runInContext(
  script +
    ';globalThis.testAPI={metrics,sumCM,sharedEpochs,duration,isoWeek,weeklyNights,weeklySummary,tabs,runTask,movementRows,segments,snoreRiseRows,nightRuns,extent,taskOf,phoneTimeline,phoneOverlay,snoreLabelSummary,phoneWindowSummary,renderSnoreNight,lockedNight,folds,score,renderFolds,miniNights,jobRequest,jobParams,tailText,jobIndicator,jobElapsed,setDoc:value=>{doc=value}}',
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
  assert.ok(context.testAPI.snoreLabelSummary({ capture, exitTest: { result: 'inconclusive', reason: 'Missing window confidences' }, phoneCoverage: .5 }).includes('Phone coverage: 50.0%'));
  assert.equal(context.testAPI.snoreLabelSummary({ exitTest: { result: 'pass', auc: .8, interval95: [.6,.9] }, clock: {synchronized:true, uncertaintyMs:2} })[0], 'Milestone 2: pass');
});

test('aggregate exit reports omit capture-only status and missing-window warnings', () => {
  const report = {kind:'snore-labels', exitTest:{result:'pass', auc:.8, interval95:[.6,.9], perNight:[{night:'synthetic',bouts:5,auc:.8}]}};
  context.testAPI.setDoc({taskReport:report});
  const host = element('div');
  context.testAPI.renderSnoreNight(host);
  const allText = e => [e.textContent, ...e.children.map(allText)].join(' ');
  const text = allText(host);
  assert.match(text, /Milestone 2: pass/);
  assert.match(text, /synthetic: 5 bouts/);
  assert.doesNotMatch(text, /[Uu]nsynchronized|undefined|[Cc]overage|[Aa]rchive|Rerun|[Cc]hannels/);
});
test('band hit titles retain every overlapping phone state and confidence', () => {
  const capture = {startMs:0,endMs:10000,coverage:[{startMs:0,endMs:6000}], events:[{startMs:2000,endMs:4000,confidence:.8}]};
  const text = context.testAPI.phoneWindowSummary(context.testAPI.phoneTimeline(capture), 1000, 9000);
  assert.match(text, /Recording · no detection/);
  assert.match(text, /Phone snore prediction · confidence 0.8/);
  assert.match(text, /Not recording/);
  assert.equal(context.testAPI.phoneWindowSummary(context.testAPI.phoneTimeline(capture), 10000, 20000), '');
  const svg = element('svg');
  context.testAPI.phoneOverlay(svg,capture,t=>t,20,false);
  assert.ok(svg.children.every(r=>r.attrs['pointer-events']==='none'));
});

const allText = (e) => [e.textContent, ...(e.children || []).map(allText)].join(' ');
test('locked holdout follows the rule in holdout.py', () => {
  const source = readFileSync(new URL('./holdout.py', import.meta.url), 'utf8');
  assert.equal(source.match(/^FREEZE = '(.+)'$/m)[1], '2026-11-14');
  assert.equal(Number(source.match(/^EVERY = (\d+)$/m)[1]), 4);
  const { lockedNight } = context.testAPI;
  assert.deepEqual(
    ['2026-11-13', '2026-11-14', '2026-11-15', '2026-11-18', '2026-11-30', '2026-12-02', 'night-3', undefined].map(lockedNight),
    [false, true, false, true, true, false, false, false],
  );
});
test('weekly views treat a locked night as a gap, never as data', () => {
  const keys = ['watch', 'pod'];
  const nights = weeklyNights(
    [
      { group: '2026-11-13', rows: [row(0, 1, 1)] },
      { group: '2026-11-14', rows: [row(300, 2, 2)] },
    ],
    keys,
  );
  assert.deepEqual(plain(nights.map((n) => [n.group, n.locked ?? false, n.gap, n.minutes])), [
    ['2026-11-13', false, false, { watch: [0, 5, 0, 0], pod: [0, 5, 0, 0] }],
    ['2026-11-14', true, true, null],
  ]);
  const [week] = weeklySummary(nights, keys);
  assert.equal(week.nightsWithData, 1);
  assert.deepEqual(plain(week.average.watch), [0, 5, 0, 0]);
});
const lockedDoc = () => ({
  state: {},
  diagnostics: { sessions: [{ group: '2026-11-13', rows: 10, labeled: 8 }], lockedGroups: ['2026-11-14'] },
  report: {
    scores: { existing_depth8: { confusion: [[0, 0, 0, 0], [0, 9, 0, 0], [0, 0, 9, 0], [0, 0, 0, 0]] } },
    folds: [
      { group: '2026-11-13', selected: 'balanced_leaf5', seconds: 1,
        scores: { balanced_leaf5: { macroF1: 0.5, kappa: 0.4, epochs: 8 },
          existing_depth8: { macroF1: 0.25, confusion: [[0, 0, 0, 0], [0, 4, 0, 0], [0, 4, 0, 0], [0, 0, 0, 0]] } } },
      { group: '2026-11-14', selected: 'balanced_leaf5', seconds: 1,
        scores: { balanced_leaf5: { macroF1: 0.987, kappa: 0.876, epochs: 8 },
          existing_depth8: { macroF1: 0.123, confusion: [[0, 0, 0, 0], [0, 5, 0, 0], [0, 0, 5, 0], [0, 0, 0, 0]] } } },
    ],
  },
});
test('scores and fold views never show a locked night', () => {
  const api = context.testAPI;
  api.setDoc(lockedDoc());
  assert.deepEqual(plain(api.folds().map((f) => f.group)), ['2026-11-13']);
  // The recorded pool includes the locked night, so the score comes from development folds.
  assert.equal(api.score('existing_depth8').epochs, 8);
  api.renderFolds();
  const table = allText(elements['fold-table']),
    pills = allText(elements['fold-pills']);
  assert.match(table, /Locked holdout/);
  assert.match(pills, /locked holdout/);
  assert.doesNotMatch(table + pills, /0\.987|0\.876|0\.123|0\.864/);
  assert.match(table, /0\.500/);
});
test('night cards show a locked night without its hypnogram', () => {
  const api = context.testAPI;
  api.setDoc({ timelines: { timeZone: 'UTC', sessions: [
    { id: '7', group: '2026-11-14', start: 0, end: 600, heldOutModel: 'balanced_leaf5',
      rows: [row(0, 1, 1, 1, 1), row(300, 2, 2, 2, 2)] },
  ] } });
  const panel = api.miniNights(),
    find = (e, tag) => e.tag === tag || (e.children || []).some((c) => find(c, tag));
  assert.match(allText(panel), /Locked holdout/);
  assert.ok(!find(panel, 'svg'));
});
test('job requests carry the launch token, and only POSTs send a JSON body', () => {
  const { jobRequest } = context.testAPI;
  assert.deepEqual(plain(jobRequest('t0k')), { cache: 'no-store', headers: { 'X-Training-Token': 't0k' } });
  const post = plain(jobRequest('t0k', { action: 'train', params: {} }));
  assert.equal(post.method, 'POST');
  assert.equal(post.headers['Content-Type'], 'application/json');
  assert.equal(post.headers['X-Training-Token'], 't0k');
  assert.deepEqual(JSON.parse(post.body), { action: 'train', params: {} });
});
test('job form values become typed parameters; blanks fall back to server defaults', () => {
  const action = { fields: [
    { name: 'features', type: 'file' }, { name: 'out', type: 'out-dir' }, { name: 'workers', type: 'int' },
    { name: 'exports', type: 'exports' }, { name: 'notes', type: 'text' }, { name: 'bad', type: 'int' },
  ] };
  assert.deepEqual(plain(context.testAPI.jobParams(action, {
    features: ' data/features.json ', out: '  ', workers: '4', exports: '1=data/a.tar.gz\n\n 2=data/b.tar.gz ',
    notes: 'why', bad: '2.5',
  })), { features: 'data/features.json', workers: 4, exports: ['1=data/a.tar.gz', '2=data/b.tar.gz'], notes: 'why', bad: '2.5' });
});
test('log tail appends chunks and trims whole lines past the limit', () => {
  const { tailText } = context.testAPI;
  assert.equal(tailText('a\n', 'b\n'), 'a\nb\n');
  assert.equal(tailText('first line\nsecond\n', 'third\n', 12), 'third\n');
  assert.equal(tailText('', 'x'.repeat(20), 5), 'xxxxx');
});
test('job indicator names the running job, else the last status', () => {
  const { jobIndicator, jobElapsed } = context.testAPI;
  const jobs = [{ id: 'b', label: 'Fusion arms', status: 'running', startedAt: 100 }, { id: 'a', label: 'Train', status: 'complete' }];
  assert.equal(jobIndicator({ jobs, active: 'b' }), 'Running · Fusion arms');
  assert.equal(jobIndicator({ jobs: jobs.slice(1), active: null }), 'Last job complete');
  assert.equal(jobIndicator({ jobs: [], active: null }), '');
  assert.equal(jobElapsed(jobs[0], 190), '1m 30s');
  assert.equal(jobElapsed({ startedAt: 100, finishedAt: 105 }, 999), '5s');
  assert.equal(jobElapsed(jobs[1]), '—');
});
test('the workspace script never calls pod model or device routes', () => {
  const source = readFileSync(new URL('./ui/dashboard.js', import.meta.url), 'utf8');
  assert.doesNotMatch(source, /sleep-stages\/model|watch-upload|\/api\/device|method: '(PUT|DELETE|PATCH)'/);
  assert.deepEqual([...source.matchAll(/fetch\(\s*'([^']+)'/g)].map((m) => m[1]).filter((u) => !u.startsWith('/api/runs')),
    ['/api/jobs/', '/api/jobs', '/api/jobs', '/api/jobs/']);
});
