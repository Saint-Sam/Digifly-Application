# Digifly Workstation backend contract v1

Status: frozen for UI integration after the Phase 3 stabilization cutoff.

This contract covers resource profiles, managed data acquisition, integrity,
model intake, lifecycle operations, and post-import SWC quality review. UI
work may compose these interfaces, but must not duplicate their filesystem,
credential, retry, validation, or transaction logic.

## Package and data boundary

- Application code, schemas, small mechanism sources, and small presets may be
  packaged. Connectomes, downloaded models, user credentials, generated
  indexes, run outputs, and caches must remain outside the package.
- Machine-local locations live in a versioned `ResourceProfile`. The managed
  data root is the only writable home for acquired scientific resources.
- Provider bundles are immutable source snapshots plus a writable `derived/`
  area. Every promoted bundle has a schema-v1 provider-neutral manifest.

## Remote request contract

- `RetryPolicy` defaults to three attempts with deterministic 0.5 s and 1.0 s
  cancellable delays, capped at 4.0 s. Server `Retry-After` is honored only up
  to that cap.
- Only transient transport failures and HTTP 408, 425, 429, 500, 502, 503,
  and 504 are retried. Authentication, authorization, not-found, malformed
  response, unsafe redirect, and integrity failures fail immediately.
- Declared response lengths and configured byte/row/file limits are enforced.
  Errors exposed to the UI never contain tokens or raw credential material.
- Cancellation interrupts backoff and staged work. It never promotes an
  incomplete bundle or rewrites a resource profile.

## neuPrint acquisition

Stable UI-facing interfaces are `NeuPrintClient`, `NeuPrintSelection`,
`NeuPrintAcquisitionRequest`, `preview_destination`,
`neuprint_checkpoint_path`, `discard_neuprint_checkpoint`, and
`acquire_neuprint_bundle`.

- Server origins are HTTPS-only and cannot contain credentials, paths, query
  strings, or fragments. Tokens stay in memory or the OS credential store.
- Dataset discovery and bounded previews are read-only. Skeleton and optional
  selected-to-selected connectivity responses have strict size/row limits.
- Checkpoints are deterministic, credential-free, atomically written, and
  bound to the reviewed request. Every reused file is path-contained,
  non-symlinked, size-checked, and SHA-256 verified.
- Promotion is a same-filesystem atomic rename. Morphology and connectivity
  bindings are persisted together. Profile failure moves the bundle back to
  resumable staging.

## ModelDB and local-model intake

Stable UI-facing interfaces are `ModelDBClient`, `ModelDBMetadata`,
`ModelImportRequest`, `inspect_model_source`, `modeldb_request`,
`modeldb_partial_paths`, `discard_modeldb_partial`, `model_destination`, and
`import_model_source`.

- Automatic downloads are restricted to the official
  `https://modeldb.science` origin and refuse external redirects.
- ModelDB archive partials use an atomic schema-v1 receipt, HTTP Range plus
  If-Range when a validator is available, declared-length/range validation,
  a hard compressed-size limit, and complete-ZIP validation before no-clobber
  promotion. Cancellation or a transient terminal failure preserves the exact
  partial for a caller that reuses the destination.
- ZIP/TAR/folder inspection is inert. It never imports code, compiles a
  mechanism, or launches a simulator. Unsafe paths, links, special/encrypted
  entries, duplicates, compression bombs, and configured size/path limits are
  rejected.
- Folder files are hashed during inspection and must match while copied.
  Archives are hashed during inspection and rechecked before and after
  extraction and while making any provenance copy.
- Manifest `file_count` and `total_bytes` describe the complete inventory,
  including a preserved original archive when present.

## Managed-resource integrity and lifecycle

Stable UI-facing interfaces are `load_managed_resource`,
`list_managed_resources`, `verify_managed_resource`, registration helpers,
and the operations in `resource_management`.

- Verification validates manifest paths, uniqueness, sizes, checksums,
  symlink exclusion, declared totals, and regular-file identity. It exposes
  cancellable byte/file progress and returns `verified`,
  `checksum_mismatch`, or `manifest_mismatch`.
- Registration and unregistration change profile bindings without rewriting
  source bytes. Trash is receipt-backed and recoverable. Permanent purge is
  restricted to one validated direct child of the managed Trash root.
- Same-volume library moves are atomic. Cross-volume moves copy and reread
  SHA-256 for every file, persist the new profile, and only then remove the
  source. Cancellation and failure leave the original usable.

## Change control

Backend-v1 changes after this cutoff must be backward-compatible and additive.
Changing a manifest/checkpoint/receipt schema, destination layout, destructive
boundary, credential behavior, or transaction ordering requires a v2 contract,
an explicit migration, regression fixtures for v1, and package-boundary audit.
UI layout and new scientific features must consume this contract rather than
quietly changing it.
