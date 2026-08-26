from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from digifly_app.core.circuit import CircuitSpec, ConnectomeRef
from digifly_app.core.models import ResultRecord
from digifly_app.core.project import DigiflyProject
from digifly_app.engines.arbor_escape_siz import ArborEscapeSizAdapter
from digifly_app.ui.circuit_builder import CIRCUIT_BUILDER_WORKFLOW
from digifly_app.ui.main_window import (
    APPLICATION_NAME,
    LEGACY_APPLICATION_NAME,
    MainWindow,
    ORGANIZATION_NAME,
    OverviewPage,
    _workspace_home,
)


def test_workstation_identity_and_writable_root_are_distinct():
    assert APPLICATION_NAME == "Digifly Workstation"
    assert LEGACY_APPLICATION_NAME == "Digifly App"
    assert ORGANIZATION_NAME == "Digifly"
    assert _workspace_home().name == "Digifly Workstation Workspace"


def test_main_window_constructs_without_importing_simulators():
    application = QApplication.instance() or QApplication([])
    overview = OverviewPage()
    assert overview.output_edit.text() == str(_workspace_home() / "runs")
    overview.close()
    window = MainWindow()
    try:
        assert window.pages.count() == 5
        assert window.windowTitle() == "Digifly Workstation"
        assert window.experiment_page.run_button.isEnabled() is False
        assert "Digifly Workstation.app" not in str(_workspace_home() / "runs")

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


def test_ablation_notebook_preset_applies_exact_active_controls():
    application = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        page = window.experiment_page
        page.preset_combo.setCurrentIndex(page.preset_combo.findData("ablation_notebook_active"))
        application.processEvents()
        config = page.config()
        assert config.preset == "ablation_notebook_active"
        assert config.gfc2_ohmic is False
        assert config.nproc == 1
        assert config.postsynaptic_only_3d_plots is True
        assert set(config.stimulus_by_condition["gap_enabled"].values()) == {0.9}
    finally:
        window.close()
        application.processEvents()


def test_experiment_page_exposes_arbor_comparison_without_claiming_parity():
    application = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        page = window.experiment_page
        page.backend_combo.setCurrentIndex(page.backend_combo.findData("arbor"))
        application.processEvents()
        config = page.active_config()
        assert page.backend_combo.currentData() == "arbor"
        assert config.static_reverse_fraction == 0.2
        assert config.requested_tau_open_ms == 6.0
        assert config.requested_tau_close_ms == 2.0
        assert page.run_button.text() == "Run Arbor custom-gap comparison"
        assert "custom HeteroRectGap" in page.plan_state.text()
        assert page.parallelism_label.text() == "Arbor CPU threads"
        assert page.nproc.value() == 4
        assert page.nproc.isEnabled()
        assert "Arbor's vectorized simulation" in page.nproc.toolTip()
        assert page.parallelism_label.toolTip() == page.nproc.toolTip()

        page.backend_combo.setCurrentIndex(page.backend_combo.findData("neuron"))
        application.processEvents()
        assert page.parallelism_label.text() == "NEURON workers"
        assert page.nproc.value() == 1
        assert not page.nproc.isEnabled()
        assert "historical multi-rank launches segfaulted" in page.nproc.toolTip()
        assert page.parallelism_label.toolTip() == page.nproc.toolTip()
    finally:
        window.close()
        application.processEvents()


def test_results_load_latest_uses_selected_arbor_backend(monkeypatch):
    application = QApplication.instance() or QApplication([])
    called = {}
    expected = ResultRecord(
        summary_path="/tmp/comparison_summary.json",
        status="complete",
        completed_at="2026-08-03T02:32:34+00:00",
        title="Arbor comparison",
        metadata={"equivalence audit": "NOT_YET_EQUIVALENT"},
        artifacts=(),
    )

    def fake_latest(self, config=None, *, output_root=None):
        called["config"] = config
        called["output_root"] = output_root
        return expected

    monkeypatch.setattr(ArborEscapeSizAdapter, "latest_result", fake_latest)
    window = MainWindow()
    try:
        page = window.experiment_page
        page.backend_combo.setCurrentIndex(page.backend_combo.findData("arbor"))
        application.processEvents()
        window.results_page.load_latest()
        assert called["config"].preset == "ablation_notebook_arbor_comparison"
        assert called["output_root"] == window.overview_page.output_edit.text()
        assert window.results_page.result_status.text().startswith("complete")
        metadata_values = {
            window.results_page.metadata.item(row, 1).text()
            for row in range(window.results_page.metadata.rowCount())
        }
        assert "NOT_YET_EQUIVALENT" in metadata_values
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
