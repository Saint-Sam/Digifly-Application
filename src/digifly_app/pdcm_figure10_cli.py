"""Prepare, locally run, or inspect a source-locked PDCM Figure 10 candidate.

Preparation never executes a simulation.  A successful ``run-local`` result
and ``validate-raw`` structural pass do not establish agreement with the paper.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .core.pdcm_figure10 import Figure10Spec, prepare_run, run_local, validate_prepared_bundle, validate_raw_output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="Stage a new, immutable author-source and recording bundle")
    prepare.add_argument("--model-dir", type=Path, required=True, help="Authors' pinned multicompartment branch")
    prepare.add_argument("--output-root", type=Path, required=True, help="Digifly Workstation runs root")
    prepare.add_argument("--name", required=True, help="Unique run name")
    prepare.add_argument("--profile", choices=("paper", "smoke", "capacity"), default="paper")
    prepare.add_argument("--thalamic-input", action="store_true", help="Explicitly enable the author's optional TH input")
    prepare.add_argument("--seed", type=int, default=3, help="Stimulation random seed")
    verify = commands.add_parser("validate", help="Recheck a transferred bundle before any execution")
    verify.add_argument("run_dir", type=Path)
    execute = commands.add_parser("run-local", help="Compile and execute one claimed bundle on this machine")
    execute.add_argument("run_dir", type=Path)
    execute.add_argument("--netpyne-python", type=Path, required=True)
    execute.add_argument("--nrnivmodl", type=Path, required=True)
    execute.add_argument("--timeout-seconds", type=int, default=1800)
    raw = commands.add_parser("validate-raw", help="Check saved signal completeness without claiming paper agreement")
    raw.add_argument("run_dir", type=Path)
    args = parser.parse_args(argv)
    if args.command == "prepare":
        spec = Figure10Spec(args.name, args.profile, args.thalamic_input, args.seed)
        root = prepare_run(spec, model_dir=args.model_dir, output_root=args.output_root)
        print(f"Prepared Figure 10 {args.profile} bundle (not run): {root}")
    elif args.command == "validate":
        validate_prepared_bundle(args.run_dir)
        print(f"Figure 10 bundle verified (not a simulation pass): {args.run_dir}")
    elif args.command == "run-local":
        raw = run_local(
            args.run_dir, python=args.netpyne_python,
            nrnivmodl=args.nrnivmodl, timeout_seconds=args.timeout_seconds,
        )
        print(f"Raw simulation saved but scientific acceptance pending: {raw}")
    else:
        report = validate_raw_output(args.run_dir)
        print(
            "Figure 10 raw structural checks passed; paper agreement remains unverified: "
            f"{report['cortical_cells']} cortical cells, {report['spikes']} spikes"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
