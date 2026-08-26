"""Stable paths for source, wheel, and native-bundle installations."""

from __future__ import annotations

import os
from pathlib import Path
import sys


RESOURCE_ROOT_ENV = "DIGIFLY_WORKSTATION_RESOURCE_ROOT"
SHARE_DIRECTORY = "digifly-workstation"


def _looks_like_resource_root(path: Path) -> bool:
    return (
        (path / "mechanisms").is_dir()
        and (path / "schemas").is_dir()
        and (path / "docs" / "ARCHITECTURE.md").is_file()
    )


def resource_root() -> Path:
    """Return the installed root for small, application-owned resources.

    Resolution is deterministic and never scans a home directory or dataset
    tree. An explicit environment override supports external worker processes.
    """

    override = os.environ.get(RESOURCE_ROOT_ENV, "").strip()
    if override:
        return Path(override).expanduser().resolve()

    module_path = Path(__file__).resolve()
    source_root = module_path.parents[3]
    candidates: list[Path] = []
    if "__compiled__" in globals() or getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent)
    candidates.extend(
        (
            source_root,
            Path(sys.prefix).resolve() / "share" / SHARE_DIRECTORY,
            Path(sys.base_prefix).resolve() / "share" / SHARE_DIRECTORY,
            Path(sys.executable).resolve().parent,
        )
    )
    for candidate in candidates:
        if _looks_like_resource_root(candidate):
            return candidate
    # Return the normal installation target to make missing-resource errors
    # precise and predictable instead of searching arbitrary parent folders.
    return Path(sys.prefix).resolve() / "share" / SHARE_DIRECTORY


def resource_path(*parts: str) -> Path:
    """Resolve a relative application resource without permitting traversal."""

    relative = Path(*parts)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"Application resource paths must be relative: {relative}")
    return resource_root().joinpath(relative)


def package_root() -> Path:
    """Return the installed Python package directory."""

    return Path(__file__).resolve().parents[1]


def worker_path(filename: str) -> Path:
    """Return one application-owned external worker script."""

    if Path(filename).name != filename or not filename.endswith(".py"):
        raise ValueError(f"Invalid worker filename: {filename}")
    return package_root() / "workers" / filename
