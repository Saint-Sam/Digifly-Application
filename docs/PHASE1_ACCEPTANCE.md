# Phase 1 acceptance

Phase 1 establishes an installable Python distribution and release boundary
without changing scientific workflows or bundling machine-local datasets.

## Implemented

- Deterministic resource resolution for source checkouts, wheel installations,
  native bundles, and explicitly bound external worker processes.
- Declared wheel installation of schemas, presets, architecture documentation,
  provenance-locked Augustin mechanisms, and Arbor gap-junction source ports.
- Wheel, source-distribution, console, GUI, doctor, and package-audit entry
  points in `pyproject.toml`.
- An explicit source-distribution manifest that excludes environments, build
  products, caches, runtime workspaces, and the machine-local Phase 0 record.
- A fail-closed artifact auditor for dataset formats, generated directories,
  machine paths, oversized files, and compiled binaries outside native bundles.

## Acceptance record — 2026-08-26

- Source suite: `161 passed`.
- Wheel: 59 files, 0.8 MiB, artifact audit passed.
- Source distribution: 95 files, 1.0 MiB, artifact audit passed.
- Native macOS bundle: 117 files, 111.0 MiB, artifact audit passed.
- The wheel installed with no source checkout on `sys.path` in a clean virtual
  environment.
- Installed schemas, mechanisms, worker scripts, and the Augustin source-hash
  contract resolved from that clean environment.

These are local development candidates, not public releases. Licensing,
platform CI, signing, notarization, and public versioning remain later gates.
