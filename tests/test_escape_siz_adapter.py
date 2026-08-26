from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path
import subprocess

from digifly_app.core.workspace import DigiflyWorkspace
from digifly_app.core.process_environment import sanitized_external_environment
from digifly_app.engines import neuron_escape_siz as escape_siz_module
from digifly_app.engines.neuron_escape_siz import (
    ABLATION_NOTEBOOK_NEURON_VERSION,
    ABLATION_NOTEBOOK_SOURCE_NPROC,
    ABLATION_STABLE_NPROC,
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
    assert config.postsynaptic_only_3d_plots is False
    assert len(config.stimulus_by_condition["gap_enabled"]) == 11
    assert set(config.errors()) == set()


def test_versioned_preset_file_stays_in_sync():
    preset_path = Path(__file__).resolve().parents[1] / "presets" / "escape_siz" / "latest_gfc2_pairwise_v1.json"
    payload = json.loads(preset_path.read_text(encoding="utf-8"))
    config = EscapeSizConfig.from_dict(payload)
    assert config.to_dict() == EscapeSizConfig().to_dict()


def test_ablation_notebook_preset_matches_current_active_cell():
    preset_path = Path(__file__).resolve().parents[1] / "presets" / "escape_siz" / "ablation_notebook_active_v1.json"
    payload = json.loads(preset_path.read_text(encoding="utf-8"))
    config = EscapeSizConfig.from_dict(payload)
    assert config.to_dict() == EscapeSizConfig.ablation_notebook_active().to_dict()
    assert config.gj_model == "heterotypic_rectifying"
    assert config.contact_site_na_multiplier == 2.5
    assert config.separate_gfs is True
    assert config.gfc2_ohmic is False
    assert ABLATION_NOTEBOOK_SOURCE_NPROC == 4
    assert config.nproc == ABLATION_STABLE_NPROC == 1
    assert config.postsynaptic_only_3d_plots is True
    assert set(config.stimulus_by_condition["gap_enabled"]) == {
        "13127", "13479", "13645", "13846", "14527", "14662",
        "15292", "15505", "15938", "16764", "17245",
    }
    assert set(config.stimulus_by_condition["gap_enabled"].values()) == {0.9}
    assert set(config.errors()) == set()


def test_ablation_notebook_plan_uses_exact_topology_and_plot_contract(tmp_path):
    workspace = DigiflyWorkspace(tmp_path / "Digifly Public")
    adapter = NeuronEscapeSizAdapter(workspace)
    config = EscapeSizConfig.ablation_notebook_active()
    adapter.camera_preset_path.parent.mkdir(parents=True)
    adapter.camera_preset_path.write_text("{}", encoding="utf-8")
    paths = adapter.workflow_paths(config, output_root=tmp_path / "output")
    assert str(paths["session_root"]).endswith("no_direct_gf_edges/gap_enabled")
    assert "gfc2_ohmic" not in str(paths["session_root"])
    plan = adapter.plan(config, output_root=tmp_path / "output")
    assert "--separate-gfs" in plan.arguments
    assert "--gfc2-ohmic" not in plan.arguments
    assert "--postsynaptic-only-3d-plots" in plan.arguments
    assert plan.arguments[plan.arguments.index("--camera-preset") + 1] == str(adapter.camera_preset_path.resolve())
    assert plan.arguments[plan.arguments.index("--nproc") + 1] == "1"


def test_latest_result_uses_configured_app_output_root(tmp_path):
    workspace = DigiflyWorkspace(tmp_path / "Digifly Public")
    adapter = NeuronEscapeSizAdapter(workspace)
    config = EscapeSizConfig.ablation_notebook_active()
    output_root = tmp_path / "app-output"
    summary = adapter.workflow_paths(config, output_root=output_root)["summary_path"]
    summary.parent.mkdir(parents=True)
    summary.write_text(
        json.dumps(
            {
                "status": "complete",
                "contact_count_policy": "deduplicated_visible_contact_sites_post_xyz",
                "visible_contact_counts": [{}],
                "summary_json": str(summary),
            }
        ),
        encoding="utf-8",
    )
    result = adapter.latest_result(config, output_root=output_root)
    assert result is not None
    assert result.summary_path == str(summary.resolve())


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
    assert "/Applications/NEURON/lib/python" not in python_paths
    assert plan.environment["DIGIFLY_GAP_MECH_DIR"] == str(adapter.gap_mechanism_root.resolve())
    assert plan.environment["DIGIFLY_APP_TEST_STUBS"] == "0"


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
    config = EscapeSizConfig.ablation_notebook_active()
    config.python_executable = str(executable)
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["environment"] = kwargs["env"]
        payload = {
            "neuron_version": "9.0.1",
            "neuron_path": "/opt/anaconda3/lib/python3.12/site-packages/neuron/__init__.py",
            "pandas_version": "2.2.0",
            "numpy_version": "2.0.0",
            "runner_path": str(adapter.runner_path),
            "plotter_path": str(adapter.postsynaptic_3d_path),
            "gap_loaded": True,
            "gap": True,
            "rect_gap": True,
            "hetero_rect_gap": True,
            "_ctypes_path": "/opt/anaconda3/lib/python3.12/lib-dynload/_ctypes.cpython-312-darwin.so",
            "ctypes_path": "/opt/anaconda3/lib/python3.12/ctypes/__init__.py",
        }
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload) + "\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    check = adapter._runtime_check(config)
    assert check.state.value == "pass"
    assert "NEURON 9.0.1" in check.detail
    assert "pandas 2.2.0" in check.detail
    assert "import _ctypes, ctypes" in captured["command"][-1]
    assert "run_baseline_with_10002_gfcs_contact_site_na_heatmaps" in captured["command"][-1]
    assert "make_manual_gf_heatmaps_plus_3d_voltage" in captured["command"][-1]
    assert "notebook plotter" in check.detail
    assert "Gap/RectGap/HeteroRectGap loaded read-only" in check.detail
    assert str(Path(__file__).resolve().parents[1] / "src") not in captured["environment"]["PYTHONPATH"]
    assert "/Applications/NEURON/lib/python" not in captured["environment"]["PYTHONPATH"]


