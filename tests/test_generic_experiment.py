from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest

from digifly_app.core.circuit import CircuitSpec, ConnectomeRef
from digifly_app.core.connectomes import NeuronRecord
from digifly_app.core.experiment import (
    ConditionSpec,
    ExperimentSpec,
    RecordingSpec,
    StimulusSpec,
)
from digifly_app.core.models import CheckState, PreflightCheck, PreflightReport
from digifly_app.core.morphology import Morphology, load_swc
from digifly_app.core.process_environment import sanitized_external_environment
from digifly_app.engines import generic_experiment as generic
from digifly_app.workers.generic_experiment_worker import _normalize_swc


def _morphology(tmp_path: Path, neuron_id: str = "1") -> Morphology:
    path = tmp_path / f"{neuron_id}.swc"
    path.write_text(
        "1 1 0 0 0 5 -1\n"
        "3 3 0 0 10 2 2\n"
        "2 1 0 0 5 5 1\n"
        "4 3 0 0 50 1 3\n",
        encoding="utf-8",
    )
    return load_swc(
        NeuronRecord(
            neuron_id,
            "test",
            "synthetic",
            str(path),
            "test:v1",
        )
    )


def _experiment(engine: str = "arbor") -> ExperimentSpec:
    return ExperimentSpec(
        name=f"{engine.title()} executable smoke",
        engine=engine,
        duration_ms=5.0,
        integration_dt_ms=0.025,
        temperature_C=22.0,
        stimuli=(
            StimulusSpec(
                waveform="step",
                amplitude_nA=0.1,
                delay_ms=1.0,
                pulse_width_ms=2.0,
                pulse_count=1,
            ),
        ),
        conditions=(ConditionSpec(name="Control"),),
        recording=RecordingSpec(
            target_region="soma",
            sample_dt_ms=0.05,
            make_plots=False,
        ),
    )


def _passing_runtime(*_args, **_kwargs) -> PreflightCheck:
    return PreflightCheck(
        "runtime",
        "Runtime",
        CheckState.PASS,
        "Test runtime accepted.",
        blocking=True,
    )


def test_generic_adapter_accepts_one_classic_hh_cell_and_writes_contract(
    tmp_path, monkeypatch
):
    morphology = _morphology(tmp_path)
    circuit = CircuitSpec(
        connectome=ConnectomeRef("test:v1", "Test", str(tmp_path)),
        neuron_ids=("1",),
    )
    experiment = _experiment()
    monkeypatch.setattr(generic, "_runtime_check", _passing_runtime)
    adapter = generic.GenericExperimentAdapter({"arbor": os.sys.executable})
    output = tmp_path / "runs"
    report = adapter.validate(circuit, experiment, (morphology,), output_root=output)
    assert report.ok
    assert all(
        check.state != CheckState.FAIL or not check.blocking
        for check in report.checks
    )

    plan = adapter.plan(circuit, experiment, output_root=output)
    payload = adapter.request_payload(
        circuit,
        experiment,
        (morphology,),
        report,
        output_root=output,
    )
    request = adapter.write_request(plan.arguments[-1], payload)
    assert request.is_file()
    assert json.loads((request.parent / "experiment.json").read_text())["name"] == (
        experiment.name
    )
    assert json.loads((request.parent / "run_manifest.json").read_text())[
        "state"
    ] == "queued"
    assert plan.expected_summary_path == str(request.parent / "summary.json")


def test_generic_adapter_blocks_unrepresented_multi_cell_connectivity(
    tmp_path, monkeypatch
):
    first = _morphology(tmp_path, "1")
    second = _morphology(tmp_path, "2")
    circuit = CircuitSpec(
        connectome=ConnectomeRef("test:v1", "Test", str(tmp_path)),
        neuron_ids=("1", "2"),
    )
    monkeypatch.setattr(generic, "_runtime_check", _passing_runtime)
    report = generic.GenericExperimentAdapter({"arbor": os.sys.executable}).validate(
        circuit,
        _experiment(),
        (first, second),
        output_root=tmp_path / "runs",
    )
    assert not report.ok
    failure = next(check for check in report.checks if check.key == "circuit_scope")
    assert failure.state == CheckState.FAIL
    assert "edge manifest" in failure.detail


