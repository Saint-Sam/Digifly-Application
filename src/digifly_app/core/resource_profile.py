"""Versioned machine-local bindings for external scientific resources."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Iterable, Mapping


PROFILE_SCHEMA_VERSION = 2
LEGACY_PROFILE_SCHEMA_VERSION = 1
PROFILE_PATH_ENV = "DIGIFLY_WORKSTATION_PROFILE"
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


class ResourceKind(str, Enum):
    DIGIFLY_WORKSPACE = "digifly_workspace"
    MORPHOLOGY_SOURCE = "morphology_source"
    CONNECTOME_SOURCE = "connectome_source"
    DATA_SOURCE = "data_source"
    MODEL_SOURCE = "model_source"
    NEURON_RUNTIME = "neuron_runtime"
    ARBOR_RUNTIME = "arbor_runtime"
    BMTK_RUNTIME = "bmtk_runtime"
    VND_VIEWER = "vnd_viewer"
    MANAGED_DATA_ROOT = "managed_data_root"
    OUTPUT_ROOT = "output_root"


class AccessMode(str, Enum):
    READ_ONLY = "read_only"
    EXECUTABLE = "executable"
    READ_WRITE = "read_write"


_EXPECTED_ACCESS = {
    ResourceKind.DIGIFLY_WORKSPACE: AccessMode.READ_ONLY,
    ResourceKind.MORPHOLOGY_SOURCE: AccessMode.READ_ONLY,
    ResourceKind.CONNECTOME_SOURCE: AccessMode.READ_ONLY,
    ResourceKind.DATA_SOURCE: AccessMode.READ_ONLY,
    ResourceKind.MODEL_SOURCE: AccessMode.READ_ONLY,
    ResourceKind.NEURON_RUNTIME: AccessMode.EXECUTABLE,
    ResourceKind.ARBOR_RUNTIME: AccessMode.EXECUTABLE,
    ResourceKind.BMTK_RUNTIME: AccessMode.EXECUTABLE,
    ResourceKind.VND_VIEWER: AccessMode.READ_ONLY,
    ResourceKind.MANAGED_DATA_ROOT: AccessMode.READ_WRITE,
    ResourceKind.OUTPUT_ROOT: AccessMode.READ_WRITE,
}


@dataclass(frozen=True)
class ResourceBinding:
    resource_id: str
    kind: ResourceKind
    path: str
    access: AccessMode
    label: str = ""
    required: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _IDENTIFIER.fullmatch(self.resource_id):
            raise ValueError(f"Invalid resource identifier: {self.resource_id}")
        if not str(self.path).strip():
            raise ValueError(f"Resource {self.resource_id} has an empty path")
        if self.access != _EXPECTED_ACCESS[self.kind]:
            raise ValueError(
                f"Resource {self.resource_id} kind {self.kind.value} requires "
                f"{_EXPECTED_ACCESS[self.kind].value} access"
            )

    @property
    def resolved_path(self) -> Path:
        return Path(self.path).expanduser().resolve()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["kind"] = self.kind.value
        payload["access"] = self.access.value
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ResourceBinding":
        return cls(
            resource_id=str(payload.get("resource_id") or ""),
            kind=ResourceKind(str(payload.get("kind") or "")),
            path=str(payload.get("path") or ""),
            access=AccessMode(str(payload.get("access") or "")),
            label=str(payload.get("label") or ""),
            required=bool(payload.get("required", True)),
            metadata=dict(payload.get("metadata") or {}),
        )


@dataclass(frozen=True)
class ResourceCheck:
    resource_id: str
    ok: bool
    blocking: bool
    detail: str
    path: str = ""


@dataclass(frozen=True)
class ResourceProfileReport:
    checks: tuple[ResourceCheck, ...]

    @property
    def ok(self) -> bool:
        return all(check.ok or not check.blocking for check in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "checks": [asdict(check) for check in self.checks]}


@dataclass(frozen=True)
class ResourceProfile:
    profile_id: str
    resources: tuple[ResourceBinding, ...]
    label: str = ""
    schema_version: int = PROFILE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != PROFILE_SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported resource profile schema {self.schema_version}; "
                f"expected {PROFILE_SCHEMA_VERSION}"
            )
        if not _IDENTIFIER.fullmatch(self.profile_id):
            raise ValueError(f"Invalid profile identifier: {self.profile_id}")
        identifiers = [binding.resource_id for binding in self.resources]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Resource identifiers must be unique within a profile")
        if len(self.bindings(ResourceKind.DIGIFLY_WORKSPACE)) != 1:
            raise ValueError("A resource profile requires exactly one Digifly workspace")
        if len(self.bindings(ResourceKind.OUTPUT_ROOT)) != 1:
            raise ValueError("A resource profile requires exactly one output root")
        if len(self.bindings(ResourceKind.MANAGED_DATA_ROOT)) != 1:
            raise ValueError("A resource profile requires exactly one managed data root")
        for kind in (
            ResourceKind.NEURON_RUNTIME,
            ResourceKind.ARBOR_RUNTIME,
            ResourceKind.BMTK_RUNTIME,
            ResourceKind.VND_VIEWER,
        ):
            if len(self.bindings(kind)) > 1:
                raise ValueError(f"A resource profile permits at most one {kind.value} binding")

    def bindings(self, kind: ResourceKind) -> tuple[ResourceBinding, ...]:
        return tuple(binding for binding in self.resources if binding.kind == kind)

    def binding(self, kind: ResourceKind) -> ResourceBinding | None:
        matches = self.bindings(kind)
        return matches[0] if matches else None

    @property
    def workspace_root(self) -> Path:
        binding = self.binding(ResourceKind.DIGIFLY_WORKSPACE)
        assert binding is not None
        return binding.resolved_path

    @property
    def output_root(self) -> Path:
        binding = self.binding(ResourceKind.OUTPUT_ROOT)
        assert binding is not None
        return binding.resolved_path

    @property
    def managed_data_root(self) -> Path:
        binding = self.binding(ResourceKind.MANAGED_DATA_ROOT)
        assert binding is not None
        return binding.resolved_path

    def runtime_path(self, kind: ResourceKind) -> Path | None:
        if kind not in {
            ResourceKind.NEURON_RUNTIME,
            ResourceKind.ARBOR_RUNTIME,
            ResourceKind.BMTK_RUNTIME,
        }:
            raise ValueError(f"{kind.value} is not a runtime binding")
        binding = self.binding(kind)
        return binding.resolved_path if binding is not None else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "profile_id": self.profile_id,
            "label": self.label,
            "resources": [binding.to_dict() for binding in self.resources],
        }

    @property
    def fingerprint(self) -> str:
        canonical = json.dumps(
            self.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ResourceProfile":
        payload = migrate_profile_payload(payload)
        resources = payload.get("resources")
        if not isinstance(resources, list):
            raise ValueError("Resource profile resources must be a list")
        return cls(
            profile_id=str(payload.get("profile_id") or ""),
            label=str(payload.get("label") or ""),
            schema_version=int(payload.get("schema_version", 0)),
            resources=tuple(ResourceBinding.from_dict(item) for item in resources),
        )

    @classmethod
    def load(cls, path: str | Path) -> "ResourceProfile":
        source = Path(path).expanduser().resolve()
        payload = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("A resource profile must contain a JSON object")
        return cls.from_dict(payload)

    def save(self, path: str | Path, *, replace: bool = False) -> Path:
        destination = Path(path).expanduser().resolve()
        if destination.exists() and not replace:
            raise FileExistsError(f"Resource profile already exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temporary_path = Path(stream.name)
                json.dump(self.to_dict(), stream, indent=2, sort_keys=True)
                stream.write("\n")
            os.replace(temporary_path, destination)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
        return destination

    def validate(self) -> ResourceProfileReport:
        checks: list[ResourceCheck] = []
        for binding in self.resources:
            path = binding.resolved_path
            if binding.kind in {ResourceKind.OUTPUT_ROOT, ResourceKind.MANAGED_DATA_ROOT}:
                exists = (
                    path.is_dir() and os.access(path, os.W_OK)
                ) or (
                    not path.exists()
                    and path.parent.is_dir()
                    and os.access(path.parent, os.W_OK)
                )
                noun = (
                    "managed data directory"
                    if binding.kind == ResourceKind.MANAGED_DATA_ROOT
                    else "output directory"
                )
                detail = (
                    f"Writable {noun} is available."
                    if exists and path.is_dir()
                    else f"{noun.capitalize()} can be created beneath its existing parent."
                    if exists
                    else f"{noun.capitalize()} is unavailable or not writable."
                )
            elif binding.kind in {
                ResourceKind.NEURON_RUNTIME,
                ResourceKind.ARBOR_RUNTIME,
                ResourceKind.BMTK_RUNTIME,
            }:
                exists = path.is_file() and os.access(path, os.X_OK)
                detail = "Executable runtime is available." if exists else "Runtime is missing or not executable."
            elif binding.kind == ResourceKind.CONNECTOME_SOURCE:
                exists = path.is_file() or path.is_dir()
                detail = "External connectome source is available." if exists else "Connectome source is missing."
            else:
                exists = path.exists()
                detail = "External resource is available." if exists else "External resource is missing."
            if binding.kind == ResourceKind.DIGIFLY_WORKSPACE and exists:
                exists = path.is_dir() and (path / "README.md").is_file()
                detail = (
                    "Digifly workspace marker is present."
                    if exists
                    else "Directory lacks the Digifly workspace README marker."
                )
            checks.append(
                ResourceCheck(
                    binding.resource_id,
                    exists,
                    binding.required,
                    detail,
                    str(path),
                )
            )

        writable_roots = (
            ("output-boundary", "Output root", self.output_root),
            ("managed-data-boundary", "Managed data root", self.managed_data_root),
        )
        for check_id, label, writable_root in writable_roots:
            for binding in self.resources:
                if binding.access != AccessMode.READ_ONLY:
                    continue
                source = binding.resolved_path
                try:
                    writable_root.relative_to(source)
                except ValueError:
                    continue
                checks.append(
                    ResourceCheck(
                        check_id,
                        False,
                        True,
                        f"{label} is inside read-only resource {binding.resource_id}.",
                        str(writable_root),
                    )
                )
        output = self.output_root
        managed = self.managed_data_root
        if _paths_overlap(output, managed):
            checks.append(
                ResourceCheck(
                    "writable-boundary",
                    False,
                    True,
                    "Output and managed data roots must not contain one another.",
                    f"{output} | {managed}",
                )
            )
        return ResourceProfileReport(tuple(checks))


def _paths_overlap(left: Path, right: Path) -> bool:
    try:
        left.relative_to(right)
        return True
    except ValueError:
        pass
    try:
        right.relative_to(left)
        return True
    except ValueError:
        return False


def _v1_managed_data_root(payload: Mapping[str, Any]) -> Path:
    resources = payload.get("resources")
    if isinstance(resources, list):
        for item in resources:
            if not isinstance(item, Mapping) or item.get("kind") != ResourceKind.OUTPUT_ROOT.value:
                continue
            output = Path(str(item.get("path") or "")).expanduser()
            if str(output).strip():
                return output.parent / "data"
    return Path.home() / "Digifly Workstation Workspace" / "data"


def migrate_profile_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return a schema-v2 payload without moving or rewriting any bound resource."""
    migrated = dict(payload)
    version = int(migrated.get("schema_version", 0))
    if version == PROFILE_SCHEMA_VERSION:
        return migrated
    if version != LEGACY_PROFILE_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported resource profile schema {version}; expected "
            f"{LEGACY_PROFILE_SCHEMA_VERSION} or {PROFILE_SCHEMA_VERSION}"
        )
    resources = migrated.get("resources")
    if not isinstance(resources, list):
        raise ValueError("Resource profile resources must be a list")
    migrated_resources = [dict(item) for item in resources]
    migrated_resources.append(
        ResourceBinding(
            "workstation-data",
            ResourceKind.MANAGED_DATA_ROOT,
            str(_v1_managed_data_root(migrated)),
            AccessMode.READ_WRITE,
            "Digifly Workstation managed data",
        ).to_dict()
    )
    migrated["schema_version"] = PROFILE_SCHEMA_VERSION
    migrated["resources"] = migrated_resources
    return migrated


