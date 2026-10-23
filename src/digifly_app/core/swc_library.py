"""Dataset/version-specific SWC libraries shared by Mac and Windows.

Explicit reviewed replacements retain previous bytes; local imports remain
additive. No coordinate or topology conversion occurs.
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
import uuid
from urllib.parse import urlsplit

from .data_library import MANIFEST_FILENAME, ManagedResource, load_managed_resource, safe_component
from .resource_profile import AccessMode, ResourceBinding, ResourceKind, ResourceProfile
from .neuron_labels import neuron_family


LIBRARY_SCHEMA = "digifly-dataset-swc-library-v1"
FAMILIES = ("AN", "DN", "IN", "MN", "SN", "UNKNOWN")


def library_root(profile: ResourceProfile, server: str, dataset: str) -> Path:
    return dataset_library_root(profile.managed_data_root, server, dataset)


def dataset_library_root(managed_data_root: Path, server: str, dataset: str) -> Path:
    origin = str(server).rstrip("/")
    if not origin or not dataset.strip():
        raise ValueError("An exact source server and dataset/version are required")
    host = safe_component(urlsplit(origin).netloc or origin, field="source server")
    # Slugging alone merges e.g. a:b with a-b; bind the exact origin and release.
    key = hashlib.sha256(f"{origin}\n{dataset}".encode()).hexdigest()[:12]
    return Path(managed_data_root) / "swc-libraries" / host / f"{safe_component(dataset)}-{key}"


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
    explicit = ''
    # A supplied known family is stronger than a type-name prefix. UNKNOWN may
    # be filled only by the explicit motor annotation/exact TTMn rule.
    for part in path.parts[:-1]:
        if part.upper() in FAMILIES:
            explicit = part.upper()
            break
    family = neuron_family(kind, superclass=str(neuron.get('superclass') or neuron.get('superClass') or ''),
                           explicit_family=explicit)
    return family, kind


def inspect_existing_swcs(profile: ResourceProfile, server: str, dataset: str,
                          body_ids) -> dict[str, dict]:
    """Read the actual dataset tree, including files absent from its index."""
    root = library_root(profile, server, dataset)
    _plain_path(root, profile.managed_data_root)
    wanted = {str(body) for body in body_ids}
    if any(not body.isdigit() for body in wanted):
        raise ValueError("Existing-neuron checks require numeric body IDs")
    found = {}
    export = root / "export_swc"
    if not export.exists():
        return found
    from .connectomes import _infer_id
    for path in export.rglob("*"):
        if path.is_symlink():
            raise ValueError("The dataset SWC folder contains a symbolic link; review it before downloading")
        if not path.is_file() or path.suffix.casefold() != ".swc":
            continue
        body = str(_infer_id(path.relative_to(export)) or "")
        if body not in wanted:
            continue
        _plain_path(path, profile.managed_data_root)
        if body in found:
            raise ValueError(f"Body {body} has multiple files in this dataset folder. Choose the intended copy before downloading")
        found[body] = {"path": path.relative_to(root).as_posix(),
                       "sha256": _hash(path), "size_bytes": path.stat().st_size}
    return found


def _write_json_atomic(path: Path, payload: dict) -> None:
    fd, name = tempfile.mkstemp(prefix=".library-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def store_dataset_download(profile: ResourceProfile, profile_path: Path,
                           resource: ManagedResource, *, server: str, dataset: str,
                           reviewed_existing: dict[str, dict], replacement_ids=()) -> ManagedResource:
    """Commit staged SWCs to one tree; approved old bytes remain recoverable."""
    incoming = load_managed_resource(resource.manifest, verify=True)
    if incoming.integrity != "verified":
        raise ValueError("The staged download failed checksum verification")
    payload = json.loads(resource.manifest.read_text(encoding="utf-8"))
    origin = str(server).rstrip("/")
    if payload.get("dataset") != dataset or payload.get("server") != origin:
        raise ValueError("The staged download belongs to a different server or dataset")
    selected = {str(row["body_id"]): row for row in payload["files"] if "body_id" in row}
    replacements = {str(body) for body in replacement_ids}
    if not replacements.issubset(reviewed_existing) or not set(reviewed_existing).issubset(selected):
        raise ValueError("Replacement permission does not match the reviewed neurons")
    root = library_root(profile, origin, dataset)
    _plain_path(root, profile.managed_data_root)
    root.mkdir(parents=True, exist_ok=True)
    manifest = root / MANIFEST_FILENAME
    with _locked(root):
        _plain_path(manifest, profile.managed_data_root)
        actual = inspect_existing_swcs(profile, origin, dataset, selected)
        if actual != reviewed_existing:
            raise ValueError("The dataset folder changed after review. Nothing was replaced; preview and review it again")
        old_bytes = manifest.read_bytes() if manifest.exists() else None
        old = json.loads(old_bytes) if old_bytes else None
        if old:
            load_managed_resource(manifest)  # Validate the old inventory's path grammar too.
        convention = payload.get("swc_coordinate_convention")
        if old and (old.get("library_schema"), old.get("dataset"), old.get("server"),
                    old.get("swc_coordinate_convention")) != (LIBRARY_SCHEMA, dataset, origin, convention):
            raise ValueError("The library server, dataset or coordinate units differ from this download")
        inventory = {str(row["body_id"]): dict(row) for row in old["files"]} if old else {}
        for body, record in inventory.items():
            path = root / record["path"]
            _plain_path(path, profile.managed_data_root)
            if body in replacements:
                continue  # Actual reviewed bytes, not a stale index, will be backed up.
            if not path.is_file() or _hash(path) != record["sha256"]:
                raise ValueError(f"Existing body {body} changed or is missing; review that file before updating this library")
        profile_file = Path(profile_path)
        if profile_file.name == "resources-v1.json":
            profile_file = profile_file.with_name("resources-v2.json")
        current = ResourceProfile.load(profile_file) if profile_file.exists() else profile
        if current.managed_data_root != profile.managed_data_root:
            raise ValueError("The Data Library location changed; reopen the download window")
        identity = hashlib.sha256((origin + "\n" + dataset).encode()).hexdigest()[:20]
        binding_id = "swc-library-" + identity
        prior = next((item for item in current.resources if item.resource_id == binding_id), None)
        if prior and (prior.path != str(root / "export_swc") or prior.metadata.get("library_schema") != LIBRARY_SCHEMA):
            raise ValueError("The dataset library identifier is already in use")
        binding = ResourceBinding(binding_id, ResourceKind.MORPHOLOGY_SOURCE, str(root / "export_swc"),
            AccessMode.READ_ONLY, f"{dataset} · SWC library", False,
            {"library_schema": LIBRARY_SCHEMA, "dataset": dataset, "server": origin,
             "manifest": str(manifest), "managed": True,
             "connectome_key": f"neuprint:{dataset}:swc-library-{identity[:8]}"})
        updated = replace(current, resources=tuple(item for item in current.resources
                           if item.resource_id != binding_id) + (binding,))
        history = root / ".history" / uuid.uuid4().hex
        _plain_path(history, profile.managed_data_root)
        history.mkdir(parents=True)
        created, moved, actions = [], [], []
        try:
            for body, record in selected.items():
                existing = actual.get(body)
                if existing and body not in replacements:
                    if existing["sha256"] != record["sha256"]:
                        raise ValueError("A kept SWC differs from the reviewed copy")
                    inventory[body] = {**record, **existing, "body_id": body,
                                       "download_action": "kept_existing"}
                    actions.append({"body_id": body, "action": "kept_existing", **existing})
                    continue
                family, kind = _labels(Path(record["path"]), body, record)
                # A replacement refreshes the exact reviewed file, not its
                # classification or location in the user's existing tree.
                relative = (Path(existing["path"]) if existing else
                            Path("export_swc") / family / kind / body / f"{body}.swc")
                destination = root / relative
                _plain_path(destination, profile.managed_data_root)
                # Stage and verify bytes before moving any existing file.
                temporary = history / "incoming" / f"{body}.swc"
                temporary.parent.mkdir(exist_ok=True)
                shutil.copyfile(resource.root / record["path"], temporary)
                if _hash(temporary) != record["sha256"]:
                    raise ValueError("Downloaded SWC bytes changed before storage")
                backup = None
                if existing:
                    old_path = root / existing["path"]
                    if _hash(old_path) != existing["sha256"]:
                        raise ValueError("The existing SWC changed after review")
                    backup = history / "previous" / existing["path"]
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(old_path, backup)
                    moved.append((old_path, backup))
                    if _hash(backup) != existing["sha256"]:
                        raise ValueError("The existing SWC changed during backup; review it again")
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists():
                    raise ValueError("The destination appeared after review; no unapproved file was replaced")
                # Hard-link publish refuses an unexpected destination atomically.
                os.link(temporary, destination)
                created.append((destination, record["sha256"]))
                temporary.unlink()
                stored = {**record, "path": relative.as_posix(), "body_id": body,
                          "download_action": "replaced" if existing else "downloaded"}
                inventory[body] = stored
                actions.append({"body_id": body, "action": stored["download_action"],
                    "path": relative.as_posix(), "sha256": record["sha256"],
                    "backup": backup.relative_to(root).as_posix() if backup else None,
                    "previous_sha256": existing["sha256"] if existing else None})
            # Retain only small download records/connectivity, not another SWC collection.
            extra_files = []
            for record in payload["files"]:
                if "body_id" in record:
                    continue
                source = resource.root / record["path"]
                destination = history / record["path"]
                _plain_path(destination, root)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
                if _hash(destination) != record["sha256"]:
                    raise ValueError("Download details changed while saving")
                extra_files.append({**record, "path": destination.relative_to(root).as_posix()})
            receipt = history / "download.json"
            _write_json_atomic(receipt, {"schema": "digifly-dataset-download-v1", "status": "stored",
                "server": origin, "dataset": dataset, "fetched_at": payload["fetched_at"],
                "selection": payload["selection"], "neurons": payload["neurons"],
                "actions": actions, "files": extra_files,
                "source_manifest_sha256": _hash(resource.manifest),
                "acquisition_entrypoint": payload.get("acquisition_entrypoint"),
                "credential_source": payload.get("credential_source")})
            acquisitions = list(old.get("acquisitions", [])) if old else []
            acquisitions.append({"manifest": str(receipt), "sha256": _hash(receipt)})
            records = sorted(inventory.values(), key=lambda row: int(row["body_id"]))
            result = {"schema_version": 1, "library_schema": LIBRARY_SCHEMA, "provider": "swc-library",
                "resource_id": root.name, "source_version": "cumulative-v1", "dataset": dataset,
                "server": origin, "fetched_at": payload["fetched_at"], "swc_coordinate_convention": convention,
                "acquisitions": acquisitions, "file_count": len(records), "swc_count": len(records),
                "total_bytes": sum(row["size_bytes"] for row in records), "files": records}
            for family in FAMILIES:
                (root / "export_swc" / family).mkdir(parents=True, exist_ok=True)
            _write_json_atomic(manifest, result)
            checked = load_managed_resource(manifest, verify=True)
            if checked.integrity != "verified":
                raise ValueError("The stored dataset library failed its final checksum check")
            updated.save(profile_file, replace=profile_file.exists())
        except Exception as exc:
            conflicts = []
            for destination, expected_hash in reversed(created):
                if destination.is_file() and not destination.is_symlink() and _hash(destination) == expected_hash:
                    destination.unlink()
                elif destination.exists() or destination.is_symlink():
                    conflicts.append(str(destination))
            for old_path, backup in reversed(moved):
                try:
                    # Refuse a surprise external file even during recovery.
                    os.link(backup, old_path)
                    backup.unlink()
                except FileExistsError:
                    conflicts.append(str(old_path))
            if old_bytes is None:
                manifest.unlink(missing_ok=True)
            else:
                # Restore the exact old index, not a newly serialized approximation.
                fd, name = tempfile.mkstemp(prefix=".restore-", dir=root)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(old_bytes)
                os.replace(name, manifest)
            _write_json_atomic(history / "failed-download.json", {
                "schema": "digifly-dataset-download-failure-v1", "status": "not_committed",
                "recovery_files": [str(backup.relative_to(root)) for _, backup in moved if backup.exists()],
                "unexpected_files_preserved": conflicts})
            if (history / "download.json").exists():
                (history / "download.json").unlink()
            if conflicts:
                raise ValueError("A file changed during recovery. It and the previous backup were preserved; review the dataset history folder") from exc
            raise
        return replace(checked, registered_binding=binding_id)


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
