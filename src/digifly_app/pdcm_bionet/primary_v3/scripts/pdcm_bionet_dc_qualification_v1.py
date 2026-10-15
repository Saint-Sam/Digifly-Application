"""Preserved fresh root27/stim207 paired 1% drive qualification; not a cohort.

Never changes v3 sources. The parent supervisor must additionally freeze runtime
and enforce prospective resources. This adapter has no retry or background loop.
"""
from __future__ import annotations
import argparse
import contextlib
import csv
import json
import math
from pathlib import Path
import shutil
import sys
import time
import traceback
import h5py
import numpy as np
from scripts import build_pdcm_bionet_primary_v3 as builder
from scripts import run_pdcm_bionet_primary_v3 as original
from scripts.audit_pdcm_bionet_primary_v3 import validate_spikes
from scripts.validate_pdcm_bionet_measurements_v1 import checked_execution, sha

ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()
PINS = {
    "scripts/build_pdcm_bionet_primary_v3.py": "d634ac906a95ee33074f01b9976e10603832bf4a186c40c768288c35239bda73",
    "scripts/run_pdcm_bionet_primary_v3.py": "96074aa1db9d2a6badc8bf48a74631f379efd1922e79f79eb13b21ba6e2151dd",
    "scripts/probe_pdcm_bionet_independent.py": "f70403b8e9865e05a79a6a0a216aa681aa276082ee8fb3b48cac03b1c96faca0",
    "src/digifly_app/pdcm_bionet/mod/DigiflyPDCMEvent.mod": "d25f643ff90083706aadf4f2f992183d2d267289b9e38e542fca7439b74c1ebc",
    "scripts/audit_pdcm_bionet_primary_v3.py": "6a8ddd412ad5828428bffd6c4dc42e83f992679e9a2f16f90fbab33659562176",
    "scripts/validate_pdcm_bionet_measurements_v1.py": "4932728ecff056bb65a6380c31252ca76b8fe75a05a8deed9bd638cd6e1f204d",
    "src/digifly_app/workers/pdcm_netpyne_worker.py": "359823360444e9a1d759fe453dd614f7c7318ee276d00e9468b02aabba086f10",
}
DRIVES = ("balanced_poisson", "balanced_dc")
EXTERNAL = {"external_spikes.h5", "network/external_nodes.h5", "network/external_node_types.csv",
            "network/external_cortex_edges.h5", "network/external_cortex_edge_types.csv"}

def load(p):
    return json.loads(Path(p).read_text())

def write_new(p, value):
    with Path(p).open("x") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")

def source_gate():
    for name, digest in PINS.items():
        if sha(ROOT/name) != digest:
            raise ValueError(f"Reviewed dependency changed: {name}")

def inventory(p):
    return {str(q.relative_to(p)): {"bytes":q.stat().st_size,"sha256":sha(q)}
            for q in sorted(p.rglob("*")) if q.is_file() and q != p/"manifest.json"}

def extra_current(manifest):
    return math.sqrt(.01)*builder.INPUTS*float(manifest["base_exc_weight_pa"])*8.*.5*.001

