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
  tasks: [
    'Sleep · Movement · Snore',
    'The workspace holds three kinds of research run. Sleep runs train and evaluate sleep-stage models. Movement runs compare a movement classifier with the pod’s own movement score. Snore runs record the piezo bandwidth gate and overnight snore-band energy. Each kind keeps its own views; switching task filters the run list.',
  ],
  cueLabels: [
    'Cue labels',
    'Movement sessions are scripted: the computer says each movement aloud and records when it asked. A window takes the class of a cued movement it half covers, starting one second after the cue, or is still when it is at least three seconds clear of every movement. Windows in between are transitions and are not scored. Cued movements are deliberate and are not the same as natural movements during sleep.',
  ],
  movingRecall: [
    'Moving recall',
    'Of the windows labeled as a movement, the share flagged as moving. Higher is better. It ignores which movement type was predicted.',
  ],
  stillFalseAlarm: [
    'False alarms while still',
    'Of the windows labeled still, the share flagged as moving. Lower is better. A detector that flags everything has perfect moving recall and a 100% false-alarm rate.',
  ],
  balancedAccuracy: [
    'Balanced accuracy',
    'The average of moving recall and the still rate (one minus false alarms). It treats moving and still windows equally, so a run with mostly still windows cannot score well by always answering still.',
  ],
  podScore: [
    'Pod movement score',
    'The pod’s existing movement measure: one value per minute from the capacitance channels, the sum of channel-to-channel changes after noise and pump gating. As the baseline it flags a window as moving when the minute containing it scores at or above a threshold chosen on the training folds. It cannot resolve movements shorter than a minute and cannot say what kind of movement happened.',
  ],
  bandEnergy: [
    'Snore-band energy',
    'Power of the raw 500 Hz piezo signal in the 16–30 Hz and 60–100 Hz bands, in dB of uncalibrated ADC units. Breathing, heartbeat, movement, voice and the pump all add energy here, so a rise is not a detected snore; only differences within one pod and setup are meaningful.',
  ],
  passBar: [
    'Bandwidth pass bar',
    'Fixed before any session: a band passes when humming or imitated snoring raises it at least 6 dB over lying still on the occupant’s channel. Every scored step needs 80% of its samples. Outcome both-bands, low-band-only, high-band-only, neither, or incomplete.',
  ],
  knocks: [
    'Knock check',
    'Each knock step needs at least three transients above 20 Hz on every channel. Knocks are for lining up the phone and pod clocks later; they do not change the band outcome. Body movement while knocking can add extra events on the occupant’s channel.',
  ],
  weeks: [
    'Weeks / weekly averages',
    'The Weeks view stacks each night’s stage minutes and groups nights into ISO weeks (Monday to Sunday) by the local noon-to-noon night date. Each night counts only intervals where every displayed source has a stage, so Watch, pod and model minutes share a denominator. Weekly averages use nights with shared data; nights without it are gaps, never zero. These are descriptive, retrospective summaries of already-inspected nights, not a trend test or a prediction for future weeks.',
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
let task = 'sleep',
  moveSession = null,
  doc = null,
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
const tabs = ['overview', 'nights', 'weeks', 'data', 'log'];
function showTab(tab, focus = false) {
  activeTab = tab;
  for (const name of tabs) {
    $(name).hidden = !isSleep() || name !== tab;
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
  renderTaskSwitch();
  const own = runs.filter((r) => runTask(r) === task),
    filtered = own.filter(
      (r) =>
        (r.name + ' ' + r.notes + ' ' + (r.candidate || ''))
          .toLowerCase()
          .includes(query) &&
        (filter === 'all' ||
          (filter === 'failed'
            ? ['failed', 'interrupted', 'cancelled'].includes(r.status)
            : r.status === filter)),
    );
  $('run-count').textContent = own.length;
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
      else if (r.summary) b.append(n('span', r.summary, 'run-score'));
      b.disabled = !r.available;
      b.onclick = () => selectRun(r.id);
      return b;
    }),
  );
  setOptions(
    $('run-select'),
    own.map((r) => [r.id, r.name + ' · ' + r.status]),
    runId,
  );
}
async function selectRun(id) {
  if (offline) return;
  runId = id;
  const picked = runs.find((r) => r.id === id);
  if (picked) task = runTask(picked);
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
  $('task-view').hidden = true;
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
    history.replaceState(null, '', '#task=' + task + '&run=' + id);
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
      const hash = new URLSearchParams(location.hash.slice(1)),
        wanted = hash.get('run') || embedded.initialRun,
        asked = ['sleep', 'movement', 'snore'].includes(hash.get('task'));
      if (asked) task = hash.get('task');
      const first =
        runs.find((r) => runTask(r) === task) || (!asked && runs[0]);
      if (wanted && runs.some((r) => r.id === wanted)) await selectRun(wanted);
      else if (first) await selectRun(first.id);
      else taskEmpty();
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
  document.body.classList.toggle('task-view', !isSleep());
  if (!isSleep()) {
    tabs.forEach((name) => ($(name).hidden = true));
    renderTaskHeader();
    renderTask();
    renderDownloads();
    return;
  }
  $('task-view').hidden = true;
  $('experiment-label').textContent = taskTitle.sleep;
  renderHeader();
  renderOverview();
  if (!document.activeElement?.closest('#overview-timeline, #nights-timeline'))
    renderTimelines();
  if (!document.activeElement?.closest('#weeks-view')) renderWeeks();
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
// Night groups are the exporter's local noon-to-noon dates (YYYY-MM-DD); weeks
// are ISO weeks of that date, computed on the calendar date alone.
function isNightDate(group) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(group ?? '')) return false;
  const [y, m, d] = group.split('-').map(Number),
    date = new Date(Date.UTC(y, m - 1, d));
  return (
    date.getUTCFullYear() === y &&
    date.getUTCMonth() === m - 1 &&
    date.getUTCDate() === d
  );
}
function isoWeek(group) {
  const [y, m, d] = group.split('-').map(Number),
    date = new Date(Date.UTC(y, m - 1, d)),
    weekday = (date.getUTCDay() + 6) % 7,
    thursday = new Date(date.getTime() + (3 - weekday) * 86400000),
    year = thursday.getUTCFullYear(),
    week = 1 + Math.floor((thursday - Date.UTC(year, 0, 1)) / 604800000),
    monday = new Date(date.getTime() - weekday * 86400000);
  return {
    key: year + '-W' + String(week).padStart(2, '0'),
    monday: monday.toISOString().slice(0, 10),
    weekday,
  };
}
// One entry per night group. Every source shares the same intervals (those
// where all displayed sources have a stage); a night without any shared
// interval is a gap, never zero minutes.
function weeklyNights(sessions, keys) {
  const groups = new Map();
  for (const session of sessions) {
    // Older saved runs can lack a night group; they cannot be placed in a week.
    if (!isNightDate(session.group)) continue;
    if (!groups.has(session.group)) groups.set(session.group, []);
    groups.get(session.group).push(...session.rows);
  }
  return [...groups.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([group, rows]) => {
      const common = sharedEpochs(rows, keys),
        minutes = Object.fromEntries(
          keys.map((key) => [
            key,
            stages.map((_, i) =>
              common
                .filter((r) => r[key] === i)
                .reduce((a, r) => a + (r.end - r.start) / 60, 0),
            ),
          ]),
        );
      return {
        group,
        week: isoWeek(group),
        epochs: common.length,
        gap: !common.length,
        minutes: common.length ? minutes : null,
      };
    });
}
// Weekly averages over nights with shared data only; Δ is source minus Watch.
function weeklySummary(nights, keys) {
  const weeks = new Map();
  for (const night of nights) {
    if (!weeks.has(night.week.key))
      weeks.set(night.week.key, { week: night.week, nights: [] });
    weeks.get(night.week.key).nights.push(night);
  }
  return [...weeks.values()].map(({ week, nights: all }) => {
    const data = all.filter((night) => !night.gap),
      average = Object.fromEntries(
        keys.map((key) => [
          key,
          data.length
            ? stages.map(
                (_, i) =>
                  data.reduce((a, night) => a + night.minutes[key][i], 0) /
                  data.length,
              )
            : null,
        ]),
      );
    return {
      week,
      nightsRecorded: all.length,
      nightsWithData: data.length,
      average,
      versusWatch: Object.fromEntries(
        keys
          .filter((key) => key !== 'watch')
          .map((key) => [
            key,
            average[key] && average.watch
              ? average[key].map((v, i) => v - average.watch[i])
              : null,
          ]),
      ),
    };
  });
}
let weekSource = 'watch';
function renderWeeks() {
  const host = $('weeks-view');
  host.replaceChildren();
  const sessions = doc.timelines?.sessions || [],
    p = panel('Weeks · stage minutes per night', 'weeks');
  host.append(p);
  if (!sessions.length) {
    p.append(
      empty(
        doc.report
          ? 'Sleep timelines were not recorded in this historical run.'
          : 'Weekly views appear after feature extraction.',
      ),
    );
    return;
  }
  const sources = [
      ['watch', 'Apple Watch · reference'],
      [
        'pod',
        doc.timelines.podSource === 'pod-api'
          ? 'Pod · captured'
          : 'Pod · rules replay',
      ],
      [mode, 'Candidate · ' + (mode === 'final' ? 'in-sample' : 'held out')],
      ['baseline', 'Baseline · held out'],
    ],
    rows = sessions.flatMap((s) => s.rows),
    available = sources.filter(([key]) => rows.some((r) => r[key] != null)),
    keys = available.map(([key]) => key),
    label = Object.fromEntries(available);
  if (!keys.includes('watch') || keys.length < 2) {
    p.append(empty('Waiting for paired stage predictions.'));
    return;
  }
  if (!keys.includes(weekSource)) weekSource = 'watch';
  const controls = n('div', null, 'timeline-controls'),
    sl = n('label', 'Show '),
    ss = n('select');
  ss.setAttribute('aria-label', 'Weekly chart source');
  setOptions(
    ss,
    available.map(([key, name]) => [key, name]),
    weekSource,
  );
  ss.onchange = () => {
    weekSource = ss.value;
    renderWeeks();
  };
  sl.append(ss);
  const ml = n('label', 'Predictions '),
    ms = n('select');
  ms.setAttribute('aria-label', 'Weekly prediction mode');
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
    renderWeeks();
    renderTimelines();
  };
  ml.append(ms, help('final'));
  controls.append(sl, ml);
  p.append(controls);
  if (mode === 'final')
    p.append(
      n(
        'p',
        'Final-model predictions are fitted on these nights. Baseline remains held out; this is not a like-for-like validation comparison.',
        'notice',
      ),
    );
  const legendRow = n('div', null, 'legend');
  order.forEach((i) => {
    const s = n('span'),
      dot = n('i');
    s.append(term('stages', stages[i]));
    dot.style.background = colors[i];
    s.prepend(dot);
    legendRow.append(s);
  });
  const gapKey = n('span', 'No shared data'),
    gapDot = n('i', null, 'week-gap-key');
  gapKey.prepend(gapDot);
  legendRow.append(gapKey);
  p.append(legendRow);
  const nights = weeklyNights(sessions, keys),
    summary = weeklySummary(nights, keys),
    byWeek = new Map(summary.map((w) => [w.week.key, []]));
  nights.forEach((night) => byWeek.get(night.week.key).push(night));
  const hours = Math.ceil(
      Math.max(
        60,
        ...nights
          .filter((night) => !night.gap)
          .map((night) => night.minutes[weekSource].reduce((a, b) => a + b, 0)),
      ) / 60,
    ),
    tickHours = hours > 8 ? 2 : 1,
    tallest = Math.ceil(hours / tickHours) * tickHours * 60,
    slot = 22,
    weekWidth = slot * 7 + 26,
    left = 44,
    top = 18,
    plot = 190,
    width = left + summary.length * weekWidth,
    svg = svgEl('svg', {
      viewBox: `0 0 ${width} ${top + plot + 48}`,
      width: Math.round(width * 1.6),
      height: Math.round((top + plot + 48) * 1.6),
      class: 'weeks-chart',
      role: 'img',
      'aria-label':
        'Stage minutes per night for ' +
        label[weekSource] +
        ', grouped by week',
    });
  for (let k = 0; k <= tallest / 60 / tickHours; k++) {
    const value = k * tickHours * 60,
      y = top + plot - (value / tallest) * plot;
    svg.append(
      svgEl('line', {
        x1: left - 4,
        x2: width,
        y1: y,
        y2: y,
        stroke: '#25271f',
        'stroke-width': 0.6,
      }),
      svgEl(
        'text',
        {
          x: left - 8,
          y: y + 3,
          fill: '#96988b',
          'font-size': 9,
          'text-anchor': 'end',
        },
        Math.round(value / 60) + 'h',
      ),
    );
  }
  summary.forEach((week, w) => {
    const x0 = left + w * weekWidth;
    svg.append(
      svgEl(
        'text',
        { x: x0, y: top + plot + 30, fill: '#e7e8df', 'font-size': 10 },
        week.week.key,
      ),
      svgEl(
        'text',
        { x: x0, y: top + plot + 42, fill: '#96988b', 'font-size': 9 },
        'from ' + week.week.monday,
      ),
    );
    const nightsByDay = new Map(
      byWeek.get(week.week.key).map((night) => [night.week.weekday, night]),
    );
    for (let day = 0; day < 7; day++) {
      const x = x0 + day * slot,
        night = nightsByDay.get(day);
      svg.append(
        svgEl(
          'text',
          {
            x: x + (slot - 4) / 2,
            y: top + plot + 14,
            fill: '#96988b',
            'font-size': 8,
            'text-anchor': 'middle',
          },
          'MTWTFSS'[day],
        ),
      );
      if (!night) continue;
      if (night.gap) {
        const r = svgEl('rect', {
          x,
          y: top + plot - 24,
          width: slot - 4,
          height: 24,
          fill: 'none',
          stroke: '#96988b',
          'stroke-dasharray': '3 2',
          rx: 2,
        });
        r.append(
          svgEl(
            'title',
            {},
            night.group + ': no interval shared by every displayed source',
          ),
        );
        svg.append(r);
        continue;
      }
      let y = top + plot;
      for (const i of [...order].reverse()) {
        const minutes = night.minutes[weekSource][i],
          h = (minutes / tallest) * plot;
        if (!h) continue;
        y -= h;
        const r = svgEl('rect', {
          x,
          y,
          width: slot - 4,
          height: Math.max(h - 1, 0.5),
          rx: 1,
          fill: colors[i],
        });
        r.append(
          svgEl(
            'title',
            {},
            `${night.group} · ${label[weekSource]}\n${order.map((s) => stages[s] + ' ' + night.minutes[weekSource][s].toFixed(0) + ' min').join(' · ')}\n${night.epochs} shared epochs`,
          ),
        );
        svg.append(r);
      }
    }
  });
  const scroll = n('div', null, 'timeline-scroll');
  scroll.append(svg);
  p.append(scroll);
  const columns = [
      'Week',
      'Nights with data',
      'Source',
      ...order.map((i) => stages[i] + ' (avg min)'),
    ],
    body = [];
  for (const week of summary)
    for (const [key, name] of available) {
      const avg = week.average[key];
      body.push([
        week.week.key,
        week.nightsWithData + ' of ' + week.nightsRecorded,
        name,
        ...order.map((i) => {
          if (!avg) return '—';
          const e = n('span', avg[i].toFixed(1)),
            d = week.versusWatch[key]?.[i];
          if (d != null)
            e.append(
              n(
                'small',
                ' (' + (d >= 0 ? '+' : '') + d.toFixed(1) + ')',
                Math.abs(d) > week.average.watch[i] * 0.15
                  ? 'architecture-warning'
                  : 'muted',
              ),
            );
          return e;
        }),
      ]);
    }
  const wrap = n('div', null, 'table-scroll'),
    t = n('table');
  t.setAttribute('aria-label', 'Weekly average stage minutes');
  table(t, columns, body);
  wrap.append(t);
  const exact = n('details'),
    nightsWrap = n('div', null, 'table-scroll'),
    nt = n('table');
  nt.setAttribute('aria-label', 'Stage minutes per night');
  table(
    nt,
    [
      'Night',
      'Week',
      'Shared epochs',
      ...available.map(([, name]) => name.split(' · ')[0] + ' W/R/L/D'),
    ],
    nights.map((night) => [
      night.group,
      night.week.key,
      night.gap ? 'None (gap)' : String(night.epochs),
      ...keys.map((key) =>
        night.gap
          ? '—'
          : order.map((i) => night.minutes[key][i].toFixed(0)).join(' / '),
      ),
    ]),
  );
  nightsWrap.append(nt);
  exact.append(n('summary', 'Minutes per night'), nightsWrap);
  p.append(
    wrap,
    exact,
    n(
      'p',
      `Nights are local noon-to-noon groups (${doc.timelines.timeZone}); weeks are ISO weeks starting Monday. Each night counts only intervals where every displayed source has a stage, so sources share a denominator. Averages use nights with shared data; days without a recording are blank and nights without shared data are dashed, never counted as zero. Δ is minutes versus Watch; amber marks a difference above 15% (a visual cue, not a release criterion).`,
      'footnote',
    ),
  );
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
// ---- Movement and snore research runs ----
const taskOf = {
    sleep: 'sleep',
    movement: 'movement',
    'snore-session': 'snore',
    'snore-night': 'snore',
  },
  taskTitle = {
    sleep: 'SLEEP STAGE EXPERIMENT',
    movement: 'MOVEMENT EXPERIMENT',
    snore: 'SNORE RESEARCH',
  },
  moveClasses = ['still', 'arm', 'leg', 'turn', 'sit'],
  moveColors = {
    still: '#55574c',
    arm: '#c3e25a',
    leg: '#6bc1e7',
    turn: '#bc9ad9',
    sit: '#d9b978',
  },
  channelColors = {
    left1: '#6bc1e7',
    right1: '#eb8376',
    left2: '#c3e25a',
    right2: '#d9b978',
  },
  snoreBands = ['60-100Hz', '16-30Hz'];
