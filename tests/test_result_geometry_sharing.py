"""Sharing identical immutable geometry must never bypass the saved-file reader."""
from dataclasses import asdict, replace
import inspect

import pytest

from digifly_app.core.connectomes import NeuronRecord
from digifly_app.core.morphology import load_swc


def pair(tmp_path, changed_radius=False):
    original = tmp_path/'original.swc'
    saved = tmp_path/'saved.swc'
    text = '1 1 0 0 0 .5 -1\n2 3 1 0 0 .5 1\n3 3 2 0 0 .5 2\n'
    original.write_text(text)
    saved.write_text(text.replace('2 3 1 0 0 .5', '2 3 1 0 0 .9') if changed_radius else text)
    before = load_swc(NeuronRecord('42','IN','fixture',str(original),'manc:v1.2.1'))
    return before, replace(before.record, swc_path=str(saved))


def with_candidate(record, before, **kwargs):
    # The old reader still runs before the implementation exists, so the
    # baseline failure demonstrates duplicate records, not merely a new API.
    if 'reuse_from' in inspect.signature(load_swc).parameters:
        kwargs['reuse_from'] = before
    return load_swc(record, **kwargs)


def test_identical_rows_share_objects_but_keep_the_saved_path_and_full_metadata(tmp_path):
    before, record = pair(tmp_path)
    actual = with_candidate(record, before)
    expected = load_swc(record)
    assert asdict(actual) == asdict(expected)
    assert actual.record.swc_path == record.swc_path != before.record.swc_path
    assert all(a is b for a,b in zip(actual.nodes,before.nodes))
    assert all(a is b for a,b in zip(actual.segments,before.segments))


def test_changed_rows_are_read_fresh_without_changing_original_geometry(tmp_path):
    before, record = pair(tmp_path, changed_radius=True)
    actual = with_candidate(record, before)
    assert asdict(actual) == asdict(load_swc(record))
    assert actual.nodes[0] is before.nodes[0]
    assert actual.nodes[1] is not before.nodes[1] and actual.nodes[1].radius == .9
    assert before.nodes[1].radius == .5
    assert actual.nodes[2] is before.nodes[2]
    assert actual.segments[0] is not before.segments[0]
    assert actual.segments[1] is before.segments[1]


@pytest.mark.parametrize('field,value',[('neuron_id','99'),('connectome_key','male-cns:v1.0')])
def test_body_or_release_mismatch_never_shares_rows(tmp_path, field, value):
    before, record = pair(tmp_path)
    candidate = replace(before, record=replace(before.record, **{field:value}))
    actual = with_candidate(record, candidate)
    assert all(a is not b for a,b in zip(actual.nodes,candidate.nodes))
    assert asdict(actual) == asdict(load_swc(record))


def test_reuse_does_not_bypass_invalid_saved_rows_or_cancellation(tmp_path):
    before, record = pair(tmp_path)
    from pathlib import Path
    Path(record.swc_path).write_text('1 1 0 0 0 .5 -1\n1 3 1 0 0 .5 1\n')
    with pytest.raises(ValueError,match='Duplicate'):
        with_candidate(record,before)
    def cancel():
        raise RuntimeError('cancel this read')
    with pytest.raises(RuntimeError,match='cancel this read'):
        with_candidate(record,before,check=cancel)


def test_saved_reader_forwards_only_an_unambiguous_candidate(tmp_path):
    import json
    from digifly_app.core.models import Artifact, ResultRecord
    from digifly_app.ui.result_loading import read_recorded_morphologies
    before, record = pair(tmp_path)
    (tmp_path/'circuit.json').write_text(json.dumps({'connectome':{'key':record.connectome_key}}))
    (tmp_path/'worker_request.json').write_text(json.dumps({'morphologies':{
        '42':{'family':'IN','neuron_type':'fixture'}}}))
    result = ResultRecord(str(tmp_path/'summary.json'),'complete','','fixture',{},
        (Artifact('morphology',record.swc_path,'Normalized morphology · 42',True),))
    items, failures, _, found = read_recorded_morphologies(result,reuse_morphologies=(before,))
    assert found and not failures and items[0].nodes[0] is before.nodes[0]
    duplicate, _, _, _ = read_recorded_morphologies(result,reuse_morphologies=(before,before))
    assert duplicate[0].nodes[0] is not before.nodes[0]


def test_harmless_rounding_difference_uses_saved_values_without_refusal(tmp_path):
    from pathlib import Path
    before, record = pair(tmp_path)
    path = Path(record.swc_path)
    path.write_text(path.read_text().replace('2 3 1 0 0 .5', '2 3 1 0 0 .5000000000000001'))
    actual = with_candidate(record,before)
    assert asdict(actual) == asdict(load_swc(record))
    assert actual.nodes[1] is not before.nodes[1]
    assert actual.nodes[1].radius > before.nodes[1].radius
