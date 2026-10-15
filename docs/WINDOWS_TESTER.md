# Digifly Workstation — current Windows tester

Builds 20261015.1 (interface-only) / 20261015.2 (included NEURON edition)
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

Copy your `unique_pairs_by_id.parquet` to the PC separately. In **Data Library**,
choose **Use connectivity Parquet**, select that file, and choose a data-reader
Python with DuckDB installed. A separate Python 3.12 x64 with `python -m pip
install duckdb` is suitable; this is not a simulator environment. The graph
can be registered without SWCs. Every supplied pair, 64-bit ID, weight and
annotation is retained in a lookup index outside the app.

No connectome, SWC corpus, token or saved experiment is bundled. Supply your
separate SWC bundle later. Aggregate weights are not conductances, and this
table does not establish contact locations or simulation readiness. You choose
the custom experimental presets; no new experiment is run here.

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

For native NEURON use the [official Windows installer](https://www.neuronsimulator.org/en/latest/install/install_instructions.html)
and compatible, separately installed Python (first target: Python 3.12 x64).
Select that interpreter in Digifly's runtime setup. NetPyNE/BioNet need their
dependencies in the same interpreter. Do not substitute the embedded interface
runtime or silently change a saved selection.

Release checks cover native Windows packaging, the actual UI and the Parquet
feature with a software fixture. They do **not** certify NEURON installer,
compiler/DLL, simulator Run/Stop/Results, Windows 11 clean-machine operation,
MPI or cross-platform numerical equivalence. Those remain separate acceptance
work with user-selected experiments. Docker is not required to open the app or
import the graph; no Docker image is built/published here. Arbor is deferred.
