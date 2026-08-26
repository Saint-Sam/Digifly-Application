from __future__ import annotations

import copy
import csv
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from digifly_app.core.workspace import DigiflyWorkspace
from digifly_app.engines.arbor_escape_siz import (
    ArborAblationComparisonConfig,
    CURRENT_CV_POLICY,
    GFC2_IDS,
    ArborEscapeSizAdapter,
    load_arbor_ablation_result,
)
from digifly_app.workers.arbor_escape_siz_worker import (
    COMPARISON_CLASS,
    COMPARISON_DIR,
    CV_POLICY,
    EXPECTED_BRANCH_HH,
    EXPECTED_LEGACY_SEED_AIS_NODE_IDS,
    EXPECTED_LEGACY_SEED_AIS_SEGMENT_COUNTS,
    EXPECTED_SOMA_HH,
    GAP_MECHANISM,
    LEGACY_SEED_AIS_OVERRIDE_PREFIX,
    LEGACY_SEED_AIS_POLICY,
    RECIPE,
    _assert_legacy_seed_ais_contract,
    _assert_gap_bridge_metadata,
    _assert_requested_contract,
    _assert_plan_contract,
    _condition_config,
    _gap_model_comparison,
    _materialize_filtered_edges,
    _parser,
    _reject_public_output,
    _validate_arbor_seed_ais_runtime,
)


