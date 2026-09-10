from __future__ import annotations

import csv
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
import heapq
from math import sqrt
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
    if not voltage_path.is_file():
        return ()

    times: dict[tuple[str, int], list[float]] = {}
    traces: dict[tuple[str, int, str], list[tuple[float, float]]] = {}
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
            if not neuron_id:
                continue
            key = (condition, repetition)
            times.setdefault(key, []).append(time_ms)
            traces.setdefault((condition, repetition, neuron_id), []).append(
                (time_ms, voltage_mV)
            )

    spikes: dict[tuple[str, int], dict[str, list[float]]] = {}
    spike_path = Path(run_root) / "spikes.csv"
    if spike_path.is_file():
        with spike_path.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                try:
                    spike_time = float(row.get("spike_time_ms", ""))
                except (TypeError, ValueError):
                    continue
                condition = str(row.get("condition") or "Recorded run").strip()
                repetition = _repetition(row.get("repetition"))
                neuron_id = str(row.get("neuron_id") or "").strip()
                if neuron_id:
                    spikes.setdefault((condition, repetition), {}).setdefault(
                        neuron_id, []
                    ).append(spike_time)

    # Older compatible runs may have voltage traces but no separate spike table.
    # Recover the same upward 0 mV crossings used by Digifly's former viewer.
    for (condition, repetition, neuron_id), samples in traces.items():
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
