"""Exact 5% app preparation and sampled supervision with full-copy reserve."""
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
from ..workers.pdcm_bionet_5pct_app_worker import (
    BOUNDS, CAVEATS, CLAIM_SCHEMA, CONTROLLER_FILENAME, EXECUTION_SCHEMA, MODEL_CONTRACT, RESOURCE_CONTRACT,
    SOURCE_PINS, WORKER_FILENAME, WORKFLOW, inventory, real_path, retained_bytes, sha,
    validate_request, validate_sources, validate_spec, validate_structure, write_new,
)


@dataclass(frozen=True)
class PDCMBioNet5PctAppSpec:
    name: str = "Independent BioNet 5% full paper observation"
    network_seed: int = 28
    stimulus_seed: int = 208
    scale: float = .05
    duration_ms: float = 60100.
    drive: str = "balanced_poisson"
    dt_ms: float = .025

    def validate(self) -> None:
        validate_spec(asdict(self))

    def to_dict(self) -> dict:
        self.validate()
        return asdict(self)


def check_resources(resources: dict, *, before_dispatch: bool, retained_output_bytes: int = 0) -> dict:
    for key in ("available_memory_bytes", "free_disk_bytes"):
        if type(resources.get(key)) is not int or resources[key] < 0:
            raise ValueError("Actual available RAM/disk measurements are required")
    if type(retained_output_bytes) is not int or not 0 <= retained_output_bytes <= BOUNDS["output_bytes"]:
        raise OSError("5% retained output exceeds its finite safeguard")
    ram = BOUNDS["memory_reserve_bytes"] + (BOUNDS["rss_bytes"] if before_dispatch else 0)
    # Budget the complete final source run AND another complete durable copy.
    # As the run grows, consumed output bytes reduce the remaining allocation.
    disk = (BOUNDS["disk_reserve_bytes"] + BOUNDS["durable_copy_bytes"]
            + (BOUNDS["output_bytes"] if before_dispatch
               else BOUNDS["output_bytes"] - retained_output_bytes))
    if resources["available_memory_bytes"] < ram or resources["free_disk_bytes"] < disk:
        raise MemoryError(f"5% BioNet has RAM {resources['available_memory_bytes']}/{ram} required bytes and disk {resources['free_disk_bytes']}/{disk} required bytes; full remaining output,12GiB copy and16GiB disk reserve required")
    return {"schema": "digifly-pdcm-bionet-5pct-resource-admission-v1", "workflow": WORKFLOW,
            "initial": before_dispatch, "observed": dict(resources),
            "retained_output_bytes": retained_output_bytes,
            "required_available_memory_bytes": ram, "required_free_disk_bytes": disk,
            "guards": BOUNDS, "resource_contract": RESOURCE_CONTRACT,
            "measurement": "Available resources and sampled process-tree guard, not an OS quota or guaranteed forecast"}


def _sample_resources(root: Path, process, started: float) -> dict:
    retained = retained_bytes(root)
    rss = _tree_rss(process.pid)
    if rss > BOUNDS["rss_bytes"]:
        raise MemoryError(f"5% condition RSS {rss} exceeds {BOUNDS['rss_bytes']} bytes (24GiB safeguard)")
    admission = check_resources(_resources(root), before_dispatch=False,
                                retained_output_bytes=retained)
    elapsed = time.monotonic() - started
    if elapsed > BOUNDS["timeout_seconds"]:
        raise TimeoutError("5% condition and verification exceeded its7200-second safeguard")
    return {"elapsed_s": elapsed, "process_tree_rss_bytes": rss,
            "retained_output_bytes": retained, **admission}


