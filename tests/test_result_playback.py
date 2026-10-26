from __future__ import annotations

from pathlib import Path
import json
import hashlib
from dataclasses import replace

import pytest

from digifly_app.core.connectomes import NeuronRecord
from digifly_app.core.morphology import Morphology, SwcNode, SwcSegment
from digifly_app.ui.result_playback import (
    ActivityFlowTrack,
    active_flow_segments,
    active_spiking_somas,
    load_activity_flow_tracks,
    segment_distances_from_soma,
    load_morphology_voltage_tracks,
    measured_segment_voltages,
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


def test_regional_edges_do_not_become_fake_soma_activity(tmp_path):
    folder=tmp_path/"morphologies/cell-a";folder.mkdir(parents=True)
    (folder/"regional_map.json").write_text(json.dumps({"neuron_id":"a","sections":[{"source_node_id":2,"is_target_soma":True}]}))
    (tmp_path/"voltage_traces.csv").write_text("condition,repetition,neuron_id,compartment_id,time_ms,voltage_mV\nControl,1,a,2,0,-60\nControl,1,a,3,0,-60\nControl,1,a,2,1,-45\nControl,1,a,3,1,20\n")
    (tmp_path/"spikes.csv").write_text("condition,repetition,neuron_id,compartment_id,spike_time_ms\nControl,1,a,3,.5\n")
    track=load_activity_flow_tracks(tmp_path)[0]
    assert track.frame_times_ms==(0.,1.)
    assert track.spikes_by_neuron["a"]==()


@pytest.mark.parametrize('voltage_file', [True, False])
def test_spike_only_results_use_saved_window_without_inventing_voltage(tmp_path, voltage_file):
    folder=tmp_path/'morphologies/cell-a';folder.mkdir(parents=True)
    (folder/'regional_map.json').write_text(json.dumps({'neuron_id':'a','sections':[
        {'source_node_id':2,'is_target_soma':True}]}))
    if voltage_file:
        (tmp_path/'voltage_traces.csv').write_text('condition,repetition,neuron_id,compartment_id,time_ms,voltage_mV\n')
    (tmp_path/'experiment.json').write_text(json.dumps({'duration_ms':4,
        'recording':{'record_voltage':False,'detect_spikes':True}}))
    (tmp_path/'spikes.csv').write_text('condition,repetition,neuron_id,compartment_id,spike_time_ms\n'
        'Control,1,a,2,1.3\nControl,1,a,3,2.0\nControl,1,a,2,nan\nControl,1,a,2,-1\n')
    track,=load_activity_flow_tracks(tmp_path,maximum_frames=40)
    assert track.spikes_by_neuron=={'a':(1.3,)}
    assert track.frame_times_ms[0]==0 and track.frame_times_ms[-1]==4
    assert 1.3 in track.frame_times_ms and len(track.frame_times_ms)<=40
    assert active_spiking_somas(track,1.3)=={'a'}


@pytest.mark.parametrize('detect_spikes', [False, True])
def test_explicit_saved_spike_policy_never_infers_extra_events(tmp_path,detect_spikes):
    (tmp_path/'experiment.json').write_text(json.dumps({'duration_ms':2,
        'recording':{'record_voltage':True,'detect_spikes':detect_spikes}}))
    (tmp_path/'voltage_traces.csv').write_text('condition,repetition,neuron_id,time_ms,voltage_mV\n'
        'Control,1,a,0,-60\nControl,1,a,1,20\nControl,1,a,2,-40\n')
    (tmp_path/'spikes.csv').write_text('condition,repetition,neuron_id,spike_time_ms\n')
    track,=load_activity_flow_tracks(tmp_path)
    assert not any(track.spikes_by_neuron.values())


def _measured_run(tmp_path):
    folder = tmp_path / "morphologies/cell"
    folder.mkdir(parents=True)
    swc = folder / "normalized_input.swc"
    swc.write_text("1 1 0 0 0 2 -1\n2 3 3 0 0 .5 1\n3 3 3 4 0 .4 2\n")
    (tmp_path / "run_manifest.json").write_text(json.dumps({"morphologies":{"10000":{"simulation_swc_sha256":hashlib.sha256(swc.read_bytes()).hexdigest()}}}))
    mapping = {"neuron_id": "10000", "sections": [
        {"source_node_id": 20, "node_id": 2},
        {"source_node_id": 30, "node_id": 3}]}
    (folder / "regional_map.json").write_text(json.dumps(mapping))
    (folder / "morphology_node_map.csv").write_text("source_node_id,run_node_id\n10,1\n20,2\n30,3\n")
    (tmp_path / "voltage_traces.csv").write_text(
        "condition,repetition,neuron_id,compartment_id,time_ms,voltage_mV\n"
        "Control,0,10000,20,1,-60\nControl,0,10000,20,3,20\n"
        "Control,0,10000,30,2,-45\nControl,0,10000,30,4,-30\n")
    return folder


def _recorded_morphology(tmp_path):
    morphology = _linear_morphology()
    return replace(morphology, record=replace(morphology.record, swc_path=str(tmp_path/"morphologies/cell/normalized_input.swc")))


def test_measured_voltage_binds_original_ids_to_run_edges_without_extrapolation(tmp_path):
    _measured_run(tmp_path)
    track = load_morphology_voltage_tracks(tmp_path, [_recorded_morphology(tmp_path)])[0]
    assert track.frame_times_ms == (1., 2., 3., 4.)
    assert measured_segment_voltages(track, 1.) == {"10000": {2: -60.}}
    assert measured_segment_voltages(track, 2.) == {"10000": {2: -20., 3: -45.}}
    assert measured_segment_voltages(track, 4.) == {"10000": {3: -30.}}
    assert measured_segment_voltages(track, .5) == {}
    assert measured_segment_voltages(track, 5.) == {}


@pytest.mark.parametrize("fault", ["missing", "unknown", "changed", "geometry", "duplicate", "nonfinite", "negative", "budget"])
def test_measured_voltage_rejects_invalid_map_or_samples(tmp_path, fault):
    folder = _measured_run(tmp_path)
    if fault == "missing":
        (folder / "regional_map.json").unlink()
        assert load_morphology_voltage_tracks(tmp_path, [_recorded_morphology(tmp_path)]) == ()
        return
    if fault in {"unknown", "changed"}:
        plan = json.loads((folder / "regional_map.json").read_text())
        if fault == "unknown":
            plan["sections"][0]["node_id"] = 999
        else:
            plan["sections"][0]["source_node_id"], plan["sections"][1]["source_node_id"] = 30, 20
        (folder / "regional_map.json").write_text(json.dumps(plan))
    elif fault == "geometry":
        (folder / "normalized_input.swc").write_text("1 1 100 0 0 2 -1\n2 3 110 0 0 1 1\n3 3 120 0 0 1 2\n")
    elif fault in {"duplicate", "nonfinite", "negative"}:
        with (tmp_path / "voltage_traces.csv").open("a") as handle:
            handle.write("Control,0,10000,20,1,-60\n" if fault == "duplicate" else "Control,0,10000,20,-1,-60\n" if fault == "negative" else "Control,0,10000,20,5,nan\n")
    with pytest.raises(ValueError):
        load_morphology_voltage_tracks(tmp_path, [_recorded_morphology(tmp_path)], maximum_samples=1 if fault == "budget" else 100)
