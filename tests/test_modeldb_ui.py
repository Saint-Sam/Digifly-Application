from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from digifly_app.core.model_import import inspect_model_source
from digifly_app.core.resource_profile import make_default_profile
from digifly_app.ui.modeldb_import import ModelDBImportDialog


def test_model_import_dialog_exposes_all_sources_inspection_and_destination(tmp_path: Path):
    application = QApplication.instance() or QApplication([])
    workspace = tmp_path / "Digifly Public"
    workspace.mkdir()
    profile = make_default_profile(
        workspace_root=workspace,
        output_root=tmp_path / "runs",
        managed_data_root=tmp_path / "library",
    )
    profile_path = profile.save(tmp_path / "resources-v2.json")
    model = tmp_path / "upstream-model"
    model.mkdir()
    (model / "README.md").write_text("Run with NEURON\n", encoding="utf-8")
    (model / "LICENSE").write_text("Example\n", encoding="utf-8")
    (model / "channel.mod").write_text("NEURON { SUFFIX x }\n", encoding="utf-8")
    dialog = ModelDBImportDialog(profile, profile_path)
    try:
        assert dialog.source_mode.count() == 3
        assert "download" in dialog.source_mode.itemText(0).casefold()
        dialog.source_mode.setCurrentIndex(2)
        dialog._selected_source = model.resolve()
        dialog.source_edit.setText(str(model))
        dialog.name_edit.setText("example-model")
        dialog.version_edit.setText("v1")
        dialog._local_inspected(inspect_model_source(model))
        application.processEvents()

        assert dialog.import_button.isEnabled()
        assert "3 file" in dialog.contents.text()
        assert "NEURON" in dialog.simulators.text()
        assert "inert" in dialog.warnings.text().casefold()
        assert str(profile.managed_data_root) in dialog.destination_label.text()
        assert "models/local/example-model/v1" in dialog.destination_label.text()
    finally:
        dialog.close()
        application.processEvents()
