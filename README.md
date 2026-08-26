# Digifly Workstation

Digifly Workstation is a standalone desktop application for configuring, validating,
running, and reviewing Digifly experiments. It does not modify Digifly's source
code. Instead, it opens a Digifly workspace; the implemented Escape-SIZ
workflows delegate execution through explicit NEURON and Arbor adapters, while
the generic Circuit Builder remains a preview until each requested mechanism
and mapping passes an adapter's capability and comparison gates.

The first guided workflow configures and wraps the documented Escape-SIZ NEURON
GFC/contact-site sodium recipe and is intended to reproduce the work described
by `ESCAPE_SIZ_AGENT_HANDOFF.md`. The app now also
has a backend-unbound Circuit Builder preview. It discovers local SWC
roots, selects morphologies by neuron ID or path-derived type/family, renders
soma-point or full-skeleton representations, and stores a classic-HH draft plus
stable SWC child-node selections. It now also preserves exact native Phase 2 Na/K/Ca mechanism
identities, regional conductance densities, and a separate gap-junction edge
policy. It does not yet load chemical/gap connectivity or translate an
arbitrary Circuit Builder design into an executable Arbor, NEURON, or BMTK
model. The dedicated Escape-SIZ Arbor comparison is a separate, locked adapter
for the active 49-cell Ablation-notebook recipe.

## What the first milestone includes

- A native Qt desktop interface for macOS, Windows, and Linux.
- Workspace discovery for NEURON, Arbor, BMTK, and VND assets.
- A Circuit Builder preview with Arbor as its default saved intent and selectors
  for future NEURON or BMTK adapter targets; these selectors do not create a
  runnable plan yet.
- Local SWC-source discovery and neuron queries by ID, type, or family, such as
  `10000, 10002`, `type:GFC2`, `family:DN`, and `all:IN`.
- A default one-marker-per-neuron soma overview with an obvious **Soma points** /
  **Full skeletons** toggle. The batched OpenGL skeleton view adds whole-neuron
  isolation and multi-segment selection by SWC child-node ID.
- Editable cell-set-, neuron-, and SWC-segment classic-HH drafts, including Na,
  K, and Ca reversal potentials, which remain capability-gated until an
  execution adapter validates them.
- A versioned catalog for the three Augustin-2019 GF mechanisms and all eight
  native Phase 2 membrane surrogates. The exact GF set is `nat`, `nap`, and `k`;
  the Phase 2 set is
  `na16a`, `na14a`, `kv14sh`, `kv42shal`, `kv21shab`, `kv31shaw`,
  `cav21cac`, and `cav31t`. Saved assignments include a stable catalog ID,
  source-relative path, and SHA-256 of the exact `.mod` source.
- A named **Augustin 2019 GF (exact)** profile keeps ELeak −85 mV, ENa +65 mV,
  EK −74 mV, the three published conductances, 25 °C, and −65 mV initialization
  together. It reports the model's predicted zero-current equilibrium separately
  (−74.670 mV); initialization is not labeled as rest. Classic-HH, Phase 2
  Para+Shab, Phase 2 channel-family, and Escape-SIZ Para+HH-K profiles remain
  available, plus fully custom multi-channel stacks with separate soma
  and branch densities. A named profile applies its HH and native-channel values
  atomically; editing either half marks it Custom.
- Selected-neuron, selected-SWC-segment, cell-set-default, and explicit
  all-loaded-neuron mass-apply actions. More-specific saved segment overrides
  remain intact when a broader neuron assignment is applied.
- Separate all-electrical-edge policies for native `Gap`, `RectGap`, and
  `HeteroRectGap` intent, including placement and per-site versus pair-total
  conductance semantics, exact source hashes, and endpoint-role labels.
  Pair-total conductance is explicitly divided equally over selected sites;
  no GJ is attached until a two-endpoint connectivity source is loaded.
- Non-destructive reusable morphology bundles containing an unchanged SWC,
  biophysics sidecar, provenance manifest, and SHA-256 identity.
- An Escape-SIZ preset matching the latest documented GFC2 experiment.
- A dedicated Arbor 0.12.2 adapter for the locked 49-cell Escape-SIZ Ablation
  comparison, with paired gap-enabled/gap-disabled plans and app-owned outputs.
- App-owned Arbor NMODL sources for `Gap`, `RectGap`, and `HeteroRectGap`, plus
  a fail-closed worker bridge that loads the compiled catalogue under the
  `digifly_` prefix and never substitutes Arbor's built-in `gj`.