def test_arbor_comparison_preset_stays_in_sync() -> None:
    path = (
        Path(__file__).resolve().parents[1]
        / "presets"
        / "escape_siz"
        / "ablation_notebook_arbor_comparison_v1.json"
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert ArborAblationComparisonConfig.from_dict(payload).to_dict() == ArborAblationComparisonConfig().to_dict()


def test_arbor_plan_is_app_owned_and_carries_exact_comparison_contract(tmp_path: Path) -> None:
    workspace = DigiflyWorkspace(tmp_path / "Digifly Public")
    adapter = ArborEscapeSizAdapter(workspace)
    output = tmp_path / "app-output"
    plan = adapter.plan(ArborAblationComparisonConfig(), output_root=output)
    assert plan.engine == "arbor"
    assert plan.output_behavior == "app_owned"
    assert plan.arguments[0] == "-B"
    assert plan.arguments[1].endswith("arbor_escape_siz_worker.py")
    assert plan.arguments[plan.arguments.index("--stim-amp-nA") + 1] == "0.9"
    assert plan.arguments[plan.arguments.index("--static-reverse-fraction") + 1] == "0.2"
    assert plan.arguments[plan.arguments.index("--threads") + 1] == "4"
    assert plan.arguments[plan.arguments.index("--cv-policy") + 1] == "every_segment"
    assert plan.expected_summary_path == str(
        output.resolve()
        / "escape_siz"
        / "arbor"
        / COMPARISON_DIR
        / "comparison_summary.json"
    )
    python_paths = plan.environment["PYTHONPATH"].split(":")
    assert str(workspace.phase2_arbor) in python_paths
    assert str(workspace.phase2_neuron) not in python_paths


def test_worker_rejects_scientific_parameter_drift() -> None:
    args = _parser().parse_args(
        [
            "--digifly-public-root",
            "/input",
            "--output-root",
            "/output",
            "--gap-catalogue",
            "/gap/digifly_gap-catalogue.so",
            "--dry-run",
        ]
    )
    _assert_requested_contract(args, SimpleNamespace(__version__="0.12.2"))
    args.stim_amp_nA = 0.8
    with pytest.raises(RuntimeError, match="stimulus amplitude"):
        _assert_requested_contract(args, SimpleNamespace(__version__="0.12.2"))

    args.stim_amp_nA = 0.9
    args.cv_policy = "legacy_neuron_section_explicit"
    with pytest.raises(RuntimeError, match="CV policy"):
        _assert_requested_contract(args, SimpleNamespace(__version__="0.12.2"))


def test_worker_requires_compiled_gap_catalogue_argument() -> None:
    with pytest.raises(SystemExit):
        _parser().parse_args(
            ["--digifly-public-root", "/input", "--output-root", "/output", "--dry-run"]
        )


def test_worker_fails_closed_on_gap_bridge_provenance_drift(tmp_path: Path) -> None:
    catalogue = tmp_path / "digifly_gap-catalogue.so"
    metadata = {
        "status": "installed",
        "arbor_version": "0.12.2",
        "catalogue_path": str(catalogue.resolve()),
        "catalogue_sha256": "catalogue-sha256",
        "catalogue_prefix": "digifly_",
        "runner_module": "digifly.phase2.arbor_build.runner",
        "mechanisms": ["gap", "hetero_rect_gap", "rect_gap"],
        "connection_weight": 1.0,
        "fallback_mechanism": None,
    }
    _assert_gap_bridge_metadata(
        metadata,
        gap_catalogue=catalogue,
        gap_catalogue_sha256="catalogue-sha256",
    )
    metadata["fallback_mechanism"] = "gj"
    with pytest.raises(RuntimeError, match="locked contract"):
        _assert_gap_bridge_metadata(
            metadata,
            gap_catalogue=catalogue,
            gap_catalogue_sha256="catalogue-sha256",
        )


def test_worker_configures_app_owned_hetero_rect_gap_equation_port(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def build_config(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {
            "gap": {
                "enabled": bool(kwargs["gap_enabled"]),
                "mechanism": "gj",
                "closed_fraction": kwargs["hetero_g_closed_frac"],
            },
            "stim": {"pulse_train": {}},
            "record": {},
            "metadata": {
                "gj_model_effective": "heterotypic_rectifying_approximation",
                "gj_model_exact": False,
                "hetero_static_reverse_fraction": 0.20,
                "heterotypic_approximation": "legacy built-in gj label",
                "heterotypic_calibration": {"selected_value": 0.20},
            },
        }

    args = _parser().parse_args(
        [
            "--digifly-public-root",
            "/input",
            "--output-root",
            "/output",
            "--gap-catalogue",
            str(tmp_path / "digifly_gap-catalogue.so"),
            "--dry-run",
        ]
    )
    catalogue = tmp_path / "digifly_gap-catalogue.so"
    config = _condition_config(
        SimpleNamespace(build_config=build_config),
        args,
        "gap_enabled",
        tmp_path / "comparison",
        tmp_path / "filtered.csv",
        gap_catalogue=catalogue,
        gap_catalogue_sha256="catalogue-sha256",
        gap_bridge_metadata={"installed": True, "mechanism": GAP_MECHANISM},
        seed_ais_nodes=EXPECTED_LEGACY_SEED_AIS_NODE_IDS,
    )

    assert captured["hetero_g_closed_frac"] == 0.0
    assert args.static_reverse_fraction == 0.20
    assert config["gap"] == {
        "enabled": True,
        "mechanism": "hetero_rect_gap",
        "directionality": "pre_to_post",
        "default_g_uS": 0.001,
        "g_closed_frac": 0.0,
        "empirical_residual_frac": 0.20,
        "vhalf_mV": 0.0,
        "vslope_mV": 5.0,
        "tau_open_ms": 6.0,
        "tau_close_ms": 2.0,
    }
    metadata = config["metadata"]
    assert metadata["app_recipe"] == RECIPE
    assert metadata["comparison_class"] == COMPARISON_CLASS
    assert metadata["gj_model_effective"] == GAP_MECHANISM
    assert metadata["gj_model_equations_preserved"] is True
    assert metadata["finite_step_bitwise_identical"] is False
    assert "gj_model_exact" not in metadata
    assert "hetero_static_reverse_fraction" not in metadata
    assert "heterotypic_approximation" not in metadata
    assert "heterotypic_calibration" not in metadata
    assert metadata["represented_parameters"]
    assert metadata["unrepresented_parameters"] == []
    assert metadata["deprecated_static_reverse_fraction"] == {
        "value": 0.20,
        "used_by_equation_port": False,
    }
    assert metadata["gap_bridge"]["installed"] is True
    assert "legacy_neuron_cv_bridge" not in metadata
    assert "legacy_section_nseg_um" not in config["arbor"]
    assert config["arbor"]["cv_policy"] == CV_POLICY == CURRENT_CV_POLICY == "every_segment"
    assert config["swc_section_nseg_um"] == 40.0
    assert config["pre_soma_hh"] == EXPECTED_SOMA_HH
    assert config["pre_branch_hh"] == EXPECTED_BRANCH_HH
    assert config["legacy_neuron_node_hh_mapping"] is True
    assert metadata["legacy_seed_ais_biophysics"]["policy"] == LEGACY_SEED_AIS_POLICY
    _assert_legacy_seed_ais_contract(config)
    _assert_plan_contract({"gap_enabled": config}, tmp_path / "filtered.csv")

    seed_groups = {
        int(group["ids"][0]): group
        for group in config["cell_biophys_overrides"]
        if str(group.get("name", "")).startswith(LEGACY_SEED_AIS_OVERRIDE_PREFIX)
    }
    assert tuple(seed_groups) == GFC2_IDS
    assert seed_groups[14527]["node_ids"] == [4073]
    assert seed_groups[14527]["node_hh"] == EXPECTED_SOMA_HH
    assert seed_groups[14527]["insert_hh"] is True
    assert "node_hh_mult" not in seed_groups[14527]
    assert metadata["legacy_seed_ais_biophysics"][
        "expected_resolved_arbor_segment_count_by_neuron"
    ]["14527"] == EXPECTED_LEGACY_SEED_AIS_SEGMENT_COUNTS[14527] == 14

    for mutation in ("node", "hh", "missing", "multiplier", "metadata"):
        changed = copy.deepcopy(config)
        groups = changed["cell_biophys_overrides"]
        target = next(
            group
            for group in groups
            if group.get("name") == f"{LEGACY_SEED_AIS_OVERRIDE_PREFIX}_14527"
        )
        if mutation == "node":
            target["node_ids"] = [4074]
        elif mutation == "hh":
            target["node_hh"]["gnabar"] = 0.02
        elif mutation == "missing":
            groups.remove(target)
        elif mutation == "multiplier":
            target.pop("node_hh")
            target["node_hh_mult"] = {"gnabar": 6.0}
        else:
            changed["metadata"]["legacy_seed_ais_biophysics"]["policy"] = "drifted"
        with pytest.raises(RuntimeError):
            _assert_legacy_seed_ais_contract(changed)

    runtime_config = copy.deepcopy(config)
    runtime_config["selection"] = {"mode": "custom", "neuron_ids": list(GFC2_IDS)}
    cell_biophys = tmp_path / "cell_biophys.csv"
    with cell_biophys.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "gid",
                "neuron_id",
                "backend",
                "soma_hh",
                "branch_hh",
                "cell_biophys_override_applied",
                "node_hh_override_count",
            ],
        )
        writer.writeheader()
        for gid, neuron_id in enumerate(GFC2_IDS):
            writer.writerow(
                {
                    "gid": gid,
                    "neuron_id": neuron_id,
                    "backend": "arbor",
                    "soma_hh": json.dumps(EXPECTED_SOMA_HH),
                    "branch_hh": json.dumps(EXPECTED_BRANCH_HH),
                    "cell_biophys_override_applied": True,
                    "node_hh_override_count": 1,
                }
            )
    by_gid = {}
    for gid, neuron_id in enumerate(GFC2_IDS):
        segment_count = EXPECTED_LEGACY_SEED_AIS_SEGMENT_COUNTS[neuron_id]
        segments = list(range(gid * 100, gid * 100 + segment_count))
        by_gid[str(gid)] = {
            "scope": "all",
            "active_region": "(all)",
            "soma_segment_ids": [-gid - 1],
            "node_hh_override_segment_count": segment_count,
            "node_hh_overrides": [
                {
                    "name": f"{LEGACY_SEED_AIS_OVERRIDE_PREFIX}_{neuron_id}",
                    "requested_node_count": 1,
                    "resolved_node_ids": [EXPECTED_LEGACY_SEED_AIS_NODE_IDS[neuron_id]],
                    "resolved_node_count": 1,
                    "resolved_segment_ids": segments,
                    "resolved_arbor_segment_count": segment_count,
                    "mapping_mode": "legacy_neuron_section_cv",
                    "resolved_neuron_cv_target_count": 1,
                    "unique_neuron_cv_target_count": 1,
                    "repeated_neuron_cv_hits": 0,
                    "missing_node_ids": [],
                }
            ],
        }
    run_summary = {"placement_diagnostics": {"biophys_policy_by_gid": by_gid}}
    evidence = _validate_arbor_seed_ais_runtime(
        run_summary=run_summary,
        resolved_config=runtime_config,
        cell_biophys_csv=cell_biophys,
    )
    assert evidence["structural_parameter_parity"] is True
    assert evidence["seeds"]["14527"]["resolved_arbor_segment_count"] == 14

    broken = copy.deepcopy(run_summary)
    broken["placement_diagnostics"]["biophys_policy_by_gid"]["4"][
        "node_hh_overrides"
    ][0]["resolved_segment_ids"] = [4073]
    with pytest.raises(RuntimeError, match="complete, disjoint legacy AIS"):
        _validate_arbor_seed_ais_runtime(
            run_summary=broken,
            resolved_config=runtime_config,
            cell_biophys_csv=cell_biophys,
        )


def test_gap_model_summary_records_equation_port_and_solver_difference(tmp_path: Path) -> None:
    args = _parser().parse_args(
        [
            "--digifly-public-root",
            "/input",
            "--output-root",
            "/output",
            "--gap-catalogue",
            str(tmp_path / "digifly_gap-catalogue.so"),
        ]
    )
    gap = _gap_model_comparison(
        args,
        gap_catalogue=tmp_path / "digifly_gap-catalogue.so",
        gap_catalogue_sha256="catalogue-sha256",
    )
    assert gap["effective_mechanism"] == "digifly_hetero_rect_gap"
    assert gap["catalogue_name"] == "digifly_gap"
    assert gap["catalogue_sha256"] == "catalogue-sha256"
    assert gap["default_g_uS"] == 0.001
    assert gap["g_closed_frac"] == 0.0
    assert gap["empirical_residual_frac"] == 0.20
    assert gap["vhalf_mV"] == 0.0
    assert gap["vslope_mV"] == 5.0
    assert gap["tau_open_ms"] == 6.0
    assert gap["tau_close_ms"] == 2.0
    assert gap["directionality"] == "pre_to_post"
    assert gap["endpoint_orientations"] == {"pre": 1, "post": -1}
    assert gap["represented_parameters"]
    assert gap["unrepresented_parameters"] == []
    assert gap["solver"] == {
        "neuron": "derivimplicit",
        "arbor": "cnexp",
        "equations_preserved": True,
        "finite_step_bitwise_identical": False,
    }


def test_worker_rejects_output_inside_digifly_public(tmp_path: Path) -> None:
    public = tmp_path / "Digifly Public"
    with pytest.raises(RuntimeError, match="must not be inside Digifly Public"):
        _reject_public_output(public / "app-runs" / "comparison", public)
    _reject_public_output(tmp_path / "Digifly App Workspace" / "comparison", public)


def test_filtered_chemical_copy_removes_only_direct_gf_rows(tmp_path: Path) -> None:
    source = tmp_path / "chemical.csv"
    destination = tmp_path / "app" / "filtered.csv"
    with source.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=("pre_id", "post_id", "weight"))
        writer.writeheader()
        for index in range(99):
            writer.writerow(
                {
                    "pre_id": 10000 if index % 2 == 0 else 10002,
                    "post_id": 10002 if index % 2 == 0 else 10000,
                    "weight": index,
                }
            )
        for index in range(2331):
            pair_index = index % 69
            writer.writerow(
                {
                    "pre_id": 20000 + pair_index,
                    "post_id": 30000 + pair_index,
                    "weight": index,
                }
            )
    validation = _materialize_filtered_edges(source, destination)
    assert validation["source_chemical_rows"] == 2430
    assert validation["direct_gf_rows_removed"] == 99
    assert validation["filtered_chemical_rows"] == 2331
    assert validation["filtered_chemical_pairs"] == 69
    with destination.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 2331
    assert not {
        (int(row["pre_id"]), int(row["post_id"]))
        for row in rows
    } & {(10000, 10002), (10002, 10000)}


