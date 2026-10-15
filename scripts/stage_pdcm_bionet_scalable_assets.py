"""Materialize only reviewed v4 scientific sources in a new frozen/build tree.

This stages source bytes, never compiles or runs a simulator. primary_v3 is
untouched; unknown files and accidental overwrites are rejected.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil

from digifly_app.workers.pdcm_bionet_scalable_app_worker import (
    SCAFFOLD, SOURCE_PINS, inventory, sha, validate_sources,
)


def freeze_project(project: Path, destination: Path) -> Path:
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("New v4 frozen source tree must not already exist")
    if not SOURCE_PINS:
        raise ValueError("v4 scientific source identities have not been reviewed/frozen")
    for relative, digest in SOURCE_PINS.items():
        source = project / relative
        if not source.is_file() or source.is_symlink() or sha(source) != digest:
            raise ValueError(f"Scientific source differs: {relative}")
    destination.mkdir(parents=True, exist_ok=False)
    for relative in (*SOURCE_PINS, *SCAFFOLD):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            if relative in SOURCE_PINS:
                with (project / relative).open("rb") as source:
                    shutil.copyfileobj(source, stream)
    validate_sources(destination)
    return destination


def stage_assets(source: Path, package: Path) -> Path:
    source, package = source.resolve(), package.resolve()
    if not package.is_dir() or package.name != "digifly_app":
        raise ValueError("Target must be a staged digifly_app package directory")
    validate_sources(source)
    destination = package / "pdcm_bionet/primary_v4"
    if source == destination or source in destination.parents:
        raise ValueError("Scientific source and staged destination must be separate")
    # Nuitka may already have included non-Python assets. Only an identical
    # known member can be reused; never overwrite unrelated or different bytes.
    expected = inventory(source)
    if destination.exists():
        observed = inventory(destination)
        if any(name not in expected or expected[name] != digest for name, digest in observed.items()):
            raise ValueError("Staged v4 tree contains unknown or changed source bytes")
    for relative in (*SOURCE_PINS, *SCAFFOLD):
        target = destination / relative
        if target.is_file():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream, (source / relative).open("rb") as original:
            shutil.copyfileobj(original, stream)
    validate_sources(destination)
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--package", type=Path)
    parser.add_argument("--project", type=Path)
    parser.add_argument("--freeze", type=Path)
    args = parser.parse_args()
    if args.project and args.freeze and not args.source and not args.package:
        print(freeze_project(args.project, args.freeze))
    elif args.source and args.package and not args.project and not args.freeze:
        print(stage_assets(args.source, args.package))
    else:
        parser.error("Choose project/freeze or source/package, exclusively")
