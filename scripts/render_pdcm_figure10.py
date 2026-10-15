#!/usr/bin/env python3
"""Render a transparent, provisional Figure 10 A-H from one saved PDCM run.

This is a read-only analysis of the 1%-scale, 600-ms multicompartment
source-default (TH=False) candidate. It is not a paper-acceptance test.
"""

from __future__ import annotations

import argparse
import hashlib
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


SCHEMA = "digifly-pdcm-figure10-candidate-panels-v1"
RAW_NAME = "digifly_pdcm_figure10_paper_data.json"
POPS = ("L2e", "L2i", "L4e", "L4i", "L5e", "L5i", "L6e", "L6i")
POP_LABELS = ("L2/3e", "L2/3i", "L4e", "L4i", "L5e", "L5i", "L6e", "L6i")
PALETTE = ("#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#56B4E9", "#7B6AA5", "#595959")
START_MS = 100.0
END_MS = 600.0


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def validate_metadata(run: dict, validation: dict, data: dict, raw_path: Path) -> None:
    """Reject a different experiment or an unvalidated/incomplete source."""
    require(run.get("workflow") == "pdcm_figure10_multicompartment_v1", "Wrong workflow")
    require(run.get("state") == "completed_unverified", "Candidate run is not completed")
    require(run.get("compile_exit_code") == 0 and run.get("simulate_exit_code") == 0, "Compile/simulation failed")
    require(run.get("scientific_acceptance") is False, "This renderer is only for the unaccepted candidate")
    require(validation.get("status") == "structural_pass_not_paper_acceptance", "Raw structural QA did not pass")
    require(validation.get("scientific_acceptance") is False, "Unexpected scientific-acceptance flag")
    require(run.get("raw_data_sha256") == validation.get("raw_data_sha256") == sha256(raw_path), "Raw file hash mismatch")
    require(run.get("raw_data_bytes") == validation.get("raw_data_bytes") == raw_path.stat().st_size, "Raw file size mismatch")
    cfg = data.get("simConfig", {})
    require(cfg.get("ScaleFactor") == 0.01 and cfg.get("duration") == 600.0, "Not the 1%/600-ms condition")
    require(cfg.get("TH") is False and cfg.get("DC") is False and cfg.get("Balanced") is True, "Not the TH=False source-default condition")
    require(cfg.get("recordStep") == 0.1, "Unexpected recorded timestep")
    depths = [position[1] for position in cfg.get("recordLFP", [])]
    require(depths == list(range(0, 1500, 100)), "The 15 electrode depths differ from the source")


def time_slice(times: np.ndarray, start: float, stop: float) -> np.ndarray:
    return (times >= start - 1e-7) & (times < stop - 1e-7)


def population_cells(cells: list[dict]) -> dict[str, list[dict]]:
    by_pop = {pop: [] for pop in POPS}
    for cell in cells:
        pop = cell.get("tags", {}).get("pop")
        if pop in by_pop:
            by_pop[pop].append(cell)
    for pop in POPS:
        by_pop[pop].sort(key=lambda cell: cell["gid"])
        require(by_pop[pop], f"Population {pop} has no cortical cells")
    require(sum(map(len, by_pop.values())) == 767, "Expected 767 cortical cells")
    return by_pop


def compartment_segments(data: dict, by_pop: dict[str, list[dict]], sample_index: int) -> tuple[np.ndarray, np.ndarray]:
    """Physical 3-D pt3d morphology, one recorded voltage per nseg=1 section."""
    segments: list[list[list[float]]] = []
    voltages: list[float] = []
    traces = data["simData"]
    for pop in POPS:
        for cell in by_pop[pop]:
            tags = cell["tags"]
            gid = int(cell["gid"])
            for section, sec in cell["secs"].items():
                trace_key = f"V_{section}"
                require(trace_key in traces, f"Missing voltage for {section}")
                trace = traces[trace_key][f"cell_{gid}"]
                require(sec["geom"].get("nseg") == 1, "One section voltage cannot color multiple segments")
                voltage = float(trace[sample_index])
                require(math.isfinite(voltage), f"Nonfinite t=150 ms voltage for gid {gid}")
                points = sec["geom"]["pt3d"]
                for first, second in zip(points, points[1:]):
                    # NetPyNE's pt3d points are relative to cell location in this run.
                    # Matplotlib axes are x, z, cortical depth (y).
                    segment = [
                        [float(tags["x"] + point[0]), float(tags["z"] + point[2]), float(tags["y"] + point[1])]
                        for point in (first, second)
                    ]
                    segments.append(segment)
                    voltages.append(voltage)
    return np.asarray(segments, dtype=np.float32), np.asarray(voltages, dtype=np.float32)


