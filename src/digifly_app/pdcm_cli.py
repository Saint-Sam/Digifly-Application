"""Reproduce a pinned PDCM NetPyNE condition in a Digifly-owned run folder."""

from __future__ import annotations

import argparse
from pathlib import Path

from digifly_app.core.pdcm_replication import PDCMReplicationSpec, prepare_run, run_prepared


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True, help="Unchanged authors' PDCM_NetPyNE source folder")
    parser.add_argument("--netpyne-python", type=Path, help="Python containing NetPyNE and NEURON (required unless preparing only)")
    parser.add_argument("--output-root", type=Path, required=True, help="Digifly Workstation runs root")
    parser.add_argument("--name", required=True, help="Unique run name")
    parser.add_argument("--scale", type=float, default=0.01)
    parser.add_argument("--duration-ms", type=float, default=1000.0)
    parser.add_argument("--drive", choices=("balanced_poisson", "balanced_dc", "unbalanced_poisson"), default="balanced_poisson")
    parser.add_argument("--seed", type=int, default=3)
    parser.add_argument("--network-seed", type=int, help="Optional independent network realization seed")
    parser.add_argument("--export-convergence", action="store_true", help="Prepare Figure 9C convergence aggregates for the MPI worker")
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    if args.export_convergence and not args.prepare_only:
        parser.error("--export-convergence requires --prepare-only and the MPI PDCM worker")
    if not args.prepare_only and args.netpyne_python is None:
        parser.error("--netpyne-python is required unless --prepare-only is set")
    spec = PDCMReplicationSpec(
        args.name, args.scale, args.duration_ms, args.drive, args.seed, args.export_convergence,
        network_seed=args.network_seed,
    )
    run = prepare_run(spec, model_dir=args.model_dir, output_root=args.output_root)
    print(f"Prepared: {run}", flush=True)
    if not args.prepare_only:
        print(f"Completed: {run_prepared(run, python=args.netpyne_python)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
