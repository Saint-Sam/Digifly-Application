#!/usr/bin/env python3
"""Render Figure 10 candidates with source-indicated NetPyNE analysis methods.

This is a new, read-only analysis of either completed 1%/600-ms raw run.  The
author branch's commented plotting settings guide the windows and LFP spectra;
they do not resolve the paper's TH or morphology ambiguities.  No simulation
is started, existing figures are never replaced, and paper acceptance is not
asserted.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import colors
from mpl_toolkits.mplot3d.art3d import Line3DCollection
import numpy as np
from scipy import signal

import render_pdcm_figure10 as th_off
import render_pdcm_figure10_th1 as th_on


SCHEMA = "digifly-pdcm-figure10-paper-methods-v1"
PAPER_URL = "https://pmc.ncbi.nlm.nih.gov/articles/PMC8382011/"
POPS = th_off.POPS
POP_LABELS = th_off.POP_LABELS
PALETTE = th_off.PALETTE
START_MS = 100.0
END_MS = 600.0


def csd_from_recorded_lfp(
    lfp: np.ndarray,
    *,
    step_ms: float = 0.1,
    start_ms: float = 100.0,
    stop_ms: float = 200.0,
    spacing_um: float = 100.0,
) -> np.ndarray:
    """Match NetPyNE 1.0.7 ``prepareCSD``: slice first, then bandpass.

    The result is the uncalibrated negative spatial second derivative used by
    NetPyNE, not an independently calibrated physical current density.
    """
    values = np.asarray(lfp, dtype=float)
    th_off.require(values.ndim == 2 and values.shape[1] >= 3, "Expected time-by-depth LFP")
    th_off.require(np.isfinite(values).all(), "Nonfinite LFP")
    th_off.require(step_ms > 0 and spacing_um > 0 and 0 <= start_ms < stop_ms, "Invalid CSD sampling")
    first = int(round(start_ms / step_ms))
    last = int(round(stop_ms / step_ms))
    th_off.require(
        math.isclose(first * step_ms, start_ms, abs_tol=1e-7)
        and math.isclose(last * step_ms, stop_ms, abs_tol=1e-7)
        and last <= values.shape[0],
        "CSD window does not align with the saved time grid",
    )
    # The authors' cfg.py sets plotCSD(timeRange=[100, 200]).  NetPyNE 1.0.7
    # prepareCSD slices LFPData before applying its forward/reverse filter.
    return th_off.csd_netpyne_style(values[first:last], spacing_um / 1000.0, 1000.0 / step_ms)


def netpyne_morlet_power(
    trace: np.ndarray,
    *,
    fs_hz: float = 10000.0,
    min_freq: float = 1.0,
    max_freq: float = 80.0,
    step_freq: float = 1.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Reproduce NetPyNE 1.0.7 ``MorletSpec`` power, without v1 edge masks.

    The source's commented ``plotLFP`` calls specify electrode 3, 100–600 ms,
    and maxFreq=80; Morlet is the pinned NetPyNE analysis default.
    """
    values = np.asarray(trace, dtype=float)
    th_off.require(values.ndim == 1 and len(values) >= 2 and np.isfinite(values).all(), "Invalid LFP trace")
    th_off.require(fs_hz > 2 * max_freq and 0 < min_freq <= max_freq and step_freq > 0, "Invalid Morlet grid")
    frequencies = np.arange(min_freq, max_freq + 1, step_freq, dtype=float)
    centered = signal.detrend(values - np.mean(values))
    power = np.empty((len(frequencies), len(centered)), dtype=float)
    for index, frequency in enumerate(frequencies):
        temporal_sigma = 7.0 / (2.0 * np.pi * frequency)
        wave_times = np.arange(-3.5 * temporal_sigma, 3.5 * temporal_sigma, 1.0 / fs_hz)
        amplitude = 1.0 / (temporal_sigma * np.sqrt(2.0 * np.pi))
        wave = amplitude * np.exp(-(wave_times**2) / (2.0 * temporal_sigma**2)) * np.exp(
            2j * np.pi * frequency * wave_times
        )
        convolved = signal.fftconvolve(centered, wave)
        lower = int(np.ceil(len(wave) / 2.0))
        upper = int(len(convolved) - np.floor(len(wave) / 2.0) + 1)
        power[index] = (2.0 * np.abs(convolved[lower:upper]) / fs_hz) ** 2
    return frequencies, power


