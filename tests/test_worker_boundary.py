from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

from digifly_app.core.process_environment import sanitized_external_environment
from digifly_app.workers import escape_siz_worker
from digifly_app.workers.escape_siz_worker import _assert_output_binding, _bind_app_owned_outputs


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
