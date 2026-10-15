"""Seal and independently recheck a data-free Windows UI artifact, not science."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import struct
import zipfile

from digifly_app.packaging.audit import audit_artifact, BINARY_SUFFIXES

MARKER = "digifly_windows_package.json"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def pe_x64(data):
    if data[:2] != b"MZ" or len(data) < 64:
        raise ValueError("Application is not a Windows PE executable")
    offset = struct.unpack_from("<I", data, 60)[0]
    if data[offset:offset + 4] != b"PE\0\0" or data[offset + 4:offset + 6] != b"\x64\x86":
        raise ValueError("Application is not Windows x86-64")


def inventory(bundle):
    result = {}
    for path in sorted(bundle.rglob("*")):
        if path.is_symlink():
            raise ValueError("Windows packages do not permit symlinks")
        if path.is_file() and path.name != MARKER:
            data = path.read_bytes()
            result[path.relative_to(bundle).as_posix()] = {"sha256": digest(data), "bytes": len(data)}
    return result


def check(bundle):
    marker = json.loads((bundle / MARKER).read_text("utf-8"))
    if marker["schema"] != "digifly-windows-package-v1" or marker["files"] != inventory(bundle):
        raise ValueError("Windows payload differs from its sealed inventory")
    pe_x64((bundle / "Digifly Workstation.exe").read_bytes())
    report = audit_artifact(bundle)
    if not report.ok:
        raise ValueError(json.dumps(report.to_dict(), indent=2))
    return report.to_dict()


def seal(bundle, source, version):
    if (bundle / MARKER).exists():
        raise ValueError("Refusing to overwrite a package inventory")
    source_data = (source / "windows_source_manifest.json").read_bytes()
    original = json.loads(source_data)
    for name, record in original["files"].items():
        data = (source / name).read_bytes()
        # Only the generated Windows icon is permitted to change during build.
        if name != "src/digifly_app/assets/digifly_icon.ico" and digest(data) != record["sha256"]:
            raise ValueError(f"Build input changed: {name}")
    (bundle / "windows_source_manifest.json").write_bytes(source_data)
    marker = {"schema": "digifly-windows-package-v1", "version": version,
              "files": inventory(bundle), "simulator_acceptance": False,
              "source_manifest_sha256": digest(source_data), "arbor": "deferred"}
    (bundle / MARKER).write_text(json.dumps(marker, indent=2) + "\n", "utf-8")
    return check(bundle)


def archive(bundle, target):
    check(bundle)
    with zipfile.ZipFile(target, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for path in sorted(bundle.rglob("*")):
            if path.is_file():
                z.write(path, path.relative_to(bundle).as_posix())
    with zipfile.ZipFile(target) as z:
        if z.testzip():
            raise ValueError("Windows ZIP CRC check failed")
        marker = json.loads(z.read(MARKER))
        names = set(z.namelist())
        if len(names) != len(z.namelist()) or names != {*marker["files"], MARKER}:
            raise ValueError("Windows archive membership mismatch")
        for name, record in marker["files"].items():
            data = z.read(name)
            if len(data) != record["bytes"] or digest(data) != record["sha256"]:
                raise ValueError(f"ZIP payload checksum differs: {name}")
    report = audit_artifact(target)
    if not report.ok:
        raise ValueError(json.dumps(report.to_dict(), indent=2))
    sha = digest(target.read_bytes())
    target.with_suffix(target.suffix + ".sha256").write_text(f"{sha}  {target.name}\n", "ascii")
    return {**report.to_dict(), "sha256": sha}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("mode", choices=("seal", "check", "archive"))
    p.add_argument("--bundle", type=Path, required=True)
    p.add_argument("--source", type=Path)
    p.add_argument("--version")
    p.add_argument("--archive", type=Path)
    a = p.parse_args()
    result = seal(a.bundle, a.source, a.version) if a.mode == "seal" else archive(a.bundle, a.archive) if a.mode == "archive" else check(a.bundle)
    print(json.dumps(result, indent=2))
