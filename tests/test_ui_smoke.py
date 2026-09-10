from __future__ import annotations

import hashlib
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QWidget

from digifly_app.core.circuit import CircuitSpec, ConnectomeRef
from digifly_app.core.connectomes import NeuronRecord
from digifly_app.core.experiment import EXPERIMENT_BUILDER_WORKFLOW, ExperimentSpec
from digifly_app.core.models import (
    Artifact,
    CheckState,
    ExecutionPlan,
    PreflightCheck,
    PreflightReport,
    ResultRecord,
)
from digifly_app.core.morphology import Morphology, SwcNode, SwcSegment
from digifly_app.core.project import DigiflyProject
from digifly_app.core.runtime_discovery import SimulatorRuntime
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
    BMTK_INSTALL_URL,
    NEURON_INSTALL_URL,
    RuntimeSetupDialog,
)
from digifly_app.ui.stimulus_preview import pulse_intervals
from digifly_app.ui.snapshot import safe_png_name, save_image_with_dialog
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


def test_visualizers_expose_high_resolution_snapshot_actions():
    application = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        circuit = window.circuit_builder_page
        experiment = window.experiment_page
        results = window.results_page
        assert circuit.save_visualization_button.text() == "Save snapshot…"
        assert experiment.save_target_visualization_button.text() == "Save snapshot…"
        assert experiment.save_stimulus_visualization_button.text() == "Save snapshot…"
        assert results.save_result_visualization_button.text() == "Save snapshot…"
        assert results.save_trace_button.text() == "Save plot…"
        image = experiment.stimulus_preview.render_high_resolution(width=1000)
        assert image.width() == 1000
        assert image.height() > experiment.stimulus_preview.height()
    finally:
        window.close()
        application.processEvents()


def test_snapshot_save_dialog_accepts_user_filename(tmp_path, monkeypatch):
    application = QApplication.instance() or QApplication([])
    output = tmp_path / "My chosen trace name"
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        lambda *_args, **_kwargs: (str(output), "PNG image (*.png)"),
    )
    image = QImage(20, 10, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.white)
    parent = QWidget()
    try:
        saved = save_image_with_dialog(
            parent,
            image,
            title="Save visualization",
            default_name="A visual name.png",
        )
        assert saved == output.with_suffix(".png").resolve()
        assert saved.is_file()
        assert safe_png_name("A visual name.png") == "A-visual-name.png"
    finally:
        parent.close()
        application.processEvents()


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
        assert BMTK_INSTALL_URL.startswith("https://alleninstitute.github.io/bmtk/")
    finally:
        dialog.close()


def test_runtime_setup_only_offers_bionet_compatible_bmtk_interpreters(tmp_path):
    application = QApplication.instance() or QApplication([])
    ready_python = tmp_path / "ready" / "bin" / "python"
    blocked_python = tmp_path / "blocked" / "bin" / "python"
    dialog = RuntimeSetupDialog()
    try:
        dialog._discovery_completed(
            (
                SimulatorRuntime(
                    ready_python,
                    "3.11.15",
                    neuron_version="8.2.6",
                    bmtk_version="1.2.0",
                    bionet_ready=True,
                ),
                SimulatorRuntime(
                    blocked_python,
                    "3.11.15",
                    bmtk_version="1.2.0",
                    bionet_error="ModuleNotFoundError: No module named 'neuron'",
                ),
            )
        )
        application.processEvents()
        assert dialog.table.rowCount() == 2
        assert dialog.bmtk_combo.count() == 1
        assert dialog.bmtk_combo.currentData() == str(ready_python)
        assert "BioNet blocked" in dialog.table.item(1, 4).text()
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
        page._runtime_selected(str(runtime), str(runtime), str(runtime))
        profile = ResourceProfile.load(profile_path)
        assert profile.runtime_path(ResourceKind.NEURON_RUNTIME) == runtime.resolve()
        assert profile.runtime_path(ResourceKind.ARBOR_RUNTIME) == runtime.resolve()
        assert profile.runtime_path(ResourceKind.BMTK_RUNTIME) == runtime.resolve()
        assert refreshed == [True]
    finally:
        page.close()
        application.processEvents()