def test_arbor_recipe_labels_gap_kinetics_as_unrepresented() -> None:
    config = ArborAblationComparisonConfig()
    assert not config.errors()
    assert GFC2_IDS == (
        13127,
        13479,
        13645,
        13846,
        14527,
        14662,
        15292,
        15505,
        15938,
        16764,
        17245,
    )
    config.static_reverse_fraction = 0.8
    assert "static reverse gap fraction" in " ".join(config.errors())


def test_arbor_result_accepts_zero_retained_direct_gf_rows(tmp_path: Path) -> None:
    summary = tmp_path / "comparison_summary.json"
    summary.write_text(
        json.dumps(
            {
                "status": "complete",
                "comparison_class": "capability_limited",
                "equivalence_claim": False,
                "input_validation": {
                    "filtered_chemical_rows": 2331,
                    "direct_gf_rows_retained": 0,
                },
                "stimulus": {"target_ids": list(GFC2_IDS), "amplitude_nA": 0.9},
                "gap_model_comparison": {
                    "effective_mechanism": "gj",
                    "static_reverse_fraction": 0.2,
                },
            }
        ),
        encoding="utf-8",
    )

    result = load_arbor_ablation_result(summary)
    topology = next(check for check in result.checks if check.key == "arbor_result_edges")
    assert topology.state.value == "pass"


