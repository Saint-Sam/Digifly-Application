#!/usr/bin/env python3
"""Fast, source-area-parameterized point Hodgkin–Huxley worker.

Each selected neuron is one classical HH state.  The adapter supplies its
source-SWC area estimates, while the worker deliberately uses the same request,
edge manifest, condition, recording, result, and cancellation contracts as the
existing experiment worker without opening or rewriting an SWC.  It is an
execution mode, not a replacement for cable NEURON/Arbor runs.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any, Mapping

import numpy as np

REQUEST_SCHEMA_VERSION = 1
RESULT_SCHEMA_VERSION = 1
DEFAULT_AREA_CM2 = 1e-4  # Fallback for point-only documents with no loaded SWCs.
SPIKE_REFRACTORY_MS = 1.0
MAX_CURRENT_VALUES = 50_000_000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(dict(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _emit(stage: str, message: str, **details: Any) -> None:
    print(json.dumps({"at": _now(), "stage": stage, "message": message, **details}, sort_keys=True), flush=True)


def _crossings(times: list[float], values: list[float], threshold: float) -> list[float]:
    return [
        float(times[index])
        for index in range(1, len(values))
        if values[index - 1] < threshold <= values[index]
    ]


def _vtrap(x: np.ndarray | float, scale: float = 10.0) -> np.ndarray:
    value = np.asarray(x, dtype=float)
    z = value / scale
    out = np.empty_like(value)
    small = np.abs(z) < 1e-7
    out[small] = scale * (1.0 - z[small] / 2.0)
    out[~small] = value[~small] / np.expm1(z[~small])
    return out


def _hh_rates(voltage: np.ndarray, temperature_c: float) -> tuple[np.ndarray, ...]:
    # NEURON's built-in HH rate convention with voltage in absolute mV,
    # the usual -65 mV shift, and its standard Q10 temperature multiplier.
    v = voltage + 65.0
    am = 0.1 * _vtrap(25.0 - v)
    bm = 4.0 * np.exp(-v / 18.0)
    ah = 0.07 * np.exp(-v / 20.0)
    bh = 1.0 / (np.exp((30.0 - v) / 10.0) + 1.0)
    an = 0.01 * _vtrap(10.0 - v)
    bn = 0.125 * np.exp(-v / 80.0)
    q10 = 3.0 ** ((float(temperature_c) - 6.3) / 10.0)
    return tuple(rate * q10 for rate in (am, bm, ah, bh, an, bn))


def _hh_values(
    circuit: Mapping[str, Any],
    neuron_id: str,
    scales: Mapping[str, Any],
    *,
    soma_area_fraction: float,
) -> tuple[dict[str, float], bool]:
    values = dict(circuit.get("hh") or {})
    values.update(dict(circuit.get("neuron_overrides") or {}).get(neuron_id) or {})
    membrane = dict(circuit.get("membrane") or {})
    membrane.update(dict(circuit.get("neuron_mechanism_overrides") or {}).get(neuron_id) or {})
    for key in ("ra_ohm_cm", "cm_uF_cm2", "g_pas_s_cm2", "e_pas_mV", "ena_mV", "ek_mV", "eca_mV", "v_init_mV", "celsius_C", "soma_gnabar_s_cm2", "soma_gkbar_s_cm2", "soma_gl_s_cm2", "soma_el_mV"):
        values.setdefault(key, 0.0)
    soma_fraction = min(1.0, max(0.0, float(soma_area_fraction)))
    branch_fraction = 1.0 - soma_fraction
    scope = str(values.get("active_scope") or "all")
    if scope == "soma_ais":
        raise ValueError("Point HH cannot preserve a soma/AIS channel split; choose NEURON.")
    factor = float(scales.get("hh", 1.0))
    na_scale = factor * float(scales.get("hh_na", 1.0))
    k_scale = factor * float(scales.get("hh_k", 1.0))
    leak_scale = factor * float(scales.get("hh_leak", 1.0))
    pas_scale = float(scales.get("pas", 1.0))
    branch_active = scope == "all"
    values["effective_gnabar_s_cm2"] = (
        soma_fraction * float(values["soma_gnabar_s_cm2"])
        + (branch_fraction * float(values["branch_gnabar_s_cm2"]) if branch_active else 0.0)
    ) * na_scale
    values["effective_gkbar_s_cm2"] = (
        soma_fraction * float(values["soma_gkbar_s_cm2"])
        + (branch_fraction * float(values["branch_gkbar_s_cm2"]) if branch_active else 0.0)
    ) * k_scale

    # NEURON applies both the built-in HH leak and the passive conductance.
    # Combine their regional conductance and reversal before collapsing the
    # cable to one state, preserving the area-weighted resting current.
    soma_hh_leak = float(values["soma_gl_s_cm2"]) * leak_scale
    branch_hh_leak = float(values["branch_gl_s_cm2"]) * leak_scale
    passive_leak = float(values["g_pas_s_cm2"]) * pas_scale
    soma_leak = soma_hh_leak + passive_leak
    branch_leak = branch_hh_leak + passive_leak
    leak_total = soma_fraction * soma_leak + branch_fraction * branch_leak
    leak_reversal = (
        soma_fraction * (soma_hh_leak * float(values["soma_el_mV"]) + passive_leak * float(values["e_pas_mV"]))
        + branch_fraction * (branch_hh_leak * float(values["branch_el_mV"]) + passive_leak * float(values["e_pas_mV"]))
    ) / leak_total if leak_total > 0.0 else float(values["soma_el_mV"])
    values["effective_gl_s_cm2"] = leak_total
    values["effective_el_mV"] = leak_reversal
    replace_builtin = bool(membrane.get("replace_builtin_hh", False))
    if replace_builtin:
        values["effective_gnabar_s_cm2"] = 0.0
        values["effective_gkbar_s_cm2"] = 0.0
    return values, replace_builtin


def _exp2syn_peak_factor(tau1_ms: float, tau2_ms: float) -> float:
    """Return NEURON Exp2Syn's unit-peak normalization factor."""
    tau1, tau2 = float(tau1_ms), float(tau2_ms)
    if not (math.isfinite(tau1) and math.isfinite(tau2) and 0.0 < tau1 < tau2):
        raise ValueError("Exp2Syn requires finite time constants with 0 < tau1 < tau2")
    peak_time = tau1 * tau2 / (tau2 - tau1) * math.log(tau2 / tau1)
    peak = math.exp(-peak_time / tau2) - math.exp(-peak_time / tau1)
    return 1.0 / peak


