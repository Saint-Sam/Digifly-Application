"""Lossless storage for new generic experiment results; legacy files stay readable.

Standard library only. This sibling also runs in external simulator Python.
Never compacts an accepted result or a source dataset.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

MAX_PLAYBACK_VOLTAGE_SAMPLES = 5_000_000


def result_path(path: Path) -> Path:
    path = Path(path)
    if path.suffix == ".gz":
        return path
    compressed = path.with_name(path.name + ".gz")
    if path.is_file() and compressed.is_file():
        raise ValueError(f"Ambiguous saved result: both {path.name} and {compressed.name}")
    return compressed if compressed.is_file() else path


def open_result_text(path: Path):
    actual = result_path(path)
    return (gzip.open(actual, "rt", encoding="utf-8", newline="")
            if actual.suffix == ".gz" else actual.open(encoding="utf-8", newline=""))


def read_result_json(path: Path):
    with open_result_text(path) as stream:
        return json.load(stream)


def result_maps(root: Path):
    names = {p.with_suffix("") if p.suffix == ".gz" else p
             for pattern in ("regional_map.json", "regional_map.json.gz")
             for p in Path(root).rglob(pattern)}
    return tuple(result_path(p) for p in sorted(names))


def _digest(stream):
    digest = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(block)
    return digest.hexdigest()


def compress_file(path: Path, *, minimum_bytes=512 * 1024, check_cancel=None):
    """Verify exact decompressed bytes before retiring this new run's plain copy."""
    if path.is_symlink() or not path.is_file():
        raise ValueError("Storage compression requires an ordinary run-owned file")
    size = path.stat().st_size
    if size < minimum_bytes:
        return None
    if shutil.disk_usage(path.parent).free < size + 2 * 1024**3:
        return None
    target = path.with_name(path.name + ".gz")
    if target.exists() or target.is_symlink():
        raise ValueError("Refusing to replace an existing compressed result")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".compact-", delete=False) as raw:
            temporary = Path(raw.name)
            with path.open("rb") as source, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as output:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    if check_cancel:
                        check_cancel()
                    output.write(block)
        with path.open("rb") as source:
            original = _digest(source)
        with gzip.open(temporary, "rb") as restored:
            if _digest(restored) != original:
                raise ValueError("Compressed result failed exact byte verification")
        if temporary.stat().st_size >= size:
            return None
        if check_cancel:
            check_cancel()
        with path.open("rb") as source:
            if path.stat().st_size != size or _digest(source) != original:
                raise ValueError("Result changed during compression; original retained")
        # Same-directory exclusive publication; never replace a racing file.
        try:
            os.link(temporary, target)
        except FileExistsError:
            raise ValueError("Refusing to replace a racing compressed result")
        except OSError:
            # Some external filesystems cannot publish a hard link. Keep the
            # valid plain file; storage optimization must not invalidate a run.
            return None
        path.unlink()
        return {"original_path": path.name, "path": target.name, "original_bytes": size,
                "stored_bytes": target.stat().st_size, "decoded_sha256": original,
                "exact_decoded_bytes_verified": True}
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def compact_outputs(root: Path, artifacts: list, *, check_cancel=None):
    root = Path(root).resolve()
    manifest = read_result_json(root / "run_manifest.json")
    if manifest.get("state") != "running" or (root / "summary.json").exists():
        raise ValueError("Only a new unpublished running result may be compacted")
    paths = [root / name for name in ("voltage_traces.csv", "spikes.csv", "condition_execution.json")]
    paths += list((root / "morphologies").glob("*/regional_map.json"))
    # Preserve dense native observations and pinned selection receipts too;
    # their original logical names/hashes identify the exact decoded bytes.
    paths += [p for p in (root / "arbor_native_reports").glob("*")
              if p.suffix in {".csv", ".json"}]
    records = []
    for path in paths:
        if not path.exists():
            continue
        if path.resolve() != path or not path.is_relative_to(root):
            raise ValueError("Result storage path leaves the run folder")
        record = compress_file(path, check_cancel=check_cancel)
        if record:
            old = path.relative_to(root).as_posix()
            new = old + ".gz"
            record.update(original_path=old, path=new)
            for artifact in artifacts:
                if Path(artifact["path"]).as_posix() == old:
                    artifact["path"] = new
            records.append(record)
    receipt = {"schema": "digifly-lossless-result-storage-v1", "files": records,
               "original_bytes": sum(r["original_bytes"] for r in records),
               "stored_bytes": sum(r["stored_bytes"] for r in records),
               "policy": "Lossless gzip; exact decoded bytes verified. No samples, source geometry or precision removed.",
               "logical_path_rule": "Original names in native receipts resolve through this file map; their hashes identify the exact decoded original bytes."}
    with (root / "result_storage.json").open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, indent=2)
    artifacts.append({"kind": "document", "path": "result_storage.json", "label": "Recording storage details"})
    return receipt


def compress_native_voltage(path: Path, h5py, *, check_cancel=None):
    """Retain the entire BioNet report, compressing its unchanged numeric data."""
    if not path.is_file() or path.is_symlink() or path.stat().st_size < 512 * 1024:
        return None
    size = path.stat().st_size
    if shutil.disk_usage(path.parent).free < size + 2 * 1024**3:
        return None  # Keep valid uncompressed output; never consume the reserve.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".compact-h5-", delete=False) as handle:
            temporary = Path(handle.name)
        with h5py.File(path, "r") as source, h5py.File(temporary, "w") as target:
            for key, value in source.attrs.items():
                target.attrs[key] = value
            def copy_group(group, destination):
                for key, old in group.items():
                    if isinstance(old, h5py.Group):
                        output = destination.create_group(key)
                        for attr, value in old.attrs.items():
                            output.attrs[attr] = value
                        copy_group(old, output)
                    elif (old.name.startswith("/report/") and key == "data"
                          and old.ndim == 2 and old.size and old.dtype.kind in "fiu"):
                        output = destination.create_dataset(key, shape=old.shape, dtype=old.dtype,
                            chunks=(min(64, old.shape[0]), min(1024, old.shape[1])),
                            compression="gzip", compression_opts=4, shuffle=True)
                        for attr, value in old.attrs.items():
                            output.attrs[attr] = value
                        for start in range(0, old.shape[0], 64):
                            if check_cancel:
                                check_cancel()
                            block = old[start:start + 64]
                            output[start:start + 64] = block
                            if output[start:start + 64].tobytes() != block.tobytes():
                                raise ValueError("Native voltage compression changed recorded values")
                    else:
                        group.copy(key, destination)
            copy_group(source, target)
        if temporary.stat().st_size >= size:
            return None
        if check_cancel:
            check_cancel()
        os.replace(temporary, path)
        return {"original_bytes": size, "stored_bytes": path.stat().st_size,
                "all_native_values_preserved": True}
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