def test_quarantined_explicit_legacy_result_and_audit_are_marked_stale(tmp_path: Path) -> None:
    root = tmp_path / "comparison"
    plans = root / "_plans"
    enabled = root / "gap_enabled"
    disabled = root / "gap_disabled"
    audit_dir = root / "equivalence_audit_old_policy"
    for directory in (plans, enabled, disabled, audit_dir):
        directory.mkdir(parents=True, exist_ok=True)
    plan = plans / "gap_enabled_config.json"
    plan.write_text(
        json.dumps({"arbor": {"cv_policy": "legacy_neuron_section_explicit"}}),
        encoding="utf-8",
    )
    for directory in (enabled, disabled):
        (directory / "config.json").write_text(
            json.dumps({"arbor": {"cv_policy": "legacy_neuron_section_explicit"}}),
            encoding="utf-8",
        )
    summary = root / "comparison_summary.json"
    summary.write_text(
        json.dumps(
            {
                "status": "complete",
                "comparison_class": "app_owned_equation_port",
                "equivalence_claim": False,
                "planned_configs": {"gap_enabled": str(plan)},
                "input_validation": {"filtered_chemical_rows": 2331, "direct_gf_rows_retained": 0},
                "stimulus": {"target_ids": list(GFC2_IDS), "amplitude_nA": 0.9},
                "gap_model_comparison": {},
            }
        ),
        encoding="utf-8",
    )
    (audit_dir / "equivalence_report.json").write_text(
        json.dumps(
            {
                "verdict": "NOT_YET_EQUIVALENT",
                "generated_at_utc": "2026-08-03T03:57:00+00:00",
                "gates": {"implementation": True},
                "arbor_runs": {"gap_enabled": str(enabled), "gap_disabled": str(disabled)},
                "artifacts": {},
            }
        ),
        encoding="utf-8",
    )

    result = load_arbor_ablation_result(summary)
    assert result.metadata["artifact CV policy"] == "legacy_neuron_section_explicit"
    assert result.metadata["current CV policy"] == "every_segment"
    assert "prior legacy_neuron_section_explicit policy" in str(
        result.metadata["equivalence audit"]
    )
    policy = next(check for check in result.checks if check.key == "arbor_result_cv_policy")
    stale = next(check for check in result.checks if check.key == "arbor_equivalence_policy_stale")
    assert policy.state.value == "warning"
    assert stale.state.value == "warning"
    assert not any(check.key == "arbor_equivalence_verdict" for check in result.checks)