def build(family: Path, duration_ms: float):
    source_gate()
    if type(duration_ms) not in (int,float) or duration_ms not in (200,60100):
        raise ValueError("Only 200 or 60100ms qualification")
    family = family.resolve()
    if family.exists():
        raise FileExistsError(family)
    family.mkdir(parents=True)
    p, dc = family/"balanced_poisson", family/"balanced_dc"
    base = builder.build(p, network_seed=27, stimulus_seed=207, duration_ms=duration_ms)
    original._verify_structure(p)
    dc.mkdir()
    for relative in base["files"]:
        if relative in EXTERNAL or relative == "structure_audit.json" or relative.startswith("components/"):
            continue
        target = dc/relative
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(p/relative,target)
    (dc/"components").mkdir()
    added = extra_current(base)
    for index,pop in enumerate(builder.POPS):
        dynamics = load(p/f"components/{pop}.json")
        dynamics["Iext"] += float(added[index])
        write_new(dc/f"components/{pop}.json",dynamics)
    shutil.copy2(p/"components/event.json",dc/"components/event.json")
    shutil.copy2(SELF,dc/"provenance"/SELF.name)
    manifest = dict(base)
    manifest.update(schema="digifly-paired-drive-qualification-structure-v1",drive="balanced_dc",
        external_nodes=0,external_contacts=0,external_events=0,external_rate_hz_by_population=[0.]*8,
        paired_poisson_input=str(p),paired_poisson_manifest_sha256=sha(p/"manifest.json"),
        adapter_sha256=sha(SELF),source_dependencies_sha256=PINS,
        dc_added_mean_drive_pa=added.tolist(),
        dc_total_current_pa=(np.asarray(base["dc_compensation_pa"])+added).tolist(),
        sampling="same own root27 graph, recurrent weights/delays and initial state; source-defined nominal DC; no external Poisson nodes/edges/events",
        caveats=base["caveats"]+["1% DC is outside the paper-active DC grid; silence is an outcome, not technical failure."])
    manifest["files"] = inventory(dc)
    write_new(dc/"manifest.json",manifest)
    record={"schema":"digifly-paired-drive-qualification-input-pair-v1","duration_ms":duration_ms,
            "network_seed":27,"stimulus_seed":207,"adapter_sha256":sha(SELF),
            "source_dependencies_sha256":PINS,
            "inputs_manifest_sha256":{drive:sha(family/drive/"manifest.json") for drive in DRIVES},
            "scientific_acceptance":False}
    write_new(family/"pair.json",record)
    for drive in DRIVES:
        verify_structure(family/drive)
    return record

def verify_structure(path: Path):
    source_gate()
    path=path.resolve()
    m=load(path/"manifest.json")
    if (m.get("scale")!=.01 or m.get("duration_ms") not in (200,60100)
            or m.get("network_seed")!=27 or m.get("stimulus_seed")!=207 or m.get("drive") not in DRIVES):
        raise ValueError("Wrong prospective root/condition")
    if m.get("clock_events")!=int(m["duration_ms"]/.025) or m.get("clock_nodes")!=1 or m.get("clock_contacts")!=767:
        raise ValueError("Exact clock count differs")
    with h5py.File(path/"clock_spikes.h5") as handle:
        clock=handle["spikes/clock"]
        if (not np.array_equal(clock["timestamps"][:],np.arange(m["clock_events"])*.025)
                or not np.all(clock["node_ids"][:]==0)):
            raise ValueError("Exact clock train differs")
    if m["drive"] == DRIVES[0]:
        return original._verify_structure(path)
    base_path=Path(m["paired_poisson_input"])
    if base_path.resolve()==path or sha(base_path/"manifest.json")!=m["paired_poisson_manifest_sha256"]:
        raise ValueError("Paired Poisson provenance differs")
    base=verify_structure(base_path)
    if base["duration_ms"] != m["duration_ms"]:
        raise ValueError("Pair durations differ")
    if m["files"] != inventory(path) or m.get("source_dependencies_sha256")!=PINS or m.get("adapter_sha256")!=sha(SELF):
        raise ValueError("DC file/source inventory differs")
    expected_files=(set(base["files"])-EXTERNAL-{"structure_audit.json"})|{"provenance/"+SELF.name}
    if set(m["files"]) != expected_files or sha(path/"provenance"/SELF.name)!=sha(SELF):
        raise ValueError("Unexpected DC input files")
    allowed_changes={"schema","drive","external_nodes","external_contacts","external_events",
                    "external_rate_hz_by_population","sampling","caveats","files"}
    for key,value in base.items():
        if key not in allowed_changes and m.get(key)!=value:
            raise ValueError(f"Paired construction contract changed: {key}")
    for relative,record in base["files"].items():
        if relative not in EXTERNAL|{"structure_audit.json"} and not relative.startswith("components/"):
            if m["files"].get(relative)!=record:
                raise ValueError(f"Paired graph/initialization/clock differs: {relative}")
    added=extra_current(base)
    total=np.asarray(base["dc_compensation_pa"])+added
    if (m["external_nodes"]!=0 or m["external_contacts"]!=0 or m["external_events"]!=0
            or m["external_rate_hz_by_population"]!=[0.]*8
            or not np.array_equal(m["dc_added_mean_drive_pa"],added)
            or not np.array_equal(m["dc_total_current_pa"],total)):
        raise ValueError("Nominal DC/no-Poisson contract differs")
    for index,pop in enumerate(builder.POPS):
        expected=load(base_path/f"components/{pop}.json")
        expected["Iext"]+=float(added[index])
        if load(path/f"components/{pop}.json")!=expected:
            raise ValueError("DC component current/initialization differs")
    if sha(path/"components/event.json")!=sha(base_path/"components/event.json"):
        raise ValueError("Signed delivery changed")
    return m

