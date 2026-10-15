"""Execute the independently authored scalable PDCM SONATA/BioNet primary.

Version 4 preserves the primary-v3 artificial cell, live signed recurrent
delivery, per-cell Poisson inputs and every zero-weight 40 kHz clock contact.
Only the explicitly source-bound 2%/60,100 ms balanced-Poisson condition is
executable here. Construction capability is not production admission.

No author builder, realized graph, mechanism or spike train is imported. The
post-run arrival census streams indexed edges and a disk-backed cortical-spike
index; it never retains the entire outgoing graph or a rule-wide arrival array.
An unreviewed technical result is not scientific replication acceptance.
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
STRUCTURE_SCRIPT = ROOT / "scripts/build_pdcm_bionet_scalable_v4.py"
PROBE = ROOT / "scripts/probe_pdcm_bionet_independent.py"
MOD = ROOT / "src/digifly_app/pdcm_bionet/mod/DigiflyPDCMEvent.mod"
SCHEMA = "digifly-bionet-released-method-technical-scalable-v4"
STRUCTURE_SCHEMA = "digifly-pdcm-bionet-scalable-v4-structure"
POLICY = "released_method_fixed_unique_integer_variance_scalable_v4"
APPROVED_CONDITION = (.02, 60100.0, "balanced_poisson")
INDEPENDENT_MECHANISM_SHA256 = "d25f643ff90083706aadf4f2f992183d2d267289b9e38e542fca7439b74c1ebc"
COMPILER_PROBE_SHA256 = "f70403b8e9865e05a79a6a0a216aa681aa276082ee8fb3b48cac03b1c96faca0"
CLOCK_PERIOD_MS = .025
INPUT_DELAY_MS = .5
GRID_TOLERANCE_MS = 1e-9
EDGE_CHUNK_SIZE = 65536
SPIKE_CHUNK_SIZE = 8192
ARRIVAL_BATCH_ITEMS = 262144


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path, data: dict) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def _artifact_inventory(output: Path) -> dict:
    return {str(path.relative_to(output)): _sha(path)
            for path in sorted(output.rglob("*"))
            if path.is_file() and path.name != "artifact_manifest.json"}


@contextlib.contextmanager
def _capture_bionet_log(io, stream):
    """Preserve BMTK's pre-bound logger as well as redirected stdout/stderr."""
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


