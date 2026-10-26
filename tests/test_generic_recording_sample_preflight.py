from __future__ import annotations

import hashlib
import sys

import pytest

from digifly_app.core.circuit import CircuitSpec, ConnectomeRef
from digifly_app.core.connectomes import NeuronRecord
from digifly_app.core.experiment import ConditionSpec, ExperimentSpec, RecordingSpec, StimulusSpec
from digifly_app.core.models import CheckState, PreflightCheck
from digifly_app.core.morphology import load_swc
from digifly_app.engines import generic_experiment as generic


def _report(tmp_path, monkeypatch, engine, duration, integration, sample, *, region='soma'):
    swc = tmp_path / "1.swc"
    swc.write_text("1 1 0 0 0 5 -1\n2 1 0 0 5 5 1\n3 3 0 0 20 2 2\n")
    morphology = load_swc(NeuronRecord("1", "control", "synthetic", str(swc), "test:v1"))
    circuit = CircuitSpec(
        connectome=ConnectomeRef("test:v1", "Test", str(tmp_path)),
        neuron_ids=("1",),
        morphology_sha256={"1": hashlib.sha256(swc.read_bytes()).hexdigest()},
    )
    experiment = ExperimentSpec(
        name="Sample preflight",
        engine=engine,
        duration_ms=duration,
        integration_dt_ms=integration,
        stimuli=(StimulusSpec(waveform="step", delay_ms=0, pulse_width_ms=.001, pulse_count=1),),
        conditions=(ConditionSpec(name="Control"),),
        recording=RecordingSpec(target_region=region, sample_dt_ms=sample, make_plots=False),
    )
    monkeypatch.setattr(generic, "_runtime_check", lambda *args, **kwargs: PreflightCheck(
        "runtime", "Runtime", CheckState.PASS, "Pure test; no simulator invoked", blocking=True))
    result = generic.GenericExperimentAdapter({engine: sys.executable}).validate(
        circuit, experiment, (morphology,), output_root=tmp_path / "runs")
    assert not (tmp_path / "runs").exists()
    return {check.key: check for check in result.checks}


@pytest.mark.parametrize("engine,duration,integration,sample,count", [
    ("bmtk", .10, .01, .05, 2),
    ("bmtk", .11, .01, .05, 2),  # End between recording samples remains valid.
    ("arbor", .10, .01, .05, 2),
    ("neuron", .05, .01, .05, 2),
])
def test_preflight_accepts_two_real_samples_on_backend_grid(tmp_path, monkeypatch, engine, duration, integration, sample, count):
    checks = _report(tmp_path, monkeypatch, engine, duration, integration, sample)
    check = checks["recording_samples"]
    assert check.state == CheckState.PASS
    assert f"supplies {count} samples" in check.detail
    if engine == "bmtk":
        assert checks["bmtk_sampling"].state == CheckState.PASS


@pytest.mark.parametrize("engine,duration,integration,sample", [
    ("bmtk", .01, .01, .01),
    ("bmtk", .05, .01, .05),
    ("bmtk", .06, .01, .05),  # A ceil forecast would falsely count two.
    ("arbor", .05, .01, .05),
    ("neuron", .04, .01, .05),
])
def test_preflight_blocks_one_actual_sample_before_run_creation(tmp_path, monkeypatch, engine, duration, integration, sample):
    checks = _report(tmp_path, monkeypatch, engine, duration, integration, sample)
    check = checks["recording_samples"]
    assert check.state == CheckState.FAIL and check.blocking
    assert "At least two actual recording samples" in check.detail


@pytest.mark.parametrize("duration,integration,sample", [
    (.11, .01, .025),  # A noninteger recording/integration ratio.
    (.115, .01, .05),  # Duration not on the integration grid.
    (1.0, 1.0, 1e-12),  # Positive but below integration dt; never ratio-rounded to zero.
])
def test_bionet_integer_grid_constraints_remain_enforced(tmp_path, monkeypatch, duration, integration, sample):
    checks = _report(tmp_path, monkeypatch, "bmtk", duration, integration, sample)
    assert checks["bmtk_sampling"].state == CheckState.FAIL
    assert "integer multiple" in checks["bmtk_sampling"].detail


@pytest.mark.parametrize('engine', ['neuron', 'arbor', 'bmtk'])
@pytest.mark.parametrize('sample,accepted', [(.000001, False), (.001, True)])
def test_all_recording_playback_budget_reaches_real_adapter_readiness(tmp_path, monkeypatch, engine, sample, accepted):
    checks = _report(tmp_path, monkeypatch, engine, 3., .000001, sample, region='all')
    check = checks['regional_biophysics']
    assert check.state == (CheckState.PASS if accepted else CheckState.FAIL)
    if not accepted:
        assert check.blocking and 'playback memory budget' in check.detail
