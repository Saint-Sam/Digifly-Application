from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from digifly_app.workers.exact_gap_equivalence_auditor import (
    ExactGapAuditError,
    _apply_required_result_gate,
    _artifact_paths,
    _assert_disjoint_output,
    _write_exact_markdown,
    audit_exact_gap_equivalence,
    exact_gap_contract_checks,
    stimulus_source_soma_readiness,
)


REPRESENTED = [
    "gmax_open_uS",
    "g_closed_frac",
    "empirical_residual_frac",
    "orientation",
    "vhalf_mV",
    "vslope_mV",
    "tau_open_ms",
    "tau_close_ms",
]


def _evidence(tmp_path: Path) -> tuple[dict, dict, dict, Path]:
    catalogue = tmp_path / "digifly-gap-catalogue.dylib"
    catalogue.write_bytes(b"loadable exact gap catalogue fixture")
    digest = hashlib.sha256(catalogue.read_bytes()).hexdigest()
    model = {
        "requested_model": "heterotypic_rectifying",
        "effective_mechanism": "digifly_hetero_rect_gap",
        "catalogue_name": "digifly_gap",
        "catalogue_path": str(catalogue),
        "catalogue_sha256": digest,
        "default_g_uS": 0.001,
        "g_closed_frac": 0.0,
        "empirical_residual_frac": 0.20,
        "vhalf_mV": 0.0,
        "vslope_mV": 5.0,
        "tau_open_ms": 6.0,
        "tau_close_ms": 2.0,
        "directionality": "pre_to_post",
        "endpoint_orientations": {"pre": 1, "post": -1},
        "represented_parameters": list(REPRESENTED),
        "unrepresented_parameters": [],
        "solver": {
            "neuron": "derivimplicit",
            "arbor": "cnexp",
            "equations_preserved": True,
            "finite_step_bitwise_identical": False,
        },
    }
    provenance = {
        "comparison_class": "app_owned_equation_port",
        "gap_catalogue": {
            "name": "digifly_gap",
            "path": str(catalogue),
            "sha256": digest,
        },
        "gap_bridge": {
            "status": "installed",
            "catalogue_prefix": "digifly_",
            "mechanisms": ["gap", "rect_gap", "hetero_rect_gap"],
            "fallback_mechanism": None,
        },
    }
    config = {
        "gap": {
            "mechanism": "hetero_rect_gap",
            "directionality": "pre_to_post",
            "default_g_uS": 0.001,
            "g_closed_frac": 0.0,
            "empirical_residual_frac": 0.20,
            "vhalf_mV": 0.0,
            "vslope_mV": 5.0,
            "tau_open_ms": 6.0,
            "tau_close_ms": 2.0,
        },
        "metadata": {"gap_model_comparison": dict(model)},
    }
    return model, provenance, config, catalogue


def _statuses(rows: list[dict]) -> dict[str, str]:
    return {str(row["check"]): str(row["status"]) for row in rows}


def test_exact_contract_accepts_prefixed_effective_name_and_unprefixed_catalogue_entry(
    tmp_path: Path,
) -> None:
    model, provenance, config, catalogue = _evidence(tmp_path)
    provenance_path = tmp_path / "worker_provenance.json"
    provenance_path.write_text(json.dumps(provenance), encoding="utf-8")

    rows, summary = exact_gap_contract_checks(
        gap_model_comparison=model,
        worker_provenance=provenance,
        enabled_config=config,
        disabled_config=config,
        provenance_path=provenance_path,
    )

    assert summary == {
        "passed": True,
        "equation_level_gap_mechanism_exact": True,
        "finite_step_bitwise_identical": False,
        "numerical_solver_difference_declared": True,
        "required_check_count": len(rows),
        "required_failure_count": 0,
        "status_counts": {"pass": len(rows), "fail": 0},
        "effective_mechanism": "digifly_hetero_rect_gap",
        "catalogue_name": "digifly_gap",
        "catalogue_path": str(catalogue.resolve()),
        "catalogue_sha256": hashlib.sha256(catalogue.read_bytes()).hexdigest(),
    }
    assert set(_statuses(rows).values()) == {"pass"}


