#!/usr/bin/env python3
"""Audit an exact-gap Arbor run against the saved Escape-SIZ NEURON run.

This module deliberately keeps the large, read-only signal comparison in the
validated Phase 2 staging auditor.  It replaces that auditor's historical
``built-in gj`` implementation gate with an app-owned gate for the custom
``hetero_rect_gap`` catalogue mechanism.

No simulator is started.  Every generated artifact is placed below the
explicit ``output_dir``; the four run directories and the staging tree are
treated as immutable inputs.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
from types import ModuleType
from typing import Any, Mapping, Sequence


CONDITIONS = ("gap_enabled", "gap_disabled")
EXACT_EFFECTIVE_MECHANISM = "digifly_hetero_rect_gap"
UNPREFIXED_MECHANISM = "hetero_rect_gap"
CATALOGUE_NAME = "digifly_gap"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
RESOURCE_ROOT_ENV = "DIGIFLY_WORKSTATION_RESOURCE_ROOT"
STIMULUS_SOURCE_SOMA_PASS_FRACTION_MIN = 1.0


def _resource_root() -> Path:
    override = os.environ.get(RESOURCE_ROOT_ENV, "").strip()
    if override:
        return Path(override).expanduser().resolve()
    source_root = Path(__file__).resolve().parents[3]
    if (source_root / "mechanisms" / "arbor_gap_junctions").is_dir():
        return source_root
    installed = Path(sys.prefix).resolve() / "share" / "digifly-workstation"
    if installed.is_dir():
        return installed
    return Path(sys.executable).resolve().parent

EXPECTED_PARAMETERS: dict[str, float] = {
    "default_g_uS": 0.001,
    "g_closed_frac": 0.0,
    "empirical_residual_frac": 0.20,
    "vhalf_mV": 0.0,
    "vslope_mV": 5.0,
    "tau_open_ms": 6.0,
    "tau_close_ms": 2.0,
}

REPRESENTED_PARAMETER_ALIASES: dict[str, frozenset[str]] = {
    "default_g_uS": frozenset(("default_g_uS", "gmax_open", "gmax_open_uS")),
    "g_closed_frac": frozenset(("g_closed_frac", "gmax_closed", "hetero_g_closed_frac")),
    "empirical_residual_frac": frozenset(("empirical_residual_frac", "requested_empirical_residual_frac")),
    "vhalf_mV": frozenset(("vhalf", "vhalf_mV", "hetero_vhalf_mV")),
    "vslope_mV": frozenset(("vslope", "vslope_mV", "hetero_vslope_mV")),
    "tau_open_ms": frozenset(("tau_open_ms", "requested_tau_open_ms")),
    "tau_close_ms": frozenset(("tau_close_ms", "requested_tau_close_ms")),
    "endpoint_orientations": frozenset(("orientation", "orientations", "endpoint_orientations")),
}

_STALE_MECHANISM_CHECKS = frozenset(
    (
        "voltage-dependent heterotypic junction mechanism",
        "heterotypic approximation is explicit and parameterized",
    )
)


class ExactGapAuditError(RuntimeError):
    """Raised when saved evidence cannot support an exact-gap audit."""


def _json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ExactGapAuditError(f"Required audit input does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ExactGapAuditError(f"Required audit input is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ExactGapAuditError(f"Required audit input is not a JSON object: {path}")
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stable(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _first(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _as_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _float_equal(actual: Any, expected: float) -> bool:
    try:
        value = float(actual)
    except (TypeError, ValueError):
        return False
    return math.isfinite(value) and math.isclose(value, expected, rel_tol=0.0, abs_tol=1e-12)


def _config_seed_ids(config: Mapping[str, Any]) -> tuple[int, ...]:
    """Resolve directly stimulated cell IDs from one saved run config."""

    direct = config.get("seeds")
    if isinstance(direct, (list, tuple, set, frozenset)):
        values = sorted({int(value) for value in direct})
        if values:
            return tuple(values)

    pulse = _as_mapping(_as_mapping(config.get("stim")).get("pulse_train"))
    amps = _as_mapping(pulse.get("amps_by_gid"))
    stimulated: set[int] = set()
    for raw_id, raw_amplitude in amps.items():
        try:
            amplitude = float(raw_amplitude)
            neuron_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if math.isfinite(amplitude) and abs(amplitude) > 0.0:
            stimulated.add(neuron_id)
    if stimulated:
        return tuple(sorted(stimulated))

    metadata = _as_mapping(config.get("metadata"))
    declared = metadata.get("stimulus_target_ids")
    if isinstance(declared, (list, tuple, set, frozenset)):
        return tuple(sorted({int(value) for value in declared}))
    return ()


def stimulus_source_soma_readiness(
    *,
    trace_rows: Sequence[Mapping[str, Any]],
    configs: Mapping[str, Mapping[str, Any]],
    pass_fraction_min: float = STIMULUS_SOURCE_SOMA_PASS_FRACTION_MIN,
) -> dict[str, Any]:
    """Summarize the gap-off soma equivalence of directly stimulated cells.

    The denominator is the complete resolved seed set, not merely the traces
    that happened to be recorded. Missing or duplicate seed-soma metrics and
    disagreement between saved run configs therefore fail closed.
    """

    threshold = float(pass_fraction_min)
    if not math.isfinite(threshold) or not 0.0 <= threshold <= 1.0:
        raise ExactGapAuditError(
            "stimulus_source_soma_pass_fraction_min must be finite and between 0 and 1."
        )
    ids_by_config = {
        str(label): list(_config_seed_ids(_as_mapping(config)))
        for label, config in configs.items()
    }
    nonempty_sets = [tuple(values) for values in ids_by_config.values() if values]
    seed_ids = sorted({value for values in nonempty_sets for value in values})
    seeds_resolved = bool(seed_ids) and len(nonempty_sets) == len(ids_by_config)
    seeds_consistent = seeds_resolved and all(values == nonempty_sets[0] for values in nonempty_sets)

    rows_by_id: dict[int, list[dict[str, Any]]] = {neuron_id: [] for neuron_id in seed_ids}
    for raw_row in trace_rows:
        row = dict(raw_row)
        try:
            neuron_id = int(row.get("neuron_id"))
        except (TypeError, ValueError):
            continue
        if (
            neuron_id in rows_by_id
            and str(row.get("condition")) == "gap_disabled"
            and str(row.get("location")) == "soma"
        ):
            rows_by_id[neuron_id].append(row)

    missing_ids = [neuron_id for neuron_id, rows in rows_by_id.items() if not rows]
    duplicate_ids = [neuron_id for neuron_id, rows in rows_by_id.items() if len(rows) > 1]
    comparable_rows = [rows[0] for rows in rows_by_id.values() if len(rows) == 1]
    passed_ids = sorted(
        int(row["neuron_id"])
        for row in comparable_rows
        if bool(row.get("scientific_pass"))
    )
    pass_fraction = float(len(passed_ids) / len(seed_ids)) if seed_ids else 0.0

    def finite_metric(row: Mapping[str, Any], key: str) -> float | None:
        try:
            value = float(row.get(key))
        except (TypeError, ValueError):
            return None
        return value if math.isfinite(value) else None

    metric_rows = [
        row for row in comparable_rows if finite_metric(row, "rmse_mV") is not None
    ]
    worst_rmse_row = max(
        metric_rows,
        key=lambda row: float(row["rmse_mV"]),
        default=None,
    )
    range_rows: list[tuple[dict[str, Any], float]] = []
    for row in comparable_rows:
        baseline_range = finite_metric(row, "baseline_range_mV")
        arbor_range = finite_metric(row, "arbor_range_mV")
        if baseline_range is not None and arbor_range is not None:
            range_rows.append((row, abs(arbor_range - baseline_range)))
    worst_range_item = max(range_rows, key=lambda item: item[1], default=None)

    def evidence(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        baseline_range = finite_metric(row, "baseline_range_mV")
        arbor_range = finite_metric(row, "arbor_range_mV")
        return {
            "neuron_id": int(row["neuron_id"]),
            "rmse_mV": finite_metric(row, "rmse_mV"),
            "baseline_range_mV": baseline_range,
            "arbor_range_mV": arbor_range,
            "absolute_range_error_mV": (
                abs(arbor_range - baseline_range)
                if baseline_range is not None and arbor_range is not None
                else None
            ),
            "scientific_pass": bool(row.get("scientific_pass")),
        }

    passed = bool(
        seeds_consistent
        and not missing_ids
        and not duplicate_ids
        and pass_fraction >= threshold
    )
    return {
        "passed": passed,
        "condition": "gap_disabled",
        "resolved_seed_ids": seed_ids,
        "seed_ids_by_config": ids_by_config,
        "seed_ids_resolved": seeds_resolved,
        "seed_ids_consistent": seeds_consistent,
        "required_seed_count": len(seed_ids),
        "comparable_seed_soma_count": len(comparable_rows),
        "passing_seed_soma_count": len(passed_ids),
        "passing_seed_ids": passed_ids,
        "missing_seed_soma_ids": missing_ids,
        "duplicate_seed_soma_ids": duplicate_ids,
        "pass_fraction": pass_fraction,
        "required_pass_fraction": threshold,
        "worst_rmse_evidence": evidence(worst_rmse_row),
        "worst_range_evidence": evidence(worst_range_item[0] if worst_range_item else None),
    }


def _apply_required_result_gate(
    report: Mapping[str, Any],
    *,
    gate_name: str,
    gate_passed: bool,
) -> dict[str, Any]:
    """Add one required result gate while preserving staged verdict semantics."""

    updated = dict(report)
    gates = dict(updated.get("gates") or {})
    gates[str(gate_name)] = bool(gate_passed)
    implementation_passed = bool(gates.get("implementation"))
    result_passed = all(bool(value) for key, value in gates.items() if key != "implementation")
    passed = bool(implementation_passed and result_passed)
    equation_exact = bool(updated.get("equation_level_gap_mechanism_exact", False))
    if passed and not equation_exact:
        verdict = "RESULT_EQUIVALENT_WITHIN_TOLERANCES_WITH_DECLARED_GAP_APPROXIMATION"
    elif passed:
        verdict = "EQUIVALENT_WITHIN_DECLARED_TOLERANCES"
    else:
        verdict = "NOT_YET_EQUIVALENT"
    updated.update(
        gates=gates,
        result_passed=result_passed,
        passed=passed,
        verdict=verdict,
    )
    return updated


def _check(
    rows: list[dict[str, Any]],
    *,
    category: str,
    name: str,
    expected: Any,
    actual: Any,
    passed: bool,
    detail: str = "",
) -> None:
    rows.append(
        {
            "category": category,
            "check": name,
            "status": "pass" if passed else "fail",
            "required": True,
            "expected": _stable(expected),
            "actual": _stable(actual),
            "detail": detail,
        }
    )


def _resolve_catalogue_path(raw: Any, provenance_path: Path | None) -> Path | None:
    if raw is None or not str(raw).strip():
        return None
    path = Path(str(raw)).expanduser()
    if not path.is_absolute() and provenance_path is not None:
        path = provenance_path.parent / path
    return path.resolve()


def _normal_names(values: Any) -> set[str]:
    if not isinstance(values, (list, tuple, set, frozenset)):
        return set()
    return {str(value).strip() for value in values if str(value).strip()}


def exact_gap_contract_checks(
    *,
    gap_model_comparison: Mapping[str, Any],
    worker_provenance: Mapping[str, Any],
    enabled_config: Mapping[str, Any] | None = None,
    disabled_config: Mapping[str, Any] | None = None,
    provenance_path: str | Path | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return required implementation checks for the custom junction.

    ``gap_model_comparison`` is the primary, human-readable contract saved in
    ``comparison_summary.json``.  The catalogue hash/path and installation are
    independently cross-checked against ``worker_provenance.json`` and the
    actual catalogue file.  Config mappings are optional for unit-level use,
    but a full audit supplies both condition configs.
    """

    model = _as_mapping(gap_model_comparison)
    provenance = _as_mapping(worker_provenance)
    provenance_file = Path(provenance_path).expanduser().resolve() if provenance_path else None
    catalogue = _as_mapping(provenance.get("gap_catalogue"))
    bridge = _as_mapping(provenance.get("gap_bridge"))
    rows: list[dict[str, Any]] = []

    requested_model = _first(model.get("requested_model"), model.get("gj_model_requested"))
    _check(
        rows,
        category="mechanism",
        name="requested NEURON gap model",
        expected="heterotypic_rectifying",
        actual=requested_model,
        passed=str(requested_model) == "heterotypic_rectifying",
    )

    summary_effective = _first(model.get("effective_mechanism"), model.get("gj_model_effective"))
    provenance_mechanism = _first(
        catalogue.get("effective_mechanism"),
        catalogue.get("mechanism"),
        bridge.get("effective_mechanism"),
        bridge.get("mechanism"),
    )
    provenance_mechanisms = _normal_names(
        _first(catalogue.get("mechanisms"), bridge.get("mechanisms"), [])
    )
    provenance_prefix = str(
        _first(
            catalogue.get("prefix"),
            catalogue.get("extension_prefix"),
            bridge.get("prefix"),
            bridge.get("catalogue_prefix"),
            "",
        )
    )
    summary_exact = str(summary_effective) == EXACT_EFFECTIVE_MECHANISM
    provenance_exact = str(provenance_mechanism) == EXACT_EFFECTIVE_MECHANISM or (
        str(provenance_mechanism) == UNPREFIXED_MECHANISM
        and provenance_prefix.rstrip("_:") in ("digifly", "digifly_gap")
    ) or (
        UNPREFIXED_MECHANISM in provenance_mechanisms
        and provenance_prefix.rstrip("_:") in ("digifly", "digifly_gap")
    )
    _check(
        rows,
        category="mechanism",
        name="effective custom Arbor junction",
        expected={
            "summary": EXACT_EFFECTIVE_MECHANISM,
            "provenance": [EXACT_EFFECTIVE_MECHANISM, UNPREFIXED_MECHANISM],
        },
        actual={
            "summary": summary_effective,
            "provenance": provenance_mechanism,
            "provenance_mechanisms": sorted(provenance_mechanisms),
            "prefix": provenance_prefix,
        },
        passed=summary_exact and provenance_exact,
        detail="The built-in Arbor gj mechanism is not accepted for this audit.",
    )

    catalogue_name = _first(model.get("catalogue_name"), catalogue.get("name"), catalogue.get("catalogue_name"))
    _check(
        rows,
        category="mechanism",
        name="custom catalogue identity",
        expected=CATALOGUE_NAME,
        actual=catalogue_name,
        passed=str(catalogue_name) == CATALOGUE_NAME,
    )

    for key, expected in EXPECTED_PARAMETERS.items():
        aliases = {
            "g_closed_frac": ("g_closed_frac", "requested_g_closed_frac", "requested_hetero_g_closed_frac"),
            "empirical_residual_frac": ("empirical_residual_frac", "requested_empirical_residual_frac"),
            "vhalf_mV": ("vhalf_mV", "hetero_vhalf_mV", "requested_vhalf_mV"),
            "vslope_mV": ("vslope_mV", "hetero_vslope_mV", "requested_vslope_mV"),
            "tau_open_ms": ("tau_open_ms", "requested_tau_open_ms"),
            "tau_close_ms": ("tau_close_ms", "requested_tau_close_ms"),
            "default_g_uS": ("default_g_uS", "gmax_open_uS"),
        }[key]
        actual = _first(*(model.get(alias) for alias in aliases))
        _check(
            rows,
            category="mechanism",
            name=f"exact heterotypic parameter: {key}",
            expected=expected,
            actual=actual,
            passed=_float_equal(actual, expected),
        )

    directionality = model.get("directionality")
    _check(
        rows,
        category="mechanism",
        name="gap directionality",
        expected="pre_to_post",
        actual=directionality,
        passed=str(directionality) == "pre_to_post",
    )

    orientations = _as_mapping(model.get("endpoint_orientations"))
    orientations_ok = _float_equal(orientations.get("pre"), 1.0) and _float_equal(
        orientations.get("post"), -1.0
    )
    _check(
        rows,
        category="mechanism",
        name="paired endpoint orientations",
        expected={"pre": 1.0, "post": -1.0},
        actual=orientations,
        passed=orientations_ok,
        detail="Each directed contact must install complementary endpoint orientations.",
    )

    represented = _normal_names(model.get("represented_parameters"))
    missing_represented = sorted(
        canonical
        for canonical, aliases in REPRESENTED_PARAMETER_ALIASES.items()
        if not represented.intersection(aliases)
    )
    _check(
        rows,
        category="mechanism",
        name="all junction parameters represented",
        expected=sorted(REPRESENTED_PARAMETER_ALIASES),
        actual={"declared": sorted(represented), "missing_semantics": missing_represented},
        passed=not missing_represented,
    )

    unrepresented = model.get("unrepresented_parameters")
    _check(
        rows,
        category="mechanism",
        name="no requested junction parameter is unrepresented",
        expected=[],
        actual=unrepresented,
        passed=isinstance(unrepresented, list) and not unrepresented,
    )

    solver = _as_mapping(model.get("solver"))
    solver_contract = {
        "neuron": str(solver.get("neuron") or "").lower(),
        "arbor": str(solver.get("arbor") or "").lower(),
        "equations_preserved": solver.get("equations_preserved"),
        "finite_step_bitwise_identical": solver.get("finite_step_bitwise_identical"),
    }
    solver_ok = solver_contract == {
        "neuron": "derivimplicit",
        "arbor": "cnexp",
        "equations_preserved": True,
        "finite_step_bitwise_identical": False,
    }
    _check(
        rows,
        category="numerics",
        name="cnexp versus derivimplicit difference declared",
        expected={
            "neuron": "derivimplicit",
            "arbor": "cnexp",
            "equations_preserved": True,
            "finite_step_bitwise_identical": False,
        },
        actual=solver_contract,
        passed=solver_ok,
        detail=(
            "The junction equations are preserved, but finite-step trajectories are not claimed "
            "to be bitwise identical across the two state solvers."
        ),
    )

    summary_path_raw = model.get("catalogue_path")
    provenance_path_raw = _first(catalogue.get("path"), catalogue.get("catalogue_path"))
    summary_catalogue_path = _resolve_catalogue_path(summary_path_raw, provenance_file)
    provenance_catalogue_path = _resolve_catalogue_path(provenance_path_raw, provenance_file)
    paths_match = (
        summary_catalogue_path is not None
        and provenance_catalogue_path is not None
        and summary_catalogue_path == provenance_catalogue_path
    )
    _check(
        rows,
        category="provenance",
        name="catalogue path cross-checked",
        expected="matching summary and worker-provenance paths",
        actual={"summary": str(summary_catalogue_path or ""), "provenance": str(provenance_catalogue_path or "")},
        passed=paths_match,
    )

    summary_hash = str(model.get("catalogue_sha256") or "").lower()
    provenance_hash = str(_first(catalogue.get("sha256"), catalogue.get("catalogue_sha256")) or "").lower()
    declared_hash_ok = bool(SHA256_RE.fullmatch(summary_hash)) and summary_hash == provenance_hash
    _check(
        rows,
        category="provenance",
        name="catalogue SHA-256 cross-checked",
        expected="same 64-character SHA-256 in summary and worker provenance",
        actual={"summary": summary_hash, "provenance": provenance_hash},
        passed=declared_hash_ok,
    )

    catalogue_path = summary_catalogue_path if paths_match else None
    actual_hash = _sha256(catalogue_path) if catalogue_path is not None and catalogue_path.is_file() else ""
    _check(
        rows,
        category="provenance",
        name="loaded catalogue artifact hash verified",
        expected=summary_hash,
        actual={"path": str(catalogue_path or ""), "sha256": actual_hash},
        passed=declared_hash_ok and actual_hash == summary_hash,
        detail="The post-run audit hashes the same shared catalogue artifact recorded by the worker.",
    )

    bridge_status = str(_first(bridge.get("status"), bridge.get("installation_status"), "")).lower()
    bridge_installed = bridge.get("installed") is True or bridge.get("active") is True or bridge_status in {
        "installed",
        "active",
        "loaded",
    }
    _check(
        rows,
        category="runtime_evidence",
        name="custom gap bridge installed",
        expected=True,
        actual=bridge,
        passed=bridge_installed,
        detail="A source/config declaration alone is insufficient; the worker must record bridge installation.",
    )
    fallback = bridge.get("fallback_mechanism", object())
    _check(
        rows,
        category="runtime_evidence",
        name="custom gap bridge has no fallback mechanism",
        expected=None,
        actual=None if fallback is None else fallback,
        passed=fallback is None,
        detail="A failed custom-catalogue load must stop the run rather than silently select built-in gj.",
    )

    configs = (("gap_enabled", enabled_config), ("gap_disabled", disabled_config))
    for condition, config_value in configs:
        if config_value is None:
            continue
        config = _as_mapping(config_value)
        config_gap = _as_mapping(config.get("gap"))
        config_meta = _as_mapping(config.get("metadata"))
        config_model = _as_mapping(config_meta.get("gap_model_comparison"))
        configured_mechanism = _first(
            config_gap.get("mechanism"),
            config_model.get("effective_mechanism"),
            config_meta.get("effective_gap_mechanism"),
            config_meta.get("gj_model_effective"),
        )
        configured_exact = str(configured_mechanism) in (EXACT_EFFECTIVE_MECHANISM, UNPREFIXED_MECHANISM)
        _check(
            rows,
            category="runtime_evidence",
            name=f"{condition}: saved config selects custom junction",
            expected=[EXACT_EFFECTIVE_MECHANISM, UNPREFIXED_MECHANISM],
            actual=configured_mechanism,
            passed=configured_exact,
        )
        config_parameters = {
            key: _first(
                config_gap.get(key),
                config_gap.get("gmax_open_uS") if key == "default_g_uS" else None,
            )
            for key in EXPECTED_PARAMETERS
        }
        config_contract_ok = all(
            _float_equal(config_parameters[key], expected)
            for key, expected in EXPECTED_PARAMETERS.items()
        ) and str(config_gap.get("directionality")) == "pre_to_post"
        _check(
            rows,
            category="runtime_evidence",
            name=f"{condition}: saved config preserves exact junction parameters",
            expected={**EXPECTED_PARAMETERS, "directionality": "pre_to_post"},
            actual={**config_parameters, "directionality": config_gap.get("directionality")},
            passed=config_contract_ok,
        )

    required_failures = [row for row in rows if row["required"] and row["status"] != "pass"]
    solver_row = next(row for row in rows if row["check"] == "cnexp versus derivimplicit difference declared")
    mechanism_rows = [row for row in rows if row["category"] == "mechanism"]
    equation_exact = bool(solver.get("equations_preserved") is True) and all(
        row["status"] == "pass" for row in mechanism_rows
    )
    summary = {
        "passed": not required_failures,
        "equation_level_gap_mechanism_exact": equation_exact,
        "finite_step_bitwise_identical": False,
        "numerical_solver_difference_declared": solver_row["status"] == "pass",
        "required_check_count": len(rows),
        "required_failure_count": len(required_failures),
        "status_counts": {
            "pass": sum(row["status"] == "pass" for row in rows),
            "fail": sum(row["status"] == "fail" for row in rows),
        },
        "effective_mechanism": summary_effective,
        "catalogue_name": catalogue_name,
        "catalogue_path": str(catalogue_path or summary_catalogue_path or ""),
        "catalogue_sha256": summary_hash,
    }
    return rows, summary


