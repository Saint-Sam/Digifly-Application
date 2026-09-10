from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from typing import Sequence

from digifly_app.core.resources import capture_resources
from digifly_app.core.workspace import DigiflyWorkspace
from digifly_app.core.resource_profile import ResourceKind, ResourceProfile


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="digifly-doctor", description="Inspect a Digifly Workstation workspace.")
    subparsers = parser.add_subparsers(dest="command")
    doctor = subparsers.add_parser("doctor", help="Run workspace and runtime checks.")
    doctor_source = doctor.add_mutually_exclusive_group(required=True)
    doctor_source.add_argument("--workspace")
    doctor_source.add_argument("--profile")
    doctor.add_argument("--python")
    doctor.add_argument("--output")
    doctor.add_argument("--json", action="store_true")

    plan = subparsers.add_parser("plan", help="Print the first Escape-SIZ execution plan.")
    plan_source = plan.add_mutually_exclusive_group(required=True)
    plan_source.add_argument("--workspace")
    plan_source.add_argument("--profile")
    plan.add_argument("--python")
    plan.add_argument("--output")
    preset = plan.add_mutually_exclusive_group()
    preset.add_argument("--ablation-notebook", action="store_true")
    preset.add_argument("--canonical-dual-gf", action="store_true")
    plan.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command is None:
        _parser().print_help()
        return 2
    profile = ResourceProfile.load(args.profile) if args.profile else None
    profile_report = profile.validate() if profile is not None else None
    if args.command == "plan" and profile_report is not None and not profile_report.ok:
        for check in profile_report.checks:
            if not check.ok and check.blocking:
                print(f"Invalid resource profile [{check.resource_id}]: {check.detail}", file=sys.stderr)
        return 2
    workspace_root = profile.workspace_root if profile is not None else Path(args.workspace)
    if workspace_root is None:
        if args.command == "plan":
            print(
                "This legacy execution plan requires a Digifly Public workspace binding; "
                "standalone data-library profiles can still be used by the Workstation app.",
                file=sys.stderr,
            )
            return 2
        workspace_root = profile.managed_data_root.parent
    output_root = (
        Path(args.output).expanduser()
        if args.output
        else profile.output_root
        if profile is not None
        else Path.home() / "Digifly Workstation Workspace" / "runs"
    )
    configured_python = (
        profile.runtime_path(ResourceKind.NEURON_RUNTIME) if profile is not None else None
    )
    python_executable = str(Path(args.python).expanduser()) if args.python else str(
        configured_python or Path("/opt/anaconda3/bin/python")
    )
    workspace = DigiflyWorkspace(workspace_root, profile=profile)
    if args.command == "plan":
        from digifly_app.engines.neuron_escape_siz import (
            EscapeSizConfig,
            NeuronEscapeSizAdapter,
        )

        if getattr(args, "canonical_dual_gf", False):
            config = EscapeSizConfig.canonical_dual_gf()
        else:
            config = EscapeSizConfig.ablation_notebook_active()
        config.python_executable = python_executable
        adapter = NeuronEscapeSizAdapter(workspace)
        resolved = adapter.plan(config, output_root=output_root)
        if args.json:
            print(json.dumps(resolved.to_dict(), indent=2))
        else:
            print(resolved.display_command)
            print(f"working directory: {resolved.working_directory}")
            print(f"output behavior: {resolved.output_behavior}")
        return 0

    workspace_report = workspace.base_preflight()
    probes = workspace.probe_engines(python_executable)
    resources = capture_resources(output_root)
    qt = _qt_probe()
    required_runtime_keys: set[str] = set()
    if profile is not None:
        runtime_kinds = {
            ResourceKind.NEURON_RUNTIME: "neuron",
            ResourceKind.ARBOR_RUNTIME: "arbor",
            ResourceKind.BMTK_RUNTIME: "bmtk",
        }
        required_runtime_keys = {
            runtime_kinds[binding.kind]
            for binding in profile.resources
            if binding.required and binding.kind in runtime_kinds
        }
    configured_runtime_validation = {
        "ok": all(
            next(
                (
                    probe.runtime_state.value
                    for probe in probes
                    if probe.key == required_key
                ),
                "missing",
            )
            == "pass"
            for required_key in required_runtime_keys
        ),
        "required": sorted(required_runtime_keys),
    }
    payload = {
        "ok": (
            workspace_report.ok
            and qt["ok"]
            and configured_runtime_validation["ok"]
            and (profile_report is None or profile_report.ok)
        ),
        "workspace": str(workspace.root),
        "workspace_validation": workspace_report.to_dict(),
        "resource_profile": str(Path(args.profile).expanduser().resolve()) if args.profile else None,
        "resource_profile_fingerprint": profile.fingerprint if profile is not None else None,
        "resource_profile_validation": profile_report.to_dict() if profile_report is not None else None,
        "qt": qt,
        "resources": resources.__dict__,
        "engines": [
            {
                "key": probe.key,
                "name": probe.name,
                "source_state": probe.source_state.value,
                "runtime_state": probe.runtime_state.value,
                "summary": probe.summary,
                "details": list(probe.details),
            }
            for probe in probes
        ],
        "configured_runtime_validation": configured_runtime_validation,
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Digifly workspace: {workspace.root}")
        if profile_report is not None:
            print("External resource profile:")
            for check in profile_report.checks:
                marker = "PASS" if check.ok else "FAIL" if check.blocking else "WARN"
                print(f"  [{marker}] {check.resource_id}: {check.detail}")
        print(f"Qt UI runtime: {'PASS' if qt['ok'] else 'FAIL'} — {qt['detail']}")
        for probe in probes:
            print(
                f"{probe.name}: source={probe.source_state.value}, "
                f"runtime={probe.runtime_state.value} — {probe.summary}"
            )
        print("Workspace validation:")
        for check in workspace_report.checks:
            marker = check.state.value.upper()
            print(f"  [{marker}] {check.title}: {check.detail}")
        if required_runtime_keys:
            print(
                "Configured runtimes: "
                f"{'PASS' if configured_runtime_validation['ok'] else 'FAIL'} — "
                f"{', '.join(sorted(required_runtime_keys))}"
            )
        print(f"Workstation healthy: {'yes' if payload['ok'] else 'no'}")
    return 0 if payload["ok"] else 1


def doctor_main() -> int:
    argv = sys.argv[1:]
    if not argv or argv[0] not in {"doctor", "plan"}:
        argv = ["doctor", *argv]
    return main(argv)


def _qt_probe() -> dict[str, object]:
    try:
        completed = subprocess.run(
            [sys.executable, "-c", "from PySide6.QtCore import qVersion; print(qVersion())"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "detail": str(exc), "python": sys.executable}
    detail = (completed.stdout if completed.returncode == 0 else completed.stderr or completed.stdout).strip()
    return {"ok": completed.returncode == 0, "detail": detail, "python": sys.executable}


if __name__ == "__main__":
    raise SystemExit(main())
