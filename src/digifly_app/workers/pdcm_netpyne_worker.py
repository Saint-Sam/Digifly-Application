"""Standalone NetPyNE worker for the pinned Romaro et al. PDCM model."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import random
import runpy
import subprocess
import sys


POPULATIONS = ("L2e", "L2i", "L4e", "L4i", "L5e", "L5i", "L6e", "L6i")
MODEL_COMMIT = "a6e39c19a0d85e0e907acac33791363591a88f45"
SOURCE_SHA256 = {
    "cfg.py": "1b7ad4912a7645ed35b49cb649adb66bd511234f4f50bd3965d5484d9a34d1f2",
    "init.py": "0fd97727ffdd0ba4049ff92437ee805b2be9f7aed837cce70d4f2604124e38e9",
    "netParams.py": "361356e4beb64e59f4eb4195348ebe80d12a2477733cc2a972df297d96299a1a",
    "IntFire_PD.mod": "9167520a8179c3b84c0a4d29d392d09d770063ffef5bbd8bbab5a2ee50f44940",
}


def _digest(path: Path) -> str:
    hash_value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hash_value.update(block)
    return hash_value.hexdigest()


def _write_json(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _apply_seed_streams(cfg, spec: dict) -> dict[str, int]:
    """Keep v1 defaults unless an independent network realization is requested.

    This small routine is mirrored in the standalone MPI worker. Each domain's
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


def _validated_request(request_path: Path) -> tuple[Path, dict, dict]:
    """Resolve a copied bundle without trusting paths or unverified model files."""
    request_path = request_path.expanduser().resolve()
    run_root = request_path.parent
    request = json.loads(request_path.read_text(encoding="utf-8"))
    if request.get("schema_version") != 1 or request.get("workflow") != "pdcm_replication_v1":
        raise ValueError("Unsupported PDCM worker request")
    spec = request.get("spec")
    if not isinstance(spec, dict):
        raise ValueError("PDCM request has no run specification")
    network_seed = spec.get("network_seed")
    if network_seed is not None and (type(network_seed) is not int or not 0 <= network_seed < 2**31):
        raise ValueError("PDCM network seed must be a nonnegative 31-bit integer")
    requested_root = request.get("run_root")
    if requested_root != "." and not (
        isinstance(requested_root, str)
        and Path(requested_root).is_absolute()
        and Path(requested_root).resolve() == run_root
    ):
        raise ValueError("PDCM worker request is outside its run folder")
    model = run_root / "model"
    provenance_path = run_root / "source_manifest.json"
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    original_hashes = provenance.get("original_sha256")
    if (
        provenance.get("model_commit") != MODEL_COMMIT
        or not isinstance(original_hashes, dict)
        or any(original_hashes.get(name) != digest for name, digest in SOURCE_SHA256.items())
    ):
        raise ValueError("PDCM source identity differs from pinned authors' revision")
    executed_hashes = provenance.get("executed_sha256")
    if not isinstance(executed_hashes, dict) or set(executed_hashes) != set((*SOURCE_SHA256, "LICENSE")):
        raise ValueError("PDCM source manifest has an unexpected file list")
    if not model.is_dir() or model.is_symlink():
        raise ValueError("PDCM model must be a regular folder inside its bundle")
    for name, expected in executed_hashes.items():
        path = model / name
        if not path.is_file() or path.is_symlink() or _digest(path) != expected:
            raise ValueError(f"Prepared PDCM model file changed: {name}")
    bundle_path = run_root / "bundle_manifest.json"
    if bundle_path.exists():
        if not bundle_path.is_file() or bundle_path.is_symlink():
            raise ValueError("PDCM bundle manifest must be a regular file")
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        files = bundle.get("files_sha256")
        expected_files = {
            "worker_request.json", "source_manifest.json",
            *(f"model/{name}" for name in (*SOURCE_SHA256, "LICENSE")),
        }
        if (
            bundle.get("schema_version") != 1
            or bundle.get("workflow") != "pdcm_replication_v1"
            or bundle.get("model_commit") != MODEL_COMMIT
            or not isinstance(files, dict)
            or set(files) != expected_files
        ):
            raise ValueError("PDCM bundle manifest has an unexpected file list")
        for name, expected in files.items():
            path = run_root / name
            if not path.is_file() or path.is_symlink() or _digest(path) != expected:
                raise ValueError(f"PDCM bundle transfer check failed: {name}")
    elif requested_root == ".":
        raise ValueError("Portable PDCM run is missing its bundle manifest")
    return run_root, request, provenance


