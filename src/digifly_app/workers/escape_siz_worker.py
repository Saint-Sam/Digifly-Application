#!/usr/bin/env python3
"""App-owned wrapper around the native Escape-SIZ NEURON recipe.

The native project remains the scientific source of truth. This worker changes
only output bindings: caches, requests, simulations, status files, and plots are
written beneath ``--output-root/escape_siz``.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import sys
from typing import Any


RUNNER_NAME = "run_baseline_with_10002_gfcs_contact_site_na_heatmaps.py"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--digifly-public-root", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--contact-site-na-multiplier", type=float, default=2.5)
    parser.add_argument("--gj-model", choices=("ohmic", "heterotypic_rectifying"), default="heterotypic_rectifying")
    parser.add_argument("--hetero-g-closed-frac", type=float, default=0.0)
    parser.add_argument("--hetero-vhalf-mV", type=float, default=0.0)
    parser.add_argument("--hetero-vslope-mV", type=float, default=5.0)
    parser.add_argument("--hetero-empirical-residual-frac", type=float, default=0.20)
    parser.add_argument("--hetero-tau-open-ms", type=float, default=6.0)
    parser.add_argument("--hetero-tau-close-ms", type=float, default=2.0)
    parser.add_argument("--separate-gfs", action="store_true")
    parser.add_argument("--gfc2-ohmic", action="store_true")
    parser.add_argument("--freq-hz", type=float, default=100.0)
    parser.add_argument("--max-pulses", type=int, default=10)
    parser.add_argument("--gap-enabled-amp-nA", type=float, default=1.0)
    parser.add_argument("--gap-disabled-amp-nA", type=float, default=0.46142578125)
    parser.add_argument("--stim-target-amps-json", default=None)
    parser.add_argument("--nproc", type=int, default=1)
    parser.add_argument("--force-restart-cache", action="store_true")
    parser.add_argument("--start-timeout-s", type=float, default=3600.0)
    parser.add_argument("--run-timeout-s", type=float, default=3600.0)
    parser.add_argument("--vmin", type=float, default=-80.0)
    parser.add_argument("--vmax", type=float, default=40.0)
    parser.add_argument("--no-extra-stim-target-heatmaps", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Resolve and verify bindings without starting NEURON.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    public_root = Path(args.digifly_public_root).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    execution_root = output_root / "escape_siz"
    gfc_root = public_root / "Phase 2" / "Projects" / "Escape-SIZ" / "Giant Fiber Ablation Comparisons"
    phase2_root = public_root / "Phase 2"
    runner_path = gfc_root / RUNNER_NAME
    if not runner_path.is_file():
        raise FileNotFoundError(f"Native Escape-SIZ runner is missing: {runner_path}")
    execution_root.mkdir(parents=True, exist_ok=True)
    for path in (str(gfc_root), str(phase2_root)):
        if path not in sys.path:
            sys.path.insert(0, path)

    _event("stage", "Importing native Escape-SIZ recipe", source=str(runner_path))
    import run_baseline_with_10002_gfcs_contact_site_na_heatmaps as native  # type: ignore

    _bind_app_owned_outputs(native, execution_root)
    native_args = _native_namespace(args)
    planned = native.workflow_paths(
        native_args.gj_model,
        hetero_g_closed_frac=native_args.hetero_g_closed_frac,
        hetero_vhalf_mV=native_args.hetero_vhalf_mV,
        hetero_vslope_mV=native_args.hetero_vslope_mV,
        hetero_empirical_residual_frac=native_args.hetero_empirical_residual_frac,
        hetero_tau_open_ms=native_args.hetero_tau_open_ms,
        hetero_tau_close_ms=native_args.hetero_tau_close_ms,
        separate_gfs=native_args.separate_gfs,
        gfc2_ohmic=native_args.gfc2_ohmic,
    )
    _assert_output_binding(planned, execution_root)
    _write_provenance(
        execution_root / "worker_provenance.json",
        public_root=public_root,
        runner_path=runner_path,
        execution_root=execution_root,
        native=native,
        args=args,
    )
    _event(
        "stage",
        "App-owned Escape-SIZ bindings verified",
        cache_session_root=str(planned["session_root"]),
        run_root=str(planned["run_root"]),
    )
    if args.dry_run:
        _event("complete", "Dry run completed; no cache or simulation was started.")
        return 0
    _event("stage", "Starting app-owned Escape-SIZ workflow")
    result = native.run_workflow(native_args)
    summary = Path(str(result["summary_json"])).expanduser().resolve()
    if not _is_relative_to(summary, execution_root):
        raise RuntimeError(f"Output boundary violation: summary escaped app root: {summary}")
    _event("artifact", "Escape-SIZ summary completed", path=str(summary), status=result.get("status"))
    return 0


def _bind_app_owned_outputs(native: Any, execution_root: Path) -> None:
    """Redirect every native write root while keeping inputs at source paths."""
    native.HERE = execution_root
    native.OHMIC_GFC_SESSION_ROOT = (
        execution_root
        / "phase2_build_cache_sessions"
        / "10000_10002_10068_10110_11446_11654_DLMs_GFCs"
        / "gap_enabled"
    )
    native.RUN_ROOT = execution_root / "runs"
    native.STATUS_PATH = execution_root / "status.json"
    native.SUMMARY_PATH = execution_root / "summary.json"
    native.bothgf.RUNS_ROOT = execution_root / "runs"
    native.bothgf.PLOTS_ROOT = execution_root / "comparison_plots" / "manual_frequency_tuning"
    native.bothgf.PLOTS_ROOT.mkdir(parents=True, exist_ok=True)


def _native_namespace(args: argparse.Namespace) -> argparse.Namespace:
    # The native parser has the same fields except for app binding arguments.
    payload = vars(args).copy()
    payload.pop("digifly_public_root", None)
    payload.pop("output_root", None)
    payload.pop("dry_run", None)
    return argparse.Namespace(**payload)


def _assert_output_binding(planned: dict[str, Path], execution_root: Path) -> None:
    for key in ("session_root", "run_root", "status_path", "summary_path"):
        target = Path(planned[key]).expanduser().resolve()
        if not _is_relative_to(target, execution_root):
            raise RuntimeError(f"Output boundary violation for {key}: {target}")


def _write_provenance(
    path: Path,
    *,
    public_root: Path,
    runner_path: Path,
    execution_root: Path,
    native: Any,
    args: argparse.Namespace,
) -> None:
    try:
        import neuron  # type: ignore

        neuron_identity = {
            "version": getattr(neuron, "__version__", "unknown"),
            "path": getattr(neuron, "__file__", "unknown"),
        }
    except Exception as exc:
        neuron_identity = {"error": str(exc)}
    payload = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "python": {"version": platform.python_version(), "executable": sys.executable},
        "neuron": neuron_identity,
        "digifly_public_root": str(public_root),
        "native_runner": str(runner_path),
        "native_runner_mtime_ns": runner_path.stat().st_mtime_ns,
        "execution_root": str(execution_root),
        "contact_edge_input": str(native.GFC_EDGE_PATH),
        "base_case_input": str(native.GFC_CASE_JSON),
        "arguments": vars(args),
        "environment": {
            key: os.environ.get(key)
            for key in (
                "PYTHONPATH",
                "PYTHONNOUSERSITE",
                "NEURON_MODULE_OPTIONS",
                "MPLCONFIGDIR",
            )
        },
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _event(kind: str, message: str, **details: Any) -> None:
    print(json.dumps({"event": kind, "message": message, **details}, sort_keys=True), flush=True)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


if __name__ == "__main__":
    raise SystemExit(main())