def test_main_window_constructs_without_importing_simulators():
    application = QApplication.instance() or QApplication([])
    overview = OverviewPage()
    assert overview.output_edit.text() == str(_workspace_home() / "runs")
    assert overview.arbor_python_edit.text()
    assert overview.bmtk_python_edit.placeholderText()
    overview.close()
    window = MainWindow()
    try:
        assert window.pages.count() == 6
        assert window.windowTitle() == "Digifly Workstation"
        assert window.experiment_page.run_button.isEnabled() is True
        assert window.data_library_page.import_in_progress is False
        assert "Digifly Workstation.app" not in str(_workspace_home() / "runs")
        assert window.experiment_page.config().engine == "arbor"
        assert "bmtk" in window.experiment_page._runtime_paths
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
    base_python = tmp_path / "base" / "python3.12"
    base_python.parent.mkdir()
    base_python.write_text("#!/bin/sh\n", encoding="utf-8")
    base_python.chmod(0o755)
    neuron_python = tmp_path / "neuron-env" / "bin" / "python"
    arbor_python = tmp_path / "arbor-env" / "bin" / "python"
    bmtk_python = tmp_path / "bmtk-env" / "bin" / "python"
    for executable in (neuron_python, arbor_python, bmtk_python):
        executable.parent.mkdir(parents=True)
        executable.symlink_to(base_python)
    profile_path = tmp_path / "resources-v2.json"
    make_default_profile(
        workspace_root=public,
        output_root=output,
        neuron_runtime=neuron_python,
        arbor_runtime=arbor_python,
        bmtk_runtime=bmtk_python,
    ).save(profile_path)
    monkeypatch.setenv("DIGIFLY_WORKSTATION_PROFILE", str(profile_path))
    settings = QSettings(ORGANIZATION_NAME, APPLICATION_NAME)
    settings.setValue("workspace_root", tmp_path / "deleted-test-workspace")
    settings.setValue("output_root", tmp_path / "deleted-test-output" / "runs")
    settings.setValue("neuron_python", "/usr/bin/python3")
    settings.setValue("arbor_python", "/usr/bin/python3")
    settings.setValue("bmtk_python", "/usr/bin/python3")
    settings.sync()

    window = MainWindow()
    try:
        assert window.overview_page.workspace_edit.text() == str(public.resolve())
        assert window.overview_page.output_edit.text() == str(output.resolve())
        assert window.overview_page.python_edit.text() == str(neuron_python.absolute())
        assert window.overview_page.arbor_python_edit.text() == str(arbor_python.absolute())
        assert window.overview_page.bmtk_python_edit.text() == str(bmtk_python.absolute())
        assert window.experiment_page._runtime_paths["bmtk"] == str(bmtk_python.absolute())
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


def test_unique_experiment_name_reaches_simulator_preflight_without_writing(
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
        assert warnings and warnings[0][0] == "Experiment cannot run yet"
        assert "Source morphology" in warnings[0][1]
        assert messages == []
        assert "simulator preflight" in window.experiment_page.validation_state.text()
        assert not (tmp_path / "runs").exists()
    finally:
        window.close()
        application.processEvents()


def test_launch_ready_experiment_creates_provenance_and_starts_worker(
    tmp_path, monkeypatch
):
    application = QApplication.instance() or QApplication([])
    warnings: list[tuple[str, str]] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )

    report = PreflightReport(
        (
            PreflightCheck(
                "ready",
                "Executable test plan",
                CheckState.PASS,
                "All launch gates passed.",
                blocking=True,
            ),
        )
    )
    monkeypatch.setattr(
        "digifly_app.ui.experiment_builder.GenericExperimentAdapter.validate",
        lambda *args, **kwargs: report,
    )

    source = tmp_path / "1.swc"
    source.write_text(
        "1 1 0 0 0 2 -1\n2 3 1 0 0 1 1\n",
        encoding="utf-8",
    )
    base = _experiment_test_morphology("1", "synthetic")
    morphology = Morphology(
        NeuronRecord("1", "test", "synthetic", str(source), "test:v1"),
        base.nodes,
        base.segments,
        base.bounds,
    )
    circuit = CircuitSpec(
        connectome=ConnectomeRef("test:v1", "Test", str(tmp_path)),
        neuron_ids=("1",),
        morphology_sha256={"1": hashlib.sha256(source.read_bytes()).hexdigest()},
    )
    started: dict[str, object] = {}
    window = MainWindow()
    try:
        output = tmp_path / "runs"
        page = window.experiment_page
        window.overview_page.output_edit.setText(str(output))
        window.overview_page.arbor_python_edit.setText(os.sys.executable)
        page.name_edit.setText("Launch-ready experiment")
        page.set_circuit_snapshot(circuit, (morphology,))

        def capture_start(plan, store, job_dir, preflight):
            started.update(
                plan=plan,
                store=store,
                job_dir=job_dir,
                report=preflight,
            )

        monkeypatch.setattr(page, "_start_process", capture_start)
        page.run_button.click()
        application.processEvents()

        assert warnings == []
        assert isinstance(started["plan"], ExecutionPlan)
        assert started["report"] is report
        job_dir = started["job_dir"]
        assert isinstance(job_dir, type(output))
        assert (job_dir / "request.json").is_file()
        run_root = output / "experiments"
        run_dirs = tuple(run_root.iterdir())
        assert len(run_dirs) == 1
        assert (run_dirs[0] / "worker_request.json").is_file()
        assert json.loads((run_dirs[0] / "run_manifest.json").read_text())[
            "state"
        ] == "queued"
        assert page.name_availability.text() == "✕ Unavailable"
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
        assert not hasattr(window.results_page, "metadata_section")
        assert not hasattr(window.results_page, "metadata")
    finally:
        window.close()
        application.processEvents()