def morlet_psd(power: np.ndarray) -> np.ndarray:
    """NetPyNE 1.0.7 ``preparePSD`` Morlet default: mean TFR over time."""
    values = np.asarray(power, dtype=float)
    th_off.require(values.ndim == 2 and values.size > 0 and np.isfinite(values).all(), "Invalid Morlet power")
    return np.mean(values, axis=1)


def collect_paper_panels(data: dict, *, thalamic_input: bool) -> dict:
    """Reuse the validated A–E extraction, replacing only method-mismatched F–H."""
    legacy = th_on if thalamic_input else th_off
    panel = legacy.collect_panels(data)
    for key in ("csd_proxy_raw_per_mm2", "morlet_freq_hz", "morlet_power", "morlet_supported", "welch_freq_hz", "welch_psd"):
        panel.pop(key, None)
    cfg = data["simConfig"]
    lfp = np.asarray(data["simData"]["LFP"], dtype=float)
    panel["csd_source_window_raw_per_mm2"] = csd_from_recorded_lfp(lfp)
    panel["csd_time_ms"] = np.arange(START_MS, 200.0, cfg["recordStep"])
    window = th_off.time_slice(np.asarray(data["simData"]["t"], dtype=float), START_MS, END_MS)
    lfp_electrode_3 = lfp[window, 3]
    frequencies, power = netpyne_morlet_power(lfp_electrode_3, fs_hz=1000.0 / cfg["recordStep"])
    panel["morlet_source_freq_hz"] = frequencies
    panel["morlet_source_power"] = power
    # NetPyNE preparePSD retains frequencies strictly less than maxFreq=80.
    panel["morlet_source_psd_freq_hz"] = frequencies[frequencies < 80]
    panel["morlet_source_psd_power"] = morlet_psd(power)[frequencies < 80]
    return panel