def test_builtin_gj_approximation_cannot_pass_exact_contract(tmp_path: Path) -> None:
    model, provenance, config, _ = _evidence(tmp_path)
    model["effective_mechanism"] = "gj"
    config["gap"]["mechanism"] = "gj"
    provenance["gap_bridge"] = {
        "status": "installed",
        "catalogue_prefix": "",
        "mechanisms": ["gj"],
    }

    rows, summary = exact_gap_contract_checks(
        gap_model_comparison=model,
        worker_provenance=provenance,
        enabled_config=config,
        disabled_config=config,
    )

    statuses = _statuses(rows)
    assert not summary["passed"]
    assert statuses["effective custom Arbor junction"] == "fail"
    assert statuses["gap_enabled: saved config selects custom junction"] == "fail"
    assert statuses["gap_disabled: saved config selects custom junction"] == "fail"


@pytest.mark.parametrize(
    ("field", "bad_value", "check"),
    [
        ("g_closed_frac", 0.2, "exact heterotypic parameter: g_closed_frac"),
        ("empirical_residual_frac", 0.0, "exact heterotypic parameter: empirical_residual_frac"),
        ("vhalf_mV", 1.0, "exact heterotypic parameter: vhalf_mV"),
        ("vslope_mV", 4.0, "exact heterotypic parameter: vslope_mV"),
        ("tau_open_ms", 2.0, "exact heterotypic parameter: tau_open_ms"),
        ("tau_close_ms", 6.0, "exact heterotypic parameter: tau_close_ms"),
    ],
)
def test_locked_heterotypic_parameters_are_individually_required(
    tmp_path: Path, field: str, bad_value: float, check: str
) -> None:
    model, provenance, _, _ = _evidence(tmp_path)
    model[field] = bad_value

    rows, summary = exact_gap_contract_checks(
        gap_model_comparison=model,
        worker_provenance=provenance,
    )

    assert not summary["passed"]
    assert _statuses(rows)[check] == "fail"


def test_orientation_and_solver_difference_are_required_evidence(tmp_path: Path) -> None:
    model, provenance, _, _ = _evidence(tmp_path)
    model["endpoint_orientations"] = {"pre": 1, "post": 1}
    model["solver"] = {
        "neuron": "derivimplicit",
        "arbor": "derivimplicit",
        "equations_preserved": True,
        "finite_step_bitwise_identical": True,
    }

    rows, summary = exact_gap_contract_checks(
        gap_model_comparison=model,
        worker_provenance=provenance,
    )

    statuses = _statuses(rows)
    assert not summary["passed"]
    assert not summary["equation_level_gap_mechanism_exact"]
    assert not summary["numerical_solver_difference_declared"]
    assert statuses["paired endpoint orientations"] == "fail"
    assert statuses["cnexp versus derivimplicit difference declared"] == "fail"


def test_catalogue_hash_is_cross_checked_with_provenance_and_loaded_file(tmp_path: Path) -> None:
    model, provenance, _, catalogue = _evidence(tmp_path)
    catalogue.write_bytes(b"changed after the worker recorded provenance")

    rows, summary = exact_gap_contract_checks(
        gap_model_comparison=model,
        worker_provenance=provenance,
    )

    statuses = _statuses(rows)
    assert not summary["passed"]
    assert statuses["catalogue SHA-256 cross-checked"] == "pass"
    assert statuses["loaded catalogue artifact hash verified"] == "fail"


def test_artifact_manifest_and_output_guard_keep_writes_disjoint(tmp_path: Path) -> None:
    output = tmp_path / "audit"
    inputs = tmp_path / "saved-run"
    inputs.mkdir()

    artifacts = _artifact_paths(output)
    assert artifacts
    assert all(path.parent == output for path in artifacts.values())
    _assert_disjoint_output(output, [inputs])
    with pytest.raises(ExactGapAuditError, match="disjoint"):
        _assert_disjoint_output(inputs / "audit", [inputs])