def per_cell_rates(gids: np.ndarray, by_pop: dict[str, list[dict]], duration_s: float) -> tuple[np.ndarray, np.ndarray]:
    """Rate per cell including silent cells; no across-run uncertainty implied."""
    ordered = np.asarray([cell["gid"] for pop in POPS for cell in by_pop[pop]], dtype=np.int64)
    counts = np.asarray([np.count_nonzero(gids == gid) for gid in ordered], dtype=np.int64)
    return ordered, counts.astype(float) / duration_s


def csd_netpyne_style(lfp: np.ndarray, spacing_mm: float, fs_hz: float) -> np.ndarray:
    """NetPyNE 1.1.1-style bandpass, Vaknin edges, then negative depth Laplacian.

    Conductivity is not supplied: output is a second-derivative proxy, not a
    calibrated absolute current density. The source's very low 0.05-Hz high-
    pass cutoff is not scientifically resolved by a 600-ms recording.
    """
    require(lfp.ndim == 2 and lfp.shape[1] >= 3, "LFP must have >=3 channels")
    require(0 < spacing_mm and fs_hz > 600, "Invalid spacing or sample rate")
    z, p, k = signal.iirfilter(4, [0.05 / (fs_hz / 2), 300 / (fs_hz / 2)], btype="band", ftype="butter", output="zpk")
    sos = signal.zpk2sos(z, p, k)
    filtered = np.empty_like(lfp, dtype=float)
    for index in range(lfp.shape[1]):
        forward = signal.sosfilt(sos, lfp[:, index])
        filtered[:, index] = signal.sosfilt(sos, forward[::-1])[::-1]
    depth_by_time = filtered.T
    padded = np.vstack((depth_by_time[:1], depth_by_time, depth_by_time[-1:]))
    return (-np.diff(padded, n=2, axis=0) / spacing_mm**2).T


def morlet_power(trace: np.ndarray, fs_hz: float, frequencies: np.ndarray, width: float = 7.0) -> tuple[np.ndarray, np.ndarray]:
    """NetPyNE-like 7-cycle Morlet power; blank insufficient edge support."""
    centered = signal.detrend(trace - np.mean(trace))
    power = np.empty((len(frequencies), len(centered)), dtype=float)
    support = np.zeros_like(power, dtype=bool)
    for index, frequency in enumerate(frequencies):
        temporal_sigma = width / (2.0 * np.pi * frequency)
        wave_times = np.arange(-3.5 * temporal_sigma, 3.5 * temporal_sigma, 1 / fs_hz)
        amplitude = 1.0 / (temporal_sigma * np.sqrt(2.0 * np.pi))
        wave = amplitude * np.exp(-wave_times**2 / (2.0 * temporal_sigma**2)) * np.exp(2j * np.pi * frequency * wave_times)
        convolved = signal.fftconvolve(centered, wave, mode="same")
        power[index] = (2.0 * np.abs(convolved) / fs_hz) ** 2
        edge_samples = int(math.ceil(3.5 * temporal_sigma * fs_hz))
        if 2 * edge_samples < len(centered):
            support[index, edge_samples:len(centered) - edge_samples] = True
    power[~support] = np.nan
    return power, support


