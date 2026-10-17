from __future__ import annotations

import os
import time
import pytest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLineEdit
from PySide6.QtTest import QTest

from digifly_app.core.neuprint import NeuPrintDataset, NeuPrintNeuron, NeuPrintSelection
from digifly_app.core.resource_profile import make_default_profile
from digifly_app.ui.neuprint_import import NeuPrintImportDialog
from digifly_app.core.credentials import token_from_workspace, workspace_token_path


class _NoCredentialStore:
    available = False


def test_repeated_background_tasks_finish_without_deleting_worker_in_its_thread(tmp_path):
    app = QApplication.instance() or QApplication([])
    profile = make_default_profile(workspace_root=tmp_path, output_root=tmp_path / "runs",
                                    managed_data_root=tmp_path / "library")
    path = profile.save(tmp_path / "profile.json")
    dialog = NeuPrintImportDialog(profile, path, credential_store=_NoCredentialStore())
    results = []
    try:
        for value in range(12):
            dialog._start_operation("fixture", lambda value=value: value, results.append)
            deadline = time.monotonic() + 3
            while dialog.operation_in_progress and time.monotonic() < deadline:
                QTest.qWait(5)
                time.sleep(0.005)
            assert not dialog.operation_in_progress
        assert results == list(range(12))
    finally:
        dialog.close()
        app.processEvents()


def test_workspace_token_reader_uses_exact_legacy_name_and_normalizes_json(tmp_path):
    path = workspace_token_path(tmp_path)
    path.parent.mkdir()
    path.write_text('{"token": "fixture-secret"}')
    assert path.name == "Neuprint Token.txt"
    assert token_from_workspace(tmp_path, "https://neuprint.janelia.org") == "fixture-secret"
    assert path.read_text() == '{"token": "fixture-secret"}'


@pytest.mark.parametrize("server", ["https://other.example", "http://neuprint.janelia.org",
                                    "https://neuprint.janelia.org.evil.example"])
def test_workspace_token_is_never_used_for_a_different_origin(tmp_path, server):
    with pytest.raises(ValueError, match="only used with"):
        token_from_workspace(tmp_path, server)


def test_missing_workspace_token_reports_safe_error(tmp_path):
    with pytest.raises(ValueError, match="Could not read"):
        token_from_workspace(tmp_path, "https://neuprint.janelia.org")


def test_dialog_discovers_file_without_exposing_or_copying_token(tmp_path):
    application = QApplication.instance() or QApplication([])
    workspace = tmp_path / "Digifly Public"
    path = workspace_token_path(workspace)
    path.parent.mkdir(parents=True)
    path.write_text("fixture-private-secret")
    profile = make_default_profile(workspace_root=workspace, output_root=tmp_path / "runs",
                                   managed_data_root=tmp_path / "library")
    profile_path = profile.save(tmp_path / "resources-v2.json")
    dialog = NeuPrintImportDialog(profile, profile_path, credential_store=_NoCredentialStore())
    try:
        assert dialog.workspace_token.isChecked()
        assert dialog.workspace_token.isEnabled()
        assert dialog.token_edit.text() == ""
        assert dialog._active_token == ""
        assert dialog._token_for_connection() == "fixture-private-secret"
        assert "fixture-private-secret" not in profile_path.read_text()
        assert "fixture-private-secret" not in dialog.status.text()
        dialog.token_edit.setText("explicit-manual-secret")
        assert dialog._token_for_connection() == "explicit-manual-secret"
        dialog.token_edit.clear()
        dialog.server_edit.setText("https://other.example")
        with pytest.raises(ValueError, match="only used with"):
            dialog._token_for_connection()
    finally:
        dialog.close()
        application.processEvents()


def test_neuprint_dialog_exposes_credentials_selection_naming_and_destination(tmp_path: Path):
    application = QApplication.instance() or QApplication([])
    workspace = tmp_path / "Digifly Public"
    workspace.mkdir()
    profile = make_default_profile(
        workspace_root=workspace,
        output_root=tmp_path / "runs",
        managed_data_root=tmp_path / "library",
    )
    profile_path = profile.save(tmp_path / "resources-v2.json")
    dialog = NeuPrintImportDialog(
        profile,
        profile_path,
        credential_store=_NoCredentialStore(),
    )
    try:
        assert dialog.token_edit.echoMode() == QLineEdit.EchoMode.Password
        assert dialog.selection_mode.count() == 7
        assert dialog.selection_mode.currentData() == "name_list"
        assert dialog.limit_spin.maximum() == 500
        assert dialog.connectivity_check.isChecked()
        assert not dialog.remember_token.isEnabled()
        dialog._active_token = "fixture-token"
        dialog.dataset_combo.addItem("manc:v1.2.1", "manc:v1.2.1")
        dialog.dataset_combo.setEnabled(True)
        dialog.selection_mode.setCurrentIndex(dialog.selection_mode.findData("type_exact"))
        dialog.selection_edit.setText("DNp01")
        dialog._preview_ready((NeuPrintNeuron(42, "DNp01", "DNp01_R"),))
        application.processEvents()

        assert dialog.preview_table.rowCount() == 1
        assert dialog.download_button.isEnabled()
        assert str(profile.managed_data_root) in dialog.destination_label.text()
        assert "neuprint.janelia.org" in dialog.destination_label.text()
        assert "dnp01" in dialog.destination_label.text()
        assert dialog.snapshot_edit.text().casefold() in dialog.destination_label.text().casefold()
        assert dialog._request().include_connectivity
    finally:
        dialog.close()
        application.processEvents()


