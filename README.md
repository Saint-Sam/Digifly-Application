# Digifly Workstation

<p align="center">
  <img src="src/digifly_app/assets/digifly_icon.png" width="180" alt="Digifly paired giant-fiber mark">
</p>

Digifly Workstation is a desktop app for loading neuron morphologies, configuring
circuits and experiments, and reviewing simulation results.

## Mac and Windows downloads

This is local test build **20261025.3**, not a published release.

The [current public release](https://github.com/Saint-Sam/Digifly-Application/releases/tag/v0.1.0-alpha.20261023.6)
contains build **20261023.6** for Mac Apple silicon and Windows x86-64.
Download and extract the ZIP for the target platform. Older app versions need
one manual installation of this release because their updater expects the
discontinued checksum files.

- **Mac:** Apple silicon, macOS 14 or newer. Local tester builds are ad-hoc
  signed, not Apple-notarized. Intel Mac packages are not currently supplied.
- **Windows:** x86-64. The current edition includes an official NEURON installer
  and a private offline runtime setup path. Existing simulator installations
  are not replaced. Arbor is deferred for this Windows edition.
- Scientific runtimes, downloaded SWCs, saved projects and run results remain
  separate from the app. The Windows edition includes only the explicitly
  selected, checksum-pinned MANC connectivity Parquet; it contains no SWCs.

## Recent improvements

Optional neuron connectivity downloads now grow one shared database per exact
connectome release, including connections between successive groups in both
directions. Repeated pairs update their saved counts without being counted twice.
Circuit Builder and Experiment Builder use the same graph revision. Saved
experiments retain their original data when new downloads arrive.

TTMn searches and motor-neuron labels work across Male CNS and MANC. Male CNS
skeletons display upright in Circuit Builder. Downloaded connection counts can
be used with an explicit chemical model: choose strength per count or per pair,
kinetics, delay, reversal and assumed soma/AIS placement. These are editable model
assumptions, not measured contact locations or conductances. Chemical count tables
do not supply gap-junction data.

Disconnected skeleton pieces can be joined in a separately saved copy after
reviewing the added length and volume. Short tapered filler compartments keep
existing points, radii and connection locations unchanged, but add membrane and
change conduction. They are synthetic cables, not recovered anatomy. Raw
Male CNS v0.9/v1.0 and MANC v1.2.1 downloads use their checked 8 nm convention;
already-converted local SWCs are not rescaled just because of their dataset label.

The new Data Library source separates linked local SWC libraries from downloaded
subsets. Workspace sources and registered folders appear without copying files;
their complete paths are visible. Explicit background SWC-file counts are
read-only snapshots, not biological completeness checks. Missing linked drives
remain visible, and linked folders have no managed trash/move controls.

Choose **Save library shortcut…** to save a small `.digifly-library.json` file
and remember an external SWC folder on this device. The default file location is
`config/library-links` beside the external app settings, not inside the app;
restarts and updates retain the folder binding. **Open library shortcut…**
reviews the location before remembering it. On another device, locate that
user's own folder once if needed. No SWCs or credentials are copied into the
shortcut; it opens through Data Library, not an OS-specific file association.
The page scrolls at smaller window sizes so controls do not overlap tables.

- A growing SWC library for each exact server and dataset release, with
  `export_swc/family/type/body-ID/` folders shared by individual and list searches.
- Literal neuron-name/ID lists such as `DNp01, TTMn, PSI`, with per-name match
  counts and a preview before downloading.
- Existing-neuron checks and explicit Keep/Replace choices, preserving previous
  files when a replacement is requested.
- Clear download destinations and plain-language download details.
- Data Library support for saved NEURON, Arbor, BioNet and NetPyNE settings.
- Verified update downloads without separate checksum files, Install and Restart, saved-draft recovery and
  restoration after early startup failure.
- Post-success cleanup of exact, unchanged updater-owned obsolete files.
  Saved projects, SWCs, installed simulators and run data are protected.
- Build-number checks that reject older downloads and disable automatic
  replacement when the installed version cannot be verified.
- Mac startup prevents bytecode-cache writes inside the installed bundle,
  preserving the strict update integrity check. Windows' shortcut/runtime
  package remains separately tested; this Mac-only guard is not a Windows
  bundled-Arbor claim.

## Modeling and results

Circuit Builder separates morphology, connectivity and membrane settings from
Experiment Builder's stimulation, recording and execution settings. Supported
paths include NEURON, Arbor and BMTK BioNet; each requested feature is checked
against the selected engine's implemented capabilities before execution.
Unsupported settings must be reported, not silently approximated.

NetPyNE and independently authored BioNet replication tools are included in the
source. Their presence does not mean every paper claim or figure is accepted.
BioNet and NetPyNE share NEURON and are not independent-solver replications.
Run-specific scientific evidence and limitations remain separate from app
installation tests.

See [architecture](docs/ARCHITECTURE.md),
[packaging boundaries](docs/PACKAGING_BOUNDARY.md),
[Windows testing](docs/WINDOWS_TESTER.md) and
[Mac testing](docs/MACOS_TESTER.md), plus
[updater checks](docs/UPDATER_REPAIR_20261017_V1.md).

## Source development

Use the setup scripts for your platform, then run `main.py` from the configured
development environment. Packaged apps include their own interface runtime;
ordinary users do not need to build the source to use a release.

## License

Digifly-authored code is [MIT licensed](LICENSE). Third-party software, channel
mechanisms and datasets retain their own terms and attribution; see
[third-party materials](THIRD_PARTY_NOTICES.md). Application test results are
engineering evidence, not scientific or legal certification.
