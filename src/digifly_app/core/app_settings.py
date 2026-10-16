"""App preferences, with explicit file isolation for engineering GUI checks."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from PySide6.QtCore import QSettings

SETTINGS_ROOT_ENV = "DIGIFLY_WORKSTATION_SETTINGS_ROOT"


def app_settings(organization: str, application: str) -> QSettings:
    root = os.environ.get(SETTINGS_ROOT_ENV, "").strip()
    if not root:
        return QSettings(organization, application)
    key = hashlib.sha256(f"{organization}\n{application}".encode()).hexdigest()[:16]
    path = Path(root).expanduser().resolve() / f"preferences-{key}.ini"
    # QSettings(org, app) always selects NativeFormat. setDefaultFormat/setPath
    # do not isolate that overload on macOS or Windows. Use a file explicitly.
    return QSettings(str(path), QSettings.Format.IniFormat)