def test_arbor_result_discovers_latest_equivalence_evidence_read_only(tmp_path: Path) -> None:
    root = tmp_path / "escape_siz" / "arbor" / "ablation_notebook_comparison"
    root.mkdir(parents=True)
    summary = root / "comparison_summary.json"
    summary.write_text(
        json.dumps(
            {
                "backend": "arbor",
                "status": "complete",
                "completed_at": "2026-08-02T22:25:00+00:00",
                "comparison_class": "capability_limited",
                "equivalence_claim": False,
                "equivalence_audit_status": "pending_neuron_reference",
                "contact_site_na_multiplier": 2.5,
                "input_validation": {
                    "filtered_chemical_rows": 2331,
                    "direct_gf_rows_retained": 0,
                },
                "stimulus": {"target_ids": list(GFC2_IDS), "amplitude_nA": 0.9},
                "gap_model_comparison": {
                    "effective_mechanism": "gj",
                    "static_reverse_fraction": 0.2,
                    "unrepresented_parameters": ["vhalf_mV", "vslope_mV"],
                },
            }
        ),
        encoding="utf-8",
    )
    stale_dir = root / "equivalence_audit_stale"
    latest_dir = root / "equivalence_audit_latest"
    stale_dir.mkdir()
    latest_dir.mkdir()
    (stale_dir / "equivalence_report.json").write_text(
        json.dumps(
            {
                "verdict": "EQUIVALENT",
                "result_passed": True,
                "generated_at_utc": "2026-08-02T20:00:00+00:00",
                "gates": {"implementation": True},
                "artifacts": {},
            }
        ),
        encoding="utf-8",
    )
    overlay = latest_dir / "key_trace_overlays.png"
    overlay.write_bytes(b"png")
    report_markdown = latest_dir / "equivalence_report.md"
    report_markdown.write_text("NOT_YET_EQUIVALENT", encoding="utf-8")
    outside = tmp_path / "outside.csv"
    outside.write_text("must not be exposed", encoding="utf-8")
    latest_report = latest_dir / "equivalence_report.json"
    latest_report.write_text(
        json.dumps(
            {
                "verdict": "NOT_YET_EQUIVALENT",
                "passed": False,
                "result_passed": False,
                "generated_at_utc": "2026-08-03T02:32:34+00:00",
                "equation_level_gap_mechanism_exact": False,
                "acceptance": {
                    "postsynaptic_soma_pass_fraction_min": 0.9,
                    "stimulus_source_soma_pass_fraction_min": 1.0,
                    "spike_count_abs_tolerance": 1.0,
                },
                "coverage": {"comparable_soma_count": 40, "key_soma_complete": True},
                "implementation_summary": {
                    "required_failure_count": 0,
                    "required_check_count": 67,
                },
                "trace_summary": {"postsynaptic_soma_pass_fraction": 0.367647},
                "baseline_readiness": {
                    "pass_fraction": 0.0,
                    "required_pass_fraction": 1.0,
                    "comparable_seed_soma_count": 11,
                    "required_seed_count": 11,
                    "worst_rmse_evidence": {"neuron_id": 14527, "rmse_mV": 51.1449},
                },
                "spike_pass_fraction": 0.833333,
                "gates": {
                    "all_key_soma_covered": True,
                    "implementation": True,
                    "stimulus_source_soma_fraction": False,
                    "postsynaptic_soma_fraction": False,
                    "key_spikes": False,
                },
                "artifacts": {
                    "report_json": str(latest_report),
                    "report_markdown": str(report_markdown),
                    "key_trace_overlays_png": str(overlay),
                    "outside_csv": str(outside),
                },
            }
        ),
        encoding="utf-8",
    )
    summary_before = summary.read_bytes()
    report_before = latest_report.read_bytes()

    result = load_arbor_ablation_result(summary)

    assert result.metadata["equivalence audit"] == "NOT_YET_EQUIVALENT"
    assert result.metadata["summary audit marker"] == "pending_neuron_reference"
    verdict = next(check for check in result.checks if check.key == "arbor_equivalence_verdict")
    assert verdict.state.value == "fail"
    assert "NOT_YET_EQUIVALENT" in verdict.detail
    implementation = next(
        check for check in result.checks if check.key == "arbor_equivalence_gate_implementation"
    )
    soma = next(
        check
        for check in result.checks
        if check.key == "arbor_equivalence_gate_postsynaptic_soma_fraction"
    )
    source_readiness = next(
        check
        for check in result.checks
        if check.key == "arbor_equivalence_gate_stimulus_source_soma_fraction"
    )
    assert implementation.state.value == "pass"
    assert soma.state.value == "fail"
    assert source_readiness.title == "Stimulus-source baseline readiness"
    assert source_readiness.state.value == "fail"
    assert "Comparable 11/11" in source_readiness.detail
    assert "51.14 mV at neuron 14527" in source_readiness.detail
    artifact_paths = {artifact.path for artifact in result.artifacts}
    assert str(latest_report.resolve()) in artifact_paths
    assert str(report_markdown.resolve()) in artifact_paths
    assert str(overlay.resolve()) in artifact_paths
    assert str(outside.resolve()) not in artifact_paths
    assert summary.read_bytes() == summary_before
    assert latest_report.read_bytes() == report_before
