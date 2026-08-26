#!/usr/bin/env python3
"""Run the bounded Escape-SIZ Arbor stimulus-source baseline diagnostic.

This worker is deliberately separate from :mod:`arbor_escape_siz_worker`.
The production comparison remains locked to its complete 99 ms, ten-pulse,
paired-condition recipe.  This diagnostic keeps the same 49-cell gap-off
circuit and biophysics, but runs one 0.4 ms pulse in a 5 ms window and records
only named soma voltages.  It then streams the matching window from an
existing NEURON gap-disabled reference and requires all eleven stimulated
GFC2 soma traces to pass the declared trace tolerances.

Digifly Public is input-only.  Every generated file is confined below the
explicit ``--output-root``.
"""

from __future__ import annotations

import argparse
import copy
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence


sys.dont_write_bytecode = True

try:
    from arbor_escape_siz_worker import (
        EXPECTED_BRANCH_HH,
        EXPECTED_LEGACY_SEED_AIS_NODE_IDS,
        EXPECTED_LEGACY_SEED_AIS_SEGMENT_COUNTS,
        EXPECTED_SOMA_HH,
        GFC2_IDS,
        LEGACY_SEED_AIS_POLICY,
        _assert_core_biophysics_contract,
        _assert_legacy_seed_ais_contract,
        _validate_arbor_seed_ais_runtime,
    )
except ModuleNotFoundError:
    from digifly_app.workers.arbor_escape_siz_worker import (
        EXPECTED_BRANCH_HH,
        EXPECTED_LEGACY_SEED_AIS_NODE_IDS,
        EXPECTED_LEGACY_SEED_AIS_SEGMENT_COUNTS,
        EXPECTED_SOMA_HH,
        GFC2_IDS,
        LEGACY_SEED_AIS_POLICY,
        _assert_core_biophysics_contract,
        _assert_legacy_seed_ais_contract,
        _validate_arbor_seed_ais_runtime,
    )


EXPECTED_SELECTED_IDS = (
    10000,
    10002,
    10068,
    10110,
    11446,
    11654,
    10074,
    10361,
    18309,
    169914,
    10014,
    10088,
    10589,
    10592,
    10892,
    10228,
    12191,
    13127,
    13479,
    13645,
    13658,
    13846,
    14527,
    14662,
    15292,
    15505,
    15938,
    16764,
    17245,
    17383,
    17458,
    21601,
    23606,
    24198,
    24412,
    24436,
    25080,
    25215,
    25629,
    26376,
    27502,
    31823,
    40029,
    41708,
    42164,
    43758,
    101102,
    101549,
    163891,
)

DIAGNOSTIC_PROFILE = "escape_siz_gap_off_source_soma_one_pulse_v1"
APP_RECIPE = "ablation_notebook_arbor_exact_gap_v2"
CV_POLICY_TOKEN = "legacy_neuron_section_explicit"
WINDOW_MS = 5.0
DT_MS = 0.01
SAMPLE_DT_MS = 0.01
EXPECTED_SAMPLES = 501
PULSE_DELAY_MS = 1.0
PULSE_DURATION_MS = 0.4
PULSE_FREQUENCY_HZ = 100.0
PULSE_AMPLITUDE_NA = 0.9
LEGACY_NSEG_UM = 40.0
MAX_RECORDS_BYTES = 2_000_000
EXPECTED_FILTERED_CHEMICAL_ROWS = 2331
EXPECTED_GAP_CONTACTS = 959
EXPECTED_GAP_HANDLES = 1918
EXPECTED_CONTACT_SITE_NA_MULTIPLIER = 2.5

TRACE_THRESHOLDS: dict[str, float] = {
    "trace_pearson_min": 0.998,
    "trace_rmse_max_mV": 2.0,
    "trace_mae_max_mV": 1.0,
    "trace_nrmse_max": 0.025,
    "trace_max_abs_error_mV": 25.0,
    "pre_stim_rmse_max_mV": 0.5,
    "pre_stim_mean_offset_max_mV": 0.5,
    "crossing_latency_error_max_ms": 0.02,
    "peak_amplitude_error_max_mV": 5.0,
    "peak_time_error_max_ms": 0.02,
    "range_ratio_min": 0.90,
    "range_ratio_max": 1.10,
    "recovery_rmse_max_mV": 2.0,
    "endpoint_error_max_mV": 2.0,
    "low_dynamic_range_mV": 1.0,
    "low_dynamic_rmse_max_mV": 2.0,
    "low_dynamic_max_abs_error_mV": 5.0,
}


