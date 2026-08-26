#!/usr/bin/env python3
"""App-owned wrapper around the native Escape-SIZ NEURON recipe.

The native project remains the scientific source of truth. This worker changes
only output bindings: caches, requests, simulations, status files, and plots are
written beneath ``--output-root/escape_siz``.
"""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from typing import Any


RUNNER_NAME = "run_baseline_with_10002_gfcs_contact_site_na_heatmaps.py"
POSTSYNAPTIC_3D_MODULE = "make_manual_gf_heatmaps_plus_3d_voltage"
GAP_MECHANISM_NAMES = ("Gap", "RectGap", "HeteroRectGap")
GAP_SOURCE_NAMES = ("Gap.mod", "RectGap.mod", "HeteroRectGap.mod")
OUTPUT_PATH_KEYS = frozenset(
    {
        "baseline_out_dir",
        "ais_cache_csv",
        "cache_session_root",
        "compartment_summary_csv",
        "gap_enabled_run_dir",
        "metadata_path",
        "out_dir",
        "pdf",
        "phase_timings_json",
        "png",
        "provenance_json",
        "records_csv",
        "records_manifest",
        "response_json",
        "run_dir",
        "run_root",
        "runs_root",
        "run_summary_json",
        "source_compartment_summary_csv",
        "source_pdf",
        "spike_times_csv",
        "spikes_csv",
        "status_path",
        "summary_json",
        "summary_path",
    }
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--digifly-public-root", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--camera-preset", default=None)
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
    parser.add_argument(
        "--postsynaptic-only-3d-plots",
        action="store_true",
        help="Reproduce the Ablation notebook's heatmap plus postsynaptic 3D figure bundle.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Resolve and verify bindings without starting NEURON.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    public_root = Path(args.digifly_public_root).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    execution_root = output_root / "escape_siz"
    gfc_root = public_root / "Phase 2" / "Projects" / "Escape-SIZ" / "Giant Fiber Ablation Comparisons"
    phase2_root = public_root / "Phase 2"
    gap_mechanism_root = phase2_root / "data"
    runner_path = gfc_root / RUNNER_NAME
    if not runner_path.is_file():
        raise FileNotFoundError(f"Native Escape-SIZ runner is missing: {runner_path}")
    execution_root.mkdir(parents=True, exist_ok=True)
    if args.camera_preset:
        _validate_camera_preset(Path(args.camera_preset).expanduser().resolve())
    for path in (str(gfc_root), str(phase2_root)):
        if path not in sys.path:
            sys.path.insert(0, path)

    # The active notebook uses /opt/anaconda3 NEURON 9.0.1.  Load the current
    # binary directly, without invoking the native compile-first helper, and
    # prove the input files were not touched.  Fresh cache children receive a
    # sitecustomize guard that replaces only the native auto-compile function;
    # normal mechanism loading remains native and read-only.
    os.environ["DIGIFLY_GAP_MECH_DIR"] = str(gap_mechanism_root.resolve())
    test_stub = os.environ.get("DIGIFLY_APP_TEST_STUBS") == "1"
    gap_metadata = (
        {"test_stub": True, "input_policy": "unit_test_only"}
        if test_stub
        else _verify_gap_mechanisms_read_only(gap_mechanism_root)
    )
    guard_path = _install_gap_autocompile_guard(execution_root)
    _prepend_pythonpath(guard_path.parent)
    if not test_stub:
        _probe_child_gap_guard(phase2_root)

    _event("stage", "Importing native Escape-SIZ recipe", source=str(runner_path))
    import run_baseline_with_10002_gfcs_contact_site_na_heatmaps as native  # type: ignore

    ais_binding = _prepare_app_owned_ais_cache(native.GFC_CASE_JSON, execution_root)
    baseline_binding = (
        {"test_stub": True, "selection_policy": "unit_test_only"}
        if test_stub
        else _freeze_baseline_contact_source(native)
    )
    _bind_app_owned_outputs(
        native,
        execution_root,
        camera_preset=Path(args.camera_preset).expanduser().resolve() if args.camera_preset else None,
        ais_cache_path=Path(ais_binding["app_path"]),
    )
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
    provenance = _write_provenance(
        execution_root / "worker_provenance.json",
        public_root=public_root,
        runner_path=runner_path,
        execution_root=execution_root,
        native=native,
        args=args,
        gap_metadata=gap_metadata,
        gap_guard_path=guard_path,
        ais_binding=ais_binding,
        baseline_binding=baseline_binding,
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
    try:
        result = native.run_workflow(native_args)
    except Exception as exc:
        failure = _failure_payload(
            stage="neuron_workflow",
            message=str(exc),
            summary_path=Path(planned["summary_path"]),
        )
        failure["provenance_json"] = str((execution_root / "worker_provenance.json").resolve())
        _write_json(Path(planned["status_path"]), failure)
        _write_json(Path(planned["summary_path"]), failure)
        _event("error", "Escape-SIZ workflow failed", detail=str(exc), stage="neuron_workflow")
        raise
    summary = Path(str(result["summary_json"])).expanduser().resolve()
    if not _is_relative_to(summary, execution_root):
        raise RuntimeError(f"Output boundary violation: summary escaped app root: {summary}")
    custom_edges = bool(result.get("separate_gfs"))
    _assert_result_output_boundary(result, execution_root, custom_edges=custom_edges)
    if args.postsynaptic_only_3d_plots:
        _event("stage", "Building Ablation-notebook heatmap and postsynaptic 3D figure")
        try:
            bundle = _make_postsynaptic_only_3d_bundle(
                result,
                execution_root=execution_root,
                vmin=float(args.vmin),
                vmax=float(args.vmax),
            )
        except Exception as exc:
            result["simulation_status"] = str(result.get("status") or "complete")
            result["status"] = "failed"
            result["failed_stage"] = "postsynaptic_3d_plot"
            result["notebook_plot_warning"] = str(exc)
            failed_provenance_path = _write_per_run_provenance(summary, provenance)
            result["provenance_json"] = str(failed_provenance_path)
            _write_json(summary, result)
            _write_json(
                Path(planned["status_path"]),
                _failure_payload(
                    stage="postsynaptic_3d_plot",
                    message=str(exc),
                    summary_path=summary,
                    simulation_status="complete",
                ),
            )
            _event("warning", "Ablation-notebook 3D figure could not be created", detail=str(exc))
            raise RuntimeError("The NEURON run completed, but the requested Ablation-notebook figure failed") from exc
        else:
            result["notebook_plot_bundle"] = bundle
            _assert_result_output_boundary(result, execution_root, custom_edges=custom_edges)
            _event(
                "artifact",
                "Ablation-notebook 3D figure completed",
                path=str(bundle.get("png") or bundle.get("pdf") or ""),
            )
    provenance_path = _write_per_run_provenance(summary, provenance)
    result["provenance_json"] = str(provenance_path)
    _write_json(summary, result)
    _event("artifact", "Escape-SIZ summary completed", path=str(summary), status=result.get("status"))
    return 0


def _bind_app_owned_outputs(
    native: Any,
    execution_root: Path,
    *,
    camera_preset: Path | None = None,
    ais_cache_path: Path | None = None,
) -> None:
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
    if ais_cache_path is not None and hasattr(native, "_visible_contact_case"):
        original_visible_contact_case = native._visible_contact_case

        def _app_owned_visible_contact_case(*args: Any, **kwargs: Any) -> Any:
            result = original_visible_contact_case(*args, **kwargs)
            case_cfg = result[0]
            case_cfg["ais_cache_csv"] = str(ais_cache_path.resolve())
            return (case_cfg, *result[1:])

        native._visible_contact_case = _app_owned_visible_contact_case
    if camera_preset is not None:
        if not camera_preset.is_file():
            raise FileNotFoundError(f"Saved-view camera preset is missing: {camera_preset}")
        native.bothgf.vplots.GF_REFERENCE_CAMERA_CANDIDATES = (camera_preset,)


def _native_namespace(args: argparse.Namespace) -> argparse.Namespace:
    # The native parser has the same fields except for app binding arguments.
    payload = vars(args).copy()
    payload.pop("digifly_public_root", None)
    payload.pop("output_root", None)
    payload.pop("camera_preset", None)
    payload.pop("postsynaptic_only_3d_plots", None)
    payload.pop("dry_run", None)
    return argparse.Namespace(**payload)


def _make_postsynaptic_only_3d_bundle(
    result: dict[str, Any],
    *,
    execution_root: Path,
    vmin: float,
    vmax: float,
) -> dict[str, Any]:
    plots = result.get("plots") or {}
    source_pdf = Path(str(plots.get("pdf") or "")).expanduser().resolve()
    if not source_pdf.is_file():
        raise FileNotFoundError(f"Native source PDF is unavailable: {source_pdf}")
    if not _is_relative_to(source_pdf, execution_root):
        raise RuntimeError(f"Output boundary violation: source PDF escaped app root: {source_pdf}")
    plotter = importlib.import_module(POSTSYNAPTIC_3D_MODULE)
    plotter.PLOTS_ROOT = execution_root / "comparison_plots" / "manual_frequency_tuning"
    plotter.PLOTS_ROOT.mkdir(parents=True, exist_ok=True)
    bundle = dict(plotter.make_figure_for_source(source_pdf, vmin=vmin, vmax=vmax))
    for key in ("png", "pdf", "compartment_summary_csv", "summary_json"):
        value = bundle.get(key)
        if not value:
            raise RuntimeError(f"Ablation-notebook plotter did not return {key}")
        artifact = Path(str(value)).expanduser().resolve()
        if not _is_relative_to(artifact, execution_root):
            raise RuntimeError(f"Output boundary violation: notebook plot {key} escaped app root: {value}")
        if not artifact.is_file():
            raise FileNotFoundError(f"Ablation-notebook plot artifact is missing: {artifact}")
    bundle["source_pdf"] = str(source_pdf)
    return bundle


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=_json_default), encoding="utf-8")


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    item = getattr(value, "item", None)
    if callable(item):
        return item()
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def _assert_output_binding(planned: dict[str, Path], execution_root: Path) -> None:
    for key in ("session_root", "run_root", "status_path", "summary_path"):
        target = Path(planned[key]).expanduser().resolve()
        if not _is_relative_to(target, execution_root):
            raise RuntimeError(f"Output boundary violation for {key}: {target}")


