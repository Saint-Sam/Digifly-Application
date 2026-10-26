"""Explicit edge identity, scope and failure controls for all-backend mapping."""
import copy
import math
from pathlib import Path
import pytest
from digifly_app.core.circuit import CircuitSpec, HodgkinHuxleySpec
from digifly_app.core.mechanisms import membrane_profile
from digifly_app.workers import generic_experiment_worker as worker
from digifly_app.workers.arbor_channel_translation import translate

ROWS = ((1,1,0.,0.,0.,5.,-1), (2,1,0.,0.,10.,5.,1),
        (3,3,0.,0.,50.,2.,2), (4,2,30.,0.,10.,1.,2), (5,2,60.,0.,10.,1.,4))


def test_native_soma_only_models_use_shared_midpoint_mesh():
    circuit = CircuitSpec().to_dict()
    experiment = {"recording":{"target_region":"soma"}, "stimuli":[{"target_region":"soma"}]}
    assert not worker._regional_execution(circuit, experiment)
    circuit["membrane"] = membrane_profile("escape_siz_para_hh_k").to_dict()
    assert worker._regional_execution(circuit, experiment)
    circuit["membrane"] = CircuitSpec().membrane.to_dict()
    circuit["neuron_mechanism_overrides"] = {"a":membrane_profile("escape_siz_para_hh_k").to_dict()}
    assert worker._regional_execution(circuit, experiment)


def cell():
    return {"hh":HodgkinHuxleySpec(active_scope="soma_ais").to_dict(),
            "membrane":membrane_profile("escape_siz_para_hh_k").to_dict(),
            "source_by_run":{str(n):n*10 for n in range(1,6)},
            "compartment_overrides":{"3":{"cm_uF_cm2":1.5,"active_scope":"all"}},
            "compartment_mechanism_overrides":{}}


def test_edge_precedence_scope_and_recording_identity():
    data = cell(); plan = worker._regional_plan(ROWS,data)
    specs = {s["node_id"]:s for s in plan["sections"]}
    assert specs[3]["hh"]["cm_uF_cm2"] == 1.5
    assert specs[4]["hh"]["cm_uF_cm2"] == 1
    assert specs[4]["ais_proxy"] and not specs[5]["ais_proxy"]
    assert specs[4]["hh"]["active_scope"] == "all"
    assert specs[5]["hh"]["active_scope"] == "soma"
    assert {s["source_node_id"] for s in worker._regional_sites(plan,"all",data)} == {20,30,40,50}
    assert [s["source_node_id"] for s in worker._regional_sites(plan,"selected_compartments",data)] == [30]
    assert [s["source_node_id"] for s in worker._regional_sites(plan,"ais",data)] == [40]
    assert [s["source_node_id"] for s in worker._regional_sites(plan,"soma",data)] == [20]
    assert all(s["nseg"] % 2 == 1 for s in plan["sections"])
    assert plan["sections"][specs[5]["parent_section_id"]]["node_id"] == 4
    assert "not anatomical" in plan["ais_policy"]


@pytest.mark.parametrize("mutation, message", [("root","incoming"),("absent","incoming"),("nan","Invalid"),("unknown","Unknown"),("scope","Unknown"),("ais_absent","type-2")])
def test_invalid_edge_maps_fail_closed(mutation,message):
    data = cell(); rows = ROWS
    if mutation in {"root","absent"}:
        data["compartment_overrides"] = {"1" if mutation=="root" else "99":{"cm_uF_cm2":2}}
    elif mutation == "nan": data["compartment_overrides"]["3"]["cm_uF_cm2"] = math.nan
    elif mutation == "unknown": data["compartment_overrides"]["3"]["fictional"] = 2
    elif mutation == "scope": data["compartment_overrides"]["3"]["active_scope"] = "fictional"
    else: rows = ROWS[:3]
    with pytest.raises(ValueError,match=message): worker._regional_plan(rows,data)


def test_source_ids_reindexed_exactly_and_unknown_source_rejected():
    circuit = CircuitSpec(neuron_ids=("a",)).to_dict()
    circuit["compartment_overrides"] = {"a":{"30":{"cm_uF_cm2":2}}}
    data = worker._regional_cell_data(circuit,"a",{10:1,20:2,30:3})
    assert data["compartment_overrides"] == {"3":{"cm_uF_cm2":2}}
    assert data["source_by_run"]["3"] == 30
    with pytest.raises(ValueError,match="Unmapped source"):
        worker._regional_cell_data(circuit,"a",{10:1,20:2})


def test_selected_region_never_falls_back_to_soma():
    data = cell(); data["compartment_overrides"] = {}
    with pytest.raises(ValueError,match="no mapped"):
        worker._regional_sites(worker._regional_plan(ROWS,data),"selected_compartments",data)