def _compile_model(model: Path) -> None:
    compiler = Path(sys.executable).parent / ("nrnivmodl.exe" if os.name == "nt" else "nrnivmodl")
    if not compiler.is_file():
        raise RuntimeError("The selected NetPyNE/NEURON environment lacks nrnivmodl")
    result = subprocess.run(
        [str(compiler), "IntFire_PD.mod"],
        cwd=model,
        capture_output=True,
        text=True,
        check=False,
    )
    print(result.stdout, flush=True)
    if result.returncode:
        raise RuntimeError(f"The paper's IntFire_PD.mod failed to compile: {result.stderr[-4000:]}")
    if not any(model.glob("*/libnrnmech.*")):
        raise RuntimeError("NEURON compilation produced no mechanism library")


def _statistics(
    run_root: Path,
    *,
    gids_by_population: dict[str, list[int]],
    spikes: list[tuple[float, int, str]],
    duration_ms: float,
) -> dict[str, float]:
    start = 100.0
    elapsed_s = (duration_ms - start) / 1000.0
    bin_ms = 3.0
    bins = max(1, math.ceil((duration_ms - start) / bin_ms))
    times_by_gid: dict[int, list[float]] = {}
    counts_by_population: dict[str, list[int]] = {
        pop: [0] * bins for pop in POPULATIONS
    }
    for time_ms, gid, population in spikes:
        if time_ms < start or time_ms >= duration_ms:
            continue
        times_by_gid.setdefault(gid, []).append(time_ms)
        index = min(bins - 1, int((time_ms - start) / bin_ms))
        counts_by_population[population][index] += 1
    rates: dict[str, float] = {}
    with (run_root / "population_statistics.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(("population", "cell_count", "mean_rate_hz", "mean_cv_isi", "synchrony_3ms", "analysis_start_ms", "analysis_end_ms"))
        for pop in POPULATIONS:
            gids = gids_by_population[pop]
            rate = sum(len(times_by_gid.get(gid, ())) for gid in gids) / (len(gids) * elapsed_s)
            cv_values = []
            for gid in gids:
                times = times_by_gid.get(gid, ())
                if len(times) < 3:
                    continue
                intervals = [b - a for a, b in zip(times, times[1:])]
                mean_isi = sum(intervals) / len(intervals)
                if mean_isi > 0:
                    variance = sum((interval - mean_isi) ** 2 for interval in intervals) / len(intervals)
                    cv_values.append(math.sqrt(variance) / mean_isi)
            counts = [value / len(gids) for value in counts_by_population[pop]]
            mean_count = sum(counts) / len(counts)
            variance_count = sum((value - mean_count) ** 2 for value in counts) / len(counts)
            synchrony = variance_count / mean_count if mean_count else float("nan")
            writer.writerow((pop, len(gids), f"{rate:.8g}", f"{sum(cv_values)/len(cv_values):.8g}" if cv_values else "", f"{synchrony:.8g}" if math.isfinite(synchrony) else "", start, duration_ms))
            rates[pop] = rate
    return rates


def _paper_synchrony(
    gids: list[int],
    times_by_gid: dict[int, list[float]],
    *,
    start_ms: float,
    end_ms: float,
) -> float:
    bin_ms = 3.0
    bins = int((end_ms - start_ms) / bin_ms)
    counts = [0] * bins
    for gid in gids:
        for time_ms in times_by_gid.get(gid, ()):
            if start_ms <= time_ms < start_ms + bins * bin_ms:
                counts[int((time_ms - start_ms) / bin_ms)] += 1
    normalized = [count / len(gids) for count in counts]
    mean = sum(normalized) / bins
    if mean == 0:
        return float("nan")
    return sum((value - mean) ** 2 for value in normalized) / bins / mean


def _paper_cross_correlation(
    gids: list[int],
    times_by_gid: dict[int, list[float]],
    *,
    start_ms: float,
    end_ms: float,
) -> tuple[float, int]:
    """Average all pairwise Pearson correlations without an N-by-N matrix."""
    bin_ms = 25.0
    bins = int((end_ms - start_ms) / bin_ms)
    standardized_sum = [0.0] * bins
    valid = 0
    for gid in gids:
        counts = [0] * bins
        for time_ms in times_by_gid.get(gid, ()):
            if start_ms <= time_ms < start_ms + bins * bin_ms:
                counts[int((time_ms - start_ms) / bin_ms)] += 1
        mean = sum(counts) / bins
        variance = sum((count - mean) ** 2 for count in counts) / bins
        if variance == 0:
            continue
        scale = 1 / math.sqrt(variance)
        for index, count in enumerate(counts):
            standardized_sum[index] += (count - mean) * scale
        valid += 1
    if valid < 2:
        return float("nan"), valid
    # Each standardized cell contributes one to the mean squared norm. The
    # remaining terms are exactly the sum over distinct cell pairs.
    pairwise_mean = (
        sum(value * value for value in standardized_sum) / bins - valid
    ) / (valid * (valid - 1))
    return pairwise_mean, valid


def _paper_absolute_cross_correlation(
    gids: list[int],
    times_by_gid: dict[int, list[float]],
    *,
    start_ms: float,
    end_ms: float,
) -> float:
    """Report correlation magnitudes separately from the signed equation."""
    bins = int((end_ms - start_ms) / 25.0)
    try:
        import numpy as np
    except ImportError:
        np = None
    if np is not None:
        counts = np.zeros((len(gids), bins), dtype=np.float64)
        for row, gid in enumerate(gids):
            for time_ms in times_by_gid.get(gid, ()):
                if start_ms <= time_ms < start_ms + bins * 25:
                    counts[row, int((time_ms - start_ms) / 25)] += 1
        counts = counts[counts.std(axis=1) > 0]
        if len(counts) < 2:
            return float("nan")
        correlations = np.corrcoef(counts)
        return float(np.abs(correlations[np.triu_indices(len(counts), 1)]).mean())
    if len(gids) > 256:
        raise RuntimeError(
            "NumPy is required to compute exact all-pairs correlation magnitudes "
            "for more than 256 cells"
        )
    standardized = []
    for gid in gids:
        counts = [0] * bins
        for time_ms in times_by_gid.get(gid, ()):
            if start_ms <= time_ms < start_ms + bins * 25:
                counts[int((time_ms - start_ms) / 25)] += 1
        mean = sum(counts) / bins
        variance = sum((count - mean) ** 2 for count in counts) / bins
        if variance:
            standardized.append([(count - mean) / math.sqrt(variance) for count in counts])
    if len(standardized) < 2:
        return float("nan")
    total = pair_count = 0
    for index, left in enumerate(standardized):
        for right in standardized[index + 1:]:
            total += abs(sum(a * b for a, b in zip(left, right)) / bins)
            pair_count += 1
    return total / pair_count


def _paper_statistics(
    run_root: Path,
    *,
    gids_by_population: dict[str, list[int]],
    spikes: list[tuple[float, int, str]],
    duration_ms: float,
    sample_seed: int,
) -> bool:
    """Write the paper's timing/binning metrics only for a full 60 s record."""
    if duration_ms < 60_100:
        return False
    start_ms, end_ms = 100.0, 60_100.0
    times_by_gid: dict[int, list[float]] = {}
    for time_ms, gid, _ in spikes:
        if start_ms <= time_ms < end_ms:
            times_by_gid.setdefault(gid, []).append(time_ms)
    sampled_by_population: dict[str, list[int]] = {}
    for index, pop in enumerate(POPULATIONS):
        gids = sorted(gids_by_population[pop])
        count = min(1000, len(gids))
        sampler = random.Random(sample_seed + index)
        sampled_by_population[pop] = sorted(sampler.sample(gids, count))
    _write_json(
        run_root / "sampling_manifest.json",
        {
            "schema_version": 1,
            "method": "uniform without replacement per population",
            "maximum_cells_per_population": 1000,
            "seed": sample_seed,
            "cell_ids": sampled_by_population,
            "note": "Deterministic Digifly sample; the paper's exact sampled IDs were not published.",
        },
    )
    with (run_root / "paper_metrics.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow((
            "population", "sampled_cells", "mean_rate_hz_60s", "mean_cv_isi_60s",
            "synchrony_3ms_5s", "synchrony_3ms_60s",
            "mean_signed_cross_correlation_25ms_5s",
            "mean_absolute_cross_correlation_25ms_5s", "correlation_defined_cells",
        ))
        for pop in POPULATIONS:
            gids = sampled_by_population[pop]
            rate = sum(len(times_by_gid.get(gid, ())) for gid in gids) / (len(gids) * 60)
            cv_values = []
            for gid in gids:
                times = times_by_gid.get(gid, ())
                if len(times) < 3:
                    continue
                intervals = [right - left for left, right in zip(times, times[1:])]
                mean_isi = sum(intervals) / len(intervals)
                if mean_isi > 0:
                    variance = sum((interval - mean_isi) ** 2 for interval in intervals) / len(intervals)
                    cv_values.append(math.sqrt(variance) / mean_isi)
            sync_5s = _paper_synchrony(gids, times_by_gid, start_ms=100, end_ms=5100)
            sync_60s = _paper_synchrony(gids, times_by_gid, start_ms=100, end_ms=60_100)
            correlation, valid = _paper_cross_correlation(
                gids, times_by_gid, start_ms=100, end_ms=5100
            )
            absolute_correlation = _paper_absolute_cross_correlation(
                gids, times_by_gid, start_ms=100, end_ms=5100
            )
            writer.writerow((
                pop, len(gids), f"{rate:.8g}",
                f"{sum(cv_values) / len(cv_values):.8g}" if cv_values else "",
                f"{sync_5s:.8g}" if math.isfinite(sync_5s) else "",
                f"{sync_60s:.8g}" if math.isfinite(sync_60s) else "",
                f"{correlation:.8g}" if math.isfinite(correlation) else "",
                f"{absolute_correlation:.8g}" if math.isfinite(absolute_correlation) else "",
                valid,
            ))
    return True


