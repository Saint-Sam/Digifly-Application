"""Stage source-bound external scientific scripts that Nuitka omits as data.

Only call this on a new build candidate, before signing and release auditing.
The qualified Python child needs the actual frozen source files, not the GUI's
compiled modules. This step does not execute or qualify a scientific run.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil

from digifly_app.workers.pdcm_bionet_app_worker import (
    SCAFFOLD, SOURCE_PINS, validate_sources,
)


def stage_assets(source: Path, package: Path) -> Path:
    source = source.resolve()
    package = package.resolve()
    if not package.is_dir() or package.name != "digifly_app":
        raise ValueError("Target must be a staged digifly_app package directory")
    validate_sources(source)
    destination = package / "pdcm_bionet" / "primary_v3"
    if source == destination or source in destination.parents:
        raise ValueError("Frozen source and staged destination must be separate")
    # Copy only declared source-bound files, not caches, scientific outputs,
    # credentials, or unrelated package contents.
    for relative in (*SOURCE_PINS, *SCAFFOLD):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, target)
    validate_sources(destination)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--package", type=Path, required=True)
    args = parser.parse_args()
    print(stage_assets(args.source, args.package))


if __name__ == "__main__":
    main()