def default_profile_path() -> Path:
    override = os.environ.get(PROFILE_PATH_ENV, "").strip()
    if override:
        return Path(override).expanduser().resolve()
    config_root = Path.home() / "Digifly Workstation Workspace" / "config"
    current = config_root / "resources-v2.json"
    legacy = config_root / "resources-v1.json"
    return current if current.is_file() or not legacy.is_file() else legacy


def load_default_profile() -> ResourceProfile | None:
    path = default_profile_path()
    return ResourceProfile.load(path) if path.is_file() else None


def migrate_profile_file(
    source: str | Path,
    destination: str | Path | None = None,
    *,
    replace: bool = False,
) -> Path:
    """Write a v2 profile beside a v1 profile, preserving the source by default."""
    source_path = Path(source).expanduser().resolve()
    profile = ResourceProfile.load(source_path)
    destination_path = (
        Path(destination).expanduser().resolve()
        if destination is not None
        else source_path.with_name("resources-v2.json")
    )
    return profile.save(destination_path, replace=replace)


def update_runtime_bindings(
    profile: ResourceProfile,
    *,
    neuron_runtime: str | Path | None = None,
    arbor_runtime: str | Path | None = None,
) -> ResourceProfile:
    """Return a profile with verified external runtime choices replaced by kind."""
    replacements = {
        ResourceKind.NEURON_RUNTIME: neuron_runtime,
        ResourceKind.ARBOR_RUNTIME: arbor_runtime,
    }
    resources = list(profile.resources)
    for kind, runtime in replacements.items():
        if runtime is None:
            continue
        existing = profile.binding(kind)
        replacement = ResourceBinding(
            existing.resource_id if existing is not None else kind.value.removesuffix("_runtime"),
            kind,
            str(Path(runtime).expanduser()),
            AccessMode.EXECUTABLE,
            existing.label if existing is not None else f"{kind.value.split('_')[0].upper()} Python",
            False,
            dict(existing.metadata) if existing is not None else {},
        )
        if existing is None:
            resources.append(replacement)
        else:
            resources[resources.index(existing)] = replacement
    return ResourceProfile(profile.profile_id, tuple(resources), profile.label)


