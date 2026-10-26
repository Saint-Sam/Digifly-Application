"""Runtime conductance manipulation and actual cell/contact ablation contracts."""
import copy
import json
import math
from dataclasses import replace
from pathlib import Path

import pytest

from digifly_app.core.circuit import CircuitSpec, HodgkinHuxleySpec
from digifly_app.core.experiment import ConditionSpec
from digifly_app.core.mechanisms import membrane_profile
from digifly_app.workers import generic_experiment_worker as common
from digifly_app.workers import bmtk_bionet_worker as bionet


def test_scaling_preserves_source_identity_kinetics_and_override_precedence():
    membrane = membrane_profile("escape_siz_para_hh_k").to_dict()
    cell = {"hh":HodgkinHuxleySpec().to_dict(), "membrane":membrane,
        "compartment_overrides":{"3":{"soma_gnabar_s_cm2":.4,"cm_uF_cm2":2}},
        "compartment_mechanism_overrides":{"3":copy.deepcopy(membrane)}}
    original = copy.deepcopy(cell)
    scaled = common._scale_cell_biophysics(cell, {"para":.5,"hh_na":.25,"hh":.5,"pas":0})
    assert cell == original
    assert scaled["hh"]["soma_gnabar_s_cm2"] == .12*.25*.5
    assert scaled["hh"]["soma_gkbar_s_cm2"] == .036*.5
    assert scaled["hh"]["g_pas_s_cm2"] == 0
    assert scaled["compartment_overrides"]["3"]["soma_gnabar_s_cm2"] == .4*.25*.5
    assert scaled["compartment_overrides"]["3"]["cm_uF_cm2"] == 2
    for payload in (scaled["membrane"], scaled["compartment_mechanism_overrides"]["3"]):
        before=original["membrane"]["channels"]["para"]
        after=payload["channels"]["para"]
        assert after["soma_gbar_s_cm2"] == before["soma_gbar_s_cm2"]*.5
        assert after["branch_gbar_s_cm2"] == before["branch_gbar_s_cm2"]*.5
        assert after["source_sha256"] == before["source_sha256"]
        assert after["parameters"] == before["parameters"]


@pytest.mark.parametrize("condition,message", [
    ({"disabled_neuron_ids":["outside"]},"outside"),
    ({"mechanism_scales":{"fictional":1}},"Unknown"),
    ({"mechanism_scales":{"para":math.nan}},"finite"),
    ({"mechanism_scales":{"hh_k":math.inf}},"finite"),
    ({"mechanism_scales":{"pas":-1}},"finite"),
])
def test_manipulation_rejects_unknown_targets_names_and_nonfinite_scales(condition,message):
    with pytest.raises(ValueError,match=message):
        common._validate_condition_manipulations({"neuron_ids":["a","b"]},condition)


def test_ablation_removes_cells_and_incident_contacts_without_inventing_trace():
    cells={n:{"hh":HodgkinHuxleySpec().to_dict(),"membrane":{}} for n in ("a","b","c")}
    edges={"neuron_ids":list(cells),"chemical_edges":[{"pre_id":"a","post_id":"b"},{"pre_id":"a","post_id":"c"}],
        "electrical_edges":[{"neuron_a":"b","neuron_b":"c","contacts":[{"contact_id":"ab"}]}]}
    condition={"disabled_neuron_ids":["b"],"mechanism_scales":{"hh_na":0}}
    active=common._condition_cells(cells,condition)
    resolved=common._condition_edges(edges,tuple(active))
    assert list(active)==["a","c"]
    assert resolved["chemical_edges"]==[{"pre_id":"a","post_id":"c"}]
    assert resolved["electrical_edges"]==[]
    assert active["a"]["hh"]["soma_gnabar_s_cm2"]==0
    assert cells["a"]["hh"]["soma_gnabar_s_cm2"]==.12
    assert common._condition_cells(cells,{"disabled_neuron_ids":list(cells)})=={}


def test_condition_schema_rejects_nan_and_infinite_multipliers():
    assert ConditionSpec(mechanism_scales={"para":math.nan}).errors()
    assert ConditionSpec(stimulus_scale=math.inf).errors()


def test_bionet_ablation_materializes_reindexed_nodes_and_owned_scaled_dynamics(tmp_path):
    model_root=tmp_path/"sonata/components/biophysical_models"
    model_root.mkdir(parents=True)
    membrane=membrane_profile("escape_siz_para_hh_k").to_dict()
    original={"hh":HodgkinHuxleySpec().to_dict(),"membrane":membrane,"regional_data":None}
    cells={}
    for gid,n in enumerate(("a","b","c")):
        (model_root/f"{n}.json").write_text(json.dumps(original))
        cells[n]={"bmtk_node_id":gid,"safe_key":n,"dynamics_params":f"{n}.json"}
    active=bionet._condition_bionet_cells(cells,{"disabled_neuron_ids":["a"],"mechanism_scales":{"para":.5}},tmp_path,"condition-2")
    assert [c["bmtk_node_id"] for c in active.values()]==[0,1]
    assert cells["b"]["bmtk_node_id"]==1
    written=json.loads((model_root/active["b"]["dynamics_params"]).read_text())
    assert written["membrane"]["channels"]["para"]["soma_gbar_s_cm2"]==membrane["channels"]["para"]["soma_gbar_s_cm2"]*.5
    assert json.loads((model_root/"b.json").read_text())==original


