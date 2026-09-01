from __future__ import annotations

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from digifly_app.core.circuit import CircuitSpec, ConnectomeRef
from digifly_app.core.connectomes import NeuronRecord
from digifly_app.core.experiment import EXPERIMENT_BUILDER_WORKFLOW, ExperimentSpec
from digifly_app.core.models import ResultRecord
from digifly_app.core.morphology import Morphology, SwcNode, SwcSegment
from digifly_app.core.project import DigiflyProject
from digifly_app.core.resource_profile import (
    ResourceKind,
    ResourceProfile,
    make_default_profile,
)
from digifly_app.ui.circuit_builder import CIRCUIT_BUILDER_WORKFLOW
from digifly_app.ui.experiment_builder import EXPERIMENT_SETTING_HELP
from digifly_app.ui.main_window import (
    APPLICATION_NAME,
    LEGACY_APPLICATION_NAME,
    MainWindow,
    ORGANIZATION_NAME,
    OverviewPage,
    _workspace_home,
)
from digifly_app.ui.runtime_setup import (
    ARBOR_INSTALL_URL,
    NEURON_INSTALL_URL,
    RuntimeSetupDialog,
)
from digifly_app.ui.stimulus_preview import pulse_intervals
from digifly_app.ui.style import (
    DARK_THEME,
    LIGHT_THEME,
    normalize_theme,
    style_for_theme,
    theme_color,
)


@pytest.fixture(autouse=True)
def _isolate_machine_settings(tmp_path, monkeypatch):
    """GUI tests must never read or overwrite the user's machine bindings."""
    previous_format = QSettings.defaultFormat()
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    monkeypatch.setenv(
        "DIGIFLY_WORKSTATION_PROFILE",
        str(tmp_path / "no-machine-profile.json"),
    )
    yield
    QSettings.setDefaultFormat(previous_format)


def test_workstation_identity_and_writable_root_are_distinct():
    assert APPLICATION_NAME == "Digifly Workstation"
    assert LEGACY_APPLICATION_NAME == "Digifly App"
    assert ORGANIZATION_NAME == "Digifly"
    assert _workspace_home().name == "Digifly Workstation Workspace"


def test_runtime_setup_requires_consent_before_search(monkeypatch):
    application = QApplication.instance() or QApplication([])
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.No,
    )
    dialog = RuntimeSetupDialog()
    try:
        dialog.request_search()
        application.processEvents()
        assert dialog._thread is None
        assert "not authorized" in dialog.status.text()
        assert NEURON_INSTALL_URL.startswith("https://nrn.readthedocs.io/")
        assert ARBOR_INSTALL_URL.startswith("https://docs.arbor-sim.org/")
    finally:
        dialog.close()


def test_runtime_selection_creates_machine_profile(tmp_path, monkeypatch):
    application = QApplication.instance() or QApplication([])
    public = tmp_path / "Digifly Public"
    public.mkdir()
    (public / "README.md").write_text("Digifly Public\n", encoding="utf-8")
    output = tmp_path / "workspace" / "runs"
    output.parent.mkdir()
    runtime = tmp_path / "simulators" / "bin" / "python"
    runtime.parent.mkdir(parents=True)
    runtime.write_text("#!/bin/sh\n", encoding="utf-8")
    runtime.chmod(0o755)
    profile_path = tmp_path / "resources-v2.json"
    monkeypatch.setenv("DIGIFLY_WORKSTATION_PROFILE", str(profile_path))
    page = OverviewPage()
    page.workspace_edit.setText(str(public))
    page.output_edit.setText(str(output))
    refreshed: list[bool] = []
    monkeypatch.setattr(page, "refresh", lambda: refreshed.append(True))
    try:
        page._runtime_selected(str(runtime), str(runtime))
        profile = ResourceProfile.load(profile_path)
        assert profile.runtime_path(ResourceKind.NEURON_RUNTIME) == runtime.resolve()
        assert profile.runtime_path(ResourceKind.ARBOR_RUNTIME) == runtime.resolve()
        assert refreshed == [True]
    finally:
        page.close()
        application.processEvents()