def test_gap_off_stimulus_source_soma_gate_uses_all_resolved_seeds() -> None:
    configs = {
        label: {"seeds": [13127, 13479, 13645]}
        for label in (
            "baseline_gap_enabled",
            "baseline_gap_disabled",
            "arbor_gap_enabled",
            "arbor_gap_disabled",
        )
    }
    rows = [
        {
            "condition": "gap_disabled",
            "neuron_id": 13127,
            "location": "soma",
            "scientific_pass": True,
            "rmse_mV": 0.4,
            "baseline_range_mV": 90.0,
            "arbor_range_mV": 89.5,
        },
        {
            "condition": "gap_disabled",
            "neuron_id": 13479,
            "location": "soma",
            "scientific_pass": False,
            "rmse_mV": 12.0,
            "baseline_range_mV": 105.0,
            "arbor_range_mV": 72.0,
        },
        # A gap-on pass for the same source cannot conceal its gap-off failure.
        {
            "condition": "gap_enabled",
            "neuron_id": 13479,
            "location": "soma",
            "scientific_pass": True,
            "rmse_mV": 0.1,
            "baseline_range_mV": 100.0,
            "arbor_range_mV": 100.0,
        },
    ]

    readiness = stimulus_source_soma_readiness(
        trace_rows=rows,
        configs=configs,
        pass_fraction_min=1.0,
    )

    assert readiness["resolved_seed_ids"] == [13127, 13479, 13645]
    assert readiness["comparable_seed_soma_count"] == 2
    assert readiness["missing_seed_soma_ids"] == [13645]
    assert readiness["passing_seed_soma_count"] == 1
    assert readiness["pass_fraction"] == pytest.approx(1 / 3)
    assert not readiness["passed"]
    assert readiness["worst_rmse_evidence"] == {
        "neuron_id": 13479,
        "rmse_mV": 12.0,
        "baseline_range_mV": 105.0,
        "arbor_range_mV": 72.0,
        "absolute_range_error_mV": 33.0,
        "scientific_pass": False,
    }
    assert readiness["worst_range_evidence"] == readiness["worst_rmse_evidence"]


def test_stimulus_seed_resolution_falls_back_to_nonzero_per_cell_amplitudes() -> None:
    config = {
        "stim": {
            "pulse_train": {
                "amps_by_gid": {"13127": 0.9, "13479": 0.0, "13645": 0.9}
            }
        }
    }
    rows = [
        {
            "condition": "gap_disabled",
            "neuron_id": neuron_id,
            "location": "soma",
            "scientific_pass": True,
            "rmse_mV": 0.1,
            "baseline_range_mV": 100.0,
            "arbor_range_mV": 100.0,
        }
        for neuron_id in (13127, 13645)
    ]

    readiness = stimulus_source_soma_readiness(
        trace_rows=rows,
        configs={"baseline_gap_disabled": config, "arbor_gap_disabled": config},
    )

    assert readiness["resolved_seed_ids"] == [13127, 13645]
    assert readiness["pass_fraction"] == 1.0
    assert readiness["passed"]


def test_stimulus_source_gate_fails_closed_on_saved_seed_disagreement() -> None:
    row = {
        "condition": "gap_disabled",
        "neuron_id": 13127,
        "location": "soma",
        "scientific_pass": True,
        "rmse_mV": 0.1,
        "baseline_range_mV": 100.0,
        "arbor_range_mV": 100.0,
    }
    readiness = stimulus_source_soma_readiness(
        trace_rows=[row],
        configs={
            "baseline_gap_disabled": {"seeds": [13127]},
            "arbor_gap_disabled": {"seeds": [13127, 13479]},
        },
    )

    assert not readiness["seed_ids_consistent"]
    assert readiness["resolved_seed_ids"] == [13127, 13479]
    assert not readiness["passed"]


def test_required_stimulus_source_gate_preserves_verdict_semantics() -> None:
    base = {
        "verdict": "EQUIVALENT_WITHIN_DECLARED_TOLERANCES",
        "passed": True,
        "result_passed": True,
        "equation_level_gap_mechanism_exact": True,
        "gates": {"implementation": True, "key_trace_shape": True},
    }

    failed = _apply_required_result_gate(
        base,
        gate_name="stimulus_source_soma_fraction",
        gate_passed=False,
    )
    assert failed["gates"]["stimulus_source_soma_fraction"] is False
    assert not failed["result_passed"]
    assert not failed["passed"]
    assert failed["verdict"] == "NOT_YET_EQUIVALENT"

    passed = _apply_required_result_gate(
        base,
        gate_name="stimulus_source_soma_fraction",
        gate_passed=True,
    )
    assert passed["result_passed"]
    assert passed["passed"]
    assert passed["verdict"] == "EQUIVALENT_WITHIN_DECLARED_TOLERANCES"


