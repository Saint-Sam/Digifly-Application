from __future__ import annotations

from dataclasses import replace

from digifly_app.core.circuit import CircuitSpec
from digifly_app.core.experiment import (
    ConditionSpec,
    ExperimentSpec,
    RecordingSpec,
    StimulusSpec,
)


def test_experiment_round_trip_is_notebook_independent():
    spec = ExperimentSpec.pulse_train_comparison()
    restored = ExperimentSpec.from_dict(spec.to_dict())
    assert restored == spec
    assert restored.template_key == "pulse_train_comparison"
    assert restored.integration_dt_ms == 0.01
    assert restored.recording.sample_dt_ms == 0.05
    assert restored.stimuli[0].frequency_hz == 100.0
    assert restored.stimuli[0].pulse_count == 10
    assert not any("notebook" in key.casefold() for key in restored.to_dict())


def test_experiment_validates_run_targets_against_circuit():
    circuit = CircuitSpec(neuron_ids=("10000", "10002"))
    stimulus = replace(
        StimulusSpec(),
        target_neuron_ids=("10002", "missing"),
    )
    spec = replace(ExperimentSpec(), stimuli=(stimulus,))
    errors = spec.errors(circuit)
    assert any("missing" in error for error in errors)


def test_experiment_keeps_run_manipulation_separate_from_circuit():
    circuit = CircuitSpec(neuron_ids=("10000", "10002"))
    before = circuit.to_dict()
    condition = ConditionSpec(
        name="Ablation",
        disabled_neuron_ids=("10002",),
        gap_junctions_enabled=False,
        mechanism_scales={"para": 0.5},
    )
    spec = replace(
        ExperimentSpec(),
        conditions=(ConditionSpec(), condition),
        recording=RecordingSpec(target_neuron_ids=("10000",)),
    )
    assert spec.errors(circuit) == []
    assert circuit.to_dict() == before

