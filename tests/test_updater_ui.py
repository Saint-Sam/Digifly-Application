from __future__ import annotations
import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QMessageBox
from digifly_app.core.app_settings import SETTINGS_ROOT_ENV
from digifly_app.core.project import DigiflyProject
from digifly_app.ui.main_window import MainWindow


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setenv(SETTINGS_ROOT_ENV, str(tmp_path / "settings"))
    monkeypatch.setenv("DIGIFLY_WORKSTATION_PROFILE", str(tmp_path / "unconfigured.json"))
    app = QApplication.instance() or QApplication([])
    widget = MainWindow()
    yield widget
    widget.close()
    widget.deleteLater()
    app.processEvents()


def test_active_science_prevents_install_preparation(window, monkeypatch):
    monkeypatch.setattr(window, "_update_busy", lambda: True)
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a: shown.append(a[2]))
    monkeypatch.setattr(window, "_save_update_draft", lambda *a: pytest.fail("active work must not stage installation"))
    window._update_download_finished("verified.zip")
    assert "Finish or safely stop" in shown[0]


def test_install_decline_keeps_window_and_draft_untouched(window, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.No)
    monkeypatch.setattr(window, "_save_update_draft", lambda *a: pytest.fail("declined install must not write"))
    window._update_download_finished("verified.zip")
    assert window.update_button.isEnabled()


def test_recovery_copy_preserves_original_project_and_current_path(window, tmp_path):
    original = tmp_path / "original.digifly.json"
    original.write_text("do not overwrite original")
    window.current_project_path = original
    destination = tmp_path / "draft.digifly.json"
    window._save_update_draft(destination)
    draft = DigiflyProject.load(destination)
    assert draft.circuit == window.circuit_builder_page.circuit_spec().to_dict()
    assert draft.experiment == json.loads(json.dumps(window.experiment_page.config().to_dict()))
    assert original.read_text() == "do not overwrite original"
    assert window.current_project_path == original


def test_close_does_not_destroy_a_preparing_update_thread(window, monkeypatch):
    class Running:
        def isRunning(self): return True
    window._update_worker = Running()
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: None)
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted()
    window._update_worker = None


def test_preparation_worker_preserves_reviewed_digest(window, monkeypatch, tmp_path):
    from digifly_app.ui import main_window as ui
    calls = []
    monkeypatch.setattr(ui, 'prepare_install', lambda *args, **kwargs: calls.append((args, kwargs)))
    values = tuple(tmp_path / name for name in ('archive.zip', 'target', 'transaction', 'draft'))
    worker = ui.UpdatePreparationWorker(*values, window, expected_sha256='a' * 64)
    worker.run()
    assert calls == [(values, {'expected_sha256': 'a' * 64})]


def test_download_dialog_carries_asset_digest_to_install_worker(window, monkeypatch, tmp_path):
    from digifly_app.ui import main_window as ui
    class Signal:
        def connect(self, callback): pass
    class Worker:
        succeeded = failed = finished = Signal()
        def __init__(self, *args, **kwargs): self.kwargs = kwargs
        def deleteLater(self): pass
        def start(self): pass
    monkeypatch.setattr(ui, 'UpdateWorker', Worker)
    monkeypatch.setattr(ui, 'UpdatePreparationWorker', Worker)
    monkeypatch.setattr(QMessageBox, 'question', lambda *a: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(window, '_update_busy', lambda: False)
    monkeypatch.setattr(window, '_save_update_draft', lambda path: path.write_text('separate draft'))
    monkeypatch.setattr(ui, 'installation_root', lambda: tmp_path / 'installed')
    monkeypatch.setattr(ui, 'update_cache_root', lambda: tmp_path / 'cache')
    asset = ui.UpdateAsset('app.zip', 'official', '', '', digest='sha256:' + 'b' * 64)
    window._update_check_finished(ui.UpdateStatus(True, asset, 'test'))
    window._update_download_finished(tmp_path / 'app.zip')
    assert window._update_worker.kwargs == {'expected_sha256': 'b' * 64}
    window._update_worker = None