def input_prefix(small: Path, full: Path):
    a,b=verify_structure(small),verify_structure(full)
    if a["duration_ms"]!=200 or b["duration_ms"]!=60100 or a["drive"]!=b["drive"]:
        raise ValueError("Own-small/full condition mismatch")
    excluded={"structure_audit.json","clock_spikes.h5","external_spikes.h5"}
    # Adapter bytes remain fixed; components must match within each drive.
    fixed=lambda x:{k:v for k,v in x["files"].items() if k not in excluded}
    if fixed(a)!=fixed(b):
        raise ValueError("Own-small/full fixed graph/initialization/current differs")
    for name in ("clock", "external") if a["drive"]==DRIVES[0] else ("clock",):
        with h5py.File(small/f"{name}_spikes.h5") as x,h5py.File(full/f"{name}_spikes.h5") as y:
            first,second=x[f"spikes/{name}"],y[f"spikes/{name}"]
            take=second["timestamps"][:]<200
            for field in ("node_ids","timestamps"):
                if not np.array_equal(first[field][:],second[field][:][take]):
                    raise ValueError("Own-small/full event prefix differs")
    return True

def check_run_inventory(run):
    record=load(run/"artifact_manifest.json")
    actual={str(p.relative_to(run)):sha(p) for p in run.rglob("*") if p.is_file() and p!=run/"artifact_manifest.json"}
    if record.get("files")!=actual:
        raise ValueError("Run file set/hash differs")

def require_small(structure, small_run, small_execution):
    if small_run is None or small_execution is None:
        raise ValueError("Full requires own accepted 200ms run and measured receipt")
    measured=checked_execution(small_execution,420)
    small=audit_data(small_run)
    if small["duration_ms"]!=200 or measured.get("command") is None:
        raise ValueError("Small receipt lacks command binding")
    command=measured["command"]
    if not isinstance(command,list) or command[:2]!=["/usr/bin/time","-l"]:
        raise ValueError("Small execution command lacks exact time-tool binding")
    invocation=command[2:]
    if (len(invocation)!=9
            or invocation[1:5]!=["-B","-m","scripts.pdcm_bionet_dc_qualification_v1","run"]
            or command.count("--output")!=1 or command.count("--structure")!=1):
        raise ValueError("Small execution command is not bound")
    if (Path(command[command.index("--output")+1]).resolve()!=small_run.resolve()
            or Path(command[command.index("--structure")+1]).resolve()!=Path(load(small_run/"provenance.json")["source_structure"]).resolve()):
        raise ValueError("Small execution receipt belongs to another run")
    if invocation[0]!=small["python_executable"] or measured["output_bytes"]!=sum(p.stat().st_size for p in small_run.rglob("*") if p.is_file()):
        raise ValueError("Small measured invocation/storage differs")
    input_prefix(small_run/"input",structure)
    return small

