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
  script + ';globalThis.testAPI={metrics,sumCM,sharedEpochs,duration}',
  context,
);
const { metrics, sumCM, sharedEpochs, duration } = context.testAPI;
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