def collect_panels(data: dict) -> dict[str, np.ndarray | dict[str, list[dict]]]:
    cfg = data["simConfig"]
    sim = data["simData"]
    times = np.asarray(sim["t"], dtype=float)
    require(times.shape == (6000,) and np.allclose(times, np.arange(6000) * 0.1, atol=1e-4), "Unexpected time vector")
    by_pop = population_cells(data["net"]["cells"])
    all_gids = {cell["gid"] for pop in POPS for cell in by_pop[pop]}
    require(len(all_gids) == 767, "Duplicate cortical gid")
    t150 = int(np.flatnonzero(np.isclose(times, 150.0))[0])
    segments, segment_voltage = compartment_segments(data, by_pop, t150)
    all_spike_t = np.asarray(sim["spkt"], dtype=float)
    all_spike_gid = np.asarray(sim["spkid"], dtype=np.int64)
    require(all_spike_t.shape == all_spike_gid.shape == (1509,), "Expected 1,509 spike pairs")
    require(np.isfinite(all_spike_t).all() and all(gid in all_gids for gid in all_spike_gid), "Invalid cortical spike")
    window = time_slice(times, START_MS, END_MS)
    spike_window = time_slice(all_spike_t, START_MS, END_MS)
    spike_t = all_spike_t[spike_window]
    spike_gid = all_spike_gid[spike_window]
    ordered_gids, cell_rates = per_cell_rates(spike_gid, by_pop, (END_MS - START_MS) / 1000)
    raster_rows = {int(gid): int(index) for index, gid in enumerate(ordered_gids)}
    spike_row = np.asarray([raster_rows[int(gid)] for gid in spike_gid], dtype=np.int64)
    selected = np.asarray([by_pop[pop][0]["gid"] for pop in POPS], dtype=np.int64)
    selected_soma = np.asarray([sim["V_soma"][f"cell_{gid}"] for gid in selected], dtype=float)[:, window]
    require(np.isfinite(selected_soma).all(), "Example soma voltage contains nonfinite values")
    lfp = np.asarray(sim["LFP"], dtype=float)
    require(lfp.shape == (6000, 15) and np.isfinite(lfp).all(), "Incomplete LFP array")
    csd = csd_netpyne_style(lfp, 0.1, 1000.0 / cfg["recordStep"])
    csd_window = time_slice(times, 100.0, 200.0)
    frequencies = np.arange(20.0, 100.1, 2.0)
    lfp_window = lfp[window]
    spectrogram, spectrogram_support = morlet_power(lfp_window[:, 3], 1000.0 / cfg["recordStep"], frequencies)
    welch_f, welch_psd = signal.welch(lfp_window[:, 3], fs=1000.0 / cfg["recordStep"], nperseg=2500, noverlap=1250, nfft=10000, detrend="linear")
    freq_window = (welch_f >= 20) & (welch_f <= 100)
    boundaries = np.cumsum([len(by_pop[pop]) for pop in POPS])
    return {
        "by_pop": by_pop,
        "time_ms": times[window],
        "segments_xyz_um": segments,
        "segment_voltage_mv": segment_voltage,
        "raster_time_ms": spike_t,
        "raster_gid": spike_gid,
        "raster_row": spike_row,
        "ordered_gids": ordered_gids,
        "pop_boundaries": boundaries,
        "cell_rate_hz": cell_rates,
        "selected_gids": selected,
        "selected_soma_mv": selected_soma,
        "lfp_raw": lfp_window,
        "depth_um": np.arange(0, 1500, 100),
        "csd_time_ms": times[csd_window],
        "csd_proxy_raw_per_mm2": csd[csd_window],
        "morlet_freq_hz": frequencies,
        "morlet_power": spectrogram,
        "morlet_supported": spectrogram_support,
        "welch_freq_hz": welch_f[freq_window],
        "welch_psd": welch_psd[freq_window],
    }


def decorate(ax, letter: str, title: str) -> None:
    if hasattr(ax, "zaxis"):
        ax.text2D(-0.07, 1.07, letter, transform=ax.transAxes, fontsize=11, weight="bold", va="top")
    else:
        ax.text(-0.07, 1.07, letter, transform=ax.transAxes, fontsize=11, weight="bold", va="top")
    ax.set_title(title, fontsize=9, loc="left", pad=8)
    if not hasattr(ax, "zaxis"):
        ax.spines[["top", "right"]].set_visible(False)


