from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
from typing import Any

from .circuit import CircuitSpec
from .connectomes import NeuronRecord


@dataclass(frozen=True)
class SwcNode:
    node_id: int
    swc_type: int
    x: float
    y: float
    z: float
    radius: float
    parent_id: int


@dataclass(frozen=True)
class SwcSegment:
    child_id: int
    parent_id: int
    child: tuple[float, float, float]
    parent: tuple[float, float, float]
    radius: float
    swc_type: int


@dataclass(frozen=True)
class Morphology:
    record: NeuronRecord
    nodes: tuple[SwcNode, ...]
    segments: tuple[SwcSegment, ...]
    bounds: tuple[float, float, float, float, float, float]

    @property
    def center(self) -> tuple[float, float, float]:
        xmin, xmax, ymin, ymax, zmin, zmax = self.bounds
        return ((xmin + xmax) / 2.0, (ymin + ymax) / 2.0, (zmin + zmax) / 2.0)

    @property
    def radius(self) -> float:
        xmin, xmax, ymin, ymax, zmin, zmax = self.bounds
        dx, dy, dz = xmax - xmin, ymax - ymin, zmax - zmin
        return max((dx * dx + dy * dy + dz * dz) ** 0.5 / 2.0, 0.01)


@dataclass(frozen=True)
class SomaLocation:
    """A display location for a cell body or an intentional DN pseudosoma."""

    node_id: int | None
    point: tuple[float, float, float]
    radius: float
    kind: str

    @property
    def is_pseudosoma(self) -> bool:
        return self.kind in {"pseudosoma", "inferred-pseudosoma"}


def locate_soma(morphology: Morphology) -> SomaLocation:
    """Locate the soma marker using Digifly's validated rostral convention.

    Phase 1 appends a type-1 cap beyond the rostral-most (maximum-Z) leaf for
    MANC DNs that have no biological soma in the volume. Phase 2 subsequently
    identifies that DN cap as the maximum-Z type-1 node, breaking a Z tie by
    radius. Ordinary somata use the widest type-1 node, matching Digifly's
    normal SWC/NEURON soma binding instead of placing their marker on a soma's
    northern surface.

    Custom SWCs need not contain a type-1 node. For those, DNs retain the
    north-end intent via a maximum-Z fallback; other cells use the widest
    root, then the widest node. Synthetic/test morphologies with segment-only
    geometry fall back to an endpoint or the morphology center.
    """

    nodes = morphology.nodes
    family = str(morphology.record.family).strip().upper()
    connectome_key = str(morphology.record.connectome_key).strip().lower()
    is_dn = family == "DN" or family.startswith("DN")
    is_manc_dn = is_dn and connectome_key.startswith("manc")
    parent_ids = {node.parent_id for node in nodes if node.parent_id >= 0}
    final_node = max(nodes, key=lambda value: value.node_id) if nodes else None
    has_native_dn_cap = bool(
        is_dn
        and final_node is not None
        and final_node.swc_type == 1
        and final_node.node_id not in parent_ids
    )
    soma_nodes = tuple(node for node in nodes if node.swc_type == 1)
    if soma_nodes:
        is_pseudosoma = is_manc_dn or has_native_dn_cap
        if is_pseudosoma:
            node = max(soma_nodes, key=lambda value: (value.z, value.radius))
        else:
            node = max(soma_nodes, key=lambda value: value.radius)
        return SomaLocation(
            node.node_id,
            (node.x, node.y, node.z),
            max(node.radius, 0.01),
            "pseudosoma" if is_pseudosoma else "soma",
        )

    if nodes:
        if is_manc_dn:
            leaves = tuple(node for node in nodes if node.node_id not in parent_ids)
            node = max(leaves or nodes, key=lambda value: (value.z, value.radius))
            kind = "inferred-pseudosoma"
        else:
            roots = tuple(node for node in nodes if node.parent_id == -1)
            candidates = roots or nodes
            node = max(candidates, key=lambda value: value.radius)
            kind = "inferred-soma"
        return SomaLocation(
            node.node_id,
            (node.x, node.y, node.z),
            max(node.radius, 0.01),
            kind,
        )

    if morphology.segments:
        parent_ids = {segment.parent_id for segment in morphology.segments}
        leaf_segments = tuple(
            segment
            for segment in morphology.segments
            if segment.child_id not in parent_ids
        )
        segment = (
            max(
                leaf_segments or morphology.segments,
                key=lambda value: (value.child[2], value.radius),
            )
            if is_manc_dn
            else morphology.segments[0]
        )
        point = segment.child if is_manc_dn else segment.parent
        node_id = segment.child_id if is_manc_dn else segment.parent_id
        return SomaLocation(
            node_id,
            point,
            max(segment.radius, 0.01),
            "inferred-pseudosoma" if is_manc_dn else "inferred-soma",
        )

    return SomaLocation(None, morphology.center, 0.01, "inferred-soma")


