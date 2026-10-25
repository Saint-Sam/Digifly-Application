#!/usr/bin/env python3
"""App-owned BMTK/BioNet HH and source-bound membrane execution worker.

The public entry point consumes the same schema-1 request document as the
generic Arbor/NEURON worker.  BMTK and NEURON imports are deliberately deferred
until after the request has passed the fail-closed contract checks, keeping the
materialization and report-conversion helpers unit-testable without either
simulator installed.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence

try:  # Package import in tests and installed Digifly.
    from . import generic_experiment_worker as _common
except ImportError:  # Direct script execution by a selected external Python.
    import generic_experiment_worker as _common


REQUEST_SCHEMA_VERSION = 1
CIRCUIT_SCHEMA_VERSION = 3
EXPERIMENT_SCHEMA_VERSION = 1
EDGE_MANIFEST_SCHEMA_VERSION = 2
RESULT_SCHEMA_VERSION = 1
POPULATION = "digifly"
CELL_MODEL_TEMPLATE = "digifly:classic_hh_v1"
LANE_ID = "digifly.bmtk-bionet.classic-hh.v1"
TARGET_SEGMENT_UM = 40.0
MAX_TOTAL_SEGMENTS = 2_000_000
TARGET_SOMA_PROXY_LENGTH_UM = 0.002
DEGENERATE_STUB_LENGTH_UM = 0.001
CHILD_CANCEL_GRACE_SECONDS = 5.0
CANCEL_REQUEST_FILENAME = "cancel.requested"


class _WorkerCancelled(RuntimeError):
    """Raised after an app cancellation has been forwarded to BioNet."""

    def __init__(self, signum: int):
        self.signum = int(signum)
        try:
            signal_name = signal.Signals(self.signum).name
        except ValueError:
            signal_name = str(self.signum)
        super().__init__(f"BMTK/BioNet run cancelled by {signal_name}")


class _CancellationController:
    """Own cancellation signals for the complete worker-process lifetime.

    Signal handlers only record the first request and forward it when a BioNet
    child is active.  Normal worker checkpoints raise the classified
    cancellation so ``main`` can preserve the manifest and quarantine any
    summary that raced with the request.
    """

    def __init__(self, marker: Path):
        self.marker = marker
        self.signum: int | None = None
        self.requested_at: float | None = None
        self.active_child: subprocess.Popen[Any] | None = None
        self._installed_handlers: dict[int, Any] = {}

    def __enter__(self) -> "_CancellationController":
        for handled_signal in (signal.SIGTERM, signal.SIGINT):
            try:
                self._installed_handlers[int(handled_signal)] = signal.signal(
                    handled_signal, self._handle_signal
                )
            except (OSError, ValueError):
                # The worker CLI runs on the main thread. Keeping this guard
                # makes the helper unit-testable on restricted hosts.
                continue
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _traceback: Any) -> None:
        self.active_child = None
        for handled_signal, previous_handler in self._installed_handlers.items():
            signal.signal(handled_signal, previous_handler)

    @property
    def requested(self) -> bool:
        return self.signum is not None

    def _record_request(self, signum: int) -> bool:
        first_request = self.signum is None
        if first_request:
            self.signum = int(signum)
            self.requested_at = time.monotonic()
        return first_request

    def _handle_signal(self, signum: int, _frame: Any) -> None:
        first_request = self._record_request(signum)
        child = self.active_child
        if child is not None and child.poll() is None:
            _signal_child_process(child, signum, force=not first_request)

    def observe_marker(self) -> None:
        if self.marker.is_file() and not self.requested:
            first_request = self._record_request(int(signal.SIGTERM))
            child = self.active_child
            if child is not None and child.poll() is None:
                _signal_child_process(
                    child,
                    self.signum or int(signal.SIGTERM),
                    force=not first_request,
                )

    def raise_if_requested(self) -> None:
        self.observe_marker()
        if self.signum is not None:
            raise _WorkerCancelled(self.signum)

    def attach_child(self, child: subprocess.Popen[Any]) -> None:
        request_preceded_child = self.requested
        self.active_child = child
        self.observe_marker()
        if request_preceded_child and child.poll() is None:
            _signal_child_process(child, self.signum or int(signal.SIGTERM))

    def detach_child(self, child: subprocess.Popen[Any]) -> None:
        if self.active_child is child:
            self.active_child = None


def _json_sha256(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _safe_component(value: str, *, prefix: str) -> str:
    raw = str(value)
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip(".-_")[:40]
    return f"{slug or prefix}-{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:12]}"


def _finite(value: Any, *, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{label} must be a finite number")
    return result


def _swc_diameter(radius_um: Any) -> float:
    """Return the exact NEURON diameter for a validated positive SWC radius."""

    radius = _finite(radius_um, label="SWC radius")
    if radius <= 0.0:
        raise ValueError("SWC radius must be positive")
    return 2.0 * radius


def _load_object(path: Path, *, label: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return payload


def _validate_companion_document(
    output_dir: Path,
    filename: str,
    expected: Mapping[str, Any],
) -> None:
    path = output_dir / filename
    if not path.is_file() or _load_object(path, label=filename) != dict(expected):
        raise ValueError(f"Run-owned {filename} does not match the worker request")


def _validate_contacts(
    edge_manifest: Mapping[str, Any], neuron_ids: tuple[str, ...]
) -> None:
    if int(edge_manifest.get("schema_version", 0)) != EDGE_MANIFEST_SCHEMA_VERSION:
        raise ValueError("The selected-subgraph edge manifest schema is unsupported")
    if edge_manifest.get("kind") != "explicit_selected_subgraph":
        raise ValueError("BioNet requires an explicit selected-subgraph edge manifest")
    if tuple(str(value) for value in edge_manifest.get("neuron_ids") or ()) != neuron_ids:
        raise ValueError("The edge-manifest neuron order does not match the circuit")

    selected = set(neuron_ids)
    contact_ids: set[str] = set()
    for raw in edge_manifest.get("chemical_edges") or ():
        contact = dict(raw or {})
        _common._validate_aggregate_chemical_contact(contact, dict(edge_manifest.get("chemical_synapse_policy") or {}))
        contact_id = str(contact.get("contact_id") or "")
        if not contact_id or contact_id in contact_ids:
            raise ValueError("Chemical contact IDs must be present and unique")
        contact_ids.add(contact_id)
        if {str(contact.get("pre_id")), str(contact.get("post_id"))} - selected:
            raise ValueError(f"Chemical contact {contact_id} leaves the selected circuit")
        weight = _finite(contact.get("weight_uS"), label=f"{contact_id} weight")
        delay = _finite(contact.get("delay_ms"), label=f"{contact_id} delay")
        tau1 = _finite(contact.get("tau1_ms"), label=f"{contact_id} tau1")
        tau2 = _finite(contact.get("tau2_ms"), label=f"{contact_id} tau2")
        _finite(contact.get("reversal_mV"), label=f"{contact_id} reversal")
        if weight < 0.0 or delay <= 0.0 or tau1 <= 0.0 or tau2 <= tau1:
            raise ValueError(f"Chemical contact {contact_id} has invalid exp2syn parameters")
        if int(contact.get("post_source_node_id", 0)) <= 0:
            raise ValueError(f"Chemical contact {contact_id} lacks a postsynaptic SWC node")
        point = list(contact.get("post_coordinate_um") or ())
        if len(point) != 3 or not all(math.isfinite(float(value)) for value in point):
            raise ValueError(f"Chemical contact {contact_id} lacks a finite post coordinate")

    for edge in edge_manifest.get("electrical_edges") or ():
        a, b = str(edge.get("neuron_a")), str(edge.get("neuron_b"))
        contacts = list(edge.get("contacts") or ())
        if a == b or {a, b} - selected or not contacts or int(edge.get("contact_count", -1)) != len(contacts):
            raise ValueError("Electrical contact pair/count is invalid")
        for contact in contacts:
            identity = str(contact.get("contact_id") or "")
            if not identity or identity in contact_ids:
                raise ValueError("Electrical contact IDs must be present and unique")
            contact_ids.add(identity)
            if {str(contact.get("pre_id")), str(contact.get("post_id"))} != {a,b}:
                raise ValueError("Electrical contact endpoints differ from their pair")
            if _finite(contact.get("effective_g_uS"), label="electrical conductance") < 0:
                raise ValueError("Electrical conductance cannot be negative")
            for end in ("pre", "post"):
                if int(contact.get(f"{end}_source_node_id", 0)) <= 0:
                    raise ValueError("Electrical contact lacks a source-edge identity")
                xyz = list(contact.get(f"{end}_coordinate_um") or ())
                if len(xyz) != 3 or not all(math.isfinite(float(v)) for v in xyz):
                    raise ValueError("Electrical contact lacks finite coordinates")
            _common._validate_gap_contact_placement(contact, dict(edge_manifest.get("gap_junction_policy") or {}))

    selected_hash = _json_sha256(
        {
            "chemical": list(edge_manifest.get("chemical_edges") or ()),
            "electrical": list(edge_manifest.get("electrical_edges") or ()),
        }
    )
    if selected_hash != str(edge_manifest.get("selected_contact_identity_sha256") or ""):
        raise ValueError("The selected contact-set identity is invalid")


def _validate_capabilities(
    circuit: Mapping[str, Any],
    experiment: Mapping[str, Any],
    edge_manifest: Mapping[str, Any],
) -> None:
    if int(circuit.get("schema_version", 0)) != CIRCUIT_SCHEMA_VERSION:
        raise ValueError("BioNet requires circuit schema 3")
    if int(experiment.get("schema_version", 0)) != EXPERIMENT_SCHEMA_VERSION:
        raise ValueError("BioNet requires experiment schema 1")
    if str(experiment.get("engine")) != "bmtk":
        raise ValueError("The BMTK/BioNet worker only accepts engine='bmtk'")

    neuron_ids = tuple(str(value) for value in circuit.get("neuron_ids") or ())
    if not neuron_ids or len(set(neuron_ids)) != len(neuron_ids):
        raise ValueError("BioNet requires one or more unique morphology-backed neuron IDs")
    _validate_contacts(edge_manifest, neuron_ids)

    chemical_policy = dict(circuit.get("chemical_synapse_policy") or {})
    manifest_policy = dict(edge_manifest.get("chemical_synapse_policy") or {})
    if manifest_policy != chemical_policy:
        raise ValueError("The edge-manifest chemical policy differs from the circuit")
    required_policy = {
        "mechanism": "exp2syn",
        "source_mode": "soma_threshold",
        "edge_scope": "all_chemical_edges",
    }
    for key, expected in required_policy.items():
        if chemical_policy.get(key) != expected:
            raise ValueError(f"Unsupported BioNet chemical policy {key}={chemical_policy.get(key)!r}")
    aggregate = chemical_policy.get("placement_policy") in {"aggregate_post_soma", "aggregate_post_ais"}
    if aggregate:
        if (chemical_policy.get("aggregation_policy") != "one_exp2syn_per_pair"
                or chemical_policy.get("weight_policy") not in {"count_times_default", "per_pair_default"}):
            raise ValueError("Unsupported BioNet aggregate chemical model")
    elif (chemical_policy.get("placement_policy") != "imported_post_contact"
            or chemical_policy.get("aggregation_policy") != "per_site_unchanged"
            or chemical_policy.get("weight_policy") != "source_or_default_per_contact"):
        raise ValueError("Unsupported BioNet located-contact chemical model")

    gap_policy = dict(circuit.get("gap_junction_policy") or {})
    if dict(edge_manifest.get("gap_junction_policy") or {}) != gap_policy:
        raise ValueError("The edge-manifest gap policy differs from the circuit")
    if gap_policy.get("mode") not in {"none", "ohmic", "rectifying", "heterotypic_rectifying"}:
        raise ValueError("Unsupported BioNet gap-junction policy")
    names = {"ohmic":"Gap", "rectifying":"RectGap", "heterotypic_rectifying":"HeteroRectGap"}
    if gap_policy["mode"] != "none":
        name = names[gap_policy["mode"]]
        if (gap_policy.get("mechanism_name") != name
                or gap_policy.get("source_sha256") != _common.NEURON_GAP_SOURCE_HASHES[name]):
            raise ValueError("BioNet gap-junction source identity is invalid")
        if gap_policy.get("placement_policy") not in {"imported_contact_sites", "soma_pair", "ais_pair"}:
            raise ValueError("BioNet electrical contacts require a supported explicit site policy")
    if edge_manifest.get("electrical_edges") and gap_policy.get("mode") == "none":
        raise ValueError("Electrical contacts have no active gap-junction policy")
    if gap_policy.get("preferred_direction", "a_to_b") not in {"a_to_b", "b_to_a"}:
        raise ValueError("Unsupported electrical preferred direction")
    for field in ("g_closed_frac", "vhalf_mV", "vslope_mV", "empirical_residual_frac", "tau_open_ms", "tau_close_ms"):
        _finite(gap_policy.get(field), label=f"gap-junction {field}")
    if (not 0 <= float(gap_policy["g_closed_frac"]) <= 1
            or not 0 <= float(gap_policy["empirical_residual_frac"]) <= 1
            or any(float(gap_policy[f]) <= 0 for f in ("vslope_mV", "tau_open_ms", "tau_close_ms"))):
        raise ValueError("Invalid gap-junction kinetics")

    membrane_payloads = _common._native_payloads(circuit)
    membrane_payloads.extend(
        dict(value or {})
        for value in dict(circuit.get("neuron_mechanism_overrides") or {}).values()
    )
    active_channels = {
        channel
        for membrane in membrane_payloads
        for channel in _common._active_native_channels(membrane)
    }
    for membrane in membrane_payloads:
        for key in _common._active_native_channels(membrane):
            _common._validated_native_assignment(key, dict(membrane["channels"][key]))

    override_ids = set(dict(circuit.get("neuron_overrides") or {}))
    if override_ids - set(neuron_ids):
        raise ValueError("A per-neuron HH override targets a neuron outside the circuit")
    for neuron_id in neuron_ids:
        hh = _common._merged_hh(circuit, neuron_id)
        if str(hh.get("active_scope") or "all") not in {"all", "soma", "soma_ais"}:
            raise ValueError("BioNet supports only all-neuron or soma-only classic HH")
        for key, value in hh.items():
            if key != "active_scope":
                _finite(value, label=f"{neuron_id} HH {key}")

    stimuli = [dict(item) for item in experiment.get("stimuli") or () if item.get("enabled", True)]
    if len(stimuli) != 1:
        raise ValueError("BioNet requires exactly one enabled stimulus")
    stimulus = stimuli[0]
    if stimulus.get("target_region") not in {"soma", "all", "ais", "selected_compartments"} or stimulus.get("waveform") not in {
        "step",
        "pulse_train",
        "ramp",
    }:
        raise ValueError("BioNet requires mapped step, pulse-train or ramp stimulation")
    target_ids = {str(value) for value in stimulus.get("target_neuron_ids") or neuron_ids}
    if target_ids - set(neuron_ids):
        raise ValueError("A stimulus target is outside the selected circuit")
    _finite(stimulus.get("amplitude_nA"), label="stimulus amplitude")
    for delay, duration in _common._pulse_windows(stimulus):
        if delay < 0.0 or duration <= 0.0 or delay + duration > float(experiment["duration_ms"]):
            raise ValueError("A stimulus pulse is outside the simulation interval")

    conditions = [dict(item) for item in experiment.get("conditions") or () if item.get("enabled", True)]
    if not conditions:
        raise ValueError("BioNet requires at least one enabled condition")
    names = [str(item.get("name") or "").strip() for item in conditions]
    if any(not name for name in names) or len({name.casefold() for name in names}) != len(names):
        raise ValueError("Enabled BioNet condition names must be non-empty and unique")
    for condition in conditions:
        _common._validate_condition_manipulations(circuit, condition)
        scale = _finite(condition.get("stimulus_scale", 1.0), label="condition stimulus scale")
        if scale < 0.0:
            raise ValueError("Condition stimulus scales cannot be negative")

    recording = dict(experiment.get("recording") or {})
    if recording.get("target_region") not in {"soma", "all", "ais", "selected_compartments"} or not (recording.get("record_voltage", False) or recording.get("detect_spikes", False)):
        raise ValueError("BioNet requires voltage recording or spike detection in a mapped region")
    record_ids = {str(value) for value in recording.get("target_neuron_ids") or neuron_ids}
    if record_ids - set(neuron_ids):
        raise ValueError("A recording target is outside the selected circuit")
    integration_dt = _finite(experiment.get("integration_dt_ms"), label="integration dt")
    sample_dt = _finite(recording.get("sample_dt_ms"), label="recording dt")
    if integration_dt <= 0.0 or sample_dt < integration_dt:
        raise ValueError("BioNet recording dt must be an integer multiple of integration dt")
    ratio = sample_dt / integration_dt
    if not math.isclose(
        ratio, round(ratio), rel_tol=1e-9, abs_tol=1e-9
    ):
        raise ValueError("BioNet recording dt must be an integer multiple of integration dt")
    if not 1 <= int(experiment.get("workers",1)) <= 256:
        raise ValueError("Worker count must be between 1 and 256")
    if int(experiment.get("repetitions", 0)) < 1:
        raise ValueError("BioNet requires at least one repetition")
    duration = _finite(experiment.get("duration_ms"), label="duration")
    if duration <= 0.0:
        raise ValueError("BioNet duration must be positive")
    duration_steps = duration / integration_dt
    if not math.isclose(
        duration_steps, round(duration_steps), rel_tol=0.0, abs_tol=1e-8
    ):
        raise ValueError("BioNet duration must be an integer multiple of integration dt")
    sample_count = (
        int(round(duration_steps)) + int(round(ratio)) - 1
    ) // int(round(ratio))
    if sample_count < 2:
        raise ValueError("BioNet soma recording requires at least two samples")
    _finite(experiment.get("initial_voltage_mV"), label="initial voltage")
    _finite(experiment.get("temperature_C"), label="temperature")


def _validated_request(request_path: Path) -> tuple[dict[str, Any], Path, dict[str, Any]]:
    request = _load_object(request_path, label="worker request")
    if int(request.get("schema_version", 0)) != REQUEST_SCHEMA_VERSION:
        raise ValueError("Unsupported BMTK worker request schema")
    output_raw = str(request.get("output_dir") or "")
    if not Path(output_raw).is_absolute():
        raise ValueError("The worker output directory must be absolute")
    output_dir = Path(output_raw).resolve()
    if request_path.resolve().parent != output_dir or not output_dir.is_dir():
        raise ValueError("Worker request must reside in its exact existing output directory")
    if str(request.get("engine")) != "bmtk":
        raise ValueError("The BMTK worker received a non-BMTK request")
    if not bool(dict(request.get("preflight") or {}).get("ok", False)):
        raise ValueError("The BMTK worker refuses a request that did not pass preflight")

    circuit = dict(request.get("circuit") or {})
    experiment = dict(request.get("experiment") or {})
    _validate_companion_document(output_dir, "circuit.json", circuit)
    _validate_companion_document(output_dir, "experiment.json", experiment)
    edge_manifest = _common._load_edge_manifest(request, output_dir)
    _validate_capabilities(circuit, experiment, edge_manifest)
    _common._validate_edge_source_identity(edge_manifest)

    neuron_ids = tuple(str(value) for value in circuit.get("neuron_ids") or ())
    morphologies = dict(request.get("morphologies") or {})
    if set(morphologies) != set(neuron_ids):
        raise ValueError("Morphology records must be keyed exactly by the selected neuron IDs")
    for neuron_id in neuron_ids:
        record = dict(morphologies[neuron_id] or {})
        raw_path = str(record.get("path") or "")
        if not Path(raw_path).is_absolute():
            raise ValueError(f"Source SWC path for {neuron_id} must be absolute")
        path = Path(raw_path).resolve()
        expected = str(record.get("sha256") or "")
        if not path.is_file() or len(expected) != 64 or _common._sha256(path) != expected:
            raise ValueError(f"Source SWC identity check failed for {neuron_id}")
    return request, output_dir, edge_manifest


def _allowed_runtime_roots() -> tuple[Path, ...]:
    roots = {Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve()}
    return tuple(sorted(roots, key=str))


def _module_origin(module: Any) -> Path:
    raw = str(getattr(module, "__file__", "") or "")
    if not raw:
        raise RuntimeError(f"Runtime module {module.__name__} has no file origin")
    return Path(raw).resolve()


def _require_bionet_runtime() -> tuple[Any, Any, Any, dict[str, Any]]:
    """Import a coherent BMTK+NEURON stack owned by this interpreter."""

    imported: dict[str, Any] = {}
    for name in ("numpy", "h5py", "neuron", "bmtk"):
        try:
            imported[name] = importlib.import_module(name)
        except Exception as exc:
            raise RuntimeError(
                "The selected BMTK runtime must contain BMTK, NEURON, NumPy, and h5py "
                f"in one interpreter; importing {name} failed: {exc}"
            ) from exc
    roots = _allowed_runtime_roots()
    origins = {name: _module_origin(module) for name, module in imported.items()}
    escaped = {
        name: str(path)
        for name, path in origins.items()
        if not any(_inside(path, root) for root in roots)
    }
    if escaped:
        detail = ", ".join(f"{name}={path}" for name, path in sorted(escaped.items()))
        raise RuntimeError(
            "The BMTK/BioNet runtime is incoherent: dependencies were inherited from "
            f"outside sys.prefix/sys.base_prefix ({detail})"
        )
    try:
        bionet = importlib.import_module("bmtk.simulator.bionet")
        builder_module = importlib.import_module("bmtk.builder")
    except Exception as exc:
        raise RuntimeError(f"The selected BMTK runtime cannot import BioNet: {exc}") from exc
    provenance = {
        # Preserve a venv launcher symlink. Resolving it can re-enter the base
        # interpreter and discard the selected environment on the next exec.
        "python_executable": str(Path(sys.executable).absolute()),
        "sys_prefix": str(Path(sys.prefix).resolve()),
        "sys_base_prefix": str(Path(sys.base_prefix).resolve()),
        "python_version": sys.version.split()[0],
        "packages": {
            name: {
                "version": str(
                    getattr(module, "__version__", "")
                    or importlib.metadata.version(name)
                ),
                "origin": str(origins[name]),
            }
            for name, module in imported.items()
        },
    }
    return builder_module.NetworkBuilder, bionet, imported["h5py"], provenance


def _section_plan(
    rows: Sequence[tuple[int, int, float, float, float, float, int]],
    *,
    prefer_rostral_soma: bool,
) -> dict[str, Any]:
    """Plan maximal branch-run sections and exact SWC-node attachment sites.

    The plan is simulator-free.  ``section_id`` is the final order of
    ``hobj.all``, which is exactly the index BioNet uses for preselected
    ``afferent_section_id`` contacts.
    """

    if not rows:
        raise ValueError("A normalized morphology cannot be empty")
    target_node_id, _ = _common._target_soma_node(
        rows, prefer_rostral=prefer_rostral_soma
    )
    by_id = {int(row[0]): row for row in rows}
    children: dict[int, list[int]] = {node_id: [] for node_id in by_id}
    roots: list[int] = []
    for node_id, _kind, _x, _y, _z, _radius, parent_id in rows:
        if parent_id == -1:
            roots.append(node_id)
        elif parent_id in children:
            children[parent_id].append(node_id)
        else:
            raise ValueError(f"Normalized SWC node {node_id} has no parent {parent_id}")
    if len(roots) != 1:
        raise ValueError("A normalized morphology must have exactly one root")
    for values in children.values():
        values.sort()

    def is_breakpoint(node_id: int) -> bool:
        row = by_id[node_id]
        parent_id = int(row[6])
        child_ids = children[node_id]
        if node_id == target_node_id or parent_id == -1:
            return True
        if len(child_ids) != 1 or int(by_id[parent_id][1]) != int(row[1]):
            return True
        return int(by_id[child_ids[0]][1]) != int(row[1])

    breakpoints = {node_id for node_id in by_id if is_breakpoint(node_id)}
    raw_sections: list[dict[str, Any]] = []
    # Values are raw section index and normalized position within that section.
    raw_sites: dict[int, tuple[int, float]] = {}
    visited_edges: set[tuple[int, int]] = set()

    def add_path(path: list[int]) -> None:
        if len(path) < 2:
            return
        arcs = [0.0]
        for previous_id, node_id in zip(path, path[1:]):
            previous = by_id[previous_id]
            node = by_id[node_id]
            arcs.append(
                arcs[-1]
                + math.dist(
                    (float(previous[2]), float(previous[3]), float(previous[4])),
                    (float(node[2]), float(node[3]), float(node[4])),
                )
            )
        geometric_length = float(arcs[-1])
        section_length = (
            geometric_length
            if geometric_length > 0.0
            else DEGENERATE_STUB_LENGTH_UM
        )
        parent_site = raw_sites.get(path[0])
        raw_index = len(raw_sections)
        raw_sections.append(
            {
                "path": list(path),
                "length_um": section_length,
                "uses_degenerate_stub": geometric_length <= 0.0,
                "parent_section_id": parent_site[0] if parent_site else None,
                "parent_section_pos": parent_site[1] if parent_site else None,
            }
        )
        denominator = geometric_length if geometric_length > 0.0 else 1.0
        for node_id, arc in zip(path, arcs):
            raw_sites.setdefault(node_id, (raw_index, float(arc / denominator)))

    def add_singleton(node_id: int) -> None:
        raw_index = len(raw_sections)
        raw_sections.append(
            {
                "path": [node_id],
                "length_um": DEGENERATE_STUB_LENGTH_UM,
                "uses_degenerate_stub": True,
                "parent_section_id": None,
                "parent_section_pos": None,
            }
        )
        raw_sites[node_id] = (raw_index, 0.0)

    def trace_path(start: int, child: int) -> list[int]:
        path = [start, child]
        visited_edges.add((start, child))
        current = child
        while current not in breakpoints:
            next_id = children[current][0]
            visited_edges.add((current, next_id))
            path.append(next_id)
            current = next_id
        return path

    queue = list(sorted(roots))
    processed: set[int] = set()
    while queue:
        start = queue.pop(0)
        if start in processed or start not in breakpoints:
            continue
        processed.add(start)
        if not children[start] and start not in raw_sites:
            add_singleton(start)
        for child in children[start]:
            if (start, child) in visited_edges:
                continue
            path = trace_path(start, child)
            add_path(path)
            end = path[-1]
            if end in breakpoints and end not in processed:
                queue.append(end)
    if set(raw_sites) != set(by_id):
        missing = sorted(set(by_id) - set(raw_sites))
        raise RuntimeError(f"Could not map normalized SWC nodes: {missing[:8]}")

    # BioNet's built-in spike detector and SomaReport are fixed at
    # hobj.soma[0](0.5).  Attach a 0.002-um app-owned proxy centered exactly on
    # Digifly's selected soma node, rather than silently recording the midpoint
    # of the incoming branch-run section (where the target is usually at x=1).
    target_parent_section, target_parent_pos = raw_sites[target_node_id]
    target_raw_section = len(raw_sections)
    raw_sections.append(
        {
            "path": [target_node_id],
            "length_um": 0.002,
            "uses_degenerate_stub": True,
            "uses_centered_target_stub": True,
            "parent_section_id": target_parent_section,
            "parent_section_pos": target_parent_pos,
        }
    )
    raw_sites[target_node_id] = (target_raw_section, 0.5)
    raw_order = [target_raw_section] + [
        index for index in range(len(raw_sections)) if index != target_raw_section
    ]
    final_by_raw = {raw: final for final, raw in enumerate(raw_order)}
    group_counts = {"soma": 0, "axon": 0, "dend": 0, "apic": 0}
    sections: list[dict[str, Any]] = []
    for final_id, raw_id in enumerate(raw_order):
        raw = dict(raw_sections[raw_id])
        path = list(raw["path"])
        newly_represented = path if len(path) == 1 else path[1:]
        represented_types = {int(by_id[node_id][1]) for node_id in newly_represented}
        if raw_id == target_raw_section or 1 in represented_types:
            group = "soma"
        elif 2 in represented_types:
            group = "axon"
        elif 4 in represented_types:
            group = "apic"
        else:
            group = "dend"
        group_index = group_counts[group]
        group_counts[group] += 1
        raw.update(
            {
                "section_id": final_id,
                "section_group": group,
                "section_group_index": group_index,
                "section_name": f"{group}[{group_index}]",
                "parent_section_id": (
                    final_by_raw[int(raw["parent_section_id"])]
                    if raw["parent_section_id"] is not None
                    else None
                ),
                "is_target_soma": raw_id == target_raw_section,
                "uses_centered_target_stub": bool(
                    raw.get("uses_centered_target_stub", False)
                ),
            }
        )
        sections.append(raw)
    sites = {
        node_id: {
            "section_id": final_by_raw[raw_id],
            "section_pos": float(position),
        }
        for node_id, (raw_id, position) in raw_sites.items()
    }
    estimated_nseg = sum(
        1 + 2 * int(float(section["length_um"]) / (2.0 * TARGET_SEGMENT_UM))
        for section in sections
    )
    return {
        "target_node_id": target_node_id,
        "sections": sections,
        "node_sites": sites,
        "estimated_nseg": estimated_nseg,
        "degenerate_stub_count": sum(
            bool(section["uses_degenerate_stub"]) for section in sections
        ),
    }


def _write_section_map(path: Path, plan: Mapping[str, Any]) -> None:
    target_node_id = int(plan["target_node_id"])
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "run_node_id",
                "bionet_section_id",
                "bionet_section_pos",
                "is_target_soma",
            ),
        )
        writer.writeheader()
        for node_id, site in sorted(dict(plan["node_sites"]).items()):
            writer.writerow(
                {
                    "run_node_id": int(node_id),
                    "bionet_section_id": int(site["section_id"]),
                    "bionet_section_pos": format(float(site["section_pos"]), ".17g"),
                    "is_target_soma": int(node_id) == target_node_id,
                }
            )


def _prepare_morphologies(
    request: Mapping[str, Any], output_dir: Path, edge_manifest: Mapping[str, Any] | None = None
) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]], dict[str, Any]]:
    circuit = dict(request["circuit"])
    neuron_ids = tuple(str(value) for value in circuit["neuron_ids"])
    source_records = dict(request["morphologies"])
    morphology_root = output_dir / "morphologies"
    component_root = output_dir / "sonata" / "components"
    model_root = component_root / "biophysical_models"
    synapse_root = component_root / "synaptic_models"
    morphology_root.mkdir(parents=True, exist_ok=False)
    model_root.mkdir(parents=True, exist_ok=False)
    synapse_root.mkdir(parents=True, exist_ok=False)

    cells: dict[str, dict[str, Any]] = {}
    artifacts: list[dict[str, str]] = []
    metadata: dict[str, Any] = {}
    total_nseg = 0
    for bmtk_node_id, neuron_id in enumerate(neuron_ids):
        source_record = dict(source_records[neuron_id])
        source_path = Path(str(source_record["path"])).resolve()
        safe_key = _safe_component(neuron_id, prefix="cell")
        cell_dir = morphology_root / safe_key
        cell_dir.mkdir()
        normalized_path, node_map_path, normalized_sha = _common._normalize_swc(
            source_path,
            cell_dir,
            expected_source_sha256=str(source_record["sha256"]),
            source_label=f"Source SWC for {neuron_id}",
            coordinate_transform=source_record.get("coordinate_transform"),
        )
        rows = _common._swc_rows(normalized_path)
        prefer_rostral = str(source_record.get("family") or "").upper().startswith("DN")
        source_to_run = _common._source_to_run_node_map(node_map_path)
        regional_data = None
        if _common._regional_execution(circuit, dict(request["experiment"])):
            regional_data = _common._regional_cell_data(circuit, neuron_id, source_to_run)
            regional_data["electrical_contact_run_node_ids"] = _common._electrical_contact_run_nodes(edge_manifest or {}, neuron_id, source_to_run)
            regional_data["prefer_rostral_soma"] = prefer_rostral
            plan = _common._regional_plan(rows, regional_data)
            _common._atomic_json(cell_dir / "regional_map.json", plan)
        else:
            plan = _section_plan(rows, prefer_rostral_soma=prefer_rostral)
        total_nseg += int(plan["estimated_nseg"])
        if total_nseg > MAX_TOTAL_SEGMENTS:
            raise RuntimeError(
                "The selected BioNet circuit exceeds the explicit "
                f"{MAX_TOTAL_SEGMENTS:,}-segment safety limit at {TARGET_SEGMENT_UM:g} um spacing"
            )
        section_map_path = cell_dir / "bionet_section_map.csv"
        _write_section_map(section_map_path, plan)
        source_to_run = _common._source_to_run_node_map(node_map_path)
        dynamics_name = f"{safe_key}.json"
        dynamics_path = model_root / dynamics_name
        _common._atomic_json(
            dynamics_path,
            {
                "schema_version": 1,
                "lane": LANE_ID,
                "neuron_id": neuron_id,
                "prefer_rostral_soma": prefer_rostral,
                "hh": _common._merged_hh(circuit, neuron_id),
                "membrane": dict(dict(circuit.get("neuron_mechanism_overrides") or {}).get(neuron_id, circuit.get("membrane") or {})),
                "regional_data": regional_data,
            },
        )
        cells[neuron_id] = {
            "bmtk_node_id": bmtk_node_id,
            "safe_key": safe_key,
            "morphology": str(normalized_path.relative_to(morphology_root)),
            "normalized_path": normalized_path,
            "normalized_sha256": normalized_sha,
            "node_map_path": node_map_path,
            "section_map_path": section_map_path,
            "source_to_run": source_to_run,
            "section_plan": plan,
            "dynamics_params": dynamics_name,
            "neuron_type": str(source_record.get("neuron_type") or "Unknown"),
            "family": str(source_record.get("family") or ""),
            "regional_data": regional_data,
            "source_sha256": str(source_record["sha256"]),
        }
        relative_dir = Path("morphologies") / safe_key
        if regional_data:
            artifacts.append({"kind": "document", "path": str(relative_dir / "regional_map.json"), "label": f"SWC edge/CV biophysics and recording map · {neuron_id}"})
        artifacts.extend(
            (
                {
                    "kind": "morphology",
                    "path": str(relative_dir / normalized_path.name),
                    "label": f"Normalized morphology · {neuron_id}",
                },
                {
                    "kind": "table",
                    "path": str(relative_dir / node_map_path.name),
                    "label": f"Source-to-run SWC map · {neuron_id}",
                },
                {
                    "kind": "table",
                    "path": str(relative_dir / section_map_path.name),
                    "label": f"BioNet section map · {neuron_id}",
                },
            )
        )
        metadata[neuron_id] = {
            "safe_key": safe_key,
            "bmtk_node_id": bmtk_node_id,
            "source_swc_sha256": str(source_record["sha256"]),
            "simulation_swc_sha256": normalized_sha,
            "coordinate_transform": source_record.get("coordinate_transform"),
            "target_run_node_id": int(plan["target_node_id"]),
            "section_count": len(plan["sections"]),
            "estimated_nseg": int(plan["estimated_nseg"]),
            "degenerate_stub_count": int(plan["degenerate_stub_count"]),
            "target_soma_proxy_length_um": TARGET_SOMA_PROXY_LENGTH_UM,
            "degenerate_stub_length_um": DEGENERATE_STUB_LENGTH_UM,
            "diameter_policy": "Exact source diameter (2 × positive SWC radius); no floor.",
        }

    crosswalk_path = output_dir / "sonata" / "node_id_crosswalk.csv"
    crosswalk_path.parent.mkdir(parents=True, exist_ok=True)
    with crosswalk_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=("bmtk_node_id", "neuron_id", "neuron_type")
        )
        writer.writeheader()
        for neuron_id in neuron_ids:
            cell = cells[neuron_id]
            writer.writerow(
                {
                    "bmtk_node_id": cell["bmtk_node_id"],
                    "neuron_id": neuron_id,
                    "neuron_type": cell["neuron_type"],
                }
            )
    artifacts.append(
        {"kind": "table", "path": "sonata/node_id_crosswalk.csv", "label": "BioNet node-ID crosswalk"}
    )
    return cells, artifacts, metadata


def _resolve_edge_manifest(
    edge_manifest: Mapping[str, Any], cells: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    resolved = json.loads(json.dumps(edge_manifest))
    for contact in resolved.get("chemical_edges") or ():
        post_id = str(contact["post_id"])
        cell = dict(cells[post_id])
        source_node_id = int(contact["post_source_node_id"])
        run_node_id = dict(cell["source_to_run"]).get(source_node_id)
        if run_node_id is None:
            raise ValueError(
                f"Chemical contact {contact['contact_id']} maps to missing source SWC node {source_node_id}"
            )
        site = dict(cell["section_plan"]["node_sites"]).get(run_node_id)
        if site is None:
            raise ValueError(
                f"Chemical contact {contact['contact_id']} maps to missing run SWC node {run_node_id}"
            )
        contact["post_run_node_id"] = int(run_node_id)
        if "post_source_edge_fraction" in contact:
            section_id, section_pos = _common._regional_contact_site(cell["section_plan"],
                run_node_id, float(contact["post_source_edge_fraction"]))
            site = {"section_id": section_id, "section_pos": section_pos}
        contact["bionet_section_id"] = int(site["section_id"])
        contact["bionet_section_pos"] = float(site["section_pos"])
    for edge in resolved.get("electrical_edges") or ():
        for contact in edge["contacts"]:
            for end in ("pre", "post"):
                cell = cells[str(contact[f"{end}_id"])]
                run_node = cell["source_to_run"].get(int(contact[f"{end}_source_node_id"]))
                if f"{end}_source_edge_fraction" in contact:
                    section_id,x = _common._regional_contact_site(cell["section_plan"],run_node,float(contact[f"{end}_source_edge_fraction"]))
                    site = {"section_id":section_id, "section_pos":x}
                else:
                    site = cell["section_plan"]["node_sites"].get(run_node)
                if site is None:
                    raise ValueError("Electrical contact cannot map to a BioNet section")
                contact[f"{end}_run_node_id"] = int(run_node)
                contact[f"{end}_bionet_node_id"] = int(cell["bmtk_node_id"])
                contact[f"{end}_bionet_section_id"] = int(site["section_id"])
                contact[f"{end}_bionet_section_pos"] = float(site["section_pos"])
    return resolved


def _condition_bionet_cells(
    cells: Mapping[str, Mapping[str, Any]], condition: Mapping[str, Any],
    output_dir: Path, condition_key: str,
) -> dict[str, dict[str, Any]]:
    """Own dynamics and contiguous SONATA node IDs for the ablated/scaled condition."""
    disabled = {str(value) for value in condition.get("disabled_neuron_ids") or ()}
    model_root = output_dir / "sonata" / "components" / "biophysical_models"
    condition_model_root = model_root / condition_key
    condition_model_root.mkdir()
    result = {}
    for neuron_id, original in cells.items():
        if neuron_id in disabled:
            continue
        cell = dict(original)
        cell["bmtk_node_id"] = len(result)
        dynamics = _load_object(model_root / str(original["dynamics_params"]), label="source-bound cell dynamics")
        dynamics = _common._scale_cell_biophysics(dynamics, dict(condition.get("mechanism_scales") or {}))
        if dynamics.get("regional_data"):
            regional = _common._scale_cell_biophysics(dynamics["regional_data"], dict(condition.get("mechanism_scales") or {}))
            dynamics["regional_data"] = regional
            cell["regional_data"] = regional
            cell["section_plan"] = _common._regional_plan(_common._swc_rows(Path(cell["normalized_path"])), regional)
        model_name = str(Path(condition_key) / (str(cell["safe_key"]) + ".json"))
        _common._atomic_json(model_root / model_name, dynamics)
        cell["dynamics_params"] = model_name
        result[neuron_id] = cell
    return result


def _write_synapse_models(
    output_dir: Path, resolved_edge_manifest: Mapping[str, Any]
) -> dict[str, str]:
    root = output_dir / "sonata" / "components" / "synaptic_models"
    models: dict[str, str] = {}
    signatures: dict[str, str] = {}
    for contact in resolved_edge_manifest.get("chemical_edges") or ():
        contact_id = str(contact["contact_id"])
        dynamics = {
            "erev": float(contact["reversal_mV"]),
            "tau1": float(contact["tau1_ms"]),
            "tau2": float(contact["tau2_ms"]),
        }
        signature = _json_sha256(dynamics)
        filename = signatures.get(signature)
        if filename is None:
            filename = f"exp2syn-{signature}.json"
            _common._atomic_json(root / filename, dynamics)
            signatures[signature] = filename
        models[contact_id] = filename
    return models


def _network_file_entries(network_dir: Path, *, chemical_enabled: bool) -> dict[str, Any]:
    nodes = {
        "nodes_file": str((network_dir / "digifly_nodes.h5").resolve()),
        "node_types_file": str((network_dir / "digifly_node_types.csv").resolve()),
    }
    edges: list[dict[str, str]] = []
    if chemical_enabled:
        edge_path = network_dir / "digifly_edges.h5"
        if edge_path.is_file():
            edges.append(
                {
                    "edges_file": str(edge_path.resolve()),
                    "edge_types_file": str(
                        (network_dir / "digifly_edge_types.csv").resolve()
                    ),
                }
            )
    return {"nodes": [nodes], "edges": edges}


def _materialize_sonata_network(
    network_builder: Any,
    output_dir: Path,
    condition_dir: Path,
    cells: Mapping[str, Mapping[str, Any]],
    resolved_edge_manifest: Mapping[str, Any],
    synapse_models: Mapping[str, str],
    *,
    chemical_enabled: bool,
) -> dict[str, Any]:
    """Build condition-exact SONATA nodes and unaggregated chemical edges."""

    network_dir = condition_dir / "network"
    network_dir.mkdir(parents=True, exist_ok=False)
    network = network_builder(POPULATION)
    for neuron_id, cell in cells.items():
        network.add_nodes(
            N=1,
            model_type="biophysical",
            model_template=CELL_MODEL_TEMPLATE,
            morphology=str(cell["morphology"]),
            dynamics_params=str(cell["dynamics_params"]),
            digifly_neuron_id=neuron_id,
        )
    if chemical_enabled:
        for contact in resolved_edge_manifest.get("chemical_edges") or ():
            network.add_edges(
                source={"digifly_neuron_id": str(contact["pre_id"])},
                target={"digifly_neuron_id": str(contact["post_id"])},
                connection_rule=1,
                model_template="exp2syn",
                dynamics_params=str(synapse_models[str(contact["contact_id"])]),
                syn_weight=float(contact["weight_uS"]),
                delay=float(contact["delay_ms"]),
                afferent_section_id=int(contact["bionet_section_id"]),
                afferent_section_pos=float(contact["bionet_section_pos"]),
                digifly_contact_id=str(contact["contact_id"]),
            )
    network.build()
    network.save_nodes(
        nodes_file_name="digifly_nodes.h5",
        node_types_file_name="digifly_node_types.csv",
        output_dir=str(network_dir),
        force_overwrite=True,
    )
    if chemical_enabled and resolved_edge_manifest.get("chemical_edges"):
        network.save_edges(
            edges_file_name="digifly_edges.h5",
            edge_types_file_name="digifly_edge_types.csv",
            output_dir=str(network_dir),
            force_overwrite=True,
        )
    entries = _network_file_entries(network_dir, chemical_enabled=chemical_enabled)
    circuit_config = {
        "schema_version": 1,
        "lane": LANE_ID,
        "target_simulator": "BioNet",
        "components": {
            "morphologies_dir": str((output_dir / "morphologies").resolve()),
            "biophysical_neuron_models_dir": str(
                (output_dir / "sonata" / "components" / "biophysical_models").resolve()
            ),
            "synaptic_models_dir": str(
                (output_dir / "sonata" / "components" / "synaptic_models").resolve()
            ),
        },
        "networks": entries,
    }
    _common._atomic_json(condition_dir / "circuit_config.json", circuit_config)
    return circuit_config


def _simulation_config(
    *,
    output_dir: Path,
    repetition_dir: Path,
    circuit_config: Mapping[str, Any],
    cells: Mapping[str, Mapping[str, Any]],
    experiment: Mapping[str, Any],
    stimulus: Mapping[str, Any],
    condition: Mapping[str, Any],
    repetition: int,
    chemical_spike_threshold_mV: float,
) -> dict[str, Any]:
    """Translate the supported generic experiment subset to one BioNet config."""

    neuron_ids = tuple(cells)
    stimulus_ids = tuple(
        str(value) for value in stimulus.get("target_neuron_ids") or neuron_ids
        if str(value) in cells
    )
    recording = dict(experiment["recording"])
    recording_ids = tuple(
        str(value) for value in recording.get("target_neuron_ids") or neuron_ids
        if str(value) in cells
    )
    node_ids = {neuron_id: int(cells[neuron_id]["bmtk_node_id"]) for neuron_id in neuron_ids}
    windows = _common._pulse_windows(stimulus)
    amplitude = float(stimulus["amplitude_nA"]) * float(
        condition.get("stimulus_scale", 1.0)
    )
    native_output = repetition_dir / "native_output"
    steps = max(
        1,
        int(
            math.ceil(
                float(experiment["duration_ms"])
                / float(experiment["integration_dt_ms"])
            )
        ),
    )
    payload = {
        "target_simulator": "BioNet",
        "digifly": {
            "schema_version": 1,
            "lane": LANE_ID,
            "condition": str(condition["name"]),
            "repetition": int(repetition),
            "random_seed": int(experiment.get("random_seed", 1)) + int(repetition) - 1,
            "chemical_synapses_enabled": bool(
                condition.get("chemical_synapses_enabled", True)
            ),
        },
        "run": {
            "tstart": 0.0,
            "tstop": float(experiment["duration_ms"]),
            "dt": float(experiment["integration_dt_ms"]),
            # Owned regional templates already materialize the shared nseg
            # map. BioCell otherwise rewrites it AFTER density decoration,
            # before constructing report/clamp segment indices.
            "dL": None if any(c.get("regional_data") for c in cells.values()) else TARGET_SEGMENT_UM,
            "spike_threshold": float(chemical_spike_threshold_mV),
            "nsteps_block": min(10_000, steps),
        },
        "conditions": {
            "v_init": float(experiment["initial_voltage_mV"]),
            "celsius": float(experiment["temperature_C"]),
        },
        "components": dict(circuit_config["components"]),
        "networks": dict(circuit_config["networks"]),
        "node_sets": {
            "stimulus_targets": {
                "population": POPULATION,
                "node_id": [node_ids[neuron_id] for neuron_id in stimulus_ids],
            },
            "recording_targets": {
                "population": POPULATION,
                "node_id": [node_ids[neuron_id] for neuron_id in recording_ids],
            },
        },
        "inputs": {
            "primary_stimulus": {
                "input_type": "current_clamp",
                "module": "IClamp",
                "node_set": "stimulus_targets",
                "amp": [amplitude for _ in windows],
                "delay": [float(delay) for delay, _ in windows],
                "duration": [float(duration) for _, duration in windows],
                "section_name": "soma",
                "section_index": 0,
                "section_dist": 0.5,
            }
        },
        "reports": {
            "soma_voltage": {
                "module": "membrane_report",
                "cells": "recording_targets",
                "variable_name": "v",
                "sections": "soma",
                "file_name": str((native_output / "soma_voltage.h5").resolve()),
                "dt": float(experiment["integration_dt_ms"]),
            }
        },
        "output": {
            "output_dir": str(native_output.resolve()),
            "log_file": str((native_output / "bionet.log").resolve()),
            "spikes_file": str((native_output / "spikes.h5").resolve()),
            "spikes_sort_order": "by_time",
            "overwrite_output_dir": True,
        },
    }
    if any(c.get("regional_data") for c in cells.values()):
        payload["reports"]["soma_voltage"]["sections"] = "all"
        payload["inputs"] = {}
        for neuron_id in stimulus_ids:
            cell = cells[neuron_id]
            specs = _common._regional_sites(cell["section_plan"], stimulus["target_region"], cell["regional_data"])
            node_set = f"stimulus_{cell['bmtk_node_id']}"
            payload["node_sets"][node_set] = {"population":POPULATION, "node_id":[node_ids[neuron_id]]}
            for spec in specs:
                payload["inputs"][f"{node_set}_edge_{spec['node_id']}"] = {
                    "input_type":"current_clamp", "module":"IClamp", "node_set":node_set,
                    "amp":[amplitude/len(specs) for _ in windows], "delay":[float(t) for t,_ in windows],
                    "duration":[float(t) for _,t in windows], "section_name":"all",
                    "section_index":spec["section_id"], "section_dist":.5}
    if stimulus.get("waveform") == "ramp":
        ramp_sites = []
        for neuron_id in stimulus_ids:
            cell = cells[neuron_id]
            specs = _common._regional_sites(cell["section_plan"], stimulus["target_region"], cell["regional_data"]) if cell.get("regional_data") else [{"section_id":0}]
            for spec in specs:
                ramp_sites.append({"node_id":node_ids[neuron_id], "neuron_id":neuron_id,
                    "section_name":"all" if cell.get("regional_data") else "soma",
                    "section_id":int(spec["section_id"]), "section_pos":.5,
                    "amplitude_nA":amplitude/len(specs)})
        payload["digifly"]["ramp_stimulus"] = {"stimulus":dict(stimulus), "sites":ramp_sites,
            "policy":"Continuous linear 0-to-peak current over declared interval, then instantaneous cutoff; total current divided over target edges"}
        payload["inputs"] = {}
    if not stimulus_ids:
        payload["inputs"] = {}
    if not recording_ids:
        payload["reports"] = {}
    return payload


class _ClassicHHCell:
    """Minimal Python NEURON morphology object consumed by BioNet's BioCell."""

    def __init__(self, h: Any, swc_path: Path, params: Mapping[str, Any]):
        if params.get("regional_data"):
            built = _common._neuron_regional_cell(h, swc_path, str(params["neuron_id"]), params["regional_data"], MAX_TOTAL_SEGMENTS)
            self.all = list(built["sections"])
            self.digifly_regional_plan = built["regional_plan"]
            # NEURON stores pt3d coordinates at native precision. Compare the
            # final BioCell with this constructed geometry, not the ideal
            # double-precision Euclidean lengths in the simulator-free plan.
            # This does not change geometry or relax the framework grid guard.
            self.digifly_regional_sections = tuple(self.all)
            self.digifly_regional_native = _regional_native_geometry(self.all)
            for group in ("soma", "axon", "dend", "apic"):
                setattr(self, group, [self.all[s["section_id"]] for s in built["regional_plan"]["sections"] if s["section_group"] == group])
            return
        rows = _common._swc_rows(swc_path)
        by_id = {int(row[0]): row for row in rows}
        plan = _section_plan(
            rows,
            prefer_rostral_soma=bool(params.get("prefer_rostral_soma", False)),
        )
        self.all: list[Any] = []
        self.soma: list[Any] = []
        self.axon: list[Any] = []
        self.dend: list[Any] = []
        self.apic: list[Any] = []
        for section_plan in plan["sections"]:
            group = str(section_plan["section_group"])
            section = h.Section(name=str(section_plan["section_name"]), cell=self)
            self.all.append(section)
            getattr(self, group).append(section)

        for section_plan, section in zip(plan["sections"], self.all):
            h.pt3dclear(sec=section)
            path = [int(value) for value in section_plan["path"]]
            if bool(section_plan.get("uses_centered_target_stub", False)):
                row = by_id[path[0]]
                diameter = _swc_diameter(row[5])
                h.pt3dadd(
                    float(row[2]) - TARGET_SOMA_PROXY_LENGTH_UM / 2.0,
                    float(row[3]),
                    float(row[4]),
                    diameter,
                    sec=section,
                )
                h.pt3dadd(
                    float(row[2]) + TARGET_SOMA_PROXY_LENGTH_UM / 2.0,
                    float(row[3]),
                    float(row[4]),
                    diameter,
                    sec=section,
                )
            else:
                for node_id in path:
                    row = by_id[node_id]
                    h.pt3dadd(
                        float(row[2]),
                        float(row[3]),
                        float(row[4]),
                        _swc_diameter(row[5]),
                        sec=section,
                    )
            if bool(section_plan["uses_degenerate_stub"]) and not bool(
                section_plan.get("uses_centered_target_stub", False)
            ):
                final = by_id[path[-1]]
                h.pt3dadd(
                    float(final[2]) + DEGENERATE_STUB_LENGTH_UM,
                    float(final[3]),
                    float(final[4]),
                    _swc_diameter(final[5]),
                    sec=section,
                )
            parent_id = section_plan["parent_section_id"]
            if parent_id is not None:
                section.connect(
                    self.all[int(parent_id)](float(section_plan["parent_section_pos"])),
                    0.0,
                )

        hh = dict(params["hh"])
        total_nseg = 0
        for section_plan, section in zip(plan["sections"], self.all):
            section.Ra = float(hh["ra_ohm_cm"])
            section.cm = float(hh["cm_uF_cm2"])
            # Segment first: NEURON creates fresh range variables when nseg
            # changes, so assigning HH values before this point would silently
            # leave the newly-created segments at mechanism defaults.
            target = 1 + 2 * int(float(section.L) / (2.0 * TARGET_SEGMENT_UM))
            section.nseg = max(1, target)
            total_nseg += int(section.nseg)
            _common._decorate_neuron_section(
                section, hh, dict(params.get("membrane") or {}),
                soma=str(section_plan["section_group"]) == "soma",
            )
        if total_nseg > MAX_TOTAL_SEGMENTS:
            raise RuntimeError("A BioNet cell exceeded the explicit segment safety limit")


