# Phase 2 acceptance

Phase 2 separates machine-local scientific resources from the installable core
through a versioned, typed profile and provider boundary.

## Implemented

- `digifly-resources-v1` JSON contract and Python model.
- Typed bindings for Digifly Public, external morphology and connectome roots,
  NEURON/Arbor/BMTK interpreters, VND, and the Workstation output root.
- Fixed access intent per type: read-only scientific inputs, executable
  runtimes, and one read-write output root.
- Atomic profile writes, deterministic fingerprints, strict identifiers, and
  validation of required resources.
- A fail-closed rule preventing output beneath any read-only scientific source.
- `digifly-resources init`, `show`, and `validate` commands.
- `digifly-doctor --profile` and `plan --profile` support; plan generation
  refuses an invalid resource boundary.
- Provider-based external morphology registration without copying or crawling
  the source until it is selected for indexing.
- Backward compatibility with the original `--workspace` doctor and native
  Digifly Public discovery path.

## Acceptance record — 2026-08-26

- Source suite: `168 passed`.
- Wheel: 63 files, 0.8 MiB, artifact audit and clean-install profile test passed.
- Source distribution: 101 files, 1.0 MiB, artifact audit passed.
- Signed macOS bundle: 124 files, 111.2 MiB, strict signature and artifact
  audits passed.
- The machine-local profile validates all configured bindings.
- NEURON, Arbor, BMTK, and VND pass discovery through the profile.
- Circuit source discovery returns MANC, Male CNS, Phase 2 local SWCs, and the
  explicitly registered external SWC provider without copying their contents.
- The rebuilt native Circuit Builder reports four local SWC/morphology sources
  after refreshing providers.
- Escape-SIZ validates its source/runtime/data boundary through the profile and
  remains intentionally launch-locked because the new Workstation output root
  contains no compatible cache and cache building was not authorized.

The active profile is machine-local state and is not included in a wheel,
source distribution, native bundle, or Git commit.
