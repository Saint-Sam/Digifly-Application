"""Actual Windows bundle setup and Run controls, using a tiny software fixture."""
from __future__ import annotations
import csv
import faulthandler
import hashlib
import json
import math
import os
import sys
from pathlib import Path
import time
import tempfile
import traceback


def _wait_until(predicate, timeout: float) -> bool:
    """Let Qt release the GIL normally while Python workers perform IO.

    Repeated QTest.qWait calls from a compiled polling loop can starve its
    Python background installer. A real event loop matches normal app use.
    """
    from PySide6.QtCore import QEventLoop, QTimer
    if predicate():
        return True
    loop = QEventLoop()
    timer = QTimer()
    deadline = time.monotonic() + timeout
    def poll():
        if predicate() or time.monotonic() >= deadline:
            loop.quit()
    timer.timeout.connect(poll)
    timer.start(25)
    try:
        loop.exec()
    finally:
        timer.stop()
    return bool(predicate())


def dispose_neuron_fixture(window, app):
    """Delete only idle engineering widgets before Python begins finalization."""
    from .runtime_setup import RuntimeSetupDialog
    from .package_library_smoke import dispose_fixture_window
    if getattr(getattr(window,'experiment_page',None),'_process',None) is not None:
        raise RuntimeError('Refusing to destroy an active fixture experiment')
    if any(dialog._thread is not None for dialog in window.findChildren(RuntimeSetupDialog)):
        raise RuntimeError('Refusing to destroy an active fixture installation')
    dispose_fixture_window(window,app)


