#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "$0")/.." && pwd)"
bootstrap_python="${DIGIFLY_BOOTSTRAP_PYTHON:-/opt/anaconda3/bin/python}"
"$bootstrap_python" -m venv "$project_dir/.venv"
"$project_dir/.venv/bin/python" -m pip install -e "$project_dir[test]"
"$project_dir/.venv/bin/python" -c 'from PySide6.QtCore import qVersion; print("Qt", qVersion(), "is ready")'