def load_swc(record: NeuronRecord) -> Morphology:
    path = Path(record.swc_path).expanduser().resolve()
    nodes: dict[int, SwcNode] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            fields = stripped.split()
            if len(fields) < 7:
                raise ValueError(f"SWC row has fewer than seven fields at {path}:{line_number}")
            try:
                node_id_value = float(fields[0])
                swc_type_value = float(fields[1])
                parent_id_value = float(fields[6])
                if not node_id_value.is_integer() or not swc_type_value.is_integer() or not parent_id_value.is_integer():
                    raise ValueError("SWC identifiers and types must be integers")
                node = SwcNode(
                    node_id=int(node_id_value),
                    swc_type=int(swc_type_value),
                    x=float(fields[2]),
                    y=float(fields[3]),
                    z=float(fields[4]),
                    radius=float(fields[5]),
                    parent_id=int(parent_id_value),
                )
            except ValueError as exc:
                raise ValueError(f"Invalid SWC row at {path}:{line_number}") from exc
            if not all(math.isfinite(value) for value in (node.x, node.y, node.z, node.radius)):
                raise ValueError(f"Non-finite SWC geometry at {path}:{line_number}")
            if node.radius < 0.0:
                raise ValueError(f"Negative SWC radius at {path}:{line_number}")
            if node.node_id in nodes:
                raise ValueError(f"Duplicate SWC node ID {node.node_id} at {path}:{line_number}")
            if node.parent_id == node.node_id:
                raise ValueError(f"SWC node {node.node_id} is its own parent at {path}:{line_number}")
            if node.parent_id < -1:
                raise ValueError(f"SWC node {node.node_id} uses invalid root sentinel {node.parent_id}")
            nodes[node.node_id] = node
    if not nodes:
        raise ValueError(f"No valid SWC nodes found in {path}")

    roots = [node.node_id for node in nodes.values() if node.parent_id == -1]
    if len(roots) != 1:
        raise ValueError(f"SWC must contain exactly one root; found {len(roots)} in {path}")
    for node in nodes.values():
        if node.parent_id >= 0 and node.parent_id not in nodes:
            raise ValueError(f"SWC node {node.node_id} has missing parent {node.parent_id} in {path}")
    resolved = {roots[0]}
    for node_id in nodes:
        current = node_id
        chain: list[int] = []
        local: set[int] = set()
        while current not in resolved:
            if current in local:
                raise ValueError(f"Cycle detected at SWC node {current} in {path}")
            local.add(current)
            chain.append(current)
            parent_id = nodes[current].parent_id
            if parent_id < 0:
                break
            current = parent_id
        resolved.update(chain)

    ordered = tuple(nodes[key] for key in sorted(nodes))
    segments: list[SwcSegment] = []
    for child in ordered:
        parent = nodes.get(child.parent_id)
        if parent is None:
            continue
        segments.append(
            SwcSegment(
                child_id=child.node_id,
                parent_id=parent.node_id,
                child=(child.x, child.y, child.z),
                parent=(parent.x, parent.y, parent.z),
                radius=child.radius,
                swc_type=child.swc_type,
            )
        )
    if not segments:
        raise ValueError(f"No drawable SWC segments found in {path}")

    xs = [node.x for node in ordered]
    ys = [node.y for node in ordered]
    zs = [node.z for node in ordered]
    return Morphology(
        record=record,
        nodes=ordered,
        segments=tuple(segments),
        bounds=(min(xs), max(xs), min(ys), max(ys), min(zs), max(zs)),
    )


def morphology_library_root() -> Path:
    return Path.home() / "Digifly Workstation Workspace" / "morphologies"


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-._") or "custom-neuron"


