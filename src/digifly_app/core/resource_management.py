"""Transactional lifecycle controls for Workstation-managed data bundles."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import threading
from typing import Any, Mapping
from uuid import uuid4

from .data_library import (
    MANIFEST_FILENAME,
    ManagedResource,
    load_managed_resource,
    safe_component,
)
from .resource_profile import (
    AccessMode,
    ResourceBinding,
    ResourceKind,
    ResourceProfile,
)


TRASH_RECEIPT = "digifly-trash.json"
RESTORE_RECEIPT = "digifly-restore.json"
STORED_ONLY_STATE = "stored_only"


@dataclass(frozen=True)
class RelinkPreview:
    current_root: Path
    new_root: Path
    managed_binding_count: int
    resource_count: int


@dataclass(frozen=True)
class LibraryMovePreview:
    source_root: Path
    target_root: Path
    same_filesystem: bool


@dataclass(frozen=True)
class LibraryMoveProgress:
    stage: str
    completed_files: int
    total_files: int
    completed_bytes: int
    total_bytes: int
    current: str = ""


@dataclass(frozen=True)
class LibraryMoveResult:
    source_root: Path
    target_root: Path
    mode: str
    source_removed: bool
    file_count: int
    total_bytes: int


class LibraryMoveCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class TrashEntry:
    trash_root: Path
    original_root: Path
    manifest_relative: Path
    provider: str
    resource_id: str
    source_version: str
    trashed_at: str
    bindings: tuple[ResourceBinding, ...]

    @property
    def manifest(self) -> Path:
        return self.trash_root / self.manifest_relative


def _profile_destination(profile_path: str | Path) -> Path:
    path = Path(profile_path).expanduser().resolve()
    return path.with_name("resources-v2.json") if path.name == "resources-v1.json" else path


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _binding_id(provider: str, resource_id: str, version: str, suffix: str = "") -> str:
    value = "managed-{}-{}-{}{}".format(
        safe_component(provider, field="provider"),
        safe_component(resource_id, field="resource identifier"),
        safe_component(version, field="source version"),
        suffix,
    )
    if len(value) <= 96:
        return value
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"{value[:83].rstrip('-._')}-{digest}"


def _manifest_payload(resource: ManagedResource) -> dict[str, Any]:
    try:
        payload = json.loads(resource.manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read managed resource manifest: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("files"), list):
        raise ValueError("Managed resource manifest must be an object with a file inventory")
    return payload


def _content_root(resource: ManagedResource, *, morphology: bool) -> Path:
    """Resolve a provider-neutral content root inside an existing bundle."""

    candidates = []
    if morphology:
        # Current neuPrint bundles use source/export_swc; legacy Digifly Public
        # acquisitions place export_swc directly beside manifest.json.
        candidates.extend(
            (resource.root / "source" / "export_swc", resource.root / "export_swc")
        )
    candidates.extend((resource.root / "source", resource.root))
    bundle = resource.root.resolve()
    for candidate in candidates:
        if candidate.is_symlink() or not candidate.is_dir():
            continue
        resolved = candidate.resolve()
        try:
            resolved.relative_to(bundle)
        except ValueError:
            continue
        return resolved
    noun = "morphology" if morphology else "content"
    raise ValueError(f"Managed {noun} root is missing inside bundle: {resource.root}")


def _contained_resource(profile: ResourceProfile, resource: ManagedResource) -> None:
    root = profile.managed_data_root.resolve()
    bundle = resource.root.resolve()
    manifest = resource.manifest.resolve()
    try:
        relative = bundle.relative_to(root)
        manifest.relative_to(bundle)
    except ValueError as exc:
        raise ValueError("The selected bundle is outside the managed Data Library") from exc
    if not relative.parts or relative.parts[0] in {".staging", ".trash"}:
        raise ValueError("Staging and Trash roots are not active managed resources")
    if not manifest.is_file():
        raise ValueError(f"Managed resource manifest is missing: {manifest}")


def _binding_belongs_to_library(
    profile: ResourceProfile,
    binding: ResourceBinding,
) -> bool:
    """Recognize current and legacy bindings whose manifests live in the library."""

    manifest_value = binding.metadata.get("manifest")
    if not manifest_value:
        return False
    try:
        Path(str(manifest_value)).expanduser().resolve().relative_to(
            profile.managed_data_root.resolve()
        )
    except ValueError:
        return False
    return True


def resource_bindings(
    profile: ResourceProfile,
    resource: ManagedResource,
) -> tuple[ResourceBinding, ...]:
    """Return active provider bindings for a managed resource."""

    return tuple(
        binding
        for binding in _matching_bindings(profile, resource)
        if binding.metadata.get("lifecycle_state") != STORED_ONLY_STATE
    )


def _matching_bindings(
    profile: ResourceProfile,
    resource: ManagedResource,
) -> tuple[ResourceBinding, ...]:
    manifest = resource.manifest.resolve()
    matches = []
    for binding in profile.resources:
        value = binding.metadata.get("manifest")
        if not value or not _binding_belongs_to_library(profile, binding):
            continue
        if Path(str(value)).expanduser().resolve() == manifest:
            matches.append(binding)
    return tuple(matches)


def _profile_without_resource(
    profile: ResourceProfile,
    resource: ManagedResource,
) -> tuple[ResourceProfile, tuple[ResourceBinding, ...]]:
    removed = _matching_bindings(profile, resource)
    removed_ids = {binding.resource_id for binding in removed}
    updated = ResourceProfile(
        profile.profile_id,
        tuple(binding for binding in profile.resources if binding.resource_id not in removed_ids),
        profile.label,
    )
    return updated, removed


def unregister_managed_resource(
    profile: ResourceProfile,
    resource: ManagedResource,
    *,
    profile_path: str | Path,
) -> tuple[str, ...]:
    """Remove only profile bindings; keep every managed byte in place."""

    _contained_resource(profile, resource)
    updated, removed = _profile_without_resource(profile, resource)
    if not removed:
        return ()
    stored_id = _binding_id(
        resource.provider,
        resource.resource_id,
        resource.source_version,
        "-stored",
    )
    if any(binding.resource_id == stored_id for binding in updated.resources):
        raise FileExistsError(f"Profile binding already exists: {stored_id}")
    first = removed[0]
    stored = ResourceBinding(
        stored_id,
        ResourceKind.DATA_SOURCE,
        str(resource.root),
        AccessMode.READ_ONLY,
        first.label or resource.resource_id,
        False,
        {
            "provider": resource.provider,
            "dataset": first.metadata.get("dataset", resource.resource_id),
            "source_version": resource.source_version,
            "manifest": str(resource.manifest),
            "managed": True,
            "lifecycle_state": STORED_ONLY_STATE,
            "display_resource_id": resource.resource_id,
            "previous_bindings": [binding.to_dict() for binding in removed],
        },
    )
    updated = ResourceProfile(
        updated.profile_id,
        (*updated.resources, stored),
        updated.label,
    )
    destination = _profile_destination(profile_path)
    updated.save(destination, replace=destination.exists())
    return tuple(binding.resource_id for binding in removed)


def _proposed_bindings(
    resource: ManagedResource,
) -> tuple[ResourceBinding, ...]:
    payload = _manifest_payload(resource)
    provider = resource.provider
    dataset = str(payload.get("dataset") or resource.resource_id)
    label = str(payload.get("name") or payload.get("label") or resource.resource_id)
    metadata = {
        "provider": provider,
        "dataset": dataset,
        "source_version": resource.source_version,
        "manifest": str(resource.manifest),
        "managed": True,
    }
    is_model = bool(payload.get("inert_code")) or provider in {"modeldb", "local-model"}
    proposed: list[ResourceBinding] = []
    if is_model:
        source_root = _content_root(resource, morphology=False)
        proposed.append(
            ResourceBinding(
                _binding_id(provider, resource.resource_id, resource.source_version),
                ResourceKind.MODEL_SOURCE,
                str(source_root),
                AccessMode.READ_ONLY,
                label,
                False,
                {**metadata, "inert_code": True},
            )
        )
    elif resource.swc_count == 0:
        source_root = _content_root(resource, morphology=False)
        proposed.append(
            ResourceBinding(
                _binding_id(provider, resource.resource_id, resource.source_version),
                ResourceKind.DATA_SOURCE,
                str(source_root),
                AccessMode.READ_ONLY,
                label,
                False,
                metadata,
            )
        )
    if resource.swc_count:
        morphology_root = _content_root(resource, morphology=not is_model)
        suffix = "-swc" if is_model else ""
        proposed.append(
            ResourceBinding(
                _binding_id(provider, resource.resource_id, resource.source_version, suffix),
                ResourceKind.MORPHOLOGY_SOURCE,
                str(morphology_root),
                AccessMode.READ_ONLY,
                f"{label} morphologies" if is_model else label,
                False,
                metadata,
            )
        )
    connectivity = payload.get("connectivity")
    if isinstance(connectivity, Mapping) and bool(connectivity.get("included")):
        table_value = str(connectivity.get("table_path") or "")
        table = (resource.root / table_value).resolve()
        try:
            table.relative_to(resource.root.resolve())
        except ValueError as exc:
            raise ValueError("Managed connectivity table escapes its resource bundle") from exc
        if not table.is_file():
            raise ValueError(f"Managed connectivity table is missing: {table}")
        proposed.append(
            ResourceBinding(
                _binding_id(
                    provider,
                    resource.resource_id,
                    resource.source_version,
                    "-data",
                ),
                ResourceKind.DATA_SOURCE,
                str(table.parent),
                AccessMode.READ_ONLY,
                f"{label} connectivity",
                False,
                {
                    **metadata,
                    "data_role": "neuprint_connectivity",
                    "connectivity_scope": str(connectivity.get("scope") or "selected"),
                },
            )
        )
    return tuple(proposed)


def _stored_bindings(
    profile: ResourceProfile,
    resource: ManagedResource,
) -> tuple[ResourceBinding, ...]:
    return tuple(
        binding
        for binding in _matching_bindings(profile, resource)
        if binding.metadata.get("lifecycle_state") == STORED_ONLY_STATE
    )


def _previous_bindings(
    resource: ManagedResource,
    stored: tuple[ResourceBinding, ...],
) -> tuple[ResourceBinding, ...]:
    proposed = []
    for catalog in stored:
        payload = catalog.metadata.get("previous_bindings")
        if not isinstance(payload, list):
            continue
        for item in payload:
            previous = ResourceBinding.from_dict(item)
            previous_manifest = Path(
                str(previous.metadata.get("manifest") or "")
            ).expanduser()
            try:
                relative = Path(previous.path).expanduser().relative_to(
                    previous_manifest.parent
                )
            except ValueError as exc:
                raise ValueError(
                    f"Stored registration escapes its original bundle: {previous.resource_id}"
                ) from exc
            candidate = (resource.root / relative).resolve()
            try:
                candidate.relative_to(resource.root.resolve())
            except ValueError as exc:
                raise ValueError(
                    f"Stored registration escapes the managed bundle: {previous.resource_id}"
                ) from exc
            if not candidate.exists():
                raise ValueError(
                    f"Stored registration path is missing: {previous.resource_id}: {candidate}"
                )
            metadata = {
                **previous.metadata,
                "manifest": str(resource.manifest),
            }
            metadata.pop("lifecycle_state", None)
            proposed.append(
                ResourceBinding(
                    previous.resource_id,
                    previous.kind,
                    str(candidate),
                    previous.access,
                    previous.label,
                    previous.required,
                    metadata,
                )
            )
    return tuple(proposed)


def register_managed_resource(
    profile: ResourceProfile,
    resource: ManagedResource,
    *,
    profile_path: str | Path,
) -> tuple[str, ...]:
    """Re-register a complete on-disk bundle without recopying it."""

    _contained_resource(profile, resource)
    manifest_matches = resource_bindings(profile, resource)
    if manifest_matches:
        return tuple(binding.resource_id for binding in manifest_matches)
    stored = _stored_bindings(profile, resource)
    proposed = _previous_bindings(resource, stored) or _proposed_bindings(resource)
    stored_ids = {binding.resource_id for binding in stored}
    base_resources = tuple(
        binding for binding in profile.resources if binding.resource_id not in stored_ids
    )
    existing_ids = {binding.resource_id for binding in base_resources}
    collision = existing_ids.intersection(binding.resource_id for binding in proposed)
    if collision:
        raise FileExistsError(f"Profile binding already exists: {sorted(collision)[0]}")
    updated = ResourceProfile(
        profile.profile_id,
        (*base_resources, *proposed),
        profile.label,
    )
    destination = _profile_destination(profile_path)
    updated.save(destination, replace=destination.exists())
    return tuple(binding.resource_id for binding in proposed)


def trash_managed_resource(
    profile: ResourceProfile,
    resource: ManagedResource,
    *,
    profile_path: str | Path,
) -> TrashEntry:
    """Atomically soft-delete a bundle into the library's recoverable Trash."""

    _contained_resource(profile, resource)
    managed_root = profile.managed_data_root.resolve()
    relative_root = resource.root.resolve().relative_to(managed_root)
    manifest_relative = resource.manifest.resolve().relative_to(resource.root.resolve())
    updated, removed = _profile_without_resource(profile, resource)
    trash_parent = managed_root / ".trash"
    trash_parent.mkdir(parents=True, exist_ok=True)
    name = "--".join(
        (
            _stamp(),
            safe_component(resource.provider, field="provider"),
            safe_component(resource.resource_id, field="resource identifier"),
            safe_component(resource.source_version, field="source version"),
        )
    )
    trash_root = trash_parent / name
    if trash_root.exists():
        raise FileExistsError(f"Trash destination already exists: {trash_root}")
    receipt_payload = {
        "schema_version": 1,
        "trashed_at": _utc_now(),
        "original_relative_root": relative_root.as_posix(),
        "manifest_relative": manifest_relative.as_posix(),
        "provider": resource.provider,
        "resource_id": resource.resource_id,
        "source_version": resource.source_version,
        "bindings": [binding.to_dict() for binding in removed],
    }
    moved = False
    try:
        os.replace(resource.root, trash_root)
        moved = True
        (trash_root / TRASH_RECEIPT).write_text(
            json.dumps(receipt_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        destination = _profile_destination(profile_path)
        updated.save(destination, replace=destination.exists())
    except Exception:
        if moved and trash_root.exists() and not resource.root.exists():
            receipt = trash_root / TRASH_RECEIPT
            if receipt.exists():
                receipt.unlink()
            resource.root.parent.mkdir(parents=True, exist_ok=True)
            os.replace(trash_root, resource.root)
        raise
    return TrashEntry(
        trash_root,
        managed_root / relative_root,
        manifest_relative,
        resource.provider,
        resource.resource_id,
        resource.source_version,
        str(receipt_payload["trashed_at"]),
        removed,
    )


def _trash_entry(path: Path, managed_root: Path) -> TrashEntry:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read Data Library Trash receipt {path}: {exc}") from exc
    if not isinstance(payload, Mapping) or int(payload.get("schema_version", 0)) != 1:
        raise ValueError(f"Unsupported Data Library Trash receipt: {path}")
    relative = Path(str(payload.get("original_relative_root") or ""))
    manifest_relative = Path(str(payload.get("manifest_relative") or ""))
    if (
        not relative.parts
        or relative.is_absolute()
        or ".." in relative.parts
        or not manifest_relative.parts
        or manifest_relative.is_absolute()
        or ".." in manifest_relative.parts
    ):
        raise ValueError(f"Unsafe Data Library Trash receipt paths: {path}")
    bindings_payload = payload.get("bindings")
    if not isinstance(bindings_payload, list):
        raise ValueError(f"Trash receipt has no binding list: {path}")
    return TrashEntry(
        path.parent.resolve(),
        (managed_root / relative).resolve(),
        manifest_relative,
        str(payload.get("provider") or "unknown"),
        str(payload.get("resource_id") or path.parent.name),
        str(payload.get("source_version") or "unknown"),
        str(payload.get("trashed_at") or ""),
        tuple(ResourceBinding.from_dict(item) for item in bindings_payload),
    )


def list_trash_entries(profile: ResourceProfile) -> tuple[TrashEntry, ...]:
    trash_root = profile.managed_data_root / ".trash"
    if not trash_root.is_dir():
        return ()
    entries = []
    for receipt in trash_root.glob(f"*/{TRASH_RECEIPT}"):
        entries.append(_trash_entry(receipt, profile.managed_data_root.resolve()))
    return tuple(sorted(entries, key=lambda item: item.trashed_at, reverse=True))


def restore_trashed_resource(
    profile: ResourceProfile,
    entry: TrashEntry,
    *,
    profile_path: str | Path,
) -> ManagedResource:
    """Restore a trashed bundle and its exact previous profile bindings."""

    managed_root = profile.managed_data_root.resolve()
    try:
        entry.trash_root.resolve().relative_to(managed_root / ".trash")
        entry.original_root.resolve().relative_to(managed_root)
    except ValueError as exc:
        raise ValueError("Trash entry does not belong to the active managed library") from exc
    if not entry.trash_root.is_dir() or not entry.manifest.is_file():
        raise ValueError("Trashed bundle or manifest is missing")
    if entry.original_root.exists():
        raise FileExistsError(f"Original bundle location is already occupied: {entry.original_root}")
    restored_bindings: list[ResourceBinding] = []
    for binding in entry.bindings:
        old_manifest = Path(str(binding.metadata.get("manifest") or "")).expanduser()
        try:
            binding_relative = Path(binding.path).expanduser().relative_to(
                old_manifest.parent
            )
        except ValueError as exc:
            raise ValueError(
                f"Trash receipt binding escapes its original bundle: {binding.resource_id}"
            ) from exc
        metadata = {
            **binding.metadata,
            "manifest": str(entry.original_root / entry.manifest_relative),
        }
        restored_bindings.append(
            ResourceBinding(
                binding.resource_id,
                binding.kind,
                str(entry.original_root / binding_relative),
                binding.access,
                binding.label,
                binding.required,
                metadata,
            )
        )
    existing_ids = {binding.resource_id for binding in profile.resources}
    collision = existing_ids.intersection(
        binding.resource_id for binding in restored_bindings
    )
    if collision:
        raise FileExistsError(f"Profile binding already exists: {sorted(collision)[0]}")
    updated = ResourceProfile(
        profile.profile_id,
        (*profile.resources, *restored_bindings),
        profile.label,
    )
    moved = False
    try:
        entry.original_root.parent.mkdir(parents=True, exist_ok=True)
        os.replace(entry.trash_root, entry.original_root)
        moved = True
        receipt = entry.original_root / TRASH_RECEIPT
        if receipt.exists():
            os.replace(receipt, entry.original_root / RESTORE_RECEIPT)
        destination = _profile_destination(profile_path)
        updated.save(destination, replace=destination.exists())
    except Exception:
        if moved and entry.original_root.exists() and not entry.trash_root.exists():
            restored_receipt = entry.original_root / RESTORE_RECEIPT
            if restored_receipt.exists():
                os.replace(restored_receipt, entry.original_root / TRASH_RECEIPT)
            entry.trash_root.parent.mkdir(parents=True, exist_ok=True)
            os.replace(entry.original_root, entry.trash_root)
        raise
    return load_managed_resource(entry.original_root / entry.manifest_relative)


def preview_library_relink(
    profile: ResourceProfile,
    new_root: str | Path,
    *,
    allow_empty: bool = False,
) -> RelinkPreview:
    """Validate a user-moved library and preview path updates without writing."""

    selected = Path(new_root).expanduser()
    if selected.is_symlink():
        raise ValueError("The relocated Data Library root cannot be a symbolic link")
    target = selected.resolve()
    current = profile.managed_data_root.resolve()
    if target == current:
        raise ValueError("Choose the new location of the relocated Data Library")
    if not target.is_dir():
        raise ValueError(f"Relocated Data Library does not exist: {target}")
    try:
        target.relative_to(current)
    except ValueError:
        pass
    else:
        raise ValueError("The relocated Data Library cannot be nested inside the current root")
    try:
        current.relative_to(target)
    except ValueError:
        pass
    else:
        raise ValueError("The relocated Data Library cannot contain the current root")
    managed_bindings = tuple(
        binding
        for binding in profile.resources
        if _binding_belongs_to_library(profile, binding)
    )

    def relocated_candidate(relative: Path, *, label: str, file: bool) -> Path:
        candidate = target / relative
        cursor = target
        for part in relative.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise ValueError(f"Relocated {label} uses a symbolic link: {cursor}")
        try:
            candidate.resolve().relative_to(target)
        except ValueError as exc:
            raise ValueError(f"Relocated {label} escapes the selected library: {candidate}") from exc
        available = candidate.is_file() if file else candidate.exists()
        if not available:
            noun = "manifest" if file else "resource path"
            raise ValueError(f"Relocated {noun} is missing for {label}: {candidate}")
        return candidate

    for binding in managed_bindings:
        manifest_value = binding.metadata.get("manifest")
        if not manifest_value:
            raise ValueError(f"Managed binding has no manifest: {binding.resource_id}")
        try:
            path_relative = Path(binding.path).expanduser().resolve().relative_to(current)
            manifest_relative = (
                Path(str(manifest_value)).expanduser().resolve().relative_to(current)
            )
        except ValueError as exc:
            raise ValueError(
                f"Managed binding is already outside the active library: {binding.resource_id}"
            ) from exc
        relocated_candidate(path_relative, label=binding.resource_id, file=False)
        relocated_candidate(manifest_relative, label=binding.resource_id, file=True)
    resource_roots = {
        manifest.parent.resolve() for manifest in target.rglob(MANIFEST_FILENAME)
    }
    resource_roots.update(
        receipt.parent.resolve()
        for receipt in (target / ".trash").glob(f"*/{TRASH_RECEIPT}")
    )
    resource_count = len(resource_roots)
    if not managed_bindings and resource_count == 0 and not allow_empty:
        raise ValueError("The selected folder contains no Digifly managed resources")
    return RelinkPreview(current, target, len(managed_bindings), resource_count)


def relink_managed_library(
    profile: ResourceProfile,
    new_root: str | Path,
    *,
    profile_path: str | Path,
    allow_empty: bool = False,
) -> RelinkPreview:
    """Point the profile at an already relocated library without copying data."""

    preview = preview_library_relink(profile, new_root, allow_empty=allow_empty)
    updated_bindings: list[ResourceBinding] = []
    for binding in profile.resources:
        if binding.kind == ResourceKind.MANAGED_DATA_ROOT:
            updated_bindings.append(
                ResourceBinding(
                    binding.resource_id,
                    binding.kind,
                    str(preview.new_root),
                    binding.access,
                    binding.label,
                    binding.required,
                    dict(binding.metadata),
                )
            )
            continue
        if not _binding_belongs_to_library(profile, binding):
            updated_bindings.append(binding)
            continue
        path_relative = Path(binding.path).expanduser().resolve().relative_to(
            preview.current_root
        )
        manifest_relative = Path(
            str(binding.metadata["manifest"])
        ).expanduser().resolve().relative_to(preview.current_root)
        metadata = {**binding.metadata, "manifest": str(preview.new_root / manifest_relative)}
        updated_bindings.append(
            ResourceBinding(
                binding.resource_id,
                binding.kind,
                str(preview.new_root / path_relative),
                binding.access,
                binding.label,
                binding.required,
                metadata,
            )
        )
    updated = ResourceProfile(profile.profile_id, tuple(updated_bindings), profile.label)
    report = updated.validate()
    blocking = [check.detail for check in report.checks if check.blocking and not check.ok]
    if blocking:
        raise ValueError(f"Relocated profile is invalid: {blocking[0]}")
    destination = _profile_destination(profile_path)
    updated.save(destination, replace=destination.exists())
    return preview


def preview_library_move(
    profile: ResourceProfile,
    new_root: str | Path,
) -> LibraryMovePreview:
    """Validate a destination for an app-managed whole-library move."""

    source = profile.managed_data_root.resolve()
    if not source.is_dir() or source.is_symlink():
        raise ValueError(f"The active managed Data Library is unavailable: {source}")
    selected = Path(new_root).expanduser()
    if selected.is_symlink() or selected.exists():
        raise FileExistsError(f"The new Data Library destination already exists: {selected}")
    if not selected.parent.is_dir() or selected.parent.is_symlink():
        raise ValueError(f"The destination parent is unavailable: {selected.parent}")
    target = selected.resolve()
    try:
        target.relative_to(source)
    except ValueError:
        pass
    else:
        raise ValueError("The new Data Library cannot be nested inside the current library")
    try:
        source.relative_to(target)
    except ValueError:
        pass
    else:
        raise ValueError("The new Data Library cannot contain the current library")
    return LibraryMovePreview(
        source,
        target,
        source.stat().st_dev == target.parent.stat().st_dev,
    )


def _library_inventory(
    root: Path,
) -> tuple[tuple[Path, ...], tuple[tuple[Path, Path, int], ...], int]:
    directories: list[Path] = []
    files: list[tuple[Path, Path, int]] = []
    total_bytes = 0
    for current, directory_names, file_names in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in directory_names:
            candidate = current_path / name
            details = os.lstat(candidate)
            if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
                raise ValueError(f"Managed library contains an unsupported directory entry: {candidate}")
            directories.append(candidate.relative_to(root))
        for name in file_names:
            candidate = current_path / name
            details = os.lstat(candidate)
            if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
                raise ValueError(f"Managed library contains an unsupported file entry: {candidate}")
            relative = candidate.relative_to(root)
            files.append((candidate, relative, details.st_size))
            total_bytes += details.st_size
    return (
        tuple(sorted(directories, key=lambda path: (len(path.parts), path.as_posix()))),
        tuple(sorted(files, key=lambda item: item[1].as_posix())),
        total_bytes,
    )


def _verified_copy(source: Path, destination: Path) -> None:
    digest = hashlib.sha256()
    with source.open("rb") as input_stream, destination.open("xb") as output_stream:
        for block in iter(lambda: input_stream.read(1024 * 1024), b""):
            digest.update(block)
            output_stream.write(block)
        output_stream.flush()
        os.fsync(output_stream.fileno())
    shutil.copystat(source, destination, follow_symlinks=False)
    copied = hashlib.sha256()
    with destination.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            copied.update(block)
    if copied.digest() != digest.digest():
        raise OSError(f"Copied file failed checksum verification: {source}")


def move_managed_library(
    profile: ResourceProfile,
    new_root: str | Path,
    *,
    profile_path: str | Path,
    progress: Any | None = None,
    cancel: threading.Event | None = None,
) -> LibraryMoveResult:
    """Move the whole library atomically or by verified cross-filesystem copy."""

    preview = preview_library_move(profile, new_root)
    cancel_event = cancel or threading.Event()

    def emit(
        stage: str,
        completed_files: int,
        total_files: int,
        completed_bytes: int,
        total_bytes: int,
        current: str = "",
    ) -> None:
        if progress is None:
            return
        try:
            progress(
                LibraryMoveProgress(
                    stage,
                    completed_files,
                    total_files,
                    completed_bytes,
                    total_bytes,
                    current,
                )
            )
        except Exception:
            pass

    emit("Inventorying managed library", 0, 0, 0, 0)
    directories, files, total_bytes = _library_inventory(preview.source_root)
    file_count = len(files)
    if cancel_event.is_set():
        raise LibraryMoveCancelled("The Data Library move was cancelled")
    if preview.same_filesystem:
        emit("Moving library atomically", 0, file_count, 0, total_bytes)
        os.replace(preview.source_root, preview.target_root)
        try:
            relink_managed_library(
                profile,
                preview.target_root,
                profile_path=profile_path,
                allow_empty=True,
            )
        except Exception:
            if preview.target_root.exists() and not preview.source_root.exists():
                os.replace(preview.target_root, preview.source_root)
            raise
        emit("Complete", file_count, file_count, total_bytes, total_bytes)
        return LibraryMoveResult(
            preview.source_root,
            preview.target_root,
            "atomic_rename",
            True,
            file_count,
            total_bytes,
        )

    if shutil.disk_usage(preview.target_root.parent).free < total_bytes:
        raise OSError(
            f"The destination needs {total_bytes:,} bytes, but only "
            f"{shutil.disk_usage(preview.target_root.parent).free:,} bytes are free"
        )
    candidate = preview.target_root.parent / (
        f".{preview.target_root.name}.digifly-copy-{uuid4().hex}"
    )
    promoted = False
    profile_updated = False
    completed_bytes = 0
    try:
        candidate.mkdir()
        for relative in directories:
            (candidate / relative).mkdir(parents=True, exist_ok=True)
        for index, (source_file, relative, size) in enumerate(files, 1):
            if cancel_event.is_set():
                raise LibraryMoveCancelled("The Data Library move was cancelled")
            emit(
                "Copying and verifying library",
                index - 1,
                file_count,
                completed_bytes,
                total_bytes,
                relative.as_posix(),
            )
            destination_file = candidate / relative
            destination_file.parent.mkdir(parents=True, exist_ok=True)
            _verified_copy(source_file, destination_file)
            completed_bytes += size
        if cancel_event.is_set():
            raise LibraryMoveCancelled("The Data Library move was cancelled")
        os.replace(candidate, preview.target_root)
        promoted = True
        relink_managed_library(
            profile,
            preview.target_root,
            profile_path=profile_path,
            allow_empty=True,
        )
        profile_updated = True
    except Exception:
        cleanup = preview.target_root if promoted else candidate
        if cleanup.exists() and not profile_updated:
            shutil.rmtree(cleanup)
        raise
    source_removed = True
    try:
        shutil.rmtree(preview.source_root)
    except OSError:
        source_removed = False
    emit("Complete", file_count, file_count, total_bytes, total_bytes)
    return LibraryMoveResult(
        preview.source_root,
        preview.target_root,
        "verified_copy",
        source_removed,
        file_count,
        total_bytes,
    )


def purge_trashed_resource(
    profile: ResourceProfile,
    entry: TrashEntry,
) -> tuple[int, int]:
    """Permanently delete exactly one validated Trash entry."""

    managed_root = profile.managed_data_root.resolve()
    trash_path = managed_root / ".trash"
    if trash_path.is_symlink():
        raise ValueError("Refusing to purge through a symbolic-link Trash root")
    trash_root = trash_path.resolve()
    if entry.trash_root.is_symlink():
        raise ValueError("Refusing to purge a symbolic-link Trash entry")
    resolved = entry.trash_root.resolve()
    try:
        relative = resolved.relative_to(trash_root)
    except ValueError as exc:
        raise ValueError("Trash entry does not belong to the active managed library") from exc
    if len(relative.parts) != 1:
        raise ValueError("Refusing to purge an unsafe Data Library Trash path")
    receipt = resolved / TRASH_RECEIPT
    if not resolved.is_dir() or not receipt.is_file() or not entry.manifest.is_file():
        raise ValueError("The selected Trash entry is incomplete")
    _directories, files, total_bytes = _library_inventory(resolved)
    shutil.rmtree(resolved)
    if trash_root.is_dir() and not any(trash_root.iterdir()):
        trash_root.rmdir()
    return len(files), total_bytes