function runTask(run) {
  return taskOf[run?.kind || 'sleep'] || 'sleep';
}
function isSleep() {
  return !doc || runTask(doc) === 'sleep';
}
// Metric rows comparing the pod movement score with the classifier.
function movementRows(report) {
  const b = report.baseline || {},
    c = report.classifier || {},
    rows = [
      ['Moving recall', b.movingRecall, c.movingRecall, 'movingRecall'],
      [
        'False alarms while still',
        b.stillFalseAlarm,
        c.stillFalseAlarm,
        'stillFalseAlarm',
      ],
      [
        'Balanced accuracy',
        b.balancedAccuracy,
        c.balancedAccuracy,
        'balancedAccuracy',
      ],
    ];
  for (const name of moveClasses) {
    const base = b.flaggedMovingByClass?.[name],
      model = c.flaggedMovingByClass?.[name];
    if (base != null || model != null)
      rows.push(['Flagged as moving · ' + name, base, model, null]);
  }
  return rows;
}
// Contiguous segments of one value per session; a gap in window times breaks a segment.
function segments(windows, field, hop) {
  const out = [];
  for (const w of [...windows].sort((a, b) => a.t - b.t)) {
    const value = w[field];
    if (value == null) continue;
    const last = out[out.length - 1];
    if (last && last.value === value && w.t - last.end <= 1e-6)
      last.end = w.t + hop;
    else out.push({ start: w.t, end: w.t + hop, value });
  }
  return out;
}
// Rises over lying still for each scored band, with the pass bar applied.
function snoreRiseRows(report) {
  const bar = report.passBar?.minRiseDb ?? 6,
    rows = [];
  for (const [band, result] of Object.entries(report.bands || {}))
    for (const [key, rise] of Object.entries(result.risesDb || {})) {
      const [stimulus, channel] = key.split('/');
      rows.push({ band, stimulus, channel, rise, pass: rise >= bar });
    }
  return rows;
}
// One band's night series for a channel, split into runs at missing values or time gaps.
function nightRuns(windows, channel, band, step) {
  const runs = [];
  let current = null,
    previous = null;
  for (const w of [...windows].sort((a, b) => a.t - b.t)) {
    const v = w.bands?.[channel]?.[band];
    if (v == null || (previous != null && w.t - previous > step)) {
      if (current) runs.push(current);
      current = null;
    }
    if (v != null) {
      current = current || [];
      current.push({ t: w.t, v });
    }
    previous = w.t;
  }
  if (current) runs.push(current);
  return runs;
}
function extent(values, pad = 0) {
  const finite = values.filter((v) => v != null && Number.isFinite(v));
  if (!finite.length) return null;
  return [Math.min(...finite) - pad, Math.max(...finite) + pad];
}
function clock(seconds) {
  return new Date(seconds * 1000).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
  });
}
function renderTaskHeader() {
  const kind = doc.kind,
    r = doc.taskReport || {};
  $('experiment-label').textContent = taskTitle[runTask(doc)];
  $('run-title').textContent = doc.config?.name || doc.name || 'Saved run';
  $('status').textContent = 'complete';
  $('status').className = 'badge complete';
  const generated = r.generatedAt
      ? new Date(r.generatedAt).toLocaleString()
      : null,
    meta =
      kind === 'movement'
        ? [
            `${r.windows ?? '—'} windows`,
            `${(r.sessions || []).length} session${(r.sessions || []).length === 1 ? '' : 's'}`,
            r.validation,
          ]
        : kind === 'snore-session'
          ? [
              'Bandwidth gate session',
              'occupant ' + (r.occupantChannels || []).join(', '),
              (r.channels || []).length + ' channels',
            ]
          : [
              'Overnight snore-band energy',
              r.windowSeconds != null
                ? (r.windowSeconds / 3600).toFixed(1) + ' h'
                : null,
              (r.channels || []).join(', '),
            ];
  $('run-meta').textContent = [...meta, generated && 'generated ' + generated]
    .filter(Boolean)
    .join(' · ');
}
function renderTask() {
  const host = $('task-view');
  host.replaceChildren();
  host.hidden = false;
  if (!doc.taskReport) {
    host.append(empty('This run has no report.json yet.'));
    return;
  }
  if (doc.kind === 'movement') renderMovement(host);
  else if (doc.kind === 'snore-session') renderSnoreSession(host);
  else renderSnoreNight(host);
}
function rate(v) {
  return v == null ? '—' : pct(v);
}
function renderMovement(host) {
  const r = doc.taskReport,
    c = r.classifier || {},
    b = r.baseline || {};
  const cards = n('div', null, 'scorecards');
  cards.append(
    card(
      'Windows',
      String(r.windows ?? '—'),
      `${r.windowSeconds ?? 2} s, ${r.hopSeconds ?? 1} s hop`,
      'cueLabels',
    ),
    card(
      'Pod score',
      rate(b.balancedAccuracy),
      'balanced accuracy',
      'podScore',
    ),
    card(
      'Classifier',
      rate(c.balancedAccuracy),
      'balanced accuracy · macro F1 ' + fmt(c.macroF1, 2),
      'balancedAccuracy',
    ),
    card(
      'Validation',
      (r.sessions || []).length > 1 ? 'By session' : 'Within one',
      r.validation || '—',
    ),
  );
  const summary = panel('Pod movement score vs classifier', 'tasks');
  summary.append(cards);
  if ((r.sessions || []).length < 2)
    summary.append(
      n(
        'p',
        'Only one session: folds share a person, night and setup, so these numbers are optimistic. Add sessions to hold each one out.',
        'notice',
      ),
    );
  const metricTable = n('table');
  metricTable.setAttribute('aria-label', 'Pod score and classifier metrics');
  table(
    metricTable,
    ['Measure', 'Pod movement score', 'Classifier'],
    movementRows(r).map(([label, base, model, key]) => {
      const name = n('span', label);
      if (key) name.append(help(key));
      return [name, rate(base), rate(model)];
    }),
  );
  const wrap = n('div', null, 'table-scroll');
  wrap.append(metricTable);
  summary.append(
    wrap,
    n(
      'p',
      'The pod score is one value per minute, so it is scored on the minute around each 2 s window and cannot tell movement types apart. Its threshold is chosen on the training folds' +
        (b.thresholds?.length ? ' (' + b.thresholds.join(', ') + ').' : '.'),
      'footnote',
    ),
  );
  host.append(summary);
  const confusion = panel('Classifier · movement types', 'cueLabels'),
    classes = c.classes || [],
    ct = n('table');
  ct.setAttribute('aria-label', 'Classifier confusion matrix');
  table(
    ct,
    ['Labeled ↓ / predicted →', ...classes, 'Recall'],
    classes.map((name, i) => [
      name,
      ...(c.confusion?.[i] || []).map(String),
      rate(c.recall?.[name]),
    ]),
  );
  const cw = n('div', null, 'table-scroll');
  cw.append(ct);
  confusion.append(
    cw,
    n(
      'p',
      'Out-of-fold predictions: every window is scored by a model that did not train on its movement' +
        ((r.sessions || []).length > 1 ? ' or its session.' : '.'),
      'footnote',
    ),
  );
  host.append(confusion);
  host.append(movementTimeline(r));
}
function movementTimeline(r) {
  const p = panel('Session timeline', 'cueLabels'),
    windows = doc.windows || [];
  if (!windows.some((w) => w.predicted != null)) {
    p.append(
      empty(
        'This run did not save per-window predictions. Retrain to see the timeline.',
      ),
    );
    return p;
  }
  const sessions = [...new Set(windows.map((w) => w.session))];
  if (!sessions.includes(moveSession)) moveSession = sessions[0];
  const controls = n('div', null, 'timeline-controls'),
    label = n('label', 'Session '),
    select = n('select');
  select.setAttribute('aria-label', 'Movement session');
  setOptions(
    select,
    sessions.map((s) => [s, s]),
    moveSession,
  );
  select.onchange = () => {
    moveSession = select.value;
    renderTask();
  };
  label.append(select);
  controls.append(label);
  const legendRow = n('div', null, 'legend');
  for (const name of moveClasses) {
    const s = n('span', name),
      dot = n('i');
    dot.style.background = moveColors[name];
    s.prepend(dot);
    legendRow.append(s);
  }
  const movingKey = n('span', 'pod score: moving'),
    movingDot = n('i');
  movingDot.style.background = '#eb8376';
  movingKey.prepend(movingDot);
  legendRow.append(movingKey);
  p.append(controls, legendRow);
  const own = windows.filter((w) => w.session === moveSession),
    hop = r.hopSeconds || 1,
    start = Math.min(...own.map((w) => w.t)),
    end = Math.max(...own.map((w) => w.t)) + hop,
    perSecond = 3,
    left = 92,
    lane = 22,
    width = left + (end - start) * perSecond + 10,
    lanes = [
      ['Label', segments(own, 'label', hop), (v) => moveColors[v]],
      ['Classifier', segments(own, 'predicted', hop), (v) => moveColors[v]],
      [
        'Pod score',
        segments(
          own.map((w) => ({
            ...w,
            moving:
              w.baselineMoving == null
                ? null
                : w.baselineMoving
                  ? 'moving'
                  : 'still',
          })),
          'moving',
          hop,
        ),
        (v) => (v === 'moving' ? '#eb8376' : moveColors.still),
      ],
    ],
    height = 20 + lanes.length * (lane + 8) + 24,
    svg = svgEl('svg', {
      viewBox: `0 0 ${width} ${height}`,
      width,
      height,
      class: 'task-chart',
      role: 'img',
      'aria-label':
        'Labels, classifier predictions and pod-score flags over the session',
    });
  lanes.forEach(([name, segs, color], i) => {
    const y = 14 + i * (lane + 8);
    svg.append(
      svgEl(
        'text',
        { x: 0, y: y + 15, fill: '#96988b', 'font-size': 11 },
        name,
      ),
    );
    for (const s of segs) {
      const rect = svgEl('rect', {
        x: left + (s.start - start) * perSecond,
        y,
        width: Math.max((s.end - s.start) * perSecond - 0.5, 0.5),
        height: lane,
        fill: color(s.value),
        rx: 2,
      });
      rect.append(
        svgEl(
          'title',
          {},
          `${name}: ${s.value} · ${Math.round(s.start - start)}–${Math.round(s.end - start)} s`,
        ),
      );
      svg.append(rect);
    }
  });
  const axisY = 14 + lanes.length * (lane + 8) + 10;
  for (let t = 0; t <= end - start; t += 30)
    svg.append(
      svgEl(
        'text',
        {
          x: left + t * perSecond,
          y: axisY,
          fill: '#96988b',
          'font-size': 9,
          'text-anchor': 'middle',
        },
        t + ' s',
      ),
    );
  const scroll = n('div', null, 'timeline-scroll');
  scroll.append(svg);
  p.append(
    scroll,
    n(
      'p',
      'Blank stretches are transitions or windows with missing data; they are not scored and are never drawn as still.',
      'footnote',
    ),
  );
  return p;
}
function renderSnoreSession(host) {
  const r = doc.taskReport,
    p = panel('Piezo bandwidth gate', 'passBar'),
    badge = n(
      'span',
      r.outcome || '—',
      'badge ' +
        (r.outcome === 'both-bands'
          ? 'complete'
          : r.outcome === 'incomplete'
            ? 'running'
            : 'failed'),
    );
  p.querySelector('.panel-heading').append(badge);
  const rises = n('table');
  rises.setAttribute('aria-label', 'Band rises over lying still');
  table(
    rises,
    [
      'Band',
      'Step',
      'Channel',
      'Rise over lying still',
      'Pass bar ' + (r.passBar?.minRiseDb ?? 6) + ' dB',
    ],
    snoreRiseRows(r).map((row) => [
      row.band,
      row.stimulus.replace('occupied_', ''),
      row.channel,
      (row.rise >= 0 ? '+' : '') + row.rise.toFixed(1) + ' dB',
      row.pass ? 'pass' : 'below',
    ]),
  );
  const rw = n('div', null, 'table-scroll');
  rw.append(rises);
  p.append(
    rw,
    n(
      'p',
      'Energy rises during deliberate humming and imitated snoring. This shows the sensor can carry these bands; it does not detect real snoring.',
      'footnote',
    ),
  );
  host.append(p);
  const knocks = panel('Knocks', 'knocks'),
    kt = n('table'),
    channels = r.channels || [];
  kt.setAttribute('aria-label', 'Knock events by step and channel');
  table(
    kt,
    ['Step', ...channels],
    Object.entries(r.knockEvents || {}).map(([step, counts]) => [
      step,
      ...channels.map((ch) =>
        counts?.[ch] == null ? '—' : String(counts[ch]),
      ),
    ]),
  );
  const kw = n('div', null, 'table-scroll');
  kw.append(kt);
  knocks.append(
    kw,
    n(
      'p',
      (r.knockPassed ? 'Knock check passed. ' : 'Knock check did not pass. ') +
        'Knocks gate the clock sync for later capture, not the band outcome.',
      'footnote',
    ),
  );
  host.append(knocks);
  const steps = panel('Steps', 'bandEnergy'),
    st = n('table'),
    bandsShown = ['16-30Hz', '60-100Hz', '1-10Hz'];
  st.setAttribute('aria-label', 'Band energy per step and channel');
  table(
    st,
    [
      'Step',
      'Pump max rpm',
      ...channels.flatMap((ch) => [
        ch + ' coverage',
        ...bandsShown.map((b) => ch + ' ' + b),
      ]),
    ],
    Object.entries(r.steps || {}).map(([name, step]) => [
      name,
      step.pump?.maxRpm == null ? '—' : String(step.pump.maxRpm),
      ...channels.flatMap((ch) => {
        const c = step.channels?.[ch] || {};
        return [
          c.coverage == null ? '—' : pct(c.coverage),
          ...bandsShown.map((b) =>
            c.bandDb?.[b] == null ? '—' : c.bandDb[b].toFixed(1),
          ),
        ];
      }),
    ]),
  );
  const sw = n('div', null, 'table-scroll');
  sw.append(st);
  steps.append(sw);
  for (const w of r.warnings || []) steps.append(n('p', w, 'notice'));
  host.append(steps);
}
function renderSnoreNight(host) {
  const r = doc.taskReport,
    windows = doc.windows || [],
    s = r.storage || {},
    p = panel('Overnight snore-band energy', 'bandEnergy'),
    cards = n('div', null, 'scorecards');
  cards.append(
    card(
      'Coverage',
      r.coverage == null ? '—' : pct(r.coverage),
      `${r.piezoSeconds ?? '—'} of ${r.windowSeconds ?? '—'} s`,
    ),
    card(
      'Channels',
      (r.channels || []).join(', ') || '—',
      (r.freq || []).join(', ') + ' Hz',
    ),
    card(
      'Archive gzip',
      s.archiveGzipRatio == null ? '—' : s.archiveGzipRatio.toFixed(2),
      s.archivedFiles + ' archived files',
    ),
    card(
      'Pod writes',
      s.exportRawBytes != null && r.windowSeconds
        ? (
            (s.exportRawBytes * (s.archiveGzipRatio ?? 1)) /
            1e6 /
            (r.windowSeconds / 3600)
          ).toFixed(1) + ' MB/h'
        : '—',
      'RAW stream after gzip, approximate',
    ),
  );
  p.append(
    cards,
    n(
      'p',
      'Vibration energy per ' +
        (r.windowSecondsEach || 30) +
        ' s window, not detected snoring: movement, voice, heartbeat and the pump also raise these bands. Times are shown in this browser’s time zone.',
      'notice',
    ),
  );
  for (const w of r.warnings || []) p.append(n('p', w, 'notice'));
  host.append(p);
  if (!windows.length) {
    host.append(
      empty(
        'This night was analyzed before per-window energy was saved. Rerun `night` to see the timeline.',
      ),
    );
    return;
  }
  const ref = doc.reference,
    step = (r.windowSecondsEach || 30) * 1.5,
    channels = r.channels || [],
    t0 = Math.min(...windows.map((w) => w.t)),
    t1 = Math.max(...windows.map((w) => w.t)) + (r.windowSecondsEach || 30),
    left = 48,
    right = 150,
    plot = 150,
    width = left + 900 + right,
    x = (t) => left + ((t - t0) / (t1 - t0)) * (width - left - right);
  for (const band of snoreBands) {
    const chart = panel(band.replace('Hz', ' Hz') + ' band', 'bandEnergy'),
      values = windows.flatMap((w) =>
        channels.map((ch) => w.bands?.[ch]?.[band]),
      ),
      refs = ref ? [ref.still?.[band], ref.snore?.[band]] : [],
      range = extent([...values, ...refs], 3);
    if (!range) {
      chart.append(empty('No energy recorded in this band.'));
      host.append(chart);
      continue;
    }
    const y = (v) =>
        12 + plot - ((v - range[0]) / (range[1] - range[0])) * plot,
      svg = svgEl('svg', {
        viewBox: `0 0 ${width} ${plot + 40}`,
        width,
        height: plot + 40,
        class: 'task-chart',
        role: 'img',
        'aria-label':
          band + ' band energy over the night for ' + channels.join(' and '),
      });
    for (let k = 0; k <= 4; k++) {
      const v = range[0] + ((range[1] - range[0]) * k) / 4;
      svg.append(
        svgEl('line', {
          x1: left,
          x2: width - right,
          y1: y(v),
          y2: y(v),
          stroke: '#25271f',
          'stroke-width': 0.6,
        }),
        svgEl(
          'text',
          {
            x: left - 6,
            y: y(v) + 3,
            fill: '#96988b',
            'font-size': 11,
            'text-anchor': 'end',
          },
          v.toFixed(0),
        ),
      );
    }
    for (let h = Math.ceil(t0 / 3600) * 3600; h <= t1; h += 3600)
      svg.append(
        svgEl(
          'text',
          {
            x: x(h),
            y: plot + 30,
            fill: '#96988b',
            'font-size': 11,
            'text-anchor': 'middle',
          },
          clock(h),
        ),
      );
    if (ref)
      for (const [key, name] of [
        ['still', 'lying still'],
        ['snore', 'imitated snore'],
      ]) {
        const v = ref[key]?.[band];
        if (v == null) continue;
        svg.append(
          svgEl('line', {
            x1: left,
            x2: width - right,
            y1: y(v),
            y2: y(v),
            stroke: '#96988b',
            'stroke-dasharray': '4 3',
          }),
          svgEl(
            'text',
            {
              x: width - right + 6,
              y: y(v) + 3,
              fill: '#96988b',
              'font-size': 11,
            },
            `scripted ${name} (${ref.channel})`,
          ),
        );
      }
    for (const ch of channels)
      for (const run of nightRuns(windows, ch, band, step))
        svg.append(
          svgEl('path', {
            d: run
              .map(
                (pt, i) =>
                  (i ? 'L' : 'M') +
                  x(pt.t).toFixed(1) +
                  ' ' +
                  y(pt.v).toFixed(1),
              )
              .join(' '),
            fill: 'none',
            stroke: channelColors[ch] || '#e7e8df',
            'stroke-width': 1.2,
            'stroke-linejoin': 'round',
          }),
        );
    for (const w of windows) {
      const hit = svgEl('rect', {
        x: x(w.t),
        y: 12,
        width: Math.max(x(w.t + (r.windowSecondsEach || 30)) - x(w.t), 1),
        height: plot,
        fill: 'transparent',
      });
      hit.append(
        svgEl(
          'title',
          {},
          clock(w.t) +
            ' · ' +
            channels
              .map(
                (ch) =>
                  ch +
                  ' ' +
                  (w.bands?.[ch]?.[band] == null
                    ? 'no data'
                    : w.bands[ch][band].toFixed(1) + ' dB'),
              )
              .join(' · ') +
            (w.pump == null ? '' : ' · pump ' + w.pump + ' rpm'),
        ),
      );
      svg.append(hit);
    }
    const legendRow = n('div', null, 'legend');
    for (const ch of channels) {
      const s2 = n('span', ch),
        dot = n('i');
      dot.style.background = channelColors[ch] || '#e7e8df';
      s2.prepend(dot);
      legendRow.append(s2);
    }
    const scroll = n('div', null, 'timeline-scroll');
    scroll.append(svg);
    chart.append(legendRow, scroll);
    if (ref)
      chart.append(
        n(
          'p',
          `Reference lines: the occupant channel’s lying-still and imitated-snore levels from bandwidth session “${ref.run}” on the same pod (${ref.outcome}).`,
          'footnote',
        ),
      );
    host.append(chart);
  }
  const pump = panel('Pump speed', 'bandEnergy'),
    maxRpm = Math.max(1, ...windows.map((w) => w.pump || 0)),
    psvg = svgEl('svg', {
      viewBox: `0 0 ${width} 60`,
      width,
      height: 60,
      class: 'task-chart',
      role: 'img',
      'aria-label': 'Pump speed over the night',
    });
  for (const w of windows) {
    if (w.pump == null) continue;
    const h = (w.pump / maxRpm) * 40,
      rect = svgEl('rect', {
        x: x(w.t),
        y: 44 - h,
        width: Math.max(x(w.t + (r.windowSecondsEach || 30)) - x(w.t), 1),
        height: h,
        fill: '#8a8b80',
      });
    rect.append(svgEl('title', {}, clock(w.t) + ' · pump ' + w.pump + ' rpm'));
    psvg.append(rect);
  }
  const pscroll = n('div', null, 'timeline-scroll');
  pscroll.append(psvg);
  pump.append(
    pscroll,
    n(
      'p',
      'Maximum pump speed of either side per window; blank means no pump reading.',
      'footnote',
    ),
  );
  host.append(pump);
  const hours = panel('Hourly band energy', 'bandEnergy'),
    ht = n('table');
  ht.setAttribute('aria-label', 'Hourly median band energy');
  table(
    ht,
    [
      'Hour',
      'Seconds',
      'Pump max rpm',
      ...channels.flatMap((ch) =>
        snoreBands.map((b) => ch + ' ' + b + ' median'),
      ),
    ],
    (r.hours || []).map((h) => [
      clock(h.startS),
      String(h.piezoSeconds),
      h.pump?.maxRpm == null ? '—' : String(h.pump.maxRpm),
      ...channels.flatMap((ch) =>
        snoreBands.map((b) => {
          const q = h.channels?.[ch]?.bandDbPercentiles?.[b];
          return q ? q[1].toFixed(1) : '—';
        }),
      ),
    ]),
  );
  const hw = n('div', null, 'table-scroll');
  hw.append(ht);
  hours.append(hw);
  host.append(hours);
}
function renderTaskSwitch() {
  document.querySelectorAll('[data-task]').forEach((b) => {
    b.setAttribute('aria-pressed', String(b.dataset.task === task));
    b.disabled = offline;
  });
}
function taskEmpty() {
  const host = $('task-view'),
    p = panel(
      task === 'movement'
        ? 'No movement runs yet'
        : task === 'snore'
          ? 'No snore runs yet'
          : 'No sleep runs yet',
      'tasks',
    ),
    command = {
      movement:
        'uv run --locked python scripts/movement.py session --side left --out data/movement/session-1.json',
      snore:
        'uv run --locked python scripts/piezo_bandwidth.py session --side left --out data/bandwidth/steps.json',
      sleep: 'scripts/quickstart.sh',
    }[task];
  host.replaceChildren(p);
  host.hidden = false;
  p.append(
    n(
      'p',
      'Start one from the terminal; it appears here when its report is written.',
    ),
    n('code', command),
  );
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
    if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) {
      e.preventDefault();
      showTab(
        tabs[
          e.key === 'Home'
            ? 0
            : e.key === 'End'
              ? tabs.length - 1
              : (i + (e.key === 'ArrowRight' ? 1 : tabs.length - 1)) %
                tabs.length
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
document.querySelectorAll('[data-task]').forEach((b) => {
  b.onclick = () => {
    if (task === b.dataset.task) return;
    task = b.dataset.task;
    renderRuns();
    const first = runs.find((r) => runTask(r) === task);
    if (first) selectRun(first.id);
    else {
      runId = null;
      doc = null;
      tabs.forEach((name) => ($(name).hidden = true));
      document.body.classList.toggle('task-view', task !== 'sleep');
      $('experiment-label').textContent = taskTitle[task];
      $('run-title').textContent = 'Training workspace';
      $('run-meta').textContent = 'No runs of this kind yet.';
      history.replaceState(null, '', '#task=' + task);
      taskEmpty();
    }
  };
});
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
  task = runTask(doc);
  renderTaskSwitch();
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
