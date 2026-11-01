import json
import os
from pathlib import Path
import subprocess
import sys
from dataclasses import replace
import pytest
from digifly_app.core.experiment import ExperimentSpec, RecordingSpec
from digifly_app.workers.current_tracking import check_mode, distributed_indices, read_values


def test_legacy_off_and_current_only_roundtrip():
    assert RecordingSpec.from_dict({}).current_tracking == "off"
    for mode in ("off", "sparse", "full"):
        config = replace(ExperimentSpec(), recording=replace(RecordingSpec(), current_tracking=mode))
        assert ExperimentSpec.from_dict(config.to_dict()).recording.current_tracking == mode
    assert not RecordingSpec(record_voltage=False, detect_spikes=False, current_tracking="full").errors()
    assert RecordingSpec(current_tracking="fiction").errors()


def test_other_solvers_and_parallel_trials_refuse_instead_of_ignoring():
    for engine, workers in (("arbor", 1), ("bmtk", 1), ("neuron", 2)):
        with pytest.raises(ValueError, match="requires NEURON"):
            check_mode({"current_tracking":"full"}, engine, workers)
    assert check_mode({}, "arbor", 8) == "off"
    assert len(distributed_indices(10000)) == 7
    assert distributed_indices(3) == (0,1,2)
    with pytest.raises(ValueError, match="whole multiple"):
        check_mode({"current_tracking":"full", "sample_dt_ms":.03}, "neuron", 1, .02)
    from digifly_app.workers.current_tracking import plan_budget
    with pytest.raises(ValueError, match="budget"):
        plan_budget({'a':{'estimated_nseg':1_000_000}}, {'duration_ms':100.,'recording':{'current_tracking':'full','sample_dt_ms':.05}})


