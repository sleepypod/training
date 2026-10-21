'use strict';
const $ = (id) => document.getElementById(id);
const stages = ['Wake', 'Light', 'Deep', 'REM'],
  order = [0, 3, 1, 2],
  levels = [0, 2, 3, 1],
  colors = ['#d9b978', '#77b1d8', '#6f79b7', '#bc9ad9'];
const names = {
  existing_depth8: 'Baseline · depth 8',
  balanced_leaf5: 'Balanced · leaf 5',
  balanced_leaf10: 'Balanced · leaf 10',
  balanced_logistic: 'Logistic regression',
  nested: 'Nested selection',
  rules: 'Rules (replay)',
  always_light: 'Always light',
};
const glossary = {
  stages: [
    'Wake, REM, Light & Deep',
    'These are the four stage labels compared in this experiment. Wake means awake; Light and Deep are non-REM sleep categories; REM means rapid eye movement sleep. Watch combines some clinical stage distinctions into its own categories. Our chart displays estimates, not a clinical sleep study.',
  ],
  training: [
    'How training works',
    'We turn pod readings into numbers describing each five-minute interval, pair them with available Watch sleep stages, and let models learn relationships. We test on whole nights left out of learning, compare with a fixed baseline, then fit a final candidate. Nothing here changes the model on your pod.',
  ],
  macroF1: [
    'Macro F1',
    'A score from 0 to 1; higher is better agreement with the reference across sleep stages. We calculate F1 for Wake, Light, Deep and REM, then average the four equally. This stops the most common stage, usually Light, from dominating the result. A stage with no correct predictions contributes zero under this evaluation convention. It is not a percentage of correct epochs.',
  ],
  f1: [
    'F1, precision & recall',
    'F1 balances precision (when we predict a stage, how often does Watch agree?) and recall (of all Watch intervals in that stage, how many did we find?). F1 = 2 × precision × recall ÷ (precision + recall). A value of 1 means perfect agreement. An absent reference stage has no measurable recall; a zero F1 alone does not prove poor performance on that missing stage.',
  ],
  kappa: [
    'κ / k / Cohen’s kappa',
    'The Greek letter κ is pronounced “kappa”; it is sometimes typed as k. It measures agreement after allowing for the agreement expected from each source’s overall stage frequencies. 1 is perfect agreement, 0 matches that chance expectation, and a negative score is worse. It can be undefined if there is no variation. It is different from k in k-fold cross-validation, where k means the number of splits.',
  ],
  accuracy: [
    'Agreement / accuracy / percentage points',
    'The fraction of labeled intervals where the prediction matches the Watch. It can look high when one stage is very common, so we also show macro F1. A rise from 60% to 65% is +5 percentage points (pp), not a 5% relative increase. Watch agreement is not clinical accuracy.',
  ],
  depth: [
    'Tree depth, leaves, trees & nodes',
    'A decision tree asks a sequence of questions about input features. Depth limits how many question levels it can use: depth 8 permits up to eight splits from root to leaf. A leaf is the final prediction region. Minimum leaf size 5 or 10 requires at least that many training rows per leaf, making rules less specific. A forest combines many trees; nodes count their questions and leaves. More complexity can memorize noise.',
  ],
  forest: [
    'Random forest & balanced models',
    'A random forest combines many decision trees trained with randomness. Our forests use 200 trees and one training worker (n_jobs=1). Balanced candidates give rare stages more weight within each tree’s sampled training set (balanced_subsample). The depth-8 baseline instead repeats minority-class examples through oversampling. These choices can change errors between stages.',
  ],
  logistic: [
    'Logistic regression & standardization',
    'A simpler model that combines weighted inputs to score each sleep stage. It learns weights, rather than trees. The pipeline standardizes each input using the training partition’s mean and spread, then uses class balancing and regularization to limit overly large weights. “Regression” here still produces stage classifications.',
  ],
  fold: [
    'Fold / cross-validation / night groups',
    'A fold is one round of training and testing. We leave one night group out, train on the other nights, and score the left-out night. Sessions from the same local noon-to-noon night stay together so a fragmented night cannot appear on both sides. This repeats for every night group. Adjacent intervals are related, so random interval splits would overstate performance.',
  ],
  heldout: [
    'Held-out / nested selection / inner & outer',
    'Held-out data is excluded from fitting. Outer folds estimate performance on a left-out night. Inner GroupKFold splits only the remaining training nights to choose the candidate, without using the outer night’s labels. The pooled candidate score evaluates this selection procedure, which may choose different models on different nights. These previously inspected nights are still retrospective evidence.',
  ],
  final: [
    'Final model / in-sample / overfitting',
    'After evaluation, we select and fit a candidate using all available nights. Its predictions on those same nights are in-sample: useful for inspection, but optimistic as an estimate of future performance. Overfitting means learning the quirks of the training data rather than patterns that carry to new nights. Keep held-out results separate from final-model views.',
  ],
  baseline: [
    'Baseline, candidate & rules replay',
    'The baseline is the fixed existing_depth8 training recipe used as a yardstick. The candidate is the recipe chosen inside each fold. Neither is necessarily the model currently active on the pod. “Pod captured” is the stage output returned by the pod API at acquisition time; “Rules replay” applies the pinned local rule engine retrospectively.',
  ],
  epoch: [
    'Epoch & hypnogram',
    'An epoch is a five-minute interval represented by one feature row. A hypnogram is a stepped sleep-stage chart over time, ordered Wake, REM, Light, Deep. Gaps stay empty when readings or labels are unavailable. Watch and pod outputs are sampled at interval midpoints, so these plots do not reproduce finer stage boundaries.',
  ],
  architecture: [
    'Sleep architecture / sleep cycles',
    'Sleep architecture summarizes time spent in each stage; a hypnogram shows their sequence through the night. These charts help inspect stage changes, but do not identify validated sleep-cycle boundaries or count cycles. For stage-duration comparisons we use only the same intervals available in every displayed source, including Watch. Missing intervals are excluded, never filled as Wake.',
  ],
  labels: [
    'Labels, reference & class balance',
    'A label is the sleep stage used as a reference answer. Here labels come from Apple Watch, which can also be wrong. Class balance describes how many labels belong to each stage. Unlabeled intervals remain as context for smoothing but are not used as training answers or scored. No Watch agreement metric proves clinical validity.',
  ],
  coverage: [
    'Coverage, missing values & time alignment',
    'Coverage is the fraction of feature intervals with an unambiguous Watch label. Missing labels and missing input features are different: a finite feature may contain the feature extractor’s documented fallback, so zero non-finite values does not mean every sensor reading was present. Timezones are validated and midpoint matching is deterministic; this does not prove that device clocks were synchronized.',
  ],
  feature: [
    'Features & feature matrix',
    'A feature is a numerical summary of sensor data or position within the night. Each row represents one interval; columns are features. The matrix is the rectangular collection of these values. Float64 uses eight bytes per number. Matrix size is only data storage, not the memory used by the whole training process.',
  ],
  distribution: [
    'Histogram, mean, standard deviation & skew',
    'A histogram groups values into ranges; taller bars mean more observations in that range. Mean is the average. Standard deviation (σ or std) describes spread. Skew describes asymmetry: positive means a longer high-value tail and negative a longer low-value tail. Constant features have undefined skew. These summaries describe inputs and do not select a model.',
  ],
  drift: [
    'Drift / KS statistic / p-value',
    'Drift means feature distributions differ between groups. We compare each night against the other nights using the two-sample Kolmogorov–Smirnov (KS) test. Its statistic ranges from 0 (similar distributions) to 1 (separated distributions). The p-value measures how surprising that difference would be under the test’s assumptions, not the probability the data is bad. Epochs are correlated and many tests are made, so unadjusted p-values are exploratory and never a release gate.',
  ],
  importance: [
    'Feature importance',
    'Importance describes how a fitted model uses its inputs; it does not prove causation. Forest impurity importance counts how much a feature improves splits in the final training fit. It can favor some inputs and split credit between correlated features. Held-out permutation importance instead measures the score change when an input is shuffled. The method is labeled explicitly; these quantities are not interchangeable. Older runs may not record either.',
  ],
  uncertainty: [
    'Bootstrap confidence interval (CI) & Δ',
    'Δ (delta) means candidate minus baseline. Our 95% bootstrap interval repeatedly resamples the per-night F1 differences and measures their mean. A range crossing zero means the direction of the average improvement is uncertain. This describes mean night differences, not the pooled macro F1 difference. Overlapping cross-validation training sets make this interval approximate; it is not a guarantee for future nights.',
  ],
  confusion: [
    'Confusion matrix / row normalization',
    'Rows are Watch stages, columns are predicted stages. Diagonal cells are matches; other cells show which stages get mixed up. Counts are paired labeled epochs. Each displayed percentage divides by its Watch row total, so a rare stage can be inspected without being hidden by common ones. An empty row has no evidence, not perfect agreement.',
  ],
  readiness: [
    'Readiness for review',
    'These checks summarize evidence to inspect, not permission to ship. Numeric promotion thresholds have not been agreed. We show improvement, uncertainty, nights won, per-stage changes, and data availability, but do not copy mock release gates from the design. A future untouched night, wake-stage evidence, parity, and pod resource measurements are still needed.',
  ],
  eta: [
    'ETA & fit throughput',
    'ETA means estimated time remaining. Here it extrapolates recent measured model-fit durations over remaining fits; it excludes data fetching, prediction, diagnostics and parity work. Different recipes have different costs, so it is approximate. Rows/s is training rows processed per wall-clock second, not the sensor sampling rate.',
  ],
  resources: [
    'CPU, RSS, KiB & MiB',
    'CPU usage for a completed fit is processor time divided by wall time, with 100% equal to one fully used core. Peak RSS is the trainer’s maximum resident memory recorded so far; it is not current memory or total system memory. KiB is 1,024 bytes and MiB is 1,048,576 bytes. Older runs may not contain these measurements.',
  ],
  parity: [
    'Parity / portable model',
    'Parity checks that an exported model makes the same predictions as the Python model when evaluated by the pod-compatible TypeScript code. It detects conversion or implementation mismatches; it does not prove the predictions are correct sleep stages. JSON is the portable text artifact; joblib is the Python-only fitted artifact.',
  ],
  config: [
    'Configuration / hyperparameters / random seed',
    'Hyperparameters are choices such as tree depth, leaf size and number of trees made before a fit. Configuration records the candidate recipes and evaluation protocol. A random seed makes algorithmic randomness reproducible; it does not eliminate uncertainty in the data. Library versions and input hashes help reproduce the experiment.',
  ],
  provenance: [
    'Provenance / hashes / API / JSON',
    'Provenance records where inputs and code came from. A SHA-256 hash is a fingerprint used to detect file changes, not proof that data is correct. API requests fetch data from the pod over your local network. JSON is a structured text format; JSONL stores one structured event on each line. Historical runs preserve the information actually recorded at the time.',
  ],
  retrospective: [
    'Retrospective / prospective / smoothing',
    'Retrospective means analyzing a completed night. Whole-night normalization and centered windows use context from later in that night, so this model is not a real-time sleep-stage detector. Prospective evaluation tests a frozen model on genuinely new nights. Smoothing adjusts isolated predictions and disallowed stage transitions; scoring and plotted predictions use the same order.',
  ],
  hrZ: [
    'hrZ · relative heart rate',
    'Heart rate for an epoch, expressed as a z-score relative to that night. A z-score says how far a value sits above or below the night’s mean in standard-deviation units. Zero is near the mean. Whole-night context makes this retrospective.',
  ],
  hrStdZ: [
    'hrStdZ · heart-rate variation',
    'The variation in heart rate within an epoch, expressed as a z-score across that night. This is different from beat-to-beat HRV.',
  ],
  motionZ: [
    'motionZ · relative movement',
    'The epoch’s transformed movement summary, expressed as a z-score across that night. It describes relative activity, not a calibrated distance traveled.',
  ],
  nightFraction: [
    'nightFraction · position in the night',
    'Elapsed time since entering bed divided by the full recorded time in bed. For example, 0.5 means halfway through the recorded session. It requires the completed session length.',
  ],
  hrvRel: [
    'hrvRel · relative HRV',
    'Mean heart-rate variability (HRV) in the epoch divided by the night’s median HRV. HRV describes variation in timing between heartbeats. A value near 1 is near the nightly reference. The extractor uses zero when no HRV samples are available.',
  ],
  hrv15Rel: [
    'hrv15Rel · surrounding HRV',
    'Mean HRV in a centered 15-minute window, relative to that night’s median HRV. The window includes context before and after the epoch midpoint; it is not purely a past-data measurement.',
  ],
  hr15StdRel: [
    'hr15StdRel · surrounding heart-rate spread',
    'Heart-rate standard deviation within a centered 15-minute window divided by the night’s overall heart-rate standard deviation. It measures local variability relative to the night.',
  ],
  br15Std: [
    'br15Std · breathing-rate spread',
    'Standard deviation of breathing rate in a centered 15-minute window, in the input breathing-rate units. Fewer than two readings use a zero fallback.',
  ],
  motion15: [
    'motion15 · surrounding movement',
    'Movement summed within a centered 15-minute window, then transformed with log(1 + movement). This compresses very large bursts so their numbers are less extreme.',
  ],
  minutesSinceBed: [
    'minutesSinceBed · elapsed time in bed',
    'Minutes from the recorded entry into bed to the current epoch. This can help a model learn patterns that vary across a night, but time in bed alone does not determine a sleep stage.',
  ],
};
let doc = null,
  runs = [],
  runId = null,
  activeTab = 'overview',
  nightId = null,
  nightPicked = false,
  mode = 'heldOut',
  against = 'watch',
  epoch = 0,
  allEvents = null,
  busy = false,
  lastPayload = '',
  lastRuns = '',
  requestVersion = 0;