def run_neuron_package_smoke(destination: Path) -> int:
    from PySide6.QtCore import Qt
    from digifly_app.core.app_settings import app_settings, SETTINGS_ROOT_ENV
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QMessageBox
    from digifly_app.core.resource_profile import PROFILE_PATH_ENV, make_standalone_profile, ResourceProfile
    from digifly_app.core.windows_neuron_bundle import default_install_root, check_neuron_health
    from digifly_app.core.connectomes import NeuronRecord
    from digifly_app.core.circuit import CircuitSpec, ConnectomeRef
    from digifly_app.core.experiment import ExperimentSpec, StimulusSpec, ConditionSpec, RecordingSpec
    from digifly_app.core.morphology import load_swc
    from digifly_app.ui.runtime_setup import RuntimeSetupDialog
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=False)
    record = {"ok": False, "scope": "Windows private NEURON install plus actual app Run on tiny engineering fixture; not user-selected presets/paper/equivalence acceptance"}
    original_question, original_warning, original_critical, original_information = QMessageBox.question, QMessageBox.warning, QMessageBox.critical, QMessageBox.information
    errors = []
    window = None
    def checkpoint(phase: str) -> None:
        record["phase"] = phase
        (destination / "receipt.json").write_text(json.dumps(record, indent=2) + "\n", "utf-8")
    stacks = (destination / "thread-stacks.txt").open("w", encoding="utf-8")
    faulthandler.enable(file=stacks, all_threads=True)
    faulthandler.dump_traceback_later(45, repeat=True, file=stacks)
    try:
        checkpoint("prepare isolated settings")
        # Installation must remain outside both a source checkout and the
        # extracted read-only app. Evidence can live in the requested folder.
        os.environ["LOCALAPPDATA"] = str(Path(tempfile.mkdtemp(prefix="digifly-neuron-check-")) / "Local App Data")
        profile_path = destination / "workspace/config/resources.json"
        make_standalone_profile(workstation_root=destination / "workspace").save(profile_path)
        os.environ[PROFILE_PATH_ENV] = str(profile_path)
        os.environ[SETTINGS_ROOT_ENV] = str(destination / "settings")
        app = QApplication.instance() or QApplication([])
        QMessageBox.question = lambda *args, **kwargs: QMessageBox.StandardButton.Yes if len(args)>1 and args[1]=="Install included NEURON?" else QMessageBox.StandardButton.No
        QMessageBox.warning = lambda *args, **kwargs: errors.append(str(args[1:])) or QMessageBox.StandardButton.Ok
        QMessageBox.critical = lambda *args, **kwargs: errors.append(str(args[1:])) or QMessageBox.StandardButton.Ok
        QMessageBox.information = lambda *args, **kwargs: errors.append(str(args[1:])) or QMessageBox.StandardButton.Ok
        from digifly_app.ui.main_window import MainWindow, ORGANIZATION_NAME, APPLICATION_NAME
        settings = app_settings(ORGANIZATION_NAME, APPLICATION_NAME)
        settings.setValue("output_root", str(destination / "workspace/runs"))
        settings.setValue("workspace_root", "")
        checkpoint("construct actual main window")
        window = MainWindow()
        window.show()
        dialog = RuntimeSetupDialog(parent=window)
        dialog.runtimes_selected.connect(window.overview_page._runtime_selected)
        dialog.show()
        QTest.qWait(500)
        assert dialog.install_neuron_button.isVisible(), "Included setup control absent"
        # Explicit engineering CLI authorizes this isolated test installation.
        # No simulator/preflight/result is mocked or replaced.
        checkpoint("click included NEURON installation")
        QTest.mouseClick(dialog.install_neuron_button, Qt.MouseButton.LeftButton)
        _wait_until(lambda: dialog._thread is None, 180)
        assert dialog._thread is None and dialog.use_button.isEnabled(), dialog.status.text()
        checkpoint("installed runtime; select binding")
        dialog.grab().save(str(destination / "installed-neuron-dialog.png"))
        QTest.mouseClick(dialog.use_button, Qt.MouseButton.LeftButton)
        python = default_install_root() / "bin/python.exe"
        assert Path(window.overview_page.python_edit.text()) == python
        assert str(python) in profile_path.read_text().replace("\\\\", "\\")
        health = check_neuron_health(python)
        checkpoint("private health passed; prepare tiny Run fixture")
        source = destination / "fixture.swc"
        source.write_text("1 1 0 0 0 5 -1\n2 1 0 0 5 5 1\n3 3 0 0 10 2 2\n4 3 0 0 50 1 3\n", "utf-8")
        sha = hashlib.sha256(source.read_bytes()).hexdigest()
        morphology = load_swc(NeuronRecord("1", "MN", "TTMn", str(source), "test:v1", ("TTMn_R",)))
        circuit = CircuitSpec(connectome=ConnectomeRef("test:v1", "Software fixture", str(destination)),
            neuron_ids=("1",), morphology_sha256={"1": sha})
        config = ExperimentSpec(name="Included NEURON installation Run check", engine="neuron",
            duration_ms=5, integration_dt_ms=0.025, temperature_C=22,
            stimuli=(StimulusSpec(waveform="step", amplitude_nA=0.1, delay_ms=1, pulse_width_ms=2, pulse_count=1),),
            conditions=(ConditionSpec(name="Control"),),
            recording=RecordingSpec(target_region="soma", sample_dt_ms=0.05, make_plots=True))
        page = window.experiment_page
        page.set_circuit_snapshot(circuit, (morphology,))
        page.set_config(config)
        page.stimulus_targets.setText("ttmn_r")
        page.recording_targets.setText("TTMn")
        assert page.config().stimuli[0].target_neuron_ids == ("1",)
        assert page.config().recording.target_neuron_ids == ("1",)
        window.pages.setCurrentWidget(page)
        checkpoint("click actual app Run; synchronous preflight")
        page.run_button.click()
        checkpoint("await actual app worker completion")
        _wait_until(lambda: page._process is None, 90)
        checkpoint("worker returned; inspect saved output")
        assert not errors, errors
        assert page._process is None and page._plan is not None, page.validation_state.text()
        root = Path(page._plan.working_directory)
        manifest = json.loads((root / "run_manifest.json").read_text())
        assert manifest["state"] == "completed", page.run_log.toPlainText()
        from digifly_app.core.result_voltage import load_voltage_table
        table = load_voltage_table(root/'voltage_traces.csv',cache_root=destination/'display-cache')
        volts = [v for samples in table.series.values() for v in samples.values]
        assert len(volts)>50 and all(math.isfinite(v) for v in volts) and max(volts)-min(volts)>1
        assert hashlib.sha256(source.read_bytes()).hexdigest()==sha
        from .circuit_viewport import CircuitViewport, _WindowsCircuitViewport
        viewports = window.findChildren(CircuitViewport)
        assert viewports, 'No actual circuit renderer in the app'
        if sys.platform == 'win32':
            assert all(isinstance(view, _WindowsCircuitViewport) for view in viewports)
        window.grab().save(str(destination / "neuron-app-run.png"))
        record.update(health=health, ui_install=True, ui_runtime_binding=True,
            ui_run=True, finite_voltage_samples=len(volts), min_mV=min(volts), max_mV=max(volts),
            run_root=str(root), installed_python=str(python), source_unchanged=True, registry_changed=False, global_path_changed=False,
            viewport_backend=CircuitViewport.__name__, viewport_count=len(viewports))
        checkpoint("close isolated app")
        dispose_neuron_fixture(window,app)
        window = None
        record.update(ok=True, fixture_widgets_deleted=True)
        checkpoint("isolated app closed")
    except Exception:
        record['ok'] = False
        record["error"] = traceback.format_exc()
    finally:
        if window is not None:
            try:
                dispose_neuron_fixture(window,app)
            except Exception:
                record['ok'] = False
                record['shutdown_error'] = traceback.format_exc()
        QMessageBox.question, QMessageBox.warning, QMessageBox.critical, QMessageBox.information = original_question, original_warning, original_critical, original_information
        faulthandler.cancel_dump_traceback_later()
        faulthandler.disable()
        stacks.close()
    (destination / "receipt.json").write_text(json.dumps(record, indent=2) + "\n", "utf-8")
    return 0 if record["ok"] else 1


