"""Independent released-method SONATA constructor with bounded I/O, version 4.

This is construction, not execution admission.  No author builder, graph, random
stream or mechanism is imported.  Accepted primary-v3 sources remain unchanged.
Rows retain their rule/source order: standard SONATA multi-range indices are
constructed by two bounded scans, never by sorting the complete graph.  Graph,
input and structural verification buffers are capped independently of duration.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import shutil

import h5py
import numpy as np

SCHEMA = "digifly-pdcm-bionet-scalable-v4-structure"
POLICY = "released_method_fixed_unique_integer_variance_scalable_v4"
CHUNK_ROWS = 65536
EVENT_BATCH = 4096
DT_MS = .025
INPUT_DELAY_MS = .5
GiB = 1024**3
POPS = ("L2e", "L2i", "L4e", "L4i", "L5e", "L5i", "L6e", "L6i")
FULL = np.array((20683, 5834, 21915, 5479, 4850, 1065, 14395, 2948), dtype=np.int64)
INPUTS = np.array((1600, 1500, 2100, 1900, 2000, 1900, 2900, 2100), dtype=np.int64)
DEPTH = ((.08,.27),(.08,.27),(.27,.58),(.27,.58),(.58,.73),(.58,.73),(.73,1.),(.73,1.))
P = np.array(((.1009,.1689,.0437,.0818,.0323,0,.0076,0),
              (.1346,.1371,.0316,.0515,.0755,0,.0042,0),
              (.0077,.0059,.0497,.135,.0067,.0003,.0453,0),
              (.0691,.0029,.0794,.1597,.0033,0,.1057,0),
              (.1004,.0622,.0505,.0057,.0831,.3726,.0204,0),
              (.0548,.0269,.0257,.0022,.06,.3158,.0086,0),
              (.0156,.0066,.0211,.0166,.0572,.0197,.0396,.2252),
              (.0364,.001,.0034,.0005,.0277,.008,.0658,.1443)), dtype=float)
FOUT = np.array((.860,2.600,4.306,5.396,8.142,8.188,.941,7.3))
EDGE_FIELDS = {"source_node_id":"u8", "target_node_id":"u8", "edge_type_id":"u4",
               "edge_group_id":"u2", "edge_group_index":"u8", "syn_weight":"f8",
               "delay":"f8", "nsyns":"u4"}
SOURCE_CONTRACT = {
    "population_counts":"floor(scale*full_counts)",
    "divergence":"round(scale*full_contacts/full_source_count), capped at target_count-1",
    "targets":"uniform unique non-self; no replacement; sustained fixed count",
    "normal":"mean_variance; numpy_sd=sqrt(variance); hard max clipping",
    "initialization":"independent per-cell N(-58mV,variance10mV^2)",
    "dc":"integer-truncated full incoming contacts; nominal external mean added only for DC",
    "network_streams":"PCG64 SeedSequence(network_seed).spawn(4): positions, targets, properties, initialization",
    "stimulus_streams":"PCG64 SeedSequence([stimulus_seed,node_id]); independent exponential renewal per cell",
    "clock":"explicit 40kHz source events tick*0.025ms beginning at t=0; zero weight; 0.5ms delivery",
    "same_time_events":"baseline independent mechanism unchanged; native/BioNet ties not assumed identical",
}
STREAMING_POLICY = {
    "edge_rows_per_buffer":CHUNK_ROWS, "event_rows_per_buffer":EVENT_BATCH,
    "indexing":"two bounded owner-run scans; no full graph sorting",
    "validation":"bounded deterministic rule/source replay and chunked inputs/indices",
    "statistics":"exact counts, minima/maxima and ties; no full-column quantile allocation",
}


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024*1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(path: Path, data) -> None:
    with path.open("x") as stream:
        json.dump(data, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def _csv(path: Path, headings, rows) -> None:
    with path.open("x", newline="") as stream:
        writer = csv.writer(stream, delimiter=" ")
        writer.writerow(headings)
        writer.writerows(rows)


def _verify_csv(path, headings, rows):
    with path.open(newline="") as stream:
        actual = list(csv.reader(stream,delimiter=" "))
    expected = [list(headings)]+[[str(value) for value in row] for row in rows]
    if actual != expected:
        raise ValueError(f"SONATA type/model semantics differ: {path.name}")


def normal_variance(rng, mean: float, variance: float, size: int):
    if not math.isfinite(mean) or not math.isfinite(variance) or variance < 0:
        raise ValueError("Invalid Gaussian mean/variance")
    return rng.normal(mean, math.sqrt(variance), size)


def validate_condition(scale, duration_ms, drive, network_seed=29, stimulus_seed=209):
    if (type(scale) not in (int,float) or type(duration_ms) not in (int,float)
            or not math.isfinite(scale) or not math.isfinite(duration_ms)):
        raise ValueError("Scale/duration must be finite numeric values")
    for value in (network_seed, stimulus_seed):
        if type(value) is not int or value < 0 or value >= 2**32:
            raise ValueError("Seed roots must be unsigned 32-bit integers")
    if drive not in ("balanced_poisson", "balanced_dc"):
        raise ValueError("Only balanced Poisson/DC construction is supported")
    valid = (scale in (.01,.02,.05) and duration_ms in (200.,60100.)) or (
        scale == .1 and duration_ms in (200.,4000.) and drive == "balanced_poisson")
    if not valid:
        raise ValueError("Construction is limited to 1/2/5% full or inert200ms, and 10%/4s BP; not execution admission")


def _parameters(scale: float, drive: str) -> dict:
    counts = np.floor(scale*FULL).astype(np.int64)
    starts = np.r_[0,np.cumsum(counts)[:-1]]
    full_contacts = np.where(P > 0, np.log1p(-P)/np.log1p(-1./(FULL[:,None]*FULL[None,:])), 0.)
    fixed = np.maximum(0,np.minimum(np.rint(scale*full_contacts/FULL[None,:]).astype(np.int64),counts[:,None]-1))
    strength = np.broadcast_to(np.array([1.,-4.]*4)[None,:],(8,8)).copy()
    strength[0,2] = 2.
    cm,tau_m,tau_s = 250.,10.,.5
    response = cm**-1*tau_m*tau_s/(tau_s-tau_m)*(
        (tau_m/tau_s)**(-tau_m/(tau_m-tau_s))-(tau_m/tau_s)**(-tau_s/(tau_m-tau_s)))
    base = .15/response
    incoming = np.trunc(full_contacts/FULL[:,None]).astype(np.int64)
    compensation = .001*tau_s*(1-math.sqrt(scale))*base*((incoming*strength*FOUT[None,:]).sum(axis=1)+INPUTS*8.)
    added = math.sqrt(scale)*INPUTS*base*8.*tau_s*.001 if drive == "balanced_dc" else np.zeros(8)
    return {"counts":counts,"starts":starts,"fixed":fixed,"full_contacts":full_contacts,
            "strength":strength,"base":base,"scaled":base/math.sqrt(scale),
            "compensation":compensation,"added":added,"current":compensation+added}


def forecast(*, scale=.02, duration_ms=60100., drive="balanced_poisson") -> dict:
    """Allocation-free exact graph counts and input expectations, not a quota."""
    validate_condition(scale,duration_ms,drive)
    parameters = _parameters(scale,drive)
    counts = parameters["counts"]
    n = int(counts.sum())
    poisson = drive == "balanced_poisson"
    clock = int(round(duration_ms/DT_MS))
    rates = INPUTS*scale*8. if poisson else np.zeros(8)
    mean = float((counts*rates).sum()*duration_ms/1000.)
    rules = parameters["fixed"]*counts[None,:]
    return {"scale":float(scale),"duration_ms":float(duration_ms),"drive":drive,
            "population_order":list(POPS),"scaled_counts":counts.tolist(),"cortical_nodes":n,
            "recurrent_contacts":int(rules.sum()),
            "recurrent_rule_contacts_target_row_source_column":rules.tolist(),
            "fixed_divergence_target_row_source_column":parameters["fixed"].tolist(),
            "external_nodes":n if poisson else 0,"external_contacts":n if poisson else 0,
            "external_event_count_mean":mean,"external_rate_hz_by_population":rates.tolist(),
            "clock_nodes":1,"clock_contacts":n,"clock_events":clock,
            "delivered_clock_events":clock*n,"input_event_storage_mean_bytes":int(math.ceil(16*(mean+clock))),
            "forecast_only":True,"scientific_acceptance":False}


class _Budget:
    def __init__(self, root: Path, maximum: int):
        if type(maximum) is not int or maximum < 1024*1024:
            raise ValueError("Structure-byte budget must be a finite integer of at least1MiB")
        self.root,self.maximum = root,maximum

    def check(self, extra=0):
        actual = sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file())
        if actual+extra > self.maximum:
            raise ValueError("Construction storage budget exceeded; preserve partial evidence, no retry")


def _nodes(path, population, count, types, props=None):
    with h5py.File(path,"x") as handle:
        group = handle.create_group(f"nodes/{population}")
        group.create_dataset("node_id",data=np.arange(count,dtype=np.uint64))
        group.create_dataset("node_type_id",data=np.asarray(types,dtype=np.uint64))
        group.create_dataset("node_group_id",data=np.zeros(count,dtype=np.uint32))
        group.create_dataset("node_group_index",data=np.arange(count,dtype=np.uint64))
        subgroup = group.create_group("0")
        for name,values in (props or {}).items():
            subgroup.create_dataset(name,data=values)


def _run_chunks(dataset, chunk_rows=CHUNK_ROWS):
    """Yield maximal same-owner runs in batches, including cross-chunk runs."""
    pending_owner,pending_start,pending_end = None,0,0
    owners,ranges = [],[]
    for start in range(0,len(dataset),chunk_rows):
        values = dataset[start:min(start+chunk_rows,len(dataset))]
        changes = np.r_[0,np.flatnonzero(values[1:] != values[:-1])+1,len(values)]
        for lo,hi in zip(changes[:-1],changes[1:]):
            owner,begin,end = int(values[lo]),int(start+lo),int(start+hi)
            if pending_owner is not None and owner == pending_owner and begin == pending_end:
                pending_end = end
                continue
            if pending_owner is not None:
                owners.append(pending_owner); ranges.append((pending_start,pending_end))
                if len(owners) == chunk_rows:
                    yield np.asarray(owners,dtype=np.int64),np.asarray(ranges,dtype=np.uint64)
                    owners,ranges = [],[]
            pending_owner,pending_start,pending_end = owner,begin,end
    if pending_owner is not None:
        owners.append(pending_owner);ranges.append((pending_start,pending_end))
    if owners:
        yield np.asarray(owners,dtype=np.int64),np.asarray(ranges,dtype=np.uint64)


def _index_offsets(dataset, node_count):
    counts = np.zeros(node_count,dtype=np.uint64)
    for owners,ranges in _run_chunks(dataset):
        if np.any(owners < 0) or np.any(owners >= node_count):
            raise ValueError("Edge index owner outside its source population")
        counts += np.bincount(owners,minlength=node_count).astype(np.uint64)
    ends = np.cumsum(counts,dtype=np.uint64)
    begins = np.r_[np.uint64(0),ends[:-1]]
    return np.stack((begins,ends),axis=1) if node_count else np.empty((0,2),dtype=np.uint64)


def _indexed_run_batches(owners,ranges):
    # This sort has at most CHUNK_ROWS entries, never the graph's edge count.
    order = np.argsort(owners,kind="stable")
    sorted_owners,sorted_ranges = owners[order],ranges[order]
    cuts = np.r_[0,np.flatnonzero(sorted_owners[1:] != sorted_owners[:-1])+1,len(order)]
    for lo,hi in zip(cuts[:-1],cuts[1:]):
        yield int(sorted_owners[lo]),sorted_ranges[lo:hi]


class Edges:
    """Capped append buffers and SONATA owner indices; never reorder graph rows."""
    def __init__(self,path,population,source,target,source_count,target_count,budget=None):
        self.handle = h5py.File(path,"x")
        self.handle.attrs["magic"] = np.uint32(0x0A7A)
        self.handle.attrs["version"] = np.array([0,1],dtype=np.uint32)
        self.group = self.handle.create_group(f"edges/{population}")
        props = self.group.create_group("0")
        self.data = {}
        for name,dtype in EDGE_FIELDS.items():
            parent = props if name in ("syn_weight","delay","nsyns") else self.group
            self.data[name] = parent.create_dataset(name,(0,),maxshape=(None,),chunks=(CHUNK_ROWS,),dtype=dtype)
        self.data["source_node_id"].attrs["node_population"] = source
        self.data["target_node_id"].attrs["node_population"] = target
        self.source_count,self.target_count = source_count,target_count
        self.count,self.buffer_count,self.buffers = 0,0,[]
        self.budget = budget

    def append(self,sources,targets,weights,delays,edge_type):
        n = len(sources)
        if any(len(x) != n for x in (targets,weights,delays)) or n > CHUNK_ROWS:
            raise ValueError("Edge append exceeds bounded aligned buffer")
        if self.buffer_count+n > CHUNK_ROWS:
            self.flush()
        if n:
            self.buffers.append((np.asarray(sources),np.asarray(targets),np.asarray(weights),np.asarray(delays),edge_type))
            self.buffer_count += n

    def flush(self):
        if not self.buffer_count:
            return
        n,start,end = self.buffer_count,self.count,self.count+self.buffer_count
        if self.budget:
            self.handle.flush();self.budget.check(n*sum(np.dtype(d).itemsize for d in EDGE_FIELDS.values())+CHUNK_ROWS*64)
        values = {"source_node_id":np.concatenate([b[0] for b in self.buffers]),
                  "target_node_id":np.concatenate([b[1] for b in self.buffers]),
                  "syn_weight":np.concatenate([b[2] for b in self.buffers]),
                  "delay":np.concatenate([b[3] for b in self.buffers]),
                  "edge_type_id":np.concatenate([np.full(len(b[0]),b[4],dtype=np.uint32) for b in self.buffers]),
                  "edge_group_id":np.zeros(n,dtype=np.uint16),
                  "edge_group_index":np.arange(start,end,dtype=np.uint64),"nsyns":np.ones(n,dtype=np.uint32)}
        for name,dataset in self.data.items():
            dataset.resize((end,));dataset[start:end] = values[name]
        self.count = end
        self.buffers,self.buffer_count = [],0

    def close(self):
        try:
            self.flush()
            index = self.group.create_group("indices")
            for direction,key,count in (("target_to_source","target_node_id",self.target_count),
                                        ("source_to_target","source_node_id",self.source_count)):
                offsets = _index_offsets(self.data[key],count)
                if self.budget:
                    self.handle.flush();self.budget.check(int(offsets[-1,1])*16+offsets.nbytes+65536 if count else 65536)
                group = index.create_group(direction)
                group.create_dataset("node_id_to_range",data=offsets)
                total = int(offsets[-1,1]) if count else 0
                ranges = group.create_dataset("range_to_edge_id",shape=(total,2),dtype="u8")
                cursor = offsets[:,0].copy()
                for owners,edges in _run_chunks(self.data[key]):
                    for owner,values in _indexed_run_batches(owners,edges):
                        begin,end = int(cursor[owner]),int(cursor[owner])+len(values)
                        ranges[begin:end] = values;cursor[owner] = end
        finally:
            self.handle.close()


class SpikeWriter:
    def __init__(self,path,population,budget=None):
        self.handle = h5py.File(path,"x")
        group = self.handle.create_group(f"spikes/{population}")
        group.attrs["sorting"] = "by_id"
        self.ids = group.create_dataset("node_ids",(0,),maxshape=(None,),chunks=(CHUNK_ROWS,),dtype="u8")
        self.times = group.create_dataset("timestamps",(0,),maxshape=(None,),chunks=(CHUNK_ROWS,),dtype="f8")
        self.count,self.budget = 0,budget

    def append(self,ids,times):
        if len(ids) != len(times) or len(ids) > CHUNK_ROWS:
            raise ValueError("Spike append exceeds bounded aligned buffer")
        if not len(ids):
            return
        if self.budget:
            self.handle.flush();self.budget.check(len(ids)*16+CHUNK_ROWS*32)
        end = self.count+len(ids)
        self.ids.resize((end,));self.times.resize((end,))
        self.ids[self.count:end],self.times[self.count:end] = ids,times
        self.count = end

    def close(self):
        self.handle.close()


def _network_streams(network_seed):
    streams = np.random.SeedSequence(network_seed).spawn(4)
    return streams,tuple(np.random.default_rng(s) for s in streams)


def _node_properties(parameters,rng_pos,rng_init):
    n = int(parameters["counts"].sum())
    initial = normal_variance(rng_init,-58.,10.,n)
    radius = np.sqrt(rng_pos.random(n))*150.
    theta = rng_pos.uniform(0,2*np.pi,n)
    y = np.concatenate([rng_pos.uniform(*DEPTH[i],int(count))*1470. for i,count in enumerate(parameters["counts"])])
    return {"x":radius*np.cos(theta),"y":y,"z":radius*np.sin(theta),"m_init":initial}


def _graph_blocks(parameters,rng_edges,rng_props):
    counts,starts,fixed = (parameters[k] for k in ("counts","starts","fixed"))
    for target_pop in range(8):
        for source_pop in range(8):
            number = int(fixed[target_pop,source_pop])
            if not number:
                continue
            eligible = np.arange(starts[target_pop],starts[target_pop]+counts[target_pop],dtype=np.int64)
            for source in range(int(starts[source_pop]),int(starts[source_pop]+counts[source_pop])):
                choices = eligible[eligible != source]
                targets = rng_edges.choice(choices,size=number,replace=False)
                raw = np.maximum(0,normal_variance(rng_props,parameters["scaled"],.1*parameters["scaled"],number))
                weights = raw*parameters["strength"][target_pop,source_pop]
                delays = np.maximum(.1,normal_variance(rng_props,1.5 if source_pop%2==0 else .75,
                                                       .75 if source_pop%2==0 else .375,number))
                yield target_pop,source_pop,np.full(number,source,dtype=np.uint64),targets,weights,delays


def _event_batches(node,rate,duration_ms,stimulus_seed):
    rng = np.random.default_rng(np.random.SeedSequence([stimulus_seed,node]))
    time = 0.
    while time < duration_ms:
        candidates = time+np.cumsum(rng.exponential(1/rate,size=EVENT_BATCH))
        valid = candidates[candidates < duration_ms]
        if len(valid):
            yield valid
        time = float(candidates[-1])


def _edge_audit():
    return {"recurrent_contact_count":0,"unique_source_target_pairs":0,"duplicate_contact_excess":0,
            "positive_contacts":0,"negative_contacts":0,"zero_recurrent_contacts":0,
            "delay_exactly_on_25us_grid":0,"delay_clipped_to_0_1ms":0,
            "signed_weight_pa_min_max":[None,None],"delay_ms_min_max":[None,None],
            "external_events_by_population":[0]*8,"external_events_exactly_on_25us_grid":0,
            "actual_recurrent_arrival_clock_ties":"not measurable before running the circuit"}


def _audit_block(audit,weights,delays):
    audit["recurrent_contact_count"] += len(weights)
    audit["unique_source_target_pairs"] += len(weights)
    for key,test in (("positive_contacts",weights>0),("negative_contacts",weights<0),("zero_recurrent_contacts",weights==0),
                     ("delay_clipped_to_0_1ms",delays==.1),
                     ("delay_exactly_on_25us_grid",np.isclose(delays/DT_MS,np.rint(delays/DT_MS),rtol=0,atol=1e-12))):
        audit[key] += int(np.count_nonzero(test))
    for key,values in (("signed_weight_pa_min_max",weights),("delay_ms_min_max",delays)):
        if len(values):
            lo,hi = float(values.min()),float(values.max())
            old = audit[key]
            audit[key] = [lo if old[0] is None else min(old[0],lo),hi if old[1] is None else max(old[1],hi)]


def build(output: Path, *, scale=.02, duration_ms=60100., network_seed=29,
          stimulus_seed=209, drive="balanced_poisson", max_structure_bytes=4*GiB) -> dict:
    validate_condition(scale,duration_ms,drive,network_seed,stimulus_seed)
    output = Path(output)
    budget = _Budget(output,max_structure_bytes)
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"Refusing to overwrite {output}")
    output.mkdir(parents=True)
    parameters,plan = _parameters(scale,drive),forecast(scale=scale,duration_ms=duration_ms,drive=drive)
    n,counts = plan["cortical_nodes"],parameters["counts"]
    streams,(rng_pos,rng_edges,rng_props,rng_init) = _network_streams(network_seed)
    network,components,provenance = (output/name for name in ("network","components","provenance"))
    for directory in (network,components,provenance):
        directory.mkdir()
    shutil.copy2(Path(__file__),provenance/Path(__file__).name)
    types = np.concatenate([np.full(int(count),100+i,dtype=np.uint64) for i,count in enumerate(counts)])
    _nodes(network/"cortex_nodes.h5","cortex",n,types,_node_properties(parameters,rng_pos,rng_init))
    _csv(network/"cortex_node_types.csv",["node_type_id","model_type","model_template","dynamics_params","population_label"],
         [[100+i,"point_neuron","digifly:DigiflyPDCMEvent",f"{pop}.json",pop] for i,pop in enumerate(POPS)])
    _nodes(network/"external_nodes.h5","external",plan["external_nodes"],np.full(plan["external_nodes"],200,dtype=np.uint64))
    _nodes(network/"clock_nodes.h5","clock",1,np.array([300],dtype=np.uint64))
    _csv(network/"external_node_types.csv",["node_type_id","model_type"],[[200,"virtual"]])
    _csv(network/"clock_node_types.csv",["node_type_id","model_type"],[[300,"virtual"]])
    for i,pop in enumerate(POPS):
        _json(components/f"{pop}.json",{"Iext":float(parameters["current"][i]),"m_init_mean":-58.,"m_init_variance":10.})
    _json(components/"event.json",{"sign":1})
    audit = _edge_audit()
    recurrent = Edges(network/"cortex_cortex_edges.h5","cortex_to_cortex","cortex","cortex",n,n,budget)
    try:
        for _,_,sources,targets,weights,delays in _graph_blocks(parameters,rng_edges,rng_props):
            recurrent.append(sources,targets,weights,delays,100)
            _audit_block(audit,weights,delays)
    finally:
        recurrent.close()
    # Consume the same property draw in both drives, preserving paired graph/init.
    external_weights = np.maximum(0,normal_variance(rng_props,parameters["scaled"],.1*parameters["scaled"],n))
    for name,count,source_count,edge_type in (("external",plan["external_contacts"],plan["external_nodes"],200),
                                              ("clock",n,1,300)):
        edges = Edges(network/f"{name}_cortex_edges.h5",f"{name}_to_cortex",name,"cortex",source_count,n,budget)
        try:
            if count:
                ids = np.arange(count,dtype=np.uint64)
                edges.append(ids if name == "external" else np.zeros(count,dtype=np.uint64),ids,
                             external_weights if name == "external" else np.zeros(count),np.full(count,INPUT_DELAY_MS),edge_type)
        finally:
            edges.close()
    for name,edge_type in (("cortex",100),("external",200),("clock",300)):
        _csv(network/f"{name}_cortex_edge_types.csv",["edge_type_id","source_query","target_query","dynamics_params"],
             [[edge_type,"*","*","event.json"]])
    writer = SpikeWriter(output/"external_spikes.h5","external",budget)
    try:
        if drive == "balanced_poisson":
            for i,count in enumerate(counts):
                rate = INPUTS[i]*scale*8./1000.
                for node in range(int(parameters["starts"][i]),int(parameters["starts"][i]+count)):
                    for times in _event_batches(node,rate,duration_ms,stimulus_seed):
                        writer.append(np.full(len(times),node,dtype=np.uint64),times)
                        audit["external_events_by_population"][i] += len(times)
                        audit["external_events_exactly_on_25us_grid"] += int(np.count_nonzero(np.isclose(times/DT_MS,np.rint(times/DT_MS),rtol=0,atol=1e-12)))
    finally:
        writer.close()
    clock = SpikeWriter(output/"clock_spikes.h5","clock",budget)
    try:
        for start in range(0,plan["clock_events"],CHUNK_ROWS):
            ticks = np.arange(start,min(start+CHUNK_ROWS,plan["clock_events"]),dtype=np.int64)
            clock.append(np.zeros(len(ticks),dtype=np.uint64),ticks*DT_MS)
    finally:
        clock.close()
    _json(output/"structure_audit.json",audit)
    manifest = {key:value for key,value in plan.items() if key not in ("forecast_only","scientific_acceptance")}
    manifest.update(schema=SCHEMA,construction_policy=POLICY,
                    primary_target="independently_reimplemented_released_executable_method",matched_reference=None,
                    independent_inputs=True,normal_parameterization=SOURCE_CONTRACT["normal"],
                    initialization=SOURCE_CONTRACT["initialization"],full_counts=FULL.tolist(),
                    network_seed=network_seed,stimulus_seed=stimulus_seed,network_streams=[list(s.spawn_key) for s in streams],
                    dt_ms=DT_MS,clock_period_ms=DT_MS,input_delay_ms=INPUT_DELAY_MS,
                    source_contract=SOURCE_CONTRACT,streaming_policy=STREAMING_POLICY,
                    external_events=sum(audit["external_events_by_population"]),
                    expected_recurrent_contacts=plan["recurrent_contacts"],
                    base_exc_weight_pa=parameters["base"],scaled_exc_weight_pa=parameters["scaled"],
                    dc_compensation_pa=parameters["compensation"].tolist(),dc_added_mean_drive_pa=parameters["added"].tolist(),
                    dc_total_current_pa=parameters["current"].tolist(),recurrent_delays_on_clock_grid=audit["delay_exactly_on_25us_grid"],
                    max_structure_bytes=max_structure_bytes,
                    reference_specification="Romaro et al. 2021 doi:10.1162/neco_a_01400; released-method numeric audit only",
                    caveats=["Not the authors' graph, NetStim event train or independent numerical solver.",
                             "NumPy PCG64 is independent from native NEURON RNG; equal numeric seeds do not pair frameworks.",
                             "F_out compensation remains the released method's stated approximation.",
                             "Delay-grid counts are not realized recurrent-arrival or refractory tie counts.",
                             "Explicit t=0 clock source delivers at0.5ms; actual first-clock behavior needs runtime verification.",
                             "Construction support is not measured execution admission or scientific acceptance."],files={})
    for path in sorted(output.rglob("*")):
        if path.is_file():
            manifest["files"][str(path.relative_to(output))] = {"bytes":path.stat().st_size,"sha256":_hash(path)}
    _json(output/"manifest.json",manifest)
    budget.check()
    return manifest


class _Rows:
    def __init__(self,datasets):
        self.datasets = datasets
        self.count = len(next(iter(datasets.values())))
        if any(len(value) != self.count for value in datasets.values()):
            raise ValueError("Dataset row lengths differ")
        self.cursor,self.start,self.end,self.buffer = 0,0,0,{}

    def take(self,count):
        if count < 0 or count > CHUNK_ROWS or self.cursor+count > self.count:
            raise ValueError("Bounded dataset rows missing or oversized")
        chunks = {key:[] for key in self.datasets}
        left = count
        while left:
            if self.cursor == self.end:
                self.start,self.end = self.cursor,min(self.cursor+CHUNK_ROWS,self.count)
                self.buffer = {key:value[self.start:self.end] for key,value in self.datasets.items()}
            n = min(left,self.end-self.cursor)
            for key,value in self.buffer.items():
                chunks[key].append(value[self.cursor-self.start:self.cursor-self.start+n])
            self.cursor += n;left -= n
        return {key:np.concatenate(values) if len(values)>1 else values[0] if values else np.empty(0)
                for key,values in chunks.items()}


def _require_equal(actual,expected,label):
    if not np.array_equal(actual,expected):
        raise ValueError(f"Structural semantics differ: {label}")


def _verify_indices(group,source_count,target_count):
    for direction,key,count in (("source_to_target","source_node_id",source_count),("target_to_source","target_node_id",target_count)):
        index = group[f"indices/{direction}"]
        offsets = _index_offsets(group[key],count)
        if index["node_id_to_range"].shape != (count,2):
            raise ValueError("SONATA node index shape differs")
        _require_equal(index["node_id_to_range"][0:count],offsets,"SONATA node index offsets")
        total = int(offsets[-1,1]) if count else 0
        if index["range_to_edge_id"].shape != (total,2):
            raise ValueError("SONATA range index shape differs")
        cursor = offsets[:,0].copy()
        for owners,ranges in _run_chunks(group[key]):
            for owner,expected in _indexed_run_batches(owners,ranges):
                begin,end = int(cursor[owner]),int(cursor[owner])+len(expected)
                _require_equal(index["range_to_edge_id"][begin:end],expected,"SONATA owned edge ranges")
                cursor[owner] = end


def _edge_group(path,population,source,target,source_count,target_count,expected):
    handle = h5py.File(path,"r")
    try:
        group = handle[f"edges/{population}"]
        if (group["source_node_id"].attrs.get("node_population") != source
                or group["target_node_id"].attrs.get("node_population") != target):
            raise ValueError("SONATA edge population identity differs")
        datasets = {name:group[("0/" if name in ("syn_weight","delay","nsyns") else "")+name] for name in EDGE_FIELDS}
        for name,dataset in datasets.items():
            if dataset.shape != (expected,) or dataset.dtype != np.dtype(EDGE_FIELDS[name]):
                raise ValueError("SONATA edge row shape/dtype differs")
        _verify_indices(group,source_count,target_count)
        return handle,group,_Rows(datasets)
    except BaseException:
        handle.close()
        raise


def _verify_nodes(path,population,count,types,props=None):
    with h5py.File(path,"r") as handle:
        group = handle[f"nodes/{population}"]
        for name,expected in (("node_id",np.arange(count,dtype=np.uint64)),("node_type_id",types),
                              ("node_group_id",np.zeros(count,dtype=np.uint32)),("node_group_index",np.arange(count,dtype=np.uint64))):
            if group[name].shape != (count,):
                raise ValueError("SONATA node row shape differs")
            _require_equal(group[name][0:count],expected,name)
        for name,expected in (props or {}).items():
            if group[f"0/{name}"].shape != (count,):
                raise ValueError("SONATA node property shape differs")
            _require_equal(group[f"0/{name}"][0:count],expected,f"node {name}")


def _verify_spikes(path,population,expected,max_id):
    handle = h5py.File(path,"r")
    try:
        group = handle[f"spikes/{population}"]
        if (group.attrs.get("sorting") != "by_id" or group["node_ids"].shape != (expected,)
                or group["timestamps"].shape != (expected,) or group["node_ids"].dtype != np.dtype("u8")
                or group["timestamps"].dtype != np.dtype("f8")):
            raise ValueError("SONATA input spike contract differs")
        return handle,_Rows({"ids":group["node_ids"],"times":group["timestamps"]})
    except BaseException:
        handle.close()
        raise


def _check_metadata(output,manifest):
    validate_condition(manifest.get("scale"),manifest.get("duration_ms"),manifest.get("drive"),
                       manifest.get("network_seed"),manifest.get("stimulus_seed"))
    if (manifest.get("schema") != SCHEMA or manifest.get("construction_policy") != POLICY
            or manifest.get("primary_target") != "independently_reimplemented_released_executable_method"
            or manifest.get("independent_inputs") is not True or manifest.get("matched_reference") is not None
            or manifest.get("source_contract") != SOURCE_CONTRACT or manifest.get("streaming_policy") != STREAMING_POLICY
            or manifest.get("normal_parameterization") != SOURCE_CONTRACT["normal"]
            or manifest.get("initialization") != SOURCE_CONTRACT["initialization"]):
        raise ValueError("Independent released-method scalable contract differs")
    parameters = _parameters(manifest["scale"],manifest["drive"])
    plan = forecast(scale=manifest["scale"],duration_ms=manifest["duration_ms"],drive=manifest["drive"])
    for key,value in plan.items():
        if key not in ("forecast_only","scientific_acceptance") and manifest.get(key) != value:
            raise ValueError(f"Deterministic source count/work differs: {key}")
    expected = {"full_counts":FULL.tolist(),"dt_ms":DT_MS,"clock_period_ms":DT_MS,"input_delay_ms":INPUT_DELAY_MS,
                "expected_recurrent_contacts":plan["recurrent_contacts"],"network_streams":[[0],[1],[2],[3]],
                "base_exc_weight_pa":parameters["base"],"scaled_exc_weight_pa":parameters["scaled"],
                "dc_compensation_pa":parameters["compensation"].tolist(),"dc_added_mean_drive_pa":parameters["added"].tolist(),
                "dc_total_current_pa":parameters["current"].tolist()}
    for key,value in expected.items():
        if manifest.get(key) != value:
            raise ValueError(f"Source-control parameter differs: {key}")
    for i,pop in enumerate(POPS):
        if json.loads((output/f"components/{pop}.json").read_text()) != {
                "Iext":float(parameters["current"][i]),"m_init_mean":-58.,"m_init_variance":10.}:
            raise ValueError("Cell current/initialization dynamics differ")
    if json.loads((output/"components/event.json").read_text()) != {"sign":1}:
        raise ValueError("Signed event weight delivery differs")
    _verify_csv(output/"network/cortex_node_types.csv",
                ["node_type_id","model_type","model_template","dynamics_params","population_label"],
                [[100+i,"point_neuron","digifly:DigiflyPDCMEvent",f"{pop}.json",pop] for i,pop in enumerate(POPS)])
    for name,type_id in (("external",200),("clock",300)):
        _verify_csv(output/f"network/{name}_node_types.csv",["node_type_id","model_type"],[[type_id,"virtual"]])
    for name,type_id in (("cortex",100),("external",200),("clock",300)):
        _verify_csv(output/f"network/{name}_cortex_edge_types.csv",
                    ["edge_type_id","source_query","target_query","dynamics_params"],[[type_id,"*","*","event.json"]])
    return parameters,plan


def verify(output: Path) -> dict:
    """Re-open and semantically verify all source/input rows in capped buffers."""
    output = Path(output)
    if output.is_symlink() or not output.is_dir():
        raise ValueError("Structure root must be an owned nonsymlink directory")
    manifest = json.loads((output/"manifest.json").read_text())
    discovered = set()
    for path in output.rglob("*"):
        if path.is_symlink():
            raise ValueError("Structure symlink is forbidden")
        if path.is_file():
            discovered.add(str(path.relative_to(output)))
    files = manifest.get("files")
    if not isinstance(files,dict) or discovered != set(files)|{"manifest.json"}:
        raise ValueError("Manifest file set mismatch")
    required = {"provenance/"+Path(__file__).name,"structure_audit.json","external_spikes.h5","clock_spikes.h5","components/event.json"}
    required |= {f"components/{pop}.json" for pop in POPS}
    required |= {f"network/{name}_{suffix}" for name in ("cortex","external","clock")
                 for suffix in ("nodes.h5","node_types.csv","cortex_edges.h5","cortex_edge_types.csv")}
    if set(files) != required:
        raise ValueError("Unexpected independent structure file inventory")
    for relative,record in files.items():
        path = output/relative
        if (Path(relative).is_absolute() or ".." in Path(relative).parts
                or path.stat().st_size != record.get("bytes") or _hash(path) != record.get("sha256")):
            raise ValueError(f"File integrity failure: {relative}")
    frozen = output/"provenance"/Path(__file__).name
    if not frozen.is_file() or _hash(frozen) != _hash(Path(__file__)):
        raise ValueError("Frozen independent builder identity differs")
    _Budget(output,manifest.get("max_structure_bytes")).check()
    parameters,plan = _check_metadata(output,manifest)
    n,counts = plan["cortical_nodes"],parameters["counts"]
    _,(rng_pos,rng_edges,rng_props,rng_init) = _network_streams(manifest["network_seed"])
    types = np.concatenate([np.full(int(count),100+i,dtype=np.uint64) for i,count in enumerate(counts)])
    _verify_nodes(output/"network/cortex_nodes.h5","cortex",n,types,_node_properties(parameters,rng_pos,rng_init))
    _verify_nodes(output/"network/external_nodes.h5","external",plan["external_nodes"],np.full(plan["external_nodes"],200,dtype=np.uint64))
    _verify_nodes(output/"network/clock_nodes.h5","clock",1,np.array([300],dtype=np.uint64))
    audit = _edge_audit()
    handle,group,rows = _edge_group(output/"network/cortex_cortex_edges.h5","cortex_to_cortex","cortex","cortex",n,n,plan["recurrent_contacts"])
    try:
        for _,_,sources,targets,weights,delays in _graph_blocks(parameters,rng_edges,rng_props):
            actual = rows.take(len(sources))
            for name,expected in (("source_node_id",sources),("target_node_id",targets),("syn_weight",weights),("delay",delays),
                                  ("edge_type_id",np.full(len(sources),100)),("edge_group_id",np.zeros(len(sources))),
                                  ("edge_group_index",np.arange(rows.cursor-len(sources),rows.cursor)),("nsyns",np.ones(len(sources)))):
                _require_equal(actual[name],expected,f"fixed unique recurrent {name}")
            _audit_block(audit,weights,delays)
        if rows.cursor != rows.count:
            raise ValueError("Unexpected trailing recurrent contacts")
    finally:
        handle.close()
    external_weights = np.maximum(0,normal_variance(rng_props,parameters["scaled"],.1*parameters["scaled"],n))
    for name,count,source_count,edge_type in (("external",plan["external_contacts"],plan["external_nodes"],200),("clock",n,1,300)):
        handle,group,rows = _edge_group(output/f"network/{name}_cortex_edges.h5",f"{name}_to_cortex",name,"cortex",source_count,n,count)
        try:
            for start in range(0,count,CHUNK_ROWS):
                ids = np.arange(start,min(start+CHUNK_ROWS,count),dtype=np.uint64)
                actual = rows.take(len(ids))
                expected = {"source_node_id":ids if name == "external" else np.zeros(len(ids)),"target_node_id":ids,
                            "syn_weight":external_weights[start:start+len(ids)] if name == "external" else np.zeros(len(ids)),
                            "delay":np.full(len(ids),INPUT_DELAY_MS),"edge_type_id":np.full(len(ids),edge_type),
                            "edge_group_id":np.zeros(len(ids)),"edge_group_index":ids,"nsyns":np.ones(len(ids))}
                for key,value in expected.items():
                    _require_equal(actual[key],value,f"{name} {key}")
        finally:
            handle.close()
    external_events = manifest.get("external_events")
    if type(external_events) is not int or external_events < 0:
        raise ValueError("External event count must be a nonnegative integer")
    handle,rows = _verify_spikes(output/"external_spikes.h5","external",external_events,plan["external_nodes"])
    try:
        if manifest["drive"] == "balanced_poisson":
            for i,count in enumerate(counts):
                for node in range(int(parameters["starts"][i]),int(parameters["starts"][i]+count)):
                    for expected in _event_batches(node,INPUTS[i]*manifest["scale"]*8./1000.,manifest["duration_ms"],manifest["stimulus_seed"]):
                        actual = rows.take(len(expected))
                        _require_equal(actual["ids"],np.full(len(expected),node),"per-cell Poisson ownership")
                        _require_equal(actual["times"],expected,"per-cell Poisson exponential stream")
                        audit["external_events_by_population"][i] += len(expected)
                        audit["external_events_exactly_on_25us_grid"] += int(np.count_nonzero(np.isclose(expected/DT_MS,np.rint(expected/DT_MS),rtol=0,atol=1e-12)))
        if rows.cursor != rows.count:
            raise ValueError("Unexpected trailing external events / DC Poisson input")
    finally:
        handle.close()
    handle,rows = _verify_spikes(output/"clock_spikes.h5","clock",plan["clock_events"],1)
    try:
        for start in range(0,plan["clock_events"],CHUNK_ROWS):
            ticks = np.arange(start,min(start+CHUNK_ROWS,plan["clock_events"]),dtype=np.int64)
            actual = rows.take(len(ticks))
            _require_equal(actual["ids"],np.zeros(len(ticks)),"clock ownership")
            _require_equal(actual["times"],ticks*DT_MS,"explicit40kHz t0 clock train")
    finally:
        handle.close()
    if (json.loads((output/"structure_audit.json").read_text()) != audit
            or manifest.get("recurrent_delays_on_clock_grid") != audit["delay_exactly_on_25us_grid"]
            or external_events != sum(audit["external_events_by_population"])):
        raise ValueError("Exact streamed structure/input audit differs")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output",type=Path)
    parser.add_argument("--scale",type=float,default=.02)
    parser.add_argument("--duration-ms",type=float,default=60100.)
    parser.add_argument("--network-seed",type=int,default=29)
    parser.add_argument("--stimulus-seed",type=int,default=209)
    parser.add_argument("--drive",choices=("balanced_poisson","balanced_dc"),default="balanced_poisson")
    parser.add_argument("--max-structure-bytes",type=int,default=4*GiB)
    parser.add_argument("--verify-only",action="store_true")
    args = parser.parse_args()
    result = verify(args.output) if args.verify_only else build(args.output,scale=args.scale,duration_ms=args.duration_ms,
             network_seed=args.network_seed,stimulus_seed=args.stimulus_seed,drive=args.drive,max_structure_bytes=args.max_structure_bytes)
    if not args.verify_only:
        verify(args.output)
    print(json.dumps({key:result[key] for key in ("scale","duration_ms","drive","cortical_nodes","recurrent_contacts","external_events","clock_events")},sort_keys=True))
