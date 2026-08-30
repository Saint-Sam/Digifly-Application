"""Safe, inert ModelDB and local computational-model acquisition.

Imported files are data-library resources.  This module never imports Python
from a model, compiles mechanisms, or launches a simulator.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
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
from .remote import RetryPolicy, parse_retry_after, run_with_retry
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


class ModelImportNetworkError(ModelImportError):
    def __init__(
        self,
        message: str,
        *,
        status: int = 0,
        retryable: bool = True,
        retry_after: float | None = None,
    ):
        super().__init__(message)
        self.status = int(status)
        self.retryable = bool(retryable)
        self.retry_after = retry_after


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
    sha256: str = ""


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
    source_sha256: str = ""

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

    def __post_init__(self) -> None:
        if int(self.accession) < 1:
            raise ValueError("ModelDB accession must be a positive integer")
        if self.archive_size_bytes is not None and int(self.archive_size_bytes) < 0:
            raise ValueError("ModelDB archive size cannot be negative")

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


def _sha256_regular(path: Path, *, expected_size: int | None = None) -> str:
    """Hash a regular source file without following a final symbolic link."""

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ModelImportError(f"Could not safely open source file: {path}") from exc
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise ModelImportError(f"Source is not a regular file: {path}")
        if expected_size is not None and details.st_size != expected_size:
            raise ModelImportError(f"Source file changed during inspection: {path}")
        digest = hashlib.sha256()
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
    finally:
        os.close(descriptor)


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
            files.append(
                ModelFile(
                    relative,
                    size,
                    _sha256_regular(candidate, expected_size=size),
                )
            )
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
            return replace(
                _inspect_zip(path, safety, cancel),
                source_sha256=_sha256_regular(path, expected_size=archive_bytes),
            )
        if tarfile.is_tarfile(path):
            return replace(
                _inspect_tar(path, safety, cancel),
                source_sha256=_sha256_regular(path, expected_size=archive_bytes),
            )
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
    expected_sha256: str = "",
    cancel: threading.Event | None,
) -> str:
    written = 0
    digest = hashlib.sha256()
    with destination.open("xb") as output:
        while True:
            _check_cancel(cancel)
            block = source.read(min(1024 * 1024, expected_size - written + 1))
            if not block:
                break
            output.write(block)
            digest.update(block)
            written += len(block)
            if written > expected_size:
                raise ModelImportError("An archive member expanded beyond its declared size")
    if written != expected_size:
        raise ModelImportError("An archive member was truncated during extraction")
    actual_sha256 = digest.hexdigest()
    if expected_sha256 and actual_sha256 != expected_sha256:
        raise ModelImportError("A source file changed after it was inspected")
    return actual_sha256


def _materialize(
    inspection: ModelInspection,
    source_root: Path,
    *,
    limits: ArchiveLimits,
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
                _copy_stream(
                    stream,
                    destination,
                    expected_size=item.size_bytes,
                    expected_sha256=item.sha256,
                    cancel=cancel,
                )
            shutil.copystat(source, destination, follow_symlinks=False)
            emit(index, item.path)
        return
    if inspection.source_kind == "zip":
        with zipfile.ZipFile(inspection.source) as archive:
            lookup = {
                _safe_archive_path(info.filename.rstrip("/"), limits): info
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
            _safe_archive_path(member.name.rstrip("/"), limits): member
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
    if staging_root.is_symlink():
        raise ModelImportError("Refusing to import through a symbolic-link staging root")
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
        if reviewed.source_kind != "folder":
            if not reviewed.source_sha256:
                raise ModelImportError("The reviewed archive has no source checksum")
            if _sha256_regular(reviewed.source) != reviewed.source_sha256:
                raise ModelImportError("The archive changed after it was inspected")
        _materialize(
            reviewed,
            source_root,
            limits=request.limits,
            cancel=cancel,
            progress=progress,
        )
        if (
            reviewed.source_kind != "folder"
            and _sha256_regular(reviewed.source) != reviewed.source_sha256
        ):
            raise ModelImportError("The archive changed while it was being imported")
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
            original_sha256 = _sha256(original)
            if original_sha256 != reviewed.source_sha256:
                raise ModelImportError("The archive changed while its provenance copy was made")
            inventory.append(
                {
                    "path": f"original/{reviewed.source.name}",
                    "size_bytes": original.stat().st_size,
                    "sha256": original_sha256,
                    "role": "original_archive",
                }
            )
        inventory_bytes = sum(int(item["size_bytes"]) for item in inventory)
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
            "file_count": len(inventory),
            "total_bytes": inventory_bytes,
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
            len(inventory),
            inventory_bytes,
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


MODELDB_PARTIAL_SCHEMA_VERSION = 1


def modeldb_partial_paths(destination: str | Path) -> tuple[Path, Path]:
    """Return the exact data and receipt paths used for a resumable download."""

    selected = Path(destination).expanduser()
    if selected.is_symlink():
        raise ValueError("Refusing a symbolic-link ModelDB destination")
    output = selected.resolve()
    partial = output.with_name(f".{output.name}.part")
    receipt = output.with_name(f".{output.name}.part.json")
    return partial, receipt


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def discard_modeldb_partial(destination: str | Path) -> bool:
    """Permanently discard one exact, receipt-backed ModelDB partial download."""

    partial, receipt = modeldb_partial_paths(destination)
    if partial.is_symlink() or receipt.is_symlink():
        raise ValueError("Refusing to discard a symbolic-link ModelDB partial download")
    if not partial.exists() and not receipt.exists():
        return False
    if not partial.is_file() or not receipt.is_file():
        raise ValueError("The ModelDB partial download is missing its data or receipt")
    try:
        payload = json.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("The ModelDB partial-download receipt is unreadable") from exc
    try:
        schema_version = int(payload.get("schema_version", 0))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("The ModelDB partial-download receipt is invalid") from exc
    if not isinstance(payload, Mapping) or schema_version != MODELDB_PARTIAL_SCHEMA_VERSION:
        raise ValueError("The ModelDB partial-download receipt is invalid")
    partial.unlink()
    receipt.unlink()
    return True


class ModelDBClient:
    """Minimal HTTPS-only client for ModelDB's official API/download routes."""

    def __init__(
        self,
        *,
        timeout: float = 30.0,
        retry_policy: RetryPolicy | None = None,
    ):
        self.timeout = float(timeout)
        if self.timeout <= 0:
            raise ValueError("ModelDB timeout must be positive")
        self.retry_policy = retry_policy or RetryPolicy()

    def _open(self, request: Request):
        return urlopen(request, timeout=self.timeout, context=ssl.create_default_context())

    @staticmethod
    def _require_official_url(url: str) -> str:
        parsed = urlsplit(str(url or ""))
        if (
            parsed.scheme.casefold() != "https"
            or parsed.hostname != "modeldb.science"
            or parsed.username
            or parsed.password
        ):
            raise ModelImportError("ModelDB downloads must use the official HTTPS origin")
        return str(url)

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

    @staticmethod
    def _response_status(response: Any) -> int:
        status = getattr(response, "status", None)
        if status is None:
            getcode = getattr(response, "getcode", None)
            status = getcode() if getcode is not None else 200
        return int(status or 200)

    def _retry(
        self,
        operation: Callable[[], Any],
        *,
        cancel: threading.Event | None = None,
    ) -> Any:
        return run_with_retry(
            operation,
            policy=self.retry_policy,
            retryable=lambda exc: isinstance(exc, ModelImportNetworkError)
            and exc.retryable,
            cancel=cancel,
            cancelled=lambda: ModelImportCancelled("The ModelDB operation was cancelled"),
        )

    @staticmethod
    def _http_failure(exc: HTTPError, context: str) -> ModelImportNetworkError:
        status = int(getattr(exc, "code", 0) or 0)
        if status == 404:
            detail = "ModelDB accession was not found"
        else:
            detail = f"{context} failed with HTTP {status or 'error'}"
        headers = getattr(exc, "headers", None)
        return ModelImportNetworkError(
            detail,
            status=status,
            retryable=status in {408, 425, 429, 500, 502, 503, 504},
            retry_after=parse_retry_after(headers.get("Retry-After") if headers else None),
        )

    def _json(
        self,
        url: str,
        *,
        max_bytes: int = 4 * 1024 * 1024,
        cancel: threading.Event | None = None,
    ) -> Mapping[str, Any]:
        def request_json() -> bytes:
            request = Request(
                url,
                headers={"Accept": "application/json", "User-Agent": "Digifly-Workstation"},
            )
            try:
                with self._open(request) as response:
                    self._require_official_response(response)
                    declared = response.headers.get("Content-Length")
                    if declared:
                        try:
                            declared_size = int(declared)
                        except ValueError as exc:
                            raise ModelImportError(
                                "ModelDB returned an invalid metadata length"
                            ) from exc
                        if declared_size > max_bytes:
                            raise ModelImportError("ModelDB metadata exceeded the response limit")
                    body = response.read(max_bytes + 1)
                    if declared and len(body) != declared_size:
                        raise ModelImportNetworkError(
                            "ModelDB metadata transfer ended before its declared length",
                            retryable=True,
                        )
                    return body
            except HTTPError as exc:
                raise self._http_failure(exc, "ModelDB metadata request") from None
            except (TimeoutError, URLError, OSError):
                raise ModelImportNetworkError("Could not reach ModelDB securely") from None

        body = self._retry(request_json, cancel=cancel)
        if len(body) > max_bytes:
            raise ModelImportError("ModelDB metadata exceeded the response limit")
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ModelImportError("ModelDB returned invalid metadata") from exc
        if not isinstance(payload, Mapping):
            raise ModelImportError("ModelDB returned an unexpected metadata shape")
        return payload

    def _archive_info(
        self,
        accession: int,
        *,
        cancel: threading.Event | None = None,
    ) -> tuple[bool, int | None]:
        url = f"{MODELDB_ORIGIN}/download/{accession}"
        def request_info() -> tuple[bool, int | None]:
            request = Request(
                url,
                method="HEAD",
                headers={"Accept": "application/zip", "User-Agent": "Digifly-Workstation"},
            )
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
                raise self._http_failure(exc, "ModelDB archive check") from None
            except (TimeoutError, URLError, OSError):
                raise ModelImportNetworkError(
                    "Could not check the ModelDB archive securely"
                ) from None

        return self._retry(request_info, cancel=cancel)

    def lookup(
        self,
        accession: int,
        *,
        cancel: threading.Event | None = None,
    ) -> ModelDBMetadata:
        if int(accession) < 1:
            raise ValueError("ModelDB accession must be a positive integer")
        identifier = int(accession)
        payload = self._json(
            f"{MODELDB_ORIGIN}/api/v1/models/{identifier}",
            cancel=cancel,
        )
        available, size = self._archive_info(identifier, cancel=cancel)
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
        archive_url = self._require_official_url(metadata.archive_url)
        selected_output = Path(destination).expanduser()
        if selected_output.is_symlink():
            raise ModelImportError("Refusing to replace a symbolic-link ModelDB destination")
        output = selected_output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists():
            raise FileExistsError(f"ModelDB download destination already exists: {output}")
        partial, receipt = modeldb_partial_paths(output)

        def load_partial() -> dict[str, Any]:
            if not partial.exists() and not receipt.exists():
                return {}
            if partial.is_symlink() or receipt.is_symlink():
                raise ModelImportError("Refusing to resume a symbolic-link ModelDB partial")
            if not partial.is_file() or not receipt.is_file():
                raise ModelImportError(
                    "The ModelDB partial download is incomplete; discard it before retrying"
                )
            try:
                payload = json.loads(receipt.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ModelImportError(
                    "The ModelDB partial-download receipt is unreadable; discard it before retrying"
                ) from exc
            try:
                schema_version = int(payload.get("schema_version", 0))
                receipt_accession = int(payload.get("accession", 0))
            except (AttributeError, TypeError, ValueError) as exc:
                raise ModelImportError("The ModelDB partial receipt is malformed") from exc
            if (
                not isinstance(payload, dict)
                or schema_version != MODELDB_PARTIAL_SCHEMA_VERSION
                or payload.get("archive_url") != archive_url
                or receipt_accession != metadata.accession
            ):
                raise ModelImportError(
                    "The ModelDB partial download belongs to a different request"
                )
            try:
                committed = int(payload.get("transferred_bytes", -1))
                expected = int(payload.get("expected_size", 0) or 0)
            except (TypeError, ValueError) as exc:
                raise ModelImportError("The ModelDB partial receipt is malformed") from exc
            actual = partial.stat(follow_symlinks=False).st_size
            if committed < 0 or committed > actual or actual > safety.max_archive_bytes:
                raise ModelImportError("The ModelDB partial download has an invalid size")
            if actual > committed:
                flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(partial, flags)
                try:
                    details = os.fstat(descriptor)
                    if not stat.S_ISREG(details.st_mode):
                        raise ModelImportError(
                            "The ModelDB partial download is not a regular file"
                        )
                    os.ftruncate(descriptor, committed)
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            if (
                metadata.archive_size_bytes
                and expected
                and int(metadata.archive_size_bytes) != expected
            ):
                raise ModelImportError(
                    "The ModelDB archive changed since this partial download was created"
                )
            return payload

        def transfer_attempt() -> int:
            state = load_partial()
            offset = int(state.get("transferred_bytes", 0) or 0)
            headers = {
                "Accept": "application/zip",
                "User-Agent": "Digifly-Workstation",
            }
            if offset:
                headers["Range"] = f"bytes={offset}-"
                validator = str(state.get("etag") or state.get("last_modified") or "")
                if validator:
                    headers["If-Range"] = validator
            request = Request(archive_url, headers=headers)
            try:
                with self._open(request) as response:
                    self._require_official_response(response)
                    status = self._response_status(response)
                    content_type = str(
                        response.headers.get("Content-Type") or ""
                    ).casefold()
                    disposition = str(
                        response.headers.get("Content-Disposition") or ""
                    ).casefold()
                    if "zip" not in content_type and ".zip" not in disposition:
                        raise ModelImportError("ModelDB did not return a ZIP archive")
                    content_range = str(
                        response.headers.get("Content-Range") or ""
                    ).strip()
                    total_from_range = 0
                    range_end = -1
                    if status == 206:
                        matched = re.fullmatch(
                            r"bytes (\d+)-(\d+)/(\d+|\*)",
                            content_range,
                        )
                        if not matched or int(matched.group(1)) != offset:
                            raise ModelImportError(
                                "ModelDB returned an invalid resume range"
                            )
                        range_end = int(matched.group(2))
                        if range_end < offset:
                            raise ModelImportError(
                                "ModelDB returned an invalid resume range"
                            )
                        if matched.group(3) != "*":
                            total_from_range = int(matched.group(3))
                            if range_end >= total_from_range:
                                raise ModelImportError(
                                    "ModelDB returned an invalid resume range"
                                )
                    elif status == 200:
                        offset = 0
                    else:
                        raise ModelImportNetworkError(
                            f"ModelDB archive download returned HTTP {status}",
                            status=status,
                            retryable=status in {408, 425, 429, 500, 502, 503, 504},
                        )
                    declared_text = response.headers.get("Content-Length")
                    if declared_text and not str(declared_text).isdigit():
                        raise ModelImportError("ModelDB returned an invalid archive length")
                    declared = int(declared_text or 0)
                    if status == 206 and declared and range_end - offset + 1 != declared:
                        raise ModelImportError(
                            "ModelDB returned an inconsistent resume length"
                        )
                    expected = total_from_range or (
                        offset + declared if declared else int(metadata.archive_size_bytes or 0)
                    )
                    if expected > safety.max_archive_bytes:
                        raise ModelImportError(
                            "ModelDB archive exceeds the compressed-size safety limit"
                        )
                    previous_expected = int(state.get("expected_size", 0) or 0)
                    if offset and previous_expected and expected and previous_expected != expected:
                        raise ModelImportError(
                            "The ModelDB archive changed during the resumed download"
                        )
                    etag = str(response.headers.get("ETag") or state.get("etag") or "")
                    last_modified = str(
                        response.headers.get("Last-Modified")
                        or state.get("last_modified")
                        or ""
                    )
                    if offset and status == 206:
                        previous_etag = str(state.get("etag") or "")
                        previous_modified = str(state.get("last_modified") or "")
                        if previous_etag and etag and previous_etag != etag:
                            raise ModelImportError(
                                "The ModelDB archive validator changed during resume"
                            )
                        if (
                            not previous_etag
                            and previous_modified
                            and last_modified
                            and previous_modified != last_modified
                        ):
                            raise ModelImportError(
                                "The ModelDB archive validator changed during resume"
                            )
                    transferred = offset
                    response_bytes = 0
                    mode = "ab" if offset and status == 206 else "wb"
                    open_flags = os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
                    open_flags |= os.O_APPEND if mode == "ab" else os.O_CREAT | os.O_TRUNC
                    if not state and mode == "wb":
                        open_flags |= os.O_EXCL
                    descriptor = os.open(partial, open_flags, 0o600)
                    try:
                        details = os.fstat(descriptor)
                        if not stat.S_ISREG(details.st_mode):
                            raise ModelImportError(
                                "The ModelDB partial download is not a regular file"
                            )
                        if mode == "ab" and details.st_size != offset:
                            raise ModelImportError(
                                "The ModelDB partial download changed before resume"
                            )
                    except Exception:
                        os.close(descriptor)
                        raise
                    with os.fdopen(descriptor, mode) as stream:
                        checkpoint = {
                            "schema_version": MODELDB_PARTIAL_SCHEMA_VERSION,
                            "archive_url": archive_url,
                            "accession": metadata.accession,
                            "expected_size": expected,
                            "etag": etag,
                            "last_modified": last_modified,
                            "transferred_bytes": transferred,
                            "updated_at": _utc_now(),
                        }
                        _write_json_atomic(receipt, checkpoint)
                        while True:
                            _check_cancel(cancel)
                            block = response.read(1024 * 1024)
                            if not block:
                                break
                            stream.write(block)
                            stream.flush()
                            os.fsync(stream.fileno())
                            transferred += len(block)
                            response_bytes += len(block)
                            if transferred > safety.max_archive_bytes:
                                raise ModelImportError(
                                    "ModelDB archive exceeded the compressed-size safety limit"
                                )
                            checkpoint["transferred_bytes"] = transferred
                            checkpoint["updated_at"] = _utc_now()
                            _write_json_atomic(receipt, checkpoint)
                            if progress is not None:
                                progress(
                                    ModelImportProgress(
                                        "Downloading ModelDB archive",
                                        transferred,
                                        expected or transferred,
                                        transferred,
                                    )
                                )
                    if declared and response_bytes != declared:
                        raise ModelImportNetworkError(
                            "ModelDB archive transfer ended before its declared length",
                            retryable=True,
                        )
                    if expected and transferred != expected:
                        raise ModelImportNetworkError(
                            "ModelDB archive transfer is incomplete",
                            retryable=True,
                        )
                    return transferred
            except HTTPError as exc:
                raise self._http_failure(exc, "ModelDB archive download") from None
            except ModelImportError:
                raise
            except (TimeoutError, URLError, OSError):
                raise ModelImportNetworkError(
                    "Could not complete the ModelDB archive download securely"
                ) from None

        transferred = self._retry(transfer_attempt, cancel=cancel)
        if transferred < 4 or not zipfile.is_zipfile(partial):
            discard_modeldb_partial(output)
            raise ModelImportError("ModelDB did not return a complete ZIP archive")
        try:
            os.link(partial, output, follow_symlinks=False)
        except FileExistsError:
            raise FileExistsError(
                f"ModelDB download destination already exists: {output}"
            ) from None
        except OSError as exc:
            raise ModelImportError("Could not promote the verified ModelDB archive") from exc
        receipt.unlink()
        partial.unlink()
        return output


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