@pytest.mark.parametrize('filler_first', [False, True])
def test_root_forks_share_exact_proximal_junction_without_extra_membrane(tmp_path, monkeypatch, filler_first):
    rows = worker._SwcRows([(1,0,0.,0.,0.,1.,-1), (2,3,3.,0.,0.,.8,1),
        (3,1,0.,4.,0.,.9,1), (4,2,0.,8.,0.,.7,3)],
        {'filler_edge_child_ids':[2]} if filler_first else {})
    data = {'hh':HodgkinHuxleySpec().to_dict(), 'membrane':CircuitSpec().membrane.to_dict()}
    plan = worker._regional_plan(rows, data)
    specs = {s['node_id']:s for s in plan['sections']}
    anchor, sibling = (3,2) if filler_first else (2,3)
    assert specs[anchor]['parent_section_id'] is None
    assert specs[sibling]['parent_section_id'] == specs[anchor]['section_id']
    assert specs[sibling]['parent_section_pos'] == 0.
    assert specs[4]['parent_section_id'] == specs[3]['section_id']
    assert specs[4]['parent_section_pos'] == 1.
    assert len(plan['sections']) == 3 and plan['estimated_nseg'] == 3
    assert sum(s['parent_section_id'] is None for s in plan['sections']) == 1
    assert plan['node_sites'][1] == {'section_id':specs[anchor]['section_id'], 'section_pos':0.}
    assert plan['root_junctions'][0]['anchor_edge_node_id'] == anchor
    # Verify the actual constructor consumes the proximal position, not its
    # old hardcoded distal (1.0) attachment. This fake performs no integration.
    path = tmp_path/'root-fork.swc'
    path.write_text('\n'.join(' '.join(map(str,row)) for row in rows)+'\n')
    class Section:
        def __init__(self): self.parent=None
        def __call__(self, x): return self, x
        def connect(self, parent, orientation): self.parent=(parent,orientation)
    class H:
        def Section(self, **kwargs): return Section()
        def pt3dclear(self, **kwargs): pass
        def pt3dadd(self, *args, **kwargs): pass
    monkeypatch.setattr(worker, '_swc_rows', lambda _path: rows)
    monkeypatch.setattr(worker, '_decorate_neuron_section', lambda *args, **kwargs: None)
    built = worker._neuron_regional_cell(H(), path, 'root-fork', data, 100)
    sections = built['sections']
    assert sections[specs[sibling]['section_id']].parent == ((sections[specs[anchor]['section_id']],0.),0)
    assert sections[specs[4]['section_id']].parent == ((sections[specs[3]['section_id']],1.),0)


def test_report_budget_valid_case_and_meaningful_failure():
    plans = {"a":{"estimated_nseg":4},"b":{"estimated_nseg":100}}
    experiment = {"duration_ms":5,"integration_dt_ms":.0125,"repetitions":1,"recording":{"target_neuron_ids":["a"]}}
    assert worker._regional_report_budget(plans,experiment) == 1604
    experiment["duration_ms"] = 100_000
    with pytest.raises(ValueError,match="safety limit"):
        worker._regional_report_budget(plans,experiment)


def test_all_compartment_recording_cannot_finish_beyond_playback_budget():
    plans = {'a': {'estimated_nseg': 1000, 'sections': [{}] * 1000}}
    experiment = {'duration_ms': 6, 'integration_dt_ms': .001, 'repetitions': 1,
        'recording': {'target_region': 'all', 'sample_dt_ms': .001, 'record_voltage': True}}
    with pytest.raises(ValueError, match='playback'):
        worker._regional_report_budget(plans, experiment)
    experiment['recording']['sample_dt_ms'] = .01
    assert worker._regional_report_budget(plans, experiment) == 6_001_000
    experiment['recording'].update(sample_dt_ms=.001, record_voltage=False)
    assert worker._regional_report_budget(plans, experiment) == 6_001_000


def test_contact_mesh_refines_all_incident_edges_without_moving_sites():
    data=cell(); baseline=worker._regional_plan(ROWS,data)
    data['electrical_contact_run_node_ids']=[3,4]
    fine=worker._regional_plan(ROWS,data)
    assert fine['node_sites']==baseline['node_sites']
    assert [s['nseg'] for s in fine['sections']]==[1,17,13,13]
    assert fine['sections'][0]==baseline['sections'][0]
    assert fine['electrical_mesh_policy']['contact_source_node_ids']==[30,40]
    branched=ROWS+((6,2,30.,30.,10.,1.,4),)
    plan=worker._regional_plan(branched,data)
    assert plan['electrical_mesh_policy']['refined_run_node_ids']==[3,4,5,6]
    assert next(s for s in plan['sections'] if s['node_id']==6)['nseg']==13


def test_contact_mapping_and_actual_refined_global_budget_fail_closed():
    edges={'electrical_edges':[{'contacts':[{'pre_id':'a','post_id':'b','pre_source_node_id':30,'post_source_node_id':99}]}]}
    assert worker._electrical_contact_run_nodes(edges,'a',{30:3})==[3]
    with pytest.raises(ValueError,match='missing source'):
        worker._electrical_contact_run_nodes(edges,'b',{30:3})
    data=cell();data['electrical_contact_run_node_ids']=[1]
    with pytest.raises(ValueError,match='non-root'):
        worker._regional_plan(ROWS,data)
    with pytest.raises(ValueError,match='non-root cable'):
        worker._regional_plan(ROWS[:1],data)
    exp={'duration_ms':.01,'integration_dt_ms':.0125,'recording':{'target_neuron_ids':['a']}}
    # A small recorded subset cannot hide the allocation of another cell.
    with pytest.raises(RuntimeError,match='segment safety limit'):
        worker._regional_report_budget({'a':{'estimated_nseg':1},'b':{'estimated_nseg':2_000_000}},exp)


def test_table_grid_is_interpolated_and_unrecognized_table_rejected():
    root = Path(__file__).resolve().parents[1]
    source = (root/"mechanisms/channels/augustin_2019_nap.mod")
    if not source.exists():
        from digifly_app.core.mechanisms import MEMBRANE_MECHANISMS
        definition = next(d for d in MEMBRANE_MECHANISMS if d.key=="augustin_nap")
        source = root/definition.source_relpath
    text = translate("augustin_nap",source.read_text())
    assert "fraction = grid-index" in text
    assert "minf = low_minf+fraction*(minf-low_minf)" in text
    assert "rates(table_v" not in text
    assert "if (v <= vmin)" in text and "if (v >= vmax)" in text
    assert "TABLE" not in text
    with pytest.raises(ValueError,match="Unsupported rate TABLE"):
        translate("augustin_nap",source.read_text().replace("WITH 1600","WITH 20"))