def _raster(run_root: Path, spikes: list[tuple[float, int, str]], gids_by_population: dict[str, list[int]], duration_ms: float) -> None:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    fig, ax = plt.subplots(figsize=(13, 7), dpi=190)
    colors = ("#3774d9", "#56a3eb", "#dc772c", "#f2b65a", "#6c42c5", "#ad83ec", "#229872", "#79c9a9")
    y_offsets = {}
    offset = 0
    for pop in POPULATIONS:
        gids = gids_by_population[pop]
        y_offsets[pop] = {gid: offset + index for index, gid in enumerate(gids)}
        offset += len(gids)
    for pop, color in zip(POPULATIONS, colors):
        x = [time for time, gid, source_pop in spikes if source_pop == pop and 100 <= time < min(duration_ms, 600)]
        y = [y_offsets[pop][gid] for time, gid, source_pop in spikes if source_pop == pop and 100 <= time < min(duration_ms, 600)]
        ax.scatter(x, y, s=2, marker="|", linewidths=0.5, color=color, label=pop)
    ax.set(xlabel="Time (ms)", ylabel="Cell (grouped by population)", title="PDCM spike raster · NetPyNE")
    ax.set_xlim(100, min(duration_ms, 600))
    ax.legend(loc="upper right", ncol=4, frameon=False)
    ax.grid(axis="x", alpha=0.18)
    fig.tight_layout()
    fig.savefig(run_root / "raster.png", dpi=190)
    plt.close(fig)


