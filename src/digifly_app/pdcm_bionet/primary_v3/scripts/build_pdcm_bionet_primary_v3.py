"""Independently generated released-method BioNet construction, version 3.

New local 1% implementation controls only; no author generator, graph, NetStim
stream, or mechanism is imported. Fixed rounded uniform unique/nonself targets,
integer incoming-count compensation, and NEURON normal(mean, VARIANCE) semantics
are independently implemented. NumPy PCG64 streams intentionally differ.
Preserves older v1/v2 sensitivity constructions and their immutable results.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import shutil
from pathlib import Path

import h5py
import numpy as np

POPS = ("L2e", "L2i", "L4e", "L4i", "L5e", "L5i", "L6e", "L6i")
FULL = np.array((20683, 5834, 21915, 5479, 4850, 1065, 14395, 2948), dtype=np.int64)
INPUTS = np.array((1600, 1500, 2100, 1900, 2000, 1900, 2900, 2100), dtype=np.int64)
DEPTH = ((.08,.27),(.08,.27),(.27,.58),(.27,.58),(.58,.73),(.58,.73),(.73,1.),(.73,1.))
# Target rows, source columns; zero entries remain truly disconnected.
P = np.array(((.1009,.1689,.0437,.0818,.0323,0,.0076,0),
              (.1346,.1371,.0316,.0515,.0755,0,.0042,0),
              (.0077,.0059,.0497,.135,.0067,.0003,.0453,0),
              (.0691,.0029,.0794,.1597,.0033,0,.1057,0),
              (.1004,.0622,.0505,.0057,.0831,.3726,.0204,0),
              (.0548,.0269,.0257,.0022,.06,.3158,.0086,0),
              (.0156,.0066,.0211,.0166,.0572,.0197,.0396,.2252),
              (.0364,.001,.0034,.0005,.0277,.008,.0658,.1443)), dtype=float)


def _hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _csv(path: Path, heading: list[str], rows: list[list[object]]) -> None:
    with path.open("x", newline="") as stream:
        writer = csv.writer(stream, delimiter=" ")
        writer.writerow(heading)
        writer.writerows(rows)


def _nodes(path: Path, population: str, count: int, types: np.ndarray,
           props: dict[str, np.ndarray] | None = None) -> None:
    with h5py.File(path, "x") as handle:
        group = handle.create_group(f"nodes/{population}")
        group.create_dataset("node_id", data=np.arange(count, dtype=np.uint64))
        group.create_dataset("node_type_id", data=types.astype(np.uint64))
        group.create_dataset("node_group_id", data=np.zeros(count, dtype=np.uint32))
        group.create_dataset("node_group_index", data=np.arange(count, dtype=np.uint64))
        subgroup = group.create_group("0")
        for name, values in (props or {}).items():
            subgroup.create_dataset(name, data=values)


class Edges:
    def __init__(self, path: Path, population: str, source: str, target: str,
                 source_count: int, target_count: int):
        self.handle = h5py.File(path, "x")
        self.handle.attrs["magic"] = np.uint32(0x0A7A)
        self.handle.attrs["version"] = np.array([0,1],dtype=np.uint32)
        self.group = self.handle.create_group(f"edges/{population}")
        self.props = self.group.create_group("0")
        self.data = {}
        for name, dtype in (("source_node_id","u8"),("target_node_id","u8"),
                            ("edge_type_id","u4"),("edge_group_id","u2"),
                            ("edge_group_index","u8")):
            self.data[name] = self.group.create_dataset(name, shape=(0,), maxshape=(None,),
                                                        chunks=(4096,), dtype=dtype)
        self.data["source_node_id"].attrs["node_population"] = source
        self.data["target_node_id"].attrs["node_population"] = target
        for name, dtype in (("syn_weight","f8"),("delay","f8"),("nsyns","u4")):
            self.data[name] = self.props.create_dataset(name, shape=(0,), maxshape=(None,),
                                                        chunks=(4096,), dtype=dtype)
        self.count = 0
        self.source_count = source_count
        self.target_count = target_count

    def append(self, sources: np.ndarray, targets: np.ndarray, weights: np.ndarray,
               delays: np.ndarray, edge_type: int) -> None:
        count = len(sources)
        if not count:
            return
        start, end = self.count, self.count + count
        values = {"source_node_id": sources, "target_node_id": targets,
                  "edge_type_id": np.full(count, edge_type),
                  "edge_group_id": np.zeros(count),
                  "edge_group_index": np.arange(start, end),
                  "syn_weight": weights, "delay": delays,
                  "nsyns": np.ones(count)}
        for name, dataset in self.data.items():
            dataset.resize((end,))
            dataset[start:end] = values[name]
        self.count = end

    def close(self) -> None:
        if self.count:
            # Target sorting gives BioNet one contiguous range per recipient.
            order = np.argsort(self.data["target_node_id"][:],kind="stable")
            for name,dataset in self.data.items():
                dataset[:] = dataset[:][order]
            self.data["edge_group_index"][:] = np.arange(self.count,dtype=np.uint64)
        indices = self.group.create_group("indices")
        for direction, node_count in (("target_to_source",self.target_count),
                                      ("source_to_target",self.source_count)):
            key = "target_node_id" if direction == "target_to_source" else "source_node_id"
            node_ids = self.data[key][:]
            boundaries = np.r_[0,np.flatnonzero(node_ids[1:] != node_ids[:-1])+1,self.count]
            ranges = np.stack((boundaries[:-1],boundaries[1:]),axis=1).astype(np.uint64)
            owners = node_ids[boundaries[:-1]] if self.count else np.empty(0,dtype=np.uint64)
            order = np.argsort(owners,kind="stable")
            owners = owners[order]
            ranges = ranges[order]
            counts = np.bincount(owners.astype(np.int64),minlength=node_count)
            ends = np.cumsum(counts,dtype=np.uint64)
            begins = np.r_[0,ends[:-1]]
            group = indices.create_group(direction)
            group.create_dataset("node_id_to_range",data=np.stack((begins,ends),axis=1))
            group.create_dataset("range_to_edge_id",data=ranges)
        self.handle.close()


def _spikes(path: Path, population: str, ids: np.ndarray, times: np.ndarray) -> None:
    order = np.lexsort((times, ids))
    with h5py.File(path, "x") as handle:
        group = handle.create_group(f"spikes/{population}")
        group.attrs["sorting"] = "by_id"
        group.create_dataset("node_ids", data=ids[order].astype(np.uint64))
        group.create_dataset("timestamps", data=times[order].astype(np.float64))


class SpikeWriter:
    """Append SONATA spikes in node-id/time order without retaining all events."""

    def __init__(self, path: Path, population: str):
        self.handle = h5py.File(path, "x")
        group = self.handle.create_group(f"spikes/{population}")
        group.attrs["sorting"] = "by_id"
        self.ids = group.create_dataset("node_ids", (0,), maxshape=(None,), chunks=(65536,), dtype="u8")
        self.times = group.create_dataset("timestamps", (0,), maxshape=(None,), chunks=(65536,), dtype="f8")
        self.count = 0

    def append(self, ids: np.ndarray, times: np.ndarray) -> None:
        if len(ids) != len(times):
            raise ValueError("Spike ID/time length mismatch")
        if not len(ids):
            return
        end = self.count + len(ids)
        self.ids.resize((end,))
        self.times.resize((end,))
        self.ids[self.count:end] = ids
        self.times[self.count:end] = times
        self.count = end

    def close(self) -> None:
        self.handle.close()


def normal_variance(rng, mean: float, variance: float, size: int):
    """Translate reviewed NEURON variance to NumPy standard deviation."""
    if not np.isfinite(mean) or not np.isfinite(variance) or variance < 0:
        raise ValueError("Invalid Gaussian parameters")
    return rng.normal(mean, math.sqrt(variance), size)


def build(output: Path, *, network_seed: int = 17, stimulus_seed: int = 3,
          duration_ms: float = 200.0) -> dict:
    if (type(network_seed) is not int or type(stimulus_seed) is not int
            or min(network_seed, stimulus_seed) < 0):
        raise ValueError("Seeds must be nonnegative integer roots")
    if duration_ms not in (200.0, 1100.0, 60100.0):
        raise ValueError("duration_ms must be an explicitly supported bounded condition")
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite {output}")
    output.mkdir(parents=True)
    scale, duration = .01, duration_ms
    counts = np.floor(scale * FULL).astype(np.int64)
    starts = np.r_[0, np.cumsum(counts)[:-1]]
    n = int(sum(counts))
    network_streams = np.random.SeedSequence(network_seed).spawn(4)
    rng_pos, rng_edges, rng_props, rng_init = (np.random.default_rng(s) for s in network_streams)
    initial_m = normal_variance(rng_init,-58.0,10.0,n)
    network = output / "network"
    network.mkdir()
    node_type_ids = np.concatenate([np.full(int(count), 100+i, dtype=np.uint64)
                                    for i, count in enumerate(counts)])
    # Uniform cylinder volume and paper layer-depth bands; independent draws.
    radius = np.sqrt(rng_pos.random(n)) * 150.0
    theta = rng_pos.uniform(0, 2*np.pi, n)
    y = np.concatenate([rng_pos.uniform(*DEPTH[i], int(count)) * 1470.0
                        for i, count in enumerate(counts)])
    _nodes(network / "cortex_nodes.h5", "cortex", n, node_type_ids,
           {"x": radius*np.cos(theta), "y": y, "z": radius*np.sin(theta), "m_init":initial_m})
    _csv(network / "cortex_node_types.csv",
         ["node_type_id","model_type","model_template","dynamics_params","population_label"],
         [[100+i,"point_neuron","digifly:DigiflyPDCMEvent",f"{pop}.json",pop]
          for i,pop in enumerate(POPS)])
    _nodes(network / "external_nodes.h5", "external", n,
           np.full(n, 200, dtype=np.uint64))
    _nodes(network / "clock_nodes.h5", "clock", 1, np.array([300], dtype=np.uint64))
    _csv(network / "external_node_types.csv", ["node_type_id","model_type"], [[200,"virtual"]])
    _csv(network / "clock_node_types.csv", ["node_type_id","model_type"], [[300,"virtual"]])

    components = output / "components"
    components.mkdir()
    provenance = output / "provenance"
    provenance.mkdir()
    shutil.copy2(Path(__file__), provenance / Path(__file__).name)
    # Paper's current-to-voltage amplitude conversion, in pA.
    cm, tau_m, tau_s = 250.0, 10.0, .5
    response = (cm**-1 * tau_m*tau_s/(tau_s-tau_m) *
                ((tau_m/tau_s)**(-tau_m/(tau_m-tau_s)) -
                 (tau_m/tau_s)**(-tau_s/(tau_m-tau_s))))
    base_weight = .15 / response
    scaled_weight = base_weight / math.sqrt(scale)
    # The paper's F_out compensation is an explicit approximation, not measured anew.
    fout = np.array((.860,2.600,4.306,5.396,8.142,8.188,.941,7.3))
    full_contacts = np.where(P > 0, np.log1p(-P) /
                             np.log1p(-1.0/(FULL[:,None]*FULL[None,:])), 0)
    incoming = np.trunc(full_contacts / FULL[:,None]).astype(np.int64)
    strength = np.array((1.,-4.,1.,-4.,1.,-4.,1.,-4.))[None,:]
    strength = np.broadcast_to(strength, (8,8)).copy()
    strength[0,2] = 2.
    current = .001*tau_s*(1-math.sqrt(scale))*base_weight * (
        (incoming * strength * fout[None,:]).sum(axis=1) + INPUTS*8.0)
    for i,pop in enumerate(POPS):
        (components / f"{pop}.json").write_text(json.dumps({"Iext":float(current[i]),"m_init_mean":-58.0,"m_init_variance":10.0})+"\n")
    # BioNet's point-process adapter must forward signed weights unchanged.
    (components / "event.json").write_text('{"sign": 1}\n')

    recurrent = Edges(network / "cortex_cortex_edges.h5", "cortex_to_cortex", "cortex", "cortex",n,n)
    rule_counts = np.zeros((8,8), dtype=np.int64)
    try:
        for target_pop in range(8):
            for source_pop in range(8):
                if P[target_pop,source_pop] == 0:
                    continue
                # Source-defined fixed rounded divergence, target-count cap.
                divergence = scale*full_contacts[target_pop,source_pop]/FULL[source_pop]
                for source in range(int(starts[source_pop]), int(starts[source_pop]+counts[source_pop])):
                    number = max(0,min(int(round(divergence)),int(counts[target_pop])-1))
                    if not number:
                        continue
                    eligible = np.arange(starts[target_pop],starts[target_pop]+counts[target_pop],dtype=np.int64)
                    eligible = eligible[eligible != source]
                    targets = rng_edges.choice(eligible,size=number,replace=False)
                    raw = np.maximum(0, normal_variance(rng_props,scaled_weight,.1*scaled_weight,number))
                    sign = strength[target_pop,source_pop]
                    delays = np.maximum(.1, normal_variance(rng_props,1.5 if source_pop%2==0 else .75,
                                               .75 if source_pop%2==0 else .375,number))
                    recurrent.append(np.full(number,source),targets,raw*sign,delays,100)
                    rule_counts[target_pop,source_pop] += number
    finally:
        recurrent.close()
    _csv(network / "cortex_cortex_edge_types.csv",
         ["edge_type_id","source_query","target_query","dynamics_params"],
         [[100,"*","*","event.json"]])
    external = Edges(network / "external_cortex_edges.h5", "external_to_cortex", "external", "cortex",n,n)
    clock = Edges(network / "clock_cortex_edges.h5", "clock_to_cortex", "clock", "cortex",1,n)
    try:
        ids = np.arange(n, dtype=np.uint64)
        external_weights = np.maximum(0,normal_variance(rng_props,scaled_weight,.1*scaled_weight,n))
        external.append(ids,ids,external_weights,
                        np.full(n,.5),200)
        clock.append(np.zeros(n,dtype=np.uint64),ids,np.zeros(n),np.full(n,.5),300)
    finally:
        external.close(); clock.close()
    for prefix, edge_id in (("external",200),("clock",300)):
        _csv(network / f"{prefix}_cortex_edge_types.csv",
             ["edge_type_id","source_query","target_query","dynamics_params"],
             [[edge_id,"*","*","event.json"]])

    # One independent Poisson process per cortical cell. NetStim's own RNG is
    # deliberately not emulated; equal seeds do not imply equal event trains.
    event_count = 0
    event_counts_by_population = [0] * 8
    event_grid_ties = 0
    writer = SpikeWriter(output / "external_spikes.h5", "external")
    try:
        for i, count in enumerate(counts):
            rate_per_ms = INPUTS[i]*scale*8.0/1000.0
            for node in range(int(starts[i]), int(starts[i]+count)):
                # Per-node streams make the shorter train a literal prefix of
                # the longer train for the same independent network and seed.
                rng_stim = np.random.default_rng(np.random.SeedSequence([stimulus_seed,node]))
                time = 0.0
                while time < duration:
                    intervals = rng_stim.exponential(1/rate_per_ms, size=4096)
                    candidate = time + np.cumsum(intervals)
                    valid = candidate[candidate < duration]
                    if len(valid):
                        writer.append(np.full(len(valid),node,dtype=np.uint64),valid)
                        event_count += len(valid)
                        event_counts_by_population[i] += len(valid)
                        event_grid_ties += int(np.count_nonzero(np.isclose(
                            valid/.025,np.rint(valid/.025),rtol=0,atol=1e-12)))
                    time = float(candidate[-1])
    finally:
        writer.close()
    # SONATA events at t=0 have simulator-dependent initialization semantics.
    clock_count = int(round(duration/.025))
    clock_writer = SpikeWriter(output / "clock_spikes.h5", "clock")
    try:
        for start in range(0,clock_count,65536):
            ticks = np.arange(start,min(start+65536,clock_count),dtype=np.int64)
            clock_writer.append(np.zeros(len(ticks),dtype=np.uint64),ticks*.025)
    finally:
        clock_writer.close()

    # This is a delay-grid census, not a recurrent-arrival/clock event census:
    # actual recurrent arrival times depend on the later BioNet spike train.
    with h5py.File(network / "cortex_cortex_edges.h5") as handle:
        group = handle["edges/cortex_to_cortex"]
        delays = group["0/delay"][:]
        weights = group["0/syn_weight"][:]
        source_ids = group["source_node_id"][:]
        target_ids = group["target_node_id"][:]
    exact_grid_delays = int(np.count_nonzero(np.isclose(delays/.025,np.rint(delays/.025),rtol=0,atol=1e-12)))
    pair_count = len(set(zip(source_ids.tolist(),target_ids.tolist())))
    audit = {"recurrent_contact_count":int(len(delays)),
             "unique_source_target_pairs":pair_count,
             "duplicate_contact_excess":int(len(delays)-pair_count),
             "positive_contacts":int(np.count_nonzero(weights>0)),
             "negative_contacts":int(np.count_nonzero(weights<0)),
             "zero_recurrent_contacts":int(np.count_nonzero(weights==0)),
             "signed_weight_pa_quantiles_0_25_50_75_100":np.quantile(weights,[0,.25,.5,.75,1]).tolist(),
             "delay_ms_quantiles_0_25_50_75_100":np.quantile(delays,[0,.25,.5,.75,1]).tolist(),
             "delay_exactly_on_25us_grid":exact_grid_delays,
             "delay_clipped_to_0_1ms":int(np.count_nonzero(delays==.1)),
             "external_events_by_population":event_counts_by_population,
             "external_events_exactly_on_25us_grid":event_grid_ties,
             "actual_recurrent_arrival_clock_ties":"not measurable before running the circuit"}
    (output / "structure_audit.json").write_text(json.dumps(audit,indent=2)+"\n")
    manifest = {"schema":"digifly-independent-sonata-released-method-structure-v3",
                "construction_policy":"released_method_fixed_unique_integer_variance_v3",
                "primary_target":"independently_reimplemented_released_executable_method",
                "approval":"Human 2026-10-04: check athene and continue bionet, approved direction",
                "matched_reference":None,
                "normal_parameterization":"mean_variance; numpy_sd=sqrt(variance); hard max clipping",
                "independent_inputs":True,
                "initialization":"own per-cell normal(mean=-58mV,variance=10mV^2), preserved by independent INITIAL",
                "scale":scale,
                "duration_ms":duration,"drive":"balanced_poisson",
                "network_seed":network_seed,"stimulus_seed":stimulus_seed,
                "network_streams":[list(s.spawn_key) for s in network_streams],
                "population_order":list(POPS),"full_counts":FULL.tolist(),"scaled_counts":counts.tolist(),
                "cortical_nodes":n,"recurrent_contacts":int(rule_counts.sum()),
                "recurrent_rule_contacts_target_row_source_column":rule_counts.tolist(),
                "external_nodes":n,"external_contacts":n,"external_events":event_count,
                "clock_nodes":1,"clock_contacts":n,"clock_events":clock_count,
                "recurrent_delays_on_clock_grid":exact_grid_delays,
                "base_exc_weight_pa":base_weight,"scaled_exc_weight_pa":scaled_weight,
                "dc_compensation_pa":current.tolist(),
                "external_rate_hz_by_population":(INPUTS*scale*8.0).tolist(),
                "continuous_recurrent_contacts_before_rounding":float((scale*full_contacts/FULL[None,:]*counts[None,:]).sum()),
                "expected_recurrent_contacts":int((np.maximum(0,np.minimum(np.rint(scale*full_contacts/FULL[None,:]).astype(int),counts[:,None]-1))*counts[None,:]).sum()),
                "sampling":"fixed rounded source divergence, unique targets, no self edges; independently sampled NumPy PCG64 graph; own independently generated weights and Poisson inputs",
                "reference_specification":"Romaro et al. 2021, doi:10.1162/neco_a_01400; pinned PDCM_NetPyNE a6e39c19 netParams.py numeric audit only",
                "caveats":["Not the authors' exact graph or NetStim event train.",
                           "F_out scaling compensation is the authors' stated approximation.",
                           "Grid-aligned delays are not realized recurrent arrival ties; those require a simulation.",
                           "t=0 clock delivery requires a separate BioNet run check."],
                "files":{}}
    for path in sorted(output.rglob("*")):
        if path.is_file():
            manifest["files"][str(path.relative_to(output))] = {"bytes":path.stat().st_size,
                                                                   "sha256":_hash(path)}
    (output / "manifest.json").write_text(json.dumps(manifest,indent=2)+"\n")
    return manifest


def verify(output: Path) -> dict:
    manifest = json.loads((output / "manifest.json").read_text())
    discovered = {str(path.relative_to(output)) for path in output.rglob("*") if path.is_file()}
    if discovered != set(manifest["files"]) | {"manifest.json"}:
        raise ValueError("Manifest file set mismatch")
    for relative, record in manifest["files"].items():
        path = output / relative
        if path.stat().st_size != record["bytes"] or _hash(path) != record["sha256"]:
            raise ValueError(f"File integrity failure: {relative}")
    if manifest.get("construction_policy") != "released_method_fixed_unique_integer_variance_v3":
        raise ValueError("Not the approved released-method version3")
    if (manifest.get("matched_reference") is not None or not manifest.get("independent_inputs")
            or manifest.get("primary_target") != "independently_reimplemented_released_executable_method"
            or manifest.get("normal_parameterization") != "mean_variance; numpy_sd=sqrt(variance); hard max clipping"):
        raise ValueError("Primary independent model contract differs")
    if _hash(output/"provenance/build_pdcm_bionet_primary_v3.py") != _hash(Path(__file__)):
        raise ValueError("Frozen builder identity differs")
    n = manifest["cortical_nodes"]
    with h5py.File(output/"network/cortex_nodes.h5") as h:
        types = h["nodes/cortex/node_type_id"][:]
        assert len(types) == n
        assert [int(np.count_nonzero(types==100+i)) for i in range(8)] == manifest["scaled_counts"]
    for name, expected in (("cortex",manifest["recurrent_contacts"]),
                           ("external",n),("clock",n)):
        path = output/f"network/{name}_cortex_edges.h5"
        with h5py.File(path) as h:
            group = h[f"edges/{name}_to_cortex"]
            assert group["source_node_id"].attrs["node_population"] == name
            assert group["target_node_id"].attrs["node_population"] == "cortex"
            assert len(group["source_node_id"]) == len(group["target_node_id"]) == len(group["0/syn_weight"]) == expected
            assert np.all(group["target_node_id"][:] < n)
            assert np.array_equal(group["edge_group_index"][:], np.arange(expected))
            assert np.all(group["0/nsyns"][:] == 1)
            for direction, count in (("target_to_source",n),
                                     ("source_to_target",1 if name == "clock" else n)):
                index = group[f"indices/{direction}"]
                assert index["node_id_to_range"].shape == (count,2)
                owner = group["target_node_id" if direction == "target_to_source" else "source_node_id"][:]
                node_ranges = index["node_id_to_range"][:]
                edge_ranges = index["range_to_edge_id"][:]
                assert sum(int(hi-lo) for lo,hi in edge_ranges) == expected
                for node_id,(lo,hi) in enumerate(node_ranges):
                    for edge_lo,edge_hi in edge_ranges[lo:hi]:
                        assert np.all(owner[edge_lo:edge_hi] == node_id)
            if name == "cortex":
                assert np.all(group["source_node_id"][:] < n)
                assert np.all(group["0/delay"][:] >= .1)
                assert np.any(group["0/syn_weight"][:] < 0)
                assert np.any(group["0/syn_weight"][:] > 0)
                src,dst=group["source_node_id"][:],group["target_node_id"][:]
                assert not np.any(src==dst)
                assert len(set(zip(src.tolist(),dst.tolist())))==len(src)
                source_pop = np.searchsorted(np.cumsum(manifest["scaled_counts"]),group["source_node_id"][:],side="right")
                target_pop = np.searchsorted(np.cumsum(manifest["scaled_counts"]),group["target_node_id"][:],side="right")
                actual = np.zeros((8,8),dtype=np.int64)
                np.add.at(actual,(target_pop,source_pop),1)
                assert actual.tolist() == manifest["recurrent_rule_contacts_target_row_source_column"]
                signed = group["0/syn_weight"][:]
                assert np.all(signed[source_pop%2==0] >= 0)
                assert np.all(signed[source_pop%2==1] <= 0)
            if name == "clock":
                assert np.all(group["0/syn_weight"][:] == 0)
                assert np.all(group["source_node_id"][:] == 0)
            if name == "external":
                assert np.array_equal(group["source_node_id"][:],group["target_node_id"][:])
    for name, expected in (("external",manifest["external_events"]),
                           ("clock",manifest["clock_events"])):
        with h5py.File(output/f"{name}_spikes.h5") as h:
            group = h[f"spikes/{name}"]
            assert len(group["node_ids"]) == len(group["timestamps"]) == expected
            assert np.all(group["timestamps"][:] >= 0)
            assert np.all(group["timestamps"][:] < manifest["duration_ms"])
            assert np.all(group["node_ids"][:] < (1 if name == "clock" else n))
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--network-seed", type=int, default=17)
    parser.add_argument("--stimulus-seed", type=int, default=3)
    parser.add_argument("--duration-ms", type=float, default=200.0)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    result = verify(args.output) if args.verify_only else build(args.output,
               network_seed=args.network_seed, stimulus_seed=args.stimulus_seed,
               duration_ms=args.duration_ms)
    if not args.verify_only:
        verify(args.output)
    print(json.dumps({key:result[key] for key in ("cortical_nodes","recurrent_contacts",
                                           "external_events","clock_events")},sort_keys=True))
