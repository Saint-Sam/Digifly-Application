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
ENDPOINT_POLICY = "adaptive_taper_local_length_and_volume_cap"
AXON_AWARE_POLICY = "adjoining_cable_profile_v1"


def _check_policy(policy):
    if policy not in (ENDPOINT_POLICY, AXON_AWARE_POLICY):
        raise ValueError("Unknown fragment bridge policy")


def _joined_path(source: Path, policy: str) -> Path:
    _check_policy(policy)
    suffix = "_axon_aware_fragment_healed.swc" if policy == AXON_AWARE_POLICY else "_fragment_healed.swc"
    return source.with_name(source.stem + suffix)


def _local_cable_support(endpoint, by_id, adjacency):
    """Three contiguous original edges, taking the thickest continuation.

    This is a deterministic local caliber estimate, not axon identification.
    Never search synthetic cable or optimize against a simulation outcome.
    """
    visited, support, current = {endpoint}, [], endpoint
    for _ in range(3):
        choices = [other for other in adjacency[current] if other not in visited]
        if not choices:
            break
        current = min(choices, key=lambda other: (-by_id[other].radius, other))
        visited.add(current)
        support.append(current)
    radius = statistics.median(by_id[node_id].radius for node_id in support) if support else by_id[endpoint].radius
    return support, radius


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
    policy: str = ENDPOINT_POLICY

    @property
    def added_compartments(self):
        return len(self.filler_nodes) + len(self.parent_changes)

    def to_dict(self):
        volume = sum(b["volume_um3"] for b in self.bridges)
        fields = asdict(self)
        fields.pop("policy")  # Keep historical default-plan serialization exact.
        return {**fields, "added_compartments": self.added_compartments,
                "added_length_um": sum(b["length_um"] for b in self.bridges),
                "added_volume_um3": volume,
                "added_volume_fraction": volume / self.original_volume_um3 if self.original_volume_um3 else None,
                "policy": self.policy,
                "biological_reconstruction": False,
                "original_coordinates_radii_and_real_edges_unchanged": True,
                "filler_biophysics": "Uses the circuit's branch membrane settings; synthetic added cable changes electrical conduction."}


