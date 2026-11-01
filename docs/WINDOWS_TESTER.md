# Digifly Workstation — current Windows tester

Build 20261017.2 (combined neuron lists, working updater, included NEURON and shared SWC libraries)
Windows x86-64 · Arbor deferred

Download the Windows ZIP, right-click it and choose **Extract All**, then open
**Digifly Workstation.exe** in the extracted folder. Keep its DLLs and folders
together. Exit an old copy before opening the new one. Do not merge the new ZIP
over an old installation: keep that folder as a rollback copy. Projects and
the Workstation Workspace remain separate and are not erased by replacement.

This is an unsigned alpha tester. Windows may warn about an unrecognized app.
Check the SHA-256 and GitHub source before choosing to run it; do not disable
antivirus or organization security policy.

## Your MANC connectivity file

The requested `unique_pairs_by_id.parquet` is included and preselected. In
**Data Library**, choose **Use included MANC connectivity Parquet**, then choose
the private Python installed with included NEURON as the data reader (its path
is visible in runtime setup). It includes DuckDB; no extra download is needed.
**Use this Parquet** retains a checksum-verified external copy so app updates
do not remove it. You can also select another existing Parquet with a suitable
reader. The graph
can be registered without SWCs. Every supplied pair, 64-bit ID, weight and
annotation is retained in a lookup index outside the app.

The supplied MANC table's exact release, whole-release completeness and weight
units remain unverified. No SWC corpus, token or saved experiment is bundled.
Supply your separate SWC bundle later. Aggregate weights are not conductances, and this
table does not establish contact locations or simulation readiness. You choose
the custom experimental presets; no new experiment is run here.

## One growing SWC folder per dataset

neuPrint downloads and **Add SWCs to dataset library…** imports join the same
library for the exact server/dataset/release. The visible download location and
**Open SWC folder** control point to
`<Data Library>/swc-libraries/<server>/<release-and-ID>/export_swc/`, containing
`AN`, `DN`, `IN`, `MN`, `SN` and `UNKNOWN`, then type, body ID and SWC file.
Separate searches no longer require separate selectable bundles. You can enter
`DNp01, TTMn, PSI` together in **Neuron names / IDs (list)**, the default bounded
selection. Each name searches type and instance prefixes ignoring case; IDs match
exactly. Preview shows per-name returned counts and warns about missing names or
the total limit. Exact matching and advanced regex remain separate options;
commas inside a regex are literal, not a name list. Review the checked rows, then
download. The original download/import snapshots are retained for
provenance; different releases and changed bytes for an existing body are never
silently merged. Existing saved bundles can be explicitly added through Data
Library without downloading again. Missing family labels stay UNKNOWN.

## Updating this tester

Build20261017.1 and later offer **Check for updates → Install and Restart**,
with a recovery draft, retained previous app and early-startup rollback. Older
download-only editions need one manual Extract All/open of the current build.
The native install/rollback checks use disposable same-build copies and offline
archives; a full live cross-version upgrade and clean Windows11 remain separate
qualification. Keep workspaces/SWCs outside the installation folder.

## Simulator installation and current limits

The included-NEURON edition supplies NEURON9.0.2, private Python3.12.10,
NumPy, Matplotlib and the DuckDB reader offline. In Workspace, open **Find or
install NEURON…**, choose **Install included NEURON9.0.2**, review/accept the
private installation, then **Use selected runtimes**. It expands to your
LocalAppData/Digifly/Runtimes folder, not the app or another installation.
There is no separate download, administrator access, registry/file-association
or global PATH change. Reuse that included Python for the Parquet data reader.
The official installer EXE is retained for provenance; the normal Digifly
button does not execute its registry/association changes. Failed/older
installations are preserved, not overwritten or deleted.

This edition still does not bundle BioNet, NetPyNE or Arbor. Installer/runtime
and a tiny built-in-HH engineering app Run check do not establish arbitrary
custom-channel compilation, MPI, full biological models or cross-OS equivalence.
The interface-only .1 edition requires separately installed scientific runtimes.

As an alternative to the included private runtime, use the [official Windows installer](https://www.neuronsimulator.org/en/latest/install/install_instructions.html)
and compatible, separately installed Python (first target: Python 3.12 x64).
Select that interpreter in Digifly's runtime setup. NetPyNE/BioNet need their
dependencies in the same interpreter. Do not substitute the embedded interface
runtime or silently change a saved selection.

The .1 checks cover native packaging/UI and Parquet with an external reader.
The .2 edition additionally passed actual extracted-app private installation,
runtime selection, a bounded built-in-HH Run and saved Results on WindowsServer2022.
These do **not** certify arbitrary native-channel compilation, full Run/Stop,
Windows11 clean-machine operation, BioNet/NetPyNE/MPI or cross-platform
numerical equivalence. Those remain separate acceptance
work with user-selected experiments. Docker is not required to open the app or
import the graph; no Docker image is built/published here. Arbor is deferred.
