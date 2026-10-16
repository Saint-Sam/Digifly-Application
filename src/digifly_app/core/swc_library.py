"""Additive, dataset/version-specific SWC libraries shared by Mac and Windows.

Acquisition bundles remain immutable provenance. Only the library manifest and
its single profile binding change; different bytes for an existing body never
silently replace a morphology. No coordinate or topology conversion occurs.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from urllib.parse import urlsplit

from .data_library import MANIFEST_FILENAME, ManagedResource, load_managed_resource, safe_component
from .resource_profile import AccessMode, ResourceBinding, ResourceKind, ResourceProfile


LIBRARY_SCHEMA = "digifly-dataset-swc-library-v1"
FAMILIES = ("AN", "DN", "IN", "MN", "SN", "UNKNOWN")


def library_root(profile: ResourceProfile, server: str, dataset: str) -> Path:
    origin = str(server).rstrip("/")
    if not origin or not dataset.strip():
        raise ValueError("An exact source server and dataset/version are required")
    host = safe_component(urlsplit(origin).netloc or origin, field="source server")
    # Slugging alone merges e.g. a:b with a-b; bind the exact origin and release.
    key = hashlib.sha256(f"{origin}\n{dataset}".encode()).hexdigest()[:12]
    return profile.managed_data_root / "swc-libraries" / host / f"{safe_component(dataset)}-{key}"


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _plain_path(path: Path, boundary: Path) -> None:
    """Reject symlinks even when they resolve back inside the managed root."""
    path.relative_to(boundary)
    current = path
    while current != boundary.parent:
        if current.is_symlink():
            raise ValueError(f"SWC library paths cannot contain symbolic links: {current}")
        current = current.parent


@contextmanager
def _locked(root: Path):
    lock = root / ".intake-lock"
    try:
        lock.mkdir()
    except FileExistsError as exc:
        raise ValueError("This dataset library is busy, or a prior intake was interrupted. "
                         "Preserve it and review the intake lock before trying again.") from exc
    try:
        yield
    finally:
        lock.rmdir()


def _component(value: str, fallback: str) -> str:
    return (re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-") or fallback)[:96]


def _labels(path: Path, body: str, neuron: dict) -> tuple[str, str]:
    kind = str(neuron.get("type") or "")
    if not kind and body in path.parts:
        position = path.parts.index(body)
        if position:
            kind = path.parts[position - 1]
    kind = _component(kind, "Unknown")
    family = next((item for item in FAMILIES[:-1] if kind.upper().startswith(item)), "UNKNOWN")
    # A supplied export's explicit family is stronger than a type-name prefix.
    for part in path.parts[:-1]:
        if part.upper() in FAMILIES:
            family = part.upper()
            break
    return family, kind


def add_bundle_to_library(
    profile: ResourceProfile,
    profile_path: Path,
    resource: ManagedResource,
    *,
    server: str,
    dataset: str,
    coordinate_convention: dict | None = None,
    retire_acquisition_binding: bool = False,
) -> ManagedResource:
    """Verify an acquisition/import and append its SWCs to one dataset library.

    Caller must explicitly identify the dataset and coordinate convention for
    local imports. Different releases/origins are never merged. Existing files
    and acquisition manifests are retained, including on conflict.
    """
    incoming = load_managed_resource(resource.manifest, verify=True)
    if incoming.integrity != "verified":
        raise ValueError("The incoming SWC bundle failed checksum verification")
    payload = json.loads(resource.manifest.read_text(encoding="utf-8"))
    if payload.get("dataset") != dataset:
        raise ValueError("The imported bundle does not declare the selected dataset/version")
    origin = str(server).rstrip("/")
    if payload.get("server") and payload["server"].rstrip("/") != origin:
        raise ValueError("The bundle's server differs from the selected library")
    convention = payload.get("swc_coordinate_convention")
    if convention is not None and coordinate_convention is not None and convention != coordinate_convention:
        raise ValueError("The imported SWC coordinate convention differs from the selected library")
    convention = convention if convention is not None else coordinate_convention
    root = library_root(profile, origin, dataset)
    _plain_path(root, profile.managed_data_root)
    root.mkdir(parents=True, exist_ok=True)
    manifest = root / MANIFEST_FILENAME
    metadata = {str(row.get("body_id", row.get("bodyId"))): row for row in payload.get("neurons", [])}
    candidates = []
    from .connectomes import _infer_id

    for raw_record in payload["files"]:
        # Earlier app downloads used bodyId metadata and `bytes` inventory
        # fields. Verification supports that original schema too; normalize
        # only the new library's inventory, never the retained source manifest.
        record = {**raw_record, "size_bytes": int(raw_record.get("size_bytes", raw_record.get("bytes", -1)))}
        relative = Path(record["path"])
        if relative.suffix.casefold() != ".swc":
            continue
        body = str(record.get("body_id") or _infer_id(relative) or "")
        if not body.isdigit():
            raise ValueError(f"Cannot determine numeric body ID for {relative.name}; use a body-ID filename")
        family, kind = _labels(relative, body, metadata.get(body, {}))
        target = Path("export_swc") / family / kind / body / f"{body}.swc"
        candidates.append((body, target, record))
    if not candidates:
        raise ValueError("The selected import contains no SWCs")
    if len({body for body, _, _ in candidates}) != len(candidates):
        raise ValueError("The import contains multiple SWCs for the same body ID; choose the intended version")

    with _locked(root):
        old = None
        if manifest.exists():
            existing = load_managed_resource(manifest, verify=True)
            if existing.integrity != "verified":
                raise ValueError("The existing SWC library failed checksum verification")
            old = json.loads(manifest.read_text(encoding="utf-8"))
            if (old.get("library_schema"), old.get("dataset"), old.get("server"), old.get("swc_coordinate_convention")) != (
                LIBRARY_SCHEMA, dataset, origin, convention
            ):
                raise ValueError("Dataset, server, or coordinate convention does not match this library")
        inventory = list(old["files"]) if old else []
        by_body = {str(row["body_id"]): row for row in inventory}
        additions = []
        for body, target, record in candidates:
            prior = by_body.get(body)
            if prior:
                if prior["sha256"] != record["sha256"]:
                    raise ValueError(f"Body {body} already has different SWC bytes. Nothing was replaced; "
                                     "keep the new acquisition separate for review.")
                continue
            destination = root / target
            _plain_path(destination, profile.managed_data_root)
            if destination.exists():
                raise ValueError(f"Unmanifested library file already exists: {destination}")
            copied = {**record, "path": target.as_posix(), "body_id": body}
            additions.append((destination, resource.root / record["path"], copied))
        if sum(item[2]["size_bytes"] for item in additions) + 1024 * 1024 > shutil.disk_usage(root).free:
            raise OSError("Insufficient free space for the dataset SWC intake")
        now = datetime.now(timezone.utc).isoformat()
        acquisitions = list(old.get("acquisitions", [])) if old else []
        provenance = {"manifest": str(resource.manifest), "sha256": _hash(resource.manifest)}
        if provenance not in acquisitions:
            acquisitions.append(provenance)
        inventory.extend(item[2] for item in additions)
        inventory.sort(key=lambda row: int(row["body_id"]))
        result = {
            "schema_version": 1, "library_schema": LIBRARY_SCHEMA,
            "provider": "swc-library", "resource_id": root.name, "source_version": "cumulative-v1",
            "dataset": dataset, "server": origin, "fetched_at": now,
            "swc_coordinate_convention": convention, "acquisitions": acquisitions,
            "file_count": len(inventory), "swc_count": len(inventory),
            "total_bytes": sum(row["size_bytes"] for row in inventory), "files": inventory,
        }
        # Read the current profile, not a stale dialog snapshot from before an
        # earlier acquisition. Refuse changed managed roots or identity clashes.
        path = Path(profile_path)
        if path.name == "resources-v1.json":
            path = path.with_name("resources-v2.json")
        current = ResourceProfile.load(path) if path.exists() else profile
        if current.managed_data_root != profile.managed_data_root:
            raise ValueError("The Data Library location changed; reopen the import dialog")
        identity_digest = hashlib.sha256((origin + "\n" + dataset).encode()).hexdigest()[:20]
        binding_id = f"swc-library-{identity_digest}"
        prior_binding = next((item for item in current.resources if item.resource_id == binding_id), None)
        if prior_binding and (prior_binding.path != str(root / "export_swc") or prior_binding.metadata.get("library_schema") != LIBRARY_SCHEMA):
            raise ValueError("The dataset library profile identifier is already in use")
        binding = ResourceBinding(binding_id, ResourceKind.MORPHOLOGY_SOURCE, str(root / "export_swc"),
                                  AccessMode.READ_ONLY, f"{dataset} · SWC library", False, {
                                      "library_schema": LIBRARY_SCHEMA, "dataset": dataset, "server": origin,
                                      "manifest": str(manifest), "managed": True,
                                      "connectome_key": f"neuprint:{dataset}:swc-library-{identity_digest[:8]}",
                                  })
        def retained(item):
            return item.resource_id != binding_id and not (
                retire_acquisition_binding and item.kind == ResourceKind.MORPHOLOGY_SOURCE
                and item.metadata.get("manifest") == str(resource.manifest)
            )
        updated = replace(current, resources=tuple(item for item in current.resources if retained(item)) + (binding,))
        created = []
        old_bytes = manifest.read_bytes() if manifest.exists() else None
        temp_manifest = None
        try:
            for destination, source, record in additions:
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open("xb") as out, source.open("rb") as inp:
                    created.append(destination)
                    shutil.copyfileobj(inp, out)
                if _hash(destination) != record["sha256"]:
                    raise ValueError("SWC bytes changed during intake")
            for family in FAMILIES:
                (root / "export_swc" / family).mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix=".library-", dir=root)
            temp_manifest = Path(name)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(result, stream, indent=2, sort_keys=True)
                stream.write("\n")
            os.replace(temp_manifest, manifest)
            updated.save(path, replace=path.exists())
        except Exception:
            for destination in created:
                destination.unlink()
            if old_bytes is not None:
                manifest.write_bytes(old_bytes)
            elif manifest.exists():
                manifest.unlink()
            raise
        finally:
            if temp_manifest and temp_manifest.exists():
                temp_manifest.unlink()
        return replace(load_managed_resource(manifest, verify=True), registered_binding=binding_id)