def _bionet_cell_loader(node: Any, _template_name: str, dynamics_params: Mapping[str, Any]) -> Any:
    from neuron import h

    morphology_path = Path(str(node.morphology_file)).resolve()
    if not morphology_path.is_file():
        raise FileNotFoundError(f"BioNet morphology is missing: {morphology_path}")
    params = dict(dynamics_params or {})
    if params.get("lane") != LANE_ID:
        raise ValueError("BioNet cell dynamics were not generated by this worker lane")
    return _ClassicHHCell(h, morphology_path, params)


def _attach_electrical_contacts(h: Any, network: Any, extension: Mapping[str, Any]) -> list[Any]:
    """Serial Digifly extension on actual BioNetwork cells, before framework run.

    BMTK1.2's built-in gap route assumes another Gap ABI and one junction per
    pair. Preserve all imported sites and the app's pinned equations instead.
    The supplemental document is explicitly not standard SONATA portability.
    """
    if not extension.get("enabled"):
        return []
    local_cells = network.get_local_cells()
    if set(int(x) for x in local_cells) != set(int(x) for x in extension["node_ids"]):
        raise ValueError("Electrical extension requires all selected BioNet cells on this serial rank")
    processes = []
    policy = extension["policy"]
    mode = policy["mode"]
    for edge in extension["edges"]:
        a, b = str(edge["neuron_a"]), str(edge["neuron_b"])
        for contact in edge["contacts"]:
            sites = {}
            for end in ("pre", "post"):
                cell = local_cells[int(contact[f"{end}_bionet_node_id"])]
                section = cell.hobj.all[int(contact[f"{end}_bionet_section_id"])]
                x = float(contact[f"{end}_bionet_section_pos"])
                if not 0 <= x <= 1:
                    raise ValueError("Electrical section position is outside its cable")
                sites[str(contact[f"{end}_id"])] = (section, x)
            site_a, site_b = sites[a], sites[b]
            g = float(contact["effective_g_uS"])*1000.0
            if mode == "ohmic":
                pair = [h.Gap(x, sec=sec) for sec, x in (site_a, site_b)]
                for process, peer in zip(pair, (site_b, site_a)):
                    process.g = g
                    _common._neuron_set_gap_pointer(h, process, peer)
                processes.extend(pair)
            elif mode == "rectifying":
                source, target = (site_a, site_b) if policy["preferred_direction"] == "a_to_b" else (site_b, site_a)
                sec, x = target
                process = h.RectGap(x, sec=sec)
                process.gmax = g
                _common._neuron_set_gap_pointer(h, process, source)
                processes.append(process)
            elif mode == "heterotypic_rectifying":
                pair = [h.HeteroRectGap(x, sec=sec) for sec, x in (site_a, site_b)]
                signs = (1., -1.) if policy["preferred_direction"] == "a_to_b" else (-1., 1.)
                for process, orientation, peer in zip(pair, signs, (site_b, site_a)):
                    process.gmax_open = g
                    process.gmax_closed = g*float(policy["g_closed_frac"])
                    process.orientation = orientation
                    for dest, source in (("vhalf", "vhalf_mV"), ("vslope", "vslope_mV"),
                                         ("empirical_residual_frac", "empirical_residual_frac"),
                                         ("tau_open_ms", "tau_open_ms"), ("tau_close_ms", "tau_close_ms")):
                        setattr(process, dest, float(policy[source]))
                    _common._neuron_set_gap_pointer(h, process, peer)
                processes.extend(pair)
            else:
                raise ValueError("Electrical extension mode is unsupported")
    return processes