def draw_panels(panel: dict, output: Path) -> None:
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

    # A: all 767 actual six-section morphologies, colored by the saved t=150 ms voltage.
    vnorm = colors.Normalize(vmin=-90.0, vmax=30.0)
    collection = Line3DCollection(panel["segments_xyz_um"], cmap="coolwarm", norm=vnorm, linewidths=0.65, alpha=0.88)
    collection.set_array(panel["segment_voltage_mv"])
    ax_a.add_collection3d(collection)
    ax_a.set(xlim=(0, 380), ylim=(0, 380), zlim=(1470, -120), xlabel="X (µm)", ylabel="Z (µm)", zlabel="Depth (µm)")
    ax_a.set_xticks((0, 250)); ax_a.set_yticks((0, 250)); ax_a.set_zticks(np.arange(0, 1500, 250))
    ax_a.set_box_aspect((1, 1, 4.2), zoom=0.84)
    ax_a.view_init(elev=13, azim=-58)
    fig.colorbar(collection, ax=ax_a, fraction=0.035, pad=0.13, label="Section V at 150 ms (mV)")
    decorate(ax_a, "A", "All cortical sections · 250-µm grid")

    # B: a literal spike raster; 100-ms initialization omitted.
    bounds = np.r_[0, panel["pop_boundaries"]]
    colors_per_spike = [PALETTE[np.searchsorted(panel["pop_boundaries"], row, side="right")] for row in panel["raster_row"]]
    ax_b.scatter(panel["raster_time_ms"], panel["raster_row"], s=2.2, marker="|", linewidths=0.5, color=colors_per_spike, rasterized=True)
    for boundary in panel["pop_boundaries"][:-1]:
        ax_b.axhline(boundary - 0.5, color="#b5bbc2", lw=0.45)
    ax_b.set(xlim=(START_MS, END_MS), ylim=(767, -1), xlabel="Time (ms)", ylabel="Population / cell")
    ax_b.set_yticks([(bounds[i] + bounds[i + 1]) / 2 for i in range(8)], POP_LABELS)
    decorate(ax_b, "B", "Cortical spikes · n = 767 cells")

    # C: one deterministic soma trace (lowest gid) per population, same scale.
    for index, pop in enumerate(POPS):
        ax_c.plot(panel["time_ms"], panel["selected_soma_mv"][index] - 115 * index, color=PALETTE[index], lw=0.68)
    ax_c.set(xlim=(START_MS, END_MS), xlabel="Time (ms)", ylabel="Soma V (mV); 115-mV offsets")
    ax_c.set_yticks([-65 - 115 * i for i in range(8)], POP_LABELS)
    ax_c.set_ylim((-65 - 115 * 7 - 85, 40))
    decorate(ax_c, "C", "Representative soma V · first gid in each population")

    # D: one estimate per cell, including nonspiking neurons. Quantized at 2 Hz.
    rates = panel["cell_rate_hz"]
    pop_rates = [rates[bounds[i]:bounds[i + 1]] for i in range(8)]
    for index, values in enumerate(pop_rates):
        x = (index + 1) + 0.24 * (np.sin(np.arange(len(values)) * 12.9898) * 43758.5453 % 1 - 0.5)
        ax_d.scatter(x, values, s=4, alpha=0.20, color=PALETTE[index], rasterized=True)
        ax_d.plot(index + 1, np.mean(values), marker="D", ms=4, color="#111111")
    ax_d.set(xlim=(0.35, 8.65), ylim=(0, max(20, np.percentile(rates, 99) * 1.15)), ylabel="Cell rate (Hz)")
    ax_d.set_xticks(range(1, 9), POP_LABELS, rotation=45, ha="right")
    decorate(ax_d, "D", "500-ms cell rates · diamonds = population means")

    # E: same 15 saved LFP electrodes, with equal raw-unit offsets.
    lfp = panel["lfp_raw"]
    offset = 2.25 * np.max(np.abs(lfp))
    for index in range(15):
        ax_e.plot(panel["time_ms"], -index * offset + lfp[:, index], color="#0072B2", lw=0.55)
    ax_e.set(xlim=(START_MS, END_MS), xlabel="Time (ms)", ylabel="Electrode depth (µm) · offset traces")
    ax_e.set_yticks([-i * offset for i in range(15)], panel["depth_um"])
    ax_e.set_ylim((-14.7 * offset, 0.9 * offset))
    decorate(ax_e, "E", "Raw LFP · 15 electrodes at 100-µm spacing")

    # F: 100–200 ms, as prescribed by the source analysis configuration.
    csd = panel["csd_proxy_raw_per_mm2"]
    vmax = float(np.percentile(np.abs(csd), 99))
    vmax = max(vmax, np.finfo(float).tiny)
    im_f = ax_f.imshow(csd.T, aspect="auto", origin="upper", extent=(100, 200, 1450, -50), cmap="RdBu", norm=colors.TwoSlopeNorm(vmin=-vmax, vcenter=0, vmax=vmax), interpolation="nearest")
    fig.colorbar(im_f, ax=ax_f, fraction=0.043, pad=0.025, label="−d²LFP/dz² (raw/mm²)")
    ax_f.set(xlabel="Time (ms)", ylabel="Electrode depth (µm)")
    decorate(ax_f, "F", "CSD proxy · red sink (−), blue source (+)")

    # G: 7-cycle Morlet power, only where the full wavelet is supported.
    power = panel["morlet_power"]
    valid = np.isfinite(power) & (power > 0)
    db = np.full_like(power, np.nan)
    peak = float(np.nanmax(power))
    db[valid] = 10 * np.log10(power[valid] / peak)
    im_g = ax_g.imshow(db, aspect="auto", origin="lower", extent=(START_MS, END_MS, 19, 101), cmap="cividis", vmin=-35, vmax=0, interpolation="nearest")
    fig.colorbar(im_g, ax=ax_g, fraction=0.043, pad=0.025, label="Relative Morlet power (dB)")
    ax_g.set(xlabel="Time (ms)", ylabel="Frequency (Hz)")
    decorate(ax_g, "G", "300-µm LFP Morlet spectrogram · white = edge-unsupported")

    # H: a genuine per-Hz PSD, explicitly Welch rather than NetPyNE's Morlet default.
    ax_h.plot(panel["welch_freq_hz"], panel["welch_psd"], color="#7B6AA5", lw=1.2)
    ax_h.set(xlim=(20, 100), xlabel="Frequency (Hz)", ylabel="Welch PSD (raw LFP²/Hz)")
    ax_h.ticklabel_format(axis="y", style="sci", scilimits=(0, 0))
    decorate(ax_h, "H", "300-µm LFP spectral density · Welch")

    fig.suptitle("PDCM Figure 10 candidate · 1% multicompartment, 600 ms, TH=False source default", fontsize=12, weight="bold")
    fig.savefig(output / "figure10_candidate_ah.png", dpi=300, facecolor="white")
    fig.savefig(output / "figure10_candidate_ah.pdf", facecolor="white")
    plt.close(fig)


