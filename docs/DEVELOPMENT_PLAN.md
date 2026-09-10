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

Status: complete. See `PHASE1_ACCEPTANCE.md`.

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

Status: complete. See `PHASE2_ACCEPTANCE.md`.

## Cross-cutting runtime onboarding

- Keep NEURON and Arbor external to the application package and permit a
  different verified Python interpreter for each simulator.
- Link users to each simulator's official installation documentation.
- Ask permission before a bounded, read-only PATH/common-environment search;
  never scan the whole disk or modify an environment.
- Probe package metadata in sanitized child processes, show versions and exact
  interpreter paths, and persist only choices the user explicitly accepts.

Status: foundation implemented; automated installation and per-platform
troubleshooting recipes remain future work.

## Phase 3 — managed data acquisition and import

- Add a configurable, Workstation-managed data-library root outside the
  installed application and migrate existing version-1 resource profiles
  without invalidating them.
- Add a provider-neutral download/import job contract with visible progress,
  cancellation, resumable staging, disk-space checks, checksums, provenance,
  license metadata, and atomic promotion into the managed library.
- Add a neuPrint provider that guides users through obtaining a token, stores
  the credential in the operating-system credential store rather than the
  resource profile, lists available datasets, and downloads selected
  connectivity, neuron metadata, and SWC skeletons into a versioned bundle.
- Add a ModelDB import flow that accepts an accession, downloaded archive, or
  local folder; inspects and explains the model contents; safely extracts or
  copies them into a versioned managed bundle; and registers the result without
  automatically executing imported code.
- Run a manifest-bounded post-import SWC audit that adapts to each neuron's own
  scale/topology, then offers a separately named radius-healed copy, a backed-up
  same-name overwrite, or an unchanged keep decision with provenance.
- Keep manual registration available for datasets users manage themselves and
  provide repair/relink tools when a managed or external resource is moved.
- Never bundle downloaded scientific data, credentials, or generated indexes
  in the core application or its release artifacts.

Status: backend frozen; UI/release acceptance remains. The v2 managed-data profile/migration, staged local-folder
import, checksum/provenance manifest, atomic promotion, adaptive SWC audit, and
Data Library foundation are implemented. The first neuPrint slice now includes
OS-keyring/session/environment credentials, live dataset discovery, bounded
body/type/instance/ROI previews, user-reviewed naming and destination, retry,
cancellation, durable credential-free resume checkpoints, selected-to-selected
connectivity CSV acquisition, staged SWC download, typed registration, and
quality-review handoff.
ModelDB accession lookup/download and offline ZIP/TAR/folder intake now inspect
bounded contents, keep code inert, preserve provenance, register model/SWC
sources, and reject malicious archive fixtures. Selected managed resources now
support manifest inspection, byte-preserving unregister/re-register,
recoverable Trash/restore, and validation-first relinking after a user moves the
library. The in-app mover now uses atomic rename on one filesystem or a
copy/checksum/relink/remove transaction across filesystems, and selected Trash
entries can be permanently purged only after an explicit irreversible-action
confirmation. Transient-only provider retry, receipt-backed ModelDB resume,
inspection-snapshot enforcement, and cancellable checksum-progress backend
contracts are complete and frozen in `BACKEND_CONTRACT_V1.md`. The remaining
Phase 3 work is UI exposure and the cross-platform acceptance matrix, not new
backend scope.
See `PHASE3_DATA_ACQUISITION_PLAN.md`.

## Phase 4 — release automation

- Add CI matrices for supported Python and operating-system targets.
- Produce checksummed artifacts and software-bill-of-materials metadata.
- Add signed/notarized macOS and signed Windows release lanes.
- Keep the private proprietary license during pre-release development; review
  third-party notices and final distribution terms before any public release.

## Phase 5 — feature development

Resume layout, workflow, and simulator-adapter work against the stable package
and resource contracts. Scientific acceptance gates remain governed by
`ROADMAP.md` and are not weakened by packaging milestones.
