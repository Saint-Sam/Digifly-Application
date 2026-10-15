"""Render the paper's paired PDCM scaling panels from completed Digifly runs.

Figures 4, 5 and 6 use a recorded, proportional 1,862-cell raster sample and
the version-2 paper-window statistics. Figure 7 uses every cell and requires
the separate, explicit all-cell sampling sidecars. This renderer never edits
simulation runs or their analysis sidecars.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import random

from digifly_app.workers.pdcm_netpyne_worker import MODEL_COMMIT, POPULATIONS, SOURCE_SHA256


PAPER_URL = "https://pmc.ncbi.nlm.nih.gov/articles/PMC8382011/"
SCALE_BY_FIGURE = {4: 0.5, 5: 0.3, 6: 0.1, 7: 0.3}
RASTER_CELLS = 1862
RASTER_START_MS = 100.0
RASTER_END_MS = 600.0
COLORS = (
    "#0072B2", "#56B4E9", "#E69F00", "#D55E00",
    "#009E73", "#A68C00", "#CC79A7", "#444444",
)
LABELS = {"L2e": "L2/3e", "L2i": "L2/3i", "L4e": "L4e", "L4i": "L4i",
          "L5e": "L5e", "L5i": "L5i", "L6e": "L6e", "L6i": "L6i"}


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _regular(path: Path) -> Path:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"Expected a regular PDCM file: {path}")
    return path


def _csv_rows(path: Path) -> dict[str, dict[str, str]]:
    with _regular(path).open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    result = {row["population"]: row for row in rows}
    if len(rows) != len(POPULATIONS) or set(result) != set(POPULATIONS):
        raise ValueError(f"Expected exactly the eight PDCM populations in {path}")
    return result


def _apportion(by_population: dict[str, list[int]], total: int) -> dict[str, int]:
    """Largest-remainder proportional allocation with deterministic tie order."""
    available = sum(len(by_population[pop]) for pop in POPULATIONS)
    if total < len(POPULATIONS) or total > available:
        raise ValueError("Raster sample must fit the available eight-population network")
    exact = {pop: total * len(by_population[pop]) / available for pop in POPULATIONS}
    counts = {pop: math.floor(exact[pop]) for pop in POPULATIONS}
    for pop in sorted(POPULATIONS, key=lambda name: (-(exact[name] - counts[name]), POPULATIONS.index(name)))[:total - sum(counts.values())]:
        counts[pop] += 1
    if any(counts[pop] < 1 or counts[pop] > len(by_population[pop]) for pop in POPULATIONS):
        raise ValueError("Proportional raster selection omitted or exceeded a population")
    return counts


def _raster_sample(by_population: dict[str, list[int]], count: int, seed: int) -> dict[str, list[int]]:
    counts = _apportion(by_population, count)
    return {
        pop: sorted(random.Random(seed + index).sample(sorted(by_population[pop]), counts[pop]))
        for index, pop in enumerate(POPULATIONS)
    }


def _read_cells(path: Path) -> dict[str, list[int]]:
    by_population = {pop: [] for pop in POPULATIONS}
    seen: set[int] = set()
    with _regular(path).open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            gid, pop = int(row["gid"]), row["population"]
            if gid in seen or pop not in by_population:
                raise ValueError(f"Duplicate or unexpected PDCM cell {gid}/{pop}")
            seen.add(gid)
            by_population[pop].append(gid)
    if any(not gids for gids in by_population.values()):
        raise ValueError("The PDCM run must contain all eight populations")
    return {pop: sorted(gids) for pop, gids in by_population.items()}


def _read_analysis(run: Path, analysis: Path, figure: int) -> tuple[dict[str, dict[str, float]], dict[str, list[int]], dict[str, str]]:
    manifest_path = _regular(analysis / "analysis_manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if figure == 7:
        if manifest.get("workflow") != "pdcm_sampling_analysis_v1" or manifest.get("profile") != "figure7_all_cells":
            raise ValueError("Figure 7 requires the figure7_all_cells sampling sidecar")
        metrics_path = analysis / "sampling_metrics.csv"
        sample_path = analysis / "sampling_manifest.json"
        if Path(manifest["source_run"]).resolve() != run:
            raise ValueError("Figure 7 analysis belongs to another run")
        field = "population_total_fano_3ms_5s"
    else:
        if manifest.get("workflow") != "pdcm_paper_window_analysis_v2":
            raise ValueError("Figures 4–6 require the paper-window version-2 sidecar")
        metrics_path = analysis / "paper_metrics.csv"
        sample_path = analysis / "sampling_manifest.json"
        comparison_path = analysis / "synchrony_metric_comparison.csv"
        field = "population_total_fano_synchrony_3ms_5s"
    source_hashes = manifest.get("source_sha256", {})
    for name in ("summary.json", "cells.csv", "spikes.csv"):
        if source_hashes.get(name) != _digest(_regular(run / name)):
            raise ValueError(f"Analysis source hash mismatch for {name}")
    for name, path in ((metrics_path.name, metrics_path), (sample_path.name, sample_path)):
        if manifest.get("analysis_sha256", {}).get(name) != _digest(_regular(path)):
            raise ValueError(f"Analysis artifact hash mismatch for {name}")
    raw = _csv_rows(metrics_path)
    if figure != 7:
        if manifest.get("analysis_sha256", {}).get(comparison_path.name) != _digest(_regular(comparison_path)):
            raise ValueError("Synchrony comparison hash mismatch")
        comparison = _csv_rows(comparison_path)
    sample_doc = json.loads(sample_path.read_text(encoding="utf-8"))
    sample = sample_doc["cell_ids"]
    if set(sample) != set(POPULATIONS):
        raise ValueError("Sampling manifest lacks one or more PDCM populations")
    metrics: dict[str, dict[str, float]] = {}
    for pop in POPULATIONS:
        gids = sample[pop]
        if not gids or len(gids) != len(set(gids)) or len(gids) != int(raw[pop]["sampled_cells"]):
            raise ValueError(f"Invalid statistic sample for {pop}")
        sync = raw[pop][field] if figure == 7 else comparison[pop][field]
        values = {
            "rate_hz": float(raw[pop]["mean_rate_hz_60s"]),
            "cv_isi": float(raw[pop]["mean_cv_isi_60s"]),
            "fano_5s": float(sync),
        }
        if any(not math.isfinite(value) or value < 0 for value in values.values()):
            raise ValueError(f"Invalid paper-window statistic for {pop}")
        metrics[pop] = values
    hashes = {path.name: _digest(path) for path in (manifest_path, metrics_path, sample_path)}
    if figure != 7:
        hashes[comparison_path.name] = _digest(comparison_path)
    return metrics, sample, hashes


def _load_run(run_root: Path, analysis: Path, figure: int, drive: str, raster_seed: int) -> dict:
    run = run_root.expanduser().resolve(strict=True)
    analysis = analysis.expanduser().resolve(strict=True)
    summary = json.loads(_regular(run / "summary.json").read_text(encoding="utf-8"))
    metadata = summary.get("metadata", {})
    if summary.get("workflow") != "pdcm_replication_v1" or summary.get("status") != "completed":
        raise ValueError("Choose a completed Digifly PDCM run")
    if (metadata.get("model_commit") != MODEL_COMMIT
            or metadata.get("drive") != drive
            or not math.isclose(float(metadata["scale"]), SCALE_BY_FIGURE[figure], abs_tol=1e-9)
            or float(metadata["duration_ms"]) < 60_100):
        raise ValueError("Run does not match the requested paper figure condition")
    provenance = json.loads(_regular(run / "source_manifest.json").read_text(encoding="utf-8"))
    if (provenance.get("model_commit") != MODEL_COMMIT
            or any(provenance.get("original_sha256", {}).get(name) != value
                   for name, value in SOURCE_SHA256.items())):
        raise ValueError("Run source differs from the pinned PDCM model")
    cells_path, spikes_path = _regular(run / "cells.csv"), _regular(run / "spikes.csv")
    cells = _read_cells(cells_path)
    if sum(map(len, cells.values())) != int(metadata["cortical_cells"]):
        raise ValueError("Run cell count disagrees with summary")
    metrics, metric_sample, analysis_hashes = _read_analysis(run, analysis, figure)
    for pop in POPULATIONS:
        if not set(metric_sample[pop]).issubset(cells[pop]):
            raise ValueError(f"Statistic sample includes an unknown {pop} cell")
    if figure == 7:
        if any(sorted(metric_sample[pop]) != cells[pop] for pop in POPULATIONS):
            raise ValueError("Figure 7 must use all cells for its statistics")
        raster_sample = cells
    else:
        raster_sample = _raster_sample(cells, RASTER_CELLS, raster_seed)
    offsets: dict[int, int] = {}
    centers: list[float] = []
    start = 0
    for pop in POPULATIONS:
        for index, gid in enumerate(raster_sample[pop]):
            offsets[gid] = start + index
        centers.append(start + (len(raster_sample[pop]) - 1) / 2)
        start += len(raster_sample[pop])
    raster_sets = {pop: set(raster_sample[pop]) for pop in POPULATIONS}
    times = {pop: [] for pop in POPULATIONS}
    positions = {pop: [] for pop in POPULATIONS}
    with spikes_path.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            time_ms = float(row["time_ms"])
            if RASTER_START_MS <= time_ms < RASTER_END_MS:
                gid = int(row["gid"])
                if gid in offsets:
                    pop = row["population"]
                    if pop not in times or gid not in raster_sets[pop]:
                        raise ValueError("Raster spike has an inconsistent population")
                    times[pop].append(time_ms - RASTER_START_MS)
                    positions[pop].append(offsets[gid])
    return {
        "run": run,
        "analysis": analysis,
        "metadata": metadata,
        "metrics": metrics,
        "metric_sample": metric_sample,
        "raster_sample": raster_sample,
        "times": times,
        "positions": positions,
        "centers": centers,
        "raster_cells": start,
        "source_sha256": {name: _digest(_regular(run / name)) for name in
                          ("summary.json", "source_manifest.json", "cells.csv", "spikes.csv")},
        "analysis_sha256": analysis_hashes,
    }


def _style_axis(ax) -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(direction="out", length=2.5, width=0.7)


def _render(figure: int, poisson: dict, dc: dict, output: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    plt.rcParams.update({
        "font.family": "sans-serif", "font.size": 8.5, "axes.labelsize": 8.5,
        "axes.titlesize": 9, "xtick.labelsize": 7.5, "ytick.labelsize": 7.5,
        "pdf.fonttype": 42, "svg.fonttype": "none",
    })
    fig, axes = plt.subplots(2, 4, figsize=(15.2, 7.4), layout="constrained",
                             gridspec_kw={"width_ratios": [1.45, 1, 1, 1], "wspace": 0.16, "hspace": 0.18})
    for row_index, (label, data) in enumerate((("Balanced Poisson", poisson), ("Balanced DC", dc))):
        raster = axes[row_index, 0]
        for pop, color in zip(POPULATIONS, COLORS):
            raster.scatter(data["times"][pop], data["positions"][pop],
                           s=7 if figure != 7 else 2.2, marker="|", linewidths=0.45,
                           color=color, rasterized=True)
        raster.set(xlim=(0, 500), ylim=(data["raster_cells"], -1),
                   xlabel="Time after settling (ms)", ylabel="Population / sampled cells",
                   title=f"{label} · {data['raster_cells']:,} cells")
        raster.set_yticks(data["centers"], [LABELS[pop] for pop in POPULATIONS])
        raster.grid(axis="x", linewidth=0.4, alpha=0.2)
        _style_axis(raster)
        for col_index, (key, title, ylabel) in enumerate((
            ("rate_hz", "Mean firing rate · 60 s", "Rate (Hz)"),
            ("cv_isi", "Spike-train irregularity · 60 s", "CV of ISI"),
            ("fano_5s", "Population synchrony · 5 s", "3 ms-bin total-count Fano"),
        ), start=1):
            ax = axes[row_index, col_index]
            values = [data["metrics"][pop][key] for pop in POPULATIONS]
            ax.bar(range(len(POPULATIONS)), values, color=COLORS, width=0.72,
                   edgecolor="#303030", linewidth=0.35)
            ax.set(title=title, ylabel=ylabel, ylim=(0, max(values) * 1.14 if any(values) else 1))
            ax.set_xticks(range(len(POPULATIONS)), [LABELS[pop] for pop in POPULATIONS],
                          rotation=45, ha="right")
            ax.grid(axis="y", linewidth=0.4, alpha=0.2)
            ax.set_axisbelow(True)
            _style_axis(ax)
        for col_index, ax in enumerate(axes[row_index]):
            ax.text(-0.095, 1.09, chr(ord("A") + row_index * 4 + col_index),
                    transform=ax.transAxes, fontweight="bold", fontsize=11,
                    ha="left", va="top")
    for col_index in range(1, 4):
        max_value = max(axes[0, col_index].get_ylim()[1], axes[1, col_index].get_ylim()[1])
        axes[0, col_index].set_ylim(0, max_value)
        axes[1, col_index].set_ylim(0, max_value)
    fig.suptitle(f"Digifly PDCM · Figure {figure} condition · {int(SCALE_BY_FIGURE[figure] * 100)}% scale",
                 fontsize=11, fontweight="bold")
    fig.savefig(output / f"figure{figure}_digifly.png", dpi=300, facecolor="white")
    fig.savefig(output / f"figure{figure}_digifly.pdf", dpi=300, facecolor="white")
    plt.close(fig)


def render(figure: int, poisson_run: Path, dc_run: Path, output_dir: Path,
           *, poisson_analysis: Path | None = None, dc_analysis: Path | None = None,
           raster_seed: int = 3) -> Path:
    if figure not in SCALE_BY_FIGURE:
        raise ValueError("Only paper Figures 4–7 have this eight-panel layout")
    if isinstance(raster_seed, bool) or not isinstance(raster_seed, int):
        raise ValueError("Raster seed must be an integer")
    poisson_run, dc_run = poisson_run.expanduser().resolve(strict=True), dc_run.expanduser().resolve(strict=True)
    if poisson_run == dc_run:
        raise ValueError("Poisson and DC inputs must be separate runs")
    if poisson_analysis is None:
        poisson_analysis = poisson_run / "analysis-paper-windows-v2"
    if dc_analysis is None:
        dc_analysis = dc_run / "analysis-paper-windows-v2"
    output = output_dir.expanduser().resolve()
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"Refusing to overwrite an existing figure folder: {output}")
    for source in (poisson_run, dc_run, poisson_analysis.resolve(), dc_analysis.resolve()):
        if output == source or source in output.parents:
            raise ValueError("Figure output must be outside the raw runs and sidecars")
    poisson = _load_run(poisson_run, poisson_analysis, figure, "balanced_poisson", raster_seed)
    dc = _load_run(dc_run, dc_analysis, figure, "balanced_dc", raster_seed)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir(exist_ok=False)
    _render(figure, poisson, dc, output)
    for data in (poisson, dc):
        if any(_digest(data["run"] / name) != digest
               for name, digest in data["source_sha256"].items()):
            raise RuntimeError("A PDCM run source changed during figure generation")
        if any(_digest(data["analysis"] / name) != digest
               for name, digest in data["analysis_sha256"].items()):
            raise RuntimeError("A PDCM analysis source changed during figure generation")
    table_path = output / f"figure{figure}_source_data.csv"
    with table_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(("drive", "population", "raster_cells", "statistic_cells",
                         "mean_rate_hz_60s", "mean_cv_isi_60s", "population_total_fano_3ms_5s"))
        for drive, data in (("balanced_poisson", poisson), ("balanced_dc", dc)):
            for pop in POPULATIONS:
                values = data["metrics"][pop]
                writer.writerow((drive, pop, len(data["raster_sample"][pop]),
                                 len(data["metric_sample"][pop]), values["rate_hz"],
                                 values["cv_isi"], values["fano_5s"]))
    figure_files = [output / f"figure{figure}_digifly.{extension}" for extension in ("png", "pdf")]
    manifest = {
        "schema_version": 1,
        "workflow": "pdcm_scaling_figure_v1",
        "paper_figure": figure,
        "paper_reference": PAPER_URL,
        "model_commit": MODEL_COMMIT,
        "renderer_sha256": _digest(Path(__file__).resolve()),
        "scale": SCALE_BY_FIGURE[figure],
        "panel_mapping": {letter: description for letter, description in zip(
            "ABCDEFGH", ("Poisson raster", "Poisson rate", "Poisson CV ISI", "Poisson synchrony",
                         "DC raster", "DC rate", "DC CV ISI", "DC synchrony"), strict=True)},
        "windows_ms": {"settling_excluded_before": 100, "raster": [100, 600],
                       "rate_cv": [100, 60100], "synchrony_5s": [100, 5098]},
        "raster_sampling": "All cells" if figure == 7 else "Proportional across eight populations; uniform without replacement within each population",
        "raster_seed": raster_seed,
        "raster_cell_ids": {drive: data["raster_sample"] for drive, data in
                            (("balanced_poisson", poisson), ("balanced_dc", dc))},
        "statistic_sampling": {drive: {pop: len(data["metric_sample"][pop]) for pop in POPULATIONS}
                               for drive, data in (("balanced_poisson", poisson), ("balanced_dc", dc))},
        "source_runs": {drive: {"run": str(data["run"]), "analysis": str(data["analysis"]),
                                "source_sha256": data["source_sha256"],
                                "analysis_sha256": data["analysis_sha256"]}
                        for drive, data in (("balanced_poisson", poisson), ("balanced_dc", dc))},
        "figure_sha256": {path.name: _digest(path) for path in figure_files},
        "source_data_sha256": {table_path.name: _digest(table_path)},
        "synchrony_interpretation": "Population-total 3 ms-bin Fano (published operational scale); the paper's printed per-cell-normalized equation is recorded separately in the analyses.",
        "caveats": ["The authors' exact sampled cell IDs are unavailable; Digifly's deterministic selections are recorded.",
                    "The paper's 8,000-cell prose cannot be fulfilled with unique cells in the 7,713-cell 10% network." if figure == 6 else
                    "Deterministic Digifly samples may differ from those used in the paper."],
    }
    (output / "figure_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    caption = (
        f"Digifly reconstruction of the Romaro et al. (2021) Figure {figure} condition. "
        f"A/E show {poisson['raster_cells']:,}/{dc['raster_cells']:,} cortical cells over 500 ms following 100 ms settling, "
        "grouped by population. B/F show sampled-cell mean firing rates over 60 s; "
        "C/G show the mean per-cell CV of interspike intervals over 60 s for cells with at least three spikes; "
        "D/H show the population-total Fano factor of sampled spike counts in complete 3 ms bins over 5 s. "
        f"Poisson (seed {poisson['metadata']['seed']}) and DC (seed {dc['metadata']['seed']}) "
        "are independent pinned-model runs. Error bars are not shown because "
        "each condition has one stochastic realization; bar heights are population statistics, not trial means. "
        f"Panel values are in {table_path.name}; cell IDs and file hashes are in figure_manifest.json. "
        "This is an independently regenerated figure, "
        "not the authors' original panel or an accepted exact replication.\n"
    )
    (output / "caption.txt").write_text(caption, encoding="utf-8")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--figure", required=True, type=int, choices=SCALE_BY_FIGURE)
    parser.add_argument("--poisson-run", required=True, type=Path)
    parser.add_argument("--dc-run", required=True, type=Path)
    parser.add_argument("--poisson-analysis", type=Path)
    parser.add_argument("--dc-analysis", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--raster-seed", type=int, default=3)
    args = parser.parse_args()
    print(render(args.figure, args.poisson_run, args.dc_run, args.output_dir,
                 poisson_analysis=args.poisson_analysis, dc_analysis=args.dc_analysis,
                 raster_seed=args.raster_seed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
