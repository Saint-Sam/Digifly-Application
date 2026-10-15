"""Actual Windows bundle setup and Run controls, using a tiny software fixture."""
from __future__ import annotations
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import time
import traceback


def run_neuron_package_smoke(destination: Path) -> int:
    from PySide6.QtCore import QSettings, Qt
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
    original_question, original_warning = QMessageBox.question, QMessageBox.warning
    errors = []
    try:
        os.environ["LOCALAPPDATA"] = str(destination / "Local App Data")
        profile_path = destination / "workspace/config/resources.json"
        make_standalone_profile(workstation_root=destination / "workspace").save(profile_path)
        os.environ[PROFILE_PATH_ENV] = str(profile_path)
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(destination / "settings"))
        app = QApplication.instance() or QApplication([])
        from digifly_app.ui.main_window import MainWindow, ORGANIZATION_NAME, APPLICATION_NAME
        settings = QSettings(ORGANIZATION_NAME, APPLICATION_NAME)
        settings.setValue("output_root", str(destination / "workspace/runs"))
        settings.setValue("workspace_root", "")
        window = MainWindow()
        window.show()
        dialog = RuntimeSetupDialog(parent=window)
        dialog.runtimes_selected.connect(window.overview_page._runtime_selected)
        dialog.show()
        QTest.qWait(500)
        assert dialog.install_neuron_button.isVisible(), "Included setup control absent"
        # Explicit engineering CLI authorizes this isolated test installation.
        # No simulator/preflight/result is mocked or replaced.
        QMessageBox.question = lambda *args, **kwargs: QMessageBox.StandardButton.Yes if len(args)>1 and args[1]=="Install included NEURON?" else QMessageBox.StandardButton.No
        QMessageBox.warning = lambda *args, **kwargs: errors.append(str(args[1:])) or QMessageBox.StandardButton.Ok
        QTest.mouseClick(dialog.install_neuron_button, Qt.MouseButton.LeftButton)
        deadline = time.monotonic() + 180
        while dialog._thread is not None and time.monotonic() < deadline:
            QTest.qWait(100)
        assert dialog._thread is None and dialog.use_button.isEnabled(), dialog.status.text()
        dialog.grab().save(str(destination / "installed-neuron-dialog.png"))
        QTest.mouseClick(dialog.use_button, Qt.MouseButton.LeftButton)
        python = default_install_root() / "python/python.exe"
        assert Path(window.overview_page.python_edit.text()) == python
        assert str(python) in profile_path.read_text().replace("\\\\", "\\")
        health = check_neuron_health(python)
        source = destination / "fixture.swc"
        source.write_text("1 1 0 0 0 5 -1\n2 1 0 0 5 5 1\n3 3 0 0 10 2 2\n4 3 0 0 50 1 3\n", "utf-8")
        sha = hashlib.sha256(source.read_bytes()).hexdigest()
        morphology = load_swc(NeuronRecord("1", "test", "software fixture", str(source), "test:v1"))
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
        window.pages.setCurrentWidget(page)
        page.run_button.click()
        deadline = time.monotonic() + 90
        while page._process is not None and time.monotonic() < deadline:
            QTest.qWait(100)
        assert not errors, errors
        assert page._process is None and page._plan is not None, page.validation_state.text()
        root = Path(page._plan.working_directory)
        manifest = json.loads((root / "run_manifest.json").read_text())
        assert manifest["state"] == "completed", page.run_log.toPlainText()
        rows = list(csv.DictReader((root / "voltage_traces.csv").open()))
        volts = [float(row["voltage_mV"]) for row in rows]
        assert len(volts)>50 and all(math.isfinite(v) for v in volts) and max(volts)-min(volts)>1
        assert hashlib.sha256(source.read_bytes()).hexdigest()==sha
        window.grab().save(str(destination / "neuron-app-run.png"))
        record.update(ok=True, health=health, ui_install=True, ui_runtime_binding=True,
            ui_run=True, finite_voltage_samples=len(volts), min_mV=min(volts), max_mV=max(volts),
            run_root=str(root), source_unchanged=True, registry_changed=False, global_path_changed=False)
        window.close()
        app.processEvents()
    except Exception:
        record["error"] = traceback.format_exc()
    finally:
        QMessageBox.question, QMessageBox.warning = original_question, original_warning
    (destination / "receipt.json").write_text(json.dumps(record, indent=2) + "\n", "utf-8")
    return 0 if record["ok"] else 1
