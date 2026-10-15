# Digifly Workstation — current Windows tester

Build 20261015.1 · Windows x86-64 · Arbor deferred

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