def _regional_native_geometry(sections: Sequence[Any]) -> list[dict[str, Any]]:
    records = []
    indices = {section: index for index, section in enumerate(sections)}
    for section in sections:
        parent = section.parentseg()
        parent_index = indices.get(parent.sec) if parent is not None else None
        if parent is not None and parent_index is None:
            raise ValueError("Owned regional section has an external parent")
        records.append({"nseg": int(section.nseg), "length_um": float(section.L),
            "parent_section_id": parent_index,
            "parent_section_pos": float(parent.x) if parent is not None else None,
            "orientation": float(section.orientation()),
            "points": [[float(section.x3d(i)), float(section.y3d(i)), float(section.z3d(i)),
                        float(section.diam3d(i))] for i in range(int(section.n3d()))]})
    return records


def _regional_framework_cell_record(cell: Any) -> dict[str, Any] | None:
    """Check the FINAL BioCell morphology before framework clamps/reports."""
    plan = getattr(cell.hobj, "digifly_regional_plan", None)
    if not plan:
        return None
    sections = list(cell.hobj.all)
    owned = getattr(cell.hobj, "digifly_regional_sections", ())
    baseline = getattr(cell.hobj, "digifly_regional_native", None)
    if (len(sections) != len(plan["sections"]) or len(owned) != len(sections)
            or any(a != b for a, b in zip(sections, owned)) or baseline is None):
        raise ValueError("BioNet changed the owned regional section map")
    native = _regional_native_geometry(sections)
    if native != baseline or any(a["nseg"] != s["nseg"] for a, s in zip(native, plan["sections"])):
        raise ValueError("BioNet changed the owned regional spatial discretization or native geometry")
    actual = [{"node_id":s["node_id"], "nseg":int(sec.nseg), "length_um":float(sec.L)}
              for s,sec in zip(plan["sections"],sections)]
    return {"neuron_id":plan["neuron_id"], "sections":actual,
        "estimated_nseg":sum(a["nseg"] for a in actual),
        "native_geometry_unchanged": True,
        "native_geometry_sha256": hashlib.sha256(json.dumps(native, sort_keys=True).encode()).hexdigest(),
        "maximum_native_vs_ideal_length_error_um": max((abs(a["length_um"]-s["length_um"])
            for a,s in zip(actual,plan["sections"])), default=0.)}


