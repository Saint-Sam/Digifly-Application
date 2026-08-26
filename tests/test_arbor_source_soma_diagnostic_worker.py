from __future__ import annotations

import copy
import csv
import json
import math
from pathlib import Path

import pytest

from digifly_app.workers.arbor_escape_siz_worker import (
    EXPECTED_BRANCH_HH,
    EXPECTED_LEGACY_SEED_AIS_NODE_IDS,
    EXPECTED_SOMA_HH,
    EXPECTED_TEMPERATURE_K,
    _install_legacy_seed_ais_soma_hh,
    _seed_ais_metadata,
)
from digifly_app.workers.arbor_source_soma_diagnostic_worker import (
    APP_RECIPE,
    CV_POLICY_TOKEN,
    DT_MS,
    EXPECTED_SAMPLES,
    EXPECTED_SELECTED_IDS,
    GFC2_IDS,
    LEGACY_NSEG_UM,
    PULSE_AMPLITUDE_NA,
    SAMPLE_DT_MS,
    SourceSomaDiagnosticError,
    WINDOW_MS,
    _parser,
    _reject_public_output,
    assert_source_diagnostic_config,
    build_source_diagnostic_config,
    compare_source_soma_records,
    diagnostic_run_root,
    validate_neuron_gap_disabled_reference,
)


def test_cli_quarantines_legacy_cv_diagnostic_to_one_thread() -> None:
    required = [
        "--digifly-public-root",
        "/input",
        "--output-root",
        "/output",
        "--gap-catalogue",
        "/catalogue.so",
        "--neuron-gap-disabled-run",
        "/reference",
    ]
    assert _parser().parse_args(required).threads == 1
    with pytest.raises(SystemExit):
        _parser().parse_args([*required, "--threads", "4"])


def _bridge_metadata() -> dict[str, object]:
    return {
        "status": "installed",
        "arbor_version": "0.12.2",
        "runner_module": "digifly.phase2.arbor_build.runner",
        "app_recipe": APP_RECIPE,
        "cv_policy": CV_POLICY_TOKEN,
        "bridge_revision": "balanced_locset_v2",
        "locset_join_strategy": "balanced_binary",
        "legacy_section_nseg_um": LEGACY_NSEG_UM,
        "fallback_cv_policy": None,
        "topology_equivalence_claim": False,
        "compatibility_class": "legacy_section_boundary_candidate",
        "soma_location": "native_legacy_swc_cell_soma_site",
        "zero_area_fork_cv_caveat": True,
        "staged_root_stub_caveat": True,
    }


def _base_config(edges: Path) -> dict[str, object]:
    config: dict[str, object] = {
        "selection": {"mode": "custom", "neuron_ids": list(EXPECTED_SELECTED_IDS)},
        "seeds": list(GFC2_IDS),
        "edges_path": str(edges.resolve()),
        "runs_root": str(edges.parent / "old-runs"),
        "run_id": "gap_disabled",
        "tstop_ms": 99.0,
        "dt_ms": DT_MS,
        "swc_section_mode": "branch",
        "swc_section_nseg_um": LEGACY_NSEG_UM,
        "legacy_neuron_node_hh_mapping": True,
        "active_compartment_scope": "all",
        "active_posts_mode": "all_selected",
        "post_active": True,
        "pre_soma_hh": dict(EXPECTED_SOMA_HH),
        "post_soma_hh": dict(EXPECTED_SOMA_HH),
        "pre_branch_hh": dict(EXPECTED_BRANCH_HH),
        "post_branch_hh": dict(EXPECTED_BRANCH_HH),
        "passive_e": -65.0,
        "passive_g": 0.0001,
        "Ra": 100.0,
        "cm": 1.0,
        "v_init_mV": -65.0,
        "tempK": EXPECTED_TEMPERATURE_K,
        "default_weight_uS": 0.000003,
        "default_delay_ms": 1.0,
        "use_geom_delay": True,
        "syn_tau1_ms": 0.5,
        "syn_tau2_ms": 3.0,
        "syn_e_rev_mV": 0.0,
        "chemical_synapse": {
            "mechanism": "exp2syn",
            "default_site": "soma",
            "allow_soma_fallback": False,
            "max_sites_per_pair": None,
            "aggregate_conductance": False,
        },
        "gap": {"enabled": False, "edges_path": None, "pairs": []},
        "stim": {
            "pulse_train": {
                "enabled": True,
                "freq_hz": 100.0,
                "amp_nA": 0.0,
                "amps_by_gid": {str(value): PULSE_AMPLITUDE_NA for value in GFC2_IDS},
                "delay_ms": 1.0,
                "dur_ms": 0.4,
                "location": "soma",
                "stop_ms": 99.0,
                "max_pulses": 10,
                "include_base_iclamp": False,
            }
        },
        "record": {
            "voltage_locations": ["soma"],
            "all_compartment_voltage": {"enabled": True, "neuron_ids": [10000]},
            "node_voltage_probes": [{"neuron_id": 10068, "node_id": 1}],
        },
        "arbor": {"cv_policy": CV_POLICY_TOKEN, "strict_connections": True},
        "metadata": {
            "app_recipe": APP_RECIPE,
            "legacy_seed_ais_biophysics": _seed_ais_metadata(
                EXPECTED_LEGACY_SEED_AIS_NODE_IDS
            ),
        },
    }
    _install_legacy_seed_ais_soma_hh(
        config,
        seed_ais_nodes=EXPECTED_LEGACY_SEED_AIS_NODE_IDS,
    )
    return config


