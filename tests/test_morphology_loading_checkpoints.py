"""Saved-skeleton preparation remains cancellable within a large neuron."""
from dataclasses import asdict, replace
import json
import pickle

import pytest

from digifly_app.core.connectomes import NeuronRecord
from digifly_app.core.morphology import load_swc
from digifly_app.core.models import Artifact, ResultRecord
from digifly_app.ui.result_loading import LoadCancelled, read_recorded_morphologies


@pytest.mark.parametrize('kind', ['SwcNode', 'SwcSegment'])
def test_geometry_records_avoid_per_element_dictionaries_without_changing_values(kind):
    import digifly_app.core.morphology as module
    value = (module.SwcNode(2, 3, 1., 2., 3., .5, 1) if kind == 'SwcNode'
             else module.SwcSegment(2, 1, (1., 2., 3.), (0., 0., 0.), .5, 3))
    assert not hasattr(value, '__dict__')
    restored = pickle.loads(pickle.dumps(value))
    assert restored == value and asdict(restored) == asdict(value)
    changed = replace(value, radius=.75)
    assert changed.radius == .75 and value.radius == .5


def skeleton(tmp_path, count=2049):
    path = tmp_path / 'cell.swc'
    path.write_text(''.join(
        f'{i} 3 {i}.0 0 0 .5 {i-1 if i > 1 else -1}\n'
        for i in range(1, count+1)))
    return NeuronRecord('42', 'IN', 'fixture', str(path), 'manc:v1.2.1')


def test_skeleton_reader_checks_inside_node_and_segment_construction(tmp_path, monkeypatch):
    import digifly_app.core.morphology as module
    record = skeleton(tmp_path)
    expected = asdict(load_swc(record))
    original_node, original_segment = module.SwcNode, module.SwcSegment
    created = [0, 0]
    checkpoints = []

    def node(*args, **kwargs):
        created[0] += 1
        return original_node(*args, **kwargs)

    def segment(*args, **kwargs):
        created[1] += 1
        return original_segment(*args, **kwargs)

    monkeypatch.setattr(module, 'SwcNode', node)
    monkeypatch.setattr(module, 'SwcSegment', segment)
    actual = load_swc(record, check=lambda: checkpoints.append(tuple(created)))
    assert asdict(actual) == expected
    assert created == [2049, 2048]
    assert checkpoints[0] == (0, 0) and checkpoints[-1] == tuple(created)
    for previous, current in zip(checkpoints, checkpoints[1:]):
        assert all(b-a <= 256 for a, b in zip(previous, current))


def test_skeleton_cancellation_does_not_finish_the_large_neuron(tmp_path, monkeypatch):
    import digifly_app.core.morphology as module
    record = skeleton(tmp_path, count=10001)
    original = module.SwcNode
    created = []

    def node(*args, **kwargs):
        created.append(1)
        return original(*args, **kwargs)

    def cancel():
        if len(created) >= 256:
            raise LoadCancelled()

    monkeypatch.setattr(module, 'SwcNode', node)
    with pytest.raises(LoadCancelled):
        load_swc(record, check=cancel)
    assert len(created) == 256


def test_recorded_result_passes_its_reader_check_into_each_skeleton(tmp_path, monkeypatch):
    import digifly_app.ui.result_loading as module
    record = skeleton(tmp_path, count=3)
    (tmp_path / 'worker_request.json').write_text(json.dumps({
        'morphologies': {'42': {'neuron_type': 'fixture', 'family': 'IN'}}}))
    result = ResultRecord(str(tmp_path/'summary.json'), 'complete', '', 'fixture', {},
        (Artifact('morphology', record.swc_path, 'Normalized morphology · 42', True),))
    original = module.load_swc
    passed = []

    def checkpoint():
        passed.append('check')

    def checked_load(neuron, *, check):
        assert check is checkpoint
        passed.append('load')
        return original(neuron, check=check)

    monkeypatch.setattr(module, 'load_swc', checked_load)
    items, failures, _, found = read_recorded_morphologies(result, checkpoint)
    assert found and not failures and len(items) == 1
    assert 'load' in passed and passed.count('check') > 1
