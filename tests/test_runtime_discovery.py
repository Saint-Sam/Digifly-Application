from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from digifly_app.core import workspace as workspace_module
from digifly_app.core.models import CheckState
from digifly_app.core.resource_profile import ResourceKind, make_default_profile, update_runtime_bindings
from digifly_app.core.process_environment import (
    external_runtime_path,
    sanitized_external_environment,
)
from digifly_app.core.runtime_discovery import (
    SimulatorRuntime,
    discover_simulator_runtimes,
    probe_simulator_runtime,
    runtime_candidates,
)
from digifly_app.core.workspace import DigiflyWorkspace


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


def test_runtime_candidates_preserve_distinct_venv_launchers_without_python3_duplicates(
    tmp_path: Path,
):
    base_python = tmp_path / "base" / "python3.12"
    base_python.parent.mkdir()
    base_python.write_text("#!/bin/sh\n", encoding="utf-8")
    base_python.chmod(0o755)
    roots = tmp_path / "envs"
    first = roots / "first" / "bin" / "python"
    second = roots / "second" / "bin" / "python"
    for launcher in (first, second):
        launcher.parent.mkdir(parents=True)
        launcher.symlink_to(base_python)
        (launcher.parent / "python3").symlink_to(base_python)

    found = runtime_candidates(environment_roots=(roots,), include_path=False)

    assert first.absolute() in found
    assert second.absolute() in found
    assert first.resolve() not in {first.absolute(), second.absolute()}
    assert sum(path.parent == first.parent for path in found) == 1
    assert sum(path.parent == second.parent for path in found) == 1


def test_external_runtime_environment_removes_standalone_neuron_redirects():
    cleaned = sanitized_external_environment(
        {},
        inherited={
            "PATH": "/selected/bin:/usr/bin",
            "NEURONHOME": "/Applications/NEURON",
            "NRNHOME": "/Applications/NEURON/nrn",
            "CORENRNHOME": "/Applications/NEURON/coreneuron",
            "NRN_PYTHONEXE": "/wrong/python",
            "NRNIVMODL": "/wrong/nrnivmodl",
            "NMODLHOME": "/wrong/nmodl",
        },
    )

    assert cleaned == {"PATH": "/selected/bin:/usr/bin"}


