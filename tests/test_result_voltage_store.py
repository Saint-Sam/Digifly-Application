"""One lossless reader, derived-cache safety and cancellation; no simulator."""
import csv
import json
from pathlib import Path
import pytest
from digifly_app.core.result_voltage import load_voltage_table, LoadCancelled


def recording(tmp_path):
    path = tmp_path/'run'/'voltage_traces.csv'
    path.parent.mkdir()
    with path.open('w', newline='') as stream:
        writer=csv.writer(stream)
        writer.writerow(('condition','repetition','neuron_id','compartment_id','time_ms','voltage_mV'))
        for condition in ('Control','No stimulus'):
            for neuron in ('1','2'):
                for edge in ('20','30'):
                    for t,v in ((.04,.123456789012345),(0.,-65.),(.02,42.)):
                        writer.writerow((condition,1,neuron,edge,t,v))
    return path


def test_cold_warm_exact_values_shared_clocks_and_no_second_parse(tmp_path, monkeypatch):
    path=recording(tmp_path); cache=tmp_path/'cache'
    first=load_voltage_table(path,cache_root=cache)
    assert first.sample_count==24 and not first.cache_hit
    series=list(first.series.values())
    assert first.mapping is not None and series[0].values.readonly
    assert tuple(series[0])==((0.,-65.),(.02,42.),(.04,.123456789012345))
    assert series[0].times is series[1].times
    assert len(list(cache.glob('*/ready.json')))==1
    import digifly_app.core.result_voltage as module
    monkeypatch.setattr(module,'_parse',lambda *a,**k:pytest.fail('warm load parsed CSV again'))
    warm=load_voltage_table(path,cache_root=cache)
    assert warm.cache_hit and tuple(next(iter(warm.series.values())))==tuple(series[0])


def test_cancel_cold_load_removes_only_its_derived_partial(tmp_path):
    path=recording(tmp_path); cache=tmp_path/'cache'; before=path.read_bytes()
    def cancelled():return True
    with pytest.raises(LoadCancelled):load_voltage_table(path,cache_root=cache,cancelled=cancelled)
    assert path.read_bytes()==before
    assert not list(cache.glob('.partial-*'))


def test_cancellation_during_cache_write_and_no_ready_commit(tmp_path):
    path=recording(tmp_path); cache=tmp_path/'cache'; stop=False
    def progress(label,done,total):
        nonlocal stop
        if label=='Saving reusable display data':stop=True
    with pytest.raises(LoadCancelled):
        load_voltage_table(path,cache_root=cache,cancelled=lambda:stop,progress=progress)
    assert not list(cache.glob('*/ready.json')) and not list(cache.glob('.partial-*'))


def test_changed_source_and_corrupt_cache_rebuild_without_changing_run(tmp_path):
    path=recording(tmp_path); cache=tmp_path/'cache'
    first=load_voltage_table(path,cache_root=cache)
    binary=next(cache.glob('*/values.bin'))
    with binary.open('r+b') as stream:stream.write(b'corrupt!')
    repaired=load_voltage_table(path,cache_root=cache)
    assert not repaired.cache_hit
    path.write_text(path.read_text().replace('42.0','43.0'))
    changed=load_voltage_table(path,cache_root=cache)
    assert not changed.cache_hit and next(iter(changed.series.values()))[1][1]==43.


def test_missing_source_does_not_resurrect_cached_voltage(tmp_path):
    path=recording(tmp_path); cache=tmp_path/'cache'
    load_voltage_table(path,cache_root=cache)
    path.rename(path.with_name('retained.csv'))
    assert not load_voltage_table(path,cache_root=cache).series


def test_finite_guard_duplicates_and_legacy_metadata(tmp_path):
    path=recording(tmp_path)
    with pytest.raises(ValueError,match='sample budget'):
        load_voltage_table(path,cache_root=tmp_path/'cache',maximum_samples=5)
    with path.open('a') as stream:stream.write('Control,1,1,20,.02,nan\nControl,1,1,20,.02,10\n')
    table=load_voltage_table(path,cache_root=tmp_path/'cache')
    assert table.morphology_error and not next(iter(table.series.values())).unique
    assert table.sample_count==25