def plan_fragment_join(report: SwcQualityReport, *, policy: str = ENDPOINT_POLICY) -> FragmentJoinPlan:
    _check_policy(policy)
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
        profile = None
        radius_limit = max(first.radius, last.radius)
        if policy == AXON_AWARE_POLICY:
            first_support, first_estimate = _local_cable_support(anchor, by_id, adjacency)
            last_support, last_estimate = _local_cable_support(root, by_id, adjacency)
            # Size and caliber must use the same bounded local support. Using
            # an intact shaft radius with only a collapsed tip's tiny volume
            # cap would manufacture thousands of sub-nanometer subdivisions.
            for endpoint, support in ((anchor, first_support), (root, last_support)):
                previous = endpoint
                for other_id in support:
                    a, b = by_id[previous], by_id[other_id]
                    length = math.dist(point(a), point(b))
                    if length > 0:
                        reference.append((length, _volume(length, a.radius, b.radius)))
                    previous = other_id
        target_length = statistics.median(length for length, _ in reference)
        max_length = max(length for length, _ in reference)
        max_volume = max(volume for _, volume in reference)
        if policy == AXON_AWARE_POLICY:
            transition = min(.25, target_length / distance)
            if transition <= 0 or not math.isfinite(1 / transition):
                raise ValueError("SWC dimensions exceed the finite filler sizing range")
            knots = ((0., first.radius), (transition, first_estimate),
                     (1 - transition, last_estimate), (1., last.radius))
            radius_limit = max(radius for _, radius in knots)
            profile = {"support_node_ids": [first_support, last_support],
                "estimated_radii_um": [first_estimate * scale, last_estimate * scale],
                "transition_length_um": transition * distance * scale,
                "sizing_reference": "Original endpoint edges and the same up-to-three-edge intact support used for caliber; no synthetic reference cable.",
                "rule": "Median radii along up to three original edges, following the thickest continuation; short endpoint transitions. Not recovered axon anatomy."}
        def radius_at(fraction):
            if profile is None:
                return first.radius + fraction * (last.radius - first.radius)
            for (left, a), (right, b) in zip(knots, knots[1:]):
                if fraction <= right:
                    return a + (fraction - left) / (right - left) * (b - a)
            return last.radius
        # A conservative cylinder bound covers every frustum in the taper.
        size_ratio = _volume(distance, radius_limit, radius_limit) / max_volume
        if not all(math.isfinite(value) for value in (distance / target_length, size_ratio)):
            raise ValueError("SWC dimensions exceed the finite filler sizing range")
        count = max(1, math.ceil(distance / target_length), math.ceil(distance / max_length), math.ceil(size_ratio))
        if profile is not None:
            count = max(count, 4, math.ceil(1 / transition))
        if len(added) + len(changes) + count > MAX_FILLER_COMPARTMENTS:
            raise ValueError("The adaptive filler exceeds the 25,000-compartment repair budget; no copy was written")
        parent = anchor
        for ordinal in range(1, count):
            fraction = ordinal / count
            xyz = tuple(a + fraction * (b - a) for a, b in zip(point(first), point(last)))
            radius = radius_at(fraction)
            added.append((next_id, 3, *xyz, radius, parent))
            parent = next_id
            next_id += 1
        changes[root] = parent
        bridge = {"anchor_node_id": anchor, "fragment_root_id": root,
            "compartments": count, "length_um": distance * scale,
            "volume_um3": _volume(distance, first.radius, last.radius) * scale**3,
            "maximum_compartment_length_um": distance / count * scale,
            "reference_maximum_length_um": max_length * scale,
            "maximum_compartment_volume_bound_um3": math.pi * distance / count * radius_limit**2 * scale**3,
            "reference_maximum_volume_um3": max_volume * scale**3,
            "endpoint_radii_um": [first.radius * scale, last.radius * scale]}
        if profile is not None:
            radii = [first.radius] + [radius_at(i / count) for i in range(1, count)] + [last.radius]
            bridge.update(local_profile=profile,
                volume_um3=sum(_volume(distance / count, a, b) for a, b in zip(radii, radii[1:])) * scale**3,
                axial_resistance_MOhm_at_100_ohm_cm=sum(
                    100 * .01 * (distance / count * scale) / (math.pi * a * b * scale**2)
                    for a, b in zip(radii, radii[1:])))
        bridges.append(bridge)
        connected.update(components[root])
        pending.remove(root)
    real_edges = [(n, by_id[n.parent_id]) for n in nodes if n.parent_id >= 0]
    length = sum(math.dist(point(n), point(p)) for n, p in real_edges) * scale
    volume = sum(_volume(math.dist(point(n), point(p)), n.radius, p.radius)
                 for n, p in real_edges) * scale**3
    return FragmentJoinPlan(str(path), report.sha256, contract, roots, changes, tuple(added), tuple(bridges), length, volume, policy)


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


def _joined_text(plan: FragmentJoinPlan):
    source = Path(plan.source_path)
    original_lines, before, errors = _parse_swc(source)
    if errors:
        raise ValueError("SWC no longer parses")
    metadata = {"schema": "digifly-filler-v1",
        "synthetic_node_ids": [int(row[0]) for row in plan.filler_nodes],
        "filler_edge_child_ids": [int(row[0]) for row in plan.filler_nodes] + list(plan.parent_changes),
        "original_root_ids": list(plan.original_roots), "original_source_sha256": plan.source_sha256}
    if plan.policy != ENDPOINT_POLICY:
        metadata["bridge_policy"] = plan.policy
    text = FILLER_HEADER + json.dumps(metadata, separators=(",", ":")) + "\n"
    for line in original_lines:
        fields = line.split()
        if fields and not line.lstrip().startswith("#") and int(fields[0]) in plan.parent_changes:
            fields[6] = str(plan.parent_changes[int(fields[0])])
            line = " ".join(fields) + "\n"
        text += line if line.endswith("\n") else line + "\n"
    for row in plan.filler_nodes:
        text += f"{row[0]} {row[1]} " + " ".join(format(value, ".17g") for value in row[2:6]) + f" {row[6]}\n"
    return text, metadata, before


