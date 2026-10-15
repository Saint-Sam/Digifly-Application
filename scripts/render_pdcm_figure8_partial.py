"""Render an explicitly incomplete Figure 8 condition grid from verified PDCM runs.

Only supplied, completed balanced-Poisson/DC simulations are plotted. Every
unsupplied scale remains a missing row in the source data and a gray band in
the figure. This is an audit/visualization of Digifly measurements, not a claim
that the complete Romaro et al. Figure 8 has been reproduced.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path

from digifly_app.workers.pdcm_netpyne_worker import MODEL_COMMIT, POPULATIONS, SOURCE_SHA256


PAPER_URL = "https://pmc.ncbi.nlm.nih.gov/articles/PMC8382011/"
SCALES_PERCENT = (1, 2, 5, 10, 20, 30, 40, 50, 60, 80, 100)
DRIVES = ("balanced_poisson", "balanced_dc")
DRIVE_LABELS = {"balanced_poisson": "Balanced Poisson", "balanced_dc": "Balanced DC"}
# Romaro et al. report no firing below 10% under balanced DC (Results 3.2).
# Those three scale/drive combinations are useful diagnostic observations, but
# are not part of the paper's active Figure 8 comparison grid.
PAPER_REQUIRED = frozenset((scale, drive) for scale in SCALES_PERCENT for drive in DRIVES
                           if drive == "balanced_poisson" or scale >= 10)
PAPER_EXCLUDED = frozenset((scale, "balanced_dc") for scale in (1, 2, 5))
LABELS = {"L2e": "L2/3e", "L2i": "L2/3i", "L4e": "L4e", "L4i": "L4i",
          "L5e": "L5e", "L5i": "L5i", "L6e": "L6e", "L6i": "L6i"}
COLORS = ("#0072B2", "#56B4E9", "#E69F00", "#D55E00",
          "#009E73", "#A68C00", "#CC79A7", "#444444")
# The Athene 1% run predates mandatory v2 sidecars. Its preserved MPI worker
# explicitly restricts correlation to [100, 5100) ms. Reject other raw-worker
# revisions rather than trusting a column name that an earlier local worker
# used for an incorrect 60-second calculation.
VERIFIED_RAW_WORKER_SHA256 = frozenset({
    "9c6ab69b7aabfca78fab33600e8eb42a5938a2efeb87079d5de4e0b52006faae",
})
SOURCE_FIELDS = (
    "scale_percent", "drive", "population", "status", "paper_scope", "run_name", "seed",
    "sampled_cells", "correlation_defined_cells", "rate_hz_60s", "cv_isi_60s",
    "population_total_fano_3ms_60s", "per_cell_normalized_synchrony_3ms_60s",
    "mean_signed_cross_correlation_25ms_5s",
    "mean_absolute_cross_correlation_25ms_5s",
)


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


def _rows(path: Path) -> dict[str, dict[str, str]]:
    with _regular(path).open(newline="", encoding="utf-8") as stream:
        values = list(csv.DictReader(stream))
    mapped = {row["population"]: row for row in values}
    if len(values) != len(POPULATIONS) or set(mapped) != set(POPULATIONS):
        raise ValueError(f"Expected exactly eight PDCM populations in {path}")
    return mapped


def _cells(path: Path) -> dict[str, set[int]]:
    by_pop = {pop: set() for pop in POPULATIONS}
    all_gids: set[int] = set()
    with _regular(path).open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            gid, pop = int(row["gid"]), row["population"]
            if pop not in by_pop or gid < 0 or gid in all_gids:
                raise ValueError(f"Invalid or duplicate PDCM cell: {gid}, {pop}")
            by_pop[pop].add(gid)
            all_gids.add(gid)
    if any(not gids for gids in by_pop.values()):
        raise ValueError("The run must contain every PDCM cortical population")
    return by_pop


def _number(row: dict[str, str], field: str, pop: str, *, signed: bool = False) -> float:
    value = float(row[field])
    if not math.isfinite(value) or (not signed and value < 0):
        raise ValueError(f"Invalid {field} for {pop}")
    return value


def _optional_number(row: dict[str, str], field: str, pop: str,
                     *, signed: bool = False) -> float | None:
    """A blank metric is undefined, never a numerical zero."""
    if row[field] == "":
        return None
    return _number(row, field, pop, signed=signed)


def _count_analysis_spikes(path: Path, by_pop: dict[str, set[int]],
                           duration_ms: float) -> tuple[int, int]:
    """Independently confirm a zero-rate sampled run is silent across *all* cells."""
    total = 0
    in_window = 0
    with _regular(path).open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            time_ms = float(row["time_ms"])
            gid, pop = int(row["gid"]), row["population"]
            if (not math.isfinite(time_ms) or time_ms < 0 or time_ms > duration_ms
                    or pop not in by_pop or gid not in by_pop[pop]):
                raise ValueError(f"Invalid cortical spike in {path}")
            total += 1
            if 100 <= time_ms < 60_100:
                in_window += 1
    return total, in_window


def _load_condition(scale: int, drive: str, run_root: Path, analysis_root: Path | None) -> dict:
    if scale not in SCALES_PERCENT or drive not in DRIVES:
        raise ValueError("Figure 8 condition is outside the published scale/drive grid")
    run = run_root.expanduser().resolve(strict=True)
    summary_path = _regular(run / "summary.json")
    provenance_path = _regular(run / "source_manifest.json")
    cells_path = _regular(run / "cells.csv")
    spikes_path = _regular(run / "spikes.csv")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    metadata = summary.get("metadata", {})
    if summary.get("workflow") != "pdcm_replication_v1" or summary.get("status") != "completed":
        raise ValueError("Choose a completed Digifly PDCM run")
    if (metadata.get("model_commit") != MODEL_COMMIT or metadata.get("drive") != drive
            or not math.isclose(float(metadata["scale"]), scale / 100, abs_tol=1e-9)
            or float(metadata["duration_ms"]) < 60_100):
        raise ValueError("Run does not match the requested Figure 8 scale, drive, duration or model")
    source = json.loads(provenance_path.read_text(encoding="utf-8"))
    if (source.get("model_commit") != MODEL_COMMIT or
            any(source.get("original_sha256", {}).get(name) != value
                for name, value in SOURCE_SHA256.items())):
        raise ValueError("Run source differs from the pinned PDCM point-neuron model")
    by_pop = _cells(cells_path)
    if sum(map(len, by_pop.values())) != int(metadata["cortical_cells"]):
        raise ValueError("Cell count differs from the completed run summary")
    source_hashes = {path.name: _digest(path) for path in
                     (summary_path, provenance_path, cells_path, spikes_path)}

    if analysis_root is None:
        if not metadata.get("paper_timed_metrics_available"):
            raise ValueError("Raw run does not declare paper-timed metrics")
        worker_path = _regular(run / "provenance" / "pdcm_netpyne_mpi_worker.py")
        worker_hash = _digest(worker_path)
        if worker_hash not in VERIFIED_RAW_WORKER_SHA256:
            raise ValueError("Raw run worker is not a verified 5-second-correlation revision; use a v2 sidecar")
        analysis = run
        metrics_path = _regular(run / "paper_metrics.csv")
        sample_path = _regular(run / "sampling_manifest.json")
        comparison = None
        analysis_kind = "run_worker_paper_metrics"
        analysis_hashes = {path.name: _digest(path) for path in
                           (worker_path, metrics_path, sample_path)}
    else:
        analysis = analysis_root.expanduser().resolve(strict=True)
        manifest_path = _regular(analysis / "analysis_manifest.json")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("workflow") != "pdcm_paper_window_analysis_v2":
            raise ValueError("Figure 8 sidecar must use paper-window analysis v2")
        for name in ("summary.json", "cells.csv", "spikes.csv"):
            if manifest.get("source_sha256", {}).get(name) != source_hashes[name]:
                raise ValueError(f"Analysis source hash mismatch: {name}")
        if (manifest.get("analysis_start_ms") != 100 or
                manifest.get("analysis_end_ms") != 60_100 or
                manifest.get("correlation_end_ms") != 5100):
            raise ValueError("Analysis windows differ from the paper workflow")
        metrics_path = _regular(analysis / "paper_metrics.csv")
        sample_path = _regular(analysis / "sampling_manifest.json")
        comparison_path = _regular(analysis / "synchrony_metric_comparison.csv")
        for path in (metrics_path, sample_path, comparison_path):
            if manifest.get("analysis_sha256", {}).get(path.name) != _digest(path):
                raise ValueError(f"Analysis artifact hash mismatch: {path.name}")
        comparison = _rows(comparison_path)
        analysis_kind = "paper_windows_v2"
        analysis_hashes = {path.name: _digest(path) for path in
                           (manifest_path, metrics_path, sample_path, comparison_path)}

    metrics = _rows(metrics_path)
    samples = json.loads(sample_path.read_text(encoding="utf-8")).get("cell_ids", {})
    if set(samples) != set(POPULATIONS):
        raise ValueError("Sampling manifest lacks a cortical population")
    output_metrics: dict[str, dict] = {}
    for pop in POPULATIONS:
        row = metrics[pop]
        ids = samples[pop]
        count = int(row["sampled_cells"])
        if (count < 1 or len(ids) != count or len(set(ids)) != count or
                not set(ids).issubset(by_pop[pop])):
            raise ValueError(f"Sample IDs/count mismatch for {pop}")
        rate = _number(row, "mean_rate_hz_60s", pop)
        cv = _optional_number(row, "mean_cv_isi_60s", pop)
        signed = _optional_number(row, "mean_signed_cross_correlation_25ms_5s", pop,
                                  signed=True)
        absolute = _optional_number(row, "mean_absolute_cross_correlation_25ms_5s", pop)
        if (signed is None) != (absolute is None):
            raise ValueError(f"Signed/absolute correlation definition differs for {pop}")
        if signed is not None and (not -1 <= signed <= 1 or not 0 <= absolute <= 1
                                   or absolute + 1e-8 < abs(signed)):
            raise ValueError(f"Signed/absolute correlation inconsistent for {pop}")
        if comparison is None:
            normalized = _optional_number(row, "synchrony_3ms_60s", pop)
            fano = normalized * count if normalized is not None else None
        else:
            sync_row = comparison[pop]
            if int(sync_row["sampled_cells"]) != count:
                raise ValueError(f"Synchrony sample size differs for {pop}")
            normalized = _optional_number(sync_row, "per_cell_normalized_synchrony_3ms_60s", pop)
            fano = _optional_number(sync_row, "population_total_fano_synchrony_3ms_60s", pop)
            if (normalized is None) != (fano is None):
                raise ValueError(f"Synchrony definitions differ for {pop}")
            if fano is not None and not math.isclose(
                    fano, normalized * count, rel_tol=2e-7, abs_tol=2e-7):
                raise ValueError(f"Synchrony normalizations disagree for {pop}")
        defined = int(row["correlation_defined_cells"])
        if not 0 <= defined <= count:
            raise ValueError(f"Invalid correlation-defined cell count for {pop}")
        if (defined < 2) != (signed is None):
            raise ValueError(f"Correlation value/count definition differs for {pop}")
        if rate == 0 and (cv is not None or normalized is not None or defined != 0):
            raise ValueError(f"Zero-rate population has defined spike metrics for {pop}")
        if rate > 0 and normalized is None:
            raise ValueError(f"Active population lacks 60-second synchrony for {pop}")
        output_metrics[pop] = {
            "sampled_cells": count,
            "correlation_defined_cells": defined,
            "rate_hz_60s": rate,
            "cv_isi_60s": cv,
            "population_total_fano_3ms_60s": fano,
            "per_cell_normalized_synchrony_3ms_60s": normalized,
            "mean_signed_cross_correlation_25ms_5s": signed,
            "mean_absolute_cross_correlation_25ms_5s": absolute,
        }
    observed_analysis_spikes = None
    observation_status = "measured"
    if all(metric["rate_hz_60s"] == 0 for metric in output_metrics.values()):
        total_spikes, observed_analysis_spikes = _count_analysis_spikes(
            spikes_path, by_pop, float(metadata["duration_ms"])
        )
        if (metadata.get("cortical_spikes") is not None and
                total_spikes != int(metadata["cortical_spikes"])):
            raise ValueError("Spike file count differs from the completed run summary")
        observation_status = ("measured_silent" if observed_analysis_spikes == 0
                              else "measured_zero_sample_rates")
    return {
        "scale_percent": scale, "drive": drive, "run": run, "analysis": analysis,
        "analysis_kind": analysis_kind, "seed": int(metadata["seed"]),
        "observation_status": observation_status,
        "observed_analysis_spikes": observed_analysis_spikes,
        "model_commit": MODEL_COMMIT, "cortical_cells": int(metadata["cortical_cells"]),
        "duration_ms": float(metadata["duration_ms"]), "metrics": output_metrics,
        "source_sha256": source_hashes, "analysis_sha256": analysis_hashes,
    }


def _source_rows(conditions: dict[tuple[int, str], dict]) -> list[dict]:
    rows = []
    for scale in SCALES_PERCENT:
        for drive in DRIVES:
            condition = conditions.get((scale, drive))
            for pop in POPULATIONS:
                row = {field: "" for field in SOURCE_FIELDS}
                paper_scope = "required" if (scale, drive) in PAPER_REQUIRED else "exploratory"
                status = (condition["observation_status"] if condition else
                          "missing" if paper_scope == "required" else "paper_excluded_unrun")
                row.update({"scale_percent": scale, "drive": drive, "population": pop,
                            "status": status, "paper_scope": paper_scope})
                if condition:
                    row.update({"run_name": condition["run"].name,
                                "seed": condition["seed"], **condition["metrics"][pop]})
                rows.append(row)
    return rows


def _style_axis(ax) -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(direction="out", length=2.5, width=0.7)
    ax.set_axisbelow(True)
    ax.grid(axis="y", linewidth=0.4, alpha=0.2)


def _plot(conditions: dict[tuple[int, str], dict], destination: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    plt.rcParams.update({
        "font.family": "sans-serif", "font.size": 8.5, "axes.labelsize": 9,
        "axes.titlesize": 9, "xtick.labelsize": 7.5, "ytick.labelsize": 7.5,
        "pdf.fonttype": 42, "svg.fonttype": "none",
    })
    fig, axes = plt.subplots(4, 2, figsize=(12.8, 10.2), layout="constrained",
                             sharex=True, gridspec_kw={"wspace": 0.10, "hspace": 0.08})
    keys = (
        ("rate_hz_60s", "Mean firing rate · 60 s", "Rate (Hz)"),
        ("cv_isi_60s", "Spike-train irregularity · 60 s", "CV of ISI"),
        ("population_total_fano_3ms_60s", "Population synchrony · 60 s", "3 ms-bin total-count Fano"),
        ("mean_absolute_cross_correlation_25ms_5s", "Cross-correlation · 5 s", "Mean pairwise correlation"),
    )
    for col, drive in enumerate(DRIVES):
        present = sum((scale, drive) in conditions for scale in SCALES_PERCENT
                      if (scale, drive) in PAPER_REQUIRED)
        required = sum((scale, drive) in PAPER_REQUIRED for scale in SCALES_PERCENT)
        exploratory = sum((scale, drive) in conditions for scale in SCALES_PERCENT
                          if (scale, drive) in PAPER_EXCLUDED)
        for row_idx, (key, title, ylabel) in enumerate(keys):
            ax = axes[row_idx, col]
            for index, scale in enumerate(SCALES_PERCENT):
                condition = conditions.get((scale, drive))
                if condition and condition["observation_status"] == "measured_silent":
                    ax.axvspan(index - 0.44, index + 0.44, color="#FFF0D9", zorder=0)
                    if row_idx > 0:
                        ax.text(index, 0.08, "NA", transform=ax.get_xaxis_transform(),
                                ha="center", va="bottom", fontsize=7, color="#865900")
                elif condition is None and (scale, drive) in PAPER_EXCLUDED:
                    ax.axvspan(index - 0.44, index + 0.44, facecolor="#ECF3F8",
                               edgecolor="#C5D5DF", hatch="///", linewidth=0, zorder=0)
                elif condition is None:
                    ax.axvspan(index - 0.44, index + 0.44, color="#F2F2F2", zorder=0)
            for pop, color in zip(POPULATIONS, COLORS, strict=True):
                xs = [index for index, scale in enumerate(SCALES_PERCENT)
                      if (scale, drive) in conditions
                      and conditions[(scale, drive)]["metrics"][pop][key] is not None]
                ys = [conditions[(SCALES_PERCENT[index], drive)]["metrics"][pop][key]
                      for index in xs]
                if xs:
                    ax.scatter(xs, ys, marker="o", s=22, color=color,
                               edgecolor="#222222", linewidth=0.35, zorder=3)
                if row_idx == 3 and xs:
                    signed_points = [(index, conditions[(SCALES_PERCENT[index], drive)]
                                      ["metrics"][pop]["mean_signed_cross_correlation_25ms_5s"])
                                     for index in xs]
                    signed_points = [(index, value) for index, value in signed_points
                                     if value is not None]
                    ax.scatter([index for index, _ in signed_points],
                               [value for _, value in signed_points], marker="x", s=26, color=color,
                               linewidth=1.0, zorder=4)
            ax.set(xlim=(-0.6, len(SCALES_PERCENT) - 0.4), ylabel=ylabel,
                   title=f"{DRIVE_LABELS[drive]} · {title}" if row_idx == 0 else title)
            if row_idx == 0:
                scope_note = f"{present}/{required} paper scales available"
                if exploratory:
                    scope_note += f" · {exploratory} exploratory"
                ax.text(0.99, 0.98, scope_note,
                        transform=ax.transAxes, ha="right", va="top", fontsize=8,
                        color="#555555")
            ax.text(-0.08, 1.055, chr(ord("A") + row_idx + 4 * col),
                    transform=ax.transAxes, ha="left", va="top",
                    fontsize=10, fontweight="bold")
            _style_axis(ax)
        axes[-1, col].set_xticks(range(len(SCALES_PERCENT)),
                                 [str(scale) for scale in SCALES_PERCENT])
        axes[-1, col].set_xlabel("Network scale (% of full model; categorical grid)")
    for row_idx in range(3):
        limits = [axes[row_idx, col].get_ylim() for col in range(2)]
        upper = max(hi for _, hi in limits)
        for col in range(2):
            axes[row_idx, col].set_ylim(0, upper)
    corr_values = [metric[key] for condition in conditions.values()
                   for metric in condition["metrics"].values()
                   for key in ("mean_signed_cross_correlation_25ms_5s",
                               "mean_absolute_cross_correlation_25ms_5s")
                   if metric[key] is not None]
    corr_top = max(corr_values, default=0.1)
    corr_bottom = min(0, min(corr_values, default=0.0))
    for col in range(2):
        axes[3, col].set_ylim(corr_bottom - 0.008, corr_top * 1.13 + 0.005)
        axes[3, col].axhline(0, color="#777777", linewidth=0.55, zorder=1)
    population_handles = [Line2D([0], [0], color=color, marker="o", linestyle="none",
                                 markersize=5, label=LABELS[pop])
                          for pop, color in zip(POPULATIONS, COLORS, strict=True)]
    mark_handles = [Line2D([0], [0], color="#333333", marker="o", linestyle="none",
                           markersize=5, label="Mean absolute r"),
                    Line2D([0], [0], color="#333333", marker="x", linestyle="none",
                           markersize=5, label="Mean signed r")]
    scope_handles = [Patch(facecolor="#F2F2F2", label="Required, not run"),
                     Patch(facecolor="#ECF3F8", edgecolor="#C5D5DF", hatch="///",
                           label="Below 10% DC, not run"),
                     Patch(facecolor="#FFF0D9", label="Observed silent; NA metrics")]
    fig.legend(handles=population_handles + mark_handles + scope_handles,
               ncol=7, loc="lower center", frameon=False,
               bbox_to_anchor=(0.5, -0.042), fontsize=7.4)
    fig.suptitle("Digifly PDCM · Figure 8 condition audit (partial, single seed)",
                 fontsize=12, fontweight="bold")
    fig.savefig(destination / "figure8_partial.png", dpi=300, facecolor="white",
                bbox_inches="tight", pad_inches=0.16)
    fig.savefig(destination / "figure8_partial.pdf", facecolor="white",
                bbox_inches="tight", pad_inches=0.16)
    plt.close(fig)


def render(specs: list[tuple[int, str, Path, Path | None]], output_dir: Path) -> Path:
    if not specs:
        raise ValueError("Supply at least one completed Figure 8 condition")
    output = output_dir.expanduser().resolve()
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"Figure output already exists: {output}")
    conditions: dict[tuple[int, str], dict] = {}
    for scale, drive, run, analysis in specs:
        key = (scale, drive)
        if key in conditions:
            raise ValueError(f"Duplicate Figure 8 condition: {key}")
        condition = _load_condition(scale, drive, run, analysis)
        if output == condition["run"] or condition["run"] in output.parents:
            raise ValueError("Figure output must not be inside a simulation run")
        if output == condition["analysis"] or condition["analysis"] in output.parents:
            raise ValueError("Figure output must not be inside an analysis sidecar")
        conditions[key] = condition
    rows = _source_rows(conditions)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir(exist_ok=False)
    data_path = output / "figure8_source_data.csv"
    with data_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=SOURCE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    _plot(conditions, output)
    missing = [{"scale_percent": scale, "drive": drive} for scale, drive in
               sorted(PAPER_REQUIRED) if (scale, drive) not in conditions]
    excluded = [{"scale_percent": scale, "drive": drive,
                 "status": (conditions[(scale, drive)]["observation_status"]
                            if (scale, drive) in conditions else "not_simulated")}
                for scale, drive in sorted(PAPER_EXCLUDED)]
    required_available = sum(key in PAPER_REQUIRED for key in conditions)
    caption = (
        "Partial condition audit for Figure 8 of Romaro et al. (2021), not a complete "
        "reproduction. The paper's active comparison includes 11 balanced-Poisson scales "
        "(1–100%) and eight balanced-DC scales (10–100%): 19 scale/drive conditions. "
        "Romaro et al. report no firing for DC below 10%; the 1%, 2%, and 5% DC slots "
        "are separately labeled exploratory, not counted as missing paper conditions. "
        "Only completed balanced-drive PDCM runs are shown. Gray bands mark missing "
        "paper-required simulations; hatched pale-blue bands mark unrun exploratory "
        "sub-10% DC slots. Amber bands mark an observed silent condition. Its zero "
        "firing rates are measured zeros; CV, Fano synchrony, and Pearson correlation "
        "are mathematically undefined and left blank in the CSV (NA in the plot), not "
        "imputed as zero. "
        "All points are one simulation seed each, so no across-seed uncertainty bars "
        "or inferential comparisons are claimed. Mean rate and CV ISI use the 100–60,100 "
        "ms window; synchrony uses 3 ms bins over that same 60 s and is plotted as "
        "population-total count Fano. The printed equation's per-cell-normalized value "
        "is retained separately in the CSV. Cross-correlation uses 25 ms bins over "
        "100–5,100 ms and all valid pairs among the recorded sample, not necessarily "
        "all cortical cells. Filled circles show mean absolute Pearson r, x marks mean "
        "signed r. The paper's printed signed equation and numerical tables disagree "
        "on this distinction; neither interpretation is silently chosen as validated. "
        "Digifly's deterministic sampled cell IDs need not match the authors' IDs. "
        "Scales use categorical spacing solely to expose missing grid positions. "
        f"Paper source: {PAPER_URL} (Results 3.2 and Figure 8).\n"
    )
    (output / "caption.txt").write_text(caption, encoding="utf-8")
    manifest = {
        "schema_version": 2,
        "workflow": "pdcm_figure8_partial_condition_audit_v2",
        "paper_reference": PAPER_URL,
        "paper_scope_basis": "Results 3.2: balanced DC below 10% has no firing activity; Figure 8 and supplementary active DC comparisons start at 10%.",
        "paper_scale_grid_percent": list(SCALES_PERCENT),
        "drive_grid": list(DRIVES),
        "paper_required_condition_grid": [
            {"scale_percent": scale, "drive": drive} for scale, drive in sorted(PAPER_REQUIRED)
        ],
        "present_conditions": [
            {
                "scale_percent": scale, "drive": drive, "run": str(condition["run"]),
                "analysis": str(condition["analysis"]), "analysis_kind": condition["analysis_kind"],
                "seed": condition["seed"], "cortical_cells": condition["cortical_cells"],
                "duration_ms": condition["duration_ms"],
                "paper_scope": ("required" if (scale, drive) in PAPER_REQUIRED else "exploratory"),
                "observation_status": condition["observation_status"],
                "observed_analysis_spikes": condition["observed_analysis_spikes"],
                "source_sha256": condition["source_sha256"],
                "analysis_sha256": condition["analysis_sha256"],
                "sampled_cell_counts": {pop: condition["metrics"][pop]["sampled_cells"]
                                        for pop in POPULATIONS},
            }
            for (scale, drive), condition in sorted(conditions.items())
        ],
        "missing_conditions": missing,
        "paper_excluded_conditions": excluded,
        "available_condition_count": len(conditions),
        "paper_required_available_count": required_available,
        "required_condition_count": len(PAPER_REQUIRED),
        "audit_grid_condition_count": len(SCALES_PERCENT) * len(DRIVES),
        "exploratory_available_count": len(conditions) - required_available,
        "metric_windows_ms": {"rate_cv_synchrony": [100, 60100],
                              "correlation": [100, 5100]},
        "bin_width_ms": {"synchrony": 3, "correlation": 25},
        "renderer_sha256": _digest(Path(__file__).resolve()),
        "output_sha256": {path.name: _digest(path) for path in
                          (data_path, output / "figure8_partial.png",
                           output / "figure8_partial.pdf", output / "caption.txt")},
        "note": "Measured single-seed points only; no source run or analysis sidecar was modified.",
    }
    (output / "figure_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--condition", nargs=4, action="append", required=True,
                        metavar=("SCALE_PERCENT", "DRIVE", "RUN_ROOT", "ANALYSIS_OR_RAW"),
                        help="Repeat for each completed condition; use raw for in-run paper metrics")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    specs = []
    for scale_text, drive, run_text, analysis_text in args.condition:
        specs.append((int(scale_text), drive, Path(run_text),
                      None if analysis_text == "raw" else Path(analysis_text)))
    print(render(specs, args.output_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