def _load_staged_auditor(path: Path) -> ModuleType:
    if not path.is_file():
        raise ExactGapAuditError(f"Staged equivalence auditor does not exist: {path}")
    module_name = f"_digifly_staged_equivalence_{hashlib.sha256(str(path).encode()).hexdigest()[:12]}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ExactGapAuditError(f"Cannot load staged equivalence auditor: {path}")

    module = importlib.util.module_from_spec(spec)
    original_path = list(sys.path)
    original_bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = original_path
        sys.dont_write_bytecode = original_bytecode
        sys.modules.pop(module_name, None)
    return module


def _common_comparison_root(arbor_runs: Mapping[str, Path]) -> Path:
    parents = {Path(path).resolve().parent for path in arbor_runs.values()}
    if len(parents) != 1:
        raise ExactGapAuditError("Arbor gap-enabled and gap-disabled runs must share one comparison root.")
    return next(iter(parents))


def _resolve_evidence_paths(
    *,
    arbor_runs: Mapping[str, Path],
    comparison_summary_path: str | Path | None,
    worker_provenance_path: str | Path | None,
) -> tuple[Path, Path]:
    root = _common_comparison_root(arbor_runs)
    summary_path = (
        Path(comparison_summary_path).expanduser().resolve()
        if comparison_summary_path
        else (root / "comparison_summary.json").resolve()
    )
    summary = _json(summary_path)
    provenance_path = (
        Path(worker_provenance_path).expanduser().resolve()
        if worker_provenance_path
        else Path(str(summary.get("worker_provenance") or root / "worker_provenance.json")).expanduser().resolve()
    )
    return summary_path, provenance_path


