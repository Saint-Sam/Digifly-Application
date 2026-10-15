"""One-shot app execution of separately frozen scalable independent BioNet v4.

Only the source-reviewed first 2% paper condition is an executable app scope.
Broader construction/analysis capability is not permission to run that scope.
No accepted primary-v3 source, original author model or graph is executed.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
from importlib import metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import traceback

WORKFLOW = "pdcm_bionet_scalable_v4_app"
GiB = 1024**3
# New source-bound contract. The independently frozen v3 guard is unchanged.
BOUNDS = {"timeout_seconds": 3600, "rss_bytes": 10 * GiB,
          "memory_reserve_bytes": 12 * GiB, "disk_reserve_bytes": 16 * GiB,
          "output_bytes": 4 * GiB}
MODEL_CONTRACT = {
    "construction_policy": "released_method_fixed_unique_integer_variance_scalable_v4",
    "scale": .02, "duration_ms": 60100., "drive": "balanced_poisson", "dt_ms": .025,
    "external_delay_ms": .5, "clock_delay_ms": .5, "clock_source_interval_ms": .025,
    "cortical_cells": 1539, "recurrent_contacts": 118550,
    "same_time_event_policy": "unchanged independent DigiflyPDCMEvent and BioNet event routing; ties counted, not resolved by forced ordering",
    "scientific_acceptance": False,
}
# Separate scientific sources, never the accepted primary-v3 or author graph.
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
    "Separately versioned scalable released-method v4; accepted primary-v3 remains unchanged.",
    "Paper windows and feasible unique samples are explicit; missing NEST values are not fabricated.",
    "Same-time-event census does not prove internal event order.",
    "Resource projections are planning scenarios; the sampled watchdog is not a kernel quota.",
]


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
        raise ValueError("Unexpected scalable BioNet condition fields")
    if not isinstance(spec["name"], str) or not spec["name"].strip() or len(spec["name"]) > 160:
        raise ValueError("Give the condition a name of at most 160 characters")
    for key in ("scale", "duration_ms", "dt_ms"):
        if type(spec[key]) not in (int, float) or spec[key] != MODEL_CONTRACT[key]:
            raise ValueError("Scalable app execution initially supports exact 2% / 60,100 ms / 0.025 ms only")
    if spec["drive"] != MODEL_CONTRACT["drive"]:
        raise ValueError("First scalable app condition is balanced Poisson only; DC and other scales are not admitted")
    for key in ("network_seed", "stimulus_seed"):
        if type(spec[key]) is not int or not 0 <= spec[key] < 2**31:
            raise ValueError(f"{key} must be a nonnegative 31-bit integer")


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
    for name in ("request.json", "bundle_manifest.json", "pdcm_bionet_scalable_app_worker.py"):
        if not real_path(root / name).is_file():
            raise ValueError("App scalable bundle is incomplete")
    request = json.loads((root / "request.json").read_text())
    if (request.get("schema_version") != 1 or request.get("workflow") != WORKFLOW
            or request.get("run_root") != "." or request.get("bounds") != BOUNDS
            or request.get("model_contract") != MODEL_CONTRACT
            or request.get("scientific_acceptance") is not False or request.get("source_sha256") != SOURCE_PINS):
        raise ValueError("Scalable condition, model contract, source or safeguards changed")
    validate_spec(request["spec"])
    validate_sources(root / "frozen")
    expected = {"request.json": sha(root / "request.json"),
                "pdcm_bionet_scalable_app_worker.py": sha(root / "pdcm_bionet_scalable_app_worker.py"),
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


def execute(root: Path) -> dict:
    if sys.flags.optimize:
        raise ValueError("Scientific checks require a non-optimized interpreter")
    request = validate_request(root)
    claim = json.loads(real_path(root / "execution_claim.json").read_text())
    if (claim.get("workflow") != WORKFLOW or claim.get("bounds") != BOUNDS
            or claim.get("request_sha256") != sha(root / "request.json")):
        raise ValueError("Only the source-bound app supervisor may start this worker")
    write_new(root / "worker_claim.json", {"workflow": WORKFLOW, "pid": os.getpid()})
    runtime = checked_runtime()
    write_new(root / "runtime.json", runtime)
    frozen = root / "frozen"
    sys.path[:0] = [str(frozen / "src"), str(frozen)]
    loaded = {}
    for role in ("build", "run", "analyze"):
        relative = f"scripts/{role}_pdcm_bionet_scalable_v4.py"
        module = importlib.import_module(f"scripts.{role}_pdcm_bionet_scalable_v4")
        if Path(module.__file__).resolve() != (frozen / relative).resolve():
            raise ValueError("Scientific import escaped the frozen scalable bundle")
        loaded[role] = module
    spec = request["spec"]
    progress(root, "Building the independently authored 2% full-duration SONATA model")
    loaded["build"].build(root / "structure", scale=spec["scale"], duration_ms=spec["duration_ms"],
                         drive=spec["drive"], network_seed=spec["network_seed"], stimulus_seed=spec["stimulus_seed"])
    progress(root, "Running the 1,539-cell BioNet paper condition with its full clock and Poisson inputs")
    loaded["run"]._run(root / "structure", root / "simulation")
    progress(root, "Checking owned outputs, every recurrent arrival and paper-window measurements")
    audit = loaded["analyze"].analyze(root / "simulation", root / "analysis")
    validate_sources(frozen)
    if audit.get("technical_acceptance") is not True or audit.get("scientific_acceptance") is not False:
        raise ValueError("Scalable independent technical audit did not pass")
    for key in ("network_seed", "stimulus_seed", "duration_ms", "scale", "drive"):
        if audit.get(key) != spec[key]:
            raise ValueError("Observed condition differs from app request")
    for name, digest in runtime["installed_files_sha256"].items():
        if sha(Path(name)) != digest:
            raise ValueError("Scientific runtime changed during execution")
    result = {"schema_version": 1, "workflow": WORKFLOW, "name": spec["name"], "engine": "bionet",
              "spec": spec, "state": "completed_technical_unreviewed", "technical_acceptance": True,
              "scientific_acceptance": False, "raw_run_root": str(root / "simulation"), "analysis_root": str(root / "analysis"),
              "audit": audit, "runtime_sha256": sha(root / "runtime.json"), "source_sha256": SOURCE_PINS,
              "model_contract": MODEL_CONTRACT, "caveats": CAVEATS,
              "artifact_manifest_sha256": sha(root / "simulation/artifact_manifest.json"),
              "analysis_manifest_sha256": sha(root / "analysis/analysis_manifest.json")}
    write_new(root / "worker_result.json", result)
    progress(root, "2% paper condition passed technical checks; independent-network statistical claims remain separate")
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
