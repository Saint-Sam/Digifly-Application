from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping


PROJECT_SCHEMA_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class DigiflyProject:
    name: str
    digifly_public_root: str
    output_root: str
    python_executable: str = "/opt/anaconda3/bin/python"
    selected_engine: str = "neuron"
    selected_workflow: str = "escape_siz_gfc_contact_na"
    experiment: dict[str, Any] = field(default_factory=dict)
    notes: str = ""
    schema_version: int = PROJECT_SCHEMA_VERSION
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["updated_at"] = _now()
        return payload

    def save(self, path: str | Path) -> Path:
        destination = Path(path).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return destination

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "DigiflyProject":
        version = int(payload.get("schema_version", 0))
        if version != PROJECT_SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported Digifly project schema {version}; expected {PROJECT_SCHEMA_VERSION}."
            )
        required = ("name", "digifly_public_root", "output_root")
        missing = [key for key in required if not str(payload.get(key, "")).strip()]
        if missing:
            raise ValueError(f"Project is missing required fields: {', '.join(missing)}")
        return cls(
            name=str(payload["name"]),
            digifly_public_root=str(payload["digifly_public_root"]),
            output_root=str(payload["output_root"]),
            python_executable=str(payload.get("python_executable") or "/opt/anaconda3/bin/python"),
            selected_engine=str(payload.get("selected_engine") or "neuron"),
            selected_workflow=str(payload.get("selected_workflow") or "escape_siz_gfc_contact_na"),
            experiment=dict(payload.get("experiment") or {}),
            notes=str(payload.get("notes") or ""),
            schema_version=version,
            created_at=str(payload.get("created_at") or _now()),
            updated_at=str(payload.get("updated_at") or _now()),
        )

    @classmethod
    def load(cls, path: str | Path) -> "DigiflyProject":
        source = Path(path).expanduser().resolve()
        payload = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("A Digifly project file must contain a JSON object.")
        return cls.from_dict(payload)
