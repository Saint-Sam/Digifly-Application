"""Disposable native packaged-app updater exercise; no live release or science."""
from __future__ import annotations

import json
import os
import sys
import shutil
from pathlib import Path
import traceback
import hashlib


def run_update_smoke(destination: Path, archive: Path, *, fail_startup=False, legacy_request=False) -> int:
    from digifly_app.core.app_settings import SETTINGS_ROOT_ENV
    from digifly_app.core.resource_profile import PROFILE_PATH_ENV, make_standalone_profile
    from digifly_app.core.circuit import CircuitSpec
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox
    from digifly_app.ui import main_window as ui
    from digifly_app.core import updates

    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=False)
    from digifly_app.core.update_installer import running_executable, installation_root, installed_build_info
    metadata = updates.build_info()
    installed = installed_build_info(installation_root(), __import__('platform').system())
    if not metadata or metadata != installed:
        raise ValueError("Packaged updater build metadata is missing or differs from the installed app.")
    # Reproduce the actual historical public catalog without replacing the
    # packaged build reader. A newer upload timestamp is deliberately hostile.
    fragment = updates.platform_asset_fragment()
    name = 'Digifly-Workstation-0.1.0-alpha.1-' + fragment
    read = updates._read_json
    updates._read_json = lambda url: {"assets": [
        {"name": name, "browser_download_url": "offline", "updated_at": "2099-01-01T00:00:00Z"},
        {"name": name + '.sha256', "browser_download_url": "offline"}]}
    try:
        result = updates.check_for_update()
        if result.available:
            raise ValueError("Actual packaged metadata offered a historical downgrade.")
    finally:
        updates._read_json = read
    (destination / "metadata-safety.json").write_text(json.dumps({
        "ok": True, "build_info": metadata, "actual_build_reader_used": True,
        "historical_catalog_refused": True}), "utf-8")
    (destination / "runtime-context.json").write_text(json.dumps({
        "runtime_executable": sys.executable, "loaded_module_executable": str(running_executable()),
        "argv0": sys.argv[0]}, indent=2), "utf-8")
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
    expected_digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    asset = updates.UpdateAsset(archive.name,
        'https://github.com/Saint-Sam/Digifly-Application/releases/download/fixture/' + archive.name,
        ('https://github.com/Saint-Sam/Digifly-Application/releases/download/fixture/' + archive.name + '.sha256') if legacy_request else '',
        '', digest='sha256:' + expected_digest)
    ui.check_for_update = lambda: updates.UpdateStatus(True, asset, "Explicit packaged updater check")
    def transfer(url, path, *, maximum):
        if legacy_request and url == asset.checksum_url:
            path.write_text(expected_digest + '  ' + archive.name, 'ascii')
            return
        if url != asset.url or archive.stat().st_size > maximum:
            raise ValueError('Unexpected updater transfer; no checksum file is supplied')
        shutil.copyfile(archive, path)
    updates._download = transfer
    updates.update_cache_root = lambda: destination / 'cache'
    real_download = updates.download_verified_update
    def download(selected):
        saved = real_download(selected)
        result = {'ok': hashlib.sha256(saved.read_bytes()).hexdigest() == expected_digest,
            'archive_sha256': expected_digest, 'github_asset_digest': selected.digest,
            'legacy_checksum_fixture': legacy_request,
            'checksum_file_requested': legacy_request, 'checksum_file_created': bool(list(saved.parent.glob('*.sha256'))),
            'actual_verifier_used': True}
        (destination / 'digest-download.json').write_text(json.dumps(result), 'utf-8')
        return saved
    ui.download_verified_update = download
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
        if legacy_request:
            request.pop("cleanup", None)  # same journal fields as the shipped old updater
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
