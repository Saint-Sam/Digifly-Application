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
import re
import sqlite3
import sys
import tempfile
from typing import Any, Mapping, Sequence


REQUEST_SCHEMA_VERSION = 1
RESULT_SCHEMA_VERSION = 1
EDGE_MANIFEST_SCHEMA_VERSION = 2
LEGACY_EDGE_MANIFEST_SCHEMA_VERSION = 1
ARBOR_ROOT_STUB_MIN_UM = 0.001
ARBOR_ROOT_STUB_RADIUS_FRACTION = 0.1
NEURON_TARGET_SEGMENT_UM = 40.0
NEURON_MAX_TOTAL_SEGMENTS = 2_000_000
NEURON_GAP_SOURCE_HASHES = {
    "Gap": "e3ab9d0a37811314d3baa8461050e6d65fbb9b8e163ff6618a6d9f2f2549c1f2",
    "RectGap": "d9fa308ff0433ad0eb424017fff4384d5c06915ca9a2cbe25485ad176db3a8d6",
    "HeteroRectGap": "22b505af799076bb8079f204d222adfa72432f8609bd89fd050f7ffb325c9c2f",
}
_LOADED_NEURON_MECHANISM_LIBRARIES: set[str] = set()
_SHA256_RE = re.compile(r"[0-9a-fA-F]{64}\Z")


def _required_sha256(value: Any, *, label: str) -> str:
    """Return a canonical digest or reject an unfrozen input identity."""

    digest = str(value or "").strip()
    if _SHA256_RE.fullmatch(digest) is None:
        raise ValueError(f"{label} SHA-256 is missing or invalid")
    return digest.lower()


def _neuron_nseg_for_length(length_um: float) -> int:
    """Apply Digifly's uncapped ~40-um odd-segment discretization rule."""

    length = float(length_um)
    if not math.isfinite(length) or length < 0.0:
        raise RuntimeError(f"NEURON section length is invalid: {length_um!r}")
    target = max(1, int(math.ceil(length / NEURON_TARGET_SEGMENT_UM)))
    return target if target % 2 else target + 1


def _require_neuron_segment_budget(
    current_segments: int,
    additional_segments: int,
    *,
    segment_budget: int,
) -> None:
    """Reject a discretization before it can exceed the explicit run budget."""

    if (
        current_segments < 0
        or additional_segments < 0
        or segment_budget < 0
        or current_segments + additional_segments > segment_budget
    ):
        raise RuntimeError(
            "The selected NEURON circuit exceeds the explicit "
            f"{NEURON_MAX_TOTAL_SEGMENTS:,}-segment safety limit at "
            f"{NEURON_TARGET_SEGMENT_UM:g} um target spacing; reduce the selected circuit."
        )


def _neuron_discretization_plan(
    section_lengths_um: Sequence[float],
    *,
    segment_budget: int = NEURON_MAX_TOTAL_SEGMENTS,
) -> tuple[int, ...]:
    """Plan all section segment counts and fail before mutating any section."""

    plan = tuple(_neuron_nseg_for_length(length) for length in section_lengths_um)
    _require_neuron_segment_budget(0, sum(plan), segment_budget=segment_budget)
    return plan


def _apply_neuron_discretization(
    sections: Sequence[Any],
    *,
    segment_budget: int = NEURON_MAX_TOTAL_SEGMENTS,
) -> int:
    """Apply one fully budget-checked plan to imported NEURON sections."""

    plan = _neuron_discretization_plan(
        tuple(float(section.L) for section in sections),
        segment_budget=segment_budget,
    )
    for section, target_nseg in zip(sections, plan):
        section.nseg = target_nseg
    return sum(plan)


def _safe_neuron_path_component(neuron_id: str) -> str:
    """Return a bounded, deterministic directory name for a biological ID.

    Biological IDs remain unchanged in documents and result tables. Only the
    run-owned filesystem component is encoded here, so an ID containing an
    absolute path, ``..``, or a path separator can never select a destination.
    """

    raw = str(neuron_id)
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", raw).strip("-_")[:32]
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"cell-{slug or 'neuron'}-{digest}"


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
    *,
    expected_source_sha256: str | None = None,
    source_label: str = "Source SWC",
) -> tuple[Path, Path, str]:
    """Write an isomorphic parent-before-child SWC plus an ID provenance map.

    When an expected digest is supplied, it is checked against the same byte
    stream that is parsed. The resulting normalized file is therefore a frozen
    snapshot of the preflight-approved morphology rather than a second,
    potentially changed read of the source path.
    """

    nodes: dict[int, tuple[int, float, float, float, float, int]] = {}
    source_digest = hashlib.sha256()
    with source.open("rb") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            source_digest.update(raw_line)
            try:
                line = raw_line.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError(
                    f"SWC is not valid UTF-8 at {source}:{line_number}"
                ) from exc
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
            if not all(math.isfinite(value) for value in values[1:5]):
                raise ValueError(f"Invalid SWC geometry at {source}:{line_number}")
            if values[4] <= 0.0:
                raise ValueError(
                    f"Non-positive SWC radius at {source}:{line_number}; "
                    "repair the source morphology before simulation"
                )
            nodes[node_id] = values
    actual_source_sha256 = source_digest.hexdigest()
    if expected_source_sha256 is not None:
        expected_digest = _required_sha256(
            expected_source_sha256,
            label=source_label,
        )
        if actual_source_sha256 != expected_digest:
            raise ValueError(
                f"{source_label} checksum changed: expected {expected_digest}, "
                f"found {actual_source_sha256}"
            )
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
            f"# source_sha256 {actual_source_sha256}\n"
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


def _arbor_morphology_and_segments_from_swc(
    arbor: Any,
    path: Path,
    *,
    prefer_rostral_soma: bool,
) -> tuple[Any, dict[int, int]]:
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
    return arbor.morphology(tree), segment_by_node


def _arbor_morphology_from_swc(
    arbor: Any,
    path: Path,
    *,
    prefer_rostral_soma: bool,
) -> Any:
    morphology, _segments = _arbor_morphology_and_segments_from_swc(
        arbor,
        path,
        prefer_rostral_soma=prefer_rostral_soma,
    )
    return morphology


