from __future__ import annotations

import pytest

from digifly_app.core.circuit import (
    CircuitSpec,
    ConnectomeRef,
    HodgkinHuxleySpec,
    NeuronQuery,
    cell_design_profile,
)
from digifly_app.core.mechanisms import GapJunctionPolicy, membrane_profile


def test_circuit_spec_round_trip_keeps_ids_as_strings_and_has_no_engine():
    spec = CircuitSpec(
        connectome=ConnectomeRef("manc-v1-2-1", "MANC v1.2.1", "/data/swc", "manc:v1.2.1"),
        query=NeuronQuery("10000, type:GFC2", 80),
        neuron_ids=("10000", "90071992547409931"),
    )
    spec.apply_neuron_override("10000", {"cm_uF_cm2": 1.2})
    spec.apply_compartment_override("10000", (4, 8), {"soma_gnabar_s_cm2": 0.15})
    spec.membrane = membrane_profile("escape_siz_para_hh_k")
    spec.gap_junction_policy = GapJunctionPolicy(mode="heterotypic_rectifying")
    spec.apply_neuron_mechanism_override("10000", spec.membrane)
    spec.apply_compartment_mechanism_override("10000", (4, 8), spec.membrane)

    payload = spec.to_dict()
    assert payload["schema_version"] == 2
    assert "engine" not in payload
    restored = CircuitSpec.from_dict(payload)
    assert restored.neuron_ids == ("10000", "90071992547409931")
    assert restored.query.expression == "10000, type:GFC2"
    assert restored.compartment_overrides["10000"]["8"]["soma_gnabar_s_cm2"] == 0.15
    assert restored.membrane.channels["para"].suffix == "na16a"
    assert restored.membrane.channels["para"].branch_gbar_s_cm2 == 0.005
    assert restored.gap_junction_policy.mode == "heterotypic_rectifying"
    assert (
        restored.compartment_mechanism_overrides["10000"]["8"]["channels"]["para"][
            "enabled"
        ]
        is True
    )

    with pytest.raises(ValueError, match="Unsupported circuit schema"):
        CircuitSpec.from_dict({"schema_version": 99})


def test_hh_values_validate_without_embedding_an_engine_contract():
    spec = HodgkinHuxleySpec(branch_gnabar_s_cm2=0.04, eca_mV=125.0)
    assert spec.to_dict()["branch_gnabar_s_cm2"] == 0.04
    assert spec.to_dict()["eca_mV"] == 125.0
    assert not hasattr(spec, "phase2_payload")
    with pytest.raises(ValueError, match="capacitance"):
        HodgkinHuxleySpec(cm_uF_cm2=0.0)
    with pytest.raises(ValueError, match="conductance"):
        HodgkinHuxleySpec(g_pas_s_cm2=-0.1)


def test_legacy_v1_circuit_migrates_to_classic_hh_without_inventing_custom_channels():
    restored = CircuitSpec.from_dict(
        {
            "schema_version": 1,
            "neuron_ids": [10000],
            "hh": {"soma_gnabar_s_cm2": 0.2},
            "neuron_overrides": {10000: {"cm_uF_cm2": 1.4}},
        }
    )
    assert restored.schema_version == 2
    assert restored.neuron_ids == ("10000",)
    assert restored.hh.soma_gnabar_s_cm2 == 0.2
    assert restored.membrane.profile_key == "custom"
    assert restored.membrane.active_channels == ()
    assert restored.gap_junction_policy.mode == "none"


def test_mass_apply_copies_hh_and_mechanism_design_to_each_loaded_neuron():
    spec = CircuitSpec()
    spec.apply_compartment_override("10000", (7,), {"branch_gnabar_s_cm2": 0.7})
    spec.apply_compartment_mechanism_override(
        "10000", (7,), membrane_profile("classic_hh")
    )
    count = spec.mass_apply_cell_design(
        ("10000", 10002, "10000"),
        HodgkinHuxleySpec(cm_uF_cm2=1.3),
        membrane_profile("phase2_para_shab"),
    )
    assert count == 2
    assert tuple(spec.neuron_overrides) == ("10000", "10002")
    assert spec.neuron_overrides["10002"]["cm_uF_cm2"] == 1.3
    assert spec.neuron_mechanism_overrides["10000"]["channels"]["shab"]["enabled"]
    assert spec.compartment_overrides["10000"]["7"]["branch_gnabar_s_cm2"] == 0.7
    assert (
        spec.compartment_mechanism_overrides["10000"]["7"]["profile_key"]
        == "classic_hh"
    )


def test_named_cell_profile_is_atomic_and_mismatches_become_custom():
    hh, membrane = cell_design_profile("escape_siz_para_hh_k")
    assert hh.soma_gnabar_s_cm2 == 0.0
    assert hh.branch_gnabar_s_cm2 == 0.0
    assert hh.soma_gkbar_s_cm2 == 0.036
    assert hh.ena_mV == 50.0
    assert hh.ek_mV == -77.0
    assert hh.celsius_C == 6.3
    assert membrane.profile_key == "escape_siz_para_hh_k"

    inconsistent = CircuitSpec(membrane=membrane_profile("escape_siz_para_hh_k"))
    assert inconsistent.membrane.profile_key == "custom"

    mutated = membrane_profile("escape_siz_para_hh_k")
    mutated.channels["para"].branch_gbar_s_cm2 = 9.0
    inconsistent_channel = CircuitSpec(hh=hh, membrane=mutated)
    assert inconsistent_channel.membrane.profile_key == "custom"


def test_augustin_profile_is_an_atomic_gf_design_not_a_resting_voltage_alias():
    hh, membrane = cell_design_profile("augustin_2019_gf_exact")
    assert hh.e_pas_mV == -85.0
    assert hh.ena_mV == 65.0
    assert hh.ek_mV == -74.0
    assert hh.v_init_mV == -65.0
    assert hh.ra_ohm_cm == 35.4
    assert hh.celsius_C == 25.0
    assert hh.soma_gnabar_s_cm2 == 0.0
    assert hh.branch_gkbar_s_cm2 == 0.0
    assert membrane.replace_builtin_hh is True
    assert membrane.profile_key == "augustin_2019_gf_exact"


def test_connection_pair_overrides_are_unordered_serialized_plan_intent():
    spec = CircuitSpec(neuron_ids=("100", "200"))
    chemical = spec.set_connection_class_enabled("200", "100", "chemical", False)
    assert chemical.neuron_a == "100"
    assert chemical.neuron_b == "200"
    assert chemical.chemical_enabled is False
    assert chemical.gap_junction_enabled is None

    updated = spec.set_connection_class_enabled("100", "200", "gap_junction", True)
    assert updated.chemical_enabled is False
    assert updated.gap_junction_enabled is True
    assert len(spec.connection_overrides) == 1

    payload = spec.to_dict()
    assert payload["connection_overrides"] == [
        {
            "neuron_a": "100",
            "neuron_b": "200",
            "chemical_enabled": False,
            "gap_junction_enabled": True,
        }
    ]
    restored = CircuitSpec.from_dict(payload)
    assert restored.connection_override("200", "100") == updated
    assert restored.clear_connection_override("100", "200") is True
    assert restored.connection_override("100", "200") is None

    with pytest.raises(ValueError, match="two different"):
        spec.set_connection_class_enabled("100", "100", "chemical", False)
    with pytest.raises(ValueError, match="Unsupported connection class"):
        spec.set_connection_class_enabled("100", "200", "modulatory", False)
