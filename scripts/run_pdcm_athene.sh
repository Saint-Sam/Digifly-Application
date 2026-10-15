#!/usr/bin/env bash
# Execute one already prepared PDCM bundle inside an Athene Slurm allocation.
# Submission parameters are deliberately external: this script never chooses
# a partition, memory budget, rank count, or walltime on the user's behalf.
set -euo pipefail

: "${SLURM_JOB_ID:?Submit this script with sbatch on a compute node}"
: "${SLURM_NTASKS:?Request an explicit Slurm rank count}"
: "${DIGIFLY_PDCM_RUN:?Set an absolute prepared PDCM run path}"
: "${DIGIFLY_PDCM_WORKER:?Set the absolute path to the matching Digifly MPI worker}"
: "${DIGIFLY_PDCM_LAYER:?Set the absolute path to the isolated NetPyNE layer}"
: "${DIGIFLY_PDCM_PYTHON:?Set an MPI-capable NetPyNE/NEURON Python executable}"
: "${DIGIFLY_PDCM_MPIEXEC:?Set the OpenMPI mpiexec matching this NEURON build}"
: "${DIGIFLY_PDCM_NRNIV:?Set the matching MPI-capable nrniv executable}"
: "${DIGIFLY_PDCM_NRNIVMODL:?Set the matching nrnivmodl executable}"

if [[ ! "$SLURM_NTASKS" =~ ^[0-9]+$ ]] || (( SLURM_NTASKS < 2 )); then
  echo "PDCM Athene jobs require at least two explicit MPI ranks" >&2
  exit 2
fi
for path in "$DIGIFLY_PDCM_RUN" "$DIGIFLY_PDCM_WORKER" "$DIGIFLY_PDCM_LAYER" "$DIGIFLY_PDCM_PYTHON" "$DIGIFLY_PDCM_MPIEXEC" "$DIGIFLY_PDCM_NRNIV" "$DIGIFLY_PDCM_NRNIVMODL"; do
  if [[ "$path" != /* ]]; then
    echo "PDCM Athene paths must be absolute: $path" >&2
    exit 2
  fi
done
for executable in "$DIGIFLY_PDCM_PYTHON" "$DIGIFLY_PDCM_MPIEXEC" "$DIGIFLY_PDCM_NRNIV" "$DIGIFLY_PDCM_NRNIVMODL"; do
  if [[ ! -f "$executable" || ! -x "$executable" ]]; then
    echo "PDCM runtime is missing or not executable: $executable" >&2
    exit 2
  fi
done
if [[ ! -d "$DIGIFLY_PDCM_RUN" || ! -f "$DIGIFLY_PDCM_WORKER" ]]; then
  echo "PDCM prepared run or Digifly MPI worker does not exist" >&2
  exit 2
fi
if [[ ! -d "$DIGIFLY_PDCM_LAYER" || ! -f "$DIGIFLY_PDCM_LAYER/digifly_layer_manifest.json" ]]; then
  echo "The isolated NetPyNE layer is missing or unverified" >&2
  exit 2
fi

run_root="$(cd "$DIGIFLY_PDCM_RUN" && pwd -P)"
model_root="$run_root/model"
worker="$DIGIFLY_PDCM_WORKER"
if [[ ! -f "$run_root/worker_request.json" || ! -f "$run_root/bundle_manifest.json" ]]; then
  echo "PDCM prepared bundle or MPI worker is incomplete" >&2
  exit 2
fi
if [[ ! -f "$model_root/IntFire_PD.mod" ]]; then
  echo "PDCM model mechanism is missing" >&2
  exit 2
fi

unset PYTHONPATH PYTHONHOME
export PYTHONPATH="$DIGIFLY_PDCM_LAYER"
export PYTHONNOUSERSITE=1
export PYTHONDONTWRITEBYTECODE=1
export NEURON_MODULE_OPTIONS=-nogui
export MPLBACKEND=Agg
export MPLCONFIGDIR="$run_root/matplotlib"
export DIGIFLY_PDCM_REQUEST="$run_root/worker_request.json"
export DIGIFLY_PDCM_EXPECTED_RANKS="$SLURM_NTASKS"
mkdir -p "$MPLCONFIGDIR"

# Verify the whole transferred bundle before compiling or claiming a job.
"$DIGIFLY_PDCM_PYTHON" -B "$worker" --validate-only --request "$DIGIFLY_PDCM_REQUEST"
# The worker validates again on every rank. This import probe fails early if
# the selected Python lacks the native scientific runtime.
"$DIGIFLY_PDCM_PYTHON" -B -c 'import netpyne, neuron; print("NetPyNE", netpyne.__version__, "NEURON", neuron.__version__)'

# A lock is intentionally retained after failure: retries require a new run
# name, preserving failed artifacts and eliminating accidental double-submit.
if ! mkdir "$run_root/.athene_job_claim" 2>/dev/null; then
  echo "This PDCM bundle was already claimed by an Athene job" >&2
  exit 3
fi

cd "$model_root"
"$DIGIFLY_PDCM_NRNIVMODL" IntFire_PD.mod > "$run_root/nrnivmodl.log" 2>&1
"$DIGIFLY_PDCM_MPIEXEC" -n "$SLURM_NTASKS" "$DIGIFLY_PDCM_NRNIV" -python -mpi -nogui "$worker" > "$run_root/mpi.log" 2>&1
if [[ ! -f "$run_root/summary.json" ]]; then
  echo "PDCM MPI worker finished without a Results summary" >&2
  exit 4
fi
echo "PDCM MPI run complete: $run_root/summary.json"
