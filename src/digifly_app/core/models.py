from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence


class CheckState(str, Enum):
    PASS = "pass"
    WARNING = "warning"
    FAIL = "fail"
    INFO = "info"


@dataclass(frozen=True)
class PreflightCheck:
    key: str
    title: str
    state: CheckState
    detail: str
    blocking: bool = False
    path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["state"] = self.state.value
        return payload


@dataclass(frozen=True)
class PreflightReport:
    checks: tuple[PreflightCheck, ...]

    @property
    def ok(self) -> bool:
        return not any(check.blocking and check.state == CheckState.FAIL for check in self.checks)

    @property
    def failures(self) -> tuple[PreflightCheck, ...]:
        return tuple(check for check in self.checks if check.state == CheckState.FAIL)

    @property
    def warnings(self) -> tuple[PreflightCheck, ...]:
        return tuple(check for check in self.checks if check.state == CheckState.WARNING)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "checks": [check.to_dict() for check in self.checks]}


@dataclass(frozen=True)
class ExecutionPlan:
    engine: str
    workflow: str
    program: str
    arguments: tuple[str, ...]
    working_directory: str
    environment: Mapping[str, str] = field(default_factory=dict)
    output_behavior: str = "app_owned"
    expected_summary_path: str | None = None
    build_time_fields: tuple[str, ...] = field(default_factory=tuple)
    runtime_safe_fields: tuple[str, ...] = field(default_factory=tuple)

    @property
    def display_command(self) -> str:
        import shlex

        return shlex.join((self.program, *self.arguments))

    def to_dict(self) -> dict[str, Any]:
        return {
            "engine": self.engine,
            "workflow": self.workflow,
            "program": self.program,
            "arguments": list(self.arguments),
            "working_directory": self.working_directory,
            "environment": dict(self.environment),
            "output_behavior": self.output_behavior,
            "expected_summary_path": self.expected_summary_path,
            "build_time_fields": list(self.build_time_fields),
            "runtime_safe_fields": list(self.runtime_safe_fields),
        }


@dataclass(frozen=True)
class Artifact:
    kind: str
    path: str
    label: str
    exists: bool


@dataclass(frozen=True)
class ResultRecord:
    summary_path: str
    status: str
    completed_at: str
    title: str
    metadata: Mapping[str, Any]
    artifacts: tuple[Artifact, ...]
    checks: tuple[PreflightCheck, ...] = field(default_factory=tuple)

    @property
    def primary_image(self) -> Path | None:
        for artifact in self.artifacts:
            if artifact.kind == "image" and artifact.exists:
                return Path(artifact.path)
        return None


@dataclass(frozen=True)
class EngineProbe:
    key: str
    name: str
    source_state: CheckState
    runtime_state: CheckState
    summary: str
    details: tuple[str, ...] = field(default_factory=tuple)


def deep_find_values(value: Any, key: str) -> list[Any]:
    """Return values for *key* found anywhere in a JSON-like object."""
    found: list[Any] = []
    if isinstance(value, Mapping):
        for child_key, child_value in value.items():
            if child_key == key:
                found.append(child_value)
            found.extend(deep_find_values(child_value, key))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in value:
            found.extend(deep_find_values(child, key))
    return found