class SourceSomaDiagnosticError(RuntimeError):
    """Saved or generated evidence cannot support this diagnostic."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--digifly-public-root", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--gap-catalogue", required=True)
    parser.add_argument(
        "--neuron-gap-disabled-run",
        required=True,
        help="Saved NEURON gap-disabled run directory containing records.csv.",
    )
    parser.add_argument(
        "--threads",
        type=int,
        choices=(1,),
        default=1,
        help="Quarantined legacy-CV diagnostics are restricted to one Arbor thread.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    public_root = Path(args.digifly_public_root).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    run_root = diagnostic_run_root(output_root)
    _reject_public_output(run_root, public_root)
    summary_path = run_root / "source_soma_diagnostic_summary.json"
    _write_json(
        summary_path,
        {
            "status": "validating",
            "passed": False,
            "profile": DIAGNOSTIC_PROFILE,
            "equivalence_claim": False,
            "topology_equivalence_claim": False,
            "started_at": _stamp(),
            "summary_json": str(summary_path.resolve()),
        },
    )
    try:
        summary = _run(args, run_root)
    except Exception as exc:
        failure = {
            "status": "failed",
            "passed": False,
            "profile": DIAGNOSTIC_PROFILE,
            "equivalence_claim": False,
            "topology_equivalence_claim": False,
            "failed_at": _stamp(),
            "error": str(exc),
            "summary_json": str(summary_path.resolve()),
        }
        _write_json(summary_path, failure)
        _event("error", "Arbor source-soma diagnostic failed", detail=str(exc))
        raise
    _write_json(summary_path, summary)
    _event(
        "complete",
        "Arbor source-soma diagnostic completed",
        passed=bool(summary["passed"]),
        passing_sources=int(summary["comparison"]["passing_source_count"]),
        required_sources=len(GFC2_IDS),
        path=str(summary_path.resolve()),
    )
    return 0


def diagnostic_run_root(output_root: str | Path, *, stamp: str | None = None) -> Path:
    root = Path(output_root).expanduser().resolve()
    token = stamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    if not token or any(char not in "0123456789TZ" for char in token):
        raise SourceSomaDiagnosticError(f"Unsafe diagnostic timestamp token: {token!r}")
    return (
        root
        / "escape_siz"
        / "arbor"
        / "source_soma_diagnostics"
        / f"gap_off_one_pulse_{token}"
    ).resolve()


def build_source_diagnostic_config(
    base_config: Mapping[str, Any],
    *,
    run_root: str | Path,
    filtered_edges: str | Path,
    legacy_bridge_metadata: Mapping[str, Any],
) -> dict[str, Any]:
    """Create the bounded config from a validated exact gap-off plan."""

    root = Path(run_root).expanduser().resolve()
    edges = Path(filtered_edges).expanduser().resolve()
    _assert_exact_base_config(base_config, edges)
    _assert_legacy_bridge_metadata(legacy_bridge_metadata)

    config = copy.deepcopy(dict(base_config))
    config["runs_root"] = str(root)
    config["run_id"] = "arbor_gap_disabled"
    config["tstop_ms"] = WINDOW_MS
    config["dt_ms"] = DT_MS

    gap = dict(config.get("gap") or {})
    gap.update({"enabled": False, "edges_path": None, "pairs": []})
    config["gap"] = gap

    pulse = dict((config.get("stim") or {}).get("pulse_train") or {})
    pulse.update(
        {
            "enabled": True,
            "freq_hz": PULSE_FREQUENCY_HZ,
            "amp_nA": 0.0,
            "amps_by_gid": {str(neuron_id): PULSE_AMPLITUDE_NA for neuron_id in GFC2_IDS},
            "delay_ms": PULSE_DELAY_MS,
            "dur_ms": PULSE_DURATION_MS,
            "location": "soma",
            "stop_ms": WINDOW_MS,
            "max_pulses": 1,
            "include_base_iclamp": False,
        }
    )
    stim = dict(config.get("stim") or {})
    stim["pulse_train"] = pulse
    config["stim"] = stim
    # A resolved config may contain the loader's top-level pulse copy.  Never
    # permit a stale ten-pulse copy to shadow the diagnostic plan.
    config.pop("pulse_train", None)

    record = {
        "soma_v": list(GFC2_IDS),
        "spikes": list(GFC2_IDS),
        "spike_thresh_mV": 0.0,
        "spike_detector_label": "spike_detector",
        "spike_source_location": "soma",
        "voltage_locations": ["soma"],
        "sample_dt_ms": SAMPLE_DT_MS,
    }
    config["record"] = record

    arbor_cfg = dict(config.get("arbor") or {})
    arbor_cfg.update(
        {
            "cv_policy": CV_POLICY_TOKEN,
            "legacy_section_nseg_um": LEGACY_NSEG_UM,
            "strict_connections": True,
            "threads": 1,
            "mpi": False,
            "gpu_id": None,
        }
    )
    config["arbor"] = arbor_cfg
    parallel = dict(config.get("parallel") or {})
    parallel["threads"] = 1
    config["parallel"] = parallel

    metadata = dict(config.get("metadata") or {})
    metadata.update(
        {
            "app_recipe": APP_RECIPE,
            "diagnostic_profile": DIAGNOSTIC_PROFILE,
            "diagnostic_window_ms": WINDOW_MS,
            "diagnostic_required_source_ids": list(GFC2_IDS),
            "diagnostic_required_pass_fraction": 1.0,
            "legacy_cv_bridge": dict(legacy_bridge_metadata),
            "equivalence_claim": False,
            "topology_equivalence_claim": False,
        }
    )
    config["metadata"] = metadata
    config["run_notes"] = (
        "Digifly App bounded source-baseline diagnostic: exact 49-cell Escape-SIZ gap-off "
        "circuit, eleven GFC2 cells at 0.9 nA, one 0.4 ms pulse, 5 ms total, named soma "
        "voltage only. The explicit grouped CV policy is a compatibility candidate; no "
        "topology or backend-equivalence claim is made."
    )
    assert_source_diagnostic_config(
        config,
        run_root=root,
        filtered_edges=edges,
        legacy_bridge_metadata=legacy_bridge_metadata,
    )
    return config


def assert_source_diagnostic_config(
    config: Mapping[str, Any],
    *,
    run_root: str | Path,
    filtered_edges: str | Path,
    legacy_bridge_metadata: Mapping[str, Any],
) -> None:
    """Fail closed unless the runnable plan is exactly the bounded profile."""

    root = Path(run_root).expanduser().resolve()
    edges = Path(filtered_edges).expanduser().resolve()
    selection = tuple(
        int(value) for value in list((config.get("selection") or {}).get("neuron_ids") or [])
    )
    seeds = tuple(int(value) for value in list(config.get("seeds") or []))
    if selection != EXPECTED_SELECTED_IDS:
        raise SourceSomaDiagnosticError("Diagnostic selection is not the exact ordered 49-cell circuit.")
    if seeds != GFC2_IDS:
        raise SourceSomaDiagnosticError("Diagnostic seeds are not the exact eleven GFC2 cells.")
    if Path(str(config.get("edges_path") or "")).expanduser().resolve() != edges:
        raise SourceSomaDiagnosticError("Diagnostic did not bind the app-owned filtered chemical table.")
    if Path(str(config.get("runs_root") or "")).expanduser().resolve() != root:
        raise SourceSomaDiagnosticError("Diagnostic runs_root escaped its dedicated run root.")
    if str(config.get("run_id")) != "arbor_gap_disabled":
        raise SourceSomaDiagnosticError("Diagnostic run_id changed.")
    if not _same_float(config.get("tstop_ms"), WINDOW_MS) or not _same_float(config.get("dt_ms"), DT_MS):
        raise SourceSomaDiagnosticError("Diagnostic must use exactly 5 ms at dt=0.01 ms.")
    try:
        _assert_core_biophysics_contract(config)
        _assert_legacy_seed_ais_contract(config)
    except RuntimeError as exc:
        raise SourceSomaDiagnosticError(
            f"Diagnostic effective biophysics do not match the locked NEURON contract: {exc}"
        ) from exc

    gap = dict(config.get("gap") or {})
    if bool(gap.get("enabled")) or gap.get("edges_path") not in (None, "") or list(gap.get("pairs") or []):
        raise SourceSomaDiagnosticError("Diagnostic must have no active gap table or inline gap pairs.")

    pulse = dict((config.get("stim") or {}).get("pulse_train") or {})
    amps = {int(key): float(value) for key, value in dict(pulse.get("amps_by_gid") or {}).items()}
    exact_pulse = bool(
        pulse.get("enabled")
        and _same_float(pulse.get("freq_hz"), PULSE_FREQUENCY_HZ)
        and _same_float(pulse.get("amp_nA"), 0.0)
        and tuple(amps) == GFC2_IDS
        and all(_same_float(value, PULSE_AMPLITUDE_NA) for value in amps.values())
        and _same_float(pulse.get("delay_ms"), PULSE_DELAY_MS)
        and _same_float(pulse.get("dur_ms"), PULSE_DURATION_MS)
        and str(pulse.get("location")) == "soma"
        and _same_float(pulse.get("stop_ms"), WINDOW_MS)
        and int(pulse.get("max_pulses") or 0) == 1
        and not bool(pulse.get("include_base_iclamp"))
    )
    if not exact_pulse:
        raise SourceSomaDiagnosticError("Diagnostic stimulus is not the exact eleven-cell one-pulse profile.")

    record = dict(config.get("record") or {})
    if tuple(record.get("voltage_locations") or []) != ("soma",):
        raise SourceSomaDiagnosticError("Diagnostic may record only the named soma voltage location.")
    if tuple(int(value) for value in list(record.get("soma_v") or [])) != GFC2_IDS:
        raise SourceSomaDiagnosticError("Diagnostic soma recording declaration changed.")
    if tuple(int(value) for value in list(record.get("spikes") or [])) != GFC2_IDS:
        raise SourceSomaDiagnosticError("Diagnostic spike recording declaration changed.")
    if not _same_float(record.get("sample_dt_ms"), SAMPLE_DT_MS):
        raise SourceSomaDiagnosticError("Diagnostic sampling must match the 0.01 ms NEURON grid.")
    if record.get("all_compartment_voltage") not in (None, {}, False):
        raise SourceSomaDiagnosticError("All-compartment recording is forbidden in the bounded diagnostic.")
    if list(record.get("node_voltage_probes") or []):
        raise SourceSomaDiagnosticError("Node/contact probes are forbidden in the bounded diagnostic.")

    arbor_cfg = dict(config.get("arbor") or {})
    if str(arbor_cfg.get("cv_policy")) != CV_POLICY_TOKEN:
        raise SourceSomaDiagnosticError("Legacy CV compatibility policy was not selected.")
    if not _same_float(config.get("swc_section_nseg_um"), LEGACY_NSEG_UM):
        raise SourceSomaDiagnosticError("Legacy section spacing is not exactly 40 um.")
    if not bool(arbor_cfg.get("strict_connections", False)):
        raise SourceSomaDiagnosticError("Strict Arbor placement is required.")
    if (
        int(arbor_cfg.get("threads") or 0) != 1
        or bool(arbor_cfg.get("mpi"))
        or arbor_cfg.get("gpu_id") is not None
        or int((config.get("parallel") or {}).get("threads") or 0) != 1
    ):
        raise SourceSomaDiagnosticError(
            "Legacy CV diagnostics are restricted to one CPU thread with MPI/GPU disabled."
        )
    _assert_legacy_bridge_metadata(legacy_bridge_metadata)
    metadata = dict(config.get("metadata") or {})
    if str(metadata.get("app_recipe")) != APP_RECIPE:
        raise SourceSomaDiagnosticError("Diagnostic lost the exact app-recipe identity.")
    if dict(metadata.get("legacy_cv_bridge") or {}) != dict(legacy_bridge_metadata):
        raise SourceSomaDiagnosticError("Diagnostic did not save the installed legacy bridge metadata.")
    if bool(metadata.get("equivalence_claim")) or bool(metadata.get("topology_equivalence_claim")):
        raise SourceSomaDiagnosticError("Diagnostic metadata made an unsupported equivalence claim.")


def compare_source_soma_records(
    *,
    neuron_run: str | Path,
    arbor_records: str | Path,
    thresholds: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Stream and compare the exact first-pulse source-soma window."""

    reference = Path(neuron_run).expanduser().resolve()
    candidate = Path(arbor_records).expanduser().resolve()
    reference_evidence = validate_neuron_gap_disabled_reference(reference)
    resolved_thresholds = {**TRACE_THRESHOLDS, **dict(thresholds or {})}
    _validate_thresholds(resolved_thresholds)

    neuron_columns = [f"{neuron_id}_soma_v" for neuron_id in GFC2_IDS]
    arbor_columns = [f"soma_v__{neuron_id}" for neuron_id in GFC2_IDS]
    neuron_path = reference / "records.csv"
    neuron_times, neuron_values, neuron_read = _read_trace_window(neuron_path, neuron_columns)
    arbor_times, arbor_values, arbor_read = _read_trace_window(candidate, arbor_columns)
    _assert_exact_grid(neuron_times, neuron_path)
    arbor_grid = _assert_dense_arbor_grid(arbor_times, candidate)

    rows: list[dict[str, Any]] = []
    for neuron_id, neuron_column, arbor_column in zip(GFC2_IDS, neuron_columns, arbor_columns):
        neuron_on_arbor_grid = _interpolate_trace(
            neuron_times,
            neuron_values[neuron_column],
            arbor_times,
        )
        metric = _trace_metric(
            arbor_times,
            neuron_on_arbor_grid,
            arbor_values[arbor_column],
            resolved_thresholds,
        )
        rows.append(
            {
                "neuron_id": int(neuron_id),
                "condition": "gap_disabled",
                "location": "soma",
                "neuron_column": neuron_column,
                "arbor_column": arbor_column,
                **metric,
                "scientific_pass": bool(metric["trace_pass"]),
            }
        )
    passing_ids = [int(row["neuron_id"]) for row in rows if bool(row["scientific_pass"])]
    failed_ids = [int(row["neuron_id"]) for row in rows if not bool(row["scientific_pass"])]
    passed = len(passing_ids) == len(GFC2_IDS) and not failed_ids
    return {
        "profile": DIAGNOSTIC_PROFILE,
        "condition": "gap_disabled",
        "passed": passed,
        "required_source_count": len(GFC2_IDS),
        "comparable_source_count": len(rows),
        "passing_source_count": len(passing_ids),
        "required_pass_fraction": 1.0,
        "pass_fraction": float(len(passing_ids) / len(GFC2_IDS)),
        "passing_source_ids": passing_ids,
        "failed_source_ids": failed_ids,
        "window_ms": [0.0, WINDOW_MS],
        "sample_dt_ms": SAMPLE_DT_MS,
        "sample_count": len(arbor_times),
        "reference_sample_count": len(neuron_times),
        "arbor_sample_count": len(arbor_times),
        "comparison_grid": "arbor_epoch_schedule",
        "interpolation": "linear_neuron_to_arbor",
        "arbor_grid": arbor_grid,
        "thresholds": resolved_thresholds,
        "reference_validation": reference_evidence,
        "streaming": {
            "neuron_rows_consumed": neuron_read,
            "arbor_rows_consumed": arbor_read,
            "stopped_at_window_end": True,
            "full_neuron_records_hash_computed": False,
        },
        "metrics": rows,
    }


