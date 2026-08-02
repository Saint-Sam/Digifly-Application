from __future__ import annotations

import csv
import json
import os
from pathlib import Path
import subprocess

from digifly_app.core.workspace import DigiflyWorkspace
from digifly_app.core.process_environment import sanitized_external_environment
from digifly_app.engines.neuron_escape_siz import (
    CANONICAL_VISIBLE_COUNTS,
    EscapeSizConfig,
    NeuronEscapeSizAdapter,
)


def test_latest_preset_matches_documented_model():
    config = EscapeSizConfig()
    assert config.gj_model == "heterotypic_rectifying"
    assert config.contact_site_na_multiplier == 2.5
    assert config.separate_gfs is True
    assert config.gfc2_ohmic is True
    assert config.nproc == 1
    assert len(config.stimulus_by_condition["gap_enabled"]) == 11
    assert set(config.errors()) == set()


def test_versioned_preset_file_stays_in_sync():
    preset_path = Path(__file__).resolve().parents[1] / "presets" / "escape_siz" / "latest_gfc2_pairwise_v1.json"
    payload = json.loads(preset_path.read_text(encoding="utf-8"))
    config = EscapeSizConfig.from_dict(payload)
    assert config.to_dict() == EscapeSizConfig().to_dict()


def test_cache_identity_and_command_are_exact_argument_arrays(tmp_path, monkeypatch):
    inherited_bundle = tmp_path / "Digifly App.app" / "Contents" / "MacOS"
    monkeypatch.setenv("PYTHONPATH", f"{inherited_bundle}{os.pathsep}/unrelated/inherited/path")
    workspace = DigiflyWorkspace(tmp_path / "Digifly Public")
    adapter = NeuronEscapeSizAdapter(workspace)
    config = EscapeSizConfig()
    paths = adapter.workflow_paths(config)
    assert "paperfit_resid0p2_tauopen6_tauclose2_closed0_vhalf0_vslope5" in str(paths["session_root"])
    assert str(paths["session_root"]).endswith("no_direct_gf_edges_gfc2_ohmic/gap_enabled")
    output_root = tmp_path / "output"
    plan = adapter.plan(config, output_root=output_root)
    assert plan.arguments[0] == "-B"
    assert plan.arguments[1].endswith("escape_siz_worker.py")
    assert plan.arguments[plan.arguments.index("--output-root") + 1] == str(output_root.resolve())
    assert "--stim-target-amps-json" in plan.arguments
    stimulus_arg = plan.arguments[plan.arguments.index("--stim-target-amps-json") + 1]
    assert json.loads(stimulus_arg)["gap_enabled"]["13127"] == 0.9
    assert plan.output_behavior == "app_owned"
    assert str(output_root.resolve() / "escape_siz") in (plan.expected_summary_path or "")
    python_paths = plan.environment["PYTHONPATH"].split(os.pathsep)
    assert str(workspace.phase2_neuron) in python_paths
    assert str(workspace.gfc_root) in python_paths
    assert str(inherited_bundle) not in python_paths
    assert "/unrelated/inherited/path" not in python_paths
    assert str(Path(__file__).resolve().parents[1] / "src") not in python_paths
    assert "/Applications/NEURON/lib/python" in python_paths or not Path(
        "/Applications/NEURON/lib/python"
    ).exists()


def test_worker_environment_removes_embedded_python_and_loader_paths():
    environment = sanitized_external_environment(
        {"PYTHONPATH": "/controlled/phase2:/controlled/neuron", "PYTHONNOUSERSITE": "1"},
        inherited={
            "PATH": "/usr/bin",
            "PYTHONPATH": "/bundle/Contents/MacOS",
            "PYTHONHOME": "/bundle/Contents/MacOS",
            "VIRTUAL_ENV": "/bundle/venv",
            "DYLD_LIBRARY_PATH": "/bundle/Contents/MacOS",
        },
    )
    assert environment["PATH"] == "/usr/bin"
    assert environment["PYTHONPATH"] == "/controlled/phase2:/controlled/neuron"
    assert environment["PYTHONNOUSERSITE"] == "1"
    assert "PYTHONHOME" not in environment
    assert "VIRTUAL_ENV" not in environment
    assert "DYLD_LIBRARY_PATH" not in environment


def test_runtime_preflight_imports_full_native_stack(tmp_path, monkeypatch):
    executable = tmp_path / "python"
    executable.write_text("placeholder", encoding="utf-8")
    workspace = DigiflyWorkspace(tmp_path / "Digifly Public")
    workspace.gfc_root.mkdir(parents=True)
    adapter = NeuronEscapeSizAdapter(workspace)
    config = EscapeSizConfig(python_executable=str(executable))
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["environment"] = kwargs["env"]
        payload = {
            "neuron_version": "8.2.6",
            "neuron_path": "/Applications/NEURON/lib/python/neuron/__init__.py",
            "pandas_version": "2.2.0",
            "numpy_version": "2.0.0",
            "runner_path": str(adapter.runner_path),
            "_ctypes_path": "/opt/anaconda3/lib/python3.12/lib-dynload/_ctypes.cpython-312-darwin.so",
            "ctypes_path": "/opt/anaconda3/lib/python3.12/ctypes/__init__.py",
        }
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload) + "\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    check = adapter._runtime_check(config)
    assert check.state.value == "pass"
    assert "NEURON 8.2.6" in check.detail
    assert "pandas 2.2.0" in check.detail
    assert "import _ctypes, ctypes" in captured["command"][-1]
    assert "run_baseline_with_10002_gfcs_contact_site_na_heatmaps" in captured["command"][-1]
    assert str(Path(__file__).resolve().parents[1] / "src") not in captured["environment"]["PYTHONPATH"]


def test_contact_policy_verifies_canonical_rows(tmp_path):
    workspace = DigiflyWorkspace(tmp_path / "Digifly Public")
    adapter = NeuronEscapeSizAdapter(workspace)
    adapter.edge_path.parent.mkdir(parents=True)
    with adapter.edge_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=("pre_id", "post_id"))
        writer.writeheader()
        for (pre, post), count in CANONICAL_VISIBLE_COUNTS.items():
            for _ in range(count):
                writer.writerow({"pre_id": pre, "post_id": post})
    check = adapter._contact_count_check()
    assert check.state.value == "pass"
    assert "all seven" in check.detail


def test_invalid_config_is_rejected():
    config = EscapeSizConfig(hetero_vslope_mV=0, nproc=0, vmin_mV=10, vmax_mV=-10)
    errors = " ".join(config.errors())
    assert "Worker count" in errors
    assert "slope" in errors
    assert "minimum" in errors
