from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QProcess

from digifly_app.ui import experiment_builder


def test_cancellation_marker_is_atomic_and_scoped_to_unique_run(tmp_path: Path):
    first_run = tmp_path / "first-run"
    second_run = tmp_path / "second-run"
    first_run.mkdir()
    second_run.mkdir()

    marker = experiment_builder._write_cancellation_request(first_run)

    assert marker == first_run / experiment_builder.CANCEL_REQUEST_FILENAME
    assert json.loads(marker.read_text(encoding="utf-8")) == {
        "schema_version": 1,
        "state": "cancel_requested",
    }
    assert not (first_run / ".cancel.requested.tmp").exists()
    assert not (second_run / experiment_builder.CANCEL_REQUEST_FILENAME).exists()


def test_cancel_termination_has_guarded_force_kill_fallback(monkeypatch):
    scheduled: list[tuple[int, object]] = []

    class FakeTimer:
        @staticmethod
        def singleShot(delay_ms, callback):
            scheduled.append((int(delay_ms), callback))

    class FakeProcess:
        def __init__(self):
            self.running = True
            self.terminate_calls = 0
            self.kill_calls = 0

        def state(self):
            return (
                QProcess.ProcessState.Running
                if self.running
                else QProcess.ProcessState.NotRunning
            )

        def terminate(self):
            self.terminate_calls += 1

        def kill(self):
            self.kill_calls += 1
            self.running = False

    class FakeLog:
        def __init__(self):
            self.messages: list[str] = []

        def appendPlainText(self, message):
            self.messages.append(str(message))

    class FakePage:
        def __init__(self, process):
            self._process = process
            self.run_log = FakeLog()

        def _kill_cancelled_process(self, process):
            experiment_builder.ExperimentBuilderPage._kill_cancelled_process(
                self, process
            )

    monkeypatch.setattr(experiment_builder, "QTimer", FakeTimer)
    process = FakeProcess()
    page = FakePage(process)

    experiment_builder.ExperimentBuilderPage._terminate_cancelled_process(
        page, process
    )

    assert process.terminate_calls == 1
    assert scheduled[0][0] == experiment_builder.CANCEL_FORCE_KILL_MS
    scheduled[0][1]()
    assert process.kill_calls == 1
    assert "forcing worker shutdown" in page.run_log.messages[-1]

    replacement = FakeProcess()
    stale = FakeProcess()
    page._process = stale
    experiment_builder.ExperimentBuilderPage._terminate_cancelled_process(page, stale)
    stale_callback = scheduled[-1][1]
    page._process = replacement
    stale_callback()
    assert stale.kill_calls == 0