def _validate_independent_trial_inputs(config_payload: Mapping[str, Any], *, hash_bound: bool) -> None:
    marker = dict(config_payload.get("digifly") or {})
    fields = {"independent_trial_input_pins", "independent_source_edge_manifest",
        "independent_mapped_edge_manifest", "independent_active_neuron_ids", "independent_mapped_contact_sha256"}
    if not hash_bound and not fields.intersection(marker):
        return  # Preserve the existing serial child lane.
    if not hash_bound or not fields.issubset(marker) or not marker["independent_trial_input_pins"]:
        raise ValueError("Independent BioNet trial contact/source binding is incomplete")
    _common._verify_trial_pins(marker["independent_trial_input_pins"])
    _common._validate_independent_contact_manifest(marker["independent_source_edge_manifest"],
        marker["independent_mapped_edge_manifest"], marker["independent_active_neuron_ids"],
        marker["independent_mapped_contact_sha256"])


def execute_config(config_path: Path, expected_sha256: str = "") -> None:
    """Internal child-process entry point for one condition/repetition."""

    config_path = config_path.resolve()
    if not config_path.is_file() or config_path.name != "simulation_config.json":
        raise ValueError("The BioNet simulation config path is invalid")
    if expected_sha256 and _common._sha256(config_path) != expected_sha256:
        raise ValueError("Independent BioNet trial config changed before child execution")
    config_payload = _load_object(config_path, label="BioNet simulation config")
    marker = dict(config_payload.get("digifly") or {})
    _validate_independent_trial_inputs(config_payload, hash_bound=bool(expected_sha256))
    if marker.get("lane") != LANE_ID or config_payload.get("target_simulator") != "BioNet":
        raise ValueError("Refusing a simulation config outside the Digifly BioNet lane")
    _network_builder, bionet, _h5py, _runtime = _require_bionet_runtime()
    native = dict(marker.get("native_channel_execution") or {})
    gap = dict(marker.get("gap_mechanism_execution") or {})
    if gap:
        import neuron
        _common._load_neuron_gap_mechanisms(neuron.h, neuron, Path(gap["mechanism_dir"]), expected_manifest_sha256=gap["manifest_sha256"])
    if native:
        import neuron
        from neuron import h
        # Upstream Augustin SUFFIX k shares a name with Import3d's hoc local.
        # Parse the standard morphology procedures before registering k;
        # preserve the original channel suffix and equations unchanged.
        if not h.load_file("import3d.hoc"):
            raise RuntimeError("BioNet morphology importer could not initialize")
        library = Path(native["library"])
        if _common._sha256(library) != native["library_sha256"] or neuron.__version__ != native["neuron_version"]:
            raise ValueError("BioNet native-channel library identity/runtime mismatch")
        for key, record in native["sources"].items():
            if (record.get("suffix"), record.get("sha256")) != _common.NATIVE_CHANNELS.get(key):
                raise ValueError("BioNet native-channel source identity mismatch")
            probe = h.Section()
            try:
                probe.insert(record["suffix"])
            except ValueError:
                pass
            else:
                raise ValueError("BioNet runtime contains an unverified channel collision")
            finally:
                h.delete_section(sec=probe)
        if not h.nrn_load_dll(str(library)):
            raise RuntimeError("BioNet could not load the verified native-channel library")
        for record in native["sources"].values():
            probe = h.Section()
            try:
                probe.insert(record["suffix"])
            finally:
                h.delete_section(sec=probe)
    bionet.cell_model(CELL_MODEL_TEMPLATE, model_type="biophysical")(
        _bionet_cell_loader
    )
    config = bionet.Config.from_json(str(config_path))
    config.build_env()
    class OwnedRegionalBioNetwork(bionet.BioNetwork):
        # BioSimulator calls build_nodes BEFORE creating IClamps or report
        # modules. Validate each constructed BioCell here, not by changing
        # nseg after framework segment caches have already been allocated.
        def _build_cell(self, *args: Any, **kwargs: Any) -> Any:
            cell = super()._build_cell(*args, **kwargs)
            record = _regional_framework_cell_record(cell)
            if record:
                self.digifly_actual_regional.append(record)
            return cell
    network = OwnedRegionalBioNetwork.from_config(config)
    network.digifly_actual_regional = []
    simulator = bionet.BioSimulator.from_config(config, network=network)
    actual_regional = network.digifly_actual_regional
    if actual_regional:
        _common._require_neuron_segment_budget(0,sum(p["estimated_nseg"] for p in actual_regional),segment_budget=MAX_TOTAL_SEGMENTS)
        _common._atomic_json(config_path.parent / "regional_execution.json", {
            "schema_version":1, "framework":"actual BMTK BioNetwork/BioSimulator cells before run",
            "framework_dL":network.dL, "cells":actual_regional})
    extension = marker.get("electrical_extension")
    if extension:
        path = Path(extension["path"]).resolve()
        if path.parent != config_path.parent or _common._sha256(path) != extension["sha256"]:
            raise ValueError("Run-owned electrical extension identity changed")
        payload = _load_object(path, label="electrical extension")
        import neuron
        network._digifly_gap_processes = _attach_electrical_contacts(neuron.h, network, payload)
        _common._atomic_json(config_path.parent / "electrical_execution.json", {
            "schema_version":1, "extension_sha256":extension["sha256"],
            "framework":"BMTK BioNetwork/BioSimulator", "workers":1,
            "enabled":payload["enabled"], "mode":payload["policy"]["mode"],
            "imported_contacts":sum(len(e["contacts"]) for e in payload["edges"]),
            "point_processes":len(network._digifly_gap_processes),
            "standard_sonata_portable":False})
    for cell in network.get_local_cells().values():
        plan = getattr(cell.hobj, "digifly_regional_plan", None)
        if plan:
            # The app-authored Python morphology has no Import3d `nl` field.
            # Annotate actual framework reports from its explicit source-edge
            # map, rather than BMTK's Import3d branch-name heuristic.
            def source_swc_id(sec_id, sec_x, plan=plan):
                spec = plan["sections"][int(sec_id)]
                return (int(spec["path"][0]) if float(sec_x) == 0 else int(spec["node_id"]), float(sec_x))
            cell.morphology.get_swc_id = source_swc_id
    ramp = marker.get("ramp_stimulus")
    if ramp:
        import neuron
        ramp_objects = []
        local_cells = network.get_local_cells()
        for site in ramp["sites"]:
            cell = local_cells[int(site["node_id"])]
            section = getattr(cell.hobj, site["section_name"])[int(site["section_id"])]
            ramp_objects.extend(_common._neuron_current_clamps(neuron.h, section(float(site["section_pos"])), ramp["stimulus"], float(site["amplitude_nA"])))
        network._digifly_ramp_objects = ramp_objects
        _common._atomic_json(config_path.parent/"ramp_execution.json", {"schema_version":1,
            "framework":"Actual BMTK BioNetwork/BioSimulator cells", "sites":ramp["sites"],
            "stimulus":ramp["stimulus"], "policy":ramp["policy"], "clamps":len(ramp["sites"])})
    simulator.run()
    _common._verify_trial_pins(marker.get("independent_trial_input_pins") or ())
    if expected_sha256 and _common._sha256(config_path) != expected_sha256:
        raise ValueError("Independent BioNet trial config changed during child execution")