def _assert_disjoint_output(output: Path, inputs: Sequence[Path]) -> None:
    for raw in inputs:
        path = raw.resolve()
        if output == path or output in path.parents or path in output.parents:
            raise ExactGapAuditError(
                f"Audit output must be disjoint from every immutable input: output={output}, input={path}"
            )


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _artifact_paths(output: Path) -> dict[str, Path]:
    artifacts = {
        "implementation_checks_csv": output / "implementation_checks.csv",
        "morphology_checks_csv": output / "morphology_checks.csv",
        "trace_metrics_csv": output / "trace_metrics.csv",
        "key_trace_metrics_csv": output / "key_trace_metrics.csv",
        "spike_metrics_csv": output / "spike_metrics.csv",
        "pulse_metrics_csv": output / "pulse_metrics.csv",
        "gap_effect_metrics_csv": output / "gap_effect_metrics.csv",
        "gap_calibration_metrics_csv": output / "gap_calibration_metrics.csv",
        "gap_calibration_key_metrics_csv": output / "gap_calibration_key_metrics.csv",
        "provenance_json": output / "provenance.json",
        "report_json": output / "equivalence_report.json",
        "report_markdown": output / "equivalence_report.md",
        "key_trace_overlays_png": output / "key_trace_overlays.png",
    }
    escaped = [path for path in artifacts.values() if not _inside(path, output)]
    if escaped:
        raise ExactGapAuditError(f"Refusing audit artifacts outside requested output: {escaped}")
    return artifacts