def _same_reviewed_plan(stored: dict, current: dict) -> bool:
    """Geometry remains bit-exact; specified display summaries allow roundoff.

    Compiled and interpreted accumulation over thousands of real edges can
    differ by a few final bits. These report-only totals do not determine the
    filler coordinates, radii, parent changes or local caps, which remain exact.
    Full original and output hashes are independently required by the caller.
    """
    reports = {'original_length_um','original_volume_um3','added_volume_fraction'}
    local_profile = current.get('policy') == AXON_AWARE_POLICY
    if local_profile:
        reports.add('added_volume_um3')
    if set(stored)!=set(current):
        return False
    def close_report(a,b):
        if isinstance(a,bool) or isinstance(b,bool):return False
        if a==b:return True
        return not (not isinstance(a,(int,float)) or not isinstance(b,(int,float))
                or not math.isfinite(a) or not math.isfinite(b)
                or not math.isclose(a,b,rel_tol=1e-12,abs_tol=0))
    for name in reports:
        if not close_report(stored[name],current[name]):
            return False
    if local_profile:
        a,b=stored['bridges'],current['bridges']
        if not isinstance(a,(list,tuple)) or not isinstance(b,(list,tuple)) or len(a)!=len(b):return False
        bridge_reports={'volume_um3','axial_resistance_MOhm_at_100_ohm_cm'}
        for left,right in zip(a,b):
            if set(left)!=set(right) or not bridge_reports<=left.keys():return False
            if any(not close_report(left[key],right[key]) for key in bridge_reports):return False
            if {k:v for k,v in left.items() if k not in bridge_reports}!={k:v for k,v in right.items() if k not in bridge_reports}:return False
        reports.add('bridges')
    return {k:v for k,v in stored.items() if k not in reports}=={k:v for k,v in current.items() if k not in reports}


def verified_fragment_source(path: Path, committed_hashes: dict[str, str]) -> Path | None:
    """Admit only the exact reviewed derivative of a committed library SWC.

    The raw library inventory stays immutable. An unlisted file is not made
    visible merely by its name: its source, deterministic plan, complete bytes
    and unit contract must still match the separately saved review record.
    """
    if not path.name.endswith("_fragment_healed.swc"):
        return None
    sidecar = path.with_suffix(".swc.quality.json")
    if path.is_symlink() or sidecar.is_symlink() or not sidecar.is_file():
        raise ValueError("Joined copy lacks its regular review record")
    record = json.loads(sidecar.read_text(encoding="utf-8"))
    source = Path(record["plan"]["source_path"])
    policy = record["plan"].get("policy", ENDPOINT_POLICY)
    expected = _joined_path(source, policy)
    key = os.path.normcase(os.path.abspath(source))
    if (record.get("schema") != "digifly-fragment-join-v1" or path != expected
            or source.is_symlink() or key not in committed_hashes
            or sha256_file(source) != committed_hashes[key]):
        raise ValueError("Joined copy is not bound to this committed source")
    plan = plan_fragment_join(analyze_swc(source), policy=policy)
    if not _same_reviewed_plan(record["plan"],json.loads(json.dumps(plan.to_dict()))):
        raise ValueError("Joined copy's adaptive plan differs from its source")
    text, metadata, _before = _joined_text(plan)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    transform = {**plan.coordinate_transform, "source_sha256": digest,
        "geometry_policy": "Original geometry and real edges preserved; explicit synthetic filler edges added in the same coordinate frame."}
    if (record.get("output_sha256") != digest or sha256_file(path) != digest
            or record.get("metadata") != metadata
            or record.get("coordinate_transform") != transform):
        raise ValueError("Joined copy bytes or physical-unit record changed after review")
    return source


def original_for_bridge_review(path: Path) -> Path:
    """Resolve a loaded reviewed copy without treating its filler as anatomy."""
    path = path.resolve()
    if not path.name.endswith("_fragment_healed.swc"):
        return path
    record = json.loads(path.with_suffix(".swc.quality.json").read_text(encoding="utf-8"))
    source = Path(record["plan"]["source_path"])
    key = os.path.normcase(os.path.abspath(source))
    verified = verified_fragment_source(path, {key: record["plan"]["source_sha256"]})
    if verified is None:
        raise ValueError("The loaded bridge copy has no verified original")
    return verified


def save_fragment_join(plan: FragmentJoinPlan) -> Path:
    source = Path(plan.source_path)
    if sha256_file(source) != plan.source_sha256:
        raise ValueError("SWC changed after the filler preview; review it again")
    if plan != plan_fragment_join(analyze_swc(source), policy=plan.policy):
        raise ValueError("Filler plan differs from the verified adaptive sizing policy; preview it again")
    output = _joined_path(source, plan.policy)
    sidecar = output.with_suffix(".swc.quality.json")
    if os.path.lexists(output) or os.path.lexists(sidecar):
        raise FileExistsError("A joined copy already exists; the original or prior copy will not be replaced")
    text, metadata, before = _joined_text(plan)
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
