"""Analyze PDCM sampling conditions in a versioned sidecar, without editing a run.

This command intentionally does not reproduce unpublished sampled cell IDs. It
records its deterministic selection and reports both interpretations of the
paper's 3 ms count-variance synchrony statistic.
"""

from __future__ import annotations

import argparse
from array import array
import csv
import hashlib
import json
import math
from pathlib import Path
import random
from typing import BinaryIO, Iterator

from digifly_app.workers.pdcm_netpyne_worker import MODEL_COMMIT, POPULATIONS, SOURCE_SHA256


PAPER_URL = "https://pmc.ncbi.nlm.nih.gov/articles/PMC8382011/"
ANALYSIS_VERSION = 1
PROFILES = (
    "table3_fixed_percentage",
    "table3_1000",
    "table3_2000",
    "all_cells",
    "figure7_all_cells",
)
TABLE3_FIXED_COUNTS = dict(zip(
    POPULATIONS, (2144, 605, 2272, 568, 503, 110, 1492, 306), strict=True
))
TABLE3_REPORTED = {
    "table3_fixed_percentage": (5.1, 1.5, 5.7, 1.4, 2.5, 1.2, 1.4, 1.0),
    "table3_1000": (2.9, 1.8, 3.0, 1.7, 4.3, 1.1, 1.2, 1.0),
    "table3_2000": (4.9, 2.7, 5.1, 2.3, 7.9, 1.1, 1.6, 1.0),
}
ANALYSIS_START_MS = 100.0
FIVE_SECOND_END_MS = 5100.0
SIXTY_SECOND_END_MS = 60_100.0
THREE_MS_BINS_5S = 1666  # Only complete bins: [100, 5098) ms.
THREE_MS_BINS_60S = 20_000  # [100, 60100) ms.
TWENTY_FIVE_MS_BINS_5S = 200


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _hashed_lines(stream: BinaryIO, digest: object) -> Iterator[str]:
    for line in stream:
        digest.update(line)
        yield line.decode("utf-8")


def _read_cells(path: Path) -> tuple[dict[str, list[int]], dict[int, str], str]:
    by_pop = {pop: [] for pop in POPULATIONS}
    pop_by_gid: dict[int, str] = {}
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        rows = csv.DictReader(_hashed_lines(stream, digest))
        if not {"gid", "population"}.issubset(rows.fieldnames or ()):
            raise ValueError("PDCM cells.csv lacks gid or population columns")
        for row in rows:
            gid, pop = int(row["gid"]), row["population"]
            if gid < 0 or gid in pop_by_gid or pop not in by_pop:
                raise ValueError(f"Duplicate or unexpected PDCM cell: {gid}, {pop}")
            pop_by_gid[gid] = pop
            by_pop[pop].append(gid)
    if any(not gids for gids in by_pop.values()):
        raise ValueError("PDCM sampling requires all eight cortical populations")
    for gids in by_pop.values():
        gids.sort()
    return by_pop, pop_by_gid, digest.hexdigest()


def _sample_cell_ids(
    by_pop: dict[str, list[int]], profile: str, seed: int
) -> dict[str, list[int]]:
    if profile not in PROFILES:
        raise ValueError(f"Unknown PDCM sampling profile: {profile}")
    sampled: dict[str, list[int]] = {}
    for index, pop in enumerate(POPULATIONS):
        available = sorted(by_pop[pop])
        if profile == "table3_fixed_percentage":
            requested = TABLE3_FIXED_COUNTS[pop]
        elif profile == "table3_1000":
            requested = 1000
        elif profile == "table3_2000":
            requested = 2000
        else:
            requested = len(available)
        if requested > len(available):
            raise ValueError(
                f"{profile} needs {requested} unique {pop} cells, but this run has "
                f"only {len(available)}; sampling with replacement or silently "
                "capping the sample would change the paper condition"
            )
        sampled[pop] = (
            available if requested == len(available)
            else sorted(random.Random(seed + index).sample(available, requested))
        )
    return sampled


