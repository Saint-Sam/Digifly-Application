from __future__ import annotations

import json
from pathlib import Path

from digifly_app.core.resource_profile import ResourceKind, make_default_profile, update_runtime_bindings
from digifly_app.core.runtime_discovery import probe_simulator_runtime, runtime_candidates


def test_runtime_candidates_are_bounded_to_one_environment_level(tmp_path: Path):
    roots = tmp_path / "envs"
    python = roots / "neuron-env" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\n", encoding="utf-8")
    python.chmod(0o755)
    nested = roots / "outer" / "inner" / "bin" / "python"
    nested.parent.mkdir(parents=True)
    nested.write_text("#!/bin/sh\n", encoding="utf-8")
    nested.chmod(0o755)

    found = runtime_candidates(environment_roots=(roots,), include_path=False)

    assert python.resolve() in found
    assert nested.resolve() not in found


def test_runtime_probe_reads_package_identity_without_importing_simulators(
    tmp_path: Path, monkeypatch
):
    executable = tmp_path / "python"
    executable.write_text("", encoding="utf-8")
    payload = {
        "python_version": "3.12.4",
        "neuron": {"version": "8.2.6", "origin": "/env/neuron/__init__.py"},
        "arbor": {"version": "0.12.2", "origin": "/env/arbor/__init__.py"},
    }

    class Completed:
        returncode = 0
        stdout = json.dumps(payload)
        stderr = ""

    called = {}

    def fake_run(command, **kwargs):
        called["command"] = command
        called["kwargs"] = kwargs
        return Completed()

    monkeypatch.setattr("digifly_app.core.runtime_discovery.subprocess.run", fake_run)
    result = probe_simulator_runtime(executable)

    assert result.has_neuron and result.has_arbor
    assert result.neuron_version == "8.2.6"
    assert result.arbor_version == "0.12.2"
    assert "import neuron" not in called["command"][2]
    assert "import arbor" not in called["command"][2]
    assert called["kwargs"]["env"]["PYTHONNOUSERSITE"] == "1"


def test_verified_runtime_choices_replace_profile_bindings(tmp_path: Path):
    workspace = tmp_path / "public"
    workspace.mkdir()
    (workspace / "README.md").write_text("Digifly Public\n", encoding="utf-8")
    profile = make_default_profile(
        workspace_root=workspace,
        output_root=tmp_path / "runs",
        neuron_runtime="/old/neuron/python",
        arbor_runtime="/old/arbor/python",
    )

    updated = update_runtime_bindings(
        profile,
        neuron_runtime="/new/neuron/python",
        arbor_runtime="/new/arbor/python",
    )

    assert str(updated.runtime_path(ResourceKind.NEURON_RUNTIME)) == "/new/neuron/python"
    assert str(updated.runtime_path(ResourceKind.ARBOR_RUNTIME)) == "/new/arbor/python"
    assert len(updated.bindings(ResourceKind.NEURON_RUNTIME)) == 1
    assert len(updated.bindings(ResourceKind.ARBOR_RUNTIME)) == 1
