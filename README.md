# Digifly App

Digifly App is a standalone desktop workbench for configuring, validating,
running, and reviewing Digifly experiments. It does not modify Digifly's source
code. Instead, it opens a Digifly workspace; the implemented Escape-SIZ
workflow delegates execution through an explicit adapter, while newer design
pages remain previews until their adapters pass capability and comparison gates.

The first guided workflow configures and wraps the documented Escape-SIZ NEURON
GFC/contact-site sodium recipe and is intended to reproduce the work described
by `ESCAPE_SIZ_AGENT_HANDOFF.md`. The app now also
has a backend-unbound Circuit Builder preview. It discovers local SWC
roots, selects morphologies by neuron ID or path-derived type/family, renders
soma-point or full-skeleton representations, and stores a classic-HH draft plus
stable SWC child-node selections. It now also preserves exact native Phase 2 Na/K/Ca mechanism
identities, regional conductance densities, and a separate gap-junction edge
policy. It does not yet load chemical/gap connectivity or translate the design
into an executable Arbor, NEURON, or BMTK model.

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
- A versioned catalog for all eight native Phase 2 membrane surrogates:
  `na16a`, `na14a`, `kv14sh`, `kv42shal`, `kv21shab`, `kv31shaw`,
  `cav21cac`, and `cav31t`. Saved assignments include a stable catalog ID,
  source-relative path, and SHA-256 of the exact `.mod` source.
- Named classic-HH, Phase 2 Para+Shab, Phase 2 channel-family, and Escape-SIZ
  Para+HH-K profiles, plus fully custom multi-channel stacks with separate soma
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
cd "/path/to/Digifly App"
./scripts/setup_dev.sh
./scripts/run_dev.sh
```

The equivalent manual setup is:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[test]"
digifly-app
```

## Open the macOS build

The locally verified development bundle is `dist/Digifly App.app`.
Double-click it in Finder. New projects default to
`~/Digifly App Workspace`, keeping large caches and simulation recordings
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
`~/Digifly App Workspace/morphologies`; it never changes the source SWCs in
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
Digifly App makes that distinction visible and blocks a launch when required
source files, contact-policy evidence, or a compatible cache are absent unless
the user explicitly permits a cache build.

The native Escape-SIZ runner hardcodes source-tree paths, so Digifly App launches
it through an app-owned worker overlay. The worker keeps native code and assets
as read-only inputs while redirecting caches, requests, simulations, statuses,
and plots beneath the configured app output root. A new cache build is always an
explicit opt-in because the validated 49-cell recipe is expensive.

## Repository status

This folder is deliberately separate from `Digifly Public` and is ready to
become the `Digifly App` GitHub repository. A license has not been chosen yet;
that decision should be made before a public release.

See [Architecture](docs/ARCHITECTURE.md) and
[Roadmap](docs/ROADMAP.md) for the integration plan.

## Scientific readiness

Milestone 1 can validate, preview, launch, monitor, and review the documented
Escape-SIZ recipe through an app-owned output boundary. The source-boundary
dry run and historical-result validation pass. The first full app-owned cache
build is intentionally not bundled or auto-started: it is an explicit opt-in,
needs roughly 4 GB for the two legacy recording tables, and is the next
scientific acceptance run.

`Phase 2_Arbor_staging` is a working Digifly runtime, not a placeholder. Four
curated scenarios pass archived compact-NEURON comparison baselines using
built-in HH/passive mechanisms, `exp2syn`, and ohmic `gj`. That evidence does
not validate arbitrary circuits, biological equivalence, Drosophila MOD-channel
parity, or true heterotypic rectifying gaps. The app therefore defaults new
designs to classic HH while exposing the native profiles explicitly; selecting
a native NMODL channel under the Arbor target produces an unsupported warning
instead of a silent approximation. Rectifying GJ choices are explicitly blocked
for Arbor; `HeteroRectGap` is preserved as the current kinetic NEURON policy and
is never presented as equivalent to Arbor's built-in ohmic `gj`. Translating the Circuit Builder design into capability-checked
execution plans is the next integration boundary. VND remains an optional
external viewer rather than a simulator dependency.