- Explicit separation of build-time and runtime-safe controls.
- Cache, contact-policy, environment, disk, and output preflight checks.
- Exact command preview with no shell interpolation.
- Streamed run logs and a guarded simulation launch.
- Read-only loading and validation of completed Escape-SIZ summaries and plots.
- A versioned `.digifly.json` project format and adapter-oriented architecture.

## Run from source

The UI uses its own environment. This is intentional: simulator environments
must remain isolated, and the pre-existing base PySide6 build is binary-incompatible
with this Mac. Create the UI environment once:

```bash
cd "/path/to/Digifly Workstation"
./scripts/setup_dev.sh
./scripts/run_dev.sh
```

The equivalent manual setup is:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[test]"
digifly-workstation
```

## Open the macOS build

The locally verified development bundle is `dist/Digifly Workstation.app`.
Double-click it in Finder. New projects default to
`~/Digifly Workstation Workspace`, keeping large caches and simulation recordings
outside both the application bundle and `Digifly Public`.

The current bundle is ad-hoc signed for local testing. A public download still
needs an Apple Developer ID signature and notarization. Rebuild instructions
are in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

Run the non-GUI workspace check with:

```bash
PYTHONPATH=src .venv/bin/python -m digifly_app.cli doctor \
  --workspace "/path/to/Digifly Public"
