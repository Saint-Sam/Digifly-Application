"""Render the anatomical position panels of Romaro et al. Figure 9.

Only panels A and B are generated. The pinned NetPyNE source makes Y cortical
depth, so its physical top-down plane is X–Z and its side plane is X–Y. The
published Figure 9 caption names different coordinate planes; this command
records that discrepancy and never relabels the underlying coordinates.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Sequence

from digifly_app.workers.pdcm_netpyne_worker import MODEL_COMMIT, POPULATIONS, SOURCE_SHA256


PAPER_URL = "https://pmc.ncbi.nlm.nih.gov/articles/PMC8382011/"
SOURCE_GEOMETRY_URL = (
    "https://github.com/suny-downstate-medical-center/PDCM_NetPyNE/"
    f"blob/{MODEL_COMMIT}/netParams.py#L136-L145"
)
EXPECTED_POPULATION_COUNTS = dict(zip(
    POPULATIONS, (2068, 583, 2191, 547, 485, 106, 1439, 294), strict=True
))
LABELS = {
    "L2e": "L2/3e", "L2i": "L2/3i", "L4e": "L4e", "L4i": "L4i",
    "L5e": "L5e", "L5i": "L5i", "L6e": "L6e", "L6i": "L6i",
}
# High-contrast, color-vision-accessible categorical colors. Marker shape also
# distinguishes excitatory (circle) from inhibitory (triangle) populations.
COLORS = {
    "L2e": "#0072B2", "L2i": "#D55E00", "L4e": "#009E73", "L4i": "#CC79A7",
    "L5e": "#E69F00", "L5i": "#000000", "L6e": "#332288", "L6i": "#AA4499",
}
DEPTH_UM = 1470.0
DIAMETER_UM = 300.0
LAYER_BOUNDARIES_UM = (0.08 * DEPTH_UM, 0.27 * DEPTH_UM, 0.58 * DEPTH_UM, 0.73 * DEPTH_UM)


@dataclass(frozen=True)
class Cell:
    gid: int
    population: str
    x_um: float
    y_um: float
    z_um: float


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_cells(path: Path, *, expected_counts: dict[str, int] | None = None) -> list[Cell]:
    """Read all cortical positions strictly; never subsample or jitter cells."""
    cells: list[Cell] = []
    seen: set[int] = set()
    counts = {pop: 0 for pop in POPULATIONS}
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != ["gid", "population", "x_um", "y_um", "z_um"]:
            raise ValueError("PDCM cells.csv must have the five expected position columns")
        for row in reader:
            gid = int(row["gid"])
            pop = row["population"]
            coordinates = tuple(float(row[key]) for key in ("x_um", "y_um", "z_um"))
            if gid < 0 or gid in seen or pop not in counts:
                raise ValueError(f"Duplicate or unexpected cortical cell: {gid}, {pop}")
            if not all(math.isfinite(value) for value in coordinates):
                raise ValueError(f"Non-finite position for cell {gid}")
            x_um, y_um, z_um = coordinates
            if not (0 <= x_um <= DIAMETER_UM and 0 <= y_um <= DEPTH_UM and 0 <= z_um <= DIAMETER_UM):
                raise ValueError(f"Cell {gid} position is outside the pinned model volume")
            seen.add(gid)
            counts[pop] += 1
            cells.append(Cell(gid, pop, x_um, y_um, z_um))
    if expected_counts is not None and counts != expected_counts:
        raise ValueError(f"Cortical population counts do not match the 10% pinned model: {counts}")
    if seen != set(range(len(cells))):
        raise ValueError("Cortical GIDs must be contiguous from zero")
    return sorted(cells, key=lambda cell: cell.gid)


def _validate_run(run_root: Path) -> tuple[dict, dict, list[Cell], dict[str, str]]:
    paths = {name: run_root / name for name in ("summary.json", "source_manifest.json", "cells.csv")}
    if any(not path.is_file() or path.is_symlink() for path in paths.values()):
        raise ValueError("Choose a Digifly run with regular summary, source manifest and cells.csv files")
    summary = json.loads(paths["summary.json"].read_text(encoding="utf-8"))
    source = json.loads(paths["source_manifest.json"].read_text(encoding="utf-8"))
    metadata = summary.get("metadata", {})
    if summary.get("workflow") != "pdcm_replication_v1" or summary.get("status") != "completed":
        raise ValueError("Choose a completed Digifly PDCM run")
    if metadata.get("model_commit") != MODEL_COMMIT or source.get("model_commit") != MODEL_COMMIT:
        raise ValueError("This run does not use the pinned PDCM point-neuron source")
    source_hashes = source.get("original_sha256")
    if not isinstance(source_hashes, dict) or any(
        source_hashes.get(name) != digest for name, digest in SOURCE_SHA256.items()
    ):
        raise ValueError("Pinned PDCM source hashes do not match")
    if metadata.get("scale") != 0.1 or metadata.get("drive") != "balanced_poisson":
        raise ValueError("Figure 9 positions require the 10% balanced-Poisson condition")
    _timing_class(float(metadata.get("duration_ms", 0)))
    cells = load_cells(paths["cells.csv"], expected_counts=EXPECTED_POPULATION_COUNTS)
    if metadata.get("cortical_cells") != len(cells):
        raise ValueError("PDCM summary cell count disagrees with cells.csv")
    return summary, source, cells, {name: _sha256(path) for name, path in paths.items()}


def _timing_class(duration_ms: float) -> str:
    if duration_ms == 4000:
        return "dedicated_4000_ms"
    if duration_ms >= 60_100:
        return "positions_from_long_run"
    raise ValueError("Figure 9 positions require the dedicated 4000 ms or an >=60100 ms completed run")


def create_figure(cells: Sequence[Cell]):
    """Build physically oriented top and side views without changing cell data."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.patches import Circle

    with plt.rc_context({
        "font.family": "sans-serif",
        "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans"],
        "font.size": 8,
        "axes.labelsize": 9,
        "axes.titlesize": 9,
        "xtick.labelsize": 7,
        "ytick.labelsize": 7,
        "axes.linewidth": 0.6,
        "pdf.fonttype": 42,
    }):
        fig = plt.figure(figsize=(7.15, 6.1), facecolor="white")
        ax_top = fig.add_axes((0.065, 0.42, 0.38, 0.44))
        ax_side = fig.add_axes((0.53, 0.115, 0.19, 0.755))

        ax_top.add_patch(Circle((150, 150), 150, fill=False, linewidth=0.55,
                                edgecolor="#777777", linestyle="--", zorder=0))
        for boundary in LAYER_BOUNDARIES_UM:
            ax_side.axhline(boundary, color="#aaaaaa", linestyle="--", linewidth=0.55, zorder=0)

        by_pop = {pop: [cell for cell in cells if cell.population == pop] for pop in POPULATIONS}
        for pop in POPULATIONS:
            group = by_pop[pop]
            marker = "^" if pop.endswith("i") else "o"
            ax_top.scatter([cell.x_um for cell in group], [cell.z_um for cell in group],
                           s=3.0, marker=marker, color=COLORS[pop], alpha=0.68,
                           edgecolors="none", rasterized=True)
            ax_side.scatter([cell.x_um for cell in group], [cell.y_um for cell in group],
                            s=3.0, marker=marker, color=COLORS[pop], alpha=0.68,
                            edgecolors="none", rasterized=True)

        ax_top.set(xlim=(0, 300), ylim=(0, 300), xlabel="X (µm)", ylabel="Z (µm)")
        ax_top.set_aspect("equal", adjustable="box")
        ax_side.set(xlim=(0, 300), ylim=(1470, 0), xlabel="X (µm)", ylabel="Depth Y (µm)")
        ax_side.set_aspect("equal", adjustable="box")
        ax_top.set_title("Top-down · X–Z plane", pad=7)
        ax_side.set_title("Side · X–Y plane", pad=7)
        for ax, letter in ((ax_top, "A"), (ax_side, "B")):
            ax.text(-0.16, 1.07, letter, transform=ax.transAxes, fontweight="bold", fontsize=11,
                    va="top", ha="left")
            ax.spines["top"].set_visible(False)
            ax.spines["right"].set_visible(False)
            ax.tick_params(direction="out", length=3, width=0.6)

        handles = [Line2D([], [], linestyle="none", marker="^" if pop.endswith("i") else "o",
                          color=COLORS[pop], markersize=5,
                          label=f"{LABELS[pop]}  (n={len(by_pop[pop]):,})") for pop in POPULATIONS]
        fig.legend(handles=handles, loc="center left", bbox_to_anchor=(0.77, 0.59), frameon=False,
                   title="Cortical populations", fontsize=8, title_fontsize=8,
                   handletextpad=0.5, labelspacing=0.9)
        fig.text(0.065, 0.235,
                 "All 7,713 cells · 10% balanced-Poisson model\n"
                 "Source Y=depth; paper caption names different planes.",
                 fontsize=7.4, va="bottom", ha="left", color="#333333")
        return fig, (ax_top, ax_side)


