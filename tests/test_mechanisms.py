from __future__ import annotations

import pytest

from digifly_app.core.mechanisms import (
    MEMBRANE_MECHANISMS,
    ChannelAssignment,
    GapJunctionPolicy,
    MembraneMechanismSpec,
    mechanism_capability_message,
    membrane_profile,
)


def test_native_phase2_channel_catalog_keeps_exact_suffixes_and_portable_provenance():
    assert [item.suffix for item in MEMBRANE_MECHANISMS] == [
        "na16a",
        "na14a",
        "kv14sh",
        "kv42shal",
        "kv21shab",
        "kv31shaw",
        "cav21cac",
        "cav31t",
    ]
    assert {item.family for item in MEMBRANE_MECHANISMS} == {
        "sodium",
        "potassium",
        "calcium",
    }
    assert all(not item.source_relpath.startswith("/") for item in MEMBRANE_MECHANISMS)
    assert all(len(item.source_sha256) == 64 for item in MEMBRANE_MECHANISMS)
    assert all(item.catalog_id.startswith("digifly.phase2.") for item in MEMBRANE_MECHANISMS)


def test_phase2_and_escape_siz_profiles_preserve_their_distinct_defaults():
    phase2 = membrane_profile("phase2_para_shab")
    assert phase2.replace_builtin_hh is True
    assert phase2.channels["para"].soma_gbar_s_cm2 == 0.03
    assert phase2.channels["shab"].branch_gbar_s_cm2 == 0.01

    escape = membrane_profile("escape_siz_para_hh_k")
    assert escape.replace_builtin_hh is False
    assert [item.suffix for item in escape.active_channels] == ["na16a"]
    assert escape.channels["para"].soma_gbar_s_cm2 == 0.03
    assert escape.channels["para"].branch_gbar_s_cm2 == 0.005


def test_custom_channel_assignment_round_trip_preserves_disabled_values_and_parameters():
    spec = MembraneMechanismSpec(
        profile_key="custom",
        channels={
            "cacophony": ChannelAssignment(
                "cacophony",
                "cav21cac",
                enabled=True,
                soma_gbar_s_cm2=0.00015,
                branch_gbar_s_cm2=0.00005,
                parameters={"q10": 1.5},
            ),
            "future_channel": ChannelAssignment(
                "future_channel",
                "future_suffix",
                enabled=True,
                soma_gbar_s_cm2=0.002,
                branch_gbar_s_cm2=0.001,
            ),
        },
    )
    restored = MembraneMechanismSpec.from_dict(spec.to_dict())
    assert restored.channels["cacophony"].parameters["q10"] == 1.5
    assert restored.channels["cacophony"].catalog_id == (
        "digifly.phase2.cacophony.cav21cac.v1"
    )
    assert restored.channels["cacophony"].enabled is True
    assert restored.channels["para_alt"].enabled is False
    assert restored.channels["future_channel"].suffix == "future_suffix"


def test_gap_policy_validation_and_backend_messages_do_not_claim_false_arbor_parity():
    hetero = GapJunctionPolicy(
        mode="heterotypic_rectifying",
        placement_policy="imported_contact_sites",
        conductance_basis="per_site",
    )
    assert GapJunctionPolicy.from_dict(hetero.to_dict()).conductance_basis == "per_site"
    message = mechanism_capability_message(
        "arbor", membrane_profile("escape_siz_para_hh_k"), hetero
    )
    assert "not Arbor-qualified" in message
    assert "BLOCKED for Arbor" in message
    assert "different model" in message

    ohmic_message = mechanism_capability_message(
        "arbor", membrane_profile("classic_hh"), GapJunctionPolicy(mode="ohmic")
    )
    assert "map to Arbor's built-in gj" in ohmic_message

    with pytest.raises(ValueError, match="Closed gap fraction"):
        GapJunctionPolicy(mode="heterotypic_rectifying", g_closed_frac=1.1)
    with pytest.raises(ValueError, match="time constants"):
        GapJunctionPolicy(mode="heterotypic_rectifying", tau_open_ms=0.0)
    with pytest.raises(ValueError, match="conductance basis"):
        GapJunctionPolicy(mode="ohmic", conductance_basis="ambiguous")
    with pytest.raises(ValueError, match="finite"):
        GapJunctionPolicy(mode="ohmic", vslope_mV=float("nan"))


def test_known_mechanism_identity_conflicts_are_rejected():
    with pytest.raises(ValueError, match="conflicting suffix"):
        ChannelAssignment("para", "kv21shab")
    with pytest.raises(ValueError, match="Channel map key"):
        MembraneMechanismSpec(
            channels={"para": ChannelAssignment("shab", "kv21shab")}
        )


def test_gap_provenance_and_pair_total_aggregation_are_explicit():
    policy = GapJunctionPolicy(
        mode="ohmic",
        conductance_basis="pair_total",
        aggregation_policy="equal_split_across_selected_sites",
    )
    assert policy.mechanism_name == "Gap"
    assert policy.source_relpath.endswith("Gap.mod")
    assert len(policy.source_sha256) == 64
    with pytest.raises(ValueError, match="aggregation policy"):
        GapJunctionPolicy(mode="ohmic", conductance_basis="pair_total")