def _report_arrays_to_rows(
    data: Any,
    node_ids: Sequence[Any],
    index_pointer: Sequence[Any],
    time_spec: Sequence[Any],
    node_id_crosswalk: Mapping[int, str],
    *,
    integration_dt_ms: float,
    expected_duration_ms: float,
    expected_sample_dt_ms: float,
    condition: str,
    repetition: int,
) -> list[dict[str, Any]]:
    """Pure conversion of a BMTK 1.2 soma report to canonical long rows.

    BioSimulator invokes ``SomaReport.step`` after each NEURON ``fadvance``;
    its first stored value is consequently at one integration step even though
    the native SONATA mapping retains the configured report start time.
    """

    raw_node_ids = [int(value) for value in node_ids]
    pointers = [int(value) for value in index_pointer]
    times = [float(value) for value in time_spec]
    if len(pointers) != len(raw_node_ids) + 1 or len(times) != 3:
        raise ValueError("The native soma report has an invalid SONATA mapping")
    if (
        not raw_node_ids
        or len(set(raw_node_ids)) != len(raw_node_ids)
        or pointers[0] != 0
        or pointers[-1] != len(raw_node_ids)
        or any(
            pointers[index + 1] - pointers[index] != 1
            for index in range(len(raw_node_ids))
        )
    ):
        raise ValueError("The BioNet soma report must contain exactly one element per node")
    if set(raw_node_ids) - set(node_id_crosswalk):
        raise ValueError("The native soma report contains an unknown BMTK node ID")
    tstart, tstop, dt = times
    if not all(math.isfinite(value) for value in times) or dt <= 0.0 or tstop <= tstart:
        raise ValueError("The native soma report has invalid time metadata")
    integration_dt = _finite(integration_dt_ms, label="BioNet integration_dt_ms")
    expected_duration = _finite(
        expected_duration_ms, label="BioNet expected duration_ms"
    )
    expected_sample_dt = _finite(
        expected_sample_dt_ms, label="BioNet expected sample_dt_ms"
    )
    if integration_dt <= 0.0 or integration_dt > dt:
        raise ValueError("BioNet integration_dt_ms must be positive and no larger than report dt")
    if expected_duration <= 0.0 or expected_sample_dt <= 0.0:
        raise ValueError("BioNet expected duration and sample dt must be positive")
    tolerance = max(1e-9, abs(expected_duration) * 1e-10)
    if not math.isclose(tstart, 0.0, rel_tol=0.0, abs_tol=tolerance):
        raise ValueError("The native soma report start time does not match the experiment")
    if not math.isclose(
        tstop, expected_duration, rel_tol=1e-10, abs_tol=tolerance
    ):
        raise ValueError("The native soma report end time does not match the experiment")
    if not math.isclose(
        dt, expected_sample_dt, rel_tol=1e-10, abs_tol=1e-10
    ):
        raise ValueError("The native soma report dt does not match the recording request")
    report_stride = dt / integration_dt
    if not math.isclose(report_stride, round(report_stride), rel_tol=0.0, abs_tol=1e-8):
        raise ValueError("The native report dt must be an integer multiple of integration_dt_ms")
    duration_steps = expected_duration / integration_dt
    if not math.isclose(
        duration_steps, round(duration_steps), rel_tol=0.0, abs_tol=1e-8
    ):
        raise ValueError("BioNet duration_ms must be an integer multiple of integration_dt_ms")
    stride_steps = int(round(report_stride))
    expected_sample_count = (
        int(round(duration_steps)) + stride_steps - 1
    ) // stride_steps

    rows: list[dict[str, Any]] = []
    sample_count = len(data)
    if sample_count != expected_sample_count:
        raise RuntimeError(
            "The native soma report sample count does not match the requested time grid: "
            f"expected {expected_sample_count}, received {sample_count}"
        )
    if sample_count < 2:
        raise RuntimeError("BioNet returned fewer than two soma-voltage samples")
    width = pointers[-1]
    for sample_index in range(sample_count):
        sample = data[sample_index]
        if len(sample) != width:
            raise ValueError("The native soma report data width does not match its mapping")
        time_ms = tstart + integration_dt + sample_index * dt
        if time_ms > tstop + max(1e-9, abs(dt) * 1e-6):
            raise ValueError("The native soma report extends beyond its declared time range")
        for node_index, bmtk_node_id in enumerate(raw_node_ids):
            voltage = float(sample[pointers[node_index]])
            if not math.isfinite(voltage):
                raise RuntimeError("BioNet returned a non-finite soma voltage")
            rows.append(
                {
                    "condition": str(condition),
                    "repetition": int(repetition),
                    "neuron_id": str(node_id_crosswalk[bmtk_node_id]),
                    "time_ms": float(time_ms),
                    "voltage_mV": voltage,
                }
            )
    return rows


