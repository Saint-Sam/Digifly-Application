# Contributing

Digifly App separates portable experiment definitions from machine-specific
scientific runtimes. New integrations should implement the adapter boundary and
run simulator imports in a worker process.

Before opening a change:

```bash
./scripts/setup_dev.sh
QT_QPA_PLATFORM=offscreen PYTHONPATH=src .venv/bin/python -m pytest
```

Do not commit native Digifly datasets, generated caches, run outputs, virtual
environments, credentials, VND binaries, compiled mechanisms, or large figures.
