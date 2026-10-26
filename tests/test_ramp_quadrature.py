"""Pure physical-current quadrature tests; never construct a cell or simulate."""
import bisect
import copy
import json
import math
from types import SimpleNamespace

import pytest

from digifly_app.workers import generic_experiment_worker as common


def envelope_at(points,time):
    index=bisect.bisect_right([p[0] for p in points],time)-1
    if index<0:return 0.
    if index==len(points)-1:return points[index][1]
    a,b=points[index:index+2]
    return a[1]+(b[1]-a[1])*(time-a[0])/(b[0]-a[0])


def steps(final,dt,epoch=None):
    start=0.
    while start<final:
        stop=min(start+(epoch if epoch else final),final)
        index=0
        while start+index*dt<stop-1e-14:
            left=start+index*dt
            yield left,min(start+(index+1)*dt,stop)
            index+=1
        start=stop


@pytest.mark.parametrize("start,width,amplitude,dt,final,epoch",[
    (1.,2.,.01,.0125,5.,None),
    (0.,2.,.01,.0125,5.,None),
    (.007,.037,-.03,.0125,.071,None),
    (0.,.25,1.,.1,.25,None),  # Cutoff exactly on the old translated left knot.
    (.015,.04,.1,.02,.052,None),
    (.02,.09,.3,.01,.145,.035),
    (0.,.037,.2,.01,.041,.035),
    (0.,.035,1.,.01,.105,None),  # Exact floating cutoff missed by plain -dt/2 shift.
    (0.,.08,0.,.01,.081,.025),
])
def test_arbor_left_api_equals_unchanged_physical_current_at_actual_midpoint(start,width,amplitude,dt,final,epoch):
    source={"waveform":"ramp","delay_ms":start,"pulse_width_ms":width}
    original=copy.deepcopy(source)
    translated=common._arbor_ramp_envelope(source,amplitude,dt,final,epoch)
    assert source==original
    assert all(a[0]<=b[0] for a,b in zip(translated,translated[1:]))
    for left,right in steps(final,dt,epoch):
        expected=common._physical_ramp_current(source,amplitude,left+.5*(right-left))
        assert envelope_at(translated,left)==pytest.approx(expected,abs=1e-13)


def test_aligned_ramp_charge_is_correct_without_input_or_timestep_tuning():
    source={"waveform":"ramp","delay_ms":1.,"pulse_width_ms":2.}
    dt=.0125
    translated=common._arbor_ramp_envelope(source,.01,dt,5.)
    old=common._ramp_envelope(source,.01)
    charge=sum(envelope_at(translated,left)*(right-left) for left,right in steps(5.,dt))
    old_charge=sum(envelope_at(old,left)*(right-left) for left,right in steps(5.,dt))
    assert charge==pytest.approx(.01,abs=1e-14)
    assert old_charge==pytest.approx(.0099375,abs=1e-14)
    assert charge-old_charge==pytest.approx(.0000625,abs=1e-14)


def test_plain_shift_exact_floating_cutoff_failure_is_repaired_without_epsilon():
    source={"waveform":"ramp","delay_ms":0.,"pulse_width_ms":.035}
    left,right=.03,.04
    midpoint=left+.5*(right-left)
    assert midpoint==.035 and common._physical_ramp_current(source,1.,midpoint)==0.
    shifted=[(t-.01/2,a) for t,a in common._ramp_envelope(source,1.)]
    assert shifted[-1][0] > left and envelope_at(shifted,left) > .999999
    translated=common._arbor_ramp_envelope(source,1.,.01,.105)
    assert envelope_at(translated,left)==0. and (left,0.) in translated
    assert source["pulse_width_ms"]==.035


def test_right_continuous_exact_grid_cutoff_for_zero_delay():
    source={"waveform":"ramp","delay_ms":0.,"pulse_width_ms":2.}
    translated=common._arbor_ramp_envelope(source,.01,.0125,5.)
    assert (2.,.01) in translated and (2.,0.) in translated
    assert envelope_at(translated,2.)==0.
    assert envelope_at(translated,0.)==pytest.approx(.01*.00625/2)
    assert common._physical_ramp_current(source,.01,-1e-9)==0.
    assert common._physical_ramp_current(source,.01,2.)==0.


@pytest.mark.parametrize("dt,final,epoch",[(0,5,None),(-1,5,None),(math.nan,5,None),
    (math.inf,5,None),(.1,0,None),(.1,math.inf,None),(.1,5,0),(.1,5,.01)])
def test_invalid_quadrature_parameters_fail_closed(dt,final,epoch):
    with pytest.raises(ValueError,match="quadrature"):
        common._arbor_ramp_envelope({"delay_ms":1,"pulse_width_ms":2},.01,dt,final,epoch)


