from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLineEdit

from digifly_app.core.neuprint import NeuPrintDataset, NeuPrintNeuron, NeuPrintSelection
from digifly_app.core.resource_profile import make_default_profile
from digifly_app.ui.neuprint_import import NeuPrintImportDialog


class _NoCredentialStore:
    available = False


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
        assert dialog.selection_mode.count() == 6
        assert dialog.limit_spin.maximum() == 500
        assert dialog.connectivity_check.isChecked()
        assert not dialog.remember_token.isEnabled()
        dialog._active_token = "fixture-token"
        dialog.dataset_combo.addItem("manc:v1.2.1", "manc:v1.2.1")
        dialog.dataset_combo.setEnabled(True)
        dialog.selection_mode.setCurrentIndex(1)
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
                NeuPrintDataset("male-cns:v0.9"),
            )
        )
        assert dialog.dataset_combo.currentData() == "male-cns:v0.9"
    finally:
        dialog.close()
        application.processEvents()
