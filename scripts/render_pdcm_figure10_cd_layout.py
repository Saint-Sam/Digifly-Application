#!/usr/bin/env python3
"""Render separate Figure 10 C/D presentation candidates from validated NPZ sidecars.

This changes only the visual layout: C plots true overlaid soma voltages, and D
shows cellwise firing-rate distributions. It is not a scientific acceptance test.
The existing paper-method figures, source sidecars, and simulation runs are read
only; output must be a new directory.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


SCHEMA = "digifly-pdcm-figure10-cd-layout-v2"
SOURCE_SCHEMA = "digifly-pdcm-figure10-paper-methods-v1"
PAPER_URL = "https://pmc.ncbi.nlm.nih.gov/articles/PMC8382011/"
SOURCE_NAME = "figure10_paper_method_source_data.npz"
LABELS = ("L2/3e", "L2/3i", "L4e", "L4i", "L5e", "L5i", "L6e", "L6i")
COLORS = ("#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#56B4E9", "#7B6AA5", "#595959")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def load_validated_source(source_dir: Path) -> tuple[dict, dict[str, np.ndarray]]:
    """Accept only intact, provisional, 1%/600-ms paper-method sidecars."""
    manifest_path = source_dir / "figure_manifest.json"
    npz_path = source_dir / SOURCE_NAME
    require(manifest_path.is_file() and npz_path.is_file(), "Missing Figure 10 paper-method source")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(manifest.get("schema_version") == SOURCE_SCHEMA, "Wrong source schema")
    require(manifest.get("status") == "provisional_method_aligned_not_paper_accepted", "Source status differs")
    require(manifest.get("scientific_acceptance") is False, "Source claims scientific acceptance")
    condition = manifest.get("condition", {})
    require(type(condition.get("TH")) is bool, "Source TH condition missing")
    require(
        condition.get("scale") == 0.01
        and condition.get("duration_ms") == 600
        and condition.get("balanced_poisson") is True,
        "Source is not the 1%/600-ms balanced-Poisson condition",
    )
    record = manifest.get("artifacts", {}).get(SOURCE_NAME, {})
    require(record.get("bytes") == npz_path.stat().st_size, "NPZ byte count differs")
    require(record.get("sha256") == sha256(npz_path), "NPZ checksum differs")
    with np.load(npz_path, allow_pickle=False) as source:
        arrays = {key: np.array(source[key]) for key in (
            "time_ms", "selected_soma_mv", "selected_gids", "cell_rate_hz", "pop_boundaries"
        )}
    validate_arrays(arrays)
    return manifest, arrays


def validate_arrays(arrays: dict[str, np.ndarray]) -> None:
    time = arrays["time_ms"]
    voltage = arrays["selected_soma_mv"]
    gids = arrays["selected_gids"]
    rates = arrays["cell_rate_hz"]
    boundaries = arrays["pop_boundaries"]
    require(time.ndim == 1 and len(time) >= 2 and np.all(np.diff(time) > 0), "Invalid time axis")
    require(np.isclose(time[0], 100.0, atol=1e-6) and time[-1] < 600.0, "Wrong analysis window")
    require(voltage.shape == (8, len(time)), "Expected eight unshifted voltage traces")
    require(gids.shape == (8,) and len(np.unique(gids)) == 8, "Expected eight distinct selected gids")
    require(rates.ndim == 1 and len(rates) == 767 and np.all(rates >= 0), "Expected 767 nonnegative rates")
    require(boundaries.shape == (8,) and boundaries[-1] == len(rates), "Wrong population boundaries")
    require(np.all(np.diff(np.r_[0, boundaries]) > 0), "Empty/unsorted population boundaries")
    require(all(np.isfinite(item).all() for item in (time, voltage, rates)), "Nonfinite source values")


def population_rates(arrays: dict[str, np.ndarray]) -> list[np.ndarray]:
    bounds = np.r_[0, arrays["pop_boundaries"]]
    return [arrays["cell_rate_hz"][bounds[index]:bounds[index + 1]] for index in range(8)]


def rate_summary(arrays: dict[str, np.ndarray]) -> list[dict]:
    rows = []
    for label, values in zip(LABELS, population_rates(arrays)):
        rows.append({
            "population": label,
            "n_cells": len(values),
            "mean_hz": float(np.mean(values)),
            "median_hz": float(np.median(values)),
            "q1_hz": float(np.percentile(values, 25)),
            "q3_hz": float(np.percentile(values, 75)),
            "min_hz": float(np.min(values)),
            "max_hz": float(np.max(values)),
        })
    return rows


def draw_cd(arrays: dict[str, np.ndarray], destination: Path, *, thalamic_input: bool) -> None:
    plt.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 9,
        "axes.labelsize": 9, "xtick.labelsize": 8, "ytick.labelsize": 8,
        "pdf.fonttype": 42,
    })
    fig, (ax_c, ax_d) = plt.subplots(1, 2, figsize=(12.0, 4.65), layout="constrained")
    time = arrays["time_ms"]
    for index, label in enumerate(LABELS):
        ax_c.plot(time, arrays["selected_soma_mv"][index], color=COLORS[index], lw=0.85,
                  alpha=0.88, label=f"{label} · gid {int(arrays['selected_gids'][index])}")
    ax_c.set(xlim=(100, 600), xlabel="Time (ms)", ylabel="Soma membrane voltage (mV)")
    ax_c.set_ylim(-100, 40)
    ax_c.grid(axis="y", color="#dadde2", lw=0.45)
    ax_c.legend(loc="upper center", bbox_to_anchor=(0.5, 0.995), ncol=4,
                frameon=True, facecolor="white", edgecolor="none", fontsize=6.5,
                columnspacing=0.65, handlelength=1.5)
    ax_c.set_title("C  ·  Example soma voltage traces", loc="left", fontweight="bold")

    distributions = population_rates(arrays)
    boxes = ax_d.boxplot(distributions, positions=np.arange(1, 9), widths=0.59,
                         patch_artist=True, showfliers=False,
                         medianprops={"color": "#1b1d21", "linewidth": 1.25},
                         whiskerprops={"color": "#52565c", "linewidth": 0.85},
                         capprops={"color": "#52565c", "linewidth": 0.85})
    for box, color in zip(boxes["boxes"], COLORS):
        box.set(facecolor=color, edgecolor=color, alpha=0.34, linewidth=1.0)
    # All 767 cell values remain visible; fixed jitter is deterministic and
    # changes only horizontal placement, never the firing-rate values.
    for index, values in enumerate(distributions):
        x = index + 1 + 0.32 * (np.mod(np.arange(len(values)) * 0.61803398875, 1.0) - 0.5)
        ax_d.scatter(x, values, s=5.2, alpha=0.34, color=COLORS[index],
                     edgecolors="none", rasterized=True, zorder=3)
    ax_d.set(xlim=(0.55, 8.45), ylim=(0, max(18, float(np.max(arrays["cell_rate_hz"])) * 1.08)),
             ylabel="Cell firing rate (Hz)")
    ax_d.set_xticks(np.arange(1, 9), LABELS, rotation=40, ha="right")
    ax_d.grid(axis="y", color="#dadde2", lw=0.45)
    ax_d.set_axisbelow(True)
    ax_d.set_title("D  ·  Population firing-rate distributions", loc="left", fontweight="bold")
    ax_d.text(0.98, 0.97, "Each point = one cell · box = cellwise quartiles",
              transform=ax_d.transAxes, ha="right", va="top", fontsize=7.3,
              color="#4b515b", bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.8})
    condition = "TH on" if thalamic_input else "TH off"
    fig.suptitle(f"PDCM Figure 10 C/D · 1% scale · 100–600 ms · {condition}",
                 fontsize=11, fontweight="bold")
    fig.savefig(destination / "figure10_cd_layout.png", dpi=300, facecolor="white")
    fig.savefig(destination / "figure10_cd_layout.pdf", facecolor="white")
    plt.close(fig)


def render(source_dir: Path, output: Path) -> dict:
    source_dir = source_dir.resolve()
    output = output.resolve()
    require(output != source_dir and source_dir not in output.parents, "Output cannot be inside the source")
    require(not output.exists(), f"Output already exists: {output}")
    manifest, arrays = load_validated_source(source_dir)
    condition = manifest["condition"]
    output.mkdir(parents=True, exist_ok=False)
    draw_cd(arrays, output, thalamic_input=condition["TH"])
    np.savez_compressed(output / "figure10_cd_source_data.npz", **arrays)
    rows = rate_summary(arrays)
    with (output / "population_rate_summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    caption = (
        "Presentation-alignment candidate for published Figure 10 C/D; this is not scientific or visual "
        "acceptance of the PDCM replication. C overlays the actual, unshifted 100–600-ms soma voltages "
        "of the first recorded gid in each of eight cortical populations. D shows each of 767 cortical "
        "cells' 100–600-ms firing rate as a point, with a cellwise median/quartile box and 1.5-IQR "
        "whiskers. Point jitter alters only horizontal placement. Rates come from one simulation seed; "
        "boxes represent between-cell distributions, not between-run uncertainty. TH on/off condition "
        "is explicit in the source manifest; the paper does not unambiguously assign TH to these panels. "
        f"Paper: {PAPER_URL}\n"
    )
    (output / "caption.txt").write_text(caption, encoding="utf-8")
    artifacts = {
        path.name: {"bytes": path.stat().st_size, "sha256": sha256(path)}
        for path in sorted(output.iterdir()) if path.is_file()
    }
    result = {
        "schema_version": SCHEMA,
        "status": "presentation_alignment_only_not_paper_accepted",
        "scientific_acceptance": False,
        "paper_url": PAPER_URL,
        "condition": condition,
        "source_directory": str(source_dir),
        "source_manifest_sha256": sha256(source_dir / "figure_manifest.json"),
        "source_npz_sha256": sha256(source_dir / SOURCE_NAME),
        "renderer_sha256": sha256(Path(__file__).resolve()),
        "visual_changes_only": ["C uses overlaid actual mV instead of vertical offsets", "D uses cellwise box distributions and visible individual rates instead of scatter plus means"],
        "panels": ["C", "D"],
        "cortical_cells": int(len(arrays["cell_rate_hz"])),
        "selected_gids": [int(gid) for gid in arrays["selected_gids"]],
        "analysis_window_ms": [100, 600],
        "rate_summary": rows,
        "limitations": ["Presentation alignment does not establish numerical agreement with the published panels", "One simulation seed provides no between-run uncertainty", "Paper TH assignment to C/D is ambiguous"],
        "artifacts": artifacts,
    }
    (output / "figure_manifest.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Validated paper-method Figure 10 sidecar")
    parser.add_argument("--out", type=Path, required=True, help="New output folder; existing paths are refused")
    args = parser.parse_args()
    summary = render(args.source, args.out)
    print(json.dumps({"status": summary["status"], "output": str(args.out.resolve()), "TH": summary["condition"]["TH"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