def make_default_profile(
    *,
    workspace_root: str | Path,
    output_root: str | Path,
    managed_data_root: str | Path | None = None,
    neuron_runtime: str | Path | None = None,
    arbor_runtime: str | Path | None = None,
    bmtk_runtime: str | Path | None = None,
    vnd_viewer: str | Path | None = None,
    morphology_sources: Iterable[tuple[str, str | Path, str]] = (),
) -> ResourceProfile:
    output_path = Path(output_root).expanduser()
    managed_path = (
        Path(managed_data_root).expanduser()
        if managed_data_root is not None
        else output_path.parent / "data"
    )
    resources = [
        ResourceBinding(
            "digifly-public",
            ResourceKind.DIGIFLY_WORKSPACE,
            str(Path(workspace_root).expanduser()),
            AccessMode.READ_ONLY,
            "Digifly Public",
        ),
        ResourceBinding(
            "workstation-output",
            ResourceKind.OUTPUT_ROOT,
            str(output_path),
            AccessMode.READ_WRITE,
            "Digifly Workstation output",
        ),
        ResourceBinding(
            "workstation-data",
            ResourceKind.MANAGED_DATA_ROOT,
            str(managed_path),
            AccessMode.READ_WRITE,
            "Digifly Workstation managed data",
        ),
    ]
    optional = (
        ("neuron", ResourceKind.NEURON_RUNTIME, neuron_runtime, "NEURON Python"),
        ("arbor", ResourceKind.ARBOR_RUNTIME, arbor_runtime, "Arbor Python"),
        ("bmtk", ResourceKind.BMTK_RUNTIME, bmtk_runtime, "BMTK Python"),
        ("vnd", ResourceKind.VND_VIEWER, vnd_viewer, "VND viewer"),
    )
    for resource_id, kind, path, label in optional:
        if path is None:
            continue
        access = AccessMode.EXECUTABLE if kind != ResourceKind.VND_VIEWER else AccessMode.READ_ONLY
        resources.append(ResourceBinding(resource_id, kind, str(Path(path).expanduser()), access, label, False))
    for resource_id, path, label in morphology_sources:
        resources.append(
            ResourceBinding(
                resource_id,
                ResourceKind.MORPHOLOGY_SOURCE,
                str(Path(path).expanduser()),
                AccessMode.READ_ONLY,
                label,
                False,
            )
        )
    return ResourceProfile("default", tuple(resources), "Default workstation resources")