def test_main_window_constructs_without_importing_simulators():
    application = QApplication.instance() or QApplication([])
    overview = OverviewPage()
    assert overview.output_edit.text() == str(_workspace_home() / "runs")
    assert overview.arbor_python_edit.text()
    overview.close()
    window = MainWindow()
    try:
        assert window.pages.count() == 6
        assert window.windowTitle() == "Digifly Workstation"
        assert window.experiment_page.run_button.isEnabled() is True
        assert window.data_library_page.import_in_progress is False
        assert "Digifly Workstation.app" not in str(_workspace_home() / "runs")
        assert window.experiment_page.config().engine == "arbor"
        assert window.experiment_page.template_combo.currentData() == "blank"
        assert window.experiment_page.config().template_key == "blank"
        assert window.experiment_page.name_edit.text() == "Untitled experiment"
        assert "Experiment Builder" in window.nav_buttons[3].text()
        assert "Escape-SIZ" not in window.nav_buttons[3].text()

        window.nav_buttons[2].setChecked(True)
        application.processEvents()
        assert window.pages.currentWidget() is window.circuit_builder_page
        assert window.nav_buttons[0].isChecked() is False

        window.nav_buttons[4].click()
        application.processEvents()
        assert window.pages.currentWidget() is window.results_page
        assert window.nav_buttons[2].isChecked() is False
    finally:
        window.close()
        application.processEvents()


def test_sun_toggle_switches_and_persists_the_application_theme():
    application = QApplication.instance() or QApplication([])
    settings = QSettings(ORGANIZATION_NAME, APPLICATION_NAME)
    settings.setValue("theme", DARK_THEME)
    settings.sync()
    window = MainWindow()
    try:
        assert window.theme == DARK_THEME
        assert window.theme_toggle.text() == "☾"
        assert window.theme_toggle.isChecked() is False
        assert window.theme_toggle.toolTip() == "Switch to light theme"
        assert application.property("digiflyTheme") == DARK_THEME

        window.theme_toggle.click()
        application.processEvents()
        assert window.theme == LIGHT_THEME
        assert window.theme_toggle.text() == "☀"
        assert window.theme_toggle.isChecked() is True
        assert window.theme_toggle.toolTip() == "Switch to dark theme"
        assert application.property("digiflyTheme") == LIGHT_THEME
        assert application.styleSheet() == style_for_theme(LIGHT_THEME)
        assert QSettings(ORGANIZATION_NAME, APPLICATION_NAME).value("theme") == LIGHT_THEME
    finally:
        window.close()
        application.processEvents()

    restored = MainWindow()
    try:
        assert restored.theme == LIGHT_THEME
        assert restored.theme_toggle.text() == "☀"
        assert restored.theme_toggle.isChecked() is True
        restored.theme_toggle.click()
        application.processEvents()
        assert restored.theme == DARK_THEME
        assert restored.theme_toggle.text() == "☾"
    finally:
        restored.close()
        application.processEvents()


def test_theme_styles_are_complete_and_use_distinct_canvas_palettes():
    assert normalize_theme("LIGHT") == LIGHT_THEME
    assert normalize_theme("unsupported") == DARK_THEME
    dark_style = style_for_theme(DARK_THEME)
    light_style = style_for_theme(LIGHT_THEME)
    assert dark_style != light_style
    assert "@root@" not in dark_style
    assert "@root@" not in light_style
    assert "QMessageBox QLabel" in dark_style
    assert "QMessageBox QLabel" in light_style
    assert theme_color(DARK_THEME, "viewport_background") != theme_color(
        LIGHT_THEME, "viewport_background"
    )
    assert theme_color(DARK_THEME, "stimulus_surface") != theme_color(
        LIGHT_THEME, "stimulus_surface"
    )
    assert theme_color(DARK_THEME, "name_available_bg") != theme_color(
        LIGHT_THEME, "name_available_bg"
    )
    assert theme_color(DARK_THEME, "name_unavailable_bg") != theme_color(
        LIGHT_THEME, "name_unavailable_bg"
    )
    assert 'QLineEdit#ExperimentName[nameAvailability="available"]' in dark_style
    assert 'QLineEdit#ExperimentName[nameAvailability="unavailable"]' in light_style


