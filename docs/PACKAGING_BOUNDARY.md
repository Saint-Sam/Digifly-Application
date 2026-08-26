# Packaging boundary

Digifly Workstation is an application package, not a scientific-data
distribution. Its installable artifact must stay small, reproducible, and
legally separable from local connectome and morphology collections.

## Included in the package

| Category | Examples |
| --- | --- |
| Application code | UI, project model, validators, adapters, worker overlays |
| Small application-owned resources | JSON schemas, mechanism source needed by an adapter, icons, documentation |
| Package metadata | Version, commands, dependency declarations, licenses, build configuration |
| Tests in source distributions | Unit and integration tests with generated or deliberately tiny fixtures |

Application-owned mechanism source may be included when it is small, has clear
provenance, and is required to reproduce an adapter. Compiled mechanism output
is platform- and simulator-version-specific and belongs in an external runtime
cache unless a release process explicitly produces a compatible optional
runtime component.

## Never included in the core package

| Category | Examples |
| --- | --- |
| Connectome datasets | MANC, MaleCNS, FANC, FlyWire, VNC or brain graph exports |
| Morphology corpora | Bulk SWC roots and downloaded neuron collections |
| Simulation products | recordings, checkpoints, result tables, plots, videos |
| Build/runtime caches | Escape-SIZ caches, Arbor catalogues, compiled NMODL, MPI state |
| Simulator installations | NEURON, Arbor, BMTK, VND and their environments |
| Machine-local configuration | absolute paths, credentials, user preferences, recent files |

These are user-selected external resources. Workstation stores references and
content identities where useful; it does not mutate source datasets or silently
copy them into its installation.

Machine bindings are stored in a versioned `digifly-resources-v1` profile.
Each binding declares its kind and access intent. Digifly workspaces,
morphology sources, connectomes, and optional viewers are read-only; simulator
interpreters are executable; only the Workstation output root is read-write.
Profile validation blocks an output root located inside any read-only resource.

## Runtime storage contract

The installed application is treated as read-only. All writable state goes to
the configured Workstation output root, which defaults to:

```text
~/Digifly Workstation Workspace
```

The workspace may contain projects, logs, requests, caches, simulation outputs,
plots, and exported unchanged-SWC-plus-sidecar bundles. Large source datasets
remain wherever the user maintains them and are opened read-only whenever the
workflow permits.

## Release gate

Before any wheel, source distribution, application bundle, or installer is
published, an automated artifact audit must reject:

- unexpectedly large files;
- common connectome, morphology, recording, database, and cache formats outside
  an explicit small-fixture allowlist;
- absolute developer-machine paths;
- generated simulator or deployment directories; and
- undeclared executable binaries or dynamic libraries.

That audit is a required packaging milestone, not an optional cleanup step.
