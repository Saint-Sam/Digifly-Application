#!/usr/bin/env bash
# Create a one-time NetPyNE overlay beside an existing MPI-enabled NEURON venv.
# This script must run in a Slurm compute allocation, never on the login node.
set -euo pipefail

: "${SLURM_JOB_ID:?Submit this installer in a Slurm compute allocation}"
: "${DIGIFLY_PDCM_BASE_PYTHON:?Set the existing NEURON environment Python}"
: "${DIGIFLY_PDCM_LAYER:?Set a new absolute scratch path for the package layer}"
if [[ "$DIGIFLY_PDCM_BASE_PYTHON" != /* || "$DIGIFLY_PDCM_LAYER" != /* ]]; then
  echo "PDCM installer paths must be absolute" >&2
  exit 2
fi
if [[ ! -x "$DIGIFLY_PDCM_BASE_PYTHON" ]]; then
  echo "The selected NEURON Python is not executable" >&2
  exit 2
fi
if [[ -e "$DIGIFLY_PDCM_LAYER" ]]; then
  echo "The PDCM package layer already exists; never mutate it in place" >&2
  exit 3
fi

export PYTHONNOUSERSITE=1
export NEURON_MODULE_OPTIONS=-nogui
export MPLBACKEND=Agg
unset PYTHONPATH PYTHONHOME
"$DIGIFLY_PDCM_BASE_PYTHON" -B -c 'import neuron, numpy; print("Base NEURON", neuron.__version__, "NumPy", numpy.__version__)'
mkdir -p "$(dirname "$DIGIFLY_PDCM_LAYER")"
mkdir "$DIGIFLY_PDCM_LAYER"
export MPLCONFIGDIR="$DIGIFLY_PDCM_LAYER/matplotlib"
mkdir "$MPLCONFIGDIR"

# Resolve no transitive dependencies: this layer may not replace the known-good
# NEURON/NumPy/MPI stack. The listed missing pure-Python dependencies are pinned.
"$DIGIFLY_PDCM_BASE_PYTHON" -m pip install \
  --target "$DIGIFLY_PDCM_LAYER" --no-deps --no-cache-dir \
  'netpyne==1.1.1' 'future==1.0.0' 'schema==0.7.8' \
  'matplotlib-scalebar==0.9.0' 'LFPykit==0.5.1' 'MEAutility==1.5.3' \
  'bokeh==3.4.3' 'Jinja2==3.1.6' 'MarkupSafe==3.0.3' \
  'tornado==6.5.10' 'xyzservices==2026.9.1'

export PYTHONPATH="$DIGIFLY_PDCM_LAYER"
"$DIGIFLY_PDCM_BASE_PYTHON" -B - <<'PY'
import importlib.metadata as metadata
import json
import os
from pathlib import Path

import netpyne
import neuron
import numpy
from netpyne import sim  # noqa: F401 - the model's required import

layer = Path(os.environ["DIGIFLY_PDCM_LAYER"])
packages = sorted(
    (dist.metadata["Name"], dist.version)
    for dist in metadata.distributions(path=[str(layer)])
)
manifest = {
    "schema_version": 1,
    "purpose": "isolated NetPyNE layer for Digifly PDCM on FAU Athene",
    "base_python": os.environ["DIGIFLY_PDCM_BASE_PYTHON"],
    "neuron": neuron.__version__,
    "neuron_file": neuron.__file__,
    "numpy": numpy.__version__,
    "numpy_file": numpy.__file__,
    "netpyne": netpyne.__version__,
    "netpyne_file": netpyne.__file__,
    "layer_packages": packages,
}
(layer / "digifly_layer_manifest.json").write_text(
    json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
print(json.dumps({"neuron": manifest["neuron"], "numpy": manifest["numpy"], "netpyne": manifest["netpyne"]}), flush=True)
PY
