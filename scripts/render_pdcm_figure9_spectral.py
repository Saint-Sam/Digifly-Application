"""Figure 9D/E analysis of a completed, pinned Digifly PDCM run.

Use either the paper's dedicated 4,000 ms 10% balanced-Poisson condition or
label a first-four-seconds analysis of a 60.1-second run as provisional. Both
use SHA-verified NetPyNE Morlet and spectral Granger code; neither alone is a
claim that the entire paper has been replicated.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import importlib.util
import json
import math
from pathlib import Path
import sys
import types

from digifly_app.workers.pdcm_netpyne_worker import MODEL_COMMIT, POPULATIONS, SOURCE_SHA256


PAPER_URL = "https://pmc.ncbi.nlm.nih.gov/articles/PMC8382011/"
MODEL_CFG_URL = (
    "https://github.com/suny-downstate-medical-center/PDCM_NetPyNE/"
    f"blob/{MODEL_COMMIT}/cfg.py#L85-L101"
)
EXPECTED_POPULATION_COUNTS = dict(zip(
    POPULATIONS, (2068, 583, 2191, 547, 485, 106, 1439, 294), strict=True
))
# Read from the existing Athene NetPyNE 1.1.1 package layer. The same exact
# analysis-file bytes are available in the local NetPyNE installation. Refuse
# different code, rather than silently changing the method during a rerun.
NETPYNE_METHOD_SHA256 = {
    "analysis/spikes_legacy.py": "a60f5b1040a777621ff4ab5f3e51687e589087f668072207814cac14940d975c",
    "analysis/info.py": "cda0841dc3df191f467fd7f5d1c5198b6328d384d2027cc43b75b61b97bf3c74",
    "analysis/utils.py": "fadcfc4da1bf580bdfb4768acbf600484859c4943308baed5d866a93b7e628a9",
    "support/morlet.py": "9dbf827ba44700bb1fcf28dee4ad2df33e65951ea823ae96c6ff17b3806443e0",
    "support/bsmart.py": "d994488b8cfafcfcb2815dd66cc8e6a763e1203a5024bcb5883cce6d027a1e18",
}
SPECTROGRAM_RANGE_MS = (0, 4000)
SPECTROGRAM_BIN_MS = 5
SPECTROGRAM_FREQ_HZ = (1, 100, 1)
GRANGER_RANGE_MS = (500, 4000)
GRANGER_BIN_MS = 4
GRANGER_ORDER = 10
# The PDCM source creates one cortical cell and one Poisson NetStim point cell
# per scaled population member, plus a single bkg_IF NetStim. NetPyNE's
# getCellsInclude(['allCells']) includes those point cells, even though the
# source cfg records spikes only from the eight cortical populations. The
# completed 10% MPI log independently reports 15,427 total instantiated cells.
NETPYNE_ALLCELLS_COUNT = 7713 + 7713 + 1


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _source_paths(run_root: Path) -> dict[str, Path]:
    paths = {name: run_root / name for name in (
        "summary.json", "source_manifest.json", "cells.csv", "spikes.csv"
    )}
    if any(not path.is_file() or path.is_symlink() for path in paths.values()):
        raise ValueError("Choose a run with regular summary, source, cells and spikes files")
    return paths


def _cell_populations(path: Path) -> dict[int, str]:
    populations: dict[int, str] = {}
    counts = {pop: 0 for pop in POPULATIONS}
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != ["gid", "population", "x_um", "y_um", "z_um"]:
            raise ValueError("PDCM cells.csv has unexpected columns")
        for row in reader:
            gid, pop = int(row["gid"]), row["population"]
            if gid < 0 or gid in populations or pop not in counts:
                raise ValueError(f"Invalid cortical cell: {gid}, {pop}")
            populations[gid] = pop
            counts[pop] += 1
    if counts != EXPECTED_POPULATION_COUNTS or set(populations) != set(range(7713)):
        raise ValueError("PDCM cortical cells do not match the pinned 10% population counts")
    return populations


def _validate_run(run_root: Path) -> tuple[dict, dict[int, str], dict[str, Path], dict[str, str]]:
    paths = _source_paths(run_root)
    summary = json.loads(paths["summary.json"].read_text(encoding="utf-8"))
    source = json.loads(paths["source_manifest.json"].read_text(encoding="utf-8"))
    metadata = summary.get("metadata", {})
    if summary.get("workflow") != "pdcm_replication_v1" or summary.get("status") != "completed":
        raise ValueError("Choose a completed Digifly PDCM run")
    if metadata.get("model_commit") != MODEL_COMMIT or source.get("model_commit") != MODEL_COMMIT:
        raise ValueError("Figure 9 needs the pinned PDCM point-neuron source")
    source_hashes = source.get("original_sha256")
    if not isinstance(source_hashes, dict) or any(
        source_hashes.get(name) != value for name, value in SOURCE_SHA256.items()
    ):
        raise ValueError("Pinned PDCM model source hashes do not match")
    if metadata.get("scale") != 0.1 or metadata.get("drive") != "balanced_poisson":
        raise ValueError("Figure 9D/E need the 10% balanced-Poisson condition")
    _timing_class(float(metadata.get("duration_ms", 0)))
    populations = _cell_populations(paths["cells.csv"])
    if metadata.get("cortical_cells") != len(populations):
        raise ValueError("PDCM summary and cells.csv cell counts disagree")
    return summary, populations, paths, {name: _sha256(path) for name, path in paths.items()}


def _timing_class(duration_ms: float) -> str:
    if duration_ms == 4000:
        return "dedicated_4000_ms"
    if duration_ms >= 60_100:
        return "first_4s_of_long_run"
    raise ValueError("Figure 9D/E require the dedicated 4000 ms run or a completed >=60100 ms provisional run")


def _load_exact_methods():
    """Load only the SHA-pinned analysis modules; never initialize NEURON."""
    spec = importlib.util.find_spec("netpyne")
    if spec is None or not spec.submodule_search_locations:
        raise RuntimeError("NetPyNE is not installed; Figure 9D/E cannot use the pinned method")
    package = Path(next(iter(spec.submodule_search_locations)))
    files = {name: package / name for name in NETPYNE_METHOD_SHA256}
    if any(not path.is_file() for path in files.values()):
        raise RuntimeError("The NetPyNE installation lacks required Morlet or Granger source files")
    actual = {name: _sha256(path) for name, path in files.items()}
    if actual != NETPYNE_METHOD_SHA256:
        raise RuntimeError("NetPyNE analysis source differs from the SHA-pinned Athene 1.1.1 method")

    # morlet.py only needs netpyne.__gui__; a temporary headless stub avoids
    # importing NEURON or opening a display during an offline analysis sidecar.
    prior = sys.modules.get("netpyne")
    stub = types.ModuleType("netpyne")
    stub.__gui__ = False
    sys.modules["netpyne"] = stub
    try:
        morlet_spec = importlib.util.spec_from_file_location("digifly_pinned_morlet", files["support/morlet.py"])
        assert morlet_spec is not None and morlet_spec.loader is not None
        morlet = importlib.util.module_from_spec(morlet_spec)
        morlet_spec.loader.exec_module(morlet)
    finally:
        if prior is None:
            del sys.modules["netpyne"]
        else:
            sys.modules["netpyne"] = prior

    bsmart_spec = importlib.util.spec_from_file_location("digifly_pinned_bsmart", files["support/bsmart.py"])
    assert bsmart_spec is not None and bsmart_spec.loader is not None
    bsmart = importlib.util.module_from_spec(bsmart_spec)
    bsmart_spec.loader.exec_module(bsmart)
    return morlet, bsmart, actual


def _read_spike_windows(path: Path, populations: dict[int, str], duration_ms: float, expected_total: int):
    """Validate the complete event stream, retain only the first four seconds."""
    all_first_four: list[float] = []
    l2i: list[float] = []
    l4e: list[float] = []
    last_key = (-math.inf, -1)
    total = 0
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != ["time_ms", "gid", "population"]:
            raise ValueError("PDCM spikes.csv has unexpected columns")
        for row in reader:
            time_ms, gid, pop = float(row["time_ms"]), int(row["gid"]), row["population"]
            if not math.isfinite(time_ms) or not 0 <= time_ms <= duration_ms + 1e-6:
                raise ValueError("Recorded spike has an invalid time")
            if populations.get(gid) != pop:
                raise ValueError(f"Recorded spike has an unknown or mismatched cortical GID: {gid}")
            key = (time_ms, gid)
            if key < last_key:
                raise ValueError("PDCM spike events are not time ordered")
            last_key = key
            total += 1
            if time_ms <= SPECTROGRAM_RANGE_MS[1]:
                all_first_four.append(time_ms)
                if pop == "L2i":
                    l2i.append(time_ms)
                elif pop == "L4e":
                    l4e.append(time_ms)
    if total != expected_total:
        raise ValueError(f"PDCM summary spike count {expected_total} disagrees with {total} rows")
    if not all_first_four or not l2i or not l4e:
        raise ValueError("The Figure 9 first-four-second or Granger populations have no spikes")
    return all_first_four, l2i, l4e, total


def _histograms(all_spikes: list[float], l2i_spikes: list[float], l4e_spikes: list[float]):
    import numpy as np

    # Match NetPyNE 1.1.1's np.histogram(..., bins=np.arange(start, stop,
    # binSize)) exactly, including NumPy's final-edge inclusion.
    d_edges = np.arange(*SPECTROGRAM_RANGE_MS, SPECTROGRAM_BIN_MS)
    e_edges = np.arange(*GRANGER_RANGE_MS, GRANGER_BIN_MS)
    d_counts = np.histogram(all_spikes, bins=d_edges)[0]
    d_rate = d_counts * (1000.0 / SPECTROGRAM_BIN_MS) / NETPYNE_ALLCELLS_COUNT
    l2i_counts = np.histogram(l2i_spikes, bins=e_edges)[0]
    l4e_counts = np.histogram(l4e_spikes, bins=e_edges)[0]
    return d_edges, d_rate, e_edges, l2i_counts, l4e_counts


def _compute(d_rate, l2i_counts, l4e_counts, morlet, bsmart):
    import numpy as np

    freq_min, freq_max, freq_step = SPECTROGRAM_FREQ_HZ
    spectrogram = morlet.MorletSpec(
        d_rate, 1000.0 / SPECTROGRAM_BIN_MS,
        freqmin=freq_min, freqmax=freq_max, freqstep=freq_step,
    )
    frequency_hz, _power, _coherence, l2i_to_l4e, l4e_to_l2i, _instantaneous = bsmart.pwcausalr(
        np.array([l2i_counts, l4e_counts]), 1, len(l2i_counts),
        GRANGER_ORDER, int(1000 / GRANGER_BIN_MS), int(1000 / GRANGER_BIN_MS / 2),
    )
    spectrum = np.asarray(spectrogram.TFR)
    frequencies = np.asarray(spectrogram.f)
    forward = np.asarray(l4e_to_l2i[0])
    reverse = np.asarray(l2i_to_l4e[0])
    if spectrum.shape != (100, 799) or len(frequency_hz) != 126:
        raise RuntimeError("Pinned NetPyNE methods produced unexpected Figure 9 spectral dimensions")
    if not all(np.isfinite(array).all() for array in (spectrum, frequencies, frequency_hz, forward, reverse)):
        raise RuntimeError("Pinned NetPyNE methods produced non-finite Figure 9 results")
    return frequencies, spectrum, np.asarray(frequency_hz), forward, reverse


def _make_figure(d_freq, d_power, e_freq, l4e_to_l2i, l2i_to_l4e, timing_class: str):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    with plt.rc_context({
        "font.family": "sans-serif", "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans"],
        "font.size": 8, "axes.labelsize": 9, "axes.titlesize": 9,
        "xtick.labelsize": 7, "ytick.labelsize": 7, "pdf.fonttype": 42,
    }):
        fig, (ax_d, ax_e) = plt.subplots(
            1, 2, figsize=(8.8, 3.7), gridspec_kw={"width_ratios": (1.5, 1)},
            constrained_layout=True,
        )
        title = (
            "Dedicated 10% balanced-Poisson simulation · 4,000 ms total (including initial settling)"
            if timing_class == "dedicated_4000_ms" else
            "Provisional reanalysis · first 4 s of a 60.1 s run; not the paper's dedicated 4 s simulation"
        )
        fig.suptitle(title, fontsize=8, color="#555555")
        image = ax_d.imshow(
            d_power, extent=(0, 4000, float(d_freq.min()), float(d_freq.max())),
            origin="lower", interpolation="none", aspect="auto", cmap="viridis", rasterized=True,
        )
        colorbar = fig.colorbar(image, ax=ax_d, pad=0.02, shrink=0.92)
        colorbar.set_label("Morlet power (NetPyNE units)")
        ax_d.set(xlabel="Time (ms)", ylabel="Frequency (Hz)", title="D  ·  NetPyNE allCells spectrogram")
        ax_d.set_xlim(0, 4000)

        ax_e.plot(e_freq, l4e_to_l2i, color="#0072B2", linewidth=1.4,
                  label="L4e → L2/3i")
        ax_e.plot(e_freq, l2i_to_l4e, color="#D55E00", linewidth=1.4,
                  label="L2/3i → L4e")
        ax_e.set(xlabel="Frequency (Hz)", ylabel="Spectral Granger causality",
                 title="E  ·  Directed information flow", xlim=(0, 125))
        ax_e.legend(frameon=False, fontsize=8, loc="best")
        for ax in (ax_d, ax_e):
            ax.spines["top"].set_visible(False)
            ax.spines["right"].set_visible(False)
            ax.tick_params(direction="out", length=3)
        return fig


def render(run_root: Path, output_dir: Path) -> Path:
    """Write a new sidecar only; reject mismatched methods or existing output."""
    run_root = run_root.expanduser().resolve(strict=True)
    output_dir = output_dir.expanduser().resolve()
    if output_dir == run_root or output_dir.is_relative_to(run_root):
        raise ValueError("Figure output must be outside the original run")
    if output_dir.exists():
        raise FileExistsError(f"Figure output already exists: {output_dir}")

    summary, populations, paths, source_hashes = _validate_run(run_root)
    morlet, bsmart, method_hashes = _load_exact_methods()
    metadata = summary["metadata"]
    timing_class = _timing_class(float(metadata["duration_ms"]))
    all_spikes, l2i_spikes, l4e_spikes, total_spikes = _read_spike_windows(
        paths["spikes.csv"], populations, float(metadata["duration_ms"]),
        int(metadata["cortical_spikes"]),
    )
    d_edges, d_rate, e_edges, l2i_counts, l4e_counts = _histograms(
        all_spikes, l2i_spikes, l4e_spikes
    )
    d_freq, d_power, e_freq, l4e_to_l2i, l2i_to_l4e = _compute(
        d_rate, l2i_counts, l4e_counts, morlet, bsmart
    )

    output_dir.mkdir(parents=True, exist_ok=False)
    import matplotlib.pyplot as plt
    import numpy as np

    fig = _make_figure(d_freq, d_power, e_freq, l4e_to_l2i, l2i_to_l4e, timing_class)
    png = output_dir / "figure9_spectral_de.png"
    pdf = output_dir / "figure9_spectral_de.pdf"
    try:
        fig.savefig(png, dpi=300, bbox_inches="tight", pad_inches=0.08, facecolor="white")
        fig.savefig(pdf, dpi=300, bbox_inches="tight", pad_inches=0.08, facecolor="white")
    finally:
        plt.close(fig)
    arrays = output_dir / "figure9_spectral_data.npz"
    np.savez_compressed(
        arrays, d_frequency_hz=d_freq, d_time_bin_edges_ms=d_edges, d_rate_hz=d_rate,
        d_morlet_power=d_power, e_frequency_hz=e_freq, e_time_bin_edges_ms=e_edges,
        e_l2i_spike_counts=l2i_counts, e_l4e_spike_counts=l4e_counts,
        e_l4e_to_l2i=l4e_to_l2i, e_l2i_to_l4e=l2i_to_l4e,
    )
    granger_csv = output_dir / "figure9e_granger.csv"
    with granger_csv.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(("frequency_hz", "l4e_to_l2i", "l2i_to_l4e"))
        writer.writerows(zip(e_freq, l4e_to_l2i, l2i_to_l4e, strict=True))

    source_after = {name: _sha256(path) for name, path in paths.items()}
    if source_after != source_hashes:
        raise RuntimeError("PDCM source files changed during Figure 9D/E reanalysis")
    manifest = {
        "schema_version": 1,
        "workflow": "pdcm_figure9_spectral_v1",
        "panels": ["9D", "9E"],
        "scientific_status": (
            "Dedicated 4,000 ms 10% balanced-Poisson simulation; the initial 100 ms settling "
            "period lies inside this duration. Figure 9D/E use SHA-pinned NetPyNE 1.1.1 "
            "analysis files, but the authors' exact analysis package version and some "
            "unstated defaults are not published. This is not a full-paper match."
            if timing_class == "dedicated_4000_ms" else
            "Provisional reanalysis of the first 4 s of a completed 60.1 s run, "
            "not the paper's dedicated 4 s simulation or a complete Figure 9. "
            "The authors' exact analysis package version and some unstated defaults "
            "are not published; this sidecar pins the available NetPyNE 1.1.1 "
            "analysis-file bytes and labels its defaults explicitly."
        ),
        "timing_class": timing_class,
        "paper_url": PAPER_URL,
        "model_cfg_url": MODEL_CFG_URL,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_run": str(run_root),
        "source_model_commit": MODEL_COMMIT,
        "source_summary": {key: metadata[key] for key in
                           ("scale", "drive", "duration_ms", "seed", "cortical_cells", "cortical_spikes")},
        "source_sha256": source_hashes,
        "source_unchanged_after_render": True,
        "input_quality": {
            "cortical_cells": len(populations),
            "validated_spike_rows": total_spikes,
            "first_four_seconds_spike_rows_including_upper_endpoint": len(all_spikes),
            "l2i_first_four_seconds_spike_rows": len(l2i_spikes),
            "l4e_first_four_seconds_spike_rows": len(l4e_spikes),
            "all_spikes_finite_ordered_and_population_matched": True,
        },
        "method": {
            "netpyne_installed_distribution_version": importlib.metadata.version("netpyne"),
            "netpyne_analysis_file_sha256": method_hashes,
            "athene_netpyne_1_1_1_method_file_hash_match": True,
            "spectrogram": {
                "include": "NetPyNE allCells (7713 cortical plus 7714 virtual NetStim point cells)",
                "time_range_ms": [0, 4000], "histogram_bin_ms": 5,
                "histogram_edge_rule": "np.arange(0,4000,5); last edge 3995 ms",
                "spike_count_to_rate": "recorded cortical count * (1000/5) / 15427 Hz",
                "allcells_denominator": NETPYNE_ALLCELLS_COUNT,
                "virtual_cell_denominator_note": (
                    "Pinned cfg records spikes only from cortical populations, but "
                    "NetPyNE getCellsInclude(['allCells']) includes 7713 Poisson "
                    "NetStim cells and one background NetStim in the denominator. "
                    "The paper describes 7713 neurons and does not discuss this "
                    "analysis denominator; this is a source-method reconstruction."
                ),
                "transform": "NetPyNE MorletSpec, linear-detrended", "wavelet_width_cycles": 7,
                "frequency_min_hz": 1, "frequency_max_hz": 100, "frequency_step_hz": 1,
                "plot_power_scale": "linear; NetPyNE viridis convention",
                "includes_initialization_period": True,
                "initialization_note": "The 100 ms settling period is inside the 4,000 ms total; spectrogram includes it.",
            },
            "granger": {
                "cells1": "L2i", "cells2": "L4e", "time_range_ms": [500, 4000],
                "histogram_bin_ms": 4,
                "histogram_edge_rule": "np.arange(500,4000,4); last edge 3996 ms",
                "input": "raw population spike counts, not per-cell rates",
                "algorithm": "NetPyNE bsmart.pwcausalr", "autoregressive_order": 10,
                "sampling_rate_hz": 250, "frequency_max_hz": 125,
                "l4e_to_l2i_output": "Fy2x (second input to first input)",
                "l2i_to_l4e_output": "Fx2y (first input to second input)",
                "population_name_note": "Paper prose says L2/3e once, but Figure 9 caption and pinned cfg use L2/3i.",
            },
        },
        "script_sha256": _sha256(Path(__file__).resolve()),
        "output_sha256": {path.name: _sha256(path) for path in (png, pdf, arrays, granger_csv)},
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
