"""Inert worker preparation stops before native setup or numerical execution."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from digifly_app.core.circuit import CircuitSpec
from digifly_app.core.experiment import ExperimentSpec, RecordingSpec
from digifly_app.workers import generic_experiment_worker as worker
from test_generic_experiment import _morphology
from test_located_contact_execution import contact


def test_each_normalized_skeleton_is_read_once_during_contact_preparation(tmp_path,monkeypatch):
    a,b = _morphology(tmp_path,'1'),_morphology(tmp_path,'2')
    circuit = CircuitSpec(neuron_ids=('1','2')).to_dict()
    model_contact = contact(a)
    model_contact.update(weight_uS=.001,delay_ms=1.,tau1_ms=.5,tau2_ms=3.,reversal_mV=0.)
    contacts = [{**copy.deepcopy(model_contact),'contact_id':f'chemical-{n}'} for n in range(40)]
    edges = dict(schema_version=2,kind='explicit_selected_subgraph',neuron_ids=['1','2'],
                 chemical_edges=contacts,electrical_edges=[],sources=[])
    edges['selected_contact_identity_sha256'] = hashlib.sha256(json.dumps(
        {'chemical':contacts,'electrical':[]},sort_keys=True,separators=(',',':')).encode()).hexdigest()
    folder = tmp_path/'preparation'
    folder.mkdir()
    edge_path = folder/'edge_manifest.json'
    worker._atomic_json(edge_path,edges)
    experiment = ExperimentSpec(engine='neuron',recording=RecordingSpec(target_region='all',make_plots=False))
    request = dict(schema_version=1,engine='neuron',output_dir=str(folder),circuit=circuit,
        experiment=experiment.to_dict(),edge_manifest_path=str(edge_path),edge_manifest_sha256=worker._sha256(edge_path),
        morphologies={m.record.neuron_id:dict(path=m.record.swc_path,sha256=worker._sha256(Path(m.record.swc_path)),
                        family='test',neuron_type='test') for m in (a,b)})
    path = folder/'worker_request.json'
    worker._atomic_json(path,request)
    reads = []
    original = worker._swc_rows
    def observed(path):
        reads.append(path)
        return original(path)
    class Prepared(Exception):pass
    def stop(*args):raise Prepared()
    monkeypatch.setattr(worker,'_swc_rows',observed)
    monkeypatch.setattr(worker,'_prepare_native_channels',stop)
    with pytest.raises(Prepared):worker.run(path)
    assert len(reads) == 2
    assert len(set(reads)) == 2
    resolved = json.loads((folder/'resolved_edge_manifest.json').read_text())
    assert len(resolved['chemical_edges']) == 40
    assert all(set(c['contact_cv'])=={'pre','post'} for c in resolved['chemical_edges'])
    assert not (folder/'run_manifest.json').exists()
    for cell in (folder/'morphologies').iterdir():
        saved = (cell/'regional_map.json').read_text()
        assert saved.count('\n') == 1
        assert json.loads(saved)['storage_schema'] == 'digifly-shared-regional-materials-v1'


def test_compact_json_is_atomic_and_keeps_all_values(tmp_path,monkeypatch):
    path = tmp_path/'map.json'
    payload = {'order':[3,2,1], 'values':[.123456789012345,-65.,1e-12], 'label':'µm'}
    worker._atomic_json(path,payload,compact=True)
    before = path.read_bytes()
    assert json.loads(before) == payload
    def failed(*args):raise OSError('deliberate publication failure')
    monkeypatch.setattr(worker.os,'replace',failed)
    with pytest.raises(OSError,match='publication failure'):
        worker._atomic_json(path,{'changed':True},compact=True)
    assert path.read_bytes() == before and list(tmp_path.iterdir()) == [path]
