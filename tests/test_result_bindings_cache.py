"""Derived replay-ID storage only; no model or simulator runs."""
import hashlib
import json
from pathlib import Path
import pytest
from digifly_app.core.result_bindings import load_replay_bindings
from digifly_app.core.result_voltage import LoadCancelled, REPLAY_BINDING_SCHEMA


def setup_case(tmp_path):
    source=tmp_path/'input.json';source.write_text('original source')
    root=tmp_path/'cache';calls=[]
    identity=(('1',str(source),(2,3)),)
    def factory():
        calls.append(1)
        return {'1':{20:2,30:3}},{'1':'20'}
    def load(**kwargs):return load_replay_bindings(root,[source],identity,factory,**kwargs)
    return source,root,calls,load


def test_bindings_reuse_checks_all_inputs_and_preserves_discrete_ids(tmp_path):
    source,root,calls,load=setup_case(tmp_path)
    first=load();second=load()
    assert first[:2]==second[:2] and first[2] is False and second[2] is True
    assert len(calls)==1 and source.read_text()=='original source'
    source.write_text('changed source')
    assert load()[2] is False and len(calls)==2


def test_corrupt_binding_index_rebuilds_without_touching_source(tmp_path):
    source,root,calls,load=setup_case(tmp_path);expected=load()
    folder=next(root.glob('*/ready.json')).parent
    (folder/'index.jsonl').write_text('broken')
    assert load()==expected and len(calls)==2
    assert load()[2] is True and source.read_text()=='original source'


def test_unknown_cache_contents_are_never_removed(tmp_path):
    source,root,calls,load=setup_case(tmp_path);load()
    folder=next(root.glob('*/ready.json')).parent
    sentinel=folder/'user-note.txt';sentinel.write_text('keep')
    assert load()[2] is False and sentinel.read_text()=='keep'


def test_cancel_removes_only_own_partial_bindings(tmp_path):
    source,root,calls,load=setup_case(tmp_path)
    def check():
        if list(root.glob('.partial-bindings-*')):raise LoadCancelled()
    with pytest.raises(LoadCancelled):load(check=check)
    assert not list(root.glob('.partial-bindings-*'))
    assert not list(root.glob('*/ready.json')) and source.read_text()=='original source'


def test_low_disk_falls_back_without_cache(tmp_path,monkeypatch):
    source,root,calls,load=setup_case(tmp_path)
    import digifly_app.core.result_bindings as module
    from collections import namedtuple
    Usage=namedtuple('Usage','total used free')
    monkeypatch.setattr(module.shutil,'disk_usage',lambda _:Usage(10,9,1))
    assert load()[2] is False and not list(root.glob('*/ready.json'))


def test_changed_geometry_cannot_reuse_previously_valid_binding_cache(tmp_path):
    from test_result_playback import _measured_run,_recorded_morphology
    from digifly_app.ui.result_playback import cached_morphology_voltage_bindings
    folder=_measured_run(tmp_path);morphologies=(_recorded_morphology(tmp_path),)
    first=cached_morphology_voltage_bindings(tmp_path,morphologies,tmp_path/'cache')
    assert first[0]=={'10000':{20:2,30:3}} and not first[2]
    assert cached_morphology_voltage_bindings(tmp_path,morphologies,tmp_path/'cache')[2]
    with (folder/'normalized_input.swc').open('a') as stream:stream.write('4 3 50 0 0 .5 3\n')
    with pytest.raises(ValueError,match='geometry identity changed'):
        cached_morphology_voltage_bindings(tmp_path,morphologies,tmp_path/'cache')


def test_source_changed_during_factory_refuses_new_cache(tmp_path):
    source=tmp_path/'source';source.write_text('before')
    def factory():source.write_text('after');return {'1':{20:2}},{}
    with pytest.raises(ValueError,match='changed during loading'):
        load_replay_bindings(tmp_path/'cache',[source],(('1',str(source),(2,)),),factory)
    assert not list((tmp_path/'cache').glob('*/ready.json'))


def test_discrete_edge_guard_and_shared_voltage_cache_budget(tmp_path,monkeypatch):
    source,root,calls,load=setup_case(tmp_path);load()
    folder=next(root.glob('*/ready.json')).parent
    index=folder/'index.jsonl'
    index.write_text(json.dumps({'soma_edges':{},'bodies':['1']})+'\n'+json.dumps(['1',20,999])+'\n')
    marker=json.loads((folder/'ready.json').read_text())
    marker['index_sha256']=hashlib.sha256(index.read_bytes()).hexdigest()
    (folder/'ready.json').write_text(json.dumps(marker))
    assert load()[0]=={'1':{20:2,30:3}} and len(calls)==2
    import digifly_app.core.result_voltage as storage
    legacy=root/('a'*64);legacy.mkdir()
    (legacy/'ready.json').write_text(json.dumps({'schema':storage.SCHEMA}))
    (legacy/'values.bin').write_bytes(b'x'*1024)
    import os
    os.utime(legacy,(1,1))
    unknown=root/'user-folder';unknown.mkdir();(unknown/'note').write_text('keep')
    source.write_text('new input creates a new checked ID cache')
    monkeypatch.setattr(storage,'CACHE_BYTES',512)
    load()
    assert not legacy.exists() and (unknown/'note').read_text()=='keep'
