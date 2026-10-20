# Pinned production source

`core/` contains byte-for-byte copies from the core revision recorded in
`core-source.json`, including its AGPL-3.0 license. These files provide the actual
production feature extraction, rules baseline, model schema/evaluator, and Python
oversampling/portable-export helpers. The pipeline does not use the helper's legacy
CSV feature extraction or its standalone CLI.

Do not edit these files independently. To update, copy the required files from a
reviewed core revision, update the commit and SHA-256 manifest, and run the full
pipeline checks. Update the source revision and research protocol in any new run.

```sh
uv run --locked python scripts/check-vendor.py
uv run --locked python scripts/check-vendor.py --core /path/to/core-checkout
```

The second command compares file bytes against that checkout; the first validates
this repository's pinned snapshot. Neither claims compatibility with an arbitrary
current pod. Before deploying, compare against the intended runtime revision.
