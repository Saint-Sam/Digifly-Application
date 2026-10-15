"""App-owned admission, freezing and supervision of a full BioNet observation."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import time
from typing import Callable
from uuid import uuid4

import psutil

from .paths import package_root, worker_path
from .pdcm_bionet_scale_plan import check_live_resources, scale_resource_plan
from .process_environment import external_runtime_launcher, external_runtime_path, sanitized_external_environment
from ..workers.pdcm_bionet_app_worker import (
    BOUNDS, CAVEATS, SOURCE_PINS, WORKFLOW, inventory, sha,
    validate_request, validate_sources, validate_spec, write_new,
)


@dataclass(frozen=True)
class PDCMBioNetAppSpec:
    name: str = "Independent BioNet PDCM full observation"
    network_seed: int = 28
    stimulus_seed: int = 208
    scale: float = .01
    duration_ms: float = 60100.
    drive: str = "balanced_poisson"

    def validate(self) -> None:
        validate_spec(asdict(self))

    def to_dict(self) -> dict:
        result = asdict(self)
        validate_spec(result)
        return result


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def plan_scale_resources(spec: PDCMBioNetAppSpec, *, measured_reference: dict) -> dict:
    """Planning only: do not make a larger spec valid for prepare_run/Run."""
    result = scale_resource_plan(asdict(spec),measured_reference)
    try:
        spec.validate()
    except ValueError:
        result["current_worker_support"] = False
    else:
        result["current_worker_support"] = True
    return result


def prepare_run(spec: PDCMBioNetAppSpec, *, output_root: str | Path) -> Path:
    """Freeze one unique app request; do not import, compile or run a simulator."""
    condition = spec.to_dict()
    assets = package_root() / "pdcm_bionet/primary_v3"
    validate_sources(assets)
    output = Path(output_root).expanduser().absolute()
    resource_location = output
    while not resource_location.exists():
        resource_location = resource_location.parent
    # Check the live resource envelope before even staging a unique bundle.
    # This does not raise any 1% worker/source/timeout/output gate.
    admission = _check_resources(_resources(resource_location))
    output.mkdir(parents=True, exist_ok=True)
    if output.is_symlink():
        raise ValueError("Run output root must not be a symlink")
    stem = re.sub(r"[^a-z0-9]+", "-", spec.name.casefold()).strip("-")[:48] or "observation"
    root = output / f"pdcm-bionet-app-{stem}-{uuid4().hex[:16]}"
    root.mkdir(exist_ok=False)
    try:
        shutil.copytree(assets, root / "frozen")
        shutil.copyfile(worker_path("pdcm_bionet_app_worker.py"), root / "pdcm_bionet_app_worker.py")
        request = {"schema_version": 1, "workflow": WORKFLOW, "run_root": ".",
                   "spec": condition, "bounds": BOUNDS, "source_sha256": SOURCE_PINS,
                   "scientific_acceptance": False}
        write_new(root / "request.json", request)
        write_new(root / "bundle_manifest.json", {"schema_version": 1, "workflow": WORKFLOW,
                  "files_sha256": {"request.json": sha(root / "request.json"),
                  "pdcm_bionet_app_worker.py": sha(root / "pdcm_bionet_app_worker.py"),
                  **{"frozen/" + n: h for n, h in inventory(root / "frozen").items()}}})
        write_new(root / "run_manifest.json", {"schema_version": 1, "workflow": WORKFLOW,
                  "state": "prepared", "prepared_at": _utc(), "scientific_acceptance": False,
                  "preparation_resource_admission": admission,
                  "request_sha256": sha(root / "request.json"),
                  "bundle_manifest_sha256": sha(root / "bundle_manifest.json")})
        validate_prepared_bundle(root)
    except BaseException as exc:
        write_new(root / "preparation_failure.json", {"error": str(exc), "scientific_acceptance": False})
        raise
    return root


def validate_prepared_bundle(run_root: str | Path) -> dict:
    root = Path(run_root).expanduser().absolute()
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Execution bundle must be a regular app-owned directory")
    result = validate_request(root)
    manifest_path = root / "run_manifest.json"
    if manifest_path.is_symlink():
        raise ValueError("Run state must not be redirected")
    manifest = json.loads(manifest_path.read_text())
    if (manifest.get("workflow") != WORKFLOW
            or manifest.get("request_sha256") != sha(root / "request.json")
            or manifest.get("bundle_manifest_sha256") != sha(root / "bundle_manifest.json")
            or manifest.get("scientific_acceptance") is not False
            or sha(root / "pdcm_bionet_app_worker.py") != sha(worker_path("pdcm_bionet_app_worker.py"))):
        raise ValueError("Immutable app request/worker/state binding changed")
    return result


def _state(root: Path, state: str, **fields) -> None:
    manifest = json.loads((root / "run_manifest.json").read_text())
    manifest.update(state=state, updated_at=_utc(), **fields)
    temporary = root / "run_manifest.json.tmp"
    write_new(temporary, manifest)
    temporary.replace(root / "run_manifest.json")


def _resources(root: Path) -> dict:
    return {"available_memory_bytes": int(psutil.virtual_memory().available),
            "free_disk_bytes": int(shutil.disk_usage(root).free)}


def _check_resources(resources: dict) -> dict:
    return check_live_resources(resources,BOUNDS)


def _tree_rss(pid: int) -> int:
    try:
        owner = psutil.Process(pid)
        processes = [owner, *owner.children(recursive=True)]
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return 0
    total = 0
    for process in processes:
        try:
            total += process.memory_info().rss
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            pass
    return total


def _stop(process) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        children = psutil.Process(process.pid).children(recursive=True)
        for child in reversed(children):
            try:
                child.terminate()
            except psutil.NoSuchProcess:
                pass
        process.terminate()
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            for child in reversed(children):
                try:
                    child.kill()
                except psutil.NoSuchProcess:
                    pass
            process.kill()
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait(timeout=5)


def _check_output(root: Path, request: dict) -> dict:
    validate_prepared_bundle(root)
    result = json.loads((root / "worker_result.json").read_text())
    audit_path = root / "analysis/audit.json"
    audit = json.loads(audit_path.read_text())
    runtime = json.loads((root / "runtime.json").read_text())
    if (result.get("workflow") != WORKFLOW or result.get("spec") != request["spec"]
            or result.get("technical_acceptance") is not True
            or result.get("scientific_acceptance") is not False
            or result.get("source_sha256") != SOURCE_PINS or result.get("audit") != audit
            or result.get("runtime_sha256") != sha(root / "runtime.json")
            or runtime.get("versions", {}).get("bmtk") != "1.2.0"
            or runtime.get("versions", {}).get("neuron") != "9.0.1"
            or audit.get("technical_acceptance") is not True
            or audit.get("scientific_acceptance") is not False):
        raise ValueError("Actual BioNet audit, runtime or condition did not pass")
    for key in ("network_seed", "stimulus_seed", "duration_ms", "scale"):
        if audit.get(key) != request["spec"][key]:
            raise ValueError("Observed app condition differs from requested condition")
    if audit.get("drive", "balanced_poisson") != request["spec"]["drive"]:
        raise ValueError("Observed app drive differs from requested drive")
    for folder, manifest_name, field, binding in (
        ("simulation", "artifact_manifest.json", "files", "artifact_manifest_sha256"),
        ("analysis", "analysis_manifest.json", "files_sha256", "analysis_manifest_sha256"),
    ):
        manifest_path = root / folder / manifest_name
        observed = inventory(root / folder)
        observed.pop(manifest_name)
        manifest = json.loads(manifest_path.read_text())
        if manifest.get(field) != observed or result.get(binding) != sha(manifest_path):
            raise ValueError("Actual simulation or analysis artifacts changed")
    if (audit.get("run_artifact_manifest_sha256") != result["artifact_manifest_sha256"]
            or result.get("raw_run_root") != str(root / "simulation")
            or result.get("analysis_root") != str(root / "analysis")):
        raise ValueError("Observed result points outside its app run")
    return result


def run_prepared(run_root: str | Path, *, python: str | Path | None = None,
                 python_executable: str | Path | None = None,
                 progress: Callable[[str], None] | None = None,
                 cancel_requested: Callable[[], bool] | None = None) -> dict:
    """Run once with one process-tree watchdog; never retry a preserved attempt."""
    root = Path(run_root).expanduser().absolute()
    request = validate_prepared_bundle(root)
    if (json.loads((root / "run_manifest.json").read_text())["state"] != "prepared"
            or (root / "execution_claim.json").exists()):
        raise FileExistsError("This app observation was already attempted; no overwrite or retry")
    selected = python_executable if python_executable is not None else python
    if selected is None:
        raise ValueError("Select the qualified BioNet scientific Python runtime")
    runtime = external_runtime_launcher(selected)
    if not runtime.is_file() or not os.access(runtime, os.X_OK):
        raise ValueError("Selected BioNet Python is not executable")
    resources = _resources(root)
    admission = _check_resources(resources)
    if cancel_requested and cancel_requested():
        raise InterruptedError("App observation cancelled before dispatch")
    environment = sanitized_external_environment({
        "PATH": external_runtime_path(runtime), "NEURON_MODULE_OPTIONS": "-nogui",
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
        "PYTHONPATH": os.pathsep.join((str(root / "frozen/src"), str(root / "frozen"))),
        "MPLBACKEND": "Agg", "MPLCONFIGDIR": str(root / "matplotlib"),
        "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
    })
    environment.pop("DISPLAY", None)
    (root / "matplotlib").mkdir(exist_ok=False)
    command = [str(runtime), "-B", str(root / "pdcm_bionet_app_worker.py"),
               "--request", str(root / "request.json")]
    write_new(root / "execution_claim.json", {"workflow": WORKFLOW, "started_at": _utc(),
              "command": command, "request_sha256": sha(root / "request.json"),
              "bounds": BOUNDS, "initial_resources": resources,
              "resource_admission": admission,
              "python_sha256": sha(runtime), "scientific_acceptance": False})
    _state(root, "running")
    started = time.monotonic()
    process = None
    peak = 0
    failure = None
    previous = 0
    try:
        if progress:
            progress("Starting one full-duration independent BioNet observation")
        with (root / "worker.log").open("x") as log:
            process = subprocess.Popen(command, cwd=root, env=environment,
                      stdout=log, stderr=subprocess.STDOUT, start_new_session=os.name != "nt")
            while process.poll() is None:
                if cancel_requested and cancel_requested():
                    raise InterruptedError("App observation cancelled by user")
                if time.monotonic() - started > BOUNDS["timeout_seconds"]:
                    raise TimeoutError("Full-duration app observation exceeded 900 seconds")
                peak = max(peak, _tree_rss(process.pid))
                if peak > BOUNDS["rss_bytes"]:
                    raise MemoryError("App observation exceeded the 2-GiB process-tree RSS safeguard")
                _check_resources(_resources(root))
                size = sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
                if size > BOUNDS["output_bytes"]:
                    raise OSError("App observation exceeded its 2-GiB retained-output safeguard")
                if progress and (root / "progress.jsonl").is_file():
                    lines = (root / "progress.jsonl").read_text().splitlines()
                    for line in lines[previous:]:
                        try:
                            progress(json.loads(line)["message"])
                        except json.JSONDecodeError:
                            break
                        previous += 1
                time.sleep(.25)
            if process.returncode != 0:
                raise RuntimeError(f"BioNet worker failed ({process.returncode}); evidence retained in {root / 'worker.log'}")
        if time.monotonic() - started > BOUNDS["timeout_seconds"]:
            raise TimeoutError("Full-duration app observation exceeded 900 seconds")
        _check_resources(_resources(root))
        if sum(p.stat().st_size for p in root.rglob("*") if p.is_file()) > BOUNDS["output_bytes"]:
            raise OSError("App observation exceeded its retained-output safeguard")
        if not 0 < peak <= BOUNDS["rss_bytes"]:
            raise ValueError("No valid process-tree resource observation was recorded")
        result = _check_output(root, request)
        if time.monotonic() - started > BOUNDS["timeout_seconds"]:
            raise TimeoutError("App observation and artifact verification exceeded 900 seconds")
    except BaseException as exc:
        failure = str(exc)
        if process is not None:
            _stop(process)
        _state(root, "cancelled" if isinstance(exc, InterruptedError) else "failed", error=failure)
        raise
    finally:
        write_new(root / "execution.json", {"workflow": WORKFLOW, "command": command,
                  "exit_code": process.returncode if process is not None else None,
                  "walltime_s": time.monotonic() - started, "sampled_process_tree_peak_rss_bytes": peak,
                  "failure": failure, "bounds": BOUNDS, "scientific_acceptance": False,
                  "claim_sha256": sha(root / "execution_claim.json"),
                  "worker_log_sha256": sha(root / "worker.log") if (root / "worker.log").exists() else None,
                  "limits": "0.25-second sampled process-tree watchdog; not a kernel quota"})
    result.update(state="completed", run_root=str(root), summary_path=str(root / "summary.json"),
                  execution_sha256=sha(root / "execution.json"), caveats=CAVEATS)
    write_new(root / "summary.json", result)
    _state(root, "completed", completed_at=_utc(), summary_sha256=sha(root / "summary.json"))
    return result
