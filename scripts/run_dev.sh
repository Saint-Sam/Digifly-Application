#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="$project_dir/src${PYTHONPATH:+:$PYTHONPATH}"
default_ui_python="$project_dir/.venv/bin/python"
if [[ ! -x "$default_ui_python" ]]; then
  default_ui_python="python3"
fi
exec "${DIGIFLY_APP_PYTHON:-$default_ui_python}" -m digifly_app "$@"
