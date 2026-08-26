from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

from digifly_app.core.process_environment import sanitized_external_environment
from digifly_app.workers import escape_siz_worker
from digifly_app.workers.escape_siz_worker import (
    _assert_output_binding,
    _assert_result_output_boundary,
    _bind_app_owned_outputs,
    _make_postsynaptic_only_3d_bundle,
    _prepare_app_owned_ais_cache,
)


def test_worker_rebinds_all_native_write_roots(tmp_path):
    native = SimpleNamespace(bothgf=SimpleNamespace())
    root = tmp_path / "escape_siz"
    _bind_app_owned_outputs(native, root)
    assert native.HERE == root
    assert native.OHMIC_GFC_SESSION_ROOT.is_relative_to(root)
    assert native.bothgf.PLOTS_ROOT.is_relative_to(root)
    planned = {
        "session_root": root / "cache",
        "run_root": root / "runs",
        "status_path": root / "status.json",
        "summary_path": root / "summary.json",
    }
    _assert_output_binding(planned, root)

    camera = tmp_path / "camera.json"
    camera.write_text("{}", encoding="utf-8")
    native.bothgf.vplots = SimpleNamespace(GF_REFERENCE_CAMERA_CANDIDATES=())
    _bind_app_owned_outputs(native, root, camera_preset=camera)
    assert native.bothgf.vplots.GF_REFERENCE_CAMERA_CANDIDATES == (camera,)


def test_worker_rebinds_ais_cache_in_cloned_case(tmp_path):
    root = tmp_path / "escape_siz"
    destination = root / "cache" / "ais" / "ais.csv"
    native = SimpleNamespace(
        bothgf=SimpleNamespace(),
        _visible_contact_case=lambda case, **kwargs: (dict(case), [], []),
    )
    _bind_app_owned_outputs(native, root, ais_cache_path=destination)
    case, _, _ = native._visible_contact_case({"ais_cache_csv": "/input/source.csv"})
    assert case["ais_cache_csv"] == str(destination.resolve())


def test_app_owned_ais_cache_is_seeded_without_changing_input(tmp_path):
    source = tmp_path / "Digifly Public" / "cache" / "ais.csv"
    source.parent.mkdir(parents=True)
    source.write_text("neuron_id,node_id\n10000,1\n", encoding="utf-8")
    base_case = tmp_path / "case.json"
    base_case.write_text(json.dumps({"ais_cache_csv": str(source)}), encoding="utf-8")
    before = source.read_bytes()
    binding = _prepare_app_owned_ais_cache(base_case, tmp_path / "app-output")
    destination = Path(binding["app_path"])
    assert destination.read_bytes() == before
    assert source.read_bytes() == before
    assert destination.is_relative_to(tmp_path / "app-output")


def test_worker_rejects_native_result_paths_outside_app_output(tmp_path):
    root = tmp_path / "escape_siz"
    escaped = tmp_path / "Digifly Public" / "records.csv"
    try:
        _assert_result_output_boundary({"run_summaries": [{"records_csv": str(escaped)}]}, root)
    except RuntimeError as exc:
        assert "records_csv" in str(exc)
    else:
        raise AssertionError("escaped native output path was accepted")
    try:
        _assert_result_output_boundary({"records_csv": "../../Digifly Public/records.csv"}, root)
    except RuntimeError as exc:
        assert "records_csv" in str(exc)
    else:
        raise AssertionError("relative native output escape was accepted")