def test_standalone_worker_rechecks_fail_closed_capabilities():
    circuit = CircuitSpec(neuron_ids=("1",)).to_dict()
    experiment = _experiment().to_dict()
    generic_worker = Path(generic.__file__).parents[1] / "workers" / "generic_experiment_worker.py"
    namespace: dict[str, object] = {"__name__": "generic_experiment_worker_test"}
    exec(compile(generic_worker.read_text(encoding="utf-8"), generic_worker, "exec"), namespace)
    validate = namespace["_validate_request_capabilities"]

    validate("arbor", circuit, experiment)
    circuit["membrane"]["replace_builtin_hh"] = True
    with pytest.raises(ValueError, match="Native membrane mechanisms"):
        validate("arbor", circuit, experiment)


def test_swc_normalization_preserves_topology_and_maps_ids(tmp_path):
    source = tmp_path / "out-of-order.swc"
    source.write_text(
        "1 1 0 0 0 5 -1\n"
        "2 3 0 0 20 1 4\n"
        "3 3 0 0 10 2 1\n"
        "4 3 0 0 15 1.5 3\n",
        encoding="utf-8",
    )
    normalized, mapping, digest = _normalize_swc(source, tmp_path)
    rows = [
        line.split()
        for line in normalized.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    assert all(int(row[6]) < int(row[0]) for row in rows if int(row[6]) >= 0)
    assert [float(row[4]) for row in rows] == [0.0, 10.0, 15.0, 20.0]
    assert "2,4" in mapping.read_text(encoding="utf-8")
    assert len(digest) == 64


@pytest.mark.parametrize("engine,module", [("arbor", "arbor"), ("neuron", "neuron")])
def test_generic_worker_executes_installed_simulator(
    tmp_path, engine, module
):
    runtime = Path("/opt/anaconda3/bin/python3.12")
    if not runtime.is_file():
        pytest.skip("Local scientific runtime is not installed")
    probe_environment = sanitized_external_environment({})
    probe_environment.pop("DISPLAY", None)
    probe = subprocess.run(
        [str(runtime), "-B", "-c", f"import {module}"],
        env=probe_environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if probe.returncode:
        pytest.skip(f"{module} is unavailable in the local scientific runtime")

    morphology = _morphology(tmp_path)
    circuit = CircuitSpec(
        connectome=ConnectomeRef("test:v1", "Test", str(tmp_path)),
        neuron_ids=("1",),
    )
    experiment = _experiment(engine)
    adapter = generic.GenericExperimentAdapter({engine: runtime})
    report = PreflightReport((_passing_runtime(),))
    output = tmp_path / "runs"
    plan = adapter.plan(circuit, experiment, output_root=output)
    payload = adapter.request_payload(
        circuit,
        experiment,
        (morphology,),
        report,
        output_root=output,
    )
    adapter.write_request(plan.arguments[-1], payload)
    environment = sanitized_external_environment(plan.environment)
    environment.pop("DISPLAY", None)
    completed = subprocess.run(
        [plan.program, *plan.arguments],
        cwd=plan.working_directory,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    result = generic.load_generic_experiment_result(plan.expected_summary_path)
    assert result.status == "complete"
    assert result.metadata["Simulator version"] != "unknown"
    assert result.metadata["Recorded samples"] > 0
    assert result.metadata["Morphology transform"] == (
        "parent_before_child_reindex_segment_tree_root_stub_v1"
        if engine == "arbor"
        else "parent_before_child_reindex_v1"
    )
    assert any(artifact.label == "Soma voltage traces" for artifact in result.artifacts)
    assert any(artifact.label == "Normalized morphology" for artifact in result.artifacts)
