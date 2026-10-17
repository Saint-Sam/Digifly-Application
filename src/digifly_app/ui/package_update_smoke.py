"""Disposable native packaged-app updater exercise; no live release or science."""
from __future__ import annotations

import json
import os
from pathlib import Path
import traceback


def run_update_smoke(destination: Path, archive: Path, *, fail_startup=False) -> int:
    from digifly_app.core.app_settings import SETTINGS_ROOT_ENV
    from digifly_app.core.resource_profile import PROFILE_PATH_ENV, make_standalone_profile
    from digifly_app.core.circuit import CircuitSpec
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox
    from digifly_app.ui import main_window as ui
    from digifly_app.core import updates

    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=False)
    profile_path = destination / "workspace/config/resources.json"
    profile = make_standalone_profile(workstation_root=destination / "workspace")
    profile.save(profile_path)
    os.environ[PROFILE_PATH_ENV] = str(profile_path)
    os.environ[SETTINGS_ROOT_ENV] = str(destination / "settings")
    app = QApplication.instance() or QApplication([])
    window = ui.MainWindow()
    window.circuit_builder_page.set_circuit_spec(CircuitSpec())
    # No authenticated request, custom scientific preset or simulation. Native
    # controls and workers exercise selection -> download result -> preparation
    # -> close -> external helper -> replacement -> actual MainWindow reopen.
    asset = updates.UpdateAsset(archive.name, "offline engineering fixture", "offline checksum", "")
    ui.check_for_update = lambda: updates.UpdateStatus(True, asset, "Explicit packaged updater check")
    ui.download_verified_update = lambda selected: archive.resolve()
    ui.update_cache_root = lambda: destination / "cache"
    QMessageBox.question = lambda *a, **kw: QMessageBox.StandardButton.Yes
    def failed(parent, title, text, *a, **kw):
        (destination / "ui-failure.json").write_text(json.dumps({"title": title, "error": text}), "utf-8")
        QTimer.singleShot(0, app.quit)
        return QMessageBox.StandardButton.Ok
    QMessageBox.warning = QMessageBox.critical = failed
    original = window._update_prepared
    def prepared(result):
        request_path = Path(result)
        request = json.loads(request_path.read_text("utf-8"))
        request["engineering_test"] = "exit-before-startup" if fail_startup else "close-after-startup"
        request_path.write_text(json.dumps(request, indent=2), "utf-8")
        (destination / "transaction.json").write_text(json.dumps({"request": str(request_path)}), "utf-8")
        original(result)
    window._update_prepared = prepared
    window.show()
    QTimer.singleShot(500, window.update_button.click)
    QTimer.singleShot(240000, app.quit)
    code = app.exec()
    return 1 if (destination / "ui-failure.json").exists() else code
