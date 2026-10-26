from __future__ import annotations

import csv
import hashlib
import json
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
import heapq
from math import isfinite, sqrt
from pathlib import Path
from typing import Iterable, Mapping

from digifly_app.core.morphology import Morphology, locate_soma


FLOW_SPEED_UM_PER_MS = 25.0
# Keep the visible band shorter than a 100 Hz inter-pulse interval so repeated
# spikes remain visibly distinct instead of leaving the entire arbor lit.
FLOW_TAIL_MS = 1.5


class SegmentDistanceMap(dict[int, float]):
    """Path distances with a sorted index for interactive large-circuit playback."""

    def __init__(self, values: Mapping[int, float]):
        super().__init__(values)
        ordered = tuple(sorted((float(distance), int(node_id)) for node_id, distance in values.items()))
        self.ordered_distances = tuple(item[0] for item in ordered)
        self.ordered_node_ids = tuple(item[1] for item in ordered)


@dataclass(frozen=True)
class ActivityFlowTrack:
    """One saved condition/repetition timeline and its per-neuron soma spikes."""

    condition: str
    repetition: int
    frame_times_ms: tuple[float, ...]
    spikes_by_neuron: Mapping[str, tuple[float, ...]]
    show_repetition: bool = False

    @property
    def label(self) -> str:
        if self.show_repetition:
            return f"{self.condition} · repetition {self.repetition}"
        return self.condition


@dataclass(frozen=True)
class MorphologyVoltageTrack:
    """Measured central-edge voltages, keyed by run-packaged SWC child ID."""

    condition: str
    repetition: int
    frame_times_ms: tuple[float, ...]
    samples_by_segment: Mapping[tuple[str, int], tuple[tuple[float, float], ...]]
    show_repetition: bool = False

    @property
    def label(self) -> str:
        return f"{self.condition} · repetition {self.repetition}" if self.show_repetition else self.condition