def test_low_disk_refuses_before_recording(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from digifly_app.workers.current_tracking import CurrentRecorder
    import digifly_app.workers.current_tracking as module
    recorder = CurrentRecorder.__new__(CurrentRecorder)
    recorder.channels = [object()]
    recorder.duration = 5.
    recorder.dt = .05
    monkeypatch.setattr(module.shutil, "disk_usage", lambda _path: SimpleNamespace(free=1024))
    with pytest.raises(ValueError, match="reserve"):
        recorder.check_storage(tmp_path / "new-run")
    assert not (tmp_path / "new-run").exists()


def test_saved_current_index_survives_result_compression(tmp_path):
    from array import array
    import hashlib
    import zlib
    from digifly_app.workers.current_tracking import read_index
    from digifly_app.workers.result_storage import compress_file
    folder = tmp_path/'currents'/'trial'
    folder.mkdir(parents=True)
    raw = array('d', (0., .001, -.002)).tobytes()
    payload = zlib.compress(raw)
    (folder/'values.bin').write_bytes(payload)
    channel = dict(samples=3,offset=0,compressed_bytes=len(payload),sha256=hashlib.sha256(raw).hexdigest())
    index = folder/'index.json'
    record = {'schema':'digifly-current-traces-v1','data':'values.bin','times_ms':[0,1,2],
              'channels':[channel], 'padding':'native current metadata '*500}
    index.write_text(json.dumps(record))
    assert compress_file(index, minimum_bytes=0)
    assert not index.exists() and read_index(index)==record
    assert read_values(index, channel)==(0.,.001,-.002)


def test_current_controls_roundtrip_and_units(tmp_path):
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from digifly_app.ui.experiment_builder import ExperimentBuilderPage
    from digifly_app.ui.current_plot import CurrentPlotWidget
    page = ExperimentBuilderPage(output_root=tmp_path)
    plot = CurrentPlotWidget()
    try:
        for mode in ("full", "sparse", "off"):
            page.current_tracking.setCurrentIndex(page.current_tracking.findData(mode))
            assert page.config().recording.current_tracking == mode
            page.set_config(ExperimentSpec.from_dict(page.config().to_dict()))
            assert page.current_tracking.currentData() == mode
        assert plot.plot.canvas.quantity_unit == "nA"
        assert "nA" in plot.plot.canvas.quantity_label
    finally:
        page.close(); plot.close()


NATIVE = Path(__file__).resolve().parents[2] / "Digifly-Runtimes/envs/digifly-channels-neuron-bionet-py312-v1/bin/python"


@pytest.mark.skipif(not NATIVE.is_file(), reason="Optional native NEURON test runtime absent")
def test_native_full_sparse_coverage_currents_balance_and_voltage_preservation(tmp_path):
    """Real production constructor, real solver, no mocked current values."""
    code = r'''
import copy, json, math, sys
import hashlib
from pathlib import Path
from digifly_app.core.circuit import CircuitSpec
from digifly_app.core.experiment import ExperimentSpec
from digifly_app.workers import generic_experiment_worker as w
from digifly_app.workers.current_tracking import read_index, read_values
root=Path(sys.argv[1]); swc=root/'cell.swc'
swc.write_text('1 1 0 0 0 4 -1\n2 1 0 0 10 4 1\n3 2 0 0 1010 1 2\n4 3 100 0 10 1 2\n')
circuit=CircuitSpec().to_dict()
base={'swc_path':str(swc),'hh':circuit['hh'],'membrane':circuit['membrane'],'regional':True,'prefer_rostral_soma':False,
      'source_by_run':{'1':1,'2':2,'3':3,'4':4},'compartment_overrides':{},'compartment_mechanism_overrides':{}}
cells={n:dict(copy.deepcopy(base),neuron_id=n) for n in ('a','b')}
e=ExperimentSpec().to_dict();e.update(duration_ms=5.,integration_dt_ms=.025,temperature_C=22.,initial_voltage_mV=-65.,workers=1)
e['recording'].update(target_region='all',sample_dt_ms=.05,make_plots=False)
stim=dict(e['stimuli'][0],target_neuron_ids=['a'],target_region='soma',waveform='step',amplitude_nA=3.,delay_ms=1.,pulse_width_ms=1.)
condition={'name':'Control','stimulus_scale':1.,'chemical_synapses_enabled':True,'gap_junctions_enabled':False}
edges={'chemical_edges':[{'contact_id':'a-to-b','pre_id':'a','post_id':'b','post_run_node_id':2,'weight_uS':.01,'reversal_mV':0.,'tau1_ms':.5,'tau2_ms':3.,'delay_ms':.5}],
       'electrical_edges':[],'chemical_synapse_policy':{'spike_threshold_mV':-60.}}
traces={}
receipts={}
for mode in ('off','sparse','full'):
    e['recording']['current_tracking']=mode
    traces[mode],_=w._neuron_network_traces(cells,e,stim,condition,edges,None,'',current_output=root/mode)
    if mode!='off': receipts[mode]=read_index(root/mode/'index.json')
assert not (root/'off').exists()
for mode in ('sparse','full'):
    for key in traces['off']:
        assert traces[mode][key][0]==traces['off'][key][0]
        assert max(abs(a-b) for a,b in zip(traces[mode][key][1],traces['off'][key][1])) < 1e-9
full=receipts['full']; sparse=receipts['sparse']; times=full['times_ms']
assert len(sparse['compartments']) < len(full['compartments'])
assert len({c['site_id'] for c in full['compartments']})==len(full['compartments'])
for n in ('a','b'):
    specs=w._regional_plan(w._swc_rows(swc),cells[n])['sections']
    assert len([c for c in full['compartments'] if c['neuron_id']==n])==sum(s['nseg'] for s in specs)
    for s in specs:
        assert {c['cv_index'] for c in full['compartments'] if c['neuron_id']==n and c['section_id']==s['section_id']}==set(range(s['nseg']))
data=[(c,read_values(root/'full/index.json',c)) for c in full['channels']]
assert all(len(v)==len(times) for c,v in data)
assert {'membrane','axial','sodium','potassium','leak','capacitive','injected','synaptic'} <= {c['signal'] for c,v in data}
assert max(abs(v) for c,vs in data if c['signal']=='injected' for v in vs)>2.9
assert max(abs(v) for c,vs in data if c['signal']=='synaptic' for v in vs)>1e-6, max(v for t,vs in traces['full'].values() for v in vs)
errors=[]
for i,t in enumerate(times):
    im=sum(v[i] for c,v in data if c['signal']=='membrane')
    inj=sum(v[i] for c,v in data if c['signal']=='injected')
    errors.append(abs(im-inj))
assert max(errors)<1e-7, max(errors)
report={'full_compartments':len(full['compartments']),'sparse_compartments':len(sparse['compartments']),
        'signals':sorted({c['signal'] for c,v in data}),'samples':len(times),'maximum_balance_error_nA':max(errors),
        'off_sparse_full_voltage_difference_limit_mV':1e-9}
e['recording'].update(record_voltage=False,detect_spikes=False,target_region='selected_compartments')
current_only,_=w._neuron_network_traces(cells,e,stim,condition,edges,None,'',current_output=root/'current-only')
assert current_only=={}
assert len(read_index(root/'current-only/index.json')['compartments'])==len(full['compartments'])
report['current_only_ignores_unused_voltage_region']=True
e['recording'].update(record_voltage=True,detect_spikes=True,target_region='all')
report['waveform_current_channels']={}
for waveform in ('ramp','pulse_train'):
    stim_case=dict(stim,waveform=waveform,pulse_width_ms=1. if waveform=='ramp' else .2,
                   pulse_count=3,frequency_hz=1000.)
    w._neuron_network_traces(cells,e,stim_case,condition,edges,None,'',current_output=root/waveform)
    wi=read_index(root/waveform/'index.json')
    injected=[c for c in wi['channels'] if c['signal']=='injected']
    assert len(injected)==(1 if waveform=='ramp' else 3)
    assert max(abs(v) for c in injected for v in read_values(root/waveform/'index.json',c))>2.
    report['waveform_current_channels'][waveform]=len(injected)
project=Path(w.__file__).resolve().parents[3]
manifests=list((project/'outputs/workstation/packaged-feature-runs-20261005-v1/_runtime/neuron_mechanisms').glob('*/mechanism_manifest.json'))
if manifests:
    manifest=manifests[0]
    report['gap_modes']={}
    for mode in ('ohmic','rectifying','heterotypic_rectifying'):
        gedges={'chemical_edges':[],'electrical_edges':[{'neuron_a':'a','neuron_b':'b','contacts':[
            {'contact_id':'gap-ab','pre_id':'a','post_id':'b','pre_run_node_id':2,'post_run_node_id':2,'effective_g_uS':.001}]}],
            'gap_junction_policy':{'mode':mode,'preferred_direction':'a_to_b'}}
        cond=dict(condition,chemical_synapses_enabled=False,gap_junctions_enabled=True)
        w._neuron_network_traces(cells,e,stim,cond,gedges,manifest.parent,hashlib.sha256(manifest.read_bytes()).hexdigest(),current_output=root/mode)
        gi=read_index(root/mode/'index.json')
        gap_data=[read_values(root/mode/'index.json',c) for c in gi['channels'] if c['signal']=='gap']
        assert len(gap_data)==(1 if mode=='rectifying' else 2)
        assert max(abs(v) for values in gap_data for v in values)>1e-9
        if mode=='ohmic': assert max(abs(a+b) for a,b in zip(*gap_data))<1e-8
        report['gap_modes'][mode]=len(gap_data)
(root/'audit.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report))
'''
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]/"src"))
    env.pop("DISPLAY", None)
    result = subprocess.run([str(NATIVE), "-c", code, str(tmp_path)], env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    audit = json.loads((tmp_path/"audit.json").read_text())
    assert audit["full_compartments"] > audit["sparse_compartments"]
    # Saved-reader corruption is rejected, never converted into zero current.
    index = json.loads((tmp_path/"full/index.json").read_text())
    channel = dict(index["channels"][0], sha256="0"*64)
    with pytest.raises(ValueError, match="changed"):
        read_values(tmp_path/"full/index.json", channel)
