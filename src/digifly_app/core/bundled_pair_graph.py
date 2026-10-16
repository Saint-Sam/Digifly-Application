"""Narrow user-authorized inclusion of one pinned MANC aggregate pair table."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

PAIR_PATH = "bundled_data/manc/unique_pairs_by_id.parquet"
PAIR_NOTICE = "bundled_data/manc/DATA_NOTICE.json"
PAIR_SIZE = 12_092_298
PAIR_SHA256 = "e07b39ae68d078b3f97364d3d235472120d1e64c19403ca63a8bba540c8d3099"
NOTICE = {
    "schema": "digifly-user-supplied-manc-pairs-v1", "file": "unique_pairs_by_id.parquet",
    "bytes": PAIR_SIZE, "sha256": PAIR_SHA256, "dataset": "MANC (user supplied)",
    "release": "unverified", "contents": "aggregate directed pair weights; no SWCs or located synapses",
    "coverage": "all rows of supplied file; whole-connectome completeness and weight units unverified",
    "licensing_scope": "User-supplied scientific data, not relicensed by Digifly's MIT software license.",
}


def verify_pair_file(path: Path) -> Path:
    if path.is_symlink() or not path.is_file() or path.stat().st_size != PAIR_SIZE:
        raise ValueError("The bundled MANC Parquet is missing, unsafe, or has the wrong size")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != PAIR_SHA256:
        raise ValueError("The bundled MANC Parquet checksum does not match the user-selected file")
    return path


def stage_pair_file(source: Path, destination_root: Path) -> Path:
    """Fresh build inputs only; the entire data folder is never allowlisted."""
    verify_pair_file(source)
    target = destination_root / PAIR_PATH
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"Bundled graph already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as inp, target.open("xb") as out:
        shutil.copyfileobj(inp, out)
    verify_pair_file(target)
    (destination_root / PAIR_NOTICE).write_text(json.dumps(NOTICE, indent=2) + "\n", encoding="utf-8")
    return target


def included_pair_path() -> Path | None:
    from .paths import resource_path

    path = resource_path(PAIR_PATH)
    if not path.exists():
        return None
    verify_pair_file(path)
    notice = json.loads(path.with_name("DATA_NOTICE.json").read_text(encoding="utf-8"))
    if notice != NOTICE:
        raise ValueError("The bundled MANC data notice differs from its source pin")
    return path


def install_included_pair(profile, source: Path) -> Path:
    """On explicit app Use, preserve a stable external copy across app upgrades."""
    verify_pair_file(source)
    parent = profile.managed_data_root / "bundled-connectivity" / ("manc-" + PAIR_SHA256[:16])
    for current in (parent.parent, parent):
        if current.is_symlink():
            raise ValueError("The managed connectivity destination cannot be a symbolic link")
    parent.mkdir(parents=True, exist_ok=True)
    target = parent / "unique_pairs_by_id.parquet"
    if target.exists() or target.is_symlink():
        return verify_pair_file(target)
    if shutil.disk_usage(parent).free < PAIR_SIZE + 1024 * 1024:
        raise OSError("Insufficient free space to retain the included connectivity file")
    fd, name = tempfile.mkstemp(prefix=".pair-", dir=parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as out, source.open("rb") as inp:
            shutil.copyfileobj(inp, out)
        verify_pair_file(temporary)
        # Exclusive destination creation also handles a second app instance.
        with temporary.open("rb") as inp, target.open("xb") as out:
            shutil.copyfileobj(inp, out)
        return verify_pair_file(target)
    finally:
        temporary.unlink()