def test_default_list_preview_reports_each_name_instance_matches_and_missing_terms(tmp_path):
    app = QApplication.instance() or QApplication([])
    profile = make_default_profile(workspace_root=tmp_path, output_root=tmp_path / "runs",
                                   managed_data_root=tmp_path / "library")
    path = profile.save(tmp_path / "profile.json")
    dialog = NeuPrintImportDialog(profile, path, credential_store=_NoCredentialStore())
    try:
        dialog._active_token = "fixture-token"
        dialog.dataset_combo.addItem("manc:v1.0", "manc:v1.0")
        dialog.selection_edit.setText("DNp01, TTMn, PSI, absent")
        dialog._preview_ready((NeuPrintNeuron(42, "DNp01", "DNp01_R"),
                               NeuPrintNeuron(43, "TTMn"), NeuPrintNeuron(45, "", "PSI_L")))
        assert dialog._request().selection.mode == "name_list"
        assert [n.body_id for n in dialog._request().neurons] == [42, 43, 45]
        assert "DNp01: 1; TTMn: 1; PSI: 1; absent: 0" in dialog.status.text()
        assert "No returned match for: absent" in dialog.status.text()
        assert "type or instance" in dialog.selection_help.text()
        dialog.limit_spin.setValue(3)
        dialog.selection_edit.setText("DNp01, TTMn, PSI")
        dialog._preview_ready((NeuPrintNeuron(42, "DNp01"), NeuPrintNeuron(43, "TTMn"),
                               NeuPrintNeuron(45, "", "PSI_L")))
        assert "Query reached its limit" in dialog.status.text()
        dialog._set_busy(True)
        assert not dialog.selection_edit.isEnabled() and not dialog.dataset_combo.isEnabled()
        assert not dialog.selection_mode.isEnabled()
        dialog._set_busy(False)
        assert dialog.selection_edit.isEnabled() and dialog.dataset_combo.isEnabled()
        dialog.selection_mode.setCurrentIndex(dialog.selection_mode.findData("type_regex"))
        assert "Commas are literal" in dialog.selection_help.text()
        assert not dialog.download_button.isEnabled()
    finally:
        dialog.close()
        app.processEvents()

def test_neuprint_dialog_prefills_circuit_builder_dataset_and_selection(tmp_path: Path):
    application = QApplication.instance() or QApplication([])
    workspace = tmp_path / "Digifly Public"
    workspace.mkdir()
    profile = make_default_profile(
        workspace_root=workspace,
        output_root=tmp_path / "runs",
        managed_data_root=tmp_path / "library",
    )
    profile_path = profile.save(tmp_path / "resources-v2.json")
    selection = NeuPrintSelection("type_exact", "AN08B098", 64)
    dialog = NeuPrintImportDialog(
        profile,
        profile_path,
        credential_store=_NoCredentialStore(),
        preferred_dataset="male-cns:v0.9",
        initial_selection=selection,
        initial_resource_id="AN08B098",
    )
    try:
        assert dialog.selection_mode.currentData() == "type_exact"
        assert dialog.selection_edit.text() == "AN08B098"
        assert dialog.limit_spin.value() == 64
        assert dialog.name_edit.text() == "an08b098"
        dialog._connected(
            (
                NeuPrintDataset("manc:v1.2.1"),
                NeuPrintDataset("male-cns:v0.9", "Long description\nwith multiple paragraphs"),
            )
        )
        assert dialog.dataset_combo.currentData() == "male-cns:v0.9"
        assert dialog.dataset_combo.currentText() == "male-cns:v0.9"
        assert "multiple paragraphs" in dialog.dataset_combo.itemData(
            dialog.dataset_combo.currentIndex(), 3)
        assert dialog.dataset_focus_shortcut.key().toString() == "Alt+D"
    finally:
        dialog.close()
        application.processEvents()