def test_main_window_recovers_stale_paths_and_prefers_profile_runtimes(tmp_path, monkeypatch):
    application = QApplication.instance() or QApplication([])
    public = tmp_path / "Digifly Public"
    public.mkdir()
    (public / "README.md").write_text("Digifly Public\n", encoding="utf-8")
    output = tmp_path / "workspace" / "runs"
    output.parent.mkdir()
    neuron_python = tmp_path / "neuron-env" / "bin" / "python"
    arbor_python = tmp_path / "arbor-env" / "bin" / "python"
    for executable in (neuron_python, arbor_python):
        executable.parent.mkdir(parents=True)
        executable.write_text("#!/bin/sh\n", encoding="utf-8")
        executable.chmod(0o755)
    profile_path = tmp_path / "resources-v2.json"
    make_default_profile(
        workspace_root=public,
        output_root=output,
        neuron_runtime=neuron_python,
        arbor_runtime=arbor_python,
    ).save(profile_path)
    monkeypatch.setenv("DIGIFLY_WORKSTATION_PROFILE", str(profile_path))
    settings = QSettings(ORGANIZATION_NAME, APPLICATION_NAME)
    settings.setValue("workspace_root", tmp_path / "deleted-test-workspace")
    settings.setValue("output_root", tmp_path / "deleted-test-output" / "runs")
    settings.setValue("neuron_python", "/usr/bin/python3")
    settings.setValue("arbor_python", "/usr/bin/python3")
    settings.sync()

    window = MainWindow()
    try:
        assert window.overview_page.workspace_edit.text() == str(public.resolve())
        assert window.overview_page.output_edit.text() == str(output.resolve())
        assert window.overview_page.python_edit.text() == str(neuron_python.resolve())
        assert window.overview_page.arbor_python_edit.text() == str(arbor_python.resolve())
        assert "Recovered unavailable" in window.overview_page.doctor_summary.text()
    finally:
        window.close()
        application.processEvents()


def test_app_owned_pulse_template_exposes_runtime_controls_without_a_notebook():
    application = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        page = window.experiment_page
        page.template_combo.setCurrentIndex(
            page.template_combo.findData("pulse_train_comparison")
        )
        application.processEvents()
        config = page.config()
        assert config.template_key == "pulse_train_comparison"
        assert config.integration_dt_ms == 0.01
        assert config.recording.sample_dt_ms == 0.05
        assert config.stimuli[0].frequency_hz == 100.0
        assert config.stimuli[0].pulse_count == 10
        assert config.conditions[1].gap_junctions_enabled is False
        page.reset()
        assert page.template_combo.currentData() == "blank"
        assert page.name_edit.text() == "Untitled experiment"
    finally:
        window.close()
        application.processEvents()


def test_experiment_run_button_warns_before_reusing_a_saved_name(tmp_path, monkeypatch):
    application = QApplication.instance() or QApplication([])
    output = tmp_path / "runs"
    saved = output / "jobs" / "20260901_120000_experiment_builder_v1"
    saved.mkdir(parents=True)
    (saved / "request.json").write_text(
        json.dumps({"experiment": {"name": "Untitled experiment"}}),
        encoding="utf-8",
    )
    warnings: list[tuple[str, str]] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )
    window = MainWindow()
    try:
        window.overview_page.output_edit.setText(str(output))
        application.processEvents()
        assert window.experiment_page.name_availability.text() == "✕ Unavailable"
        assert (
            window.experiment_page.name_availability.property("availability")
            == "unavailable"
        )
        assert (
            window.experiment_page.name_edit.property("nameAvailability")
            == "unavailable"
        )
        assert window.experiment_page.run_button.isEnabled()
        window.experiment_page.run_button.click()
        application.processEvents()
        assert warnings and warnings[0][0] == "Experiment name already used"
        assert "Untitled experiment" in warnings[0][1]
        assert str(saved.resolve()) in warnings[0][1]
        assert "No files were changed" in warnings[0][1]
        assert "Name already used" in window.experiment_page.validation_state.text()
    finally:
        window.close()
        application.processEvents()


