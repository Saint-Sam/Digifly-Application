from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from digifly_app.core.data_library import import_local_source, list_managed_resources
from digifly_app.core.resource_profile import (
    PROFILE_PATH_ENV,
    ResourceProfile,
    make_default_profile,
)
from digifly_app.ui.data_library import DataLibraryPage
from digifly_app.ui.resource_management import ManifestDialog, TrashDialog


def _library(tmp_path: Path):
    workspace = tmp_path / "Digifly Public"
    workspace.mkdir()
    (workspace / "README.md").write_text("Digifly Public\n", encoding="utf-8")
    profile = make_default_profile(
        workspace_root=workspace,
        output_root=tmp_path / "runs",
        managed_data_root=tmp_path / "data",
    )
    profile_path = profile.save(tmp_path / "resources-v2.json")
    source = tmp_path / "source"
    source.mkdir()
    (source / "cell.swc").write_text("1 1 0 0 0 1 -1\n", encoding="utf-8")
    import_local_source(
        profile,
        source,
        provider="local",
        resource_id="ui-resource",
        source_version="v1",
        profile_path=profile_path,
    )
    return profile_path


def test_data_library_selected_resource_controls_toggle_and_soft_delete(
    tmp_path: Path,
    monkeypatch,
):
    application = QApplication.instance() or QApplication([])
    profile_path = _library(tmp_path)
    monkeypatch.setenv(PROFILE_PATH_ENV, str(profile_path))
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    page = DataLibraryPage()
    try:
        assert page.table.rowCount() == 1
        assert page.table.item(0, 3).text() == "Registered"
        page.table.selectRow(0)
        application.processEvents()
        assert page.inspect_button.isEnabled()
        assert page.registration_button.text() == "Unregister"
        assert page.trash_button.isEnabled()

        page.toggle_registration()
        application.processEvents()
        assert page.table.item(0, 3).text() == "Stored only"
        page.table.selectRow(0)
        page.toggle_registration()
        application.processEvents()
        assert page.table.item(0, 3).text() == "Registered"

        page.table.selectRow(0)
        page.trash_selected()
        application.processEvents()
        assert page.table.rowCount() == 0
    finally:
        page.close()
        application.processEvents()


def test_manifest_and_trash_dialogs_render_and_restore(tmp_path: Path, monkeypatch):
    application = QApplication.instance() or QApplication([])
    profile_path = _library(tmp_path)
    profile = ResourceProfile.load(profile_path)
    resource = list_managed_resources(profile)[0]
    manifest = ManifestDialog(resource)
    try:
        assert "ui-resource" in manifest.windowTitle()
        assert '"resource_id": "ui-resource"' in manifest.raw.toPlainText()
        assert manifest.raw.isReadOnly()
    finally:
        manifest.close()

    from digifly_app.core.resource_management import trash_managed_resource

    trash_managed_resource(profile, resource, profile_path=profile_path)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    trash = TrashDialog(ResourceProfile.load(profile_path), profile_path)
    try:
        assert trash.table.rowCount() == 1
        trash.table.selectRow(0)
        application.processEvents()
        assert trash.restore_button.isEnabled()
        trash.restore_selected()
        application.processEvents()
        assert trash.table.rowCount() == 0
        assert list_managed_resources(ResourceProfile.load(profile_path))[0].resource_id == "ui-resource"
    finally:
        trash.close()
        application.processEvents()