def test_ramp_is_one_continuous_envelope_not_a_pulse_train():
    from digifly_app.core.experiment import StimulusSpec
    stimulus={"waveform":"ramp","delay_ms":1,"pulse_width_ms":2,"pulse_count":10,"frequency_hz":100}
    assert common._pulse_windows(stimulus)==((1,2),)
    assert common._ramp_envelope(stimulus,.3)==[(1,0),(3,.3),(3,0)]
    assert StimulusSpec(waveform="ramp",delay_ms=1,pulse_width_ms=2,pulse_count=10).final_offset_ms==3


def test_neuron_ramp_uses_linear_play_and_keeps_vectors_alive():
    from types import SimpleNamespace
    class Vector(list):
        def play(self,reference,times,continuous):
            self.play_call=(reference,list(times),continuous)
    h=SimpleNamespace(IClamp=lambda site:SimpleNamespace(_ref_amp="amp"),Vector=Vector)
    objects=common._neuron_current_clamps(h,"mapped-site",{"waveform":"ramp","delay_ms":1,"pulse_width_ms":2},.3)
    assert objects[0].delay==1 and objects[0].dur==2
    assert objects[2].play_call==("amp",[0,1,3],1)
    assert list(objects[2])==[0,0,.3]


def test_bionet_ramp_config_preserves_actual_sites_and_ablated_target_sets(tmp_path):
    from test_bmtk_bionet_worker import _request_fixture
    request_path,request,edges,circuit=_request_fixture(tmp_path)
    experiment=request["experiment"]
    experiment["stimuli"][0]["waveform"]="ramp"
    experiment["recording"]["record_voltage"]=False
    experiment["conditions"][1]["disabled_neuron_ids"]=["cell-b"]
    experiment["conditions"][1]["mechanism_scales"]={"hh_k":0}
    bionet._validate_capabilities(circuit,experiment,edges)
    cells,_,_=bionet._prepare_morphologies(request,request_path.parent,edges)
    active=bionet._condition_bionet_cells(cells,experiment["conditions"][1],request_path.parent,"comparison")
    config=bionet._simulation_config(output_dir=request_path.parent,repetition_dir=request_path.parent/"rep",
        circuit_config={"components":{},"networks":{}},cells=active,experiment=experiment,
        stimulus=experiment["stimuli"][0],condition=experiment["conditions"][1],repetition=1,chemical_spike_threshold_mV=0)
    assert config["inputs"]=={}
    assert config["node_sets"]["recording_targets"]["node_id"]==[0]
    assert config["digifly"]["ramp_stimulus"]["sites"]==[{"node_id":0,"neuron_id":"cell/a","section_name":"soma","section_id":0,"section_pos":.5,"amplitude_nA":1}]


def test_generic_production_export_obeys_spike_only_and_empty_ablation(tmp_path,monkeypatch):
    import csv
    import os
    from test_generic_experiment import _morphology,_morphology_hashes,_experiment,_passing_runtime
    from digifly_app.engines import generic_experiment as generic
    from digifly_app.core.connectomes import ConnectomeRef
    morphology=_morphology(tmp_path)
    circuit=CircuitSpec(connectome=ConnectomeRef("test:v1","Test",str(tmp_path)),neuron_ids=("1",),
        morphology_sha256=_morphology_hashes((morphology,)))
    experiment=_experiment()
    experiment=replace(experiment,recording=replace(experiment.recording,record_voltage=False,detect_spikes=True),
        stimuli=(replace(experiment.stimuli[0],waveform="ramp"),),
        conditions=(ConditionSpec(name="Scaled",mechanism_scales={"hh_na":0}),ConditionSpec(name="All ablated",disabled_neuron_ids=("1",))))
    monkeypatch.setattr(generic,"_runtime_check",_passing_runtime)
    adapter=generic.GenericExperimentAdapter({"arbor":os.sys.executable})
    report=adapter.validate(circuit,experiment,(morphology,),output_root=tmp_path/"runs")
    assert report.ok
    plan=adapter.plan(circuit,experiment,output_root=tmp_path/"runs")
    payload=adapter.request_payload(circuit,experiment,(morphology,),report,output_root=tmp_path/"runs")
    request=adapter.write_request(plan.arguments[-1],payload)
    seen=[]
    def fake_simulate(path,hh,*args,**kwargs):
        seen.append(hh)
        return [0,1,2,3],[-65,-65,-10,-65],"fixture"
    monkeypatch.setattr(common,"_arbor_trace",fake_simulate)
    # This is an inert export test, not a simulator/compiler check. Catalogue
    # identity/compilation is exercised separately in the real runtime tests.
    monkeypatch.setattr(common,"_prepare_arbor_channels",lambda *args,**kwargs:{})
    summary=json.loads(common.run(request).read_text())
    assert len(seen)==1 and seen[0]["soma_gnabar_s_cm2"]==0
    assert list(csv.DictReader((request.parent/"voltage_traces.csv").open()))==[]
    spikes=list(csv.DictReader((request.parent/"spikes.csv").open()))
    assert len(spikes)==1 and spikes[0]["condition"]=="Scaled"
    assert not any(a["path"]=="voltage_traces.csv" for a in summary["artifacts"])
    receipt=json.loads((request.parent/"condition_execution.json").read_text())
    assert receipt["conditions"][1]["active_neuron_ids"]==[]


def test_channel_enabled_only_on_a_source_edge_is_included_in_compilation_set():
    circuit=CircuitSpec(neuron_ids=("a",)).to_dict()
    override=membrane_profile("escape_siz_para_hh_k").to_dict()
    circuit["compartment_mechanism_overrides"]={"a":{"3":override}}
    required={key for membrane in common._native_payloads(circuit) for key in common._active_native_channels(membrane)}
    assert "para" in required
