from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any, Iterable, Mapping

from digifly_app.core.circuit import CircuitSpec, HodgkinHuxleySpec
from digifly_app.core.experiment import EXPERIMENT_BUILDER_WORKFLOW, ExperimentSpec
from digifly_app.core.mechanisms import MembraneMechanismSpec
from digifly_app.core.models import (
    Artifact,
    CheckState,
    ExecutionPlan,
    PreflightCheck,
    PreflightReport,
    ResultRecord,
)
from digifly_app.core.morphology import Morphology
from digifly_app.core.paths import worker_path
from digifly_app.core.process_environment import (
    external_runtime_path,
    sanitized_external_environment,
)


GENERIC_EXPERIMENT_SCHEMA_VERSION = 1
GENERIC_RESULT_SCHEMA_VERSION = 1
SUPPORTED_ENGINES = {"arbor", "neuron"}
_SAFE_COMPONENT = re.compile(r"[^a-z0-9]+")


def _check(
    key: str,
    title: str,
    state: CheckState,
    detail: str,
    *,
    blocking: bool = False,
    path: str | Path | None = None,
) -> PreflightCheck:
    return PreflightCheck(
        key=key,
        title=title,
        state=state,
        detail=detail,
        blocking=blocking,
        path=str(path) if path is not None else None,
    )


def experiment_run_directory(output_root: str | Path, name: str) -> Path:
    """Return a stable, collision-resistant directory for one unique name."""

    normalized = " ".join(str(name).split())
    slug = _SAFE_COMPONENT.sub("-", normalized.casefold()).strip("-")[:52]
    slug = slug or "experiment"
    digest = hashlib.sha256(normalized.casefold().encode("utf-8")).hexdigest()[:10]
    return Path(output_root).expanduser().resolve() / "experiments" / f"{slug}-{digest}"


