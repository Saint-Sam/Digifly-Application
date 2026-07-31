# Roadmap

## Milestone 1 — Escape-SIZ workbench

- [x] Workspace doctor and engine discovery.
- [x] Latest GFC2 NEURON preset.
- [x] Safe preflight and cache compatibility display.
- [x] App-owned cache/run/plot output boundary.
- [x] Background process execution and log capture.
- [x] Completed-summary and canonical-figure review.
- [ ] First full app-owned cache build and reproduction acceptance run.

## Milestone 2 — Native Escape-SIZ extraction

- Move experiment configuration into a stable, versioned schema.
- Add threshold, sodium-reserve, frequency/recovery, and Voltage Sink recipes.
- Add mutation-bundle and custom-SWC selection.
- Validate `real_ap_v1`, passive matching, anatomy ordering, and contact counts.

## Milestone 3 — Arbor

- Wrap cache-free Escape-SIZ acceptance pilots.
- Add backend comparison with common input and result contracts.
- Support `DIGIFLY_PHASE2_ARBOR_OUTPUT_ROOT` directly.

## Milestone 4 — BMTK and VND

- Run the BMTK doctor and validate SONATA manifests/crosswalks.
- Add BioNet, PointNet, and DPointNet environment profiles.
- Export selected activity to focused or expanded VND views.
- Launch VND as an optional external viewer without treating it as a simulator.

## Milestone 5 — Distribution

- Automated tests on macOS, Windows, and Linux.
- Signed/notarized macOS build and packaged Windows/Linux builds.
- Example dataset small enough for CI.
- GitHub Actions, release artifacts, contribution guide, and chosen license.
