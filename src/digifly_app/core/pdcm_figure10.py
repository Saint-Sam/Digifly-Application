"""Isolated, source-locked preparation for the paper's multicompartment Figure 10.

This lane deliberately does not call the point-neuron PDCM worker.  Preparation
copies the authors' exact branch into a portable run, applies only recorded
execution/recording overrides, and never starts a simulation on its own.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess

import psutil


WORKFLOW = "pdcm_figure10_multicompartment_v1"
PAPER_URL = "https://pmc.ncbi.nlm.nih.gov/articles/PMC8382011/"
MODEL_URL = "https://github.com/suny-downstate-medical-center/PDCM_NetPyNE"
MODEL_COMMIT = "fa2a850b58ef4de4b40d671d2a5b4fe7ce1a6a4d"
SOURCE_FINGERPRINT = "758bd2ff9b520c3d44b431c5447b77ae43d63c5aab011e79f4a6cd18a8d2a6fe"
SOURCE_FILES = (
    "LICENSE", "README.md", "cfg.py", "init.py", "netParams.py",
    "cells/CSTR_cellParams.json", "cells/FScell.hoc",
    *(f"mod/{name}.mod" for name in (
        "HCN1", "IC", "IKsin", "ar", "ar_traub", "beforestep_py",
        "ca", "cad", "cadad", "cadyn", "cagk", "cal_mh", "cal_mig",
        "can_mig", "canin", "cat", "cat_mig", "cat_traub", "catcb",
        "dipole", "dipole_pp", "gabab", "h_BS", "h_harnett", "h_kole",
        "h_migliore", "hh2", "hh3", "hin", "ican_sidi", "kBK",
        "kap_BS", "kapcb", "kapin", "kca", "kctin", "kdmc_BS",
        "kdr_BS", "kdrin", "km", "lfp", "mea", "nafx", "nap_sidi",
        "nax_BS", "savedist", "vecevent", "vecstim",
    )),
)
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
_SECTIONS = ("soma", "Adend1", "Adend2", "Adend3", "Bdend", "axon")
_MECHANISMS = {"pas", "kBK", "cat", "ih", "kap", "can", "cal", "nax", "kdr"}
_FULL_POPULATIONS = (20683, 5834, 21915, 5479, 4850, 1065, 14395, 2948)
_THALAMIC_POPULATIONS = (("L4e", 2, 93), ("L4i", 3, 84), ("L6e", 6, 47), ("L6i", 7, 34))


def source_thalamic_events_per_cell(scale: float) -> dict[str, int]:
    """Exact `nTH` truncation in pinned netParams.py (15 Hz for 10 ms)."""
    return {
        label: int(math.sqrt(scale) * afferents * 15 * 10 / 1000)
        for label, _, afferents in _THALAMIC_POPULATIONS
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _inventory(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for name in SOURCE_FILES:
        path = root / name
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Figure 10 authors' source is missing regular file {name}")
        result[name] = _sha256(path)
    actual_mods = {f"mod/{p.name}" for p in (root / "mod").glob("*.mod")}
    expected_mods = {name for name in SOURCE_FILES if name.startswith("mod/")}
    if actual_mods != expected_mods:
        raise ValueError("Figure 10 NMODL file set differs from the pinned branch")
    return result


def _fingerprint(inventory: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for name in sorted(inventory):
        digest.update(f"{name}\0{inventory[name]}\n".encode("utf-8"))
    return digest.hexdigest()


def validate_model_source(model_dir: str | Path) -> dict[str, str]:
    chosen = Path(model_dir).expanduser()
    if chosen.is_symlink() or not chosen.is_dir():
        raise ValueError("Choose a regular folder containing the authors' multicompartment branch")
    root = chosen.resolve()
    inventory = _inventory(root)
    if _fingerprint(inventory) != SOURCE_FINGERPRINT:
        raise ValueError(f"Figure 10 source differs from pinned authors' revision {MODEL_COMMIT}")
    cell = json.loads((root / "cells/CSTR_cellParams.json").read_text(encoding="utf-8"))
    sections = cell.get("secs")
    if not isinstance(sections, dict) or set(sections) != set(_SECTIONS):
        raise ValueError("Figure 10 cell sections differ from the pinned six-section morphology")
    mechanisms = {mech for section in sections.values() for mech in section.get("mechs", {})}
    if mechanisms != _MECHANISMS:
        raise ValueError("Figure 10 cell mechanisms differ from the pinned model")
    return inventory


@dataclass(frozen=True)
class Figure10Spec:
    name: str
    profile: str = "paper"
    thalamic_input: bool = False
    seed: int = 3

    @property
    def scale(self) -> float:
        return 0.001 if self.profile == "smoke" else 0.01

    @property
    def duration_ms(self) -> int:
        return 600 if self.profile == "paper" else 20

    def validate(self) -> None:
        if not self.name.strip():
            raise ValueError("A unique Figure 10 run name is required")
        if self.profile not in {"paper", "smoke", "capacity"}:
            raise ValueError("Figure 10 profile must be paper, smoke or capacity")
        if type(self.thalamic_input) is not bool:
            raise ValueError("Thalamic input must be an explicit Boolean")
        if not 0 <= self.seed < 2**31:
            raise ValueError("Figure 10 stimulation seed must be a nonnegative 31-bit integer")


def run_directory(output_root: str | Path, name: str) -> Path:
    normalized = " ".join(name.split())
    slug = _SAFE_NAME.sub("-", normalized.casefold()).strip("-")[:44] or "figure10"
    digest = hashlib.sha256(normalized.casefold().encode()).hexdigest()[:10]
    return Path(output_root).expanduser().resolve() / "experiments" / f"pdcm-fig10-{slug}-{digest}"


def _patched_parameters(original: str) -> str:
    content = original
    for before, after in NUMPY_COMPATIBILITY_EDITS:
        if content.count(before) != 1:
            raise ValueError("Pinned Figure 10 NumPy compatibility patch no longer matches")
        content = content.replace(before, after)
    return content


def _patched_config(original: str, spec: Figure10Spec) -> str:
    # The author cfg defaults to 200 ms, does not record voltage traces, and
    # enables a 100–200 ms CSD plot.  Preserve the original in author/ and
    # append explicit raw-recording overrides only in model/.
    spec.validate()
    if not original.endswith("\n"):
        raise ValueError("Pinned Figure 10 configuration has unexpected ending")
    trace_defs = ", ".join(
        f"'V_{section}': {{'sec': '{section}', 'loc': 0.5, 'var': 'v'}}"
        for section in _SECTIONS
    )
    return original + (
        "\n# Digifly Figure 10 raw-data profile; authors' cfg is preserved in ../author.\n"
        f"cfg.seeds['stim'] = {spec.seed}\n"
        f"cfg.ScaleFactor = {spec.scale!r}\n"
        f"cfg.duration = {spec.duration_ms}.0\n"
        f"cfg.TH = {spec.thalamic_input!r}\n"
        "cfg.DC = False\n"
        "cfg.Balanced = True\n"
        "cfg.hParams['celsius'] = 34\n"
        f"cfg.simLabel = 'digifly_pdcm_figure10_{spec.profile}'\n"
        "cfg.filename = cfg.simLabel\n"
        "cfg.saveFolder = '../data'\n"
        "cfg.savePickle = False\n"
        "cfg.saveJson = True\n"
        "cfg.recordTime = True\n"
        "cfg.recordStep = 0.1\n"
        "cfg.recordCells = list(cfg.recordCellsSpikes)\n"
        f"cfg.recordTraces = {{{trace_defs}}}\n"
        "cfg.recordLFP = [[150, y, 150] for y in range(0, 1500, 100)]\n"
        "cfg.analysis = {}  # Figure panels are generated from retained raw data.\n"
    )


def _write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def prepare_run(spec: Figure10Spec, *, model_dir: str | Path, output_root: str | Path) -> Path:
    """Freeze an untouched author copy and separate executable copy; never run it."""
    spec.validate()
    selected_source = Path(model_dir).expanduser()
    original_hashes = validate_model_source(selected_source)
    source = selected_source.resolve()
    root = run_directory(output_root, spec.name)
    if root.exists():
        raise FileExistsError(f"A Figure 10 run with this name already exists: {root}")
    root.parent.mkdir(parents=True, exist_ok=True)
    root.mkdir(exist_ok=False)
    for folder in ("author", "model", "data"):
        (root / folder).mkdir()
    for name, expected in original_hashes.items():
        for folder in ("author", "model"):
            target = root / folder / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / name, target)
            if _sha256(target) != expected:
                raise RuntimeError(f"Figure 10 source changed while copying: {name}")
    (root / "model/cfg.py").write_text(
        _patched_config((root / "author/cfg.py").read_text(encoding="utf-8"), spec), encoding="utf-8"
    )
    (root / "model/netParams.py").write_text(
        _patched_parameters((root / "author/netParams.py").read_text(encoding="utf-8")), encoding="utf-8"
    )
    executed_hashes = _inventory(root / "model")
    _write_json(root / "worker_request.json", {
        "schema_version": 1, "workflow": WORKFLOW, "run_root": ".", "spec": asdict(spec),
    })
    _write_json(root / "source_manifest.json", {
        "schema_version": 1, "workflow": WORKFLOW, "paper_url": PAPER_URL,
        "model_url": MODEL_URL, "model_commit": MODEL_COMMIT,
        "source_fingerprint": SOURCE_FINGERPRINT,
        "original_sha256": original_hashes, "executed_sha256": executed_hashes,
        "compatibility_patch": "Three NumPy generator arguments materialized as lists; equations unchanged.",
        "recording_overrides": {
            "scale": spec.scale, "duration_ms": spec.duration_ms,
            "poisson_balanced": True, "thalamic_input": spec.thalamic_input,
            "temperature_c": 34, "LFP_sites_um": [[150, y, 150] for y in range(0, 1500, 100)],
            "voltage_sections": list(_SECTIONS), "record_step_ms": 0.1,
            "format": "NetPyNE JSON", "figure_panels_generated": False,
        },
        "known_ambiguities": [
            "Paper says two apical dendrites, but pinned JSON has Adend1, Adend2 and Adend3.",
            "Branch Figure 10A comment suggests TH=True; its executable default is TH=False.",
        ],
    })
    bundle_files = ("worker_request.json", "source_manifest.json") + tuple(
        f"{folder}/{name}" for folder in ("author", "model") for name in SOURCE_FILES
    )
    _write_json(root / "bundle_manifest.json", {
        "schema_version": 1, "workflow": WORKFLOW, "model_commit": MODEL_COMMIT,
        "files_sha256": {name: _sha256(root / name) for name in bundle_files},
    })
    _write_json(root / "run_manifest.json", {
        "schema_version": 1, "workflow": WORKFLOW, "name": spec.name,
        "profile": spec.profile, "state": "prepared", "simulation_started": False,
    })
    validate_prepared_bundle(root)
    return root


def validate_prepared_bundle(run_root: str | Path) -> dict:
    """Reject transfer damage, source drift and modified executable model files."""
    chosen = Path(run_root).expanduser()
    if chosen.is_symlink() or not chosen.is_dir():
        raise ValueError("Figure 10 run must be a regular folder")
    root = chosen.resolve()
    request = json.loads((root / "worker_request.json").read_text(encoding="utf-8"))
    if request.get("schema_version") != 1 or request.get("workflow") != WORKFLOW or request.get("run_root") != ".":
        raise ValueError("Unsupported or nonportable Figure 10 worker request")
    try:
        spec = Figure10Spec(**request["spec"])
        spec.validate()
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Invalid Figure 10 worker specification") from exc
    source = json.loads((root / "source_manifest.json").read_text(encoding="utf-8"))
    if (
        source.get("schema_version") != 1 or source.get("workflow") != WORKFLOW
        or source.get("model_commit") != MODEL_COMMIT
        or source.get("source_fingerprint") != SOURCE_FINGERPRINT
    ):
        raise ValueError("Figure 10 source source details differs from the pinned branch")
    original_hashes = validate_model_source(root / "author")
    if source.get("original_sha256") != original_hashes:
        raise ValueError("Figure 10 author copy differs from its source manifest")
    for name in SOURCE_FILES:
        executed = root / "model" / name
        if not executed.is_file() or executed.is_symlink():
            raise ValueError(f"Figure 10 executable model is missing {name}")
        if name == "cfg.py":
            expected = _patched_config((root / "author/cfg.py").read_text(encoding="utf-8"), spec)
            if executed.read_text(encoding="utf-8") != expected:
                raise ValueError("Figure 10 executable configuration differs from pinned overrides")
        elif name == "netParams.py":
            expected = _patched_parameters((root / "author/netParams.py").read_text(encoding="utf-8"))
            if executed.read_text(encoding="utf-8") != expected:
                raise ValueError("Figure 10 executable parameters differ from pinned compatibility patch")
        elif _sha256(executed) != original_hashes[name]:
            raise ValueError(f"Figure 10 executable source differs from the authors' copy: {name}")
    if source.get("executed_sha256") != _inventory(root / "model"):
        raise ValueError("Figure 10 executed source hashes differ from the manifest")
    bundle_path = root / "bundle_manifest.json"
    if not bundle_path.is_file() or bundle_path.is_symlink():
        raise ValueError("Figure 10 transfer manifest is missing")
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    expected_files = {"worker_request.json", "source_manifest.json"} | {
        f"{folder}/{name}" for folder in ("author", "model") for name in SOURCE_FILES
    }
    hashes = bundle.get("files_sha256")
    if (
        bundle.get("schema_version") != 1 or bundle.get("workflow") != WORKFLOW
        or bundle.get("model_commit") != MODEL_COMMIT
        or not isinstance(hashes, dict) or set(hashes) != expected_files
    ):
        raise ValueError("Figure 10 transfer manifest has an unexpected file list")
    for name, expected in hashes.items():
        path = root / name
        if not path.is_file() or path.is_symlink() or _sha256(path) != expected:
            raise ValueError(f"Figure 10 transfer check failed: {name}")
    return request


def run_local(
    run_root: str | Path,
    *,
    python: str | Path,
    nrnivmodl: str | Path,
    timeout_seconds: int = 1800,
) -> Path:
    """Compile and execute one prepared bundle, retaining all logs and raw data.

    A zero process exit only proves raw execution; the resulting state is
    ``completed_unverified`` until figure-specific numerical checks and panels
    are independently examined.  This is a serial local lane, never Slurm.
    """
    request = validate_prepared_bundle(run_root)
    root = Path(run_root).expanduser().resolve()
    spec = Figure10Spec(**request["spec"])
    runtime = Path(python).expanduser().resolve()
    compiler = Path(nrnivmodl).expanduser().resolve()
    for path, label in ((runtime, "NetPyNE Python"), (compiler, "NEURON mechanism compiler")):
        if not path.is_file() or not os.access(path, os.X_OK):
            raise ValueError(f"Choose an executable {label}: {path}")
    if not 1 <= timeout_seconds <= 7200:
        raise ValueError("Figure 10 local timeout must be between 1 and 7,200 seconds")
    if spec.profile == "paper" and psutil.virtual_memory().available < 32 * 1024**3:
        raise RuntimeError("The 1%/600 ms Figure 10 candidate needs at least 32 GiB free memory")
    if shutil.disk_usage(root).free < (5 if spec.profile == "paper" else 1) * 1024**3:
        raise RuntimeError("Not enough free disk for Figure 10 raw voltage/LFP output")
    manifest_path = root / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("state") != "prepared" or manifest.get("simulation_started") is not False:
        raise ValueError("Figure 10 run has already been claimed; prepare a fresh name")
    provenance = root / "provenance"
    provenance.mkdir(exist_ok=False)
    (provenance / "matplotlib").mkdir()
    manifest.update({
        "state": "running", "simulation_started": True,
        "python": str(runtime), "nrnivmodl": str(compiler),
        "timeout_seconds": timeout_seconds,
    })
    _write_json(manifest_path, manifest)
    environment = os.environ.copy()
    # The app's own relative PYTHONPATH must not leak into an external NEURON
    # interpreter after cwd changes.  A framework NEURON install, however,
    # needs its own lib/python on that path (for ``from neuron import init``).
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    neuron_python = compiler.parent.parent / "lib/python"
    if neuron_python.is_dir():
        environment["PYTHONPATH"] = str(neuron_python)
        manifest["external_pythonpath"] = str(neuron_python)
        _write_json(manifest_path, manifest)
    environment["NEURON_MODULE_OPTIONS"] = "-nogui"
    environment["MPLCONFIGDIR"] = str(provenance / "matplotlib")
    model = root / "model"
    compile_command = [str(compiler), "mod"]
    run_command = [
        str(runtime), "-c",
        "import runpy; from neuron import h; "
        "probe = h.Section(); probe.insert('kBK'); del probe; "
        "runpy.run_path('init.py', run_name='__main__')",
    ]
    try:
        version_command = [
            str(runtime), "-c",
            "import json, sys, neuron; import netpyne, numpy; "
            "print(json.dumps({'python': sys.version, 'neuron': neuron.__version__, "
            "'netpyne': netpyne.__version__, 'numpy': numpy.__version__}))",
        ]
        version_check = subprocess.run(
            version_command, cwd=model, env=environment, capture_output=True,
            timeout=60, check=False,
        )
        (provenance / "runtime_versions.stdout.txt").write_bytes(version_check.stdout)
        (provenance / "runtime_versions.stderr.txt").write_bytes(version_check.stderr)
        if version_check.returncode != 0:
            raise RuntimeError("Figure 10 NetPyNE/NEURON runtime preflight failed; see source details logs")
        versions = json.loads(version_check.stdout.decode("utf-8").splitlines()[-1])
        manifest["runtime_versions"] = versions
        _write_json(manifest_path, manifest)
        for stage, command, limit in (
            ("compile", compile_command, min(timeout_seconds, 600)),
            ("simulate", run_command, timeout_seconds),
        ):
            manifest["stage"] = stage
            manifest[f"{stage}_command"] = command
            _write_json(manifest_path, manifest)
            with (provenance / f"{stage}.stdout.txt").open("wb") as stdout, (
                provenance / f"{stage}.stderr.txt"
            ).open("wb") as stderr:
                completed = subprocess.run(
                    command, cwd=model, env=environment, stdout=stdout, stderr=stderr,
                    timeout=limit, check=False,
                )
            manifest[f"{stage}_exit_code"] = completed.returncode
            _write_json(manifest_path, manifest)
            if completed.returncode != 0:
                raise RuntimeError(f"Figure 10 {stage} exited {completed.returncode}; see source details logs")
        raw = root / "data" / f"digifly_pdcm_figure10_{spec.profile}_data.json"
        if not raw.is_file() or raw.stat().st_size == 0:
            raise RuntimeError("Figure 10 execution did not save the expected NetPyNE JSON")
        manifest.update({
            "state": "completed_unverified", "stage": "saved",
            "raw_data_file": str(raw.relative_to(root)),
            "raw_data_bytes": raw.stat().st_size,
            "raw_data_sha256": _sha256(raw),
            "scientific_acceptance": False,
        })
    except Exception as exc:
        manifest.update({"state": "failed", "error": f"{type(exc).__name__}: {exc}"})
        _write_json(manifest_path, manifest)
        raise
    _write_json(manifest_path, manifest)
    return raw


def validate_raw_output(run_root: str | Path, *, write_sidecar: bool = True) -> dict:
    """Check saved Figure 10 signal completeness without asserting paper agreement.

    The NetPyNE JSON can be near a gigabyte; insist on headroom before parsing
    it and keep the immutable simulation artifact unchanged.  The sidecar is
    deliberately separate from the run's source/transfer manifests.
    """
    validate_prepared_bundle(run_root)
    root = Path(run_root).expanduser().resolve()
    run = json.loads((root / "run_manifest.json").read_text(encoding="utf-8"))
    if run.get("state") != "completed_unverified" or run.get("scientific_acceptance") is not False:
        raise ValueError("Figure 10 raw output can only be checked after an unverified completed run")
    spec = Figure10Spec(**json.loads((root / "worker_request.json").read_text())["spec"])
    raw = root / "data" / f"digifly_pdcm_figure10_{spec.profile}_data.json"
    if run.get("raw_data_file") != str(raw.relative_to(root)) or not raw.is_file():
        raise ValueError("Figure 10 raw data file is missing or mismatched")
    if run.get("raw_data_bytes") != raw.stat().st_size or run.get("raw_data_sha256") != _sha256(raw):
        raise ValueError("Figure 10 raw data hash or size changed")
    if psutil.virtual_memory().available < max(4 * 1024**3, 8 * raw.stat().st_size):
        raise RuntimeError("Insufficient free RAM for full Figure 10 raw JSON validation")
    sidecar = root / "analysis-raw-validation-v1.json"
    if write_sidecar and sidecar.exists():
        raise FileExistsError("Figure 10 raw validation sidecar already exists")
    with raw.open("r", encoding="utf-8") as stream:
        data = json.load(stream)
    config = data.get("simConfig", {})
    expected_sites = [[150, depth, 150] for depth in range(0, 1500, 100)]
    expected_samples = int(spec.duration_ms / 0.1)
    expected_cortical = sum(int(spec.scale * count) for count in _FULL_POPULATIONS[:8])
    thalamic_cells = sum(int(spec.scale * _FULL_POPULATIONS[index]) for _, index, _ in _THALAMIC_POPULATIONS) if spec.thalamic_input else 0
    thalamic_events_per_cell = source_thalamic_events_per_cell(spec.scale) if spec.thalamic_input else {}
    if (
        config.get("duration") != float(spec.duration_ms)
        or config.get("ScaleFactor") != spec.scale
        or config.get("TH") is not spec.thalamic_input
        or config.get("DC") is not False or config.get("Balanced") is not True
        or config.get("recordStep") != 0.1
        or config.get("recordLFP") != expected_sites
        or config.get("hParams", {}).get("celsius") != 34
        or set(config.get("recordTraces", {})) != {f"V_{section}" for section in _SECTIONS}
    ):
        raise ValueError("Figure 10 raw configuration differs from prepared conditions")
    cells = data.get("net", {}).get("cells", [])
    if not isinstance(cells, list):
        raise ValueError("Figure 10 saved network cells are missing")
    cortical = {cell.get("gid") for cell in cells if cell.get("tags", {}).get("cellType") == "MC"}
    if len(cortical) != expected_cortical or len(cells) != 2 * expected_cortical + thalamic_cells:
        raise ValueError("Figure 10 cortical or virtual cell count differs from pinned source")
    actual_thalamic_cells = sum(
        cell.get("tags", {}).get("pop", "").startswith("bkg_TH") for cell in cells
    )
    if actual_thalamic_cells != thalamic_cells:
        raise ValueError("Figure 10 thalamic input population count differs from pinned source")
    sim_data = data.get("simData", {})
    times = sim_data.get("t", [])
    if len(times) != expected_samples or any(
        not isinstance(value, (int, float)) or not math.isfinite(value)
        or abs(value - index * 0.1) > 1e-4
        for index, value in enumerate(times)
    ):
        raise ValueError("Figure 10 saved time grid is incomplete or nonfinite")
    lfp = sim_data.get("LFP", [])
    if len(lfp) != expected_samples or any(
        not isinstance(row, list) or len(row) != 15
        or any(not isinstance(value, (int, float)) or not math.isfinite(value) for value in row)
        for row in lfp
    ):
        raise ValueError("Figure 10 LFP channels are incomplete or nonfinite")
    voltage_ranges: dict[str, list[float]] = {}
    expected_keys = {f"cell_{gid}" for gid in cortical}
    for section in _SECTIONS:
        label = f"V_{section}"
        traces = sim_data.get(label, {})
        if not isinstance(traces, dict) or set(traces) != expected_keys:
            raise ValueError(f"Figure 10 {label} cell traces are incomplete")
        low = math.inf
        high = -math.inf
        for trace in traces.values():
            if len(trace) != expected_samples:
                raise ValueError(f"Figure 10 {label} time samples are incomplete")
            for value in trace:
                if not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError(f"Figure 10 {label} contains a nonfinite voltage")
                low = min(low, value)
                high = max(high, value)
        voltage_ranges[label] = [low, high]
    spike_ids = sim_data.get("spkid", [])
    spike_times = sim_data.get("spkt", [])
    if len(spike_ids) != len(spike_times) or any(gid not in cortical for gid in spike_ids) or any(
        not isinstance(value, (int, float)) or not math.isfinite(value)
        or value < 0 or value >= spec.duration_ms for value in spike_times
    ):
        raise ValueError("Figure 10 cortical spike IDs or times are invalid")
    report = {
        "schema_version": 1, "workflow": WORKFLOW,
        "status": "structural_pass_not_paper_acceptance", "scientific_acceptance": False,
        "profile": spec.profile, "thalamic_input": spec.thalamic_input,
        "duration_ms": spec.duration_ms, "scale": spec.scale,
        "source_commit": MODEL_COMMIT, "source_fingerprint": SOURCE_FINGERPRINT,
        "raw_data_file": str(raw.relative_to(root)),
        "raw_data_bytes": raw.stat().st_size, "raw_data_sha256": _sha256(raw),
        "cortical_cells": len(cortical), "virtual_input_cells": len(cells) - len(cortical),
        "thalamic_virtual_cells": actual_thalamic_cells,
        "source_thalamic_events_per_cell": thalamic_events_per_cell,
        "spikes": len(spike_times), "time_samples": len(times),
        "voltage_sections": list(_SECTIONS), "voltage_traces_per_section": len(cortical),
        "voltage_range_mv": voltage_ranges,
        "lfp_electrodes_um": expected_sites, "lfp_shape": [len(lfp), 15],
        "lfp_range": [min(min(row) for row in lfp), max(max(row) for row in lfp)],
        "caveat": "This checks raw structure and finite values, not similarity to Figure 10 panels or resolution of the TH input ambiguity.",
    }
    if write_sidecar:
        _write_json(sidecar, report)
    return report