def test_unique_experiment_name_passes_name_gate_without_starting_backend(
    tmp_path, monkeypatch
):
    application = QApplication.instance() or QApplication([])
    messages: list[tuple[str, str]] = []
    warnings: list[tuple[str, str]] = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda _parent, title, message: messages.append((title, message)),
    )
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )
    window = MainWindow()
    try:
        window.overview_page.output_edit.setText(str(tmp_path / "runs"))
        window.experiment_page.name_edit.setText("First unique run")
        application.processEvents()
        assert window.experiment_page.name_availability.text() == "✓ Available"
        assert (
            window.experiment_page.name_edit.property("nameAvailability")
            == "available"
        )
        window.experiment_page.set_circuit_spec(
            CircuitSpec(
                connectome=ConnectomeRef("manc:v1.2.1", "MANC", "/data/swc"),
                neuron_ids=("10000",),
            )
        )
        window.experiment_page.run_button.click()
        application.processEvents()
        assert warnings == []
        assert messages == []
        assert "Name available" in window.experiment_page.validation_state.text()
        assert not (tmp_path / "runs").exists()
    finally:
        window.close()
        application.processEvents()


def test_experiment_name_availability_updates_during_typing(tmp_path):
    application = QApplication.instance() or QApplication([])
    output = tmp_path / "runs"
    saved = output / "jobs" / "20260901_120000_experiment_builder_v1"
    saved.mkdir(parents=True)
    (saved / "request.json").write_text(
        json.dumps({"experiment": {"name": "Untitled experiment"}}),
        encoding="utf-8",
    )
    window = MainWindow()
    try:
        page = window.experiment_page
        window.overview_page.output_edit.setText(str(output))
        application.processEvents()
        assert page.name_availability.text() == "✕ Unavailable"

        page.name_edit.selectAll()
        QTest.keyClicks(page.name_edit, "Fresh typed run")
        application.processEvents()
        assert page.name_availability.text() == "✓ Available"
        assert page.name_availability.property("availability") == "available"
        assert page.name_edit.property("nameAvailability") == "available"

        page.name_edit.selectAll()
        QTest.keyClicks(page.name_edit, "  UNTITLED   EXPERIMENT  ")
        application.processEvents()
        assert page.name_availability.text() == "✕ Unavailable"
        assert page.name_availability.property("availability") == "unavailable"
        assert page.name_edit.property("nameAvailability") == "unavailable"
        assert str(saved.resolve()) in page.name_edit.toolTip()
    finally:
        window.close()
        application.processEvents()


def test_experiment_builder_uses_left_disclosures_and_reactive_stimulus_preview():
    application = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        page = window.experiment_page
        assert tuple(page.selector_sections) == (
            "experiment_identity",
            "circuit_input",
            "primary_stimulus",
            "simulation_compute",
            "runtime_conditions",
            "recording_outputs",
        )
        assert page.selector_sections["experiment_identity"].is_expanded
        assert all(
            not section.is_expanded
            for key, section in page.selector_sections.items()
            if key != "experiment_identity"
        )

        page.duration.setValue(40.0)
        page.amplitude.setValue(1.5)
        page.delay.setValue(5.0)
        page.pulse_width.setValue(2.0)
        page.frequency.setValue(50.0)
        page.pulse_count.setValue(3)
        page.seed.setValue(42)
        application.processEvents()

        assert page.stimulus_preview.protocol() == {
            "duration_ms": 40.0,
            "random_seed": 42,
            "amplitude_nA": 1.5,
            "delay_ms": 5.0,
            "pulse_width_ms": 2.0,
            "frequency_hz": 50.0,
            "pulse_count": 3,
            "waveform": "pulse_train",
        }
        assert page.stimulus_preview.intervals() == ((5.0, 7.0), (25.0, 27.0))
        assert "1.5 nA" in page.stimulus_preview_summary.text()
        assert "simulation ends at 40 ms" in page.stimulus_preview_summary.text()
        assert "Seed: 42" in page.stimulus_preview.accessibleDescription()

        assert set(page.help_buttons) == set(EXPERIMENT_SETTING_HELP)
        assert all(button.text() == "?" for button in page.help_buttons.values())
        assert all(
            button.toolTip() == "Click for help"
            for button in page.help_buttons.values()
        )
        seed_help = page.help_labels["random_seed"]
        assert seed_help.text_label.text() == "Random seed"
        assert seed_help.help_button.accessibleName() == "Help for Random seed"
        requested_help = []
        seed_help.help_button.help_requested.connect(
            lambda key, text: requested_help.append((key, text))
        )
        seed_help.help_button.click()
        assert requested_help == [
            ("random_seed", EXPERIMENT_SETTING_HELP["random_seed"])
        ]
        assert "same random draws" in seed_help.help_button.accessibleDescription()
        assert seed_help.help_button.help_popup is not None
        assert seed_help.help_button.help_popup.isVisible()
        seed_help.help_button.help_popup.close()

        page.selector_sections["primary_stimulus"].set_expanded(True)
        page.frequency.lineEdit().selectAll()
        QTest.keyClicks(page.frequency.lineEdit(), "75")
        application.processEvents()
        assert page.frequency.value() == 75.0
        assert page.stimulus_preview.protocol()["frequency_hz"] == 75.0
        assert "3 pulses @ 75 Hz" in page.stimulus_preview_summary.text()

        simulation_section = page.selector_sections["simulation_compute"]
        simulation_section.toggle_button.click()
        simulation_section.toggle_button.click()
        assert page.duration.value() == 40.0

        page.stimulus_preview.resize(620, 330)
        assert not page.stimulus_preview.grab().isNull()
    finally:
        window.close()
        application.processEvents()