def draw_panels(panel: dict, output: Path, *, thalamic_input: bool) -> None:
    """Keep the accessible A–H composition while using source-analysis F–H."""
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 7.5, "axes.labelsize": 8, "xtick.labelsize": 7, "ytick.labelsize": 7, "pdf.fonttype": 42})
    fig = plt.figure(figsize=(12.6, 17.6), layout="constrained")
    grid = fig.add_gridspec(4, 2, height_ratios=(1.3, 1, 1.15, 1))
    ax_a = fig.add_subplot(grid[0, 0], projection="3d")
    ax_b = fig.add_subplot(grid[0, 1])
    ax_c = fig.add_subplot(grid[1, 0])
    ax_d = fig.add_subplot(grid[1, 1])
    ax_e = fig.add_subplot(grid[2, 0])
    ax_f = fig.add_subplot(grid[2, 1])
    ax_g = fig.add_subplot(grid[3, 0])
    ax_h = fig.add_subplot(grid[3, 1])

    vnorm = colors.Normalize(vmin=-90.0, vmax=30.0)
    collection = Line3DCollection(panel["segments_xyz_um"], cmap="coolwarm", norm=vnorm, linewidths=0.65, alpha=0.88)
    collection.set_array(panel["segment_voltage_mv"])
    ax_a.add_collection3d(collection)
    ax_a.set(xlim=(0, 380), ylim=(0, 380), zlim=(1470, -120), xlabel="X (µm)", ylabel="Z (µm)", zlabel="Depth (µm)")
    ax_a.set_xticks((0, 250)); ax_a.set_yticks((0, 250)); ax_a.set_zticks(np.arange(0, 1500, 250))
    ax_a.set_box_aspect((1, 1, 4.2), zoom=0.84)
    ax_a.view_init(elev=13, azim=-58)
    fig.colorbar(collection, ax=ax_a, fraction=0.035, pad=0.13, label="Section V at 150 ms (mV)")
    th_off.decorate(ax_a, "A", "All cortical sections · 250-µm grid")

    bounds = np.r_[0, panel["pop_boundaries"]]
    colors_per_spike = [PALETTE[np.searchsorted(panel["pop_boundaries"], row, side="right")] for row in panel["raster_row"]]
    ax_b.scatter(panel["raster_time_ms"], panel["raster_row"], s=2.2, marker="|", linewidths=0.5, color=colors_per_spike, rasterized=True)
    for boundary in panel["pop_boundaries"][:-1]:
        ax_b.axhline(boundary - 0.5, color="#b5bbc2", lw=0.45)
    ax_b.set(xlim=(START_MS, END_MS), ylim=(767, -1), xlabel="Time (ms)", ylabel="Population / cell")
    ax_b.set_yticks([(bounds[i] + bounds[i + 1]) / 2 for i in range(8)], POP_LABELS)
    th_off.decorate(ax_b, "B", "Cortical spikes · n = 767 cells")

    for index, pop in enumerate(POPS):
        ax_c.plot(panel["time_ms"], panel["selected_soma_mv"][index] - 115 * index, color=PALETTE[index], lw=0.68)
    ax_c.set(xlim=(START_MS, END_MS), xlabel="Time (ms)", ylabel="Soma V (mV); 115-mV offsets")
    ax_c.set_yticks([-65 - 115 * i for i in range(8)], POP_LABELS)
    ax_c.set_ylim((-65 - 115 * 7 - 85, 40))
    th_off.decorate(ax_c, "C", "Representative soma V · first gid in each population")

    rates = panel["cell_rate_hz"]
    pop_rates = [rates[bounds[i]:bounds[i + 1]] for i in range(8)]
    for index, values in enumerate(pop_rates):
        x = (index + 1) + 0.24 * (np.sin(np.arange(len(values)) * 12.9898) * 43758.5453 % 1 - 0.5)
        ax_d.scatter(x, values, s=4, alpha=0.20, color=PALETTE[index], rasterized=True)
        ax_d.plot(index + 1, np.mean(values), marker="D", ms=4, color="#111111")
    ax_d.set(xlim=(0.35, 8.65), ylim=(0, max(20, np.percentile(rates, 99) * 1.15)), ylabel="Cell rate (Hz)")
    ax_d.set_xticks(range(1, 9), POP_LABELS, rotation=45, ha="right")
    th_off.decorate(ax_d, "D", "500-ms cell rates · diamonds = population means")

    lfp = panel["lfp_raw"]
    offset = 2.25 * np.max(np.abs(lfp))
    for index in range(15):
        ax_e.plot(panel["time_ms"], -index * offset + lfp[:, index], color="#0072B2", lw=0.55)
    ax_e.set(xlim=(START_MS, END_MS), xlabel="Time (ms)", ylabel="Electrode depth (µm) · offset traces")
    ax_e.set_yticks([-i * offset for i in range(15)], panel["depth_um"])
    ax_e.set_ylim((-14.7 * offset, 0.9 * offset))
    th_off.decorate(ax_e, "E", "Raw LFP · 15 electrodes at 100-µm spacing")

    csd = panel["csd_source_window_raw_per_mm2"]
    vmax = max(float(np.percentile(np.abs(csd), 99)), np.finfo(float).tiny)
    im_f = ax_f.imshow(csd.T, aspect="auto", origin="upper", extent=(100, 200, 1450, -50), cmap="RdBu", norm=colors.TwoSlopeNorm(vmin=-vmax, vcenter=0, vmax=vmax), interpolation="nearest")
    fig.colorbar(im_f, ax=ax_f, fraction=0.043, pad=0.025, label="−d²LFP/dz² (raw/mm²)")
    ax_f.set(xlabel="Time (ms)", ylabel="Electrode depth (µm)")
    th_off.decorate(ax_f, "F", "CSD source method · slice before filtering")

    power = panel["morlet_source_power"]
    im_g = ax_g.imshow(power, aspect="auto", origin="lower", extent=(START_MS, END_MS, 0.5, 80.5), cmap="cividis", interpolation="nearest")
    fig.colorbar(im_g, ax=ax_g, fraction=0.043, pad=0.025, label="Morlet power (raw LFP²)")
    ax_g.set(xlabel="Time (ms)", ylabel="Frequency (Hz)")
    th_off.decorate(ax_g, "G", "300-µm LFP · NetPyNE Morlet, 1–80 Hz")

    ax_h.plot(panel["morlet_source_psd_freq_hz"], panel["morlet_source_psd_power"], color="#7B6AA5", lw=1.2)
    ax_h.set(xlim=(1, 80), xlabel="Frequency (Hz)", ylabel="Mean Morlet power (raw LFP²)")
    ax_h.ticklabel_format(axis="y", style="sci", scilimits=(0, 0))
    th_off.decorate(ax_h, "H", "300-µm LFP · NetPyNE Morlet mean power")

    condition = "TH=True explicit" if thalamic_input else "TH=False source default"
    fig.suptitle(f"PDCM Figure 10 paper-method candidate · 1%, 600 ms, {condition}", fontsize=12, weight="bold")
    fig.savefig(output / "figure10_paper_method_ah.png", dpi=300, facecolor="white")
    fig.savefig(output / "figure10_paper_method_ah.pdf", facecolor="white")
    plt.close(fig)