def run_reduced_hh_package_smoke(destination: Path, runtime: Path) -> int:
    """Run a short Reduced HH experiment in the packaged GUI."""
    from PySide6.QtWidgets import QApplication, QMessageBox
    from digifly_app.core.app_settings import SETTINGS_ROOT_ENV, app_settings
    from digifly_app.core.circuit import CircuitSpec, ConnectomeRef
    from digifly_app.core.connectomes import NeuronRecord
    from digifly_app.core.experiment import ExperimentSpec, StimulusSpec, ConditionSpec, RecordingSpec
    from digifly_app.core.morphology import load_swc
    from digifly_app.core.resource_profile import PROFILE_PATH_ENV, make_standalone_profile
    from digifly_app.ui.student_readiness_check import capture_qt_callback_errors

    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=False)
    workspace = destination / "workspace"
    profile = workspace / "config/resources.json"
    make_standalone_profile(workstation_root=workspace).save(profile)
    os.environ[PROFILE_PATH_ENV] = str(profile)
    os.environ[SETTINGS_ROOT_ENV] = str(destination / "settings")
    from digifly_app.ui.main_window import MainWindow, ORGANIZATION_NAME, APPLICATION_NAME

    app = QApplication.instance() or QApplication([])
    settings = app_settings(ORGANIZATION_NAME, APPLICATION_NAME)
    settings.setValue("output_root", str(workspace / "runs"))
    warning = QMessageBox.warning
    warnings = []
    QMessageBox.warning = lambda *args, **kwargs: warnings.append(str(args[1:])) or QMessageBox.StandardButton.Ok
    outcome = {"ok": False, "engine": "reduced_hh", "playback": False}
    window = None
    try:
        with capture_qt_callback_errors() as callbacks:
            source = destination / "fixture.swc"
            source.write_text(
                "1 1 0 0 0 0.5 -1\n2 1 0 0 0.05 0.5 1\n3 2 0 0 0.10 0.1 2\n"
                "4 2 0 0 0.15 0.1 3\n5 2 0 0 0.20 0.1 4\n6 2 0 0 0.25 0.1 5\n", "utf-8")
            identity = hashlib.sha256(source.read_bytes()).hexdigest()
            record = NeuronRecord("1", "IN", "Fixture", str(source), "test:v1")
            morphology = load_swc(record)
            circuit = CircuitSpec(connectome=ConnectomeRef("test:v1", "Fixture", str(destination)),
                                  neuron_ids=("1",), morphology_sha256={"1": identity})
            config = ExperimentSpec(
                name="Reduced HH package check", engine="reduced_hh", duration_ms=2.0,
                integration_dt_ms=0.01,
                stimuli=(StimulusSpec(waveform="step", target_neuron_ids=("1",), delay_ms=0.2,
                                     pulse_width_ms=0.6, amplitude_nA=0.003),),
                recording=RecordingSpec(target_region="all", target_neuron_ids=("1",),
                                        sample_dt_ms=0.05, make_plots=False),
                conditions=(ConditionSpec(name="Control"),),
            )
            window = MainWindow()
            window.show()
            page = window.experiment_page
            page.set_neuron_runtime(runtime)
            page.set_circuit_snapshot(circuit, (morphology,))
            page.set_config(config)
            assert page.engine_combo.currentText() == "Reduced HH"
            window.pages.setCurrentWidget(page)
            page.run_button.click()
            assert page._plan is not None and not warnings, warnings or page.validation_state.text()
            assert _wait_until(lambda: page._process is None, 45)
            result = window.results_page
            assert _wait_until(lambda: not result.loading, 30)
            assert result._voltage_table is not None, result.result_load_note.text()
            assert result._voltage_table.sample_count == 205
            assert result.voltage_morphology_button.isEnabled()
            result.voltage_morphology_button.click()
            assert result.activity_slider.isEnabled()
            assert "grouped edges share a trace" in result.activity_method.text()
            result.activity_slider.setValue(14)
            run = Path(page._plan.working_directory)
            summary = json.loads((run / "summary.json").read_text("utf-8"))
            assert summary["engine"] == "reduced_hh" and summary["status"] == "complete"
            assert hashlib.sha256(source.read_bytes()).hexdigest() == identity
            series = result._voltage_table.series
            voltages = [v for samples in series.values() for v in samples.values]
            assert all(math.isfinite(v) for v in voltages) and max(voltages) - min(voltages) > 0.01
            outcome.update(ok=True, playback=True, voltage_samples=len(voltages),
                           engine_runtime=summary["metadata"]["Simulator version"],
                           run_summary_sha256=hashlib.sha256((run / "summary.json").read_bytes()).hexdigest())
            dispose_neuron_fixture(window, app)
            window = None
            assert not callbacks, callbacks
    except Exception:
        outcome.update(ok=False, error=traceback.format_exc())
    finally:
        if window is not None:
            if window.experiment_page._process is not None:
                window.experiment_page._process.kill()
                window.experiment_page._process.waitForFinished(3000)
                app.processEvents()
            try:
                dispose_neuron_fixture(window, app)
            except Exception:
                outcome.update(ok=False, shutdown_error=traceback.format_exc())
        QMessageBox.warning = warning
    (destination / "receipt.json").write_text(json.dumps(outcome, indent=2) + "\n", "utf-8")
    return 0 if outcome["ok"] else 1