def run(structure, output, small_run=None, small_execution=None):
    m=verify_structure(structure)
    if output.exists():raise FileExistsError(output)
    small=None
    if m["duration_ms"]==60100:
        small=require_small(structure,small_run,small_execution)
    import bmtk
    import neuron
    from neuron import h
    from bmtk.simulator import bionet
    from bmtk.simulator.bionet.io_tools import io as bio_io
    if small and (small["python_executable"]!=sys.executable or small["bmtk_version"]!=bmtk.__version__ or small["neuron_version"]!=neuron.__version__):
        raise ValueError("Own-small/full runtime invocation/version differs")
    output.mkdir(parents=True)
    shutil.copytree(structure,output/"input")
    verify_structure(output/"input")
    shutil.copy2(SELF,output/SELF.name)
    for relative in PINS:
        target=output/"provenance_sources"/relative
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(ROOT/relative,target)
    import importlib.util
    spec=importlib.util.spec_from_file_location("dc_qualification_probe",original.PROBE)
    probe=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    mod=probe._mechanisms(output)
    compiled=list(mod.glob("*/libnrnmech.*"))
    if len(compiled)!=1:raise ValueError("Expected one mechanism binary")
    neuron.load_mechanisms(str(mod))
    @bionet.cell_model("digifly:DigiflyPDCMEvent",model_type="point_neuron")
    def cell(node,template,dynamics):
        value=h.DigiflyPDCMEvent()
        value.Iext=float(dynamics["Iext"]);value.m=float(node["m_init"]);value.i=0.
        return value
    copied=output/"input"
    names=("cortex","external","clock") if m["drive"]==DRIVES[0] else ("cortex","clock")
    inputs={name+"_input":{"input_type":"spikes","module":"sonata","node_set":name+"_input",
                            "input_file":str((copied/f"{name}_spikes.h5").resolve())} for name in names if name!="cortex"}
    native=output/"native_output"
    config={"target_simulator":"BioNet","run":{"tstart":0.,"tstop":m["duration_ms"],"dt":.025,"nsteps_block":400},
        "conditions":{"v_init":-65.,"celsius":34.},
        "components":{"point_neuron_models_dir":str((copied/"components").resolve()),"synaptic_models_dir":str((copied/"components").resolve()),"mechanisms_dir":str(mod.resolve())},
        "networks":{"nodes":[{"nodes_file":str((copied/f"network/{n}_nodes.h5").resolve()),"node_types_file":str((copied/f"network/{n}_node_types.csv").resolve())} for n in names],
                    "edges":[{"edges_file":str((copied/f"network/{n}_cortex_edges.h5").resolve()),"edge_types_file":str((copied/f"network/{n}_cortex_edge_types.csv").resolve())} for n in names]},
        "node_sets":{n+"_input":{"population":n} for n in names if n!="cortex"},"inputs":inputs,
        "output":{"output_dir":str(native.resolve()),"spikes_file":str((native/"spikes.h5").resolve()),"spikes_sort_order":"by_time","overwrite_output_dir":False}}
    write_new(output/"config.json",config)
    provenance={"schema":"digifly-paired-drive-qualification-execution-v1","source_structure":str(structure.resolve()),
        "drive":m["drive"],"duration_ms":m["duration_ms"],"network_seed":27,"stimulus_seed":207,
        "python_executable":sys.executable,"bmtk_version":bmtk.__version__,"neuron_version":neuron.__version__,
        "adapter_sha256":sha(SELF),"source_dependencies_sha256":PINS,"input_manifest_sha256":sha(copied/"manifest.json"),
        "mechanism_source_sha256":sha(original.MOD),"mechanism_compiled_sha256":sha(compiled[0]),
        "own_small_run_manifest_sha256":sha(small_run/"artifact_manifest.json") if small else None,
        "own_small_run":str(small_run.resolve()) if small else None,
        "own_small_execution":str(small_execution.resolve()) if small else None,
        "own_small_execution_sha256":sha(small_execution/"execution.json") if small else None,"scientific_acceptance":False}
    write_new(output/"provenance.json",provenance)
    with (output/"run.log").open("x") as log,contextlib.redirect_stdout(log),contextlib.redirect_stderr(log),original._capture_bionet_log(bio_io,log):
        cfg=bionet.Config.from_json(str(output/"config.json"));cfg.build_env()
        net=bionet.BioNetwork.from_config(cfg)
        start=time.monotonic();bionet.BioSimulator.from_config(cfg,network=net).run();elapsed=time.monotonic()-start
    result=dict(provenance,state="completed_technical_unreviewed",simulation_walltime_s=elapsed)
    write_new(output/"technical_result.json",result)
    write_new(output/"artifact_manifest.json",{"files":{str(p.relative_to(output)):sha(p) for p in output.rglob("*") if p.is_file()}})
    return result

