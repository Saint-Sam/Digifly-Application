# Phase 0 baseline

Phase 0 preserves the working local application before packaging or feature
development changes its structure. The preserved application is **Digifly
Workstation**; the original **Digifly App** folder remains in place and is not a
build dependency of this repository.

## Lineage

- Original working folder at capture time:
  `<legacy-worktree>/Digifly App`
- Original committed parent: `132913f56aa6568ec32b502cbb093c478e9b6429`
- Complete working-state snapshot: `7e4f55018ec49c68805559e963bf1098a47c10b6`
- Immutable local baseline tag: `phase0-local-baseline-20260826`
- Workstation development branch: `phase0/workstation-baseline`

The snapshot commit includes the original folder's tracked modifications and
untracked source files. It deliberately excludes reproducible local products:
`.venv`, `dist`, deployment/Nuitka output, Python caches, pytest caches, macOS
metadata, and crash reports.

## Separation guarantees

- Product name: `Digifly Workstation`
- Python distribution: `digifly-workstation`
- Primary command: `digifly-workstation`
- Development interpreter override: `DIGIFLY_WORKSTATION_PYTHON`
- macOS bundle: `Digifly Workstation.app`
- macOS bundle identifier: `org.digifly.workstation`
- Qt settings domain: `Digifly / Digifly Workstation`
- Default writable workspace: `~/Digifly Workstation Workspace`

On first launch, Workstation may import the original app's configured read-only
Digifly workspace and NEURON interpreter paths. It does not import the original
output root. New caches, requests, simulation records, plots, exported
morphologies, and project state therefore default to the Workstation workspace.

## External scientific data

No connectome, morphology corpus, simulator runtime, compiled mechanism
catalogue, cache, or completed result set is copied into this repository or
application bundle. See [PACKAGING_BOUNDARY.md](PACKAGING_BOUNDARY.md) for the
enforced package contract.

## Reproduce the development app

```bash
cd "/path/to/Digifly-Application"
./scripts/setup_dev.sh
PYTHONPATH=src .venv/bin/python -m pytest
./scripts/run_dev.sh
```

Build the independent macOS bundle with:

```bash
./scripts/build_macos.sh
```

Phase 0 is complete only after the test suite passes in the Workstation-owned
environment and the independently named bundle launches successfully.

## Acceptance record — 2026-08-26

- `157 passed` in the Workstation-owned `.venv`.
- `Digifly Workstation.app` built as a 64-bit Apple Silicon (`arm64`) bundle.
- The 111 MB bundle passed `codesign --verify --deep --strict`.
- Bundle metadata reports `Digifly Workstation` and
  `org.digifly.workstation`.
- No SWC, CSV, Parquet, Feather, HDF5, SQLite, NumPy dataset, or file larger
  than 100 MB was present in the bundle.
- The packaged Workspace, Circuit Builder, and Escape-SIZ pages opened in the
  native application.
- The migrated Digifly Public root remained an external read-only source; the
  displayed output root was the separate
  `~/Digifly Workstation Workspace/runs` location.