def _assert_result_output_boundary(
    value: Any,
    execution_root: Path,
    *,
    key: str | None = None,
    custom_edges: bool = False,
) -> None:
    if isinstance(value, dict):
        for child_key, child in value.items():
            _assert_result_output_boundary(
                child,
                execution_root,
                key=str(child_key),
                custom_edges=custom_edges,
            )
        return
    if isinstance(value, (list, tuple)):
        for child in value:
            _assert_result_output_boundary(child, execution_root, key=key, custom_edges=custom_edges)
        return
    is_output_key = key in OUTPUT_PATH_KEYS or (custom_edges and key in {"chemical_edges_path", "edges_path"})
    if not is_output_key or not isinstance(value, (str, Path)) or not str(value):
        return
    target = Path(value).expanduser()
    resolved = target.resolve() if target.is_absolute() else (execution_root / target).resolve()
    if not _is_relative_to(resolved, execution_root):
        raise RuntimeError(f"Output boundary violation for result {key}: {target}")


def _write_provenance(
    path: Path,
    *,
    public_root: Path,
    runner_path: Path,
    execution_root: Path,
    native: Any,
    args: argparse.Namespace,
    gap_metadata: dict[str, Any],
    gap_guard_path: Path,
    ais_binding: dict[str, Any],
    baseline_binding: dict[str, Any],
) -> dict[str, Any]:
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
        "native_runner_sha256": _sha256(runner_path),
        "execution_root": str(execution_root),
        "contact_edge_input": str(native.GFC_EDGE_PATH),
        "contact_edge_sha256": _sha256(Path(native.GFC_EDGE_PATH)),
        "base_case_input": str(native.GFC_CASE_JSON),
        "base_case_sha256": _sha256(Path(native.GFC_CASE_JSON)),
        "postsynaptic_3d_plotter": str(Path(native.__file__).resolve().parent / f"{POSTSYNAPTIC_3D_MODULE}.py"),
        "postsynaptic_3d_plotter_sha256": _sha256(
            Path(native.__file__).resolve().parent / f"{POSTSYNAPTIC_3D_MODULE}.py"
        ),
        "camera_preset": args.camera_preset,
        "camera_preset_sha256": _sha256(Path(args.camera_preset).expanduser().resolve()) if args.camera_preset else None,
        "gap_mechanisms": gap_metadata,
        "gap_autocompile_guard": str(gap_guard_path),
        "gap_autocompile_guard_sha256": _sha256(gap_guard_path),
        "ais_cache_binding": ais_binding,
        "frozen_contact_node_source": baseline_binding,
        "arguments": vars(args),
        "environment": {
            key: os.environ.get(key)
            for key in (
                "PYTHONPATH",
                "PYTHONNOUSERSITE",
                "NEURON_MODULE_OPTIONS",
                "MPLCONFIGDIR",
                "DIGIFLY_GAP_MECH_DIR",
                "DIGIFLY_APP_TEST_STUBS",
            )
        },
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return payload