def audit_data(run):
    source_gate();check_run_inventory(run)
    m=verify_structure(run/"input");p=load(run/"provenance.json");t=load(run/"technical_result.json");cfg=load(run/"config.json")
    if any(t.get(k)!=v for k,v in p.items()) or t.get("state")!="completed_technical_unreviewed" or p.get("scientific_acceptance") is not False:
        raise ValueError("Execution identity/state differs")
    if p["input_manifest_sha256"]!=sha(run/"input/manifest.json") or p["adapter_sha256"]!=sha(SELF) or sha(run/SELF.name)!=sha(SELF) or p["source_dependencies_sha256"]!=PINS:
        raise ValueError("Executed adapter/input/source identity differs")
    for relative,digest in PINS.items():
        if sha(run/"provenance_sources"/relative)!=digest:raise ValueError("Executed dependency differs")
    for key in ("drive","duration_ms","network_seed","stimulus_seed"):
        if p[key]!=m[key]:raise ValueError("Run/structure condition differs")
    binaries=list((run/"mechanisms").glob("*/libnrnmech.*"))
    if len(binaries)!=1 or sha(binaries[0])!=p["mechanism_compiled_sha256"] or sha(run/"mechanisms/DigiflyPDCMEvent.mod")!=p["mechanism_source_sha256"]:
        raise ValueError("Executed compiled/source mechanism differs")
    if cfg["run"]!={"tstart":0.,"tstop":m["duration_ms"],"dt":.025,"nsteps_block":400}:
        raise ValueError("Executed integration configuration differs")
    if (cfg.get("target_simulator")!="BioNet" or cfg.get("conditions")!={"v_init":-65.,"celsius":34.}
            or cfg.get("components")!={"point_neuron_models_dir":str((run/"input/components").resolve()),
                "synaptic_models_dir":str((run/"input/components").resolve()),"mechanisms_dir":str((run/"mechanisms").resolve())}
            or cfg.get("output")!={"output_dir":str((run/"native_output").resolve()),
                "spikes_file":str((run/"native_output/spikes.h5").resolve()),"spikes_sort_order":"by_time","overwrite_output_dir":False}):
        raise ValueError("Executed model/recording route differs")
    names=("cortex","external","clock") if m["drive"]==DRIVES[0] else ("cortex","clock")
    if {Path(n["nodes_file"]).name for n in cfg["networks"]["nodes"]}!={n+"_nodes.h5" for n in names} or {Path(n["edges_file"]).name for n in cfg["networks"]["edges"]}!={n+"_cortex_edges.h5" for n in names} or set(cfg["inputs"])!={n+"_input" for n in names if n!="cortex"}:
        raise ValueError("Actual drive routing differs")
    if cfg.get("node_sets")!={n+"_input":{"population":n} for n in names if n!="cortex"} or len(cfg["networks"]["nodes"])!=len(names) or len(cfg["networks"]["edges"])!=len(names):
        raise ValueError("Actual node-set routing differs")
    for n in names:
        for kind in ("nodes","edges"):
            field="nodes_file" if kind=="nodes" else "edges_file"
            filename=f"{n}_nodes.h5" if kind=="nodes" else f"{n}_cortex_edges.h5"
            row=next(x for x in cfg["networks"][kind] if Path(x[field]).name==filename)
            if Path(row[field]).resolve()!=(run/"input/network"/filename).resolve():raise ValueError("Actual network route is not owned input")
            type_field="node_types_file" if kind=="nodes" else "edge_types_file"
            type_name=f"{n}_node_types.csv" if kind=="nodes" else f"{n}_cortex_edge_types.csv"
            if Path(row[type_field]).resolve()!=(run/"input/network"/type_name).resolve():raise ValueError("Actual type route differs")
    for n in names:
        if n!="cortex" and cfg["inputs"][n+"_input"]!={"input_type":"spikes","module":"sonata","node_set":n+"_input","input_file":str((run/"input"/f"{n}_spikes.h5").resolve())}:raise ValueError("Actual event route differs")
    if not all(term in (run/"run.log").read_text() for term in ("Building cells","Running simulation","Simulation completed")):
        raise ValueError("Actual lifecycle incomplete")
    with h5py.File(run/"native_output/spikes.h5") as h:
        ids=h["spikes/cortex/node_ids"][:];times=h["spikes/cortex/timestamps"][:]
    validate_spikes(ids,times,767,m["duration_ms"])
    if m["duration_ms"]==60100:
        small_run=Path(p["own_small_run"]);small_execution=Path(p["own_small_execution"])
        require_small(run/"input",small_run,small_execution)
        if sha(small_run/"artifact_manifest.json")!=p["own_small_run_manifest_sha256"] or sha(small_execution/"execution.json")!=p["own_small_execution_sha256"]:
            raise ValueError("Bound own-small evidence changed")
        with h5py.File(small_run/"native_output/spikes.h5") as handle:
            small_ids=handle["spikes/cortex/node_ids"][:];small_times=handle["spikes/cortex/timestamps"][:]
        # Compare the declared half-open interval; inclusive200ms is not inferred.
        small_take=small_times<200;full_take=times<200
        if not np.array_equal(small_ids[small_take],ids[full_take]) or not np.array_equal(small_times[small_take],times[full_take]):
            raise ValueError("Own-small/full cortical output prefix differs")
    with h5py.File(run/"input/network/cortex_nodes.h5") as h:initial=h["nodes/cortex/0/m_init"][:]
    currents=np.repeat(m.get("dc_total_current_pa",m["dc_compensation_pa"]),m["scaled_counts"])
    predicted=np.flatnonzero(initial+.5*((-65-initial)/10+currents/250)>-50)
    if not np.array_equal(np.sort(ids[np.isclose(times,.5,atol=1e-12,rtol=0)]),predicted):raise ValueError("First clock oracle differs")
    settled=(times>=100)&(times<m["duration_ms"])
    counts=np.bincount(np.searchsorted(np.cumsum(m["scaled_counts"]),ids[settled],side="right"),minlength=8)
    with h5py.File(run/"input/network/cortex_cortex_edges.h5") as h:
        g=h["edges/cortex_to_cortex"];sources=g["source_node_id"][:];delays=g["0/delay"][:]
    arrivals=ties=0
    for node in np.unique(ids):
        events=times[ids==node,None]+delays[sources==node][None,:]
        within=events<=m["duration_ms"];grid=np.rint((events-.5)/.025)
        arrivals+=int(np.count_nonzero(within))
        ties+=int(np.count_nonzero(within&(grid>=0)&(grid<m["clock_events"])&(np.abs(events-(.5+grid*.025))<=1e-9)))
    return dict(p,technical_acceptance=True,cortical_spikes=len(ids),settled_spikes=int(sum(counts)),
        startup_spikes_at_0_5ms=len(predicted),recurrent_arrivals_within_run=arrivals,
        recurrent_arrival_clock_grid_ties_1ns_tolerance=ties,
        all_cell_rates_hz=(counts/(np.asarray(m["scaled_counts"])*(m["duration_ms"]-100)/1000)).tolist(),
        run_manifest_sha256=sha(run/"artifact_manifest.json"),limitations=["Technical/finite-window only, no cohort or equivalence.","Shared NEURON, no solver independence.","Sampled parent resources are not kernel quotas.","Arrival ties do not prove internal event ordering."])