def _native_voltage_rows(
    h5py: Any,
    report_path: Path,
    node_id_crosswalk: Mapping[int, str],
    *,
    integration_dt_ms: float,
    expected_duration_ms: float,
    expected_sample_dt_ms: float,
    condition: str,
    repetition: int,
    regional_cells: Mapping[str, Any] | None = None,
    recording_region: str = "soma",
) -> list[dict[str, Any]]:
    if not report_path.is_file():
        raise FileNotFoundError(f"BioNet did not produce its soma report: {report_path}")
    with h5py.File(report_path, "r") as handle:
        report_group = handle.get("report")
        if report_group is None:
            raise ValueError("The native soma report has no /report group")
        if POPULATION in report_group:
            population_group = report_group[POPULATION]
        else:
            names = list(report_group.keys())
            if len(names) != 1:
                raise ValueError("The native soma report population is ambiguous")
            population_group = report_group[names[0]]
        mapping = population_group.get("mapping")
        data = population_group.get("data")
        if mapping is None or data is None:
            raise ValueError("The native soma report is missing data or mapping")
        if regional_cells:
            nodes = [int(n) for n in mapping["node_ids"][:]]
            pointers = [int(n) for n in mapping["index_pointer"][:]]
            elements = list(mapping["element_ids"][:])
            positions = list(mapping["element_pos"][:])
            if len(pointers) != len(nodes)+1 or pointers[0] != 0 or pointers[-1] != data.shape[1] or len(elements) != data.shape[1] or len(positions) != data.shape[1] or len(set(nodes)) != len(nodes):
                raise ValueError("Invalid regional SONATA compartment mapping")
            result = []
            for i, bmtk_id in enumerate(nodes):
                if bmtk_id not in node_id_crosswalk:
                    raise ValueError("Unknown regional SONATA node ID")
                neuron_id = node_id_crosswalk[bmtk_id]
                cell = regional_cells[neuron_id]
                specs = _common._regional_sites(cell["section_plan"], recording_region, cell["regional_data"])
                for spec in specs:
                    columns = [j for j in range(pointers[i], pointers[i+1]) if int(elements[j]) == spec["section_id"] and math.isclose(float(positions[j]), .5, abs_tol=1e-9)]
                    if len(columns) != 1:
                        raise ValueError("SWC edge midpoint does not have exactly one SONATA element")
                    rows = _report_arrays_to_rows(data[:, columns], [bmtk_id], [0,1], mapping["time"][:], node_id_crosswalk,
                        integration_dt_ms=integration_dt_ms, expected_duration_ms=expected_duration_ms,
                        expected_sample_dt_ms=expected_sample_dt_ms, condition=condition, repetition=repetition)
                    for row in rows:
                        row["compartment_id"] = spec["source_node_id"]
                    result.extend(rows)
            return result
        return _report_arrays_to_rows(
            data,
            mapping["node_ids"][:],
            mapping["index_pointer"][:],
            mapping["time"][:],
            node_id_crosswalk,
            integration_dt_ms=integration_dt_ms,
            expected_duration_ms=expected_duration_ms,
            expected_sample_dt_ms=expected_sample_dt_ms,
            condition=condition,
            repetition=repetition,
        )


def _child_environment() -> dict[str, str]:
    environment = dict(os.environ)
    exact_remove = {
        "DISPLAY",
        "PYTHONHOME",
        "PYTHONPATH",
        "PYTHONEXECUTABLE",
        "PYTHONSTARTUP",
        "PYTHONINSPECT",
        "PYTHONUSERBASE",
        "PYTHONPLATLIBDIR",
        "__PYVENV_LAUNCHER__",
        "VIRTUAL_ENV",
        "_PYTHON_SYSCONFIGDATA_NAME",
        "LD_LIBRARY_PATH",
        "LD_PRELOAD",
    }
    prefix_remove = (
        "CONDA_",
        "DYLD_",
        "QT_",
        "QML_",
        "NEURON",
        "NRN",
        "CORENRN",
        "NMODL",
    )
    for key in tuple(environment):
        if key in exact_remove or key.startswith(prefix_remove):
            environment.pop(key, None)
    executable_dir = str(Path(sys.executable).absolute().parent)
    inherited_path = environment.get("PATH", "")
    path_entries = [executable_dir]
    path_entries.extend(
        entry
        for entry in inherited_path.split(os.pathsep)
        if entry and ".app/Contents/MacOS" not in entry
    )
    environment["PATH"] = os.pathsep.join(dict.fromkeys(path_entries))
    environment.update(
        {
            "NEURON_MODULE_OPTIONS": "-nogui",
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "MPLBACKEND": "Agg",
        }
    )
    return environment


def _signal_child_process(
    process: subprocess.Popen[Any], signum: int, *, force: bool = False
) -> None:
    """Forward cancellation to the isolated BioNet process and its descendants."""

    if process.poll() is not None:
        return
    forwarded_signal = signal.SIGKILL if force else int(signum)
    if os.name != "nt":
        try:
            os.killpg(process.pid, forwarded_signal)
            return
        except ProcessLookupError:
            return
        except OSError:
            # Fall back to the direct-child methods if process-group signalling
            # is unavailable on a particular host.
            pass
    if os.name == "nt" and force:
        completed = subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if completed.returncode != 0 and process.poll() is None:
            process.kill()
    elif os.name == "nt" and hasattr(signal, "CTRL_BREAK_EVENT"):
        try:
            process.send_signal(signal.CTRL_BREAK_EVENT)
        except (OSError, ValueError):
            process.terminate()
    elif force:
        process.kill()
    else:
        process.terminate()


def _run_child(
    config_path: Path,
    repetition_dir: Path,
    *,
    cancel_marker: Path | None = None,
    cancellation: _CancellationController | None = None,
) -> Path:
    if cancellation is None:
        marker = cancel_marker or repetition_dir / CANCEL_REQUEST_FILENAME
        with _CancellationController(marker) as owned_cancellation:
            owned_cancellation.raise_if_requested()
            return _run_child(
                config_path,
                repetition_dir,
                cancel_marker=marker,
                cancellation=owned_cancellation,
            )
    command = [
        str(Path(sys.executable).absolute()),
        "-B",
        str(Path(__file__).resolve()),
        "--execute-config",
        str(config_path.resolve()),
    ]
    log_path = repetition_dir / "worker_subprocess.log"
    popen_options: dict[str, Any] = {}
    if os.name == "nt":
        creation_flag = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        if creation_flag:
            popen_options["creationflags"] = creation_flag
    else:
        popen_options["start_new_session"] = True

    with log_path.open("w", encoding="utf-8") as log_handle:
        process = subprocess.Popen(
            command,
            cwd=str(repetition_dir),
            env=_child_environment(),
            text=True,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            **popen_options,
        )
        cancellation.attach_child(process)
        try:
            while True:
                cancellation.observe_marker()
                try:
                    returncode = process.wait(timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    cancellation.observe_marker()
                    if (
                        cancellation.requested
                        and cancellation.requested_at is not None
                        and time.monotonic()
                        >= cancellation.requested_at + CHILD_CANCEL_GRACE_SECONDS
                    ):
                        _signal_child_process(
                            process,
                            cancellation.signum or int(signal.SIGTERM),
                            force=True,
                        )
                        try:
                            returncode = process.wait(timeout=5.0)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            returncode = process.wait(timeout=5.0)
                        break
        finally:
            cancellation.detach_child(process)

    if cancellation.signum is not None:
        raise _WorkerCancelled(cancellation.signum)
    if returncode != 0:
        tail = "\n".join(
            log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-20:]
        )
        raise RuntimeError(
            f"BioNet child process exited with status {returncode}: {tail}"
        )
    return log_path


def _relative_artifact(
    output_dir: Path, path: Path, *, kind: str, label: str
) -> dict[str, str]:
    resolved = path.resolve()
    if not _inside(resolved, output_dir) or not resolved.is_file():
        raise ValueError(f"Cannot publish missing or external artifact: {resolved}")
    return {
        "kind": kind,
        "path": str(resolved.relative_to(output_dir)),
        "label": label,
    }