def caption(*, thalamic_input: bool) -> str:
    condition = "TH=True explicit" if thalamic_input else "TH=False source default"
    return (
        f"Provisional Figure 10 A–H candidate from a completed 1%-scale, 600-ms, balanced-Poisson, {condition} "
        "source-locked multicompartment run. All panels derive from the same hash-validated raw NetPyNE JSON. "
        "A: all 767 cortical neurons' six source-defined sections, with section voltage at 150 ms and 250-µm grid. "
        "B: cortical spike raster over 100–600 ms. C: first-gid soma voltage for each of eight populations over "
        "100–600 ms. D: individual and mean population firing rates over 100–600 ms, one seed and no error bars. "
        "E: raw NetPyNE LFP at 15 electrodes spaced 100 µm, over 100–600 ms. F: 100–200 ms negative second-depth "
        "difference of LFP (raw/mm²), with the authors' NetPyNE 1.0.7 CSD method: slice to [100,200) ms before "
        "0.05–300-Hz fourth-order forward/reverse Butterworth filtering and Vaknin endpoint correction. Unlike the "
        "earlier candidate, future TH events cannot leak into this pre-200-ms panel. A conductivity calibration is "
        "not asserted. G: raw seven-cycle Morlet power for the 300-µm electrode over 100–600 ms, 1–80 Hz at 1-Hz "
        "steps, with NetPyNE's unmasked convolution edges. H: time-mean of the same Morlet power, 1–79 Hz; "
        "NetPyNE preparePSD omits frequencies equal to maxFreq=80. These G/H settings follow commented plotLFP "
        "calls in the pinned author init.py and NetPyNE 1.0.7 defaults; the paper caption does not specify its exact "
        "transform settings. One 500-ms analysis window cannot resolve the lowest frequencies robustly. The paper "
        "does not unambiguously assign the TH condition to B–H, and its two-apical-dendrite wording differs from the "
        "pinned three-apical-section source morphology. This is a source-method-aligned candidate, not accepted "
        f"paper replication. Paper: {PAPER_URL}\n"
    )