def test_builds_exact_bounded_config_without_weakening_full_plan(tmp_path: Path) -> None:
    edges = tmp_path / "filtered.csv"
    edges.write_text("pre_id,post_id\n", encoding="utf-8")
    original = _base_config(edges)
    untouched = copy.deepcopy(original)
    root = tmp_path / "diagnostic"
    bridge = _bridge_metadata()

    config = build_source_diagnostic_config(
        original,
        run_root=root,
        filtered_edges=edges,
        legacy_bridge_metadata=bridge,
    )

    assert original == untouched
    assert config["tstop_ms"] == WINDOW_MS
    assert config["run_id"] == "arbor_gap_disabled"
    assert config["gap"]["enabled"] is False
    assert config["gap"]["pairs"] == []
    assert config["stim"]["pulse_train"]["max_pulses"] == 1
    assert config["stim"]["pulse_train"]["stop_ms"] == WINDOW_MS
    assert config["record"]["voltage_locations"] == ["soma"]
    assert "all_compartment_voltage" not in config["record"]
    assert "node_voltage_probes" not in config["record"]
    assert config["record"]["sample_dt_ms"] == SAMPLE_DT_MS
    assert config["arbor"]["cv_policy"] == CV_POLICY_TOKEN
    assert config["arbor"]["threads"] == 1
    assert config["arbor"]["mpi"] is False
    assert config["arbor"]["gpu_id"] is None
    assert config["parallel"]["threads"] == 1
    assert config["metadata"]["topology_equivalence_claim"] is False
    assert_source_diagnostic_config(
        config,
        run_root=root,
        filtered_edges=edges,
        legacy_bridge_metadata=bridge,
    )


@pytest.mark.parametrize(
    "mutation",
    ["selection", "seeds", "gaps", "recording", "threads", "bridge_claim"],
)
def test_bounded_config_fails_closed_on_contract_drift(tmp_path: Path, mutation: str) -> None:
    edges = tmp_path / "filtered.csv"
    edges.write_text("pre_id,post_id\n", encoding="utf-8")
    root = tmp_path / "diagnostic"
    bridge = _bridge_metadata()
    config = build_source_diagnostic_config(
        _base_config(edges),
        run_root=root,
        filtered_edges=edges,
        legacy_bridge_metadata=bridge,
    )
    if mutation == "selection":
        config["selection"]["neuron_ids"] = list(EXPECTED_SELECTED_IDS[:-1])
    elif mutation == "seeds":
        config["seeds"] = list(GFC2_IDS[:-1])
    elif mutation == "gaps":
        config["gap"]["pairs"] = [{"a_id": 10000, "b_id": 10002}]
    elif mutation == "recording":
        config["record"]["voltage_locations"] = ["soma", "distal"]
    elif mutation == "threads":
        config["arbor"]["threads"] = 4
    else:
        changed = dict(bridge)
        changed["topology_equivalence_claim"] = True
        config["metadata"]["legacy_cv_bridge"] = changed
        bridge = changed

    with pytest.raises(SourceSomaDiagnosticError):
        assert_source_diagnostic_config(
            config,
            run_root=root,
            filtered_edges=edges,
            legacy_bridge_metadata=bridge,
        )


