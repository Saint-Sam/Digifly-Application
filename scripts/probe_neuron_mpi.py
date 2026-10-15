"""Minimal Slurm/NEURON MPI diagnostic; no scientific model is loaded."""

from __future__ import annotations

import os

from neuron import h


h.nrnmpi_init()
pc = h.ParallelContext()
expected = int(os.environ["DIGIFLY_PDCM_EXPECTED_RANKS"])
actual = int(pc.nhost())
rank = int(pc.id())
print(f"Digifly NEURON MPI probe: rank={rank} ranks={actual} expected={expected}", flush=True)
if actual != expected:
    raise RuntimeError(f"NEURON initialized {actual} ranks, expected {expected}")
pc.barrier()
