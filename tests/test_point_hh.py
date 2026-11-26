from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from digifly_app.core.circuit import CircuitSpec, ConnectomeRef
from digifly_app.core.experiment import ConditionSpec, ExperimentSpec, RecordingSpec, StimulusSpec
from digifly_app.engines.generic_experiment import GenericExperimentAdapter


def _request(tmp_path: Path, *, current_tracking: str = "off") -> Path:
    experiment = ExperimentSpec(
        name="point smoke",
        engine="point_hh",
        duration_ms=2.0,
        integration_dt_ms=0.01,
        initial_voltage_mV=-65.0,
        stimuli=(StimulusSpec(
            waveform="step", delay_ms=0.0, pulse_width_ms=1.0,
            amplitude_nA=0.1, target_neuron_ids=("1",),
        ),),
        conditions=(
            ConditionSpec(name="Control"),
            ConditionSpec(name="Connections off", chemical_synapses_enabled=False, gap_junctions_enabled=False),
        ),
        recording=RecordingSpec(
            target_neuron_ids=("1", "2"), sample_dt_ms=0.05,
            make_plots=False, current_tracking=current_tracking,
        ),
    ).to_dict()
    circuit = CircuitSpec(
        connectome=ConnectomeRef("test:v1", "Test", str(tmp_path)),
        neuron_ids=("1", "2"),
    ).to_dict()
    edge_manifest = {
        "schema_version": 2,
        "kind": "point_selected_subgraph",
        "neuron_ids": ["1", "2"],
        "chemical_synapse_policy": circuit["chemical_synapse_policy"],
        "gap_junction_policy": circuit["gap_junction_policy"],
        "chemical_edges": [{
            "contact_id": "chem-0000001", "source_edge_rowid": "1",
            "source_edge_id": "test-1-2", "pre_id": "1", "post_id": "2",
            "weight_uS": 0.001, "delay_ms": 0.1, "tau1_ms": 0.5,
            "tau2_ms": 3.0, "reversal_mV": 0.0,
        }],
        "electrical_edges": [],
    }
    edge_path = tmp_path / "edge_manifest.json"
    edge_path.write_text(json.dumps(edge_manifest), encoding="utf-8")
    (tmp_path / "experiment.json").write_text(json.dumps(experiment), encoding="utf-8")
    (tmp_path / "circuit.json").write_text(json.dumps(circuit), encoding="utf-8")
    (tmp_path / "run_manifest.json").write_text(json.dumps({"state": "queued"}), encoding="utf-8")
    request_path = tmp_path / "worker_request.json"
    request_path.write_text(json.dumps({
        "schema_version": 1, "output_dir": str(tmp_path), "engine": "point_hh",
        "experiment": experiment, "circuit": circuit,
        "point_hh_parameters": {
            "effective_area_cm2": {"1": 1e-6, "2": 2e-6},
            "soma_area_cm2": {"1": 2e-7, "2": 4e-7},
            "branch_area_cm2": {"1": 8e-7, "2": 16e-7},
        },
        "edge_manifest_path": str(edge_path),
    }), encoding="utf-8")
    return request_path


def test_point_hh_is_an_additive_experiment_engine(tmp_path):
    circuit = CircuitSpec(
        connectome=ConnectomeRef("test:v1", "Test", str(tmp_path)),
        neuron_ids=("1",),
    )
    experiment = ExperimentSpec(
        name="point preflight", engine="point_hh", duration_ms=110.0,
        stimuli=(StimulusSpec(target_neuron_ids=("1",)),),
        conditions=(ConditionSpec(name="Control"),),
        recording=RecordingSpec(target_neuron_ids=("1",), sample_dt_ms=0.05, make_plots=False),
    )
    report = GenericExperimentAdapter({"point_hh": sys.executable}).validate(
        circuit, experiment, (), output_root=tmp_path / "runs"
    )
    assert report.ok
    assert {check.key for check in report.checks} >= {
        "point_mechanisms", "point_stimuli", "point_conditions", "point_recording",
        "point_morphology", "recording_samples",
    }
    details = {check.key: check.detail for check in report.checks}
    assert "no spatial injection sites" in details["point_stimuli"]
    assert "do not create spatial sites" in details["point_morphology"]


