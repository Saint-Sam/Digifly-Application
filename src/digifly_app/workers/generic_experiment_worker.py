#!/usr/bin/env python3
"""Standalone classic-HH worker for Experiment Builder.

This file is intentionally self-contained: it is executed by the user's
selected Arbor or NEURON Python, which need not have Digifly installed.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Mapping, Sequence


REQUEST_SCHEMA_VERSION = 1
RESULT_SCHEMA_VERSION = 1
ARBOR_ROOT_STUB_MIN_UM = 0.001
ARBOR_ROOT_STUB_RADIUS_FRACTION = 0.1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(dict(payload), handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _emit(stage: str, message: str, **details: Any) -> None:
    print(
        json.dumps(
            {"at": _now(), "stage": stage, "message": message, **details},
            sort_keys=True,
        ),
        flush=True,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _normalize_swc(
    source: Path,
    output_dir: Path,
) -> tuple[Path, Path, str]:
    """Write an isomorphic parent-before-child SWC plus an ID provenance map."""

    nodes: dict[int, tuple[int, float, float, float, float, int]] = {}
    with source.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            fields = stripped.split()
            if len(fields) < 7:
                raise ValueError(
                    f"SWC row has fewer than seven fields at {source}:{line_number}"
                )
            try:
                node_value = float(fields[0])
                type_value = float(fields[1])
                parent_value = float(fields[6])
                if not (
                    node_value.is_integer()
                    and type_value.is_integer()
                    and parent_value.is_integer()
                ):
                    raise ValueError("SWC identifiers and types must be integers")
                node_id = int(node_value)
                parent_id = int(parent_value)
                values = (
                    int(type_value),
                    float(fields[2]),
                    float(fields[3]),
                    float(fields[4]),
                    float(fields[5]),
                    parent_id,
                )
            except ValueError as exc:
                raise ValueError(f"Invalid SWC row at {source}:{line_number}") from exc
            if node_id in nodes:
                raise ValueError(f"Duplicate SWC node ID {node_id} in {source}")
            if parent_id == node_id or parent_id < -1:
                raise ValueError(f"Invalid parent {parent_id} for SWC node {node_id}")
            if not all(math.isfinite(value) for value in values[1:5]) or values[4] < 0:
                raise ValueError(f"Invalid SWC geometry at {source}:{line_number}")
            nodes[node_id] = values
    roots = [node_id for node_id, values in nodes.items() if values[-1] == -1]
    if len(roots) != 1:
        raise ValueError(f"SWC must contain exactly one root; found {len(roots)}")
    children: dict[int, list[int]] = {node_id: [] for node_id in nodes}
    for node_id, values in nodes.items():
        parent_id = values[-1]
        if parent_id == -1:
            continue
        if parent_id not in nodes:
            raise ValueError(f"SWC node {node_id} has missing parent {parent_id}")
        children[parent_id].append(node_id)

    ordered: list[int] = []
    visited: set[int] = set()
    stack = [roots[0]]
    while stack:
        node_id = stack.pop()
        if node_id in visited:
            raise ValueError(f"Cycle detected at SWC node {node_id}")
        visited.add(node_id)
        ordered.append(node_id)
        stack.extend(reversed(sorted(children[node_id])))
    if len(ordered) != len(nodes):
        raise ValueError("SWC contains nodes disconnected from its root")
    remap = {old_id: new_id for new_id, old_id in enumerate(ordered, start=1)}

    normalized_path = output_dir / "normalized_input.swc"
    temporary_path = normalized_path.with_suffix(".swc.tmp")
    with temporary_path.open("w", encoding="utf-8") as handle:
        handle.write(
            "# Digifly parent-before-child topology normalization\n"
            f"# source_sha256 {_sha256(source)}\n"
        )
        for old_id in ordered:
            swc_type, x, y, z, radius, old_parent = nodes[old_id]
            new_parent = -1 if old_parent == -1 else remap[old_parent]
            handle.write(
                f"{remap[old_id]} {swc_type} {x:.17g} {y:.17g} {z:.17g} "
                f"{radius:.17g} {new_parent}\n"
            )
    os.replace(temporary_path, normalized_path)

    mapping_path = output_dir / "morphology_node_map.csv"
    with mapping_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=("source_node_id", "run_node_id"))
        writer.writeheader()
        writer.writerows(
            {"source_node_id": old_id, "run_node_id": remap[old_id]}
            for old_id in ordered
        )
    return normalized_path, mapping_path, _sha256(normalized_path)


def _swc_rows(
    path: Path,
) -> tuple[tuple[int, int, float, float, float, float, int], ...]:
    rows: list[tuple[int, int, float, float, float, float, int]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            fields = stripped.split()
            rows.append(
                (
                    int(fields[0]),
                    int(fields[1]),
                    float(fields[2]),
                    float(fields[3]),
                    float(fields[4]),
                    float(fields[5]),
                    int(fields[6]),
                )
            )
    return tuple(rows)


def _target_soma_node(
    rows: Sequence[tuple[int, int, float, float, float, float, int]],
    *,
    prefer_rostral: bool,
) -> tuple[int, tuple[float, float, float]]:
    non_root = [row for row in rows if row[6] != -1]
    candidates = [row for row in non_root if row[1] == 1] or non_root
    if not candidates:
        raise ValueError("The morphology has no non-root cable segment for stimulation")
    target = (
        max(candidates, key=lambda row: (row[4], row[5]))
        if prefer_rostral
        else max(candidates, key=lambda row: row[5])
    )
    return target[0], (target[2], target[3], target[4])


def _arbor_root_stub_length(
    rows: Sequence[tuple[int, int, float, float, float, float, int]],
) -> float:
    root = next(row for row in rows if row[6] == -1)
    return max(root[5] * ARBOR_ROOT_STUB_RADIUS_FRACTION, ARBOR_ROOT_STUB_MIN_UM)


def _arbor_morphology_from_swc(
    arbor: Any,
    path: Path,
    *,
    prefer_rostral_soma: bool,
) -> Any:
    """Build a cable tree directly, avoiding loader-specific SWC soma rules."""

    rows = _swc_rows(path)
    target_node_id, _point = _target_soma_node(
        rows,
        prefer_rostral=prefer_rostral_soma,
    )
    root = next(row for row in rows if row[6] == -1)
    root_id, root_type, root_x, root_y, root_z, root_radius, _parent = root
    direction_row = next(
        (
            row
            for row in rows
            if row[0] != root_id
            and (row[2] - root_x) ** 2
            + (row[3] - root_y) ** 2
            + (row[4] - root_z) ** 2
            > 0.0
        ),
        None,
    )
    if direction_row is None:
        unit = (1.0, 0.0, 0.0)
    else:
        delta = (
            direction_row[2] - root_x,
            direction_row[3] - root_y,
            direction_row[4] - root_z,
        )
        magnitude = math.sqrt(sum(value * value for value in delta))
        unit = tuple(value / magnitude for value in delta)
    stub_length = _arbor_root_stub_length(rows)
    root_proximal = arbor.mpoint(
        root_x - unit[0] * stub_length,
        root_y - unit[1] * stub_length,
        root_z - unit[2] * stub_length,
        root_radius,
    )
    root_distal = arbor.mpoint(root_x, root_y, root_z, root_radius)
    tree = arbor.segment_tree()
    segment_by_node: dict[int, int] = {}
    for node_id, swc_type, x, y, z, radius, parent_id in rows:
        point = arbor.mpoint(x, y, z, radius)
        tag = 1001 if node_id == target_node_id else swc_type
        if parent_id == -1:
            segment_id = tree.append(
                arbor.mnpos,
                root_proximal,
                root_distal,
                root_type,
            )
        else:
            if parent_id not in segment_by_node:
                raise ValueError(
                    f"Normalized SWC node {node_id} precedes parent {parent_id}"
                )
            segment_id = tree.append(segment_by_node[parent_id], point, tag)
        segment_by_node[node_id] = segment_id
    return arbor.morphology(tree)


def _merged_hh(circuit: Mapping[str, Any], neuron_id: str) -> dict[str, Any]:
    values = dict(circuit.get("hh") or {})
    values.update(dict((circuit.get("neuron_overrides") or {}).get(neuron_id) or {}))
    return values


def _combined_leak(hh: Mapping[str, Any], *, soma: bool) -> tuple[float, float]:
    region = "soma" if soma else "branch"
    hh_g = float(hh[f"{region}_gl_s_cm2"])
    hh_e = float(hh[f"{region}_el_mV"])
    passive_g = float(hh["g_pas_s_cm2"])
    passive_e = float(hh["e_pas_mV"])
    total = hh_g + passive_g
    reversal = (hh_g * hh_e + passive_g * passive_e) / total if total else hh_e
    return total, reversal


def _region_hh(hh: Mapping[str, Any], *, soma: bool) -> dict[str, float]:
    region = "soma" if soma else "branch"
    gl, el = _combined_leak(hh, soma=soma)
    active = soma or str(hh.get("active_scope") or "all") == "all"
    return {
        "gnabar": float(hh[f"{region}_gnabar_s_cm2"]) if active else 0.0,
        "gkbar": float(hh[f"{region}_gkbar_s_cm2"]) if active else 0.0,
        "gl": gl,
        "el": el,
    }


def _pulse_windows(stimulus: Mapping[str, Any]) -> tuple[tuple[float, float], ...]:
    delay = float(stimulus["delay_ms"])
    duration = float(stimulus["pulse_width_ms"])
    if str(stimulus.get("waveform")) == "step":
        return ((delay, duration),)
    period = 1000.0 / float(stimulus["frequency_hz"])
    return tuple(
        (delay + index * period, duration)
        for index in range(int(stimulus["pulse_count"]))
    )


def _active_native_channels(membrane: Mapping[str, Any]) -> tuple[str, ...]:
    active: list[str] = []
    for key, raw_channel in dict(membrane.get("channels") or {}).items():
        channel = dict(raw_channel or {})
        if channel.get("enabled", False) and (
            float(channel.get("soma_gbar_s_cm2", 0.0)) > 0.0
            or float(channel.get("branch_gbar_s_cm2", 0.0)) > 0.0
        ):
            active.append(str(key))
    return tuple(active)


def _validate_request_capabilities(
    engine: str,
    circuit: Mapping[str, Any],
    experiment: Mapping[str, Any],
) -> None:
    """Defend the fail-closed execution boundary inside the worker process."""

    if engine not in {"arbor", "neuron"}:
        raise ValueError(f"Unsupported generic experiment engine: {engine}")
    neuron_ids = tuple(str(value) for value in circuit.get("neuron_ids") or ())
    if len(neuron_ids) != 1:
        raise ValueError("The generic worker requires exactly one neuron")

    membrane_payloads = [dict(circuit.get("membrane") or {})]
    membrane_payloads.extend(
        dict(value or {})
        for value in dict(circuit.get("neuron_mechanism_overrides") or {}).values()
    )
    membrane_payloads.extend(
        dict(value or {})
        for neuron_map in dict(
            circuit.get("compartment_mechanism_overrides") or {}
        ).values()
        for value in dict(neuron_map or {}).values()
    )
    active_channels = {
        channel
        for membrane in membrane_payloads
        for channel in _active_native_channels(membrane)
    }
    if any(
        membrane.get("replace_builtin_hh", False) for membrane in membrane_payloads
    ) or active_channels:
        detail = ", ".join(sorted(active_channels)) or "replace_builtin_hh"
        raise ValueError(
            "Native membrane mechanisms are outside the classic-HH worker lane: "
            + detail
        )
    if circuit.get("compartment_overrides") or circuit.get(
        "compartment_mechanism_overrides"
    ):
        raise ValueError(
            "Per-compartment biophysics requires an explicit simulator CV mapping"
        )

    hh_payloads = [dict(circuit.get("hh") or {})]
    hh_payloads.extend(
        {**hh_payloads[0], **dict(value or {})}
        for value in dict(circuit.get("neuron_overrides") or {}).values()
    )
    if any(str(values.get("active_scope") or "all") not in {"all", "soma"} for values in hh_payloads):
        raise ValueError("Only whole-neuron or soma-only classic HH is supported")

    stimuli = [
        dict(item)
        for item in experiment.get("stimuli") or ()
        if item.get("enabled", True)
    ]
    if len(stimuli) != 1 or stimuli[0].get("target_region") != "soma" or stimuli[0].get(
        "waveform"
    ) not in {"pulse_train", "step"}:
        raise ValueError(
            "The classic-HH worker requires one soma pulse-train or step stimulus"
        )
    conditions = [
        dict(item)
        for item in experiment.get("conditions") or ()
        if item.get("enabled", True)
    ]
    if not conditions:
        raise ValueError("At least one enabled condition is required")
    if any(
        condition.get("disabled_neuron_ids") or condition.get("mechanism_scales")
        for condition in conditions
    ):
        raise ValueError(
            "Neuron disabling and mechanism scaling are outside the classic-HH worker lane"
        )
    recording = dict(experiment.get("recording") or {})
    if recording.get("target_region") != "soma" or not recording.get(
        "record_voltage", False
    ):
        raise ValueError("The classic-HH worker requires soma voltage recording")
    if engine == "neuron" and int(experiment.get("workers", 1)) != 1:
        raise ValueError("The first NEURON worker lane requires exactly one worker")


def _crossings(
    times: Sequence[float], voltages: Sequence[float], threshold: float
) -> list[float]:
    return [
        float(times[index])
        for index in range(1, min(len(times), len(voltages)))
        if float(voltages[index - 1]) < threshold <= float(voltages[index])
    ]


def _arbor_trace(
    swc_path: Path,
    hh: Mapping[str, Any],
    experiment: Mapping[str, Any],
    stimulus: Mapping[str, Any],
    condition: Mapping[str, Any],
    *,
    prefer_rostral_soma: bool,
) -> tuple[list[float], list[float], str]:
    import arbor

    morphology = _arbor_morphology_from_swc(
        arbor,
        swc_path,
        prefer_rostral_soma=prefer_rostral_soma,
    )
    units = arbor.units
    labels = arbor.label_dict(
        {
            "all": "(all)",
            "soma": "(join (tag 1) (tag 1001))",
            "branches": "(difference (all) (join (tag 1) (tag 1001)))",
            "soma-center": "(on-components 0.5 (tag 1001))",
        }
    )
    decor = arbor.decor()
    decor.set_property(
        Vm=float(experiment["initial_voltage_mV"]) * units.mV,
        tempK=(float(experiment["temperature_C"]) + 273.15) * units.Kelvin,
        rL=float(hh["ra_ohm_cm"]) * units.Ohm * units.cm,
        cm=float(hh["cm_uF_cm2"]) * units.uF / units.cm2,
    )
    decor.set_ion("na", rev_pot=float(hh["ena_mV"]) * units.mV)
    decor.set_ion("k", rev_pot=float(hh["ek_mV"]) * units.mV)

    decor.paint('"soma"', arbor.density("hh", _region_hh(hh, soma=True)))
    decor.paint('"branches"', arbor.density("hh", _region_hh(hh, soma=False)))
    amplitude = float(stimulus["amplitude_nA"]) * float(
        condition.get("stimulus_scale", 1.0)
    )
    for delay, duration in _pulse_windows(stimulus):
        decor.place(
            '"soma-center"',
            arbor.i_clamp(delay * units.ms, duration * units.ms, amplitude * units.nA),
        )
    cell = arbor.cable_cell(morphology, decor, labels)

    class Recipe(arbor.recipe):
        def __init__(self) -> None:
            super().__init__()

        def num_cells(self) -> int:
            return 1

        def cell_kind(self, _gid: int) -> Any:
            return arbor.cell_kind.cable

        def cell_description(self, _gid: int) -> Any:
            return cell

        def probes(self, _gid: int) -> list[Any]:
            return [
                arbor.cable_probe_membrane_voltage(
                    '"soma-center"', "soma_voltage"
                )
            ]

        def global_properties(self, _kind: Any) -> Any:
            return arbor.neuron_cable_properties()

    context = arbor.context(threads=int(experiment.get("workers", 1)))
    simulation = arbor.simulation(
        Recipe(),
        context=context,
        seed=int(experiment.get("random_seed", 1)),
    )
    handle = simulation.sample(
        (0, "soma_voltage"),
        arbor.regular_schedule(
            float(experiment["recording"]["sample_dt_ms"]) * units.ms
        ),
    )
    simulation.run(
        tfinal=float(experiment["duration_ms"]) * units.ms,
        dt=float(experiment["integration_dt_ms"]) * units.ms,
    )
    samples = simulation.samples(handle)
    if len(samples) != 1:
        raise RuntimeError(
            f"The soma label resolved to {len(samples)} probes; exactly one is required."
        )
    data, _metadata = samples[0]
    times = [float(value) for value in data[:, 0]]
    voltages = [float(value) for value in data[:, 1]]
    return times, voltages, str(getattr(arbor, "__version__", "unknown"))


def _neuron_trace(
    swc_path: Path,
    hh: Mapping[str, Any],
    experiment: Mapping[str, Any],
    stimulus: Mapping[str, Any],
    condition: Mapping[str, Any],
    *,
    prefer_rostral_soma: bool,
) -> tuple[list[float], list[float], str]:
    os.environ.pop("DISPLAY", None)
    from neuron import h
    import neuron

    h("forall delete_section()")
    h.load_file("stdrun.hoc")
    h.load_file("import3d.hoc")

    class Cell:
        pass

    cell = Cell()
    reader = h.Import3d_SWC_read()
    reader.input(str(swc_path))
    importer = h.Import3d_GUI(reader, 0)
    importer.instantiate(cell)
    sections = list(getattr(cell, "all", ()))
    if not sections:
        sections = list(h.allsec())
    if not sections:
        raise RuntimeError(f"NEURON imported no sections from {swc_path}")
    soma_candidates = list(getattr(cell, "soma", ()))
    soma = (
        soma_candidates[0]
        if soma_candidates
        else max(sections, key=lambda section: section.diam)
    )
    target_node_id, target_point = _target_soma_node(
        _swc_rows(swc_path),
        prefer_rostral=prefer_rostral_soma,
    )
    target_section = soma
    target_position = 0.5
    nearest_distance = math.inf
    for section in sections:
        point_count = int(h.n3d(sec=section))
        if point_count <= 0:
            continue
        section_length = max(float(section.L), 1e-12)
        for point_index in range(point_count):
            coordinates = (
                float(h.x3d(point_index, sec=section)),
                float(h.y3d(point_index, sec=section)),
                float(h.z3d(point_index, sec=section)),
            )
            distance = sum(
                (coordinates[axis] - target_point[axis]) ** 2 for axis in range(3)
            )
            if distance < nearest_distance:
                nearest_distance = distance
                target_section = section
                target_position = min(
                    0.999999,
                    max(
                        0.000001,
                        float(h.arc3d(point_index, sec=section)) / section_length,
                    ),
                )
    if not math.isfinite(nearest_distance):
        raise RuntimeError(
            f"NEURON exposed no 3-D section points for target SWC node {target_node_id}"
        )
    soma_names = {section.name() for section in soma_candidates}
    soma_names.add(target_section.name())
    for section in sections:
        section.Ra = float(hh["ra_ohm_cm"])
        section.cm = float(hh["cm_uF_cm2"])
        # Odd nseg values retain a segment center while bounding very long cables.
        target = max(1, min(101, int(math.ceil(float(section.L) / 40.0))))
        section.nseg = target if target % 2 else target + 1
        section.insert("hh")
        section.ena = float(hh["ena_mV"])
        section.ek = float(hh["ek_mV"])
        values = _region_hh(hh, soma=section.name() in soma_names)
        for segment in section:
            segment.hh.gnabar = values["gnabar"]
            segment.hh.gkbar = values["gkbar"]
            segment.hh.gl = values["gl"]
            segment.hh.el = values["el"]

    amplitude = float(stimulus["amplitude_nA"]) * float(
        condition.get("stimulus_scale", 1.0)
    )
    clamps = []
    for delay, duration in _pulse_windows(stimulus):
        clamp = h.IClamp(target_section(target_position))
        clamp.delay = delay
        clamp.dur = duration
        clamp.amp = amplitude
        clamps.append(clamp)

    sample_dt = float(experiment["recording"]["sample_dt_ms"])
    time_vector = h.Vector()
    voltage_vector = h.Vector()
    time_vector.record(h._ref_t, sample_dt)
    voltage_vector.record(target_section(target_position)._ref_v, sample_dt)
    h.dt = float(experiment["integration_dt_ms"])
    h.steps_per_ms = 1.0 / h.dt
    h.celsius = float(experiment["temperature_C"])
    h.tstop = float(experiment["duration_ms"])
    h.finitialize(float(experiment["initial_voltage_mV"]))
    h.continuerun(h.tstop)
    # Keep references alive through continuerun.
    _ = clamps
    return (
        [float(value) for value in time_vector],
        [float(value) for value in voltage_vector],
        str(getattr(neuron, "__version__", "unknown")),
    )


def _write_plot(
    path: Path,
    rows: Sequence[Mapping[str, Any]],
    experiment: Mapping[str, Any],
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    grouped: dict[tuple[str, int], tuple[list[float], list[float]]] = {}
    for row in rows:
        key = (str(row["condition"]), int(row["repetition"]))
        times, voltages = grouped.setdefault(key, ([], []))
        times.append(float(row["time_ms"]))
        voltages.append(float(row["voltage_mV"]))
    figure, axis = plt.subplots(figsize=(10.0, 5.4), constrained_layout=True)
    for (condition, repetition), (times, voltages) in grouped.items():
        label = condition if int(experiment.get("repetitions", 1)) == 1 else f"{condition} · rep {repetition}"
        axis.plot(times, voltages, linewidth=1.35, label=label)
    axis.set_title(str(experiment.get("name") or "Digifly experiment"))
    axis.set_xlabel("Time (ms)")
    axis.set_ylabel("Soma membrane voltage (mV)")
    axis.grid(True, alpha=0.25)
    axis.legend(loc="best")
    figure.savefig(path, dpi=150)
    plt.close(figure)


def run(request_path: Path) -> Path:
    request = json.loads(request_path.read_text(encoding="utf-8"))
    if int(request.get("schema_version", 0)) != REQUEST_SCHEMA_VERSION:
        raise ValueError("Unsupported generic experiment request schema")
    output_dir = Path(str(request["output_dir"])).expanduser().resolve()
    if request_path.resolve().parent != output_dir:
        raise ValueError("Worker request must reside inside its exact output directory")
    experiment = dict(request["experiment"])
    circuit = dict(request["circuit"])
    engine = str(request["engine"])
    _validate_request_capabilities(engine, circuit, experiment)
    neuron_ids = tuple(str(value) for value in circuit.get("neuron_ids") or ())
    neuron_id = neuron_ids[0]
    morphology_record = dict((request.get("morphologies") or {}).get(neuron_id) or {})
    swc_path = Path(str(morphology_record.get("path") or "")).expanduser().resolve()
    if not swc_path.is_file():
        raise FileNotFoundError(f"Source SWC is missing: {swc_path}")
    expected_digest = str(morphology_record.get("sha256") or "")
    actual_digest = _sha256(swc_path)
    if expected_digest and actual_digest != expected_digest:
        raise ValueError(
            f"Source SWC checksum changed: expected {expected_digest}, found {actual_digest}"
        )

    normalized_swc_path, node_map_path, normalized_digest = _normalize_swc(
        swc_path,
        output_dir,
    )
    simulation_swc = normalized_swc_path

    morphology_transform = (
        "parent_before_child_reindex_segment_tree_root_stub_v1"
        if engine == "arbor"
        else "parent_before_child_reindex_v1"
    )
    arbor_root_stub_length_um = (
        _arbor_root_stub_length(_swc_rows(simulation_swc))
        if engine == "arbor"
        else 0.0
    )
    prefer_rostral_soma = str(morphology_record.get("family") or "").upper().startswith(
        "DN"
    )
    source_target_node_id, _target_point = _target_soma_node(
        _swc_rows(swc_path),
        prefer_rostral=prefer_rostral_soma,
    )
    target_policy = (
        "rostral max-Z non-root type-1 node"
        if prefer_rostral_soma
        else "widest non-root type-1 node"
    )

    manifest_path = output_dir / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        {
            "state": "running",
            "started_at": _now(),
            "source_swc_sha256": actual_digest,
            "simulation_swc_sha256": normalized_digest,
            "morphology_transform": morphology_transform,
            "arbor_root_stub_length_um": arbor_root_stub_length_um,
            "source_soma_target_node_id": source_target_node_id,
            "soma_target_policy": target_policy,
        }
    )
    _atomic_json(manifest_path, manifest)
    _emit("start", f"Starting {engine.upper()} classic-HH experiment", neuron_id=neuron_id)

    hh = _merged_hh(circuit, neuron_id)
    stimuli = [item for item in experiment.get("stimuli") or () if item.get("enabled", True)]
    conditions = [
        item for item in experiment.get("conditions") or () if item.get("enabled", True)
    ]
    stimulus = dict(stimuli[0])
    trace_rows: list[dict[str, Any]] = []
    spike_rows: list[dict[str, Any]] = []
    runtime_version = "unknown"
    total = len(conditions) * int(experiment["repetitions"])
    completed = 0
    simulate = _arbor_trace if engine == "arbor" else _neuron_trace
    for condition in conditions:
        for repetition in range(1, int(experiment["repetitions"]) + 1):
            _emit(
                "simulate",
                f"Running {condition['name']} repetition {repetition}",
                completed=completed,
                total=total,
            )
            times, voltages, runtime_version = simulate(
                simulation_swc,
                hh,
                experiment,
                stimulus,
                condition,
                prefer_rostral_soma=prefer_rostral_soma,
            )
            if len(times) < 2 or len(times) != len(voltages):
                raise RuntimeError(
                    "Simulator returned an incomplete soma-voltage trace"
                )
            if not all(
                math.isfinite(float(time_ms)) and math.isfinite(float(voltage_mV))
                for time_ms, voltage_mV in zip(times, voltages)
            ):
                raise RuntimeError(
                    "Simulator returned non-finite soma voltage; no result was accepted"
                )
            for time_ms, voltage_mV in zip(times, voltages):
                trace_rows.append(
                    {
                        "condition": str(condition["name"]),
                        "repetition": repetition,
                        "neuron_id": neuron_id,
                        "time_ms": time_ms,
                        "voltage_mV": voltage_mV,
                    }
                )
            if experiment["recording"].get("detect_spikes", True):
                for spike_ms in _crossings(
                    times,
                    voltages,
                    float(experiment["recording"]["spike_threshold_mV"]),
                ):
                    spike_rows.append(
                        {
                            "condition": str(condition["name"]),
                            "repetition": repetition,
                            "neuron_id": neuron_id,
                            "spike_time_ms": spike_ms,
                        }
                    )
            completed += 1

    trace_path = output_dir / "voltage_traces.csv"
    with trace_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "condition",
                "repetition",
                "neuron_id",
                "time_ms",
                "voltage_mV",
            ),
        )
        writer.writeheader()
        writer.writerows(trace_rows)
    spike_path = output_dir / "spikes.csv"
    with spike_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("condition", "repetition", "neuron_id", "spike_time_ms"),
        )
        writer.writeheader()
        writer.writerows(spike_rows)

    artifacts = [
        {"kind": "table", "path": trace_path.name, "label": "Soma voltage traces"},
        {"kind": "table", "path": spike_path.name, "label": "Threshold-crossing spikes"},
        {"kind": "document", "path": "experiment.json", "label": "Experiment document"},
        {"kind": "document", "path": "circuit.json", "label": "Circuit document"},
        {"kind": "document", "path": request_path.name, "label": "Resolved worker request"},
    ]
    artifacts.extend(
        (
            {
                "kind": "morphology",
                "path": normalized_swc_path.name,
                "label": "Normalized morphology",
            },
            {
                "kind": "table",
                "path": node_map_path.name,
                "label": "Source-to-run SWC node map",
            },
        )
    )
    if experiment["recording"].get("make_plots", True):
        plot_path = output_dir / "voltage_comparison.png"
        _write_plot(plot_path, trace_rows, experiment)
        artifacts.append(
            {"kind": "image", "path": plot_path.name, "label": "Voltage comparison"}
        )
    completed_at = _now()
    metadata = {
        "Neuron ID": neuron_id,
        "Neuron type": str(morphology_record.get("neuron_type") or "Unknown"),
        "Simulator version": runtime_version,
        "Conditions": len(conditions),
        "Repetitions": int(experiment["repetitions"]),
        "Duration (ms)": float(experiment["duration_ms"]),
        "Integration step (ms)": float(experiment["integration_dt_ms"]),
        "Recorded samples": len(trace_rows),
        "Detected spikes": len(spike_rows),
        "SWC SHA-256": actual_digest,
        "Simulation SWC SHA-256": normalized_digest,
        "Morphology transform": morphology_transform,
        "Source soma target node ID": source_target_node_id,
        "Soma target policy": target_policy,
    }
    if engine == "arbor":
        metadata["Arbor root stub length (um)"] = arbor_root_stub_length_um
    summary = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "workflow": "experiment_builder_v1",
        "engine": engine,
        "status": "complete",
        "completed_at": completed_at,
        "title": str(experiment["name"]),
        "metadata": metadata,
        "artifacts": artifacts,
    }
    summary_path = output_dir / "summary.json"
    _atomic_json(summary_path, summary)
    manifest.update(
        {
            "state": "completed",
            "completed_at": completed_at,
            "summary_path": str(summary_path),
        }
    )
    _atomic_json(manifest_path, manifest)
    _emit("complete", "Experiment completed", summary_path=str(summary_path))
    return summary_path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    args = parser.parse_args(argv)
    try:
        run(Path(args.request).expanduser().resolve())
    except Exception as exc:
        request_path = Path(args.request).expanduser().resolve()
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
