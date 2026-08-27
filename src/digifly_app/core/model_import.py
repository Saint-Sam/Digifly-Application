"""Safe, inert ModelDB and local computational-model acquisition.

Imported files are data-library resources.  This module never imports Python
from a model, compiles mechanisms, or launches a simulator.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import ssl
import stat
import tarfile
import tempfile
import threading
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import zipfile

from digifly_app import __version__

from .data_library import (
    MANIFEST_FILENAME,
    MANIFEST_SCHEMA_VERSION,
    ManagedResource,
    safe_component,
)
from .resource_profile import (
    AccessMode,
    ResourceBinding,
    ResourceKind,
    ResourceProfile,
)
from .swc_quality import ADAPTIVE_RADIUS_RULE_ID, analyze_swc


MODELDB_ORIGIN = "https://modeldb.science"
DEFAULT_MAX_FILES = 100_000
DEFAULT_MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_MAX_FILE_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_MAX_TOTAL_BYTES = 10 * 1024 * 1024 * 1024
DEFAULT_MAX_COMPRESSION_RATIO = 250.0
DEFAULT_MAX_PATH_LENGTH = 512
DEFAULT_MAX_PATH_DEPTH = 32
_WINDOWS_DRIVE = re.compile(r"^[a-zA-Z]:")


class ModelImportError(RuntimeError):
    """A safe, user-facing model intake failure."""


class ModelImportCancelled(ModelImportError):
    pass


@dataclass(frozen=True)
class ArchiveLimits:
    max_files: int = DEFAULT_MAX_FILES
    max_archive_bytes: int = DEFAULT_MAX_ARCHIVE_BYTES
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES
    max_compression_ratio: float = DEFAULT_MAX_COMPRESSION_RATIO
    max_path_length: int = DEFAULT_MAX_PATH_LENGTH
    max_path_depth: int = DEFAULT_MAX_PATH_DEPTH

    def __post_init__(self) -> None:
        if min(
            self.max_files,
            self.max_archive_bytes,
            self.max_file_bytes,
            self.max_total_bytes,
            self.max_path_length,
            self.max_path_depth,
        ) < 1 or self.max_compression_ratio < 1:
            raise ValueError("Archive safety limits must all be positive")


@dataclass(frozen=True)
class ModelFile:
    path: str
    size_bytes: int


@dataclass(frozen=True)
class ModelInspection:
    source: Path
    source_kind: str
    files: tuple[ModelFile, ...]
    total_bytes: int
    simulators: tuple[str, ...]
    swc_paths: tuple[str, ...]
    mechanism_paths: tuple[str, ...]
    entry_points: tuple[str, ...]
    readme_paths: tuple[str, ...]
    license_paths: tuple[str, ...]
    warnings: tuple[str, ...] = ()

    @property
    def file_count(self) -> int:
        return len(self.files)

    @property
    def swc_count(self) -> int:
        return len(self.swc_paths)

    @property
    def mechanism_count(self) -> int:
        return len(self.mechanism_paths)


@dataclass(frozen=True)
class ModelDBMetadata:
    accession: int
    name: str
    version: str
    version_date: str = ""
    notes: str = ""
    simulators: tuple[str, ...] = ()
    papers: tuple[str, ...] = ()
    implementers: tuple[str, ...] = ()
    model_url: str = ""
    archive_url: str = ""
    archive_available: bool = False
    archive_size_bytes: int | None = None

    @property
    def citation(self) -> str:
        return "; ".join(self.papers)


@dataclass(frozen=True)
class ModelImportRequest:
    source: Path
    provider: str
    resource_id: str
    source_version: str
    label: str = ""
    source_url: str = ""
    accession: int | None = None
    citation: str = ""
    license_text: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)
    preserve_archive: bool = True
    limits: ArchiveLimits = field(default_factory=ArchiveLimits)

    def __post_init__(self) -> None:
        if self.provider not in {"modeldb", "local-model"}:
            raise ValueError("Model imports must use the modeldb or local-model provider")
        safe_component(self.resource_id, field="model folder")
        safe_component(self.source_version, field="model version")
        if self.accession is not None and self.accession < 1:
            raise ValueError("ModelDB accession must be a positive integer")


@dataclass(frozen=True)
class ModelImportProgress:
    stage: str
    completed: int
    total: int
    transferred_bytes: int = 0
    current: str = ""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _check_cancel(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise ModelImportCancelled("The model import was cancelled")


def _safe_archive_path(name: str, limits: ArchiveLimits) -> str:
    if not name or "\x00" in name:
        raise ModelImportError("The archive contains an empty or null path")
    portable = name.replace("\\", "/")
    if (
        portable.startswith("/")
        or portable.startswith("//")
        or _WINDOWS_DRIVE.match(portable)
    ):
        raise ModelImportError(f"Archive path is absolute: {name!r}")
    parts = PurePosixPath(portable).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise ModelImportError(f"Archive path is unsafe: {name!r}")
    normalized = PurePosixPath(*parts).as_posix()
    if len(normalized) > limits.max_path_length:
        raise ModelImportError(f"Archive path exceeds the length limit: {name!r}")
    if len(parts) > limits.max_path_depth:
        raise ModelImportError(f"Archive path exceeds the depth limit: {name!r}")
    return normalized


def _classify(files: tuple[ModelFile, ...]) -> tuple[
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
]:
    simulators: set[str] = set()
    swcs: list[str] = []
    mechanisms: list[str] = []
    entry_points: list[str] = []
    readmes: list[str] = []
    licenses: list[str] = []
    for item in files:
        path = PurePosixPath(item.path)
        name = path.name.casefold()
        suffix = path.suffix.casefold()
        lower_path = item.path.casefold()
        if suffix == ".swc":
            swcs.append(item.path)
        if suffix == ".mod":
            mechanisms.append(item.path)
            simulators.add("NEURON")
        if suffix in {".hoc", ".nrn"} or name in {"mosinit.hoc", "nrngui.hoc"}:
            simulators.add("NEURON")
        if suffix == ".m":
            simulators.add("MATLAB/Octave")
        if suffix in {".g", ".p"} and "genesis" in lower_path:
            simulators.add("GENESIS")
        if suffix == ".py":
            simulators.add("Python")
            if "arbor" in lower_path:
                simulators.add("Arbor")
            if "netpyne" in lower_path:
                simulators.add("NetPyNE")
            if "brian" in lower_path:
                simulators.add("Brian")
        if name in {
            "mosinit.hoc",
            "init.hoc",
            "main.py",
            "run.py",
            "run.hoc",
            "makefile",
        } or name.startswith("run_"):
            entry_points.append(item.path)
        if name.startswith("readme"):
            readmes.append(item.path)
        if name.startswith(("license", "licence", "copying")):
            licenses.append(item.path)
    return (
        tuple(sorted(simulators)),
        tuple(swcs),
        tuple(mechanisms),
        tuple(entry_points),
        tuple(readmes),
        tuple(licenses),
    )


def _finish_inspection(
    source: Path,
    source_kind: str,
    files: list[ModelFile],
    total_bytes: int,
    warnings: list[str],
) -> ModelInspection:
    if not files:
        raise ModelImportError("The selected model source contains no files")
    simulators, swcs, mechanisms, entry_points, readmes, licenses = _classify(
        tuple(files)
    )
    if not readmes:
        warnings.append("No README file was detected")
    if not licenses:
        warnings.append("No license file was detected; review upstream terms before redistribution")
    return ModelInspection(
        source,
        source_kind,
        tuple(files),
        total_bytes,
        simulators,
        swcs,
        mechanisms,
        entry_points,
        readmes,
        licenses,
        tuple(warnings),
    )


def _inspect_folder(
    source: Path, limits: ArchiveLimits, cancel: threading.Event | None
) -> ModelInspection:
    files: list[ModelFile] = []
    total = 0
    seen: set[str] = set()
    if source.is_symlink() or not source.is_dir():
        raise ModelImportError(f"Model folder is not a regular directory: {source}")
    for current, directory_names, file_names in os.walk(source, followlinks=False):
        _check_cancel(cancel)
        current_path = Path(current)
        for name in directory_names:
            candidate = current_path / name
            if candidate.is_symlink():
                raise ModelImportError(f"Symbolic links are not accepted: {candidate}")
            if not candidate.is_dir():
                raise ModelImportError(f"Unsupported directory entry: {candidate}")
        for name in file_names:
            _check_cancel(cancel)
            candidate = current_path / name
            if candidate.is_symlink():
                raise ModelImportError(f"Symbolic links are not accepted: {candidate}")
            mode = candidate.stat(follow_symlinks=False).st_mode
            if not stat.S_ISREG(mode):
                raise ModelImportError(f"Unsupported non-regular file: {candidate}")
            relative = _safe_archive_path(candidate.relative_to(source).as_posix(), limits)
            folded = relative.casefold()
            if folded in seen:
                raise ModelImportError(f"Model source contains a case-colliding path: {relative}")
            seen.add(folded)
            size = candidate.stat(follow_symlinks=False).st_size
            if size > limits.max_file_bytes:
                raise ModelImportError(f"File exceeds the per-file safety limit: {relative}")
            files.append(ModelFile(relative, size))
            total += size
            if len(files) > limits.max_files:
                raise ModelImportError(f"Model source exceeds the {limits.max_files:,}-file limit")
            if total > limits.max_total_bytes:
                raise ModelImportError("Model source exceeds the expanded-size safety limit")
    return _finish_inspection(source, "folder", files, total, [])


def _inspect_zip(
    source: Path, limits: ArchiveLimits, cancel: threading.Event | None
) -> ModelInspection:
    files: list[ModelFile] = []
    total = 0
    seen: set[str] = set()
    warnings: list[str] = []
    with zipfile.ZipFile(source) as archive:
        for member in archive.infolist():
            _check_cancel(cancel)
            normalized = _safe_archive_path(member.filename.rstrip("/"), limits)
            folded = normalized.casefold()
            if folded in seen:
                raise ModelImportError(f"Archive contains a duplicate or case-colliding path: {normalized}")
            seen.add(folded)
            unix_mode = (member.external_attr >> 16) & 0xFFFF
            file_type = stat.S_IFMT(unix_mode)
            if file_type and file_type not in {stat.S_IFREG, stat.S_IFDIR}:
                raise ModelImportError(f"Archive contains a link or special file: {normalized}")
            if member.flag_bits & 0x1:
                raise ModelImportError(f"Encrypted archive entries are not supported: {normalized}")
            if member.is_dir():
                continue
            size = int(member.file_size)
            if size > limits.max_file_bytes:
                raise ModelImportError(f"Archive member exceeds the per-file limit: {normalized}")
            if size and size / max(1, int(member.compress_size)) > limits.max_compression_ratio:
                raise ModelImportError(f"Archive member has an unsafe compression ratio: {normalized}")
            files.append(ModelFile(normalized, size))
            total += size
            if len(files) > limits.max_files:
                raise ModelImportError(f"Archive exceeds the {limits.max_files:,}-file limit")
            if total > limits.max_total_bytes:
                raise ModelImportError("Archive exceeds the expanded-size safety limit")
    return _finish_inspection(source, "zip", files, total, warnings)


def _inspect_tar(
    source: Path, limits: ArchiveLimits, cancel: threading.Event | None
) -> ModelInspection:
    files: list[ModelFile] = []
    total = 0
    seen: set[str] = set()
    with tarfile.open(source, mode="r:*") as archive:
        for member in archive:
            _check_cancel(cancel)
            normalized = _safe_archive_path(member.name.rstrip("/"), limits)
            folded = normalized.casefold()
            if folded in seen:
                raise ModelImportError(f"Archive contains a duplicate or case-colliding path: {normalized}")
            seen.add(folded)
            if member.isdir():
                continue
            if not member.isfile():
                raise ModelImportError(f"Archive contains a link or special file: {normalized}")
            size = int(member.size)
            if size > limits.max_file_bytes:
                raise ModelImportError(f"Archive member exceeds the per-file limit: {normalized}")
            files.append(ModelFile(normalized, size))
            total += size
            if len(files) > limits.max_files:
                raise ModelImportError(f"Archive exceeds the {limits.max_files:,}-file limit")
            if total > limits.max_total_bytes:
                raise ModelImportError("Archive exceeds the expanded-size safety limit")
    archive_size = max(1, source.stat().st_size)
    if total / archive_size > limits.max_compression_ratio:
        raise ModelImportError("Archive has an unsafe overall compression ratio")
    return _finish_inspection(source, "tar", files, total, [])


def inspect_model_source(
    source: str | Path,
    *,
    limits: ArchiveLimits | None = None,
    cancel: threading.Event | None = None,
) -> ModelInspection:
    """Inspect a folder or ZIP/TAR without executing or extracting model code."""

    safety = limits or ArchiveLimits()
    selected = Path(source).expanduser()
    if selected.is_symlink():
        raise ModelImportError(f"Symbolic links are not accepted: {selected}")
    path = selected.resolve()
    _check_cancel(cancel)
    if path.is_dir():
        return _inspect_folder(path, safety, cancel)
    if path.is_symlink() or not path.is_file():
        raise ModelImportError(f"Model source does not exist: {path}")
    archive_bytes = path.stat().st_size
    if archive_bytes > safety.max_archive_bytes:
        raise ModelImportError("Archive exceeds the compressed-size safety limit")
    try:
        if zipfile.is_zipfile(path):
            return _inspect_zip(path, safety, cancel)
        if tarfile.is_tarfile(path):
            return _inspect_tar(path, safety, cancel)
    except (OSError, tarfile.TarError, zipfile.BadZipFile, RuntimeError) as exc:
        raise ModelImportError(f"The archive could not be safely inspected: {exc}") from exc
    raise ModelImportError("Choose a ZIP or TAR archive, or an unpacked model folder")


def model_destination(profile: ResourceProfile, request: ModelImportRequest) -> Path:
    provider_root = "modeldb" if request.provider == "modeldb" else "models/local"
    return (
        profile.managed_data_root
        / provider_root
        / safe_component(request.resource_id, field="model folder")
        / safe_component(request.source_version, field="model version")
    )


def _copy_stream(
    source: Any,
    destination: Path,
    *,
    expected_size: int,
    cancel: threading.Event | None,
) -> None:
    written = 0
    with destination.open("xb") as output:
        while True:
            _check_cancel(cancel)
            block = source.read(min(1024 * 1024, expected_size - written + 1))
            if not block:
                break
            output.write(block)
            written += len(block)
            if written > expected_size:
                raise ModelImportError("An archive member expanded beyond its declared size")
    if written != expected_size:
        raise ModelImportError("An archive member was truncated during extraction")


def _materialize(
    inspection: ModelInspection,
    source_root: Path,
    *,
    cancel: threading.Event | None,
    progress: Callable[[ModelImportProgress], None] | None,
) -> None:
    total_files = inspection.file_count

    def emit(completed: int, current: str) -> None:
        if progress is not None:
            progress(ModelImportProgress("Copying inert model files", completed, total_files, current=current))

    if inspection.source_kind == "folder":
        for index, item in enumerate(inspection.files, 1):
            _check_cancel(cancel)
            source = inspection.source / PurePosixPath(item.path)
            destination = source_root / PurePosixPath(item.path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(source, flags)
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or opened.st_size != item.size_bytes:
                os.close(descriptor)
                raise ModelImportError(
                    f"Source file changed after inspection: {item.path}"
                )
            with os.fdopen(descriptor, "rb") as stream:
                _copy_stream(stream, destination, expected_size=item.size_bytes, cancel=cancel)
            shutil.copystat(source, destination, follow_symlinks=False)
            emit(index, item.path)
        return
    if inspection.source_kind == "zip":
        with zipfile.ZipFile(inspection.source) as archive:
            lookup = {
                _safe_archive_path(info.filename.rstrip("/"), ArchiveLimits()): info
                for info in archive.infolist()
                if not info.is_dir()
            }
            for index, item in enumerate(inspection.files, 1):
                destination = source_root / PurePosixPath(item.path)
                destination.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with archive.open(lookup[item.path], "r") as stream:
                        _copy_stream(stream, destination, expected_size=item.size_bytes, cancel=cancel)
                except (zipfile.BadZipFile, RuntimeError) as exc:
                    raise ModelImportError(f"Archive integrity check failed for {item.path}") from exc
                emit(index, item.path)
        return
    with tarfile.open(inspection.source, mode="r:*") as archive:
        lookup = {
            _safe_archive_path(member.name.rstrip("/"), ArchiveLimits()): member
            for member in archive
            if member.isfile()
        }
        for index, item in enumerate(inspection.files, 1):
            member_stream = archive.extractfile(lookup[item.path])
            if member_stream is None:
                raise ModelImportError(f"Archive member could not be read: {item.path}")
            destination = source_root / PurePosixPath(item.path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with member_stream:
                _copy_stream(member_stream, destination, expected_size=item.size_bytes, cancel=cancel)
            emit(index, item.path)


def _binding_id(provider: str, resource_id: str, version: str, suffix: str = "") -> str:
    value = f"managed-{provider}-{resource_id}-{version}{suffix}"
    if len(value) <= 96:
        return value
    digest = hashlib.sha256(value.encode()).hexdigest()[:12]
    return f"{value[:83].rstrip('-._')}-{digest}"


def _register_model(
    profile: ResourceProfile,
    profile_path: Path,
    resource: ManagedResource,
    inspection: ModelInspection,
    *,
    label: str,
    metadata: Mapping[str, Any],
) -> str:
    base_metadata = {
        "provider": resource.provider,
        "source_version": resource.source_version,
        "manifest": str(resource.manifest),
        "managed": True,
        "inert_code": True,
        **dict(metadata),
    }
    model_binding_id = _binding_id(
        resource.provider, resource.resource_id, resource.source_version
    )
    new_bindings = [
        ResourceBinding(
            model_binding_id,
            ResourceKind.MODEL_SOURCE,
            str(resource.root / "source"),
            AccessMode.READ_ONLY,
            label or resource.resource_id,
            False,
            base_metadata,
        )
    ]
    if inspection.swc_count:
        morphology_id = _binding_id(
            resource.provider, resource.resource_id, resource.source_version, "-swc"
        )
        new_bindings.append(
            ResourceBinding(
                morphology_id,
                ResourceKind.MORPHOLOGY_SOURCE,
                str(resource.root / "source"),
                AccessMode.READ_ONLY,
                f"{label or resource.resource_id} morphologies",
                False,
                {**base_metadata, "dataset": resource.resource_id},
            )
        )
    existing = {binding.resource_id for binding in profile.resources}
    collision = existing.intersection(binding.resource_id for binding in new_bindings)
    if collision:
        raise FileExistsError(f"Profile binding already exists: {sorted(collision)[0]}")
    updated = ResourceProfile(
        profile.profile_id,
        (*profile.resources, *new_bindings),
        profile.label,
    )
    destination = Path(profile_path).expanduser().resolve()
    if destination.name == "resources-v1.json":
        destination = destination.with_name("resources-v2.json")
    updated.save(destination, replace=destination.exists())
    return model_binding_id


def import_model_source(
    profile: ResourceProfile,
    profile_path: str | Path,
    request: ModelImportRequest,
    *,
    inspection: ModelInspection | None = None,
    progress: Callable[[ModelImportProgress], None] | None = None,
    cancel: threading.Event | None = None,
) -> ManagedResource:
    """Safely extract/copy, inventory, audit, promote, and register inert code."""

    reviewed = inspection or inspect_model_source(request.source, limits=request.limits)
    if reviewed.source != Path(request.source).expanduser().resolve():
        raise ValueError("The inspection does not describe the selected source")
    destination = model_destination(profile, request)
    if destination.exists():
        raise FileExistsError(f"Managed model already exists: {destination}")
    managed_root = profile.managed_data_root
    managed_root.mkdir(parents=True, exist_ok=True)
    required = reviewed.total_bytes
    if reviewed.source_kind != "folder" and request.preserve_archive:
        required += reviewed.source.stat().st_size
    if shutil.disk_usage(managed_root).free < required:
        raise OSError(f"The model import needs at least {required:,} free bytes")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging_root = managed_root / ".staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="model-", dir=staging_root))
    promoted = False
    imported_at = _utc_now()
    try:
        source_root = staging / "source"
        derived_root = staging / "derived"
        source_root.mkdir()
        derived_root.mkdir()
        _check_cancel(cancel)
        _materialize(reviewed, source_root, cancel=cancel, progress=progress)
        inventory: list[dict[str, Any]] = []
        reviews = 0
        errors = 0
        for index, item in enumerate(reviewed.files, 1):
            _check_cancel(cancel)
            copied = source_root / PurePosixPath(item.path)
            record: dict[str, Any] = {
                "path": (PurePosixPath("source") / item.path).as_posix(),
                "size_bytes": item.size_bytes,
                "sha256": _sha256(copied),
            }
            if item.path.casefold().endswith(".swc"):
                report = analyze_swc(
                    copied,
                    dataset=request.resource_id,
                    provider=request.provider,
                )
                reviews += int(report.needs_review)
                errors += len(report.errors)
                record["swc_quality"] = {
                    "needs_review": report.needs_review,
                    "finding_count": len(report.findings),
                    "error_count": len(report.errors),
                }
            inventory.append(record)
            if progress is not None:
                progress(ModelImportProgress("Checksumming and auditing", index, reviewed.file_count, current=item.path))
        if reviewed.source_kind != "folder" and request.preserve_archive:
            original_root = staging / "original"
            original_root.mkdir()
            original = original_root / reviewed.source.name
            shutil.copy2(reviewed.source, original, follow_symlinks=False)
            inventory.append(
                {
                    "path": f"original/{reviewed.source.name}",
                    "size_bytes": original.stat().st_size,
                    "sha256": _sha256(original),
                    "role": "original_archive",
                }
            )
        manifest_payload = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "provider": request.provider,
            "resource_id": safe_component(request.resource_id),
            "dataset": safe_component(request.resource_id),
            "source_version": safe_component(request.source_version),
            "source_url": request.source_url,
            "source_path_at_import": str(reviewed.source),
            "accession": request.accession,
            "fetched_at": imported_at,
            "citation": request.citation,
            "license": request.license_text,
            "digifly_version": __version__,
            "derived_data_directory": "derived",
            "inert_code": True,
            "execution_performed": False,
            "inspection": {
                "source_kind": reviewed.source_kind,
                "simulators": list(reviewed.simulators),
                "mechanism_count": reviewed.mechanism_count,
                "entry_points": list(reviewed.entry_points),
                "readmes": list(reviewed.readme_paths),
                "license_files": list(reviewed.license_paths),
                "warnings": list(reviewed.warnings),
            },
            "provider_metadata": dict(request.metadata),
            "file_count": reviewed.file_count,
            "total_bytes": reviewed.total_bytes,
            "swc_count": reviewed.swc_count,
            "post_import_quality": {
                "rule": ADAPTIVE_RADIUS_RULE_ID,
                "audited_swcs": reviewed.swc_count,
                "needs_review": reviews,
                "structural_errors": errors,
            },
            "files": inventory,
        }
        manifest = staging / MANIFEST_FILENAME
        manifest.write_text(
            json.dumps(manifest_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        _check_cancel(cancel)
        if progress is not None:
            progress(ModelImportProgress("Promoting validated model", reviewed.file_count, reviewed.file_count))
        os.replace(staging, destination)
        promoted = True
        resource = ManagedResource(
            request.provider,
            safe_component(request.resource_id),
            safe_component(request.source_version),
            destination,
            destination / MANIFEST_FILENAME,
            reviewed.file_count,
            reviewed.total_bytes,
            reviewed.swc_count,
            imported_at,
        )
        binding_id = _register_model(
            profile,
            Path(profile_path),
            resource,
            reviewed,
            label=request.label,
            metadata={
                "accession": request.accession,
                "simulators": list(reviewed.simulators),
            },
        )
        return ManagedResource(**{**resource.__dict__, "registered_binding": binding_id})
    except Exception:
        if promoted and destination.exists():
            os.replace(destination, staging)
            promoted = False
        raise
    finally:
        if not promoted and staging.exists():
            shutil.rmtree(staging)


def _attribute_value(payload: Mapping[str, Any], key: str) -> Any:
    value = payload.get(key)
    return value.get("value") if isinstance(value, Mapping) else value


def _object_names(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(
        str(item.get("object_name") or "").strip()
        for item in value
        if isinstance(item, Mapping) and str(item.get("object_name") or "").strip()
    )


class ModelDBClient:
    """Minimal HTTPS-only client for ModelDB's official API/download routes."""

    def __init__(self, *, timeout: float = 30.0):
        self.timeout = timeout

    def _open(self, request: Request):
        return urlopen(request, timeout=self.timeout, context=ssl.create_default_context())

    @staticmethod
    def _require_official_response(response: Any) -> None:
        geturl = getattr(response, "geturl", None)
        if geturl is None:
            return
        final_url = str(geturl())
        parsed = urlsplit(final_url)
        if parsed.scheme.casefold() != "https" or parsed.hostname != "modeldb.science":
            raise ModelImportError(
                "ModelDB redirected to an external host; download that code manually, then choose its archive or folder"
            )

    def _json(self, url: str, *, max_bytes: int = 4 * 1024 * 1024) -> Mapping[str, Any]:
        request = Request(url, headers={"Accept": "application/json", "User-Agent": "Digifly-Workstation"})
        try:
            with self._open(request) as response:
                self._require_official_response(response)
                declared = response.headers.get("Content-Length")
                if declared and int(declared) > max_bytes:
                    raise ModelImportError("ModelDB metadata exceeded the response limit")
                body = response.read(max_bytes + 1)
        except HTTPError as exc:
            if exc.code == 404:
                raise ModelImportError("ModelDB accession was not found") from exc
            raise ModelImportError(f"ModelDB metadata request failed with HTTP {exc.code}") from exc
        except (URLError, OSError, ValueError) as exc:
            raise ModelImportError(f"Could not reach ModelDB: {exc}") from exc
        if len(body) > max_bytes:
            raise ModelImportError("ModelDB metadata exceeded the response limit")
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ModelImportError("ModelDB returned invalid metadata") from exc
        if not isinstance(payload, Mapping):
            raise ModelImportError("ModelDB returned an unexpected metadata shape")
        return payload

    def _archive_info(self, accession: int) -> tuple[bool, int | None]:
        url = f"{MODELDB_ORIGIN}/download/{accession}"
        request = Request(url, method="HEAD", headers={"Accept": "application/zip", "User-Agent": "Digifly-Workstation"})
        try:
            with self._open(request) as response:
                self._require_official_response(response)
                content_type = str(response.headers.get("Content-Type") or "").casefold()
                disposition = str(response.headers.get("Content-Disposition") or "").casefold()
                available = "zip" in content_type or ".zip" in disposition
                declared = response.headers.get("Content-Length")
                return available, int(declared) if declared and declared.isdigit() else None
        except HTTPError as exc:
            if exc.code in {404, 405}:
                return False, None
            raise ModelImportError(f"ModelDB archive check failed with HTTP {exc.code}") from exc
        except (URLError, OSError, ValueError) as exc:
            raise ModelImportError(f"Could not check the ModelDB archive: {exc}") from exc

    def lookup(self, accession: int) -> ModelDBMetadata:
        if int(accession) < 1:
            raise ValueError("ModelDB accession must be a positive integer")
        identifier = int(accession)
        payload = self._json(f"{MODELDB_ORIGIN}/api/v1/models/{identifier}")
        available, size = self._archive_info(identifier)
        version_number = str(payload.get("ver_number") or "unknown")
        return ModelDBMetadata(
            accession=identifier,
            name=str(payload.get("name") or f"ModelDB {identifier}"),
            version=f"v{version_number}" if version_number != "unknown" else "unknown",
            version_date=str(payload.get("ver_date") or ""),
            notes=str(_attribute_value(payload, "notes") or ""),
            simulators=_object_names(_attribute_value(payload, "modeling_application")),
            papers=_object_names(_attribute_value(payload, "model_paper")),
            implementers=_object_names(_attribute_value(payload, "implemented_by")),
            model_url=f"{MODELDB_ORIGIN}/{identifier}",
            archive_url=f"{MODELDB_ORIGIN}/download/{identifier}" if available else "",
            archive_available=available,
            archive_size_bytes=size,
        )

    def download_archive(
        self,
        metadata: ModelDBMetadata,
        destination: str | Path,
        *,
        limits: ArchiveLimits | None = None,
        progress: Callable[[ModelImportProgress], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> Path:
        safety = limits or ArchiveLimits()
        if not metadata.archive_available or not metadata.archive_url:
            raise ModelImportError(
                "This ModelDB record does not expose a locally hosted ZIP. Follow its upstream code link, then choose the downloaded archive or folder."
            )
        output = Path(destination).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        request = Request(metadata.archive_url, headers={"Accept": "application/zip", "User-Agent": "Digifly-Workstation"})
        temporary = output.with_name(f".{output.name}.part")
        transferred = 0
        try:
            with self._open(request) as response:
                self._require_official_response(response)
                declared_text = response.headers.get("Content-Length")
                declared = int(declared_text) if declared_text and declared_text.isdigit() else 0
                if declared > safety.max_archive_bytes:
                    raise ModelImportError("ModelDB archive exceeds the compressed-size safety limit")
                with temporary.open("xb") as stream:
                    while True:
                        _check_cancel(cancel)
                        block = response.read(1024 * 1024)
                        if not block:
                            break
                        stream.write(block)
                        transferred += len(block)
                        if transferred > safety.max_archive_bytes:
                            raise ModelImportError("ModelDB archive exceeded the compressed-size safety limit")
                        if progress is not None:
                            progress(ModelImportProgress("Downloading ModelDB archive", transferred, declared or transferred, transferred))
            with temporary.open("rb") as stream:
                signature = stream.read(4)
            if transferred < 4 or signature not in {
                b"PK\x03\x04",
                b"PK\x05\x06",
                b"PK\x07\x08",
            }:
                raise ModelImportError("ModelDB did not return a ZIP archive")
            os.replace(temporary, output)
            return output
        except HTTPError as exc:
            raise ModelImportError(f"ModelDB archive download failed with HTTP {exc.code}") from exc
        except (URLError, OSError) as exc:
            raise ModelImportError(f"Could not download the ModelDB archive: {exc}") from exc
        finally:
            if temporary.exists():
                temporary.unlink()


def modeldb_request(
    archive: Path,
    metadata: ModelDBMetadata,
    *,
    resource_id: str | None = None,
    source_version: str | None = None,
    preserve_archive: bool = True,
    limits: ArchiveLimits | None = None,
) -> ModelImportRequest:
    return ModelImportRequest(
        source=archive,
        provider="modeldb",
        resource_id=resource_id or str(metadata.accession),
        source_version=source_version or metadata.version,
        label=metadata.name,
        source_url=metadata.model_url,
        accession=metadata.accession,
        citation=metadata.citation,
        metadata={
            "name": metadata.name,
            "version_date": metadata.version_date,
            "simulators": list(metadata.simulators),
            "papers": list(metadata.papers),
            "implementers": list(metadata.implementers),
        },
        preserve_archive=preserve_archive,
        limits=limits or ArchiveLimits(),
    )