def test_point_hh_worker_writes_results_and_current_recording(tmp_path):
    request = _request(tmp_path, current_tracking="sparse")
    completed = subprocess.run(
        [sys.executable, "-B", "src/digifly_app/workers/point_hh_worker.py", "--request", str(request)],
        capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["engine"] == "point_hh"
    assert summary["status"] == "complete"
    assert (tmp_path / "voltage_traces.csv").is_file()
    assert (tmp_path / "spikes.csv").is_file()
    assert (tmp_path / "current_traces.csv").is_file()
    assert json.loads((tmp_path / "run_manifest.json").read_text(encoding="utf-8"))["state"] == "completed"
    from digifly_app.core.result_voltage import load_voltage_table
    table = load_voltage_table(
        tmp_path / "voltage_traces.csv", cache_root=tmp_path / "display-cache"
    )
    assert table.sample_count > 0
    assert len(table.series) == 4
    assert not table.morphology_error
    execution = json.loads((tmp_path / "condition_execution.json").read_text(encoding="utf-8"))
    assert [item["edge_counts"]["chemical"] for item in execution["conditions"]] == [1, 0]
    assert [item["edge_counts"]["electrical"] for item in execution["conditions"]] == [0, 0]


def test_point_hh_worker_honors_safe_cancellation(tmp_path):
    request = _request(tmp_path)
    (tmp_path / "cancel.requested").write_text("requested\n", encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, "-B", "src/digifly_app/workers/point_hh_worker.py", "--request", str(request)],
        capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 2, completed.stdout + completed.stderr
    assert json.loads((tmp_path / "run_manifest.json").read_text(encoding="utf-8"))["state"] == "cancelled"


def test_point_hh_request_keeps_circuit_edges_but_omits_morphology(tmp_path):
    circuit = CircuitSpec(
        connectome=ConnectomeRef("test:v1", "Test", str(tmp_path)),
        neuron_ids=("1",),
    )
    experiment = ExperimentSpec(
        name="point request", engine="point_hh", duration_ms=110.0,
        stimuli=(StimulusSpec(target_neuron_ids=("1",)),),
        conditions=(ConditionSpec(name="Control"),),
        recording=RecordingSpec(target_neuron_ids=("1",), sample_dt_ms=0.05, make_plots=False),
    )
    adapter = GenericExperimentAdapter({"point_hh": sys.executable})
    report = adapter.validate(circuit, experiment, (), output_root=tmp_path / "runs")
    assert report.ok
    payload = adapter.request_payload(circuit, experiment, (), report, output_root=tmp_path / "runs")
    assert payload["engine"] == "point_hh"
    assert payload["morphologies"] == {}
    assert payload["edge_manifest"]["kind"] == "point_selected_subgraph"


def test_point_hh_uses_neuron_temperature_q10_and_exp2syn_normalization():
    import numpy as np
    from digifly_app.workers.point_hh_worker import _exp2syn_peak_factor, _hh_rates

    cold = _hh_rates(np.asarray([-65.0]), 6.3)
    warm = _hh_rates(np.asarray([-65.0]), 22.0)
    q10 = 3.0 ** ((22.0 - 6.3) / 10.0)
    for cold_rate, warm_rate in zip(cold, warm):
        assert np.isclose(float(warm_rate[0] / cold_rate[0]), q10)

    factor = _exp2syn_peak_factor(0.5, 3.0)
    peak_time = 0.5 * 3.0 / (3.0 - 0.5) * np.log(3.0 / 0.5)
    peak = np.exp(-peak_time / 3.0) - np.exp(-peak_time / 0.5)
    assert np.isclose(factor * peak, 1.0)


def test_point_hh_area_weights_regional_channels_and_passive_leak():
    from digifly_app.core.circuit import CircuitSpec, ConnectomeRef
    from digifly_app.workers.point_hh_worker import _hh_values

    circuit = CircuitSpec(
        connectome=ConnectomeRef("test:v1", "Test", "/tmp"),
        neuron_ids=("1",),
    ).to_dict()
    values, passive = _hh_values(circuit, "1", {}, soma_area_fraction=0.25)
    assert not passive
    assert values["effective_gnabar_s_cm2"] == 0.045
    assert values["effective_gkbar_s_cm2"] == 0.0165
    assert values["effective_gl_s_cm2"] == 0.00025
    assert values["effective_el_mV"] == -65.0


def test_point_hh_readiness_refuses_unrepresentable_soma_ais_scope(tmp_path):
    circuit_data = CircuitSpec(
        connectome=ConnectomeRef("test:v1", "Test", str(tmp_path)),
        neuron_ids=("1",),
    ).to_dict()
    circuit_data["hh"]["active_scope"] = "soma_ais"
    circuit = CircuitSpec.from_dict(circuit_data)
    experiment = ExperimentSpec(
        name="point soma AIS refusal", engine="point_hh", duration_ms=2.0,
        stimuli=(StimulusSpec(target_neuron_ids=("1",)),),
        conditions=(ConditionSpec(name="Control"),),
        recording=RecordingSpec(target_neuron_ids=("1",), sample_dt_ms=0.05, make_plots=False),
    )
    report = GenericExperimentAdapter({"point_hh": sys.executable}).validate(
        circuit, experiment, (), output_root=tmp_path / "runs"
    )
    regional = next(check for check in report.checks if check.key == "point_regions")
    assert regional.state.value == "fail"
    assert "soma/AIS split" in regional.detail