def test_runtime_clamp_helper_requires_dt_and_preserves_step_path():
    arbor=SimpleNamespace(units=SimpleNamespace(ms=1.,nA=1.),i_clamp=lambda *args:args)
    source={"waveform":"ramp","delay_ms":1,"pulse_width_ms":2}
    with pytest.raises(ValueError,match="explicit integration"):
        common._arbor_current_clamps(arbor,source,.01)
    actual=common._arbor_current_clamps(arbor,source,.01,integration_dt_ms=.0125,duration_ms=5.)
    assert actual==[(common._arbor_ramp_envelope(source,.01,.0125,5.),)]
    source["waveform"]="step"
    assert common._arbor_current_clamps(arbor,source,.01)==[(1.,2.,.01)]


def test_chemical_epoch_precision_and_disabled_condition_are_explicit():
    edges={"chemical_edges":[{"delay_ms":.07},{"delay_ms":.2}]}
    interval=common._arbor_ramp_epoch_interval(edges,True)
    assert interval==.03500000014901161  # native float32 delay /2
    assert common._arbor_ramp_epoch_interval(edges,False) is None
    assert common._arbor_ramp_epoch_interval({"chemical_edges":[]},True) is None


def test_run_receipt_preserves_source_and_condition_amplitudes_without_fake_ablated_clamps():
    experiment={"integration_dt_ms":.0125,"duration_ms":5.}
    source={"waveform":"ramp","delay_ms":1.,"pulse_width_ms":2.,"target_neuron_ids":["1"],
        "target_region":"soma","amplitude_nA":.01}
    condition={"name":"Scaled","stimulus_scale":2.}
    record=common._ramp_quadrature_record("arbor",experiment,source,condition,{"1":{}},{"chemical_edges":[]})
    assert record["physical_stimulus"]==source and record["integration_dt_ms"]==.0125
    assert record["api_time_offset_ms"]==0. and record["nominal_midpoint_offset_ms"]==.00625
    assert record["sites"][0]["physical_envelope_ms_nA"]==[(1.,0.),(3.,.02),(3.,0.)]
    assert envelope_at(record["sites"][0]["api_envelope_ms_nA"],1.)==pytest.approx(.02*.00625/2.)
    assert envelope_at(record["sites"][0]["api_envelope_ms_nA"],3.)==0.
    assert common._ramp_quadrature_record("arbor",experiment,source,condition,{},{} )["sites"]==[]
    neuron=common._ramp_quadrature_record("neuron",experiment,source,condition,{"1":{}},{})
    assert neuron["api_time_offset_ms"]==0.
    assert neuron["sites"][0]["api_envelope_ms_nA"]==record["sites"][0]["physical_envelope_ms_nA"]


def test_generic_production_writes_source_bound_ramp_receipt(tmp_path,monkeypatch):
    from dataclasses import replace
    from test_generic_experiment import _morphology,_morphology_hashes,_experiment,_passing_runtime
    from digifly_app.core.circuit import CircuitSpec
    from digifly_app.core.connectomes import ConnectomeRef
    from digifly_app.engines import generic_experiment as generic
    import sys
    morphology=_morphology(tmp_path)
    circuit=CircuitSpec(connectome=ConnectomeRef("test:v1","Test",str(tmp_path)),neuron_ids=("1",),
        morphology_sha256=_morphology_hashes((morphology,)))
    experiment=_experiment("arbor")
    experiment=replace(experiment,stimuli=(replace(experiment.stimuli[0],waveform="ramp"),))
    monkeypatch.setattr(generic,"_runtime_check",_passing_runtime)
    adapter=generic.GenericExperimentAdapter({"arbor":sys.executable})
    report=adapter.validate(circuit,experiment,(morphology,),output_root=tmp_path/"runs")
    assert report.ok
    plan=adapter.plan(circuit,experiment,output_root=tmp_path/"runs")
    request=adapter.write_request(plan.arguments[-1],adapter.request_payload(circuit,experiment,(morphology,),report,output_root=tmp_path/"runs"))
    source_stimulus=json.loads(request.read_text())["experiment"]["stimuli"][0]
    monkeypatch.setattr(common,"_arbor_trace",lambda *args,**kwargs:([0,1,2],[-65,-64,-63],"inert fixture"))
    monkeypatch.setattr(common,"_prepare_arbor_channels",lambda *args,**kwargs:{})
    summary=json.loads(common.run(request).read_text())
    receipt=json.loads((request.parent/"stimulus_quadrature.json").read_text())
    assert receipt["schema"]=="digifly-ramp-quadrature-v1" and receipt["output_timestamp_adjustment_ms"]==0.
    assert all(c["physical_stimulus"]==source_stimulus for c in receipt["conditions"])
    assert any(a["path"]=="stimulus_quadrature.json" for a in summary["artifacts"])
