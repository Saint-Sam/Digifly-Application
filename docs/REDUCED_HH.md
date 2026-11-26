# Reduced Hodgkin–Huxley simulation

Experiment Builder also offers **Reduced HH**. It uses classic HH at grouped
cable states and keeps the mapped soma, axial paths, and chemical contact
locations. Neighboring edges can share a state when their membrane
parameters match and the total grouped cable length is at most 0.125 µm.
Use the usual mapped stimulus and recording regions. All-compartment
playback shows the state assigned to each edge; edges in one group share
their voltage trace.

Reduced HH uses the configured NumPy-capable simulator Python, with optional
Numba or SciPy acceleration. It currently requires chemical delays aligned
to the integration step and a single solver segment per SWC edge. Use
NEURON for gap junctions, current recording and native channel mechanisms.
Readiness reports settings that need to change.

The solver is in `src/digifly_app/workers/reduced_hh_solver.py`.
