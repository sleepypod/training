# Local input data

Keep inputs and outputs under `data/`, whose contents are ignored except for the
tracked `.gitkeep` placeholder. A clone creates the directory but includes no
personal exports, databases, per-epoch rows, or fitted models.

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
