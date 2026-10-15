"""Create paper-window PDCM statistics beside, not over, a completed raw run."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

from digifly_app.workers.pdcm_netpyne_worker import POPULATIONS, _paper_statistics


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _write_synchrony_comparison(metrics_path: Path, destination: Path) -> None:
    """Expose both count normalizations without changing the worker's output."""
    windows = ("5s", "60s")
    with metrics_path.open(newline="", encoding="utf-8") as source, destination.open(
        "w", newline="", encoding="utf-8"
    ) as target:
        reader = csv.DictReader(source)
        writer = csv.writer(target)
        writer.writerow(
            ("population", "sampled_cells")
            + tuple(
                field
                for window in windows
                for field in (
                    f"per_cell_normalized_synchrony_3ms_{window}",
                    f"population_total_fano_synchrony_3ms_{window}",
                )
            )
        )
        for row in reader:
            sampled_cells = int(row["sampled_cells"])
            if sampled_cells < 1:
                raise ValueError("Each sampled population needs at least one cell")
            values = [row["population"], sampled_cells]
            for window in windows:
                normalized = row[f"synchrony_3ms_{window}"]
                values.extend(
                    (normalized, f"{float(normalized) * sampled_cells:.8g}")
                    if normalized
                    else ("", "")
                )
            writer.writerow(values)


def analyze(run_root: Path, output_dir: Path | None = None) -> Path:
    run_root = run_root.expanduser().resolve()
    summary = json.loads((run_root / "summary.json").read_text(encoding="utf-8"))
    if summary.get("workflow") != "pdcm_replication_v1" or summary.get("status") != "completed":
        raise ValueError("Choose a completed Digifly PDCM run")
    duration_ms = float(summary["metadata"]["duration_ms"])
    if duration_ms < 60_100:
        raise ValueError("The paper's 60-second statistics require at least 60,100 ms including settling")
    cells_path, spikes_path = run_root / "cells.csv", run_root / "spikes.csv"
    gids_by_population = {pop: [] for pop in POPULATIONS}
    with cells_path.open(newline="", encoding="utf-8") as stream:
        for cell in csv.DictReader(stream):
            gids_by_population[cell["population"]].append(int(cell["gid"]))
    if any(len(gids) > 256 for gids in gids_by_population.values()):
        try:
            import numpy  # noqa: F401 - required by exact all-pairs magnitude calculation
        except ImportError as exc:
            raise RuntimeError(
                "NumPy is required to reanalyze correlations for a population with "
                "more than 256 cells; use a Python environment with NumPy installed"
            ) from exc
    spikes = []
    with spikes_path.open(newline="", encoding="utf-8") as stream:
        for event in csv.DictReader(stream):
            spikes.append((float(event["time_ms"]), int(event["gid"]), event["population"]))
    if any(not gids for gids in gids_by_population.values()):
        raise ValueError("The run must have all eight cortical populations")
    destination = (
        output_dir.expanduser().resolve()
        if output_dir is not None
        else run_root / "analysis-paper-windows-v2"
    )
    destination.mkdir(parents=True, exist_ok=False)
    sample_seed = int(summary["metadata"]["seed"])
    _paper_statistics(
        destination,
        gids_by_population=gids_by_population,
        spikes=spikes,
        duration_ms=duration_ms,
        sample_seed=sample_seed,
    )
    comparison = destination / "synchrony_metric_comparison.csv"
    _write_synchrony_comparison(destination / "paper_metrics.csv", comparison)
    (destination / "analysis_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "workflow": "pdcm_paper_window_analysis_v2",
                "source_summary": str(run_root / "summary.json"),
                "source_sha256": {
                    "summary.json": _digest(run_root / "summary.json"),
                    "cells.csv": _digest(cells_path),
                    "spikes.csv": _digest(spikes_path),
                },
                "analysis_sha256": {
                    "script": _digest(Path(__file__).resolve()),
                    "worker_functions": _digest(
                        Path(_paper_statistics.__code__.co_filename).resolve()
                    ),
                    "paper_metrics.csv": _digest(destination / "paper_metrics.csv"),
                    "sampling_manifest.json": _digest(destination / "sampling_manifest.json"),
                    "synchrony_metric_comparison.csv": _digest(comparison),
                },
                "analysis_start_ms": 100,
                "analysis_end_ms": 60_100,
                "correlation_end_ms": 5100,
                "sample_seed": sample_seed,
                "synchrony_definitions": {
                    "source_columns": ["synchrony_3ms_5s", "synchrony_3ms_60s"],
                    "comparison_file": comparison.name,
                    "bin_width_ms": 3,
                    "binning": "Non-overlapping bins; left edge inclusive, right edge exclusive. The 5 s nominal window has 1666 complete bins (4998 ms); the 60 s window has 20000 complete bins.",
                    "population_total_fano": "For sampled cells, C[b] is their total spike count in bin b; Fano = population variance of C[b] across bins / mean of C[b].",
                    "per_cell_normalized": "For N sampled cells, use C[b]/N in the same variance/mean expression; this equals population_total_fano/N.",
                    "derivation": "Population-total Fano values are the existing per-cell-normalized CSV values multiplied by sampled_cells. The worker writes eight significant digits to paper_metrics.csv before this derivation.",
                    "paper_comparison_reference": "Romaro et al. (2021), Supplementary Table S5 in the accessible author preprint, https://arxiv.org/pdf/2005.03764; final article supplement linked from https://pmc.ncbi.nlm.nih.gov/articles/PMC8382011/",
                    "interpretation": "The population-total Fano scale is comparable to the paper's tabulated 10% Poisson synchrony values. Both normalizations are reported explicitly; this sidecar does not resolve differences between the paper's equation and reported numerical values, reproduce unpublished sampled cell IDs, or treat Fano as a unique measure of synchrony.",
                },
                "note": "Derived outputs do not modify or supersede the original run artifacts.",
            },
            indent=2,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_root", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    print(analyze(args.run_root, args.output_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
