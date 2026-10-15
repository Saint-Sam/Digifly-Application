"""One-shot exact 5% app condition using immutable scalable-v4 science.

The new condition/resource contract does not widen or change either accepted
app controller. Inputs remain complete; a resource failure stops and preserves
the attempt rather than clipping events, shortening clocks or retrying.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
from importlib import metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import traceback

WORKFLOW = "pdcm_bionet_5pct_v1_app"
WORKER_FILENAME = "pdcm_bionet_5pct_app_worker.py"
CONTROLLER_FILENAME = "pdcm_bionet_5pct_app_controller.py"
RESOURCE_SCHEMA = "digifly-pdcm-bionet-5pct-resource-contract-v1"
EXECUTION_SCHEMA = "digifly-pdcm-bionet-5pct-app-execution-v1"
CLAIM_SCHEMA = "digifly-pdcm-bionet-5pct-app-execution-claim-v1"
GiB = 1024**3
# These are finite stop safeguards, separate from the unchanged science.
BOUNDS = {"timeout_seconds": 7200, "rss_bytes": 24 * GiB,
          "memory_reserve_bytes": 16 * GiB, "disk_reserve_bytes": 16 * GiB,
          "output_bytes": 12 * GiB, "durable_copy_bytes": 12 * GiB,
          "structure_bytes": 4 * GiB, "external_event_count": 220_000_000,
          "external_events_per_cell": 80_000,
          "sample_interval_seconds": .25}
MODEL_CONTRACT = {
    "construction_policy": "released_method_fixed_unique_integer_variance_scalable_v4",
    "scale": .05, "duration_ms": 60100., "drive": "balanced_poisson", "dt_ms": .025,
    "network_seed": 28, "stimulus_seed": 208,
    "external_delay_ms": .5, "clock_delay_ms": .5, "clock_source_interval_ms": .025,
    "cortical_cells": 3854, "recurrent_contacts": 744950,
    "clock_source_events": 2404000, "clock_contacts": 3854,
    "source_streaming_policy": "unchanged bounded v4 graph/input writer, full source events, no clipping",
    "execution_adapter_policy": "validated exact5% condition replaces only loaded-v4 APPROVED_CONDITION during_run; restored in finally; unchanged scientific checks",
    "same_time_event_policy": "unchanged independent DigiflyPDCMEvent and BioNet event routing; ties counted, not resolved by forced ordering",
    "scientific_acceptance": False,
}
# Exact immutable scalable-v4 scientific sources; only this app contract is new.
# Independent source/resource review and packaged execution are separate gates.
SOURCE_PINS = {
    "scripts/build_pdcm_bionet_scalable_v4.py": "ef0f3b61cfcb353236a44e7f2b250ca2dae19a8ae1cc1e54a08251f6bf5135bc",
    "scripts/run_pdcm_bionet_scalable_v4.py": "e61b35b0afe1728a30dccbeee23740412bdcdbe7e4ad6532cf92171d65cf1636",
    "scripts/analyze_pdcm_bionet_scalable_v4.py": "a8b3209aa11e036fa0eeaaac1e8b643eb01b024020e72a15b7ef86a684fd58ea",
    "scripts/probe_pdcm_bionet_independent.py": "f70403b8e9865e05a79a6a0a216aa681aa276082ee8fb3b48cac03b1c96faca0",
    "src/digifly_app/pdcm_bionet/event_cell.py": "ffdfcd603910f51be491c5a3e0304034b4afff3a35f4221c78675d5cd797baff",
    "src/digifly_app/pdcm_bionet/mod/DigiflyPDCMEvent.mod": "d25f643ff90083706aadf4f2f992183d2d267289b9e38e542fca7439b74c1ebc",
    "src/digifly_app/workers/pdcm_netpyne_worker.py": "359823360444e9a1d759fe453dd614f7c7318ee276d00e9468b02aabba086f10",
}
SCAFFOLD = ("scripts/__init__.py", "src/digifly_app/__init__.py",
            "src/digifly_app/pdcm_bionet/__init__.py", "src/digifly_app/workers/__init__.py")
CAVEATS = [
    "One independently constructed network, not a confirmatory cohort or statistical equivalence result.",
    "BioNet and NetPyNE share NEURON; this is not independent-solver replication.",
    "Separate 5% app condition/resource contract uses unchanged scalable-v4 scientific sources.",
    "The 2% measured costs forecast 5% headroom; they do not guarantee 5% memory or output.",
    "Full durable-copy capacity is reserved; completion still requires checksum-verified copying.",
    "Paper windows and feasible unique samples are explicit; missing NEST values are not fabricated.",
    "Same-time-event census does not prove internal event order.",
    "Resource projections are planning scenarios; the sampled watchdog is not a kernel quota.",
]

RESOURCE_CONTRACT = {
    "schema": RESOURCE_SCHEMA, "workflow": WORKFLOW, "bounds": BOUNDS,
    "condition": {key: MODEL_CONTRACT[key] for key in
                  ("scale", "duration_ms", "drive", "dt_ms", "network_seed", "stimulus_seed")},
    "forecast": {"cortical_cells": 3854, "recurrent_contacts": 744950,
                 "external_event_count_mean": 189_617_904,
                 "source_and_run_input_copy_gib_approximately": 5.95,
                 "forecast_only": True},
    "measured_reference": {
        "scale": .02, "walltime_seconds": 432.01210949989036,
        "sampled_process_tree_peak_rss_bytes": 3014344704,
        "retained_logical_bytes": 1086380628,
        "postflight": "outputs/workstation/bionet-scalable-v4-app-postflight-20261007-v1.json",
        "postflight_sha256": "cd58c215b71faac3e50e9209db841b989a93e839864292fbdd3dea455708bfbf"},
    "planning_basis": [
        "Cell-clock work scales about2.50x; contacts/external inputs about6.28x versus measured2%.",
        "24GiB sampled RSS exceeds the roughly17.64GiB graph/input-only scaled RSS scenario.",
        "7200s exceeds the roughly2715s graph/input-only walltime scenario; it is a stop guard.",
        "4GiB structure budget,220million total and80000 per-cell pre-append stop caps cover expected189617904 inputs.",
        "BMTK materializes all external IDs and retains VecStim time vectors; builder streaming does not bound live RSS.",
        "BMTK recording finalization materializes and sorts CSV data;24GiB RSS and12GiB output remain sampled stop guards.",
        "Analysis retains3725 sampled-cell timestamp lists; temporary H5 indexes are placed inside the guarded run.",
        "12GiB total run capacity covers structure plus its complete input copy, recordings and analysis.",
        "Another12GiB is reserved for a complete durable copy, with16GiB free disk remaining.",
        "At admission require40GiB available RAM and40GiB free disk; sampled execution retains16GiB RAM.",
    ],
    "disk_policy": "same-volume full durable-copy reserve plus remaining output capacity and16GiB reserve",
    "sample_policy": "0.25-second process-tree/resources/logical-output sampling; not a kernel quota",
    "failure_policy": "stop and preserve all partial evidence; no input clipping, automatic retry or overwrite",
    "source_sha256": SOURCE_PINS, "scientific_acceptance": False,
}


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_new(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")


def real_path(path: Path) -> Path:
    path = path.absolute()
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError("App scientific paths cannot contain symbolic links")
    return path


def inventory(root: Path) -> dict[str, str]:
    real_path(root)
    result = {}
    for path in sorted(root.rglob("*")):
        real_path(path)
        if path.is_file():
            result[path.relative_to(root).as_posix()] = sha(path)
    return result


def validate_spec(spec: dict) -> None:
    if set(spec) != {"name", "scale", "duration_ms", "drive", "dt_ms", "network_seed", "stimulus_seed"}:
        raise ValueError("Unexpected 5% BioNet condition fields")
    if not isinstance(spec["name"], str) or not spec["name"].strip() or len(spec["name"]) > 160:
        raise ValueError("Give the condition a name of at most 160 characters")
    for key in ("scale", "duration_ms", "dt_ms"):
        if type(spec[key]) not in (int, float) or spec[key] != MODEL_CONTRACT[key]:
            raise ValueError("5% app execution supports exact 5% / 60,100 ms / 0.025 ms only")
    if spec["drive"] != MODEL_CONTRACT["drive"]:
        raise ValueError("5% app condition is balanced Poisson only")
    for key in ("network_seed", "stimulus_seed"):
        if type(spec[key]) is not int or spec[key] != MODEL_CONTRACT[key]:
            raise ValueError(f"{key} must match the authorized 5% condition ({MODEL_CONTRACT[key]})")


def validate_sources(root: Path) -> None:
    required = {"scripts/build_pdcm_bionet_scalable_v4.py", "scripts/run_pdcm_bionet_scalable_v4.py",
                "scripts/analyze_pdcm_bionet_scalable_v4.py", "src/digifly_app/pdcm_bionet/mod/DigiflyPDCMEvent.mod"}
    if not required.issubset(SOURCE_PINS):
        raise ValueError("Scalable scientific sources are not yet frozen for execution")
    expected = {**SOURCE_PINS, **{name: hashlib.sha256(b"").hexdigest() for name in SCAFFOLD}}
    if inventory(root) != expected:
        raise ValueError("Frozen scalable scientific source identity or inventory differs")


def validate_request(root: Path) -> dict:
    real_path(root)
    for name in ("request.json", "bundle_manifest.json", WORKER_FILENAME, CONTROLLER_FILENAME, "resource_contract.json"):
        if not real_path(root / name).is_file():
            raise ValueError("App scalable bundle is incomplete")
    request = json.loads((root / "request.json").read_text())
    if set(request) != {"schema_version", "workflow", "run_root", "spec", "bounds", "model_contract",
                        "source_sha256", "resource_contract", "resource_contract_sha256", "scientific_acceptance"}:
        raise ValueError("Unexpected 5% request fields")
    if (request.get("schema_version") != 1 or request.get("workflow") != WORKFLOW
            or request.get("run_root") != "." or request.get("bounds") != BOUNDS
            or request.get("model_contract") != MODEL_CONTRACT
            or request.get("scientific_acceptance") is not False or request.get("source_sha256") != SOURCE_PINS
            or request.get("resource_contract") != RESOURCE_CONTRACT
            or json.loads((root / "resource_contract.json").read_text()) != RESOURCE_CONTRACT
            or request.get("resource_contract_sha256") != sha(root / "resource_contract.json")):
        raise ValueError("Scalable condition, model contract, source or safeguards changed")
    validate_spec(request["spec"])
    validate_sources(root / "frozen")
    expected = {"request.json": sha(root / "request.json"),
                WORKER_FILENAME: sha(root / WORKER_FILENAME),
                CONTROLLER_FILENAME: sha(root / CONTROLLER_FILENAME),
                "resource_contract.json": sha(root / "resource_contract.json"),
                **{"frozen/" + name: digest for name, digest in inventory(root / "frozen").items()}}
    if json.loads((root / "bundle_manifest.json").read_text()) != {"schema_version": 1, "workflow": WORKFLOW, "files_sha256": expected}:
        raise ValueError("Scalable app execution bundle differs from its manifest")
    return request


def checked_runtime() -> dict:
    versions, modules = {}, {}
    for name in ("bmtk", "neuron", "numpy", "h5py"):
        module = importlib.import_module(name)
        versions[name] = str(module.__version__)
        source = Path(module.__file__)
        modules[name] = {"path": str(source), "sha256": sha(source)}
    expected = {"bmtk": "1.2.0", "neuron": "9.0.1", "numpy": "1.26.4", "h5py": "3.11.0"}
    if versions != expected:
        raise ValueError("Scalable BioNet requires the qualified BMTK1.2.0/NEURON9.0.1/NumPy1.26.4/h5py3.11.0 runtime")
    compiler = Path(sys.prefix) / "bin/nrnivmodl"
    if not compiler.is_file() or not os.access(compiler, os.X_OK):
        raise ValueError("Selected BioNet runtime lacks its NEURON compiler")
    import bmtk
    event_mod = Path(bmtk.__file__).parent / "utils/scripts/bionet/mechanisms/modfiles/vecevent.mod"
    if not event_mod.is_file():
        raise ValueError("Selected BioNet runtime lacks its VecStim source")
    installed, distributions = {}, {}
    for name in ("bmtk", "neuron", "numpy", "h5py", "scipy", "pandas"):
        distribution = metadata.distribution(name)
        distributions[name] = distribution.version
        for relative in distribution.files or ():
            path = Path(distribution.locate_file(relative)).resolve()
            if path.is_file() and path.suffix != ".pyc":
                installed[str(path)] = sha(path)
    tool = os.environ.get("CXX") or shutil.which("c++")
    if sys.platform == "darwin" and not os.environ.get("CXX"):
        tool = subprocess.run(["/usr/bin/xcrun", "--find", "clang++"], capture_output=True, text=True, check=True, timeout=15).stdout.strip()
    if not tool or not Path(tool).is_file():
        raise ValueError("Selected runtime lacks an identifiable C++ compiler")
    tool = Path(tool).resolve()
    version = subprocess.run([str(tool), "--version"], capture_output=True, text=True, check=True, timeout=15).stdout
    return {"python_executable": sys.executable, "python_version": sys.version,
            "python_sha256": sha(Path(sys.executable)), "versions": versions, "module_sources": modules,
            "compiler": str(compiler), "compiler_sha256": sha(compiler),
            "scientific_distributions": distributions, "installed_files_sha256": installed,
            "cxx_compiler": str(tool), "cxx_compiler_sha256": sha(tool), "cxx_compiler_version": version,
            "bmtk_vecevent_mod_sha256": sha(event_mod),
            "environment": {key: os.environ.get(key) for key in (
                "PATH", "PYTHONPATH", "PYTHONDONTWRITEBYTECODE", "PYTHONNOUSERSITE", "NEURON_MODULE_OPTIONS",
                "MPLBACKEND", "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "CC", "CXX",
                "CFLAGS", "CXXFLAGS", "CPPFLAGS", "LDFLAGS", "SDKROOT", "MACOSX_DEPLOYMENT_TARGET")}}


def progress(root: Path, message: str) -> None:
    with (root / "progress.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"message": message}) + "\n")
    print(message, flush=True)


def retained_bytes(root: Path) -> int:
    real_path(root)
    total = 0
    for path in root.rglob("*"):
        real_path(path)
        if path.is_file():
            try:
                total += path.stat().st_size
            except FileNotFoundError:
                # The analyzer may remove its owned temporary index concurrently.
                pass
    return total


def _stdlib_available_memory() -> int:
    """Measured free+inactive RAM on macOS without modifying the scientific runtime."""
    if sys.platform == "darwin":
        output = subprocess.run(["/usr/bin/vm_stat"], capture_output=True, text=True,
                                check=True, timeout=5).stdout
        page = re.search(r"page size of (\d+) bytes", output)
        values = {key: int(value) for key, value in
                  re.findall(r"^(Pages free|Pages inactive):\s+(\d+)\.", output, re.MULTILINE)}
        if page is None or set(values) != {"Pages free", "Pages inactive"}:
            raise ValueError("Actual available RAM measurement is unavailable")
        return sum(values.values()) * int(page.group(1))
    if sys.platform.startswith("linux"):
        text = Path("/proc/meminfo").read_text()
        available = re.search(r"^MemAvailable:\s+(\d+) kB$", text, re.MULTILINE)
        if available is not None:
            return int(available.group(1)) * 1024
    raise ValueError("Actual available RAM requires psutil or a supported measured platform fallback")


def available_memory_bytes() -> int:
    try:
        import psutil
    except ImportError:
        return _stdlib_available_memory()
    return int(psutil.virtual_memory().available)


def phase_guard(root: Path, *, planned_allocation_bytes: int = 0) -> dict:
    """Reserve complete future output/copy before each potentially large phase."""
    if type(planned_allocation_bytes) is not int or planned_allocation_bytes < 0:
        raise ValueError("Phase allocation forecast must be a nonnegative integer")
    retained = retained_bytes(root)
    if retained + planned_allocation_bytes > BOUNDS["output_bytes"]:
        raise OSError("5% phase would exceed the retained-output safeguard")
    available = available_memory_bytes()
    free = int(shutil.disk_usage(root).free)
    required_disk = (BOUNDS["disk_reserve_bytes"] + BOUNDS["durable_copy_bytes"]
                     + max(0, BOUNDS["output_bytes"] - retained))
    if available < BOUNDS["memory_reserve_bytes"] or free < required_disk:
        raise MemoryError("5% phase lacks available RAM or complete output/durable-copy disk reserve")
    return {"retained_bytes": retained, "planned_allocation_bytes": planned_allocation_bytes,
            "available_memory_bytes": available, "free_disk_bytes": free,
            "required_free_disk_bytes": required_disk}


def validate_structure(manifest: dict, spec: dict) -> None:
    for key in ("network_seed", "stimulus_seed", "duration_ms", "scale", "drive", "dt_ms"):
        if manifest.get(key) != spec[key]:
            raise ValueError("Built 5% structure differs from its requested condition")
    for key, value in (("cortical_nodes", 3854), ("recurrent_contacts", 744950),
                       ("clock_events", 2404000), ("clock_contacts", 3854),
                       ("external_nodes", 3854), ("external_contacts", 3854)):
        if manifest.get(key) != value:
            raise ValueError("Built 5% cell/contact/full-clock count differs")
    events = manifest.get("external_events")
    if type(events) is not int or not 0 <= events <= BOUNDS["external_event_count"]:
        raise MemoryError("Complete Poisson input exceeds the distinct 5% event safeguard; no events clipped")


def guarded_build(builder, output: Path, spec: dict) -> tuple[dict, dict]:
    """Check unchanged batches before append; restore generation for source replay.

    The immutable generator draws exactly the original events. This adapter
    accepts each complete batch or fails the entire attempt before its append.
    No event is edited, redrawn, rounded, clipped or selected differently.
    """
    original = builder._event_batches
    cells, total = {}, 0

    def checked_batches(node, *args, **kwargs):
        nonlocal total
        for batch in original(node, *args, **kwargs):
            cell_total = cells.get(node, 0) + len(batch)
            next_total = total + len(batch)
            if (cell_total > BOUNDS["external_events_per_cell"]
                    or next_total > BOUNDS["external_event_count"]):
                raise MemoryError("Complete Poisson batch exceeds 5% input resource safeguard; attempt preserved without clipping")
            cells[node], total = cell_total, next_total
            yield batch

    builder._event_batches = checked_batches
    try:
        manifest = builder.build(output, scale=spec["scale"], duration_ms=spec["duration_ms"],
                                 drive=spec["drive"], network_seed=spec["network_seed"],
                                 stimulus_seed=spec["stimulus_seed"],
                                 max_structure_bytes=BOUNDS["structure_bytes"])
    finally:
        builder._event_batches = original
    validate_structure(manifest, spec)
    if manifest["external_events"] != total or len(cells) != MODEL_CONTRACT["cortical_cells"]:
        raise ValueError("Complete input batch accounting differs from built 5% ownership")
    return manifest, {"schema": "digifly-pdcm-bionet-5pct-input-resource-guard-v1",
                      "workflow": WORKFLOW, "external_events": total,
                      "external_cells": len(cells), "maximum_events_in_one_cell": max(cells.values()),
                      "bounds": BOUNDS, "batch_values_unchanged": True,
                      "generator_restored_before_source_replay": True,
                      "scientific_acceptance": False}


def run_exact_condition(runner, source: Path, output: Path, spec: dict) -> dict:
    """Execute unchanged v4 science under this condition's separately bound gate."""
    validate_spec(spec)
    manifest = json.loads(real_path(source / "manifest.json").read_text())
    validate_structure(manifest, spec)
    original = runner.APPROVED_CONDITION
    if original != (.02, 60100., "balanced_poisson"):
        raise ValueError("Frozen v4 execution admission guard changed")
    source_hash = sha(Path(runner.__file__))
    if source_hash != SOURCE_PINS["scripts/run_pdcm_bionet_scalable_v4.py"]:
        raise ValueError("Executed v4 runner source changed")
    runner.APPROVED_CONDITION = (.05, 60100., "balanced_poisson")
    succeeded = False
    try:
        result = runner._run(source, output)
        succeeded = True
        return result
    finally:
        runner.APPROVED_CONDITION = original
        write_new(output.parent / "execution_adapter.json", {
            "schema": "digifly-pdcm-bionet-5pct-v4-execution-adapter-v1",
            "workflow": WORKFLOW, "policy": MODEL_CONTRACT["execution_adapter_policy"],
            "runner_source_sha256": source_hash,
            "original_approved_condition": list(original),
            "effective_approved_condition": [.05, 60100., "balanced_poisson"],
            "restored_approved_condition": list(runner.APPROVED_CONDITION),
            "spec": spec, "runner_returned": succeeded, "scientific_acceptance": False})