def prepare_run(spec: PDCMBioNet5PctAppSpec, *, output_root: str | Path) -> Path:
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
    root = output / f"pdcm-bionet-5pct-v1-app-{stem}-{uuid4().hex[:16]}"
    root.mkdir(exist_ok=False)
    try:
        shutil.copytree(assets, root / "frozen")
        shutil.copyfile(worker_path(WORKER_FILENAME), root / WORKER_FILENAME)
        shutil.copyfile(package_root() / "core/pdcm_bionet_5pct_app.py", root / CONTROLLER_FILENAME)
        write_new(root / "resource_contract.json", RESOURCE_CONTRACT)
        write_new(root / "request.json", {"schema_version": 1, "workflow": WORKFLOW, "run_root": ".",
                  "spec": condition, "bounds": BOUNDS, "model_contract": MODEL_CONTRACT,
                  "source_sha256": SOURCE_PINS, "resource_contract": RESOURCE_CONTRACT,
                  "resource_contract_sha256": sha(root / "resource_contract.json"), "scientific_acceptance": False})
        write_new(root / "bundle_manifest.json", {"schema_version": 1, "workflow": WORKFLOW,
                  "files_sha256": {"request.json": sha(root / "request.json"),
                  WORKER_FILENAME: sha(root / WORKER_FILENAME),
                  CONTROLLER_FILENAME: sha(root / CONTROLLER_FILENAME),
                  "resource_contract.json": sha(root / "resource_contract.json"),
                  **{"frozen/" + n: h for n, h in inventory(root / "frozen").items()}}})
        write_new(root / "run_manifest.json", {"schema_version": 1, "workflow": WORKFLOW,
                  "state": "prepared", "prepared_at": _utc(), "scientific_acceptance": False,
                  "preparation_resource_admission": admission,
                  "resource_contract_sha256": sha(root / "resource_contract.json"),
                  "request_sha256": sha(root / "request.json"), "bundle_manifest_sha256": sha(root / "bundle_manifest.json")})
        validate_prepared_bundle(root)
    except BaseException as exc:
        write_new(root / "preparation_failure.json", {"error": str(exc), "scientific_acceptance": False})
        raise
    return root


def validate_prepared_bundle(run_root: str | Path) -> dict:
    root = real_path(Path(run_root).expanduser())
    if not root.is_dir():
        raise ValueError("5% app bundle must be an owned directory")
    request = validate_request(root)
    state = json.loads(real_path(root / "run_manifest.json").read_text())
    if (state.get("workflow") != WORKFLOW or state.get("scientific_acceptance") is not False
            or state.get("request_sha256") != sha(root / "request.json")
            or state.get("bundle_manifest_sha256") != sha(root / "bundle_manifest.json")
            or state.get("resource_contract_sha256") != sha(root / "resource_contract.json")
            or sha(root / WORKER_FILENAME) != sha(worker_path(WORKER_FILENAME))
            or sha(root / CONTROLLER_FILENAME) != sha(package_root() / "core/pdcm_bionet_5pct_app.py")):
        raise ValueError("Source-bound 5% worker, request, resource contract or state changed")
    return request


