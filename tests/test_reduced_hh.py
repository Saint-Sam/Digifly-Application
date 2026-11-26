from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from digifly_app.core.circuit import CircuitSpec, ConnectomeRef
from digifly_app.core.connectomes import NeuronRecord
from digifly_app.core.experiment import ConditionSpec, ExperimentSpec, RecordingSpec, StimulusSpec
from digifly_app.core.morphology import load_swc
from digifly_app.core.process_environment import sanitized_external_environment
from digifly_app.core.result_voltage import load_voltage_table
from digifly_app.engines.generic_experiment import GenericExperimentAdapter, load_generic_experiment_result


def _model(tmp_path):
    swc = tmp_path / "cable.swc"
    swc.write_text(
        "1 1 0 0 0 0.5 -1\n2 1 0 0 0.05 0.5 1\n3 2 0 0 0.10 0.1 2\n"
        "4 2 0 0 0.15 0.1 3\n5 2 0 0 0.20 0.1 4\n6 2 0 0 0.25 0.1 5\n",
        encoding="utf-8",
    )
    connectome = ConnectomeRef("test:v1", "Test", str(tmp_path))
    morphology = load_swc(NeuronRecord("1", "IN", "Example", str(swc), connectome.key))
    circuit = CircuitSpec(connectome=connectome, neuron_ids=("1",),
                          morphology_sha256={"1": hashlib.sha256(swc.read_bytes()).hexdigest()})
    experiment = ExperimentSpec(
        name="Reduced worker smoke", engine="reduced_hh", duration_ms=2,
        integration_dt_ms=0.01,
        stimuli=(StimulusSpec(waveform="step", delay_ms=0.2, pulse_width_ms=0.6,
                             amplitude_nA=0.003, target_neuron_ids=("1",)),),
        recording=RecordingSpec(target_region="all", target_neuron_ids=("1",),
                                sample_dt_ms=0.05, make_plots=False),
        conditions=(ConditionSpec(name="Control"),),
    )
    return circuit, experiment, (morphology,)


def _assert_result(result, tmp_path):
    assert result.metadata["Engine"] == "reduced_hh"
    assert result.status == "complete"
    assert all(a.exists for a in result.artifacts)
    artifact = next(a for a in result.artifacts if a.path.endswith("/voltage/ready.json"))
    table = load_voltage_table(Path(artifact.path), cache_root=tmp_path / "display-cache")
    assert table.has_compartments and table.sample_count == 205
    plan = Path(result.summary_path).parent / "condition_execution.json"
    outcome = json.loads(plan.read_text())["conditions"][0]["reduced_hh_trials"][0]
    mapped = outcome["cells"]["1"]
    assert mapped["source_edges"] == 5
    assert mapped["membrane_states"] < mapped["source_edges"]
    assert mapped["source_node_to_membrane_state"]["3"] != mapped["source_node_to_membrane_state"]["2"]


def test_reduced_worker_normal_entry_and_recording(tmp_path):
    circuit, experiment, morphologies = _model(tmp_path)
    adapter = GenericExperimentAdapter({"reduced_hh": sys.executable})
    report = adapter.validate(circuit, experiment, morphologies, output_root=tmp_path)
    assert report.ok, report.to_dict()
    plan = adapter.plan(circuit, experiment, output_root=tmp_path)
    adapter.write_request(plan.arguments[-1], adapter.request_payload(
        circuit, experiment, morphologies, report, output_root=tmp_path))
    process = subprocess.run([plan.program, *plan.arguments], cwd=plan.working_directory,
                             env=sanitized_external_environment(plan.environment),
                             capture_output=True, text=True, timeout=60)
    assert process.returncode == 0, process.stdout + process.stderr
    _assert_result(load_generic_experiment_result(plan.expected_summary_path), tmp_path)


def test_reduced_worker_refuses_current_at_readiness_and_execution(tmp_path):
    circuit, experiment, morphologies = _model(tmp_path)
    unsupported = replace(experiment, recording=replace(experiment.recording, current_tracking="full"))
    adapter = GenericExperimentAdapter({"reduced_hh": sys.executable})
    report = adapter.validate(circuit, unsupported, morphologies, output_root=tmp_path)
    assert not report.ok
    assert "current tracking" in next(c.detail for c in report.checks if c.key == "reduced_hh")
    assert not (tmp_path / "experiments").exists()
    # Worker must also refuse an edited request independently of the app check.
    plan = adapter.plan(circuit, unsupported, output_root=tmp_path)
    adapter.write_request(plan.arguments[-1], adapter.request_payload(
        circuit, unsupported, morphologies, report, output_root=tmp_path))
    process = subprocess.run([plan.program, *plan.arguments], cwd=plan.working_directory,
                             env=sanitized_external_environment(plan.environment),
                             capture_output=True, text=True, timeout=60)
    assert process.returncode == 1
    assert "current tracking" in process.stdout
    assert not Path(plan.expected_summary_path).exists()


def test_reduced_hh_experiment_builder_run_button(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMessageBox
    from digifly_app.ui.experiment_builder import ExperimentBuilderPage
    from digifly_app.ui.main_window import OverviewPage, ResultsPage
    from digifly_app.ui.result_playback import load_morphology_voltage_tracks, measured_segment_voltages

    app = QApplication.instance() or QApplication([])
    circuit, experiment, morphologies = _model(tmp_path)
    page = ExperimentBuilderPage(output_root=tmp_path, neuron_runtime=sys.executable)
    overview = OverviewPage()
    results_page = ResultsPage(overview, page)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    results = []
    page.result_ready.connect(results.append)
    page.result_ready.connect(results_page.display_result)
    page.set_circuit_snapshot(circuit, morphologies)
    page.set_config(experiment)
    assert page.engine_combo.currentText() == "Reduced HH"
    assert page.config().engine == experiment.engine
    try:
        page.validate_draft()
        assert "Preflight passed" in page.validation_state.text()
        page.run_button.click()
        deadline = time.monotonic() + 35
        while not results and not warnings and page._process is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert not warnings, warnings
        assert len(results) == 1, page.run_log.toPlainText()
        _assert_result(results[0], tmp_path)
        run = Path(results[0].summary_path).parent
        normalized, = (run / "morphologies").glob("*/normalized_input.swc")
        displayed = load_swc(replace(morphologies[0].record, swc_path=str(normalized)))
        track, = load_morphology_voltage_tracks(run, (displayed,))
        at_rest = measured_segment_voltages(track, 0.0)["1"]
        during_step = measured_segment_voltages(track, 0.7)["1"]
        assert at_rest.keys() == during_step.keys() == {2, 3, 4, 5, 6}
        assert any(abs(during_step[k] - v) > 0.01 for k, v in at_rest.items())
        deadline = time.monotonic() + 10
        while results_page.loading and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert results_page.voltage_morphology_button.isEnabled()
        results_page.voltage_morphology_button.click()
        assert "grouped edges share a trace" in results_page.activity_method.text()
        assert results_page.activity_slider.isEnabled()
    finally:
        if page._process is not None:
            page._process.kill()
            page._process.waitForFinished(3000)
            app.processEvents()
        page.close()
        results_page.close()
        overview.close()
