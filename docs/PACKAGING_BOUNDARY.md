# Packaging boundary

Digifly Workstation is an application package, not a scientific-data
distribution. Its installable artifact must stay small, reproducible, and
legally separable from local connectome and morphology collections.

## Included in the package

| Category | Examples |
| --- | --- |
| Application code | UI, project model, validators, adapters, worker overlays |
| Small application-owned resources | JSON schemas, mechanism source needed by an adapter, icons, documentation |
| Package metadata | Version, commands, dependency declarations, private project license, build configuration |
| Tests in source distributions | Unit and integration tests with generated or deliberately tiny fixtures |

Application-owned mechanism source may be included when it is small, has clear
provenance, and is required to reproduce an adapter. Compiled mechanism output
is platform- and simulator-version-specific and belongs in an external runtime
cache unless a release process explicitly produces a compatible optional
runtime component.
The packaged NEURON gap sources follow this rule: exact source bytes and their
hash manifest ship with the app, while `nrnivmodl` output is load-probed and
cached below the configured Workstation output root, never in the app bundle or
a connectome source tree.
The app-owned BMTK BioNet worker and SONATA translation code may also ship, but
the BMTK, NEURON, NumPy, and `h5py` packages do not. BioNet is enabled only when
all four coexist in one user-selected external interpreter.

## Never included in the core package

| Category | Examples |
| --- | --- |
| Connectome datasets | MANC, MaleCNS, FANC, FlyWire, VNC or brain graph exports |
| Morphology corpora | Bulk SWC roots and downloaded neuron collections |
| Simulation products | recordings, checkpoints, result tables, plots, videos |
| Build/runtime caches | Escape-SIZ caches, Arbor catalogues, compiled NMODL, MPI state |
| Simulator/scientific runtimes | NEURON, Arbor, BMTK, `h5py`, VND and their environments |
| Machine-local configuration | absolute paths, credentials, user preferences, recent files |

These are external resources. Workstation stores references and content
identities for user-managed sources, and Phase 3 may explicitly download or
import data into a configurable Workstation-managed data library. It never
mutates user-managed source datasets or silently copies scientific data into
its installation.

Machine bindings are stored in a versioned `digifly-resources-v2` profile;
version-1 profiles remain readable and can be migrated to a separate v2 file
without moving or altering their resources.
Each binding declares its kind and access intent. Digifly workspaces,
morphology sources, connectomes, and optional viewers are read-only; simulator
interpreters are executable; the Workstation output and managed-data roots are
read-write. Profile validation blocks either writable root inside a read-only
resource and prevents the two writable roots from containing one another.

## Runtime storage contract

The installed application is treated as read-only. All writable state goes to
the configured Workstation output root, which defaults to:

```text
~/Digifly Workstation Workspace
```

The workspace may contain projects, logs, requests, caches, simulation outputs,
plots, run-owned BMTK SONATA networks and native reports, exported unchanged-
SWC-plus-sidecar bundles, and an explicitly managed data library. Large user-
managed source datasets remain wherever the user maintains them and are opened
read-only whenever the workflow permits. Data
acquisition uses staging, validation, and atomic promotion into the managed
library; it never writes downloaded data into the application bundle.

Credentials are not data resources. Provider tokens must be stored in the
operating-system credential store (or supplied ephemerally through a supported
environment variable), referenced by opaque identifier, redacted from logs,
and excluded from profiles, manifests, projects, and artifacts.

## Release gate

Before any wheel, source distribution, application bundle, or installer is
published, an automated artifact audit must reject:

- unexpectedly large files;
- common connectome, morphology, recording, database, and cache formats outside
  an explicit small-fixture allowlist;
- absolute developer-machine paths;
- generated simulator or deployment directories; and
- undeclared executable binaries or dynamic libraries.

Canonical Digifly releases must also contain a byte-identical copy of the
repository's private project license. A license bundled by a dependency, a
title-only stub, or a modified copy does not satisfy this requirement.

That audit is a required packaging milestone, not an optional cleanup step.
