from __future__ import annotations

from pathlib import Path

import pytest

from digifly_app.core.connectomes import NeuronRecord
from digifly_app.core.morphology import Morphology, SwcNode, SwcSegment
from digifly_app.ui.result_playback import (
    ActivityFlowTrack,
    active_flow_segments,
    active_spiking_somas,
    load_activity_flow_tracks,
    segment_distances_from_soma,
)


def _linear_morphology() -> Morphology:
    record = NeuronRecord(
        neuron_id="10000",
        family="MN",
        neuron_type="Example",
        swc_path="/tmp/example.swc",
        connectome_key="manc:v1.2.1",
    )
    nodes = (
        SwcNode(1, 1, 0.0, 0.0, 0.0, 2.0, -1),
        SwcNode(2, 3, 3.0, 0.0, 0.0, 0.5, 1),
        SwcNode(3, 3, 3.0, 4.0, 0.0, 0.4, 2),
    )
    segments = (
        SwcSegment(2, 1, (3.0, 0.0, 0.0), (0.0, 0.0, 0.0), 0.5, 3),
        SwcSegment(3, 2, (3.0, 4.0, 0.0), (3.0, 0.0, 0.0), 0.4, 3),
    )
    return Morphology(record, nodes, segments, (0.0, 3.0, 0.0, 4.0, 0.0, 0.0))


def test_activity_flow_moves_outward_by_swc_graph_distance():
    morphology = _linear_morphology()
    distances = segment_distances_from_soma(morphology)
    assert distances == {2: 3.0, 3: 7.0}
    track = ActivityFlowTrack("Control", 0, (0.0, 20.0), {"10000": (10.0,)})

    near_soma = active_flow_segments(
        track,
        13.5,
        {"10000": distances},
        speed_um_per_ms=1.0,
        tail_ms=1.0,
    )
    farther_out = active_flow_segments(
        track,
        17.5,
        {"10000": distances},
        speed_um_per_ms=1.0,
        tail_ms=1.0,
    )
    assert near_soma == {"10000": {2}}
    assert farther_out == {"10000": {3}}
    assert active_spiking_somas(track, 10.5, tail_ms=1.0) == {"10000"}
    assert active_spiking_somas(track, 12.0, tail_ms=1.0) == set()


def test_load_activity_tracks_uses_saved_conditions_spikes_and_voltage_fallback(
    tmp_path: Path,
):
    (tmp_path / "voltage_traces.csv").write_text(
        "condition,repetition,neuron_id,time_ms,voltage_mV\n"
        "Control,0,10000,0,-60\n"
        "Control,0,10000,1,20\n"
        "Control,0,10000,2,-40\n"
        "Gap disabled,0,10000,0,-60\n"
        "Gap disabled,0,10000,1,-40\n"
        "Gap disabled,0,10000,2,20\n",
        encoding="utf-8",
    )
    (tmp_path / "spikes.csv").write_text(
        "condition,repetition,neuron_id,spike_time_ms\n"
        "Control,0,10000,0.75\n",
        encoding="utf-8",
    )

    tracks = load_activity_flow_tracks(tmp_path)
    assert tuple(track.label for track in tracks) == ("Control", "Gap disabled")
    assert tracks[0].frame_times_ms == (0.0, 1.0, 2.0)
    assert tracks[0].spikes_by_neuron["10000"] == (0.75,)
    assert tracks[1].spikes_by_neuron["10000"] == pytest.approx((5.0 / 3.0,))