def _check_output(root: Path, request: dict) -> dict:
    validate_prepared_bundle(root)
    result = json.loads(real_path(root / "worker_result.json").read_text())
    audit = json.loads(real_path(root / "analysis/audit.json").read_text())
    runtime = json.loads(real_path(root / "runtime.json").read_text())
    structure = json.loads(real_path(root / "structure/manifest.json").read_text())
    validate_structure(structure, request["spec"])
    guard = json.loads(real_path(root / "input_resource_guard.json").read_text())
    adapter = json.loads(real_path(root / "execution_adapter.json").read_text())
    if (adapter.get("schema") != "digifly-pdcm-bionet-5pct-v4-execution-adapter-v1"
            or adapter.get("workflow") != WORKFLOW or adapter.get("spec") != request["spec"]
            or adapter.get("runner_source_sha256") != SOURCE_PINS["scripts/run_pdcm_bionet_scalable_v4.py"]
            or adapter.get("policy") != MODEL_CONTRACT["execution_adapter_policy"]
            or adapter.get("original_approved_condition") != [.02, 60100., "balanced_poisson"]
            or adapter.get("effective_approved_condition") != [.05, 60100., "balanced_poisson"]
            or adapter.get("restored_approved_condition") != [.02, 60100., "balanced_poisson"]
            or adapter.get("runner_returned") is not True):
        raise ValueError("5% exact runner invocation/restoration did not pass")
    if (guard.get("workflow") != WORKFLOW or guard.get("bounds") != BOUNDS
            or guard.get("external_events") != structure["external_events"]
            or guard.get("external_cells") != MODEL_CONTRACT["cortical_cells"]
            or type(guard.get("maximum_events_in_one_cell")) is not int
            or not 0 < guard["maximum_events_in_one_cell"] <= BOUNDS["external_events_per_cell"]
            or guard.get("batch_values_unchanged") is not True
            or guard.get("generator_restored_before_source_replay") is not True):
        raise ValueError("5% complete input resource accounting differs")
    if (result.get("workflow") != WORKFLOW or result.get("spec") != request["spec"]
            or result.get("model_contract") != MODEL_CONTRACT or result.get("source_sha256") != SOURCE_PINS
            or result.get("resource_contract") != RESOURCE_CONTRACT
            or result.get("resource_contract_sha256") != sha(root / "resource_contract.json")
            or result.get("execution_claim_sha256") != sha(root / "execution_claim.json")
            or result.get("worker_claim_sha256") != sha(root / "worker_claim.json")
            or result.get("input_resource_guard_sha256") != sha(root / "input_resource_guard.json")
            or result.get("worker_sha256") != sha(root / WORKER_FILENAME)
            or result.get("controller_source_sha256") != sha(root / CONTROLLER_FILENAME)
            or result.get("execution_adapter_sha256") != sha(root / "execution_adapter.json")
            or result.get("audit") != audit or result.get("technical_acceptance") is not True
            or result.get("scientific_acceptance") is not False or result.get("runtime_sha256") != sha(root / "runtime.json")
            or runtime.get("versions") != {"bmtk": "1.2.0", "neuron": "9.0.1", "numpy": "1.26.4", "h5py": "3.11.0"}
            or audit.get("technical_acceptance") is not True or audit.get("scientific_acceptance") is not False
            or audit.get("cortical_cells") != MODEL_CONTRACT["cortical_cells"]
            or audit.get("recurrent_contacts_censused") != MODEL_CONTRACT["recurrent_contacts"]):
        raise ValueError("Actual 5% audit, source, condition or runtime did not pass")
    for key in ("network_seed", "stimulus_seed", "duration_ms", "scale", "drive"):
        if audit.get(key) != request["spec"][key]:
            raise ValueError("5% audit condition differs from its request")
    expected_modules = {role: {"path": str(root / "frozen" / relative), "sha256": SOURCE_PINS[relative]}
                        for role in ("build", "run", "analyze")
                        for relative in (f"scripts/{role}_pdcm_bionet_scalable_v4.py",)}
    if result.get("executed_scientific_modules") != expected_modules:
        raise ValueError("5% executed scientific modules differ from frozen owned sources")
    for folder, field, binding in (("simulation", "files", "artifact_manifest_sha256"),
                                   ("analysis", "files_sha256", "analysis_manifest_sha256")):
        name = "artifact_manifest.json" if folder == "simulation" else "analysis_manifest.json"
        manifest = root / folder / name
        observed = inventory(root / folder)
        observed.pop(name)
        if json.loads(manifest.read_text()).get(field) != observed or result.get(binding) != sha(manifest):
            raise ValueError("5% observed artifact inventory changed")
    if (audit.get("run_artifact_manifest_sha256") != result["artifact_manifest_sha256"]
            or result.get("raw_run_root") != str(root / "simulation") or result.get("analysis_root") != str(root / "analysis")):
        raise ValueError("5% result points outside its owned run")
    return result