def _arbor_network_morphology_from_swc(
    arbor: Any,
    path: Path,
    *,
    prefer_rostral_soma: bool,
    contact_node_ids: set[int],
) -> tuple[Any, dict[int, str], dict[str, str]]:
    """Build a cable tree with unambiguous locsets for imported contacts.

    SWC node/segment indices are not Arbor branch indices. Contact-bearing
    segments therefore receive collision-free temporary morphology tags, and
    each junction is placed at the distal end of its exact tagged segment.
    """

    rows = _swc_rows(path)
    target_node_id, _point = _target_soma_node(
        rows,
        prefer_rostral=prefer_rostral_soma,
    )
    root = next(row for row in rows if row[6] == -1)
    root_id, _root_type, root_x, root_y, root_z, root_radius, _parent = root
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
    tree = arbor.segment_tree()
    segment_by_node: dict[int, int] = {}
    contact_locset_by_node: dict[int, str] = {}
    soma_tags = {1, 1001}
    target_tag = 1001
    for node_id, swc_type, x, y, z, radius, parent_id in rows:
        point = arbor.mpoint(x, y, z, radius)
        tag = 1001 if node_id == target_node_id else swc_type
        if node_id in contact_node_ids:
            tag = 1_000_000 + node_id
            contact_locset_by_node[node_id] = f"(distal (tag {tag}))"
            if swc_type == 1:
                soma_tags.add(tag)
        if node_id == target_node_id:
            target_tag = tag
        if parent_id == -1:
            segment_id = tree.append(
                arbor.mnpos,
                root_proximal,
                point,
                tag,
            )
        else:
            if parent_id not in segment_by_node:
                raise ValueError(
                    f"Normalized SWC node {node_id} precedes parent {parent_id}"
                )
            segment_id = tree.append(segment_by_node[parent_id], point, tag)
        segment_by_node[node_id] = segment_id

    soma_terms = [f"(tag {tag})" for tag in sorted(soma_tags)]
    soma_region = soma_terms[0]
    for term in soma_terms[1:]:
        soma_region = f"(join {soma_region} {term})"
    labels = {
        "all": "(all)",
        "soma": soma_region,
        "branches": f"(difference (all) {soma_region})",
        "soma-center": f"(on-components 0.5 (tag {target_tag}))",
    }
    return arbor.morphology(tree), contact_locset_by_node, labels


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
    edge_manifest: Mapping[str, Any] | None = None,
) -> None:
    """Defend the fail-closed execution boundary inside the worker process."""

    if engine not in {"arbor", "neuron"}:
        raise ValueError(f"Unsupported generic experiment engine: {engine}")
    neuron_ids = tuple(str(value) for value in circuit.get("neuron_ids") or ())
    if not neuron_ids:
        raise ValueError("The generic worker requires at least one neuron")
    if len(set(neuron_ids)) != len(neuron_ids):
        raise ValueError("The generic worker received duplicate neuron IDs")
    if len(neuron_ids) > 1:
        manifest = dict(edge_manifest or {})
        if int(manifest.get("schema_version", 0)) != EDGE_MANIFEST_SCHEMA_VERSION:
            raise ValueError("The selected-subgraph edge manifest schema is missing or unsupported")
        if manifest.get("kind") != "explicit_selected_subgraph":
            raise ValueError("The multi-cell worker requires an explicit selected-subgraph manifest")
        if tuple(str(value) for value in manifest.get("neuron_ids") or ()) != neuron_ids:
            raise ValueError("The edge manifest neuron order does not match the circuit")
        selected = set(neuron_ids)
        contact_ids: set[str] = set()
        for raw in manifest.get("chemical_edges") or ():
            edge = dict(raw or {})
            contact_id = str(edge.get("contact_id") or "")
            if not contact_id or contact_id in contact_ids:
                raise ValueError("Chemical contact IDs must be present and unique")
            contact_ids.add(contact_id)
            if {str(edge.get("pre_id")), str(edge.get("post_id"))} - selected:
                raise ValueError("A chemical contact endpoint is outside the selected circuit")
            values = {
                "weight": float(edge.get("weight_uS", -1.0)),
                "delay": float(edge.get("delay_ms", -1.0)),
                "tau1": float(edge.get("tau1_ms", -1.0)),
                "tau2": float(edge.get("tau2_ms", -1.0)),
                "reversal": float(edge.get("reversal_mV", math.nan)),
            }
            if not all(math.isfinite(value) for value in values.values()):
                raise ValueError(f"Chemical contact {contact_id} has non-finite parameters")
            if values["weight"] < 0.0 or values["delay"] <= 0.0:
                raise ValueError(f"Chemical contact {contact_id} has invalid weight or delay")
            if values["tau1"] <= 0.0 or values["tau2"] <= values["tau1"]:
                raise ValueError(f"Chemical contact {contact_id} has invalid exp2syn kinetics")
            if int(edge.get("post_source_node_id", 0)) <= 0:
                raise ValueError(f"Chemical contact {contact_id} has no postsynaptic SWC node")
        for raw in manifest.get("electrical_edges") or ():
            edge = dict(raw or {})
            neuron_a = str(edge.get("neuron_a") or "")
            neuron_b = str(edge.get("neuron_b") or "")
            if neuron_a == neuron_b or {neuron_a, neuron_b} - selected:
                raise ValueError("An electrical pair endpoint is outside the selected circuit")
            contacts = list(edge.get("contacts") or ())
            if not contacts or int(edge.get("contact_count", -1)) != len(contacts):
                raise ValueError("An electrical pair has no complete contact manifest")
            for raw_contact in contacts:
                contact = dict(raw_contact or {})
                contact_id = str(contact.get("contact_id") or "")
                if not contact_id or contact_id in contact_ids:
                    raise ValueError("Electrical contact IDs must be present and unique")
                contact_ids.add(contact_id)
                pre_id = str(contact.get("pre_id") or "")
                post_id = str(contact.get("post_id") or "")
                if pre_id == post_id or {pre_id, post_id} != {neuron_a, neuron_b}:
                    raise ValueError(
                        f"Electrical contact {contact_id} does not match its outer pair"
                    )
                if int(contact.get("pre_source_node_id", 0)) <= 0 or int(
                    contact.get("post_source_node_id", 0)
                ) <= 0:
                    raise ValueError(
                        f"Electrical contact {contact_id} has no complete SWC endpoint mapping"
                    )
                conductance = float(contact.get("effective_g_uS", math.nan))
                if not math.isfinite(conductance) or conductance < 0.0:
                    raise ValueError(
                        f"Electrical contact {contact_id} has invalid conductance"
                    )
                coordinate_sources = dict(contact.get("coordinate_sources") or {})
                for endpoint in ("pre", "post"):
                    coordinates = list(
                        contact.get(f"{endpoint}_coordinate_um") or ()
                    )
                    if len(coordinates) != 3 or not all(
                        math.isfinite(float(value)) for value in coordinates
                    ):
                        raise ValueError(
                            f"Electrical contact {contact_id} has no finite {endpoint} coordinate"
                        )
                    if coordinate_sources.get(endpoint) not in {
                        f"{endpoint}_xyz",
                        f"{'post' if endpoint == 'pre' else 'pre'}_xyz_fallback",
                    }:
                        raise ValueError(
                            f"Electrical contact {contact_id} has invalid {endpoint} coordinate provenance"
                        )
        if manifest.get("electrical_edges"):
            gap_policy = dict(manifest.get("gap_junction_policy") or {})
            if gap_policy.get("mode") not in {
                "ohmic",
                "rectifying",
                "heterotypic_rectifying",
            }:
                raise ValueError("Electrical contacts have no supported gap-junction policy")

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


def _load_edge_manifest(request: Mapping[str, Any], output_dir: Path) -> dict[str, Any]:
    path = Path(str(request.get("edge_manifest_path") or "")).expanduser().resolve()
    if path.parent != output_dir or path.name != "edge_manifest.json" or not path.is_file():
        raise ValueError("The run-owned edge manifest path is missing or outside the run")
    expected = str(request.get("edge_manifest_sha256") or "")
    actual = _sha256(path)
    if len(expected) != 64 or actual != expected:
        raise ValueError("The run-owned edge manifest checksum does not match the request")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("The run-owned edge manifest must contain a JSON object")
    return payload


