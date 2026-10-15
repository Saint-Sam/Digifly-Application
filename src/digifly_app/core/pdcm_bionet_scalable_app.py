"""App-only first scalable BioNet condition, isolated from immutable primary-v3."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Callable
from uuid import uuid4

from .paths import package_root, worker_path
from .pdcm_bionet_app import _resources, _state, _stop, _tree_rss, _utc
from .process_environment import external_runtime_launcher, external_runtime_path, sanitized_external_environment
from ..workers.pdcm_bionet_scalable_app_worker import (
    BOUNDS, CAVEATS, MODEL_CONTRACT, SOURCE_PINS, WORKFLOW,
    inventory, real_path, sha, validate_request, validate_sources, validate_spec, write_new,
)


@dataclass(frozen=True)
class PDCMBioNetScalableAppSpec:
    name: str = "Independent BioNet 2% full paper observation"
    network_seed: int = 28
    stimulus_seed: int = 208
    scale: float = .02
    duration_ms: float = 60100.
    drive: str = "balanced_poisson"
    dt_ms: float = .025

    def validate(self) -> None:
        validate_spec(asdict(self))

    def to_dict(self) -> dict:
        self.validate()
        return asdict(self)


def check_resources(resources: dict, *, before_dispatch: bool) -> dict:
    for key in ("available_memory_bytes", "free_disk_bytes"):
        if type(resources.get(key)) is not int or resources[key] < 0:
            raise ValueError("Actual available RAM/disk measurements are required")
    ram = BOUNDS["memory_reserve_bytes"] + (BOUNDS["rss_bytes"] if before_dispatch else 0)
    disk = BOUNDS["disk_reserve_bytes"] + (BOUNDS["output_bytes"] if before_dispatch else 0)
    if resources["available_memory_bytes"] < ram or resources["free_disk_bytes"] < disk:
        raise MemoryError("Scalable BioNet needs 22 GiB available RAM and 20 GiB free disk before staging/launch, retaining 12 GiB RAM and 16 GiB disk reserves during execution")
    return {"workflow": WORKFLOW, "initial": before_dispatch, "observed": dict(resources),
            "required_available_memory_bytes": ram, "required_free_disk_bytes": disk,
            "guards": BOUNDS, "measurement": "Available resources and sampled process-tree guard, not an OS quota or guaranteed upper forecast"}


def prepare_run(spec: PDCMBioNetScalableAppSpec, *, output_root: str | Path) -> Path:
    condition = spec.to_dict()
    assets = package_root() / "pdcm_bionet/primary_v4"
    validate_sources(assets)
    output = real_path(Path(output_root).expanduser())
    location = output
    while not location.exists():
        location = location.parent
    admission = check_resources(_resources(location), before_dispatch=True)
    output.mkdir(parents=True, exist_ok=True)
    real_path(output)
    stem = re.sub(r"[^a-z0-9]+", "-", spec.name.casefold()).strip("-")[:48] or "condition"
    root = output / f"pdcm-bionet-v4-app-{stem}-{uuid4().hex[:16]}"
    root.mkdir(exist_ok=False)
    try:
        shutil.copytree(assets, root / "frozen")
        shutil.copyfile(worker_path("pdcm_bionet_scalable_app_worker.py"), root / "pdcm_bionet_scalable_app_worker.py")
        write_new(root / "request.json", {"schema_version": 1, "workflow": WORKFLOW, "run_root": ".",
                  "spec": condition, "bounds": BOUNDS, "model_contract": MODEL_CONTRACT,
                  "source_sha256": SOURCE_PINS, "scientific_acceptance": False})
        write_new(root / "bundle_manifest.json", {"schema_version": 1, "workflow": WORKFLOW,
                  "files_sha256": {"request.json": sha(root / "request.json"),
                  "pdcm_bionet_scalable_app_worker.py": sha(root / "pdcm_bionet_scalable_app_worker.py"),
                  **{"frozen/" + n: h for n, h in inventory(root / "frozen").items()}}})
        write_new(root / "run_manifest.json", {"schema_version": 1, "workflow": WORKFLOW,
                  "state": "prepared", "prepared_at": _utc(), "scientific_acceptance": False,
                  "preparation_resource_admission": admission,
                  "request_sha256": sha(root / "request.json"), "bundle_manifest_sha256": sha(root / "bundle_manifest.json")})
        validate_prepared_bundle(root)
    except BaseException as exc:
        write_new(root / "preparation_failure.json", {"error": str(exc), "scientific_acceptance": False})
        raise
    return root


def validate_prepared_bundle(run_root: str | Path) -> dict:
    root = real_path(Path(run_root).expanduser())
    if not root.is_dir():
        raise ValueError("Scalable app bundle must be an owned directory")
    request = validate_request(root)
    state = json.loads(real_path(root / "run_manifest.json").read_text())
    if (state.get("workflow") != WORKFLOW or state.get("scientific_acceptance") is not False
            or state.get("request_sha256") != sha(root / "request.json")
            or state.get("bundle_manifest_sha256") != sha(root / "bundle_manifest.json")
            or sha(root / "pdcm_bionet_scalable_app_worker.py") != sha(worker_path("pdcm_bionet_scalable_app_worker.py"))):
        raise ValueError("Source-bound scalable worker, request or state changed")
    return request


def _check_output(root: Path, request: dict) -> dict:
    validate_prepared_bundle(root)
    result = json.loads(real_path(root / "worker_result.json").read_text())
    audit = json.loads(real_path(root / "analysis/audit.json").read_text())
    runtime = json.loads(real_path(root / "runtime.json").read_text())
    if (result.get("workflow") != WORKFLOW or result.get("spec") != request["spec"]
            or result.get("model_contract") != MODEL_CONTRACT or result.get("source_sha256") != SOURCE_PINS
            or result.get("audit") != audit or result.get("technical_acceptance") is not True
            or result.get("scientific_acceptance") is not False or result.get("runtime_sha256") != sha(root / "runtime.json")
            or runtime.get("versions") != {"bmtk": "1.2.0", "neuron": "9.0.1", "numpy": "1.26.4", "h5py": "3.11.0"}
            or audit.get("technical_acceptance") is not True or audit.get("scientific_acceptance") is not False):
        raise ValueError("Actual scalable audit, source, condition or runtime did not pass")
    for key in ("network_seed", "stimulus_seed", "duration_ms", "scale", "drive"):
        if audit.get(key) != request["spec"][key]:
            raise ValueError("Scalable audit condition differs from its request")
    for folder, field, binding in (("simulation", "files", "artifact_manifest_sha256"),
                                   ("analysis", "files_sha256", "analysis_manifest_sha256")):
        name = "artifact_manifest.json" if folder == "simulation" else "analysis_manifest.json"
        manifest = root / folder / name
        observed = inventory(root / folder)
        observed.pop(name)
        if json.loads(manifest.read_text()).get(field) != observed or result.get(binding) != sha(manifest):
            raise ValueError("Scalable observed artifact inventory changed")
    if (audit.get("run_artifact_manifest_sha256") != result["artifact_manifest_sha256"]
            or result.get("raw_run_root") != str(root / "simulation") or result.get("analysis_root") != str(root / "analysis")):
        raise ValueError("Scalable result points outside its owned run")
    return result


def run_prepared(run_root: str | Path, *, python_executable: str | Path,
                 progress: Callable[[str], None] | None = None,
                 cancel_requested: Callable[[], bool] | None = None) -> dict:
    root = real_path(Path(run_root).expanduser())
    request = validate_prepared_bundle(root)
    if json.loads((root / "run_manifest.json").read_text())["state"] != "prepared" or (root / "execution_claim.json").exists():
        raise FileExistsError("Scalable app condition was already attempted; no overwrite or automatic retry")
    runtime = external_runtime_launcher(python_executable)
    if not runtime.is_file() or not os.access(runtime, os.X_OK):
        raise ValueError("Choose the qualified scientific BioNet Python")
    resources = _resources(root)
    admission = check_resources(resources, before_dispatch=True)
    if cancel_requested and cancel_requested():
        raise InterruptedError("Scalable condition cancelled before dispatch")
    env = sanitized_external_environment({"PATH": external_runtime_path(runtime), "NEURON_MODULE_OPTIONS": "-nogui",
          "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
          "PYTHONPATH": os.pathsep.join((str(root / "frozen/src"), str(root / "frozen"))),
          "MPLBACKEND": "Agg", "MPLCONFIGDIR": str(root / "matplotlib"),
          "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"})
    env.pop("DISPLAY", None)
    (root / "matplotlib").mkdir(exist_ok=False)
    command = [str(runtime), "-B", str(root / "pdcm_bionet_scalable_app_worker.py"), "--request", str(root / "request.json")]
    write_new(root / "execution_claim.json", {"workflow": WORKFLOW, "started_at": _utc(), "command": command,
              "request_sha256": sha(root / "request.json"), "bounds": BOUNDS, "initial_resources": resources,
              "resource_admission": admission, "python_sha256": sha(runtime), "scientific_acceptance": False})
    _state(root, "running")
    started, process, peak, failure, previous = time.monotonic(), None, 0, None, 0
    try:
        if progress:
            progress("Starting the independent 2% full-duration BioNet paper condition")
        with (root / "worker.log").open("x") as log:
            process = subprocess.Popen(command, cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT,
                                       start_new_session=os.name != "nt")
            while process.poll() is None:
                if cancel_requested and cancel_requested():
                    raise InterruptedError("Scalable condition cancelled by the user")
                if time.monotonic() - started > BOUNDS["timeout_seconds"]:
                    raise TimeoutError("Scalable condition exceeded its 3,600-second guard")
                peak = max(peak, _tree_rss(process.pid))
                if peak > BOUNDS["rss_bytes"]:
                    raise MemoryError("Scalable condition exceeded its 10-GiB process-tree RSS safeguard")
                check_resources(_resources(root), before_dispatch=False)
                if sum(p.stat().st_size for p in root.rglob("*") if p.is_file()) > BOUNDS["output_bytes"]:
                    raise OSError("Scalable condition exceeded its 4-GiB retained-output safeguard")
                if progress and (root / "progress.jsonl").is_file():
                    lines = (root / "progress.jsonl").read_text().splitlines()
                    for line in lines[previous:]:
                        try:
                            message = json.loads(line)["message"]
                        except json.JSONDecodeError:
                            break  # A concurrent append may not yet be complete.
                        progress(message)
                        previous += 1
                time.sleep(.25)
            if process.returncode != 0:
                raise RuntimeError(f"BioNet scalable worker failed ({process.returncode}); partial evidence retained in {root / 'worker.log'}")
        check_resources(_resources(root), before_dispatch=False)
        if sum(p.stat().st_size for p in root.rglob("*") if p.is_file()) > BOUNDS["output_bytes"]:
            raise OSError("Scalable output exceeds its retained-output guard")
        if not 0 < peak <= BOUNDS["rss_bytes"]:
            raise ValueError("No valid process-tree RAM measurement was recorded")
        result = _check_output(root, request)
        if time.monotonic() - started > BOUNDS["timeout_seconds"]:
            raise TimeoutError("Scalable condition and verification exceeded its time guard")
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
                  "limits": "0.25-second sampled process-tree supervisor; not a kernel RAM quota"})
    result.update(state="completed", run_root=str(root), summary_path=str(root / "summary.json"),
                  execution_sha256=sha(root / "execution.json"), caveats=CAVEATS)
    write_new(root / "summary.json", result)
    _state(root, "completed", completed_at=_utc(), summary_sha256=sha(root / "summary.json"))
    return result
