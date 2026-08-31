from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping


PROJECT_SCHEMA_VERSION = 2


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class DigiflyProject:
    name: str
    digifly_public_root: str
    output_root: str
    python_executable: str = "/opt/anaconda3/bin/python"
    selected_engine: str = "neuron"
    selected_workflow: str = "experiment_builder_v1"
    circuit: dict[str, Any] = field(default_factory=dict)
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
        if version not in {1, PROJECT_SCHEMA_VERSION}:
            raise ValueError(
                f"Unsupported Digifly project schema {version}; expected 1 or {PROJECT_SCHEMA_VERSION}."
            )
        required = ("name", "digifly_public_root", "output_root")
        missing = [key for key in required if not str(payload.get(key, "")).strip()]
        if missing:
            raise ValueError(f"Project is missing required fields: {', '.join(missing)}")
        selected_workflow = str(
            payload.get("selected_workflow") or "escape_siz_gfc_contact_na"
        )
        legacy_payload = dict(payload.get("experiment") or {})
        circuit = dict(payload.get("circuit") or {})
        experiment = legacy_payload
        if version == 1 and selected_workflow == "circuit_builder_v1":
            circuit = legacy_payload
            experiment = {}
        elif version == 1 and selected_workflow == "escape_siz_gfc_contact_na":
            from .experiment import ExperimentSpec

            migrated = ExperimentSpec.pulse_train_comparison().to_dict()
            migrated["engine"] = str(payload.get("selected_engine") or "neuron")
            migrated["name"] = str(payload.get("name") or "Migrated experiment")
            stimulus = dict(migrated["stimuli"][0])
            stimulus["frequency_hz"] = float(legacy_payload.get("frequency_hz", 100.0))
            stimulus["pulse_count"] = int(legacy_payload.get("max_pulses", 10))
            stimulus["amplitude_nA"] = float(
                legacy_payload.get("gap_enabled_amp_nA", 0.9)
            )
            migrated["stimuli"] = [stimulus]
            migrated["workers"] = int(legacy_payload.get("nproc", 1))
            experiment = migrated
            selected_workflow = "experiment_builder_v1"
        return cls(
            name=str(payload["name"]),
            digifly_public_root=str(payload["digifly_public_root"]),
            output_root=str(payload["output_root"]),
            python_executable=str(payload.get("python_executable") or "/opt/anaconda3/bin/python"),
            selected_engine=str(payload.get("selected_engine") or "neuron"),
            selected_workflow=selected_workflow,
            circuit=circuit,
            experiment=experiment,
            notes=str(payload.get("notes") or ""),
            schema_version=PROJECT_SCHEMA_VERSION,
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