def validate_neuron_gap_disabled_reference(run_dir: str | Path) -> dict[str, Any]:
    """Validate the exact saved NEURON source-baseline evidence.

    The reference directory is caller-selected, so labels alone are
    insufficient.  Require its config, runtime response, paths, chemical
    table, and core biophysics to agree before any voltage values are used.
    """

    root = Path(run_dir).expanduser().resolve()
    config_path = root / "config.json"
    records_path = root / "records.csv"
    runtime_path = root / "contact_site_na_gfc_heatmap_summary.json"
    cell_biophys_path = root / "cell_biophys.csv"
    for path in (config_path, records_path, runtime_path, cell_biophys_path):
        if not path.is_file():
            raise SourceSomaDiagnosticError(f"Missing NEURON reference artifact: {path}")
    config = _read_json(config_path)
    runtime = _read_json(runtime_path)
    selection = tuple(
        int(value) for value in list((config.get("selection") or {}).get("neuron_ids") or [])
    )
    seeds = tuple(int(value) for value in list(config.get("seeds") or []))
    if selection != EXPECTED_SELECTED_IDS or seeds != GFC2_IDS:
        raise SourceSomaDiagnosticError("NEURON reference is not the exact ordered 49-cell/11-seed run.")
    if not _same_float(config.get("dt_ms"), DT_MS) or float(config.get("tstop_ms") or 0.0) < WINDOW_MS:
        raise SourceSomaDiagnosticError("NEURON reference cannot cover the exact 0-5 ms grid.")
    _assert_neuron_reference_biophysics(config)
    effective_biophysics = _validate_neuron_effective_biophysics(cell_biophys_path)

    record = dict(config.get("record") or {})
    try:
        soma_recordings = tuple(int(value) for value in list(record.get("soma_v") or []))
    except (TypeError, ValueError) as exc:
        raise SourceSomaDiagnosticError(
            "NEURON reference has an invalid soma recording declaration."
        ) from exc
    missing_soma = [neuron_id for neuron_id in GFC2_IDS if neuron_id not in soma_recordings]
    if missing_soma or len(soma_recordings) != len(set(soma_recordings)):
        raise SourceSomaDiagnosticError(
            "NEURON reference does not record every source soma exactly once: "
            f"missing={missing_soma}."
        )

    pulse = dict(config.get("pulse_train") or (config.get("stim") or {}).get("pulse_train") or {})
    amps = {int(key): float(value) for key, value in dict(pulse.get("amps_by_gid") or {}).items()}
    if not (
        bool(pulse.get("enabled"))
        and tuple(amps) == GFC2_IDS
        and all(_same_float(value, PULSE_AMPLITUDE_NA) for value in amps.values())
        and _same_float(pulse.get("freq_hz"), PULSE_FREQUENCY_HZ)
        and _same_float(pulse.get("delay_ms"), PULSE_DELAY_MS)
        and _same_float(pulse.get("dur_ms"), PULSE_DURATION_MS)
        and str(pulse.get("location")) == "soma"
        and not bool(pulse.get("include_base_iclamp"))
        and int(pulse.get("max_pulses") or 0) >= 1
        and float(pulse.get("stop_ms") or 0.0) >= WINDOW_MS
    ):
        raise SourceSomaDiagnosticError("NEURON reference first-pulse contract changed.")

    runtime_root = _required_resolved_path(runtime.get("run_dir"), "NEURON runtime run_dir")
    if runtime_root != root:
        raise SourceSomaDiagnosticError(
            f"NEURON runtime run_dir does not identify its containing run: {runtime_root}"
        )
    cache_response = dict(runtime.get("cache_response") or {})
    cache_out = _required_resolved_path(
        cache_response.get("out_dir"), "NEURON cache response out_dir"
    )
    cache_baseline_out = _required_resolved_path(
        cache_response.get("baseline_out_dir"),
        "NEURON cache response baseline_out_dir",
    )
    cache_records = _required_resolved_path(
        cache_response.get("records_csv"), "NEURON cache response records_csv"
    )
    if cache_out != root or cache_baseline_out != root or cache_records != records_path:
        raise SourceSomaDiagnosticError(
            "NEURON runtime out_dir/records_csv evidence is not self-consistent."
        )
    try:
        cache_returncode = int(cache_response.get("returncode"))
    except (TypeError, ValueError) as exc:
        raise SourceSomaDiagnosticError("NEURON cache response has no valid return code.") from exc
    try:
        final_network_ids = tuple(
            int(value) for value in list(cache_response.get("final_network_ids") or [])
        )
        cache_seed_ids = tuple(int(value) for value in list(cache_response.get("seed_ids") or []))
    except (TypeError, ValueError) as exc:
        raise SourceSomaDiagnosticError("NEURON cache response has invalid circuit IDs.") from exc
    if (
        str(cache_response.get("status")) != "ok"
        or cache_returncode != 0
        or final_network_ids != tuple(sorted(EXPECTED_SELECTED_IDS))
        or cache_seed_ids != GFC2_IDS
    ):
        raise SourceSomaDiagnosticError(
            "NEURON cache response does not prove the exact successful 49-cell/11-seed run."
        )

    config_edges = _required_resolved_path(config.get("edges_path"), "NEURON config edges_path")
    config_edges_csv = _required_resolved_path(
        config.get("edges_csv"), "NEURON config edges_csv"
    )
    runtime_edges = _required_resolved_path(
        cache_response.get("edges_path"), "NEURON cache response edges_path"
    )
    if config_edges != config_edges_csv or config_edges != runtime_edges:
        raise SourceSomaDiagnosticError(
            "NEURON config and runtime do not identify the same filtered chemical table."
        )
    chemical_evidence = _validate_filtered_chemical_edges(config_edges)

    gap_groups = list(cache_response.get("gap_group_summary") or [])
    if len(gap_groups) != 1 or not isinstance(gap_groups[0], Mapping):
        raise SourceSomaDiagnosticError(
            "NEURON runtime must contain exactly one gap-disabled application summary."
        )
    gap_group = dict(gap_groups[0])
    try:
        matched_gaps = int(gap_group.get("matched_gaps"))
        updated_handles = int(gap_group.get("updated_gap_handles"))
    except (TypeError, ValueError) as exc:
        raise SourceSomaDiagnosticError(
            "NEURON gap-disabled application summary has invalid counts."
        ) from exc
    if not (
        str(gap_group.get("name")) == "runtime_gap_profile_gap_disabled"
        and matched_gaps == EXPECTED_GAP_CONTACTS
        and updated_handles == EXPECTED_GAP_HANDLES
        and _same_float(gap_group.get("g_mult"), 0.0)
        and gap_group.get("g_uS") is None
        and gap_group.get("global_aggregated") is True
    ):
        raise SourceSomaDiagnosticError(
            "NEURON runtime does not prove all 959 gaps / 1,918 handles were set to zero."
        )

    runtime_amps = {
        int(key): float(value)
        for key, value in dict(runtime.get("stim_amp_nA_by_gid") or {}).items()
    }
    if not (
        str(runtime.get("gap_label")) == "gap_disabled"
        and _same_float(runtime.get("gap_profile_mult"), 0.0)
        and _same_float(
            runtime.get("contact_site_sodium_multiplier"),
            EXPECTED_CONTACT_SITE_NA_MULTIPLIER,
        )
        and str(runtime.get("circuit_label")) == "baseline_with_10002_GFCs"
        and runtime.get("separate_gfs") is True
        and tuple(int(value) for value in list(runtime.get("stim_target_ids") or [])) == GFC2_IDS
        and tuple(runtime_amps) == GFC2_IDS
        and all(_same_float(value, PULSE_AMPLITUDE_NA) for value in runtime_amps.values())
        and _same_float(runtime.get("stim_delay_ms"), PULSE_DELAY_MS)
        and _same_float(runtime.get("stim_dur_ms"), PULSE_DURATION_MS)
        and _same_float(runtime.get("stim_freq_hz"), PULSE_FREQUENCY_HZ)
        and float(runtime.get("tstop_ms") or 0.0) >= WINDOW_MS
    ):
        raise SourceSomaDiagnosticError("NEURON runtime evidence does not prove the exact gap-off condition.")
    return {
        "run_dir": str(root),
        "config_json": str(config_path),
        "runtime_summary_json": str(runtime_path),
        "records_csv": str(records_path),
        "cell_biophys_csv": str(cell_biophys_path),
        "records_size_bytes": int(records_path.stat().st_size),
        "records_mtime_ns": int(records_path.stat().st_mtime_ns),
        "config_sha256": _sha256(config_path),
        "runtime_summary_sha256": _sha256(runtime_path),
        "cell_biophys_sha256": _sha256(cell_biophys_path),
        "gap_label": "gap_disabled",
        "gap_profile_mult": 0.0,
        "contact_site_sodium_multiplier": EXPECTED_CONTACT_SITE_NA_MULTIPLIER,
        "runtime_paths_self_consistent": True,
        "cache_response_status": "ok",
        "cache_response_returncode": 0,
        "gap_contacts_zeroed": matched_gaps,
        "gap_handles_zeroed": updated_handles,
        "filtered_chemical_edges": chemical_evidence,
        "effective_biophysics": effective_biophysics,
        "full_records_hash_computed": False,
    }