```

Run tests with:

```bash
PYTHONPATH=src .venv/bin/python -m pytest
```

## Circuit Builder controls

New cell sets open in **Soma points** mode in the orthographic VIP GLIA anatomy
orientation used by `Ablation Baseline and Na Response Match.ipynb`. The
adjacent **Soma points** / **Full skeletons** buttons switch representations at
any time. MANC DNs use Digifly's rostral type-1 pseudosoma convention, placing
their marker at the northern end of the reference view. Switching modes,
focusing a neuron, and `F` fit the camera to the active point or skeleton
representation; `R` also restores the reference orientation.

- Left-drag or `W/A/S/D`: rotate.
- Shift-left-drag, middle-drag, or arrow keys: pan.
- Mouse wheel: zoom.
- Right-click: center the picked neuron; right-double-click: restore all.
- In **Soma points**, left-click a marker to select and isolate that neuron.
- In **Full skeletons**, left-click a visible skeleton to select and isolate it.
- Left-click isolated skeleton segments: toggle individual SWC compartments;
  selected compartments are always electric magenta.
- Cmd/Ctrl+Shift-left-drag on an isolated neuron: draw a box that adds every
  intersecting compartment to the selection.
- `Escape` or `I`: restore/isolate; `F`: fit; `R`: reset view; `C`: clear
  segment selection.

Compartment selection and editing are available only in **Full skeletons**.
Existing compartment selections remain stored when switching to **Soma
points**, where they are hidden until the skeleton view is restored.

Readouts and guidance text wrap within the viewport card and support normal
mouse text selection plus the native right-click Copy menu. Large multi-neuron
views in **Full skeletons** use a connected topology-preserving preview only
during overview camera motion; deep zoom uses the exact morphology with
hierarchical off-screen culling. Settled skeleton rendering, picking, editing,
and exports always use the exact source geometry; the point overview does not
alter it.

The editor writes unchanged-SWC + biophysics bundles under
`~/Digifly Workstation Workspace/morphologies`; it never changes the source SWCs in
`Digifly Public`. The sidecar preserves channel catalog IDs, exact NMODL
suffixes, regional densities, and SWC-node overrides. Gap junctions remain in
the circuit project because a single-neuron bundle cannot preserve both edge
endpoints.

The current Circuit Builder stores one future all-electrical-edge GJ policy.
That is sufficient for a uniform edge set, but it cannot yet represent the full
mixed Escape-SIZ topology (heterotypic GF→target contact gaps together with the
optional 55 GFC2–GFC2 ohmic AIS pairs). Multiple named edge sets and contact-file
identity are required before the generic Circuit Builder can execute that mixed
design.

## Safety model

Escape-SIZ distinguishes expensive build-time state from runtime-safe state.
Digifly Workstation makes that distinction visible and blocks a launch when required
source files, contact-policy evidence, or a compatible cache are absent unless
the user explicitly permits a cache build.

The native Escape-SIZ runner hardcodes source-tree paths, so Digifly Workstation launches
it through an app-owned worker overlay. The worker keeps native code and assets
as read-only inputs while redirecting caches, requests, simulations, statuses,
and plots beneath the configured app output root. A new cache build is always an
explicit opt-in because the validated 49-cell recipe is expensive.

The Arbor comparison has its own app-owned worker boundary. Preflight loads and
inspects the compiled `digifly_gap` mechanism catalogue in the selected Arbor
runtime before a run is allowed. The catalogue is built for Arbor 0.12.2 and the
current compiler/platform ABI, then reused only from the app output runtime
cache under `_runtime/arbor_catalogues`; it is not treated as portable across
Arbor versions or architectures.

## Repository status

This folder is deliberately separate from `Digifly Public` and is ready to
become the `Digifly Workstation` repository. A license has not been chosen yet;
that decision should be made before a public release.

See [Architecture](docs/ARCHITECTURE.md) and
[Roadmap](docs/ROADMAP.md) for the integration plan. The preserved starting
point and package/data contract are recorded in
[Phase 0 baseline](docs/PHASE0_BASELINE.md) and
[Packaging boundary](docs/PACKAGING_BOUNDARY.md). The distributable package
foundation is recorded in [Phase 1 acceptance](docs/PHASE1_ACCEPTANCE.md).

## Scientific readiness

Milestone 1 can validate, preview, launch, monitor, and review the documented
Escape-SIZ recipe through an app-owned output boundary. The source-boundary
dry run and historical-result validation pass. The first full app-owned cache
build is intentionally not bundled or auto-started: it is an explicit opt-in,
needs roughly 4 GB for the two legacy recording tables, and is the next
scientific acceptance run.

`Phase 2_Arbor_staging` is a working Digifly runtime, not a placeholder. In
addition to its earlier compact-NEURON baselines, Digifly Workstation now implements a
dedicated Arbor 0.12.2 execution path for the active 49-cell Escape-SIZ Ablation
comparison. It validates the locked inputs, removes the 99 direct-GF chemical
rows to retain 2,331 rows, preserves the 959 gap-contact rows, and runs paired
gap-enabled and gap-disabled conditions beneath an app-owned output boundary.

The app-owned `digifly_gap` catalogue contains Arbor ports of `Gap`, `RectGap`,
and `HeteroRectGap`. For the locked Ablation comparison, the worker bridge uses
`digifly_hetero_rect_gap` with the notebook's voltage-dependent gate, residual
floor, endpoint orientation, and opening/closing time constants. This is an
equation and parameter port, not a claim of biological or finite-step numerical
equivalence: Arbor advances the gate with `cnexp`, whereas the NEURON source
uses `derivimplicit`. The first empirical 49-cell NEURON–Arbor audit has now
run. Catalogue, implementation, placement, and provenance checks pass, but the
overall verdict is `NOT_YET_EQUIVALENT`: all 11 stimulated source somas fail the
required gap-disabled baseline-readiness gate before the gap mechanism can be
judged. A prescribed-voltage solver replay puts the `cnexp`/`derivimplicit`
difference at about 0.036% of peak junction current. Production runs remain on
the previously completed `every_segment` policy. An experimental
`legacy_neuron_section_explicit` boundary/soma-site bridge is retained only for
bounded, single-thread diagnostics. The original bridge encoded thousands of
boundaries as one recursively folded locset and a four-thread 49-cell setup
exited with `SIGBUS`. Its replacement builds a balanced binary locset with the
same CV boundaries; an 11-source four-thread construction A/B no longer crashes,
and a full 49-cell four-thread 0.02 ms construction/run probe also completes.
The policy remains quarantined from production until the complete threaded
diagnostic is requalified.
It also remains a compatibility candidate rather than topology equivalence
because Arbor adds zero-area fork CVs and retains the staged tree's tiny root
stub. A one-thread 49-cell, one-pulse diagnostic completed, but its corrected
dense-grid comparison again passed 0 of 11 source somas despite high waveform
correlations. The next acceptance step is resolving that upstream absolute-
voltage mismatch. Result metadata keeps `equivalence_claim` false.

The custom-gap adapter does not make arbitrary Circuit Builder designs runnable
and does not establish parity for the other native Drosophila membrane MOD
channels. Unsupported generic mappings remain capability-gated instead of being
silently approximated. Translating the Circuit Builder design into validated
execution plans is the next integration boundary. VND remains an optional
external viewer rather than a simulator dependency.