def run_prepared(run_root: str | Path, *, python_executable: str | Path,
                 progress: Callable[[str], None] | None = None,
                 cancel_requested: Callable[[], bool] | None = None) -> dict:
    root = real_path(Path(run_root).expanduser())
    request = validate_prepared_bundle(root)
    if json.loads((root / "run_manifest.json").read_text())["state"] != "prepared" or (root / "execution_claim.json").exists():
        raise FileExistsError("5% app condition was already attempted; no overwrite or automatic retry")
    runtime = external_runtime_launcher(python_executable)
    if not runtime.is_file() or not os.access(runtime, os.X_OK):
        raise ValueError("Choose the qualified scientific BioNet Python")
    resources = _resources(root)
    admission = check_resources(resources, before_dispatch=True)
    if cancel_requested and cancel_requested():
        raise InterruptedError("5% condition cancelled before dispatch")
    env = sanitized_external_environment({"PATH": external_runtime_path(runtime), "NEURON_MODULE_OPTIONS": "-nogui",
          "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
          "PYTHONPATH": os.pathsep.join((str(root / "frozen/src"), str(root / "frozen"))),
          "MPLBACKEND": "Agg", "MPLCONFIGDIR": str(root / "matplotlib"),
          "TMPDIR": str(root / "temporary"), "TEMP": str(root / "temporary"), "TMP": str(root / "temporary"),
          "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"})
    env.pop("DISPLAY", None)
    (root / "matplotlib").mkdir(exist_ok=False)
    (root / "temporary").mkdir(exist_ok=False)
    command = [str(runtime), "-B", str(root / WORKER_FILENAME), "--request", str(root / "request.json")]
    write_new(root / "execution_claim.json", {"schema": CLAIM_SCHEMA, "workflow": WORKFLOW, "started_at": _utc(), "command": command,
              "request_sha256": sha(root / "request.json"), "bounds": BOUNDS, "initial_resources": resources,
              "resource_contract": RESOURCE_CONTRACT, "resource_contract_sha256": sha(root / "resource_contract.json"),
              "bundle_manifest_sha256": sha(root / "bundle_manifest.json"), "worker_sha256": sha(root / WORKER_FILENAME),
              "controller_source_sha256": sha(root / CONTROLLER_FILENAME),
              "resource_admission": admission, "python_sha256": sha(runtime), "scientific_acceptance": False})
    _state(root, "running")
    started, process, peak, failure, previous = time.monotonic(), None, 0, None, 0
    peak_output, sample_count, minimum_ram, minimum_disk = 0, 0, None, None
    try:
        if progress:
            progress("Starting the independent 5% full-duration BioNet paper condition")
        with (root / "worker.log").open("x") as log, (root / "resource_samples.jsonl").open("x") as samples:
            process = subprocess.Popen(command, cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT,
                                       start_new_session=os.name != "nt")
            while process.poll() is None:
                if cancel_requested and cancel_requested():
                    raise InterruptedError("5% condition cancelled by the user")
                sample = _sample_resources(root, process, started)
                peak = max(peak, sample["process_tree_rss_bytes"])
                peak_output = max(peak_output, sample["retained_output_bytes"])
                ram, disk = (sample["observed"][key] for key in ("available_memory_bytes", "free_disk_bytes"))
                minimum_ram = ram if minimum_ram is None else min(minimum_ram, ram)
                minimum_disk = disk if minimum_disk is None else min(minimum_disk, disk)
                samples.write(json.dumps(sample, sort_keys=True, allow_nan=False) + "\n")
                samples.flush()
                sample_count += 1
                if progress and (root / "progress.jsonl").is_file():
                    lines = (root / "progress.jsonl").read_text().splitlines()
                    for line in lines[previous:]:
                        try:
                            message = json.loads(line)["message"]
                        except json.JSONDecodeError:
                            break  # A concurrent append may not yet be complete.
                        progress(message)
                        previous += 1
                time.sleep(BOUNDS["sample_interval_seconds"])
            if process.returncode != 0:
                raise RuntimeError(f"BioNet5% worker failed ({process.returncode}); partial evidence retained in {root / 'worker.log'}")
        # Leave room for the final resource/summary/state receipts before success.
        final_size = retained_bytes(root)
        if final_size + 1024 * 1024 > BOUNDS["output_bytes"]:
            raise OSError("5% output lacks room for complete final receipts")
        check_resources(_resources(root), before_dispatch=False, retained_output_bytes=final_size)
        peak_output = max(peak_output, final_size)
        if not 0 < peak <= BOUNDS["rss_bytes"]:
            raise ValueError("No valid process-tree RAM measurement was recorded")
        result = _check_output(root, request)
        check_resources(_resources(root), before_dispatch=False, retained_output_bytes=retained_bytes(root))
        if time.monotonic() - started > BOUNDS["timeout_seconds"]:
            raise TimeoutError("5% condition and verification exceeded its7200-second time guard")
    except BaseException as exc:
        failure = str(exc)
        if process is not None:
            _stop(process)
        _state(root, "cancelled" if isinstance(exc, InterruptedError) else "failed", error=failure)
        raise
    finally:
        write_new(root / "execution.json", {"schema": EXECUTION_SCHEMA, "workflow": WORKFLOW, "command": command,
                  "exit_code": process.returncode if process is not None else None,
                  "walltime_s": time.monotonic() - started, "sampled_process_tree_peak_rss_bytes": peak,
                  "failure": failure, "bounds": BOUNDS, "scientific_acceptance": False,
                  "resource_contract": RESOURCE_CONTRACT,
                  "resource_contract_sha256": sha(root / "resource_contract.json"),
                  "sample_count": sample_count, "sampled_retained_output_peak_bytes": peak_output,
                  "sampled_minimum_available_memory_bytes": minimum_ram,
                  "sampled_minimum_free_disk_bytes": minimum_disk,
                  "resource_samples_sha256": sha(root / "resource_samples.jsonl") if (root / "resource_samples.jsonl").exists() else None,
                  "claim_sha256": sha(root / "execution_claim.json"),
                  "worker_log_sha256": sha(root / "worker.log") if (root / "worker.log").exists() else None,
                  "limits": "0.25-second sampled process-tree/resources/logical-output supervisor; owned temporary files included, complete durable-copy capacity reserved, not a kernel quota"})
    result.update(state="completed", run_root=str(root), summary_path=str(root / "summary.json"),
                  execution_sha256=sha(root / "execution.json"), caveats=CAVEATS)
    write_new(root / "summary.json", result)
    _state(root, "completed", completed_at=_utc(), summary_sha256=sha(root / "summary.json"))
    return result
