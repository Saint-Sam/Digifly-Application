"""MPI-safe, relocatable worker for the pinned Romaro et al. PDCM model.

Launch with ``mpiexec -n N nrniv -python -mpi <this file>`` after compiling
``model/IntFire_PD.mod`` once outside the ranks. The request is supplied via
``DIGIFLY_PDCM_REQUEST`` so NEURON need not parse application arguments.
Every rank writes only its own shards. Rank zero combines them after a barrier;
NetPyNE's all-cell gather is intentionally avoided at full paper scale.
"""

from __future__ import annotations

import csv
import argparse
from bisect import bisect_right
from datetime import datetime, timezone
import hashlib
import heapq
import json
import math
import numbers
import os
from pathlib import Path
import random
import sys


POPULATIONS = ("L2e", "L2i", "L4e", "L4i", "L5e", "L5i", "L6e", "L6i")
MODEL_COMMIT = "a6e39c19a0d85e0e907acac33791363591a88f45"
ORIGINAL_SHA256 = {
    "cfg.py": "1b7ad4912a7645ed35b49cb649adb66bd511234f4f50bd3965d5484d9a34d1f2",
    "init.py": "0fd97727ffdd0ba4049ff92437ee805b2be9f7aed837cce70d4f2604124e38e9",
    "netParams.py": "361356e4beb64e59f4eb4195348ebe80d12a2477733cc2a972df297d96299a1a",
    "IntFire_PD.mod": "9167520a8179c3b84c0a4d29d392d09d770063ffef5bbd8bbab5a2ee50f44940",
}
_BUNDLE_FILES = (
    "worker_request.json",
    "source_manifest.json",
    *(f"model/{name}" for name in (*ORIGINAL_SHA256, "LICENSE")),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _apply_seed_streams(cfg, spec: dict) -> dict[str, int]:
    """Keep v1 defaults unless an independent network realization is requested.

    This small routine is mirrored in the standalone serial worker. Each domain's
    SHA-256 mask is XORed with the 31-bit root, so distinct roots produce
    distinct values within every stream without relying on Python hash seeds.
    """
    cfg.seeds["stim"] = int(spec["seed"])
    network_seed = spec.get("network_seed")
    if network_seed is not None:
        if type(network_seed) is not int or not 0 <= network_seed < 2**31:
            raise ValueError("PDCM network seed must be a nonnegative 31-bit integer")
        for domain in ("conn", "loc", "cell", "m"):
            mask = int.from_bytes(
                hashlib.sha256(f"digifly-pdcm-network-v1:{domain}".encode("ascii")).digest()[:4],
                "big",
            ) & 0x7fffffff
            cfg.seeds[domain] = network_seed ^ mask
    return {domain: int(cfg.seeds[domain]) for domain in ("conn", "loc", "cell", "m", "stim")}


def _prepared_bundle(request_path: Path) -> tuple[Path, Path, dict, dict]:
    """Use the request's actual parent, never its old absolute run_root field."""
    request_path = request_path.expanduser()
    if request_path.name != "worker_request.json" or request_path.is_symlink():
        raise ValueError("Expected a regular worker_request.json inside the run bundle")
    request_path = request_path.resolve(strict=True)
    run_root = request_path.parent
    request = json.loads(request_path.read_text(encoding="utf-8"))
    if request.get("schema_version") != 1 or request.get("workflow") != "pdcm_replication_v1":
        raise ValueError("Unsupported PDCM worker request")
    requested_root = request.get("run_root")
    if requested_root != "." and not (
        isinstance(requested_root, str)
        and Path(requested_root).is_absolute()
        and Path(requested_root).resolve() == run_root
    ):
        raise ValueError("PDCM worker request is outside its run folder")
    spec = request.get("spec")
    if not isinstance(spec, dict):
        raise ValueError("PDCM request has no run specification")
    try:
        scale = float(spec["scale"])
        duration = float(spec["duration_ms"])
        seed = int(spec["seed"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Invalid PDCM run specification") from exc
    if not (0.01 <= scale <= 1.0 and 200 <= duration <= 61_000 and 0 <= seed < 2**31):
        raise ValueError("PDCM scale, duration, or seed is out of range")
    if spec.get("drive") not in {"balanced_poisson", "balanced_dc", "unbalanced_poisson"}:
        raise ValueError("Unsupported PDCM drive condition")
    if type(spec.get("export_convergence", False)) is not bool:
        raise ValueError("PDCM convergence export must be a boolean")
    network_seed = spec.get("network_seed")
    if network_seed is not None and (type(network_seed) is not int or not 0 <= network_seed < 2**31):
        raise ValueError("PDCM network seed must be a nonnegative 31-bit integer")
    model = run_root / "model"
    if not model.is_dir() or model.is_symlink():
        raise ValueError("Prepared model must be a regular directory")
    source_manifest_path = run_root / "source_manifest.json"
    if not source_manifest_path.is_file() or source_manifest_path.is_symlink():
        raise ValueError("Prepared source manifest is missing")
    provenance = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    if provenance.get("model_commit") != MODEL_COMMIT:
        raise ValueError("Prepared model is not the pinned paper revision")
    if any(provenance.get("original_sha256", {}).get(name) != digest for name, digest in ORIGINAL_SHA256.items()):
        raise ValueError("Prepared model manifest does not identify the pinned paper source")
    executed = provenance.get("executed_sha256")
    if not isinstance(executed, dict) or set(executed) != set(ORIGINAL_SHA256) | {"LICENSE"}:
        raise ValueError("Prepared source manifest has an unexpected file set")
    for name, expected in executed.items():
        source = model / name
        if not source.is_file() or source.is_symlink() or _sha256(source) != expected:
            raise ValueError(f"Prepared PDCM model file changed: {name}")
    bundle_path = run_root / "bundle_manifest.json"
    if bundle_path.exists():
        if not bundle_path.is_file() or bundle_path.is_symlink():
            raise ValueError("PDCM bundle manifest must be a regular file")
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        files = bundle.get("files_sha256")
        if (
            bundle.get("schema_version") != 1
            or bundle.get("workflow") != "pdcm_replication_v1"
            or bundle.get("model_commit") != MODEL_COMMIT
            or not isinstance(files, dict)
            or set(files) != set(_BUNDLE_FILES)
        ):
            raise ValueError("PDCM bundle manifest has an unexpected file list")
        for name, expected in files.items():
            bundled_file = run_root / name
            if not bundled_file.is_file() or bundled_file.is_symlink() or _sha256(bundled_file) != expected:
                raise ValueError(f"PDCM bundle transfer check failed: {name}")
    elif requested_root == ".":
        raise ValueError("Portable PDCM run is missing its bundle manifest")
    return run_root, model, spec, provenance


def _compiled_library(model: Path) -> Path:
    # NEURON releases place the library either directly under the architecture
    # directory or under .libs; a build may leave both copies behind.
    direct = sorted(path for path in model.glob("*/libnrnmech.*") if path.is_file() and not path.is_symlink())
    nested = sorted(path for path in model.glob("*/.libs/libnrnmech.*") if path.is_file() and not path.is_symlink())
    candidates = direct or nested
    if len(candidates) != 1:
        raise RuntimeError("Compile IntFire_PD.mod once in model/ before launching MPI ranks")
    return candidates[0]


def _rank_paths(run_root: Path, rank: int) -> tuple[Path, Path, Path]:
    shard_dir = run_root / "mpi_shards"
    return (
        shard_dir / f"cells_rank_{rank:06d}.csv",
        shard_dir / f"spikes_rank_{rank:06d}.csv",
        shard_dir / f"metrics_rank_{rank:06d}.json",
    )


def _peak_rss_mib() -> float:
    import resource

    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux reports KiB; macOS reports bytes. Athene uses Linux.
    divisor = 1024 if sys.platform.startswith("linux") else 1024 * 1024
    return round(raw / divisor, 3)


def _write_rank_cells(
    run_root: Path,
    rank: int,
    cells: list,
    cortical_gid_limit: int,
    convergence_counts: dict[str, int] | None = None,
) -> dict:
    cells_path, _, metrics_path = _rank_paths(run_root, rank)
    by_population = {pop: 0 for pop in POPULATIONS}
    contacts = {"total": 0, "recurrent": 0, "external": 0, "unclassified": 0}
    convergence = None
    boundaries = []
    if convergence_counts is not None:
        if tuple(convergence_counts) != POPULATIONS or any(type(n) is not int or n <= 0 for n in convergence_counts.values()):
            raise ValueError("Convergence export requires all eight ordered cortical population sizes")
        total = 0
        for pop in POPULATIONS:
            total += convergence_counts[pop]
            boundaries.append(total)
        if total != cortical_gid_limit:
            raise ValueError("Convergence population sizes do not match cortical gid range")
        convergence = {pre: {post: 0 for post in POPULATIONS} for pre in POPULATIONS}
    gids = set()
    with cells_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(("gid", "population", "x_um", "y_um", "z_um"))
        for cell in cells:
            pop = cell.tags.get("pop")
            if pop not in by_population or cell.tags.get("cellModel") != "IntFire_PD":
                continue
            gid = int(cell.gid)
            if gid in gids:
                raise RuntimeError(f"Duplicate local cortical gid {gid} on rank {rank}")
            gids.add(gid)
            by_population[pop] += 1
            writer.writerow((gid, pop, cell.tags.get("x", ""), cell.tags.get("y", ""), cell.tags.get("z", "")))
            distinct_inputs = set() if convergence is not None else None
            for conn in cell.conns:
                contacts["total"] += 1
                pre = conn.get("preGid")
                if isinstance(pre, numbers.Integral):
                    contacts["recurrent" if 0 <= int(pre) < cortical_gid_limit else "external"] += 1
                    if distinct_inputs is not None and 0 <= int(pre) < cortical_gid_limit:
                        distinct_inputs.add(int(pre))
                elif pre == "NetStim":
                    contacts["external"] += 1
                else:
                    contacts["unclassified"] += 1
            if distinct_inputs is not None:
                # Match the authors' plotConn(feature='convergence', synOrConn='conn'):
                # count each presynaptic cortical GID once per postsynaptic cell.
                # This temporary set is discarded after each cell; no edge list is saved.
                for pre_gid in distinct_inputs:
                    pre_pop = POPULATIONS[bisect_right(boundaries, pre_gid)]
                    convergence[pre_pop][pop] += 1
    metrics = {
        "rank": rank,
        "cortical_cells": len(gids),
        "cells_by_population": by_population,
        "synaptic_contacts": contacts,
        "cells_sha256": _sha256(cells_path),
    }
    if convergence is not None:
        metrics["convergence_unique_cell_pairs"] = convergence
    _write_json(metrics_path, metrics)
    return metrics


def _write_rank_spikes(run_root: Path, rank: int, times, gids, local_populations: dict[int, str]) -> dict:
    _, spikes_path, metrics_path = _rank_paths(run_root, rank)
    if len(times) != len(gids):
        raise RuntimeError(f"Rank {rank} recorded mismatched spike times and gids")
    spikes = sorted(
        (float(time_ms), int(gid), local_populations[int(gid)])
        for time_ms, gid in zip(times, gids)
        if int(gid) in local_populations
    )
    with spikes_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(("time_ms", "gid", "population"))
        writer.writerows(spikes)
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    metrics.update(cortical_spikes=len(spikes), spikes_sha256=_sha256(spikes_path), peak_rss_mib=_peak_rss_mib())
    _write_json(metrics_path, metrics)
    return metrics


def _read_rank_spikes(path: Path):
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != ["time_ms", "gid", "population"]:
            raise ValueError(f"Invalid MPI spike shard: {path.name}")
        for row in reader:
            yield float(row["time_ms"]), int(row["gid"]), row["population"]


class _ISI:
    def __init__(self) -> None:
        self.last: float | None = None
        self.count = 0
        self.mean = 0.0
        self.m2 = 0.0

    def add(self, time_ms: float) -> None:
        if self.last is not None:
            interval = time_ms - self.last
            self.count += 1
            delta = interval - self.mean
            self.mean += delta / self.count
            self.m2 += delta * (interval - self.mean)
        self.last = time_ms

    def cv(self) -> float | None:
        if self.count < 2 or self.mean <= 0:
            return None
        return math.sqrt(max(0.0, self.m2 / self.count)) / self.mean


def _synchrony(counts: list[int], cell_count: int) -> float | None:
    if not counts or cell_count <= 0:
        return None
    normalized = [value / cell_count for value in counts]
    mean = sum(normalized) / len(normalized)
    if mean == 0:
        return None
    variance = sum((value - mean) ** 2 for value in normalized) / len(normalized)
    return variance / mean


def _correlations(counts) -> tuple[float | None, float | None, int]:
    if len(counts) < 2:
        return None, None, 0
    try:
        import numpy as np
    except ImportError:
        np = None
    if np is not None:
        array = np.asarray(counts, dtype=np.float64)
        array = array[array.std(axis=1) > 0]
        valid = len(array)
        if valid < 2:
            return None, None, valid
        correlation = np.corrcoef(array)
        upper = correlation[np.triu_indices(valid, 1)]
        return float(upper.mean()), float(np.abs(upper).mean()), valid
    if len(counts) > 256:
        raise RuntimeError("NumPy is required for large-scale exact paper correlations")
    normalized = []
    for row in counts:
        mean = sum(row) / len(row)
        variance = sum((value - mean) ** 2 for value in row) / len(row)
        if variance > 0:
            normalized.append([(value - mean) / math.sqrt(variance) for value in row])
    valid = len(normalized)
    if valid < 2:
        return None, None, valid
    signed = absolute = 0.0
    pairs = 0
    for index, left in enumerate(normalized):
        for right in normalized[index + 1:]:
            correlation = sum(a * b for a, b in zip(left, right)) / len(left)
            signed += correlation
            absolute += abs(correlation)
            pairs += 1
    return signed / pairs, absolute / pairs, valid


def _metric_string(value: float | None) -> str:
    return f"{value:.8g}" if value is not None and math.isfinite(value) else ""


def _raster(run_root: Path, raster_spikes: list[tuple[float, int, str]], gids_by_population: dict[str, list[int]], duration: float) -> None:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    fig, ax = plt.subplots(figsize=(13, 7), dpi=190)
    colors = ("#3774d9", "#56a3eb", "#dc772c", "#f2b65a", "#6c42c5", "#ad83ec", "#229872", "#79c9a9")
    offsets = {}
    offset = 0
    for pop in POPULATIONS:
        gids = gids_by_population[pop]
        offsets[pop] = {gid: offset + index for index, gid in enumerate(gids)}
        offset += len(gids)
    by_population = {pop: ([], []) for pop in POPULATIONS}
    for time_ms, gid, pop in raster_spikes:
        x, y = by_population[pop]
        x.append(time_ms)
        y.append(offsets[pop][gid])
    for pop, color in zip(POPULATIONS, colors):
        x, y = by_population[pop]
        ax.scatter(x, y, s=2, marker="|", linewidths=0.5, color=color, label=pop)
    ax.set(xlabel="Time (ms)", ylabel="Cell (grouped by population)", title="PDCM spike raster · NetPyNE MPI")
    ax.set_xlim(100, min(duration, 600))
    ax.legend(loc="upper right", ncol=4, frameon=False)
    ax.grid(axis="x", alpha=0.18)
    fig.tight_layout()
    fig.savefig(run_root / "raster.png", dpi=190)
    plt.close(fig)


def _collect_rank_cells(run_root: Path, ranks: int, expected_counts: dict[str, int]) -> tuple[dict[str, list[int]], dict[int, str], list[dict]]:
    by_population = {pop: [] for pop in POPULATIONS}
    population_by_gid: dict[int, str] = {}
    rank_metrics = []
    output = run_root / "cells.csv"
    with output.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.writer(destination)
        writer.writerow(("gid", "population", "x_um", "y_um", "z_um"))
        for rank in range(ranks):
            cells_path, spikes_path, metrics_path = _rank_paths(run_root, rank)
            if not all(path.is_file() for path in (cells_path, spikes_path, metrics_path)):
                raise RuntimeError(f"Missing MPI output shard for rank {rank}")
            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
            if metrics.get("rank") != rank or metrics.get("cells_sha256") != _sha256(cells_path) or metrics.get("spikes_sha256") != _sha256(spikes_path):
                raise RuntimeError(f"MPI output shard changed or mismatched rank {rank}")
            rank_metrics.append(metrics)
            local_count = 0
            local_pop_counts = {pop: 0 for pop in POPULATIONS}
            with cells_path.open(newline="", encoding="utf-8") as source:
                reader = csv.DictReader(source)
                if reader.fieldnames != ["gid", "population", "x_um", "y_um", "z_um"]:
                    raise ValueError(f"Invalid MPI cell shard for rank {rank}")
                for row in reader:
                    gid, pop = int(row["gid"]), row["population"]
                    if gid in population_by_gid or pop not in by_population:
                        raise RuntimeError(f"Duplicate or unexpected cortical gid {gid}")
                    population_by_gid[gid] = pop
                    by_population[pop].append(gid)
                    local_pop_counts[pop] += 1
                    writer.writerow((gid, pop, row["x_um"], row["y_um"], row["z_um"]))
                    local_count += 1
            if local_count != metrics.get("cortical_cells"):
                raise RuntimeError(f"MPI rank {rank} cell count disagrees with its shard")
            if local_pop_counts != metrics.get("cells_by_population"):
                raise RuntimeError(f"MPI rank {rank} population counts disagree with its shard")
    for pop in POPULATIONS:
        by_population[pop].sort()
        if len(by_population[pop]) != expected_counts[pop]:
            raise RuntimeError(f"Population {pop} has {len(by_population[pop])} cells, expected {expected_counts[pop]}")
    if set(population_by_gid) != set(range(sum(expected_counts.values()))):
        raise RuntimeError("Pinned PDCM cortical gids are not the expected contiguous range")
    return by_population, population_by_gid, rank_metrics


def _collect_rank_spikes(
    run_root: Path,
    ranks: int,
    spec: dict,
    gids_by_population: dict[str, list[int]],
    population_by_gid: dict[int, str],
    rank_metrics: list[dict],
) -> tuple[dict[str, float], int, bool]:
    duration = float(spec["duration_ms"])
    start = 100.0
    pop_counts = {pop: 0 for pop in POPULATIONS}
    bins = max(1, math.ceil((duration - start) / 3.0))
    binned = {pop: [0] * bins for pop in POPULATIONS}
    isi: dict[int, _ISI] = {}
    has_paper_metrics = duration >= 60_100
    paper_isi: dict[int, _ISI] = {}
    sampled = {}
    sampled_lookup = {}
    sampled_counts = {}
    paper_bins = {}
    corr_counts = {}
    if has_paper_metrics:
        try:
            import numpy as np
        except ImportError:
            np = None
        for index, pop in enumerate(POPULATIONS):
            gids = gids_by_population[pop]
            sampler = random.Random(int(spec["seed"]) + index)
            chosen = sorted(sampler.sample(gids, min(1000, len(gids))))
            sampled[pop] = chosen
            sampled_lookup.update({gid: (pop, row) for row, gid in enumerate(chosen)})
            sampled_counts[pop] = 0
            paper_bins[pop] = [0] * 20_000
            corr_counts[pop] = np.zeros((len(chosen), 200), dtype=np.int32) if np is not None else [[0] * 200 for _ in chosen]
    raster_spikes = []
    last_key = (-math.inf, -1)
    count = 0
    iterators = [_read_rank_spikes(_rank_paths(run_root, rank)[1]) for rank in range(ranks)]
    with (run_root / "spikes.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(("time_ms", "gid", "population"))
        for time_ms, gid, pop in heapq.merge(*iterators):
            if not math.isfinite(time_ms) or not 0 <= time_ms <= duration + 1e-6:
                raise RuntimeError("Recorded cortical spike has an invalid time")
            if population_by_gid.get(gid) != pop:
                raise RuntimeError(f"Recorded cortical spike references an unknown or mismatched gid {gid}")
            key = (time_ms, gid)
            if key < last_key:
                raise RuntimeError("MPI spike shards were not time ordered")
            last_key = key
            writer.writerow((time_ms, gid, pop))
            count += 1
            if 100 <= time_ms < min(duration, 600):
                raster_spikes.append((time_ms, gid, pop))
            if not 100 <= time_ms < duration:
                continue
            pop_counts[pop] += 1
            binned[pop][min(bins - 1, int((time_ms - 100) / 3))] += 1
            state = isi.setdefault(gid, _ISI())
            state.add(time_ms)
            if has_paper_metrics and gid in sampled_lookup and time_ms < 60_100:
                paper_isi.setdefault(gid, _ISI()).add(time_ms)
                sampled_pop, row = sampled_lookup[gid]
                sampled_counts[sampled_pop] += 1
                paper_bins[sampled_pop][int((time_ms - 100) / 3)] += 1
                if time_ms < 5100:
                    corr_counts[sampled_pop][row][int((time_ms - 100) / 25)] += 1
    if count != sum(int(item["cortical_spikes"]) for item in rank_metrics):
        raise RuntimeError("MPI spike total does not match rank shard counts")
    if not count:
        raise RuntimeError("PDCM simulation recorded no cortical spikes")
    rates = {}
    with (run_root / "population_statistics.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(("population", "cell_count", "mean_rate_hz", "mean_cv_isi", "synchrony_3ms", "analysis_start_ms", "analysis_end_ms"))
        for pop in POPULATIONS:
            gids = gids_by_population[pop]
            rate = pop_counts[pop] / (len(gids) * ((duration - 100) / 1000))
            cv_values = [value for gid in gids if (value := isi.get(gid, _ISI()).cv()) is not None]
            synchronization = _synchrony(binned[pop], len(gids))
            rates[pop] = rate
            writer.writerow((pop, len(gids), _metric_string(rate), _metric_string(sum(cv_values) / len(cv_values) if cv_values else None), _metric_string(synchronization), 100, duration))
    if has_paper_metrics:
        _write_json(run_root / "sampling_manifest.json", {
            "schema_version": 1,
            "method": "uniform without replacement per population",
            "maximum_cells_per_population": 1000,
            "seed": int(spec["seed"]),
            "cell_ids": sampled,
            "note": "Deterministic Digifly sample; the paper's exact sampled IDs were not published.",
        })
        with (run_root / "paper_metrics.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow((
                "population", "sampled_cells", "mean_rate_hz_60s", "mean_cv_isi_60s",
                "synchrony_3ms_5s", "synchrony_3ms_60s",
                "mean_signed_cross_correlation_25ms_5s",
                "mean_absolute_cross_correlation_25ms_5s", "correlation_defined_cells",
            ))
            for pop in POPULATIONS:
                gids = sampled[pop]
                cv_values = [value for gid in gids if (value := paper_isi.get(gid, _ISI()).cv()) is not None]
                signed, absolute, valid = _correlations(corr_counts[pop])
                writer.writerow((
                    pop, len(gids), _metric_string(sampled_counts[pop] / (len(gids) * 60)),
                    _metric_string(sum(cv_values) / len(cv_values) if cv_values else None),
                    _metric_string(_synchrony(paper_bins[pop][:int(5000 / 3)], len(gids))),
                    _metric_string(_synchrony(paper_bins[pop], len(gids))),
                    _metric_string(signed), _metric_string(absolute), valid,
                ))
    _raster(run_root, raster_spikes, gids_by_population, duration)
    return rates, count, has_paper_metrics


def _export_convergence(run_root: Path, rank_metrics: list[dict], expected_counts: dict[str, int]) -> dict:
    """Reproduce the pinned source's cortical plotConn convergence matrix.

    The authors specify ``synOrConn='conn'`` and only the eight cortical
    populations. A cell pair with duplicate synaptic contacts counts once.
    Rank-local 8x8 integer sums are sufficient; full edge lists never leave
    the owning rank.
    """
    totals = {pre: {post: 0 for post in POPULATIONS} for pre in POPULATIONS}
    for metrics in rank_metrics:
        matrix = metrics.get("convergence_unique_cell_pairs")
        if not isinstance(matrix, dict) or set(matrix) != set(POPULATIONS):
            raise RuntimeError("A rank is missing its Figure 9C convergence aggregate")
        local_total = 0
        for pre in POPULATIONS:
            row = matrix[pre]
            if not isinstance(row, dict) or set(row) != set(POPULATIONS):
                raise RuntimeError("A rank has an incomplete Figure 9C convergence matrix")
            for post in POPULATIONS:
                value = row[post]
                if type(value) is not int or not 0 <= value <= expected_counts[pre] * metrics["cells_by_population"][post]:
                    raise RuntimeError("A rank has an invalid Figure 9C convergence count")
                totals[pre][post] += value
                local_total += value
        if local_total > metrics["synaptic_contacts"]["recurrent"]:
            raise RuntimeError("Figure 9C unique-input count exceeds recurrent contacts")
    output = run_root / "figure9c_convergence.csv"
    with output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(("pre_population", "post_population", "post_cell_count", "unique_presynaptic_cell_pairs", "mean_convergence_per_post_cell"))
        for pre in POPULATIONS:
            for post in POPULATIONS:
                count = totals[pre][post]
                writer.writerow((pre, post, expected_counts[post], count, f"{count / expected_counts[post]:.12g}"))
    return {
        "path": output.name,
        "sha256": _sha256(output),
        "model_commit": MODEL_COMMIT,
        "method": "NetPyNE plotConn(groupBy='pop', feature='convergence', synOrConn='conn')",
        "include_pre": list(POPULATIONS),
        "include_post": list(POPULATIONS),
        "external_netstims_included": False,
        "unique_cell_pairs": sum(value for row in totals.values() for value in row.values()),
        "note": "Aggregate Figure 9C inputs only; no figure or full-paper match is claimed.",
    }


def _finish(run_root: Path, spec: dict, provenance: dict, ranks: int, expected_counts: dict[str, int], mechanism: Path, netpyne_version: str, neuron_version: str, seed_streams: dict[str, int]) -> Path:
    by_population, population_by_gid, rank_metrics = _collect_rank_cells(run_root, ranks, expected_counts)
    rates, spikes, has_paper_metrics = _collect_rank_spikes(run_root, ranks, spec, by_population, population_by_gid, rank_metrics)
    contacts = {key: sum(item["synaptic_contacts"][key] for item in rank_metrics) for key in ("total", "recurrent", "external", "unclassified")}
    convergence_export = _export_convergence(run_root, rank_metrics, expected_counts) if spec.get("export_convergence", False) else None
    acceptance = {
        "all_expected_cortical_cells_present": True,
        "all_rank_shards_present_and_intact": True,
        "all_recorded_spikes_match_cortical_gids": True,
        "rank_spike_counts_match_merged_total": True,
        "all_population_rates_finite_nonnegative": all(math.isfinite(rate) and rate >= 0 for rate in rates.values()),
        "at_least_one_cortical_spike": spikes > 0,
        "paper_timed_metrics_available": has_paper_metrics,
    }
    if convergence_export is not None:
        acceptance["convergence_aggregate_complete"] = True
    if not all(value for key, value in acceptance.items() if key != "paper_timed_metrics_available"):
        raise RuntimeError("MPI PDCM numerical acceptance failed")
    _write_json(run_root / "mpi_execution.json", {
        "schema_version": 1,
        "launch": "OpenMPI mpiexec -> NEURON nrniv -python -mpi; one externally compiled mechanism",
        "rank_count": ranks,
        "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
        "rank_metrics": rank_metrics,
        "synaptic_contacts": contacts,
        "convergence_export": convergence_export,
        "mechanism_library": str(mechanism.relative_to(run_root)),
        "mechanism_sha256": _sha256(mechanism),
        "worker_sha256": _sha256(Path(__file__).resolve()),
        "network_seed": spec.get("network_seed"),
        "seed_streams": seed_streams,
        "numeric_acceptance": acceptance,
        "aggregation": "Per-rank local spikes merged by time on rank 0; no NetPyNE all-cell gather",
        "scientific_caveat": "Recurrent versus external contact classification assumes the pinned model's contiguous cortical gids. Reproduction still requires matched scale, duration, drive, seeds, and independent figure comparison.",
    })
    artifacts = [
        {"kind": "image", "path": "raster.png", "label": "Population spike raster"},
        {"kind": "table", "path": "population_statistics.csv", "label": "Population statistics"},
        {"kind": "table", "path": "spikes.csv", "label": "Cortical spike events"},
        {"kind": "table", "path": "cells.csv", "label": "Cortical cell positions and IDs"},
        {"kind": "json", "path": "source_manifest.json", "label": "Pinned model source"},
        {"kind": "json", "path": "mpi_execution.json", "label": "MPI execution and numerical acceptance"},
    ]
    if has_paper_metrics:
        artifacts.extend((
            {"kind": "table", "path": "paper_metrics.csv", "label": "Paper-timed population metrics"},
            {"kind": "json", "path": "sampling_manifest.json", "label": "Paper-metric sample IDs"},
        ))
    if convergence_export is not None:
        artifacts.append({"kind": "table", "path": convergence_export["path"], "label": "Figure 9C cortical convergence inputs (not a figure)"})
    for log_name, label in (
        ("mpi.log", "NetPyNE MPI execution log"),
        ("nrnivmodl.log", "NEURON mechanism compilation log"),
        ("worker.log", "NetPyNE worker log"),
    ):
        if (run_root / log_name).is_file():
            artifacts.append({"kind": "file", "path": log_name, "label": label})
    summary = {
        "schema_version": 1,
        "workflow": "pdcm_replication_v1",
        "status": "completed",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "title": "Romaro et al. 2021 · PDCM NetPyNE MPI replication",
        "engine": "netpyne",
        "metadata": {
            "scale": spec["scale"], "drive": spec["drive"], "duration_ms": spec["duration_ms"], "seed": spec["seed"],
            "network_seed": spec.get("network_seed"), "seed_streams": seed_streams,
            "seed_derivation": "digifly-pdcm-network-v1:sha256-domain-xor31" if spec.get("network_seed") is not None else "authors-and-netpyne-defaults",
            "cortical_cells": len(population_by_gid), "cortical_spikes": spikes,
            "mean_rates_hz": rates, "mpi_ranks": ranks, "synaptic_contacts": contacts,
            "netpyne_version": netpyne_version, "neuron_version": neuron_version,
            "model_commit": provenance["model_commit"],
            "paper_timed_metrics_available": has_paper_metrics,
            "figure9c_convergence_available": convergence_export is not None,
            "scientific_status": "MPI model run complete; figures require matched-condition comparison with the paper",
        },
        "artifacts": artifacts,
    }
    output = run_root / "summary.json"
    _write_json(output, summary)
    return output


def execute(request_path: Path, expected_ranks: int) -> Path | None:
    # Engineering-only opt-in; unchanged frozen production jobs do not use it.
    compact_flag = os.environ.get("DIGIFLY_PDCM_COMPACT_CONNECTIONS")
    if compact_flag is not None and compact_flag != "1":
        raise ValueError("DIGIFLY_PDCM_COMPACT_CONNECTIONS must be absent or exactly '1'")
    run_root, model, spec, provenance = _prepared_bundle(request_path)
    mechanism = _compiled_library(model)
    if (run_root / "summary.json").exists():
        raise FileExistsError("This PDCM run already has a completed summary")
    os.chdir(model)
    sys.path.insert(0, str(model))
    import neuron
    from neuron import h

    if not hasattr(h, "IntFire_PD"):
        neuron.load_mechanisms(str(model))
    if not hasattr(h, "IntFire_PD"):
        raise RuntimeError("Compiled IntFire_PD mechanism did not load in this NEURON rank")
    from cfg import cfg

    cfg.ScaleFactor = float(spec["scale"])
    cfg.duration = float(spec["duration_ms"])
    cfg.DC = spec["drive"] == "balanced_dc"
    cfg.Balanced = spec["drive"] != "unbalanced_poisson"
    cfg.TH = False
    seed_streams = _apply_seed_streams(cfg, spec)
    cfg.savePickle = False
    cfg.saveJson = False
    cfg.saveCellSecs = False
    cfg.saveCellConns = False
    cfg.createPyStruct = True
    cfg.gatherOnlySimData = True
    cfg.saveFolder = str(run_root / "netpyne")
    from netParams import netParams
    # The pinned source adjusts cfg.analysis while netParams.py is imported.
    # Disable plotting only after those source-side edits are complete.
    cfg.analysis.clear()
    from netpyne import sim
    import netpyne

    sim.initialize(simConfig=cfg, netParams=netParams)
    rank, ranks = int(sim.rank), int(sim.nhosts)
    if ranks != expected_ranks:
        raise RuntimeError(f"NEURON MPI initialized {ranks} ranks, expected {expected_ranks}")
    if rank == 0:
        shards = run_root / "mpi_shards"
        if shards.exists():
            raise FileExistsError("An MPI shard folder already exists; use a new prepared run")
        shards.mkdir()
        _write_json(run_root / "run_manifest.json", {
            "schema_version": 1, "workflow": "pdcm_replication_v1", "state": "running",
            "name": spec.get("name", "PDCM MPI run"), "mpi_ranks": ranks,
        })
    sim.pc.barrier()
    sim.net.createPops()
    sim.net.createCells()
    rand = h.Random()
    for cell in sim.net.cells:
        if cell.tags.get("cellModel") == "IntFire_PD":
            rand.Random123(cell.gid, cfg.seeds["m"])
            cell.hPointp.m = rand.normal(-58, 10)
    sim.net.addStims()
    compact_audit = None
    if compact_flag == "1":
        if __package__:
            from .pdcm_compact_connections import compact_connections, realized_connection_fingerprint
        else:
            from pdcm_compact_connections import compact_connections, realized_connection_fingerprint
        from netpyne.cell.pointCell import PointCell

        with compact_connections(cfg, PointCell, netParams, netpyne_version=netpyne.__version__) as compact_audit:
            sim.net.connectCells()
    else:
        sim.net.connectCells()
    expected_counts = {pop: int(netParams.popParams[pop]["numCells"]) for pop in POPULATIONS}
    _write_rank_cells(
        run_root, rank, sim.net.cells, sum(expected_counts.values()),
        convergence_counts=expected_counts if spec.get("export_convergence", False) else None,
    )
    if compact_audit is not None:
        evidence = compact_audit.evidence()
        realized = realized_connection_fingerprint(
            sim.net.cells, sum(expected_counts.values()),
        )
        if evidence["contacts_created"] != realized["synaptic_contacts"]["total"]:
            raise RuntimeError("Compact connection creation count disagrees with live native handles")
        evidence.update(rank=rank, rank_count=ranks, realized_connections=realized)
        _write_json(run_root / "mpi_shards" / f"compact_rank_{rank:06d}.json", evidence)
    sim.pc.barrier()
    sim.setupRecording()
    sim.runSim()
    local_populations = {
        int(cell.gid): cell.tags["pop"] for cell in sim.net.cells
        if cell.tags.get("pop") in POPULATIONS and cell.tags.get("cellModel") == "IntFire_PD"
    }
    _write_rank_spikes(run_root, rank, sim.simData.get("spkt", ()), sim.simData.get("spkid", ()), local_populations)
    sim.pc.barrier()
    output = None
    if rank == 0:
        output = _finish(run_root, spec, provenance, ranks, expected_counts, mechanism, netpyne.__version__, neuron.__version__, seed_streams)
        manifest = json.loads((run_root / "run_manifest.json").read_text(encoding="utf-8"))
        manifest.update(state="completed", completed_at=datetime.now(timezone.utc).isoformat())
        _write_json(run_root / "run_manifest.json", manifest)
    # Keep workers alive until rank zero has finished the disk-backed merge.
    sim.pc.barrier()
    return output


def main() -> int:
    if "--validate-only" in sys.argv:
        parser = argparse.ArgumentParser(description="Validate a portable PDCM bundle without NEURON or writes")
        parser.add_argument("--validate-only", action="store_true", required=True)
        parser.add_argument("--request", required=True, type=Path)
        args = parser.parse_args()
        run_root, _model, _spec, _provenance = _prepared_bundle(args.request)
        print(f"Validated pinned PDCM source in {run_root}", flush=True)
        return 0
    request = os.environ.get("DIGIFLY_PDCM_REQUEST")
    expected = os.environ.get("DIGIFLY_PDCM_EXPECTED_RANKS")
    if not request or not expected:
        raise SystemExit("Set DIGIFLY_PDCM_REQUEST and DIGIFLY_PDCM_EXPECTED_RANKS before NEURON MPI launch")
    count = int(expected)
    if count < 1:
        raise SystemExit("DIGIFLY_PDCM_EXPECTED_RANKS must be positive")
    result = execute(Path(request), count)
    if result is not None:
        print(result, flush=True)
    # nrniv hosts this Python interpreter; all ranks must leave through NEURON
    # so it can finalize MPI instead of having Python terminate the process.
    from neuron import h
    h.quit()
    return 0


if __name__ == "__main__":
    main()
