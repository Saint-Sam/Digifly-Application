from __future__ import annotations

from datetime import datetime, timezone
import csv
import hashlib
from itertools import combinations
import json
import math
import os
from pathlib import Path
import platform
import re
import subprocess
import tempfile
from typing import Any, Iterable, Mapping

from digifly_app.core.circuit import CircuitSpec, HodgkinHuxleySpec
from digifly_app.core.connectomes import ConnectomeEdgeCatalog
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
from digifly_app.core.morphology import Morphology, locate_soma
from digifly_app.core.paths import resource_path, worker_path
from digifly_app.core.process_environment import (
    external_runtime_launcher,
    external_runtime_path,
    sanitized_external_environment,
)


GENERIC_EXPERIMENT_SCHEMA_VERSION = 1
GENERIC_RESULT_SCHEMA_VERSION = 1
EDGE_MANIFEST_SCHEMA_VERSION = 2
SUPPORTED_ENGINES = {"arbor", "neuron", "bmtk"}
_SAFE_COMPONENT = re.compile(r"[^a-z0-9]+")
_ARBOR_GAP_CATALOGUE_NAME = "digifly_gap"
_ARBOR_GAP_CATALOGUE_FILE = "digifly_gap-catalogue.so"
_ARBOR_GAP_CACHE_VERSION = "v1"
_SUPPORTED_ARBOR_GAP_VERSION = "0.12.2"
_NEURON_GAP_CACHE_VERSION = "v1"
_NEURON_GAP_MANIFEST_FILE = "mechanism_manifest.json"
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


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
    if engine == "bmtk":
        plotting = "import matplotlib; modules['matplotlib'] = matplotlib; " if make_plots else ""
        code = (
            "import importlib, json, pathlib, sys; "
            "modules = {}; "
            "modules['bmtk'] = importlib.import_module('bmtk'); "
            "modules['bionet'] = importlib.import_module('bmtk.simulator.bionet'); "
            "modules['neuron'] = importlib.import_module('neuron'); "
            "modules['h5py'] = importlib.import_module('h5py'); "
            "modules['numpy'] = importlib.import_module('numpy'); "
            + plotting
            + "prefix = pathlib.Path(sys.prefix).resolve(); "
            "roots = {prefix, pathlib.Path(sys.base_prefix).resolve()}; "
            "origins = {name: str(pathlib.Path(getattr(module, '__file__', '')).resolve()) "
            "for name, module in modules.items()}; "
            "outside = {name: origin for name, origin in origins.items() "
            "if not any(pathlib.Path(origin).is_relative_to(root) for root in roots)}; "
            "assert not outside, f'BMTK BioNet dependencies are outside the selected interpreter roots {sorted(map(str, roots))}: {outside}'; "
            "print(json.dumps({'python': sys.executable, 'version': getattr(modules['bmtk'], '__version__', 'unknown'), "
            "'neuron': getattr(modules['neuron'], '__version__', 'unknown'), "
            "'h5py': getattr(modules['h5py'], '__version__', 'unknown'), "
            "'numpy': getattr(modules['numpy'], '__version__', 'unknown'), "
            "'matplotlib': getattr(modules.get('matplotlib'), '__version__', None), "
            "'origins': origins}))"
        )
        module = "BMTK BioNet, NEURON, h5py, and NumPy"
    else:
        module = engine
        code = (
            "import json, sys; "
            f"import {module} as simulator; "
            + ("import matplotlib; " if make_plots else "")
            + "print(json.dumps({'python': sys.executable, "
            "'version': getattr(simulator, '__version__', 'unknown'), "
            f"'module': '{module}'" + (", 'matplotlib': matplotlib.__version__" if make_plots else "") + "}))"
        )
    runtime_environment = {
        "PATH": external_runtime_path(executable),
        "MPLBACKEND": "Agg",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
    }
    if engine == "bmtk":
        runtime_environment["NEURON_MODULE_OPTIONS"] = "-nogui"
    environment = sanitized_external_environment(runtime_environment)
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
    dependency_detail = (
        f" with NEURON {identity.get('neuron')}, h5py {identity.get('h5py')}, "
        f"and NumPy {identity.get('numpy')}"
        if engine == "bmtk"
        else ""
    )
    return _check(
        "runtime",
        f"{engine.upper()} runtime",
        CheckState.PASS,
        f"{engine.upper()} {identity.get('version')} loaded in {executable}"
        f"{dependency_detail}{plot_detail}.",
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


def _nearest_node_id(morphology: Morphology, point: tuple[float, float, float]) -> int:
    return min(
        morphology.nodes,
        key=lambda node: (
            (node.x - point[0]) ** 2
            + (node.y - point[1]) ** 2
            + (node.z - point[2]) ** 2,
            node.node_id,
        ),
    ).node_id


def _finite_contact_endpoint(
    row: Mapping[str, str], prefix: str
) -> tuple[float, float, float] | None:
    try:
        point = tuple(float(row[f"{prefix}_{axis}"]) for axis in "xyz")
    except (KeyError, TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in point):
        return None
    return point[0], point[1], point[2]


def _finite_contact_endpoints(
    row: Mapping[str, str],
) -> tuple[
    tuple[float, float, float],
    tuple[float, float, float],
    str,
    str,
]:
    """Resolve both imported endpoints without collapsing distinct contact sites."""

    pre_point = _finite_contact_endpoint(row, "pre")
    post_point = _finite_contact_endpoint(row, "post")
    if pre_point is None and post_point is None:
        raise ValueError("A selected gap-contact row has no finite endpoint coordinates")
    if pre_point is None:
        assert post_point is not None
        return post_point, post_point, "post_xyz_fallback", "post_xyz"
    if post_point is None:
        return pre_point, pre_point, "pre_xyz", "pre_xyz_fallback"
    return pre_point, post_point, "pre_xyz", "post_xyz"


def _optional_finite_float(value: Any) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _selected_morphology_issues(
    circuit: CircuitSpec,
    morphology_by_id: Mapping[str, Morphology],
) -> tuple[
    list[str],
    list[str],
    list[str],
    list[str],
    dict[str, tuple[int, ...]],
]:
    """Return fail-closed source-identity and radius diagnostics."""

    missing_sources: list[str] = []
    invalid_digests: list[str] = []
    changed_digests: list[str] = []
    unreadable_sources: list[str] = []
    nonpositive_radii: dict[str, tuple[int, ...]] = {}
    for neuron_id in circuit.neuron_ids:
        morphology = morphology_by_id.get(neuron_id)
        if morphology is None:
            missing_sources.append(neuron_id)
            invalid_digests.append(neuron_id)
            continue
        path = Path(morphology.record.swc_path)
        if not path.is_file():
            missing_sources.append(neuron_id)

        expected = str(circuit.morphology_sha256.get(neuron_id, "")).strip()
        digest_is_valid = _SHA256.fullmatch(expected) is not None
        if not digest_is_valid:
            invalid_digests.append(neuron_id)
        elif path.is_file():
            try:
                if _file_sha256(path).casefold() != expected.casefold():
                    changed_digests.append(neuron_id)
            except OSError:
                unreadable_sources.append(neuron_id)

        invalid_node_ids = tuple(
            node.node_id for node in morphology.nodes if node.radius <= 0.0
        )
        if invalid_node_ids:
            nonpositive_radii[neuron_id] = invalid_node_ids
    return (
        missing_sources,
        invalid_digests,
        changed_digests,
        unreadable_sources,
        nonpositive_radii,
    )


def _morphology_integrity_error(
    circuit: CircuitSpec,
    morphology_by_id: Mapping[str, Morphology],
) -> str | None:
    (
        missing_sources,
        invalid_digests,
        changed_digests,
        unreadable_sources,
        nonpositive_radii,
    ) = _selected_morphology_issues(circuit, morphology_by_id)
    details: list[str] = []
    if missing_sources:
        details.append("missing source SWC for " + ", ".join(missing_sources))
    if invalid_digests:
        details.append(
            "missing or invalid 64-character source_sha256 for "
            + ", ".join(invalid_digests)
        )
    if changed_digests:
        details.append(
            "source SWC no longer matches source_sha256 for "
            + ", ".join(changed_digests)
        )
    if unreadable_sources:
        details.append("unreadable source SWC for " + ", ".join(unreadable_sources))
    if nonpositive_radii:
        details.append(
            "non-positive SWC radius at "
            + ", ".join(
                f"{neuron_id} node(s) {', '.join(map(str, node_ids))}"
                for neuron_id, node_ids in nonpositive_radii.items()
            )
        )
    return "; ".join(details) or None


class GenericExperimentAdapter:
    """Capability-gated classic-HH execution for Experiment Builder."""

    def __init__(
        self,
        runtime_paths: Mapping[str, str | Path],
        *,
        digifly_public_root: str | Path | None = None,
    ):
        self.runtime_paths = {
            str(key): external_runtime_launcher(value)
            for key, value in runtime_paths.items()
            if str(value).strip()
        }
        self.digifly_public_root = (
            Path(digifly_public_root).expanduser().resolve()
            if digifly_public_root and str(digifly_public_root).strip()
            else None
        )

    @property
    def worker_path(self) -> Path:
        return worker_path("generic_experiment_worker.py")

    @property
    def bmtk_worker_path(self) -> Path:
        return worker_path("bmtk_bionet_worker.py")

    def execution_worker_path(self, engine: str) -> Path:
        return self.bmtk_worker_path if engine == "bmtk" else self.worker_path

    @property
    def gap_catalogue_builder_path(self) -> Path:
        return resource_path("scripts", "build_arbor_gap_catalogue.py")

    @property
    def neuron_gap_builder_path(self) -> Path:
        return resource_path("scripts", "build_neuron_gap_mechanisms.py")

    @property
    def parquet_edge_query_worker_path(self) -> Path:
        return worker_path("parquet_edge_query_worker.py")

    def gap_catalogue_path(self, output_root: str | Path) -> Path:
        override = os.environ.get("DIGIFLY_ARBOR_GAP_CATALOGUE", "").strip()
        if override:
            return Path(override).expanduser().resolve()
        runtime = Path(output_root).expanduser().resolve() / "_runtime" / "arbor_catalogues"
        cache_key = (
            f"arbor-{_SUPPORTED_ARBOR_GAP_VERSION}-{platform.system().lower()}-"
            f"{platform.machine().lower()}-digifly-gap-{_ARBOR_GAP_CACHE_VERSION}"
        )
        return runtime / cache_key / _ARBOR_GAP_CATALOGUE_FILE

    def neuron_gap_mechanism_path(self, output_root: str | Path) -> Path:
        """Return an interpreter-specific cache directory for compiled NMODL."""

        override = os.environ.get("DIGIFLY_NEURON_GAP_MECHANISMS", "").strip()
        if override:
            return Path(override).expanduser().resolve()
        runtime = self.runtime_paths.get("neuron")
        runtime_identity = "unconfigured"
        if runtime is not None and runtime.is_file():
            stat = runtime.stat()
            runtime_identity = hashlib.sha256(
                f"{runtime}:{stat.st_size}:{stat.st_mtime_ns}".encode("utf-8")
            ).hexdigest()[:16]
        cache_root = (
            Path(output_root).expanduser().resolve()
            / "_runtime"
            / "neuron_mechanisms"
        )
        cache_key = (
            f"neuron-{platform.system().lower()}-{platform.machine().lower()}-"
            f"{runtime_identity}-digifly-gap-{_NEURON_GAP_CACHE_VERSION}"
        )
        return cache_root / cache_key

    def _pair_override(self, circuit: CircuitSpec):
        if len(circuit.neuron_ids) != 2:
            return None
        return circuit.connection_override(*circuit.neuron_ids)

    def _edge_catalog(self, circuit: CircuitSpec) -> ConnectomeEdgeCatalog:
        from digifly_app.core.workspace import DigiflyWorkspace

        workspace = (
            DigiflyWorkspace(self.digifly_public_root)
            if self.digifly_public_root is not None
            else None
        )
        return ConnectomeEdgeCatalog(circuit.connectome, workspace)

    def _gap_summary(self, circuit: CircuitSpec):
        if len(circuit.neuron_ids) != 2:
            return None
        if self.digifly_public_root is None:
            return None
        # ConnectomeEdgeCatalog only reads the supplied roots and opens native
        # SQLite inputs immutable/read-only.
        from digifly_app.core.workspace import DigiflyWorkspace

        return ConnectomeEdgeCatalog(
            circuit.connectome,
            DigiflyWorkspace(self.digifly_public_root),
        ).pair_summary(*circuit.neuron_ids).gap_junction

    def _male_cns_chemical_contacts(
        self,
        catalog: ConnectomeEdgeCatalog,
        neuron_ids: tuple[str, ...],
    ) -> tuple[dict[str, Any], ...]:
        runtime = (
            self.runtime_paths.get("arbor")
            or self.runtime_paths.get("neuron")
            or self.runtime_paths.get("bmtk")
        )
        if runtime is None or not runtime.is_file():
            raise ValueError(
                "Choose an Arbor, NEURON, or BMTK BioNet scientific Python before querying Male-CNS contacts."
            )
        worker = self.parquet_edge_query_worker_path
        if not worker.is_file():
            raise ValueError(f"The packaged Male-CNS edge query worker is missing: {worker}")
        arguments = [
            str(runtime),
            "-B",
            str(worker),
            "--source",
            str(catalog.chemical_parquet),
        ]
        for neuron_id in neuron_ids:
            arguments.extend(("--neuron-id", neuron_id))
        environment = sanitized_external_environment(
            {
                "PATH": external_runtime_path(runtime),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONNOUSERSITE": "1",
            }
        )
        completed = subprocess.run(
            arguments,
            env=environment,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        if completed.returncode:
            detail = (completed.stderr or completed.stdout).strip()
            raise ValueError(
                detail[-6000:]
                or "The selected scientific Python could not query Male-CNS contacts."
            )
        rows: list[dict[str, Any]] = []
        for line_number, line in enumerate(completed.stdout.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Male-CNS edge query returned invalid JSON at row {line_number}."
                ) from exc
            if not isinstance(payload, dict):
                raise ValueError("Male-CNS edge query returned a non-object row.")
            rows.append(payload)
        return tuple(rows)

    def needs_gap_catalogue(self, circuit: CircuitSpec, experiment: ExperimentSpec) -> bool:
        return bool(
            experiment.engine in {"arbor", "neuron"}
            and len(circuit.neuron_ids) > 1
            and circuit.gap_junction_policy.mode != "none"
        )

    def ensure_gap_catalogue(
        self,
        circuit: CircuitSpec,
        experiment: ExperimentSpec,
        *,
        output_root: str | Path,
    ) -> Path | None:
        """Build the engine-specific app-owned gap mechanisms on first use."""

        if not self.needs_gap_catalogue(circuit, experiment):
            return None
        engine = experiment.engine
        if engine == "arbor":
            destination = self.gap_catalogue_path(output_root)
            manifest = destination.with_name("catalogue_manifest.json")
            runtime_key = "arbor"
            builder = self.gap_catalogue_builder_path
            builder_label = "Arbor catalogue"
            build_dir = destination.parent
        else:
            destination = self.neuron_gap_mechanism_path(output_root)
            manifest = destination / _NEURON_GAP_MANIFEST_FILE
            runtime_key = "neuron"
            builder = self.neuron_gap_builder_path
            builder_label = "NEURON mechanism"
            build_dir = destination
        if destination.is_file() and manifest.is_file():
            return destination
        if engine == "neuron" and destination.is_dir() and manifest.is_file():
            return destination
        runtime = self.runtime_paths.get(runtime_key)
        if runtime is None or not runtime.is_file():
            raise RuntimeError(
                f"Choose a {engine.upper()} Python before building gap mechanisms."
            )
        if not builder.is_file():
            raise RuntimeError(
                f"The packaged {builder_label} builder is missing: {builder}"
            )
        if build_dir.exists() and any(build_dir.iterdir()):
            raise RuntimeError(
                f"The incomplete {builder_label} cache is not empty: {build_dir}"
            )
        build_dir.mkdir(parents=True, exist_ok=True)
        environment = sanitized_external_environment(
            {
                "PATH": external_runtime_path(runtime),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONNOUSERSITE": "1",
            }
        )
        completed = subprocess.run(
            [
                str(runtime),
                "-B",
                str(builder),
                "--python",
                str(runtime),
                "--output-dir",
                str(build_dir),
            ],
            env=environment,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        artifact_ok = destination.is_file() if engine == "arbor" else destination.is_dir()
        if completed.returncode or not artifact_ok or not manifest.is_file():
            detail = (completed.stderr or completed.stdout).strip()
            raise RuntimeError(
                detail[-6000:]
                or f"The app-owned {builder_label} cache could not be built."
            )
        return destination

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
                    f"{engine.upper()} is not connected yet. Choose Arbor, NEURON, or BMTK BioNet.",
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
        selected_worker = self.execution_worker_path(engine)
        checks.append(
            _check(
                "worker",
                "App-owned simulator worker",
                CheckState.PASS if selected_worker.is_file() else CheckState.FAIL,
                f"The standalone {engine.upper()} worker script is present."
                if selected_worker.is_file()
                else f"The packaged {engine.upper()} worker script is missing.",
                blocking=True,
                path=selected_worker,
            )
        )

        morphology_by_id = {
            morphology.record.neuron_id: morphology for morphology in morphologies
        }
        candidate_manifest: dict[str, Any] | None = None
        scope_error = ""
        if engine in SUPPORTED_ENGINES:
            try:
                candidate_manifest = self._edge_manifest(
                    circuit, morphology_by_id.values()
                )
            except (OSError, ValueError) as exc:
                scope_error = str(exc)
        if candidate_manifest is not None:
            chemical_count = len(candidate_manifest.get("chemical_edges") or ())
            electrical_count = sum(
                int(edge.get("contact_count", 0))
                for edge in candidate_manifest.get("electrical_edges") or ()
            )
            scope_detail = (
                f"The {len(circuit.neuron_ids)}-cell selected subgraph is executable. "
                f"Its run-owned manifest contains {chemical_count} chemical contact(s) "
                f"and {electrical_count} electrical contact(s); condition toggles rebuild "
                "the same cells with either connection class enabled or disabled."
            )
            scope_state = CheckState.PASS
        else:
            scope_detail = (
                "The selected network edge manifest cannot be materialized without omitting or inventing connectivity: "
                + (scope_error or "no executable cells are selected")
            )
            scope_state = CheckState.FAIL
        checks.append(
            _check(
                "circuit_scope",
                "Executable circuit scope",
                scope_state,
                scope_detail,
                blocking=True,
            )
        )

        if engine == "bmtk":
            electrical_count = (
                sum(
                    int(edge.get("contact_count", 0))
                    for edge in candidate_manifest.get("electrical_edges") or ()
                )
                if candidate_manifest is not None
                else 0
            )
            bmtk_connectivity_ok = (
                circuit.gap_junction_policy.mode == "none"
                and electrical_count == 0
            )
            checks.append(
                _check(
                    "bmtk_connectivity",
                    "BMTK BioNet connectivity subset",
                    CheckState.PASS if bmtk_connectivity_ok else CheckState.FAIL,
                    "The first BioNet lane preserves morphology-backed cells and chemical contacts in run-owned SONATA files."
                    if bmtk_connectivity_ok
                    else "BMTK BioNet currently executes chemical contacts only. Choose no gap-junction mechanism; electrical contacts are never silently dropped or approximated.",
                    blocking=True,
                )
            )

        if self.needs_gap_catalogue(circuit, experiment):
            if engine == "arbor":
                catalogue = self.gap_catalogue_path(output)
                manifest = catalogue.with_name("catalogue_manifest.json")
                catalogue_ok = catalogue.is_file() and manifest.is_file()
                key = "arbor_gap_catalogue"
                title = "App-owned Arbor gap catalogue"
                detail = (
                    "The compiler/ABI-specific app-owned Digifly gap catalogue is ready."
                    if catalogue_ok
                    else "The Digifly gap catalogue is not built for this Arbor runtime yet. Run will build it once in the app-owned runtime cache."
                )
            else:
                catalogue = self.neuron_gap_mechanism_path(output)
                manifest = catalogue / _NEURON_GAP_MANIFEST_FILE
                catalogue_ok = catalogue.is_dir() and manifest.is_file()
                key = "neuron_gap_mechanisms"
                title = "App-owned NEURON gap mechanisms"
                detail = (
                    "The compiler/ABI-specific app-owned NEURON mechanisms are ready."
                    if catalogue_ok
                    else "The Digifly NEURON gap mechanisms are not built for this runtime yet. Run will compile them once in the app-owned runtime cache."
                )
            checks.append(
                _check(
                    key,
                    title,
                    CheckState.PASS if catalogue_ok else CheckState.FAIL,
                    detail,
                    blocking=True,
                    path=catalogue,
                )
            )

        (
            missing,
            invalid_digests,
            changed_digests,
            unreadable_sources,
            nonpositive_radii,
        ) = _selected_morphology_issues(circuit, morphology_by_id)
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
        identity_failed = bool(
            invalid_digests or changed_digests or unreadable_sources
        )
        identity_problems: list[str] = []
        if invalid_digests:
            identity_problems.append(
                "Missing or invalid source_sha256 (expected 64 hexadecimal characters) for: "
                + ", ".join(invalid_digests)
            )
        if changed_digests:
            identity_problems.append(
                "Source SWC bytes no longer match source_sha256 for: "
                + ", ".join(changed_digests)
            )
        if unreadable_sources:
            identity_problems.append(
                "Source SWCs could not be read for hashing: "
                + ", ".join(unreadable_sources)
            )
        identity_detail = (
            " ".join(identity_problems)
            + " Reload each affected SWC in Circuit Builder so Digifly records and verifies its source identity before running."
            if identity_problems
            else "Every selected morphology has a valid source SHA-256 that still matches its source SWC."
        )
        checks.append(
            _check(
                "morphology_identity",
                "Morphology identity",
                CheckState.FAIL if identity_failed else CheckState.PASS,
                identity_detail,
                blocking=True,
            )
        )
        radius_detail = (
            "Non-positive SWC radii were found at: "
            + "; ".join(
                f"{neuron_id} node(s) {', '.join(map(str, node_ids))}"
                for neuron_id, node_ids in nonpositive_radii.items()
            )
            + ". Use Circuit Builder's SWC quality/healer workflow, select the repaired SWC, and reload the circuit before running."
            if nonpositive_radii
            else "Every selected SWC node has a positive radius; tiny positive radii are preserved without a hidden floor."
        )
        checks.append(
            _check(
                "morphology_radii",
                "Morphology radii",
                CheckState.FAIL if nonpositive_radii else CheckState.PASS,
                radius_detail,
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
                "Built-in classic HH and its regional conductances are supported on the selected engine."
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

        if engine == "bmtk":
            sampling_ratio = (
                experiment.recording.sample_dt_ms / experiment.integration_dt_ms
            )
            duration_ratio = (
                experiment.duration_ms / experiment.recording.sample_dt_ms
            )
            integration_grid_ok = math.isclose(
                sampling_ratio,
                round(sampling_ratio),
                rel_tol=1e-9,
                abs_tol=1e-9,
            )
            duration_grid_ok = math.isclose(
                duration_ratio,
                round(duration_ratio),
                rel_tol=1e-9,
                abs_tol=1e-9,
            )
            bmtk_sampling_ok = integration_grid_ok and duration_grid_ok
            checks.append(
                _check(
                    "bmtk_sampling",
                    "BMTK BioNet sampling interval",
                    CheckState.PASS if bmtk_sampling_ok else CheckState.FAIL,
                    "The recording interval is an integer multiple of the fixed integration step and divides the duration into a whole number of samples."
                    if bmtk_sampling_ok
                    else "For BioNet, choose a recording interval that is both an integer multiple of the integration step and an exact divisor of the experiment duration.",
                    blocking=True,
                )
            )

        parallel_ok = engine == "arbor" or experiment.workers == 1
        checks.append(
            _check(
                "parallelism",
                "Compute allocation",
                CheckState.PASS if parallel_ok else CheckState.FAIL,
                f"{experiment.workers} Arbor CPU thread(s) requested."
                if engine == "arbor"
                else f"The first {engine.upper()} lane runs one isolated process."
                if parallel_ok
                else f"Set Workers / threads to 1 for the first {engine.upper()} lane.",
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
        elif candidate_manifest is not None:
            checks.append(
                _check(
                    "connectivity_conditions",
                    "Connection-dependent conditions",
                    CheckState.PASS,
                    "Chemical and electrical condition switches independently include or remove the exact run-owned contact sets.",
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
        if experiment.engine in {"neuron", "bmtk"}:
            environment["NEURON_MODULE_OPTIONS"] = "-nogui"
        selected_worker = self.execution_worker_path(experiment.engine)
        return ExecutionPlan(
            engine=experiment.engine,
            workflow=EXPERIMENT_BUILDER_WORKFLOW,
            program=str(runtime),
            arguments=("-B", str(selected_worker), "--request", str(request_path)),
            working_directory=str(run_dir),
            environment=environment,
            output_behavior="app_owned",
            expected_summary_path=str(run_dir / "summary.json"),
            build_time_fields=("circuit", "morphology", "membrane"),
            runtime_safe_fields=("experiment",),
        )

    def _edge_manifest(
        self,
        circuit: CircuitSpec,
        morphologies: Iterable[Morphology],
    ) -> dict[str, Any]:
        neuron_ids = tuple(circuit.neuron_ids)
        if not neuron_ids:
            raise ValueError("Select at least one morphology-backed neuron.")
        if len(set(neuron_ids)) != len(neuron_ids):
            raise ValueError("The selected circuit contains duplicate neuron IDs.")
        morphology_by_id = {
            morphology.record.neuron_id: morphology for morphology in morphologies
        }
        if any(neuron_id not in morphology_by_id for neuron_id in neuron_ids):
            raise ValueError("Every selected neuron needs a loaded morphology.")

        selected_pairs = tuple(combinations(neuron_ids, 2))
        # Phase-2 SWCs usually contain explicit synapse nodes at the imported
        # contact coordinates. Resolve those in O(1) and retain the exact
        # nearest-node fallback for plain skeletons. This matters for large
        # selected subgraphs where a linear scan per contact is needlessly
        # expensive but must not change placement semantics.
        exact_node_ids: dict[str, dict[tuple[float, float, float], int]] = {}
        for neuron_id, morphology in morphology_by_id.items():
            coordinates: dict[tuple[float, float, float], int] = {}
            for node in morphology.nodes:
                point = (node.x, node.y, node.z)
                previous = coordinates.get(point)
                if previous is None or node.node_id < previous:
                    coordinates[point] = node.node_id
            exact_node_ids[neuron_id] = coordinates

        def nearest_node_id(neuron_id: str, point: tuple[float, float, float]) -> int:
            exact = exact_node_ids[neuron_id].get(point)
            if exact is not None:
                return exact
            return _nearest_node_id(morphology_by_id[neuron_id], point)

        def pair_allows(first: str, second: str, connection_class: str) -> bool:
            if first == second:
                return True
            override = circuit.connection_override(first, second)
            if override is None:
                return True
            value = (
                override.chemical_enabled
                if connection_class == "chemical"
                else override.gap_junction_enabled
            )
            return value is not False

        edge_catalog = self._edge_catalog(circuit)
        sources: list[dict[str, Any]] = []
        chemical_rows: tuple[dict[str, Any], ...] = ()
        try:
            if edge_catalog.is_male_cns:
                chemical_rows = self._male_cns_chemical_contacts(
                    edge_catalog, neuron_ids
                )
                sources.append(edge_catalog.male_cns_source_record())
            else:
                chemical_rows = edge_catalog.selected_chemical_contacts(neuron_ids)
                sources.append(edge_catalog.chemical_source_record())
        except ValueError as exc:
            all_pairs_disabled = bool(selected_pairs) and all(
                (
                    (override := circuit.connection_override(first, second))
                    is not None
                    and override.chemical_enabled is False
                )
                for first, second in selected_pairs
            )
            if len(neuron_ids) > 1 and not all_pairs_disabled:
                raise ValueError(str(exc)) from exc

        chemical_policy = circuit.chemical_synapse_policy
        chemical_edges: list[dict[str, Any]] = []
        for row in chemical_rows:
            pre_id, post_id = str(row["pre_id"]), str(row["post_id"])
            if not pair_allows(pre_id, post_id, "chemical"):
                continue
            try:
                post_point = tuple(float(row[f"post_{axis}"]) for axis in "xyz")
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(
                    f"Chemical contact row {row.get('source_edge_rowid')} has no postsynaptic coordinate."
                ) from exc
            if not all(math.isfinite(value) for value in post_point):
                raise ValueError(
                    f"Chemical contact row {row.get('source_edge_rowid')} has a non-finite postsynaptic coordinate."
                )
            source_weight = _optional_finite_float(row.get("weight_uS"))
            source_delay = _optional_finite_float(row.get("delay_ms"))
            source_tau1 = _optional_finite_float(row.get("tau1_ms"))
            source_tau2 = _optional_finite_float(row.get("tau2_ms"))
            source_reversal = _optional_finite_float(row.get("syn_e_rev_mV"))
            effective_delay = (
                chemical_policy.base_release_delay_ms
                + math.dist(
                    locate_soma(morphology_by_id[pre_id]).point,
                    post_point,
                )
                / chemical_policy.conduction_velocity_um_per_ms
                if chemical_policy.use_geometric_delay
                else (
                    source_delay
                    if source_delay is not None
                    else chemical_policy.default_delay_ms
                )
            )
            chemical_edges.append(
                {
                    "contact_id": f"chem-{len(chemical_edges) + 1:07d}",
                    "source_edge_rowid": int(row["source_edge_rowid"]),
                    "source_edge_id": str(row.get("source_edge_id") or ""),
                    "pre_id": pre_id,
                    "post_id": post_id,
                    "post_source_node_id": nearest_node_id(
                        post_id, (post_point[0], post_point[1], post_point[2])
                    ),
                    "post_coordinate_um": list(post_point),
                    "weight_uS": float(
                        (source_weight if source_weight is not None else chemical_policy.default_weight_uS)
                        * chemical_policy.weight_scale
                    ),
                    "delay_ms": float(effective_delay),
                    "tau1_ms": float(
                        source_tau1 if source_tau1 is not None else chemical_policy.tau1_ms
                    ),
                    "tau2_ms": float(
                        source_tau2 if source_tau2 is not None else chemical_policy.tau2_ms
                    ),
                    "reversal_mV": float(
                        source_reversal
                        if source_reversal is not None
                        else chemical_policy.reversal_mV
                    ),
                    "parameter_sources": {
                        "weight": "connectome_row" if source_weight is not None else "circuit_policy",
                        "delay": (
                            "circuit_policy_geometric"
                            if chemical_policy.use_geometric_delay
                            else "connectome_row"
                            if source_delay is not None
                            else "circuit_policy"
                        ),
                        "tau1": "connectome_row" if source_tau1 is not None else "circuit_policy",
                        "tau2": "connectome_row" if source_tau2 is not None else "circuit_policy",
                        "reversal": "connectome_row" if source_reversal is not None else "circuit_policy",
                    },
                    "source_annotations": {
                        "confidence": row.get("confidence"),
                        "neurotransmitter": row.get("neurotransmitter"),
                        "neurotransmitter_confidence": row.get(
                            "neurotransmitter_confidence"
                        ),
                        "neurotransmitter_probabilities": row.get(
                            "neurotransmitter_probabilities"
                        ),
                    },
                }
            )

        electrical_rows: tuple[dict[str, str], ...] = ()
        gap_policy = circuit.gap_junction_policy
        if len(neuron_ids) > 1 and gap_policy.mode != "none":
            if gap_policy.placement_policy != "imported_contact_sites":
                raise ValueError(
                    "General electrical execution requires imported contact-site placement."
                )
            try:
                electrical_rows = edge_catalog.selected_gap_contacts(neuron_ids)
                sources.append(edge_catalog.gap_source_record())
            except ValueError as exc:
                all_pairs_disabled = all(
                    (
                        (override := circuit.connection_override(first, second))
                        is not None
                        and override.gap_junction_enabled is False
                    )
                    for first, second in selected_pairs
                )
                if not all_pairs_disabled:
                    raise ValueError(str(exc)) from exc

        rows_by_pair: dict[tuple[str, str], list[dict[str, str]]] = {}
        for row in electrical_rows:
            pre_id, post_id = str(row["pre_id"]), str(row["post_id"])
            if pre_id == post_id or not pair_allows(pre_id, post_id, "gap_junction"):
                continue
            pair = tuple(sorted((pre_id, post_id)))
            rows_by_pair.setdefault(pair, []).append(row)

        electrical_edges: list[dict[str, Any]] = []
        contact_serial = 0
        for (neuron_a, neuron_b), rows in sorted(rows_by_pair.items()):
            per_contact_g = (
                gap_policy.g_uS
                if gap_policy.conductance_basis == "per_site"
                else gap_policy.g_uS / len(rows)
            )
            contacts: list[dict[str, Any]] = []
            for row in rows:
                contact_serial += 1
                (
                    pre_point,
                    post_point,
                    pre_coordinate_source,
                    post_coordinate_source,
                ) = _finite_contact_endpoints(row)
                pre_id, post_id = str(row["pre_id"]), str(row["post_id"])
                contacts.append(
                    {
                        "contact_id": f"gap-{contact_serial:07d}",
                        "source_edge_rowid": str(row.get("source_edge_rowid") or ""),
                        "pre_id": pre_id,
                        "post_id": post_id,
                        "pre_source_node_id": nearest_node_id(pre_id, pre_point),
                        "post_source_node_id": nearest_node_id(post_id, post_point),
                        "pre_coordinate_um": list(pre_point),
                        "post_coordinate_um": list(post_point),
                        "coordinate_sources": {
                            "pre": pre_coordinate_source,
                            "post": post_coordinate_source,
                        },
                        "source_g_uS": float(row.get("g_uS") or 0.0),
                        "effective_g_uS": float(per_contact_g),
                    }
                )
            electrical_edges.append(
                {
                    "edge_id": f"gap-pair-{neuron_a}-{neuron_b}",
                    "neuron_a": neuron_a,
                    "neuron_b": neuron_b,
                    "contact_count": len(contacts),
                    "contacts": contacts,
                }
            )

        selected_contact_identity = hashlib.sha256(
            json.dumps(
                {"chemical": chemical_edges, "electrical": electrical_edges},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return {
            "schema_version": EDGE_MANIFEST_SCHEMA_VERSION,
            "kind": "explicit_selected_subgraph",
            "neuron_ids": list(neuron_ids),
            "sources": sources,
            "selected_contact_identity_sha256": selected_contact_identity,
            "connection_overrides": [
                override.to_dict()
                for override in sorted(
                    circuit.connection_overrides.values(),
                    key=lambda item: (item.neuron_a, item.neuron_b),
                )
            ],
            "chemical_synapse_policy": chemical_policy.to_dict(),
            "gap_junction_policy": gap_policy.to_dict(),
            "mapping_policy": {
                "chemical": "Map each imported post_xyz coordinate to the nearest source SWC node.",
                "electrical": (
                    "Map imported pre_xyz to the presynaptic source SWC and imported post_xyz "
                    "to the postsynaptic source SWC; only a missing endpoint falls back to its peer."
                ),
            },
            "chemical_edges": chemical_edges,
            "electrical_edges": electrical_edges,
        }

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
        integrity_error = _morphology_integrity_error(circuit, morphology_by_id)
        if integrity_error:
            raise ValueError(
                "Cannot build a simulator request because morphology integrity checks failed: "
                f"{integrity_error}. Reload affected SWCs in Circuit Builder and use the "
                "SWC quality/healer workflow for non-positive radii."
            )
        edge_manifest = self._edge_manifest(circuit, morphology_by_id.values())
        neuron_gap_manifest = (
            self.neuron_gap_mechanism_path(output_root) / _NEURON_GAP_MANIFEST_FILE
        )
        return {
            "schema_version": GENERIC_EXPERIMENT_SCHEMA_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "engine": experiment.engine,
            "output_dir": str(run_dir),
            "circuit": circuit.to_dict(),
            "experiment": experiment.to_dict(),
            "edge_manifest": edge_manifest,
            "arbor_gap_catalogue": (
                str(self.gap_catalogue_path(output_root))
                if experiment.engine == "arbor"
                and self.needs_gap_catalogue(circuit, experiment)
                else ""
            ),
            "neuron_gap_mechanisms": (
                str(self.neuron_gap_mechanism_path(output_root))
                if experiment.engine == "neuron"
                and self.needs_gap_catalogue(circuit, experiment)
                else ""
            ),
            "neuron_gap_manifest_sha256": (
                _file_sha256(neuron_gap_manifest)
                if experiment.engine == "neuron"
                and self.needs_gap_catalogue(circuit, experiment)
                and neuron_gap_manifest.is_file()
                else ""
            ),
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
        request_payload = dict(payload)
        edge_manifest = dict(request_payload.pop("edge_manifest") or {})
        edge_manifest_path = destination.parent / "edge_manifest.json"
        _atomic_json(edge_manifest_path, edge_manifest)
        request_payload["edge_manifest_path"] = str(edge_manifest_path)
        request_payload["edge_manifest_sha256"] = _file_sha256(edge_manifest_path)
        _atomic_json(destination, request_payload)
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
                "edge_manifest_path": str(edge_manifest_path),
                "edge_manifest_sha256": request_payload["edge_manifest_sha256"],
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
    artifacts_list: list[Artifact] = []
    for item in payload.get("artifacts", ()):
        if not isinstance(item, Mapping) or not item.get("path"):
            continue
        raw_path = str(item["path"])
        relative_path = Path(raw_path)
        if relative_path.is_absolute():
            raise ValueError(
                f"Generic experiment artifact paths must be run-relative, not absolute: {raw_path}"
            )
        if ".." in relative_path.parts:
            raise ValueError(
                f"Generic experiment artifact paths cannot contain '..': {raw_path}"
            )
        resolved = (root / relative_path).resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError(
                f"Generic experiment artifact resolves outside its run directory: {raw_path}"
            ) from exc
        artifacts_list.append(
            Artifact(
                kind=str(item.get("kind") or "file"),
                path=str(resolved),
                label=str(item.get("label") or raw_path or "Artifact"),
                exists=resolved.is_file(),
            )
        )
    artifacts = tuple(artifacts_list)
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
