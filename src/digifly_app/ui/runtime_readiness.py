"""Read-only runtime checks outside the GUI thread; never starts a model."""
from __future__ import annotations

from datetime import datetime, timezone
from PySide6.QtCore import QThread, Signal

from digifly_app.core.resources import capture_resources
from digifly_app.core.workspace import DigiflyWorkspace, MAX_BIONET_READINESS_SECONDS


class WorkspaceReadinessWorker(QThread):
    succeeded = Signal(object)
    failed = Signal(object)

    def __init__(self, snapshot: tuple[str, ...], parent=None):
        super().__init__(parent)
        self.snapshot = snapshot

    def run(self) -> None:
        try:
            root, output, neuron, arbor, bionet, netpyne = self.snapshot
            workspace = DigiflyWorkspace(root)
            base = workspace.base_preflight()
            probes = workspace.probe_engines(
                neuron, arbor, bionet, netpyne,
                bionet_timeout=MAX_BIONET_READINESS_SECONDS,
            )
            self.succeeded.emit({
                "snapshot": self.snapshot, "base": base, "probes": probes,
                "resources": capture_resources(output),
                "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "bionet_timeout": MAX_BIONET_READINESS_SECONDS,
            })
        except Exception as exc:
            self.failed.emit({"snapshot": self.snapshot, "error": str(exc)})