def test_worker_dry_run_imports_native_boundary_without_simulating(tmp_path):
    public_root = tmp_path / "Digifly Public"
    native_root = (
        public_root
        / "Phase 2"
        / "Projects"
        / "Escape-SIZ"
        / "Giant Fiber Ablation Comparisons"
    )
    native_root.mkdir(parents=True)
    runner = native_root / escape_siz_worker.RUNNER_NAME
    runner.write_text(
        """
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
OHMIC_GFC_SESSION_ROOT = HERE / "native_cache" / "gap_enabled"
RUN_ROOT = HERE / "native_runs"
STATUS_PATH = HERE / "native_status.json"
SUMMARY_PATH = HERE / "native_summary.json"
GFC_EDGE_PATH = HERE / "edges.csv"
GFC_CASE_JSON = HERE / "case.json"
bothgf = SimpleNamespace(RUNS_ROOT=HERE / "native_runs", PLOTS_ROOT=HERE / "native_plots")

def workflow_paths(gj_model, **kwargs):
    stem = "stub_escape_siz"
    session_root = HERE / "phase2_build_cache_sessions" / stem / "gap_enabled"
    run_root = HERE / "runs" / stem
    return {
        "session_root": session_root,
        "run_root": run_root,
        "status_path": run_root / f"{stem}_status.json",
        "summary_path": run_root / f"{stem}_summary.json",
    }

def run_workflow(args):
    (HERE / "RUN_WORKFLOW_WAS_CALLED").write_text("unexpected", encoding="utf-8")
    raise RuntimeError("dry-run crossed the simulation boundary")
""".lstrip(),
        encoding="utf-8",
    )
    output_root = tmp_path / "app-output"
    worker_path = Path(escape_siz_worker.__file__).resolve()
    environment = sanitized_external_environment(
        {
            "NEURON_MODULE_OPTIONS": "-nogui",
            "PYTHONNOUSERSITE": "1",
            "DIGIFLY_APP_TEST_STUBS": "1",
        }
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-B",
            str(worker_path),
            "--digifly-public-root",
            str(public_root),
            "--output-root",
            str(output_root),
            "--dry-run",
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    events = [json.loads(line) for line in completed.stdout.splitlines() if line.startswith("{")]
    assert any(event.get("event") == "complete" for event in events)
    execution_root = output_root / "escape_siz"
    assert (execution_root / "worker_provenance.json").is_file()
    assert not (execution_root / "RUN_WORKFLOW_WAS_CALLED").exists()


def test_notebook_plot_bundle_stays_inside_app_output(tmp_path, monkeypatch):
    execution_root = tmp_path / "escape_siz"
    source_pdf = execution_root / "comparison_plots" / "native.pdf"
    source_pdf.parent.mkdir(parents=True)
    source_pdf.write_bytes(b"%PDF")
    captured = {}

    def make_figure(path, *, vmin, vmax):
        captured.update(path=path, vmin=vmin, vmax=vmax)
        outputs = {
            "png": execution_root / "comparison_plots" / "notebook.png",
            "pdf": execution_root / "comparison_plots" / "notebook.pdf",
            "compartment_summary_csv": execution_root / "comparison_plots" / "notebook.csv",
            "summary_json": execution_root / "comparison_plots" / "notebook.json",
        }
        for output in outputs.values():
            output.write_text("artifact", encoding="utf-8")
        return {key: str(value) for key, value in outputs.items()}

    plotter = SimpleNamespace(PLOTS_ROOT=None, make_figure_for_source=make_figure)
    monkeypatch.setitem(sys.modules, escape_siz_worker.POSTSYNAPTIC_3D_MODULE, plotter)
    bundle = _make_postsynaptic_only_3d_bundle(
        {"plots": {"pdf": str(source_pdf)}},
        execution_root=execution_root,
        vmin=-80.0,
        vmax=40.0,
    )
    assert captured == {"path": source_pdf, "vmin": -80.0, "vmax": 40.0}
    assert Path(bundle["png"]).is_relative_to(execution_root)
    assert bundle["source_pdf"] == str(source_pdf)


def test_worker_persists_requested_notebook_plot_bundle(tmp_path):
    public_root, native_root = _write_completed_native_stub(tmp_path)
    (native_root / f"{escape_siz_worker.POSTSYNAPTIC_3D_MODULE}.py").write_text(
        """
from pathlib import Path

PLOTS_ROOT = Path(__file__).resolve().parent

def make_figure_for_source(source_pdf, *, vmin, vmax):
    PLOTS_ROOT.mkdir(parents=True, exist_ok=True)
    outputs = {
        "png": PLOTS_ROOT / "notebook.png",
        "pdf": PLOTS_ROOT / "notebook.pdf",
        "compartment_summary_csv": PLOTS_ROOT / "notebook.csv",
        "summary_json": PLOTS_ROOT / "notebook.json",
    }
    for output in outputs.values():
        output.write_text("artifact", encoding="utf-8")
    return {key: str(value) for key, value in outputs.items()}
""".lstrip(),
        encoding="utf-8",
    )
    output_root = tmp_path / "app-output"
    completed = _run_stub_worker(public_root, output_root, "--postsynaptic-only-3d-plots")
    assert completed.returncode == 0, completed.stderr or completed.stdout
    summary = output_root / "escape_siz" / "runs" / "stub_escape_siz" / "stub_summary.json"
    payload = json.loads(summary.read_text(encoding="utf-8"))
    assert Path(payload["notebook_plot_bundle"]["png"]).is_file()
    assert Path(payload["notebook_plot_bundle"]["png"]).is_relative_to(output_root / "escape_siz")


def test_worker_fails_when_requested_notebook_plot_bundle_fails(tmp_path):
    public_root, native_root = _write_completed_native_stub(tmp_path)
    (native_root / f"{escape_siz_worker.POSTSYNAPTIC_3D_MODULE}.py").write_text(
        "def make_figure_for_source(*args, **kwargs):\n    raise RuntimeError('plot failed')\n",
        encoding="utf-8",
    )
    output_root = tmp_path / "app-output"
    completed = _run_stub_worker(public_root, output_root, "--postsynaptic-only-3d-plots")
    assert completed.returncode != 0
    summary = output_root / "escape_siz" / "runs" / "stub_escape_siz" / "stub_summary.json"
    payload = json.loads(summary.read_text(encoding="utf-8"))
    assert payload["notebook_plot_warning"] == "plot failed"
    assert payload["status"] == "failed"
    assert payload["simulation_status"] == "complete"
    assert Path(payload["provenance_json"]).is_file()


def _write_completed_native_stub(tmp_path):
    public_root = tmp_path / "Digifly Public"
    native_root = (
        public_root
        / "Phase 2"
        / "Projects"
        / "Escape-SIZ"
        / "Giant Fiber Ablation Comparisons"
    )
    native_root.mkdir(parents=True)
    (native_root / escape_siz_worker.RUNNER_NAME).write_text(
        """
from pathlib import Path
from types import SimpleNamespace
import json

HERE = Path(__file__).resolve().parent
OHMIC_GFC_SESSION_ROOT = HERE / "native_cache" / "gap_enabled"
RUN_ROOT = HERE / "native_runs"
STATUS_PATH = HERE / "native_status.json"
SUMMARY_PATH = HERE / "native_summary.json"
GFC_EDGE_PATH = HERE / "edges.csv"
GFC_CASE_JSON = HERE / "case.json"
bothgf = SimpleNamespace(RUNS_ROOT=HERE / "native_runs", PLOTS_ROOT=HERE / "native_plots")

def workflow_paths(gj_model, **kwargs):
    run_root = HERE / "runs" / "stub_escape_siz"
    return {
        "session_root": HERE / "phase2_build_cache_sessions" / "stub" / "gap_enabled",
        "run_root": run_root,
        "status_path": run_root / "stub_status.json",
        "summary_path": run_root / "stub_summary.json",
    }

def run_workflow(args):
    planned = workflow_paths(args.gj_model)
    planned["run_root"].mkdir(parents=True, exist_ok=True)
    bothgf.PLOTS_ROOT.mkdir(parents=True, exist_ok=True)
    source_pdf = bothgf.PLOTS_ROOT / "native.pdf"
    source_png = bothgf.PLOTS_ROOT / "native.png"
    source_pdf.write_bytes(b"%PDF")
    source_png.write_bytes(b"image")
    result = {
        "status": "complete",
        "summary_json": str(planned["summary_path"]),
        "cache_session_root": str(planned["session_root"]),
        "contact_count_policy": "deduplicated_visible_contact_sites_post_xyz",
        "visible_contact_counts": [{}],
        "plots": {"pdf": str(source_pdf), "png": str(source_png)},
    }
    planned["summary_path"].write_text(json.dumps(result), encoding="utf-8")
    return result
""".lstrip(),
        encoding="utf-8",
    )
    return public_root, native_root


def _run_stub_worker(public_root, output_root, *extra_args):
    environment = sanitized_external_environment(
        {
            "NEURON_MODULE_OPTIONS": "-nogui",
            "PYTHONNOUSERSITE": "1",
            "DIGIFLY_APP_TEST_STUBS": "1",
        }
    )
    return subprocess.run(
        [
            sys.executable,
            "-B",
            str(Path(escape_siz_worker.__file__).resolve()),
            "--digifly-public-root",
            str(public_root),
            "--output-root",
            str(output_root),
            *extra_args,
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
