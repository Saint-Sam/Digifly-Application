"""Authoritative binary recordings, with no derived CSV or model cache."""
import hashlib
import json
import struct
import sys

import pytest

from digifly_app.core.result_voltage import load_voltage_table, LoadCancelled
from digifly_app.workers.result_storage import VoltageRows, result_path,iter_binary_traces


def recording(root):
    rows = VoltageRows()
    for condition in ('Control','No stimulus'):
        for neuron in ('1','2'):
            for edge in ('20','30'):
                rows.add(condition,1,neuron,edge,[0.,.02,.04],[-65.,42.,.123456789012345])
    return rows,rows.write_binary(root)


def rehash(root):
    ready = root/'voltage/ready.json'
    record = json.loads(ready.read_text())
    record.update(index_sha256=hashlib.sha256((ready.parent/'index.jsonl').read_bytes()).hexdigest(),
                  values_sha256=hashlib.sha256((ready.parent/'values.bin').read_bytes()).hexdigest())
    ready.write_text(json.dumps(record))


def test_direct_float64_roundtrip_readonly_shared_clock_no_csv_or_cache(tmp_path):
    rows,path = recording(tmp_path)
    cache = tmp_path/'display-cache'
    before = {p.name:p.read_bytes() for p in path.parent.iterdir()}
    table = load_voltage_table(tmp_path/'voltage_traces.csv',cache_root=cache)
    assert table.sample_count == len(rows) == 24
    assert len(table.series)==8 and table.has_compartments
    first,second = list(table.series.values())[:2]
    assert first.times is second.times and first.values.readonly
    assert tuple(first)==((0.,-65.),(.02,42.),(.04,.123456789012345))
    assert table.clocks['Control',1]==(0.,.02,.04)
    assert result_path(tmp_path/'voltage_traces.csv')==path
    assert not (tmp_path/'voltage_traces.csv').exists() and not cache.exists()
    assert before=={p.name:p.read_bytes() for p in path.parent.iterdir()}
    streamed=list(iter_binary_traces(path.parent))
    assert len(streamed)==8 and tuple(zip(streamed[0][1],streamed[0][2]))==tuple(first)


def test_cancel_publish_cleans_partial_but_keeps_user_files(tmp_path):
    keep=tmp_path/'keep';keep.write_text('user file')
    rows=VoltageRows();rows.add('Control',1,'1','20',[0,1],[-65,40])
    def cancel():raise LoadCancelled()
    with pytest.raises(LoadCancelled):rows.write_binary(tmp_path,check_cancel=cancel)
    assert list(tmp_path.iterdir())==[keep]


@pytest.mark.parametrize('name',['index.jsonl','values.bin'])
def test_corrupt_or_truncated_recording_refused(tmp_path,name):
    _,path=recording(tmp_path)
    changed=path.parent/name;changed.write_bytes(changed.read_bytes()[:-1])
    with pytest.raises(ValueError):load_voltage_table(path,cache_root=tmp_path/'cache')


@pytest.mark.parametrize('mutation',['duplicate','offset','nan','clock','count','bounds','condition'])
def test_even_rehashed_invalid_structure_refused(tmp_path,mutation):
    _,path=recording(tmp_path)
    index=path.parent/'index.jsonl'
    entries=[json.loads(line) for line in index.read_text().splitlines()]
    if mutation=='duplicate':entries[2]['key']=entries[1]['key']
    if mutation=='offset':entries[1]['offset']+=8
    if mutation=='count':entries[0]['sample_count']-=1
    if mutation=='bounds':entries[1]['bounds']=[-65,43]
    if mutation=='condition':entries[0]['condition_clock_ids']=[]
    if mutation in ('nan','clock'):
        payload=path.parent/'values.bin';data=bytearray(payload.read_bytes())
        struct.pack_into('d',data,entries[1]['offset'] if mutation=='nan' else 8,float('nan') if mutation=='nan' else -1)
        payload.write_bytes(data)
    index.write_text(''.join(json.dumps(e)+'\n' for e in entries));rehash(tmp_path)
    with pytest.raises(ValueError):load_voltage_table(path,cache_root=tmp_path/'cache')


def test_moved_opposite_endian_recording_retains_exact_values(tmp_path):
    _,path=recording(tmp_path)
    data=path.parent/'values.bin';raw=data.read_bytes()
    data.write_bytes(b''.join(raw[i:i+8][::-1] for i in range(0,len(raw),8)))
    index=path.parent/'index.jsonl';lines=index.read_text().splitlines()
    header=json.loads(lines[0]);header['byteorder']='big' if sys.byteorder=='little' else 'little'
    lines[0]=json.dumps(header);index.write_text('\n'.join(lines)+'\n');rehash(tmp_path)
    table=load_voltage_table(path,cache_root=tmp_path/'cache')
    assert tuple(next(iter(table.series.values())))==((0.,-65.),(.02,42.),(.04,.123456789012345))


def test_existing_destination_and_ambiguous_format_refuse(tmp_path):
    rows,path=recording(tmp_path)
    with pytest.raises(ValueError,match='replace'):rows.write_binary(tmp_path)
    (tmp_path/'voltage_traces.csv').write_text('legacy')
    with pytest.raises(ValueError,match='Ambiguous'):result_path(tmp_path/'voltage_traces.csv')


def test_binary_load_limits_and_cancel(tmp_path):
    _,path=recording(tmp_path)
    with pytest.raises(ValueError,match='budget'):
        load_voltage_table(path,cache_root=tmp_path/'cache',maximum_samples=2)
    with pytest.raises(LoadCancelled):
        load_voltage_table(path,cache_root=tmp_path/'cache',cancelled=lambda:True)