def _validate_run(run_root: Path, profile: str) -> tuple[dict, dict[str, Path], dict[str, str]]:
    if profile not in PROFILES:
        raise ValueError(f"Unknown PDCM sampling profile: {profile}")
    paths = {name: run_root / name for name in (
        "summary.json", "source_manifest.json", "cells.csv", "spikes.csv"
    )}
    for name, path in paths.items():
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"PDCM source {name} must be a regular file")
    summary_bytes = paths["summary.json"].read_bytes()
    provenance_bytes = paths["source_manifest.json"].read_bytes()
    summary = json.loads(summary_bytes)
    provenance = json.loads(provenance_bytes)
    if summary.get("workflow") != "pdcm_replication_v1" or summary.get("status") != "completed":
        raise ValueError("Choose a completed Digifly PDCM run")
    metadata = summary.get("metadata")
    if not isinstance(metadata, dict):
        raise ValueError("PDCM summary lacks run metadata")
    original_hashes = provenance.get("original_sha256")
    if (
        metadata.get("model_commit") != MODEL_COMMIT
        or provenance.get("model_commit") != MODEL_COMMIT
        or not isinstance(original_hashes, dict)
        or any(original_hashes.get(name) != value
               for name, value in SOURCE_SHA256.items())
    ):
        raise ValueError("PDCM source provenance differs from the pinned paper model")
    duration = float(metadata["duration_ms"])
    scale = float(metadata["scale"])
    drive = metadata.get("drive")
    if not math.isfinite(duration) or duration < SIXTY_SECOND_END_MS:
        raise ValueError("PDCM 60-second sampling requires 60,100 ms including settling")
    if not math.isfinite(scale) or not 0 < scale <= 1:
        raise ValueError("PDCM run has an invalid scale")
    if drive not in {"balanced_poisson", "balanced_dc"}:
        raise ValueError("Sampling analysis requires a balanced Poisson or DC drive")
    if profile.startswith("table3_") and (
        not math.isclose(scale, 1.0, abs_tol=1e-9)
        or drive != "balanced_poisson"
    ):
        raise ValueError("Table 3 requires a full-scale balanced-Poisson PDCM run")
    if profile == "figure7_all_cells" and not math.isclose(scale, 0.3, abs_tol=1e-9):
        raise ValueError("Figure 7 requires a 30%-scale PDCM run")
    digests = {
        "summary.json": hashlib.sha256(summary_bytes).hexdigest(),
        "source_manifest.json": hashlib.sha256(provenance_bytes).hexdigest(),
    }
    return metadata, paths, digests


def _synchrony(counts: list[int], number_of_cells: int) -> tuple[float | None, float | None]:
    bins = len(counts)
    mean = sum(counts) / bins
    if mean == 0:
        return None, None
    variance = max(0.0, sum(count * count for count in counts) / bins - mean * mean)
    total_fano = variance / mean
    return total_fano, total_fano / number_of_cells


def _mean_signed_correlation(
    selected: list[int], binned: dict[int, array]
) -> tuple[float | None, int]:
    """Exact signed pair mean via summed standardized traces, in O(cells*bins)."""
    summed = [0.0] * TWENTY_FIVE_MS_BINS_5S
    valid = 0
    for gid in selected:
        counts = binned.get(gid)
        if counts is None:
            continue
        mean = sum(counts) / TWENTY_FIVE_MS_BINS_5S
        variance = sum(value * value for value in counts) / TWENTY_FIVE_MS_BINS_5S - mean * mean
        if variance <= 0:
            continue
        scale = 1 / math.sqrt(variance)
        for index, value in enumerate(counts):
            summed[index] += (value - mean) * scale
        valid += 1
    if valid < 2:
        return None, valid
    result = (sum(value * value for value in summed) / TWENTY_FIVE_MS_BINS_5S - valid) / (valid * (valid - 1))
    return result, valid


def _format(value: float | None) -> str:
    return "" if value is None else f"{value:.12g}"


