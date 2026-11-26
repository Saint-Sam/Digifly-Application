#!/bin/sh
set -eu
state_dir="$HOME/.local/state/digifly-browser"
mkdir -p "$state_dir"
exec 9>"$state_dir/session.lock"
flock -n 9 || exit 0
trap 'kill $(jobs -p) 2>/dev/null || true' EXIT
Xvfb "$DISPLAY" -screen 0 1440x900x24 -nolisten tcp &
display_pid=$!
until xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; do
  kill -0 "$display_pid"
  sleep 0.2
done
openbox &
x11vnc -display "$DISPLAY" -listen 127.0.0.1 -rfbport 5900 -forever -shared -nopw -noxdamage &
websockify --web /opt/digifly-web 6080 127.0.0.1:5900 &
python - <<'PYTHON'
from PySide6.QtCore import QSettings
settings = QSettings("Digifly", "Digifly Workstation")
for key in ("neuron_python", "arbor_python", "bmtk_python"):
    if not settings.contains(key):
        settings.setValue(key, "/opt/digifly-venv/bin/python")
settings.sync()
PYTHON
digifly-workstation
