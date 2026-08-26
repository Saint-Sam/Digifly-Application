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


PROFILE_SCHEMA_VERSION = 1
PROFILE_PATH_ENV = "DIGIFLY_WORKSTATION_PROFILE"
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


class ResourceKind(str, Enum):
    DIGIFLY_WORKSPACE = "digifly_workspace"
    MORPHOLOGY_SOURCE = "morphology_source"
    CONNECTOME_SOURCE = "connectome_source"
    NEURON_RUNTIME = "neuron_runtime"
    ARBOR_RUNTIME = "arbor_runtime"
    BMTK_RUNTIME = "bmtk_runtime"
    VND_VIEWER = "vnd_viewer"
    OUTPUT_ROOT = "output_root"


class AccessMode(str, Enum):
    READ_ONLY = "read_only"
    EXECUTABLE = "executable"
    READ_WRITE = "read_write"


_EXPECTED_ACCESS = {
    ResourceKind.DIGIFLY_WORKSPACE: AccessMode.READ_ONLY,
    ResourceKind.MORPHOLOGY_SOURCE: AccessMode.READ_ONLY,
    ResourceKind.CONNECTOME_SOURCE: AccessMode.READ_ONLY,
    ResourceKind.NEURON_RUNTIME: AccessMode.EXECUTABLE,
    ResourceKind.ARBOR_RUNTIME: AccessMode.EXECUTABLE,
    ResourceKind.BMTK_RUNTIME: AccessMode.EXECUTABLE,
    ResourceKind.VND_VIEWER: AccessMode.READ_ONLY,
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
            if binding.kind == ResourceKind.OUTPUT_ROOT:
                exists = path.is_dir() or (not path.exists() and path.parent.is_dir())
                detail = (
                    "Writable output directory is available."
                    if path.is_dir()
                    else "Output directory can be created beneath its existing parent."
                    if exists
                    else "Output directory and its parent do not exist."
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

        output = self.output_root
        for binding in self.resources:
            if binding.access != AccessMode.READ_ONLY:
                continue
            source = binding.resolved_path
            try:
                output.relative_to(source)
            except ValueError:
                continue
            checks.append(
                ResourceCheck(
                    "output-boundary",
                    False,
                    True,
                    f"Output root is inside read-only resource {binding.resource_id}.",
                    str(output),
                )
            )
        return ResourceProfileReport(tuple(checks))


def default_profile_path() -> Path:
    override = os.environ.get(PROFILE_PATH_ENV, "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / "Digifly Workstation Workspace" / "config" / "resources-v1.json"


def load_default_profile() -> ResourceProfile | None:
    path = default_profile_path()
    return ResourceProfile.load(path) if path.is_file() else None


def make_default_profile(
    *,
    workspace_root: str | Path,
    output_root: str | Path,
    neuron_runtime: str | Path | None = None,
    arbor_runtime: str | Path | None = None,
    bmtk_runtime: str | Path | None = None,
    vnd_viewer: str | Path | None = None,
    morphology_sources: Iterable[tuple[str, str | Path, str]] = (),
) -> ResourceProfile:
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
            str(Path(output_root).expanduser()),
            AccessMode.READ_WRITE,
            "Digifly Workstation output",
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
