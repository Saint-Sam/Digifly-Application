"""Source-bound, streamed BioNet paper-window analysis, separately versioned v4.

No simulator is imported or run. Immutable run/input inventories are checked,
raw spikes are scanned in bounded chunks, and a temporary disk-backed per-cell
time index permits an independently written streamed recurrent-arrival census.
Accepted primary-v3 code and observations are never modified. Technical checks
and descriptive outcomes are not scientific, cohort, or equivalence acceptance.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import random
import tempfile
from typing import Mapping

import h5py
import numpy as np

from digifly_app.workers.pdcm_netpyne_worker import (
    POPULATIONS, _paper_absolute_cross_correlation, _paper_cross_correlation,
    _paper_synchrony,
)

SCHEMA = "digifly-bionet-released-method-paper-window-audit-scalable-v4"
BUILDER_NAME = "build_pdcm_bionet_scalable_v4.py"
RUNNER_NAME = "run_pdcm_bionet_scalable_v4.py"
CONDITION_KEYS = ("scale", "duration_ms", "drive", "network_seed", "stimulus_seed")
FULL_COUNTS = np.array((20683, 5834, 21915, 5479, 4850, 1065, 14395, 2948))
DEFAULT_CHUNK = 65536
ARRIVAL_ITEMS = 262144


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def _checked_relative(root: Path, relative: str) -> Path:
    if not isinstance(relative, str):
        raise ValueError("Unsafe manifest path")
    item = Path(relative)
    if item.is_absolute() or not item.parts or any(
            part in (".", "..") for part in item.parts):
        raise ValueError("Unsafe manifest path")
    path = root / item
    if any(part.is_symlink() for part in (path, *path.parents) if part != root.parent):
        raise ValueError("Symlinked artifact path")
    if not path.is_file() or not path.resolve().is_relative_to(root):
        raise ValueError("Artifact is outside its immutable run")
    return path


def _checked_inventory(root: Path, manifest_name: str, field: str,
                       *, structure_records: bool = False) -> dict:
    manifest_path = root / manifest_name
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("Missing regular immutable manifest")
    manifest = json.loads(manifest_path.read_text())
    files = manifest.get(field)
    if not isinstance(files, dict):
        raise ValueError("Invalid immutable manifest file map")
    observed = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("Symlinked artifact path")
        if path.is_file():
            observed.add(str(path.relative_to(root)))
    if observed != set(files) | {manifest_name}:
        raise ValueError("Run file set differs from its immutable artifact manifest")
    for relative, record in files.items():
        path = _checked_relative(root, relative)
        digest = record.get("sha256") if structure_records else record
        if (structure_records and (type(record.get("bytes")) is not int or
                                   path.stat().st_size != record["bytes"])):
            raise ValueError(f"Input artifact size mismatch: {relative}")
        if not isinstance(digest, str) or sha(path) != digest:
            raise ValueError(f"Run artifact hash mismatch: {relative}")
    return manifest


def _verify_structure(path: Path) -> dict:
    from scripts import build_pdcm_bionet_scalable_v4 as builder
    return builder.verify(path)


def _condition(structure: dict, spec: Mapping | None) -> tuple[np.ndarray, bool]:
    if spec is not None and any(spec.get(key) != structure.get(key) for key in CONDITION_KEYS):
        raise ValueError("Requested condition differs from frozen run")
    scale, duration = structure.get("scale"), structure.get("duration_ms")
    if (type(scale) not in (int, float) or not math.isfinite(scale) or not 0 < scale <= 1
            or type(duration) not in (int, float) or not math.isfinite(duration)
            or structure.get("drive") not in ("balanced_poisson", "balanced_dc")):
        raise ValueError("Invalid exact condition identity")
    for key in ("network_seed", "stimulus_seed"):
        if type(structure.get(key)) is not int or structure[key] < 0:
            raise ValueError("Seeds must be nonnegative integer roots")
    full_window = duration == 60100.0
    if not full_window and not (scale == .1 and duration == 4000.0 and
                                structure["drive"] == "balanced_poisson"):
        raise ValueError("No approved analysis window for this duration/condition")
    counts = np.floor(scale * FULL_COUNTS).astype(np.int64)
    if (structure.get("population_order") != list(POPULATIONS) or
            structure.get("scaled_counts") != counts.tolist() or np.any(counts <= 0) or
            structure.get("cortical_nodes") != int(counts.sum()) or
            structure.get("clock_events") != round(duration / .025)):
        raise ValueError("Population/clock ownership differs from exact scale")
    if (structure.get("matched_reference") is not None or
            structure.get("schema") != "digifly-pdcm-bionet-scalable-v4-structure" or
            structure.get("construction_policy") != "released_method_fixed_unique_integer_variance_scalable_v4" or
            structure.get("independent_inputs") is not True or
            structure.get("primary_target") != "independently_reimplemented_released_executable_method" or
            structure.get("normal_parameterization") !=
            "mean_variance; numpy_sd=sqrt(variance); hard max clipping"):
        raise ValueError("Independent released-method source contract differs")
    if any(structure.get(key) != value for key, value in
           (("dt_ms", .025), ("clock_period_ms", .025), ("input_delay_ms", .5))):
        raise ValueError("Fixed clock/input timing contract differs")
    return counts, full_window


def check_execution_provenance(run: Path, technical: dict, structure: dict,
                               counts: np.ndarray, full_window: bool) -> dict:
    provenance = json.loads((run / "provenance.json").read_text())
    config = json.loads((run / "config.json").read_text())
    if (provenance.get("schema") != "digifly-bionet-released-method-technical-scalable-v4" or
            provenance.get("independent_inputs") is not True or
            provenance.get("normal_parameterization") != structure["normal_parameterization"]):
        raise ValueError("Executed scalable-v4 source contract differs")
    for key in CONDITION_KEYS:
        if technical.get(key) != structure[key] or provenance.get(key) != structure[key]:
            raise ValueError(f"Execution provenance differs for {key}")
    for key, value in provenance.items():
        if technical.get(key) != value:
            raise ValueError(f"Technical result differs from frozen provenance: {key}")
    n = int(counts.sum())
    nodes = {"cortex": n, "external": structure["external_nodes"], "clock": 1}
    edges = {"cortex": structure["recurrent_contacts"],
             "external": structure["external_contacts"], "clock": n}
    if (provenance.get("node_counts") != nodes or provenance.get("edge_counts") != edges or
            provenance.get("paper_condition") is not full_window or
            provenance.get("construction_policy") != structure.get("construction_policy") or
            technical.get("state") != "completed_technical_unreviewed" or
            technical.get("scientific_acceptance") is not False or
            config.get("target_simulator") != "BioNet" or
            config.get("run", {}).get("tstop") != structure["duration_ms"] or
            config["run"].get("tstart") != 0.0 or config["run"].get("dt") != .025):
        raise ValueError("Execution node/edge/condition/configuration claims differ")
    pins = {"input_manifest_sha256": run / "input/manifest.json",
            "generator_sha256": run / "input/provenance" / BUILDER_NAME,
            "runner_sha256": run / RUNNER_NAME,
            "mechanism_source_sha256": run / "mechanisms/DigiflyPDCMEvent.mod"}
    for key, path in pins.items():
        if provenance.get(key) != sha(path):
            raise ValueError(f"Executed source provenance differs: {key}")
    compiled = list((run / "mechanisms").glob("*/libnrnmech.*"))
    if len(compiled) != 1 or sha(compiled[0]) != provenance.get("mechanism_compiled_sha256"):
        raise ValueError("Compiled mechanism provenance differs")
    if "compiler_probe_sha256" in provenance:
        probe = run / "probe_pdcm_bionet_independent.py"
        if sha(probe) != provenance["compiler_probe_sha256"]:
            raise ValueError("Executed compiler helper provenance differs")
    if "bmtk_vecstim_source_sha256" in provenance and (
            sha(run / "mechanisms/vecevent.mod") != provenance["bmtk_vecstim_source_sha256"]):
        raise ValueError("Executed BioNet VecStim source differs")
    if "spike_index_sha256" in technical:
        index_path = run / "cortical_spike_census_index.h5"
        if sha(index_path) != technical["spike_index_sha256"]:
            raise ValueError("Recorded runtime spike-index hash differs")
        with h5py.File(index_path, "r") as handle:
            if (handle.attrs.get("schema") != "digifly-pdcm-cortical-spike-census-index-v1" or
                    handle.attrs.get("native_spikes_sha256") != sha(run / "native_output/spikes.h5")):
                raise ValueError("Runtime spike-index source binding differs")
    if not all(term in (run / "run.log").read_text() for term in
               ("Building cells", "Running simulation", "Simulation completed")):
        raise ValueError("Lifecycle logs incomplete")
    return provenance


def _read_spike_chunks(path: Path, chunk_size: int):
    with h5py.File(path, "r") as handle:
        group = handle["spikes/cortex"]
        ids, times = group["node_ids"], group["timestamps"]
        if ids.ndim != 1 or times.ndim != 1 or len(ids) != len(times):
            raise ValueError("Invalid cortical spike dataset shape/length")
        if ids.dtype.kind not in "iu" or times.dtype.kind not in "fiu":
            raise ValueError("Cortical spikes must use integer IDs and numeric times")
        for begin in range(0, len(ids), chunk_size):
            yield ids[begin:begin + chunk_size], times[begin:begin + chunk_size]


def _groups(ids: np.ndarray, values: np.ndarray):
    """Stable bounded-chunk grouping without O(unique cells × chunk) masks."""
    if not len(ids):
        return
    order = np.argsort(ids, kind="stable")
    ordered_ids, ordered_values = ids[order], values[order]
    boundaries = np.r_[0, np.flatnonzero(ordered_ids[1:] != ordered_ids[:-1]) + 1, len(ids)]
    for begin, end in zip(boundaries[:-1], boundaries[1:]):
        yield int(ordered_ids[begin]), ordered_values[begin:end]


def _scan_spikes(path: Path, counts: np.ndarray, duration: float, sample_seed: int,
                 *, chunk_size: int = DEFAULT_CHUNK) -> dict:
    """Validate ownership/order while retaining at most 1000 sampled cells/pop."""
    n = int(counts.sum())
    boundaries = np.cumsum(counts)
    begins = np.r_[0, boundaries[:-1]]
    sampled = {pop: sorted(random.Random(sample_seed + i).sample(
        range(int(begins[i]), int(boundaries[i])), min(1000, int(counts[i]))))
        for i, pop in enumerate(POPULATIONS)}
    sampled_ids = set(gid for group in sampled.values() for gid in group)
    times_by_gid = {gid: [] for gid in sampled_ids}
    total_counts = np.zeros(n, dtype=np.int64)
    settled_counts = np.zeros(n, dtype=np.int64)
    previous = np.full(n, -np.inf)
    # Online interval summaries are O(cells), including silent/undefined cells.
    previous_settled = np.full(n, np.nan)
    interval_n = np.zeros(n, dtype=np.int64)
    interval_mean = np.zeros(n)
    interval_m2 = np.zeros(n)
    full_window = duration == 60100.
    population_bins_60s = np.zeros((8, 20000), dtype=np.int64) if full_window else None
    population_bins_5s = np.zeros((8, 1666), dtype=np.int64) if full_window else None
    startup_ids = []
    total = settled = 0
    first = last = None
    previous_global_time = -np.inf
    for ids, times in _read_spike_chunks(path, chunk_size):
        if (not np.all((ids >= 0) & (ids < n)) or not np.all(np.isfinite(times)) or
                not np.all((times >= 0) & (times <= duration))):
            raise ValueError("Nonfinite, out-of-window or unowned cortical spike")
        if len(times) and (times[0] < previous_global_time or np.any(np.diff(times) < 0)):
            raise ValueError("Native by-time cortical output is not sorted")
        if len(times):
            previous_global_time = float(times[-1])
        ids = ids.astype(np.int64)
        total += len(ids)
        if len(times):
            first = float(times.min()) if first is None else min(first, float(times.min()))
            last = float(times.max()) if last is None else max(last, float(times.max()))
        np.add.at(total_counts, ids, 1)
        if full_window:
            pops = np.searchsorted(boundaries, ids, side="right")
            for end, bins in ((60100., population_bins_60s), (5098., population_bins_5s)):
                window = (times >= 100) & (times < end)
                np.add.at(bins, (pops[window], ((times[window] - 100.) / 3.).astype(np.int64)), 1)
        for gid, cell_times in _groups(ids, times):
            if cell_times[0] < previous[gid] or np.any(np.diff(cell_times) < 0):
                raise ValueError("Per-cell spike times are not sorted")
            previous[gid] = cell_times[-1]
            if gid in sampled_ids:
                times_by_gid[int(gid)].extend(float(t) for t in cell_times if 100 <= t < duration)
            accepted = cell_times[(cell_times >= 100) & (cell_times < duration)]
            settled_counts[gid] += len(accepted)
            settled += len(accepted)
            if len(accepted):
                intervals = np.diff(accepted)
                if math.isfinite(previous_settled[gid]):
                    intervals = np.r_[accepted[0] - previous_settled[gid], intervals]
                for interval in intervals:
                    interval_n[gid] += 1
                    delta = interval - interval_mean[gid]
                    interval_mean[gid] += delta / interval_n[gid]
                    interval_m2[gid] += delta * (interval - interval_mean[gid])
                previous_settled[gid] = accepted[-1]
        startup_ids.extend(ids[np.isclose(times, .5, rtol=0, atol=1e-12)].tolist())
    cv = np.full(n, np.nan)
    defined = (interval_n >= 2) & (interval_mean > 0)
    cv[defined] = np.sqrt(interval_m2[defined] / interval_n[defined]) / interval_mean[defined]
    return {"sampled": sampled, "times_by_gid": times_by_gid,
            "total_counts": total_counts, "settled_counts": settled_counts,
            "cv": cv, "startup_ids": startup_ids, "total": total, "settled": settled,
            "first": first, "last": last, "whole_bins_60s": population_bins_60s,
            "whole_bins_5s": population_bins_5s}


def _disk_spike_index(path: Path, indexed: Path, counts: np.ndarray,
                      chunk_size: int) -> np.ndarray:
    """Stable per-cell timestamp index built without whole-file reads/sorting."""
    ends = np.cumsum(counts, dtype=np.int64)
    begins = np.r_[0, ends[:-1]]
    written = begins.copy()
    with h5py.File(indexed, "x") as out:
        data = out.create_dataset("times", shape=(int(ends[-1]),), dtype="f8")
        for ids, times in _read_spike_chunks(path, chunk_size):
            for gid, selected in _groups(ids, times):
                lo = int(written[int(gid)])
                data[lo:lo + len(selected)] = selected
                written[int(gid)] += len(selected)
        if not np.array_equal(written, ends):
            raise ValueError("Disk-backed spike index census differs")
    return np.r_[0, ends]


def census_arrivals(edge_path: Path, spike_path: Path, spike_counts: np.ndarray,
                    duration_ms: float, clock_events: int, *,
                    edge_chunk_size: int = DEFAULT_CHUNK,
                    spike_chunk_size: int = 8192,
                    arrival_batch_items: int = ARRIVAL_ITEMS) -> dict:
    """Independent v4 count, chunking edges, spike index and Cartesian batches."""
    for value in (edge_chunk_size, spike_chunk_size, arrival_batch_items):
        if type(value) is not int or value < 1:
            raise ValueError("Census chunk limits must be positive integers")
    spike_counts = np.asarray(spike_counts)
    if (spike_counts.ndim != 1 or not len(spike_counts) or spike_counts.dtype.kind not in "iu" or
            np.any(spike_counts < 0) or type(clock_events) is not int or clock_events <= 0 or
            type(duration_ms) not in (int, float) or not math.isfinite(duration_ms) or duration_ms <= 0):
        raise ValueError("Census ownership/duration/clock metadata is invalid")
    arrivals = ties = edge_count = 0
    with tempfile.TemporaryDirectory(prefix="digifly-bionet-v4-analysis-") as temporary:
        index_path = Path(temporary) / "cortical_times_by_node.h5"
        offsets = _disk_spike_index(spike_path, index_path, spike_counts, DEFAULT_CHUNK)
        with h5py.File(index_path, "r") as index, h5py.File(edge_path, "r") as edges_file:
            edges = edges_file["edges/cortex_to_cortex"]
            sources, delays = edges["source_node_id"], edges["0/delay"]
            if (sources.ndim != 1 or delays.ndim != 1 or len(sources) != len(delays)
                    or sources.dtype.kind not in "iu"):
                raise ValueError("Invalid recurrent edge census data")
            edge_count = len(sources)
            for start in range(0, edge_count, edge_chunk_size):
                ids = sources[start:start + edge_chunk_size]
                delay = delays[start:start + edge_chunk_size]
                if (not np.all(ids < len(spike_counts)) or not np.all(ids >= 0) or
                        not np.all(np.isfinite(delay)) or not np.all(delay >= .1)):
                    raise ValueError("Unowned or invalid recurrent edge")
                for gid, cell_delays in _groups(ids, delay):
                    # Cap products even if one source has unusually many edges.
                    delay_block_size = min(len(cell_delays), arrival_batch_items)
                    for offset in range(0, len(cell_delays), delay_block_size):
                        block = cell_delays[offset:offset + delay_block_size]
                        time_block_size = min(spike_chunk_size, max(1, arrival_batch_items // len(block)))
                        for lo in range(int(offsets[int(gid)]), int(offsets[int(gid) + 1]), time_block_size):
                            ts = index["times"][lo:min(lo + time_block_size, int(offsets[int(gid) + 1]))]
                            delivered = ts[:, None] + block[None, :]
                            within = delivered <= duration_ms
                            arrivals += int(np.count_nonzero(within))
                            grid = np.rint((delivered - .5) / .025)
                            ties += int(np.count_nonzero(within & (grid >= 0) & (grid < clock_events) &
                                (np.abs(delivered - (.5 + grid * .025)) <= 1e-9)))
    return {"recurrent_arrivals_within_run": arrivals,
            "recurrent_arrival_clock_grid_ties_1ns_tolerance": ties,
            "recurrent_contacts_censused": edge_count,
            "census_method": "independent streamed edges; disk-backed per-cell spike index; bounded Cartesian products",
            "arrival_batch_items": arrival_batch_items,
            "tie_census_limit": "Counts delivered clock-grid coincidences only; no internal refractory-release census or event-order inference"}


def _optional_number(value: float):
    return float(value) if math.isfinite(value) else ""


def _write_metrics(output: Path, counts: np.ndarray, scan: dict, sample_seed: int) -> None:
    sampled, times_by_gid = scan["sampled"], scan["times_by_gid"]
    _json(output / "sampling_manifest.json", {
        "schema_version": 1, "method": "uniform without replacement per population",
        "maximum_cells_per_population": 1000, "seed": sample_seed,
        "cell_ids": sampled, "sample_caps": {p: len(sampled[p]) for p in POPULATIONS},
        "note": "Deterministic unique feasible Digifly sample, not unpublished author sampled IDs"})
    with (output / "paper_metrics.csv").open("x", newline="") as stream, \
            (output / "synchrony_metric_comparison.csv").open("x", newline="") as sync_stream:
        writer, sync = csv.writer(stream), csv.writer(sync_stream)
        writer.writerow(("population", "sampled_cells", "mean_rate_hz_60s", "mean_cv_isi_60s",
            "synchrony_3ms_5s", "synchrony_3ms_60s", "mean_signed_cross_correlation_25ms_5s",
            "mean_absolute_cross_correlation_25ms_5s", "correlation_defined_cells",
            "sampled_silent_cells_60s", "sampled_cv_defined_cells_60s",
            "sampled_cv_undefined_cells_60s", "correlation_undefined_cells_5s"))
        sync.writerow(("population", "sampled_cells", "population_total_fano_3ms_5s",
                       "population_total_fano_3ms_60s", "per_cell_normalized_3ms_5s",
                       "per_cell_normalized_3ms_60s"))
        for pop in POPULATIONS:
            gids = sampled[pop]
            rate = float(scan["settled_counts"][gids].sum()) / (len(gids) * 60)
            values = scan["cv"][gids]
            valid_cv = values[np.isfinite(values)]
            sync5 = _paper_synchrony(gids, times_by_gid, start_ms=100, end_ms=5100)
            sync60 = _paper_synchrony(gids, times_by_gid, start_ms=100, end_ms=60100)
            signed, valid = _paper_cross_correlation(gids, times_by_gid, start_ms=100, end_ms=5100)
            absolute = _paper_absolute_cross_correlation(gids, times_by_gid, start_ms=100, end_ms=5100)
            writer.writerow((pop, len(gids), rate,
                float(valid_cv.mean()) if len(valid_cv) else "", _optional_number(sync5),
                _optional_number(sync60), _optional_number(signed), _optional_number(absolute), valid,
                int(np.count_nonzero(scan["settled_counts"][gids] == 0)), len(valid_cv),
                len(gids) - len(valid_cv), len(gids) - valid))
            sync.writerow((pop, len(gids), _optional_number(len(gids) * sync5),
                           _optional_number(len(gids) * sync60), _optional_number(sync5),
                           _optional_number(sync60)))
    # Table2 whole-network and Table3 sampled-cell contracts are never conflated.
    with (output / "whole_population_metrics.csv").open("x", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("population", "owned_cells", "settled_spikes", "mean_rate_hz_60s",
            "across_cell_rate_sd_population", "silent_cells_60s", "cv_defined_cells_60s",
            "cv_undefined_cells_60s", "mean_cv_isi_60s", "across_cell_cv_sd_population",
            "population_total_fano_3ms_5s", "population_total_fano_3ms_60s",
            "per_cell_normalized_synchrony_3ms_5s", "per_cell_normalized_synchrony_3ms_60s"))
        begin = 0
        for index, (pop, count) in enumerate(zip(POPULATIONS, counts)):
            cell_counts = scan["settled_counts"][begin:begin + count]
            rates = cell_counts / 60.
            cv = scan["cv"][begin:begin + count]
            cv = cv[np.isfinite(cv)]
            fanos = []
            for bins in (scan["whole_bins_5s"][index], scan["whole_bins_60s"][index]):
                mean = float(bins.mean())
                fanos.append(float(bins.var(ddof=0)) / mean if mean else float("nan"))
            writer.writerow((pop, int(count), int(cell_counts.sum()), float(rates.mean()),
                float(rates.std(ddof=0)), int(np.count_nonzero(cell_counts == 0)), len(cv),
                int(count) - len(cv), float(cv.mean()) if len(cv) else "",
                float(cv.std(ddof=0)) if len(cv) else "",
                *[_optional_number(fano) for fano in fanos],
                *[_optional_number(fano / int(count)) for fano in fanos]))
            begin += count


def analyze(run: Path, output: Path, spec: Mapping | None = None) -> dict:
    run, output = Path(run).resolve(), Path(output).resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite {output}")
    if output == run or output.is_relative_to(run) or run.is_relative_to(output):
        raise ValueError("Analysis sidecar must be separate from immutable run")
    _checked_inventory(run, "artifact_manifest.json", "files")
    structure = _checked_inventory(run / "input", "manifest.json", "files", structure_records=True)
    checked_structure = _verify_structure(run / "input")
    if checked_structure != structure:
        raise ValueError("Independent structure verifier returned different frozen identity")
    counts, full_window = _condition(structure, spec)
    technical = json.loads((run / "technical_result.json").read_text())
    provenance = check_execution_provenance(run, technical, structure, counts, full_window)
    spikes_path = run / "native_output/spikes.h5"
    scan = _scan_spikes(spikes_path, counts, structure["duration_ms"], structure["stimulus_seed"])
    if (scan["total"] != technical.get("cortical_spikes") or
            int(np.count_nonzero(scan["total_counts"])) != technical.get("spiking_cells") or
            scan["first"] != technical.get("first_spike_ms") or scan["last"] != technical.get("last_spike_ms")):
        raise ValueError("Independent cortical spike census differs")
    with h5py.File(run / "input/network/cortex_nodes.h5") as handle:
        initial = handle["nodes/cortex/0/m_init"][:]
    current_key = "dc_total_current_pa" if "dc_total_current_pa" in structure else "dc_compensation_pa"
    current = np.repeat(structure[current_key], counts)
    first_m = initial + .5 * ((-65. - initial) / 10. + current / 250.)
    if not np.array_equal(np.sort(scan["startup_ids"]), np.flatnonzero(first_m > -50.)):
        raise ValueError("First delivered clock-event census differs from initial-state oracle")
    census = census_arrivals(run / "input/network/cortex_cortex_edges.h5", spikes_path,
        scan["total_counts"], structure["duration_ms"], structure["clock_events"])
    if (census["recurrent_contacts_censused"] != structure["recurrent_contacts"] or
            any(census[key] != technical.get(key) for key in
                ("recurrent_arrivals_within_run", "recurrent_arrival_clock_grid_ties_1ns_tolerance"))):
        raise ValueError("Independent recurrent-arrival/clock-tie census differs")
    output.mkdir(parents=True)
    if full_window:
        _write_metrics(output, counts, scan, structure["stimulus_seed"])
    else:
        with (output / "descriptive_population_metrics.csv").open("x", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(("population", "owned_cells", "settled_spikes", "mean_rate_hz_descriptive",
                             "start_ms", "end_ms"))
            begin = 0
            for pop, count in zip(POPULATIONS, counts):
                total = int(scan["settled_counts"][begin:begin + count].sum())
                writer.writerow((pop, int(count), total, total / (int(count) * 3.9), 100, 4000))
                begin += count
    population_total = [int(values.sum()) for values in np.split(scan["total_counts"], np.cumsum(counts)[:-1])]
    result = {"schema": SCHEMA, "construction_policy": structure["construction_policy"],
        "technical_acceptance": True, "scientific_acceptance": False,
        **{key: structure[key] for key in CONDITION_KEYS},
        "cortical_cells": int(counts.sum()), "cortical_spikes": scan["total"],
        "per_population_spikes": dict(zip(POPULATIONS, population_total)),
        "post_settling_spikes": scan["settled"], "startup_spikes_at_0_5ms": len(scan["startup_ids"]),
        **census, "paper_timed_metrics_available": full_window,
        "analysis_windows_ms": {"settled": [100, 60100], "correlation": [100, 5100],
                                "synchrony_figures4_7": [100, 5100], "synchrony_figure8": [100, 60100]}
            if full_window else {"descriptive_settled": [100, 4000]},
        "figure9_spectral_or_granger_acceptance": False,
        "run_artifact_manifest_sha256": sha(run / "artifact_manifest.json"),
        "input_manifest_sha256": sha(run / "input/manifest.json"),
        "source_h5_sha256": sha(spikes_path),
        "runtime_versions": {key: provenance.get(key) for key in ("bmtk_version", "neuron_version")},
        "metric_contract": {"intervals": "half-open [start,end); final run-boundary spikes are validated but excluded",
            "rates": "silent cells remain in denominator; whole-population and sampled-cell tables distinct",
            "cv": "population SD of within-cell ISIs / within-cell mean; at least3 spikes and positive mean ISI",
            "synchrony": "3ms bins; only complete bins; normalized-count variance/mean and total-count Fano both retained",
            "correlation": "25ms bins [100,5100); signed Pearson and mean absolute pair correlations; constant cells undefined",
            "replicate_unit": "independent network, not neurons/bins; no cohort inference"},
        "caveats": ["One network/stimulus realization is descriptive, not statistical replication or equivalence.",
            "No author graph, builder, original sampled IDs or unavailable NEST values are supplied.",
            "BioNet shares NEURON with NetPyNE; this is not an independent solver.",
            "Both synchrony normalizations and signed/absolute correlations remain explicit method sensitivities.",
            "The independent arrival census does not establish internal refractory-release or simultaneous-event ordering."]}
    if not full_window:
        result["caveats"].append("The10%/4s Figure9 condition receives no60s/5s paper metrics; spectral/Granger claims need their separate analysis.")
    _json(output / "audit.json", result)
    _json(output / "analysis_manifest.json", {
        "schema": "digifly-bionet-scalable-v4-analysis-manifest", "source_run": str(run),
        "source_artifact_manifest_sha256": result["run_artifact_manifest_sha256"],
        "analysis_script_sha256": sha(Path(__file__)),
        "shared_metric_function_sha256": sha(Path(_paper_synchrony.__code__.co_filename)),
        "files_sha256": {str(path.relative_to(output)): sha(path) for path in sorted(output.rglob("*")) if path.is_file()}})
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--spec", type=Path, help="Optional frozen exact condition JSON")
    arguments = parser.parse_args()
    condition = json.loads(arguments.spec.read_text()) if arguments.spec else None
    print(json.dumps(analyze(arguments.run, arguments.output, condition), sort_keys=True))
