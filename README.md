# Digifly App

Digifly App is a standalone desktop workbench for configuring, validating,
running, and reviewing Digifly experiments. It does not modify Digifly's source
code. Instead, it opens a Digifly workspace and delegates execution through
explicit simulator adapters.

The first guided workflow is the Escape-SIZ NEURON GFC/contact-site sodium
experiment described by `ESCAPE_SIZ_AGENT_HANDOFF.md`. The application also
discovers the Arbor and BMTK/VND trees so their workflows can be added behind
the same execution contract.

## What the first milestone includes

- A native Qt desktop interface for macOS, Windows, and Linux.
- Workspace discovery for NEURON, Arbor, BMTK, and VND assets.
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
cd "/Users/juanlopez2016/Desktop/Digifly App"
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
  --workspace "/Users/juanlopez2016/Desktop/Digifly Public"
```

Run tests with:

```bash
PYTHONPATH=src .venv/bin/python -m pytest
```

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

Arbor, BMTK/SONATA, and VND are discovered and represented as isolated engine
lanes, but their guided experiment adapters remain roadmap work. VND remains an
optional external viewer rather than a simulator dependency.
