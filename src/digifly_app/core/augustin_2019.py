"""App-owned Augustin et al. (2019) Giant Fiber membrane contract.

The published model initializes voltage at -65 mV, but that value is not its
zero-current equilibrium.  This module keeps initialization, reversal
potentials, and the predicted equilibrium separate so the UI and downstream
paper workflows cannot conflate them.
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any, Callable


PROFILE_KEY = "augustin_2019_gf_exact"
PROFILE_VERSION = "1.0.0"
DOI = "10.1523/ENEURO.0423-18.2019"
MODELDB_ACCESSION = 245415
MODELDB_DOWNLOAD_SHA256 = (
    "381a0c2716b4af7d9d5e49817ae29e80bbb23294ccdae6eda92f2c3215a3e949"
)
MODELDB_GFPY_SHA256 = (
    "2b36d6f47ca1aa0b523b787292fbcd822e56ceabb5b6568ebd17f64012f8b8a9"
)
MODELDB_SOURCE_SHA256 = {
    "nat.mod": "a89cea7401ed4d474f900652e0affa94eb4cbdcfd2e59f790846481572947b6d",
    "nap.mod": "d9523d389af5cc7b18e6969871dccaa12c7dace9b05e7b5ee1de40c095558a90",
    "k.mod": "69838017d32211374d8bbc2bf82a03cb374301f07361bb20c2cd57bcd99fe26d",
}
APP_SOURCE_SHA256 = {
    "nat.mod": "609f7786edf939ae7ac44f2e06216a99fc3217fd2ee5fbb449982516da1ab925",
    "nap.mod": "582dfd3fad7505554de5742a2f59c01093124ee4fd183440a4a5e7cfa8e31126",
    "k.mod": "73f5d0a0f1baaf575430c9744c6a5e060567831e6b94d1e1add2df6defd30da7",
}
APP_ROOT = Path(__file__).resolve().parents[3]
APP_SOURCE_ROOT = APP_ROOT / "mechanisms" / "augustin_2019"

# Table 1 and the exact ModelDB source values, expressed in Digifly units.
PARAMETERS = {
    "ra_ohm_cm": 35.4,
    "cm_uF_cm2": 1.0,
    "g_pas_s_cm2": 30e-6,
    "e_pas_mV": -85.0,
    "ena_mV": 65.0,
    "ek_mV": -74.0,
    "v_init_mV": -65.0,
    "celsius_C": 25.0,
    "gnatbar_s_cm2": 300e-3,
    "gnapbar_s_cm2": 110e-6,
    "gkbar_s_cm2": 10e-3,
}

# Independently checked against the original ModelDB NEURON source and an
# app-linked Arbor equation port.  It is a prediction of this membrane model,
# not a direct measurement of an in-vivo GF resting voltage.
EXPECTED_ZERO_CURRENT_EQUILIBRIUM_MV = -74.66963065854796
MODELDB_NEURON_VOLTAGE_AT_100_MS_MV = -74.669704622474


def steady_state_gates(voltage_mV: float) -> dict[str, float]:
    """Return the exact steady-state gates from nat.mod, nap.mod, and k.mod."""

    voltage = float(voltage_mV)
    return {
        "nat_m": 1.0 / (1.0 + math.exp((voltage + 29.13) / -8.92)),
        "nat_h": 1.0 / (1.0 + math.exp((voltage + 47.0) / 5.0)),
        "nap_m": 1.0 / (1.0 + math.exp((voltage + 48.77) / -3.68)),
        "k_m": 1.0 / (1.0 + math.exp((voltage + 12.85) / -19.91)),
    }


def steady_state_currents(voltage_mV: float) -> dict[str, float]:
    """Return outward-positive current densities in mA/cm²."""

    voltage = float(voltage_mV)
    gates = steady_state_gates(voltage)
    leak = PARAMETERS["g_pas_s_cm2"] * (voltage - PARAMETERS["e_pas_mV"])
    transient_na = (
        PARAMETERS["gnatbar_s_cm2"]
        * gates["nat_m"] ** 3
        * gates["nat_h"]
        * (voltage - PARAMETERS["ena_mV"])
    )
    persistent_na = (
        PARAMETERS["gnapbar_s_cm2"]
        * gates["nap_m"]
        * (voltage - PARAMETERS["ena_mV"])
    )
    potassium = (
        PARAMETERS["gkbar_s_cm2"]
        * gates["k_m"]
        * (voltage - PARAMETERS["ek_mV"])
    )
    return {
        "leak_mA_cm2": leak,
        "transient_na_mA_cm2": transient_na,
        "persistent_na_mA_cm2": persistent_na,
        "potassium_mA_cm2": potassium,
        "total_mA_cm2": leak + transient_na + persistent_na + potassium,
    }


def _bisect(function: Callable[[float], float], low: float, high: float) -> float:
    low_value = function(low)
    high_value = function(high)
    if low_value * high_value >= 0.0:
        raise ValueError("Equilibrium bracket does not change sign.")
    for _ in range(200):
        middle = 0.5 * (low + high)
        middle_value = function(middle)
        if low_value * middle_value <= 0.0:
            high = middle
            high_value = middle_value
        else:
            low = middle
            low_value = middle_value
    return 0.5 * (low + high)


def zero_current_equilibrium_mV() -> float:
    """Solve the physiological zero-current root of the published membrane."""

    return _bisect(
        lambda voltage: steady_state_currents(voltage)["total_mA_cm2"],
        -90.0,
        -60.0,
    )


def profile_contract() -> dict[str, Any]:
    """Return a JSON-safe contract for the app and reproducible exports."""

    equilibrium = zero_current_equilibrium_mV()
    return {
        "schema_version": 1,
        "profile_key": PROFILE_KEY,
        "profile_version": PROFILE_VERSION,
        "scope": "Augustin-2019 Giant Fiber membrane; not a universal fly-neuron profile",
        "source": {
            "citation": "Augustin, Zylbertal & Partridge (2019)",
            "doi": DOI,
            "modeldb_accession": MODELDB_ACCESSION,
            "modeldb_download_sha256": MODELDB_DOWNLOAD_SHA256,
            "modeldb_gfpn_py_sha256": MODELDB_GFPY_SHA256,
            "modeldb_channel_source_sha256": dict(MODELDB_SOURCE_SHA256),
            "app_channel_source_sha256": dict(APP_SOURCE_SHA256),
        },
        "parameters": dict(PARAMETERS),
        "predicted_zero_current_equilibrium_mV": equilibrium,
        "modeldb_neuron_voltage_at_100_ms_mV": MODELDB_NEURON_VOLTAGE_AT_100_MS_MV,
        "interpretation": {
            "v_init_is_resting_equilibrium": False,
            "equilibrium_is_model_prediction_not_measurement": True,
        },
    }


def validate_profile_contract() -> dict[str, bool]:
    equilibrium = zero_current_equilibrium_mV()
    residual = steady_state_currents(equilibrium)["total_mA_cm2"]
    source_hashes_match = True
    for filename, expected in APP_SOURCE_SHA256.items():
        path = APP_SOURCE_ROOT / filename
        if not path.is_file():
            source_hashes_match = False
            break
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != expected:
            source_hashes_match = False
            break
    return {
        "equilibrium_matches_locked_value": abs(
            equilibrium - EXPECTED_ZERO_CURRENT_EQUILIBRIUM_MV
        )
        <= 1e-12,
        "equilibrium_current_residual_below_1e-12_mA_cm2": abs(residual) <= 1e-12,
        "initial_voltage_is_distinct_from_equilibrium": abs(
            PARAMETERS["v_init_mV"] - equilibrium
        )
        > 1.0,
        "leak_reversal_is_distinct_from_equilibrium": abs(
            PARAMETERS["e_pas_mV"] - equilibrium
        )
        > 1.0,
        "app_channel_sources_match_locked_hashes": source_hashes_match,
    }
