"""Command-line access to post-import SWC quality review."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .core.resource_profile import ResourceProfile, default_profile_path
from .core.swc_quality import (
    DEFAULT_RECENT_DAYS,
    heal_swc,
    record_review_decision,
    reviewed_hashes,
    scan_recent_imports,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="digifly-swc-quality",
        description="Audit recent manifest-declared SWC imports and review adaptive radius repairs.",
    )
    parser.add_argument("--profile", default=str(default_profile_path()))
    parser.add_argument("--recent-days", type=int, default=DEFAULT_RECENT_DAYS)
    subparsers = parser.add_subparsers(dest="command", required=True)
    scan = subparsers.add_parser("scan", help="Run a read-only audit.")
    scan.add_argument("--json", action="store_true")
    review = subparsers.add_parser("review", help="Interactively review flagged SWCs.")
    review.add_argument(
        "--include-reviewed",
        action="store_true",
        help="Offer imports whose current hash already has a recorded decision.",
    )
    return parser


def _summary(report: object) -> str:
    body_id = getattr(report, "body_id", "") or "unknown body"
    dataset = getattr(report, "dataset", "") or "unknown dataset"
    findings = getattr(report, "findings", ())
    errors = getattr(report, "errors", ())
    return f"{dataset} · {body_id}: {len(findings)} radius candidate(s), {len(errors)} error(s)"


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    profile = ResourceProfile.load(Path(args.profile).expanduser().resolve())
    batch = scan_recent_imports(profile, recent_days=args.recent_days)
    if args.command == "scan":
        if args.json:
            print(json.dumps(batch.to_dict(), indent=2, sort_keys=True))
        else:
            print(
                f"Audited {len(batch.reports)} recent SWC import(s); "
                f"{len(batch.review_reports)} need review."
            )
            for report in batch.reports:
                print(f"- {_summary(report)}")
            for skipped in batch.skipped:
                print(f"- skipped: {skipped}")
        return 2 if batch.review_reports else 0

    pending = [
        report
        for report in batch.review_reports
        if args.include_reviewed or report.sha256 not in reviewed_hashes(report)
    ]
    if not pending:
        print("No unreviewed recent SWC findings.")
        return 0
    for report in pending:
        print(f"\n{_summary(report)}")
        print(report.path)
        for finding in report.findings[:12]:
            print(
                f"  node {finding.node_id}: {finding.radius_um:.6g} -> "
                f"{finding.suggested_radius_um:.6g} um · {finding.reason}"
            )
        if len(report.findings) > 12:
            print(f"  ...and {len(report.findings) - 12} more")
        if report.errors:
            print("  Automatic radius healing is disabled because structural errors were found:")
            for error in report.errors:
                print(f"  - {error}")
            record_review_decision(report, action="keep")
            continue
        choice = input(
            "[c] save healed copy (recommended), [o] overwrite with backup, "
            "[k] keep, [q] quit: "
        ).strip().casefold()
        if choice == "q":
            break
        if choice == "c":
            result = heal_swc(report, mode="copy")
            record_review_decision(report, action="copy", healing_result=result)
            print(f"Saved {result.output_path}")
        elif choice == "o":
            result = heal_swc(report, mode="overwrite")
            record_review_decision(report, action="overwrite", healing_result=result)
            print(f"Updated {result.output_path}; backup: {result.backup_path}")
        else:
            record_review_decision(report, action="keep")
            print("Kept the imported SWC unchanged.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