def test_runtime_probe_reads_identity_and_bionet_capability_in_child_process(
    tmp_path: Path, monkeypatch
):
    executable = tmp_path / "python"
    executable.write_text("", encoding="utf-8")
    executable.chmod(0o755)
    payload = {
        "python_version": "3.12.4",
        "neuron": {"version": "8.2.6", "origin": "/env/neuron/__init__.py"},
        "arbor": {"version": "0.12.2", "origin": "/env/arbor/__init__.py"},
        "bmtk": {"version": "1.2.0", "origin": "/env/bmtk/__init__.py"},
        "bionet": {
            "ready": True,
            "origin": "/env/bmtk/simulator/bionet/__init__.py",
            "error": "",
        },
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
    assert result.has_bmtk and result.bionet_ready
    assert result.neuron_version == "8.2.6"
    assert result.arbor_version == "0.12.2"
    assert result.bmtk_version == "1.2.0"
    assert result.bionet_origin.endswith("bionet/__init__.py")
    assert "import neuron" not in called["command"][2]
    assert "import arbor" not in called["command"][2]
    assert "importlib.import_module('bmtk.simulator.bionet')" in called["command"][2]
    assert "importlib.import_module('neuron')" in called["command"][2]
    assert "importlib.import_module('numpy')" in called["command"][2]
    assert "importlib.import_module('h5py')" in called["command"][2]
    assert "sys.base_prefix" in called["command"][2]
    assert "is_relative_to" in called["command"][2]
    assert called["kwargs"]["env"]["PYTHONNOUSERSITE"] == "1"
    assert called["kwargs"]["env"]["NEURON_MODULE_OPTIONS"] == "-nogui"
    assert "DISPLAY" not in called["kwargs"]["env"]


def test_runtime_probe_and_path_keep_selected_venv_launcher(tmp_path: Path, monkeypatch):
    base_python = tmp_path / "base" / "python3.12"
    base_python.parent.mkdir()
    base_python.symlink_to(Path(sys.executable))
    launcher = tmp_path / "bmtk-env" / "bin" / "python"
    launcher.parent.mkdir(parents=True)
    launcher.symlink_to(base_python)
    called = {}

    class Completed:
        returncode = 0
        stdout = json.dumps(
            {
                "python_version": "3.11.15",
                "neuron": {"version": "9.0.1", "origin": "/env/neuron.py"},
                "arbor": {"version": "", "origin": ""},
                "bmtk": {"version": "1.2.0", "origin": "/env/bmtk.py"},
                "bionet": {"ready": True, "origin": "/env/bionet.py", "error": ""},
            }
        )
        stderr = ""

    def fake_run(command, **kwargs):
        called["command"] = command
        return Completed()

    monkeypatch.setattr("digifly_app.core.runtime_discovery.subprocess.run", fake_run)

    result = probe_simulator_runtime(launcher)

    assert result.python == launcher.absolute()
    assert result.python != launcher.resolve()
    assert called["command"][0] == str(launcher.absolute())
    assert external_runtime_path(launcher).split(":", 1)[0] == str(launcher.parent)


def test_discovery_retains_bmtk_installation_when_bionet_is_blocked(
    tmp_path: Path, monkeypatch
):
    executable = tmp_path / "python"
    executable.write_text("", encoding="utf-8")
    blocked = SimulatorRuntime(
        python=executable,
        python_version="3.11.15",
        bmtk_version="1.2.0",
        bmtk_origin="/env/bmtk/__init__.py",
        bionet_ready=False,
        bionet_error="ModuleNotFoundError: No module named 'neuron'",
    )
    monkeypatch.setattr(
        "digifly_app.core.runtime_discovery.runtime_candidates",
        lambda **_kwargs: (executable,),
    )
    monkeypatch.setattr(
        "digifly_app.core.runtime_discovery.probe_simulator_runtime",
        lambda _path: blocked,
    )

    discovered = discover_simulator_runtimes(include_path=False)

    assert discovered == (blocked,)
    assert discovered[0].has_bmtk
    assert not discovered[0].bionet_ready
    assert "No module named 'neuron'" in discovered[0].bionet_error


def test_workspace_does_not_probe_unselected_legacy_bmtk_runtime(
    tmp_path: Path, monkeypatch
):
    public = tmp_path / "Digifly Public"
    public.mkdir()
    (public / "README.md").write_text("Digifly Public\n", encoding="utf-8")
    python = tmp_path / "python"
    python.write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setattr(
        "digifly_app.core.workspace._probe_neuron_runtime",
        lambda *_args: (None, "not relevant"),
    )
    monkeypatch.setattr(
        "digifly_app.core.workspace._probe_python_module",
        lambda *_args, **_kwargs: (None, "not relevant"),
    )

    def unexpected_probe(_python):
        raise AssertionError("an unselected BMTK runtime must not be executed")

    monkeypatch.setattr(
        "digifly_app.core.workspace._probe_bionet_runtime",
        unexpected_probe,
    )

    probes = DigiflyWorkspace(public).probe_engines(str(python), str(python), "")

    bmtk = next(probe for probe in probes if probe.key == "bmtk")
    assert "No BMTK BioNet interpreter was selected" in bmtk.details[2]


def test_clean_workspace_neuron_doctor_uses_packaged_sources_not_legacy_phase2(
    tmp_path: Path, monkeypatch
):
    public = tmp_path / "Digifly Public"
    public.mkdir()
    (public / "README.md").write_text("Digifly Public\n", encoding="utf-8")
    assert not (public / "Phase 2").exists()

    packaged_worker = tmp_path / "package/digifly_app/workers/generic_experiment_worker.py"
    packaged_worker.parent.mkdir(parents=True)
    packaged_worker.write_text("# packaged worker\n", encoding="utf-8")
    mechanism_root = tmp_path / "share/digifly-workstation/mechanisms/neuron_gap_junctions"
    mechanism_root.mkdir(parents=True)
    for filename in ("Gap.mod", "RectGap.mod", "HeteroRectGap.mod", "source_manifest.json"):
        (mechanism_root / filename).write_text("{}\n", encoding="utf-8")

    launcher = tmp_path / "neuron-env/bin/python"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    launcher.chmod(0o755)
    probed: list[str] = []

    monkeypatch.setattr(workspace_module, "worker_path", lambda _name: packaged_worker)
    monkeypatch.setattr(workspace_module, "resource_path", lambda *_parts: mechanism_root)
    monkeypatch.setattr(
        workspace_module,
        "_probe_neuron_runtime",
        lambda value: (probed.append(value) or CheckState.PASS, "NEURON 9.0.1"),
    )
    monkeypatch.setattr(
        workspace_module,
        "_probe_python_module",
        lambda *_args, **_kwargs: (CheckState.WARNING, "not configured"),
    )

    probes = DigiflyWorkspace(public).probe_engines(str(launcher), str(launcher), "")

    neuron = next(probe for probe in probes if probe.key == "neuron")
    assert neuron.source_state == CheckState.PASS
    assert neuron.runtime_state == CheckState.PASS
    assert "app-owned NEURON worker" in neuron.summary
    assert all("Phase 2" not in detail for detail in neuron.details)
    assert probed == [str(launcher)]


def test_workspace_neuron_probe_preserves_launcher_and_removes_legacy_paths(
    tmp_path: Path, monkeypatch
):
    base_python = tmp_path / "base/python3.12"
    base_python.parent.mkdir()
    base_python.symlink_to(Path(sys.executable))
    launcher = tmp_path / "neuron-env/bin/python"
    launcher.parent.mkdir(parents=True)
    launcher.symlink_to(base_python)
    called: dict[str, object] = {}

    class Completed:
        returncode = 0
        stdout = json.dumps(
            {"version": "9.0.1", "origin": "/selected/neuron/__init__.py"}
        )
        stderr = ""

    def fake_run(command, **kwargs):
        called["command"] = command
        called["environment"] = kwargs["env"]
        return Completed()

    monkeypatch.setenv("PYTHONPATH", "/legacy/Digifly Public/Phase 2")
    monkeypatch.setenv("NEURONHOME", "/Applications/NEURON")
    monkeypatch.setenv("CONDA_PREFIX", "/unrelated/conda")
    monkeypatch.setattr(workspace_module.subprocess, "run", fake_run)

    state, detail = workspace_module._probe_neuron_runtime(str(launcher))

    assert state == CheckState.PASS
    assert "NEURON 9.0.1" in detail
    assert called["command"][0] == str(launcher.absolute())
    environment = called["environment"]
    assert isinstance(environment, dict)
    assert "PYTHONPATH" not in environment
    assert "NEURONHOME" not in environment
    assert "CONDA_PREFIX" not in environment
    assert environment["PYTHONNOUSERSITE"] == "1"
    assert environment["PATH"].split(os.pathsep)[0] == str(launcher.parent)


def test_verified_runtime_choices_replace_profile_bindings(tmp_path: Path):
    workspace = tmp_path / "public"
    workspace.mkdir()
    (workspace / "README.md").write_text("Digifly Public\n", encoding="utf-8")
    profile = make_default_profile(
        workspace_root=workspace,
        output_root=tmp_path / "runs",
        neuron_runtime="/old/neuron/python",
        arbor_runtime="/old/arbor/python",
        bmtk_runtime="/old/bmtk/python",
    )

    updated = update_runtime_bindings(
        profile,
        neuron_runtime="/new/neuron/python",
        arbor_runtime="/new/arbor/python",
        bmtk_runtime="/new/bmtk/python",
    )

    assert str(updated.runtime_path(ResourceKind.NEURON_RUNTIME)) == "/new/neuron/python"
    assert str(updated.runtime_path(ResourceKind.ARBOR_RUNTIME)) == "/new/arbor/python"
    assert str(updated.runtime_path(ResourceKind.BMTK_RUNTIME)) == "/new/bmtk/python"
    assert len(updated.bindings(ResourceKind.NEURON_RUNTIME)) == 1
    assert len(updated.bindings(ResourceKind.ARBOR_RUNTIME)) == 1
    assert len(updated.bindings(ResourceKind.BMTK_RUNTIME)) == 1