def execute(request_path: Path) -> Path:
    run_root, request, provenance = _validated_request(request_path)
    spec = request["spec"]
    if spec.get("export_convergence", False):
        raise ValueError("Convergence export is available only in the pinned MPI PDCM worker")
    model = run_root / "model"
    _compile_model(model)
    os.chdir(model)
    sys.path.insert(0, str(model))
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
    cfg.gatherOnlySimData = True
    cfg.saveFolder = str(run_root / "netpyne")
    from netParams import netParams  # noqa: F401 - source config must be built after overrides

    cfg.analysis.clear()
    runpy.run_path(str(model / "init.py"), run_name="__main__")
    from netpyne import sim

    gids_by_population = {pop: [] for pop in POPULATIONS}
    population_by_gid = {}
    for cell in sim.net.cells:
        pop = cell.tags.get("pop")
        if pop in gids_by_population and cell.tags.get("cellModel") == "IntFire_PD":
            gid = int(cell.gid)
            gids_by_population[pop].append(gid)
            population_by_gid[gid] = pop
    if any(not gids for gids in gids_by_population.values()):
        raise RuntimeError("The PDCM run did not instantiate all eight cortical populations")
    spikes = sorted(
        (float(time), int(gid), population_by_gid[int(gid)])
        for time, gid in zip(sim.allSimData.get("spkt", ()), sim.allSimData.get("spkid", ()))
        if int(gid) in population_by_gid
    )
    with (run_root / "spikes.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(("time_ms", "gid", "population"))
        writer.writerows(spikes)
    with (run_root / "cells.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(("gid", "population", "x_um", "y_um", "z_um"))
        for cell in sim.net.cells:
            if int(cell.gid) in population_by_gid:
                writer.writerow((
                    int(cell.gid), population_by_gid[int(cell.gid)],
                    cell.tags.get("x", ""), cell.tags.get("y", ""), cell.tags.get("z", ""),
                ))
    rates = _statistics(run_root, gids_by_population=gids_by_population, spikes=spikes, duration_ms=float(spec["duration_ms"]))
    has_paper_metrics = _paper_statistics(
        run_root,
        gids_by_population=gids_by_population,
        spikes=spikes,
        duration_ms=float(spec["duration_ms"]),
        sample_seed=int(spec["seed"]),
    )
    _raster(run_root, spikes, gids_by_population, float(spec["duration_ms"]))
    import netpyne
    import neuron

    summary = {
        "schema_version": 1,
        "workflow": "pdcm_replication_v1",
        "status": "completed",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "title": "Romaro et al. 2021 · PDCM NetPyNE replication",
        "engine": "netpyne",
        "metadata": {
            "scale": spec["scale"],
            "drive": spec["drive"],
            "duration_ms": spec["duration_ms"],
            "seed": spec["seed"],
            "network_seed": spec.get("network_seed"),
            "seed_streams": seed_streams,
            "seed_derivation": "digifly-pdcm-network-v1:sha256-domain-xor31" if spec.get("network_seed") is not None else "authors-and-netpyne-defaults",
            "cortical_cells": sum(map(len, gids_by_population.values())),
            "cortical_spikes": len(spikes),
            "mean_rates_hz": rates,
            "netpyne_version": netpyne.__version__,
            "neuron_version": neuron.__version__,
            "model_commit": provenance["model_commit"],
            "scientific_status": "compatibility pilot; compare with paper only at matched scale, duration, drive, and sampling",
            "paper_timed_metrics_available": has_paper_metrics,
        },
        "artifacts": [
            {"kind": "image", "path": "raster.png", "label": "Population spike raster"},
            {"kind": "table", "path": "population_statistics.csv", "label": "Population statistics"},
            {"kind": "table", "path": "spikes.csv", "label": "Cortical spike events"},
            {"kind": "table", "path": "cells.csv", "label": "Cortical cell positions and IDs"},
            *([
                {"kind": "table", "path": "paper_metrics.csv", "label": "Paper-timed population metrics"},
                {"kind": "json", "path": "sampling_manifest.json", "label": "Paper-metric sample IDs"},
            ] if has_paper_metrics else []),
            {"kind": "json", "path": "source_manifest.json", "label": "Pinned source and compatibility patch"},
            {"kind": "file", "path": "worker.log", "label": "NetPyNE worker log"},
        ],
    }
    output = run_root / "summary.json"
    _write_json(output, summary)
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True, type=Path)
    args = parser.parse_args()
    try:
        print(execute(args.request), flush=True)
        return 0
    except Exception as exc:
        print(f"PDCM NetPyNE worker failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
