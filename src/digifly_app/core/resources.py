from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil


@dataclass(frozen=True)
class ResourceSnapshot:
    logical_cores: int
    total_memory_gb: float | None
    available_memory_gb: float | None
    disk_total_gb: float
    disk_free_gb: float
    disk_used_percent: float
    neuron_worker_default: int
    note: str


def _gb(value: int) -> float:
    return round(value / (1024**3), 2)


def capture_resources(path: str | Path) -> ResourceSnapshot:
    target = Path(path).expanduser()
    disk_target = target if target.exists() else target.parent
    while not disk_target.exists() and disk_target != disk_target.parent:
        disk_target = disk_target.parent
    usage = shutil.disk_usage(disk_target)
    total_memory: float | None = None
    available_memory: float | None = None
    try:
        import psutil  # type: ignore

        memory = psutil.virtual_memory()
        total_memory = _gb(int(memory.total))
        available_memory = _gb(int(memory.available))
    except (ImportError, OSError):
        pass
    cores = max(1, int(os.cpu_count() or 1))
    # Escape-SIZ's handoff explicitly identifies safe4 cache families and warns
    # that worker count is constrained by model memory, not CPU availability.
    worker_default = min(4, cores)
    used_percent = round(100.0 * (usage.total - usage.free) / usage.total, 1)
    note = (
        f"{cores} logical cores detected. Escape-SIZ defaults to at most "
        f"{worker_default} workers because each NEURON process can be memory-heavy."
    )
    return ResourceSnapshot(
        logical_cores=cores,
        total_memory_gb=total_memory,
        available_memory_gb=available_memory,
        disk_total_gb=_gb(usage.total),
        disk_free_gb=_gb(usage.free),
        disk_used_percent=used_percent,
        neuron_worker_default=worker_default,
        note=note,
    )
