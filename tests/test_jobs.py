from __future__ import annotations

import json

from digifly_app.core.jobs import JobStore
from digifly_app.core.models import ExecutionPlan, PreflightReport


def test_job_store_persists_reproducibility_bundle(tmp_path):
    plan = ExecutionPlan(
        engine="neuron",
        workflow="escape_siz_test",
        program="/usr/bin/python3",
        arguments=("runner.py", "--dry-run"),
        working_directory=str(tmp_path),
    )
    job = JobStore(tmp_path).create(plan, PreflightReport(tuple()), {"recipe": 1})
    assert json.loads((job / "resolved_plan.json").read_text())["arguments"] == [
        "runner.py",
        "--dry-run",
    ]
    assert json.loads((job / "status.json").read_text())["state"] == "queued"
    assert (job / "events.jsonl").is_file()