def _assert_neuron_reference_biophysics(config: Mapping[str, Any]) -> None:
    if str(config.get("swc_section_mode")) != "branch" or not _same_float(
        config.get("swc_section_nseg_um"), LEGACY_NSEG_UM
    ):
        raise SourceSomaDiagnosticError(
            "NEURON reference lost the branch-section / 40 um discretization contract."
        )
    if (
        str(config.get("active_compartment_scope")) != "all"
        or str(config.get("active_posts_mode")) != "all_selected"
        or config.get("post_active") is not True
    ):
        raise SourceSomaDiagnosticError("NEURON reference active-compartment policy changed.")

    expected_hh = {
        "pre_branch_hh": EXPECTED_BRANCH_HH,
        "post_branch_hh": EXPECTED_BRANCH_HH,
        "pre_soma_hh": EXPECTED_SOMA_HH,
        "post_soma_hh": EXPECTED_SOMA_HH,
    }
    for key, expected in expected_hh.items():
        actual = dict(config.get(key) or {})
        if set(actual) != set(expected) or any(
            not _same_float(actual.get(parameter), value)
            for parameter, value in expected.items()
        ):
            raise SourceSomaDiagnosticError(f"NEURON reference {key} contract changed.")

    expected_scalars = {
        "passive_e": -65.0,
        "passive_g": 0.0001,
        "Ra": 100.0,
        "cm": 1.0,
        "v_init_mV": -65.0,
        "default_weight_uS": 0.000003,
        "default_delay_ms": 1.0,
        "syn_tau1_ms": 0.5,
        "syn_tau2_ms": 3.0,
        "syn_e_rev_mV": 0.0,
    }
    changed_scalars = [
        key for key, expected in expected_scalars.items() if not _same_float(config.get(key), expected)
    ]
    chemical = dict(config.get("chemical_synapse") or {})
    if (
        changed_scalars
        or config.get("use_geom_delay") is not True
        or chemical != {"aggregate_conductance": False, "max_sites_per_pair": None}
    ):
        raise SourceSomaDiagnosticError(
            "NEURON reference passive or chemical-synapse contract changed: "
            f"scalars={changed_scalars}."
        )


def _validate_neuron_effective_biophysics(path: Path) -> dict[str, Any]:
    """Validate the mechanisms measured from the completed NEURON cells."""

    required = {
        "neuron_id",
        "soma_sec",
        "soma_has_hh",
        "soma_gnabar_hh",
        "soma_gkbar_hh",
        "soma_gl_hh",
        "ais_sec",
        "ais_has_hh",
        "ais_gnabar_hh",
        "ais_gkbar_hh",
        "ais_gl_hh",
    }
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        if len(fieldnames) != len(set(fieldnames)) or not required.issubset(fieldnames):
            raise SourceSomaDiagnosticError(
                "NEURON cell_biophys.csv lacks unique effective-biophysics columns."
            )
        rows = [dict(row) for row in reader]
    by_neuron: dict[int, dict[str, str]] = {}
    for row in rows:
        try:
            neuron_id = int(row["neuron_id"])
        except (TypeError, ValueError) as exc:
            raise SourceSomaDiagnosticError(
                "NEURON cell_biophys.csv contains an invalid neuron_id."
            ) from exc
        if neuron_id in by_neuron:
            raise SourceSomaDiagnosticError(
                f"NEURON cell_biophys.csv duplicates neuron {neuron_id}."
            )
        by_neuron[neuron_id] = row
    if set(by_neuron) != set(EXPECTED_SELECTED_IDS):
        raise SourceSomaDiagnosticError(
            "NEURON cell_biophys.csv does not exactly cover the 49-cell circuit."
        )

    custom_has_columns = [
        column
        for column in fieldnames
        if column.startswith(("soma_has_", "ais_has_")) and not column.endswith("_hh")
    ]
    custom_gbar_columns = [
        column for column in fieldnames if column.startswith(("soma_gbar_", "ais_gbar_"))
    ]
    custom_suffix_columns = [
        column for column in ("soma_custom_channel_suffixes", "ais_custom_channel_suffixes")
        if column in fieldnames
    ]

    def require_conductances(
        row: Mapping[str, str],
        *,
        prefix: str,
        expected: Mapping[str, float],
        neuron_id: int,
    ) -> None:
        if str(row.get(f"{prefix}_has_hh", "")).strip().lower() != "true":
            raise SourceSomaDiagnosticError(
                f"NEURON neuron {neuron_id} {prefix} does not contain HH at runtime."
            )
        measured = {
            "gnabar": row.get(f"{prefix}_gnabar_hh"),
            "gkbar": row.get(f"{prefix}_gkbar_hh"),
            "gl": row.get(f"{prefix}_gl_hh"),
        }
        for parameter in ("gnabar", "gkbar", "gl"):
            if not _same_float(measured[parameter], expected[parameter]):
                raise SourceSomaDiagnosticError(
                    f"NEURON neuron {neuron_id} {prefix} runtime {parameter} changed: "
                    f"expected={expected[parameter]}, found={measured[parameter]!r}."
                )

    for neuron_id, row in by_neuron.items():
        require_conductances(
            row,
            prefix="soma",
            expected=EXPECTED_SOMA_HH,
            neuron_id=neuron_id,
        )
        expected_ais = EXPECTED_SOMA_HH if neuron_id in GFC2_IDS else EXPECTED_BRANCH_HH
        require_conductances(
            row,
            prefix="ais",
            expected=expected_ais,
            neuron_id=neuron_id,
        )
        if neuron_id in GFC2_IDS and (
            not str(row.get("soma_sec") or "").strip()
            or not str(row.get("ais_sec") or "").strip()
            or str(row.get("soma_sec")) == str(row.get("ais_sec"))
        ):
            raise SourceSomaDiagnosticError(
                f"NEURON seed {neuron_id} does not prove a distinct AIS Section."
            )
        active_custom = [
            column
            for column in custom_has_columns
            if str(row.get(column, "")).strip().lower() == "true"
        ]
        nonzero_custom = []
        for column in custom_gbar_columns:
            raw = str(row.get(column, "")).strip()
            if not raw:
                continue
            try:
                if not _same_float(float(raw), 0.0):
                    nonzero_custom.append(column)
            except ValueError:
                nonzero_custom.append(column)
        suffixes = [
            column
            for column in custom_suffix_columns
            if str(row.get(column, "")).strip() not in {"", "[]"}
        ]
        if active_custom or nonzero_custom or suffixes:
            raise SourceSomaDiagnosticError(
                f"NEURON exact reference unexpectedly uses custom channels on neuron {neuron_id}: "
                f"active={active_custom}, nonzero={nonzero_custom}, suffixes={suffixes}."
            )

    return {
        "status": "passed",
        "row_count": len(by_neuron),
        "all_soma_hh": dict(EXPECTED_SOMA_HH),
        "seed_ais_hh": dict(EXPECTED_SOMA_HH),
        "nonseed_ais_hh": dict(EXPECTED_BRANCH_HH),
        "seed_count": len(GFC2_IDS),
        "nonseed_count": len(EXPECTED_SELECTED_IDS) - len(GFC2_IDS),
        "custom_channels_active": False,
        "hh_el_source": "locked config; NEURON cell_biophys.csv measures conductances only",
        "cell_biophys_csv": str(path.resolve()),
        "cell_biophys_sha256": _sha256(path),
    }