def _stimulus_current(t_ms: float, stimulus: Mapping[str, Any]) -> float:
    if not stimulus.get("enabled", True):
        return 0.0
    delay = float(stimulus.get("delay_ms", 0.0))
    width = float(stimulus.get("pulse_width_ms", 0.0))
    amplitude = float(stimulus.get("amplitude_nA", 0.0))
    waveform = str(stimulus.get("waveform") or "pulse_train")
    if t_ms < delay:
        return 0.0
    if waveform == "step":
        return amplitude if t_ms <= delay + width else 0.0
    if waveform == "ramp":
        if t_ms > delay + width:
            return 0.0
        return amplitude * min(1.0, max(0.0, (t_ms - delay) / max(width, 1e-12)))
    frequency = float(stimulus.get("frequency_hz", 1.0))
    count = int(stimulus.get("pulse_count", 1))
    period = 1000.0 / max(frequency, 1e-12)
    pulse = math.floor((t_ms - delay) / period + 1e-12)
    return amplitude if 0 <= pulse < count and t_ms <= delay + pulse * period + width else 0.0


def _write_plot(path: Path, rows: list[dict[str, Any]], title: str) -> bool:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return False
    by_cell: dict[str, tuple[list[float], list[float]]] = {}
    for row in rows:
        key = f"{row['condition']} · {row['neuron_id']}"
        times, values = by_cell.setdefault(key, ([], []))
        times.append(float(row["time_ms"]))
        values.append(float(row["voltage_mV"]))
    figure, axis = plt.subplots(figsize=(10, 6), constrained_layout=True)
    for key, (times, values) in by_cell.items():
        axis.plot(times, values, label=key, linewidth=0.8)
    axis.set(xlabel="Time (ms)", ylabel="Voltage (mV)", title=title)
    if len(by_cell) <= 20:
        axis.legend(loc="upper right", fontsize=7)
    figure.savefig(path, dpi=150)
    plt.close(figure)
    return True