def _reference_config(edges: Path) -> dict[str, object]:
    return {
        "selection": {"mode": "custom", "neuron_ids": list(EXPECTED_SELECTED_IDS)},
        "seeds": list(GFC2_IDS),
        "edges_path": str(edges.resolve()),
        "edges_csv": str(edges.resolve()),
        "dt_ms": DT_MS,
        "tstop_ms": 99.0,
        "swc_section_mode": "branch",
        "swc_section_nseg_um": LEGACY_NSEG_UM,
        "active_compartment_scope": "all",
        "active_posts_mode": "all_selected",
        "post_active": True,
        "pre_branch_hh": {"el": -65.0, "gkbar": 0.01, "gl": 0.0001, "gnabar": 0.02},
        "post_branch_hh": {"el": -65.0, "gkbar": 0.01, "gl": 0.0001, "gnabar": 0.02},
        "pre_soma_hh": {"el": -65.0, "gkbar": 0.036, "gl": 0.0003, "gnabar": 0.12},
        "post_soma_hh": {"el": -65.0, "gkbar": 0.036, "gl": 0.0003, "gnabar": 0.12},
        "passive_e": -65.0,
        "passive_g": 0.0001,
        "Ra": 100.0,
        "cm": 1.0,
        "v_init_mV": -65.0,
        "default_weight_uS": 0.000003,
        "default_delay_ms": 1.0,
        "use_geom_delay": True,
        "syn_tau1_ms": 0.5,
        "syn_tau2_ms": 3.0,
        "syn_e_rev_mV": 0.0,
        "chemical_synapse": {
            "aggregate_conductance": False,
            "max_sites_per_pair": None,
        },
        "gap": {"enabled": True},
        "record": {"soma_v": list(GFC2_IDS)},
        "pulse_train": {
            "enabled": True,
            "freq_hz": 100.0,
            "amps_by_gid": {str(value): PULSE_AMPLITUDE_NA for value in GFC2_IDS},
            "delay_ms": 1.0,
            "dur_ms": 0.4,
            "location": "soma",
            "include_base_iclamp": False,
            "stop_ms": 99.0,
            "max_pulses": 10,
        },
    }


def _runtime_summary(root: Path, edges: Path) -> dict[str, object]:
    return {
        "run_dir": str(root.resolve()),
        "gap_label": "gap_disabled",
        "gap_profile_mult": 0.0,
        "contact_site_sodium_multiplier": 2.5,
        "circuit_label": "baseline_with_10002_GFCs",
        "separate_gfs": True,
        "stim_target_ids": list(GFC2_IDS),
        "stim_amp_nA_by_gid": {str(value): PULSE_AMPLITUDE_NA for value in GFC2_IDS},
        "stim_delay_ms": 1.0,
        "stim_dur_ms": 0.4,
        "stim_freq_hz": 100.0,
        "tstop_ms": 99.0,
        "cache_response": {
            "status": "ok",
            "returncode": 0,
            "baseline_out_dir": str(root.resolve()),
            "out_dir": str(root.resolve()),
            "records_csv": str((root / "records.csv").resolve()),
            "edges_path": str(edges.resolve()),
            "final_network_ids": sorted(EXPECTED_SELECTED_IDS),
            "seed_ids": list(GFC2_IDS),
            "gap_group_summary": [
                {
                    "name": "runtime_gap_profile_gap_disabled",
                    "matched_gaps": 959,
                    "updated_gap_handles": 1918,
                    "g_mult": 0.0,
                    "g_uS": None,
                    "global_aggregated": True,
                }
            ],
        },
    }


def _trace_value(neuron_id: int, time_ms: float) -> float:
    # A smooth, dynamic single response with a threshold crossing.
    return -65.0 + (90.0 + (neuron_id % 7)) * math.exp(-((time_ms - 1.2) / 0.14) ** 2)


