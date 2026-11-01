"""Real coordinate/cable/CV contract tests; not biological circuit acceptance."""
from dataclasses import replace
import copy
import math

import pytest

from digifly_app.core.contact_mapping import project_contact
from digifly_app.core.morphology import SwcNode, SwcSegment
from digifly_app.core.circuit import CircuitSpec
from digifly_app.workers import generic_experiment_worker as common
from digifly_app.workers import bmtk_bionet_worker as bionet
from test_generic_experiment import _morphology


def test_projection_is_onto_a_real_cable_not_just_the_nearest_node(tmp_path):
    morphology = _morphology(tmp_path)
    site = project_contact(morphology, (2,0,6))
    assert site['source_node_id']==3 and site['source_parent_id']==2
    assert site['source_edge_fraction']==.2
    assert site['projected_coordinate_um']==[0,0,6]
    assert site['measured_coordinate_um']==[2,0,6]
    assert site['projection_distance_um']==2


def test_adding_a_closer_filler_cannot_move_an_existing_contact(tmp_path):
    original = _morphology(tmp_path)
    point = (2.,0.,6.)
    extra = SwcNode(99,3,2,0,5,1,2), SwcNode(100,3,2,0,7,1,99)
    filled = replace(original,nodes=(*original.nodes,*extra),
        segments=(*original.segments,SwcSegment(100,99,(2,0,7),(2,0,5),1,3)),
        filler_metadata={'synthetic_node_ids':[99,100],'filler_edge_child_ids':[99,100]})
    assert project_contact(original,point)==project_contact(filled,point)


def contact(morphology):
    points = {'pre':[1.,0.,6.], 'post':[2.,0.,6.]}
    mappings = {end:project_contact(morphology,point) for end,point in points.items()}
    native = {'source_body_id':1,'target_body_id':2,'pre_synapse_id':123,'post_synapse_id':456,
              'pre_confidence':.9,'post_confidence':.8,
              **{f'{end}_{axis}':v/.008 for end,point in points.items() for axis,v in zip('xyz',point)}}
    return {'contact_id':'contact-1','pre_id':'1','post_id':'2',
        **{f'{end}_source_node_id':mappings[end]['source_node_id'] for end in points},
        **{f'{end}_coordinate_um':points[end] for end in points},
        **{f'{end}_source_edge_fraction':mappings[end]['source_edge_fraction'] for end in points},
        'located_contact':{'schema':'digifly-neuprint-located-contact-v1','native_site':native,
            'coordinate_convention':{'scale_to_um':.008},'mapping':mappings,'electrical_anatomy':False}}


def test_all_backends_resolve_same_local_cv_and_report_discretization_error(tmp_path):
    morphology = _morphology(tmp_path)
    edge = contact(morphology)
    rows = [(n.node_id,n.swc_type,n.x,n.y,n.z,n.radius,n.parent_id) for n in morphology.nodes]
    node_map = {n.node_id:n.node_id for n in morphology.nodes}
    spec = CircuitSpec(neuron_ids=('1','2')).to_dict()
    data = common._regional_cell_data(spec,'1',node_map)
    data['electrical_contact_run_node_ids']=[3]
    plan = common._regional_plan(rows,data)
    details = common._contact_cv_details(edge,'post',plan,rows,node_map)
    section, x = common._regional_contact_site(plan,3,.2)
    assert details['section_id']==section and details['section_pos']==x
    assert x==pytest.approx(1/6)
    assert details['projection_to_cv_distance_um']==pytest.approx(1/6)
    assert details['measured_to_cv_distance_um']==pytest.approx(math.hypot(2,1/6))
    assert common._arbor_gap_contact_locset(plan,'unused',3,edge,'post') == f'(on-components {x:.17g} (tag 1000003))'
    cells={n:{'bmtk_node_id':i,'source_to_run':node_map,'section_plan':plan,
              'normalized_path':morphology.record.swc_path} for i,n in enumerate(('1','2'))}
    resolved=bionet._resolve_edge_manifest({'chemical_edges':[edge]},cells)['chemical_edges'][0]
    assert resolved['bionet_section_id']==section and resolved['bionet_section_pos']==x
    assert resolved['contact_cv']['post']==details
    assert resolved['post_coordinate_um']==edge['post_coordinate_um']


@pytest.mark.parametrize('mutation',['coordinate','fraction','filler','parent','distance'])
def test_changed_contact_mapping_is_refused_before_execution(tmp_path,mutation):
    morphology=_morphology(tmp_path); edge=copy.deepcopy(contact(morphology))
    mapping=edge['located_contact']['mapping']['post']
    if mutation=='coordinate':edge['post_coordinate_um'][0]+=1
    if mutation=='fraction':edge['post_source_edge_fraction']=.8
    if mutation=='filler':mapping['filler_target']=True
    if mutation=='parent':mapping['source_parent_id']=99
    if mutation=='distance':mapping['projection_distance_um']=0
    rows=[(n.node_id,n.swc_type,n.x,n.y,n.z,n.radius,n.parent_id) for n in morphology.nodes]
    node_map={n.node_id:n.node_id for n in morphology.nodes}
    plan=common._regional_plan(rows,common._regional_cell_data(CircuitSpec(neuron_ids=('1','2')).to_dict(),'1',node_map))
    with pytest.raises(ValueError):common._contact_cv_details(edge,'post',plan,rows,node_map)