def paper_outputs(output,ids,times,m):
    """Full-window outputs with both normalizations and explicit undefined census."""
    from digifly_app.workers.pdcm_netpyne_worker import _paper_statistics
    if m["duration_ms"]!=60100 or m["scaled_counts"]!=np.floor(.01*builder.FULL).astype(int).tolist():
        raise ValueError("Not the full1% declared paper-window structure")
    validate_spikes(ids,times,767,60100)
    ends=np.cumsum(m["scaled_counts"]);begins=np.r_[0,ends[:-1]]
    gids={pop:list(range(int(begins[i]),int(ends[i]))) for i,pop in enumerate(builder.POPS)}
    pops=np.searchsorted(ends,ids,side="right")
    spikes=[(float(t),int(n),builder.POPS[int(p)]) for n,t,p in zip(ids,times,pops)]
    if not _paper_statistics(output,gids_by_population=gids,spikes=spikes,duration_ms=60100.,sample_seed=207):
        raise ValueError("Paper windows absent")
    with (output/"paper_metrics.csv").open(newline="") as stream:rows=list(csv.DictReader(stream))
    if [r["population"] for r in rows]!=list(builder.POPS):raise ValueError("Incomplete paper populations")
    with (output/"synchrony_metric_comparison.csv").open("x",newline="") as stream:
        writer=csv.writer(stream)
        writer.writerow(("population","sampled_cells","population_total_fano_3ms_5s","population_total_fano_3ms_60s","per_cell_normalized_3ms_5s","per_cell_normalized_3ms_60s"))
        for row in rows:
            n=int(row["sampled_cells"])
            values=[float(row[f"synchrony_3ms_{w}"]) if row[f"synchrony_3ms_{w}"] else None for w in ("5s","60s")]
            if any(v is not None and not math.isfinite(v) for v in values):raise ValueError("Nonfinite synchrony output")
            writer.writerow([row["population"],n,*[n*v if v is not None else "" for v in values],*[v if v is not None else "" for v in values]])
    with (output/"defined_cell_census.csv").open("x",newline="") as stream:
        writer=csv.writer(stream)
        writer.writerow(("population","all_cells","settled_active_cells","cv_defined_cells","cv_undefined_cells","correlation_defined_cells","correlation_undefined_cells"))
        for index,pop in enumerate(builder.POPS):
            active=cv=corr=0;n=len(gids[pop])
            for node in gids[pop]:
                train=times[(ids==node)&(times>=100)&(times<60100)]
                active+=int(len(train)>0)
                cv+=int(len(train)>=3 and float(np.diff(train).mean())>0)
                counts=np.histogram(train[train<5100],bins=100+np.arange(201)*25)[0]
                corr+=int(np.var(counts)>0)
            if int(rows[index]["sampled_cells"])!=n or int(rows[index]["correlation_defined_cells"])!=corr:
                raise ValueError("Shared metric/cell census differs")
            writer.writerow((pop,n,active,cv,n-cv,corr,n-corr))