def _load_script(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Cannot load source-bound script: {path.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _require_condition(manifest: dict) -> None:
    if manifest.get("schema") != STRUCTURE_SCHEMA or manifest.get("construction_policy") != POLICY:
        raise ValueError("Not the independently authored scalable-v4 structure")
    if (manifest.get("scale"), manifest.get("duration_ms"), manifest.get("drive")) != APPROVED_CONDITION:
        raise ValueError("Only explicit 2%/60100ms balanced-Poisson execution is admitted")
    if (manifest.get("dt_ms") != CLOCK_PERIOD_MS
            or manifest.get("clock_period_ms") != CLOCK_PERIOD_MS
            or manifest.get("input_delay_ms") != INPUT_DELAY_MS):
        raise ValueError("Scalable clock/external timing contract differs")
    for key in ("network_seed", "stimulus_seed"):
        if type(manifest.get(key)) is not int or manifest[key] < 0:
            raise ValueError("Network and stimulus roots must be owned nonnegative integers")
    if (manifest.get("primary_target") != "independently_reimplemented_released_executable_method"
            or manifest.get("matched_reference") is not None
            or manifest.get("independent_inputs") is not True
            or manifest.get("normal_parameterization") != "mean_variance; numpy_sd=sqrt(variance); hard max clipping"):
        raise ValueError("Independent released-method primary contract differs")


def _verify_control_semantics(path: Path, manifest: dict, module) -> None:
    """Check scale-aware released semantics without materializing the graph."""
    counts = np.asarray(manifest["scaled_counts"], dtype=np.int64)
    scale = float(manifest["scale"])
    expected_counts = np.floor(scale * module.FULL).astype(np.int64)
    if (counts.shape != (8,) or not np.array_equal(counts, expected_counts)
            or manifest.get("population_order") != list(module.POPS)
            or manifest.get("cortical_nodes") != int(counts.sum())):
        raise ValueError("Source-control population counts/order differ")
    node_count = int(counts.sum())
    contacts = np.where(module.P > 0, np.log1p(-module.P) /
                        np.log1p(-1 / (module.FULL[:, None] * module.FULL[None, :])), 0)
    fixed = np.maximum(0, np.minimum(
        np.rint(scale * contacts / module.FULL[None, :]).astype(np.int64), counts[:, None] - 1))
    source_pop = np.searchsorted(np.cumsum(counts), np.arange(node_count), side="right")
    observed = np.zeros((8, node_count), dtype=np.int64)
    with h5py.File(path / "network/cortex_cortex_edges.h5", "r") as handle:
        group = handle["edges/cortex_to_cortex"]
        edge_count = len(group["source_node_id"])
        for start in range(0, edge_count, EDGE_CHUNK_SIZE):
            stop = min(edge_count, start + EDGE_CHUNK_SIZE)
            sources = group["source_node_id"][start:stop]
            targets = group["target_node_id"][start:stop]
            weights = group["0/syn_weight"][start:stop]
            delays = group["0/delay"][start:stop]
            if (sources.dtype.kind not in "iu" or targets.dtype.kind not in "iu"
                    or np.any(sources >= node_count) or np.any(targets >= node_count)
                    or np.any(sources < 0) or np.any(targets < 0)
                    or np.any(sources == targets)):
                raise ValueError("Recurrent contact identity/self-edge differs")
            if (not np.all(np.isfinite(weights)) or not np.all(np.isfinite(delays))
                    or np.any(delays < .1) or not np.all(group["0/nsyns"][start:stop] == 1)):
                raise ValueError("Nonfinite or invalid recurrent weight/delay/multiplicity")
            target_pop = np.searchsorted(np.cumsum(counts), targets, side="right")
            np.add.at(observed, (target_pop, sources), 1)
    if (not np.array_equal(observed, fixed[:, source_pop])
            or edge_count != manifest["recurrent_contacts"]
            or not np.array_equal((fixed * counts[None, :]),
                                  manifest["recurrent_rule_contacts_target_row_source_column"])):
        raise ValueError("Per-source fixed divergence differs from released source semantics")
    strengths = np.broadcast_to(np.asarray([1., -4.] * 4)[None, :], (8, 8)).copy()
    strengths[0, 2] = 2.
    incoming = np.trunc(contacts / module.FULL[:, None]).astype(np.int64)
    expected_current = .001 * .5 * (1 - np.sqrt(scale)) * manifest["base_exc_weight_pa"] * (
        (incoming * strengths * np.asarray([.860, 2.600, 4.306, 5.396, 8.142, 8.188, .941, 7.3])[None, :]).sum(axis=1)
        + module.INPUTS * 8.)
    if not np.allclose(expected_current, manifest["dc_compensation_pa"], rtol=0, atol=1e-10):
        raise ValueError("Integer incoming-contact DC compensation differs")
    for pop, current in zip(module.POPS, expected_current):
        dynamics = json.loads((path / f"components/{pop}.json").read_text())
        if (dynamics.get("m_init_mean") != -58. or dynamics.get("m_init_variance") != 10.
                or not np.isclose(dynamics.get("Iext", np.nan), current, rtol=0, atol=1e-10)):
            raise ValueError("Per-population initialization/current differs")
    stream = np.random.SeedSequence(manifest["network_seed"]).spawn(4)[3]
    expected_initial = np.random.default_rng(stream).normal(-58., np.sqrt(10.), node_count)
    with h5py.File(path / "network/cortex_nodes.h5", "r") as handle:
        if not np.array_equal(handle["nodes/cortex/0/m_init"][:], expected_initial):
            raise ValueError("Own per-cell released-method initialization differs")
    for name in ("external", "clock"):
        with h5py.File(path / f"network/{name}_cortex_edges.h5", "r") as handle:
            group = handle[f"edges/{name}_to_cortex"]
            if len(group["source_node_id"]) != node_count:
                raise ValueError("External/clock recipient count differs")
            for start in range(0, node_count, EDGE_CHUNK_SIZE):
                stop = min(node_count, start + EDGE_CHUNK_SIZE)
                expected_target = np.arange(start, stop, dtype=np.uint64)
                if (not np.array_equal(group["target_node_id"][start:stop], expected_target)
                        or not np.all(group["0/delay"][start:stop] == INPUT_DELAY_MS)
                        or not np.all(group["0/nsyns"][start:stop] == 1)):
                    raise ValueError("External/clock fixed delivery differs")
                sources = group["source_node_id"][start:stop]
                weights = group["0/syn_weight"][start:stop]
                if not np.all(np.isfinite(weights)):
                    raise ValueError("Nonfinite external/clock weight")
                if name == "clock":
                    if not np.all(sources == 0) or not np.all(weights == 0):
                        raise ValueError("Live zero-weight shared clock differs")
                elif not np.array_equal(sources, expected_target) or np.any(weights < 0):
                    raise ValueError("Own one-to-one positive Poisson route differs")


def _verify_structure(path: Path) -> dict:
    builder = _load_script(STRUCTURE_SCRIPT, "pdcm_scalable_v4_structure")
    manifest = builder.verify(path)
    _require_condition(manifest)
    if json.loads((path / "components/event.json").read_text()) != {"sign": 1}:
        raise ValueError("Signed-weight delivery is not configured")
    frozen_builder = path / "provenance" / STRUCTURE_SCRIPT.name
    if _sha(frozen_builder) != _sha(STRUCTURE_SCRIPT):
        raise ValueError("Executed builder differs from frozen structure source")
    _verify_control_semantics(path, manifest, builder)
    return manifest


def _positive_integer(value, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _index_cortical_spikes(spikes_path: Path, index_path: Path, *, node_count: int,
                           duration_ms: float, chunk_size: int) -> dict:
    """Validate every native event and scatter to a fresh on-disk source index."""
    _positive_integer(node_count, "node_count")
    _positive_integer(chunk_size, "chunk_size")
    if index_path.exists():
        raise FileExistsError(f"Refusing to overwrite spike census index {index_path}")
    if not np.isfinite(duration_ms) or duration_ms <= 0:
        raise ValueError("duration_ms must be finite and positive")
    counts = np.zeros(node_count, dtype=np.int64)
    first = last = None
    with h5py.File(spikes_path, "r") as source:
        group = source["spikes/cortex"]
        ids, times = group["node_ids"], group["timestamps"]
        if ids.ndim != 1 or times.ndim != 1 or len(ids) != len(times) or ids.dtype.kind not in "iu":
            raise ValueError("Cortical spikes are not equal-length owned integer IDs/times")
        previous_time = -np.inf
        for start in range(0, len(ids), chunk_size):
            stop = min(len(ids), start + chunk_size)
            chunk_ids, chunk_times = ids[start:stop], times[start:stop]
            if np.any(chunk_ids < 0) or np.any(chunk_ids >= node_count):
                raise ValueError("Cortical spike IDs are outside owned node ranges")
            if (not np.all(np.isfinite(chunk_times)) or np.any(chunk_times < 0)
                    or np.any(chunk_times > duration_ms)):
                raise ValueError("Nonfinite or out-of-window cortical spike time")
            if np.any(np.diff(chunk_times) < 0) or chunk_times[0] < previous_time:
                raise ValueError("Native by-time cortical output is not sorted")
            previous_time = float(chunk_times[-1])
            first = float(chunk_times[0]) if first is None else first
            last = previous_time
            np.add.at(counts, chunk_ids, 1)
        offsets = np.r_[0, np.cumsum(counts, dtype=np.int64)]
        # This is an audit derivative, not a replacement live input or rerun.
        with h5py.File(index_path, "x") as indexed:
            indexed.attrs["schema"] = "digifly-pdcm-cortical-spike-census-index-v1"
            indexed.attrs["native_spikes_sha256"] = _sha(spikes_path)
            indexed.create_dataset("source_offsets", data=offsets.astype(np.uint64))
            saved_times = indexed.create_dataset("timestamps", shape=(len(ids),), dtype="f8")
            positions = offsets[:-1].copy()
            for start in range(0, len(ids), chunk_size):
                stop = min(len(ids), start + chunk_size)
                chunk_ids, chunk_times = ids[start:stop], times[start:stop]
                for node in np.unique(chunk_ids):
                    selected = chunk_times[chunk_ids == node]
                    position = int(positions[int(node)])
                    saved_times[position:position + len(selected)] = selected
                    positions[int(node)] += len(selected)
            if not np.array_equal(positions, offsets[1:]):
                raise ValueError("Disk-backed source spike index is incomplete")
    return {"cortical_spikes": int(counts.sum()), "spiking_cells": int(np.count_nonzero(counts)),
            "first_spike_ms": first, "last_spike_ms": last,
            "spike_index_sha256": _sha(index_path)}


def census_recurrent_arrivals(edges_path: Path, spikes_path: Path, index_path: Path, *,
                              node_count: int, duration_ms: float, clock_events: int,
                              edge_chunk_size: int = EDGE_CHUNK_SIZE,
                              spike_chunk_size: int = SPIKE_CHUNK_SIZE,
                              arrival_batch_items: int = ARRIVAL_BATCH_ITEMS) -> dict:
    """Exhaustive own-source arrivals/ties with a hard cap on each array.

    Standard SONATA source indices may refer to many unsorted canonical edge
    runs. Validate each run's ownership/order and visit every edge exactly once;
    never sort or retain the graph. Every emitted event/outgoing contact pair
    is counted if it arrives at or before tstop, including zero-weight contacts.
    """
    for value, name in ((edge_chunk_size, "edge_chunk_size"), (spike_chunk_size, "spike_chunk_size"),
                        (arrival_batch_items, "arrival_batch_items"), (clock_events, "clock_events")):
        _positive_integer(value, name)
    stats = _index_cortical_spikes(spikes_path, index_path, node_count=node_count,
                                   duration_ms=duration_ms, chunk_size=spike_chunk_size)
    arrivals = ties = edges_visited = comparisons = largest_batch = 0
    with h5py.File(index_path, "r") as indexed, h5py.File(edges_path, "r") as source:
        group = source["edges/cortex_to_cortex"]
        ids, delays = group["source_node_id"], group["0/delay"]
        if ids.ndim != 1 or delays.ndim != 1 or len(ids) != len(delays) or ids.dtype.kind not in "iu":
            raise ValueError("Invalid recurrent census datasets")
        source_index = group["indices/source_to_target"]
        node_ranges = source_index["node_id_to_range"]
        edge_ranges = source_index["range_to_edge_id"]
        if (node_ranges.shape != (node_count, 2) or edge_ranges.ndim != 2 or edge_ranges.shape[1] != 2
                or node_ranges.dtype.kind not in "iu" or edge_ranges.dtype.kind not in "iu"):
            raise ValueError("Invalid source-to-target index shape/type")
        offsets, times = indexed["source_offsets"], indexed["timestamps"]
        previous_range_stop = 0
        for node in range(node_count):
            begin, end = (int(value) for value in node_ranges[node])
            if not (begin == previous_range_stop <= end <= len(edge_ranges)):
                raise ValueError("Source-to-target node ranges omit/overlap index rows")
            previous_range_stop = end
            spike_start, spike_stop = (int(value) for value in offsets[node:node + 2])
            previous_edge_stop = -1
            for range_start in range(begin, end, edge_chunk_size):
                for edge_start, edge_stop in edge_ranges[range_start:min(end, range_start + edge_chunk_size)]:
                    edge_start, edge_stop = int(edge_start), int(edge_stop)
                    if not (0 <= edge_start < edge_stop <= len(ids)) or edge_start < previous_edge_stop:
                        raise ValueError("Source edge ranges are invalid, reordered or overlapping")
                    previous_edge_stop = edge_stop
                    for start in range(edge_start, edge_stop, edge_chunk_size):
                        stop = min(edge_stop, start + edge_chunk_size)
                        owned = ids[start:stop]
                        chunk_delays = delays[start:stop]
                        if (not np.all(owned == node) or not np.all(np.isfinite(chunk_delays))
                                or np.any(chunk_delays < .1)):
                            raise ValueError("Indexed recurrence has wrong owner/nonfinite delay")
                        edges_visited += len(chunk_delays)
                        # Both axes are bounded even when one source fires unusually often.
                        delay_width = min(len(chunk_delays), arrival_batch_items)
                        for delay_start in range(0, len(chunk_delays), delay_width):
                            selected_delays = chunk_delays[delay_start:delay_start + delay_width]
                            time_width = min(spike_chunk_size, max(1, arrival_batch_items // len(selected_delays)))
                            for time_start in range(spike_start, spike_stop, time_width):
                                selected_times = times[time_start:min(spike_stop, time_start + time_width)]
                                event_times = selected_times[:, None] + selected_delays[None, :]
                                comparisons += int(event_times.size)
                                largest_batch = max(largest_batch, int(event_times.size))
                                within = event_times <= duration_ms
                                arrivals += int(np.count_nonzero(within))
                                grid = np.rint((event_times - INPUT_DELAY_MS) / CLOCK_PERIOD_MS)
                                ties += int(np.count_nonzero(within & (grid >= 0) & (grid < clock_events) &
                                    (np.abs(event_times - (INPUT_DELAY_MS + grid * CLOCK_PERIOD_MS)) <= GRID_TOLERANCE_MS)))
        if previous_range_stop != len(edge_ranges) or edges_visited != len(ids):
            raise ValueError("Source edge index did not cover every recurrent contact exactly once")
    return {**stats, "recurrent_arrivals_within_run": arrivals,
            "recurrent_arrival_clock_grid_ties_1ns_tolerance": ties,
            "arrival_census": {"method": "exhaustive_source_indexed_edges_disk_backed_cortical_spikes",
                "recurrent_edges_visited": edges_visited, "spike_contact_pairs_evaluated": comparisons,
                "largest_arrival_batch_items": largest_batch, "arrival_batch_item_limit": arrival_batch_items,
                "edge_chunk_size": edge_chunk_size, "spike_chunk_size": spike_chunk_size,
                "clock_period_ms": CLOCK_PERIOD_MS, "first_delivered_clock_ms": INPUT_DELAY_MS,
                "clock_events": clock_events, "grid_tolerance_ms": GRID_TOLERANCE_MS},
            "tie_census_limit": "Compares arrivals to delivered clock times 0.5+25us*n; internal refractory ties not counted; BioNet event ordering not inferred."}


def _reader_counts(network_dir: Path, manifest: dict) -> tuple[dict, dict]:
    from bmtk.utils.sonata import File
    node_counts, edge_counts = {}, {}
    for name in ("cortex", "external", "clock"):
        nodes = File(data_files=str(network_dir / f"{name}_nodes.h5"),
                     data_type_files=str(network_dir / f"{name}_node_types.csv"))
        node_counts[name] = len(nodes.nodes[name])
        edges = File(data_files=str(network_dir / f"{name}_cortex_edges.h5"),
                     data_type_files=str(network_dir / f"{name}_cortex_edge_types.csv"))
        population = edges.edges[f"{name}_to_cortex"]
        edge_counts[name] = len(population)
        if edge_counts[name]:
            first = next(iter(population))
            if not np.isfinite(first["syn_weight"]) or not np.isfinite(first["delay"]):
                raise ValueError(f"Nonfinite first {name} edge")
    expected_nodes = {"cortex": manifest["cortical_nodes"], "external": manifest["external_nodes"], "clock": manifest["clock_nodes"]}
    expected_edges = {"cortex": manifest["recurrent_contacts"], "external": manifest["external_contacts"], "clock": manifest["clock_contacts"]}
    if node_counts != expected_nodes or edge_counts != expected_edges:
        raise ValueError("BioNet SONATA-reader node/contact counts differ from owned structure")
    return node_counts, edge_counts


def _compile_mechanisms(output: Path) -> tuple[Path, Path, dict]:
    if _sha(MOD) != INDEPENDENT_MECHANISM_SHA256 or _sha(PROBE) != COMPILER_PROBE_SHA256:
        raise ValueError("Independent mechanism/compiler probe differs from approved primary")
    probe = _load_script(PROBE, "pdcm_scalable_v4_compiler_probe")
    if Path(probe.MECHANISM).resolve() != MOD.resolve():
        raise ValueError("Compiler probe does not consume the independent mechanism")
    compiler = Path(sys.prefix) / "bin/nrnivmodl"
    bmtk_spec = importlib.util.find_spec("bmtk")
    if bmtk_spec is None or bmtk_spec.origin is None or not compiler.is_file():
        raise RuntimeError("Compatible BioNet/NEURON compiler paths are required")
    vecstim = Path(bmtk_spec.origin).parent / "utils/scripts/bionet/mechanisms/modfiles/vecevent.mod"
    if not vecstim.is_file():
        raise RuntimeError("BioNet VecStim mechanism source is missing")
    identities = {"compiler_path": str(compiler.resolve()), "compiler_sha256": _sha(compiler),
                  "bmtk_vecstim_source_path": str(vecstim.resolve()), "bmtk_vecstim_source_sha256": _sha(vecstim)}
    shutil.copy2(PROBE, output / PROBE.name)
    mechanisms = probe._mechanisms(output)
    if (_sha(mechanisms / MOD.name) != INDEPENDENT_MECHANISM_SHA256
            or _sha(mechanisms / "vecevent.mod") != identities["bmtk_vecstim_source_sha256"]):
        raise ValueError("Compiled mechanism inputs differ from frozen independent sources")
    compiled = list(mechanisms.glob("*/libnrnmech.*"))
    if len(compiled) != 1:
        raise RuntimeError("Expected exactly one compiled mechanism library")
    return mechanisms, compiled[0], identities


def _config(copied: Path, output: Path, mechanisms: Path, manifest: dict) -> dict:
    network = copied / "network"
    names = ("cortex", "external", "clock")
    native = output / "native_output"
    return {"target_simulator": "BioNet",
        "run": {"tstart": 0., "tstop": manifest["duration_ms"], "dt": CLOCK_PERIOD_MS, "nsteps_block": 400},
        "conditions": {"v_init": -65., "celsius": 34.},
        "components": {"point_neuron_models_dir": str((copied / "components").resolve()),
            "synaptic_models_dir": str((copied / "components").resolve()), "mechanisms_dir": str(mechanisms.resolve())},
        "networks": {"nodes": [{"nodes_file": str((network / f"{name}_nodes.h5").resolve()),
            "node_types_file": str((network / f"{name}_node_types.csv").resolve())} for name in names],
            "edges": [{"edges_file": str((network / f"{name}_cortex_edges.h5").resolve()),
                "edge_types_file": str((network / f"{name}_cortex_edge_types.csv").resolve())} for name in names]},
        "node_sets": {f"{name}_input": {"population": name} for name in ("external", "clock")},
        "inputs": {f"{name}_input": {"input_type": "spikes", "module": "sonata",
            "node_set": f"{name}_input", "input_file": str((copied / f"{name}_spikes.h5").resolve())}
            for name in ("external", "clock")},
        "output": {"output_dir": str(native.resolve()), "spikes_file": str((native / "spikes.h5").resolve()),
            "spikes_sort_order": "by_time", "overwrite_output_dir": False}}


def _run(source: Path, output: Path) -> dict:
    source, output = source.resolve(), output.resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite {output}")
    if output == source or source in output.parents or output in source.parents:
        raise ValueError("Run and immutable structure must be separate non-nested folders")
    manifest = _verify_structure(source)
    import bmtk
    import neuron
    from neuron import h
    from bmtk.simulator import bionet
    from bmtk.simulator.bionet.io_tools import io as bionet_io
    if bmtk.__version__ != "1.2.0" or neuron.__version__ != "9.0.1":
        raise ValueError("Scalable-v4 requires the source-qualified BMTK1.2.0/NEURON9.0.1 runtime")
    output.mkdir(parents=True)
    copied = output / "input"
    shutil.copytree(source, copied)
    _verify_structure(copied)
    script_copy = output / Path(__file__).name
    shutil.copy2(Path(__file__), script_copy)
    node_counts, edge_counts = _reader_counts(copied / "network", manifest)
    mechanisms, compiled, compiler_identity = _compile_mechanisms(output)
    neuron.load_mechanisms(str(mechanisms))

    @bionet.cell_model("digifly:DigiflyPDCMEvent", model_type="point_neuron")
    def _cell(node, _template, dynamics):
        cell = h.DigiflyPDCMEvent()
        cell.Iext, cell.m, cell.i = float(dynamics["Iext"]), float(node["m_init"]), 0.
        return cell

    config = _config(copied, output, mechanisms, manifest)
    _json(output / "config.json", config)
    provenance = {"schema": SCHEMA, "construction_policy": POLICY, "source_structure": str(source),
        "paper_condition": True, "primary_target": manifest["primary_target"],
        "normal_parameterization": manifest["normal_parameterization"], "independent_inputs": True,
        "input_manifest_sha256": _sha(copied / "manifest.json"),
        "generator_sha256": _sha(copied / "provenance" / STRUCTURE_SCRIPT.name),
        "runner_sha256": _sha(script_copy), "compiler_probe_sha256": _sha(PROBE),
        "mechanism_source_sha256": _sha(MOD), "mechanism_compiled_sha256": _sha(compiled),
        **compiler_identity, "python_executable": sys.executable,
        "python_executable_sha256": _sha(Path(sys.executable).resolve()),
        "python_version": sys.version, "bmtk_version": bmtk.__version__, "neuron_version": neuron.__version__,
        "node_counts": node_counts, "edge_counts": edge_counts, "scale": manifest["scale"],
        "drive": manifest["drive"], "duration_ms": manifest["duration_ms"], "dt_ms": CLOCK_PERIOD_MS,
        "clock_period_ms": CLOCK_PERIOD_MS, "input_delay_ms": INPUT_DELAY_MS,
        "stimulus_seed": manifest["stimulus_seed"], "network_seed": manifest["network_seed"],
        "scientific_acceptance": False}
    _json(output / "provenance.json", provenance)
    build_start = time.monotonic()
    with (output / "run.log").open("x", encoding="utf-8") as log, contextlib.redirect_stdout(log), \
            contextlib.redirect_stderr(log), _capture_bionet_log(bionet_io, log):
        cfg = bionet.Config.from_json(str(output / "config.json"))
        cfg.build_env()
        network = bionet.BioNetwork.from_config(cfg)
        simulation_start = time.monotonic()
        bionet.BioSimulator.from_config(cfg, network=network).run()
        simulation_walltime = time.monotonic() - simulation_start
    build_simulation_walltime = time.monotonic() - build_start
    lifecycle = (output / "run.log").read_text()
    if not all(term in lifecycle for term in ("Building cells", "Running simulation", "Simulation completed")):
        raise ValueError("BioNet lifecycle logger did not preserve build/run/completion")
    audit_start = time.monotonic()
    census = census_recurrent_arrivals(copied / "network/cortex_cortex_edges.h5",
        output / "native_output/spikes.h5", output / "cortical_spike_census_index.h5",
        node_count=manifest["cortical_nodes"], duration_ms=manifest["duration_ms"], clock_events=manifest["clock_events"])
    result = {**provenance, **census, "state": "completed_technical_unreviewed",
        "simulation_walltime_s": simulation_walltime, "network_build_and_simulation_walltime_s": build_simulation_walltime,
        "arrival_census_walltime_s": time.monotonic() - audit_start}
    _json(output / "technical_result.json", result)
    _json(output / "artifact_manifest.json", {"schema": "digifly-pdcm-bionet-scalable-v4-run-artifacts",
        "files": _artifact_inventory(output), "scientific_acceptance": False})
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--structure", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite {output}")
    try:
        result = _run(args.structure.resolve(), output)
    except Exception:
        if output.exists():
            _json(output / "failure.json", {"state": "failed_preserved", "traceback": traceback.format_exc()})
            _json(output / "artifact_manifest.json", {"schema": "digifly-pdcm-bionet-scalable-v4-run-artifacts",
                "files": _artifact_inventory(output), "scientific_acceptance": False})
        raise
    print(json.dumps({key: result[key] for key in ("state", "cortical_spikes", "spiking_cells",
                     "recurrent_arrival_clock_grid_ties_1ns_tolerance")}, sort_keys=True))


if __name__ == "__main__":
    main()