def test_results_load_latest_uses_completed_generic_experiment(tmp_path, monkeypatch):
    application = QApplication.instance() or QApplication([])
    called = {}
    expected = ResultRecord(
        summary_path="/tmp/summary.json",
        status="complete",
        completed_at="2026-09-03T19:56:46+00:00",
        title="DNp01 TTMn Gap Junction Validation",
        metadata={"Electrical contacts": 146},
        artifacts=(),
    )

    def fake_load(path):
        called["path"] = path
        return expected

    window = MainWindow()
    try:
        output = tmp_path / "runs"
        run = output / "experiments" / "dnp01-ttmn-gap-junction-validation"
        run.mkdir(parents=True)
        summary = run / "summary.json"
        summary.write_text("{}\n", encoding="utf-8")
        (run / "run_manifest.json").write_text(
            json.dumps({"state": "completed"}), encoding="utf-8"
        )
        window.overview_page.output_edit.setText(str(output))
        monkeypatch.setattr(window.results_page, "_load_result", fake_load)
        window.results_page.load_latest()
        assert called["path"] == summary
        assert window.results_page.result_status.text().startswith("complete")
    finally:
        window.close()
        application.processEvents()


def test_results_artifacts_default_closed_and_status_counts_failures(tmp_path):
    application = QApplication.instance() or QApplication([])
    existing = tmp_path / "existing.csv"
    existing.write_text("value\n1\n", encoding="utf-8")
    missing = tmp_path / "missing.csv"
    window = MainWindow()
    try:
        page = window.results_page
        assert page.artifact_section.is_expanded is False
        assert page.artifact_section.body.isHidden()

        page.display_result(
            ResultRecord(
                summary_path=str(tmp_path / "summary.json"),
                status="complete",
                completed_at="2026-09-04T12:00:00+00:00",
                title="Artifact status",
                metadata={},
                artifacts=(
                    Artifact("table", str(existing), "Existing", True),
                    Artifact("table", str(missing), "Missing", False),
                ),
            )
        )
        application.processEvents()
        assert page.artifact_section.status_label.text() == "1 failed"
        assert page.artifact_section.status_label.property("artifactState") == "warning"

        page.display_result(
            ResultRecord(
                summary_path=str(tmp_path / "summary.json"),
                status="complete",
                completed_at="2026-09-04T12:01:00+00:00",
                title="Artifact status",
                metadata={},
                artifacts=(Artifact("table", str(existing), "Existing", True),),
            )
        )
        application.processEvents()
        assert page.artifact_section.status_label.text() == "all pass"
        assert page.artifact_section.status_label.property("artifactState") == "pass"
        page.artifact_section.toggle_button.click()
        application.processEvents()
        assert page.artifact_section.is_expanded is True
        assert not page.artifact_section.body.isHidden()
    finally:
        window.close()
        application.processEvents()