def _runtime_check(engine: str, executable: Path, make_plots: bool) -> PreflightCheck:
    module = engine
    code = (
        "import json, sys; "
        f"import {module} as simulator; "
        + ("import matplotlib; " if make_plots else "")
        + "print(json.dumps({'python': sys.executable, "
        "'version': getattr(simulator, '__version__', 'unknown'), "
        f"'module': '{module}'" + (", 'matplotlib': matplotlib.__version__" if make_plots else "") + "}))"
    )
    environment = sanitized_external_environment(
        {
            "PATH": external_runtime_path(executable),
            "MPLBACKEND": "Agg",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
        }
    )
    environment.pop("DISPLAY", None)
    try:
        completed = subprocess.run(
            [str(executable), "-B", "-c", code],
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _check(
            "runtime",
            f"{engine.upper()} runtime",
            CheckState.FAIL,
            f"Runtime probe failed: {exc}",
            blocking=True,
            path=executable,
        )
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip()
        return _check(
            "runtime",
            f"{engine.upper()} runtime",
            CheckState.FAIL,
            detail[-4000:] or f"The configured Python could not import {module}.",
            blocking=True,
            path=executable,
        )
    try:
        identity = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        identity = {"version": "unknown"}
    plot_detail = (
        f"; Matplotlib {identity.get('matplotlib')} is available for plots"
        if make_plots
        else ""
    )
    return _check(
        "runtime",
        f"{engine.upper()} runtime",
        CheckState.PASS,
        f"{engine.upper()} {identity.get('version')} loaded in {executable}{plot_detail}.",
        path=executable,
    )


def _effective_membrane_specs(
    circuit: CircuitSpec,
) -> tuple[MembraneMechanismSpec, ...]:
    return (
        circuit.membrane,
        *(
            MembraneMechanismSpec.from_dict(payload)
            for payload in circuit.neuron_mechanism_overrides.values()
        ),
        *(
            MembraneMechanismSpec.from_dict(payload)
            for node_map in circuit.compartment_mechanism_overrides.values()
            for payload in node_map.values()
        ),
    )


def _effective_hh(circuit: CircuitSpec, neuron_id: str) -> HodgkinHuxleySpec:
    values = circuit.hh.to_dict()
    values.update(circuit.neuron_overrides.get(neuron_id, {}))
    return HodgkinHuxleySpec.from_dict(values)


class GenericExperimentAdapter:
    """Capability-gated classic-HH execution for Experiment Builder."""

    def __init__(self, runtime_paths: Mapping[str, str | Path]):
        self.runtime_paths = {
            str(key): Path(value).expanduser().resolve()
            for key, value in runtime_paths.items()
            if str(value).strip()
        }

    @property
    def worker_path(self) -> Path:
        return worker_path("generic_experiment_worker.py")

    def validate(
        self,
        circuit: CircuitSpec,
        experiment: ExperimentSpec,
        morphologies: Iterable[Morphology],
        *,
        output_root: str | Path,
    ) -> PreflightReport:
        checks: list[PreflightCheck] = []
        errors = experiment.errors(circuit)
        checks.append(
            _check(
                "documents",
                "Circuit and experiment documents",
                CheckState.PASS if not errors else CheckState.FAIL,
                "Both app-owned documents pass schema validation."
                if not errors
                else errors[0],
                blocking=True,
            )
        )

        engine = experiment.engine
        if engine not in SUPPORTED_ENGINES:
            checks.append(
                _check(
                    "engine",
                    "Executable engine adapter",
                    CheckState.FAIL,
                    f"{engine.upper()} is not connected yet. Choose Arbor or NEURON.",
                    blocking=True,
                )
            )
        else:
            checks.append(
                _check(
                    "engine",
                    "Executable engine adapter",
                    CheckState.PASS,
                    f"The simulator-neutral draft can target the {engine.upper()} classic-HH worker.",
                    blocking=True,
                )
            )

        runtime = self.runtime_paths.get(engine)
        if engine in SUPPORTED_ENGINES:
            if runtime is None or not runtime.is_file() or not os.access(runtime, os.X_OK):
                checks.append(
                    _check(
                        "runtime",
                        f"{engine.upper()} runtime",
                        CheckState.FAIL,
                        f"Choose an executable {engine.upper()} Python in Workspace before running.",
                        blocking=True,
                        path=runtime,
                    )
                )
            else:
                checks.append(_runtime_check(engine, runtime, experiment.recording.make_plots))

        output = Path(output_root).expanduser().resolve()
        writable = (
            output.is_dir() and os.access(output, os.W_OK)
        ) or (
            not output.exists()
            and output.parent.is_dir()
            and os.access(output.parent, os.W_OK)
        )
        checks.append(
            _check(
                "output",
                "App-owned output root",
                CheckState.PASS if writable else CheckState.FAIL,
                "Run documents, traces, plots, and logs will stay under this folder."
                if writable
                else "The configured output folder cannot be created or written.",
                blocking=True,
                path=output,
            )
        )
        reserved = experiment_run_directory(output, experiment.name)
        checks.append(
            _check(
                "run_directory",
                "Unique app-owned run directory",
                CheckState.PASS if not reserved.exists() else CheckState.FAIL,
                "The normalized experiment name maps to a new run directory."
                if not reserved.exists()
                else "This name's app-owned run directory already exists; choose a new experiment name.",
                blocking=True,
                path=reserved,
            )
        )
        checks.append(
            _check(
                "worker",
                "App-owned generic worker",
                CheckState.PASS if self.worker_path.is_file() else CheckState.FAIL,
                "The standalone worker script is present."
                if self.worker_path.is_file()
                else "The packaged generic worker script is missing.",
                blocking=True,
                path=self.worker_path,
            )
        )

        morphology_by_id = {
            morphology.record.neuron_id: morphology for morphology in morphologies
        }
        if len(circuit.neuron_ids) != 1:
            scope_detail = (
                "Load exactly one neuron for the first generic execution lane. "
                "Multi-neuron runs remain blocked until the circuit document carries a complete, versioned edge manifest."
            )
            scope_state = CheckState.FAIL
        else:
            scope_detail = "One morphology-backed neuron is selected; no connectivity can be silently omitted."
            scope_state = CheckState.PASS
        checks.append(
            _check(
                "circuit_scope",
                "Executable circuit scope",
                scope_state,
                scope_detail,
                blocking=True,
            )
        )

        missing = [
            neuron_id
            for neuron_id in circuit.neuron_ids
            if neuron_id not in morphology_by_id
            or not Path(morphology_by_id[neuron_id].record.swc_path).is_file()
        ]
        checks.append(
            _check(
                "morphology",
                "Source morphology",
                CheckState.PASS if not missing and bool(circuit.neuron_ids) else CheckState.FAIL,
                "Every selected neuron resolves to its read-only source SWC."
                if not missing and circuit.neuron_ids
                else "Missing loaded/source morphology for: " + ", ".join(missing or circuit.neuron_ids),
                blocking=True,
            )
        )
        changed_digests: list[str] = []
        for neuron_id in circuit.neuron_ids:
            morphology = morphology_by_id.get(neuron_id)
            expected = circuit.morphology_sha256.get(neuron_id, "")
            if morphology is None or not expected:
                continue
            path = Path(morphology.record.swc_path)
            if path.is_file() and _file_sha256(path) != expected:
                changed_digests.append(neuron_id)
        checks.append(
            _check(
                "morphology_identity",
                "Morphology identity",
                CheckState.FAIL if changed_digests else CheckState.PASS,
                "Every recorded morphology checksum still matches its source SWC."
                if not changed_digests
                else "Source SWC checksums changed for: " + ", ".join(changed_digests),
                blocking=True,
            )
        )

        active_native = {
            channel.mechanism_key
            for membrane in _effective_membrane_specs(circuit)
            for channel in membrane.active_channels
        }
        replaces_hh = any(
            membrane.replace_builtin_hh for membrane in _effective_membrane_specs(circuit)
        )
        mechanism_ok = not active_native and not replaces_hh
        checks.append(
            _check(
                "mechanisms",
                "Membrane mechanism translation",
                CheckState.PASS if mechanism_ok else CheckState.FAIL,
                "Built-in classic HH and its regional conductances are supported on both engines."
                if mechanism_ok
                else "This first lane does not silently substitute native mechanisms: "
                + ", ".join(sorted(active_native or {"replace_builtin_hh"})),
                blocking=True,
            )
        )

        compartment_count = sum(
            len(values) for values in circuit.compartment_overrides.values()
        ) + sum(
            len(values) for values in circuit.compartment_mechanism_overrides.values()
        )
        hh_scopes = {
            _effective_hh(circuit, neuron_id).active_scope
            for neuron_id in circuit.neuron_ids
        }
        regional_ok = not compartment_count and hh_scopes <= {"all", "soma"}
        checks.append(
            _check(
                "regional_biophysics",
                "Regional biophysics mapping",
                CheckState.PASS if regional_ok else CheckState.FAIL,
                "Whole-neuron and soma/branch classic-HH values can be translated exactly."
                if regional_ok
                else "Per-SWC-compartment overrides and soma+AIS-only scope need an explicit CV mapping before execution.",
                blocking=True,
            )
        )

        enabled_stimuli = tuple(
            stimulus for stimulus in experiment.stimuli if stimulus.enabled
        )
        stimuli_ok = len(enabled_stimuli) == 1 and all(
            stimulus.target_region == "soma"
            and stimulus.waveform in {"pulse_train", "step"}
            for stimulus in enabled_stimuli
        )
        checks.append(
            _check(
                "stimuli",
                "Stimulus translation",
                CheckState.PASS if stimuli_ok else CheckState.FAIL,
                "Soma square-step and pulse-train current clamps are supported."
                if stimuli_ok
                else "Choose Soma with Pulse train or Single step for the first executable lane.",
                blocking=True,
            )
        )

        conditions_ok = all(
            not condition.disabled_neuron_ids and not condition.mechanism_scales
            for condition in experiment.conditions
            if condition.enabled
        )
        checks.append(
            _check(
                "conditions",
                "Runtime conditions",
                CheckState.PASS if conditions_ok else CheckState.FAIL,
                "Enabled conditions and stimulus multipliers can be executed independently."
                if conditions_ok
                else "Neuron disabling and mechanism-scale maps need an additional execution translation.",
                blocking=True,
            )
        )

        recording_ok = (
            experiment.recording.target_region == "soma"
            and experiment.recording.record_voltage
        )
        checks.append(
            _check(
                "recording",
                "Recording translation",
                CheckState.PASS if recording_ok else CheckState.FAIL,
                "Soma voltage traces and threshold-crossing spike times will be recorded."
                if recording_ok
                else "Choose Soma and enable membrane-voltage recording; all-compartment recording is intentionally not approximated.",
                blocking=True,
            )
        )

        parallel_ok = engine != "neuron" or experiment.workers == 1
        checks.append(
            _check(
                "parallelism",
                "Compute allocation",
                CheckState.PASS if parallel_ok else CheckState.FAIL,
                f"{experiment.workers} Arbor CPU thread(s) requested."
                if engine == "arbor"
                else "The first NEURON lane runs one isolated process."
                if parallel_ok
                else "Set Workers / threads to 1 for the first NEURON lane.",
                blocking=True,
            )
        )
        if len(circuit.neuron_ids) == 1 and len(experiment.conditions) > 1:
            checks.append(
                _check(
                    "connectivity_conditions",
                    "Connection-dependent conditions",
                    CheckState.INFO,
                    "A single-cell circuit has no chemical or electrical edges; edge toggles are recorded but do not alter this run.",
                )
            )
        return PreflightReport(tuple(checks))

    def plan(
        self,
        circuit: CircuitSpec,
        experiment: ExperimentSpec,
        *,
        output_root: str | Path,
    ) -> ExecutionPlan:
        runtime = self.runtime_paths.get(experiment.engine)
        if runtime is None:
            raise ValueError(f"No {experiment.engine.upper()} runtime is configured")
        run_dir = experiment_run_directory(output_root, experiment.name)
        request_path = run_dir / "worker_request.json"
        environment = {
            "PATH": external_runtime_path(runtime),
            "MPLBACKEND": "Agg",
            "MPLCONFIGDIR": str(
                Path(output_root).expanduser().resolve() / "_runtime" / "matplotlib"
            ),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
        }
        return ExecutionPlan(
            engine=experiment.engine,
            workflow=EXPERIMENT_BUILDER_WORKFLOW,
            program=str(runtime),
            arguments=("-B", str(self.worker_path), "--request", str(request_path)),
            working_directory=str(run_dir),
            environment=environment,
            output_behavior="app_owned",
            expected_summary_path=str(run_dir / "summary.json"),
            build_time_fields=("circuit", "morphology", "membrane"),
            runtime_safe_fields=("experiment",),
        )

    def request_payload(
        self,
        circuit: CircuitSpec,
        experiment: ExperimentSpec,
        morphologies: Iterable[Morphology],
        report: PreflightReport,
        *,
        output_root: str | Path,
    ) -> dict[str, Any]:
        run_dir = experiment_run_directory(output_root, experiment.name)
        morphology_by_id = {
            item.record.neuron_id: item for item in morphologies
        }
        return {
            "schema_version": GENERIC_EXPERIMENT_SCHEMA_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "engine": experiment.engine,
            "output_dir": str(run_dir),
            "circuit": circuit.to_dict(),
            "experiment": experiment.to_dict(),
            "morphologies": {
                neuron_id: {
                    "path": morphology_by_id[neuron_id].record.swc_path,
                    "sha256": circuit.morphology_sha256.get(neuron_id, ""),
                    "neuron_type": morphology_by_id[neuron_id].record.neuron_type,
                    "family": morphology_by_id[neuron_id].record.family,
                }
                for neuron_id in circuit.neuron_ids
                if neuron_id in morphology_by_id
            },
            "preflight": report.to_dict(),
        }

    def write_request(self, path: str | Path, payload: Mapping[str, Any]) -> Path:
        destination = Path(path).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=False)
        _atomic_json(destination, payload)
        _atomic_json(
            destination.parent / "experiment.json",
            dict(payload.get("experiment") or {}),
        )
        _atomic_json(
            destination.parent / "circuit.json",
            dict(payload.get("circuit") or {}),
        )
        _atomic_json(
            destination.parent / "run_manifest.json",
            {
                "schema_version": GENERIC_EXPERIMENT_SCHEMA_VERSION,
                "state": "queued",
                "created_at": payload.get("created_at"),
                "engine": payload.get("engine"),
                "experiment": dict(payload.get("experiment") or {}),
                "summary_path": str(destination.parent / "summary.json"),
            },
        )
        return destination


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(dict(payload), handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def update_run_manifest_state(
    run_directory: str | Path,
    state: str,
    **details: Any,
) -> None:
    path = Path(run_directory).expanduser().resolve() / "run_manifest.json"
    if not path.is_file():
        return
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return
    payload.update(
        {
            "state": str(state),
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            **details,
        }
    )
    _atomic_json(path, payload)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_generic_experiment_result(path: str | Path) -> ResultRecord:
    source = Path(path).expanduser().resolve()
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Generic experiment summary must contain a JSON object")
    if int(payload.get("schema_version", 0)) != GENERIC_RESULT_SCHEMA_VERSION:
        raise ValueError("Unsupported generic experiment result schema")
    root = source.parent
    artifacts = tuple(
        Artifact(
            kind=str(item.get("kind") or "file"),
            path=str(
                (root / str(item.get("path"))).resolve()
                if not Path(str(item.get("path"))).is_absolute()
                else Path(str(item.get("path"))).resolve()
            ),
            label=str(item.get("label") or item.get("path") or "Artifact"),
            exists=(
                (root / str(item.get("path"))).is_file()
                if not Path(str(item.get("path"))).is_absolute()
                else Path(str(item.get("path"))).is_file()
            ),
        )
        for item in payload.get("artifacts", ())
        if isinstance(item, Mapping) and item.get("path")
    )
    metadata = dict(payload.get("metadata") or {})
    metadata.setdefault("Engine", str(payload.get("engine") or "unknown"))
    return ResultRecord(
        summary_path=str(source),
        status=str(payload.get("status") or "unknown"),
        completed_at=str(payload.get("completed_at") or ""),
        title=str(payload.get("title") or "Digifly experiment"),
        metadata=metadata,
        artifacts=artifacts,
    )