def load_morphology_voltage_tracks(
    run_root: Path,
    morphologies: Iterable[Morphology],
    *,
    maximum_frames: int = 240,
    maximum_samples: int = 5_000_000,
) -> tuple[MorphologyVoltageTrack, ...]:
    """Bind source-edge CSV IDs to displayed run edges; never spread soma data.

    Malformed, ambiguous or missing identities reject this view. Older soma-only
    CSVs deliberately remain available only as traces/inferred flow.
    """
    run_root = Path(run_root)
    voltage_path = run_root / "voltage_traces.csv"
    if not voltage_path.is_file():
        return ()
    items = tuple(morphologies)
    visible = {m.record.neuron_id: {s.child_id for s in m.segments} for m in items}
    displayed_paths = {m.record.neuron_id: Path(m.record.swc_path).resolve() for m in items}
    map_paths = tuple((run_root / "morphologies").rglob("regional_map.json"))
    if not map_paths:
        return ()
    manifest = json.loads((run_root / "run_manifest.json").read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("Recorded run manifest must be an object")
    morphology_metadata = manifest.get("morphologies") or {}
    edge_maps: dict[str, dict[int, int]] = {}
    for path in map_paths:
        plan = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(plan, dict):
            raise ValueError("Recorded regional map must be an object")
        neuron = str(plan.get("neuron_id") or "")
        if neuron not in visible or neuron in edge_maps:
            raise ValueError("Ambiguous or unknown recorded morphology identity")
        normalized = (path.parent / "normalized_input.swc").resolve()
        expected = morphology_metadata.get(neuron, {}).get("simulation_swc_sha256")
        if (normalized != displayed_paths[neuron] or not expected
                or hashlib.sha256(normalized.read_bytes()).hexdigest() != expected):
            raise ValueError("Recorded morphology voltage geometry identity changed or is missing")
        with (path.parent / "morphology_node_map.csv").open(encoding="utf-8", newline="") as handle:
            normalization = {}
            normalized_nodes = set()
            for row in csv.DictReader(handle):
                source, node = int(row["source_node_id"]), int(row["run_node_id"])
                if source in normalization or node in normalized_nodes:
                    raise ValueError("Ambiguous normalization edge map")
                normalization[source] = node
                normalized_nodes.add(node)
        mapping = {}
        mapped_nodes = set()
        for section in plan["sections"]:
            source, node = int(section["source_node_id"]), int(section["node_id"])
            if (source in mapping or node not in visible[neuron]
                    or node in mapped_nodes or normalization.get(source) != node):
                raise ValueError("Invalid source-edge to displayed-edge voltage mapping")
            mapping[source] = node
            mapped_nodes.add(node)
        edge_maps[neuron] = mapping
    if not edge_maps:
        return ()
    grouped: dict[tuple[str, int], dict[tuple[str, int], dict[float, float]]] = {}
    with voltage_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if "compartment_id" not in (reader.fieldnames or ()):
            return ()
        for count, row in enumerate(reader, 1):
            if count > maximum_samples:
                raise ValueError("Morphology voltage display exceeds its sample budget")
            source = row.get("compartment_id")
            if not source:
                raise ValueError("Measured morphology voltage requires explicit edge IDs")
            neuron = str(row["neuron_id"])
            try:
                node = edge_maps[neuron][int(source)]
                t, v = float(row["time_ms"]), float(row["voltage_mV"])
                repetition = int(row.get("repetition") or 0)
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("Unmapped or invalid compartment voltage sample") from exc
            if not isfinite(t) or not isfinite(v) or t < 0.0:
                raise ValueError("Non-finite compartment voltage sample")
            key = (str(row.get("condition") or "Recorded run"), repetition)
            samples = grouped.setdefault(key, {}).setdefault((neuron, node), {})
            if t in samples:
                raise ValueError("Duplicate compartment voltage timestamp")
            samples[t] = v
    repetitions: dict[str, set[int]] = {}
    for condition, repetition in grouped:
        repetitions.setdefault(condition, set()).add(repetition)
    return tuple(
        MorphologyVoltageTrack(condition, repetition,
            _downsample((t for values in segments.values() for t in values), max(2, maximum_frames)),
            {key: tuple(sorted(values.items())) for key, values in segments.items()},
            len(repetitions[condition]) > 1)
        for (condition, repetition), segments in grouped.items()
    )


def measured_segment_voltages(track: MorphologyVoltageTrack, time_ms: float) -> dict[str, dict[int, float]]:
    """Interpolate in observed support only; absent edges/times stay uncolored."""
    result: dict[str, dict[int, float]] = {}
    for (neuron, node), samples in track.samples_by_segment.items():
        if not samples or time_ms < samples[0][0] or time_ms > samples[-1][0]:
            continue
        index = bisect_left(samples, (time_ms, float("-inf")))
        if index < len(samples) and samples[index][0] == time_ms:
            voltage = samples[index][1]
        else:
            t0, v0 = samples[index-1]
            t1, v1 = samples[index]
            voltage = v0 + (v1-v0) * (time_ms-t0) / (t1-t0)
        result.setdefault(neuron, {})[node] = voltage
    return result


def _downsample(values: Iterable[float], maximum: int) -> tuple[float, ...]:
    ordered = tuple(sorted(set(float(value) for value in values)))
    if len(ordered) <= maximum:
        return ordered
    last = len(ordered) - 1
    indices = tuple(round(index * last / (maximum - 1)) for index in range(maximum))
    return tuple(ordered[index] for index in indices)


def _repetition(value: object) -> int:
    try:
        return max(0, int(float(str(value or 0))))
    except (TypeError, ValueError):
        return 0


def load_activity_flow_tracks(
    run_root: Path,
    *,
    maximum_frames: int = 240,
) -> tuple[ActivityFlowTrack, ...]:
    """Load playback tracks from generic experiment voltage/spike CSV artifacts."""

    voltage_path = Path(run_root) / "voltage_traces.csv"
    try:
        experiment = json.loads((Path(run_root) / "experiment.json").read_text())
        recording = experiment.get("recording", {})
        explicit_spike_policy = isinstance(recording, dict) and isinstance(recording.get("detect_spikes"), bool)
        duration = float(experiment.get("duration_ms", 0))
        duration = duration if isfinite(duration) and duration > 0 else None
    except (OSError, ValueError, TypeError, AttributeError):
        recording, explicit_spike_policy, duration = {}, False, None

    times: dict[tuple[str, int], list[float]] = {}
    traces: dict[tuple[str, int, str], list[tuple[float, float]]] = {}
    # Regional recordings identify edges, not whole-cell soma voltage. Use
    # only the explicit soma-edge map for the older inferred-soma-flow view.
    soma_edges = {}
    for map_path in (Path(run_root)/"morphologies").rglob("regional_map.json"):
        try:
            plan = json.loads(map_path.read_text())
            source_id = str(next(s["source_node_id"] for s in plan["sections"] if s["is_target_soma"]))
            if plan.get("neuron_id"):
                soma_edges[str(plan["neuron_id"])] = source_id
                continue
            request = json.loads((Path(run_root)/"worker_request.json").read_text())
            # Both workers store the biological ID in morphology metadata.
            manifest = json.loads((Path(run_root)/"run_manifest.json").read_text())
            for neuron_id, meta in manifest.get("morphologies", {}).items():
                component = meta.get("run_path_component") or meta.get("safe_key")
                if component == map_path.parent.name:
                    soma_edges[str(neuron_id)] = source_id
            if len(request.get("circuit", {}).get("neuron_ids", ())) == 1:
                soma_edges[str(request["circuit"]["neuron_ids"][0])] = source_id
        except (OSError, ValueError, KeyError, StopIteration):
            continue  # Unknown mapping must not be treated as soma.
    if voltage_path.is_file():
        with voltage_path.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                try:
                    time_ms = float(row.get("time_ms", ""))
                    voltage_mV = float(row.get("voltage_mV", ""))
                except (TypeError, ValueError):
                    continue
                condition = str(row.get("condition") or "Recorded run").strip()
                repetition = _repetition(row.get("repetition"))
                neuron_id = str(row.get("neuron_id") or "").strip()
                if not neuron_id or not isfinite(time_ms) or not isfinite(voltage_mV) or time_ms < 0:
                    continue
                key = (condition, repetition)
                times.setdefault(key, []).append(time_ms)
                compartment_id = str(row.get("compartment_id") or "")
                if compartment_id and compartment_id != soma_edges.get(neuron_id):
                    continue
                traces.setdefault((condition, repetition, neuron_id), []).append(
                    (time_ms, voltage_mV)
                )

    spikes: dict[tuple[str, int], dict[str, list[float]]] = {}
    spike_path = Path(run_root) / "spikes.csv"
    if spike_path.is_file() and not (explicit_spike_policy and recording["detect_spikes"] is False):
        with spike_path.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                try:
                    spike_time = float(row.get("spike_time_ms", ""))
                except (TypeError, ValueError):
                    continue
                if not isfinite(spike_time) or spike_time < 0 or (duration is not None and spike_time > duration):
                    continue
                condition = str(row.get("condition") or "Recorded run").strip()
                repetition = _repetition(row.get("repetition"))
                neuron_id = str(row.get("neuron_id") or "").strip()
                compartment_id = str(row.get("compartment_id") or "")
                if compartment_id and compartment_id != soma_edges.get(neuron_id):
                    continue
                if neuron_id:
                    spikes.setdefault((condition, repetition), {}).setdefault(
                        neuron_id, []
                    ).append(spike_time)

    # Older compatible runs may have voltage traces but no separate spike table.
    # Recover the same upward 0 mV crossings used by Digifly's former viewer.
    for (condition, repetition, neuron_id), samples in traces.items():
        if explicit_spike_policy:
            continue  # An explicit disabled/zero-event export is authoritative.
        neuron_spikes = spikes.setdefault((condition, repetition), {}).setdefault(
            neuron_id, []
        )
        if neuron_spikes:
            continue
        ordered = sorted(samples)
        for (t0, v0), (t1, v1) in zip(ordered, ordered[1:]):
            if v0 < 0.0 <= v1:
                denominator = v1 - v0
                neuron_spikes.append(
                    t1 if abs(denominator) < 1e-12 else t0 + (-v0 / denominator) * (t1 - t0)
                )

    for key, neuron_events in spikes.items():
        if times.get(key):
            continue
        # This is a display clock for real saved soma events, not voltage
        # samples or a measured propagation velocity. No voltage is invented.
        event_times = sorted({t for values in neuron_events.values() for t in values})
        if not event_times:
            continue
        frames = max(2, int(maximum_frames))
        grid_count = max(2, frames - len(event_times))
        display_clock = ([duration * i / (grid_count - 1) for i in range(grid_count)]
                         if duration is not None else [])
        times[key] = event_times + display_clock

    repetitions_per_condition: dict[str, set[int]] = {}
    for condition, repetition in times:
        repetitions_per_condition.setdefault(condition, set()).add(repetition)
    tracks: list[ActivityFlowTrack] = []
    for key, frame_values in times.items():
        condition, repetition = key
        frame_times = _downsample(frame_values, max(2, int(maximum_frames)))
        if not frame_times:
            continue
        tracks.append(
            ActivityFlowTrack(
                condition=condition,
                repetition=repetition,
                frame_times_ms=frame_times,
                spikes_by_neuron={
                    neuron_id: tuple(sorted(values))
                    for neuron_id, values in spikes.get(key, {}).items()
                },
                show_repetition=len(repetitions_per_condition.get(condition, ())) > 1,
            )
        )
    return tuple(tracks)


def segment_distances_from_soma(morphology: Morphology) -> SegmentDistanceMap:
    """Return graph-path distance from the displayed soma to each SWC child node."""

    positions = {
        node.node_id: (node.x, node.y, node.z) for node in morphology.nodes
    }
    adjacency: dict[int, list[tuple[int, float]]] = {
        node_id: [] for node_id in positions
    }
    for segment in morphology.segments:
        positions.setdefault(segment.parent_id, segment.parent)
        positions.setdefault(segment.child_id, segment.child)
        dx = segment.child[0] - segment.parent[0]
        dy = segment.child[1] - segment.parent[1]
        dz = segment.child[2] - segment.parent[2]
        length = sqrt(dx * dx + dy * dy + dz * dz)
        adjacency.setdefault(segment.parent_id, []).append((segment.child_id, length))
        adjacency.setdefault(segment.child_id, []).append((segment.parent_id, length))
    source = locate_soma(morphology).node_id
    if source is None or source not in positions:
        source = next(iter(positions), None)
    if source is None:
        return SegmentDistanceMap({})

    distances = {source: 0.0}
    pending = [(0.0, source)]
    while pending:
        distance, node_id = heapq.heappop(pending)
        if distance > distances.get(node_id, float("inf")):
            continue
        for neighbor, edge_length in adjacency.get(node_id, ()):
            candidate = distance + edge_length
            if candidate < distances.get(neighbor, float("inf")):
                distances[neighbor] = candidate
                heapq.heappush(pending, (candidate, neighbor))
    return SegmentDistanceMap(
        {
            segment.child_id: distances[segment.child_id]
            for segment in morphology.segments
            if segment.child_id in distances
        }
    )


def active_spiking_somas(
    track: ActivityFlowTrack,
    time_ms: float,
    *,
    tail_ms: float = FLOW_TAIL_MS,
) -> set[str]:
    """Return soma IDs whose recorded spike is active in the current frame."""

    tail = max(0.0, float(tail_ms))
    return {
        str(neuron_id)
        for neuron_id, spike_times in track.spikes_by_neuron.items()
        if any(0.0 <= float(time_ms) - float(spike_time) <= tail for spike_time in spike_times)
    }


def active_flow_segments(
    track: ActivityFlowTrack,
    time_ms: float,
    distances_by_neuron: Mapping[str, Mapping[int, float]],
    *,
    speed_um_per_ms: float = FLOW_SPEED_UM_PER_MS,
    tail_ms: float = FLOW_TAIL_MS,
) -> dict[str, set[int]]:
    """Find the causal moving band behind every inferred spike wavefront."""

    speed = max(1e-6, float(speed_um_per_ms))
    tail = max(0.0, float(tail_ms))
    active: dict[str, set[int]] = {}
    for neuron_id, distances in distances_by_neuron.items():
        spike_times = track.spikes_by_neuron.get(str(neuron_id), ())
        if not spike_times:
            continue
        if isinstance(distances, SegmentDistanceMap):
            wanted: set[int] = set()
            ordered_distances = distances.ordered_distances
            for spike_time in spike_times:
                elapsed = float(time_ms) - float(spike_time)
                if elapsed < 0.0:
                    continue
                low = max(0.0, (elapsed - tail) * speed)
                high = elapsed * speed
                start = bisect_left(ordered_distances, low)
                stop = bisect_right(ordered_distances, high)
                wanted.update(distances.ordered_node_ids[start:stop])
        else:
            wanted = {
                child_id
                for child_id, distance in distances.items()
                if any(
                    0.0 <= float(time_ms) - (spike_time + distance / speed) <= tail
                    for spike_time in spike_times
                )
            }
        if wanted:
            active[str(neuron_id)] = wanted
    return active