def _required_resolved_path(raw_path: Any, label: str) -> Path:
    if not isinstance(raw_path, (str, Path)) or not str(raw_path).strip():
        raise SourceSomaDiagnosticError(f"Missing {label}.")
    return Path(raw_path).expanduser().resolve()


def _validate_filtered_chemical_edges(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise SourceSomaDiagnosticError(
            f"NEURON filtered chemical table does not exist: {path}"
        )
    row_count = 0
    direct_gf_rows = 0
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        if len(fieldnames) != len(set(fieldnames)) or not {"pre_id", "post_id"}.issubset(
            fieldnames
        ):
            raise SourceSomaDiagnosticError(
                "NEURON filtered chemical table lacks unique pre_id/post_id columns."
            )
        for row_number, row in enumerate(reader, start=2):
            try:
                pre_id = int(float(row["pre_id"]))
                post_id = int(float(row["post_id"]))
            except (KeyError, TypeError, ValueError) as exc:
                raise SourceSomaDiagnosticError(
                    f"Invalid chemical edge IDs at row {row_number}: {path}"
                ) from exc
            row_count += 1
            if (pre_id, post_id) in {(10000, 10002), (10002, 10000)}:
                direct_gf_rows += 1
    if row_count != EXPECTED_FILTERED_CHEMICAL_ROWS or direct_gf_rows != 0:
        raise SourceSomaDiagnosticError(
            "NEURON filtered chemical table contract changed: "
            f"rows={row_count}, direct_gf_rows={direct_gf_rows}."
        )
    return {
        "path": str(path.resolve()),
        "row_count": row_count,
        "direct_gf_rows": direct_gf_rows,
        "sha256": _sha256(path),
    }


def write_comparison_metrics(path: str | Path, comparison: Mapping[str, Any]) -> Path:
    destination = Path(path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = (
        "neuron_id",
        "condition",
        "location",
        "neuron_column",
        "arbor_column",
        "pearson_r",
        "rmse_mV",
        "mae_mV",
        "max_abs_error_mV",
        "nrmse",
        "baseline_range_mV",
        "arbor_range_mV",
        "baseline_min_mV",
        "baseline_max_mV",
        "arbor_min_mV",
        "arbor_max_mV",
        "pre_stim_rmse_mV",
        "pre_stim_mean_offset_mV",
        "recovery_rmse_mV",
        "endpoint_error_mV",
        "baseline_first_crossing_ms",
        "arbor_first_crossing_ms",
        "crossing_latency_error_ms",
        "baseline_trace_crossings",
        "arbor_trace_crossings",
        "baseline_peak_mV",
        "arbor_peak_mV",
        "peak_amplitude_error_mV",
        "baseline_peak_time_ms",
        "arbor_peak_time_ms",
        "peak_time_error_ms",
        "range_ratio",
        "failed_checks",
        "comparison_policy",
        "trace_pass",
        "scientific_pass",
    )
    with destination.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(list(comparison.get("metrics") or []))
    return destination


def _run(args: argparse.Namespace, run_root: Path) -> dict[str, Any]:
    public_root = Path(args.digifly_public_root).expanduser().resolve()
    neuron_run = Path(args.neuron_gap_disabled_run).expanduser().resolve()
    gap_catalogue = Path(args.gap_catalogue).expanduser().resolve()
    if int(args.threads) != 1:
        raise SourceSomaDiagnosticError(
            "The legacy-CV diagnostic is quarantined to one Arbor thread after a native "
            "four-thread SIGBUS; no automatic fallback or retry is permitted."
        )
    if not gap_catalogue.is_file():
        raise FileNotFoundError(f"Required app-owned Arbor gap catalogue is missing: {gap_catalogue}")
    _reject_public_output(run_root, public_root)
    _assert_inside(run_root, Path(args.output_root), "diagnostic root")

    staging_root = public_root / "Phase 2_Arbor_staging"
    project_root = staging_root / "Projects" / "Escape-SIZ" / "Giant Fiber Ablation Comparisons"
    input_root = staging_root / "Projects" / "Escape-SIZ" / "arbor_inputs" / "giant_fiber_ablation"
    helper_path = project_root / "giant_fiber_ablation_arbor.py"
    filtered_edges = run_root / "_inputs" / "chemical_edges_no_direct_gf.csv"
    plan_path = run_root / "_plans" / "arbor_gap_disabled_config.json"
    provenance_path = run_root / "diagnostic_provenance.json"
    metrics_path = run_root / "source_soma_metrics.csv"

    for path in (staging_root, project_root):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    import arbor  # type: ignore
    import giant_fiber_ablation_arbor as gfa  # type: ignore
    from digifly.phase2.arbor_build import runner as arbor_runner  # type: ignore

    try:
        import arbor_escape_siz_worker as exact_worker
    except ModuleNotFoundError:
        from digifly_app.workers import arbor_escape_siz_worker as exact_worker
    try:
        from arbor_gap_bridge import install_arbor_gap_bridge
    except ModuleNotFoundError:
        from digifly_app.workers.arbor_gap_bridge import install_arbor_gap_bridge
    try:
        from arbor_legacy_cv_bridge import install_arbor_legacy_cv_bridge
    except ModuleNotFoundError:
        from digifly_app.workers.arbor_legacy_cv_bridge import install_arbor_legacy_cv_bridge

    if str(getattr(arbor, "__version__", "unknown")) != "0.12.2":
        raise SourceSomaDiagnosticError(
            f"The bounded diagnostic requires Arbor 0.12.2; found {getattr(arbor, '__version__', 'unknown')}."
        )
    gap_bridge = install_arbor_gap_bridge(
        arbor_module=arbor,
        runner_module=arbor_runner,
        catalogue_path=gap_catalogue,
    )
    gap_bridge_metadata = dict(gap_bridge.metadata)
    exact_worker._assert_gap_bridge_metadata(
        gap_bridge_metadata,
        gap_catalogue=gap_catalogue,
        gap_catalogue_sha256=_sha256(gap_catalogue),
    )
    legacy_bridge = install_arbor_legacy_cv_bridge(
        arbor_module=arbor,
        runner_module=arbor_runner,
    )
    legacy_bridge_metadata = dict(legacy_bridge.metadata)
    _assert_legacy_bridge_metadata(legacy_bridge_metadata)

    input_snapshot = exact_worker._input_snapshot(input_root)
    source_chemical = input_root / "chemical_edges.csv"
    gap_source = input_root / "gap_contacts.csv"
    gap_arbor = input_root / "gap_contacts_arbor.csv"
    contact_nodes = input_root / "contact_site_nodes.json"
    manifest = input_root / "manifest.json"
    input_validation = exact_worker._validate_input_contract(
        native_validation=dict(gfa.validate_inputs()),
        source_chemical=source_chemical,
        gap_source=gap_source,
        gap_arbor=gap_arbor,
        contact_nodes=contact_nodes,
        manifest=manifest,
        swc_tools=arbor_runner,
    )
    input_validation.update(exact_worker._materialize_filtered_edges(source_chemical, filtered_edges))
    seed_ais_nodes = {
        int(neuron_id): int(node_id)
        for neuron_id, node_id in dict(
            input_validation["legacy_seed_ais_biophysics"]["node_ids_by_neuron"]
        ).items()
    }
    gfa.CHEMICAL_EDGES = filtered_edges.resolve()

    exact_args = argparse.Namespace(
        contact_site_na_multiplier=2.5,
        static_reverse_fraction=0.20,
        requested_hetero_g_closed_frac=0.0,
        requested_vhalf_mV=0.0,
        requested_vslope_mV=5.0,
        requested_empirical_residual_frac=0.20,
        requested_tau_open_ms=6.0,
        requested_tau_close_ms=2.0,
        freq_hz=100.0,
        max_pulses=10,
        stim_amp_nA=0.9,
        stim_dur_ms=0.4,
        dt_ms=0.01,
        sample_dt_ms=0.05,
        threads=int(args.threads),
        # Build the shared production base through its own supported policy;
        # build_source_diagnostic_config() then replaces this only inside the
        # quarantined legacy-CV diagnostic plan.
        cv_policy=str(exact_worker.CV_POLICY),
        cv_max_extent_um=20.0,
    )
    base = exact_worker._condition_config(
        gfa,
        exact_args,
        "gap_disabled",
        run_root,
        filtered_edges,
        gap_catalogue=gap_catalogue,
        gap_catalogue_sha256=_sha256(gap_catalogue),
        gap_bridge_metadata=gap_bridge_metadata,
        seed_ais_nodes=seed_ais_nodes,
    )
    config = build_source_diagnostic_config(
        base,
        run_root=run_root,
        filtered_edges=filtered_edges,
        legacy_bridge_metadata=legacy_bridge_metadata,
    )
    _write_json(plan_path, config)
    _write_json(
        provenance_path,
        {
            "schema_version": 1,
            "created_at": _stamp(),
            "profile": DIAGNOSTIC_PROFILE,
            "equivalence_claim": False,
            "topology_equivalence_claim": False,
            "digifly_public_root": str(public_root),
            "diagnostic_root": str(run_root),
            "helper": {"path": str(helper_path), "sha256": _sha256(helper_path)},
            "gap_catalogue": {"path": str(gap_catalogue), "sha256": _sha256(gap_catalogue)},
            "gap_bridge": gap_bridge_metadata,
            "legacy_cv_bridge": legacy_bridge_metadata,
            "input_validation": input_validation,
            "neuron_reference": validate_neuron_gap_disabled_reference(neuron_run),
        },
    )

    _event("stage", "Running bounded Arbor gap-off source-soma diagnostic", window_ms=WINDOW_MS)
    run_dir = Path(gfa.run_walking_simulation(config, strict=True)).expanduser().resolve()
    _assert_inside(run_dir, run_root, "Arbor diagnostic run")
    artifacts = _validate_generated_run(
        run_dir,
        run_root=run_root,
        filtered_edges=filtered_edges,
        legacy_bridge_metadata=legacy_bridge_metadata,
    )
    comparison = compare_source_soma_records(
        neuron_run=neuron_run,
        arbor_records=artifacts["records_csv"],
    )
    write_comparison_metrics(metrics_path, comparison)
    _assert_inside(metrics_path, run_root, "source soma metrics")
    if exact_worker._input_snapshot(input_root) != input_snapshot:
        raise SourceSomaDiagnosticError("The bounded diagnostic changed the Arbor input bundle.")

    generated = {
        "run_dir": str(run_dir),
        **artifacts,
        "planned_config_json": str(plan_path.resolve()),
        "provenance_json": str(provenance_path.resolve()),
        "metrics_csv": str(metrics_path.resolve()),
    }
    for label, raw_path in generated.items():
        _assert_inside(Path(raw_path), run_root, label)
    return {
        "status": "complete",
        "passed": bool(comparison["passed"]),
        "structural_parameter_parity": _read_json(
            Path(artifacts["seed_ais_biophysics_json"])
        ),
        "profile": DIAGNOSTIC_PROFILE,
        "equivalence_claim": False,
        "topology_equivalence_claim": False,
        "completed_at": _stamp(),
        "backend": "arbor",
        "condition": "gap_disabled",
        "window_ms": WINDOW_MS,
        "dt_ms": DT_MS,
        "sample_dt_ms": SAMPLE_DT_MS,
        "selection_count": len(EXPECTED_SELECTED_IDS),
        "stimulus": {
            "target_ids": list(GFC2_IDS),
            "amplitude_nA": PULSE_AMPLITUDE_NA,
            "delay_ms": PULSE_DELAY_MS,
            "duration_ms": PULSE_DURATION_MS,
            "pulse_count": 1,
        },
        "gap_pair_count": 0,
        "recording": {
            "kind": "named_soma_voltage_only",
            "declared_source_ids": list(GFC2_IDS),
            "actual_soma_trace_count": len(EXPECTED_SELECTED_IDS),
            "note": "The staged runner applies named voltage_locations to every selected cell; only the eleven sources are compared.",
        },
        "legacy_cv_bridge": legacy_bridge_metadata,
        "input_validation": input_validation,
        "comparison": comparison,
        "generated_artifacts": generated,
        "summary_json": str((run_root / "source_soma_diagnostic_summary.json").resolve()),
    }


def _validate_generated_run(
    run_dir: Path,
    *,
    run_root: Path,
    filtered_edges: Path,
    legacy_bridge_metadata: Mapping[str, Any],
) -> dict[str, str]:
    _assert_inside(run_dir, run_root, "run directory")
    paths = {
        "records_csv": run_dir / "records.csv",
        "spikes_csv": run_dir / "spikes.csv",
        "config_json": run_dir / "config.json",
        "run_summary_json": run_dir / "run_summary.json",
        "phase_timings_json": run_dir / "_phase_timings.json",
        "cell_biophys_csv": run_dir / "cell_biophys.csv",
    }
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise SourceSomaDiagnosticError(f"Arbor diagnostic omitted required artifacts: {missing}")
    for label, path in paths.items():
        _assert_inside(path, run_root, label)
    summary = _read_json(paths["run_summary_json"])
    try:
        gap_pair_count = int(summary.get("gap_pairs", -1))
    except (TypeError, ValueError):
        gap_pair_count = -1
    if str(summary.get("status")) != "completed" or gap_pair_count != 0:
        raise SourceSomaDiagnosticError("Arbor diagnostic did not complete with exactly zero gap pairs.")
    backend = dict(summary.get("backend_metadata") or {})
    if (
        int(backend.get("threads") or 0) != 1
        or bool(backend.get("mpi"))
        or backend.get("gpu_id") is not None
    ):
        raise SourceSomaDiagnosticError(
            "Saved Arbor runtime evidence does not prove the one-thread CPU quarantine."
        )
    runtime_bridge = dict((summary.get("placement_diagnostics") or {}).get("legacy_neuron_cv_bridge") or {})
    if str(runtime_bridge.get("status")) != "installed" or runtime_bridge.get("fallback_cv_policy") is not None:
        raise SourceSomaDiagnosticError("Saved Arbor placement diagnostics do not prove legacy bridge installation.")
    if bool(runtime_bridge.get("topology_equivalence_claim")):
        raise SourceSomaDiagnosticError("Runtime bridge diagnostics made a topology-equivalence claim.")
    resolved = _read_json(paths["config_json"])
    assert_source_diagnostic_config(
        resolved,
        run_root=run_root,
        filtered_edges=filtered_edges,
        legacy_bridge_metadata=legacy_bridge_metadata,
    )
    try:
        seed_ais_evidence = _validate_arbor_seed_ais_runtime(
            run_summary=summary,
            resolved_config=resolved,
            cell_biophys_csv=paths["cell_biophys_csv"],
        )
    except RuntimeError as exc:
        raise SourceSomaDiagnosticError(
            f"Generated Arbor seed-AIS parameter parity failed: {exc}"
        ) from exc
    seed_ais_path = run_dir / "seed_ais_biophysics_parity.json"
    _write_json(seed_ais_path, seed_ais_evidence)
    _assert_inside(seed_ais_path, run_root, "seed AIS biophysics parity")
    paths["seed_ais_biophysics_json"] = seed_ais_path
    records_size = int(paths["records_csv"].stat().st_size)
    if records_size > MAX_RECORDS_BYTES:
        raise SourceSomaDiagnosticError(
            f"Bounded soma records unexpectedly exceed {MAX_RECORDS_BYTES} bytes: {records_size}"
        )
    with paths["records_csv"].open(newline="", encoding="utf-8") as handle:
        header = next(csv.reader(handle), [])
    expected = ["t_ms", *(f"soma_v__{neuron_id}" for neuron_id in EXPECTED_SELECTED_IDS)]
    if header != expected:
        raise SourceSomaDiagnosticError("Arbor diagnostic emitted non-soma, missing, or reordered voltage traces.")
    return {label: str(path.resolve()) for label, path in paths.items()}


def _assert_exact_base_config(config: Mapping[str, Any], filtered_edges: Path) -> None:
    try:
        _assert_core_biophysics_contract(config)
        _assert_legacy_seed_ais_contract(config)
    except RuntimeError as exc:
        raise SourceSomaDiagnosticError(
            f"Base plan effective biophysics do not match the locked NEURON contract: {exc}"
        ) from exc
    selection = tuple(
        int(value) for value in list((config.get("selection") or {}).get("neuron_ids") or [])
    )
    seeds = tuple(int(value) for value in list(config.get("seeds") or []))
    pulse = dict((config.get("stim") or {}).get("pulse_train") or {})
    amps = {int(key): float(value) for key, value in dict(pulse.get("amps_by_gid") or {}).items()}
    gap = dict(config.get("gap") or {})
    if selection != EXPECTED_SELECTED_IDS or seeds != GFC2_IDS:
        raise SourceSomaDiagnosticError("Base plan is not the exact ordered 49-cell/11-seed circuit.")
    if Path(str(config.get("edges_path") or "")).expanduser().resolve() != filtered_edges.resolve():
        raise SourceSomaDiagnosticError("Base plan is not bound to the filtered app-owned chemical table.")
    if bool(gap.get("enabled")) or list(gap.get("pairs") or []):
        raise SourceSomaDiagnosticError("Base plan is not gap-disabled.")
    if not (
        tuple(amps) == GFC2_IDS
        and all(_same_float(value, PULSE_AMPLITUDE_NA) for value in amps.values())
        and _same_float(pulse.get("delay_ms"), PULSE_DELAY_MS)
        and _same_float(pulse.get("dur_ms"), PULSE_DURATION_MS)
        and _same_float(pulse.get("freq_hz"), PULSE_FREQUENCY_HZ)
    ):
        raise SourceSomaDiagnosticError("Base plan does not carry the exact source stimulus.")
    if not _same_float(config.get("dt_ms"), DT_MS):
        raise SourceSomaDiagnosticError("Base plan integration step changed.")
    if str(config.get("swc_section_mode")) != "branch" or not _same_float(
        config.get("swc_section_nseg_um"), LEGACY_NSEG_UM
    ):
        raise SourceSomaDiagnosticError("Base plan lost the legacy branch-section contract.")
    metadata = dict(config.get("metadata") or {})
    if str(metadata.get("app_recipe")) != APP_RECIPE:
        raise SourceSomaDiagnosticError("Base plan has the wrong exact-recipe identity.")


def _assert_legacy_bridge_metadata(metadata: Mapping[str, Any]) -> None:
    expected = {
        "status": "installed",
        "arbor_version": "0.12.2",
        "runner_module": "digifly.phase2.arbor_build.runner",
        "app_recipe": APP_RECIPE,
        "cv_policy": CV_POLICY_TOKEN,
        "bridge_revision": "balanced_locset_v2",
        "locset_join_strategy": "balanced_binary",
        "fallback_cv_policy": None,
        "topology_equivalence_claim": False,
        "compatibility_class": "legacy_section_boundary_candidate",
        "soma_location": "native_legacy_swc_cell_soma_site",
        "zero_area_fork_cv_caveat": True,
        "staged_root_stub_caveat": True,
    }
    changed = {
        key: {"expected": expected_value, "found": metadata.get(key)}
        for key, expected_value in expected.items()
        if metadata.get(key) != expected_value
    }
    if changed or not _same_float(metadata.get("legacy_section_nseg_um"), LEGACY_NSEG_UM):
        raise SourceSomaDiagnosticError(
            f"Legacy CV bridge is not installed with the bounded compatibility contract: {changed}"
        )


def _read_trace_window(
    path: Path,
    columns: Sequence[str],
) -> tuple[list[float], dict[str, list[float]], int]:
    if not path.is_file():
        raise SourceSomaDiagnosticError(f"Trace table does not exist: {path}")
    values = {str(column): [] for column in columns}
    times: list[float] = []
    consumed = 0
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if not header:
            raise SourceSomaDiagnosticError(f"Trace table has no header: {path}")
        if len(set(header)) != len(header):
            raise SourceSomaDiagnosticError(f"Trace table has duplicate columns: {path}")
        required = ["t_ms", *columns]
        missing = [column for column in required if column not in header]
        if missing:
            raise SourceSomaDiagnosticError(f"Trace table is missing required columns {missing}: {path}")
        time_index = header.index("t_ms")
        indices = {column: header.index(column) for column in columns}
        max_index = max([time_index, *indices.values()])
        for row in reader:
            consumed += 1
            if len(row) <= max_index:
                raise SourceSomaDiagnosticError(f"Short trace row {consumed} in {path}")
            try:
                time_ms = float(row[time_index])
            except (TypeError, ValueError) as exc:
                raise SourceSomaDiagnosticError(f"Invalid time at trace row {consumed} in {path}") from exc
            if not math.isfinite(time_ms):
                raise SourceSomaDiagnosticError(f"Non-finite time at trace row {consumed} in {path}")
            if time_ms > WINDOW_MS + 1e-8:
                break
            if times and time_ms <= times[-1]:
                raise SourceSomaDiagnosticError(f"Trace times are not strictly increasing in {path}")
            times.append(time_ms)
            for column, index in indices.items():
                try:
                    value = float(row[index])
                except (TypeError, ValueError) as exc:
                    raise SourceSomaDiagnosticError(
                        f"Invalid {column} value at trace row {consumed} in {path}"
                    ) from exc
                if not math.isfinite(value):
                    raise SourceSomaDiagnosticError(
                        f"Non-finite {column} value at trace row {consumed} in {path}"
                    )
                values[column].append(value)
    return times, values, consumed


def _assert_exact_grid(times: Sequence[float], path: Path) -> None:
    if len(times) != EXPECTED_SAMPLES:
        raise SourceSomaDiagnosticError(
            f"Trace table must provide exactly {EXPECTED_SAMPLES} samples through 5 ms; "
            f"found {len(times)} in {path}"
        )
    for index, actual in enumerate(times):
        expected = index * SAMPLE_DT_MS
        if not math.isclose(float(actual), expected, rel_tol=0.0, abs_tol=1e-8):
            raise SourceSomaDiagnosticError(
                f"Trace grid mismatch at sample {index}: expected {expected}, found {actual} in {path}"
            )


def _assert_dense_arbor_grid(times: Sequence[float], path: Path) -> dict[str, float | int]:
    """Validate Arbor's dense epoch-relative regular schedule.

    The staged runner advances the simulation in minimum-delay epochs. Arbor's
    ``regular_schedule`` restarts at those epoch boundaries, so the saved times
    are deliberately not required to coincide with NEURON's global 0.01 ms
    lattice.  They must still contain the expected bounded sample count, begin
    at zero, densely cover the full window, and never leave a gap materially
    larger than the requested sampling interval.
    """

    if len(times) != EXPECTED_SAMPLES:
        raise SourceSomaDiagnosticError(
            f"Arbor trace table must provide exactly {EXPECTED_SAMPLES} dense samples; "
            f"found {len(times)} in {path}"
        )
    if not math.isclose(float(times[0]), 0.0, rel_tol=0.0, abs_tol=1e-8):
        raise SourceSomaDiagnosticError(f"Arbor trace grid must begin at 0 ms: {path}")
    steps = [float(right) - float(left) for left, right in zip(times, times[1:])]
    max_step = max(steps)
    min_step = min(steps)
    if min_step <= 0.0:
        raise SourceSomaDiagnosticError(f"Arbor trace grid is not strictly increasing: {path}")
    if max_step > SAMPLE_DT_MS * 1.1 + 1e-8:
        raise SourceSomaDiagnosticError(
            f"Arbor trace grid is not dense enough: maximum step {max_step} ms in {path}"
        )
    last_time = float(times[-1])
    if last_time < WINDOW_MS - SAMPLE_DT_MS - 1e-8 or last_time > WINDOW_MS + 1e-8:
        raise SourceSomaDiagnosticError(
            f"Arbor trace grid does not cover the 0-{WINDOW_MS:g} ms window: "
            f"last sample {last_time} ms in {path}"
        )
    return {
        "sample_count": len(times),
        "first_time_ms": float(times[0]),
        "last_time_ms": last_time,
        "minimum_step_ms": min_step,
        "maximum_step_ms": max_step,
    }


def _interpolate_trace(
    source_times: Sequence[float],
    source_values: Sequence[float],
    target_times: Sequence[float],
) -> list[float]:
    """Linearly evaluate a validated reference trace on the Arbor grid."""

    if len(source_times) != len(source_values) or len(source_times) < 2:
        raise SourceSomaDiagnosticError("Reference interpolation inputs have inconsistent lengths.")
    if not target_times:
        raise SourceSomaDiagnosticError("Reference interpolation target grid is empty.")
    tolerance = 1e-8
    if (
        float(target_times[0]) < float(source_times[0]) - tolerance
        or float(target_times[-1]) > float(source_times[-1]) + tolerance
    ):
        raise SourceSomaDiagnosticError("Arbor comparison grid lies outside the NEURON reference window.")

    result: list[float] = []
    left_index = 0
    for raw_target in target_times:
        target = float(raw_target)
        while (
            left_index + 1 < len(source_times) - 1
            and float(source_times[left_index + 1]) < target - tolerance
        ):
            left_index += 1
        left_time = float(source_times[left_index])
        right_time = float(source_times[left_index + 1])
        left_value = float(source_values[left_index])
        right_value = float(source_values[left_index + 1])
        if math.isclose(target, left_time, rel_tol=0.0, abs_tol=tolerance):
            result.append(left_value)
            continue
        if math.isclose(target, right_time, rel_tol=0.0, abs_tol=tolerance):
            result.append(right_value)
            continue
        if target < left_time or target > right_time:
            raise SourceSomaDiagnosticError("Could not bracket an Arbor sample on the NEURON grid.")
        fraction = (target - left_time) / (right_time - left_time)
        result.append(left_value + fraction * (right_value - left_value))
    return result


def _trace_metric(
    times: Sequence[float],
    baseline: Sequence[float],
    arbor: Sequence[float],
    thresholds: Mapping[str, float],
) -> dict[str, Any]:
    if len(times) != len(baseline) or len(times) != len(arbor) or len(times) < 2:
        raise SourceSomaDiagnosticError("Trace metric inputs have inconsistent lengths.")
    left = [float(value) for value in baseline]
    right = [float(value) for value in arbor]
    diff = [candidate - reference for reference, candidate in zip(left, right)]
    baseline_range = max(left) - min(left)
    arbor_range = max(right) - min(right)
    rmse = math.sqrt(sum(value * value for value in diff) / len(diff))
    mae = sum(abs(value) for value in diff) / len(diff)
    max_abs = max(abs(value) for value in diff)
    nrmse = rmse / max(baseline_range, 1.0)
    pearson = _pearson(left, right)
    pre_stim_indices = [index for index, time_ms in enumerate(times) if float(time_ms) <= PULSE_DELAY_MS + 1e-12]
    recovery_start_ms = PULSE_DELAY_MS + PULSE_DURATION_MS
    recovery_indices = [index for index, time_ms in enumerate(times) if float(time_ms) >= recovery_start_ms - 1e-12]
    if not pre_stim_indices or not recovery_indices:
        raise SourceSomaDiagnosticError("Trace window does not cover pre-stimulus and recovery epochs.")
    pre_stim_diff = [diff[index] for index in pre_stim_indices]
    recovery_diff = [diff[index] for index in recovery_indices]
    pre_stim_rmse = math.sqrt(
        sum(value * value for value in pre_stim_diff) / len(pre_stim_diff)
    )
    pre_stim_mean_offset = sum(pre_stim_diff) / len(pre_stim_diff)
    recovery_rmse = math.sqrt(
        sum(value * value for value in recovery_diff) / len(recovery_diff)
    )
    endpoint_error = diff[-1]
    baseline_crossings = _crossing_count(left)
    arbor_crossings = _crossing_count(right)
    baseline_first_crossing = _first_crossing(times, left)
    arbor_first_crossing = _first_crossing(times, right)
    crossing_latency_error = (
        float(arbor_first_crossing - baseline_first_crossing)
        if baseline_first_crossing is not None and arbor_first_crossing is not None
        else None
    )
    baseline_peak_index = max(range(len(left)), key=left.__getitem__)
    arbor_peak_index = max(range(len(right)), key=right.__getitem__)
    peak_amplitude_error = right[arbor_peak_index] - left[baseline_peak_index]
    peak_time_error = float(times[arbor_peak_index]) - float(times[baseline_peak_index])
    range_ratio = arbor_range / max(baseline_range, 1e-12)
    dynamic = baseline_range >= float(thresholds["low_dynamic_range_mV"])
    if dynamic:
        checks = {
            "pearson": pearson is not None
            and pearson >= float(thresholds["trace_pearson_min"]),
            "rmse": rmse <= float(thresholds["trace_rmse_max_mV"]),
            "mae": mae <= float(thresholds["trace_mae_max_mV"]),
            "nrmse": nrmse <= float(thresholds["trace_nrmse_max"]),
            "max_abs_error": max_abs <= float(thresholds["trace_max_abs_error_mV"]),
            "pre_stim_rmse": pre_stim_rmse
            <= float(thresholds["pre_stim_rmse_max_mV"]),
            "pre_stim_mean_offset": abs(pre_stim_mean_offset)
            <= float(thresholds["pre_stim_mean_offset_max_mV"]),
            "matching_upward_crossings": baseline_crossings >= 1
            and arbor_crossings == baseline_crossings,
            "crossing_latency": crossing_latency_error is not None
            and abs(crossing_latency_error)
            <= float(thresholds["crossing_latency_error_max_ms"]),
            "peak_amplitude": abs(peak_amplitude_error)
            <= float(thresholds["peak_amplitude_error_max_mV"]),
            "peak_time": abs(peak_time_error)
            <= float(thresholds["peak_time_error_max_ms"]),
            "range_ratio": float(thresholds["range_ratio_min"])
            <= range_ratio
            <= float(thresholds["range_ratio_max"]),
            "recovery_rmse": recovery_rmse
            <= float(thresholds["recovery_rmse_max_mV"]),
            "endpoint_error": abs(endpoint_error)
            <= float(thresholds["endpoint_error_max_mV"]),
        }
        trace_pass = all(checks.values())
        policy = "strict_dynamic_trace"
    else:
        checks = {
            "low_dynamic_rmse": rmse
            <= float(thresholds["low_dynamic_rmse_max_mV"]),
            "low_dynamic_max_abs_error": max_abs
            <= float(thresholds["low_dynamic_max_abs_error_mV"]),
        }
        trace_pass = all(checks.values())
        policy = "low_dynamic_trace"
    return {
        "pearson_r": pearson,
        "rmse_mV": rmse,
        "mae_mV": mae,
        "max_abs_error_mV": max_abs,
        "nrmse": nrmse,
        "baseline_range_mV": baseline_range,
        "arbor_range_mV": arbor_range,
        "baseline_min_mV": min(left),
        "baseline_max_mV": max(left),
        "arbor_min_mV": min(right),
        "arbor_max_mV": max(right),
        "pre_stim_rmse_mV": pre_stim_rmse,
        "pre_stim_mean_offset_mV": pre_stim_mean_offset,
        "recovery_rmse_mV": recovery_rmse,
        "endpoint_error_mV": endpoint_error,
        "baseline_first_crossing_ms": baseline_first_crossing,
        "arbor_first_crossing_ms": arbor_first_crossing,
        "crossing_latency_error_ms": crossing_latency_error,
        "baseline_trace_crossings": baseline_crossings,
        "arbor_trace_crossings": arbor_crossings,
        "baseline_peak_mV": left[baseline_peak_index],
        "arbor_peak_mV": right[arbor_peak_index],
        "peak_amplitude_error_mV": peak_amplitude_error,
        "baseline_peak_time_ms": float(times[baseline_peak_index]),
        "arbor_peak_time_ms": float(times[arbor_peak_index]),
        "peak_time_error_ms": peak_time_error,
        "range_ratio": range_ratio,
        "checks": checks,
        "failed_checks": [name for name, passed in checks.items() if not passed],
        "comparison_policy": policy,
        "trace_pass": trace_pass,
    }


def _pearson(left: Sequence[float], right: Sequence[float]) -> float | None:
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    left_centered = [value - left_mean for value in left]
    right_centered = [value - right_mean for value in right]
    left_ss = sum(value * value for value in left_centered)
    right_ss = sum(value * value for value in right_centered)
    if left_ss <= 1e-24 or right_ss <= 1e-24:
        return 1.0 if all(math.isclose(a, b, rel_tol=0.0, abs_tol=1e-9) for a, b in zip(left, right)) else None
    return sum(a * b for a, b in zip(left_centered, right_centered)) / math.sqrt(left_ss * right_ss)


def _first_crossing(times: Sequence[float], values: Sequence[float]) -> float | None:
    for index, (left, right) in enumerate(zip(values[:-1], values[1:])):
        if left < 0.0 <= right:
            delta = right - left
            fraction = 0.0 if abs(delta) < 1e-15 else -left / delta
            return float(times[index] + fraction * (times[index + 1] - times[index]))
    return None


def _crossing_count(values: Sequence[float]) -> int:
    return sum(left < 0.0 <= right for left, right in zip(values[:-1], values[1:]))


def _validate_thresholds(thresholds: Mapping[str, float]) -> None:
    missing = [key for key in TRACE_THRESHOLDS if key not in thresholds]
    invalid = [
        key
        for key in TRACE_THRESHOLDS
        if key in thresholds and (not math.isfinite(float(thresholds[key])) or float(thresholds[key]) < 0.0)
    ]
    invalid_range = (
        not missing
        and float(thresholds["range_ratio_min"]) > float(thresholds["range_ratio_max"])
    )
    if missing or invalid or invalid_range:
        raise SourceSomaDiagnosticError(
            "Invalid source-soma trace thresholds: "
            f"missing={missing}, invalid={invalid}, invalid_range={invalid_range}"
        )


def _same_float(actual: Any, expected: float) -> bool:
    try:
        value = float(actual)
    except (TypeError, ValueError):
        return False
    return math.isfinite(value) and math.isclose(value, expected, rel_tol=0.0, abs_tol=1e-12)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SourceSomaDiagnosticError(f"Required JSON does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SourceSomaDiagnosticError(f"Required JSON is invalid: {path}") from exc
    if not isinstance(payload, dict):
        raise SourceSomaDiagnosticError(f"Required JSON is not an object: {path}")
    return payload


def _write_json(path: Path, payload: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(payload), indent=2, sort_keys=True, default=str, allow_nan=False),
        encoding="utf-8",
    )
    return path.resolve()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _assert_inside(path: str | Path, root: str | Path, label: str) -> None:
    candidate = Path(path).expanduser().resolve()
    boundary = Path(root).expanduser().resolve()
    try:
        candidate.relative_to(boundary)
    except ValueError as exc:
        raise SourceSomaDiagnosticError(
            f"Diagnostic output boundary violation for {label}: {candidate}"
        ) from exc


def _reject_public_output(output: Path, public_root: Path) -> None:
    try:
        output.resolve().relative_to(public_root.resolve())
    except ValueError:
        return
    raise SourceSomaDiagnosticError(
        "Diagnostic output boundary violation: output must not be inside Digifly Public."
    )


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _event(kind: str, message: str, **details: Any) -> None:
    print(json.dumps({"event": kind, "message": message, **details}, sort_keys=True), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
