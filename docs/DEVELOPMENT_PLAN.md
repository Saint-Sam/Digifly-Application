# Workstation backend development plan

This plan is separate from the scientific-feature milestones in
`ROADMAP.md`. Its phases establish a distributable application without moving
local scientific datasets into the repository or release artifacts.

## Phase 0 — preserve and separate

- Capture the complete working Digifly App state in an immutable Git commit.
- Establish the Digifly Workstation identity, settings domain, writable root,
  environment, bundle identifier, and independently launchable app.
- Prove existing behavior with the complete test suite and a packaged UI smoke.

Status: complete. See `PHASE0_BASELINE.md`.

## Phase 1 — installable package foundation

- Make resource lookup work from a checkout, wheel installation, and native
  application bundle.
- Declare every small application-owned resource in package metadata.
- Build reproducible wheel and source-distribution artifacts.
- Add a mandatory artifact audit that blocks datasets, caches, machine-local
  paths, unexpected large files, and undeclared binaries.
- Test installation in a clean virtual environment without relying on the
  source checkout.

## Phase 2 — external resource profiles

- Introduce a versioned machine-local profile for Digifly Public, morphology
  sources, simulator interpreters, optional viewers, and the writable output
  root.
- Represent resources as typed bindings with explicit read-only, executable,
  or read-write access intent.
- Validate that writable output never resolves inside a read-only scientific
  source.
- Add CLI support for creating, inspecting, and validating profiles.
- Route morphology/connectome discovery through registered providers while
  retaining the legacy single-workspace behavior.

## Phase 3 — release automation

- Add CI matrices for supported Python and operating-system targets.
- Produce checksummed artifacts and software-bill-of-materials metadata.
- Add signed/notarized macOS and signed Windows release lanes.
- Select and record the project license before public distribution.

## Phase 4 — feature development

Resume layout, workflow, and simulator-adapter work against the stable package
and resource contracts. Scientific acceptance gates remain governed by
`ROADMAP.md` and are not weakened by packaging milestones.