def _analyze_spikes(
    path: Path,
    metadata: dict,
    pop_by_gid: dict[int, str],
    sampled: dict[str, list[int]],
) -> tuple[list[dict[str, str]], str]:
    selected_pop = {gid: pop for pop, gids in sampled.items() for gid in gids}
    counts = {pop: [0] * THREE_MS_BINS_60S for pop in POPULATIONS}
    sampled_spikes = {pop: 0 for pop in POPULATIONS}
    # [previous spike time, interval count, sum(interval), sum(interval**2)].
    isi: dict[int, list[float]] = {}
    corr_bins: dict[int, array] = {}
    digest = hashlib.sha256()
    duration = float(metadata["duration_ms"])
    with path.open("rb") as stream:
        rows = csv.DictReader(_hashed_lines(stream, digest))
        if not {"time_ms", "gid", "population"}.issubset(rows.fieldnames or ()):
            raise ValueError("PDCM spikes.csv lacks expected columns")
        for row in rows:
            time_ms = float(row["time_ms"])
            gid = int(row["gid"])
            pop = row["population"]
            if not math.isfinite(time_ms) or not 0 <= time_ms <= duration + 1e-6:
                raise ValueError("PDCM spike has an invalid time")
            if pop_by_gid.get(gid) != pop:
                raise ValueError(f"PDCM spike references an unknown or mismatched cell: {gid}")
            if not ANALYSIS_START_MS <= time_ms < SIXTY_SECOND_END_MS:
                continue
            if selected_pop.get(gid) != pop:
                continue
            sampled_spikes[pop] += 1
            bin_index = int((time_ms - ANALYSIS_START_MS) / 3.0)
            counts[pop][bin_index] += 1
            state = isi.get(gid)
            if state is None:
                isi[gid] = [time_ms, 0.0, 0.0, 0.0]
            else:
                interval = time_ms - state[0]
                if interval < 0:
                    raise ValueError(f"PDCM spikes for cell {gid} are not time ordered")
                state[0] = time_ms
                state[1] += 1
                state[2] += interval
                state[3] += interval * interval
            if time_ms < FIVE_SECOND_END_MS:
                cell_bins = corr_bins.get(gid)
                if cell_bins is None:
                    cell_bins = array("I", [0]) * TWENTY_FIVE_MS_BINS_5S
                    corr_bins[gid] = cell_bins
                cell_bins[int((time_ms - ANALYSIS_START_MS) / 25.0)] += 1
    rows_out = []
    for index, pop in enumerate(POPULATIONS):
        gids = sampled[pop]
        cvs = []
        for gid in gids:
            state = isi.get(gid)
            if state is None or state[1] < 2:
                continue
            mean_isi = state[2] / state[1]
            if mean_isi > 0:
                variance = max(0.0, state[3] / state[1] - mean_isi * mean_isi)
                cvs.append(math.sqrt(variance) / mean_isi)
        fano_5s, normalized_5s = _synchrony(counts[pop][:THREE_MS_BINS_5S], len(gids))
        fano_60s, normalized_60s = _synchrony(counts[pop], len(gids))
        signed_corr, corr_valid = _mean_signed_correlation(gids, corr_bins)
        reported = TABLE3_REPORTED.get(metadata["profile"])
        rows_out.append({
            "population": pop,
            "sampled_cells": str(len(gids)),
            "mean_rate_hz_60s": _format(sampled_spikes[pop] / (len(gids) * 60.0)),
            "mean_cv_isi_60s": _format(sum(cvs) / len(cvs) if cvs else None),
            "population_total_fano_3ms_5s": _format(fano_5s),
            "per_cell_normalized_synchrony_3ms_5s": _format(normalized_5s),
            "population_total_fano_3ms_60s": _format(fano_60s),
            "per_cell_normalized_synchrony_3ms_60s": _format(normalized_60s),
            "mean_signed_cross_correlation_25ms_5s": _format(signed_corr),
            "correlation_defined_cells": str(corr_valid),
            "paper_table3_reported_synchrony_60s": _format(reported[index] if reported else None),
        })
    return rows_out, digest.hexdigest()