def _verify_gap_mechanisms_read_only(root: Path) -> dict[str, Any]:
    resolved = root.expanduser().resolve()
    sentinel = resolved / ".digifly_gap_mechanisms.json"
    sources = [resolved / name for name in GAP_SOURCE_NAMES]
    libraries = sorted(resolved.glob("*/libnrnmech.*")) + sorted(resolved.glob("libnrnmech.*"))
    required = [*sources, sentinel, *libraries]
    missing = [str(path) for path in required if not path.is_file()]
    if not libraries:
        missing.append(str(resolved / "<architecture>" / "libnrnmech"))
    if missing:
        raise FileNotFoundError("Required gap mechanism inputs are missing: " + ", ".join(missing))
    before = _file_snapshots(required)
    import neuron  # type: ignore
    from neuron import h, load_mechanisms  # type: ignore

    sentinel_payload = json.loads(sentinel.read_text(encoding="utf-8"))
    compiled_version = str((sentinel_payload.get("neuron_runtime") or {}).get("neuron_version") or "")
    active_version = str(getattr(neuron, "__version__", ""))
    if compiled_version != active_version:
        raise RuntimeError(
            f"Input-only gap mechanisms were compiled for NEURON {compiled_version or 'unknown'}, "
            f"but the worker resolved NEURON {active_version or 'unknown'}."
        )
    recorded_sources = dict(sentinel_payload.get("sources") or {})
    stale = [
        source.name
        for source in sources
        if int(recorded_sources.get(source.name, -1)) != int(source.stat().st_mtime_ns)
    ]
    if stale:
        raise RuntimeError("Input-only gap mechanism sentinel is stale for: " + ", ".join(stale))
    load_mechanisms(str(resolved))
    missing_mechanisms = [name for name in GAP_MECHANISM_NAMES if not hasattr(h, name)]
    if missing_mechanisms:
        raise RuntimeError("Could not load required gap mechanisms: " + ", ".join(missing_mechanisms))
    after = _file_snapshots(required)
    if before != after:
        raise RuntimeError("A read-only mechanism probe unexpectedly changed Digifly Public inputs.")
    return {
        "root": str(resolved),
        "active_neuron_version": active_version,
        "active_neuron_path": str(getattr(neuron, "__file__", "unknown")),
        "compile_sentinel": str(sentinel),
        "files": before,
        "loaded": list(GAP_MECHANISM_NAMES),
        "input_policy": "read_only_no_native_autocompile",
    }