def sha256_file(path: str | Path) -> str:
    path = Path(path).expanduser().resolve()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_custom_biophysics(
    bundle_root: str | Path,
    neuron_id: str,
    *,
    swc_path: str | Path,
) -> dict[str, Any] | None:
    """Load and minimally validate a custom bundle's portable HH sidecar."""
    root = Path(bundle_root).expanduser().resolve()
    path = root / "digifly-biophysics.json"
    if not path.is_file():
        return None
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError(f"Custom biophysics bundle has no provenance manifest: {root}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or int(manifest.get("schema_version", 0)) != 1:
        raise ValueError(f"Unsupported custom morphology manifest: {manifest_path}")
    manifest_neuron = dict(manifest.get("neuron") or {})
    if str(manifest_neuron.get("id") or "") != str(neuron_id):
        raise ValueError(f"Custom morphology manifest neuron ID does not match {neuron_id}")
    recorded_swc = Path(str(manifest.get("custom_swc") or ""))
    recorded_swc = recorded_swc.resolve() if recorded_swc.is_absolute() else (root / recorded_swc).resolve()
    actual_swc = Path(swc_path).expanduser().resolve()
    if not recorded_swc.is_relative_to(root) or recorded_swc != actual_swc:
        raise ValueError(f"Custom morphology manifest does not identify the loaded SWC: {actual_swc}")
    expected_hash = str(manifest.get("custom_sha256") or manifest.get("source_sha256") or "")
    if len(expected_hash) != 64 or sha256_file(actual_swc) != expected_hash:
        raise ValueError(f"Custom morphology SWC identity check failed: {actual_swc}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or int(payload.get("schema_version", 0)) not in {1, 2}:
        raise ValueError(f"Unsupported custom biophysics sidecar: {path}")
    if str(payload.get("neuron_id") or "") != str(neuron_id):
        raise ValueError(f"Biophysics sidecar neuron ID does not match {neuron_id}: {path}")
    for key in ("base_hh", "neuron_override", "compartment_overrides"):
        if not isinstance(payload.get(key, {}), dict):
            raise ValueError(f"Biophysics sidecar field {key} must be an object: {path}")
    if int(payload.get("schema_version", 0)) >= 2:
        for key in (
            "base_membrane",
            "neuron_mechanism_override",
            "compartment_mechanism_overrides",
        ):
            if not isinstance(payload.get(key, {}), dict):
                raise ValueError(f"Biophysics sidecar field {key} must be an object: {path}")
    return payload


def save_custom_morphology(
    morphology: Morphology,
    circuit: CircuitSpec,
    *,
    selected_engine: str,
    library_root: str | Path | None = None,
    label: str = "custom",
) -> Path:
    """Save a non-destructive SWC copy and engine-neutral biophysics sidecar."""
    root = Path(library_root or morphology_library_root()).expanduser().resolve()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    record = morphology.record
    bundle_name = _safe_name(f"{record.neuron_type}-{record.neuron_id}-{label}-{timestamp}")
    bundle = root / bundle_name
    suffix = 1
    while bundle.exists():
        suffix += 1
        bundle = root / f"{bundle_name}-{suffix}"

    family = record.family if record.family != "UNKNOWN" else "UNCLASSIFIED"
    morphology_dir = bundle / _safe_name(family) / _safe_name(record.neuron_type) / record.neuron_id
    morphology_dir.mkdir(parents=True, exist_ok=False)
    source = Path(record.swc_path).expanduser().resolve()
    destination = morphology_dir / source.name
    shutil.copy2(source, destination)

    neuron_id = record.neuron_id
    sidecar = {
        "schema_version": 2,
        "neuron_id": neuron_id,
        "base_hh": circuit.hh.to_dict(),
        "base_membrane": circuit.membrane.to_dict(),
        "neuron_override": circuit.neuron_overrides.get(neuron_id, {}),
        "compartment_overrides": circuit.compartment_overrides.get(neuron_id, {}),
        "neuron_mechanism_override": circuit.neuron_mechanism_overrides.get(neuron_id, {}),
        "compartment_mechanism_overrides": circuit.compartment_mechanism_overrides.get(
            neuron_id, {}
        ),
    }
    sidecar_path = bundle / "digifly-biophysics.json"
    sidecar_path.write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "selected_engine_at_save": str(selected_engine),
        "connectome": circuit.connectome.to_dict(),
        "neuron": {
            "id": neuron_id,
            "family": record.family,
            "type": record.neuron_type,
        },
        "source_swc": str(source),
        "source_sha256": sha256_file(source),
        "custom_sha256": sha256_file(destination),
        "custom_swc": destination.relative_to(bundle).as_posix(),
        "biophysics_sidecar": sidecar_path.relative_to(bundle).as_posix(),
        "note": (
            "SWC geometry is copied unchanged; HH and membrane-mechanism overrides are stored "
            "in the sidecar by SWC node ID. Gap junctions remain circuit-edge policies."
        ),
    }
    (bundle / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return bundle
