"""Run one bounded independent 1% PDCM SONATA condition in BioNet.

The 200 ms case is an event-routing test; longer cases need separate analysis.
Input structure is immutable and copied into a fresh output folder. Failures are
kept as evidence and never retried into the same folder.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import json
import logging
from pathlib import Path
import shutil
import sys
import time
import traceback

import h5py
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
STRUCTURE_SCRIPT = ROOT / "scripts/build_pdcm_bionet_primary_v3.py"
PROBE = ROOT / "scripts/probe_pdcm_bionet_independent.py"
MOD = ROOT / "src/digifly_app/pdcm_bionet/mod/DigiflyPDCMEvent.mod"


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data,indent=2,sort_keys=True)+"\n")


@contextlib.contextmanager
def _capture_bionet_log(io, stream):
    """Capture BMTK's already-created logger as well as redirected prints.

    BMTK binds its console handler to the original stdout when the logger is
    first accessed, so redirect_stdout alone does not preserve lifecycle logs.
    """
    logger = io.logger
    handler = logging.StreamHandler(stream)
    handler.setFormatter(io._log_format)
    logger.addHandler(handler)
    try:
        yield
    finally:
        logger.removeHandler(handler)
        handler.flush()
        handler.close()


def _verify_structure(path: Path) -> dict:
    spec = importlib.util.spec_from_file_location("pdcm_structure_builder", STRUCTURE_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    manifest = module.verify(path)
    if (manifest["scale"] != .01 or manifest["duration_ms"] not in (200.0,1100.0,60100.0)
            or manifest["drive"] != "balanced_poisson"):
        raise ValueError("The input is not an approved independent 1% Poisson circuit")
    if json.loads((path/"components/event.json").read_text()) != {"sign":1}:
        raise ValueError("Signed-weight delivery is not configured")
    if _sha(path/"provenance/build_pdcm_bionet_primary_v3.py") != _sha(STRUCTURE_SCRIPT):
        raise ValueError("Executed builder source differs from frozen structure source")
    _verify_control_semantics(path,manifest,module)
    return manifest


def _verify_control_semantics(path: Path, manifest: dict, module) -> None:
    """Reject a hash-consistent artifact with wrong prospective source semantics."""
    counts=np.array(manifest["scaled_counts"],dtype=int)
    if not np.array_equal(counts,np.floor(.01*module.FULL).astype(int)):
        raise ValueError("Source-control population counts differ")
    contacts=np.where(module.P>0,np.log1p(-module.P)/
                      np.log1p(-1/(module.FULL[:,None]*module.FULL[None,:])),0)
    fixed=np.maximum(0,np.minimum(np.rint(.01*contacts/module.FULL[None,:]).astype(int),counts[:,None]-1))
    with h5py.File(path/"network/cortex_cortex_edges.h5") as handle:
        group=handle["edges/cortex_to_cortex"]
        src,dst=group["source_node_id"][:],group["target_node_id"][:]
        target_pop=np.searchsorted(np.cumsum(counts),dst,side="right")
        if not np.all(np.isfinite(group["0/syn_weight"][:])) or not np.all(np.isfinite(group["0/delay"][:])):
            raise ValueError("Nonfinite recurrent weight or delay")
    source_pop=np.searchsorted(np.cumsum(counts),np.arange(sum(counts)),side="right")
    observed=np.zeros((8,sum(counts)),dtype=int)
    np.add.at(observed,(target_pop,src),1)
    if not np.array_equal(observed,fixed[:,source_pop]):
        raise ValueError("Per-source fixed divergence differs from reviewed source semantics")
    strengths=np.broadcast_to(np.array([1.,-4.]*4)[None,:],(8,8)).copy()
    strengths[0,2]=2.
    incoming=np.trunc(contacts/module.FULL[:,None]).astype(int)
    expected=.001*.5*.9*manifest["base_exc_weight_pa"]*((incoming*strengths*
               np.array([.860,2.600,4.306,5.396,8.142,8.188,.941,7.3])[None,:]).sum(axis=1)+module.INPUTS*8.)
    if not np.allclose(expected,manifest["dc_compensation_pa"],rtol=0,atol=1e-10):
        raise ValueError("Integer incoming-contact DC compensation differs")
    for pop,current in zip(module.POPS,expected):
        dynamics=json.loads((path/f"components/{pop}.json").read_text())
        if (dynamics.get("m_init_mean")!=-58. or dynamics.get("m_init_variance")!=10.
                or not np.isclose(dynamics["Iext"],current,rtol=0,atol=1e-10)):
            raise ValueError("Cell initialization or source-control current differs")
    stream=np.random.SeedSequence(manifest["network_seed"]).spawn(4)[3]
    expected_initial=np.random.default_rng(stream).normal(-58.,np.sqrt(10.),sum(counts))
    with h5py.File(path/"network/cortex_nodes.h5") as handle:
        if not np.array_equal(handle["nodes/cortex/0/m_init"][:],expected_initial):
            raise ValueError("Own per-cell released-method initialization differs")
    if manifest.get("matched_reference") is not None or not manifest.get("independent_inputs"):
        raise ValueError("Matched inputs cannot be the independent primary")
    if manifest.get("normal_parameterization") != "mean_variance; numpy_sd=sqrt(variance); hard max clipping":
        raise ValueError("Gaussian variance contract differs")
    for name, group_name in (("external","external_to_cortex"),("clock","clock_to_cortex")):
        with h5py.File(path/f"network/{name}_cortex_edges.h5") as handle:
            if not np.all(handle[f"edges/{group_name}/0/delay"][:]==.5):
                raise ValueError("External/clock fixed delay differs")


def _run(source: Path, output: Path) -> dict:
    import bmtk
    import neuron
    from neuron import h
    from bmtk.simulator import bionet
    from bmtk.simulator.bionet.io_tools import io as bionet_io
    from bmtk.utils.sonata import File

    source_manifest = _verify_structure(source)
    if source_manifest.get("construction_policy") != "released_method_fixed_unique_integer_variance_v3":
        raise ValueError("Not independent released-method control")
    duration_ms = float(source_manifest["duration_ms"])
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite {output}")
    output.mkdir(parents=True)
    copied = output / "input"
    shutil.copytree(source,copied)
    _verify_structure(copied)
    script_copy = output / "run_pdcm_bionet_primary_v3.py"
    shutil.copy2(Path(__file__),script_copy)
    network_dir = copied / "network"
    node_counts, edge_counts = {}, {}
    for name in ("cortex","external","clock"):
        nodes = File(data_files=str(network_dir/f"{name}_nodes.h5"),
                     data_type_files=str(network_dir/f"{name}_node_types.csv"))
        node_counts[name] = len(nodes.nodes[name])
        edges = File(data_files=str(network_dir/f"{name}_cortex_edges.h5"),
                     data_type_files=str(network_dir/f"{name}_cortex_edge_types.csv"))
        population = edges.edges[f"{name}_to_cortex"]
        edge_counts[name] = len(population)
        first = next(iter(population))
        if not np.isfinite(first["syn_weight"]) or not np.isfinite(first["delay"]):
            raise ValueError(f"Nonfinite first {name} edge")
    if node_counts != {"cortex":767,"external":767,"clock":1}:
        raise ValueError(f"Unexpected node counts: {node_counts}")
    if edge_counts != {"cortex":source_manifest["recurrent_contacts"],"external":767,"clock":767}:
        raise ValueError(f"Unexpected edge counts: {edge_counts}")

    spec = importlib.util.spec_from_file_location("pdcm_tiny_probe",PROBE)
    assert spec and spec.loader
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    shutil.copy2(PROBE,output/PROBE.name)
    mod_dir = probe._mechanisms(output)
    compiled = list(mod_dir.glob("*/libnrnmech.*"))
    if len(compiled) != 1:
        raise RuntimeError(f"Expected one compiled mechanism library, found {compiled}")
    neuron.load_mechanisms(str(mod_dir))

    @bionet.cell_model("digifly:DigiflyPDCMEvent",model_type="point_neuron")
    def _cell(_node, _template, dynamics):
        cell = h.DigiflyPDCMEvent()
        cell.Iext = float(dynamics["Iext"])
        cell.m = float(_node["m_init"])
        cell.i = 0.0
        return cell

    native = output / "native_output"
    config = {
        "target_simulator":"BioNet",
        "run":{"tstart":0.0,"tstop":duration_ms,"dt":.025,"nsteps_block":400},
        "conditions":{"v_init":-65.0,"celsius":34.0},
        "components":{"point_neuron_models_dir":str((copied/"components").resolve()),
                      "synaptic_models_dir":str((copied/"components").resolve()),
                      "mechanisms_dir":str(mod_dir.resolve())},
        "networks":{
            "nodes":[{"nodes_file":str((network_dir/f"{name}_nodes.h5").resolve()),
                      "node_types_file":str((network_dir/f"{name}_node_types.csv").resolve())}
                     for name in ("cortex","external","clock")],
            "edges":[{"edges_file":str((network_dir/f"{name}_cortex_edges.h5").resolve()),
                      "edge_types_file":str((network_dir/f"{name}_cortex_edge_types.csv").resolve())}
                     for name in ("cortex","external","clock")]},
        "node_sets":{"external_input":{"population":"external"},
                     "clock_input":{"population":"clock"}},
        "inputs":{"external_input":{"input_type":"spikes","module":"sonata",
                                     "node_set":"external_input",
                                     "input_file":str((copied/"external_spikes.h5").resolve())},
                  "clock_input":{"input_type":"spikes","module":"sonata",
                                 "node_set":"clock_input",
                                 "input_file":str((copied/"clock_spikes.h5").resolve())}},
        "output":{"output_dir":str(native.resolve()),
                  "spikes_file":str((native/"spikes.h5").resolve()),
                  "spikes_sort_order":"by_time","overwrite_output_dir":False},
    }
    _json(output/"config.json",config)
    frozen = {"schema":"digifly-bionet-released-method-technical-v3","construction_policy":source_manifest["construction_policy"],"paper_condition":duration_ms==60100.0,
            "primary_target":source_manifest["primary_target"],
            "normal_parameterization":source_manifest["normal_parameterization"],
            "independent_inputs":True,
            "input_manifest_sha256":_sha(copied/"manifest.json"),
            "generator_sha256":_sha(copied/"provenance/build_pdcm_bionet_primary_v3.py"),
            "runner_sha256":_sha(script_copy),"mechanism_source_sha256":_sha(MOD),
            "mechanism_compiled_sha256":_sha(compiled[0]),
            "compiler_probe_sha256":_sha(PROBE),
            "python_executable":sys.executable,
            "bmtk_version":bmtk.__version__,"neuron_version":neuron.__version__,
            "node_counts":node_counts,"edge_counts":edge_counts,
            "duration_ms":duration_ms,
            "stimulus_seed":source_manifest["stimulus_seed"],
            "network_seed":source_manifest["network_seed"]}
    _json(output/"provenance.json",frozen)
    with (output/"run.log").open("x") as log, contextlib.redirect_stdout(log), contextlib.redirect_stderr(log), _capture_bionet_log(bionet_io,log):
        bionet_config = bionet.Config.from_json(str(output/"config.json"))
        bionet_config.build_env()
        network = bionet.BioNetwork.from_config(bionet_config)
        start = time.monotonic()
        bionet.BioSimulator.from_config(bionet_config,network=network).run()
        elapsed = time.monotonic()-start
    with h5py.File(native/"spikes.h5") as handle:
        group = handle["spikes/cortex"]
        node_ids = group["node_ids"][:]
        spike_times = group["timestamps"][:]
    if len(node_ids) != len(spike_times) or not np.all(np.isfinite(spike_times)):
        raise ValueError("Invalid cortical spike data")
    if not np.all((node_ids>=0)&(node_ids<767)) or not np.all((spike_times>=0)&(spike_times<=duration_ms)):
        raise ValueError("Cortical spike identity/time outside declared bounds")
    with h5py.File(network_dir/"cortex_cortex_edges.h5") as handle:
        group = handle["edges/cortex_to_cortex"]
        sources, targets = group["source_node_id"][:],group["target_node_id"][:]
        delays = group["0/delay"][:]
    # Only observed spikes can create a recurrent arrival. This counts exact
    # grid coincidences for each emitted source spike and every outgoing edge.
    outgoing: dict[int,list[float]] = {}
    for source,delay in zip(sources,delays):
        outgoing.setdefault(int(source),[]).append(float(delay))
    arrivals = 0
    grid_ties = 0
    grid_tolerance_ms = 1e-9
    for node,time_ms in zip(node_ids,spike_times):
        for delay in outgoing.get(int(node),()):
            arrival = float(time_ms)+delay
            if arrival > duration_ms:
                continue
            arrivals += 1
            nearest = round((arrival-.5)/.025)
            if 0 <= nearest < source_manifest["clock_events"] and abs(arrival-(.5+nearest*.025)) <= grid_tolerance_ms:
                grid_ties += 1
    result = dict(frozen)
    result.update({"state":"completed_technical_unreviewed","simulation_walltime_s":elapsed,
                   "cortical_spikes":int(len(spike_times)),
                   "spiking_cells":int(len(np.unique(node_ids))),
                   "first_spike_ms":float(min(spike_times)) if len(spike_times) else None,
                   "last_spike_ms":float(max(spike_times)) if len(spike_times) else None,
                   "recurrent_arrivals_within_run":arrivals,
                   "recurrent_arrival_clock_grid_ties_1ns_tolerance":grid_ties,
                   "tie_census_limit":"Compares arrivals to delivered clock times 0.5+25us*n; internal refractory ties not counted; BioNet event ordering not inferred.",
                   "scientific_acceptance":False})
    _json(output/"technical_result.json",result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--structure",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args = parser.parse_args()
    source = args.structure.resolve()
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite {output}")
    try:
        result = _run(source,output)
    except Exception:
        if output.exists():
            _json(output/"failure.json",{"state":"failed_preserved","traceback":traceback.format_exc()})
            _json(output/"artifact_manifest.json",{"files":{str(p.relative_to(output)):_sha(p)
                 for p in sorted(output.rglob("*")) if p.is_file() and p.name!="artifact_manifest.json"}})
        raise
    _json(output/"artifact_manifest.json",{"files":{str(p.relative_to(output)):_sha(p)
         for p in sorted(output.rglob("*")) if p.is_file() and p.name!="artifact_manifest.json"}})
    print(json.dumps({k:result[k] for k in ("state","cortical_spikes","spiking_cells",
                                             "recurrent_arrival_clock_grid_ties_1ns_tolerance")},sort_keys=True))


if __name__ == "__main__":
    main()
