from __future__ import annotations

import pytest

from digifly_app.core.circuit import CircuitSpec, ConnectomeRef, HodgkinHuxleySpec, NeuronQuery


def test_circuit_spec_round_trip_keeps_ids_as_strings_and_has_no_engine():
    spec = CircuitSpec(
        connectome=ConnectomeRef("manc-v1-2-1", "MANC v1.2.1", "/data/swc", "manc:v1.2.1"),
        query=NeuronQuery("10000, type:GFC2", 80),
        neuron_ids=("10000", "90071992547409931"),
    )
    spec.apply_neuron_override("10000", {"cm_uF_cm2": 1.2})
    spec.apply_compartment_override("10000", (4, 8), {"soma_gnabar_s_cm2": 0.15})

    payload = spec.to_dict()
    assert payload["schema_version"] == 1
    assert "engine" not in payload
    restored = CircuitSpec.from_dict(payload)
    assert restored.neuron_ids == ("10000", "90071992547409931")
    assert restored.query.expression == "10000, type:GFC2"
    assert restored.compartment_overrides["10000"]["8"]["soma_gnabar_s_cm2"] == 0.15

    with pytest.raises(ValueError, match="Unsupported circuit schema"):
        CircuitSpec.from_dict({"schema_version": 99})


def test_hh_values_validate_without_embedding_an_engine_contract():
    spec = HodgkinHuxleySpec(branch_gnabar_s_cm2=0.04)
    assert spec.to_dict()["branch_gnabar_s_cm2"] == 0.04
    assert not hasattr(spec, "phase2_payload")
    with pytest.raises(ValueError, match="capacitance"):
        HodgkinHuxleySpec(cm_uF_cm2=0.0)
    with pytest.raises(ValueError, match="conductance"):
        HodgkinHuxleySpec(g_pas_s_cm2=-0.1)
