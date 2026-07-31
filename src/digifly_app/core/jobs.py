from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping

from .models import ExecutionPlan, PreflightReport


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class JobStore:
    """File-backed job provenance independent of the simulator output tree."""

    def __init__(self, output_root: str | Path):
        self.output_root = Path(output_root).expanduser().resolve()

    def create(
        self,
        plan: ExecutionPlan,
        preflight: PreflightReport,
        request: Mapping[str, Any],
    ) -> Path:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        job_dir = self.output_root / "jobs" / f"{stamp}_{plan.workflow}"
        suffix = 1
        while job_dir.exists():
            suffix += 1
            job_dir = self.output_root / "jobs" / f"{stamp}_{plan.workflow}_{suffix}"
        job_dir.mkdir(parents=True, exist_ok=False)
        _write_json(job_dir / "request.json", dict(request))
        _write_json(job_dir / "resolved_plan.json", plan.to_dict())
        _write_json(job_dir / "preflight.json", preflight.to_dict())
        self.update_status(job_dir, "queued")
        self.append_event(job_dir, "queued", "Execution plan created.")
        return job_dir

    def update_status(
        self,
        job_dir: str | Path,
        state: str,
        **details: Any,
    ) -> None:
        payload = {"state": state, "updated_at": _now(), **details}
        _write_json(Path(job_dir) / "status.json", payload)

    def append_event(self, job_dir: str | Path, kind: str, message: str, **details: Any) -> None:
        event = {"at": _now(), "kind": kind, "message": message, **details}
        path = Path(job_dir) / "events.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True) + "\n")


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)
