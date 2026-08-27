"""Provider-neutral managed data staging, promotion, and registration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Iterable

from digifly_app import __version__

from .resource_profile import (
    AccessMode,
    ResourceBinding,
    ResourceKind,
    ResourceProfile,
)


MANIFEST_FILENAME = "digifly-resource.json"
MANIFEST_SCHEMA_VERSION = 1
DEFAULT_MAX_FILES = 500_000
_SAFE_COMPONENT = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


@dataclass(frozen=True)
class SourceFile:
    path: Path
    relative_path: Path
    size_bytes: int


@dataclass(frozen=True)
class ImportPreview:
    source: Path
    files: tuple[SourceFile, ...]
    total_bytes: int
    swc_count: int

    @property
    def file_count(self) -> int:
        return len(self.files)


@dataclass(frozen=True)
class ManagedResource:
    provider: str
    resource_id: str
    source_version: str
    root: Path
    manifest: Path
    file_count: int
    total_bytes: int
    swc_count: int
    imported_at: str
    registered_binding: str = ""
    integrity: str = "not_checked"

    @property
    def is_registered(self) -> bool:
        return bool(self.registered_binding)

    @property
    def registered_bindings(self) -> tuple[str, ...]:
        return tuple(value for value in self.registered_binding.split(",") if value)


def safe_component(value: str, *, field: str = "identifier") -> str:
    normalized = value.strip().casefold().replace(" ", "-")
    normalized = re.sub(r"[^a-z0-9._-]+", "-", normalized).strip("-._")
    if not normalized or not _SAFE_COMPONENT.fullmatch(normalized):
        raise ValueError(f"Invalid {field}: {value!r}")
    return normalized


def _walk_source(root: Path) -> Iterable[SourceFile]:
    if root.is_symlink():
        raise ValueError(f"Symbolic links are not accepted for managed imports: {root}")
    if root.is_file():
        yield SourceFile(root, Path(root.name), root.stat().st_size)
        return
    if not root.is_dir():
        raise ValueError(f"Import source is not a regular file or directory: {root}")
    for current, directory_names, file_names in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in tuple(directory_names):
            candidate = current_path / name
            if candidate.is_symlink():
                raise ValueError(
                    f"Symbolic links are not accepted for managed imports: {candidate}"
                )
            if not candidate.is_dir():
                raise ValueError(f"Unsupported directory entry: {candidate}")
        for name in file_names:
            candidate = current_path / name
            if candidate.is_symlink():
                raise ValueError(
                    f"Symbolic links are not accepted for managed imports: {candidate}"
                )
            if not candidate.is_file():
                raise ValueError(f"Unsupported non-regular file: {candidate}")
            yield SourceFile(
                candidate,
                candidate.relative_to(root),
                candidate.stat().st_size,
            )


def preview_local_source(
    source: str | Path,
    *,
    max_files: int = DEFAULT_MAX_FILES,
) -> ImportPreview:
    source_path = Path(source).expanduser().resolve()
    files: list[SourceFile] = []
    total_bytes = 0
    swc_count = 0
    for item in _walk_source(source_path):
        files.append(item)
        if len(files) > max_files:
            raise ValueError(f"Import exceeds the configured {max_files:,}-file limit")
        total_bytes += item.size_bytes
        if item.relative_path.suffix.casefold() == ".swc":
            swc_count += 1
    if not files:
        raise ValueError("Import source contains no files")
    return ImportPreview(source_path, tuple(files), total_bytes, swc_count)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _resource_destination(
    managed_root: Path,
    provider: str,
    resource_id: str,
    source_version: str,
) -> Path:
    return managed_root / "imports" / provider / resource_id / source_version


def _binding_id(provider: str, resource_id: str, source_version: str) -> str:
    value = f"managed-{provider}-{resource_id}-{source_version}"
    if len(value) <= 96:
        return value
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"{value[:83].rstrip('-._')}-{digest}"


def _registered_profile(
    profile: ResourceProfile,
    resource: ManagedResource,
    *,
    label: str,
    dataset: str,
    morphology_root: Path | None = None,
    metadata: dict[str, Any] | None = None,
) -> tuple[ResourceProfile, str]:
    if resource.swc_count == 0:
        return profile, ""
    binding_id = _binding_id(
        resource.provider, resource.resource_id, resource.source_version
    )
    if any(binding.resource_id == binding_id for binding in profile.resources):
        raise FileExistsError(f"Profile binding already exists: {binding_id}")
    source_root = (morphology_root or (resource.root / "source")).resolve()
    if not source_root.is_dir():
        raise ValueError(f"Managed morphology root does not exist: {source_root}")
    try:
        source_root.relative_to(resource.root.resolve())
    except ValueError as exc:
        raise ValueError("Managed morphology root must stay inside its resource bundle") from exc
    binding_metadata = {
        "provider": resource.provider,
        "dataset": dataset or resource.resource_id,
        "source_version": resource.source_version,
        "manifest": str(resource.manifest),
        "managed": True,
    }
    binding_metadata.update(metadata or {})
    binding = ResourceBinding(
        binding_id,
        ResourceKind.MORPHOLOGY_SOURCE,
        str(source_root),
        AccessMode.READ_ONLY,
        label or resource.resource_id,
        False,
        binding_metadata,
    )
    return (
        ResourceProfile(
            profile.profile_id,
            (*profile.resources, binding),
            profile.label,
        ),
        binding_id,
    )


def register_managed_morphology(
    profile: ResourceProfile,
    resource: ManagedResource,
    *,
    profile_path: str | Path,
    morphology_root: str | Path | None = None,
    label: str = "",
    dataset: str = "",
    metadata: dict[str, Any] | None = None,
) -> str:
    """Register an atomically promoted provider bundle in the current profile."""

    updated, binding_id = _registered_profile(
        profile,
        resource,
        label=label,
        dataset=dataset,
        morphology_root=(
            Path(morphology_root).expanduser().resolve()
            if morphology_root is not None
            else None
        ),
        metadata=metadata,
    )
    if updated != profile:
        destination = _current_profile_destination(profile_path)
        updated.save(destination, replace=destination.exists())
    return binding_id


def _current_profile_destination(profile_path: str | Path) -> Path:
    source = Path(profile_path).expanduser().resolve()
    if source.name == "resources-v1.json":
        return source.with_name("resources-v2.json")
    return source


def import_local_source(
    profile: ResourceProfile,
    source: str | Path,
    *,
    provider: str,
    resource_id: str,
    source_version: str,
    profile_path: str | Path | None = None,
    label: str = "",
    dataset: str = "",
    source_url: str = "",
    citation: str = "",
    license_text: str = "",
    selection: dict[str, Any] | None = None,
    max_files: int = DEFAULT_MAX_FILES,
) -> ManagedResource:
    """Copy a local source through staging and atomically promote a complete bundle."""
    provider_key = safe_component(provider, field="provider")
    resource_key = safe_component(resource_id, field="resource identifier")
    version_key = safe_component(source_version, field="source version")
    preview = preview_local_source(source, max_files=max_files)
    managed_root = profile.managed_data_root
    try:
        preview.source.relative_to(managed_root)
    except ValueError:
        pass
    else:
        raise ValueError(
            "The selected source is already inside the managed library; register or relink it instead."
        )
    managed_root.mkdir(parents=True, exist_ok=True)
    free_bytes = shutil.disk_usage(managed_root).free
    if preview.total_bytes > free_bytes:
        raise OSError(
            f"Import needs {preview.total_bytes:,} bytes but only {free_bytes:,} bytes are free"
        )
    destination = _resource_destination(
        managed_root, provider_key, resource_key, version_key
    )
    if destination.exists():
        raise FileExistsError(f"Managed resource already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging_root = managed_root / ".staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="import-", dir=staging_root))
    promoted = False
    imported_at = _utc_now()
    try:
        source_root = staging / "source"
        derived_root = staging / "derived"
        source_root.mkdir()
        derived_root.mkdir()
        inventory: list[dict[str, Any]] = []
        quality_audited = 0
        quality_review = 0
        quality_errors = 0
        for item in preview.files:
            destination_file = source_root / item.relative_path
            destination_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item.path, destination_file, follow_symlinks=False)
            record: dict[str, Any] = {
                "path": (Path("source") / item.relative_path).as_posix(),
                "size_bytes": item.size_bytes,
                "sha256": _sha256(destination_file),
            }
            if item.relative_path.suffix.casefold() == ".swc":
                from .swc_quality import analyze_swc

                report = analyze_swc(
                    destination_file,
                    dataset=dataset or resource_key,
                    provider=provider_key,
                )
                quality_audited += 1
                quality_review += int(report.needs_review)
                quality_errors += len(report.errors)
                record["swc_quality"] = {
                    "needs_review": report.needs_review,
                    "finding_count": len(report.findings),
                    "error_count": len(report.errors),
                }
            inventory.append(record)
        manifest_payload = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "provider": provider_key,
            "resource_id": resource_key,
            "dataset": dataset or resource_key,
            "source_version": version_key,
            "source_url": source_url,
            "source_path_at_import": str(preview.source),
            "fetched_at": imported_at,
            "selection": selection or {},
            "citation": citation,
            "license": license_text,
            "digifly_version": __version__,
            "derived_data_directory": "derived",
            "file_count": preview.file_count,
            "total_bytes": preview.total_bytes,
            "swc_count": preview.swc_count,
            "post_import_quality": {
                "rule": "per-swc-adaptive-radius-island-v2",
                "audited_swcs": quality_audited,
                "needs_review": quality_review,
                "structural_errors": quality_errors,
            },
            "files": inventory,
        }
        manifest_path = staging / MANIFEST_FILENAME
        manifest_path.write_text(
            json.dumps(manifest_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(staging, destination)
        promoted = True
        resource = ManagedResource(
            provider_key,
            resource_key,
            version_key,
            destination,
            destination / MANIFEST_FILENAME,
            preview.file_count,
            preview.total_bytes,
            preview.swc_count,
            imported_at,
        )
        if profile_path is not None:
            binding_id = register_managed_morphology(
                profile,
                resource,
                profile_path=profile_path,
                label=label,
                dataset=dataset,
            )
        else:
            _registered, binding_id = _registered_profile(
                profile,
                resource,
                label=label,
                dataset=dataset,
            )
        return ManagedResource(
            **{**resource.__dict__, "registered_binding": binding_id}
        )
    except Exception:
        if promoted and destination.exists():
            os.replace(destination, staging)
            promoted = False
        raise
    finally:
        if not promoted and staging.exists():
            shutil.rmtree(staging)


def _resource_from_manifest(path: Path, *, verify: bool) -> ManagedResource:
    payload = json.loads(path.read_text(encoding="utf-8"))
    files = payload.get("files")
    if not isinstance(files, list):
        raise ValueError(f"Managed resource manifest has no file inventory: {path}")
    integrity = "not_checked"
    if verify:
        integrity = "verified"
        for record in files:
            relative = Path(str(record.get("path") or ""))
            candidate = (path.parent / relative).resolve()
            try:
                candidate.relative_to(path.parent.resolve())
            except ValueError as exc:
                raise ValueError(f"Manifest path escapes resource root: {relative}") from exc
            if not candidate.is_file() or _sha256(candidate) != str(record.get("sha256") or ""):
                integrity = "checksum_mismatch"
                break
    swc_count = int(
        payload.get(
            "swc_count",
            sum(
                1
                for record in files
                if str(record.get("path") or "").casefold().endswith(".swc")
            ),
        )
    )
    return ManagedResource(
        provider=str(payload.get("provider") or "unknown"),
        resource_id=str(payload.get("resource_id") or path.parent.name),
        source_version=str(
            payload.get("source_version") or payload.get("dataset") or "unknown"
        ),
        root=path.parent.resolve(),
        manifest=path.resolve(),
        file_count=int(payload.get("file_count", len(files))),
        total_bytes=int(
            payload.get(
                "total_bytes",
                sum(
                    int(record.get("size_bytes", record.get("bytes", 0)))
                    for record in files
                ),
            )
        ),
        swc_count=swc_count,
        imported_at=str(payload.get("fetched_at") or ""),
        integrity=integrity,
    )


def load_managed_resource(
    manifest: str | Path,
    *,
    verify: bool = False,
) -> ManagedResource:
    """Load one provider-neutral managed bundle from its manifest."""

    path = Path(manifest).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"Managed resource manifest does not exist: {path}")
    return _resource_from_manifest(path, verify=verify)


def list_managed_resources(
    profile: ResourceProfile,
    *,
    verify: bool = False,
) -> tuple[ManagedResource, ...]:
    root = profile.managed_data_root
    if not root.is_dir():
        return ()
    resources = []
    manifests_seen: set[Path] = set()
    bindings_by_manifest: dict[Path, list[str]] = {}
    identity_by_manifest: dict[Path, str] = {}
    for binding in profile.resources:
        manifest_value = binding.metadata.get("manifest")
        if not manifest_value:
            continue
        manifest = Path(str(manifest_value)).expanduser().resolve()
        try:
            manifest.relative_to(root)
        except ValueError:
            continue
        display_id = str(binding.metadata.get("display_resource_id") or "")
        if display_id:
            identity_by_manifest.setdefault(manifest, display_id)
        if binding.metadata.get("lifecycle_state") != "stored_only":
            bindings_by_manifest.setdefault(manifest, []).append(binding.resource_id)

    def with_registration(resource: ManagedResource) -> ManagedResource:
        binding_ids = sorted(bindings_by_manifest.get(resource.manifest.resolve(), ()))
        return ManagedResource(
            **{
                **resource.__dict__,
                "resource_id": identity_by_manifest.get(
                    resource.manifest.resolve(), resource.resource_id
                ),
                "registered_binding": ",".join(binding_ids),
            }
        )
    imports_root = root / "imports"
    if imports_root.is_dir():
        for manifest in imports_root.rglob(MANIFEST_FILENAME):
            resources.append(
                with_registration(_resource_from_manifest(manifest, verify=verify))
            )
            manifests_seen.add(manifest.resolve())
    for provider_root in (root / "modeldb", root / "models"):
        if provider_root.is_dir():
            for manifest in provider_root.rglob(MANIFEST_FILENAME):
                resolved = manifest.resolve()
                if resolved in manifests_seen:
                    continue
                resources.append(
                    with_registration(_resource_from_manifest(manifest, verify=verify))
                )
                manifests_seen.add(resolved)
    for binding in profile.resources:
        if binding.kind not in {
            ResourceKind.MORPHOLOGY_SOURCE,
            ResourceKind.CONNECTOME_SOURCE,
            ResourceKind.DATA_SOURCE,
            ResourceKind.MODEL_SOURCE,
        }:
            continue
        manifest_value = binding.metadata.get("manifest")
        if not manifest_value:
            continue
        manifest = Path(str(manifest_value)).expanduser().resolve()
        try:
            manifest.relative_to(root)
        except ValueError:
            continue
        if manifest in manifests_seen or not manifest.is_file():
            continue
        resource = with_registration(_resource_from_manifest(manifest, verify=verify))
        try:
            declared_resource_id = json.loads(
                manifest.read_text(encoding="utf-8")
            ).get("resource_id")
        except (OSError, AttributeError, json.JSONDecodeError):
            declared_resource_id = None
        resources.append(
            ManagedResource(
                **{
                    **resource.__dict__,
                    "resource_id": (
                        resource.resource_id
                        if declared_resource_id or resource.manifest in identity_by_manifest
                        else binding.resource_id
                    ),
                    "registered_binding": resource.registered_binding
                    or (
                        ""
                        if binding.metadata.get("lifecycle_state") == "stored_only"
                        else binding.resource_id
                    ),
                }
            )
        )
        manifests_seen.add(manifest)
    return tuple(
        sorted(
            resources,
            key=lambda item: (item.provider, item.resource_id, item.source_version),
        )
    )


def register_existing_morphology(
    profile: ResourceProfile,
    folder: str | Path,
    *,
    resource_id: str,
    profile_path: str | Path,
    label: str = "",
    dataset: str = "external",
) -> Path:
    """Register a user-managed SWC folder read-only without copying its contents."""
    root = Path(folder).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Morphology folder does not exist: {root}")
    if not any(path.is_file() and path.suffix.casefold() == ".swc" for path in root.rglob("*")):
        raise ValueError(f"Morphology folder contains no SWC files: {root}")
    resource_key = safe_component(resource_id, field="resource identifier")
    binding_id = f"external-{resource_key}"
    if any(binding.resource_id == binding_id for binding in profile.resources):
        raise FileExistsError(f"Profile binding already exists: {binding_id}")
    binding = ResourceBinding(
        binding_id,
        ResourceKind.MORPHOLOGY_SOURCE,
        str(root),
        AccessMode.READ_ONLY,
        label or root.name,
        False,
        {
            "provider": "local",
            "dataset": dataset,
            "managed": False,
        },
    )
    updated = ResourceProfile(
        profile.profile_id,
        (*profile.resources, binding),
        profile.label,
    )
    destination = _current_profile_destination(profile_path)
    return updated.save(destination, replace=destination.exists())