def test_stimulus_preview_clips_pulses_to_the_simulation_window():
    assert pulse_intervals(
        duration_ms=30.0,
        delay_ms=5.0,
        pulse_width_ms=0.4,
        frequency_hz=100.0,
        pulse_count=10,
        waveform="pulse_train",
    ) == ((5.0, 5.4), (15.0, 15.4), (25.0, 25.4))


def _experiment_test_morphology(
    neuron_id: str = "10000",
    neuron_type: str = "DNp01",
    *,
    offset: float = 0.0,
) -> Morphology:
    record = NeuronRecord(
        neuron_id,
        "DN",
        neuron_type,
        f"/data/swc/{neuron_id}.swc",
        "manc:v1.2.1",
    )
    nodes = (
        SwcNode(1, 1, offset + 0.0, 0.0, 0.0, 2.0, -1),
        SwcNode(2, 1, offset + 1.0, 0.0, 0.0, 1.0, 1),
        SwcNode(3, 2, offset + 2.0, 0.0, 0.0, 0.8, 2),
        SwcNode(4, 2, offset + 3.0, 0.0, 0.0, 0.6, 3),
        SwcNode(5, 3, offset + 1.0, 1.0, 0.0, 0.5, 2),
    )
    segments = tuple(
        SwcSegment(
            node.node_id,
            node.parent_id,
            (node.x, node.y, node.z),
            (nodes[node.parent_id - 1].x, nodes[node.parent_id - 1].y, nodes[node.parent_id - 1].z),
            node.radius,
            node.swc_type,
        )
        for node in nodes[1:]
    )
    return Morphology(
        record,
        nodes,
        segments,
        (offset, offset + 3.0, 0.0, 1.0, 0.0, 0.0),
    )


def test_experiment_builder_copies_circuit_geometry_and_highlights_target_regions():
    application = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        morphology = _experiment_test_morphology()
        ttmn = _experiment_test_morphology("20000", "TTMn", offset=10.0)
        circuit = CircuitSpec(
            connectome=ConnectomeRef("manc:v1.2.1", "MANC v1.2.1", "/data/swc"),
            neuron_ids=("10000", "20000"),
        )
        circuit.apply_compartment_override("10000", (4,), circuit.hh.to_dict())
        before = circuit.to_dict()
        window.circuit_builder_page.spec = circuit
        window.circuit_builder_page.loaded_morphologies = {
            "10000": morphology,
            "20000": ttmn,
        }
        window.circuit_builder_page.circuit_changed.emit(circuit)
        application.processEvents()

        page = window.experiment_page
        assert not hasattr(page, "circuit_viewport")
        assert page.target_region_viewport.camera_only is True
        assert page.target_region_viewport.neuron_count == 2
        assert page.target_region_viewport.highlighted_soma_ids == {"10000", "20000"}
        assert "Soma" in page.target_region_visualization_label.text()

        page.stimulus_targets.setText("dnp01, TTMn")
        page.stimulus_targets.textEdited.emit("dnp01, TTMn")
        application.processEvents()
        assert page.config().stimuli[0].target_neuron_ids == ("10000", "20000")
        assert page.target_region_viewport.highlighted_soma_ids == {"10000", "20000"}

        page.stimulus_region.setCurrentIndex(page.stimulus_region.findData("ais"))
        application.processEvents()
        assert page.target_region_viewport.highlighted_segment_count == 2
        assert "visual proxy" in page.target_region_visualization_label.text()

        page.stimulus_region.setCurrentIndex(
            page.stimulus_region.findData("selected_compartments")
        )
        application.processEvents()
        assert page.target_region_viewport.highlighted_segment_ids == {"10000": {4}}
        assert "1 applied Circuit Builder compartment" in (
            page.target_region_visualization_label.text()
        )

        page.stimulus_region.setCurrentIndex(page.stimulus_region.findData("all"))
        application.processEvents()
        assert page.target_region_viewport.highlighted_neuron_ids == {
            "10000",
            "20000",
        }
        assert circuit.to_dict() == before
    finally:
        window.close()
        application.processEvents()


