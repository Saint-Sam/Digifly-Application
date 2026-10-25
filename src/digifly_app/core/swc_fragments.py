"""Explicit, copy-only joins of disconnected SWC components.

No existing point, radius, ID, or real edge is moved. New root attachments and
short tapered filler cables are model assumptions, not recovered anatomy.
"""
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import tempfile

from .swc_quality import SwcQualityReport, analyze_swc, _parse_swc, _topology_issues, sha256_file
from .swc_units import source_coordinate_contract

FILLER_HEADER = "# digifly_filler "
MAX_FILLER_COMPARTMENTS = 25_000


def _volume(length, first_radius, last_radius):
    try:
        result = math.pi * length * (first_radius**2 + first_radius * last_radius + last_radius**2) / 3
    except OverflowError as exc:
        raise ValueError("SWC dimensions exceed the finite filler sizing range") from exc
    if not math.isfinite(result):
        raise ValueError("SWC dimensions exceed the finite filler sizing range")
    return result


@dataclass(frozen=True)
class FragmentJoinPlan:
    source_path: str
    source_sha256: str
    coordinate_transform: dict
    original_roots: tuple[int, ...]
    parent_changes: dict[int, int]
    filler_nodes: tuple[tuple, ...]
    bridges: tuple[dict, ...]
    original_length_um: float
    original_volume_um3: float

    @property
    def added_compartments(self):
        return len(self.filler_nodes) + len(self.parent_changes)

    def to_dict(self):
        volume = sum(b["volume_um3"] for b in self.bridges)
        return {**asdict(self), "added_compartments": self.added_compartments,
                "added_length_um": sum(b["length_um"] for b in self.bridges),
                "added_volume_um3": volume,
                "added_volume_fraction": volume / self.original_volume_um3 if self.original_volume_um3 else None,
                "policy": "adaptive_taper_local_length_and_volume_cap",
                "biological_reconstruction": False,
                "original_coordinates_radii_and_real_edges_unchanged": True,
                "filler_biophysics": "Uses the circuit's branch membrane settings; synthetic added cable changes electrical conduction."}


def plan_fragment_join(report: SwcQualityReport) -> FragmentJoinPlan:
    path = Path(report.path).resolve()
    if sha256_file(path) != report.sha256:
        raise ValueError("SWC changed after review; audit it again")
    _lines, nodes, parse_errors = _parse_swc(path)
    errors, _warnings = _topology_issues(nodes)
    if parse_errors or errors or any(n.node_id <= 0 for n in nodes):
        raise ValueError("Fragment joining requires valid positive-radius acyclic SWC components")
    by_id = {n.node_id: n for n in nodes}
    roots = tuple(sorted(n.node_id for n in nodes if n.parent_id < 0))
    if len(roots) < 2:
        raise ValueError("This SWC is already connected")
    contract = source_coordinate_contract(path)
    scale = float(contract["scale_to_um"])
    adjacency = {n.node_id: [] for n in nodes}
    for node in nodes:
        if node.parent_id >= 0:
            adjacency[node.parent_id].append(node.node_id)
            adjacency[node.node_id].append(node.parent_id)
    components = {}
    for root in roots:
        seen, stack = set(), [root]
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            stack.extend(adjacency[current])
        components[root] = seen
    primary = min(roots, key=lambda root: (-len(components[root]), root))
    connected = set(components[primary])
    pending = set(roots) - {primary}
    next_id = max(by_id) + 1
    added, changes, bridges = [], {}, []

    def point(node):
        return node.x, node.y, node.z

    while pending:
        distance, root, anchor = min(
            (math.dist(point(by_id[root]), point(by_id[anchor])), root, anchor)
            for root in sorted(pending) for anchor in sorted(connected))
        if not math.isfinite(distance):
            raise ValueError("SWC dimensions exceed the finite filler sizing range")
        if distance <= 0:
            raise ValueError("Coincident disconnected endpoints need an explicit zero-length junction policy, not an invented filler")
        first, last = by_id[anchor], by_id[root]
        reference = []
        for endpoint in (first, last):
            for other_id in adjacency[endpoint.node_id]:
                other = by_id[other_id]
                length = math.dist(point(endpoint), point(other))
                if length > 0:
                    reference.append((length, _volume(length, endpoint.radius, other.radius)))
        if not reference:
            raise ValueError("No adjoining real cable is available to size this fragment's filler")
        target_length = statistics.median(length for length, _ in reference)
        max_length = max(length for length, _ in reference)
        max_volume = max(volume for _, volume in reference)
        # A conservative cylinder bound covers every frustum in the taper.
        size_ratio = _volume(distance, max(first.radius, last.radius), max(first.radius, last.radius)) / max_volume
        if not all(math.isfinite(value) for value in (distance / target_length, size_ratio)):
            raise ValueError("SWC dimensions exceed the finite filler sizing range")
        count = max(1, math.ceil(distance / target_length), math.ceil(distance / max_length), math.ceil(size_ratio))
        if len(added) + len(changes) + count > MAX_FILLER_COMPARTMENTS:
            raise ValueError("The adaptive filler exceeds the 25,000-compartment repair budget; no copy was written")
        parent = anchor
        for ordinal in range(1, count):
            fraction = ordinal / count
            xyz = tuple(a + fraction * (b - a) for a, b in zip(point(first), point(last)))
            radius = first.radius + fraction * (last.radius - first.radius)
            added.append((next_id, 3, *xyz, radius, parent))
            parent = next_id
            next_id += 1
        changes[root] = parent
        bridges.append({"anchor_node_id": anchor, "fragment_root_id": root,
            "compartments": count, "length_um": distance * scale,
            "volume_um3": _volume(distance, first.radius, last.radius) * scale**3,
            "maximum_compartment_length_um": distance / count * scale,
            "reference_maximum_length_um": max_length * scale,
            "maximum_compartment_volume_bound_um3": math.pi * distance / count * max(first.radius, last.radius)**2 * scale**3,
            "reference_maximum_volume_um3": max_volume * scale**3,
            "endpoint_radii_um": [first.radius * scale, last.radius * scale]})
        connected.update(components[root])
        pending.remove(root)
    real_edges = [(n, by_id[n.parent_id]) for n in nodes if n.parent_id >= 0]
    length = sum(math.dist(point(n), point(p)) for n, p in real_edges) * scale
    volume = sum(_volume(math.dist(point(n), point(p)), n.radius, p.radius)
                 for n, p in real_edges) * scale**3
    return FragmentJoinPlan(str(path), report.sha256, contract, roots, changes, tuple(added), tuple(bridges), length, volume)


