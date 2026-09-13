from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _launcher_module():
    path = Path(__file__).parents[1] / "scripts" / "docker_python_launcher.py"
    spec = importlib.util.spec_from_file_location("digifly_docker_launcher", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_container_worker_mounts_only_run_and_referenced_inputs(tmp_path, monkeypatch):
    launcher = _launcher_module()
    run = tmp_path / "workspace" / "runs" / "experiments" / "trial"
    run.mkdir(parents=True)
    source = tmp_path / "data" / "cell.swc"
    source.parent.mkdir()
    source.write_text("1 1 0 0 0 1 -1\n", encoding="utf-8")
    request = run / "worker_request.json"
    request.write_text(
        json.dumps({"output": str(run / "summary.json"), "morphology": str(source)}),
        encoding="utf-8",
    )
    captured = {}

    class Completed:
        returncode = 0

    def fake_run(command, **_kwargs):
        captured["command"] = command
        return Completed()

    monkeypatch.setattr(launcher, "_docker", lambda: "docker")
    monkeypatch.setattr(launcher.subprocess, "run", fake_run)

    assert launcher._worker_run(["-B", "/app/generic_experiment_worker.py", "--request", str(request)]) == 0

    command = captured["command"]
    assert command[:3] == ["docker", "run", "--rm"]
    assert ["--network", "none"] == command[command.index("--network") : command.index("--network") + 2]
    assert f"{run}:/digifly/run:rw" in command
    assert f"{source.parent}:/digifly/inputs/0000:ro" in command
    assert command[-3:] == [
        "/opt/digifly/src/digifly_app/workers/generic_experiment_worker.py",
        "--request",
        "/digifly/run/worker_request.container.json",
    ]
    rewritten = json.loads((run / "worker_request.container.json").read_text(encoding="utf-8"))
    assert rewritten["output"] == "/digifly/run/summary.json"
    assert rewritten["morphology"] == "/digifly/inputs/0000/cell.swc"