def _write_trace_csv(
    path: Path,
    *,
    backend: str,
    shifted_neuron: int | None = None,
    malformed_after_window: bool = False,
    grid_error_index: int | None = None,
    grid_error_ms: float = 0.02,
    epoch_shifted: bool = False,
) -> None:
    if backend == "neuron":
        columns = [f"{value}_soma_v" for value in GFC2_IDS]
    else:
        columns = [f"soma_v__{value}" for value in GFC2_IDS]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["t_ms", *columns])
        for index in range(EXPECTED_SAMPLES):
            time_ms = index * SAMPLE_DT_MS
            if epoch_shifted:
                # Arbor's staged runner restarts regular schedules at internal
                # epochs. A slightly compressed dense grid models the resulting
                # valid non-coincident sample times without coupling the unit
                # test to one particular minimum chemical delay.
                time_ms *= 4.994 / WINDOW_MS
            if grid_error_index == index:
                time_ms += grid_error_ms
            values = []
            for neuron_id in GFC2_IDS:
                value = _trace_value(neuron_id, time_ms)
                if backend == "arbor" and shifted_neuron == neuron_id:
                    value += 20.0
                values.append(value)
            writer.writerow([time_ms, *values])
        if malformed_after_window:
            writer.writerow([WINDOW_MS + SAMPLE_DT_MS, *(["not-read"] * len(columns))])


def _write_neuron_cell_biophys(path: Path) -> None:
    fieldnames = [
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
        "soma_has_na16a",
        "soma_gbar_na16a",
        "ais_has_na16a",
        "ais_gbar_na16a",
        "soma_custom_channel_suffixes",
        "ais_custom_channel_suffixes",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for neuron_id in EXPECTED_SELECTED_IDS:
            ais_hh = EXPECTED_SOMA_HH if neuron_id in GFC2_IDS else EXPECTED_BRANCH_HH
            writer.writerow(
                {
                    "neuron_id": neuron_id,
                    "soma_sec": f"cell{neuron_id}_soma",
                    "soma_has_hh": True,
                    "soma_gnabar_hh": EXPECTED_SOMA_HH["gnabar"],
                    "soma_gkbar_hh": EXPECTED_SOMA_HH["gkbar"],
                    "soma_gl_hh": EXPECTED_SOMA_HH["gl"],
                    "ais_sec": f"cell{neuron_id}_ais",
                    "ais_has_hh": True,
                    "ais_gnabar_hh": ais_hh["gnabar"],
                    "ais_gkbar_hh": ais_hh["gkbar"],
                    "ais_gl_hh": ais_hh["gl"],
                    "soma_has_na16a": False,
                    "soma_gbar_na16a": "",
                    "ais_has_na16a": False,
                    "ais_gbar_na16a": "",
                    "soma_custom_channel_suffixes": "",
                    "ais_custom_channel_suffixes": "",
                }
            )


def _write_reference(
    root: Path,
    *,
    malformed_after_window: bool = False,
    grid_error_index: int | None = None,
    grid_error_ms: float = 0.02,
) -> None:
    root.mkdir(parents=True)
    edges = root / "filtered_chemical_edges.csv"
    with edges.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["pre_id", "post_id", "weight_uS"])
        for index in range(2331):
            writer.writerow(
                [
                    GFC2_IDS[index % len(GFC2_IDS)],
                    EXPECTED_SELECTED_IDS[index % len(EXPECTED_SELECTED_IDS)],
                    0.000003,
                ]
            )
    (root / "config.json").write_text(
        json.dumps(_reference_config(edges)), encoding="utf-8"
    )
    (root / "contact_site_na_gfc_heatmap_summary.json").write_text(
        json.dumps(_runtime_summary(root, edges)), encoding="utf-8"
    )
    _write_trace_csv(
        root / "records.csv",
        backend="neuron",
        malformed_after_window=malformed_after_window,
        grid_error_index=grid_error_index,
        grid_error_ms=grid_error_ms,
    )
    _write_neuron_cell_biophys(root / "cell_biophys.csv")


def test_neuron_reference_validation_records_exact_runtime_and_edge_evidence(
    tmp_path: Path,
) -> None:
    reference = tmp_path / "neuron-gap-disabled"
    _write_reference(reference)

    evidence = validate_neuron_gap_disabled_reference(reference)

    assert evidence["runtime_paths_self_consistent"] is True
    assert evidence["contact_site_sodium_multiplier"] == 2.5
    assert evidence["gap_contacts_zeroed"] == 959
    assert evidence["gap_handles_zeroed"] == 1918
    assert evidence["filtered_chemical_edges"]["row_count"] == 2331
    assert evidence["filtered_chemical_edges"]["direct_gf_rows"] == 0
    assert len(evidence["filtered_chemical_edges"]["sha256"]) == 64
    assert evidence["effective_biophysics"]["status"] == "passed"
    assert evidence["effective_biophysics"]["seed_count"] == len(GFC2_IDS)
    assert evidence["effective_biophysics"]["nonseed_count"] == 38
    assert evidence["effective_biophysics"]["custom_channels_active"] is False


