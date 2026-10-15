"""Render provisional Romaro et al. Figures 1–3 from completed full-scale runs.

The paper's original NEST stars are never invented: Figures 1 and 3 require a
reference CSV with columns figure,panel,population,value,source_url,method.
Use a fresh output directory. No simulation or analysis input is modified.
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
FULL_POPULATION_COUNTS = dict(zip(
    POPULATIONS, (20683, 5834, 21915, 5479, 4850, 1065, 14395, 2948), strict=True
))
METRIC_CELLS_PER_POPULATION = 1000
RASTER_CELLS = 1862
RASTER_WINDOW = (100.0, 600.0)
ONE_SECOND_WINDOW = (100.0, 1100.0)
SIXTY_SECOND_END = 60100.0
COLORS = ("#0072B2", "#56B4E9", "#E69F00", "#D55E00",
          "#009E73", "#A68C00", "#CC79A7", "#444444")
LABELS = {"L2e": "L2/3e", "L2i": "L2/3i", "L4e": "L4e", "L4i": "L4i",
          "L5e": "L5e", "L5i": "L5i", "L6e": "L6e", "L6i": "L6i"}
REFERENCE_PANELS = {1: ("B", "C", "D"), 3: ("B", "C")}


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
        rows = list(csv.DictReader(stream))
    result = {row["population"]: row for row in rows}
    if len(rows) != len(POPULATIONS) or set(result) != set(POPULATIONS):
        raise ValueError(f"Expected exactly eight PDCM population rows in {path}")
    return result


def _number(value: str, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Missing or invalid {label}") from error
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"Missing or invalid {label}")
    return result


def _cells(path: Path) -> dict[str, list[int]]:
    result = {pop: [] for pop in POPULATIONS}
    seen: set[int] = set()
    with _regular(path).open(newline="", encoding="utf-8") as stream:
        rows = csv.DictReader(stream)
        if not {"gid", "population"}.issubset(rows.fieldnames or ()):
            raise ValueError("PDCM cells.csv lacks gid/population columns")
        for row in rows:
            gid, pop = int(row["gid"]), row["population"]
            if gid < 0 or gid in seen or pop not in result:
                raise ValueError("PDCM cells.csv contains duplicate or unexpected cells")
            seen.add(gid)
            result[pop].append(gid)
    if {pop: len(result[pop]) for pop in POPULATIONS} != FULL_POPULATION_COUNTS:
        raise ValueError("This is not the paper's 77,169-cell full-scale network")
    return {pop: sorted(gids) for pop, gids in result.items()}


def _run(path: Path, drive: str, minimum_duration: float) -> dict:
    if path.expanduser().is_symlink():
        raise ValueError("PDCM run directory must not be a symlink")
    root = path.expanduser().resolve(strict=True)
    files = {name: _regular(root / name) for name in
             ("run_manifest.json", "summary.json", "source_manifest.json", "cells.csv", "spikes.csv")}
    run_manifest = json.loads(files["run_manifest.json"].read_text(encoding="utf-8"))
    summary = json.loads(files["summary.json"].read_text(encoding="utf-8"))
    source = json.loads(files["source_manifest.json"].read_text(encoding="utf-8"))
    metadata = summary.get("metadata", {})
    if (run_manifest.get("state") != "completed"
            or summary.get("workflow") != "pdcm_replication_v1"
            or summary.get("status") != "completed"):
        raise ValueError("Choose a completed Digifly PDCM run")
    duration = float(metadata.get("duration_ms", -1))
    if (metadata.get("model_commit") != MODEL_COMMIT
            or metadata.get("drive") != drive
            or not math.isclose(float(metadata.get("scale", -1)), 1.0, abs_tol=1e-9)
            or not math.isfinite(duration) or duration < minimum_duration
            or int(metadata.get("cortical_cells", -1)) != sum(FULL_POPULATION_COUNTS.values())):
        raise ValueError("Run does not match the full-scale figure condition")
    if (source.get("model_commit") != MODEL_COMMIT
            or any(source.get("original_sha256", {}).get(name) != value
                   for name, value in SOURCE_SHA256.items())):
        raise ValueError("Run does not contain the pinned author model")
    cells = _cells(files["cells.csv"])
    return {"root": root, "files": files, "digests": {name: _digest(file) for name, file in files.items()},
            "metadata": metadata, "cells": cells}


def _sample(cells: dict[str, list[int]], seed: int, *, total: int | None = None,
            per_population: int | None = None) -> dict[str, list[int]]:
    if (total is None) == (per_population is None):
        raise ValueError("Choose exactly one PDCM sampling rule")
    if per_population is not None:
        counts = {pop: per_population for pop in POPULATIONS}
    else:
        assert total is not None
        available = sum(map(len, cells.values()))
        exact = {pop: total * len(cells[pop]) / available for pop in POPULATIONS}
        counts = {pop: math.floor(exact[pop]) for pop in POPULATIONS}
        order = sorted(POPULATIONS, key=lambda pop: (-(exact[pop] - counts[pop]), POPULATIONS.index(pop)))
        for pop in order[:total - sum(counts.values())]:
            counts[pop] += 1
    if any(counts[pop] < 1 or counts[pop] > len(cells[pop]) for pop in POPULATIONS):
        raise ValueError("PDCM sample exceeds an available population")
    return {pop: sorted(random.Random(seed + index).sample(cells[pop], counts[pop]))
            for index, pop in enumerate(POPULATIONS)}


def _analysis(run: dict, path: Path, kind: str) -> dict:
    if path.expanduser().is_symlink():
        raise ValueError("Analysis directory must not be a symlink")
    root = path.expanduser().resolve(strict=True)
    manifest_file = _regular(root / "analysis_manifest.json")
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    sample_file = _regular(root / "sampling_manifest.json")
    metrics_file = _regular(root / ("paper_metrics.csv" if kind == "dc" else "sampling_metrics.csv"))
    if kind == "dc":
        if (manifest.get("workflow") != "pdcm_paper_window_analysis_v2"
                or Path(manifest.get("source_summary", "")).resolve() != run["files"]["summary.json"]
                or manifest.get("analysis_start_ms") != 100
                or manifest.get("analysis_end_ms") != 60100):
            raise ValueError("Figure 3 DC requires a matching paper-window-v2 sidecar")
    else:
        profile = "table3_1000" if kind == "sampled" else "all_cells"
        windows = manifest.get("analysis_windows_ms", {})
        if (manifest.get("workflow") != "pdcm_sampling_analysis_v1"
                or manifest.get("profile") != profile
                or Path(manifest.get("source_run", "")).resolve() != run["root"]
                or windows.get("rate_and_cv") != [100, 60100]
                or windows.get("synchrony_5s") != [100, 5098]):
            raise ValueError(f"Figure analysis requires a matching {profile} sidecar")
    if any(manifest.get("source_sha256", {}).get(name) != run["digests"][name]
           for name in ("summary.json", "cells.csv", "spikes.csv")):
        raise ValueError("Analysis no longer matches its simulation source")
    if any(manifest.get("analysis_sha256", {}).get(file.name) != _digest(file)
           for file in (sample_file, metrics_file)):
        raise ValueError("Analysis sidecar hash mismatch")
    sample = json.loads(sample_file.read_text(encoding="utf-8")).get("cell_ids", {})
    metrics_rows = _rows(metrics_file)
    if set(sample) != set(POPULATIONS):
        raise ValueError("Analysis lacks a PDCM population sample")
    metrics = {}
    for pop in POPULATIONS:
        gids = sample[pop]
        expected = len(run["cells"][pop]) if kind == "all" else METRIC_CELLS_PER_POPULATION
        if (len(gids) != expected or len(set(gids)) != expected
                or not set(gids).issubset(run["cells"][pop])
                or int(metrics_rows[pop]["sampled_cells"]) != expected):
            raise ValueError(f"Analysis has the wrong unique-cell sample for {pop}")
        row = metrics_rows[pop]
        metrics[pop] = {
            "rate": _number(row["mean_rate_hz_60s"], f"{pop} rate"),
        }
        if kind != "dc":
            metrics[pop]["cv"] = _number(row["mean_cv_isi_60s"], f"{pop} CV ISI")
            metrics[pop]["sync"] = _number(row["population_total_fano_3ms_5s"], f"{pop} synchrony")
    return {"root": root, "files": (manifest_file, sample_file, metrics_file),
            "digests": {file.name: _digest(file) for file in (manifest_file, sample_file, metrics_file)},
            "sample": sample, "metrics": metrics}


def _reference(path: Path, figure: int) -> dict:
    if figure not in REFERENCE_PANELS:
        return {}
    if path is None:
        raise ValueError(f"Figure {figure} requires sourced NEST reference stars; supply --reference-csv")
    if path.expanduser().is_symlink():
        raise ValueError("NEST reference CSV must not be a symlink")
    file = _regular(path.expanduser().resolve(strict=True))
    with file.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        required_columns = {"figure", "panel", "population", "value", "source_url", "method"}
        if not required_columns.issubset(reader.fieldnames or ()):
            raise ValueError("NEST reference CSV lacks provenance or value columns")
        rows = list(reader)
    result = {}
    for row in rows:
        key = (row["panel"], row["population"])
        if (row["figure"] != str(figure) or row["panel"] not in REFERENCE_PANELS[figure]
                or row["population"] not in POPULATIONS or key in result
                or not row["source_url"].startswith("https://") or not row["method"].strip()):
            raise ValueError("NEST reference CSV has missing, duplicate, or unsourced rows")
        result[key] = {"value": _number(row["value"], "NEST reference value"),
                       "source_url": row["source_url"], "method": row["method"]}
    expected = {(panel, pop) for panel in REFERENCE_PANELS[figure] for pop in POPULATIONS}
    if set(result) != expected:
        raise ValueError("NEST reference CSV must provide every required star")
    return {"path": file, "sha256": _digest(file), "values": result}


def _spikes(run: dict, raster_sample: dict[str, list[int]],
            rate_sample: dict[str, list[int]] | None = None) -> dict:
    offsets: dict[int, tuple[str, int]] = {}
    centers = []
    base = 0
    for pop in POPULATIONS:
        for index, gid in enumerate(raster_sample[pop]):
            offsets[gid] = (pop, base + index)
        centers.append(base + (len(raster_sample[pop]) - 1) / 2)
        base += len(raster_sample[pop])
    pop_by_gid = {gid: pop for pop in POPULATIONS for gid in run["cells"][pop]}
    rate_pop = {gid: pop for pop, gids in (rate_sample or {}).items() for gid in gids}
    rate_counts = {pop: 0 for pop in POPULATIONS}
    times, positions = {pop: [] for pop in POPULATIONS}, {pop: [] for pop in POPULATIONS}
    with run["files"]["spikes.csv"].open(newline="", encoding="utf-8") as stream:
        rows = csv.DictReader(stream)
        if not {"gid", "population", "time_ms"}.issubset(rows.fieldnames or ()):
            raise ValueError("PDCM spikes.csv lacks required columns")
        for row in rows:
            gid, pop, time = int(row["gid"]), row["population"], float(row["time_ms"])
            if (pop_by_gid.get(gid) != pop or not math.isfinite(time)
                    or time < 0 or time > float(run["metadata"]["duration_ms"]) + 1e-6):
                raise ValueError("PDCM spike references an unknown cell or invalid time")
            if RASTER_WINDOW[0] <= time < RASTER_WINDOW[1] and gid in offsets:
                selected_pop, position = offsets[gid]
                if selected_pop != pop:
                    raise ValueError("PDCM raster sample has an inconsistent population")
                times[pop].append(time - RASTER_WINDOW[0])
                positions[pop].append(position)
            if ONE_SECOND_WINDOW[0] <= time < ONE_SECOND_WINDOW[1] and rate_pop.get(gid) == pop:
                rate_counts[pop] += 1
    return {"times": times, "positions": positions, "centers": centers,
            "raster_cells": base, "one_second_rate": {
                pop: rate_counts[pop] / len(rate_sample[pop]) if rate_sample else None
                for pop in POPULATIONS}}


def _style(ax) -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(direction="out", length=2.5, width=0.7)


def _raster(ax, data: dict, title: str) -> None:
    for pop, color in zip(POPULATIONS, COLORS, strict=True):
        ax.scatter(data["times"][pop], data["positions"][pop],
                   s=2 if data["raster_cells"] > 2000 else 7,
                   marker="|", linewidths=0.4, color=color, rasterized=True)
    ax.set(xlim=(0, 500), ylim=(data["raster_cells"], -1),
           xlabel="Time after settling (ms)", ylabel="Population / cells", title=title)
    ax.set_yticks(data["centers"], [LABELS[pop] for pop in POPULATIONS])
    ax.grid(axis="x", linewidth=0.4, alpha=0.2)
    _style(ax)


def _bars(ax, values: dict[str, float], title: str, ylabel: str,
          reference: dict | None = None) -> None:
    heights = [values[pop] for pop in POPULATIONS]
    reference_heights = ([reference[pop] for pop in POPULATIONS] if reference else [])
    ax.bar(range(len(POPULATIONS)), heights, color=COLORS, width=0.72,
           edgecolor="#303030", linewidth=0.35, label="Digifly / NetPyNE")
    if reference_heights:
        ax.scatter(range(len(POPULATIONS)), reference_heights, color="#111111",
                   marker="*", s=65, zorder=4, label="Original NEST reference")
        ax.legend(loc="upper right", frameon=False, fontsize=7)
    ax.set(title=title, ylabel=ylabel,
           ylim=(0, max([*heights, *reference_heights, 1e-6]) * 1.16))
    ax.set_xticks(range(len(POPULATIONS)), [LABELS[pop] for pop in POPULATIONS],
                  rotation=45, ha="right")
    ax.grid(axis="y", linewidth=0.4, alpha=0.2)
    ax.set_axisbelow(True)
    _style(ax)


def _render(figure: int, data: dict, output: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    plt.rcParams.update({"font.family": "sans-serif", "font.size": 8.5,
                         "axes.labelsize": 8.5, "axes.titlesize": 9,
                         "xtick.labelsize": 7.5, "ytick.labelsize": 7.5,
                         "pdf.fonttype": 42})
    if figure in (1, 2):
        fig, axes = plt.subplots(1, 4, figsize=(15.2, 5.2), layout="constrained",
                                 gridspec_kw={"width_ratios": [1.45, 1, 1, 1], "wspace": 0.16})
        _raster(axes[0], data["balanced_spikes"],
                f"Balanced Poisson · {data['balanced_spikes']['raster_cells']:,} cells")
        for panel, key, title, ylabel in (
            ("B", "rate", "Mean firing rate · 60 s", "Rate (Hz)"),
            ("C", "cv", "Spike-train irregularity · 60 s", "CV of ISI"),
            ("D", "sync", "Population synchrony · 5 s", "3 ms-bin total-count Fano"),
        ):
            reference = ({pop: data["reference"]["values"][(panel, pop)]["value"]
                          for pop in POPULATIONS} if figure == 1 else None)
            _bars(axes[ord(panel) - ord("A")],
                  {pop: data["balanced_analysis"]["metrics"][pop][key] for pop in POPULATIONS},
                  title, ylabel, reference)
    else:
        fig, axes = plt.subplots(2, 2, figsize=(11.8, 8.7), layout="constrained")
        _raster(axes[0, 0], data["balanced_spikes"], "Balanced DC · 1,862-cell raster")
        _bars(axes[0, 1],
              {pop: data["balanced_analysis"]["metrics"][pop]["rate"] for pop in POPULATIONS},
              "Balanced DC · 60 s", "Rate (Hz)",
              {pop: data["reference"]["values"][("B", pop)]["value"] for pop in POPULATIONS})
        _bars(axes[1, 0], data["unbalanced_spikes"]["one_second_rate"],
              "Unbalanced Poisson · 1 s", "Rate (Hz)",
              {pop: data["reference"]["values"][("C", pop)]["value"] for pop in POPULATIONS})
        _raster(axes[1, 1], data["unbalanced_spikes"], "Unbalanced Poisson · 1,862-cell raster")
        axes = axes.flat
    for letter, ax in zip("ABCD", axes, strict=True):
        ax.text(-0.09, 1.09, letter, transform=ax.transAxes,
                fontweight="bold", fontsize=11, ha="left", va="top")
    fig.suptitle(f"Digifly PDCM · Figure {figure} full-scale condition",
                 fontsize=11, fontweight="bold")
    fig.savefig(output / f"figure{figure}_digifly.png", dpi=300, facecolor="white")
    fig.savefig(output / f"figure{figure}_digifly.pdf", dpi=300, facecolor="white")
    plt.close(fig)


def render(figure: int, balanced_run: Path, balanced_analysis: Path, output_dir: Path,
           *, unbalanced_run: Path | None = None, reference_csv: Path | None = None,
           raster_seed: int = 3) -> Path:
    if figure not in (1, 2, 3):
        raise ValueError("Only paper Figures 1–3 use this full-scale renderer")
    if isinstance(raster_seed, bool) or not isinstance(raster_seed, int):
        raise ValueError("Raster seed must be an integer")
    raw_output = output_dir.expanduser()
    if raw_output.exists() or raw_output.is_symlink():
        raise FileExistsError(f"Refusing to overwrite a figure folder: {raw_output}")
    output = raw_output.resolve()
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"Refusing to overwrite a figure folder: {output}")
    drive = "balanced_dc" if figure == 3 else "balanced_poisson"
    balanced = _run(balanced_run, drive, SIXTY_SECOND_END)
    kind = "dc" if figure == 3 else "sampled" if figure == 1 else "all"
    analysis = _analysis(balanced, balanced_analysis, kind)
    reference = _reference(reference_csv, figure)
    sources = [balanced["root"], analysis["root"]]
    if reference:
        sources.append(reference["path"])
    if figure == 3:
        if unbalanced_run is None:
            raise ValueError("Figure 3 requires a full-scale unbalanced-Poisson run")
        unbalanced = _run(unbalanced_run, "unbalanced_poisson", ONE_SECOND_WINDOW[1])
        if unbalanced["root"] == balanced["root"]:
            raise ValueError("Figure 3 drives must be separate simulations")
        sources.append(unbalanced["root"])
    elif unbalanced_run is not None:
        raise ValueError("Only Figure 3 accepts an unbalanced-Poisson run")
    if any(source == output or source in output.parents for source in sources):
        raise ValueError("Figure output must be outside runs, analyses, and reference files")
    raster_sample = _sample(balanced["cells"], raster_seed, total=RASTER_CELLS) if figure != 2 else balanced["cells"]
    balanced_spikes = _spikes(balanced, raster_sample)
    data = {"balanced": balanced, "balanced_analysis": analysis,
            "balanced_spikes": balanced_spikes, "balanced_raster_sample": raster_sample,
            "reference": reference}
    if figure == 3:
        unbalanced_raster = _sample(unbalanced["cells"], raster_seed, total=RASTER_CELLS)
        unbalanced_metric = _sample(unbalanced["cells"], raster_seed,
                                    per_population=METRIC_CELLS_PER_POPULATION)
        data.update(unbalanced=unbalanced, unbalanced_raster_sample=unbalanced_raster,
                    unbalanced_metric_sample=unbalanced_metric,
                    unbalanced_spikes=_spikes(unbalanced, unbalanced_raster, unbalanced_metric))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir(exist_ok=False)
    _render(figure, data, output)
    for source in (balanced, data.get("unbalanced")):
        if source and any(_digest(path) != source["digests"][name]
                          for name, path in source["files"].items()):
            raise RuntimeError("A PDCM simulation file changed during figure generation")
    if any(_digest(path) != analysis["digests"][path.name] for path in analysis["files"]):
        raise RuntimeError("A PDCM analysis file changed during figure generation")
    if reference and _digest(reference["path"]) != reference["sha256"]:
        raise RuntimeError("NEST reference CSV changed during figure generation")
    table_path = output / f"figure{figure}_source_data.csv"
    with table_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(("panel", "drive", "population", "metric", "digifly_value",
                         "nest_reference_value", "statistic_cells", "raster_cells"))
        panels = (("B", "rate"),) if figure == 3 else (
            ("B", "rate"), ("C", "cv"), ("D", "sync")
        )
        measured = [
            (panel, drive, key,
             {pop: analysis["metrics"][pop][key] for pop in POPULATIONS},
             analysis["sample"], raster_sample)
            for panel, key in panels
        ]
        if figure == 3:
            measured.append(("C", "unbalanced_poisson", "rate_1s",
                             data["unbalanced_spikes"]["one_second_rate"],
                             data["unbalanced_metric_sample"], data["unbalanced_raster_sample"]))
        for panel, drive_name, metric_name, values, sample, raster in measured:
            for pop in POPULATIONS:
                ref = reference["values"].get((panel, pop)) if reference else None
                writer.writerow((panel, drive_name, pop, metric_name, values[pop],
                                 ref["value"] if ref else "", len(sample[pop]), len(raster[pop])))
    figure_files = [output / f"figure{figure}_digifly.{extension}" for extension in ("png", "pdf")]
    manifest = {
        "schema_version": 1, "workflow": "pdcm_fullscale_figures_v1",
        "status": "provisional_not_paper_accepted", "paper_figure": figure,
        "paper_reference": PAPER_URL, "model_commit": MODEL_COMMIT,
        "renderer_sha256": _digest(Path(__file__).resolve()),
        "panel_mapping": ({"A": "Poisson 500-ms raster", "B": "Poisson 60-s rate",
                           "C": "Poisson 60-s CV ISI", "D": "Poisson 5-s synchrony"}
                          if figure in (1, 2) else
                          {"A": "Balanced DC 500-ms raster", "B": "Balanced DC 60-s rate",
                           "C": "Unbalanced Poisson 1-s rate", "D": "Unbalanced Poisson 500-ms raster"}),
        "windows_ms": {"settling_excluded_before": 100, "raster": [100, 600],
                       "rate_cv": [100, 60100], "synchrony_5s_complete_bins": [100, 5098],
                       "unbalanced_rate_1s": [100, 1100] if figure == 3 else None},
        "raster_seed": raster_seed,
        "raster_cell_ids": {drive: raster_sample,
                            **({"unbalanced_poisson": data["unbalanced_raster_sample"]}
                               if figure == 3 else {})},
        "statistic_cell_ids": {drive: analysis["sample"],
                               **({"unbalanced_poisson": data["unbalanced_metric_sample"]}
                                  if figure == 3 else {})},
        "source_runs": {drive: {"run": str(balanced["root"]),
                                "analysis": str(analysis["root"]),
                                "source_sha256": balanced["digests"],
                                "analysis_sha256": analysis["digests"]},
                        **({"unbalanced_poisson": {"run": str(data["unbalanced"]["root"]),
                                                    "source_sha256": data["unbalanced"]["digests"]}}
                           if figure == 3 else {})},
        "nest_reference": ({"path": str(reference["path"]), "sha256": reference["sha256"],
                            "entries": [{"panel": panel, "population": pop, **reference["values"][(panel, pop)]}
                                        for panel in REFERENCE_PANELS[figure] for pop in POPULATIONS]}
                           if reference else None),
        "figure_sha256": {path.name: _digest(path) for path in figure_files},
        "source_data_sha256": {table_path.name: _digest(table_path)},
        "caveats": ["The authors' sampled cell IDs were not published; deterministic Digifly IDs are recorded.",
                    "Original NEST star values are operator-supplied with explicit source/method; this renderer does not verify the transcription.",
                    "The paper's printed synchrony equation and numerical tables differ in normalization; the population-total Fano used here follows the published operational scale."],
    }
    (output / "figure_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    caption = (
        f"Provisional Digifly reconstruction of Romaro et al. (2021) Figure {figure}. "
        "Full-scale pinned NetPyNE/NEURON runs are used, with 100 ms settling excluded. "
        + ("A is a 500 ms, 1,862-cell raster; B/C are 60 s sampled-cell rate/CV ISI; "
           "D is 5 s sampled-cell synchrony (complete 3 ms bins). " if figure == 1 else
           "A includes all 77,169 cells in a 500 ms raster; B/C/D use all cells for 60 s rate/CV and 5 s synchrony. "
           if figure == 2 else
           "A/B are balanced-DC 500 ms raster and 60 s rate; C/D are unbalanced-Poisson 1 s rate and 500 ms raster. ")
        + ("Black stars use the supplied NEST-reference CSV; its transcription is not independently certified. "
           if reference else "")
        + "Exact sampled IDs, source hashes, and panel values are retained in this folder. "
          "This image is not yet an accepted paper replication.\n"
    )
    (output / "caption.txt").write_text(caption, encoding="utf-8")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--figure", type=int, required=True, choices=(1, 2, 3))
    parser.add_argument("--balanced-run", type=Path, required=True)
    parser.add_argument("--balanced-analysis", type=Path, required=True)
    parser.add_argument("--unbalanced-run", type=Path)
    parser.add_argument("--reference-csv", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--raster-seed", type=int, default=3)
    args = parser.parse_args()
    print(render(args.figure, args.balanced_run, args.balanced_analysis, args.output_dir,
                 unbalanced_run=args.unbalanced_run, reference_csv=args.reference_csv,
                 raster_seed=args.raster_seed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
