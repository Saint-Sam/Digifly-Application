"""Compare two validated Figure 10 raw candidates without claiming causality.

Expected inputs are the separately named, source-locked TH=False and TH=True
1%/600 ms runs.  The pinned source adds virtual TH populations before building
recurrent connections; this comparison cannot assume identical random streams.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
import struct
from pathlib import Path

import psutil

from digifly_app.core.pdcm_figure10 import MODEL_COMMIT, SOURCE_FINGERPRINT, validate_prepared_bundle


POPS = ("L2e", "L2i", "L4e", "L4i", "L5e", "L5i", "L6e", "L6i")
WINDOWS = (("before_200ms", 0.0, 200.0), ("after_200ms", 200.0, 600.0))
VOLTAGE_SECTIONS = ("soma", "Adend1", "Adend2", "Adend3", "Bdend", "axon")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def summarize(data: dict) -> dict:
    cells = data["net"]["cells"]
    gid_to_pop = {cell["gid"]: cell["tags"]["pop"] for cell in cells if cell["tags"].get("pop") in POPS}
    pop_sizes = {pop: sum(label == pop for label in gid_to_pop.values()) for pop in POPS}
    spkid = data["simData"]["spkid"]
    spkt = data["simData"]["spkt"]
    lfp = data["simData"]["LFP"]
    times = data["simData"]["t"]
    soma = data["simData"]["V_soma"]
    windows: dict[str, dict] = {}
    for name, start, stop in WINDOWS:
        counts = {pop: 0 for pop in POPS}
        for gid, time in zip(spkid, spkt):
            if start <= time < stop:
                counts[gid_to_pop[gid]] += 1
        sample_indices = [index for index, time in enumerate(times) if start <= time < stop]
        values = [lfp[index][3] for index in sample_indices]  # 300 µm electrode
        if not values:
            raise ValueError(f"No saved LFP samples in {name}")
        voltage_sum = 0.0
        voltage_low = math.inf
        voltage_high = -math.inf
        voltage_samples = 0
        for trace in soma.values():
            for index in sample_indices:
                value = trace[index]
                voltage_sum += value
                voltage_samples += 1
                voltage_low = min(voltage_low, value)
                voltage_high = max(voltage_high, value)
        windows[name] = {
            "spike_count": sum(counts.values()),
            "spike_counts_by_population": counts,
            "rates_hz_by_population": {
                pop: counts[pop] / (pop_sizes[pop] * (stop - start) / 1000)
                for pop in POPS
            },
            "lfp_300um_mean": sum(values) / len(values),
            "lfp_300um_rms": math.sqrt(sum(value * value for value in values) / len(values)),
            "lfp_300um_min": min(values), "lfp_300um_max": max(values),
            "soma_voltage_mean_mv": voltage_sum / voltage_samples,
            "soma_voltage_min_mv": voltage_low, "soma_voltage_max_mv": voltage_high,
        }
    return {
        "total_spikes": len(spkt), "cortical_cells": len(gid_to_pop),
        "population_sizes": pop_sizes, "windows": windows,
        "netpyne_pop_rates_hz": {pop: data["simData"]["popRates"].get(pop) for pop in POPS},
    }


def pre200_fingerprints(data: dict) -> dict[str, str]:
    """Hash every recorded pre-input signal in a stable numeric order.

    These hashes test observed identity, not whether the two network builds
    consumed identical random streams or whether Figure 10 is reproduced.
    """
    sim = data["simData"]
    n_pre = sum(float(time) < 200.0 for time in sim["t"])
    gids = sorted(
        cell["gid"] for cell in data["net"]["cells"]
        if cell["tags"].get("pop") in POPS
    )
    fingerprints: dict[str, str] = {}

    time_digest = hashlib.sha256(b"figure10-pre200-time-f64be-v1\0")
    time_digest.update(struct.pack(">I", n_pre))
    time_digest.update(struct.pack(f">{n_pre}d", *sim["t"][:n_pre]))
    fingerprints["time_grid"] = time_digest.hexdigest()

    spike_digest = hashlib.sha256(b"figure10-pre200-spikes-gid-time-v1\0")
    spikes = sorted(
        (float(time), int(gid))
        for gid, time in zip(sim["spkid"], sim["spkt"])
        if float(time) < 200.0
    )
    spike_digest.update(struct.pack(">I", len(spikes)))
    for time, gid in spikes:
        spike_digest.update(struct.pack(">qd", gid, time))
    fingerprints["spikes"] = spike_digest.hexdigest()

    lfp_digest = hashlib.sha256(b"figure10-pre200-lfp-15channels-f64be-v1\0")
    lfp_digest.update(struct.pack(">I", n_pre))
    for row in sim["LFP"][:n_pre]:
        lfp_digest.update(struct.pack(">15d", *row))
    fingerprints["lfp_all_depths"] = lfp_digest.hexdigest()

    voltage_digest = hashlib.sha256(b"figure10-pre200-six-sections-f64be-v1\0")
    voltage_digest.update(struct.pack(">II", len(gids), n_pre))
    for section in VOLTAGE_SECTIONS:
        voltage_digest.update(section.encode("ascii") + b"\0")
        traces = sim[f"V_{section}"]
        for gid in gids:
            voltage_digest.update(struct.pack(">q", gid))
            voltage_digest.update(struct.pack(f">{n_pre}d", *traces[f"cell_{gid}"][:n_pre]))
    fingerprints["voltage_all_cells_sections"] = voltage_digest.hexdigest()
    return fingerprints


def _validated_summary(root: Path, *, thalamic_input: bool) -> dict:
    request = validate_prepared_bundle(root)
    spec = request["spec"]
    if spec["profile"] != "paper" or spec["thalamic_input"] is not thalamic_input:
        raise ValueError("Comparison requires paper-profile TH=False and TH=True candidates")
    run = json.loads((root / "run_manifest.json").read_text(encoding="utf-8"))
    qa = json.loads((root / "analysis-raw-validation-v1.json").read_text(encoding="utf-8"))
    if (
        run.get("state") != "completed_unverified"
        or qa.get("status") != "structural_pass_not_paper_acceptance"
        or run.get("scientific_acceptance") is not False
        or qa.get("scientific_acceptance") is not False
        or qa.get("source_commit") != MODEL_COMMIT
        or qa.get("source_fingerprint") != SOURCE_FINGERPRINT
    ):
        raise ValueError("Figure 10 inputs lack structural QA or pinned source provenance")
    raw = root / run["raw_data_file"]
    if not raw.is_file() or run["raw_data_bytes"] != raw.stat().st_size:
        raise ValueError("Figure 10 raw size changed after validation")
    raw_hash = _sha256(raw)
    if raw_hash != run["raw_data_sha256"] or raw_hash != qa["raw_data_sha256"]:
        raise ValueError("Figure 10 raw hash changed after validation")
    if psutil.virtual_memory().available < max(4 * 1024**3, 8 * raw.stat().st_size):
        raise RuntimeError("Insufficient memory to compare full Figure 10 raw JSON")
    with raw.open("r", encoding="utf-8") as stream:
        data = json.load(stream)
    result = summarize(data)
    result["pre200_recorded_signal_sha256"] = pre200_fingerprints(data)
    del data
    gc.collect()
    result["run_folder"] = str(root)
    result["raw_data_sha256"] = raw_hash
    result["runtime_versions"] = run["runtime_versions"]
    result["source_thalamic_events_per_cell"] = qa.get("source_thalamic_events_per_cell", {})
    return result


def compare(th_off: Path, th_on: Path, output_dir: Path) -> Path:
    if output_dir.exists():
        raise FileExistsError("Figure 10 comparison output already exists")
    off = _validated_summary(th_off, thalamic_input=False)
    on = _validated_summary(th_on, thalamic_input=True)
    if off["runtime_versions"] != on["runtime_versions"] or off["population_sizes"] != on["population_sizes"]:
        raise ValueError("Figure 10 candidates have different runtimes or cortical population sizes")
    differences = {}
    for name, _, _ in WINDOWS:
        before = off["windows"][name]
        after = on["windows"][name]
        differences[name] = {
            "spike_count_th_on_minus_off": after["spike_count"] - before["spike_count"],
            "rate_hz_th_on_minus_off": {
                pop: after["rates_hz_by_population"][pop] - before["rates_hz_by_population"][pop]
                for pop in POPS
            },
            "lfp_300um_rms_th_on_minus_off": after["lfp_300um_rms"] - before["lfp_300um_rms"],
            "soma_voltage_mean_mv_th_on_minus_off": after["soma_voltage_mean_mv"] - before["soma_voltage_mean_mv"],
        }
    pre200_equal_by_signal = {
        signal: digest == on["pre200_recorded_signal_sha256"][signal]
        for signal, digest in off["pre200_recorded_signal_sha256"].items()
    }
    report = {
        "schema_version": 2, "paper_figure": 10, "status": "candidate_comparison_not_paper_acceptance",
        "source_commit": MODEL_COMMIT, "source_fingerprint": SOURCE_FINGERPRINT,
        "th_off": off, "th_on": on, "differences": differences,
        "pre200_recorded_signals_equal_by_sha256": pre200_equal_by_signal,
        "pre200_all_recorded_signals_equal": all(pre200_equal_by_signal.values()),
        "caveat": (
            "TH=True adds 445 source-defined virtual input cells and scheduled L4e/L4i events at 200 ms. "
            "The pre-200 ms checks cover saved spikes, time, all LFP depths, and every cortical "
            "voltage trace, not unsaved state or random-stream identity. Post-200 ms differences "
            "are descriptive, not independently validated causality or paper acceptance."
        ),
    }
    output_dir.mkdir(parents=True, exist_ok=False)
    target = output_dir / "comparison.json"
    target.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--th-off", type=Path, required=True)
    parser.add_argument("--th-on", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(compare(args.th_off, args.th_on, args.output_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