def test_results_figure_preview_fits_source_inside_frame(tmp_path):
    application = QApplication.instance() or QApplication([])
    image_path = tmp_path / "figure.png"
    source = QPixmap(1500, 810)
    source.fill(Qt.GlobalColor.white)
    assert source.save(str(image_path), "PNG")
    window = MainWindow()
    try:
        window.resize(1180, 900)
        window.show()
        page = window.results_page
        window.show_page(window.pages.indexOf(page))
        page.display_result(
            ResultRecord(
                summary_path=str(tmp_path / "summary.json"),
                status="complete",
                completed_at="2026-09-04T12:00:00+00:00",
                title="Figure fit",
                metadata={},
                artifacts=(Artifact("image", str(image_path), "Figure", True),),
            )
        )
        application.processEvents()
        rendered = page.image_label.pixmap()
        assert not rendered.isNull()
        logical_size = rendered.deviceIndependentSize()
        assert logical_size.width() <= page.image_label.contentsRect().width()
        assert logical_size.height() <= page.image_label.contentsRect().height()
        assert page.image_scroll.horizontalScrollBar().maximum() == 0
        assert page.image_scroll.verticalScrollBar().maximum() == 0
        assert page.image_info.text() == "Source PNG 1500 × 810 px"
        assert page.full_resolution_button.isEnabled()
        page.full_resolution_button.click()
        application.processEvents()
        assert page._figure_dialog.isVisible()
        assert "full resolution" in page._figure_dialog.windowTitle()
        page._figure_dialog.close()
        application.processEvents()
    finally:
        window.close()
        application.processEvents()