def render(run_root: Path, output_dir: Path) -> Path:
    """Render a new, non-destructive sidecar and hash its exact source files."""
    run_root = run_root.expanduser().resolve(strict=True)
    output_dir = output_dir.expanduser().resolve()
    if output_dir == run_root or output_dir.is_relative_to(run_root):
        raise ValueError("Figure output must be outside the original run")
    if output_dir.exists():
        raise FileExistsError(f"Figure output already exists: {output_dir}")
    summary, source, cells, source_hashes = _validate_run(run_root)
    output_dir.mkdir(parents=True, exist_ok=False)

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, _axes = create_figure(cells)
    png = output_dir / "figure9_positions_ab.png"
    pdf = output_dir / "figure9_positions_ab.pdf"
    try:
        fig.savefig(png, dpi=300, bbox_inches="tight", pad_inches=0.08, facecolor="white")
        fig.savefig(pdf, dpi=300, bbox_inches="tight", pad_inches=0.08, facecolor="white")
    finally:
        plt.close(fig)

    source_after = {name: _sha256(run_root / name) for name in source_hashes}
    if source_after != source_hashes:
        raise RuntimeError("PDCM run source changed during figure generation")
    metadata = summary["metadata"]
    timing_class = _timing_class(float(metadata["duration_ms"]))
    manifest = {
        "schema_version": 1,
        "workflow": "pdcm_figure9_positions_v1",
        "panels": ["9A", "9B"],
        "scientific_status": (
            "Anatomical position panels from the dedicated 4,000 ms Figure 9 simulation; "
            "the initial 100 ms settling period is inside this duration. These panels "
            "alone are not a complete Figure 9 or full-paper replication. Source Y is "
            "cortical depth, unlike the planes named in the published caption."
            if timing_class == "dedicated_4000_ms" else
            "Anatomical position panels only, not a complete Figure 9 replication; "
            "these positions come from a completed 60.1 s run rather than the paper's "
            "dedicated 4 s Figure 9 simulation. Geometry is fixed at network build. "
            "Panels C, D and E are not generated here."
        ),
        "timing_class": timing_class,
        "paper_url": PAPER_URL,
        "source_geometry_url": SOURCE_GEOMETRY_URL,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_run": str(run_root),
        "source_model_commit": source["model_commit"],
        "source_summary": {
            key: metadata[key] for key in
            ("scale", "drive", "duration_ms", "seed", "cortical_cells")
        },
        "source_sha256": source_hashes,
        "source_unchanged_after_render": True,
        "cell_counts": EXPECTED_POPULATION_COUNTS,
        "axis_mapping": {
            "A": {"view": "physical top-down", "horizontal": "x_um", "vertical": "z_um"},
            "B": {"view": "physical side", "horizontal": "x_um", "vertical": "y_um, increasing downward"},
        },
        "coordinate_discrepancy": (
            "The paper's Figure 9 caption says top-down X–Y and side X–Z, "
            "but pinned netParams.py sets sizeY=1470 µm as cortical depth and "
            "sizeX=sizeZ=300 µm as horizontal dimensions. No coordinates were swapped."
        ),
        "plotting": {
            "all_cells_plotted": True,
            "subsampling": None,
            "position_jitter": None,
            "depth_axis_inverted_for_anatomical_orientation": True,
            "raster_dpi": 300,
            "excitatory_marker": "circle",
            "inhibitory_marker": "triangle",
            "palette": COLORS,
        },
        "script_sha256": _sha256(Path(__file__).resolve()),
        "output_sha256": {path.name: _sha256(path) for path in (png, pdf)},
    }
    (output_dir / "figure_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return output_dir


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path, help="Completed 10% balanced-Poisson Digifly PDCM run")
    parser.add_argument("--output-dir", type=Path, required=True, help="New output directory outside the run")
    args = parser.parse_args()
    print(render(args.run, args.output_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
