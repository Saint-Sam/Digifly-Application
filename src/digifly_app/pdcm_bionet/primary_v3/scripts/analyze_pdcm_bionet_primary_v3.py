"""Independently audit and analyze a full-window 1% BioNet SONATA run.

This is a technical and descriptive sidecar, not paper or cohort acceptance.
Never mutates the immutable simulation directory.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

import h5py
import numpy as np

from digifly_app.workers.pdcm_netpyne_worker import POPULATIONS, _paper_statistics


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_spikes(ids,times,count,duration):
    if ids.dtype.kind not in "iu" or not np.all((ids>=0)&(ids<count)):
        raise ValueError("Cortical spike IDs are not owned integer cell IDs")
    if len(ids)!=len(times) or not np.all(np.isfinite(times)) or not np.all((times>=0)&(times<=duration)):
        raise ValueError("Nonfinite or out-of-window spike time")
    for node in np.unique(ids):
        if np.any(np.diff(times[ids==node])<0):
            raise ValueError("Per-cell spike times are not sorted")


def check_execution_provenance(run: Path, technical: dict, structure: dict) -> None:
    """Cross-check claims as well as file integrity; seeds are not interchangeable."""
    provenance = json.loads((run / "provenance.json").read_text())
    for key in ("network_seed", "stimulus_seed", "duration_ms"):
        if technical[key] != structure[key] or provenance[key] != structure[key]:
            raise ValueError(f"Execution provenance differs for {key}")
    for key, value in provenance.items():
        if technical.get(key) != value:
            raise ValueError(f"Technical result differs from frozen provenance: {key}")
    if (provenance["paper_condition"] is not True or
            provenance["node_counts"] != {"cortex": 767, "external": 767, "clock": 1} or
            provenance["edge_counts"] != {"cortex": structure["recurrent_contacts"],
                                          "external": 767, "clock": 767}):
        raise ValueError("Execution node/edge/paper-condition claims differ")
    compiled = list((run / "mechanisms").glob("*/libnrnmech.*"))
    if len(compiled) != 1 or sha(compiled[0]) != provenance["mechanism_compiled_sha256"]:
        raise ValueError("Compiled mechanism provenance differs")


def analyze(run: Path, output: Path) -> dict:
    run, output = run.resolve(), output.resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite {output}")
    artifact = json.loads((run / "artifact_manifest.json").read_text())
    expected = set(artifact["files"]) | {"artifact_manifest.json"}
    observed = {str(path.relative_to(run)) for path in run.rglob("*") if path.is_file()}
    if expected != observed:
        raise ValueError("Run file set differs from its immutable artifact manifest")
    for relative, digest in artifact["files"].items():
        if sha(run / relative) != digest:
            raise ValueError(f"Run artifact hash mismatch: {relative}")
    technical = json.loads((run / "technical_result.json").read_text())
    structure = json.loads((run / "input/manifest.json").read_text())
    config = json.loads((run / "config.json").read_text())
    check_execution_provenance(run, technical, structure)
    from scripts.build_pdcm_bionet_primary_v3 import verify
    verify(run/"input")
    from scripts import build_pdcm_bionet_primary_v3 as builder
    from scripts.run_pdcm_bionet_primary_v3 import _verify_control_semantics
    _verify_control_semantics(run/"input",structure,builder)
    if technical.get("construction_policy") != "released_method_fixed_unique_integer_variance_v3":
        raise ValueError("Source-semantics policy differs")
    if not all(term in (run/"run.log").read_text() for term in ("Building cells","Running simulation","Simulation completed")):
        raise ValueError("Lifecycle logs incomplete")
    input_files = {str(path.relative_to(run / "input")) for path in (run / "input").rglob("*")
                   if path.is_file()}
    if input_files != set(structure["files"]) | {"manifest.json"}:
        raise ValueError("Copied SONATA input file set differs from structure manifest")
    for relative, record in structure["files"].items():
        path = run / "input" / relative
        if path.stat().st_size != record["bytes"] or sha(path) != record["sha256"]:
            raise ValueError(f"Copied SONATA input hash mismatch: {relative}")
    if technical["state"] != "completed_technical_unreviewed" or technical["scientific_acceptance"]:
        raise ValueError("Run is not an unreviewed technical result")
    if (structure["scale"] != .01 or structure["duration_ms"] != 60100.0
            or structure["drive"] != "balanced_poisson"
            or config["run"]["tstop"] != 60100.0 or config["run"]["dt"] != .025
            or technical["duration_ms"] != 60100.0):
        raise ValueError("Not the paper-window 1% balanced-Poisson condition")
    if technical["input_manifest_sha256"] != sha(run / "input/manifest.json"):
        raise ValueError("Input manifest hash differs from runner provenance")
    if technical["generator_sha256"] != sha(run / "input/provenance/build_pdcm_bionet_primary_v3.py"):
        raise ValueError("Executed generator source hash differs")
    if technical["runner_sha256"] != sha(run / "run_pdcm_bionet_primary_v3.py"):
        raise ValueError("Executed runner source hash differs")
    if technical["mechanism_source_sha256"] != sha(run / "mechanisms/DigiflyPDCMEvent.mod"):
        raise ValueError("Executed independent mechanism source hash differs")
    if structure["clock_events"] != 2404000 or structure["cortical_nodes"] != 767:
        raise ValueError("Unexpected clock or cortical cell count")
    counts = np.array(structure["scaled_counts"], dtype=int)
    boundaries = np.cumsum(counts)
    with h5py.File(run / "native_output/spikes.h5") as handle:
        group = handle["spikes/cortex"]
        ids = group["node_ids"][:]
        times = group["timestamps"][:]
    if (len(ids) != technical["cortical_spikes"] or len(times) != len(ids)
            or not np.all(np.isfinite(times)) or not np.all((times >= 0) & (times <= 60100))
            or not np.all(ids < 767)):
        raise ValueError("Invalid or inconsistent cortical spikes")
    validate_spikes(ids,times,767,60100.)
    startup = np.isclose(times, .5, rtol=0, atol=1e-12)
    with h5py.File(run/"input/network/cortex_nodes.h5") as handle:
        initial_m=handle["nodes/cortex/0/m_init"][:]
    current=np.repeat(structure["dc_compensation_pa"],counts)
    first_m=initial_m+.5*((-65.-initial_m)/10.+current/250.)
    if not np.array_equal(np.sort(ids[startup]),np.flatnonzero(first_m > -50.)):
        raise ValueError("First delivered clock-event census differs from initial-state oracle")
    if len(np.unique(ids)) != technical["spiking_cells"]:
        raise ValueError("Spiking-cell census differs")
    with h5py.File(run / "input/network/cortex_cortex_edges.h5") as handle:
        edges = handle["edges/cortex_to_cortex"]
        edge_sources = edges["source_node_id"][:]
        edge_delays = edges["0/delay"][:]
    by_source = np.argsort(edge_sources, kind="stable")
    edge_sources, edge_delays = edge_sources[by_source], edge_delays[by_source]
    by_cell = np.argsort(ids, kind="stable")
    sorted_ids, sorted_times = ids[by_cell], times[by_cell]
    edge_bounds = np.searchsorted(edge_sources, np.arange(768), side="left")
    spike_bounds = np.searchsorted(sorted_ids, np.arange(768), side="left")
    arrivals = ties = 0
    for node in range(767):
        delays = edge_delays[edge_bounds[node]:edge_bounds[node + 1]]
        source_times = sorted_times[spike_bounds[node]:spike_bounds[node + 1]]
        if not len(delays) or not len(source_times):
            continue
        event_times = source_times[:, None] + delays[None, :]
        within = event_times <= 60100.0
        arrivals += int(np.count_nonzero(within))
        grid = np.rint((event_times - .5) / .025)
        ties += int(np.count_nonzero(within & (grid >= 0) & (grid < 2404000) &
                                     (np.abs(event_times - (.5 + grid * .025)) <= 1e-9)))
    if (arrivals != technical["recurrent_arrivals_within_run"] or
            ties != technical["recurrent_arrival_clock_grid_ties_1ns_tolerance"]):
        raise ValueError("Independent recurrent-arrival/clock-tie census differs")
    pop_indices = np.searchsorted(boundaries, ids, side="right")
    if np.any(pop_indices >= 8):
        raise ValueError("Population mapping out of bounds")
    begins = np.r_[0, boundaries[:-1]]
    gids = {pop: list(range(int(begins[i]), int(boundaries[i])))
            for i, pop in enumerate(POPULATIONS)}
    spikes = [(float(time), int(node), POPULATIONS[int(pop)])
              for time, node, pop in zip(times, ids, pop_indices)]
    output.mkdir(parents=True)
    if not _paper_statistics(output, gids_by_population=gids, spikes=spikes,
                             duration_ms=60100.0, sample_seed=int(structure["stimulus_seed"])):
        raise ValueError("Paper-window statistics were not produced")
    # Explicitly expose the two synchrony normalizations; the paper's prose
    # and supplementary values do not unambiguously select one.
    comparison = output / "synchrony_metric_comparison.csv"
    with (output / "paper_metrics.csv").open(newline="") as source, comparison.open("x", newline="") as target:
        rows = csv.DictReader(source)
        writer = csv.writer(target)
        writer.writerow(("population", "sampled_cells", "population_total_fano_3ms_5s",
                         "population_total_fano_3ms_60s", "per_cell_normalized_3ms_5s",
                         "per_cell_normalized_3ms_60s"))
        for row in rows:
            n = int(row["sampled_cells"])
            values = [float(value) if value else None for value in
                      (row[f"synchrony_3ms_{window}"] for window in ("5s", "60s"))]
            writer.writerow([row["population"], n,
                             *[n * value if value is not None else "" for value in values],
                             *[value if value is not None else "" for value in values]])
    result = {
        "schema":"digifly-bionet-released-method-paper-window-audit-v3",
        "construction_policy":technical["construction_policy"],
        "technical_acceptance": True,
        "scientific_acceptance": False,
        "scale": .01, "duration_ms": 60100.0,
        "network_seed": structure["network_seed"], "stimulus_seed": structure["stimulus_seed"],
        "cortical_cells": 767, "cortical_spikes": len(ids),
        "per_population_spikes": dict(zip(POPULATIONS, np.bincount(pop_indices, minlength=8).astype(int).tolist())),
        "post_settling_spikes": int(np.count_nonzero((times >= 100) & (times < 60100))),
        "startup_spikes_at_0_5ms": int(np.count_nonzero(startup)),
        "recurrent_arrivals_within_run": arrivals,
        "recurrent_arrival_clock_grid_ties_1ns_tolerance": ties,
        "run_artifact_manifest_sha256": sha(run / "artifact_manifest.json"),
        "input_manifest_sha256": sha(run / "input/manifest.json"),
        "source_h5_sha256": sha(run / "native_output/spikes.h5"),
        "analysis_windows_ms": {"settled": [100, 60100], "correlation": [100, 5100]},
        "caveats": [
            "The 1% scale is paper-required, but this one-seed BioNet run is a capacity/implementation control, not an accepted Figure 8 point.",
            "One network realization and one stimulus seed do not establish statistical replication.",
            "Own per-cell initialization uses N(-58,variance10), not previous m_init=0 control; first100ms excluded.",
            "The exact original sampled cell IDs and full-scale NEST reference values are unavailable.",
            "BioNet and NetPyNE both use NEURON; this is not an independent-solver replication.",
            "The published synchrony equation and supplementary numerical scale remain ambiguous; both normalizations are given.",
        ],
    }
    (output / "audit.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    hashes = {str(path.relative_to(output)): sha(path) for path in sorted(output.rglob("*")) if path.is_file()}
    (output / "analysis_manifest.json").write_text(json.dumps({
        "source_run": str(run), "source_artifact_manifest_sha256": result["run_artifact_manifest_sha256"],
        "analysis_script_sha256": sha(Path(__file__)),
        "shared_metric_function_sha256": sha(Path(_paper_statistics.__code__.co_filename)),
        "files_sha256": hashes,
    }, indent=2, sort_keys=True) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(analyze(args.run, args.output), sort_keys=True))
