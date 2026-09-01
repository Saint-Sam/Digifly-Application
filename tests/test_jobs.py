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


def test_job_store_finds_saved_experiment_names_without_scanning_datasets(tmp_path):
    job = tmp_path / "jobs" / "20260901_120000_experiment_builder_v1"
    job.mkdir(parents=True)
    (job / "request.json").write_text(
        json.dumps({"experiment": {"name": "Wing steering response"}}),
        encoding="utf-8",
    )
    run = tmp_path / "experiments" / "wing-steering" / "run-002"
    run.mkdir(parents=True)
    (run / "experiment.json").write_text(
        json.dumps({"name": "Different experiment"}),
        encoding="utf-8",
    )
    unrelated = tmp_path / "managed-data" / "request.json"
    unrelated.parent.mkdir()
    unrelated.write_text(
        json.dumps({"experiment": {"name": "Wing steering response"}}),
        encoding="utf-8",
    )

    assert JobStore(tmp_path).matching_experiment_runs(
        "  wing   STEERING response "
    ) == (job.resolve(),)
    assert JobStore(tmp_path).matching_experiment_runs("new name") == ()