const offline = location.protocol === 'file:' || !!embedded.state;
const n = (tag, text, cls) => {
  const e = document.createElement(tag);
  if (text != null) e.textContent = text;
  if (cls) e.className = cls;
  return e;
};
const fmt = (v, d = 3) =>
  v == null || !Number.isFinite(v) ? '—' : v.toFixed(d);
const delta = (v) =>
  v == null
    ? '—'
    : (v >= 0 ? '+' : '') +
      fmt(v, Math.abs(v) > 0 && Math.abs(v) < 0.0005 ? 5 : 3);
const pct = (v) => (v == null ? '—' : (v * 100).toFixed(1) + '%');
const duration = (s) =>
  s == null
    ? '—'
    : s < 1
      ? s.toFixed(3) + 's'
      : s < 60
        ? Math.round(s) + 's'
        : Math.floor(Math.round(s) / 60) + 'm ' + (Math.round(s) % 60) + 's';
const bytes = (v) =>
  v == null
    ? 'Not recorded'
    : v >= 1048576
      ? (v / 1048576).toFixed(1) + ' MiB'
      : (v / 1024).toFixed(1) + ' KiB';
const modelName = (k) => names[k] || k || 'Pending';
function help(key, label) {
  const b = n('button', label || '?', 'help');
  b.dataset.help = key;
  b.setAttribute('aria-label', 'Explain ' + (glossary[key]?.[0] || key));
  return b;
}
function term(key, label) {
  const b = n('button', label || key, 'inline-term');
  b.dataset.help = key;
  return b;
}
function heading(title, key) {
  const h = n('h2', title);
  if (key) h.append(help(key));
  return h;
}
function panel(title, key) {
  const p = n('div', null, 'panel'),
    h = n('div', null, 'panel-heading');
  h.append(heading(title, key));
  p.append(h);
  return p;
}
function empty(text) {
  return n('div', text, 'empty-note');
}
function cell(tag, value) {
  const e = n(tag);
  e.append(
    value instanceof Node ? value : document.createTextNode(value ?? '—'),
  );
  return e;
}
function table(id, headers, rows) {
  const t = typeof id === 'string' ? $(id) : id;
  t.replaceChildren();
  const head = n('thead'),
    tr = n('tr');
  headers.forEach((h) => tr.append(cell('th', h)));
  head.append(tr);
  t.append(head);
  const body = n('tbody');
  rows.forEach((row) => {
    const tr = n('tr');
    row.forEach((v) => tr.append(cell('td', v)));
    body.append(tr);
  });
  t.append(body);
}
function setOptions(select, options, previous = select.value) {
  select.replaceChildren(
    ...options.map(([value, label]) => new Option(label, value)),
  );
  if (options.some((o) => o[0] === previous)) select.value = previous;
}
function svgEl(tag, attrs = {}, text) {
  const e = document.createElementNS('http://www.w3.org/2000/svg', tag);
  Object.entries(attrs).forEach(([k, v]) => e.setAttribute(k, v));
  if (text != null) e.textContent = text;
  return e;
}
function time(s, date = false) {
  try {
    return new Intl.DateTimeFormat('en', {
      timeZone: doc?.timelines?.timeZone || doc?.diagnostics?.timeZone || 'UTC',
      ...(date ? { month: 'short', day: 'numeric' } : {}),
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    }).format(new Date(s * 1000));
  } catch {
    return 'Unknown time';
  }
}
function sumCM(cms) {
  const out = Array.from({ length: 4 }, () => [0, 0, 0, 0]);
  for (const cm of cms)
    for (let i = 0; i < 4; i++)
      for (let j = 0; j < 4; j++) out[i][j] += cm[i][j];
  return out;
}
function metrics(cm) {
  if (!cm) return null;
  const total = cm.flat().reduce((a, b) => a + b, 0);
  if (!total) return null;
  const rows = cm.map((r) => r.reduce((a, b) => a + b, 0)),
    cols = cm.map((_, i) => cm.reduce((a, r) => a + r[i], 0)),
    correct = cm.reduce((a, r, i) => a + r[i], 0),
    chance = rows.reduce((a, r, i) => a + r * cols[i], 0) / (total * total),
    f1 = cm.map((r, i) =>
      rows[i] + cols[i] ? (2 * r[i]) / (rows[i] + cols[i]) : 0,
    );
  return {
    epochs: total,
    macroF1: f1.reduce((a, b) => a + b, 0) / 4,
    kappa: chance === 1 ? null : (correct / total - chance) / (1 - chance),
    accuracy: correct / total,
    confusion: cm,
    f1,
    rows,
    cols,
  };
}
function folds() {
  return doc?.report?.folds || doc?.state?.folds || [];
}
function score(key) {
  const recorded = doc?.report?.scores?.[key];
  if (recorded) return { ...metrics(recorded.confusion), ...recorded };
  const fs = folds();
  if (!fs.length) return null;
  const cms = fs
    .map((f) => f.scores?.[key === 'nested' ? f.selected : key]?.confusion)
    .filter(Boolean);
  return cms.length ? metrics(sumCM(cms)) : null;
}
function diff(a, b, key) {
  return a?.[key] != null && b?.[key] != null ? a[key] - b[key] : null;
}
function showTab(tab, focus = false) {
  activeTab = tab;
  for (const name of ['overview', 'nights', 'data', 'log']) {
    $(name).hidden = name !== tab;
    const b = $('tab-' + name);
    b.setAttribute('aria-selected', String(name === tab));
    b.tabIndex = name === tab ? 0 : -1;
    if (focus && name === tab) b.focus();
  }
  if (tab === 'log') drawLog();
}
function showHelp(key) {
  const dialog = $('help-dialog');
  $('glossary-search').value = '';
  renderGlossary(key);
  if (!dialog.open) dialog.showModal();
}
function renderGlossary(key) {
  const root = $('help-content');
  root.replaceChildren();
  const query = $('glossary-search').value.toLowerCase();
  for (const [id, [title, text]] of Object.entries(glossary)) {
    if (key && id !== key) continue;
    if (query && !`${title} ${text}`.toLowerCase().includes(query)) continue;
    const e = n('section', null, 'term');
    e.append(n('h3', title), n('p', text));
    root.append(e);
  }
  if (!root.children.length) root.append(empty('No matching terms.'));
  if (key) {
    const b = n('button', 'See all explanations');
    b.onclick = () => renderGlossary();
    root.append(b);
  }
}
function renderRuns() {
  const query = $('run-search').value.toLowerCase(),
    filter = $('run-filter').value;
  const filtered = runs.filter(
    (r) =>
      (r.name + ' ' + r.notes + ' ' + (r.candidate || ''))
        .toLowerCase()
        .includes(query) &&
      (filter === 'all' ||
        (filter === 'failed'
          ? ['failed', 'interrupted', 'cancelled'].includes(r.status)
          : r.status === filter)),
  );
  $('run-count').textContent = runs.length;
  $('run-list').replaceChildren(
    ...filtered.map((r) => {
      const b = n('button', null, 'run ' + (r.id === runId ? 'selected' : ''));
      b.setAttribute('aria-current', r.id === runId ? 'true' : 'false');
      b.append(
        n('strong', r.name),
        n(
          'small',
          r.status +
            ' · ' +
            new Date(r.startedAt * 1000).toLocaleDateString() +
            ' · ' +
            r.id.slice(0, 6),
        ),
      );
      if (r.macroF1 != null)
        b.append(n('span', fmt(r.macroF1) + ' F1', 'run-score'));
      b.disabled = !r.available;
      b.onclick = () => selectRun(r.id);
      return b;
    }),
  );
  setOptions(
    $('run-select'),
    runs.map((r) => [r.id, r.name + ' · ' + r.status]),
    runId,
  );
}
async function selectRun(id) {
  if (offline) return;
  runId = id;
  requestVersion++;
  lastPayload = '';
  allEvents = null;
  nightId = null;
  nightPicked = false;
  epoch = 0;
  mode = 'heldOut';
  renderRuns();
  doc = null;
  $('run-title').textContent = 'Loading run…';
  $('connection').textContent = '';
  document
    .querySelectorAll('[role=tabpanel]')
    .forEach((e) => (e.hidden = true));
  try {
    const version = requestVersion,
      response = await fetch('/api/runs/' + id, { cache: 'no-store' });
    if (!response.ok) throw Error('Run unavailable');
    const text = await response.text();
    if (version !== requestVersion) return;
    doc = JSON.parse(text);
    lastPayload = text;
    render();
    showTab(activeTab);
    history.replaceState(null, '', '#run=' + id);
  } catch (e) {
    $('connection').textContent =
      'Could not load this run. Its folder may have moved.';
  }
}
async function poll() {
  if (offline || busy || document.hidden) return;
  busy = true;
  try {
    const r = await fetch('/api/runs', { cache: 'no-store' });
    if (!r.ok) throw Error();
    const text = await r.text();
    if (text !== lastRuns) {
      runs = JSON.parse(text);
      lastRuns = text;
      renderRuns();
    }
    $('empty').classList.toggle('hidden', runs.length > 0);
    if (!runId && runs.length) {
      await selectRun(
        new URLSearchParams(location.hash.slice(1)).get('run') ||
          embedded.initialRun ||
          runs[0].id,
      );
    } else if (runId && (!doc || doc.state?.status === 'running')) {
      const version = requestVersion,
        r = await fetch('/api/runs/' + runId, { cache: 'no-store' });
      if (!r.ok) throw Error();
      const text = await r.text();
      if (version === requestVersion && text !== lastPayload) {
        doc = JSON.parse(text);
        lastPayload = text;
        render();
        if (allEvents) loadLog();
      }
    }
    $('connection').textContent = '';
  } catch {
    $('connection').textContent =
      'Connection lost · showing last received results';
  } finally {
    busy = false;
  }
}
function render() {
  if (!doc) return;
  renderHeader();
  renderOverview();
  if (!document.activeElement?.closest('#overview-timeline, #nights-timeline'))
    renderTimelines();
  if (document.activeElement !== $('drift-night')) renderData();
  renderConfig();
  drawLog();
  renderDownloads();
}
function renderHeader() {
  const s = doc.state || {},
    d = doc.diagnostics || {},
    r = doc.report;
  $('run-title').textContent = doc.config?.name || doc.name || 'Saved run';
  $('status').textContent = s.status || 'Archived';
  $('status').className = 'badge ' + s.status;
  const count =
    r?.groups ||
    s.totalFolds ||
    new Set((d.sessions || []).map((n) => n.group)).size;
  $('run-meta').textContent = [
    `${count || '—'} night groups`,
    `${d.rows ?? s.dataset?.rows ?? '—'} epochs`,
    `${d.features ?? s.dataset?.features ?? '—'} features`,
    d.side || doc.config?.side,
    d.timeZone || doc.config?.timezone,
    s.startedAt
      ? 'started ' + new Date(s.startedAt * 1000).toLocaleString()
      : null,
  ]
    .filter(Boolean)
    .join(' · ');
  $('header-score').textContent = fmt(score('nested')?.macroF1);
  $('detail').textContent =
    s.detail || 'Historical artifacts · no active training process';
  $('elapsed').textContent = duration(s.elapsedSeconds);
  updateClock();
  const phases = ['Fetching', 'Features', 'Training', 'Parity', 'Complete'],
    map = { Preparing: 0, Labels: 1, Export: 3, Stopped: -1 },
    phaseName = s.phase === 'Stopped' ? s.stoppedFrom : s.phase,
    phase = map[phaseName] ?? phases.indexOf(phaseName);
  $('pipeline').replaceChildren(
    ...phases.map((name, i) => {
      const e = n(
        'div',
        name +
          (name === 'Training' && s.totalFits
            ? ' ' + (s.completedFits || 0) + '/' + s.totalFits + ' fits'
            : ''),
        'pipeline-step',
      );
      if (s.status === 'complete' || i < phase) e.classList.add('done');
      if (i === phase) e.classList.add('active');
      e.append(
        help(
          name === 'Parity'
            ? 'parity'
            : name === 'Features'
              ? 'feature'
              : name === 'Fetching'
                ? 'provenance'
                : name === 'Training'
                  ? 'training'
                  : 'readiness',
        ),
      );
      if (['failed', 'cancelled'].includes(s.status) && i === phase)
        e.classList.add('failed');
      return e;
    }),
  );
}
function updateClock() {
  if (!doc) return;
  const s = doc.state || {};
  const elapsed =
    !offline && s.status === 'running' && s.startedAt
      ? Date.now() / 1000 - s.startedAt
      : s.elapsedSeconds;
  $('elapsed').textContent = duration(elapsed);
  const fits = s.fitHistory || [];
  const mean = fits.length
    ? fits.reduce((a, f) => a + f.seconds, 0) / fits.length
    : null;
  $('eta').textContent =
    s.status === 'running' && s.phase === 'Training' && mean != null
      ? '~' + duration(Math.max(0, (s.totalFits - s.completedFits) * mean))
      : s.status === 'complete'
        ? 'Done'
        : '—';
}
function card(label, value, sub, key) {
  const e = n('div', null, 'scorecard'),
    l = n('div', label, 'label');
  if (key) l.append(help(key));
  e.append(l, n('strong', value), n('small', sub));
  return e;
}
function check(label, value, status, key) {
  const e = n('div', null, 'check');
  e.append(
    n(
      'span',
      status === 'good' ? '✓' : status === 'bad' ? '!' : '○',
      'check-icon ' + status,
    ),
  );
  const text = n('span', label);
  if (key) text.append(help(key));
  e.append(text, n('span', value, 'check-value'));
  return e;
}
function renderOverview() {
  const s = doc.state || {},
    r = doc.report,
    a = score('nested'),
    b = score('existing_depth8'),
    fs = folds(),
    complete = s.status === 'complete';
  const wins = fs.filter(
      (f) => f.scores[f.selected].macroF1 > f.scores.existing_depth8.macroF1,
    ).length,
    ci = r?.uncertainty?.nightBootstrap95Percent;
  const ds = doc.diagnostics;
  const stageDiff = a && b ? a.f1.map((v, i) => v - b.f1[i]) : [];
  const worst = stageDiff.length ? Math.min(...stageDiff) : null;
  $('verdict').textContent = complete
    ? 'Needs review'
    : ['failed', 'cancelled', 'interrupted'].includes(s.status)
      ? 'Incomplete'
      : 'Provisional';
  $('decision-summary').textContent =
    `${fs.length} of ${r?.groups || s.totalFolds || '—'} night groups evaluated. ${complete ? 'Training and parity finished. Future-night validation is still outstanding.' : 'Results remain partial until evaluation and parity finish.'}`;
  $('checks').replaceChildren(
    check(
      'Macro F1 vs baseline',
      a && b ? fmt(a.macroF1) + ' vs ' + fmt(b.macroF1) : 'Awaiting scores',
      a && b ? (a.macroF1 > b.macroF1 ? 'good' : 'bad') : 'pending',
      'macroF1',
    ),
    check(
      '95% interval of mean night improvement',
      ci ? `[${delta(ci[0])}, ${delta(ci[1])}]` : 'Available after evaluation',
      ci ? (ci[0] > 0 ? 'good' : 'pending') : 'pending',
      'uncertainty',
    ),
    check(
      'Nights with higher F1',
      `${wins} / ${fs.length} · no release threshold`,
      'pending',
      'fold',
    ),
    check(
      'Largest per-stage change',
      worst == null
        ? 'Awaiting scores'
        : stages[stageDiff.indexOf(worst)] + ' ' + delta(worst),
      worst != null && worst < 0 ? 'bad' : 'pending',
      'f1',
    ),
    check(
      'Input validity & label coverage',
      ds && !ds.error
        ? `${ds.nonfinite} non-finite · ${pct(ds.labeled / ds.rows)} labeled`
        : 'Not recorded',
      ds && !ds.error && !ds.nonfinite ? 'good' : 'pending',
      'coverage',
    ),
  );
  $('performance-note').textContent =
    (complete ? 'Complete' : 'Provisional') +
    ' · ' +
    fs.length +
    ' night groups';
  $('scorecards').replaceChildren(
    card(
      'Macro F1',
      fmt(a?.macroF1),
      'Baseline ' + fmt(b?.macroF1) + ' · Δ ' + delta(diff(a, b, 'macroF1')),
      'macroF1',
    ),
    card(
      'Cohen’s κ',
      fmt(a?.kappa),
      'Baseline ' + fmt(b?.kappa) + ' · Δ ' + delta(diff(a, b, 'kappa')),
      'kappa',
    ),
    card(
      'Agreement vs Watch',
      pct(a?.accuracy),
      'Baseline ' +
        pct(b?.accuracy) +
        (a && b
          ? ' · ' + (diff(a, b, 'accuracy') * 100).toFixed(1) + ' pp'
          : ''),
      'accuracy',
    ),
    card(
      'Nights won',
      wins + ' / ' + fs.length,
      'Strictly higher F1; ties are not wins',
      'fold',
    ),
  );
  renderFolds();
  renderMatrix();
  $('stage-scores').replaceChildren();
  if (a && b) {
    for (const i of order) {
      const row = n('div', null, 'stage-row');
      row.append(n('span', stages[i]));
      const tracks = n('div');
      for (const [v, cls] of [
        [a.f1[i], 'bar'],
        [b.f1[i], 'bar baseline-bar'],
      ]) {
        const t = n('div', null, 'track'),
          bar = n('div', null, cls);
        bar.style.width = v * 100 + '%';
        t.append(bar);
        tracks.append(t);
      }
      row.append(
        tracks,
        n(
          'span',
          fmt(a.f1[i], 2) +
            ' / ' +
            fmt(b.f1[i], 2) +
            '\n' +
            delta(a.f1[i] - b.f1[i]),
          a.f1[i] >= b.f1[i] ? 'positive' : 'negative',
        ),
      );
      $('stage-scores').append(row);
    }
  } else
    $('stage-scores').append(
      empty('Stage scores appear after the first complete fold.'),
    );
  $('importance').replaceChildren();
  const imp = r?.featureImportance;
  if (imp?.values) {
    $('importance').append(n('p', imp.method, 'footnote'));
    const max = Math.max(...Object.values(imp.values).map(Math.abs), 0.001);
    for (const [key, v] of Object.entries(imp.values).sort(
      (a, b) => b[1] - a[1],
    )) {
      const row = n('div', null, 'barrow'),
        track = n('div', null, 'track'),
        bar = n('div', null, 'bar');
      bar.style.width = (Math.abs(v) / Math.max(max, Math.abs(v))) * 100 + '%';
      track.append(bar);
      row.append(
        term(key),
        track,
        n(
          'span',
          fmt(v) + (imp.std?.[key] != null ? ' ± ' + fmt(imp.std[key]) : ''),
        ),
      );
      if (v < 0) {
        row.classList.add('negative');
        bar.style.background = 'var(--red)';
      }
      $('importance').append(row);
    }
    $('importance').append(n('p', imp.note, 'footnote'));
  } else
    $('importance').append(
      empty(
        r
          ? 'Not recorded for this run or model type.'
          : 'Available after held-out sensitivity measurements finish.',
      ),
    );
  const fit = s.fitResult || {},
    d = s.dataset || {};
  $('health-state').textContent = s.status || 'Archived';
  $('health').replaceChildren(
    ...[
      ['Fits', `${s.completedFits ?? '—'} / ${s.totalFits ?? '—'}`],
      ['Last fit', duration(fit.seconds)],
      [
        'Fit rows / second',
        fit.rowsPerSecond == null
          ? '—'
          : Math.round(fit.rowsPerSecond).toLocaleString(),
      ],
      [
        'CPU / last fit',
        fit.cpuPercent == null
          ? 'Not recorded'
          : Math.round(fit.cpuPercent) + '%',
      ],
      ['Peak RSS', bytes(fit.peakRssBytes)],
      ['Feature matrix', bytes(d.matrixBytes ?? ds?.matrixBytes)],
    ].map(([label, value]) => {
      const e = n('div');
      const title = n('small', label);
      title.append(
        help(
          label === 'Fits'
            ? 'fold'
            : label.includes('rows')
              ? 'eta'
              : label === 'Last fit'
                ? 'eta'
                : 'resources',
        ),
      );
      e.append(title, n('strong', value));
      return e;
    }),
  );
  fitChart(s.fitHistory || []);
  renderComparisons();
}
function renderFolds() {
  const fs = folds(),
    sessions = doc.diagnostics?.sessions || [],
    groups = [...new Set(sessions.map((n) => n.group))].sort();
  if (!groups.length) groups.push(...fs.map((f) => f.group));
  $('fold-pills').replaceChildren(
    ...groups.map((g, i) => {
      const f = fs.find((f) => f.group === g),
        b = n(
          'button',
          'Night ' +
            (i + 1) +
            ' · ' +
            (f
              ? delta(
                  f.scores[f.selected].macroF1 -
                    f.scores.existing_depth8.macroF1,
                )
              : 'pending'),
        );
      b.onclick = () => openGroup(g);
      return b;
    }),
  );
  table(
    'fold-table',
    [
      term('fold', 'Fold'),
      'Night group',
      term('epoch', 'Epochs'),
      term('labels', 'Labeled'),
      term('heldout', 'Selected (inner)'),
      term('macroF1', 'F1'),
      'Base F1',
      'Δ',
      term('kappa', 'κ'),
      'Time',
      'Status',
    ],
    groups.map((g, i) => {
      const f = fs.find((f) => f.group === g),
        ns = sessions.filter((n) => n.group === g),
        a = f?.scores[f.selected],
        b = f?.scores.existing_depth8,
        link = n('button', g);
      link.onclick = () => openGroup(g);
      return [
        i + 1,
        link,
        ns.length ? ns.reduce((a, n) => a + n.rows, 0) : '—',
        a?.epochs ?? ns.reduce((a, n) => a + n.labeled, 0),
        f
          ? term(
              f.selected === 'balanced_logistic' ? 'logistic' : 'depth',
              modelName(f.selected),
            )
          : '—',
        fmt(a?.macroF1),
        fmt(b?.macroF1),
        delta(diff(a, b, 'macroF1')),
        fmt(a?.kappa),
        duration(f?.seconds),
        f
          ? 'evaluated'
          : doc.state?.status === 'running'
            ? 'pending'
            : 'not evaluated',
      ];
    }),
  );
}
function renderMatrix() {
  const s = score($('matrix-model').value);
  if (!s) {
    $('matrix').replaceChildren();
    return;
  }
  table(
    'matrix',
    ['Watch ↓ / Model →', ...order.map((i) => stages[i])],
    order.map((i) => [
      stages[i],
      ...order.map((j) => {
        const e = n(
          'div',
          s.rows[i] ? pct(s.confusion[i][j] / s.rows[i]) : '—',
        );
        e.append(n('small', s.confusion[i][j] + ' epochs'));
        e.style.background = `rgba(195,226,90,${s.rows[i] ? 0.08 + (0.35 * s.confusion[i][j]) / s.rows[i] : 0.03})`;
        e.style.padding = '8px';
        return e;
      }),
    ]),
  );
}
function renderComparisons() {
  const metric = $('comparison-metric').value;
  $('model-comparisons').replaceChildren();
  for (const [key, name] of Object.entries(names)) {
    const s = score(key);
    if (!s) continue;
    const row = n('div', null, 'barrow'),
      track = n('div', null, 'track'),
      bar = n('div', null, 'bar');
    bar.style.width = Math.max(0, s[metric] || 0) * 100 + '%';
    track.append(bar);
    const label = term(
      key === 'balanced_logistic'
        ? 'logistic'
        : key === 'nested'
          ? 'heldout'
          : 'baseline',
      name,
    );
    row.append(label, track, n('span', fmt(s[metric])));
    row.title = s.epochs + ' labeled epochs';
    $('model-comparisons').append(row);
  }
}
function fitChart(history) {
  const svg = $('fit-chart');
  svg.replaceChildren();
  if (!history.length) {
    svg.append(
      svgEl(
        'text',
        { x: 15, y: 70, fill: '#96988b', 'font-size': 12 },
        'No fit timing recorded yet',
      ),
    );
    return;
  }
  const max = Math.max(...history.map((f) => f.seconds), 0.001);
  [0, 0.5, 1].forEach((r) => {
    svg.append(
      svgEl('line', {
        x1: 40,
        x2: 490,
        y1: 115 - r * 95,
        y2: 115 - r * 95,
        stroke: '#2c2d26',
      }),
      svgEl(
        'text',
        { x: 0, y: 119 - r * 95, fill: '#96988b', 'font-size': 10 },
        (max * r).toFixed(1) + 's',
      ),
    );
  });
  svg.append(
    svgEl('polyline', {
      points: history
        .map(
          (f, i) =>
            `${40 + (i / Math.max(1, history.length - 1)) * 450},${115 - (f.seconds / max) * 95}`,
        )
        .join(' '),
      fill: 'none',
      stroke: '#c3e25a',
      'stroke-width': 2,
    }),
  );
}
function openGroup(group) {
  const night = doc.timelines?.sessions.find((n) => n.group === group);
  if (night) {
    nightId = night.id;
    nightPicked = true;
    epoch = 0;
    renderTimelines();
    showTab('nights');
    $('content').scrollIntoView({ block: 'start' });
  }
}
function legend() {
  const e = n('div', null, 'legend');
  order.forEach((i) => {
    const s = n('span'),
      dot = n('i');
    s.append(term('stages', stages[i]));
    dot.style.background = colors[i];
    s.prepend(dot);
    e.append(s);
  });
  const s = n('span', 'Disagreement'),
    dot = n('i');
  dot.style.background = '#d39862';
  s.prepend(dot);
  e.append(s);
  return e;
}
function selectedNight() {
  return doc.timelines?.sessions.find((n) => n.id === nightId);
}
function renderTimelines() {
  const sessions = doc.timelines?.sessions || [];
  const evaluated = sessions.filter((n) => n.heldOutModel);
  if (!nightPicked && evaluated.length)
    nightId = evaluated[evaluated.length - 1].id;
  else if (!sessions.some((n) => n.id === nightId)) nightId = sessions[0]?.id;
  for (const target of ['overview-timeline', 'nights-timeline']) {
    $(target).replaceChildren();
    if (!sessions.length) {
      const p = panel('The same night, side by side', 'epoch');
      p.append(
        empty(
          doc.report
            ? 'Sleep timelines were not recorded in this historical run.'
            : 'Sleep timelines appear after feature extraction.',
        ),
      );
      $(target).append(p);
      continue;
    }
    $(target).append(timelinePanel(target === 'overview-timeline'));
  }
  if (sessions.length) {
    $('overview-timeline').append(
      miniNights(),
      architecture(
        sessions.flatMap((n) => n.rows),
        'Sleep architecture · evaluated intervals',
        'heldOut',
      ),
    );
    $('nights-timeline').append(
      architecture(
        selectedNight().rows,
        'Sleep architecture · this night',
        mode,
      ),
    );
    const host = $('nights-timeline'),
      content = n('div');
    content.append(...Array.from(host.children));
    const nav = n('aside', null, 'night-nav');
    nav.setAttribute('aria-label', 'Sleep sessions');
    nav.append(n('h3', 'Nights'));
    for (const night of sessions) {
      const b = n(
        'button',
        time(night.start, true) + ' · ' + night.id,
        night.id === nightId ? 'selected' : '',
      );
      b.append(n('small', night.heldOutModel ? 'Evaluated' : 'Pending'));
      b.onclick = () => {
        nightId = night.id;
        nightPicked = true;
        epoch = 0;
        renderTimelines();
      };
      nav.append(b);
    }
    const layout = n('div', null, 'nights-layout');
    layout.append(content, nav);
    host.append(layout);
  }
}
function timelinePanel(overview) {
  const night = selectedNight(),
    p = panel('The same night, side by side', 'epoch'),
    ph = p.querySelector('.panel-heading');
  if (overview) {
    const open = n('button', 'Open in Nights →');
    open.onclick = () => showTab('nights');
    ph.append(open);
  }
  const controls = n('div', null, 'timeline-controls'),
    label = n('label', 'Sleep session '),
    select = n('select');
  select.setAttribute(
    'aria-label',
    overview ? 'Overview sleep session' : 'Sleep session',
  );
  setOptions(
    select,
    doc.timelines.sessions.map((n) => [
      n.id,
      time(n.start, true) + ' – ' + time(n.end) + ' · ' + n.id,
    ]),
    nightId,
  );
  select.onchange = () => {
    nightId = select.value;
    nightPicked = true;
    epoch = 0;
    renderTimelines();
  };
  label.append(select);
  controls.append(label);
  const ml = n('label', 'Predictions '),
    ms = n('select');
  ms.setAttribute(
    'aria-label',
    overview ? 'Overview prediction mode' : 'Prediction mode',
  );
  setOptions(
    ms,
    [
      ['heldOut', 'Held-out · validation'],
      ['final', 'Final fit · in-sample'],
    ],
    mode,
  );
  ms.onchange = () => {
    mode = ms.value;
    epoch = 0;
    renderTimelines();
  };
  ml.append(ms, help('final'));
  controls.append(ml);
  const dl = n('label', 'Disagreement '),
    ds = n('select');
  ds.setAttribute(
    'aria-label',
    overview ? 'Overview disagreement reference' : 'Disagreement reference',
  );
  setOptions(
    ds,
    [
      ['watch', 'Candidate vs Watch'],
      ['pod', 'Candidate vs Pod'],
    ],
    against,
  );
  ds.onchange = () => {
    against = ds.value;
    renderTimelines();
  };
  dl.append(ds);
  controls.append(dl);
  p.append(controls);
  const note =
    mode === 'final'
      ? 'Final-model predictions are fitted on these nights. Baseline remains held out; this is not a like-for-like validation comparison.'
      : night.heldOutModel
        ? 'Candidate: ' +
          modelName(night.heldOutModel) +
          ' · this night group was excluded from fitting and candidate selection.'
        : 'Not evaluated yet. Pod and Watch data remain visible.';
  p.append(n('p', note, 'notice'), legend());
  const scroll = n('div', null, 'timeline-scroll'),
    svg = svgEl('svg', {
      viewBox: '0 0 1000 540',
      class: 'hypnogram',
      role: 'img',
      'aria-label': 'Aligned Pod, candidate, baseline and Watch hypnograms',
    });
  scroll.append(svg);
  p.append(scroll);
  const lanes = [
    [
      'pod',
      doc.timelines.podSource === 'pod-api'
        ? 'Pod · captured'
        : 'Pod · rules replay',
    ],
    [mode, 'Candidate · ' + (mode === 'final' ? 'in-sample' : 'held out')],
    ['baseline', 'Baseline · held out'],
    ['watch', 'Apple Watch · reference'],
  ];
  drawHypnogram(svg, night, lanes, false);
  const stats = n('div', null, 'timeline-stats');
  for (const [key, label] of [
    [mode, 'Candidate vs Watch'],
    ['baseline', 'Baseline vs Watch'],
    ['pod', 'Pod vs Watch'],
  ]) {
    const rows = night.rows.filter((r) => r[key] != null && r.watch != null),
      e = n('div', label);
    e.append(
      n(
        'strong',
        rows.length
          ? pct(rows.filter((r) => r[key] === r.watch).length / rows.length)
          : 'Not recorded',
      ),
      n('span', rows.length + ' matched epochs', 'muted'),
    );
    stats.append(e);
  }
  const paired = night.rows.filter((r) => r[mode] != null && r.pod != null),
    dis = n('div', 'Pod vs candidate');
  dis.append(
    n(
      'strong',
      paired.filter((r) => r[mode] !== r.pod).length + ' / ' + paired.length,
    ),
    n('span', 'matched epochs disagree', 'muted'),
  );
  stats.append(dis);
  p.append(stats);
  const nav = n('div', null, 'controls'),
    prev = n('button', '← Previous disagreement'),
    next = n('button', 'Next disagreement →'),
    disagreements = night.rows
      .map((r, i) =>
        r[mode] != null && r[against] != null && r[mode] !== r[against]
          ? i
          : -1,
      )
      .filter((i) => i >= 0);
  prev.disabled = next.disabled = !disagreements.length;
  const step = (direction) => {
    const found =
      direction > 0
        ? disagreements.find((i) => i > epoch)
        : [...disagreements].reverse().find((i) => i < epoch);
    epoch =
      found ??
      (direction > 0
        ? disagreements[0]
        : disagreements[disagreements.length - 1]);
    renderTimelines();
  };
  prev.onclick = () => step(-1);
  next.onclick = () => step(1);
  nav.append(
    prev,
    n('span', disagreements.length + ' disagreements', 'muted'),
    next,
  );
  p.append(nav);
  const detail = n('div', null, 'epoch-detail');
  p.append(detail);
  const drawDetail = () => {
    const row = night.rows[epoch];
    detail.replaceChildren();
    if (!row) return;
    detail.append(n('strong', time(row.start) + ' – ' + time(row.end)));
    const labels = lanes.map(
      ([key, name]) =>
        name.split(' · ')[0] +
        ': ' +
        (row[key] == null ? 'Not recorded' : stages[row[key]]),
    );
    detail.append(n('div', labels.join(' · ')));
    if (row.x) {
      const inputs = n('div', null, 'inputs-grid');
      (doc.timelines.features || []).forEach((key, i) => {
        const e = n('span');
        e.append(term(key), document.createTextNode(' ' + fmt(row.x[i])));
        inputs.append(e);
      });
      detail.append(inputs);
    }
    svg.querySelectorAll('.selection-line').forEach((e) => e.remove());
    const x =
      172 + ((row.start - night.start) / (night.end - night.start)) * 810;
    svg.append(
      svgEl('line', {
        x1: x,
        x2: x,
        y1: 6,
        y2: 490,
        stroke: '#e7e8df',
        'stroke-width': 1,
        class: 'selection-line',
      }),
    );
  };
  drawDetail();
  svg.addEventListener('pointermove', (event) => {
    const bounds = svg.getBoundingClientRect(),
      x = ((event.clientX - bounds.left) / bounds.width) * 1000,
      seconds = night.start + ((x - 172) / 810) * (night.end - night.start);
    const index = night.rows.findIndex(
      (r) => r.start <= seconds && seconds < r.end,
    );
    if (index >= 0) {
      epoch = index;
      drawDetail();
    }
  });
  svg.addEventListener('click', () => drawDetail());
  const exact = n('details'),
    summary = n('summary', 'Exact aligned epochs'),
    tableWrap = n('div', null, 'table-scroll'),
    t = n('table');
  t.setAttribute('aria-label', 'Exact aligned epochs');
  table(
    t,
    ['Interval', 'Pod', 'Candidate', 'Baseline', 'Watch'],
    night.rows.map((r) => [
      time(r.start),
      ...['pod', mode, 'baseline', 'watch'].map((k) =>
        r[k] == null ? '—' : stages[r[k]],
      ),
    ]),
  );
  tableWrap.append(t);
  exact.append(summary, tableWrap);
  p.append(
    exact,
    n(
      'p',
      `Shared five-minute epochs · ${doc.timelines.timeZone}. Pod and Watch sampled at interval midpoints. Missing and ambiguous values stay empty; no interpolation across gaps. Disagreement does not establish which prediction is correct.`,
      'footnote',
    ),
  );
  return p;
}
function drawHypnogram(svg, night, lanes, mini) {
  const left = mini ? 46 : 172,
    width = mini ? 340 : 810,
    height = mini ? 48 : 116,
    top = mini ? 5 : 15,
    step = mini ? 9 : 20;
  lanes.forEach(([key, label], lane) => {
    const y = top + lane * height;
    svg.append(
      svgEl(
        'text',
        {
          x: mini ? 0 : 8,
          y: y + 12,
          fill: '#e7e8df',
          'font-size': mini ? 9 : 12,
        },
        mini ? label : label.split(' · ')[0],
      ),
    );
    if (!mini && label.includes(' · '))
      svg.append(
        svgEl(
          'text',
          { x: 8, y: y + 28, fill: '#96988b', 'font-size': 9 },
          label.split(' · ')[1],
        ),
      );
    if (!mini)
      order.forEach((stage, k) => {
        svg.append(
          svgEl(
            'text',
            {
              x: 158,
              y: y + 15 + k * step,
              fill: '#96988b',
              'font-size': 9,
              'text-anchor': 'end',
            },
            stages[stage],
          ),
          svgEl('line', {
            x1: left,
            x2: left + width,
            y1: y + 12 + k * step,
            y2: y + 12 + k * step,
            stroke: '#25271f',
            'stroke-width': 0.6,
          }),
        );
      });
    let previous = null;
    let has = false;
    for (const row of night.rows) {
      const value = row[key];
      if (value == null) {
        previous = null;
        continue;
      }
      has = true;
      const x =
          left +
          ((row.start - night.start) / (night.end - night.start)) * width,
        w = Math.max(
          0.6,
          ((row.end - row.start) / (night.end - night.start)) * width,
        ),
        sy = y + 12 + levels[value] * step;
      if (mini && key !== 'watch' && row.watch != null && row.watch !== value)
        svg.append(
          svgEl('rect', {
            x,
            y,
            width: w + 0.15,
            height: height - 4,
            fill: '#d39862',
            opacity: 0.16,
          }),
        );
      svg.append(
        svgEl('rect', {
          x,
          y: sy - 3,
          width: w + 0.15,
          height: mini ? 3 : 7,
          rx: 1,
          fill: colors[value],
        }),
      );
      if (previous && previous.end === row.start && previous.value !== value)
        svg.append(
          svgEl('line', {
            x1: x,
            x2: x,
            y1: previous.y,
            y2: sy,
            stroke: colors[value],
            opacity: 0.65,
            'stroke-width': 1,
          }),
        );
      previous = { end: row.end, value, y: sy };
    }
    if (!has)
      svg.append(
        svgEl(
          'text',
          {
            x: left + 12,
            y: y + 25,
            fill: '#96988b',
            'font-size': mini ? 9 : 12,
          },
          'Not evaluated / not recorded',
        ),
      );
  });
  if (!mini) {
    const y = 480;
    svg.append(
      svgEl(
        'text',
        { x: 8, y: y + 10, fill: '#96988b', 'font-size': 11 },
        'Δ vs ' + (against === 'watch' ? 'Watch' : 'Pod'),
      ),
    );
    for (const r of night.rows)
      if (r[mode] != null && r[against] != null && r[mode] !== r[against])
        svg.append(
          svgEl('rect', {
            x:
              left +
              ((r.start - night.start) / (night.end - night.start)) * width,
            y,
            width: Math.max(
              1,
              ((r.end - r.start) / (night.end - night.start)) * width,
            ),
            height: 10,
            fill: '#d39862',
          }),
        );
    for (let i = 0; i <= 6; i++)
      svg.append(
        svgEl(
          'text',
          {
            x: left + (i / 6) * width,
            y: 522,
            fill: '#96988b',
            'font-size': 10,
            'text-anchor': i === 0 ? 'start' : i === 6 ? 'end' : 'middle',
          },
          time(night.start + (i / 6) * (night.end - night.start)),
        ),
      );
  }
}
function miniNights() {
  const p = panel('Hypnograms · all nights', 'epoch');
  p.append(legend());
  const grid = n('div', null, 'mini-nights');
  for (const night of doc.timelines.sessions) {
    const b = n('button', null, 'mini-night');
    b.setAttribute('aria-label', 'Inspect night ' + night.id);
    b.append(
      n('strong', time(night.start, true) + ' · ' + night.id),
      n('span', night.heldOutModel ? 'Held out' : 'Not evaluated', 'muted'),
    );
    const svg = svgEl('svg', {
      viewBox: '0 0 390 156',
      role: 'img',
      'aria-label': 'Watch, candidate and baseline stage sequences',
    });
    drawHypnogram(
      svg,
      night,
      [
        ['watch', 'Watch'],
        ['heldOut', 'Cand'],
        ['baseline', 'Base'],
      ],
      true,
    );
    b.append(svg);
    b.onclick = () => {
      nightId = night.id;
      nightPicked = true;
      epoch = 0;
      renderTimelines();
      showTab('nights');
      $('content').scrollIntoView({ block: 'start' });
    };
    grid.append(b);
  }
  p.append(
    grid,
    n(
      'p',
      'Each line runs Wake, REM, Light, Deep from top to bottom. Orange bands mark disagreements with Watch. Select a night to inspect it.',
      'footnote',
    ),
  );
  return p;
}
function sharedEpochs(rows, keys) {
  return rows.filter((r) => keys.every((key) => r[key] != null));
}
function architecture(rows, title, prediction) {
  const p = panel(title, 'architecture');
  const sources = [
    ['watch', 'Apple Watch'],
    [
      'pod',
      doc.timelines.podSource === 'pod-api'
        ? 'Pod · captured'
        : 'Pod · rules replay',
    ],
    [
      prediction,
      'Candidate' + (prediction === 'final' ? ' · in-sample' : ' · held out'),
    ],
    ['baseline', 'Baseline · held out'],
  ];
  const available = sources.filter(([key]) => rows.some((r) => r[key] != null)),
    missing = sources.filter(([key]) => !rows.some((r) => r[key] != null));
  const common = sharedEpochs(
    rows,
    available.map(([key]) => key),
  );
  if (!common.length || available.length < 2) {
    p.append(empty('Waiting for paired stage predictions.'));
    return p;
  }
  const totals = available.map(([key, label]) => ({
    label,
    minutes: stages.map((_, i) =>
      common
        .filter((r) => r[key] === i)
        .reduce((a, r) => a + (r.end - r.start) / 60, 0),
    ),
  }));
  const total = totals[0].minutes.reduce((a, b) => a + b, 0);
  for (const source of totals) {
    const row = n('div', null, 'architecture-row');
    row.append(n('span', source.label));
    const bar = n('div', null, 'balance-bar');
    for (const i of order) {
      const part = n(
        'span',
        source.minutes[i] / total > 0.08
          ? Math.round((source.minutes[i] / total) * 100) + '%'
          : '',
      );
      part.style.width = (source.minutes[i] / total) * 100 + '%';
      part.style.background = colors[i];
      part.title = stages[i] + ': ' + source.minutes[i].toFixed(1) + ' min';
      bar.append(part);
    }
    row.append(bar, n('span', total.toFixed(0) + ' min', 'muted'));
    p.append(row);
  }
  const wrap = n('div', null, 'table-scroll'),
    t = n('table');
  t.setAttribute('aria-label', title + ' minutes');
  table(
    t,
    ['Minutes', ...order.map((i) => stages[i])],
    totals.map((s, index) => [
      s.label,
      ...order.map((i) => {
        const difference = s.minutes[i] - totals[0].minutes[i],
          e = n('span', s.minutes[i].toFixed(1));
        if (index) {
          e.append(
            n(
              'small',
              ' (' + (difference >= 0 ? '+' : '') + difference.toFixed(1) + ')',
              Math.abs(difference) > totals[0].minutes[i] * 0.15
                ? 'architecture-warning'
                : 'muted',
            ),
          );
        }
        return e;
      }),
    ]),
  );
  wrap.append(t);
  p.append(
    wrap,
    n(
      'p',
      `${common.length} common labeled epochs, ${total.toFixed(1)} minutes shared by every displayed source. ${missing.length ? 'Not recorded: ' + missing.map((s) => s[1]).join(', ') + '. ' : ''}Differences are minutes versus Watch; amber marks a relative difference above 15% (a visual cue, not a release criterion). These charts do not infer sleep-cycle boundaries.`,
      'footnote',
    ),
  );
  return p;
}
function renderData() {
  const d = doc.diagnostics,
    s = doc.state || {},
    transfer = s.transfer;
  $('intake').replaceChildren(
    card(
      'Downloaded',
      transfer ? bytes(transfer.bytes) : 'Not recorded',
      transfer
        ? transfer.requests + ' API requests'
        : 'Snapshot or historical run',
      'provenance',
    ),
    card(
      'Transfer average',
      transfer ? bytes(transfer.bytesPerSecond) + '/s' : '—',
      'Response bodies; includes repeated requests',
      'eta',
    ),
    card(
      'Feature epochs',
      String(d?.rows ?? s.dataset?.rows ?? '—'),
      'Five-minute rows after filtering',
      'epoch',
    ),
    card(
      'Watch coverage',
      d && !d.error ? pct(d.labeled / d.rows) : '—',
      'Unambiguous labels / feature epochs',
      'coverage',
    ),
  );
  $('data-checks').replaceChildren();
  if (!d || d.error) {
    $('data-checks').append(
      empty(d?.error || 'Feature diagnostics not recorded.'),
    );
    $('night-balance').replaceChildren();
    $('features').replaceChildren();
    return;
  }
  $('data-checks').append(
    check(
      'Finite input values',
      d.nonfinite + ' non-finite',
      d.nonfinite ? 'bad' : 'good',
      'coverage',
    ),
    check(
      'Watch coverage',
      `${d.labeled} / ${d.rows} epochs`,
      d.labeled < d.rows ? 'pending' : 'good',
      'labels',
    ),
    check(
      'Time alignment',
      d.timeZone
        ? 'Midpoint matching · ' + d.timeZone
        : 'Timezone not recorded',
      'pending',
      'coverage',
    ),
    check(
      'Feature time gaps',
      d.sessions.reduce((a, n) => a + n.gaps, 0) +
        ' gaps longer than five minutes',
      'pending',
      'epoch',
    ),
    check(
      'Distribution comparisons',
      'Exploratory · not a pass/fail test',
      'pending',
      'drift',
    ),
  );
  $('night-balance').replaceChildren(
    ...d.sessions.map((s) => {
      const row = n('div', null, 'night-balance-row'),
        button = n('button', s.id);
      button.onclick = () => openGroup(s.group);
      const bar = n('div', null, 'balance-bar');
      for (const i of order) {
        const count = s.classes[stages[i].toLowerCase()],
          part = n('span');
        part.style.width = (count / s.rows) * 100 + '%';
        part.style.background = colors[i];
        part.title = stages[i] + ': ' + count;
        bar.append(part);
      }
      row.append(button, bar, n('span', pct(s.labeled / s.rows)));
      return row;
    }),
  );
  $('balance-legend').replaceChildren(
    legend(),
    n('span', 'Empty remainder = no Watch label', 'footnote'),
  );
  setOptions(
    $('drift-night'),
    [...new Set(d.sessions.map((n) => n.group))].map((g) => [g, g]),
  );
  renderFeatures();
}
function renderFeatures() {
  const d = doc?.diagnostics;
  if (!d || d.error) return;
  const group = $('drift-night').value;
  table(
    'features',
    [
      term('feature', 'Feature'),
      term('distribution', 'Distribution'),
      'Min',
      'Mean',
      'Max',
      term('distribution', 'σ'),
      term('distribution', 'Skew'),
      'Non-finite',
      term('drift', 'KS / p'),
      term('importance', 'Importance'),
    ],
    d.columns.map((c) => {
      const svg = svgEl('svg', {
          viewBox: '0 0 120 35',
          class: 'histogram',
          role: 'img',
          'aria-label': c.name + ' histogram',
        }),
        counts = c.histogram.counts,
        max = Math.max(...counts, 1);
      counts.forEach((v, i) => {
        const bar = svgEl('rect', {
          x: i * 10,
          y: 33 - (v / max) * 30,
          width: 8,
          height: (v / max) * 30,
          fill: '#969f78',
        });
        bar.append(
          svgEl(
            'title',
            {},
            `${fmt(c.histogram.edges[i])} to ${fmt(c.histogram.edges[i + 1])}: ${v} epochs`,
          ),
        );
        svg.append(bar);
      });
      const drift = c.drift.find((v) => v.group === group);
      return [
        term(glossary[c.name] ? c.name : 'feature', c.name),
        svg,
        fmt(c.min, 2),
        fmt(c.mean, 2),
        fmt(c.max, 2),
        fmt(c.std, 2),
        fmt(c.skew, 2),
        c.missing,
        drift
          ? fmt(drift.statistic) +
            ' / ' +
            (drift.pValue == null
              ? '—'
              : drift.pValue < 0.001
                ? drift.pValue.toExponential(1)
                : fmt(drift.pValue))
          : '—',
        fmt(doc.report?.featureImportance?.values?.[c.name]),
      ];
    }),
  );
  $('drift-note').textContent = d.driftNote;
}
function renderConfig() {
  const r = doc.report,
    c = doc.config || {},
    p = doc.protocol || r?.protocol || {};
  const entries = [
    [
      'Candidates',
      (p.candidates || []).map(modelName).join(' · ') || 'Not recorded',
    ],
    ['Outer CV', p.outer || 'Not recorded'],
    ['Inner CV', p.inner || 'Not recorded'],
    ['Objective', p.objective || 'Not recorded'],
    ['Final candidate', r ? modelName(r.finalSelected) : 'Pending'],
    [
      'Environment',
      c.environment
        ? Object.entries(c.environment)
            .map(([k, v]) => k + ' ' + v)
            .join(' · ')
        : 'Not recorded',
    ],
    [
      'Data source',
      c.pod
        ? 'Pod HTTP · ' + c.side
        : c.snapshot
          ? 'SQLite snapshot · ' + c.side
          : 'Not recorded',
    ],
  ];
  $('config-summary').replaceChildren(
    ...entries.map(([k, v]) => {
      const row = n('div', null, 'config-row');
      row.append(n('span', k), n('span', v));
      return row;
    }),
  );
  $('notes').textContent =
    c.notes || 'No experiment notes were recorded for this run.';
  $('limitations').replaceChildren(
    ...(
      r?.limitations || [
        'Same-person retrospective evaluation. Watch labels are a reference; a future untouched night is required.',
      ]
    ).map((t) => n('p', t, 'footnote')),
  );
  $('configuration').textContent = JSON.stringify(
    { run: c, protocol: p, artifact: r?.artifact, uncertainty: r?.uncertainty },
    null,
    2,
  );
  $('retry-note').textContent = c.command
    ? 'Original command (choose a fresh --out directory when rerunning): ' +
      c.command
    : 'Original command was not recorded.';
  const failed = ['failed', 'cancelled', 'interrupted'].includes(
    doc.state?.status,
  );
  $('failure').classList.toggle('hidden', !failed);
  $('error').textContent =
    doc.error || doc.state?.detail || 'No traceback was recorded.';
}
function drawLog() {
  if (!doc) return;
  const s = doc.state || {},
    events = allEvents || s.events || [],
    filter = $('log-search').value.toLowerCase(),
    phase = $('log-phase').value;
  setOptions(
    $('log-phase'),
    [
      ['', 'All phases'],
      ...[...new Set(events.map((e) => e.phase))]
        .filter(Boolean)
        .map((p) => [p, p]),
    ],
    phase,
  );
  const selected = events.filter(
    (e) =>
      (!phase || e.phase === phase) &&
      JSON.stringify(e).toLowerCase().includes(filter),
  );
  const log = $('event-log'),
    scroll = log.scrollTop;
  log.replaceChildren(
    ...selected.map((e) => {
      const row = n(
        'div',
        null,
        'log-row ' + (e.status === 'failed' ? 'error' : ''),
      );
      row.append(
        n('span', duration(e.elapsedSeconds), 'log-time'),
        n('span', e.phase, 'log-phase'),
        n('span', e.detail, 'log-detail'),
      );
      return row;
    }),
  );
  log.scrollTop = $('follow').checked ? log.scrollHeight : scroll;
  $('log-note').textContent =
    `${selected.length} events shown. ${allEvents ? 'Full recorded history loaded.' : 'Recent 120 events; full history remains in events.jsonl.'}`;
  $('load-log').disabled = offline || !!allEvents;
}
async function loadLog() {
  if (!runId || offline) return;
  const id = runId;
  try {
    const response = await fetch('/api/runs/' + id + '/events');
    if (!response.ok) throw Error();
    const text = await response.text();
    if (id !== runId) return;
    allEvents = text
      .trim()
      .split('\n')
      .filter(Boolean)
      .flatMap((line) => {
        try {
          return [JSON.parse(line)];
        } catch {
          return [];
        }
      });
    drawLog();
  } catch {
    $('log-note').textContent = 'Full log is not available for this run.';
  }
}
function renderDownloads() {
  for (const [id, suffix, available] of [
    ['download-report', 'report', true],
    ['download-log', 'events', !!doc.state?.events],
    ['download-model', 'candidate', !!doc.report?.artifact],
  ]) {
    const a = $(id);
    a.classList.toggle('hidden', offline || !available);
    if (!offline) a.href = '/api/runs/' + runId + '/' + suffix;
  }
}
document.addEventListener('click', (e) => {
  const h = e.target.closest('[data-help]');
  if (h) showHelp(h.dataset.help);
  const t = e.target.closest('[data-open-tab]');
  if (t) showTab(t.dataset.openTab);
});
$('learn').onclick = () => showHelp();
$('close-help').onclick = () => $('help-dialog').close();
$('glossary-search').oninput = () => renderGlossary();
$('help-dialog').addEventListener('click', (e) => {
  if (e.target === $('help-dialog')) {
    const r = e.target.getBoundingClientRect();
    if (
      e.clientX < r.left ||
      e.clientX > r.right ||
      e.clientY < r.top ||
      e.clientY > r.bottom
    )
      e.target.close();
  }
});
document.querySelectorAll('[data-tab]').forEach((b, i) => {
  b.onclick = () => showTab(b.dataset.tab);
  b.onkeydown = (e) => {
    const list = ['overview', 'nights', 'data', 'log'];
    if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) {
      e.preventDefault();
      showTab(
        list[
          e.key === 'Home'
            ? 0
            : e.key === 'End'
              ? 3
              : (i + (e.key === 'ArrowRight' ? 1 : 3)) % 4
        ],
        true,
      );
    }
  };
});
$('matrix-model').onchange = renderMatrix;
$('comparison-metric').onchange = renderComparisons;
$('drift-night').onchange = renderFeatures;
$('log-search').oninput = drawLog;
$('log-phase').onchange = drawLog;
$('load-log').onclick = loadLog;
$('run-search').oninput = renderRuns;
$('run-filter').onchange = renderRuns;
$('run-select').onchange = (e) => selectRun(e.target.value);
$('layout').onchange = () => {
  document.body.className = 'layout-' + $('layout').value;
  try {
    localStorage.setItem('training-layout', $('layout').value);
  } catch {}
};
try {
  const layout = localStorage.getItem('training-layout');
  if (['sidebar', 'header', 'inspector'].includes(layout)) {
    $('layout').value = layout;
    document.body.className = 'layout-' + layout;
  }
} catch {}
if (offline) {
  doc = embedded;
  render();
  $('run-list').append(n('p', 'Offline report · this run only', 'footnote'));
  $('run-select').replaceChildren(
    new Option(doc.config?.name || doc.name || 'Saved run', ''),
  );
  $('run-search').disabled =
    $('run-filter').disabled =
    $('run-select').disabled =
      true;
  $('run-count').textContent = '1';
} else {
  poll();
  setInterval(poll, 2000);
}
setInterval(updateClock, 1000);