def _merge_implementation_checks(
    auditor: ModuleType,
    implementation: Any,
    staged_summary: Mapping[str, Any],
    exact_rows: Sequence[Mapping[str, Any]],
    exact_summary: Mapping[str, Any],
) -> tuple[Any, dict[str, Any]]:
    keep = ~implementation["check"].isin(_STALE_MECHANISM_CHECKS)
    merged = auditor.pd.concat(
        (implementation.loc[keep].copy(), auditor.pd.DataFrame(list(exact_rows))),
        ignore_index=True,
    )
    required_failures = merged[merged["required"] & (merged["status"] != "pass")]
    status_counts = merged["status"].value_counts().to_dict()
    summary = {
        **dict(staged_summary),
        **dict(exact_summary),
        "passed": bool(required_failures.empty),
        "required_check_count": int(merged["required"].sum()),
        "required_failure_count": int(len(required_failures)),
        "status_counts": {str(key): int(value) for key, value in status_counts.items()},
        "equation_level_gap_mechanism_exact": bool(exact_summary.get("equation_level_gap_mechanism_exact")),
    }
    return merged, summary


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(dict(payload), indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def _file_provenance(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": str(path.resolve()),
        "size_bytes": int(stat.st_size),
        "mtime_utc": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        "sha256": _sha256(path),
    }


def _write_exact_markdown(
    auditor: ModuleType,
    *,
    path: Path,
    report: Mapping[str, Any],
    implementation: Any,
    traces: Any,
    spikes: Any,
    pulses: Any,
    gap_effects: Any,
    calibration: Any,
) -> None:
    auditor._write_markdown(
        path,
        report,
        implementation,
        traces,
        spikes,
        pulses,
        gap_effects,
        calibration,
    )
    text = path.read_text(encoding="utf-8")
    text = text.replace("# Escape-SIZ Simulator Equivalence Audit", "# Escape-SIZ Exact-Gap Simulator Equivalence Audit")
    text = text.replace("## Declared Approximations", "## Declared Numerical and Discretization Differences")
    differences_heading = "## Declared Numerical and Discretization Differences"
    key_heading = "## Key Trace Metrics"
    differences_start = text.index(differences_heading)
    key_start = text.index(key_heading, differences_start)
    differences_lines = text[differences_start:key_start].rstrip().splitlines()
    last_content = max(index for index, line in enumerate(differences_lines) if line.strip())
    differences_lines[last_content] = (
        "The equation port preserves the logistic gate, residual floor, endpoint orientation, and asymmetric "
        "open/close dynamics. Arbor uses `cnexp` while NEURON uses `derivimplicit`; finite-step trajectories "
        "are therefore compared within declared tolerances rather than claimed to be bitwise identical."
    )
    text = text[:differences_start] + "\n".join(differences_lines) + "\n\n" + text[key_start:]

    calibration_start = text.find("\n## Gap ", text.index("## Gap-On Minus Gap-Off Direction"))
    coverage_start = text.find("\n## Coverage", calibration_start)
    if calibration_start >= 0 and coverage_start > calibration_start:
        text = text[:calibration_start] + text[coverage_start:]
    contract = dict(report.get("implementation_summary") or {})
    exact_section = (
        "## Exact Gap Mechanism Contract\n\n"
        f"- Effective mechanism: `{contract.get('effective_mechanism', '')}`\n"
        f"- Catalogue: `{contract.get('catalogue_name', '')}`\n"
        f"- Catalogue SHA-256: `{contract.get('catalogue_sha256', '')}`\n"
        "- State solvers: NEURON `derivimplicit`; Arbor `cnexp` (declared numerical difference)\n\n"
    )
    readiness = _as_mapping(report.get("baseline_readiness"))
    readiness_section = ""
    if readiness:
        worst_rmse = _as_mapping(readiness.get("worst_rmse_evidence"))
        worst_range = _as_mapping(readiness.get("worst_range_evidence"))
        readiness_section = (
            "## Gap-Off Stimulus-Source Soma Baseline Readiness\n\n"
            f"- Required gate: `stimulus_source_soma_fraction` = "
            f"`{'PASS' if readiness.get('passed') else 'FAIL'}`\n"
            f"- Resolved seed IDs: `{readiness.get('resolved_seed_ids', [])}`\n"
            f"- Pass fraction: `{float(readiness.get('pass_fraction', 0.0)):.6f}` "
            f"(required `{float(readiness.get('required_pass_fraction', 1.0)):.6f}`)\n"
            f"- Comparable seed somas: `{readiness.get('comparable_seed_soma_count', 0)}` / "
            f"`{readiness.get('required_seed_count', 0)}`\n"
            f"- Missing seed somas: `{readiness.get('missing_seed_soma_ids', [])}`\n"
            f"- Worst RMSE: seed `{worst_rmse.get('neuron_id', '')}`, "
            f"`{worst_rmse.get('rmse_mV', '')}` mV; baseline/Arbor ranges "
            f"`{worst_rmse.get('baseline_range_mV', '')}` / "
            f"`{worst_rmse.get('arbor_range_mV', '')}` mV\n"
            f"- Worst range mismatch: seed `{worst_range.get('neuron_id', '')}`, "
            f"absolute range error `{worst_range.get('absolute_range_error_mV', '')}` mV "
            f"(baseline/Arbor `{worst_range.get('baseline_range_mV', '')}` / "
            f"`{worst_range.get('arbor_range_mV', '')}` mV)\n\n"
        )
    text = text.replace(key_heading, exact_section + readiness_section + key_heading, 1)
    path.write_text(text, encoding="utf-8")


def _plot_key_overlays_inside_output(
    path: Path,
    key_cache: Mapping[str, Mapping[int, Mapping[str, Any]]],
) -> None:
    """Render the staged overlay layout without writing caches outside output."""

    import os

    old_mplconfig = os.environ.get("MPLCONFIGDIR")
    try:
        with tempfile.TemporaryDirectory(prefix=".digifly-mpl-", dir=path.parent) as mpl_dir:
            os.environ["MPLCONFIGDIR"] = mpl_dir
            import matplotlib.pyplot as plt

            neuron_ids = (10000, 10002, 10068, 10110, 11446, 11654)
            fig, axes = plt.subplots(
                2,
                3,
                figsize=(16, 8),
                sharex=True,
                sharey=True,
                constrained_layout=True,
            )
            for index, neuron_id in enumerate(neuron_ids):
                ax = axes.flat[index]
                for condition, color in (("gap_enabled", "#b42318"), ("gap_disabled", "#1769aa")):
                    traces = key_cache[condition][neuron_id]
                    ax.plot(
                        traces["t_ms"],
                        traces["baseline"],
                        color=color,
                        linewidth=1.0,
                        alpha=0.85,
                        label=f"NEURON {condition}",
                    )
                    ax.plot(
                        traces["t_ms"],
                        traces["arbor"],
                        color=color,
                        linewidth=1.0,
                        linestyle="--",
                        alpha=0.85,
                        label=f"Arbor {condition}",
                    )
                ax.axhline(0.0, color="#555555", linewidth=0.6)
                ax.set_title(str(neuron_id))
                ax.set_xlabel("Time (ms)")
                ax.set_ylabel("Vm (mV)")
            handles, labels = axes.flat[0].get_legend_handles_labels()
            fig.legend(handles, labels, loc="outside upper center", ncol=4, fontsize=8)
            fig.savefig(path, dpi=180)
            plt.close(fig)
    finally:
        if old_mplconfig is None:
            os.environ.pop("MPLCONFIGDIR", None)
        else:
            os.environ["MPLCONFIGDIR"] = old_mplconfig


def audit_exact_gap_equivalence(
    *,
    baseline_gap_enabled: str | Path,
    baseline_gap_disabled: str | Path,
    arbor_gap_enabled: str | Path,
    arbor_gap_disabled: str | Path,
    output_dir: str | Path,
    staged_auditor_path: str | Path,
    comparison_summary_path: str | Path | None = None,
    worker_provenance_path: str | Path | None = None,
    acceptance: Mapping[str, float] | None = None,
    hash_records: bool = True,
    write_plot: bool = True,
) -> dict[str, Any]:
    """Run the read-only trace audit with the exact custom-gap gate."""

    baseline_runs = {
        "gap_enabled": Path(baseline_gap_enabled).expanduser().resolve(),
        "gap_disabled": Path(baseline_gap_disabled).expanduser().resolve(),
    }
    arbor_runs = {
        "gap_enabled": Path(arbor_gap_enabled).expanduser().resolve(),
        "gap_disabled": Path(arbor_gap_disabled).expanduser().resolve(),
    }
    output = Path(output_dir).expanduser().resolve()
    staged_path = Path(staged_auditor_path).expanduser().resolve()
    summary_path, provenance_path = _resolve_evidence_paths(
        arbor_runs=arbor_runs,
        comparison_summary_path=comparison_summary_path,
        worker_provenance_path=worker_provenance_path,
    )
    _assert_disjoint_output(
        output,
        [*baseline_runs.values(), *arbor_runs.values(), staged_path, summary_path, provenance_path],
    )
    if output.exists():
        raise ExactGapAuditError(f"Audit output already exists: {output}")

    comparison_summary = _json(summary_path)
    worker_provenance = _json(provenance_path)
    if str(comparison_summary.get("status")) != "complete":
        raise ExactGapAuditError(
            "The exact-gap comparison summary is not complete: "
            f"{comparison_summary.get('status')!r}"
        )
    if str(comparison_summary.get("comparison_class")) != "app_owned_equation_port":
        raise ExactGapAuditError(
            "The Arbor comparison was not labeled as the app-owned equation port: "
            f"{comparison_summary.get('comparison_class')!r}"
        )
    if str(worker_provenance.get("comparison_class")) != "app_owned_equation_port":
        raise ExactGapAuditError(
            "Worker provenance does not identify the app-owned equation port: "
            f"{worker_provenance.get('comparison_class')!r}"
        )
    enabled_config = _json(arbor_runs["gap_enabled"] / "config.json")
    disabled_config = _json(arbor_runs["gap_disabled"] / "config.json")
    exact_rows, exact_summary = exact_gap_contract_checks(
        gap_model_comparison=_as_mapping(comparison_summary.get("gap_model_comparison")),
        worker_provenance=worker_provenance,
        enabled_config=enabled_config,
        disabled_config=disabled_config,
        provenance_path=provenance_path,
    )
    if not exact_summary["passed"]:
        failures = [row["check"] for row in exact_rows if row["status"] != "pass"]
        raise ExactGapAuditError(
            "Saved Arbor evidence does not satisfy the exact-gap contract: " + "; ".join(failures)
        )

    auditor = _load_staged_auditor(staged_path)
    for backend, run_map in (("baseline", baseline_runs), ("arbor", arbor_runs)):
        for condition, run_dir in run_map.items():
            if not auditor._valid_run_dir(run_dir):
                raise ExactGapAuditError(f"Incomplete {backend} {condition} run: {run_dir}")
            if backend == "arbor":
                run_summary = _json(run_dir / "run_summary.json")
                if str(run_summary.get("status")) != "completed":
                    raise ExactGapAuditError(
                        f"Arbor {condition} run summary is not completed: {run_summary.get('status')!r}"
                    )

    resolved_acceptance = {**auditor.DEFAULT_ACCEPTANCE, **dict(acceptance or {})}
    resolved_acceptance.setdefault(
        "stimulus_source_soma_pass_fraction_min",
        STIMULUS_SOURCE_SOMA_PASS_FRACTION_MIN,
    )
    output.mkdir(parents=True, exist_ok=False)
    artifacts = _artifact_paths(output)

    implementation, morphology, staged_implementation_summary = auditor.implementation_audit(
        baseline_runs, arbor_runs
    )
    implementation, implementation_summary = _merge_implementation_checks(
        auditor,
        implementation,
        staged_implementation_summary,
        exact_rows,
        exact_summary,
    )
    baseline_configs = {
        condition: auditor._json(baseline_runs[condition] / "config.json") for condition in CONDITIONS
    }
    selected_ids = auditor._selection(baseline_configs["gap_enabled"])
    traces, coverage, key_cache = auditor.trace_audit(
        baseline_runs, arbor_runs, selected_ids, resolved_acceptance
    )
    baseline_readiness = stimulus_source_soma_readiness(
        trace_rows=traces.to_dict("records"),
        configs={
            "baseline_gap_enabled": baseline_configs["gap_enabled"],
            "baseline_gap_disabled": baseline_configs["gap_disabled"],
            "arbor_gap_enabled": enabled_config,
            "arbor_gap_disabled": disabled_config,
        },
        pass_fraction_min=float(
            resolved_acceptance["stimulus_source_soma_pass_fraction_min"]
        ),
    )
    source_ids = set(int(value) for value in baseline_readiness["resolved_seed_ids"])
    traces = traces.copy()
    traces["stimulus_source_soma"] = traces.apply(
        lambda row: bool(
            str(row["condition"]) == "gap_disabled"
            and str(row["location"]) == "soma"
            and int(row["neuron_id"]) in source_ids
        ),
        axis=1,
    )
    spikes = auditor.spike_audit(baseline_runs, arbor_runs, resolved_acceptance)
    pulses = auditor.pulse_audit(key_cache, baseline_configs, resolved_acceptance)
    gap_effects = auditor.gap_effect_audit(spikes, pulses)

    calibration_columns = (
        "candidate",
        "static_reverse_fraction",
        "selected",
        "key_pass_fraction",
        "spike_count_error_sum",
        "minimum_pearson_r",
        "maximum_rmse_mV",
        "all_key_metrics_pass",
    )
    calibration = auditor.pd.DataFrame(columns=calibration_columns)
    calibration_details = auditor.pd.DataFrame()
    calibration_summary = {
        "candidate_count": 0,
        "mode": "not_applicable_exact_custom_mechanism",
        "sweep_reproducible": False,
    }
    report = auditor.verdict_summary(
        implementation_summary,
        traces,
        coverage,
        spikes,
        pulses,
        gap_effects,
        resolved_acceptance,
        calibration_summary,
    )
    report = _apply_required_result_gate(
        report,
        gate_name="stimulus_source_soma_fraction",
        gate_passed=bool(baseline_readiness["passed"]),
    )
    trace_summary = dict(report.get("trace_summary") or {})
    trace_summary.update(
        {
            "stimulus_source_soma_pass_fraction": float(
                baseline_readiness["pass_fraction"]
            ),
            "stimulus_source_soma_required_count": int(
                baseline_readiness["required_seed_count"]
            ),
            "stimulus_source_soma_comparable_count": int(
                baseline_readiness["comparable_seed_soma_count"]
            ),
            "stimulus_source_soma_worst_rmse_evidence": baseline_readiness[
                "worst_rmse_evidence"
            ],
            "stimulus_source_soma_worst_range_evidence": baseline_readiness[
                "worst_range_evidence"
            ],
        }
    )
    report["trace_summary"] = trace_summary
    report["stimulus_source_soma_pass_fraction"] = float(
        baseline_readiness["pass_fraction"]
    )
    report["baseline_readiness"] = baseline_readiness
    report.update(
        {
            "schema_version": 2,
            "audit_profile": "escape_siz_exact_hetero_rect_gap_v1",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "baseline_runs": {key: str(value) for key, value in baseline_runs.items()},
            "arbor_runs": {key: str(value) for key, value in arbor_runs.items()},
            "output_dir": str(output),
            "implementation_summary": implementation_summary,
            "gap_model_comparison": dict(comparison_summary.get("gap_model_comparison") or {}),
        }
    )

    implementation.to_csv(artifacts["implementation_checks_csv"], index=False)
    morphology.to_csv(artifacts["morphology_checks_csv"], index=False)
    traces.to_csv(artifacts["trace_metrics_csv"], index=False)
    traces[traces["key_signal"]].to_csv(artifacts["key_trace_metrics_csv"], index=False)
    spikes.to_csv(artifacts["spike_metrics_csv"], index=False)
    pulses.to_csv(artifacts["pulse_metrics_csv"], index=False)
    gap_effects.to_csv(artifacts["gap_effect_metrics_csv"], index=False)
    calibration.to_csv(artifacts["gap_calibration_metrics_csv"], index=False)
    calibration_details.to_csv(artifacts["gap_calibration_key_metrics_csv"], index=False)

    audit_provenance = auditor.provenance(baseline_runs, arbor_runs, hash_records=hash_records)
    stale_staged_port = (
        "Phase 2_Arbor_staging/data/arbor_mod/hetero_rect_gap.mod"
    )
    audit_provenance["code"] = [
        row
        for row in list(audit_provenance.get("code") or [])
        if stale_staged_port not in str(row.get("path") or "")
    ]
    resource_root = _resource_root()
    worker_root = Path(__file__).resolve().parent
    exact_source_paths = (
        resource_root / "mechanisms" / "arbor_gap_junctions" / "hetero_rect_gap.mod",
        resource_root / "mechanisms" / "arbor_gap_junctions" / "source_manifest.json",
        worker_root / "arbor_gap_bridge.py",
        worker_root / "arbor_escape_siz_worker.py",
        Path(__file__).resolve(),
    )
    exact_source_provenance = [_file_provenance(path) for path in exact_source_paths if path.is_file()]
    audit_provenance["code"].extend(exact_source_provenance)
    audit_provenance.update(
        {
            "schema_version": 2,
            "audit_profile": "escape_siz_exact_hetero_rect_gap_v1",
            "staged_auditor": {"path": str(staged_path), "sha256": _sha256(staged_path)},
            "app_auditor": {"path": str(Path(__file__).resolve()), "sha256": _sha256(Path(__file__))},
            "comparison_summary": {"path": str(summary_path), "sha256": _sha256(summary_path)},
            "worker_provenance": {"path": str(provenance_path), "sha256": _sha256(provenance_path)},
            "exact_gap_contract": exact_summary,
            "gap_equation_port_sources": exact_source_provenance,
        }
    )
    _write_json(artifacts["provenance_json"], audit_provenance)
    if write_plot:
        _plot_key_overlays_inside_output(artifacts["key_trace_overlays_png"], key_cache)
    else:
        artifacts.pop("key_trace_overlays_png")

    report["artifacts"] = {key: str(value) for key, value in artifacts.items()}
    _write_json(artifacts["report_json"], report)
    _write_exact_markdown(
        auditor,
        path=artifacts["report_markdown"],
        report=report,
        implementation=implementation,
        traces=traces,
        spikes=spikes,
        pulses=pulses,
        gap_effects=gap_effects,
        calibration=calibration,
    )
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-gap-enabled", required=True)
    parser.add_argument("--baseline-gap-disabled", required=True)
    parser.add_argument("--arbor-gap-enabled", required=True)
    parser.add_argument("--arbor-gap-disabled", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--staged-auditor", required=True)
    parser.add_argument("--comparison-summary")
    parser.add_argument("--worker-provenance")
    parser.add_argument("--no-record-hashes", action="store_true")
    parser.add_argument("--no-plot", action="store_true")
    parser.add_argument("--fail-on-non-equivalence", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = audit_exact_gap_equivalence(
        baseline_gap_enabled=args.baseline_gap_enabled,
        baseline_gap_disabled=args.baseline_gap_disabled,
        arbor_gap_enabled=args.arbor_gap_enabled,
        arbor_gap_disabled=args.arbor_gap_disabled,
        output_dir=args.output_dir,
        staged_auditor_path=args.staged_auditor,
        comparison_summary_path=args.comparison_summary,
        worker_provenance_path=args.worker_provenance,
        hash_records=not args.no_record_hashes,
        write_plot=not args.no_plot,
    )
    print(
        json.dumps(
            {
                "verdict": report["verdict"],
                "passed": report["passed"],
                "report": report["artifacts"]["report_json"],
            },
            indent=2,
        )
    )
    return 2 if args.fail_on_non_equivalence and not report["passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
