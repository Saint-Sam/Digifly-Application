#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "$0")/.." && pwd)"
bootstrap_python="${DIGIFLY_BOOTSTRAP_PYTHON:-}"
if [[ -z "$bootstrap_python" ]]; then
  for candidate in python3 python /opt/anaconda3/bin/python; do
    if candidate_path="$(command -v "$candidate" 2>/dev/null)" && \
      "$candidate_path" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
      bootstrap_python="$candidate_path"
      break
    fi
  done
fi
if [[ -z "$bootstrap_python" || ! -x "$bootstrap_python" ]]; then
  echo "Python 3.11 or newer is required. Set DIGIFLY_BOOTSTRAP_PYTHON to its executable path." >&2
  exit 2
fi
if ! "$bootstrap_python" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
  echo "DIGIFLY_BOOTSTRAP_PYTHON must point to Python 3.11 or newer: $bootstrap_python" >&2
  exit 2
fi
echo "Creating the development environment with $bootstrap_python"
"$bootstrap_python" -m venv "$project_dir/.venv"
"$project_dir/.venv/bin/python" -m pip install "setuptools>=68" wheel
"$project_dir/.venv/bin/python" -m pip install -e "$project_dir[test,deploy,package]"
"$project_dir/.venv/bin/python" -c 'from PySide6.QtCore import qVersion; print("Qt", qVersion(), "is ready")'