def _file_provenance(output_dir: Path, path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    if not _inside(resolved, output_dir) or not resolved.is_file():
        raise ValueError(f"Cannot record source details for {resolved}")
    return {
        "path": str(resolved.relative_to(output_dir)),
        "sha256": _common._sha256(resolved),
        "size_bytes": resolved.stat().st_size,
    }


def _run(
    request_path: Path, *, cancellation: _CancellationController
) -> Path:
    request_path = request_path.resolve()
    cancellation.raise_if_requested()
    request, output_dir, edge_manifest = _validated_request(request_path)
    cancellation.raise_if_requested()
    network_builder, _bionet, h5py, runtime = _require_bionet_runtime()
    cancellation.raise_if_requested()
    circuit = dict(request["circuit"])
    experiment = dict(request["experiment"])
    neuron_ids = tuple(str(value) for value in circuit["neuron_ids"])
    cancellation.raise_if_requested()

    cells, morphology_artifacts, morphology_metadata = _prepare_morphologies(
        request, output_dir, edge_manifest
    )
    if any(c.get("regional_data") for c in cells.values()):
        _common._regional_report_budget({n:c["section_plan"] for n,c in cells.items()}, experiment)
    native_evidence = _common._prepare_native_channels(request, output_dir)
    gap_evidence = {}
    if edge_manifest.get("electrical_edges"):
        gap_dir = Path(str(request.get("neuron_gap_mechanisms") or "")).resolve()
        manifest_sha = str(request.get("neuron_gap_manifest_sha256") or "")
        gap_manifest, gap_library = _common._validated_neuron_gap_manifest(
            gap_dir, neuron_version=runtime["packages"]["neuron"]["version"], expected_manifest_sha256=manifest_sha)
        gap_evidence = {"mechanism_dir":str(gap_dir), "manifest_sha256":manifest_sha,
                        "library_sha256":_common._sha256(gap_library),
                        "sources":gap_manifest["mechanisms"], "compiler":gap_manifest.get("compiler")}
    cancellation.raise_if_requested()
    resolved_edge_manifest = _resolve_edge_manifest(edge_manifest, cells)
    resolved_edge_path = output_dir / "resolved_edge_manifest.json"
    _common._atomic_json(resolved_edge_path, resolved_edge_manifest)
    synapse_models = _write_synapse_models(output_dir, resolved_edge_manifest)
    cancellation.raise_if_requested()

    manifest_path = output_dir / "run_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError("The run-owned run_manifest.json is missing")
    manifest = _load_object(manifest_path, label="run manifest")
    manifest.update(
        {
            "state": "running",
            "started_at": _common._now(),
            "lane": LANE_ID,
            "native_channel_execution": native_evidence,
            "gap_mechanism_execution": gap_evidence,
            "morphology_transform": "parent_before_child_reindex_source_swc_edge_odd_cv_v1" if any(c.get("regional_data") for c in cells.values()) else "parent_before_child_reindex_bionet_branch_runs_v1",
            "morphologies": morphology_metadata,
            "resolved_edge_manifest_path": str(resolved_edge_path),
            "resolved_edge_manifest_sha256": _common._sha256(resolved_edge_path),
        }
    )
    _common._atomic_json(manifest_path, manifest)

    chemical_contacts = list(resolved_edge_manifest.get("chemical_edges") or ())
    _common._emit(
        "start",
        "Starting BMTK/BioNet source-bound membrane experiment",
        neuron_ids=list(neuron_ids),
        chemical_contacts=len(chemical_contacts),
    )
    stimuli = [dict(item) for item in experiment["stimuli"] if item.get("enabled", True)]
    stimulus = stimuli[0]
    conditions = [dict(item) for item in experiment["conditions"] if item.get("enabled", True)]
    repetitions = int(experiment["repetitions"])
    node_crosswalk = {
        int(cell["bmtk_node_id"]): neuron_id for neuron_id, cell in cells.items()
    }
    recording = dict(experiment["recording"])
    spike_threshold = float(recording["spike_threshold_mV"])
    trace_path = output_dir / "voltage_traces.csv"
    spike_path = output_dir / "spikes.csv"
    plot_rows: list[dict[str, Any]] = []
    spike_plot_rows: list[dict[str, Any]] = []
    execution_records: list[dict[str, Any]] = []
    execution_artifacts: list[dict[str, str]] = []
    sample_counts: dict[tuple[str, int, str], int] = {}
    condition_execution = []
    previous_voltage: dict[tuple[str, int, str], float] = {}
    detected_spikes = 0
    total_trace_rows = 0
    expected_samples = max(
        2,
        int(math.ceil(float(experiment["duration_ms"]) / float(recording["sample_dt_ms"]))),
    )
    recording_count = len(recording.get("target_neuron_ids") or neuron_ids)
    projected_rows = len(conditions) * repetitions * expected_samples * recording_count
    plot_stride = max(1, int(math.ceil(projected_rows / 500_000)))

    prepared = _prepare_condition_trials(network_builder,output_dir,cells,resolved_edge_manifest,synapse_models,experiment,
        stimulus,conditions,native_evidence,gap_evidence,cancellation)
    parallel = int(experiment.get("workers",1)) > 1
    if parallel:
        manifest["worker_execution"] = _execute_condition_trials(prepared,conditions,request_path,experiment,cells,cancellation)
        _common._atomic_json(manifest_path,manifest)

    with trace_path.open("w", encoding="utf-8", newline="") as trace_handle, spike_path.open(
        "w", encoding="utf-8", newline=""
    ) as spike_handle:
        trace_writer = csv.DictWriter(
            trace_handle,
            fieldnames=("condition", "repetition", "neuron_id", "compartment_id", "time_ms", "voltage_mV"),
        )
        spike_writer = csv.DictWriter(
            spike_handle,
            fieldnames=("condition", "repetition", "neuron_id", "compartment_id", "spike_time_ms"),
        )
        trace_writer.writeheader()
        spike_writer.writeheader()
        completed_runs = 0
        total_runs = len(conditions) * repetitions
        for condition_index, condition in enumerate(conditions, start=1):
            cancellation.raise_if_requested()
            state=prepared[condition_index-1]
            condition_dir=state["directory"]
            condition_cells=state["cells"]
            condition_edges=state["edges"]
            condition_crosswalk=state["crosswalk"]
            condition_execution.append({"condition":str(condition["name"]), "active_neuron_ids":list(condition_cells),
                "disabled_neuron_ids":list(condition.get("disabled_neuron_ids") or ()),
                "mechanism_scales":dict(condition.get("mechanism_scales") or {}),
                "ablation_policy":"Remove cells, all incident chemical/electrical contacts, clamps and recordings; no fabricated voltage",
                "scale_policy":"Multiply conductances after default/neuron/source-edge precedence; kinetics/reversals/source hashes unchanged",
                "node_crosswalk":condition_crosswalk, "edge_manifest":condition_edges,
                "dynamics":{n:{"path":str(Path("sonata/components/biophysical_models")/c["dynamics_params"]),
                    "sha256":_common._sha256(output_dir/"sonata/components/biophysical_models"/c["dynamics_params"])} for n,c in condition_cells.items()}})
            if not condition_cells:
                execution_records.append({"condition":str(condition["name"]), "state":"empty_after_ablation", "active_neuron_ids":[]})
                continue
            chemical_enabled = bool(condition.get("chemical_synapses_enabled", True))
            cancellation.raise_if_requested()
            execution_artifacts.append(
                _relative_artifact(
                    output_dir,
                    condition_dir / "circuit_config.json",
                    kind="document",
                    label=f"BioNet circuit config · {condition['name']}",
                )
            )
            network_dir = condition_dir / "network"
            for filename, kind, label in (
                ("digifly_nodes.h5", "sonata", "SONATA nodes"),
                ("digifly_node_types.csv", "table", "SONATA node types"),
                ("digifly_edges.h5", "sonata", "SONATA chemical edges"),
                ("digifly_edge_types.csv", "table", "SONATA chemical edge types"),
            ):
                path = network_dir / filename
                if path.is_file():
                    execution_artifacts.append(
                        _relative_artifact(
                            output_dir,
                            path,
                            kind=kind,
                            label=f"{label} · {condition['name']}",
                        )
                    )
            for repetition in range(1, repetitions + 1):
                completed_runs += 1
                _common._emit(
                    "simulate",
                    f"Running {condition['name']} repetition {repetition}",
                    completed=completed_runs - 1,
                    total=total_runs,
                )
                trial=state["trials"][repetition-1]
                repetition_dir=trial["directory"]
                config_payload=trial["config"]
                config_path=trial["path"]
                extension_path=repetition_dir/"electrical_extension.json"
                child_log = repetition_dir/"worker_subprocess.log" if parallel else _run_child(
                    config_path,
                    repetition_dir,
                    cancellation=cancellation,
                )
                if condition_edges.get("electrical_edges"):
                    receipt = _load_object(repetition_dir / "electrical_execution.json", label="electrical execution")
                    contact_count = sum(len(e["contacts"]) for e in condition_edges["electrical_edges"])
                    enabled = bool(condition.get("gap_junctions_enabled", True))
                    expected_processes = contact_count * (1 if resolved_edge_manifest["gap_junction_policy"]["mode"] == "rectifying" else 2) if enabled else 0
                    if (receipt.get("extension_sha256") != _common._sha256(extension_path)
                            or receipt.get("point_processes") != expected_processes
                            or receipt.get("imported_contacts") != contact_count
                            or receipt.get("enabled") != enabled):
                        raise ValueError("BioNet did not materialize the exact electrical contact set")
                    for filename in ("electrical_extension.json", "electrical_execution.json"):
                        execution_artifacts.append(_relative_artifact(output_dir, repetition_dir/filename,
                            kind="document", label=f"Digifly electrical extension · {condition['name']} · {filename}"))
                cancellation.raise_if_requested()
                native_output = repetition_dir / "native_output"
                voltage_h5 = native_output / "soma_voltage.h5"
                native_spikes = native_output / "spikes.h5"
                if not native_spikes.is_file():
                    raise FileNotFoundError(
                        f"BioNet did not produce its native spike report: {native_spikes}"
                    )
                rows = _native_voltage_rows(
                    h5py,
                    voltage_h5,
                    condition_crosswalk,
                    integration_dt_ms=float(experiment["integration_dt_ms"]),
                    expected_duration_ms=float(experiment["duration_ms"]),
                    expected_sample_dt_ms=float(experiment["integration_dt_ms"]),
                    condition=str(condition["name"]),
                    repetition=repetition,
                    regional_cells=condition_cells if any(c.get("regional_data") for c in condition_cells.values()) else None,
                    recording_region=recording["target_region"],
                ) if config_payload["reports"] else ()
                for row in rows:
                    # Preserve the full native report. Export only measurements
                    # actually taken on the requested grid, never interpolate or
                    # rename post-fadvance samples as t=0.
                    grid_index = float(row["time_ms"])/float(recording["sample_dt_ms"])
                    if not math.isclose(grid_index, round(grid_index), abs_tol=1e-8):
                        continue
                    if total_trace_rows % 4096 == 0:
                        cancellation.raise_if_requested()
                    if recording.get("record_voltage", True):
                        trace_writer.writerow(row)
                    total_trace_rows += 1
                    key = (
                        str(row["condition"]),
                        int(row["repetition"]),
                        str(row["neuron_id"]),
                        str(row.get("compartment_id", "")),
                    )
                    count = sample_counts.get(key, 0)
                    sample_counts[key] = count + 1
                    if count % plot_stride == 0:
                        plot_rows.append(row)
                    voltage = float(row["voltage_mV"])
                    previous = previous_voltage.get(key)
                    if (
                        recording.get("detect_spikes", True)
                        and previous is not None
                        and previous < spike_threshold <= voltage
                    ):
                        spike_row = {
                                "condition": key[0],
                                "repetition": key[1],
                                "neuron_id": key[2],
                                "compartment_id": key[3],
                                "spike_time_ms": float(row["time_ms"]),
                            }
                        spike_writer.writerow(spike_row)
                        spike_plot_rows.append(spike_row)
                        detected_spikes += 1
                    previous_voltage[key] = voltage
                for path, kind, label in (
                    (config_path, "document", "BioNet simulation config"),
                    (child_log, "text", "BioNet child log"),
                    (voltage_h5, "sonata", "Native BioNet soma voltage"),
                    (native_spikes, "sonata", "Native BioNet spikes"),
                ):
                    if not path.is_file() and path == voltage_h5 and not config_payload["reports"]:
                        continue
                    execution_artifacts.append(
                        _relative_artifact(
                            output_dir,
                            path,
                            kind=kind,
                            label=f"{label} · {condition['name']} · rep {repetition}",
                        )
                    )
                if config_payload["digifly"].get("ramp_stimulus"):
                    ramp_path = repetition_dir / "ramp_execution.json"
                    ramp_receipt = _load_object(ramp_path, label="actual BioNet ramp execution")
                    if ramp_receipt["sites"] != config_payload["digifly"]["ramp_stimulus"]["sites"]:
                        raise ValueError("BioNet did not attach the exact declared ramp sites")
                    execution_artifacts.append(_relative_artifact(output_dir, ramp_path, kind="document",label=f"BioNet ramp execution · {condition['name']}"))
                execution_records.append(
                    {
                        "condition": str(condition["name"]),
                        "repetition": repetition,
                        "chemical_synapses_enabled": chemical_enabled,
                        "gap_junctions_enabled": bool(condition.get("gap_junctions_enabled", True)),
                        "electrical_extension": _file_provenance(output_dir, extension_path) if condition_edges.get("electrical_edges") else None,
                        "electrical_execution": _file_provenance(output_dir, repetition_dir/"electrical_execution.json") if condition_edges.get("electrical_edges") else None,
                        "simulation_config": _file_provenance(output_dir, config_path),
                        "native_soma_voltage": _file_provenance(output_dir, voltage_h5) if config_payload["reports"] else None,
                        "native_spikes": _file_provenance(output_dir, native_spikes),
                    }
                )

    cancellation.raise_if_requested()
    record_compartments = {
        neuron_id: [str(s["source_node_id"]) for s in _common._regional_sites(cells[neuron_id]["section_plan"], recording["target_region"], cells[neuron_id]["regional_data"])] if cells[neuron_id].get("regional_data") else [""]
        for neuron_id in (recording.get("target_neuron_ids") or neuron_ids)
    }
    expected_keys = {
        (str(condition["name"]), repetition, neuron_id, compartment_id)
        for condition in conditions
        for repetition in range(1, repetitions + 1)
        for neuron_id in (recording.get("target_neuron_ids") or neuron_ids)
        if neuron_id not in (condition.get("disabled_neuron_ids") or ())
        for compartment_id in record_compartments[neuron_id]
    }
    if set(sample_counts) != expected_keys or any(count < 2 for count in sample_counts.values()):
        raise RuntimeError("BioNet did not return a complete soma trace for every recording target")

    artifacts: list[dict[str, str]] = [
        {"kind": "table", "path": trace_path.name, "label": "Mapped membrane voltage traces"},
        {"kind": "table", "path": spike_path.name, "label": "Threshold-crossing spikes"},
        {"kind": "document", "path": "experiment.json", "label": "Experiment document"},
        {"kind": "document", "path": "circuit.json", "label": "Circuit document"},
        {"kind": "document", "path": "edge_manifest.json", "label": "Source edge manifest"},
        {
            "kind": "document",
            "path": resolved_edge_path.name,
            "label": "Resolved BioNet edge manifest",
        },
        {"kind": "document", "path": request_path.name, "label": "Resolved worker request"},
    ]
    artifacts.extend(morphology_artifacts)
    artifacts.extend(execution_artifacts)
    if parallel:
        artifacts.extend({"kind":"document","path":name,"label":label} for name,label in
            (("worker_resource_forecast.json","Requested/effective independent trial resources"),("independent_trial_execution.json","Independent child execution and cleanup receipt")))
    conditions_path = output_dir / "condition_execution.json"
    _common._atomic_json(conditions_path, {"schema_version":1, "conditions":condition_execution})
    artifacts.append({"kind":"document", "path":conditions_path.name, "label":"Condition ablation and materialized conductances"})
    voltage_enabled = bool(recording.get("record_voltage", True))
    if not voltage_enabled:
        artifacts = [a for a in artifacts if a["path"] != trace_path.name]
    if experiment["recording"].get("make_plots", True):
        plot_path = output_dir / ("voltage_comparison.png" if voltage_enabled else "spike_raster.png")
        (_common._write_plot if voltage_enabled else _common._write_spike_plot)(plot_path, plot_rows if voltage_enabled else spike_plot_rows, experiment)
        cancellation.raise_if_requested()
        artifacts.append(
            {"kind": "image", "path": plot_path.name, "label": "Voltage comparison" if voltage_enabled else "Spike raster"}
        )

    cancellation.raise_if_requested()
    provenance_payload = {
        "schema_version": 1,
        "lane": LANE_ID,
        "created_at": _common._now(),
        "runtime": runtime,
        "isolation": {
            "mode": "fresh_same_interpreter_process_per_condition_repetition",
            "pythonpath_removed": True,
            "display_removed": True,
        },
        "worker": _file_provenance(output_dir, Path(__file__))
        if _inside(Path(__file__).resolve(), output_dir)
        else {
            "path": str(Path(__file__).resolve()),
            "sha256": _common._sha256(Path(__file__).resolve()),
        },
        "request_sha256": _common._sha256(request_path),
        "edge_manifest_sha256": str(request["edge_manifest_sha256"]),
        "resolved_edge_manifest_sha256": _common._sha256(resolved_edge_path),
        "morphology_policy": {
            "normalization": "parent_before_child_reindex_bionet_branch_runs_v1",
            "source_swc_preserved": True,
            "source_diameter_rule": "exact 2 × positive SWC radius",
            "diameter_floor_um": None,
            "target_soma_proxy_length_um": TARGET_SOMA_PROXY_LENGTH_UM,
            "degenerate_stub_length_um": DEGENERATE_STUB_LENGTH_UM,
            "per_neuron": morphology_metadata,
        },
        "canonical_voltage_timing": {
            "native_sonata_mapping_preserved": True,
            "native_mapping_time_semantics": "configured report window",
            "sampling_hook": "BioSimulator post_fadvance",
            "first_sample_offset_ms": float(recording["sample_dt_ms"]),
            "export_policy": "Full integration-step native SONATA report retained; canonical CSV selects actual requested-grid samples. No interpolation or invented t=0.",
            "validated_native_mapping": {
                "start_ms": 0.0,
                "stop_ms": float(experiment["duration_ms"]),
                "sample_dt_ms": float(experiment["integration_dt_ms"]),
                "expected_samples_per_target": int(round(float(experiment["duration_ms"])/float(experiment["integration_dt_ms"]))),
            },
        },
        "executions": execution_records,
    }
    provenance_path = output_dir / "bmtk_bionet_provenance.json"
    _common._atomic_json(provenance_path, provenance_payload)
    artifacts.append(
        {"kind": "document", "path": provenance_path.name, "label": "BioNet source details"}
    )

    completed_at = _common._now()
    packages = dict(runtime["packages"])
    summary = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "workflow": "experiment_builder_v1",
        "engine": "bmtk",
        "status": "complete",
        "completed_at": completed_at,
        "title": str(experiment["name"]),
        "metadata": {
            "Neuron IDs": ", ".join(neuron_ids),
            "Neuron types": ", ".join(
                f"{neuron_id}: {cells[neuron_id]['neuron_type']}" for neuron_id in neuron_ids
            ),
            "Simulator": "BMTK BioNet",
            "BMTK version": packages["bmtk"]["version"],
            "NEURON version": packages["neuron"]["version"],
            "Conditions": len(conditions),
            "Repetitions": repetitions,
            "Requested independent trial workers":int(experiment.get("workers",1)),
            "Effective independent trial workers":manifest.get("worker_execution",{}).get("effective_workers",1),
            "Duration (ms)": float(experiment["duration_ms"]),
            "Integration step (ms)": float(experiment["integration_dt_ms"]),
            "Recorded samples": total_trace_rows if voltage_enabled else 0,
            "Samples used for spike detection": total_trace_rows,
            "Detected spikes": detected_spikes,
            "Morphology transform": "parent-before-child normalized maximal branch runs",
            "Simulation diameter floor (um)": "None; exact positive SWC diameters",
            "Target soma proxy length (um)": TARGET_SOMA_PROXY_LENGTH_UM,
            "Chemical contacts": len(chemical_contacts),
            "Electrical contacts": sum(len(e["contacts"]) for e in resolved_edge_manifest.get("electrical_edges") or ()),
            "Electrical portability": "Digifly serial extension; not standard SONATA" if gap_evidence else "No electrical contacts",
            "Native output": "SONATA HDF5 preserved per condition/repetition",
            "Edge manifest SHA-256": str(request["edge_manifest_sha256"]),
        },
        "artifacts": artifacts,
    }
    summary_path = output_dir / "summary.json"
    _common._atomic_json(summary_path, summary)
    cancellation.raise_if_requested()
    manifest.update(
        {
            "state": "completed",
            "completed_at": completed_at,
            "summary_path": str(summary_path),
            "provenance_path": str(provenance_path),
        }
    )
    _common._atomic_json(manifest_path, manifest)
    _common._emit("complete", "BMTK/BioNet experiment completed", summary_path=str(summary_path))
    return summary_path


