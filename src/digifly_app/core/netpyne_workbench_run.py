"""Local, out-of-process execution for the version-1 synthetic NetPyNE Workbench.

This lane does not run PDCM, import external morphologies, or translate the
morphology-backed Circuit Builder format. Every execution uses immutable input
files in a unique run directory and a selected NetPyNE/NEURON interpreter.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import time
from typing import Callable

import psutil

from .netpyne_workbench import NetPyNEWorkbenchSpec, save_workbench_spec, to_netpyne_payload
from .paths import worker_path
from .process_environment import (
    external_runtime_launcher,
    external_runtime_path,
    sanitized_external_environment,
)
from .runtime_discovery import probe_simulator_runtime


NETPYNE_WORKBENCH_WORKFLOW = "netpyne_workbench_v1"
_SAFE_NAME = re.compile(r"[^a-z0-9]+")
_FROZEN_FILES = (
    "model.json",
    "netpyne_network.json",
    "netpyne_simulation.json",
    "worker_request.json",
    "netpyne_workbench_worker.py",
)


def _write_json(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def workbench_run_directory(output_root: str | Path, run_name: str) -> Path:
    if not isinstance(run_name, str) or not run_name.strip():
        raise ValueError("A NetPyNE run name is required")
    normalized = " ".join(run_name.split())
    slug = _SAFE_NAME.sub("-", normalized.casefold()).strip("-")[:48] or "run"
    digest = hashlib.sha256(normalized.casefold().encode("utf-8")).hexdigest()[:10]
    return Path(output_root).expanduser().resolve() / "experiments" / f"netpyne-{slug}-{digest}"


def _verified_runtime(python_executable: str | Path):
    runtime = external_runtime_launcher(python_executable)
    if not runtime.is_file() or not os.access(runtime, os.X_OK):
        raise ValueError(f"Choose an executable Python with NetPyNE and NEURON: {runtime}")
    identity = probe_simulator_runtime(runtime, timeout=30)
    if not identity.has_netpyne or not identity.has_neuron:
        raise ValueError(
            "The selected Python must contain both NetPyNE and NEURON"
            + (f": {identity.error}" if identity.error else "")
        )
    return runtime, identity


def prepare_workbench_run(
    model: NetPyNEWorkbenchSpec,
    *,
    python_executable: str | Path,
    output_root: str | Path,
    run_name: str,
) -> Path:
    """Validate before allocation and freeze one non-overwriting local run."""
    network, simulation = to_netpyne_payload(model)  # fails closed on external model inputs
    run_root = workbench_run_directory(output_root, run_name)
    if run_root.exists():
        raise FileExistsError(f"A NetPyNE run with this name already exists: {run_root}")
    source_worker = worker_path("netpyne_workbench_worker.py")
    if not source_worker.is_file():
        raise FileNotFoundError(f"Digifly NetPyNE worker is missing: {source_worker}")
    runtime, identity = _verified_runtime(python_executable)

    run_root.parent.mkdir(parents=True, exist_ok=True)
    run_root.mkdir(exist_ok=False)
    try:
        save_workbench_spec(run_root / "model.json", model)
        _write_json(run_root / "netpyne_network.json", network)
        _write_json(run_root / "netpyne_simulation.json", simulation)
        _write_json(
            run_root / "worker_request.json",
            {
                "schema_version": 1,
                "workflow": NETPYNE_WORKBENCH_WORKFLOW,
                "run_root": ".",
                "run_name": " ".join(run_name.split()),
            },
        )
        shutil.copyfile(source_worker, run_root / "netpyne_workbench_worker.py")
        _write_json(
            run_root / "bundle_manifest.json",
            {
                "schema_version": 1,
                "workflow": NETPYNE_WORKBENCH_WORKFLOW,
                "files_sha256": {name: _sha256(run_root / name) for name in _FROZEN_FILES},
            },
        )
        _write_json(
            run_root / "run_manifest.json",
            {
                "schema_version": 1,
                "workflow": NETPYNE_WORKBENCH_WORKFLOW,
                "run_name": " ".join(run_name.split()),
                "model_name": model.name,
                "state": "prepared",
                "prepared_at": datetime.now(timezone.utc).isoformat(),
                "runtime": {
                    "python": str(runtime),
                    "python_version": identity.python_version,
                    "netpyne_version": identity.netpyne_version,
                    "neuron_version": identity.neuron_version,
                },
            },
        )
    except BaseException:
        # No successful run exists; the newly created directory belongs solely
        # to this preparation attempt. Keep it for diagnosis instead of erasing
        # any source or workspace data.
        raise
    return run_root


def validate_prepared_workbench_run(run_dir: str | Path) -> dict:
    root = Path(run_dir).expanduser().resolve()
    manifest_path = root / "bundle_manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise ValueError("This is not a prepared NetPyNE Workbench run")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = manifest.get("files_sha256")
    if (
        manifest.get("schema_version") != 1
        or manifest.get("workflow") != NETPYNE_WORKBENCH_WORKFLOW
        or not isinstance(files, dict)
        or set(files) != set(_FROZEN_FILES)
    ):
        raise ValueError("Invalid NetPyNE Workbench bundle manifest")
    for name, expected in files.items():
        path = root / name
        if not path.is_file() or path.is_symlink() or _sha256(path) != expected:
            raise ValueError(f"Prepared NetPyNE Workbench file changed: {name}")
    request = json.loads((root / "worker_request.json").read_text(encoding="utf-8"))
    if (
        request.get("schema_version") != 1
        or request.get("workflow") != NETPYNE_WORKBENCH_WORKFLOW
        or request.get("run_root") != "."
    ):
        raise ValueError("Invalid or non-portable NetPyNE Workbench worker request")
    return request


def _stop_worker(process: subprocess.Popen) -> None:
    """Terminate this worker process tree, leaving other simulations alone."""
    if os.name != "nt":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)
        return
    try:
        parent = psutil.Process(process.pid)
        descendants = parent.children(recursive=True)
    except psutil.NoSuchProcess:
        descendants = []
        parent = None
    for child in descendants:
        try:
            child.terminate()
        except psutil.NoSuchProcess:
            pass
    if parent is not None:
        try:
            parent.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(descendants + ([parent] if parent is not None else []), timeout=5)
    for child in alive:
        try:
            child.kill()
        except psutil.NoSuchProcess:
            pass
    process.wait(timeout=5)


def _stop_docker_container(root: Path, runtime: Path) -> None:
    if runtime.name.casefold() != "digifly-python.exe":
        return
    docker = shutil.which("docker")
    if docker is None:
        return
    name = f"digifly-{hashlib.sha256(str(root).encode()).hexdigest()[:12]}"
    try:
        subprocess.run([docker, "stop", "--time", "2", name], timeout=10, check=False, capture_output=True)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _write_incomplete_summary(root: Path, manifest: dict, state: str, error: str) -> None:
    prior_summary = root / "summary.json"
    if prior_summary.exists():
        # A worker may write its final JSON and then exit unsuccessfully. Keep
        # those bytes for diagnosis, while making Results reflect the failure.
        preserved = root / "worker_partial_summary.json"
        if not preserved.exists():
            prior_summary.replace(preserved)
    artifacts = [
        {"kind": "json", "path": name, "label": label}
        for name, label in (
            ("model.json", "Frozen Workbench model"),
            ("netpyne_network.json", "NetPyNE network settings"),
            ("netpyne_simulation.json", "NetPyNE simulation settings"),
            ("bundle_manifest.json", "Input integrity manifest"),
            ("run_manifest.json", "Run state and runtime"),
        )
    ]
    if (root / "worker.log").is_file():
        artifacts.append({"kind": "file", "path": "worker.log", "label": "NetPyNE worker log"})
    if (root / "worker_partial_summary.json").is_file():
        artifacts.append({"kind": "json", "path": "worker_partial_summary.json", "label": "Partial worker summary"})
    _write_json(
        root / "summary.json",
        {
            "schema_version": 1,
            "workflow": NETPYNE_WORKBENCH_WORKFLOW,
            "status": state,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "title": f"NetPyNE Workbench · {manifest.get('run_name', 'run')}",
            "engine": "netpyne",
            "metadata": {"error": error, "model_family": "synthetic_single_soma_hh"},
            "artifacts": artifacts,
        },
    )


def run_prepared_workbench(
    run_dir: str | Path,
    *,
    cancel_requested: Callable[[], bool] | None = None,
    python_executable: str | Path | None = None,
) -> Path:
    """Run one prepared bundle; return its conventional Digifly summary path."""
    root = Path(run_dir).expanduser().resolve()
    validate_prepared_workbench_run(root)
    manifest_path = root / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("workflow") != NETPYNE_WORKBENCH_WORKFLOW or manifest.get("state") != "prepared":
        raise ValueError("This NetPyNE Workbench run has already been started")
    if (root / "summary.json").exists():
        raise ValueError("The prepared run folder already contains a summary")
    selected_python = python_executable or manifest.get("runtime", {}).get("python", "")
    runtime, identity = _verified_runtime(selected_python)
    if cancel_requested is not None and cancel_requested():
        manifest.update(state="cancelled", error="NetPyNE run cancelled before launch")
        _write_json(manifest_path, manifest)
        _write_incomplete_summary(root, manifest, "cancelled", manifest["error"])
        raise InterruptedError(manifest["error"])
    environment = sanitized_external_environment(
        {
            "PATH": external_runtime_path(runtime),
            "NEURON_MODULE_OPTIONS": "-nogui",
            "MPLBACKEND": "Agg",
            "MPLCONFIGDIR": str(root / "matplotlib"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
        }
    )
    environment.pop("DISPLAY", None)
    (root / "matplotlib").mkdir(exist_ok=True)
    manifest.update(
        state="running",
        started_at=datetime.now(timezone.utc).isoformat(),
        runtime={
            "python": str(runtime),
            "python_version": identity.python_version,
            "netpyne_version": identity.netpyne_version,
            "neuron_version": identity.neuron_version,
        },
    )
    _write_json(manifest_path, manifest)
    process: subprocess.Popen | None = None
    try:
        with (root / "worker.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                [str(runtime), "-B", str(root / "netpyne_workbench_worker.py"), "--request", str(root / "worker_request.json")],
                cwd=root,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=os.name != "nt",
            )
            while process.poll() is None:
                if cancel_requested is not None and cancel_requested():
                    raise InterruptedError("NetPyNE run cancelled by user")
                time.sleep(0.2)
            if process.returncode or not (root / "summary.json").is_file():
                raise RuntimeError(f"NetPyNE worker exited {process.returncode}; inspect {root / 'worker.log'}")
        summary = json.loads((root / "summary.json").read_text(encoding="utf-8"))
        if summary.get("status") != "completed" or summary.get("workflow") != NETPYNE_WORKBENCH_WORKFLOW:
            raise RuntimeError("NetPyNE worker did not produce a completed Workbench summary")
    except InterruptedError as exc:
        if process is not None and process.poll() is None:
            _stop_docker_container(root, runtime)
            _stop_worker(process)
        manifest.update(state="cancelled", error=str(exc), completed_at=datetime.now(timezone.utc).isoformat())
        _write_json(manifest_path, manifest)
        _write_incomplete_summary(root, manifest, "cancelled", str(exc))
        raise
    except BaseException as exc:
        if process is not None and process.poll() is None:
            _stop_docker_container(root, runtime)
            _stop_worker(process)
        manifest.update(state="failed", error=str(exc), completed_at=datetime.now(timezone.utc).isoformat())
        _write_json(manifest_path, manifest)
        _write_incomplete_summary(root, manifest, "failed", str(exc))
        raise
    manifest.update(state="completed", completed_at=datetime.now(timezone.utc).isoformat())
    _write_json(manifest_path, manifest)
    return root / "summary.json"