def test_results_visualization_uses_run_packaged_swcs_and_marks_stimulus(tmp_path):
    application = QApplication.instance() or QApplication([])
    run = tmp_path / "runs" / "experiments" / "recorded-circuit"
    morphology_paths = {}
    for neuron_id, offset in (("10000", 0.0), ("10110", 10.0)):
        path = run / "morphologies" / neuron_id / "normalized_input.swc"
        path.parent.mkdir(parents=True)
        path.write_text(
            "\n".join(
                (
                    f"1 1 {offset} 0 0 2 -1",
                    f"2 3 {offset + 1} 0 0 0.5 1",
                    f"3 3 {offset + 2} 1 0 0.4 2",
                    "",
                )
            ),
            encoding="utf-8",
        )
        morphology_paths[neuron_id] = path
    (run / "summary.json").write_text("{}\n", encoding="utf-8")
    (run / "worker_request.json").write_text(
        json.dumps(
            {
                "morphologies": {
                    "10000": {"family": "DN", "neuron_type": "DNp01"},
                    "10110": {"family": "MN", "neuron_type": "TTMn"},
                }
            }
        ),
        encoding="utf-8",
    )
    (run / "circuit.json").write_text(
        json.dumps({"connectome": {"key": "manc:v1.2.1:full-local"}}),
        encoding="utf-8",
    )
    (run / "experiment.json").write_text(
        json.dumps(
            {
                "stimuli": [
                    {
                        "enabled": True,
                        "target_neuron_ids": ["10000"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (run / "voltage_traces.csv").write_text(
        "condition,repetition,neuron_id,time_ms,voltage_mV\n"
        "Control,0,10000,0,-60\n"
        "Control,0,10110,0,-60\n"
        "Control,0,10000,1,20\n"
        "Control,0,10110,1,-45\n"
        "Control,0,10000,2,-40\n"
        "Control,0,10110,2,15\n"
        "Gap disabled,0,10000,0,-60\n"
        "Gap disabled,0,10110,0,-60\n"
        "Gap disabled,0,10000,1,20\n"
        "Gap disabled,0,10110,1,-45\n"
        "Gap disabled,0,10000,2,-40\n"
        "Gap disabled,0,10110,2,-30\n",
        encoding="utf-8",
    )
    (run / "spikes.csv").write_text(
        "condition,repetition,neuron_id,spike_time_ms\n"
        "Control,0,10000,1\n"
        "Control,0,10110,1.5\n"
        "Gap disabled,0,10000,1\n",
        encoding="utf-8",
    )
    artifacts = tuple(
        Artifact("morphology", str(path), f"Normalized morphology · {neuron_id}", True)
        for neuron_id, path in morphology_paths.items()
    )
    window = MainWindow()
    try:
        page = window.results_page
        page.display_result(
            ResultRecord(
                summary_path=str(run / "summary.json"),
                status="complete",
                completed_at="2026-09-04T15:18:04+00:00",
                title="Recorded circuit",
                metadata={},
                artifacts=artifacts,
            )
        )
        application.processEvents()
        assert page.result_viewport.camera_only is True
        assert page.result_viewport.neuron_count == 2
        assert page.result_viewport.display_mode == "full_skeletons"
        assert page.result_viewport.highlighted_soma_ids == {"10000"}
        assert page.result_viewport.morphologies["10000"].record.neuron_type == "DNp01"
        assert page.result_viewport.morphologies["10110"].record.neuron_type == "TTMn"
        assert "DNp01 · body 10000 · STIMULATED" in page.circuit_identity.text()
        assert "TTMn · body 10110 · recorded" in page.circuit_identity.text()
        assert "loaded from this saved run" in page.circuit_info.text()
        assert page.circuit_identity_scroll.maximumHeight() == 156
        assert page.circuit_identity_scroll.widget() is page.circuit_identity
        assert page.activity_condition_combo.isEnabled()
        assert page.activity_condition_combo.count() == 2
        assert page.activity_condition_combo.itemText(0) == "Control"
        assert page.activity_play_button.isEnabled()
        page.activity_slider.setValue(2)
        application.processEvents()
        assert page.activity_time_label.text() == "2.00 ms"
        assert page.result_viewport.activity_segment_count > 0
        assert page.save_result_visualization_button.isEnabled()
        assert page.figure_stack.currentWidget() is page.voltage_plot
        assert page.voltage_plot.trace_count == 4
        assert page.voltage_plot.sample_count == 12
        assert page.voltage_2d_button.isEnabled()
        assert page.voltage_3d_button.isEnabled()
        assert not page.voltage_morphology_button.isEnabled()
        assert not page.circuit_panel.isHidden()
        page.expand_trace_button.click()
        application.processEvents()
        assert page.expand_trace_button.text() == "Restore split"
        assert not page.circuit_panel.isVisible()
        page.expand_trace_button.click()
        application.processEvents()
        assert page.expand_trace_button.text() == "Expand traces"
        first_trace = page.voltage_plot.traces[0]
        page.voltage_plot.legend_buttons[first_trace.key].click()
        application.processEvents()
        assert first_trace.key not in page.voltage_plot.canvas.active_keys
        page.voltage_3d_button.click()
        application.processEvents()
        assert page.voltage_plot.mode == "3d_stack"
    finally:
        window.close()
        application.processEvents()


def test_results_resolves_bmtk_safe_morphology_folders_to_biological_ids(tmp_path):
    application = QApplication.instance() or QApplication([])
    run = tmp_path / "runs" / "experiments" / "bmtk-recorded-circuit"
    identities = {
        "cell/a": ("cell-a-4f8f5f", "DNp01", "DN", 0.0),
        "cell-b": ("cell-b-a9d712", "TTMn", "MN", 10.0),
    }
    artifacts = []
    for neuron_id, (safe_key, _neuron_type, _family, offset) in identities.items():
        path = run / "morphologies" / safe_key / "normalized_input.swc"
        path.parent.mkdir(parents=True)
        path.write_text(
            f"1 1 {offset} 0 0 2 -1\n2 3 {offset + 1} 0 0 0.5 1\n",
            encoding="utf-8",
        )
        artifacts.append(
            Artifact(
                "morphology",
                str(path),
                f"Normalized morphology · {neuron_id}",
                True,
            )
        )
    (run / "summary.json").write_text("{}\n", encoding="utf-8")
    (run / "worker_request.json").write_text(
        json.dumps(
            {
                "morphologies": {
                    neuron_id: {"neuron_type": values[1], "family": values[2]}
                    for neuron_id, values in identities.items()
                }
            }
        ),
        encoding="utf-8",
    )
    (run / "circuit.json").write_text(
        json.dumps({"connectome": {"key": "manc:v1.2.1:full-local"}}),
        encoding="utf-8",
    )
    (run / "experiment.json").write_text(
        json.dumps(
            {
                "stimuli": [
                    {"enabled": True, "target_neuron_ids": ["cell/a"]}
                ]
            }
        ),
        encoding="utf-8",
    )

    window = MainWindow()
    try:
        page = window.results_page
        page.display_result(
            ResultRecord(
                summary_path=str(run / "summary.json"),
                status="complete",
                completed_at="2026-09-09T12:00:00+00:00",
                title="BMTK recorded circuit",
                metadata={},
                artifacts=tuple(artifacts),
            )
        )
        application.processEvents()
        assert set(page.result_viewport.morphologies) == {"cell/a", "cell-b"}
        assert page.result_viewport.morphologies["cell/a"].record.neuron_type == "DNp01"
        assert page.result_viewport.morphologies["cell-b"].record.neuron_type == "TTMn"
        assert page.result_viewport.highlighted_soma_ids == {"cell/a"}
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
        assert saved.circuit["schema_version"] == 3
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