def _publish_new_text(path: Path, text: str) -> None:
    """Publish a complete file with no-replace semantics, including race cases."""
    handle, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def save_fragment_join(plan: FragmentJoinPlan) -> Path:
    source = Path(plan.source_path)
    if sha256_file(source) != plan.source_sha256:
        raise ValueError("SWC changed after the filler preview; review it again")
    if plan != plan_fragment_join(analyze_swc(source)):
        raise ValueError("Filler plan differs from the verified adaptive sizing policy; preview it again")
    original_lines, before, errors = _parse_swc(source)
    if errors:
        raise ValueError("SWC no longer parses")
    metadata = {"schema": "digifly-filler-v1",
        "synthetic_node_ids": [int(row[0]) for row in plan.filler_nodes],
        "filler_edge_child_ids": [int(row[0]) for row in plan.filler_nodes] + list(plan.parent_changes),
        "original_root_ids": list(plan.original_roots), "original_source_sha256": plan.source_sha256}
    output = source.with_name(f"{source.stem}_fragment_healed.swc")
    sidecar = output.with_suffix(".swc.quality.json")
    if os.path.lexists(output) or os.path.lexists(sidecar):
        raise FileExistsError("A joined copy already exists; the original or prior copy will not be replaced")
    text = FILLER_HEADER + json.dumps(metadata, separators=(",", ":")) + "\n"
    for line in original_lines:
        fields = line.split()
        if fields and not line.lstrip().startswith("#") and int(fields[0]) in plan.parent_changes:
            fields[6] = str(plan.parent_changes[int(fields[0])])
            line = " ".join(fields) + "\n"
        text += line if line.endswith("\n") else line + "\n"
    for row in plan.filler_nodes:
        text += f"{row[0]} {row[1]} " + " ".join(format(value, ".17g") for value in row[2:6]) + f" {row[6]}\n"
    handle, name = tempfile.mkstemp(prefix=".fragment-check-", dir=source.parent)
    checked = Path(name)
    with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
        stream.write(text)
    try:
        _lines, after, parse_errors = _parse_swc(checked)
    finally:
        checked.unlink()
    errors, _warnings = _topology_issues(after)
    after_by_id = {n.node_id:n for n in after}
    unchanged = all((n.node_id, n.swc_type, n.x, n.y, n.z, n.radius) ==
                    (after_by_id[n.node_id].node_id, after_by_id[n.node_id].swc_type,
                     after_by_id[n.node_id].x, after_by_id[n.node_id].y, after_by_id[n.node_id].z,
                     after_by_id[n.node_id].radius) and
                    after_by_id[n.node_id].parent_id == plan.parent_changes.get(n.node_id, n.parent_id)
                    for n in before)
    if parse_errors or errors or not unchanged or sum(n.parent_id < 0 for n in after) != 1:
        raise ValueError("Joined copy failed geometry/topology verification; no copy was saved")
    if sha256_file(source) != plan.source_sha256:
        raise ValueError("SWC changed during repair; no copy was saved")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    record = {"schema": "digifly-fragment-join-v1", "plan": plan.to_dict(),
        "output_sha256": digest, "source_unchanged": True,
        "coordinate_transform": {**plan.coordinate_transform, "source_sha256": digest,
            "geometry_policy": "Original geometry and real edges preserved; explicit synthetic filler edges added in the same coordinate frame."},
        "metadata": metadata}
    record_text = json.dumps(record, indent=2) + "\n"
    published = []
    try:
        # Publish the contract first: a visible joined SWC never lacks its units.
        _publish_new_text(sidecar, record_text)
        published.append((sidecar, hashlib.sha256(record_text.encode()).hexdigest()))
        _publish_new_text(output, text)
        published.append((output, digest))
        if sha256_file(source) != plan.source_sha256:
            raise ValueError("SWC changed during repair; rejected new copy removed")
    except Exception:
        for path, expected in reversed(published):
            if path.is_file() and not path.is_symlink() and sha256_file(path) == expected:
                path.unlink()
        raise
    return output
