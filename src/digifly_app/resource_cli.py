"""Manage versioned, machine-local external resource profiles."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .core.resource_profile import (
    ResourceProfile,
    default_profile_path,
    make_default_profile,
    migrate_profile_file,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="digifly-resources",
        description="Create and validate Digifly Workstation external resource profiles.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    initialize = subparsers.add_parser("init", help="Create a machine-local resource profile.")
    initialize.add_argument("--profile", default=str(default_profile_path()))
    initialize.add_argument(
        "--workspace",
        help="Optional legacy Digifly Public workspace; standalone data profiles do not need one.",
    )
    initialize.add_argument("--output", required=True)
    initialize.add_argument(
        "--managed-data",
        help="Writable managed library root (defaults to a data folder beside the output root).",
    )
    initialize.add_argument("--neuron-python")
    initialize.add_argument("--arbor-python")
    initialize.add_argument("--bmtk-python")
    initialize.add_argument("--vnd")
    initialize.add_argument(
        "--morphology",
        action="append",
        default=[],
        metavar="ID=PATH",
        help="Register an external morphology root without copying it; repeat as needed.",
    )
    initialize.add_argument("--replace", action="store_true")
    initialize.add_argument("--json", action="store_true")

    show = subparsers.add_parser("show", help="Print a resource profile.")
    show.add_argument("--profile", default=str(default_profile_path()))

    validate = subparsers.add_parser("validate", help="Validate resource bindings and boundaries.")
    validate.add_argument("--profile", default=str(default_profile_path()))
    validate.add_argument("--json", action="store_true")

    migrate = subparsers.add_parser(
        "migrate", help="Write a schema-v2 profile while preserving the source profile."
    )
    migrate.add_argument("--profile", default=str(default_profile_path()))
    migrate.add_argument("--destination")
    migrate.add_argument("--replace", action="store_true")
    migrate.add_argument("--json", action="store_true")
    return parser


def _morphology_bindings(values: list[str]) -> list[tuple[str, str, str]]:
    parsed = []
    for value in values:
        resource_id, separator, path = value.partition("=")
        if not separator or not resource_id or not path:
            raise ValueError(f"Morphology bindings must use ID=PATH: {value}")
        parsed.append((resource_id, path, f"External morphology · {resource_id}"))
    return parsed


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    profile_path = Path(args.profile).expanduser().resolve()
    if args.command == "init":
        profile = make_default_profile(
            workspace_root=args.workspace,
            output_root=args.output,
            managed_data_root=args.managed_data,
            neuron_runtime=args.neuron_python,
            arbor_runtime=args.arbor_python,
            bmtk_runtime=args.bmtk_python,
            vnd_viewer=args.vnd,
            morphology_sources=_morphology_bindings(args.morphology),
        )
        destination = profile.save(profile_path, replace=bool(args.replace))
        payload = {
            "profile": str(destination),
            "fingerprint": profile.fingerprint,
            "validation": profile.validate().to_dict(),
        }
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print(f"Created {destination}")
            print(f"Profile fingerprint: {profile.fingerprint}")
            print(f"Bindings valid: {'yes' if payload['validation']['ok'] else 'no'}")
        return 0 if payload["validation"]["ok"] else 1

    if args.command == "migrate":
        destination = migrate_profile_file(
            profile_path,
            args.destination,
            replace=bool(args.replace),
        )
        profile = ResourceProfile.load(destination)
        payload = {
            "source": str(profile_path),
            "profile": str(destination),
            "fingerprint": profile.fingerprint,
            "validation": profile.validate().to_dict(),
        }
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print(f"Migrated {profile_path}")
            print(f"Created {destination}")
            print(f"Bindings valid: {'yes' if payload['validation']['ok'] else 'no'}")
        return 0 if payload["validation"]["ok"] else 1

    profile = ResourceProfile.load(profile_path)
    if args.command == "show":
        print(json.dumps(profile.to_dict(), indent=2, sort_keys=True))
        return 0
    report = profile.validate()
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        for check in report.checks:
            marker = "PASS" if check.ok else "FAIL" if check.blocking else "WARN"
            print(f"[{marker}] {check.resource_id}: {check.detail} {check.path}")
        print(f"Profile valid: {'yes' if report.ok else 'no'}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