def audit(run,output):
    if output.exists():raise FileExistsError(output)
    result=audit_data(run);output.mkdir(parents=True)
    m=load(run/"input/manifest.json")
    if m["duration_ms"]==60100:
        with h5py.File(run/"native_output/spikes.h5") as h:
            ids=h["spikes/cortex/node_ids"][:];times=h["spikes/cortex/timestamps"][:]
        paper_outputs(output,ids,times,m)
        result["analysis_windows_ms"]={"settled":[100,60100],"correlation":[100,5100]}
    write_new(output/"audit.json",result)
    write_new(output/"manifest.json",{"adapter_sha256":sha(SELF),"source_run_manifest_sha256":result["run_manifest_sha256"],"files":inventory(output)})
    return result

def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest="mode",required=True)
    b=sub.add_parser("build");b.add_argument("--family",type=Path,required=True);b.add_argument("--duration-ms",type=int,choices=(200,60100),required=True)
    r=sub.add_parser("run");r.add_argument("--structure",type=Path,required=True);r.add_argument("--output",type=Path,required=True);r.add_argument("--small-run",type=Path);r.add_argument("--small-execution",type=Path)
    a=sub.add_parser("audit");a.add_argument("--run",type=Path,required=True);a.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    try:
        if args.mode=="build":result=build(args.family,args.duration_ms)
        elif args.mode=="run":result=run(args.structure.resolve(),args.output.resolve(),args.small_run,args.small_execution)
        else:result=audit(args.run.resolve(),args.output.resolve())
    except Exception:
        if args.mode=="run" and args.output.exists() and not (args.output/"artifact_manifest.json").exists():
            write_new(args.output/"failure.json",{"state":"failed_preserved","traceback":traceback.format_exc()})
            write_new(args.output/"artifact_manifest.json",{"files":{str(p.relative_to(args.output)):sha(p) for p in args.output.rglob("*") if p.is_file()}})
        raise
    print(json.dumps(result,sort_keys=True,allow_nan=False))

if __name__=="__main__":main()
