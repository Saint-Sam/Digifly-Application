"""Version-locked, app-owned entry point for the Romaro et al. PDCM benchmark.

This is a separate point-network lane. It never translates the paper's LIF cells
into the morphology-backed Circuit Builder model or executes an arbitrary
ModelDB import: only the identified authors' source revision is accepted.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess
import time
from typing import Callable

import psutil

from .paths import worker_path
from .process_environment import external_runtime_launcher, external_runtime_path, sanitized_external_environment
from .runtime_discovery import probe_simulator_runtime


PDCM_WORKFLOW = "pdcm_replication_v1"
PAPER_URL = "https://pmc.ncbi.nlm.nih.gov/articles/PMC8382011/"
MODEL_URL = "https://github.com/suny-downstate-medical-center/PDCM_NetPyNE"
MODEL_COMMIT = "a6e39c19a0d85e0e907acac33791363591a88f45"
SOURCE_SHA256 = {
    "cfg.py": "1b7ad4912a7645ed35b49cb649adb66bd511234f4f50bd3965d5484d9a34d1f2",
    "init.py": "0fd97727ffdd0ba4049ff92437ee805b2be9f7aed837cce70d4f2604124e38e9",
    "netParams.py": "361356e4beb64e59f4eb4195348ebe80d12a2477733cc2a972df297d96299a1a",
    "IntFire_PD.mod": "9167520a8179c3b84c0a4d29d392d09d770063ffef5bbd8bbab5a2ee50f44940",
}
# NumPy 1.26 rejects generator arguments here; materializing the identical
# sequences changes no model parameter or scaling equation.
NUMPY_COMPATIBILITY_EDITS = (
    (
        "np.vstack(np.column_stack(0 for i in range(0,8)) for i in range(0,8))",
        "np.vstack([np.column_stack([0 for i in range(0,8)]) for i in range(0,8)])",
    ),
    (
        "np.column_stack(np.vstack( [1.0, -4.0] for i in range(0,8)) for i in range(0,4))",
        "np.column_stack([np.vstack([[1.0, -4.0] for i in range(0,8)]) for i in range(0,4)])",
    ),
    (
        "np.column_stack(0 for i in range(0,8))",
        "np.column_stack([0 for i in range(0,8)])",
    ),
)
_SAFE_NAME = re.compile(r"[^a-z0-9]+")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


_BUNDLE_FILES = (
    "worker_request.json",
    "source_manifest.json",
    *(f"model/{name}" for name in (*SOURCE_SHA256, "LICENSE")),
)


def _request_matches_run_root(requested_root: object, root: Path) -> bool:
    """Accept portable requests and exact-location legacy absolute requests only."""
    if not isinstance(requested_root, str) or not requested_root:
        return False
    candidate = Path(requested_root)
    if not candidate.is_absolute():
        return requested_root == "."
    return candidate.resolve() == root


def validate_prepared_bundle(run_root: str | Path) -> dict:
    """Verify a staged run before launching scientific code or changing its state.

    The file hashes detect missing/incomplete transfers; the pinned original
    hashes independently identify the authors' source. Older, local runs made
    before transfer manifests existed remain executable at their original path.
    """
    root = Path(run_root).expanduser().resolve()
    request_path = root / "worker_request.json"
    source_path = root / "source_manifest.json"
    if not request_path.is_file() or not source_path.is_file():
        raise ValueError("This is not a prepared PDCM run")
    request = json.loads(request_path.read_text(encoding="utf-8"))
    if request.get("schema_version") != 1 or request.get("workflow") != PDCM_WORKFLOW:
        raise ValueError("Unsupported PDCM worker request")
    if not _request_matches_run_root(request.get("run_root"), root):
        raise ValueError("PDCM worker request is outside its run folder")
    try:
        PDCMReplicationSpec(**request["spec"]).validate()
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Invalid PDCM worker specification") from exc
    source = json.loads(source_path.read_text(encoding="utf-8"))
    original_hashes = source.get("original_sha256")
    if (
        source.get("model_commit") != MODEL_COMMIT
        or not isinstance(original_hashes, dict)
        or any(original_hashes.get(name) != expected for name, expected in SOURCE_SHA256.items())
    ):
        raise ValueError("PDCM source provenance differs from pinned authors' revision")
    expected_hashes = source.get("executed_sha256")
    if not isinstance(expected_hashes, dict) or set(expected_hashes) != set((*SOURCE_SHA256, "LICENSE")):
        raise ValueError("PDCM source manifest has an unexpected file list")
    model_dir = root / "model"
    if not model_dir.is_dir() or model_dir.is_symlink():
        raise ValueError("PDCM model must be a regular folder inside its bundle")
    for name, expected in expected_hashes.items():
        model_file = model_dir / name
        if not model_file.is_file() or model_file.is_symlink() or _sha256(model_file) != expected:
            raise ValueError(f"Prepared PDCM model file changed: {name}")
    bundle_path = root / "bundle_manifest.json"
    if bundle_path.exists():
        if not bundle_path.is_file() or bundle_path.is_symlink():
            raise ValueError("PDCM bundle manifest must be a regular file")
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        files = bundle.get("files_sha256")
        if (
            bundle.get("schema_version") != 1
            or bundle.get("workflow") != PDCM_WORKFLOW
            or bundle.get("model_commit") != MODEL_COMMIT
            or not isinstance(files, dict)
            or set(files) != set(_BUNDLE_FILES)
        ):
            raise ValueError("PDCM bundle manifest has an unexpected file list")
        for name, expected in files.items():
            bundled_file = root / name
            if not bundled_file.is_file() or bundled_file.is_symlink() or _sha256(bundled_file) != expected:
                raise ValueError(f"PDCM bundle transfer check failed: {name}")
    elif request.get("run_root") == ".":
        raise ValueError("Portable PDCM run is missing its bundle manifest")
    return request


@dataclass(frozen=True)
class PDCMReplicationSpec:
    name: str
    scale: float = 0.01
    duration_ms: float = 1000.0
    drive: str = "balanced_poisson"
    seed: int = 3
    export_convergence: bool = False
    network_seed: int | None = None

    def validate(self) -> None:
        if not self.name.strip():
            raise ValueError("A replication run name is required")
        if not 0.01 <= self.scale <= 1.0:
            raise ValueError("PDCM scale must be between 0.01 and 1.0")
        if not 200 <= self.duration_ms <= 61_000:
            raise ValueError("PDCM duration must be between 200 and 61,000 ms")
        if self.drive not in {"balanced_poisson", "balanced_dc", "unbalanced_poisson"}:
            raise ValueError("Unsupported PDCM external-drive condition")
        if not 0 <= self.seed < 2**31:
            raise ValueError("PDCM seed must be a nonnegative 31-bit integer")
        if type(self.export_convergence) is not bool:
            raise ValueError("PDCM convergence export must be enabled or disabled explicitly")
        if self.network_seed is not None and (
            type(self.network_seed) is not int or not 0 <= self.network_seed < 2**31
        ):
            raise ValueError("PDCM network seed must be a nonnegative 31-bit integer")

    def to_dict(self) -> dict:
        self.validate()
        payload = asdict(self)
        # Existing v1 requests remain byte-identical when network_seed is omitted.
        if self.network_seed is None:
            payload.pop("network_seed")
        return payload


def run_directory(output_root: str | Path, name: str) -> Path:
    normalized = " ".join(name.split())
    slug = _SAFE_NAME.sub("-", normalized.casefold()).strip("-")[:48] or "pdcm"
    digest = hashlib.sha256(normalized.casefold().encode()).hexdigest()[:10]
    return Path(output_root).expanduser().resolve() / "experiments" / f"pdcm-{slug}-{digest}"


def validate_model_source(model_dir: str | Path) -> dict[str, str]:
    root = Path(model_dir).expanduser().resolve()
    if not root.is_dir() or root.is_symlink():
        raise ValueError("Choose a regular folder containing the authors' PDCM source")
    for name, expected in SOURCE_SHA256.items():
        source = root / name
        if not source.is_file() or source.is_symlink():
            raise ValueError(f"The PDCM source is missing regular file {name}")
        if _sha256(source) != expected:
            raise ValueError(f"PDCM source {name} differs from pinned authors' revision {MODEL_COMMIT}")
    license_file = root / "LICENSE"
    if not license_file.is_file() or license_file.is_symlink():
        raise ValueError("The authors' MIT LICENSE must accompany this source")
    return {name: _sha256(root / name) for name in (*SOURCE_SHA256, "LICENSE")}


def prepare_run(
    spec: PDCMReplicationSpec,
    *,
    model_dir: str | Path,
    output_root: str | Path,
) -> Path:
    """Freeze the exact source and explicit compatibility patch in a new run."""
    spec.validate()
    source_root = Path(model_dir).expanduser().resolve()
    originals = validate_model_source(source_root)
    run_root = run_directory(output_root, spec.name)
    if run_root.exists():
        raise FileExistsError(f"A replication run with this name already exists: {run_root}")
    run_root.parent.mkdir(parents=True, exist_ok=True)
    run_root.mkdir(exist_ok=False)
    model_copy = run_root / "model"
    model_copy.mkdir()
    for name in originals:
        shutil.copyfile(source_root / name, model_copy / name)
        if _sha256(model_copy / name) != originals[name]:
            raise RuntimeError(f"PDCM source changed while copying: {name}")
    parameters = model_copy / "netParams.py"
    content = parameters.read_text(encoding="utf-8")
    for before, after in NUMPY_COMPATIBILITY_EDITS:
        if content.count(before) != 1:
            raise RuntimeError("The pinned NumPy compatibility patch no longer matches")
        content = content.replace(before, after)
    parameters.write_text(content, encoding="utf-8")
    _write_json(
        run_root / "source_manifest.json",
        {
            "schema_version": 1,
            "paper_url": PAPER_URL,
            "model_url": MODEL_URL,
            "model_commit": MODEL_COMMIT,
            "original_sha256": originals,
            "executed_sha256": {name: _sha256(model_copy / name) for name in originals},
            "compatibility_patch": "Materialize three NumPy generator arguments as lists; no equations or parameters changed.",
        },
    )
    _write_json(
        run_root / "worker_request.json",
        {"schema_version": 1, "workflow": PDCM_WORKFLOW, "run_root": ".", "spec": spec.to_dict()},
    )
    _write_json(
        run_root / "bundle_manifest.json",
        {
            "schema_version": 1,
            "workflow": PDCM_WORKFLOW,
            "model_commit": MODEL_COMMIT,
            "files_sha256": {name: _sha256(run_root / name) for name in _BUNDLE_FILES},
            "note": "Copy this whole directory; file paths are relative to the bundle root. The run manifest is mutable and excluded from transfer hashes.",
        },
    )
    _write_json(
        run_root / "run_manifest.json",
        {"schema_version": 1, "workflow": PDCM_WORKFLOW, "state": "prepared", "name": spec.name},
    )
    return run_root


def _stop_worker(process: subprocess.Popen) -> None:
    """Stop only the selected scientific worker and its descendants."""
    if os.name != "nt":
        # Popen starts a fresh POSIX session, so this group contains only this
        # worker and its mechanism compiler/simulator descendants. Avoid macOS
        # process-enumeration APIs, which may be unavailable in an app sandbox.
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
        children = parent.children(recursive=True)
    except psutil.NoSuchProcess:
        children = []
        parent = None
    for child in children:
        try:
            child.terminate()
        except psutil.NoSuchProcess:
            pass
    if parent is not None:
        try:
            parent.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(children + ([parent] if parent is not None else []), timeout=5)
    for remaining in alive:
        try:
            remaining.kill()
        except psutil.NoSuchProcess:
            pass
    process.wait(timeout=5)


def _stop_container_for_run(run_root: Path, runtime: Path) -> None:
    """Stop a run's named Docker container before stopping its Windows launcher."""
    if runtime.name.casefold() != "digifly-python.exe":
        return
    docker = shutil.which("docker")
    if docker is None:
        return
    name = f"digifly-{hashlib.sha256(str(run_root).encode()).hexdigest()[:12]}"
    try:
        subprocess.run(
            [docker, "stop", "--time", "2", name],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def validate_runtime_imports(runtime: Path, *, environment: dict[str, str], cwd: Path) -> None:
    """Check the worker's actual import path, not just installed version metadata.

    Run before mechanism compilation or marking a prepared run started. Inherited
    NEURON paths can make an interactive probe pass while this sanitized launcher
    fails. This is a capability gate, not a scientific acceptance check.
    """
    marker = "DIGIFLY_PDCM_IMPORTS_OK"
    code = (
        "from netpyne import specs, sim; from neuron import h; "
        "assert callable(specs.NetParams); assert callable(sim.initialize); "
        f"assert hasattr(h, 'Vector'); print('{marker}')"
    )
    try:
        result = subprocess.run(
            [str(runtime), "-B", "-c", code], cwd=cwd, env=environment,
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"PDCM runtime import preflight could not finish: {exc}") from exc
    if result.returncode or marker not in result.stdout.splitlines():
        diagnostic = (result.stderr or result.stdout or "No import confirmation").strip()[-4000:]
        raise ValueError(
            "PDCM runtime import preflight failed in the selected interpreter's "
            "sanitized environment; no simulation or mechanism compilation started. "
            "Select a compatible NetPyNE/NEURON environment.\n" + diagnostic
        )


def run_prepared(
    run_root: str | Path,
    *,
    python: str | Path,
    cancel_requested: Callable[[], bool] | None = None,
) -> Path:
    root = Path(run_root).expanduser().resolve()
    request = root / "worker_request.json"
    prepared = validate_prepared_bundle(root)
    manifest = json.loads((root / "run_manifest.json").read_text(encoding="utf-8"))
    if manifest.get("state") != "prepared":
        raise ValueError("This PDCM run has already been started")
    if prepared["spec"].get("export_convergence", False):
        raise ValueError("Convergence export requires the pinned MPI PDCM worker; use a prepared bundle on Athene")
    runtime = external_runtime_launcher(python)
    if not runtime.is_file() or not os.access(runtime, os.X_OK):
        raise ValueError("Choose an executable Python with NetPyNE and NEURON")
    identity = probe_simulator_runtime(runtime, timeout=30)
    if not identity.has_netpyne or not identity.has_neuron:
        raise ValueError("The selected Python must contain NetPyNE and NEURON together")
    worker = worker_path("pdcm_netpyne_worker.py")
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
    validate_runtime_imports(runtime, environment=environment, cwd=root)
    manifest.update(state="running", runtime={"python": str(runtime), "netpyne": identity.netpyne_version, "neuron": identity.neuron_version})
    _write_json(root / "run_manifest.json", manifest)
    try:
        with (root / "worker.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                [str(runtime), "-B", str(worker), "--request", str(request)],
                cwd=root,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=os.name != "nt",
            )
            while process.poll() is None:
                if cancel_requested is not None and cancel_requested():
                    _stop_container_for_run(root, runtime)
                    _stop_worker(process)
                    raise InterruptedError("PDCM run cancelled by user")
                time.sleep(0.2)
            if process.returncode or not (root / "summary.json").is_file():
                raise RuntimeError(f"NetPyNE worker exited {process.returncode}; inspect {root / 'worker.log'}")
    except InterruptedError as exc:
        manifest.update(state="cancelled", error=str(exc))
        _write_json(root / "run_manifest.json", manifest)
        raise
    except BaseException as exc:
        manifest.update(state="failed", error=str(exc))
        _write_json(root / "run_manifest.json", manifest)
        raise
    manifest.update(state="completed", completed_at=datetime.now(timezone.utc).isoformat())
    _write_json(root / "run_manifest.json", manifest)
    return root / "summary.json"