def _validate_edge_source_identity(edge_manifest: Mapping[str, Any]) -> None:
    """Reject a connected run if its validated contact source changed after preflight."""

    if edge_manifest.get("kind") == "explicit_two_cell_gap_only":
        source = dict(edge_manifest.get("source") or {})
        for path_key, digest_key, label in (
            ("path", "sha256", "gap-contact table"),
            ("manifest_path", "manifest_sha256", "gap-contact source manifest"),
        ):
            path = Path(str(source.get(path_key) or "")).expanduser().resolve()
            expected = str(source.get(digest_key) or "")
            if not path.is_file():
                raise FileNotFoundError(f"The validated {label} is missing: {path}")
            if len(expected) != 64 or _sha256(path) != expected:
                raise ValueError(f"The validated {label} changed after preflight")
        return
    if edge_manifest.get("kind") != "explicit_selected_subgraph":
        return
    for raw_source in edge_manifest.get("sources") or ():
        source = dict(raw_source or {})
        kind = str(source.get("kind") or "")
        path = Path(str(source.get("path") or "")).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"A selected edge source is missing: {path}")
        if kind == "sha256_files":
            if _sha256(path) != str(source.get("sha256") or ""):
                raise ValueError("A selected edge source changed after preflight")
            manifest_path = Path(str(source.get("manifest_path") or "")).expanduser().resolve()
            if not manifest_path.is_file() or _sha256(manifest_path) != str(
                source.get("manifest_sha256") or ""
            ):
                raise ValueError("An edge-source provenance manifest changed after preflight")
        elif kind == "immutable_sqlite":
            stat = path.stat()
            if int(source.get("size_bytes", -1)) != stat.st_size or int(
                source.get("mtime_ns", -1)
            ) != stat.st_mtime_ns:
                raise ValueError("The indexed chemical-edge cache changed after preflight")
            connection = sqlite3.connect(
                f"{path.as_uri()}?mode=ro&immutable=1", uri=True
            )
            try:
                schema_row = connection.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name='edges'"
                ).fetchone()
                meta_rows = connection.execute("SELECT k, v FROM meta ORDER BY k").fetchall()
            finally:
                connection.close()
            identity = {
                "schema": str(schema_row[0]) if schema_row else "",
                "meta": [[str(key), str(value)] for key, value in meta_rows],
                "size_bytes": int(stat.st_size),
                "mtime_ns": int(stat.st_mtime_ns),
            }
            actual = hashlib.sha256(
                json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            if actual != str(source.get("identity_sha256") or ""):
                raise ValueError("The indexed chemical-edge cache identity changed after preflight")
        elif kind == "immutable_parquet":
            stat = path.stat()
            identity = {
                "schema_contract": str(source.get("schema_contract") or ""),
                "size_bytes": int(stat.st_size),
                "mtime_ns": int(stat.st_mtime_ns),
            }
            actual = hashlib.sha256(
                json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            if (
                identity["schema_contract"]
                != "male-cns-v0.9-synapses-parquet-v1"
                or actual != str(source.get("identity_sha256") or "")
            ):
                raise ValueError("The Male-CNS chemical source identity changed after preflight")
        else:
            raise ValueError(f"Unsupported edge-source identity kind: {kind}")

    selected_identity = hashlib.sha256(
        json.dumps(
            {
                "chemical": list(edge_manifest.get("chemical_edges") or ()),
                "electrical": list(edge_manifest.get("electrical_edges") or ()),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if selected_identity != str(edge_manifest.get("selected_contact_identity_sha256") or ""):
        raise ValueError("The selected contact set identity is invalid")


def _source_to_run_node_map(path: Path) -> dict[int, int]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return {
            int(row["source_node_id"]): int(row["run_node_id"])
            for row in csv.DictReader(handle)
        }


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


def _arbor_gap_junction(
    arbor: Any,
    policy: Mapping[str, Any],
    conductance_uS: float,
    *,
    endpoint: str,
) -> Any:
    mode = str(policy.get("mode") or "")
    direction = str(policy.get("preferred_direction") or "a_to_b")
    forward = direction == "a_to_b"
    is_source = (endpoint == "a" and forward) or (endpoint == "b" and not forward)
    if mode == "ohmic":
        return arbor.junction("digifly_gap", {"g": float(conductance_uS)})
    if mode == "rectifying":
        if is_source:
            return arbor.junction("digifly_gap", {"g": 0.0})
        return arbor.junction("digifly_rect_gap", {"gmax": float(conductance_uS)})
    if mode == "heterotypic_rectifying":
        orientation = 1.0 if is_source else -1.0
        return arbor.junction(
            "digifly_hetero_rect_gap",
            {
                "gmax_open": float(conductance_uS),
                "gmax_closed": float(conductance_uS)
                * float(policy.get("g_closed_frac", 0.0)),
                "orientation": orientation,
                "vhalf": float(policy.get("vhalf_mV", 0.0)),
                "vslope": float(policy.get("vslope_mV", 5.0)),
                "empirical_residual_frac": float(
                    policy.get("empirical_residual_frac", 0.2)
                ),
                "tau_open_ms": float(policy.get("tau_open_ms", 6.0)),
                "tau_close_ms": float(policy.get("tau_close_ms", 2.0)),
            },
        )
    raise ValueError(f"Unsupported Digifly gap-junction mode: {mode}")


def _arbor_network_traces(
    cells: Mapping[str, Mapping[str, Any]],
    experiment: Mapping[str, Any],
    stimulus: Mapping[str, Any],
    condition: Mapping[str, Any],
    edge_manifest: Mapping[str, Any],
    catalogue_path: Path | None,
) -> tuple[dict[str, tuple[list[float], list[float]]], str]:
    import arbor

    neuron_ids = tuple(cells)
    gid_by_id = {neuron_id: gid for gid, neuron_id in enumerate(neuron_ids)}
    target_ids = {
        str(value) for value in stimulus.get("target_neuron_ids") or neuron_ids
    }
    recording = dict(experiment.get("recording") or {})
    record_ids = {
        str(value) for value in recording.get("target_neuron_ids") or neuron_ids
    }
    gap_enabled = bool(condition.get("gap_junctions_enabled", True))
    chemical_enabled = bool(condition.get("chemical_synapses_enabled", True))
    policy = dict(edge_manifest.get("gap_junction_policy") or {})
    electrical_edges = [
        dict(item) for item in edge_manifest.get("electrical_edges") or ()
    ]
    chemical_edges = [dict(item) for item in edge_manifest.get("chemical_edges") or ()]
    has_electrical = bool(electrical_edges)
    properties = arbor.neuron_cable_properties()
    if has_electrical:
        if catalogue_path is None:
            raise FileNotFoundError("The app-owned Arbor gap catalogue path is missing")
        catalogue_manifest_path = catalogue_path.with_name("catalogue_manifest.json")
        if not catalogue_manifest_path.is_file():
            raise FileNotFoundError(
                f"The Arbor gap catalogue provenance manifest is missing: {catalogue_manifest_path}"
            )
        catalogue_manifest = json.loads(
            catalogue_manifest_path.read_text(encoding="utf-8")
        )
        if (
            catalogue_manifest.get("catalogue") != "digifly_gap"
            or catalogue_manifest.get("arbor_version")
            != str(getattr(arbor, "__version__", "unknown"))
            or catalogue_manifest.get("catalogue_sha256") != _sha256(catalogue_path)
        ):
            raise ValueError(
                "The Arbor gap catalogue does not match its ABI provenance manifest"
            )
        properties.catalogue.extend(
            arbor.load_catalogue(str(catalogue_path)), "digifly_"
        )

    built_cells: list[Any] = []
    contact_labels: list[dict[str, Any]] = []
    chemical_connections: list[dict[str, Any]] = []
    units = arbor.units
    contact_nodes_by_id = {neuron_id: set() for neuron_id in neuron_ids}
    for edge in electrical_edges:
        for raw_contact in edge.get("contacts") or ():
            contact = dict(raw_contact)
            contact_nodes_by_id[str(contact["pre_id"])].add(
                int(contact["pre_run_node_id"])
            )
            contact_nodes_by_id[str(contact["post_id"])].add(
                int(contact["post_run_node_id"])
            )
    for contact in chemical_edges:
        contact_nodes_by_id[str(contact["post_id"])].add(
            int(contact["post_run_node_id"])
        )
    for neuron_id in neuron_ids:
        cell_data = dict(cells[neuron_id])
        swc_path = Path(str(cell_data["swc_path"]))
        hh = dict(cell_data["hh"])
        morphology, contact_locset_by_node, label_values = (
            _arbor_network_morphology_from_swc(
                arbor,
                swc_path,
                prefer_rostral_soma=bool(cell_data["prefer_rostral_soma"]),
                contact_node_ids=contact_nodes_by_id[neuron_id],
            )
        )
        labels = arbor.label_dict(label_values)
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
        if neuron_id in target_ids:
            amplitude = float(stimulus["amplitude_nA"]) * float(
                condition.get("stimulus_scale", 1.0)
            )
            for delay, duration in _pulse_windows(stimulus):
                decor.place(
                    '"soma-center"',
                    arbor.i_clamp(
                        delay * units.ms,
                        duration * units.ms,
                        amplitude * units.nA,
                    ),
                )
        if chemical_enabled and chemical_edges:
            chemical_policy = dict(
                edge_manifest.get("chemical_synapse_policy") or {}
            )
            decor.place(
                '"soma-center"',
                arbor.threshold_detector(
                    float(chemical_policy.get("spike_threshold_mV", 0.0))
                    * units.mV
                ),
                "chemical-source",
            )
            for contact in chemical_edges:
                if str(contact["post_id"]) != neuron_id:
                    continue
                source_node = int(contact["post_run_node_id"])
                if source_node not in contact_locset_by_node:
                    raise ValueError(
                        f"Chemical contact {contact['contact_id']} maps to missing run node {source_node}"
                    )
                synapse_label = str(contact["contact_id"])
                decor.place(
                    contact_locset_by_node[source_node],
                    arbor.synapse(
                        "exp2syn",
                        {
                            "tau1": float(contact["tau1_ms"]),
                            "tau2": float(contact["tau2_ms"]),
                            "e": float(contact["reversal_mV"]),
                        },
                    ),
                    synapse_label,
                )
                chemical_connections.append(
                    {
                        "pre_gid": gid_by_id[str(contact["pre_id"])],
                        "post_gid": gid_by_id[neuron_id],
                        "synapse_label": synapse_label,
                        "weight_uS": float(contact["weight_uS"]),
                        "delay_ms": float(contact["delay_ms"]),
                    }
                )
        if gap_enabled:
            for edge in electrical_edges:
                for raw_contact in edge.get("contacts") or ():
                    contact = dict(raw_contact)
                    if contact["pre_id"] == neuron_id:
                        source_node = int(contact["pre_run_node_id"])
                        endpoint = "a" if edge["neuron_a"] == neuron_id else "b"
                    elif contact["post_id"] == neuron_id:
                        source_node = int(contact["post_run_node_id"])
                        endpoint = "a" if edge["neuron_a"] == neuron_id else "b"
                    else:
                        continue
                    if source_node not in contact_locset_by_node:
                        raise ValueError(
                            f"Gap contact {contact['contact_id']} maps to missing run node {source_node}"
                        )
                    label = f"{contact['contact_id']}-{endpoint}"
                    decor.place(
                        contact_locset_by_node[source_node],
                        _arbor_gap_junction(
                            arbor,
                            policy,
                            float(contact["effective_g_uS"]),
                            endpoint=endpoint,
                        ),
                        label,
                    )
        built_cells.append(arbor.cable_cell(morphology, decor, labels))

    if gap_enabled:
        for edge in electrical_edges:
            for raw_contact in edge.get("contacts") or ():
                contact = dict(raw_contact)
                pre_id, post_id = str(contact["pre_id"]), str(contact["post_id"])
                pre_endpoint = "a" if edge["neuron_a"] == pre_id else "b"
                post_endpoint = "a" if edge["neuron_a"] == post_id else "b"
                contact_labels.append(
                    {
                        "pre_gid": gid_by_id[pre_id],
                        "post_gid": gid_by_id[post_id],
                        "pre_label": f"{contact['contact_id']}-{pre_endpoint}",
                        "post_label": f"{contact['contact_id']}-{post_endpoint}",
                    }
                )

    class Recipe(arbor.recipe):
        def __init__(self) -> None:
            super().__init__()

        def num_cells(self) -> int:
            return len(built_cells)

        def cell_kind(self, _gid: int) -> Any:
            return arbor.cell_kind.cable

        def cell_description(self, gid: int) -> Any:
            return built_cells[int(gid)]

        def connections_on(self, gid: int) -> list[Any]:
            return [
                arbor.connection(
                    arbor.cell_global_label(
                        int(item["pre_gid"]), "chemical-source"
                    ),
                    arbor.cell_local_label(str(item["synapse_label"])),
                    float(item["weight_uS"]),
                    float(item["delay_ms"]) * units.ms,
                )
                for item in chemical_connections
                if int(item["post_gid"]) == int(gid)
            ]

        def gap_junctions_on(self, gid: int) -> list[Any]:
            connections: list[Any] = []
            for item in contact_labels:
                if int(item["pre_gid"]) == int(gid):
                    connections.append(
                        arbor.gap_junction_connection(
                            (int(item["post_gid"]), str(item["post_label"])),
                            str(item["pre_label"]),
                            1.0,
                        )
                    )
                elif int(item["post_gid"]) == int(gid):
                    connections.append(
                        arbor.gap_junction_connection(
                            (int(item["pre_gid"]), str(item["pre_label"])),
                            str(item["post_label"]),
                            1.0,
                        )
                    )
            return connections

        def probes(self, gid: int) -> list[Any]:
            neuron_id = neuron_ids[int(gid)]
            return (
                [arbor.cable_probe_membrane_voltage('"soma-center"', "soma_voltage")]
                if neuron_id in record_ids
                else []
            )

        def global_properties(self, _kind: Any) -> Any:
            return properties

    context = arbor.context(threads=int(experiment.get("workers", 1)))
    simulation = arbor.simulation(
        Recipe(),
        context=context,
        seed=int(experiment.get("random_seed", 1)),
    )
    handles = {
        neuron_id: simulation.sample(
            (gid_by_id[neuron_id], "soma_voltage"),
            arbor.regular_schedule(float(recording["sample_dt_ms"]) * units.ms),
        )
        for neuron_id in neuron_ids
        if neuron_id in record_ids
    }
    simulation.run(
        tfinal=float(experiment["duration_ms"]) * units.ms,
        dt=float(experiment["integration_dt_ms"]) * units.ms,
    )
    traces: dict[str, tuple[list[float], list[float]]] = {}
    for neuron_id, handle in handles.items():
        samples = simulation.samples(handle)
        if len(samples) != 1:
            raise RuntimeError(
                f"The soma label for neuron {neuron_id} resolved to {len(samples)} probes"
            )
        data, _metadata = samples[0]
        traces[neuron_id] = (
            [float(value) for value in data[:, 0]],
            [float(value) for value in data[:, 1]],
        )
    return traces, str(getattr(arbor, "__version__", "unknown"))


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
    _apply_neuron_discretization(
        sections,
        segment_budget=NEURON_MAX_TOTAL_SEGMENTS,
    )
    for section in sections:
        section.Ra = float(hh["ra_ohm_cm"])
        section.cm = float(hh["cm_uF_cm2"])
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


def _validated_neuron_gap_manifest(
    mechanism_dir: Path,
    *,
    neuron_version: str | None = None,
    expected_manifest_sha256: str | None = None,
) -> tuple[dict[str, Any], Path]:
    """Verify the complete app-owned NMODL cache before loading executable code."""

    root = mechanism_dir.expanduser().resolve()
    manifest_path = root / "mechanism_manifest.json"
    if not root.is_dir() or not manifest_path.is_file():
        raise FileNotFoundError(
            f"The app-owned NEURON gap mechanism cache is incomplete: {root}"
        )
    if expected_manifest_sha256 is not None and (
        len(expected_manifest_sha256) != 64
        or _sha256(manifest_path) != expected_manifest_sha256
    ):
        raise ValueError(
            "The app-owned NEURON gap mechanism manifest changed after preflight"
        )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("The NEURON gap mechanism manifest must contain an object")
    if int(payload.get("schema_version", 0)) != 1 or payload.get("engine") != "neuron":
        raise ValueError("The NEURON gap mechanism manifest schema is unsupported")
    if neuron_version is not None and str(payload.get("neuron_version") or "") != str(
        neuron_version
    ):
        raise ValueError(
            "The compiled gap mechanisms belong to a different NEURON runtime"
        )

    records = dict(payload.get("mechanisms") or {})
    if set(records) != set(NEURON_GAP_SOURCE_HASHES):
        raise ValueError("The NEURON gap mechanism manifest is incomplete")
    for mechanism, expected_digest in NEURON_GAP_SOURCE_HASHES.items():
        record = dict(records.get(mechanism) or {})
        if str(record.get("source_sha256") or "") != expected_digest:
            raise ValueError(f"The frozen {mechanism} source identity is invalid")
        source_relpath = Path(str(record.get("source_relpath") or ""))
        if source_relpath.is_absolute():
            raise ValueError(f"The {mechanism} source path is outside its cache")
        source_path = (root / source_relpath).resolve()
        try:
            source_path.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"The {mechanism} source path escapes its cache") from exc
        if not source_path.is_file() or _sha256(source_path) != expected_digest:
            raise ValueError(f"The staged {mechanism} source does not match its hash")

    library_relpath = Path(str(payload.get("library_relpath") or ""))
    if library_relpath.is_absolute():
        raise ValueError("The compiled NEURON mechanism path must be cache-relative")
    library_path = (root / library_relpath).resolve()
    try:
        library_path.relative_to(root)
    except ValueError as exc:
        raise ValueError("The compiled NEURON mechanism path escapes its cache") from exc
    if not library_path.is_file() or _sha256(library_path) != str(
        payload.get("library_sha256") or ""
    ):
        raise ValueError("The compiled NEURON mechanism library hash is invalid")
    return payload, library_path


def _load_neuron_gap_mechanisms(
    h: Any,
    neuron: Any,
    mechanism_dir: Path,
    *,
    expected_manifest_sha256: str,
) -> tuple[dict[str, Any], Path]:
    manifest, library_path = _validated_neuron_gap_manifest(
        mechanism_dir,
        neuron_version=str(getattr(neuron, "__version__", "unknown")),
        expected_manifest_sha256=expected_manifest_sha256,
    )
    library_key = str(library_path)
    if library_key not in _LOADED_NEURON_MECHANISM_LIBRARIES:
        collisions = [name for name in NEURON_GAP_SOURCE_HASHES if hasattr(h, name)]
        if collisions:
            raise RuntimeError(
                "The selected NEURON runtime already exposes unverified mechanisms with "
                "Digifly names: " + ", ".join(collisions)
            )
        h.nrn_load_dll(library_key)
        missing = [name for name in NEURON_GAP_SOURCE_HASHES if not hasattr(h, name)]
        if missing:
            raise RuntimeError(
                "The app-owned NEURON mechanism library did not register: "
                + ", ".join(missing)
            )
        _LOADED_NEURON_MECHANISM_LIBRARIES.add(library_key)
    return manifest, library_path


def _neuron_branch_cell(
    h: Any,
    swc_path: Path,
    neuron_id: str,
    hh: Mapping[str, Any],
    *,
    prefer_rostral_soma: bool,
    segment_budget: int,
) -> dict[str, Any]:
    """Build maximal branch-run sections and an O(1) SWC-node site map."""

    rows = _swc_rows(swc_path)
    target_node_id, _target_point = _target_soma_node(
        rows,
        prefer_rostral=prefer_rostral_soma,
    )
    by_id = {row[0]: row for row in rows}
    children: dict[int, list[int]] = {node_id: [] for node_id in by_id}
    for node_id, _swc_type, _x, _y, _z, _radius, parent_id in rows:
        if parent_id in children:
            children[parent_id].append(node_id)
    for values in children.values():
        values.sort()

    def is_breakpoint(node_id: int) -> bool:
        row = by_id[node_id]
        parent_id = row[6]
        child_ids = children[node_id]
        if node_id == target_node_id or parent_id == -1:
            return True
        if len(child_ids) != 1 or by_id[parent_id][1] != row[1]:
            return True
        return by_id[child_ids[0]][1] != row[1]

    breakpoints = {node_id for node_id in by_id if is_breakpoint(node_id)}
    safe_id = "".join(character if character.isalnum() else "_" for character in neuron_id)
    sections: list[Any] = []
    node_sites: dict[int, tuple[Any, float]] = {}
    soma_names: set[str] = set()
    visited_edges: set[tuple[int, int]] = set()
    total_nseg = 0

    def make_section(path: list[int]) -> None:
        nonlocal total_nseg
        if len(path) < 2:
            return
        section = h.Section(
            name=f"digifly_{safe_id}_b{len(sections)}_{path[0]}_{path[-1]}"
        )
        h.pt3dclear(sec=section)
        arcs = [0.0]
        for index, node_id in enumerate(path):
            row = by_id[node_id]
            h.pt3dadd(
                float(row[2]),
                float(row[3]),
                float(row[4]),
                2.0 * float(row[5]),
                sec=section,
            )
            if index:
                previous = by_id[path[index - 1]]
                arcs.append(
                    arcs[-1]
                    + math.dist(
                        (previous[2], previous[3], previous[4]),
                        (row[2], row[3], row[4]),
                    )
                )
        total_arc = arcs[-1]
        if total_arc <= 0.0:
            final = by_id[path[-1]]
            h.pt3dadd(
                float(final[2]) + 0.001,
                float(final[3]),
                float(final[4]),
                2.0 * float(final[5]),
                sec=section,
            )
            total_arc = 0.001
        target_nseg = _neuron_nseg_for_length(total_arc)
        _require_neuron_segment_budget(
            total_nseg,
            target_nseg,
            segment_budget=segment_budget,
        )
        section.nseg = target_nseg
        total_nseg += target_nseg
        parent_site = node_sites.get(path[0])
        if parent_site is not None:
            parent_section, parent_x = parent_site
            try:
                section.connect(parent_section(float(parent_x)), 0.0)
            except TypeError:
                section.connect(parent_section(float(parent_x)))
        sections.append(section)
        denominator = arcs[-1] if arcs[-1] > 0.0 else 1.0
        for index, node_id in enumerate(path):
            if index == 0 and node_id in node_sites:
                continue
            node_sites[node_id] = (section, float(arcs[index] / denominator))
        if any(by_id[node_id][1] == 1 for node_id in path[1:]):
            soma_names.add(section.name())

    def make_singleton(node_id: int) -> None:
        nonlocal total_nseg
        _require_neuron_segment_budget(
            total_nseg,
            1,
            segment_budget=segment_budget,
        )
        row = by_id[node_id]
        section = h.Section(name=f"digifly_{safe_id}_b{len(sections)}_{node_id}")
        h.pt3dclear(sec=section)
        diameter = 2.0 * float(row[5])
        h.pt3dadd(float(row[2]), float(row[3]), float(row[4]), diameter, sec=section)
        h.pt3dadd(
            float(row[2]) + 0.001,
            float(row[3]),
            float(row[4]),
            diameter,
            sec=section,
        )
        sections.append(section)
        total_nseg += 1
        node_sites[node_id] = (section, 0.0)
        if row[1] == 1:
            soma_names.add(section.name())

    def trace_path(start: int, child: int) -> list[int]:
        path = [start, child]
        visited_edges.add((start, child))
        current = child
        while current not in breakpoints:
            next_ids = children[current]
            if len(next_ids) != 1:
                break
            next_id = next_ids[0]
            visited_edges.add((current, next_id))
            path.append(next_id)
            current = next_id
        return path

    roots = sorted(node_id for node_id, row in by_id.items() if row[6] == -1)
    # Start only at component roots. End breakpoints are appended after their
    # incoming section exists, so every child connects to an established site.
    queue = roots
    processed: set[int] = set()
    queue_index = 0
    while queue_index < len(queue):
        start = queue[queue_index]
        queue_index += 1
        if start in processed or start not in breakpoints:
            continue
        processed.add(start)
        child_ids = children[start]
        if not child_ids:
            if start not in node_sites:
                make_singleton(start)
            continue
        for child in child_ids:
            if (start, child) in visited_edges:
                continue
            path = trace_path(start, child)
            make_section(path)
            end = path[-1]
            if end in breakpoints and end not in processed:
                queue.append(end)

    for node_id in sorted(by_id):
        if node_id not in node_sites:
            make_singleton(node_id)
    if target_node_id not in node_sites:
        raise RuntimeError(f"NEURON could not map soma target node {target_node_id}")
    soma_site = node_sites[target_node_id]
    soma_names.add(soma_site[0].name())
    for section in sections:
        section.Ra = float(hh["ra_ohm_cm"])
        section.cm = float(hh["cm_uF_cm2"])
        section.insert("hh")
        section.ena = float(hh["ena_mV"])
        section.ek = float(hh["ek_mV"])
        values = _region_hh(hh, soma=section.name() in soma_names)
        for segment in section:
            segment.hh.gnabar = values["gnabar"]
            segment.hh.gkbar = values["gkbar"]
            segment.hh.gl = values["gl"]
            segment.hh.el = values["el"]
    return {
        "sections": tuple(sections),
        "node_sites": node_sites,
        "soma_site": soma_site,
        "nseg": total_nseg,
    }


def _neuron_set_gap_pointer(
    h: Any,
    process: Any,
    peer_site: tuple[Any, float],
) -> None:
    peer_section, peer_x = peer_site
    try:
        h.setpointer(peer_section(float(peer_x))._ref_v, "vgap_ptr", process)
    except Exception:
        h.setpointer(peer_section(float(peer_x))._ref_v, process, "vgap_ptr")


def _neuron_network_traces(
    cells: Mapping[str, Mapping[str, Any]],
    experiment: Mapping[str, Any],
    stimulus: Mapping[str, Any],
    condition: Mapping[str, Any],
    edge_manifest: Mapping[str, Any],
    mechanism_dir: Path | None,
    mechanism_manifest_sha256: str,
) -> tuple[dict[str, tuple[list[float], list[float]]], str]:
    """Execute one deterministic, fixed-step multi-cell NEURON network."""

    os.environ.pop("DISPLAY", None)
    from neuron import h
    import neuron

    h("forall delete_section()")
    h.load_file("stdrun.hoc")
    neuron_ids = tuple(cells)
    chemical_enabled = bool(condition.get("chemical_synapses_enabled", True))
    gap_enabled = bool(condition.get("gap_junctions_enabled", True))
    chemical_edges = [dict(item) for item in edge_manifest.get("chemical_edges") or ()]
    electrical_edges = [dict(item) for item in edge_manifest.get("electrical_edges") or ()]
    if gap_enabled and electrical_edges:
        if mechanism_dir is None:
            raise FileNotFoundError("The app-owned NEURON gap mechanism path is missing")
        _load_neuron_gap_mechanisms(
            h,
            neuron,
            mechanism_dir,
            expected_manifest_sha256=mechanism_manifest_sha256,
        )

    built: dict[str, dict[str, Any]] = {}
    total_segments = 0
    for neuron_id in neuron_ids:
        cell = dict(cells[neuron_id])
        built[neuron_id] = _neuron_branch_cell(
            h,
            Path(str(cell["swc_path"])),
            neuron_id,
            dict(cell["hh"]),
            prefer_rostral_soma=bool(cell["prefer_rostral_soma"]),
            segment_budget=NEURON_MAX_TOTAL_SEGMENTS - total_segments,
        )
        total_segments += int(built[neuron_id]["nseg"])
    h.define_shape()

    target_ids = {
        str(value) for value in stimulus.get("target_neuron_ids") or neuron_ids
    }
    recording = dict(experiment.get("recording") or {})
    record_ids = {
        str(value) for value in recording.get("target_neuron_ids") or neuron_ids
    }
    amplitude = float(stimulus["amplitude_nA"]) * float(
        condition.get("stimulus_scale", 1.0)
    )
    clamps: list[Any] = []
    for neuron_id in neuron_ids:
        if neuron_id not in target_ids:
            continue
        soma_section, soma_x = built[neuron_id]["soma_site"]
        for delay, duration in _pulse_windows(stimulus):
            clamp = h.IClamp(soma_section(float(soma_x)))
            clamp.delay = float(delay)
            clamp.dur = float(duration)
            clamp.amp = amplitude
            clamps.append(clamp)

    synapses: list[Any] = []
    netcons: list[Any] = []
    if chemical_enabled:
        threshold = float(
            dict(edge_manifest.get("chemical_synapse_policy") or {}).get(
                "spike_threshold_mV", 0.0
            )
        )
        for contact in chemical_edges:
            pre_id = str(contact["pre_id"])
            post_id = str(contact["post_id"])
            source_section, source_x = built[pre_id]["soma_site"]
            post_node = int(contact["post_run_node_id"])
            post_site = built[post_id]["node_sites"].get(post_node)
            if post_site is None:
                raise ValueError(
                    f"Chemical contact {contact['contact_id']} maps to missing run node {post_node}"
                )
            post_section, post_x = post_site
            synapse = h.Exp2Syn(post_section(float(post_x)))
            synapse.e = float(contact["reversal_mV"])
            synapse.tau1 = float(contact["tau1_ms"])
            synapse.tau2 = float(contact["tau2_ms"])
            netcon = h.NetCon(
                source_section(float(source_x))._ref_v,
                synapse,
                sec=source_section,
            )
            netcon.threshold = threshold
            netcon.weight[0] = float(contact["weight_uS"])
            netcon.delay = float(contact["delay_ms"])
            synapses.append(synapse)
            netcons.append(netcon)

    gap_processes: list[Any] = []
    if gap_enabled:
        policy = dict(edge_manifest.get("gap_junction_policy") or {})
        mode = str(policy.get("mode") or "")
        preferred = str(policy.get("preferred_direction") or "a_to_b")
        for edge in electrical_edges:
            neuron_a = str(edge["neuron_a"])
            neuron_b = str(edge["neuron_b"])
            for raw_contact in edge.get("contacts") or ():
                contact = dict(raw_contact)

                def site_for(neuron_id: str) -> tuple[Any, float]:
                    if str(contact["pre_id"]) == neuron_id:
                        node_id = int(contact["pre_run_node_id"])
                    elif str(contact["post_id"]) == neuron_id:
                        node_id = int(contact["post_run_node_id"])
                    else:
                        raise ValueError(
                            f"Gap contact {contact['contact_id']} is outside pair {neuron_a}, {neuron_b}"
                        )
                    site = built[neuron_id]["node_sites"].get(node_id)
                    if site is None:
                        raise ValueError(
                            f"Gap contact {contact['contact_id']} maps to missing run node {node_id}"
                        )
                    return site

                site_a = site_for(neuron_a)
                site_b = site_for(neuron_b)
                conductance_ns = float(contact["effective_g_uS"]) * 1000.0
                if mode == "ohmic":
                    section_a, x_a = site_a
                    section_b, x_b = site_b
                    gap_a = h.Gap(float(x_a), sec=section_a)
                    gap_b = h.Gap(float(x_b), sec=section_b)
                    gap_a.g = conductance_ns
                    gap_b.g = conductance_ns
                    _neuron_set_gap_pointer(h, gap_a, site_b)
                    _neuron_set_gap_pointer(h, gap_b, site_a)
                    gap_processes.extend((gap_a, gap_b))
                elif mode == "rectifying":
                    source_site, target_site = (
                        (site_a, site_b) if preferred == "a_to_b" else (site_b, site_a)
                    )
                    target_section, target_x = target_site
                    gap = h.RectGap(float(target_x), sec=target_section)
                    gap.gmax = conductance_ns
                    _neuron_set_gap_pointer(h, gap, source_site)
                    gap_processes.append(gap)
                elif mode == "heterotypic_rectifying":
                    section_a, x_a = site_a
                    section_b, x_b = site_b
                    gap_a = h.HeteroRectGap(float(x_a), sec=section_a)
                    gap_b = h.HeteroRectGap(float(x_b), sec=section_b)
                    orient_a, orient_b = (
                        (1.0, -1.0) if preferred == "a_to_b" else (-1.0, 1.0)
                    )
                    for process, orientation in ((gap_a, orient_a), (gap_b, orient_b)):
                        process.gmax_open = conductance_ns
                        process.gmax_closed = conductance_ns * float(
                            policy.get("g_closed_frac", 0.0)
                        )
                        process.orientation = orientation
                        process.vhalf = float(policy.get("vhalf_mV", 0.0))
                        process.vslope = float(policy.get("vslope_mV", 5.0))
                        process.empirical_residual_frac = float(
                            policy.get("empirical_residual_frac", 0.2)
                        )
                        process.tau_open_ms = float(policy.get("tau_open_ms", 6.0))
                        process.tau_close_ms = float(policy.get("tau_close_ms", 2.0))
                    _neuron_set_gap_pointer(h, gap_a, site_b)
                    _neuron_set_gap_pointer(h, gap_b, site_a)
                    gap_processes.extend((gap_a, gap_b))
                else:
                    raise ValueError(f"Unsupported Digifly gap-junction mode: {mode}")

    sample_dt = float(recording["sample_dt_ms"])
    time_vector = h.Vector()
    time_vector.record(h._ref_t, sample_dt)
    voltage_vectors: dict[str, Any] = {}
    for neuron_id in neuron_ids:
        if neuron_id not in record_ids:
            continue
        soma_section, soma_x = built[neuron_id]["soma_site"]
        vector = h.Vector()
        vector.record(soma_section(float(soma_x))._ref_v, sample_dt)
        voltage_vectors[neuron_id] = vector
    h.dt = float(experiment["integration_dt_ms"])
    h.steps_per_ms = 1.0 / h.dt
    h.celsius = float(experiment["temperature_C"])
    h.tstop = float(experiment["duration_ms"])
    try:
        h.CVode().active(0)
    except Exception:
        pass
    h.finitialize(float(experiment["initial_voltage_mV"]))
    h.continuerun(h.tstop)
    times = [float(value) for value in time_vector]
    traces = {
        neuron_id: (times, [float(value) for value in voltage_vectors[neuron_id]])
        for neuron_id in neuron_ids
        if neuron_id in voltage_vectors
    }
    # Keep all hoc objects and sections alive through continuerun.
    _ = (built, clamps, synapses, netcons, gap_processes)
    return traces, str(getattr(neuron, "__version__", "unknown"))


def _write_plot(
    path: Path,
    rows: Sequence[Mapping[str, Any]],
    experiment: Mapping[str, Any],
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    grouped: dict[tuple[str, int, str], tuple[list[float], list[float]]] = {}
    for row in rows:
        key = (
            str(row["condition"]),
            int(row["repetition"]),
            str(row["neuron_id"]),
        )
        times, voltages = grouped.setdefault(key, ([], []))
        times.append(float(row["time_ms"]))
        voltages.append(float(row["voltage_mV"]))
    figure, axis = plt.subplots(figsize=(11.0, 5.9), constrained_layout=True)
    for (condition, repetition, neuron_id), (times, voltages) in grouped.items():
        label = f"{condition} · {neuron_id}"
        if int(experiment.get("repetitions", 1)) != 1:
            label += f" · rep {repetition}"
        axis.plot(times, voltages, linewidth=1.9, label=label)
    axis.set_title(
        str(experiment.get("name") or "Digifly experiment"),
        fontsize=16,
    )
    axis.set_xlabel("Time (ms)", fontsize=13)
    axis.set_ylabel("Soma membrane voltage (mV)", fontsize=13)
    axis.tick_params(axis="both", labelsize=11)
    axis.grid(True, alpha=0.25)
    axis.legend(loc="best", fontsize=11)
    # Preserve enough source pixels for a crisp fit-to-frame preview on Retina
    # displays while keeping the portable PNG comfortably below dataset scale.
    figure.savefig(path, dpi=260)
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
    edge_manifest = _load_edge_manifest(request, output_dir)
    _validate_request_capabilities(engine, circuit, experiment, edge_manifest)
    _validate_edge_source_identity(edge_manifest)
    neuron_ids = tuple(str(value) for value in circuit.get("neuron_ids") or ())
    morphology_transform = (
        "parent_before_child_reindex_segment_tree_root_stub_v1"
        if engine == "arbor"
        else "parent_before_child_reindex_neuron_branch_runs_v1"
        if len(tuple(circuit.get("neuron_ids") or ())) > 1
        else "parent_before_child_reindex_v1"
    )
    morphology_records: dict[str, dict[str, Any]] = {}
    simulation_cells: dict[str, dict[str, Any]] = {}
    morphology_artifacts: list[dict[str, str]] = []
    morphology_metadata: dict[str, dict[str, Any]] = {}
    resolved_edge_manifest = json.loads(json.dumps(edge_manifest))
    chemical_contacts = list(resolved_edge_manifest.get("chemical_edges") or ())
    electrical_contacts = [
        contact
        for edge in resolved_edge_manifest.get("electrical_edges") or ()
        for contact in edge.get("contacts") or ()
    ]
    morphology_root = output_dir / "morphologies"
    morphology_root.mkdir(parents=True, exist_ok=False)
    path_components = {
        neuron_id: _safe_neuron_path_component(neuron_id)
        for neuron_id in neuron_ids
    }
    if len(set(path_components.values())) != len(path_components):
        raise ValueError("Neuron IDs did not produce unique run path components")
    for neuron_id in neuron_ids:
        morphology_record = dict(
            (request.get("morphologies") or {}).get(neuron_id) or {}
        )
        swc_path = Path(str(morphology_record.get("path") or "")).expanduser().resolve()
        if not swc_path.is_file():
            raise FileNotFoundError(f"Source SWC is missing: {swc_path}")
        source_label = f"Source SWC for neuron {neuron_id}"
        expected_digest = _required_sha256(
            morphology_record.get("sha256"),
            label=source_label,
        )
        path_component = path_components[neuron_id]
        cell_dir = morphology_root / path_component
        cell_dir.mkdir(exist_ok=False)
        normalized_swc_path, node_map_path, normalized_digest = _normalize_swc(
            swc_path,
            cell_dir,
            expected_source_sha256=expected_digest,
            source_label=source_label,
        )
        actual_digest = expected_digest
        node_map = _source_to_run_node_map(node_map_path)
        for contact in electrical_contacts:
            if str(contact["pre_id"]) == neuron_id:
                contact["pre_run_node_id"] = node_map[int(contact["pre_source_node_id"])]
            if str(contact["post_id"]) == neuron_id:
                contact["post_run_node_id"] = node_map[int(contact["post_source_node_id"])]
        for contact in chemical_contacts:
            if str(contact["post_id"]) == neuron_id:
                contact["post_run_node_id"] = node_map[int(contact["post_source_node_id"])]
        prefer_rostral_soma = str(
            morphology_record.get("family") or ""
        ).upper().startswith("DN")
        run_target_node_id, _target_point = _target_soma_node(
            _swc_rows(normalized_swc_path),
            prefer_rostral=prefer_rostral_soma,
        )
        source_by_run_node = {
            run_node_id: source_node_id
            for source_node_id, run_node_id in node_map.items()
        }
        source_target_node_id = source_by_run_node[run_target_node_id]
        target_policy = (
            "rostral max-Z non-root type-1 node"
            if prefer_rostral_soma
            else "widest non-root type-1 node"
        )
        root_stub = (
            _arbor_root_stub_length(_swc_rows(normalized_swc_path))
            if engine == "arbor"
            else 0.0
        )
        morphology_records[neuron_id] = morphology_record
        simulation_cells[neuron_id] = {
            "swc_path": str(normalized_swc_path),
            "hh": _merged_hh(circuit, neuron_id),
            "prefer_rostral_soma": prefer_rostral_soma,
        }
        morphology_metadata[neuron_id] = {
            "run_path_component": path_component,
            "neuron_type": str(morphology_record.get("neuron_type") or "Unknown"),
            "source_swc_sha256": actual_digest,
            "simulation_swc_sha256": normalized_digest,
            "source_soma_target_node_id": source_target_node_id,
            "soma_target_policy": target_policy,
            "arbor_root_stub_length_um": root_stub,
        }
        relative_dir = Path("morphologies") / path_component
        morphology_label = (
            "Normalized morphology"
            if len(neuron_ids) == 1
            else f"Normalized morphology · {neuron_id}"
        )
        node_map_label = (
            "Source-to-run SWC node map"
            if len(neuron_ids) == 1
            else f"Source-to-run SWC node map · {neuron_id}"
        )
        morphology_artifacts.extend(
            (
                {
                    "kind": "morphology",
                    "path": str(relative_dir / normalized_swc_path.name),
                    "label": morphology_label,
                },
                {
                    "kind": "table",
                    "path": str(relative_dir / node_map_path.name),
                    "label": node_map_label,
                },
            )
        )
    resolved_edge_path = output_dir / "resolved_edge_manifest.json"
    _atomic_json(resolved_edge_path, resolved_edge_manifest)

    manifest_path = output_dir / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        {
            "state": "running",
            "started_at": _now(),
            "morphology_transform": morphology_transform,
            "morphologies": morphology_metadata,
            "resolved_edge_manifest_path": str(resolved_edge_path),
            "resolved_edge_manifest_sha256": _sha256(resolved_edge_path),
        }
    )
    _atomic_json(manifest_path, manifest)
    _emit(
        "start",
        f"Starting {engine.upper()} classic-HH experiment",
        neuron_ids=list(neuron_ids),
        chemical_contacts=len(chemical_contacts),
        electrical_contacts=len(electrical_contacts),
    )

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
    for condition in conditions:
        for repetition in range(1, int(experiment["repetitions"]) + 1):
            _emit(
                "simulate",
                f"Running {condition['name']} repetition {repetition}",
                completed=completed,
                total=total,
            )
            if engine == "arbor" and (
                len(neuron_ids) > 1 or chemical_contacts or electrical_contacts
            ):
                catalogue_text = str(request.get("arbor_gap_catalogue") or "").strip()
                catalogue_path = Path(catalogue_text) if catalogue_text else None
                if electrical_contacts and (
                    catalogue_path is None or not catalogue_path.is_file()
                ):
                    raise FileNotFoundError(
                        f"The app-owned Arbor gap catalogue is missing: {catalogue_path}"
                    )
                traces, runtime_version = _arbor_network_traces(
                    simulation_cells,
                    experiment,
                    stimulus,
                    condition,
                    resolved_edge_manifest,
                    catalogue_path,
                )
            elif engine == "neuron" and (
                len(neuron_ids) > 1 or chemical_contacts or electrical_contacts
            ):
                mechanism_text = str(
                    request.get("neuron_gap_mechanisms") or ""
                ).strip()
                mechanism_dir = Path(mechanism_text) if mechanism_text else None
                if electrical_contacts and (
                    mechanism_dir is None or not mechanism_dir.is_dir()
                ):
                    raise FileNotFoundError(
                        f"The app-owned NEURON gap mechanism cache is missing: {mechanism_dir}"
                    )
                traces, runtime_version = _neuron_network_traces(
                    simulation_cells,
                    experiment,
                    stimulus,
                    condition,
                    resolved_edge_manifest,
                    mechanism_dir,
                    str(request.get("neuron_gap_manifest_sha256") or ""),
                )
            else:
                neuron_id = neuron_ids[0]
                cell = simulation_cells[neuron_id]
                simulate = _arbor_trace if engine == "arbor" else _neuron_trace
                times, voltages, runtime_version = simulate(
                    Path(str(cell["swc_path"])),
                    dict(cell["hh"]),
                    experiment,
                    stimulus,
                    condition,
                    prefer_rostral_soma=bool(cell["prefer_rostral_soma"]),
                )
                traces = {neuron_id: (times, voltages)}
            for neuron_id, (times, voltages) in traces.items():
                if len(times) < 2 or len(times) != len(voltages):
                    raise RuntimeError(
                        f"Simulator returned an incomplete soma-voltage trace for {neuron_id}"
                    )
                if not all(
                    math.isfinite(float(time_ms)) and math.isfinite(float(voltage_mV))
                    for time_ms, voltage_mV in zip(times, voltages)
                ):
                    raise RuntimeError(
                        f"Simulator returned non-finite soma voltage for {neuron_id}; no result was accepted"
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
        {"kind": "document", "path": "edge_manifest.json", "label": "Source edge manifest"},
        {"kind": "document", "path": resolved_edge_path.name, "label": "Resolved edge manifest"},
        {"kind": "document", "path": request_path.name, "label": "Resolved worker request"},
    ]
    artifacts.extend(morphology_artifacts)
    if experiment["recording"].get("make_plots", True):
        plot_path = output_dir / "voltage_comparison.png"
        _write_plot(plot_path, trace_rows, experiment)
        artifacts.append(
            {"kind": "image", "path": plot_path.name, "label": "Voltage comparison"}
        )
    completed_at = _now()
    neuron_types = {
        neuron_id: str(record.get("neuron_type") or "Unknown")
        for neuron_id, record in morphology_records.items()
    }
    metadata = {
        "Neuron IDs": ", ".join(neuron_ids),
        "Neuron types": ", ".join(
            f"{neuron_id}: {neuron_types[neuron_id]}" for neuron_id in neuron_ids
        ),
        "Simulator version": runtime_version,
        "Conditions": len(conditions),
        "Repetitions": int(experiment["repetitions"]),
        "Duration (ms)": float(experiment["duration_ms"]),
        "Integration step (ms)": float(experiment["integration_dt_ms"]),
        "Recorded samples": len(trace_rows),
        "Detected spikes": len(spike_rows),
        "Morphology transform": morphology_transform,
        "Chemical contacts": len(chemical_contacts),
        "Electrical contacts": len(electrical_contacts),
        "Edge manifest SHA-256": str(request["edge_manifest_sha256"]),
    }
    if engine == "neuron":
        metadata.update(
            {
                "NEURON target segment length (um)": NEURON_TARGET_SEGMENT_UM,
                "NEURON total segment safety limit": NEURON_MAX_TOTAL_SEGMENTS,
            }
        )
    if electrical_contacts:
        metadata.update(
            {
                "Gap-junction mode": str(
                    resolved_edge_manifest["gap_junction_policy"]["mode"]
                ),
                "Gap conductance basis": str(
                    resolved_edge_manifest["gap_junction_policy"][
                        "conductance_basis"
                    ]
                ),
            }
        )
        if engine == "arbor":
            catalogue_path = Path(str(request["arbor_gap_catalogue"])).resolve()
            metadata["Arbor gap catalogue SHA-256"] = _sha256(catalogue_path)
        else:
            mechanism_dir = Path(str(request["neuron_gap_mechanisms"])).resolve()
            mechanism_manifest, library_path = _validated_neuron_gap_manifest(
                mechanism_dir,
                neuron_version=runtime_version,
                expected_manifest_sha256=str(
                    request.get("neuron_gap_manifest_sha256") or ""
                ),
            )
            metadata.update(
                {
                    "NEURON gap mechanism library SHA-256": str(
                        mechanism_manifest["library_sha256"]
                    ),
                    "NEURON gap mechanism library": str(library_path),
                }
            )
    if len(neuron_ids) == 1:
        neuron_id = neuron_ids[0]
        single = morphology_metadata[neuron_id]
        metadata.update(
            {
                "Neuron ID": neuron_id,
                "Neuron type": neuron_types[neuron_id],
                "SWC SHA-256": single["source_swc_sha256"],
                "Simulation SWC SHA-256": single["simulation_swc_sha256"],
                "Source soma target node ID": single["source_soma_target_node_id"],
                "Soma target policy": single["soma_target_policy"],
            }
        )
        if engine == "arbor":
            metadata["Arbor root stub length (um)"] = single[
                "arbor_root_stub_length_um"
            ]
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
