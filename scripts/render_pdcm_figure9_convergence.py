"""Render paper Figure 9C from a completed, dedicated 10% PDCM MPI run.

The opt-in worker exports only 64 cortical-population aggregates. This script
rechecks those aggregates against all rank shards and never reconstructs or
stores the millions of individual connections.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

from digifly_app.core.pdcm_replication import MODEL_COMMIT, SOURCE_SHA256, validate_prepared_bundle
from digifly_app.workers.pdcm_netpyne_mpi_worker import POPULATIONS


PAPER_URL = "https://pmc.ncbi.nlm.nih.gov/articles/PMC8382011/"
EXPECTED_COUNTS = dict(zip(POPULATIONS, (2068, 583, 2191, 547, 485, 106, 1439, 294), strict=True))
LABELS = {"L2e": "L2/3e", "L2i": "L2/3i", **{pop: pop for pop in POPULATIONS if pop not in {"L2e", "L2i"}}}
COLORS = ("#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#000000", "#332288", "#AA4499")
CONVERGENCE_METHOD_SHA256 = "066fd347431518c1f487c997d48f8c32af4b7d49d1c31f00c0a37f3fe80a25d3"
EXPECTED_WORKER_SHA256 = "5ca57ce1f25d3dcdc2dea0af92161cb17c7a0d2a976163617614800143b831dd"
CSV_COLUMNS = ["pre_population", "post_population", "post_cell_count", "unique_presynaptic_cell_pairs", "mean_convergence_per_post_cell"]
SOURCE_FILES = ("worker_request.json", "source_manifest.json", "run_manifest.json", "summary.json", "mpi_execution.json", "cells.csv", "figure9c_convergence.csv")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _regular_sources(root: Path) -> dict[str, Path]:
    paths = {name: root / name for name in SOURCE_FILES}
    if any(not path.is_file() or path.is_symlink() for path in paths.values()):
        raise ValueError("Figure 9C needs all regular run, execution, cell and convergence files")
    return paths


def _cell_counts(path: Path) -> dict[str, int]:
    counts = {pop: 0 for pop in POPULATIONS}
    gids = set()
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != ["gid", "population", "x_um", "y_um", "z_um"]:
            raise ValueError("PDCM cells.csv columns changed")
        for row in reader:
            gid, pop = int(row["gid"]), row["population"]
            if gid < 0 or gid in gids or pop not in counts:
                raise ValueError("PDCM cells.csv has duplicate or unexpected cortical cells")
            gids.add(gid)
            counts[pop] += 1
    if counts != EXPECTED_COUNTS or gids != set(range(7713)):
        raise ValueError("PDCM 10% cortical cell counts or GIDs changed")
    return counts


def _validated_matrix(root: Path) -> tuple[dict[str, dict[str, float]], dict, dict[str, str]]:
    request = validate_prepared_bundle(root)
    spec = request["spec"]
    if (spec.get("scale"), spec.get("drive"), float(spec.get("duration_ms", 0)), spec.get("seed"), spec.get("export_convergence")) != (0.1, "balanced_poisson", 4000.0, 3, True):
        raise ValueError("Figure 9C needs the dedicated 10%/4000 ms balanced-Poisson seed-3 convergence run")
    paths = _regular_sources(root)
    source_hashes = {name: _sha256(path) for name, path in paths.items()}
    manifest = json.loads(paths["run_manifest.json"].read_text(encoding="utf-8"))
    summary = json.loads(paths["summary.json"].read_text(encoding="utf-8"))
    source = json.loads(paths["source_manifest.json"].read_text(encoding="utf-8"))
    execution = json.loads(paths["mpi_execution.json"].read_text(encoding="utf-8"))
    if manifest.get("state") != "completed" or summary.get("workflow") != "pdcm_replication_v1" or summary.get("status") != "completed":
        raise ValueError("Figure 9C needs a completed PDCM run")
    metadata = summary.get("metadata", {})
    if (metadata.get("scale"), metadata.get("drive"), float(metadata.get("duration_ms", 0)), metadata.get("seed"), metadata.get("cortical_cells"), metadata.get("model_commit")) != (0.1, "balanced_poisson", 4000.0, 3, 7713, MODEL_COMMIT):
        raise ValueError("PDCM Figure 9C summary disagrees with the dedicated run request")
    if source.get("model_commit") != MODEL_COMMIT or any(source.get("original_sha256", {}).get(name) != digest for name, digest in SOURCE_SHA256.items()):
        raise ValueError("Figure 9C source is not the pinned authors' model")
    if execution.get("worker_sha256") != EXPECTED_WORKER_SHA256:
        raise ValueError("Figure 9C needs the SHA-pinned convergence-export MPI worker")
    counts = _cell_counts(paths["cells.csv"])
    acceptance = execution.get("numeric_acceptance", {})
    required = ("all_expected_cortical_cells_present", "all_rank_shards_present_and_intact", "all_recorded_spikes_match_cortical_gids", "rank_spike_counts_match_merged_total", "all_population_rates_finite_nonnegative", "at_least_one_cortical_spike", "convergence_aggregate_complete")
    if any(acceptance.get(flag) is not True for flag in required) or acceptance.get("paper_timed_metrics_available") is not False:
        raise ValueError("Figure 9C run lacks its required numeric acceptance checks")
    metrics = execution.get("rank_metrics")
    if execution.get("rank_count") != 8 or not isinstance(metrics, list) or len(metrics) != 8 or {item.get("rank") for item in metrics} != set(range(8)):
        raise ValueError("Figure 9C needs eight validated MPI rank aggregates")
    export = execution.get("convergence_export", {})
    if (export.get("path") != "figure9c_convergence.csv" or export.get("sha256") != source_hashes["figure9c_convergence.csv"] or export.get("include_pre") != list(POPULATIONS) or export.get("include_post") != list(POPULATIONS) or export.get("external_netstims_included") is not False):
        raise ValueError("Figure 9C convergence export metadata or SHA-256 is invalid")
    if export.get("method") != "NetPyNE plotConn(groupBy='pop', feature='convergence', synOrConn='conn')":
        raise ValueError("Figure 9C convergence method differs from the pinned NetPyNE configuration")
    if not any(item.get("path") == "figure9c_convergence.csv" for item in summary.get("artifacts", [])):
        raise ValueError("Figure 9C convergence table is missing from the Results artifacts")

    totals = {pre: {post: 0 for post in POPULATIONS} for pre in POPULATIONS}
    for item in metrics:
        shard = item.get("convergence_unique_cell_pairs")
        if not isinstance(shard, dict) or set(shard) != set(POPULATIONS):
            raise ValueError("Figure 9C rank aggregate is incomplete")
        for pre in POPULATIONS:
            if not isinstance(shard[pre], dict) or set(shard[pre]) != set(POPULATIONS):
                raise ValueError("Figure 9C rank aggregate is incomplete")
            for post in POPULATIONS:
                value = shard[pre][post]
                if type(value) is not int or value < 0:
                    raise ValueError("Figure 9C rank aggregate has an invalid count")
                totals[pre][post] += value

    matrix = {pre: {} for pre in POPULATIONS}
    with paths["figure9c_convergence.csv"].open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != CSV_COLUMNS:
            raise ValueError("Figure 9C convergence table columns changed")
        rows = list(reader)
    if len(rows) != 64:
        raise ValueError("Figure 9C convergence table must have exactly 64 population pairs")
    for row, (pre, post) in zip(rows, ((pre, post) for pre in POPULATIONS for post in POPULATIONS), strict=True):
        if (row["pre_population"], row["post_population"]) != (pre, post):
            raise ValueError("Figure 9C convergence table population order changed")
        post_count = int(row["post_cell_count"])
        pairs = int(row["unique_presynaptic_cell_pairs"])
        mean = float(row["mean_convergence_per_post_cell"])
        if post_count != counts[post] or pairs != totals[pre][post] or not 0 <= pairs <= counts[pre] * counts[post]:
            raise ValueError("Figure 9C convergence table disagrees with validated rank aggregates")
        if not math.isfinite(mean) or not math.isclose(mean, pairs / post_count, rel_tol=1e-10, abs_tol=1e-10):
            raise ValueError("Figure 9C mean convergence is not the NetPyNE count/post-cell mean")
        matrix[pre][post] = mean
    total_pairs = sum(sum(row.values()) for row in totals.values())
    if total_pairs != export.get("unique_cell_pairs") or total_pairs > execution.get("synaptic_contacts", {}).get("recurrent", -1):
        raise ValueError("Figure 9C total cortical connections disagree with execution metadata")
    return matrix, {"summary": summary, "execution": execution, "total_unique_cell_pairs": total_pairs}, source_hashes


def _figure(matrix: dict[str, dict[str, float]]):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    with plt.rc_context({"font.family": "sans-serif", "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans"], "font.size": 9, "axes.labelsize": 10, "xtick.labelsize": 8, "ytick.labelsize": 8, "pdf.fonttype": 42}):
        fig, ax = plt.subplots(figsize=(8.4, 4.6), constrained_layout=True)
        bottoms = [0.0] * len(POPULATIONS)
        for pre, color in zip(POPULATIONS, COLORS, strict=True):
            heights = [matrix[pre][post] for post in POPULATIONS]
            ax.bar([LABELS[post] for post in POPULATIONS], heights, bottom=bottoms,
                   width=0.72, color=color, edgecolor="white", linewidth=0.25, label=LABELS[pre])
            bottoms = [old + height for old, height in zip(bottoms, heights, strict=True)]
        ax.set(xlabel="Postsynaptic population", ylabel="Mean convergence (cells/post cell)")
        ax.set_ylim(bottom=0)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.tick_params(direction="out", length=3)
        ax.text(-0.08, 1.03, "C", transform=ax.transAxes, fontsize=12, fontweight="bold", va="bottom")
        ax.legend(title="Presynaptic population", ncol=4, loc="upper center", bbox_to_anchor=(0.5, -0.18), frameon=False, fontsize=8, title_fontsize=8)
        return fig


def render(run_root: Path, output_dir: Path) -> Path:
    run_root = run_root.expanduser().resolve(strict=True)
    output_dir = output_dir.expanduser().resolve()
    if output_dir == run_root or output_dir.is_relative_to(run_root):
        raise ValueError("Figure 9C output must be outside the original run")
    if output_dir.exists():
        raise FileExistsError(f"Figure 9C output already exists: {output_dir}")
    matrix, metadata, source_hashes = _validated_matrix(run_root)
    output_dir.mkdir(parents=True, exist_ok=False)
    import matplotlib.pyplot as plt
    fig = _figure(matrix)
    png, pdf = output_dir / "figure9_convergence_c.png", output_dir / "figure9_convergence_c.pdf"
    try:
        fig.savefig(png, dpi=300, bbox_inches="tight", pad_inches=0.08, facecolor="white")
        fig.savefig(pdf, dpi=300, bbox_inches="tight", pad_inches=0.08, facecolor="white")
    finally:
        plt.close(fig)
    source_data = output_dir / "figure9c_source_data.csv"
    with source_data.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(("pre_population", "post_population", "mean_convergence_per_post_cell"))
        for pre in POPULATIONS:
            for post in POPULATIONS:
                writer.writerow((pre, post, f"{matrix[pre][post]:.12g}"))
    caption = output_dir / "caption.txt"
    caption.write_text(
        "Figure 9C reconstruction. Ten-percent balanced-Poisson PDCM, 7,713 cortical cells, "
        "4,000 ms total (including the initial 100 ms settling period), seed 3. "
        "Each stacked segment is the mean number of distinct cells in a presynaptic cortical "
        "population targeting one postsynaptic cell; duplicate synaptic records between a cell "
        "pair count once, as in NetPyNE plotConn(feature='convergence', synOrConn='conn'). "
        "External NetStim cells are excluded. One simulation; no error bars. The pinned source "
        "has a contact/synapse-count discrepancy from the paper that remains unresolved.\n",
        encoding="utf-8",
    )
    source_after = {name: _sha256(run_root / name) for name in source_hashes}
    if source_after != source_hashes:
        raise RuntimeError("PDCM Figure 9C source changed while rendering")
    manifest = {
        "schema_version": 1,
        "workflow": "pdcm_figure9_convergence_v1",
        "panels": ["9C"],
        "paper_url": PAPER_URL,
        "scientific_status": "Dedicated four-second Figure 9C reconstruction from pinned NetPyNE convergence semantics; not a full-paper match because the source/paper contact definition discrepancy is unresolved.",
        "source_run": str(run_root),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_commit": MODEL_COMMIT,
        "source_summary": {key: metadata["summary"]["metadata"][key] for key in ("scale", "drive", "duration_ms", "seed", "cortical_cells", "cortical_spikes")},
        "rank_count": metadata["execution"]["rank_count"],
        "total_unique_cortical_cell_pairs": metadata["total_unique_cell_pairs"],
        "recurrent_contact_records": metadata["execution"]["synaptic_contacts"]["recurrent"],
        "method": "NetPyNE 1.1.1 plotConn(groupBy='pop', feature='convergence', synOrConn='conn', graphType='bar')",
        "netpyne_analysis_network_py_sha256": CONVERGENCE_METHOD_SHA256,
        "convergence_export_worker_sha256": EXPECTED_WORKER_SHA256,
        "source_sha256": source_hashes,
        "source_unchanged_after_render": True,
        "script_sha256": _sha256(Path(__file__).resolve()),
        "output_sha256": {path.name: _sha256(path) for path in (png, pdf, source_data, caption)},
    }
    (output_dir / "figure_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output_dir


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path, help="Completed dedicated 10%/4000 ms PDCM convergence run")
    parser.add_argument("--output-dir", required=True, type=Path, help="New output directory outside the run")
    args = parser.parse_args()
    print(render(args.run, args.output_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
