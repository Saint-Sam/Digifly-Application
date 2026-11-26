#!/bin/bash
set -euo pipefail
state_dir="$HOME/.local/state/digifly-browser"
mkdir -p "$state_dir"
exec 9>"$state_dir/session.lock"
flock -n 9 || exit 0
exec > >(tee -a "$state_dir/session.log") 2>&1
children=()
cleanup() {
  if (("${#children[@]}")); then
    kill "${children[@]}" 2>/dev/null || true
    wait || true
  fi
}
trap cleanup EXIT
trap 'exit 0' TERM INT
printf 'Starting DigiFly Workstation on display %s, HTTP port 6080\n' "$DISPLAY"
Xvfb "$DISPLAY" -screen 0 1440x900x24 -nolisten tcp &
display_pid=$!
children+=("$display_pid")
until xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; do
  kill -0 "$display_pid"
  sleep 0.2
done
openbox &
children+=("$!")
x11vnc -display "$DISPLAY" -listen 127.0.0.1 -rfbport 5900 -forever -shared -nopw -noxdamage &
children+=("$!")
websockify --web /opt/digifly-web 6080 127.0.0.1:5900 &
children+=("$!")
python - <<'PYTHON'
from PySide6.QtCore import QSettings
settings = QSettings("Digifly", "Digifly Workstation")
for key in ("neuron_python", "arbor_python", "bmtk_python"):
    if not settings.contains(key):
        settings.setValue(key, "/opt/digifly-venv/bin/python")
settings.sync()
PYTHON
digifly-workstation &
children+=("$!")
status=0
wait -n "${children[@]}" || status=$?
printf 'A DigiFly desktop process exited with status %d\n' "$status"
exit "$status"