def _file_snapshots(paths: list[Path]) -> dict[str, dict[str, Any]]:
    return {
        str(path.resolve()): {
            "size": int(path.stat().st_size),
            "mtime_ns": int(path.stat().st_mtime_ns),
            "sha256": _sha256(path),
        }
        for path in paths
    }


def _install_gap_autocompile_guard(execution_root: Path) -> Path:
    bootstrap = execution_root / "_runtime" / "python_bootstrap"
    bootstrap.mkdir(parents=True, exist_ok=True)
    guard = bootstrap / "sitecustomize.py"
    source = '''"""Digifly App child guard: never compile mechanisms in the input workspace."""
import os

if os.environ.get("DIGIFLY_GAP_MECH_DIR"):
    from digifly.phase2.neuron_build import gaps as _digifly_gaps

    def _digifly_app_input_only_compile(root):
        return f"{root}: native auto-compile disabled by Digifly App input-only policy"

    _digifly_app_input_only_compile._digifly_app_input_only_guard = True
    _digifly_gaps._compile_gap_mechanisms = _digifly_app_input_only_compile
'''
    guard.write_text(source, encoding="utf-8")
    compile(source, str(guard), "exec")
    return guard


def _prepend_pythonpath(path: Path) -> None:
    value = str(path.expanduser().resolve())
    existing = [item for item in os.environ.get("PYTHONPATH", "").split(os.pathsep) if item]
    os.environ["PYTHONPATH"] = os.pathsep.join(dict.fromkeys([value, *existing]))