def test_experiment_builder_receives_circuit_without_mutating_network_design():
    application = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        page = window.experiment_page
        circuit = CircuitSpec(
            connectome=ConnectomeRef("manc:v1.2.1", "MANC v1.2.1", "/data/swc"),
            neuron_ids=("10000", "10002"),
        )
        before = circuit.to_dict()
        window.circuit_builder_page.circuit_changed.emit(circuit)
        application.processEvents()
        assert page.circuit_spec().neuron_ids == ("10000", "10002")
        assert "2 neuron(s)" in page.circuit_summary.text()
        page.disabled_neurons.setText("10002")
        page.engine_combo.setCurrentIndex(page.engine_combo.findData("neuron"))
        window.show_page(window.pages.indexOf(window.circuit_builder_page))
        window.show_page(window.pages.indexOf(page))
        config = page.config()
        assert config.conditions[1].disabled_neuron_ids == ("10002",)
        assert config.engine == "neuron"
        assert circuit.to_dict() == before
    finally:
        window.close()
        application.processEvents()


def test_results_load_latest_uses_completed_job_summary(tmp_path, monkeypatch):
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

    def fake_load(path):
        called["path"] = path
        return expected
    window = MainWindow()
    try:
        output = tmp_path / "runs"
        summary = output / "scientific" / "summary.json"
        summary.parent.mkdir(parents=True)
        summary.write_text("{}\n", encoding="utf-8")
        job = output / "jobs" / "20260831_120000_experiment"
        job.mkdir(parents=True)
        (job / "status.json").write_text(
            json.dumps({"state": "completed"}), encoding="utf-8"
        )
        (job / "resolved_plan.json").write_text(
            json.dumps({"expected_summary_path": str(summary)}), encoding="utf-8"
        )
        window.overview_page.output_edit.setText(str(output))
        monkeypatch.setattr(window.results_page, "_load_result", fake_load)
        window.results_page.load_latest()
        assert called["path"] == summary
        assert window.results_page.result_status.text().startswith("complete")
        metadata_values = {
            window.results_page.metadata.item(row, 1).text()
            for row in range(window.results_page.metadata.rowCount())
        }
        assert "NOT_YET_EQUIVALENT" in metadata_values
    finally:
        window.close()
        application.processEvents()


def test_switching_editors_saves_one_unified_project(tmp_path):
    application = QApplication.instance() or QApplication([])
    original = DigiflyProject(
        name="unified",
        digifly_public_root=str(tmp_path),
        output_root=str(tmp_path / "runs"),
        selected_workflow=EXPERIMENT_BUILDER_WORKFLOW,
        experiment=ExperimentSpec().to_dict(),
    ).save(tmp_path / "unified.digifly.json")
    window = MainWindow()
    try:
        window.current_project_path = original
        window._project_workflow = EXPERIMENT_BUILDER_WORKFLOW
        window.show_page(window.pages.indexOf(window.circuit_builder_page))
        window.save_project()
        saved = DigiflyProject.load(original)
        assert saved.selected_workflow == CIRCUIT_BUILDER_WORKFLOW
        assert saved.circuit["schema_version"] == 2
        assert saved.experiment["schema_version"] == 1
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
        circuit=spec.to_dict(),
        experiment=ExperimentSpec().to_dict(),
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