def test_failed_exact_contract_creates_no_output(tmp_path: Path) -> None:
    comparison = tmp_path / "comparison"
    enabled = comparison / "gap_enabled"
    disabled = comparison / "gap_disabled"
    enabled.mkdir(parents=True)
    disabled.mkdir()
    model, provenance, config, _ = _evidence(tmp_path)
    model["effective_mechanism"] = "gj"
    (comparison / "comparison_summary.json").write_text(
        json.dumps(
            {
                "status": "complete",
                "comparison_class": "app_owned_equation_port",
                "worker_provenance": str(comparison / "worker_provenance.json"),
                "gap_model_comparison": model,
            }
        ),
        encoding="utf-8",
    )
    (comparison / "worker_provenance.json").write_text(json.dumps(provenance), encoding="utf-8")
    for run_dir in (enabled, disabled):
        (run_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
    output = tmp_path / "requested-audit-output"

    with pytest.raises(ExactGapAuditError, match="exact-gap contract"):
        audit_exact_gap_equivalence(
            baseline_gap_enabled=tmp_path / "baseline-enabled",
            baseline_gap_disabled=tmp_path / "baseline-disabled",
            arbor_gap_enabled=enabled,
            arbor_gap_disabled=disabled,
            output_dir=output,
            staged_auditor_path=tmp_path / "missing-staged-auditor.py",
        )

    assert not output.exists()


def test_markdown_rewrites_staged_approximation_prose_to_equation_port_contract(
    tmp_path: Path,
) -> None:
    path = tmp_path / "report.md"

    def staged_writer(target: Path, *_args: object) -> None:
        target.write_text(
            "\n".join(
                (
                    "# Escape-SIZ Simulator Equivalence Audit",
                    "",
                    "## Declared Approximations",
                    "",
                    "_None._",
                    "",
                    "The built-in junction is a fixed asymmetric approximation.",
                    "",
                    "## Key Trace Metrics",
                    "",
                    "trace table",
                    "",
                    "## Gap-On Minus Gap-Off Direction",
                    "",
                    "direction table",
                    "",
                    "## Gap Approximation Calibration",
                    "",
                    "static reverse fraction table",
                    "",
                    "## Coverage",
                    "",
                    "coverage text",
                )
            ),
            encoding="utf-8",
        )

    auditor = SimpleNamespace(_write_markdown=staged_writer)
    _write_exact_markdown(
        auditor,
        path=path,
        report={
            "implementation_summary": {
                "effective_mechanism": "digifly_hetero_rect_gap",
                "catalogue_name": "digifly_gap",
                "catalogue_sha256": "a" * 64,
            },
            "baseline_readiness": {
                "passed": False,
                "resolved_seed_ids": [13127, 13479],
                "pass_fraction": 0.5,
                "required_pass_fraction": 1.0,
                "comparable_seed_soma_count": 2,
                "required_seed_count": 2,
                "missing_seed_soma_ids": [],
                "worst_rmse_evidence": {
                    "neuron_id": 13479,
                    "rmse_mV": 12.0,
                    "baseline_range_mV": 105.0,
                    "arbor_range_mV": 72.0,
                },
                "worst_range_evidence": {
                    "neuron_id": 13479,
                    "absolute_range_error_mV": 33.0,
                    "baseline_range_mV": 105.0,
                    "arbor_range_mV": 72.0,
                },
            },
        },
        implementation=None,
        traces=None,
        spikes=None,
        pulses=None,
        gap_effects=None,
        calibration=None,
    )

    rendered = path.read_text(encoding="utf-8")
    assert "# Escape-SIZ Exact-Gap Simulator Equivalence Audit" in rendered
    assert "## Exact Gap Mechanism Contract" in rendered
    assert "logistic gate, residual floor, endpoint orientation" in rendered
    assert "`cnexp` while NEURON uses `derivimplicit`" in rendered
    assert "## Gap-Off Stimulus-Source Soma Baseline Readiness" in rendered
    assert "`stimulus_source_soma_fraction` = `FAIL`" in rendered
    assert "Pass fraction: `0.500000`" in rendered
    assert "Worst RMSE: seed `13479`, `12.0` mV" in rendered
    assert "fixed asymmetric approximation" not in rendered
    assert "Gap Approximation Calibration" not in rendered
    assert "static reverse fraction table" not in rendered