def test_runtime_preflight_rejects_notebook_neuron_version_drift(tmp_path, monkeypatch):
    executable = tmp_path / "python"
    executable.write_text("placeholder", encoding="utf-8")
    workspace = DigiflyWorkspace(tmp_path / "Digifly Public")
    workspace.gfc_root.mkdir(parents=True)
    adapter = NeuronEscapeSizAdapter(workspace)
    config = EscapeSizConfig.ablation_notebook_active()
    config.python_executable = str(executable)
    payload = {
        "neuron_version": "8.2.6",
        "neuron_path": "/Applications/NEURON/lib/python/neuron/__init__.py",
        "gap": True,
        "rect_gap": True,
        "hetero_rect_gap": True,
    }
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, stdout=json.dumps(payload) + "\n", stderr=""
        ),
    )
    check = adapter._runtime_check(config)
    assert check.state.value == "fail"
    assert ABLATION_NOTEBOOK_NEURON_VERSION in check.detail


def test_gap_metadata_preflight_blocks_stale_public_sources(tmp_path):
    workspace = DigiflyWorkspace(tmp_path / "Digifly Public")
    adapter = NeuronEscapeSizAdapter(workspace)
    root = adapter.gap_mechanism_root
    library = root / "arm64" / "libnrnmech.dylib"
    library.parent.mkdir(parents=True)
    library.write_bytes(b"compiled")
    sources = []
    for name in ("Gap.mod", "RectGap.mod", "HeteroRectGap.mod"):
        source = root / name
        source.write_text(name, encoding="utf-8")
        sources.append(source)
    sentinel = {
        "neuron_runtime": {"neuron_version": ABLATION_NOTEBOOK_NEURON_VERSION},
        "sources": {source.name: source.stat().st_mtime_ns for source in sources},
    }
    (root / ".digifly_gap_mechanisms.json").write_text(json.dumps(sentinel), encoding="utf-8")
    config = EscapeSizConfig.ablation_notebook_active()
    assert adapter._gap_mechanism_metadata_check(config).state.value == "pass"
    sources[0].write_text("changed", encoding="utf-8")
    check = adapter._gap_mechanism_metadata_check(config)
    assert check.state.value == "fail"
    assert "refuses native auto-compilation" in check.detail


def test_contact_policy_verifies_canonical_rows(tmp_path, monkeypatch):
    workspace = DigiflyWorkspace(tmp_path / "Digifly Public")
    adapter = NeuronEscapeSizAdapter(workspace)
    adapter.edge_path.parent.mkdir(parents=True)
    with adapter.edge_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=("pre_id", "post_id"))
        writer.writeheader()
        for (pre, post), count in CANONICAL_VISIBLE_COUNTS.items():
            for _ in range(count):
                writer.writerow({"pre_id": pre, "post_id": post})
    monkeypatch.setattr(escape_siz_module, "EXPECTED_GFC_CONTACT_ROWS", sum(CANONICAL_VISIBLE_COUNTS.values()))
    monkeypatch.setattr(escape_siz_module, "EXPECTED_GFC_CONTACT_PAIRS", len(CANONICAL_VISIBLE_COUNTS))
    monkeypatch.setattr(
        escape_siz_module,
        "EXPECTED_GFC_CONTACT_SHA256",
        hashlib.sha256(adapter.edge_path.read_bytes()).hexdigest(),
    )
    check = adapter._contact_count_check()
    assert check.state.value == "pass"
    assert "all seven" in check.detail


def test_invalid_config_is_rejected():
    config = EscapeSizConfig(hetero_vslope_mV=0, nproc=0, vmin_mV=10, vmax_mV=-10)
    errors = " ".join(config.errors())
    assert "Worker count" in errors
    assert "slope" in errors
    assert "minimum" in errors

    notebook = EscapeSizConfig.ablation_notebook_active()
    notebook.contact_site_na_multiplier = 3.1
    notebook.nproc = 4
    notebook_errors = " ".join(notebook.errors())
    assert "2–3× ChAT prior" in notebook_errors
    assert "pinned to one" in notebook_errors


def test_notebook_plot_requires_saved_camera_asset(tmp_path):
    workspace = DigiflyWorkspace(tmp_path / "Digifly Public")
    adapter = NeuronEscapeSizAdapter(workspace)
    check = adapter._camera_preset_check(required=True)
    assert check.state.value == "fail"
    assert check.blocking is True
