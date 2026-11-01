# Contributing

Digifly Workstation separates portable experiment definitions from machine-specific
scientific runtimes. New integrations should implement the adapter boundary and
run simulator imports in a worker process.

Before opening a change:

```bash
./scripts/setup_dev.sh
QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/bin/python -m pytest
```

Do not commit native Digifly datasets, generated caches, run outputs, virtual
environments, credentials, VND binaries, compiled mechanisms, or large figures.

## Bug and fix history

Keep [the bug and fix log](docs/BUG_FIX_LOG.md) up to date with debugging changes.
Add a stable DF ID for each new issue and record the symptom, established cause
(or what remains unknown), repair, test evidence and remaining limitations.
Update that entry as the fix moves from source tests to a checked package,
installation and public release. Do not mark a source-only repair as released.

Backfill older issues from dated records, not memory alone. Preserve failed
checks and distinguish software tests from simulator and scientific validation.
Keep credentials, private device paths, raw datasets and run files out of the
public log. Include the relevant log changes with the reviewed source update;
release notes may summarize them but do not replace the history.
