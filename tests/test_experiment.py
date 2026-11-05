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


def test_resolution_covers_all_saved_stimuli_conditions_and_recording():
    from digifly_app.core.connectomes import NeuronRecord
    circuit = CircuitSpec(neuron_ids=("1", "2", "3"))
    records = (NeuronRecord("1", "DN", "DNp01", "unused", "test", ("GF",)),
               NeuronRecord("2", "MN", "TTMn", "unused", "test", ("TTMn_R",)),
               NeuronRecord("3", "MN", "TTMn", "unused", "test", ("TTMn_L",)),
               NeuronRecord("4", "MN", "TTMn", "unused", "test"))
    original = ExperimentSpec(stimuli=(StimulusSpec(target_neuron_ids=("gf",)),
        StimulusSpec(name="Second", target_neuron_ids=("TTMn",))),
        conditions=(ConditionSpec(name="A", disabled_neuron_ids=("TTMn_R",)),
            ConditionSpec(name="B", disabled_neuron_ids=("TTMn_L",))),
        recording=RecordingSpec(target_neuron_ids=("TTMn", "2")))
    resolved = original.resolve_targets(circuit, records)
    assert resolved.stimuli[0].target_neuron_ids == ("1",)
    assert resolved.stimuli[1].target_neuron_ids == ("2", "3")
    assert [c.disabled_neuron_ids for c in resolved.conditions] == [("2",), ("3",)]
    assert resolved.recording.target_neuron_ids == ("2", "3")
    assert original.recording.target_neuron_ids == ("TTMn", "2")
    assert resolved.resolve_targets(circuit, records) == resolved
    assert not resolved.errors(circuit)


def test_legacy_string_target_fields_accept_the_same_separators():
    spec = ExperimentSpec.from_dict({"stimuli": [{"target_neuron_ids": "1;2,3\n1"}],
        "conditions": [{"disabled_neuron_ids": "1;2,3\n1"}],
        "recording": {"target_neuron_ids": "1;2,3\n1"}})
    assert spec.stimuli[0].target_neuron_ids == ("1", "2", "3")
    assert spec.conditions[0].disabled_neuron_ids == ("1", "2", "3")
    assert spec.recording.target_neuron_ids == ("1", "2", "3")


def test_target_names_do_not_expand_prefixes_or_include_unloaded_cells():
    from digifly_app.core.connectomes import NeuronRecord
    from digifly_app.core.experiment import resolve_neuron_targets
    records = tuple(NeuronRecord(str(i), "MN", "TTMn", "unused", "test") for i in range(100))
    circuit_ids = tuple(str(i) for i in range(90))
    found, missing = resolve_neuron_targets(("ttmn", "1"), circuit_ids, records)
    assert found == circuit_ids and not missing
    assert resolve_neuron_targets(("TTM", "TTMn*"), circuit_ids, records) == ((), ("TTM", "TTMn*"))
    assert resolve_neuron_targets(("not-loaded",), circuit_ids, ()) == ((), ("not-loaded",))
    # A literal circuit identifier wins over a same-spelled type annotation.
    unusual = (NeuronRecord("1", "MN", "TTMn", "unused", "test"),)
    assert resolve_neuron_targets(("TTMn",), ("1", "TTMn"), unusual) == (("TTMn",), ())
