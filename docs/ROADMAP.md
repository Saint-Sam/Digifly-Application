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

## Milestone 3 — Backend-unbound Circuit Builder preview

- [x] Discover native local SWC/morphology roots without copying source data.
- [x] Resolve morphology members by neuron ID, type, and path-derived
  AN/DN/IN/MN/SN family.
- [x] Define a serializable backend-unbound cable design and classic-HH draft;
  adapters may translate or reject fields.
- [x] Default new designs to the Arbor target with NEURON/BMTK selection.
- [x] Add batched OpenGL rendering, camera controls, isolation, and stable
  SWC child-node selection.
- [x] Save non-destructive reusable SWC/biophysics/provenance bundles.
- [x] Preserve SWC variants, validate topology, and restore exact saved IDs.
- [x] Catalog the eight native Phase 2 Na/K/Ca mechanisms and preserve exact
  mechanism identity, source hash, advanced parameters, and soma/branch density
  in circuit and morphology sidecars.
- [x] Apply HH + membrane profiles to the cell-set default, one neuron,
  selected SWC segments, or all currently loaded neurons.
- [x] Store `Gap`/`RectGap`/`HeteroRectGap` as capability-gated electrical-edge
  policy intent instead of attaching GJs to isolated cells.
- [ ] Add synapse/contact editing and circuit connectivity overlays.
- [ ] Import explicit gap endpoint/contact sets and apply saved GJ policies to
  selected edges with per-site/pair-total validation.
- [ ] Support multiple named electrical-edge sets so heterotypic GF contact gaps
  and optional GFC2 ohmic AIS pairs can coexist in one Escape-SIZ design.
- [ ] Add canonical connectome/neuron/morphology/edge/biophysics manifests.
- [ ] Move large morphology-source indexing off the GUI thread.

## Milestone 4 — Unified execution adapters

- Wrap the four curated `Phase 2_Arbor_staging` comparison workflows first.
- Translate `CircuitSpec` into a validated Arbor recipe and run manifest.
- Translate supported subsets into adapter-specific NEURON and BMTK/BioNet
  representations, with comparison gates.
- Gate unsupported mechanisms explicitly instead of silently approximating.
- Add backend comparison with common inputs, traces, metrics, and provenance.
- Support `DIGIFLY_PHASE2_ARBOR_OUTPUT_ROOT` directly.

## Milestone 5 — BMTK and VND

- Run the BMTK doctor and validate SONATA manifests/crosswalks.
- Add BioNet, PointNet, and DPointNet environment profiles.
- Export selected activity to focused or expanded VND views.
- Launch VND as an optional external viewer without treating it as a simulator.

## Milestone 6 — Distribution

- Automated tests on macOS, Windows, and Linux.
- Signed/notarized macOS build and packaged Windows/Linux builds.
- Example dataset small enough for CI.
- GitHub Actions, release artifacts, contribution guide, and chosen license.
