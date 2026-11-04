"""Acceleration must preserve the exhaustive anatomical mapping contract."""
from dataclasses import replace
import math
import random
from types import SimpleNamespace

import pytest

from digifly_app.core.contact_mapping import ContactProjector, project_contact
from digifly_app.core.morphology import SwcSegment


def morphology(edges, metadata=None):
    return SimpleNamespace(segments=tuple(edges), filler_metadata=metadata)


def edge(child, parent, start, end):
    return SwcSegment(child, parent, tuple(end), tuple(start), 1., 3)


@pytest.mark.parametrize('seed', range(8))
def test_random_cables_match_exhaustive_search(seed):
    rng = random.Random(seed)
    edges = [edge(i+1, i, [rng.uniform(-100,100) for _ in range(3)],
                  [rng.uniform(-100,100) for _ in range(3)]) for i in range(180)]
    # Shuffle order to exercise non-ID tree order and internal-node pruning.
    rng.shuffle(edges)
    model = morphology(edges)
    projector = ContactProjector(model)
    for point in ([rng.uniform(-200,200) for _ in range(3)] for _ in range(90)):
        assert projector.project(point) == project_contact(model, point)


@pytest.mark.parametrize('scale', [1e-60, 1e-9, 1., 1e9, 1e60])
def test_endpoints_long_cables_ties_and_close_rounding(scale):
    # The nearest cable's endpoints can be much farther away than unrelated
    # short edges. A nearest-node shortcut would choose the wrong cable.
    edges = [edge(2,1,(-1000*scale,0.,0.),(1000*scale,0.,0.)),
             edge(99,98,(-1000*scale,0.,0.),(1000*scale,0.,0.))]
    edges += [edge(i+200,i+199,(i*scale,4*scale,0.),(i*scale,5*scale,0.)) for i in range(60)]
    model = morphology(list(reversed(edges)))
    projector = ContactProjector(model)
    for x in (0., scale, -1000*scale, 1000*scale, math.nextafter(1000*scale, math.inf)):
        for y in (0., scale, math.nextafter(scale, math.inf)):
            point = x,y,0.
            assert projector.project(point) == project_contact(model, point)
            assert projector.project(point)['source_node_id'] == 2


def test_filler_zero_length_and_synthetic_parent_are_excluded():
    model = morphology([edge(2,1,(0,0,0),(10,0,0)),
        edge(3,2,(0,0,0),(0,0,0)), edge(4,3,(0,1,0),(10,1,0)),
        edge(6,5,(0,1,0),(10,1,0)), edge(8,7,(0,1,0),(10,1,0))],
        {'filler_edge_child_ids':[4], 'synthetic_node_ids':[5,8]})
    projector = ContactProjector(model)
    assert projector.project((5,1,0)) == project_contact(model,(5,1,0))
    assert projector.project((5,1,0))['source_node_id'] == 2


@pytest.mark.parametrize('point', [(1,2), (1,2,3,4), (1,2,math.nan), (1,math.inf,3)])
def test_invalid_coordinates_refuse(point):
    with pytest.raises(ValueError, match='three finite'):
        ContactProjector(morphology([])).project(point)


def test_empty_real_cable_set_refuses():
    with pytest.raises(ValueError, match='nonzero real SWC'):
        ContactProjector(morphology([edge(2,1,(0,0,0),(0,0,0))])).project((0,0,0))


def test_index_is_local_and_original_model_is_unchanged():
    model = morphology([edge(2,1,(0,0,0),(10,0,0))])
    before = vars(model).copy()
    ContactProjector(model).project((5,1,0))
    assert vars(model) == before


def test_frame_and_work_budget_refuse_before_index_construction(tmp_path, monkeypatch):
    from digifly_app.core import pair_graph, contact_mapping
    from digifly_app.core.circuit import CircuitSpec
    from digifly_app.engines.generic_experiment import GenericExperimentAdapter
    from test_generic_experiment import _morphology
    model = replace(_morphology(tmp_path), coordinate_transform={'coordinate_frame':'wrong'})
    sites = ({'source_body_id':1, 'target_body_id':1},)
    monkeypatch.setattr(pair_graph, 'selected_contact_sites', lambda *args: (
        {'contact_coordinate_convention':{'frame':'expected', 'scale_to_um':.008}}, sites))
    def forbidden(*args):
        pytest.fail('An index must not precede the frame/budget refusal')
    monkeypatch.setattr(contact_mapping, 'ContactProjector', forbidden)
    adapter = GenericExperimentAdapter({})
    with pytest.raises(ValueError, match='skeleton frame'):
        adapter._located_download_rows(CircuitSpec(), {'1':model})
    class TooMany:
        def __len__(self): return 25_000_001
    with pytest.raises(ValueError, match='25-million'):
        adapter._located_download_rows(CircuitSpec(), {'1':replace(model, segments=TooMany())})
