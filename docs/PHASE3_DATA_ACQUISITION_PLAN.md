# Phase 3 — managed data acquisition and import

## Outcome

Users can obtain or import scientific data without knowing Digifly's directory
layout. Workstation places managed data in a configurable library outside the
application package, validates it, records its identity and provenance, and
registers it with the same provider boundary used for existing local data.

The default library will be beneath the Workstation-owned root, for example:

```text
~/Digifly Workstation Workspace/data/
  neuprint/<server>/<dataset>/<snapshot>/
  modeldb/<accession>/<version>/
  imports/<provider>/<resource-id>/<version>/
```

Users may choose another library location. Existing folders that users manage
themselves remain read-only external resources; Workstation never reorganizes
or mutates them without an explicit import action.

## Storage and registry contract

- Evolve the resource profile with a read-write managed-data root and provide
  an explicit, tested migration from `digifly-resources-v1`.
- Give every acquired resource a stable ID and a manifest containing provider,
  source URL/server, dataset or accession, source version, retrieval time,
  selection/query parameters, license/citation information, file inventory,
  byte sizes, and SHA-256 checksums.
- Preserve downloaded source bytes when practical. Put normalized SWCs,
  indexes, and other derived files in a separate derived-data directory and
  record their producing Digifly version and inputs.
- After every completed import, audit each manifest-declared SWC for structural
  errors and per-neuron adaptive radius discontinuities. Never use hard-coded
  node IDs or one absolute radius across diverse neurons and connectomes.
- Download or extract into a temporary staging directory, validate the staged
  bundle, then atomically promote it into the library and register it. Failed or
  cancelled jobs must never appear as complete resources.
- Detect name collisions, insufficient disk space, moved folders, incomplete
  downloads, and checksum mismatches; offer retry, repair, relink, or remove
  actions without touching unrelated data.

## neuPrint provider

- Let the user choose a neuPrint server and sign in through the server's normal
  web interface, with clear instructions for copying their application token.
- Accept a pasted token or the standard neuPrint credentials environment
  variable for development, test it before starting a download, redact it from
  logs/errors, and store persistent credentials only in the operating-system
  credential store. The profile records a credential reference, never the
  token itself.
- Query the server for available datasets and require the user to select the
  exact dataset/version before download.
- Support bounded selections by body ID, type, instance, ROI, or an explicit
  reviewed query. Preview estimated record counts and require confirmation for
  large jobs.
- Download selected neuron metadata, connectivity tables, and skeletons in SWC
  form, with pagination/batching, retry/backoff, cancellation, and resumable
  checkpoints where the server permits them.
- Register the completed bundle as connectome and morphology providers so the
  Circuit Builder can use it immediately.

## ModelDB and local-model import

- Accept a ModelDB accession number, a user-downloaded ZIP/TAR archive, or a
  local model folder. Use ModelDB metadata when an accession is available, but
  retain a completely offline manual-import path.
- Show the detected simulator, morphology files, mechanisms, entry points,
  README, citation, license, archive size, and proposed destination before the
  user confirms import.
- Reject unsafe archive paths, symlink escapes, extraction bombs, truncated
  archives, and files outside configured limits. Imported scripts, binaries,
  HOC, Python, and NMODL are data until the user explicitly chooses a later,
  sandboxed validation or execution workflow.
- Preserve the original archive when requested, extract into a versioned model
  bundle, and generate a Digifly manifest without rewriting the model source.
- If an archive is not directly runnable by Digifly, explain what is missing
  and keep it registered as a source model instead of presenting a false
  success state.

## User experience

- Add a **Data Library** page with **Download from neuPrint**, **Import ModelDB
  model**, **Import local data**, and **Register existing folder** entry points.
- Display per-job progress, transferred/total bytes when known, current stage,
  destination, pause/cancel/retry controls, validation results, and required
  citations/licenses.
- Provide a post-import action to open the resource in Circuit Builder, inspect
  its manifest, reveal it in the file manager, or remove only the Workstation
  registration while leaving externally managed data untouched.
- Present every flagged SWC for review with **Save healed copy** (default),
  **Overwrite original** (with a recoverable backup), or **Keep original**.
  Automatic healing must change radii only and emit a hash-addressed quality
  provenance sidecar.

## Acceptance checklist

- [x] Version-1 profiles migrate without losing or moving existing bindings.
- [ ] The managed library can be relocated and relinked on macOS, Windows, and
      Linux.
- [ ] Tokens never appear in profiles, project files, logs, crash reports,
      manifests, command previews, or release artifacts.
- [ ] A neuPrint test account can list datasets and acquire a small selected
      bundle that immediately appears as connectome and morphology providers.
- [x] Recent manifest-declared SWCs receive a provider-neutral, per-SWC adaptive
      radius audit; copy and backed-up overwrite modes prove that topology and
      geometry remain unchanged.
- [ ] A ModelDB archive and a local folder can be safely imported, inspected,
      registered, reopened after restart, and removed from the registry.
- [ ] Interrupted acquisition resumes or restarts cleanly and never registers
      a partial bundle as complete.
- [ ] Malicious archive fixtures covering traversal, symlink escape, and
      decompression limits are rejected without writing outside staging.
- [ ] Package audits still prove that no managed data or credentials enter the
      wheel, source distribution, native app, or installer.
- [ ] Unit tests use small fixtures or mocked provider responses; no large
      public dataset is required to build or test Digifly Workstation.

## Current implementation checkpoint

Implemented in the first Phase 3 foundation increment:

- schema-v2 resource profiles with one managed-data root, automatic in-memory
  v1 compatibility, and explicit side-by-side migration;
- a provider-neutral local-folder importer with collision/free-space checks,
  symlink and special-file rejection, source-byte preservation, SHA-256 file
  inventory, provenance metadata, same-filesystem staging, atomic promotion,
  and post-promotion profile registration;
- a mandatory per-file SWC structural/adaptive-radius audit recorded in each
  import manifest, followed by the existing explicit user repair review;
- Data Library navigation, managed-resource inventory, local import, reveal,
  and read-only no-copy registration of existing SWC folders.

The neuPrint and ModelDB controls deliberately remain guidance-only until their
credential, query/archive safety, cancellation, retry, and resume contracts are
implemented and tested.

## External interfaces verified during planning

- neuPrint's supported Python client accepts a server, dataset, and personal
  application token, can list datasets, query connectivity, and export neuron
  skeletons as SWC.
- ModelDB exposes public metadata through its versioned JSON API and supports
  downloading model archives; some entries point to externally hosted code, so
  manual archive/folder import remains a required first-class path.
