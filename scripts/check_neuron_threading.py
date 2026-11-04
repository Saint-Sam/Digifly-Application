"""Bounded numerical engineering check in a selected NEURON interpreter.

This uses four tiny artificial morphologies. It does not execute a user circuit.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import time


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--worker',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--gap-root',type=Path)
    parser.add_argument('--waveform',choices=('step','ramp','pulse_train'),default='step')
    parser.add_argument('--sample-dt',type=float,default=.05)
    parser.add_argument('--native-run',type=Path)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    spec=importlib.util.spec_from_file_location('thread_check_worker',args.worker)
    worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
    hh=dict(ra_ohm_cm=100.,cm_uF_cm2=1.,g_pas_s_cm2=.0001,e_pas_mV=-65.,ena_mV=65.,ek_mV=-74.,eca_mV=120.,
            v_init_mV=-65.,celsius_C=22.,soma_gnabar_s_cm2=.12,soma_gkbar_s_cm2=.036,soma_gl_s_cm2=.0003,
            soma_el_mV=-65.,branch_gnabar_s_cm2=.02,branch_gkbar_s_cm2=.01,branch_gl_s_cm2=.0001,branch_el_mV=-65.,active_scope='all')
    cells={}
    for n in ('a','b','c','d'):
        path=args.output/f'{n}.swc'
        path.write_text('1 1 0 0 0 5 -1\n2 1 0 10 0 5 1\n3 2 0 30 0 1 2\n4 2 0 80 0 1 3\n5 3 20 30 0 1 2\n')
        cells[n]=dict(swc_path=str(path),hh=hh,membrane={},prefer_rostral_soma=False)
    if args.native_run:
        saved=json.loads((args.native_run/'run_manifest.json').read_text())['native_channel_execution']
        worker._load_precompiled_native_channels(saved)
        membrane=json.loads((args.native_run/'worker_request.json').read_text())['circuit']['membrane']
        for cell in cells.values():cell['membrane']=membrane
    stimulus=dict(target_neuron_ids=['a'],target_region='soma',waveform=args.waveform,amplitude_nA=2.,delay_ms=1.,pulse_width_ms=2.,pulse_count=1,frequency_hz=100.)
    experiment=dict(duration_ms=8.,integration_dt_ms=.005,temperature_C=22.,initial_voltage_mV=-65.,workers=1,
        recording=dict(target_neuron_ids=[],target_region='soma',record_voltage=True,detect_spikes=True,sample_dt_ms=args.sample_dt,current_tracking='off'))
    condition=dict(name='Control',gap_junctions_enabled=True,chemical_synapses_enabled=True,stimulus_scale=1.)
    chemical=[dict(contact_id='a-b',pre_id='a',post_id='b',post_run_node_id=4,reversal_mV=0.,tau1_ms=.2,tau2_ms=2.,weight_uS=.05,delay_ms=.1)]
    gap=[dict(neuron_a='b',neuron_b='c',contacts=[dict(contact_id='b-c',pre_id='b',post_id='c',pre_run_node_id=4,post_run_node_id=4,effective_g_uS=.02)])]
    gap_hash=worker._sha256(args.gap_root/'mechanism_manifest.json') if args.gap_root else ''
    outcomes=[]
    modes=['chemical']+(['ohmic','rectifying','heterotypic_rectifying'] if args.gap_root else [])
    from neuron import h
    for mode in modes:
        edges=dict(chemical_edges=chemical,electrical_edges=[] if mode=='chemical' else gap,
                   gap_junction_policy=dict(mode=mode,preferred_direction='a_to_b'))
        for current in (('off','sparse','full') if abs(args.sample_dt/.005-round(args.sample_dt/.005))<1e-10 else ('off',)):
            baseline=None
            for count in (1,2,4):
                receipts=[];start=time.perf_counter()
                config={**experiment,'neuron_multithreading':True,'neuron_threads':count,
                        'recording':{**experiment['recording'],'current_tracking':current}}
                folder=args.output/f'{mode}-{current}-{count}'
                traces,version=worker._neuron_network_traces(cells,config,stimulus,condition,edges,args.gap_root,gap_hash,
                    current_output=folder,thread_receipts=receipts)
                (args.output/f'{mode}-{current}-{count}-voltage.json').write_text(json.dumps(traces,default=list))
                error=0.
                if baseline is None:baseline=traces
                else:
                    assert set(traces)==set(baseline)
                    for key,(clock,values) in traces.items():
                        assert len(clock)==len(baseline[key][0])
                        assert all(abs(a-b)<=1e-10 for a,b in zip(clock,baseline[key][0]))
                        error=max(error,max(abs(a-b) for a,b in zip(values,baseline[key][1])))
                assert error<1e-8, (mode,current,count,error)
                spikes={str(key):worker._crossings(*trace,-20.) for key,trace in traces.items()}
                expected={str(key):worker._crossings(*trace,-20.) for key,trace in baseline.items()}
                assert set(spikes)==set(expected)
                for key in spikes:
                    assert len(spikes[key])==len(expected[key])
                    assert all(abs(a-b)<=1e-9 for a,b in zip(spikes[key],expected[key]))
                current_error=0.
                if current!='off':
                    index=worker._currents.read_index(folder/'index.json')
                    values=[v for channel in index['channels'] for v in worker._currents.read_values(folder/'index.json',channel)]
                    if count==1:baseline_current=values
                    else:
                        assert len(values)==len(baseline_current)
                        current_error=max(abs(a-b) for a,b in zip(values,baseline_current))
                        assert current_error<1e-8,(mode,current,count,current_error)
                assert int(h.ParallelContext().nthread())==1
                result=dict(mode=mode,current=current,threads=count,version=version,seconds=time.perf_counter()-start,
                    max_voltage_error_mV=error,max_current_error_nA=current_error,spikes=spikes,execution=receipts)
                outcomes.append(result)
                (args.output/'results.json').write_text(json.dumps(dict(ok=False,trials=outcomes),indent=2))
                print(mode,current,count,'voltage error',error,'current error',current_error,flush=True)
    (args.output/'results.json').write_text(json.dumps(dict(ok=True,trials=outcomes),indent=2))


if __name__=='__main__':main()