def _prepare_condition_trials(network_builder: Any, output_dir: Path, cells: Mapping[str,Any], resolved_edges: Mapping[str,Any],
                              synapse_models: Mapping[str,Any], experiment: Mapping[str,Any], stimulus: Mapping[str,Any],
                              conditions: Sequence[Mapping[str,Any]], native: Mapping[str,Any], gap: Mapping[str,Any],
                              cancellation: _CancellationController) -> list[dict[str,Any]]:
    """Serial materialization before any independent child starts."""
    prepared=[]
    for index,condition in enumerate(conditions,start=1):
        cancellation.raise_if_requested()
        key=f"condition-{index:03d}-{_safe_component(str(condition['name']),prefix='condition')}"
        directory=output_dir/"sonata"/"conditions"/key
        directory.mkdir(parents=True,exist_ok=False)
        condition_cells=_condition_bionet_cells(cells,condition,output_dir,key)
        edges=_resolve_edge_manifest(_common._condition_edges(resolved_edges,tuple(condition_cells)),condition_cells)
        crosswalk={int(c["bmtk_node_id"]):n for n,c in condition_cells.items()}
        state={"directory":directory,"cells":condition_cells,"edges":edges,"crosswalk":crosswalk,"trials":[]}
        prepared.append(state)
        if not condition_cells:
            continue
        circuit_config=_materialize_sonata_network(network_builder,output_dir,directory,condition_cells,edges,synapse_models,
            chemical_enabled=bool(condition.get("chemical_synapses_enabled",True)))
        for repetition in range(1,int(experiment["repetitions"])+1):
            repdir=directory/f"repetition-{repetition:03d}"
            repdir.mkdir()
            config=_simulation_config(output_dir=output_dir,repetition_dir=repdir,circuit_config=circuit_config,cells=condition_cells,
                experiment=experiment,stimulus=stimulus,condition=condition,repetition=repetition,
                chemical_spike_threshold_mV=float(resolved_edges["chemical_synapse_policy"]["spike_threshold_mV"]))
            config["digifly"]["native_channel_execution"]=native
            config["digifly"]["gap_mechanism_execution"]=gap
            if edges.get("electrical_edges"):
                extension=repdir/"electrical_extension.json"
                _common._atomic_json(extension,{"schema_version":1,"enabled":bool(condition.get("gap_junctions_enabled",True)),
                    "policy":edges["gap_junction_policy"],"edges":edges["electrical_edges"],"node_ids":sorted(crosswalk),
                    "execution":"Digifly serial extension on framework-owned cells; not standard SONATA electrical portability"})
                config["digifly"]["electrical_extension"]={"path":str(extension.resolve()),"sha256":_common._sha256(extension)}
            config_path=repdir/"simulation_config.json"
            _common._atomic_json(config_path,config)
            state["trials"].append({"directory":repdir,"path":config_path,"config":config})
    return prepared


def _execute_condition_trials(prepared: Sequence[Mapping[str,Any]], conditions: Sequence[Mapping[str,Any]],
                              request_path: Path, experiment: Mapping[str,Any], cells: Mapping[str,Any],
                              cancellation: _CancellationController) -> dict[str,Any]:
    tasks=[]
    # Includes every run-owned immutable morphology, dynamic model, network and
    # extension file. Reports and receipts are created only after this snapshot.
    static=[p for p in (request_path.parent/"sonata").rglob("*") if p.is_file() and p.name!="simulation_config.json"]
    static.extend([request_path,Path(__file__),Path(_common.__file__),Path(_common._trials.__file__)])
    request=_load_object(request_path,label="independent trial parent request")
    static.extend(Path(record["path"]) for record in request.get("morphologies",{}).values())
    static.extend([request_path.parent/"edge_manifest.json",request_path.parent/"resolved_edge_manifest.json"])
    source_edges=_common._load_edge_manifest(request,request_path.parent)
    _common._validate_edge_source_identity(source_edges)
    source_record={"path":request["edge_manifest_path"],"sha256":request["edge_manifest_sha256"]}
    native=next((trial["config"]["digifly"].get("native_channel_execution") for state in prepared for trial in state["trials"] if trial["config"]["digifly"].get("native_channel_execution")),{})
    if native:
        static.extend([Path(native["library"]),Path(native["builder"]["path"]),*[Path(record["path"]) for record in native["sources"].values()]])
    pins=_common._trial_input_pins(static)
    for state,condition in zip(prepared,conditions):
        for rep,trial in enumerate(state["trials"],start=1):
            path=trial["path"]
            config=trial["config"]
            config["digifly"]["independent_trial_input_pins"]=pins
            config["digifly"]["independent_source_edge_manifest"]=source_record
            config["digifly"]["independent_mapped_edge_manifest"]=state["edges"]
            config["digifly"]["independent_active_neuron_ids"]=list(state["cells"])
            config["digifly"]["independent_mapped_contact_sha256"]=_common._contact_manifest_sha256(state["edges"])
            _validate_independent_trial_inputs(config,hash_bound=True)
            _common._atomic_json(path,config)
            digest=_common._sha256(path)
            tasks.append({"label":f"{condition['name']} repetition {rep}","seed":int(experiment.get("random_seed",1))+rep-1,
                "cwd":str(trial["directory"]),"log_path":str(trial["directory"]/"worker_subprocess.log"),
                "pins":[*pins,{"path":str(path),"sha256":digest}],
                "command":[str(Path(sys.executable).absolute()),"-B",str(Path(__file__).resolve()),"--execute-config",str(path),"--config-sha256",digest]})
    segments=sum(int(c["section_plan"]["estimated_nseg"]) for c in cells.values())
    values=max(2,int(math.ceil(float(experiment["duration_ms"])/float(experiment["integration_dt_ms"]))))*max(1,segments)
    forecast=_common._trials.resource_forecast(int(experiment.get("workers",1)),len(tasks),engine="bmtk",segments=segments,report_values=values)
    _common._atomic_json(request_path.parent/"worker_resource_forecast.json",forecast)
    environment=_child_environment()
    environment.update(OMP_NUM_THREADS="1",OPENBLAS_NUM_THREADS="1",MKL_NUM_THREADS="1")
    _common._trials.execute(tasks,forecast["effective_workers"],environment=environment,
        receipt_path=request_path.parent/"independent_trial_execution.json",cancellation=cancellation.raise_if_requested)
    return forecast


def run(request_path: Path) -> Path:
    """Execute one request under a process-lifetime cancellation scope."""

    resolved_request = request_path.expanduser().resolve()
    cancellation = _CancellationController(
        resolved_request.parent / CANCEL_REQUEST_FILENAME
    )
    with cancellation:
        cancellation.raise_if_requested()
        summary_path = _run(resolved_request, cancellation=cancellation)
        cancellation.raise_if_requested()
        return summary_path


def _mark_failed(request_path: Path, exc: Exception) -> None:
    output_dir = request_path.resolve().parent
    manifest_path = output_dir / "run_manifest.json"
    if not manifest_path.is_file():
        return
    try:
        manifest = _load_object(manifest_path, label="run manifest")
        cancelled = isinstance(exc, _WorkerCancelled)
        timestamp_key = "cancelled_at" if cancelled else "failed_at"
        if cancelled:
            summary_path = output_dir / "summary.json"
            if summary_path.is_file():
                partial_summary_path = output_dir / "summary.cancelled.json"
                summary_path.replace(partial_summary_path)
                manifest["partial_summary_path"] = str(partial_summary_path)
                manifest.pop("summary_path", None)
        child_logs = sorted(
            str(path.relative_to(output_dir))
            for path in output_dir.rglob("worker_subprocess.log")
            if path.is_file()
        )
        manifest.update(
            {
                "state": "cancelled" if cancelled else "failed",
                timestamp_key: _common._now(),
                "error": str(exc),
                "error_type": type(exc).__name__,
            }
        )
        if child_logs:
            manifest["preserved_child_logs"] = child_logs
        _common._atomic_json(manifest_path, manifest)
    except Exception:
        pass


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--request")
    modes.add_argument("--execute-config")
    parser.add_argument("--config-sha256",default="")
    args = parser.parse_args(argv)
    try:
        if args.execute_config:
            execute_config(Path(args.execute_config).expanduser().resolve(),args.config_sha256)
        else:
            run(Path(args.request).expanduser().resolve())
    except Exception as exc:
        if args.request:
            _mark_failed(Path(args.request), exc)
        stage = "cancelled" if isinstance(exc, _WorkerCancelled) else "failed"
        _common._emit(stage, str(exc), error_type=type(exc).__name__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