def _probe_child_gap_guard(phase2_root: Path) -> None:
    code = (
        "from digifly.phase2.neuron_build import gaps; "
        "assert getattr(gaps._compile_gap_mechanisms, '_digifly_app_input_only_guard', False); "
        "print('input-only-gap-guard=ready')"
    )
    completed = subprocess.run(
        [sys.executable, "-B", "-c", code],
        cwd=str(phase2_root),
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0 or "input-only-gap-guard=ready" not in completed.stdout:
        raise RuntimeError(
            "Fresh cache children did not activate the input-only gap guard.\n"
            + (completed.stderr or completed.stdout).strip()
        )


def _prepare_app_owned_ais_cache(base_case_path: Path, execution_root: Path) -> dict[str, Any]:
    case_path = Path(base_case_path)
    case = json.loads(case_path.read_text(encoding="utf-8")) if case_path.is_file() else {}
    raw_source = str(case.get("ais_cache_csv") or "").strip()
    source = Path(raw_source).expanduser().resolve() if raw_source else None
    name = source.name if source is not None else "escape_siz_ais_cache.csv"
    destination = execution_root / "cache" / "ais" / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    seeded = False
    if not destination.exists() and source is not None and source.is_file():
        shutil.copy2(source, destination)
        seeded = True
    return {
        "source_path": str(source) if source is not None else None,
        "source_sha256": _sha256(source) if source is not None else None,
        "app_path": str(destination.resolve()),
        "app_sha256_before_run": _sha256(destination),
        "seeded_from_input": seeded,
    }


def _freeze_baseline_contact_source(native: Any) -> dict[str, Any]:
    contact_na = native.contact_na
    candidates = sorted(
        Path(contact_na.HERE).joinpath("runs").glob("baseline_with_10002_manual_style_heatmaps_*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    selected: Path | None = None
    payload: dict[str, Any] | None = None
    for path in candidates:
        candidate = json.loads(path.read_text(encoding="utf-8"))
        if (
            candidate.get("contact_count_policy") == native.bothgf.CONTACT_COUNT_POLICY
            and candidate.get("run_summaries")
        ):
            selected = path.resolve()
            payload = candidate
            break
    if selected is None:
        fallback = Path(contact_na.HERE) / "runs" / "baseline_with_10002_manual_style_heatmaps_20260706_134355.json"
        if fallback.is_file():
            selected = fallback.resolve()
            payload = json.loads(selected.read_text(encoding="utf-8"))
    if selected is None or payload is None:
        raise FileNotFoundError("Could not freeze the corrected baseline summary used for contact-node selection.")
    frozen_payload = copy.deepcopy(payload)
    contact_na._latest_corrected_baseline_summary = lambda: copy.deepcopy(frozen_payload)
    return {
        "path": str(selected),
        "sha256": _sha256(selected),
        "mtime_ns": int(selected.stat().st_mtime_ns),
        "selection_policy": "frozen_before_run_from_native_latest_corrected_baseline_summary",
    }


def _validate_camera_preset(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"Saved-view camera preset is missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    camera = payload.get("camera") if isinstance(payload, dict) else None
    for key in ("position", "focal_point", "view_up"):
        values = camera.get(key) if isinstance(camera, dict) else None
        if not isinstance(values, list) or len(values) != 3:
            raise ValueError(f"Camera preset field camera.{key} must be a three-number list: {path}")
        if not all(math.isfinite(float(value)) for value in values):
            raise ValueError(f"Camera preset field camera.{key} contains a non-finite value: {path}")


def _failure_payload(
    *,
    stage: str,
    message: str,
    summary_path: Path,
    simulation_status: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": "failed",
        "failed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "failed_stage": stage,
        "error": message,
        "summary_json": str(summary_path.resolve()),
    }
    if simulation_status is not None:
        payload["simulation_status"] = simulation_status
    return payload


def _write_per_run_provenance(summary: Path, payload: dict[str, Any]) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    destination = summary.parent / "provenance" / f"escape_siz_{stamp}.json"
    _write_json(destination, payload)
    return destination.resolve()


def _event(kind: str, message: str, **details: Any) -> None:
    print(json.dumps({"event": kind, "message": message, **details}, sort_keys=True), flush=True)


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


if __name__ == "__main__":
    raise SystemExit(main())
