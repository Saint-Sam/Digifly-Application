from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Generic, TypeVar

from digifly_app.core.models import ExecutionPlan, PreflightReport, ResultRecord
from digifly_app.core.workspace import DigiflyWorkspace


ConfigT = TypeVar("ConfigT")


class EngineAdapter(ABC, Generic[ConfigT]):
    """Planning boundary between the GUI and a scientific runtime."""

    key: str
    display_name: str

    def __init__(self, workspace: DigiflyWorkspace):
        self.workspace = workspace

    @abstractmethod
    def validate(
        self,
        config: ConfigT,
        *,
        output_root: str | Path,
        allow_new_cache_build: bool,
        legacy_write_acknowledged: bool,
    ) -> PreflightReport:
        raise NotImplementedError

    @abstractmethod
    def plan(self, config: ConfigT, *, output_root: str | Path) -> ExecutionPlan:
        raise NotImplementedError

    @abstractmethod
    def latest_result(
        self,
        config: ConfigT | None = None,
        *,
        output_root: str | Path | None = None,
    ) -> ResultRecord | None:
        raise NotImplementedError
