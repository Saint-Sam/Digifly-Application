"""The fixed sidebar identifies the running package on every page."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint
from PySide6.QtWidgets import QApplication, QMessageBox

from digifly_app.core.app_settings import SETTINGS_ROOT_ENV
from digifly_app.core.resource_profile import PROFILE_PATH_ENV
from digifly_app.ui import main_window
from digifly_app.ui.style import DARK_THEME, LIGHT_THEME


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setenv(SETTINGS_ROOT_ENV, str(tmp_path / "settings"))
    monkeypatch.setenv(PROFILE_PATH_ENV, str(tmp_path / "no-machine-profile.json"))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    dialogs = []
    def record_warning(parent, title, message, *args, **kwargs):
        dialogs.append((title, message))
        return QMessageBox.StandardButton.Ok
    monkeypatch.setattr(QMessageBox, "warning", record_warning)
    monkeypatch.setattr(QMessageBox, "critical", record_warning)
    app = QApplication.instance() or QApplication([])
    created = []

    def create(metadata):
        monkeypatch.setattr(main_window, "build_info", lambda: metadata, raising=False)
        result = main_window.MainWindow()
        created.append(result)
        result.footer_test_dialogs = dialogs
        result.show()
        app.processEvents()
        result.first_experiment_guide.pause()
        return result, app

    yield create
    from digifly_app.ui.package_library_smoke import dispose_fixture_window
    for result in created:
        result.close()
        dispose_fixture_window(result, app)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("version", ["0.2.0", "0.2.1", "0.3.12"])
def test_footer_uses_running_build_instead_of_library_version(window, version):
    ui, app = window({"version": version, "build": "1"})
    assert ui.version_label.text() == f"Version {version}"
    assert app.applicationVersion() == version
    assert ui.version_label.accessibleName() == "Application version"
    assert ui.version_label.parentWidget().objectName() == "Sidebar"


def test_footer_stays_bottom_left_on_all_pages_and_status_messages(window):
    ui, app = window({"version": "0.2.1", "build": "1"})
    for theme in (LIGHT_THEME, DARK_THEME):
        ui._apply_theme(theme, persist=False)
        for width, height in ((1440, 940), (1120, 760)):
            ui.resize(width, height)
            for index, button in enumerate(ui.nav_buttons):
                button.click()
                ui.statusBar().showMessage("A temporary operation is in progress")
                app.processEvents()
                label = ui.version_label
                position = label.mapTo(ui, QPoint(0, 0))
                assert ui.pages.currentIndex() == index
                assert label.isVisible() and label.text() == "Version 0.2.1"
                assert 0 <= position.x() < 225
                assert ui.height() - 110 < position.y() < ui.height()
                assert label.visibleRegion().boundingRect().contains(label.rect())
    assert not ui.footer_test_dialogs


def test_missing_build_metadata_is_honest_and_clears_stale_qt_version(window):
    ui, app = window({})
    assert ui.version_label.text() == "Development build"
    assert app.applicationVersion() == ""


def test_native_check_refuses_a_stale_label(window, monkeypatch, tmp_path):
    from digifly_app.core import updates
    from digifly_app.ui.package_library_smoke import check_version_footer
    metadata = {"version": "0.2.1", "build": "1"}
    ui, app = window(metadata)
    monkeypatch.setattr(updates, "build_info", lambda: metadata)
    ui.version_label.setText("Version 0.1.0")
    with pytest.raises(ValueError, match="running build label"):
        check_version_footer(ui, app, tmp_path)
    assert not (tmp_path / "version-footer.json").exists()