def execute(root: Path) -> dict:
    if sys.flags.optimize:
        raise ValueError("Scientific checks require a non-optimized interpreter")
    request = validate_request(root)
    claim = json.loads(real_path(root / "execution_claim.json").read_text())
    if (claim.get("schema") != CLAIM_SCHEMA or claim.get("workflow") != WORKFLOW
            or claim.get("bounds") != BOUNDS or claim.get("resource_contract") != RESOURCE_CONTRACT
            or claim.get("request_sha256") != sha(root / "request.json")
            or claim.get("resource_contract_sha256") != sha(root / "resource_contract.json")
            or claim.get("bundle_manifest_sha256") != sha(root / "bundle_manifest.json")
            or claim.get("worker_sha256") != sha(root / WORKER_FILENAME)
            or claim.get("controller_source_sha256") != sha(root / CONTROLLER_FILENAME)
            or claim.get("python_sha256") != sha(Path(sys.executable))):
        raise ValueError("Only the source-bound app supervisor may start this worker")
    write_new(root / "worker_claim.json", {"workflow": WORKFLOW, "pid": os.getpid(),
              "execution_claim_sha256": sha(root / "execution_claim.json")})
    temporary = real_path(root / "temporary")
    if not temporary.is_dir() or any(os.environ.get(key) != str(temporary) for key in ("TMPDIR", "TEMP", "TMP")):
        raise ValueError("5% analysis temporary files must stay inside the guarded owned run")
    phase_guard(root)
    runtime = checked_runtime()
    write_new(root / "runtime.json", runtime)
    frozen = root / "frozen"
    sys.path[:0] = [str(frozen / "src"), str(frozen)]
    loaded = {}
    loaded_identity = {}
    for role in ("build", "run", "analyze"):
        relative = f"scripts/{role}_pdcm_bionet_scalable_v4.py"
        module = importlib.import_module(f"scripts.{role}_pdcm_bionet_scalable_v4")
        if Path(module.__file__).resolve() != (frozen / relative).resolve():
            raise ValueError("Scientific import escaped the frozen scalable bundle")
        loaded[role] = module
        loaded_identity[role] = {"path": str(Path(module.__file__).resolve()),
                                 "sha256": sha(Path(module.__file__))}
    spec = request["spec"]
    progress(root, "Building the independently authored 5% full-duration SONATA model")
    phase_guard(root, planned_allocation_bytes=BOUNDS["structure_bytes"])
    structure, input_guard = guarded_build(loaded["build"], root / "structure", spec)
    write_new(root / "input_resource_guard.json", input_guard)
    phase_guard(root, planned_allocation_bytes=retained_bytes(root / "structure"))
    progress(root, "Running the 3,854-cell BioNet paper condition with its full clock and Poisson inputs")
    run_exact_condition(loaded["run"], root / "structure", root / "simulation", spec)
    phase_guard(root)
    progress(root, "Checking owned outputs, every recurrent arrival and paper-window measurements")
    audit = loaded["analyze"].analyze(root / "simulation", root / "analysis")
    phase_guard(root)
    validate_sources(frozen)
    if audit.get("technical_acceptance") is not True or audit.get("scientific_acceptance") is not False:
        raise ValueError("Scalable independent technical audit did not pass")
    for key in ("network_seed", "stimulus_seed", "duration_ms", "scale", "drive"):
        if audit.get(key) != spec[key]:
            raise ValueError("Observed condition differs from app request")
    if audit.get("cortical_cells") != 3854 or audit.get("recurrent_contacts_censused") != 744950:
        raise ValueError("Actual 5% cell/contact ownership did not pass")
    for name, digest in runtime["installed_files_sha256"].items():
        if sha(Path(name)) != digest:
            raise ValueError("Scientific runtime changed during execution")
    result = {"schema_version": 1, "workflow": WORKFLOW, "name": spec["name"], "engine": "bionet",
              "spec": spec, "state": "completed_technical_unreviewed", "technical_acceptance": True,
              "scientific_acceptance": False, "raw_run_root": str(root / "simulation"), "analysis_root": str(root / "analysis"),
              "audit": audit, "runtime_sha256": sha(root / "runtime.json"), "source_sha256": SOURCE_PINS,
              "model_contract": MODEL_CONTRACT, "caveats": CAVEATS,
              "resource_contract": RESOURCE_CONTRACT,
              "resource_contract_sha256": sha(root / "resource_contract.json"),
              "execution_claim_sha256": sha(root / "execution_claim.json"),
              "worker_claim_sha256": sha(root / "worker_claim.json"),
              "worker_sha256": sha(root / WORKER_FILENAME),
              "controller_source_sha256": sha(root / CONTROLLER_FILENAME),
              "input_resource_guard_sha256": sha(root / "input_resource_guard.json"),
              "execution_adapter_sha256": sha(root / "execution_adapter.json"),
              "executed_scientific_modules": loaded_identity,
              "artifact_manifest_sha256": sha(root / "simulation/artifact_manifest.json"),
              "analysis_manifest_sha256": sha(root / "analysis/analysis_manifest.json")}
    write_new(root / "worker_result.json", result)
    progress(root, "5% paper condition passed technical checks; independent-network statistical claims remain separate")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    args = parser.parse_args()
    root = args.request.absolute().parent
    try:
        execute(root)
    except BaseException:
        if not (root / "worker_failure.json").exists():
            write_new(root / "worker_failure.json", {"traceback": traceback.format_exc(), "scientific_acceptance": False})
        raise


if __name__ == "__main__":
    main()