def save_source_data(panel: dict, output: Path) -> Path:
    path = output / "figure10_candidate_source_data.npz"
    arrays = {key: value for key, value in panel.items() if isinstance(value, np.ndarray)}
    np.savez_compressed(path, **arrays)
    return path


def caption(panel: dict) -> str:
    counts = [len(panel["by_pop"][pop]) for pop in POPS]
    gids = panel["selected_gids"].tolist()
    return (
        "Provisional, independent reconstruction of Romaro et al. (2021) Figure 10 A–H "
        "from one completed Digifly source-default NetPyNE multicompartment run (1% scale, "
        "600 ms, balanced Poisson, TH=False). All panels derive from the same immutable raw JSON. "
        "A: all 767 cortical cells and six source-defined one-segment sections, positioned from "
        "saved pt3d/cell coordinates and colored by their own section voltage at exactly 150 ms "
        "(grid ticks every 250 µm). B: 100–600 ms cortical spike raster. C: 100–600 ms soma "
        "voltage from the lowest-gid cell of each population, in population order "
        f"{', '.join(POPS)} (gids {gids}); traces have stated 115-mV vertical offsets. "
        "D: 100–600 ms per-cell firing rate, including silent cells, with mean diamonds; "
        f"population sizes {counts}, one seed, no between-run uncertainty. E: raw, uncalibrated "
        "NetPyNE LFP at all 15 saved electrodes, 0–1400 µm at 100-µm spacing, 100–600 ms; "
        "traces have equal raw-unit vertical offsets. F: current-source-density *proxy* over "
        "100–200 ms from the same LFP, using the installed NetPyNE 1.1.1-style 0.05–300-Hz "
        "fourth-order forward/reverse Butterworth filter, Vaknin endpoint extension, and "
        "negative second spatial difference divided by (0.1 mm)²; no tissue-conductivity "
        "factor is asserted. Red is negative sink, blue is positive source. G: 300-µm electrode "
        "20–100-Hz seven-cycle Morlet power after linear detrending, with unsupported wavelet "
        "edges blanked and power relative to its maximum. H: 20–100-Hz Welch PSD for that "
        "electrode (250-ms segments, 50% overlap, 1-Hz FFT grid); this differs from NetPyNE's "
        "default Morlet PSD method. The 600-ms recording limits low-frequency resolution. "
        "Author source comments associate TH=True with Figure 10A, whereas the executable "
        "multicompartment source defaults to TH=False. At 1% scale TH=True produces a "
        "distinct model condition, and its comparison is pending. The paper "
        "describes two apical dendrites while the saved author-source model has three. "
        "Structural QA passed, but no panel is asserted to match the paper or to pass "
        "scientific replication acceptance. Paper: https://pmc.ncbi.nlm.nih.gov/articles/PMC8382011/\n"
    )


