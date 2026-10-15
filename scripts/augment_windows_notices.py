"""Add CPython third-party notices without changing any compiled/model bytes."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile
from digifly_app.packaging.audit import audit_artifact


SOURCES = {
    "OpenSSL-3.0.16-LICENSE.txt": ("OpenSSL", "3.0.16", "Apache-2.0", "https://raw.githubusercontent.com/openssl/openssl/openssl-3.0.16/LICENSE.txt"),
    "libffi-3.4.4-LICENSE.txt": ("libffi", "3.4.4", "MIT", "https://raw.githubusercontent.com/libffi/libffi/v3.4.4/LICENSE"),
    "zlib-1.3.1-LICENSE.txt": ("zlib", "1.3.1", "Zlib", "https://raw.githubusercontent.com/madler/zlib/v1.3.1/LICENSE"),
    "SQLite-3.49.1-LICENSE.txt": ("SQLite", "3.49.1", "public-domain", "https://raw.githubusercontent.com/sqlite/sqlite/version-3.49.1/LICENSE.md"),
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def augment(base, extra, destination, additional=None):
    if destination.exists():
        raise ValueError("New notice-complete archive must not overwrite an artifact")
    report = audit_artifact(base)
    if not report.ok:
        raise ValueError("Original Windows archive failed audit")
    with zipfile.ZipFile(base) as source:
        marker = json.loads(source.read("digifly_windows_package.json"))
        if set(source.namelist()) != {*marker["files"], "digifly_windows_package.json"} or source.testzip():
            raise ValueError("Original archive members/CRC differ")
        payload = {}
        for name, record in marker["files"].items():
            data = source.read(name)
            if len(data) != record["bytes"] or sha(data) != record["sha256"]:
                raise ValueError("Original archive checksum differs: " + name)
            payload[name] = data
    original = dict(marker["files"])
    inventory = json.loads(payload["THIRD_PARTY_COMPONENTS.json"])
    added = []
    for file, (component, version, license_id, url) in SOURCES.items():
        path = "licenses/CPython-third-party/" + file
        if path in payload:
            raise ValueError("Notices overlay would replace an existing file")
        data = (extra / file).read_bytes()
        payload[path] = data
        added.append({"component": component, "version": version, "license": license_id,
                      "notices": [{"path": path, "sha256": sha(data), "source_url": url}],
                      "version_provenance": "CPython3.12.10 Windows PCbuild/python.props upstream dependency declaration; not an independent binary ABI measurement"})
    if additional:
        manifest = json.loads((additional / "notices_manifest.json").read_text("utf-8"))
        if manifest.get("schema") != "digifly-extra-license-notices-v1":
            raise ValueError("Additional notices require a versioned source/hash inventory")
        notices = []
        for record in manifest["notices"]:
            file = record["file"]
            if Path(file).name != file:
                raise ValueError("Additional notice must be a plain file name")
            data = (additional / file).read_bytes()
            path = "licenses/Qt-PDF-6.10.3/" + file
            if sha(data) != record["sha256"] or path in payload:
                raise ValueError("Additional notice checksum/collision")
            payload[path] = data
            notices.append({"path": path, "sha256": sha(data), "source_url": record["source_url"]})
        added.append({key: manifest[key] for key in ("component", "version", "license", "scope")})
        added[-1]["notices"] = notices
    inventory["components"].extend(added)
    payload["THIRD_PARTY_COMPONENTS.json"] = (json.dumps(inventory, indent=2) + "\n").encode()
    provenance = {"schema": "digifly-windows-license-overlay-v1", "base_archive_sha256": sha(base.read_bytes()),
        "base_source_manifest_sha256": marker["source_manifest_sha256"],
        "scope": "License/copyright notice additions only. Every original file except this notice inventory and package marker is byte-identical; no recompilation, scientific source change, simulator or new preset.",
        "upstream_python_dependency_record": "https://github.com/python/cpython/blob/v3.12.10/PCbuild/python.props",
        "added": added}
    payload["windows_license_overlay.json"] = (json.dumps(provenance, indent=2) + "\n").encode()
    for name, record in original.items():
        if name != "THIRD_PARTY_COMPONENTS.json" and sha(payload[name]) != record["sha256"]:
            raise ValueError("Notice overlay changed executable/model/runtime data: " + name)
    marker["files"] = {name: {"sha256": sha(data), "bytes": len(data)} for name, data in sorted(payload.items())}
    marker["license_overlay_base_sha256"] = provenance["base_archive_sha256"]
    payload["digifly_windows_package.json"] = (json.dumps(marker, indent=2) + "\n").encode()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, data in sorted(payload.items()):
            archive.writestr(name, data)
    report = audit_artifact(destination)
    if not report.ok:
        raise ValueError(json.dumps(report.to_dict()))
    digest = sha(destination.read_bytes())
    destination.with_suffix(destination.suffix + ".sha256").write_text(digest + "  " + destination.name + "\n", "ascii")
    return {"archive": str(destination), "sha256": digest, "base_sha256": provenance["base_archive_sha256"],
            "original_payload_files_preserved": len(original) - 1, "notice_additions": len(added), "audit": report.to_dict()}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base", type=Path, required=True)
    p.add_argument("--extra", type=Path, required=True)
    p.add_argument("--destination", type=Path, required=True)
    p.add_argument("--additional", type=Path)
    a = p.parse_args()
    print(json.dumps(augment(a.base, a.extra, a.destination, a.additional), indent=2))