def render(run_dir: Path, output: Path) -> dict:
    run_dir = run_dir.resolve()
    output = output.resolve()
    th_off.require(output != run_dir and run_dir not in output.parents, "Output cannot be inside the immutable run")
    th_off.require(not output.exists(), f"Output already exists: {output}")
    raw = run_dir / "data" / th_off.RAW_NAME
    run_path = run_dir / "run_manifest.json"
    qa_path = run_dir / "analysis-raw-validation-v1.json"
    source_cfg_path = run_dir / "author/cfg.py"
    source_init_path = run_dir / "author/init.py"
    th_off.require(all(path.is_file() for path in (raw, run_path, qa_path, source_cfg_path, source_init_path)), "Missing Figure 10 inputs")
    run_manifest = json.loads(run_path.read_text(encoding="utf-8"))
    qa = json.loads(qa_path.read_text(encoding="utf-8"))
    with raw.open(encoding="utf-8") as handle:
        data = json.load(handle)
    thalamic_input = data.get("simConfig", {}).get("TH")
    th_off.require(type(thalamic_input) is bool, "TH must be explicit in raw data")
    legacy = th_on if thalamic_input else th_off
    legacy.validate_metadata(run_manifest, qa, data, raw)
    panel = collect_paper_panels(data, thalamic_input=thalamic_input)
    output.mkdir(parents=True, exist_ok=False)
    draw_panels(panel, output, thalamic_input=thalamic_input)
    arrays = {key: value for key, value in panel.items() if isinstance(value, np.ndarray)}
    np.savez_compressed(output / "figure10_paper_method_source_data.npz", **arrays)
    (output / "caption.txt").write_text(caption(thalamic_input=thalamic_input), encoding="utf-8")
    artifacts = {path.name: {"bytes": path.stat().st_size, "sha256": th_off.sha256(path)} for path in sorted(output.iterdir()) if path.is_file()}
    summary = {
        "schema_version": SCHEMA,
        "status": "provisional_method_aligned_not_paper_accepted",
        "scientific_acceptance": False,
        "paper_url": PAPER_URL,
        "raw_run": str(run_dir),
        "raw_data_sha256": th_off.sha256(raw),
        "raw_data_bytes": raw.stat().st_size,
        "run_manifest_sha256": th_off.sha256(run_path),
        "raw_validation_sha256": th_off.sha256(qa_path),
        "author_cfg_sha256": th_off.sha256(source_cfg_path),
        "author_init_sha256": th_off.sha256(source_init_path),
        "renderer_sha256": th_off.sha256(Path(__file__).resolve()),
        "base_renderer_sha256": th_off.sha256(Path(th_off.__file__).resolve()),
        "condition": {"scale": 0.01, "duration_ms": 600, "balanced_poisson": True, "TH": thalamic_input},
        "methods": {
            "A_to_E": "Same validated raw extraction and accessible display as Figure10 v1; 100–600-ms analysis window except A at 150 ms",
            "F": "Pinned NetPyNE 1.0.7 prepareCSD math: LFP sliced to [100,200) ms before 0.05–300-Hz Butterworth forward/reverse filtering; Vaknin; −second depth difference/(0.1 mm)^2",
            "G": "Pinned NetPyNE 1.0.7 MorletSpec math; 7 cycles; electrode index 3; [100,600) ms; 1–80 Hz by 1 Hz; raw power; unmasked edges",
            "H": "Pinned NetPyNE 1.0.7 preparePSD Morlet mean TFR; same electrode/window; 1–79 Hz because frequencies < maxFreq=80",
        },
        "method_source": "Pinned author cfg.py CSD call and commented author init.py plotLFP calls; NetPyNE 1.0.7 defaults",
        "cortical_cells": 767,
        "spikes_all_600ms": int(len(data["simData"]["spkt"])),
        "spikes_plotted_100_to_600ms": int(len(panel["raster_gid"])),
        "artifacts": artifacts,
        "limitations": [
            "Paper does not assign TH condition to all panels; source comment says TH=True for A, executable default is TH=False",
            "Paper wording says two apical dendrites whereas source has three apical sections",
            "Authors' exact original NetPyNE analysis version and G/H parameters are not specified in the paper",
            "The 600-ms simulation offers only a 500-ms spectral analysis window; low frequencies and edge power are poorly resolved",
            "LFP/CSD physical units are not conductivity-calibrated",
            "One simulation seed has no between-run uncertainty",
            "Visual/numeric agreement with published panel has not been accepted",
        ],
    }
    (output / "figure_manifest.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True, help="Completed TH-off or TH-on Figure 10 source-locked run")
    parser.add_argument("--out", type=Path, required=True, help="New output folder; existing paths are refused")
    args = parser.parse_args()
    summary = render(args.run, args.out)
    print(json.dumps({"status": summary["status"], "output": str(args.out.resolve()), "raw_data_sha256": summary["raw_data_sha256"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