def analyze(
    run_root: Path,
    profile: str,
    *,
    output_dir: Path | None = None,
    seed: int | None = None,
) -> Path:
    run_root = run_root.expanduser().resolve(strict=True)
    metadata, source_paths, source_digests = _validate_run(run_root, profile)
    if seed is None:
        seed = metadata["seed"]
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("PDCM sampling seed must be an integer")
    destination = (
        output_dir.expanduser().resolve()
        if output_dir is not None
        else run_root.with_name(f"{run_root.name}.sampling-{profile}-v{ANALYSIS_VERSION}-seed{seed}")
    )
    if destination == run_root or run_root in destination.parents:
        raise ValueError("Sampling output must be outside the original PDCM run")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"Sampling output already exists: {destination}")
    by_pop, pop_by_gid, source_digests["cells.csv"] = _read_cells(source_paths["cells.csv"])
    if int(metadata["cortical_cells"]) != len(pop_by_gid):
        raise ValueError("PDCM cell count disagrees with the completed run summary")
    sampled = _sample_cell_ids(by_pop, profile, seed)
    analysis_metadata = {**metadata, "profile": profile}
    metrics, source_digests["spikes.csv"] = _analyze_spikes(
        source_paths["spikes.csv"], analysis_metadata, pop_by_gid, sampled
    )
    if any(_digest(path) != source_digests[name] for name, path in source_paths.items()):
        raise ValueError("PDCM source files changed during sampling analysis")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir(exist_ok=False)
    sample_path = destination / "sampling_manifest.json"
    sample_path.write_text(json.dumps({
        "schema_version": ANALYSIS_VERSION,
        "profile": profile,
        "seed": seed,
        "method": "uniform without replacement per population; all cells for no-subsampling profiles",
        "cell_ids": sampled,
        "sampled_cell_counts": {pop: len(sampled[pop]) for pop in POPULATIONS},
        "note": "The paper's exact sampled IDs were not published; these are deterministic Digifly selections.",
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    metrics_path = destination / "sampling_metrics.csv"
    with metrics_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(metrics[0]))
        writer.writeheader()
        writer.writerows(metrics)
    manifest = {
        "schema_version": ANALYSIS_VERSION,
        "workflow": "pdcm_sampling_analysis_v1",
        "profile": profile,
        "source_run": str(run_root),
        "source_workflow": "pdcm_replication_v1",
        "source_sha256": source_digests,
        "analysis_sha256": {
            "script": _digest(Path(__file__).resolve()),
            sample_path.name: _digest(sample_path),
            metrics_path.name: _digest(metrics_path),
        },
        "scale": float(metadata["scale"]),
        "drive": metadata["drive"],
        "duration_ms": float(metadata["duration_ms"]),
        "seed": seed,
        "source_unchanged_after_analysis": True,
        "analysis_windows_ms": {
            "rate_and_cv": [100, 60100],
            "synchrony_5s": [100, 5098],
            "synchrony_60s": [100, 60100],
            "correlation_5s": [100, 5100],
        },
        "bin_width_ms": {"synchrony": 3, "correlation": 25},
        "synchrony_definitions": {
            "population_total_fano": "Var across bins of total sampled-population spike counts / mean of those counts.",
            "per_cell_normalized": "Population-total Fano divided by the number of sampled cells; this follows the paper's per-cell-normalized count equation.",
            "interpretation": "The published Table 3 numerical scale resembles population-total Fano, whereas the printed equation normalizes each bin by cell count. Both are reported; neither is declared a paper match automatically.",
        },
        "correlation_method": "Exact mean signed pairwise Pearson correlation from summed standardized 25 ms traces; O(cells × bins), with no all-pairs matrix. Absolute pairwise mean is not computed.",
        "paper_reference": PAPER_URL,
        "note": "Sidecar only: no file in the completed simulation was modified.",
    }
    (destination / "analysis_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_root", type=Path)
    parser.add_argument("--profile", required=True, choices=PROFILES)
    parser.add_argument("--seed", type=int, help="Sampling seed; defaults to the simulation seed")
    parser.add_argument("--output-dir", type=Path, help="Fresh directory outside the source run")
    args = parser.parse_args()
    print(analyze(args.run_root, args.profile, output_dir=args.output_dir, seed=args.seed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
