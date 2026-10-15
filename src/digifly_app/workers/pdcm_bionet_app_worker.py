"""Standalone, one-shot app worker for the approved independent PDCM v3.

Scientific sources are immutable package assets, not reconstructed translations.
No original-author model is executed; the shared metric implementation is used
only after the independent BioNet simulation has finished.
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

WORKFLOW = "pdcm_bionet_primary_v3_app"
GiB = 1024**3
BOUNDS = {"timeout_seconds": 900, "rss_bytes": 2 * GiB,
          "memory_reserve_bytes": 8 * GiB, "disk_reserve_bytes": 10 * GiB,
          "output_bytes": 2 * GiB}
SOURCE_PINS = {
    "scripts/app_pdcm_bionet_dc_v1.py": "33bfbe3079d90a10adafe3ed367dae1a08eedb03f2e602b051d033f8bbfc6316",
    "scripts/pdcm_bionet_dc_qualification_v1.py": "3ec3d626076a8782183c25daaf15f81e260d54e8592a6c652786f644415dc2c5",
    "scripts/build_pdcm_bionet_primary_v3.py": "d634ac906a95ee33074f01b9976e10603832bf4a186c40c768288c35239bda73",
    "scripts/run_pdcm_bionet_primary_v3.py": "96074aa1db9d2a6badc8bf48a74631f379efd1922e79f79eb13b21ba6e2151dd",
    "scripts/analyze_pdcm_bionet_primary_v3.py": "f2bb135328c3e48174b9469e73b2e6baa83e3269c9b960f7f5f3c2feb17b8f7c",
    "scripts/probe_pdcm_bionet_independent.py": "f70403b8e9865e05a79a6a0a216aa681aa276082ee8fb3b48cac03b1c96faca0",
    "src/digifly_app/pdcm_bionet/event_cell.py": "ffdfcd603910f51be491c5a3e0304034b4afff3a35f4221c78675d5cd797baff",
    "src/digifly_app/pdcm_bionet/mod/DigiflyPDCMEvent.mod": "d25f643ff90083706aadf4f2f992183d2d267289b9e38e542fca7439b74c1ebc",
    "src/digifly_app/workers/pdcm_netpyne_worker.py": "359823360444e9a1d759fe453dd614f7c7318ee276d00e9468b02aabba086f10",
}
SCAFFOLD = ("scripts/__init__.py", "src/digifly_app/__init__.py",
            "src/digifly_app/pdcm_bionet/__init__.py", "src/digifly_app/workers/__init__.py")
CAVEATS = [
    "One independently constructed network, not a cohort or statistical equivalence result.",
    "BioNet and native NetPyNE share NEURON; this is not independent-solver replication.",
    "Released-executable-method v3; paper/reference ambiguities remain explicit.",
    "Paper windows are matched, but exact original sampled IDs and missing NEST values are unavailable.",
    "Same-time-event census does not prove internal event ordering.",
    "The process-tree resource watchdog is sampled, not a kernel memory quota.",
]


def sha(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def write_new(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")


def inventory(root: Path) -> dict[str, str]:
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("Bundle must not contain symlinks")
        if path.is_file():
            result[str(path.relative_to(root))] = sha(path)
    return result


def validate_spec(spec: dict) -> None:
    if set(spec) != {"name", "scale", "duration_ms", "drive", "network_seed", "stimulus_seed"}:
        raise ValueError("Unexpected independent BioNet specification")
    if (not isinstance(spec["name"], str) or not spec["name"].strip()
            or len(spec["name"]) > 160):
        raise ValueError("Give this observation a name of at most 160 characters")
    if (type(spec["scale"]) not in (int, float) or spec["scale"] != .01
            or type(spec["duration_ms"]) not in (int, float) or spec["duration_ms"] != 60100
            or spec["drive"] not in ("balanced_poisson", "balanced_dc")):
        raise ValueError("This approved app adapter supports 1% / 60100 ms / balanced Poisson or DC only")
    for key in ("network_seed", "stimulus_seed"):
        if type(spec[key]) is not int or not 0 <= spec[key] < 2**31:
            raise ValueError(f"{key} must be a nonnegative 31-bit integer")


def validate_sources(root: Path) -> None:
    expected = {**SOURCE_PINS, **{name: hashlib.sha256(b"").hexdigest() for name in SCAFFOLD}}
    if inventory(root) != expected:
        raise ValueError("Approved independent scientific source bytes or file set changed")


def validate_request(root: Path) -> dict:
    for name in ("request.json", "bundle_manifest.json", "pdcm_bionet_app_worker.py"):
        if not (root / name).is_file() or (root / name).is_symlink():
            raise ValueError("App bundle is incomplete or contains a redirected file")
    request = json.loads((root / "request.json").read_text())
    if (request.get("schema_version") != 1 or request.get("workflow") != WORKFLOW
            or request.get("run_root") != "." or request.get("bounds") != BOUNDS
            or request.get("scientific_acceptance") is not False
            or request.get("source_sha256") != SOURCE_PINS):
        raise ValueError("App request condition, source, or safeguards changed")
    validate_spec(request["spec"])
    validate_sources(root / "frozen")
    bundle = json.loads((root / "bundle_manifest.json").read_text())
    expected = {"request.json": sha(root / "request.json"),
                "pdcm_bionet_app_worker.py": sha(root / "pdcm_bionet_app_worker.py"),
                **{"frozen/" + name: digest for name, digest in inventory(root / "frozen").items()}}
    if bundle != {"schema_version": 1, "workflow": WORKFLOW, "files_sha256": expected}:
        raise ValueError("App execution bundle differs from its immutable manifest")
    return request


def checked_runtime() -> dict:
    """Fail before input generation or compilation in an unqualified runtime."""
    versions = {}
    modules = {}
    for name in ("bmtk", "neuron", "numpy", "h5py"):
        module = importlib.import_module(name)
        versions[name] = str(module.__version__)
        source = Path(module.__file__)
        modules[name] = {"path": str(source), "sha256": sha(source)}
    if versions["bmtk"] != "1.2.0" or versions["neuron"] != "9.0.1":
        raise ValueError("Approved BioNet v3 app runtime requires BMTK 1.2.0 and NEURON 9.0.1")
    compiler = Path(sys.prefix) / "bin/nrnivmodl"
    if not compiler.is_file() or not os.access(compiler, os.X_OK):
        raise ValueError("Selected scientific runtime lacks its own NEURON compiler")
    import bmtk
    event_mod = Path(bmtk.__file__).parent / "utils/scripts/bionet/mechanisms/modfiles/vecevent.mod"
    if not event_mod.is_file():
        raise ValueError("Selected BMTK runtime lacks its VecStim source")
    # Record the installed scientific distribution bytes, including native
    # libraries, without copying an environment or exposing inherited secrets.
    installed_files = {}
    distributions = {}
    for name in ("bmtk", "neuron", "numpy", "h5py", "scipy", "pandas"):
        distribution = metadata.distribution(name)
        distributions[name] = distribution.version
        for relative in distribution.files or ():
            path = Path(distribution.locate_file(relative)).resolve()
            if path.is_file() and path.suffix != ".pyc":
                installed_files[str(path)] = sha(path)
    tool = os.environ.get("CXX") or shutil.which("c++")
    if not tool or not Path(tool).is_file():
        raise ValueError("Selected scientific environment lacks an identifiable C++ compiler")
    if sys.platform == "darwin" and not os.environ.get("CXX"):
        actual = subprocess.run(["/usr/bin/xcrun", "--find", "clang++"],
                                capture_output=True, text=True, check=True, timeout=15)
        tool = actual.stdout.strip()
    tool_path = Path(tool).resolve()
    compiler_version = subprocess.run([str(tool_path), "--version"],
                       capture_output=True, text=True, check=True, timeout=15).stdout
    return {"python_executable": sys.executable, "python_version": sys.version,
            "python_sha256": sha(Path(sys.executable)), "versions": versions,
            "module_sources": modules, "compiler": str(compiler), "compiler_sha256": sha(compiler),
            "scientific_distributions": distributions, "installed_files_sha256": installed_files,
            "cxx_compiler": str(tool_path), "cxx_compiler_sha256": sha(tool_path),
            "cxx_compiler_version": compiler_version,
            "bmtk_vecevent_mod_sha256": sha(event_mod),
            "environment": {key: os.environ.get(key) for key in (
                "PATH", "PYTHONPATH", "PYTHONDONTWRITEBYTECODE", "PYTHONNOUSERSITE",
                "NEURON_MODULE_OPTIONS", "MPLBACKEND", "OMP_NUM_THREADS",
                "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "CC", "CXX", "CFLAGS",
                "CXXFLAGS", "CPPFLAGS", "LDFLAGS", "SDKROOT", "MACOSX_DEPLOYMENT_TARGET")}}


def progress(root: Path, message: str) -> None:
    with (root / "progress.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"message": message}) + "\n")
    print(message, flush=True)


def execute(root: Path) -> dict:
    if sys.flags.optimize:
        raise ValueError("Scientific source assertions require a non-optimized interpreter")
    request = validate_request(root)
    if not (root / "execution_claim.json").is_file():
        raise ValueError("Only the app-owned supervisor may start this worker")
    write_new(root / "worker_claim.json", {"workflow": WORKFLOW, "pid": os.getpid()})
    runtime = checked_runtime()
    write_new(root / "runtime.json", runtime)
    frozen = root / "frozen"
    sys.path[:0] = [str(frozen / "src"), str(frozen)]
    builder = importlib.import_module("scripts.build_pdcm_bionet_primary_v3")
    runner = importlib.import_module("scripts.run_pdcm_bionet_primary_v3")
    analyzer = importlib.import_module("scripts.analyze_pdcm_bionet_primary_v3")
    for module, relative in ((builder, "scripts/build_pdcm_bionet_primary_v3.py"),
                             (runner, "scripts/run_pdcm_bionet_primary_v3.py"),
                             (analyzer, "scripts/analyze_pdcm_bionet_primary_v3.py")):
        if Path(module.__file__).resolve() != (frozen / relative).resolve():
            raise ValueError("Scientific import escaped the frozen app bundle")
    spec = request["spec"]
    progress(root, "Building independent full-duration SONATA inputs")
    structure = root / "structure"
    if spec["drive"] == "balanced_dc":
        dc = importlib.import_module("scripts.app_pdcm_bionet_dc_v1")
        if Path(dc.__file__).resolve() != (frozen / "scripts/app_pdcm_bionet_dc_v1.py").resolve():
            raise ValueError("DC adapter import escaped the frozen app bundle")
        reference = root / "structure_reference_poisson"
        builder.build(reference, network_seed=spec["network_seed"],
                      stimulus_seed=spec["stimulus_seed"], duration_ms=60100.)
        dc.transform(reference, structure)
        run_function, analyze_function = dc.run, dc.analyze
    else:
        builder.build(structure, network_seed=spec["network_seed"],
                      stimulus_seed=spec["stimulus_seed"], duration_ms=60100.)
        run_function, analyze_function = runner._run, analyzer.analyze
    progress(root, "Running the approved independent BioNet model")
    simulation = root / "simulation"
    try:
        run_function(structure, simulation)
    finally:
        if simulation.exists():
            write_new(simulation / "artifact_manifest.json", {"files": inventory(simulation)})
    progress(root, "Auditing full-duration outputs and paper-window measurements")
    audit = analyze_function(simulation, root / "analysis")
    validate_sources(frozen)
    for name, digest in runtime.get("installed_files_sha256", {}).items():
        if sha(Path(name)) != digest:
            raise ValueError("Scientific runtime files changed during the app observation")
    if audit.get("technical_acceptance") is not True or audit.get("scientific_acceptance") is not False:
        raise ValueError("Independent technical audit did not pass")
    for key in ("network_seed", "stimulus_seed", "duration_ms"):
        if audit.get(key) != spec[key]:
            raise ValueError("Actual observed condition differs from app request")
    if audit.get("drive", "balanced_poisson") != spec["drive"]:
        raise ValueError("Observed BioNet drive differs from app request")
    result = {"schema_version": 1, "workflow": WORKFLOW, "name": spec["name"],
              "engine": "bionet", "spec": spec, "state": "completed_technical_unreviewed",
              "technical_acceptance": True, "scientific_acceptance": False,
              "raw_run_root": str(simulation), "analysis_root": str(root / "analysis"),
              "audit": audit, "runtime_sha256": sha(root / "runtime.json"),
              "source_sha256": SOURCE_PINS, "caveats": CAVEATS,
              "artifact_manifest_sha256": sha(simulation / "artifact_manifest.json"),
              "analysis_manifest_sha256": sha(root / "analysis/analysis_manifest.json")}
    write_new(root / "worker_result.json", result)
    progress(root, "Full-duration observation passed technical checks; statistical replication remains unclaimed")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    args = parser.parse_args()
    root = args.request.resolve().parent
    try:
        execute(root)
    except BaseException:
        if not (root / "worker_failure.json").exists():
            write_new(root / "worker_failure.json", {"traceback": traceback.format_exc(),
                      "scientific_acceptance": False})
        raise


if __name__ == "__main__":
    main()
