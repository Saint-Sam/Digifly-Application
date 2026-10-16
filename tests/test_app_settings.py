from pathlib import Path

from PySide6.QtCore import QSettings

from digifly_app.core.app_settings import app_settings, SETTINGS_ROOT_ENV


def test_explicit_gui_test_file_is_not_native_preferences(tmp_path, monkeypatch):
    native = QSettings("digifly", "Digifly Workstation")
    before = {key: native.value(key) for key in native.allKeys()}
    monkeypatch.setenv(SETTINGS_ROOT_ENV, str(tmp_path))
    isolated = app_settings("digifly", "Digifly Workstation")
    assert isolated.format() == QSettings.Format.IniFormat
    assert Path(isolated.fileName()).resolve().is_relative_to(tmp_path.resolve())
    assert isolated.fileName() != native.fileName()
    isolated.setValue("output_root", "fixture-only")
    isolated.sync()
    assert app_settings("digifly", "Digifly Workstation").value("output_root") == "fixture-only"
    native.sync()
    assert {key: native.value(key) for key in native.allKeys()} == before
    assert app_settings("digifly", "Digifly App").fileName() != isolated.fileName()


def test_normal_launch_retains_existing_native_preferences(monkeypatch):
    monkeypatch.delenv(SETTINGS_ROOT_ENV, raising=False)
    assert app_settings("digifly", "Digifly Workstation").fileName() == QSettings("digifly", "Digifly Workstation").fileName()