def render(run_dir: Path, output: Path) -> dict:
    run_dir = run_dir.resolve()
    output = output.resolve()
    require(output != run_dir and run_dir not in output.parents, "Output cannot be inside the immutable run")
    require(not output.exists(), f"Output already exists: {output}")
    raw = run_dir / "data" / RAW_NAME
    run_path = run_dir / "run_manifest.json"
    qa_path = run_dir / "analysis-raw-validation-v1.json"
    require(raw.is_file() and run_path.is_file() and qa_path.is_file(), "Missing raw/manifest/QA input")
    with run_path.open(encoding="utf-8") as handle:
        run_manifest = json.load(handle)
    with qa_path.open(encoding="utf-8") as handle:
        qa = json.load(handle)
    with raw.open(encoding="utf-8") as handle:
        data = json.load(handle)
    validate_metadata(run_manifest, qa, data, raw)
    panel = collect_panels(data)
    output.mkdir(parents=True, exist_ok=False)
    draw_panels(panel, output)
    save_source_data(panel, output)
    (output / "caption.txt").write_text(caption(panel), encoding="utf-8")
    script = Path(__file__).resolve()
    artifacts = {path.name: {"bytes": path.stat().st_size, "sha256": sha256(path)} for path in sorted(output.iterdir()) if path.is_file()}
    summary = {
        "schema_version": SCHEMA,
        "status": "provisional_structurally_valid_not_paper_accepted",
        "paper_url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC8382011/",
        "raw_run": str(run_dir),
        "raw_data_sha256": sha256(raw),
        "raw_data_bytes": raw.stat().st_size,
        "run_manifest_sha256": sha256(run_path),
        "raw_validation_sha256": sha256(qa_path),
        "renderer_sha256": sha256(script),
        "condition": {"scale": 0.01, "duration_ms": 600, "balanced_poisson": True, "TH": False},
        "panels": {
            "A": "3-D six-section voltage at 150 ms; source TH ambiguity unresolved",
            "B": "100–600-ms cortical spike raster",
            "C": "100–600-ms representative soma voltage for eight populations",
            "D": "100–600-ms per-cell firing rates; single seed",
            "E": "100–600-ms raw LFP at 15 depth electrodes",
            "F": "100–200-ms uncalibrated CSD spatial-derivative proxy",
            "G": "100–600-ms 300-µm-electrode Morlet power, edge mask, 20–100 Hz",
            "H": "100–600-ms 300-µm-electrode Welch PSD, 20–100 Hz; not source-default Morlet PSD",
        },
        "selected_gids": {pop: int(gid) for pop, gid in zip(POPS, panel["selected_gids"])},
        "cortical_cells": 767,
        "spikes_all_600ms": 1509,
        "spikes_plotted_100_to_600ms": int(len(panel["raster_gid"])),
        "artifacts": artifacts,
        "limitations": ["TH=False versus author's Figure 10A TH=True comment: TH=True comparison pending", "source model has three apical dendrite sections versus paper wording of two", "600-ms duration and one seed limit frequency/rate interpretation", "LFP/CSD absolute physical units uncalibrated", "H uses Welch rather than NetPyNE default Morlet PSD", "visual/numeric paper acceptance not established"],
    }
    (output / "figure_manifest.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True, help="Completed Figure 10 source-default candidate run")
    parser.add_argument("--out", type=Path, required=True, help="New output directory; existing paths are refused")
    args = parser.parse_args()
    summary = render(args.run, args.out)
    print(json.dumps({"status": summary["status"], "output": str(args.out.resolve()), "raw_data_sha256": summary["raw_data_sha256"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
