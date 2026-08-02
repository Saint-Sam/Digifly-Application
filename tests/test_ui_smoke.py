from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from digifly_app.core.circuit import CircuitSpec, ConnectomeRef
from digifly_app.core.project import DigiflyProject
from digifly_app.ui.circuit_builder import CIRCUIT_BUILDER_WORKFLOW
from digifly_app.ui.main_window import MainWindow, OverviewPage, _workspace_home


def test_main_window_constructs_without_importing_simulators():
    application = QApplication.instance() or QApplication([])
    overview = OverviewPage()
    assert overview.output_edit.text() == str(_workspace_home() / "runs")
    overview.close()
    window = MainWindow()
    try:
        assert window.pages.count() == 5
        assert window.windowTitle() == "Digifly App"
        assert window.experiment_page.run_button.isEnabled() is False
        assert "Digifly App.app" not in str(_workspace_home() / "runs")

        window.nav_buttons[1].setChecked(True)
        application.processEvents()
        assert window.pages.currentWidget() is window.circuit_builder_page
        assert window.nav_buttons[0].isChecked() is False

        window.nav_buttons[3].click()
        application.processEvents()
        assert window.pages.currentWidget() is window.results_page
        assert window.nav_buttons[1].isChecked() is False
    finally:
        window.close()
        application.processEvents()


def test_switching_editors_cannot_overwrite_a_different_workflow(tmp_path, monkeypatch):
    application = QApplication.instance() or QApplication([])
    original = tmp_path / "escape.digifly.json"
    original.write_text("do not replace", encoding="utf-8")
    replacement = tmp_path / "circuit.digifly.json"
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        lambda *args, **kwargs: (str(replacement), "Digifly projects (*.digifly.json)"),
    )
    window = MainWindow()
    try:
        window.current_project_path = original
        window._project_workflow = "escape_siz_gfc_contact_na"
        window.show_page(window.pages.indexOf(window.circuit_builder_page))
        window.save_project()
        assert original.read_text(encoding="utf-8") == "do not replace"
        assert DigiflyProject.load(replacement).selected_workflow == CIRCUIT_BUILDER_WORKFLOW
    finally:
        window.close()
        application.processEvents()


def test_failed_circuit_open_detaches_previous_project_path(tmp_path, monkeypatch):
    application = QApplication.instance() or QApplication([])
    old_path = tmp_path / "old.digifly.json"
    old_path.write_text("old project", encoding="utf-8")
    missing_root = tmp_path / "missing-swc-root"
    spec = CircuitSpec(
        connectome=ConnectomeRef("manc:v1.2.1", "MANC v1.2.1", str(missing_root), "manc_v1.2.1"),
        neuron_ids=("10000",),
    )
    bad_project = DigiflyProject(
        name="unavailable",
        digifly_public_root=str(tmp_path / "Digifly Public"),
        output_root=str(tmp_path / "runs"),
        selected_engine="arbor",
        selected_workflow=CIRCUIT_BUILDER_WORKFLOW,
        experiment=spec.to_dict(),
    ).save(tmp_path / "unavailable.digifly.json")
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        lambda *args, **kwargs: (str(bad_project), "Digifly projects (*.digifly.json)"),
    )
    monkeypatch.setattr(QMessageBox, "critical", lambda *args, **kwargs: None)
    window = MainWindow()
    try:
        window.current_project_path = old_path
        window._project_workflow = CIRCUIT_BUILDER_WORKFLOW
        window.open_project()
        assert window.current_project_path is None
        assert window._project_workflow is None
        assert "Open failed" in window.project_label.text()
    finally:
        window.close()
        application.processEvents()
