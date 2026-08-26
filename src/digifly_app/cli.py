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
from digifly_app.engines.neuron_escape_siz import EscapeSizConfig, NeuronEscapeSizAdapter


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="digifly-doctor", description="Inspect a Digifly Workstation workspace.")
    subparsers = parser.add_subparsers(dest="command")
    doctor = subparsers.add_parser("doctor", help="Run workspace and runtime checks.")
    doctor_source = doctor.add_mutually_exclusive_group(required=True)
    doctor_source.add_argument("--workspace")
    doctor_source.add_argument("--profile")
    doctor.add_argument("--python")
    doctor.add_argument("--output")
    doctor.add_argument("--allow-cache-build", action="store_true")
    doctor.add_argument("--acknowledge-legacy-writes", action="store_true")
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
    if getattr(args, "ablation_notebook", False):
        config = EscapeSizConfig.ablation_notebook_active()
    elif getattr(args, "canonical_dual_gf", False):
        config = EscapeSizConfig.canonical_dual_gf()
    else:
        config = EscapeSizConfig.ablation_notebook_active()
    config.python_executable = python_executable
    adapter = NeuronEscapeSizAdapter(workspace)
    if args.command == "plan":
        resolved = adapter.plan(config, output_root=output_root)
        if args.json:
            print(json.dumps(resolved.to_dict(), indent=2))
        else:
            print(resolved.display_command)
            print(f"working directory: {resolved.working_directory}")
            print(f"output behavior: {resolved.output_behavior}")
        return 0

    report = adapter.validate(
        config,
        output_root=output_root,
        allow_new_cache_build=bool(args.allow_cache_build),
        legacy_write_acknowledged=bool(args.acknowledge_legacy_writes),
    )
    probes = workspace.probe_engines(python_executable)
    resources = capture_resources(output_root)
    qt = _qt_probe()
    payload = {
        "ok": report.ok and (profile_report is None or profile_report.ok),
        "workspace": str(workspace.root),
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
        "escape_siz": report.to_dict(),
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
        print("Escape-SIZ preflight:")
        for check in report.checks:
            marker = check.state.value.upper()
            print(f"  [{marker}] {check.title}: {check.detail}")
        print(f"Launch-ready: {'yes' if report.ok else 'no'}")
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
