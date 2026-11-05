# Local input data

Keep inputs and outputs under `data/`, whose contents are ignored except for the
tracked `.gitkeep` placeholder. A clone creates the directory but includes no
personal exports, databases, per-epoch rows, or fitted models.

## Read directly from the pod

`train.py --pod <ip> --side left` acquires the dataset through GET requests on the
LAN API, without credentials. The default window is 30 days, bounded at fetch
start. Only completed 3–14 hour sessions are included; active sessions are skipped.
The pod must have imported Watch reference stages unless you provide a local
Watch export. All responses, request parameters, hashes, and current stage-view
provenance are retained in the ignored run directory.

The local `biometrics.db` is a minimal materialization for the training exporter,
not a full backup of the pod database. It contains the selected sessions, vitals,
movement, and captured rule/calibration settings. Multi-request acquisition is not
atomic; changes to old records during acquisition are not always detectable.

## Consistent pod backup

Copying an active SQLite database without its WAL can silently omit recent data.
Take a SQLite backup on the pod first. These commands create a uniquely named
snapshot, copy it locally, and remove only that temporary snapshot. Substitute
your pod address; SSH access must already be configured.

```sh
mkdir -p data
pod_host='YOUR_POD_IP'
remote_snapshot=$(ssh -p 8822 "root@$pod_host" python3 - <<'PY'
import sqlite3
import tempfile
import os
fd, path = tempfile.mkstemp(prefix='training-', suffix='.db', dir='/persistent/sleepypod-data')
os.close(fd)
try:
    with sqlite3.connect('file:/persistent/sleepypod-data/biometrics.db?mode=ro', uri=True) as src:
        with sqlite3.connect(path) as dst:
            src.backup(dst)
    print(path)
except Exception:
    os.unlink(path)
    raise
PY
)
# Inspect remote_snapshot before proceeding if SSH emitted unexpected output.
scp -P 8822 "root@$pod_host:$remote_snapshot" data/biometrics.db
# After a successful copy, remove the exact temporary file on the pod.
ssh -p 8822 "root@$pod_host" python3 - "$remote_snapshot" <<'PY'
import os, sys
p = sys.argv[1]
assert p.startswith('/persistent/sleepypod-data/training-') and p.endswith('.db')
os.unlink(p)
PY
```

Use a new local filename if you need to preserve an existing snapshot. The training
exporter opens the local copy read-only. It expects the current core table/column
layout; an incompatible schema fails rather than inventing missing data.

## Watch reference labels

On iPhone: Health → profile → Export All Health Data. Place the ZIP in `data/`.
The parser streams XML from the ZIP without extracting unrelated archive files.
Only Awake, Core, Deep, and REM sleep records matching the selected source are used.
InBed and unspecified asleep records are excluded. Duplicate intervals are removed;
conflicting overlapping stage labels are retained so the feature exporter can
exclude ambiguous epoch midpoints. Timestamps must include explicit offsets.

Alternatively supply JSON:

```json
{"stages": [[1700000000000, 1700000300000, "light"]]}
```

Numbers are Unix **milliseconds**, not seconds. Valid stage strings are `wake`,
`light`, `deep`, and `rem`. Match the participant and side yourself before training;
this pipeline does not infer identity from heart rate. Preserve consent and source
provenance outside Git when working with others' data.

Night grouping currently matches the historical exporter: subtract 12 elapsed
hours, then format the date in the supplied timezone. Sessions near DST transitions
and noon need review; this is not a new civil-calendar grouping implementation.

## Wearable measurements

Stage labels are the only wearable data that reach training. Everything else a
wearable records, such as HRV, breathing rate, blood oxygen, resting heart rate,
wrist temperature and breathing disturbances, is compared with the pod and never
becomes a model feature, because a pod model has to run on nights with no wearable.
Every one of these is optional: a device reports what it has, and a missing metric
stays missing.

`train.py` writes `reference-agreement.json` beside the model, and a problem there
cannot fail a run. To run it alone:

```sh
uv run --locked python scripts/reference_agreement.py --snapshot data/biometrics.db \
  --side left --export data/export.zip --out data/reference-agreement.json
```

Results are kept per metric, device and algorithm version, because the same metric
can differ between them. Pod HRV is a 5-minute variability index, not RMSSD or SDNN,
so HRV reports trend only. Metrics with no pod counterpart report coverage only.

`train.py --pod` also fetches the metrics the pod says it holds, into the snapshot's
`reference_samples` table. A pod on older software answers for breathing rate and
SDNN at most, and that is not an error.

### Adding a device

Implement `ReferenceDevice` in `scripts/reference_devices.py` for a new export format,
or convert to the normalized format and pass `--measurements`, with no code here:

```json
{
  "device": "Ring 4",
  "measurements": [
    { "metric": "hrv_rmssd", "value": 42, "unit": "ms", "startMs": 1767333600000, "endMs": 1767333900000, "algorithm": "2" }
  ]
}
```

Metrics and units follow `METRICS` in that file, which mirrors sleepypod-core. Blood
oxygen is a percentage from 0 to 100. `skin_temperature` is absolute, so a device that
reports only a deviation from baseline must not use it.