def test_cache_index_corruption_is_not_trusted(tmp_path):
    path=recording(tmp_path); cache=tmp_path/'cache'
    load_voltage_table(path,cache_root=cache)
    index=next(cache.glob('*/index.jsonl')); lines=index.read_text().splitlines()
    record=json.loads(lines[1]);record['key'][2]='wrong';lines[1]=json.dumps(record)
    index.write_text('\n'.join(lines)+'\n')
    table=load_voltage_table(path,cache_root=cache)
    assert not table.cache_hit and next(iter(table.series))[2]=='1'


def test_low_disk_and_unknown_cache_folders_are_left_safe(tmp_path,monkeypatch):
    from types import SimpleNamespace
    import digifly_app.core.result_voltage as module
    path=recording(tmp_path);cache=tmp_path/'cache'
    monkeypatch.setattr(module.shutil,'disk_usage',lambda p:SimpleNamespace(free=1))
    table=load_voltage_table(path,cache_root=cache)
    assert table.sample_count==24 and 'free disk' in table.cache_note
    assert not list(cache.glob('*/ready.json'))
    unrelated=cache/('a'*64);unrelated.mkdir();(unrelated/'values.bin').write_bytes(b'keep')
    monkeypatch.setattr(module,'CACHE_BYTES',0)
    module._prune(cache,None)
    assert (unrelated/'values.bin').read_bytes()==b'keep'


def test_source_changed_while_warm_cache_is_read_refuses_without_reparse(tmp_path,monkeypatch):
    import digifly_app.core.result_voltage as module
    path=recording(tmp_path);cache=tmp_path/'cache'
    load_voltage_table(path,cache_root=cache)
    original=module._read_cache
    def changed(*args,**kwargs):
        table=original(*args,**kwargs)
        path.write_text(path.read_text().replace('42.0','43.0'))
        return table
    monkeypatch.setattr(module,'_read_cache',changed)
    monkeypatch.setattr(module,'_parse',lambda *a,**k:pytest.fail('source mutation must not silently retry'))
    with pytest.raises(ValueError,match='changed during loading'):
        load_voltage_table(path,cache_root=cache)


def test_eviction_only_removes_complete_recognized_derived_cache(tmp_path,monkeypatch):
    import digifly_app.core.result_voltage as module
    path=recording(tmp_path);cache=tmp_path/'cache'
    table=load_voltage_table(path,cache_root=cache)
    valid=next(cache.glob('*/ready.json')).parent
    unknown=cache/('b'*64);unknown.mkdir();(unknown/'values.bin').write_bytes(b'keep')
    outside=tmp_path/'outside';outside.mkdir();(outside/'keep').write_text('keep')
    (cache/('c'*64)).symlink_to(outside,target_is_directory=True)
    monkeypatch.setattr(module,'CACHE_BYTES',0)
    module._prune(cache,None)
    if __import__('sys').platform=='win32':
        # Windows holds a mapped file open. Preserve its marker until all
        # views are closed, then retry eviction without touching saved input.
        assert valid.exists() and module._marked_cache(valid)
        mapping=table.mapping
        table.series={};table.mapping=None
        mapping.close()
        module._prune(cache,None)
    assert not valid.exists()
    assert (unknown/'values.bin').read_bytes()==b'keep' and (outside/'keep').read_text()=='keep'
    assert table.sample_count==24


def test_locked_cache_keeps_its_marker_and_can_be_retired_later(tmp_path,monkeypatch):
    import digifly_app.core.result_voltage as module
    path=recording(tmp_path);cache=tmp_path/'cache'
    table=load_voltage_table(path,cache_root=cache)
    folder=next(cache.glob('*/ready.json')).parent
    from pathlib import Path
    original=module.os.unlink
    def locked(value,*args,**kwargs):
        if Path(value).name=='values.bin':raise PermissionError('mapped file is still open')
        return original(value,*args,**kwargs)
    monkeypatch.setattr(module.os,'unlink',locked)
    monkeypatch.setattr(module,'CACHE_BYTES',0)
    module._prune(cache,None)
    assert module._marked_cache(folder)
    assert {p.name for p in folder.iterdir()}=={'ready.json','index.jsonl','values.bin'}
    assert table.sample_count==24