def run(request_path: Path) -> Path:
    request = json.loads(request_path.read_text(encoding="utf-8"))
    if int(request.get("schema_version", 0)) != REQUEST_SCHEMA_VERSION:
        raise ValueError("Unsupported Point HH request schema")
    output_dir = Path(str(request["output_dir"])).expanduser().resolve()
    if request_path.resolve().parent != output_dir:
        raise ValueError("Point HH request must reside inside its exact output directory")
    experiment = dict(request["experiment"])
    circuit = dict(request["circuit"])
    edge_manifest_path = Path(str(request["edge_manifest_path"])).expanduser().resolve()
    edge_manifest = json.loads(edge_manifest_path.read_text(encoding="utf-8"))
    neuron_ids = tuple(str(value) for value in circuit.get("neuron_ids") or ())
    if tuple(str(value) for value in edge_manifest.get("neuron_ids") or ()) != neuron_ids:
        raise ValueError("Point HH edge manifest does not match the circuit cell set")
    if edge_manifest.get("kind") != "point_selected_subgraph":
        raise ValueError("Point HH requires a point-selected edge manifest")
    point_parameters = dict(request.get("point_hh_parameters") or {})
    raw_areas = dict(point_parameters.get("effective_area_cm2") or {})
    raw_soma_areas = dict(point_parameters.get("soma_area_cm2") or {})
    raw_branch_areas = dict(point_parameters.get("branch_area_cm2") or {})
    area_by_id: dict[str, float] = {}
    soma_area_fraction_by_id: dict[str, float] = {}
    for neuron_id in neuron_ids:
        raw = raw_areas.get(neuron_id, DEFAULT_AREA_CM2)
        try:
            area = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Point HH effective area for neuron {neuron_id} is not numeric") from exc
        if not math.isfinite(area) or area <= 0.0:
            raise ValueError(f"Point HH effective area for neuron {neuron_id} must be positive and finite")
        area_by_id[neuron_id] = area
        try:
            soma_area = float(raw_soma_areas.get(neuron_id, area))
            branch_area = float(raw_branch_areas.get(neuron_id, max(0.0, area - soma_area)))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Point HH regional area for neuron {neuron_id} is not numeric") from exc
        if (not math.isfinite(soma_area) or not math.isfinite(branch_area)
                or soma_area < 0.0 or branch_area < 0.0 or soma_area + branch_area <= 0.0):
            raise ValueError(f"Point HH regional areas for neuron {neuron_id} must be finite and nonnegative")
        soma_area_fraction_by_id[neuron_id] = soma_area / (soma_area + branch_area)
    manifest_path = output_dir / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update({"state": "running", "started_at": _now(), "point_model": "classic_hh_one_state_per_neuron_regional_area_weighted"})
    _atomic_json(manifest_path, manifest)
    _emit("start", "Starting Point HH experiment", neuron_ids=list(neuron_ids), chemical_contacts=len(edge_manifest.get("chemical_edges") or ()))

    cancel_marker = output_dir / "cancel.requested"

    def check_cancel() -> None:
        if cancel_marker.is_file():
            raise InterruptedError("Point HH experiment cancelled by user")

    dt = float(experiment["integration_dt_ms"])
    duration = float(experiment["duration_ms"])
    steps = int(math.floor(duration / dt + 1e-10))
    sample_dt = float(experiment["recording"]["sample_dt_ms"])
    sample_every = int(round(sample_dt / dt))
    if sample_every < 1 or not math.isclose(sample_dt / dt, sample_every, rel_tol=1e-9, abs_tol=1e-9):
        raise ValueError("Point HH recording interval must be a whole multiple of the integration step")
    threshold = float(experiment["recording"].get("spike_threshold_mV", -20.0))
    stimuli = [item for item in experiment.get("stimuli") or () if item.get("enabled", True)]
    if len(stimuli) != 1:
        raise ValueError("Point HH requires exactly one enabled stimulus")
    stimulus = dict(stimuli[0])
    conditions = [item for item in experiment.get("conditions") or () if item.get("enabled", True)]
    if not conditions:
        raise ValueError("Point HH requires at least one enabled condition")

    chemical_edges = [dict(edge) for edge in edge_manifest.get("chemical_edges") or ()]
    electrical_contacts = [dict(contact) for edge in edge_manifest.get("electrical_edges") or () for contact in edge.get("contacts") or ()]
    edge_index_by_pre: dict[str, list[int]] = {}
    for index, edge in enumerate(chemical_edges):
        edge_index_by_pre.setdefault(str(edge["pre_id"]), []).append(index)
    voltage_rows: list[dict[str, Any]] = []
    spike_rows: list[dict[str, Any]] = []
    current_rows: list[dict[str, Any]] = []
    condition_receipts: list[dict[str, Any]] = []
    runtime_version = f"NumPy {np.__version__}"
    recording = dict(experiment["recording"])
    recorded_ids = tuple(str(value) for value in recording.get("target_neuron_ids") or neuron_ids)
    recorded_ids = tuple(value for value in recorded_ids if value in neuron_ids)
    if not recorded_ids:
        raise ValueError("Point HH recording targets are not present in the circuit")
    current_mode = str(recording.get("current_tracking") or "off")
    if current_mode != "off":
        estimated_current_values = (
            (math.floor(duration / sample_dt) + 1)
            * len(recorded_ids)
            * 3
            * len(conditions)
        )
        if estimated_current_values > MAX_CURRENT_VALUES:
            raise ValueError(
                f"Point HH current recording requires about {estimated_current_values:,} values; "
                f"the local limit is {MAX_CURRENT_VALUES:,}. Record fewer cells, increase the "
                "sample interval or shorten the run."
            )
    for condition_index, condition in enumerate(conditions):
        check_cancel()
        name = str(condition.get("name") or f"Condition {condition_index + 1}")
        disabled = {str(value) for value in condition.get("disabled_neuron_ids") or ()}
        active_ids = tuple(value for value in neuron_ids if value not in disabled)
        if not active_ids:
            condition_receipts.append({"condition": name, "active_neuron_ids": [], "disabled_neuron_ids": sorted(disabled)})
            continue
        index = {neuron_id: position for position, neuron_id in enumerate(active_ids)}
        count = len(active_ids)
        hh = []
        hh_report = {}
        passive = []
        for neuron_id in active_ids:
            values, is_passive = _hh_values(
                circuit,
                neuron_id,
                dict(condition.get("mechanism_scales") or {}),
                soma_area_fraction=soma_area_fraction_by_id[neuron_id],
            )
            hh.append(values)
            passive.append(is_passive)
            hh_report[neuron_id] = {
                "soma_area_fraction": soma_area_fraction_by_id[neuron_id],
                "effective_gnabar_s_cm2": values["effective_gnabar_s_cm2"],
                "effective_gkbar_s_cm2": values["effective_gkbar_s_cm2"],
                "effective_gl_s_cm2": values["effective_gl_s_cm2"],
                "effective_el_mV": values["effective_el_mV"],
            }
        # Match the existing NEURON execution contract: the experiment's
        # initial-voltage control initializes every active cell.  The circuit
        # HH ``v_init_mV`` value remains available as a saved design value, but
        # it must not silently override the per-run experiment setting.
        voltage = np.full(count, float(experiment.get("initial_voltage_mV", -65.0)), dtype=float)
        area_cm2 = np.asarray([area_by_id[neuron_id] for neuron_id in active_ids], dtype=float)
        temperature_c = float(experiment.get("temperature_C", 6.3))
        am, bm, ah, bh, an, bn = _hh_rates(voltage, temperature_c)
        m = am / (am + bm)
        h = ah / (ah + bh)
        n = an / (an + bn)
        x = np.zeros(len(chemical_edges), dtype=float)
        y = np.zeros(len(chemical_edges), dtype=float)
        events: dict[int, list[int]] = {}
        last_chemical_spike_step = np.full(count, -10**12, dtype=np.int64)
        last_recording_spike_step = np.full(count, -10**12, dtype=np.int64)
        previous = voltage.copy()
        condition_edges = [
            edge for edge in chemical_edges
            if condition.get("chemical_synapses_enabled", True)
            and str(edge["pre_id"]) in index
            and str(edge["post_id"]) in index
        ]
        condition_gaps = [
            edge for edge in electrical_contacts
            if condition.get("gap_junctions_enabled", True)
            and str(edge["pre_id"]) in index
            and str(edge["post_id"]) in index
        ]
        # Remap the filtered edge list to compact state arrays.
        edge_index_by_pre_local: dict[str, list[int]] = {}
        for edge_position, edge in enumerate(condition_edges):
            edge_index_by_pre_local.setdefault(str(edge["pre_id"]), []).append(edge_position)
        x = np.zeros(len(condition_edges), dtype=float)
        y = np.zeros(len(condition_edges), dtype=float)
        exp2_peak_factors = np.asarray([
            _exp2syn_peak_factor(
                float(edge.get("tau1_ms", 0.5)),
                float(edge.get("tau2_ms", 3.0)),
            )
            for edge in condition_edges
        ], dtype=float)
        condition_voltage_rows: list[dict[str, Any]] = []
        condition_spikes: list[dict[str, Any]] = []
        condition_current_rows: list[dict[str, Any]] = []
        for step in range(steps + 1):
            if step % 100 == 0:
                check_cancel()
            t_ms = step * dt
            if step:
                # Deliver delayed events, then decay the two exp2syn states.
                for edge_position in events.pop(step, ()):
                    weight_nS = float(condition_edges[edge_position].get("weight_uS", 0.0)) * 1000.0
                    x[edge_position] += weight_nS
                    y[edge_position] += weight_nS
                for edge_position, edge in enumerate(condition_edges):
                    x[edge_position] *= math.exp(-dt / max(float(edge.get("tau1_ms", 0.5)), 1e-9))
                    y[edge_position] *= math.exp(-dt / max(float(edge.get("tau2_ms", 3.0)), 1e-9))
                synaptic_pA = np.zeros(count, dtype=float)
                for edge_position, edge in enumerate(condition_edges):
                    post = index[str(edge["post_id"])]
                    conductance_nS = (y[edge_position] - x[edge_position]) * exp2_peak_factors[edge_position]
                    synaptic_pA[post] += conductance_nS * (float(edge.get("reversal_mV", 0.0)) - voltage[post])
                gap_pA = np.zeros(count, dtype=float)
                for edge in condition_gaps:
                    first, second = index[str(edge["pre_id"])], index[str(edge["post_id"])]
                    g_nS = float(edge.get("effective_g_uS", 0.0)) * 1000.0
                    difference = voltage[first] - voltage[second]
                    mode = str(edge.get("mode") or "ohmic")
                    direction = str(edge.get("preferred_direction") or "a_to_b")
                    if mode != "ohmic" and ((direction == "a_to_b" and difference < 0) or (direction == "b_to_a" and difference > 0)):
                        difference = 0.0
                    if direction == "a_to_b":
                        gap_pA[second] += g_nS * difference
                        gap_pA[first] -= g_nS * difference
                    else:
                        gap_pA[first] -= g_nS * difference
                        gap_pA[second] += g_nS * difference
                injected = np.zeros(count, dtype=float)
                targets = [index[value] for value in stimulus.get("target_neuron_ids") or active_ids if value in index]
                if targets:
                    injected[targets] = float(condition.get("stimulus_scale", 1.0)) * _stimulus_current(t_ms, stimulus)
                hh_na = np.zeros(count, dtype=float)
                hh_k = np.zeros(count, dtype=float)
                leak = np.zeros(count, dtype=float)
                for position, values in enumerate(hh):
                    if not passive[position]:
                        hh_na[position] = float(values["effective_gnabar_s_cm2"]) * area_cm2[position] * 1e9 * (m[position] ** 3) * h[position] * (voltage[position] - float(values.get("ena_mV", 65.0)))
                        hh_k[position] = float(values["effective_gkbar_s_cm2"]) * area_cm2[position] * 1e9 * (n[position] ** 4) * (voltage[position] - float(values.get("ek_mV", -74.0)))
                    leak[position] = float(values["effective_gl_s_cm2"]) * area_cm2[position] * 1e9 * (voltage[position] - float(values["effective_el_mV"]))
                capacitance_pF = np.asarray([max(1e-9, float(values.get("cm_uF_cm2", 1.0)) * area_cm2[position] * 1e6) for position, values in enumerate(hh)])
                total_inward_pA = -hh_na - hh_k - leak + synaptic_pA + gap_pA + injected * 1000.0
                voltage_new = voltage + (dt / capacitance_pF) * total_inward_pA
                am, bm, ah, bh, an, bn = _hh_rates(voltage, temperature_c)
                m = np.clip(m + dt * (am * (1.0 - m) - bm * m), 0.0, 1.0)
                h = np.clip(h + dt * (ah * (1.0 - h) - bh * h), 0.0, 1.0)
                n = np.clip(n + dt * (an * (1.0 - n) - bn * n), 0.0, 1.0)
                chemical_threshold = float(
                    circuit.get("chemical_synapse_policy", {}).get("spike_threshold_mV", 0.0)
                )
                chemical_crossings = (previous < chemical_threshold) & (voltage_new >= chemical_threshold)
                recording_crossings = (previous < threshold) & (voltage_new >= threshold)
                refractory_steps = max(1, int(math.ceil(SPIKE_REFRACTORY_MS / dt)))
                spikes = chemical_crossings & ((step - last_chemical_spike_step) >= refractory_steps)
                recording_spikes = recording_crossings & ((step - last_recording_spike_step) >= refractory_steps)
                for source_position in np.flatnonzero(spikes):
                    last_chemical_spike_step[int(source_position)] = step
                    source_id = active_ids[int(source_position)]
                    delay = min((float(edge.get("delay_ms", 1.0)) for edge in condition_edges if str(edge["pre_id"]) == source_id), default=1.0)
                    due = step + max(1, int(round(delay / dt)))
                    events.setdefault(due, []).extend(edge_index_by_pre_local.get(source_id, ()))
                if recording.get("detect_spikes", True):
                    for source_position in np.flatnonzero(recording_spikes):
                        last_recording_spike_step[int(source_position)] = step
                        source_id = active_ids[int(source_position)]
                        if source_id in recorded_ids:
                            condition_spikes.append({"condition": name, "repetition": 1, "neuron_id": source_id, "compartment_id": "point", "spike_time_ms": float(t_ms)})
                previous, voltage = voltage, voltage_new
            if step % sample_every == 0:
                for neuron_id in recorded_ids:
                    position = index.get(neuron_id)
                    if position is None:
                        continue
                    if recording.get("record_voltage", True):
                        condition_voltage_rows.append({"condition": name, "repetition": 1, "neuron_id": neuron_id, "compartment_id": "point", "time_ms": float(t_ms), "voltage_mV": float(voltage[position])})
                    if current_mode != "off" and step:
                        condition_current_rows.extend([
                            {"condition": name, "repetition": 1, "neuron_id": neuron_id, "compartment_id": "point", "time_ms": float(t_ms), "signal": "sodium", "current_nA": float(hh_na[position] / 1000.0)},
                            {"condition": name, "repetition": 1, "neuron_id": neuron_id, "compartment_id": "point", "time_ms": float(t_ms), "signal": "potassium", "current_nA": float(hh_k[position] / 1000.0)},
                            {"condition": name, "repetition": 1, "neuron_id": neuron_id, "compartment_id": "point", "time_ms": float(t_ms), "signal": "leak", "current_nA": float(leak[position] / 1000.0)},
                        ])
        voltage_rows.extend(condition_voltage_rows)
        spike_rows.extend(condition_spikes)
        current_rows.extend(condition_current_rows)
        condition_receipts.append({"condition": name, "active_neuron_ids": list(active_ids), "disabled_neuron_ids": sorted(disabled), "edge_counts": {"chemical": len(condition_edges), "electrical": len(condition_gaps)}, "model": "point_hh_one_state_per_neuron_regional_area_weighted", "effective_hh_parameters": hh_report})

    trace_path = output_dir / "voltage_traces.csv"
    with trace_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=("condition", "repetition", "neuron_id", "compartment_id", "time_ms", "voltage_mV"))
        writer.writeheader()
        writer.writerows(voltage_rows)
    spike_path = output_dir / "spikes.csv"
    with spike_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=("condition", "repetition", "neuron_id", "compartment_id", "spike_time_ms"))
        writer.writeheader()
        writer.writerows(spike_rows)
    artifacts: list[dict[str, str]] = [
        {"kind": "table", "path": trace_path.name, "label": "Point-cell membrane voltage traces"},
        {"kind": "table", "path": spike_path.name, "label": "Threshold-crossing spikes"},
        {"kind": "document", "path": "experiment.json", "label": "Experiment document"},
        {"kind": "document", "path": "circuit.json", "label": "Circuit document"},
        {"kind": "document", "path": "edge_manifest.json", "label": "Source edge manifest"},
        {"kind": "document", "path": "resolved_edge_manifest.json", "label": "Resolved point edge manifest"},
        {"kind": "document", "path": request_path.name, "label": "Resolved worker request"},
        {"kind": "document", "path": "condition_execution.json", "label": "Condition execution"},
    ]
    if current_rows:
        current_path = output_dir / "current_traces.csv"
        with current_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=("condition", "repetition", "neuron_id", "compartment_id", "time_ms", "signal", "current_nA"))
            writer.writeheader()
            writer.writerows(current_rows)
        artifacts.append({"kind": "table", "path": current_path.name, "label": "Point-cell current traces"})
        _atomic_json(output_dir / "current_recording.json", {"schema": "digifly-point-current-recording-v1", "mode": recording.get("current_tracking", "off"), "compartment_count_per_cell": 1, "signals": ["sodium", "potassium", "leak"]})
        artifacts.append({"kind": "document", "path": "current_recording.json", "label": "Point current recording details"})
    _atomic_json(output_dir / "condition_execution.json", {"schema_version": 1, "conditions": condition_receipts})
    _atomic_json(output_dir / "resolved_edge_manifest.json", edge_manifest)
    if bool(recording.get("make_plots", True)) and voltage_rows:
        plot_path = output_dir / "voltage_comparison.png"
        if _write_plot(plot_path, voltage_rows, str(experiment.get("name") or "Point HH experiment")):
            artifacts.append({"kind": "image", "path": plot_path.name, "label": "Point-cell voltage traces"})
    metadata = {
        "Neuron IDs": ", ".join(neuron_ids),
        "Simulator version": runtime_version,
        "Point model": "Classical Hodgkin–Huxley, one state per neuron",
        "Temperature (°C)": temperature_c,
        "HH rate Q10": 3.0 ** ((temperature_c - 6.3) / 10.0),
        "Exp2Syn normalization": "unit peak, matching NEURON Exp2Syn",
        "Spike detector refractory (ms)": SPIKE_REFRACTORY_MS,
        "Effective membrane areas (cm²)": area_by_id,
        "Soma area fractions": soma_area_fraction_by_id,
        "Conditions": len(conditions),
        "Duration (ms)": duration,
        "Integration step (ms)": dt,
        "Recorded samples": len(voltage_rows),
        "Detected spikes": len(spike_rows),
        "Current tracking": recording.get("current_tracking", "off"),
        "Chemical contacts": len(chemical_edges),
        "Electrical contacts": len(electrical_contacts),
        "Morphology use": "SWCs were not read by the Point HH worker",
    }
    summary = {"schema_version": RESULT_SCHEMA_VERSION, "workflow": "experiment_builder_v1", "engine": "point_hh", "status": "complete", "completed_at": _now(), "title": str(experiment.get("name") or "Point HH experiment"), "metadata": metadata, "artifacts": artifacts}
    summary_path = output_dir / "summary.json"
    _atomic_json(summary_path, summary)
    manifest.update({"state": "completed", "completed_at": summary["completed_at"], "summary_path": str(summary_path), "point_model": "classic_hh_one_state_per_neuron_regional_area_weighted"})
    _atomic_json(manifest_path, manifest)
    _emit("complete", "Point HH experiment completed", summary_path=str(summary_path))
    return summary_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    args = parser.parse_args(argv)
    request_path = Path(args.request).expanduser().resolve()
    try:
        run(request_path)
    except InterruptedError as exc:
        manifest_path = request_path.parent / "run_manifest.json"
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest.update({"state": "cancelled", "cancelled_at": _now(), "error": str(exc)})
                _atomic_json(manifest_path, manifest)
            except Exception:
                pass
        _emit("cancelled", str(exc), error_type=type(exc).__name__)
        return 2
    except Exception as exc:
        manifest_path = request_path.parent / "run_manifest.json"
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest.update({"state": "failed", "failed_at": _now(), "error": str(exc)})
                _atomic_json(manifest_path, manifest)
            except Exception:
                pass
        _emit("failed", str(exc), error_type=type(exc).__name__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
