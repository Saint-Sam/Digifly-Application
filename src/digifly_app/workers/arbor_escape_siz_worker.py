#!/usr/bin/env python3
"""Run the active Escape-SIZ Ablation recipe as a labeled Arbor comparison.

The staged Arbor project is input-only.  This wrapper materializes the
notebook's no-direct-GF chemical table, plans, simulations, metrics, plots,
and provenance beneath ``--output-root/escape_siz/arbor``.

The gap-enabled condition uses the app-owned ``hetero_rect_gap`` Arbor
junction catalogue.  It preserves the NEURON mechanism equations and locked
notebook parameters while recording Arbor's ``cnexp`` integration in place of
NEURON's ``derivimplicit`` method.  Equivalence is never claimed before the
cross-backend audit passes.
"""

from __future__ import annotations

import argparse
from collections import deque
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
from typing import Any, Mapping, Sequence

sys.dont_write_bytecode = True


GFC2_IDS = (
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
LEGACY_SEED_AIS_POLICY = "legacy_neuron_seed_ais_soma_hh_v1"
LEGACY_SEED_AIS_OVERRIDE_PREFIX = "legacy_neuron_seed_ais_soma_hh"
EXPECTED_LEGACY_SEED_AIS_NODE_IDS: dict[int, int] = {
    13127: 1528,
    13479: 1212,
    13645: 1135,
    13846: 1038,
    14527: 4073,
    14662: 4002,
    15292: 2546,
    15505: 1115,
    15938: 1515,
    16764: 3079,
    17245: 1866,
}
EXPECTED_LEGACY_SEED_AIS_SEGMENT_COUNTS: dict[int, int] = {
    13127: 28,
    13479: 8,
    13645: 5,
    13846: 50,
    14527: 14,
    14662: 48,
    15292: 8,
    15505: 5,
    15938: 27,
    16764: 47,
    17245: 31,
}
EXPECTED_BRANCH_HH: dict[str, float] = {
    "el": -65.0,
    "gkbar": 0.01,
    "gl": 0.0001,
    "gnabar": 0.02,
}
EXPECTED_SOMA_HH: dict[str, float] = {
    "el": -65.0,
    "gkbar": 0.036,
    "gl": 0.0003,
    "gnabar": 0.12,
}
EXPECTED_TEMPERATURE_K = 279.45
COMPARTMENT_RECORDING_IDS = (10000, 10002, 13127, 13479)
EXPECTED_CHEMICAL_SHA256 = "90016e471b1608d2516ecbe84df6a78e4463766fe2125dddeb6a86970e0924f7"
EXPECTED_GAP_SOURCE_SHA256 = "df64a7822294119c6e58b385c34f6b59354805b7ad8bf5f6054b29bd8c74235f"
EXPECTED_CHEMICAL_ROWS = 2430
EXPECTED_DIRECT_GF_ROWS = 99
EXPECTED_FILTERED_ROWS = 2331
EXPECTED_FILTERED_PAIRS = 69
EXPECTED_GAP_ROWS = 959
COMPARISON_CLASS = "app_owned_equation_port"
RECIPE = "ablation_notebook_arbor_exact_gap_v2"
COMPARISON_DIR = "ablation_notebook_exact_gap_comparison"
GAP_CATALOGUE_NAME = "digifly_gap"
GAP_MECHANISM = "digifly_hetero_rect_gap"
CV_POLICY = "every_segment"
REPRESENTED_GAP_PARAMETERS = (
    "gmax_open_uS",
    "g_closed_frac",
    "empirical_residual_frac",
    "orientation",
    "vhalf_mV",
    "vslope_mV",
    "tau_open_ms",
    "tau_close_ms",
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--digifly-public-root", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument(
        "--gap-catalogue",
        required=True,
        help="Path to the compiled app-owned digifly_gap Arbor catalogue.",
    )
    parser.add_argument("--contact-site-na-multiplier", type=float, default=2.5)
    parser.add_argument(
        "--static-reverse-fraction",
        type=float,
        default=0.20,
        help=(
            "Deprecated locked provenance value from the legacy comparison. "
            "It is not used as HeteroRectGap closed conductance."
        ),
    )
    parser.add_argument("--requested-hetero-g-closed-frac", type=float, default=0.0)
    parser.add_argument("--requested-vhalf-mV", type=float, default=0.0)
    parser.add_argument("--requested-vslope-mV", type=float, default=5.0)
    parser.add_argument("--requested-empirical-residual-frac", type=float, default=0.20)
    parser.add_argument("--requested-tau-open-ms", type=float, default=6.0)
    parser.add_argument("--requested-tau-close-ms", type=float, default=2.0)
    parser.add_argument("--freq-hz", type=float, default=100.0)
    parser.add_argument("--max-pulses", type=int, default=10)
    parser.add_argument("--stim-amp-nA", type=float, default=0.9)
    parser.add_argument("--stim-dur-ms", type=float, default=0.4)
    parser.add_argument("--dt-ms", type=float, default=0.01)
    parser.add_argument("--sample-dt-ms", type=float, default=0.05)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument(
        "--cv-policy",
        choices=(CV_POLICY,),
        default=CV_POLICY,
    )
    parser.add_argument("--cv-max-extent-um", type=float, default=20.0)
    parser.add_argument("--vmin", type=float, default=-80.0)
    parser.add_argument("--vmax", type=float, default=40.0)
    parser.add_argument("--no-plots", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    public_root = Path(args.digifly_public_root).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    comparison_root = output_root / "escape_siz" / "arbor" / COMPARISON_DIR
    summary_path = comparison_root / (
        "dry_run_summary.json" if args.dry_run else "comparison_summary.json"
    )
    _reject_public_output(comparison_root, public_root)
    comparison_root.mkdir(parents=True, exist_ok=True)
    _write_json(
        summary_path,
        {
            "status": "validating",
            "backend": "arbor",
            "recipe": RECIPE,
            "comparison_class": COMPARISON_CLASS,
            "equivalence_claim": False,
            "started_at": _stamp(),
            "simulation_started": False,
            "summary_json": str(summary_path.resolve()),
        },
    )
    try:
        return _run(args)
    except Exception as exc:
        try:
            failure = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            failure = {}
        if str(failure.get("status")) != "failed":
            failure.update(
                status="failed",
                backend="arbor",
                recipe=RECIPE,
                comparison_class=COMPARISON_CLASS,
                equivalence_claim=False,
                failed_at=_stamp(),
                error=str(exc),
                summary_json=str(summary_path.resolve()),
            )
            _write_json(summary_path, failure)
            _event("error", "Arbor comparison failed during setup", detail=str(exc))
        raise


def _run(args: argparse.Namespace) -> int:
    public_root = Path(args.digifly_public_root).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    staging_root = public_root / "Phase 2_Arbor_staging"
    project_root = staging_root / "Projects" / "Escape-SIZ" / "Giant Fiber Ablation Comparisons"
    input_root = staging_root / "Projects" / "Escape-SIZ" / "arbor_inputs" / "giant_fiber_ablation"
    comparison_root = output_root / "escape_siz" / "arbor" / COMPARISON_DIR
    plans_root = comparison_root / "_plans"
    filtered_edges = comparison_root / "_inputs" / "chemical_edges_no_direct_gf.csv"
    summary_path = comparison_root / "comparison_summary.json"
    dry_summary_path = comparison_root / "dry_run_summary.json"
    provenance_path = comparison_root / "worker_provenance.json"
    helper_path = project_root / "giant_fiber_ablation_arbor.py"
    auditor_path = project_root / "giant_fiber_neuron_arbor_equivalence.py"
    source_chemical = input_root / "chemical_edges.csv"
    gap_source = input_root / "gap_contacts.csv"
    gap_arbor = input_root / "gap_contacts_arbor.csv"
    contact_nodes = input_root / "contact_site_nodes.json"
    manifest = input_root / "manifest.json"
    gap_catalogue = Path(args.gap_catalogue).expanduser().resolve()
    if not gap_catalogue.is_file():
        raise FileNotFoundError(f"The required app-owned Arbor gap catalogue is missing: {gap_catalogue}")
    gap_catalogue_sha256 = _sha256(gap_catalogue)

    for path in (staging_root, project_root):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    comparison_root.mkdir(parents=True, exist_ok=True)
    plans_root.mkdir(parents=True, exist_ok=True)

    _event("stage", "Validating the self-contained Arbor input bundle", source=str(input_root))
    import arbor  # type: ignore
    import giant_fiber_ablation_arbor as gfa  # type: ignore
    from digifly.phase2.arbor_build import runner as arbor_runner  # type: ignore
    # The GUI launches this file as a standalone external-runtime worker, and
    # the packaged app ships the bridge beside it as data.  Import the sibling
    # first so neither the source checkout nor the frozen GUI package has to be
    # added to the scientific runtime's PYTHONPATH.
    try:
        from arbor_gap_bridge import install_arbor_gap_bridge
    except ModuleNotFoundError:
        # Module-mode execution in tests/development still uses the package
        # import path.
        from digifly_app.workers.arbor_gap_bridge import install_arbor_gap_bridge
    gap_bridge = install_arbor_gap_bridge(
        arbor_module=arbor,
        runner_module=arbor_runner,
        catalogue_path=gap_catalogue,
    )
    gap_bridge_metadata = dict(gap_bridge.metadata)
    _assert_gap_bridge_metadata(
        gap_bridge_metadata,
        gap_catalogue=gap_catalogue,
        gap_catalogue_sha256=gap_catalogue_sha256,
    )
    _event(
        "stage",
        "Installed the app-owned Arbor HeteroRectGap equation port",
        mechanism=GAP_MECHANISM,
        catalogue=str(gap_catalogue),
        catalogue_sha256=gap_catalogue_sha256,
    )
    _assert_requested_contract(args, arbor)
    input_snapshot = _input_snapshot(input_root)
    native_validation = dict(gfa.validate_inputs())
    input_validation = _validate_input_contract(
        native_validation=native_validation,
        source_chemical=source_chemical,
        gap_source=gap_source,
        gap_arbor=gap_arbor,
        contact_nodes=contact_nodes,
        manifest=manifest,
        swc_tools=arbor_runner,
    )
    filtered_validation = _materialize_filtered_edges(source_chemical, filtered_edges)
    input_validation.update(filtered_validation)
    input_validation["direct_gf_rows_retained"] = 0
    seed_ais_nodes = {
        int(neuron_id): int(node_id)
        for neuron_id, node_id in dict(
            input_validation["legacy_seed_ais_biophysics"]["node_ids_by_neuron"]
        ).items()
    }

    # The helper's module global is the only source used by build_config for
    # this circuit.  Rebinding it to the app-owned filtered copy keeps all
    # staged files input-only and reproduces separate_gfs=True.
    gfa.CHEMICAL_EDGES = filtered_edges.resolve()

    condition_configs: dict[str, dict[str, Any]] = {}
    planned_configs: dict[str, str] = {}
    for label in ("gap_enabled", "gap_disabled"):
        config = _condition_config(
            gfa,
            args,
            label,
            comparison_root,
            filtered_edges,
            gap_catalogue=gap_catalogue,
            gap_catalogue_sha256=gap_catalogue_sha256,
            gap_bridge_metadata=gap_bridge_metadata,
            seed_ais_nodes=seed_ais_nodes,
        )
        config_path = plans_root / f"{label}_config.json"
        _write_json(config_path, config)
        condition_configs[label] = config
        planned_configs[label] = str(config_path.resolve())
    _assert_plan_contract(condition_configs, filtered_edges)

    provenance = _provenance(
        args=args,
        public_root=public_root,
        comparison_root=comparison_root,
        arbor_module=arbor,
        helper_path=helper_path,
        auditor_path=auditor_path,
        manifest=manifest,
        source_chemical=source_chemical,
        filtered_edges=filtered_edges,
        gap_source=gap_source,
        gap_arbor=gap_arbor,
        contact_nodes=contact_nodes,
        input_validation=input_validation,
        gap_catalogue=gap_catalogue,
        gap_catalogue_sha256=gap_catalogue_sha256,
        gap_bridge_metadata=gap_bridge_metadata,
    )
    _write_json(provenance_path, provenance)
    _event(
        "stage",
        "Arbor comparison plan verified",
        filtered_chemical_rows=input_validation["filtered_chemical_rows"],
        stimulus_targets=len(GFC2_IDS),
        comparison_class=COMPARISON_CLASS,
        mechanism=GAP_MECHANISM,
    )

    if args.dry_run:
        payload = _summary_base(
            args=args,
            input_validation=input_validation,
            filtered_edges=filtered_edges,
            provenance_path=provenance_path,
            auditor_path=auditor_path,
            planned_configs=planned_configs,
            gap_catalogue=gap_catalogue,
            gap_catalogue_sha256=gap_catalogue_sha256,
            gap_bridge_metadata=gap_bridge_metadata,
        )
        payload.update(status="dry_run_complete", completed_at=_stamp(), simulation_started=False)
        _write_json(dry_summary_path, payload)
        if _input_snapshot(input_root) != input_snapshot:
            raise RuntimeError("The Arbor dry run changed the input bundle.")
        _event("complete", "Arbor dry run completed; no simulation was started.", path=str(dry_summary_path))
        return 0

    try:
        run_dirs: dict[str, str] = {}
        for label in ("gap_enabled", "gap_disabled"):
            _event("stage", f"Running Arbor {label.replace('_', ' ')}")
            run_dir = Path(gfa.run_walking_simulation(condition_configs[label], strict=True)).expanduser().resolve()
            _assert_inside(run_dir, comparison_root, label)
            run_dirs[label] = str(run_dir)
            _event("artifact", f"Arbor {label.replace('_', ' ')} completed", path=str(run_dir))

        plots = (
            dict(
                gfa.make_comparison_plots(
                    run_dirs,
                    output_dir=comparison_root / "plots",
                    circuit="baseline_with_10002_gfcs",
                    vmin=float(args.vmin),
                    vmax=float(args.vmax),
                )
            )
            if not args.no_plots
            else {}
        )
        response_metrics = dict(
            gfa.write_response_metrics(
                run_dirs,
                output_dir=comparison_root,
                circuit="baseline_with_10002_gfcs",
                freq_hz=float(args.freq_hz),
                max_pulses=int(args.max_pulses),
            )
        )
        run_artifacts = {
            label: {
                "run_dir": path,
                "records_csv": str((Path(path) / "records.csv").resolve()),
                "spikes_csv": str((Path(path) / "spikes.csv").resolve()),
                "config_json": str((Path(path) / "config.json").resolve()),
                "run_summary_json": str((Path(path) / "run_summary.json").resolve()),
                "phase_timings_json": str((Path(path) / "_phase_timings.json").resolve()),
                "cell_biophys_csv": str((Path(path) / "cell_biophys.csv").resolve()),
            }
            for label, path in run_dirs.items()
        }
        for label, artifacts in run_artifacts.items():
            missing = [path for key, path in artifacts.items() if key != "run_dir" and not Path(path).is_file()]
            if missing:
                raise RuntimeError(f"Arbor {label} omitted required artifacts: {missing}")
            run_summary = json.loads(Path(artifacts["run_summary_json"]).read_text(encoding="utf-8"))
            if str(run_summary.get("status")) != "completed":
                raise RuntimeError(f"Arbor {label} did not complete: {run_summary}")
            seed_ais_path, seed_ais_evidence = _write_arbor_seed_ais_runtime_evidence(
                Path(artifacts["run_dir"])
            )
            artifacts["seed_ais_biophysics_json"] = str(seed_ais_path.resolve())
            artifacts["seed_ais_biophysics"] = seed_ais_evidence
        payload = _summary_base(
            args=args,
            input_validation=input_validation,
            filtered_edges=filtered_edges,
            provenance_path=provenance_path,
            auditor_path=auditor_path,
            planned_configs=planned_configs,
            gap_catalogue=gap_catalogue,
            gap_catalogue_sha256=gap_catalogue_sha256,
            gap_bridge_metadata=gap_bridge_metadata,
        )
        payload.update(
            status="complete",
            completed_at=_stamp(),
            simulation_started=True,
            plots=plots,
            response_metrics=response_metrics,
            run_artifacts=run_artifacts,
            summary_json=str(summary_path.resolve()),
        )
        _assert_generated_paths(payload, comparison_root)
        if _input_snapshot(input_root) != input_snapshot:
            raise RuntimeError("The Arbor comparison changed the input bundle.")
        _write_json(summary_path, payload)
    except Exception as exc:
        failure = _summary_base(
            args=args,
            input_validation=input_validation,
            filtered_edges=filtered_edges,
            provenance_path=provenance_path,
            auditor_path=auditor_path,
            planned_configs=planned_configs,
            gap_catalogue=gap_catalogue,
            gap_catalogue_sha256=gap_catalogue_sha256,
            gap_bridge_metadata=gap_bridge_metadata,
        )
        failure.update(
            status="failed",
            failed_at=_stamp(),
            error=str(exc),
            summary_json=str(summary_path.resolve()),
        )
        _write_json(summary_path, failure)
        _event("error", "Arbor comparison failed", detail=str(exc))
        raise

    _event("complete", "Arbor comparison completed", path=str(summary_path))
    return 0


def _normalized_seed_ais_nodes(values: Mapping[int | str, int]) -> dict[int, int]:
    try:
        normalized = {int(neuron_id): int(node_id) for neuron_id, node_id in dict(values).items()}
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Stimulated-cell AIS node mapping is invalid.") from exc
    if normalized != EXPECTED_LEGACY_SEED_AIS_NODE_IDS:
        raise RuntimeError(
            "Stimulated-cell AIS node mapping changed: "
            f"expected={EXPECTED_LEGACY_SEED_AIS_NODE_IDS}, found={normalized}"
        )
    return {neuron_id: normalized[neuron_id] for neuron_id in GFC2_IDS}


def _seed_ais_metadata(seed_ais_nodes: Mapping[int | str, int]) -> dict[str, Any]:
    nodes = _normalized_seed_ais_nodes(seed_ais_nodes)
    return {
        "policy": LEGACY_SEED_AIS_POLICY,
        "source_backend": "neuron",
        "target_backend": "arbor",
        "mapping_mode": "legacy_neuron_section_cv",
        "stimulated_neuron_ids": list(GFC2_IDS),
        "node_ids_by_neuron": {str(neuron_id): nodes[neuron_id] for neuron_id in GFC2_IDS},
        "legacy_ais_section_nseg_by_neuron": {
            str(neuron_id): 1 for neuron_id in GFC2_IDS
        },
        "expected_resolved_arbor_segment_count_by_neuron": {
            str(neuron_id): EXPECTED_LEGACY_SEED_AIS_SEGMENT_COUNTS[neuron_id]
            for neuron_id in GFC2_IDS
        },
        "effective_ais_hh": dict(EXPECTED_SOMA_HH),
        "ordinary_branch_hh": dict(EXPECTED_BRANCH_HH),
        "entire_legacy_ais_section": True,
        "equivalence_claim": False,
    }


def _install_legacy_seed_ais_soma_hh(
    config: dict[str, Any],
    *,
    seed_ais_nodes: Mapping[int | str, int],
) -> None:
    """Mirror the legacy NEURON seed-role AIS repaint in an Arbor plan."""

    nodes = _normalized_seed_ais_nodes(seed_ais_nodes)
    existing = list(config.get("cell_biophys_overrides") or [])
    for group in existing:
        if not isinstance(group, Mapping) or not list(group.get("node_ids") or []):
            continue
        raw_ids = group.get("ids", group.get("neuron_ids", group.get("cell_ids")))
        applies_to_seed = raw_ids is None
        if raw_ids is not None:
            try:
                applies_to_seed = bool({int(value) for value in list(raw_ids)} & set(GFC2_IDS))
            except (TypeError, ValueError):
                applies_to_seed = True
        if applies_to_seed:
            raise RuntimeError(
                "The exact Arbor recipe already contains a node-local HH override that could "
                f"change a stimulated GFC2 cell: {dict(group)}"
            )

    seed_groups = [
        {
            "name": f"{LEGACY_SEED_AIS_OVERRIDE_PREFIX}_{neuron_id}",
            "ids": [neuron_id],
            "node_ids": [nodes[neuron_id]],
            "insert_hh": True,
            "node_hh": dict(EXPECTED_SOMA_HH),
        }
        for neuron_id in GFC2_IDS
    ]
    config["cell_biophys_overrides"] = [*existing, *seed_groups]
    # The staging runner uses this opt-in to expand the selected SWC node to
    # its complete legacy NEURON CV.  All canonical seed AIS sections have
    # nseg=1; runtime validation below proves that the whole Section was hit.
    config["legacy_neuron_node_hh_mapping"] = True


def _same_number(actual: Any, expected: float) -> bool:
    try:
        return abs(float(actual) - float(expected)) <= 1e-12
    except (TypeError, ValueError):
        return False


def _assert_hh_values(actual: Any, expected: Mapping[str, float], label: str) -> None:
    values = dict(actual or {}) if isinstance(actual, Mapping) else {}
    if set(values) != set(expected) or any(
        not _same_number(values.get(parameter), value)
        for parameter, value in expected.items()
    ):
        raise RuntimeError(f"{label} changed: expected={dict(expected)}, found={values}")


def _assert_core_biophysics_contract(config: Mapping[str, Any]) -> None:
    for key in ("pre_soma_hh", "post_soma_hh"):
        _assert_hh_values(config.get(key), EXPECTED_SOMA_HH, key)
    for key in ("pre_branch_hh", "post_branch_hh"):
        _assert_hh_values(config.get(key), EXPECTED_BRANCH_HH, key)
    expected_scalars = {
        "passive_e": -65.0,
        "passive_g": 0.0001,
        "Ra": 100.0,
        "cm": 1.0,
        "v_init_mV": -65.0,
        "tempK": EXPECTED_TEMPERATURE_K,
        "default_weight_uS": 0.000003,
        "default_delay_ms": 1.0,
        "syn_tau1_ms": 0.5,
        "syn_tau2_ms": 3.0,
        "syn_e_rev_mV": 0.0,
    }
    changed = {
        key: config.get(key)
        for key, expected in expected_scalars.items()
        if not _same_number(config.get(key), expected)
    }
    chemical = dict(config.get("chemical_synapse") or {})
    expected_chemical = {
        "mechanism": "exp2syn",
        "default_site": "soma",
        "allow_soma_fallback": False,
        "max_sites_per_pair": None,
        "aggregate_conductance": False,
    }
    if (
        changed
        or str(config.get("active_compartment_scope")) != "all"
        or str(config.get("active_posts_mode")) != "all_selected"
        or config.get("post_active") is not True
        or config.get("use_geom_delay") is not True
        or chemical != expected_chemical
    ):
        raise RuntimeError(
            "The exact Arbor core-biophysics contract changed: "
            f"scalars={changed}, chemical_synapse={chemical}."
        )


def _assert_legacy_seed_ais_contract(
    config: Mapping[str, Any],
    *,
    seed_ais_nodes: Mapping[int | str, int] = EXPECTED_LEGACY_SEED_AIS_NODE_IDS,
) -> None:
    nodes = _normalized_seed_ais_nodes(seed_ais_nodes)
    if config.get("legacy_neuron_node_hh_mapping") is not True:
        raise RuntimeError("Legacy NEURON node-to-CV HH mapping is not explicitly enabled.")

    groups = [group for group in list(config.get("cell_biophys_overrides") or []) if isinstance(group, Mapping)]
    by_name: dict[str, list[Mapping[str, Any]]] = {}
    for group in groups:
        by_name.setdefault(str(group.get("name") or ""), []).append(group)
    expected_names = {
        f"{LEGACY_SEED_AIS_OVERRIDE_PREFIX}_{neuron_id}" for neuron_id in GFC2_IDS
    }
    for neuron_id in GFC2_IDS:
        name = f"{LEGACY_SEED_AIS_OVERRIDE_PREFIX}_{neuron_id}"
        matches = by_name.get(name, [])
        if len(matches) != 1:
            raise RuntimeError(f"Expected exactly one {name!r} override; found {len(matches)}.")
        group = dict(matches[0])
        if (
            tuple(int(value) for value in list(group.get("ids") or [])) != (neuron_id,)
            or tuple(int(value) for value in list(group.get("node_ids") or []))
            != (nodes[neuron_id],)
            or group.get("insert_hh") is not True
            or bool(group.get("node_hh_mult"))
        ):
            raise RuntimeError(f"Stimulated-cell AIS override changed for neuron {neuron_id}: {group}")
        _assert_hh_values(group.get("node_hh"), EXPECTED_SOMA_HH, f"seed {neuron_id} AIS HH")

    for group in groups:
        if str(group.get("name") or "") in expected_names or not list(group.get("node_ids") or []):
            continue
        raw_ids = group.get("ids", group.get("neuron_ids", group.get("cell_ids")))
        if raw_ids is None:
            raise RuntimeError("A global node-local HH override could alter stimulated AIS biophysics.")
        try:
            touched = {int(value) for value in list(raw_ids)} & set(GFC2_IDS)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("A node-local HH override has invalid cell IDs.") from exc
        if touched:
            raise RuntimeError(f"Unexpected node-local HH override also touches seeds: {sorted(touched)}")

    expected_metadata = _seed_ais_metadata(nodes)
    actual_metadata = dict((config.get("metadata") or {}).get("legacy_seed_ais_biophysics") or {})
    if actual_metadata != expected_metadata:
        raise RuntimeError(
            "Saved stimulated-cell AIS provenance changed: "
            f"expected={expected_metadata}, found={actual_metadata}"
        )


def _load_locked_swc_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 7:
                continue
            rows.append(
                {
                    "node_id": int(float(parts[0])),
                    "tag": int(float(parts[1])),
                    "x": float(parts[2]),
                    "y": float(parts[3]),
                    "z": float(parts[4]),
                    "radius": float(parts[5]),
                    "parent_id": int(float(parts[6])),
                }
            )
    if not rows:
        raise RuntimeError(f"Locked seed SWC contains no nodes: {path}")
    return rows


def _legacy_spatial_ais_row(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_id = {int(row["node_id"]): dict(row) for row in rows}
    soma_rows = [dict(row) for row in rows if int(row["tag"]) == 1]
    root_rows = [dict(row) for row in rows if int(row["parent_id"]) < 0]
    soma = max(soma_rows or root_rows or [dict(row) for row in rows], key=lambda row: float(row["radius"]))
    children: dict[int, list[int]] = {}
    for row in rows:
        children.setdefault(int(row["parent_id"]), []).append(int(row["node_id"]))

    def squared_distance(row: Mapping[str, Any]) -> float:
        return sum((float(row[key]) - float(soma[key])) ** 2 for key in ("x", "y", "z"))

    soma_id = int(soma["node_id"])
    direct = [by_id[node_id] for node_id in children.get(soma_id, []) if int(by_id[node_id]["tag"]) == 2]
    if direct:
        return dict(min(direct, key=squared_distance))

    frontier = deque(children.get(soma_id, []))
    seen = {soma_id}
    while frontier:
        level: list[dict[str, Any]] = []
        for _ in range(len(frontier)):
            node_id = int(frontier.popleft())
            if node_id in seen:
                continue
            seen.add(node_id)
            row = by_id[node_id]
            if int(row["tag"]) == 2:
                level.append(row)
            frontier.extend(children.get(node_id, []))
        if level:
            return dict(min(level, key=squared_distance))
    axon_rows = [dict(row) for row in rows if int(row["tag"]) == 2]
    return dict(min(axon_rows, key=squared_distance)) if axon_rows else dict(soma)


def _validate_legacy_seed_ais_inputs(
    swc_root: Path,
    *,
    swc_tools: Any | None = None,
) -> dict[str, Any]:
    root = swc_root.expanduser().resolve()
    details: dict[str, Any] = {}
    derived: dict[int, int] = {}
    for neuron_id in GFC2_IDS:
        path = root / f"{neuron_id}_axodendro_with_synapses.swc"
        if not path.is_file():
            raise FileNotFoundError(f"Missing locked stimulated-cell SWC: {path}")
        rows = _load_locked_swc_rows(path)
        row = _legacy_spatial_ais_row(rows)
        legacy_ais_section_nseg = 1
        resolved_segment_count = EXPECTED_LEGACY_SEED_AIS_SEGMENT_COUNTS[neuron_id]
        if swc_tools is not None:
            runtime_nodes = swc_tools.load_swc_nodes(path)
            runtime_ais = swc_tools.ais_node(runtime_nodes)
            row = {
                "node_id": int(runtime_ais.node_id),
                "tag": int(runtime_ais.tag),
                "x": float(runtime_ais.x),
                "y": float(runtime_ais.y),
                "z": float(runtime_ais.z),
            }
            layout = swc_tools.legacy_neuron_cv_layout(runtime_nodes, nseg_um=40.0)
            cv_target = layout.node_to_cv.get(int(runtime_ais.node_id))
            if cv_target is None:
                raise RuntimeError(
                    f"Legacy CV layout did not resolve seed {neuron_id} AIS node {runtime_ais.node_id}."
                )
            section_index = int(cv_target[0])
            legacy_ais_section_nseg = int(layout.section_nseg[section_index])
            resolved_segment_count = len(layout.cv_to_segment_node_ids.get(cv_target, ()))
        node_id = int(row["node_id"])
        derived[neuron_id] = node_id
        if legacy_ais_section_nseg != 1:
            raise RuntimeError(
                f"Seed {neuron_id} legacy AIS Section now has nseg={legacy_ais_section_nseg}; "
                "a one-CV override would no longer reproduce NEURON's whole-Section repaint."
            )
        if resolved_segment_count != EXPECTED_LEGACY_SEED_AIS_SEGMENT_COUNTS[neuron_id]:
            raise RuntimeError(
                f"Seed {neuron_id} legacy AIS Section segment count changed: "
                f"expected={EXPECTED_LEGACY_SEED_AIS_SEGMENT_COUNTS[neuron_id]}, "
                f"found={resolved_segment_count}."
            )
        details[str(neuron_id)] = {
            "swc_path": str(path),
            "swc_sha256": _sha256(path),
            "node_id": node_id,
            "tag": int(row["tag"]),
            "xyz_um": [float(row[key]) for key in ("x", "y", "z")],
            "legacy_ais_section_nseg": legacy_ais_section_nseg,
            "expected_resolved_arbor_segment_count": resolved_segment_count,
        }
    _normalized_seed_ais_nodes(derived)
    if any(int(item["tag"]) != 2 for item in details.values()):
        raise RuntimeError("A locked stimulated-cell AIS node is no longer axonal SWC type 2.")
    return {
        **_seed_ais_metadata(derived),
        "selection_algorithm": "legacy_spatial_ais_fallback",
        "input_nodes_validated": True,
        "swcs_by_neuron": details,
    }


def _condition_config(
    gfa: Any,
    args: argparse.Namespace,
    label: str,
    comparison_root: Path,
    filtered_edges: Path,
    *,
    gap_catalogue: Path,
    gap_catalogue_sha256: str,
    gap_bridge_metadata: Mapping[str, Any],
    seed_ais_nodes: Mapping[int | str, int],
) -> dict[str, Any]:
    enabled = label == "gap_enabled"
    config = dict(
        gfa.build_config(
            circuit="baseline_with_10002_gfcs",
            gap_enabled=enabled,
            output_root=comparison_root,
            run_id=label,
            stim_amps_nA={10000: 0.0, 10002: 0.0},
            contact_site_na_multiplier=float(args.contact_site_na_multiplier),
            freq_hz=float(args.freq_hz),
            max_pulses=int(args.max_pulses),
            stim_dur_ms=float(args.stim_dur_ms),
            dt_ms=float(args.dt_ms),
            sample_dt_ms=float(args.sample_dt_ms),
            threads=int(args.threads),
            cv_policy=str(args.cv_policy),
            cv_max_extent_um=float(args.cv_max_extent_um),
            record_all_gf_compartments=True,
            record_post_contact_sites=True,
            max_gf_compartments=None,
            gj_model="heterotypic_rectifying",
            hetero_g_closed_frac=float(args.requested_hetero_g_closed_frac),
            hetero_vhalf_mV=float(args.requested_vhalf_mV),
            hetero_vslope_mV=float(args.requested_vslope_mV),
        )
    )
    config["edges_path"] = str(filtered_edges.resolve())
    config["seeds"] = list(GFC2_IDS)
    gap = dict(config.get("gap") or {})
    gap.pop("closed_fraction", None)
    gap.update(
        {
            "mechanism": "hetero_rect_gap",
            "directionality": "pre_to_post",
            "default_g_uS": 0.001,
            "g_closed_frac": float(args.requested_hetero_g_closed_frac),
            "empirical_residual_frac": float(args.requested_empirical_residual_frac),
            "vhalf_mV": float(args.requested_vhalf_mV),
            "vslope_mV": float(args.requested_vslope_mV),
            "tau_open_ms": float(args.requested_tau_open_ms),
            "tau_close_ms": float(args.requested_tau_close_ms),
        }
    )
    config["gap"] = gap
    arbor_cfg = dict(config.get("arbor") or {})
    arbor_cfg["cv_policy"] = CV_POLICY
    arbor_cfg.pop("legacy_section_nseg_um", None)
    config["arbor"] = arbor_cfg
    config["swc_section_mode"] = "branch"
    config["swc_section_nseg_um"] = 40.0
    # Do not inherit scientific defaults implicitly in the compatibility
    # recipe.  These are the effective values recorded by the matching
    # NEURON run and are repeated here so drift is visible in the saved plan.
    config.update(
        {
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
        }
    )
    _install_legacy_seed_ais_soma_hh(config, seed_ais_nodes=seed_ais_nodes)
    pulse = dict((config.get("stim") or {}).get("pulse_train") or {})
    pulse["amps_by_gid"] = {str(neuron_id): float(args.stim_amp_nA) for neuron_id in GFC2_IDS}
    pulse["amp_nA"] = 0.0
    stim = dict(config.get("stim") or {})
    stim["pulse_train"] = pulse
    config["stim"] = stim
    record = dict(config.get("record") or {})
    record["all_compartment_voltage"] = {
        "enabled": True,
        "neuron_ids": list(COMPARTMENT_RECORDING_IDS),
        "max_compartments_per_neuron": None,
    }
    config["record"] = record
    metadata = dict(config.get("metadata") or {})
    for obsolete_key in (
        "gj_model_effective",
        "gj_model_exact",
        "hetero_static_reverse_fraction",
        "heterotypic_approximation",
        "heterotypic_calibration",
    ):
        metadata.pop(obsolete_key, None)
    metadata.update(
        {
            "app_recipe": RECIPE,
            "comparison_class": COMPARISON_CLASS,
            "equivalence_claim": False,
            "gj_model_effective": GAP_MECHANISM,
            "gj_model_equations_preserved": True,
            "finite_step_bitwise_identical": False,
            "separate_gfs": True,
            "direct_gf_chemical_rows_removed": EXPECTED_DIRECT_GF_ROWS,
            "stimulus_target_ids": list(GFC2_IDS),
            "stimulus_amp_nA": float(args.stim_amp_nA),
            "stim_amps_nA": {
                str(neuron_id): float(args.stim_amp_nA) for neuron_id in GFC2_IDS
            },
            "temperature_K": EXPECTED_TEMPERATURE_K,
            "legacy_seed_ais_biophysics": _seed_ais_metadata(seed_ais_nodes),
            "requested_hetero_g_closed_frac": float(args.requested_hetero_g_closed_frac),
            "requested_empirical_residual_frac": float(args.requested_empirical_residual_frac),
            "requested_tau_open_ms": float(args.requested_tau_open_ms),
            "requested_tau_close_ms": float(args.requested_tau_close_ms),
            "deprecated_static_reverse_fraction": {
                "value": float(args.static_reverse_fraction),
                "used_by_equation_port": False,
            },
            "represented_parameters": list(REPRESENTED_GAP_PARAMETERS),
            "unrepresented_parameters": [],
            "gap_model_comparison": _gap_model_comparison(
                args,
                gap_catalogue=gap_catalogue,
                gap_catalogue_sha256=gap_catalogue_sha256,
            ),
            "gap_bridge": dict(gap_bridge_metadata),
            "heterotypic_equation_port": (
                "App-owned Arbor HeteroRectGap junction preserving the voltage-dependent gate, "
                "empirical residual floor, endpoint orientation, and opening/closing time constants."
            ),
        }
    )
    config["metadata"] = metadata
    config["run_notes"] = (
        "Digifly App active-Ablation Arbor comparison: 11 GFC2 cells at 0.9 nA; direct GF chemical "
        "rows removed; 2.5x GF contact-site Na. Each stimulated GFC2 legacy AIS section is "
        "explicitly painted with the same soma HH values used by NEURON. App-owned "
        "HeteroRectGap equation port; production uses the validated Arbor every-segment CV "
        "policy. Equivalence remains pending the cross-backend audit."
    )
    return config


def _assert_requested_contract(args: argparse.Namespace, arbor_module: Any) -> None:
    version = str(getattr(arbor_module, "__version__", "unknown"))
    if version != "0.12.2":
        raise RuntimeError(f"The validated comparison requires Arbor 0.12.2; found {version}.")
    exact = (
        ("contact-site Na multiplier", args.contact_site_na_multiplier, 2.5),
        ("static reverse fraction", args.static_reverse_fraction, 0.20),
        ("requested g-closed fraction", args.requested_hetero_g_closed_frac, 0.0),
        ("requested vhalf", args.requested_vhalf_mV, 0.0),
        ("requested vslope", args.requested_vslope_mV, 5.0),
        ("requested empirical residual", args.requested_empirical_residual_frac, 0.20),
        ("requested tau-open", args.requested_tau_open_ms, 6.0),
        ("requested tau-close", args.requested_tau_close_ms, 2.0),
        ("frequency", args.freq_hz, 100.0),
        ("stimulus amplitude", args.stim_amp_nA, 0.9),
        ("stimulus duration", args.stim_dur_ms, 0.4),
        ("integration dt", args.dt_ms, 0.01),
        ("sampling dt", args.sample_dt_ms, 0.05),
    )
    changed = [f"{label}={actual!r} (expected {expected!r})" for label, actual, expected in exact if abs(float(actual) - expected) > 1e-12]
    if int(args.max_pulses) != 10:
        changed.append(f"max pulses={args.max_pulses!r} (expected 10)")
    if str(args.cv_policy) != CV_POLICY:
        changed.append(f"CV policy={args.cv_policy!r} (expected {CV_POLICY!r})")
    if changed:
        raise RuntimeError("The Arbor worker only accepts the locked active-notebook comparison: " + "; ".join(changed))


def _assert_gap_bridge_metadata(
    metadata: Mapping[str, Any],
    *,
    gap_catalogue: Path,
    gap_catalogue_sha256: str,
) -> None:
    expected = {
        "status": "installed",
        "arbor_version": "0.12.2",
        "catalogue_path": str(gap_catalogue.resolve()),
        "catalogue_sha256": str(gap_catalogue_sha256),
        "catalogue_prefix": "digifly_",
        "runner_module": "digifly.phase2.arbor_build.runner",
        "connection_weight": 1.0,
        "fallback_mechanism": None,
    }
    changed = {
        key: {"expected": value, "found": metadata.get(key)}
        for key, value in expected.items()
        if metadata.get(key) != value
    }
    mechanisms = set(metadata.get("mechanisms") or [])
    expected_mechanisms = {"gap", "rect_gap", "hetero_rect_gap"}
    if changed or mechanisms != expected_mechanisms:
        raise RuntimeError(
            "The app-owned Arbor gap bridge did not install with its locked contract: "
            f"metadata={changed}, mechanisms={sorted(mechanisms)}"
        )


def _validate_input_contract(
    *,
    native_validation: Mapping[str, Any],
    source_chemical: Path,
    gap_source: Path,
    gap_arbor: Path,
    contact_nodes: Path,
    manifest: Path,
    swc_tools: Any | None = None,
) -> dict[str, Any]:
    required = (source_chemical, gap_source, gap_arbor, contact_nodes, manifest)
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError("Missing Arbor comparison inputs: " + ", ".join(missing))
    if _sha256(source_chemical) != EXPECTED_CHEMICAL_SHA256:
        raise RuntimeError("The staged 2,430-row chemical table hash changed.")
    if _sha256(gap_source) != EXPECTED_GAP_SOURCE_SHA256:
        raise RuntimeError("The 959-contact source table hash changed.")
    if native_validation != {
        "swc_count": 49,
        "chemical_edge_rows": EXPECTED_CHEMICAL_ROWS,
        "gap_contact_rows": EXPECTED_GAP_ROWS,
        "contact_site_driver_ids": [10000, 10002],
    }:
        raise RuntimeError(f"Staged helper input validation changed: {native_validation}")
    gap_rows, gap_pairs = _row_pair_counts(gap_arbor)
    if (gap_rows, gap_pairs) != (EXPECTED_GAP_ROWS, 58):
        raise RuntimeError(f"Arbor gap table changed: rows={gap_rows}, pairs={gap_pairs}")
    nodes = json.loads(contact_nodes.read_text(encoding="utf-8"))
    by_driver = dict(nodes.get("nodes_by_driver") or {})
    counts = {
        str(driver): (
            sum(len(list(values)) for values in dict(by_driver.get(str(driver)) or {}).values()),
            len(
                {
                    int(node_id)
                    for values in dict(by_driver.get(str(driver)) or {}).values()
                    for node_id in list(values)
                }
            ),
        )
        for driver in (10000, 10002)
    }
    if counts != {"10000": (115, 112), "10002": (144, 138)}:
        raise RuntimeError(f"GF contact-site node counts changed: {counts}")
    seed_ais_biophysics = _validate_legacy_seed_ais_inputs(
        manifest.parent / "swcs",
        swc_tools=swc_tools,
    )
    return {
        **dict(native_validation),
        "gap_contact_pairs": gap_pairs,
        "contact_site_node_counts_requested_unique": counts,
        "chemical_source_sha256": _sha256(source_chemical),
        "gap_source_sha256": _sha256(gap_source),
        "manifest_sha256": _sha256(manifest),
        "legacy_seed_ais_biophysics": seed_ais_biophysics,
    }


def _csv_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes"}


def _read_arbor_cell_biophys(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        required = {
            "gid",
            "neuron_id",
            "backend",
            "soma_hh",
            "branch_hh",
            "cell_biophys_override_applied",
            "node_hh_override_count",
        }
        if len(fieldnames) != len(set(fieldnames)) or not required.issubset(fieldnames):
            raise RuntimeError(
                "Arbor cell_biophys.csv lacks unique effective-biophysics columns: "
                f"header={fieldnames}"
            )
        rows = [dict(row) for row in reader]
    if not rows:
        raise RuntimeError(f"Arbor cell_biophys.csv is empty: {path}")
    return rows


def _validate_arbor_seed_ais_runtime(
    *,
    run_summary: Mapping[str, Any],
    resolved_config: Mapping[str, Any],
    cell_biophys_csv: Path,
) -> dict[str, Any]:
    """Prove that the generated Arbor decor, not just the plan, hit every seed AIS."""

    _assert_core_biophysics_contract(resolved_config)
    _assert_legacy_seed_ais_contract(resolved_config)
    rows = _read_arbor_cell_biophys(cell_biophys_csv)
    by_neuron: dict[int, dict[str, str]] = {}
    gid_to_neuron: dict[int, int] = {}
    for row in rows:
        try:
            gid = int(row["gid"])
            neuron_id = int(row["neuron_id"])
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Arbor cell_biophys.csv has invalid gid/neuron_id values.") from exc
        if neuron_id in by_neuron or gid in gid_to_neuron:
            raise RuntimeError("Arbor cell_biophys.csv has duplicate gid or neuron_id rows.")
        if str(row.get("backend")) != "arbor":
            raise RuntimeError(f"Arbor cell-biophysics row {neuron_id} has the wrong backend label.")
        try:
            soma_hh = json.loads(str(row["soma_hh"]))
            branch_hh = json.loads(str(row["branch_hh"]))
        except (TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Arbor cell-biophysics row {neuron_id} has invalid HH JSON.") from exc
        _assert_hh_values(soma_hh, EXPECTED_SOMA_HH, f"Arbor neuron {neuron_id} soma HH")
        _assert_hh_values(branch_hh, EXPECTED_BRANCH_HH, f"Arbor neuron {neuron_id} branch HH")
        by_neuron[neuron_id] = row
        gid_to_neuron[gid] = neuron_id

    selection = tuple(
        int(value)
        for value in list((resolved_config.get("selection") or {}).get("neuron_ids") or [])
    )
    if len(selection) != len(set(selection)) or set(by_neuron) != set(selection):
        raise RuntimeError(
            "Arbor cell-biophysics rows do not exactly cover the resolved circuit selection."
        )

    placement = dict(run_summary.get("placement_diagnostics") or {})
    raw_by_gid = dict(placement.get("biophys_policy_by_gid") or {})
    if set(raw_by_gid) != {str(gid) for gid in gid_to_neuron}:
        raise RuntimeError("Arbor placement diagnostics do not cover every generated cell exactly once.")

    details: dict[str, Any] = {}
    compatibility_hits: dict[int, list[dict[str, Any]]] = {}
    for gid, neuron_id in gid_to_neuron.items():
        policy = dict(raw_by_gid[str(gid)] or {})
        for item in list(policy.get("node_hh_overrides") or []):
            if not isinstance(item, Mapping):
                raise RuntimeError(f"Arbor neuron {neuron_id} has invalid node-HH diagnostics.")
            diagnostic = dict(item)
            if str(diagnostic.get("name") or "").startswith(
                LEGACY_SEED_AIS_OVERRIDE_PREFIX
            ):
                compatibility_hits.setdefault(neuron_id, []).append(diagnostic)

    spillover = sorted(set(compatibility_hits) - set(GFC2_IDS))
    if spillover:
        raise RuntimeError(
            "Legacy seed-AIS soma HH was applied to nonseed neurons: "
            f"{spillover}"
        )

    for neuron_id in GFC2_IDS:
        row = by_neuron.get(neuron_id)
        if row is None:
            raise RuntimeError(f"Arbor effective-biophysics evidence omitted seed {neuron_id}.")
        try:
            override_count = int(row["node_hh_override_count"])
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"Arbor seed {neuron_id} has an invalid override count.") from exc
        if not _csv_bool(row["cell_biophys_override_applied"]) or override_count != 1:
            raise RuntimeError(
                f"Arbor seed {neuron_id} does not prove exactly one applied AIS HH override."
            )

        hits = compatibility_hits.get(neuron_id, [])
        if len(hits) != 1:
            raise RuntimeError(
                f"Arbor seed {neuron_id} has {len(hits)} runtime seed-AIS placements; expected one."
            )
        diagnostic = hits[0]
        expected_node = EXPECTED_LEGACY_SEED_AIS_NODE_IDS[neuron_id]
        expected_segments = EXPECTED_LEGACY_SEED_AIS_SEGMENT_COUNTS[neuron_id]
        resolved_segments = sorted(
            {int(value) for value in list(diagnostic.get("resolved_segment_ids") or [])}
        )
        gid = next(gid for gid, value in gid_to_neuron.items() if value == neuron_id)
        policy = dict(raw_by_gid[str(gid)] or {})
        soma_segments = {int(value) for value in list(policy.get("soma_segment_ids") or [])}
        exact = bool(
            str(diagnostic.get("name"))
            == f"{LEGACY_SEED_AIS_OVERRIDE_PREFIX}_{neuron_id}"
            and int(diagnostic.get("requested_node_count", -1)) == 1
            and int(diagnostic.get("resolved_node_count", -1)) == 1
            and tuple(int(value) for value in list(diagnostic.get("resolved_node_ids") or []))
            == (expected_node,)
            and not list(diagnostic.get("missing_node_ids") or [])
            and str(diagnostic.get("mapping_mode")) == "legacy_neuron_section_cv"
            and int(diagnostic.get("resolved_neuron_cv_target_count", -1)) == 1
            and int(diagnostic.get("unique_neuron_cv_target_count", -1)) == 1
            and int(diagnostic.get("repeated_neuron_cv_hits", -1)) == 0
            and int(diagnostic.get("resolved_arbor_segment_count", -1))
            == expected_segments
            and len(resolved_segments) == expected_segments
            and int(policy.get("node_hh_override_segment_count", -1))
            == expected_segments
            and not (set(resolved_segments) & soma_segments)
            and str(policy.get("scope")) == "all"
            and str(policy.get("active_region")) == "(all)"
        )
        if not exact:
            raise RuntimeError(
                f"Arbor seed {neuron_id} did not paint its complete, disjoint legacy AIS "
                f"Section: diagnostic={diagnostic}, soma_segments={sorted(soma_segments)}."
            )
        details[str(neuron_id)] = {
            "gid": gid,
            "node_id": expected_node,
            "legacy_ais_section_nseg": 1,
            "resolved_arbor_segment_count": expected_segments,
            "resolved_segment_ids": resolved_segments,
            "soma_segment_ids": sorted(soma_segments),
            "effective_ais_hh": dict(EXPECTED_SOMA_HH),
            "ordinary_branch_hh": dict(EXPECTED_BRANCH_HH),
            "placement_diagnostic": diagnostic,
        }

    return {
        "status": "passed",
        "policy": LEGACY_SEED_AIS_POLICY,
        "structural_parameter_parity": True,
        "stimulated_seed_count": len(GFC2_IDS),
        "nonseed_spillover_count": 0,
        "temperature_K": EXPECTED_TEMPERATURE_K,
        "parameter_sources": {
            "soma_and_branch_hh": "generated Arbor cell_biophys.csv",
            "ais_hh": "resolved config plus generated placement diagnostics",
            "hh_el": "resolved config (runner cell CSV does not expose mechanism el)",
        },
        "cell_biophys_csv": str(cell_biophys_csv.resolve()),
        "cell_biophys_sha256": _sha256(cell_biophys_csv),
        "seeds": details,
    }


def _write_arbor_seed_ais_runtime_evidence(run_dir: Path) -> tuple[Path, dict[str, Any]]:
    evidence = _validate_arbor_seed_ais_runtime(
        run_summary=json.loads((run_dir / "run_summary.json").read_text(encoding="utf-8")),
        resolved_config=json.loads((run_dir / "config.json").read_text(encoding="utf-8")),
        cell_biophys_csv=run_dir / "cell_biophys.csv",
    )
    path = run_dir / "seed_ais_biophysics_parity.json"
    _write_json(path, evidence)
    return path, evidence


def _materialize_filtered_edges(source: Path, destination: Path) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    removed = 0
    retained = 0
    pairs: set[tuple[int, int]] = set()
    with source.open(newline="", encoding="utf-8") as input_handle:
        reader = csv.DictReader(input_handle)
        fieldnames = list(reader.fieldnames or [])
        if not {"pre_id", "post_id"}.issubset(fieldnames):
            raise ValueError("Chemical edge input lacks pre_id/post_id.")
        with destination.open("w", newline="", encoding="utf-8") as output_handle:
            writer = csv.DictWriter(output_handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in reader:
                total += 1
                pair = (int(float(row["pre_id"])), int(float(row["post_id"])))
                if pair in {(10000, 10002), (10002, 10000)}:
                    removed += 1
                    continue
                writer.writerow(row)
                retained += 1
                pairs.add(pair)
    if (total, removed, retained, len(pairs)) != (
        EXPECTED_CHEMICAL_ROWS,
        EXPECTED_DIRECT_GF_ROWS,
        EXPECTED_FILTERED_ROWS,
        EXPECTED_FILTERED_PAIRS,
    ):
        raise RuntimeError(
            f"Filtered chemical contract changed: total={total}, removed={removed}, retained={retained}, pairs={len(pairs)}"
        )
    return {
        "source_chemical_rows": total,
        "direct_gf_rows_removed": removed,
        "filtered_chemical_rows": retained,
        "filtered_chemical_pairs": len(pairs),
        "filtered_chemical_sha256": _sha256(destination),
    }


def _assert_plan_contract(configs: Mapping[str, Mapping[str, Any]], filtered_edges: Path) -> None:
    for label, config in configs.items():
        _assert_core_biophysics_contract(config)
        _assert_legacy_seed_ais_contract(config)
        arbor_cfg = dict(config.get("arbor") or {})
        if arbor_cfg.get("cv_policy") != CV_POLICY:
            raise RuntimeError(f"{label} did not bind the production {CV_POLICY} CV policy.")
        if "legacy_section_nseg_um" in arbor_cfg:
            raise RuntimeError(f"{label} retained quarantined legacy CV-policy metadata.")
        if str(config.get("swc_section_mode") or "") != "branch":
            raise RuntimeError(f"{label} did not bind the staged grouped branch morphology.")
        if float(config.get("swc_section_nseg_um", -1.0)) != 40.0:
            raise RuntimeError(f"{label} changed the staged 40 um section target.")
        if "legacy_neuron_cv_bridge" in dict(config.get("metadata") or {}):
            raise RuntimeError(f"{label} retained quarantined legacy CV-bridge metadata.")
        if Path(str(config.get("edges_path"))).resolve() != filtered_edges.resolve():
            raise RuntimeError(f"{label} did not bind the app-owned filtered chemical table.")
        if tuple(int(value) for value in config.get("seeds") or []) != GFC2_IDS:
            raise RuntimeError(f"{label} stimulus seeds do not match the 11 GFC2 cells.")
        pulse = dict((config.get("stim") or {}).get("pulse_train") or {})
        amps = {int(key): float(value) for key, value in dict(pulse.get("amps_by_gid") or {}).items()}
        if tuple(amps) != GFC2_IDS or set(amps.values()) != {0.9}:
            raise RuntimeError(f"{label} stimulus amplitudes do not match the active notebook.")
        recorded = tuple(
            int(value)
            for value in dict((config.get("record") or {}).get("all_compartment_voltage") or {}).get("neuron_ids") or []
        )
        if recorded != COMPARTMENT_RECORDING_IDS:
            raise RuntimeError(f"{label} compartment recording contract changed: {recorded}")
        if bool((config.get("gap") or {}).get("enabled")) != (label == "gap_enabled"):
            raise RuntimeError(f"{label} gap condition is inconsistent.")
        gap = dict(config.get("gap") or {})
        expected_gap = {
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
        changed = {
            key: gap.get(key)
            for key, expected in expected_gap.items()
            if gap.get(key) != expected
        }
        if changed or "closed_fraction" in gap:
            raise RuntimeError(
                f"{label} app-owned HeteroRectGap equation-port contract changed: {changed}"
            )


def _gap_model_comparison(
    args: argparse.Namespace,
    *,
    gap_catalogue: Path,
    gap_catalogue_sha256: str,
) -> dict[str, Any]:
    return {
        "requested_model": "heterotypic_rectifying",
        "effective_mechanism": GAP_MECHANISM,
        "catalogue_name": GAP_CATALOGUE_NAME,
        "catalogue_path": str(gap_catalogue.resolve()),
        "catalogue_sha256": str(gap_catalogue_sha256),
        "default_g_uS": 0.001,
        "g_closed_frac": float(args.requested_hetero_g_closed_frac),
        "empirical_residual_frac": float(args.requested_empirical_residual_frac),
        "vhalf_mV": float(args.requested_vhalf_mV),
        "vslope_mV": float(args.requested_vslope_mV),
        "tau_open_ms": float(args.requested_tau_open_ms),
        "tau_close_ms": float(args.requested_tau_close_ms),
        "directionality": "pre_to_post",
        "endpoint_orientations": {"pre": 1, "post": -1},
        "represented_parameters": list(REPRESENTED_GAP_PARAMETERS),
        "unrepresented_parameters": [],
        "solver": {
            "neuron": "derivimplicit",
            "arbor": "cnexp",
            "equations_preserved": True,
            "finite_step_bitwise_identical": False,
        },
        "deprecated_static_reverse_fraction": {
            "value": float(args.static_reverse_fraction),
            "used_by_equation_port": False,
        },
        "note": (
            "The app-owned Arbor junction preserves the HeteroRectGap equations and all locked "
            "parameters. Numerical integration differs, so equivalence remains pending audit."
        ),
    }


def _summary_base(
    *,
    args: argparse.Namespace,
    input_validation: Mapping[str, Any],
    filtered_edges: Path,
    provenance_path: Path,
    auditor_path: Path,
    planned_configs: Mapping[str, str],
    gap_catalogue: Path,
    gap_catalogue_sha256: str,
    gap_bridge_metadata: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "backend": "arbor",
        "cache_free": True,
        "recipe": RECIPE,
        "comparison_class": COMPARISON_CLASS,
        "equivalence_claim": False,
        "equivalence_audit_status": "pending_neuron_reference",
        "equivalence_auditor": str(auditor_path.resolve()),
        "contact_site_na_multiplier": float(args.contact_site_na_multiplier),
        "input_validation": dict(input_validation),
        "filtered_chemical_edges": str(filtered_edges.resolve()),
        "worker_provenance": str(provenance_path.resolve()),
        "planned_configs": dict(planned_configs),
        "stimulus": {
            "target_ids": list(GFC2_IDS),
            "amplitude_nA": float(args.stim_amp_nA),
            "frequency_hz": float(args.freq_hz),
            "max_pulses": int(args.max_pulses),
            "duration_ms": float(args.stim_dur_ms),
        },
        "gap_model_comparison": _gap_model_comparison(
            args,
            gap_catalogue=gap_catalogue,
            gap_catalogue_sha256=gap_catalogue_sha256,
        ),
        "gap_bridge": dict(gap_bridge_metadata),
    }


def _provenance(
    *,
    args: argparse.Namespace,
    public_root: Path,
    comparison_root: Path,
    arbor_module: Any,
    helper_path: Path,
    auditor_path: Path,
    manifest: Path,
    source_chemical: Path,
    filtered_edges: Path,
    gap_source: Path,
    gap_arbor: Path,
    contact_nodes: Path,
    input_validation: Mapping[str, Any],
    gap_catalogue: Path,
    gap_catalogue_sha256: str,
    gap_bridge_metadata: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "created_at": _stamp(),
        "python": {"version": platform.python_version(), "executable": sys.executable},
        "arbor": {
            "version": getattr(arbor_module, "__version__", "unknown"),
            "path": getattr(arbor_module, "__file__", "unknown"),
            "config": arbor_module.config(),
        },
        "digifly_public_root": str(public_root),
        "comparison_root": str(comparison_root),
        "comparison_class": COMPARISON_CLASS,
        "equivalence_claim": False,
        "gap_catalogue": {
            "name": GAP_CATALOGUE_NAME,
            "path": str(gap_catalogue.resolve()),
            "sha256": str(gap_catalogue_sha256),
        },
        "gap_bridge": dict(gap_bridge_metadata),
        "gap_model_comparison": _gap_model_comparison(
            args,
            gap_catalogue=gap_catalogue,
            gap_catalogue_sha256=gap_catalogue_sha256,
        ),
        "arguments": vars(args),
        "input_validation": dict(input_validation),
        "input_hashes": {
            str(path.resolve()): _sha256(path)
            for path in (
                helper_path,
                auditor_path,
                manifest,
                source_chemical,
                filtered_edges,
                gap_source,
                gap_arbor,
                contact_nodes,
            )
            if path.is_file()
        },
        "environment": {
            key: os.environ.get(key)
            for key in (
                "PYTHONPATH",
                "PYTHONNOUSERSITE",
                "MPLCONFIGDIR",
                "DIGIFLY_PHASE2_ARBOR_OUTPUT_ROOT",
                "DIGIFLY_GIANT_FIBER_ARBOR_OUTPUT_ROOT",
            )
        },
    }


def _input_snapshot(root: Path) -> dict[str, tuple[int, int]]:
    return {
        str(path.relative_to(root)): (int(path.stat().st_size), int(path.stat().st_mtime_ns))
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _row_pair_counts(path: Path) -> tuple[int, int]:
    rows = 0
    pairs: set[tuple[int, int]] = set()
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            rows += 1
            pairs.add((int(float(row["pre_id"])), int(float(row["post_id"]))))
    return rows, len(pairs)


def _assert_generated_paths(value: Any, root: Path, key: str | None = None) -> None:
    if isinstance(value, Mapping):
        for child_key, child in value.items():
            _assert_generated_paths(child, root, str(child_key))
        return
    if isinstance(value, (list, tuple)):
        for child in value:
            _assert_generated_paths(child, root, key)
        return
    generated_keys = {
        "filtered_chemical_edges",
        "worker_provenance",
        "run_dir",
        "records_csv",
        "spikes_csv",
        "config_json",
        "run_summary_json",
        "phase_timings_json",
        "cell_biophys_csv",
        "seed_ais_biophysics_json",
        "response_metrics_csv",
        "spike_count_comparison_csv",
        "summary_json",
        "png",
        "pdf",
        "compartment_summary_csv",
        "working_model_png",
        "working_model_pdf",
        "working_model_trace_summary_csv",
    }
    if key in generated_keys and isinstance(value, str) and value:
        _assert_inside(Path(value), root, key)


def _assert_inside(path: Path, root: Path, label: str) -> None:
    try:
        path.expanduser().resolve().relative_to(root.expanduser().resolve())
    except ValueError as exc:
        raise RuntimeError(f"Arbor output boundary violation for {label}: {path}") from exc


def _reject_public_output(comparison_root: Path, public_root: Path) -> None:
    try:
        comparison_root.expanduser().resolve().relative_to(public_root.expanduser().resolve())
    except ValueError:
        return
    raise RuntimeError(
        "Arbor output boundary violation: the app comparison root must not be inside Digifly Public."
    )


def _write_json(path: Path, payload: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), indent=2, sort_keys=True, default=str), encoding="utf-8")
    return path.resolve()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _event(kind: str, message: str, **details: Any) -> None:
    print(json.dumps({"event": kind, "message": message, **details}, sort_keys=True), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