@pytest.mark.parametrize(
    "mutation",
    [
        "runtime_path",
        "contact_na",
        "cache_status",
        "final_ids",
        "gap_application",
        "biophysics",
        "soma_recording",
        "edge_link",
        "direct_gf_edge",
        "edge_row_count",
        "seed_ais_reverted",
        "nonseed_ais_promoted",
        "duplicate_biophys_row",
    ],
)
def test_neuron_reference_validation_fails_closed_on_drift(
    tmp_path: Path,
    mutation: str,
) -> None:
    reference = tmp_path / "neuron-gap-disabled"
    _write_reference(reference)
    config_path = reference / "config.json"
    runtime_path = reference / "contact_site_na_gfc_heatmap_summary.json"
    edges_path = reference / "filtered_chemical_edges.csv"
    cell_biophys_path = reference / "cell_biophys.csv"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))

    if mutation == "runtime_path":
        runtime["run_dir"] = str(tmp_path / "other-run")
    elif mutation == "contact_na":
        runtime["contact_site_sodium_multiplier"] = 2.0
    elif mutation == "cache_status":
        runtime["cache_response"]["returncode"] = 1
    elif mutation == "final_ids":
        runtime["cache_response"]["final_network_ids"] = sorted(EXPECTED_SELECTED_IDS[:-1])
    elif mutation == "gap_application":
        runtime["cache_response"]["gap_group_summary"][0]["updated_gap_handles"] = 1917
    elif mutation == "biophysics":
        config["post_soma_hh"]["gnabar"] = 0.11
    elif mutation == "soma_recording":
        config["record"]["soma_v"] = list(GFC2_IDS[:-1])
    elif mutation == "edge_link":
        runtime["cache_response"]["edges_path"] = str(tmp_path / "other-edges.csv")
    elif mutation == "direct_gf_edge":
        rows = list(csv.reader(edges_path.open(newline="", encoding="utf-8")))
        rows[1][0:2] = ["10000", "10002"]
        with edges_path.open("w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerows(rows)
    elif mutation == "edge_row_count":
        rows = list(csv.reader(edges_path.open(newline="", encoding="utf-8")))
        with edges_path.open("w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerows(rows[:-1])
    else:
        with cell_biophys_path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            fieldnames = list(reader.fieldnames or [])
            rows = [dict(row) for row in reader]
        if mutation == "seed_ais_reverted":
            row = next(item for item in rows if int(item["neuron_id"]) == GFC2_IDS[0])
            row["ais_gnabar_hh"] = str(EXPECTED_BRANCH_HH["gnabar"])
            row["ais_gkbar_hh"] = str(EXPECTED_BRANCH_HH["gkbar"])
            row["ais_gl_hh"] = str(EXPECTED_BRANCH_HH["gl"])
        elif mutation == "nonseed_ais_promoted":
            row = next(item for item in rows if int(item["neuron_id"]) == 10000)
            row["ais_gnabar_hh"] = str(EXPECTED_SOMA_HH["gnabar"])
            row["ais_gkbar_hh"] = str(EXPECTED_SOMA_HH["gkbar"])
            row["ais_gl_hh"] = str(EXPECTED_SOMA_HH["gl"])
        else:
            rows.append(dict(rows[0]))
        with cell_biophys_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    config_path.write_text(json.dumps(config), encoding="utf-8")
    runtime_path.write_text(json.dumps(runtime), encoding="utf-8")
    with pytest.raises(SourceSomaDiagnosticError):
        validate_neuron_gap_disabled_reference(reference)


def test_streaming_comparator_requires_all_eleven_and_stops_after_window(tmp_path: Path) -> None:
    reference = tmp_path / "neuron-gap-disabled"
    _write_reference(reference, malformed_after_window=True)
    arbor = tmp_path / "arbor-records.csv"
    _write_trace_csv(arbor, backend="arbor")

    result = compare_source_soma_records(neuron_run=reference, arbor_records=arbor)

    assert result["passed"] is True
    assert result["passing_source_count"] == 11
    assert result["pass_fraction"] == 1.0
    assert result["failed_source_ids"] == []
    assert result["sample_count"] == EXPECTED_SAMPLES
    assert len(result["metrics"]) == 11
    # One additional row is consumed only far enough to see t > 5 ms; its
    # deliberately invalid voltage values are never parsed.
    assert result["streaming"]["neuron_rows_consumed"] == EXPECTED_SAMPLES + 1
    assert result["streaming"]["full_neuron_records_hash_computed"] is False


def test_streaming_comparator_reports_one_source_failure(tmp_path: Path) -> None:
    reference = tmp_path / "neuron-gap-disabled"
    _write_reference(reference)
    arbor = tmp_path / "arbor-records.csv"
    failed_id = GFC2_IDS[4]
    _write_trace_csv(arbor, backend="arbor", shifted_neuron=failed_id)

    result = compare_source_soma_records(neuron_run=reference, arbor_records=arbor)

    assert result["passed"] is False
    assert result["passing_source_count"] == 10
    assert result["failed_source_ids"] == [failed_id]
    row = next(item for item in result["metrics"] if item["neuron_id"] == failed_id)
    assert row["rmse_mV"] == pytest.approx(20.0)
    assert row["scientific_pass"] is False


def test_streaming_comparator_interpolates_neuron_onto_dense_arbor_epoch_grid(
    tmp_path: Path,
) -> None:
    reference = tmp_path / "neuron-gap-disabled"
    _write_reference(reference)
    arbor = tmp_path / "arbor-epoch-records.csv"
    _write_trace_csv(arbor, backend="arbor", epoch_shifted=True)

    result = compare_source_soma_records(neuron_run=reference, arbor_records=arbor)

    assert result["passed"] is True
    assert result["comparison_grid"] == "arbor_epoch_schedule"
    assert result["interpolation"] == "linear_neuron_to_arbor"
    assert result["reference_sample_count"] == EXPECTED_SAMPLES
    assert result["arbor_sample_count"] == EXPECTED_SAMPLES
    assert result["arbor_grid"]["last_time_ms"] == pytest.approx(4.994)
    assert result["arbor_grid"]["maximum_step_ms"] <= SAMPLE_DT_MS * 1.1


def test_streaming_comparator_fails_closed_on_missing_column_or_invalid_grid(tmp_path: Path) -> None:
    reference = tmp_path / "neuron-gap-disabled"
    _write_reference(reference)
    missing = tmp_path / "missing.csv"
    with missing.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["t_ms", *(f"soma_v__{value}" for value in GFC2_IDS[:-1])])
        writer.writerow([0.0, *([-65.0] * (len(GFC2_IDS) - 1))])
    with pytest.raises(SourceSomaDiagnosticError, match="missing required columns"):
        compare_source_soma_records(neuron_run=reference, arbor_records=missing)

    drift = tmp_path / "drift.csv"
    _write_trace_csv(drift, backend="arbor", grid_error_index=250)
    with pytest.raises(SourceSomaDiagnosticError, match="strictly increasing"):
        compare_source_soma_records(neuron_run=reference, arbor_records=drift)

    reference_drift = tmp_path / "neuron-grid-drift"
    _write_reference(reference_drift, grid_error_index=250, grid_error_ms=0.0005)
    valid_arbor = tmp_path / "valid-arbor.csv"
    _write_trace_csv(valid_arbor, backend="arbor")
    with pytest.raises(SourceSomaDiagnosticError, match="grid mismatch"):
        compare_source_soma_records(neuron_run=reference_drift, arbor_records=valid_arbor)


def test_output_root_is_timestamped_and_cannot_be_inside_public(tmp_path: Path) -> None:
    output = tmp_path / "workspace"
    root = diagnostic_run_root(output, stamp="20260803T010203000004Z")
    root.relative_to(output.resolve())
    public = tmp_path / "Digifly Public"
    nested = diagnostic_run_root(public, stamp="20260803T010203000004Z")
    with pytest.raises(SourceSomaDiagnosticError, match="must not be inside Digifly Public"):
        _reject_public_output(nested, public)
