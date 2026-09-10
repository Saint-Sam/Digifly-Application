from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from typing import Iterable, TYPE_CHECKING

from .models import CheckState, EngineProbe, PreflightCheck, PreflightReport
from .paths import resource_path, worker_path
from .process_environment import (
    external_runtime_launcher,
    external_runtime_path,
    sanitized_external_environment,
)
from .runtime_discovery import probe_simulator_runtime

if TYPE_CHECKING:
    from .resource_profile import ResourceProfile


class DigiflyWorkspace:
    """Resolved native-file layout for a Digifly Public workspace."""

    def __init__(self, root: str | Path, profile: "ResourceProfile | None" = None):
        self.root = Path(root).expanduser().resolve()
        self.profile = profile

    @property
    def phase2_neuron(self) -> Path:
        return self.root / "Phase 2"

    @property
    def phase2_arbor(self) -> Path:
        return self.root / "Phase 2_Arbor_staging"

    @property
    def phase2_bmtk(self) -> Path:
        return self.root / "Phase 2 BMTK"

    @property
    def escape_siz_root(self) -> Path:
        return self.phase2_neuron / "Projects" / "Escape-SIZ"

    @property
    def escape_siz_handoff(self) -> Path:
        return self.escape_siz_root / "ESCAPE_SIZ_AGENT_HANDOFF.md"

    @property
    def plotting_contract(self) -> Path:
        return self.escape_siz_root / "SIMULATION_PLOTTING_CONTRACT.md"

    @property
    def gfc_root(self) -> Path:
        return self.escape_siz_root / "Giant Fiber Ablation Comparisons"

    @property
    def voltage_sink_root(self) -> Path:
        return self.escape_siz_root / "Voltage Sink Studies"

    @property
    def vnd_candidates(self) -> tuple[Path, ...]:
        configured: tuple[Path, ...] = ()
        if self.profile is not None:
            from .resource_profile import ResourceKind

            binding = self.profile.binding(ResourceKind.VND_VIEWER)
            configured = (binding.resolved_path,) if binding is not None else ()
        desktop = self.root.parent
        return configured + (
            desktop / "VND 1.14 UIUC" / "VND r1.14_1.9.4a57-arm64.app",
            desktop / "VND 1.14 UIUC" / "VND r1.14_1.9.4a57.app",
        )

    @property
    def bmtk_python_candidates(self) -> tuple[Path, ...]:
        configured: tuple[Path, ...] = ()
        if self.profile is not None:
            from .resource_profile import ResourceKind

            runtime = self.profile.runtime_path(ResourceKind.BMTK_RUNTIME)
            configured = (runtime,) if runtime is not None else ()
        desktop = self.root.parent
        return configured + (
            desktop / "Digifly-Runtimes" / "envs" / "digifly-bmtk-dpointnet-py311" / "bin" / "python",
            Path("/opt/anaconda3/envs/digifly-bmtk-dpointnet-py311/bin/python"),
        )

    def base_preflight(self) -> PreflightReport:
        """Validate only the generic Digifly workspace boundary.

        Scientific workflow markers belong to their adapters.  In particular,
        an Escape-SIZ handoff, plotting contract, or cache is not required for
        a healthy Digifly Workstation installation.
        """
        checks: list[PreflightCheck] = []
        checks.append(
            _path_check(
                "workspace",
                "Digifly workspace",
                self.root,
                expected="directory",
                blocking=True,
            )
        )
        checks.append(
            _path_check(
                "readme",
                "Digifly Public marker",
                self.root / "README.md",
                expected="file",
                blocking=True,
            )
        )
        return PreflightReport(tuple(checks))

    def probe_engines(
        self,
        python_executable: str,
        arbor_python_executable: str | None = None,
        bmtk_python_executable: str | None = None,
    ) -> tuple[EngineProbe, ...]:
        neuron_python = Path(python_executable).expanduser()
        arbor_python = Path(arbor_python_executable or python_executable).expanduser()
        bmtk_python = (
            Path(bmtk_python_executable).expanduser()
            if bmtk_python_executable and str(bmtk_python_executable).strip()
            else None
        )
        if self.profile is not None:
            from .resource_profile import ResourceKind

            neuron_python = self.profile.runtime_path(ResourceKind.NEURON_RUNTIME) or neuron_python
            arbor_python = self.profile.runtime_path(ResourceKind.ARBOR_RUNTIME) or neuron_python
            bmtk_python = self.profile.runtime_path(ResourceKind.BMTK_RUNTIME) or bmtk_python
        neuron_worker = worker_path("generic_experiment_worker.py")
        neuron_mechanism_root = resource_path(
            "mechanisms", "neuron_gap_junctions"
        )
        required_neuron_sources = (
            neuron_mechanism_root / "Gap.mod",
            neuron_mechanism_root / "RectGap.mod",
            neuron_mechanism_root / "HeteroRectGap.mod",
            neuron_mechanism_root / "source_manifest.json",
        )
        neuron_source_ok = neuron_worker.is_file() and all(
            path.is_file() for path in required_neuron_sources
        )
        neuron_runtime = _probe_neuron_runtime(str(neuron_python))
        neuron = EngineProbe(
            key="neuron",
            name="NEURON",
            source_state=CheckState.PASS if neuron_source_ok else CheckState.FAIL,
            runtime_state=neuron_runtime[0],
            summary=(
                "The app-owned NEURON worker and mechanism sources are ready with the selected runtime."
                if neuron_source_ok and neuron_runtime[0] == CheckState.PASS
                else "NEURON needs its packaged worker, mechanism sources, and a compatible external runtime."
            ),
            details=(
                str(neuron_worker),
                str(neuron_mechanism_root),
                neuron_runtime[1],
            ),
        )

        arbor_source = self.phase2_arbor / "digifly" / "phase2"
        arbor_runtime = _probe_python_module(str(arbor_python), "arbor")
        arbor = EngineProbe(
            key="arbor",
            name="Arbor",
            source_state=CheckState.PASS if arbor_source.is_dir() else CheckState.WARNING,
            runtime_state=arbor_runtime[0],
            summary=(
                "Cache-free staging source and Arbor runtime are available."
                if arbor_source.is_dir() and arbor_runtime[0] == CheckState.PASS
                else "Arbor source or runtime is not yet configured."
            ),
            details=(str(arbor_source), arbor_runtime[1]),
        )

        bmtk_source = self.phase2_bmtk / "src" / "digifly_bmtk"
        bmtk_worker = worker_path("bmtk_bionet_worker.py")
        bmtk_runtime = (
            _probe_bionet_runtime(str(bmtk_python))
            if bmtk_python
            else (
                CheckState.WARNING,
                "No BMTK BioNet interpreter was selected. It must contain BMTK, NEURON, NumPy, and h5py together.",
            )
        )
        bmtk = EngineProbe(
            key="bmtk",
            name="BMTK / SONATA",
            source_state=CheckState.PASS if bmtk_worker.is_file() else CheckState.FAIL,
            runtime_state=bmtk_runtime[0],
            summary=(
                "The app-owned BioNet worker and a compatible external runtime are ready."
                if bmtk_worker.is_file() and bmtk_runtime[0] == CheckState.PASS
                else "BMTK BioNet needs its app-owned worker and one compatible external runtime."
            ),
            details=(
                str(bmtk_worker),
                str(bmtk_python or "runtime not configured"),
                bmtk_runtime[1],
                (
                    f"Optional legacy interoperability source: {bmtk_source}"
                    if bmtk_source.is_dir()
                    else "No legacy BMTK source is required by this app-owned lane."
                ),
            ),
        )

        vnd_path = next((path for path in self.vnd_candidates if path.exists()), None)
        vnd = EngineProbe(
            key="vnd",
            name="VND",
            source_state=CheckState.PASS if vnd_path else CheckState.WARNING,
            runtime_state=CheckState.PASS if vnd_path else CheckState.WARNING,
            summary="External VND application detected." if vnd_path else "VND is optional and was not detected.",
            details=(str(vnd_path) if vnd_path else "No configured VND application path",),
        )
        return neuron, arbor, bmtk, vnd


def _path_check(
    key: str,
    title: str,
    path: Path,
    *,
    expected: str,
    blocking: bool,
) -> PreflightCheck:
    exists = path.is_dir() if expected == "directory" else path.is_file()
    return PreflightCheck(
        key=key,
        title=title,
        state=CheckState.PASS if exists else CheckState.FAIL,
        detail=f"Found {path}" if exists else f"Missing required {expected}: {path}",
        blocking=blocking,
        path=str(path),
    )


def _probe_python_module(
    python_executable: str,
    module: str,
    *,
    clean_environment: bool = False,
) -> tuple[CheckState, str]:
    executable = Path(python_executable).expanduser()
    if not executable.is_file():
        return CheckState.FAIL, f"Python executable not found: {executable}"
    snippet = (
        "import importlib.util, json; "
        f"s=importlib.util.find_spec({module!r}); "
        "print(json.dumps({'found': bool(s), 'origin': getattr(s, 'origin', None)}))"
    )
    try:
        environment = sanitized_external_environment({})
        if clean_environment:
            environment["PYTHONNOUSERSITE"] = "1"
        completed = subprocess.run(
            [str(executable), "-c", snippet],
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CheckState.FAIL, f"Runtime probe failed: {exc}"
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout).strip()
        return CheckState.FAIL, message or f"Could not probe {module}."
    try:
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return CheckState.FAIL, f"Unexpected probe output for {module}."
    if payload.get("found"):
        return CheckState.PASS, f"{module} available at {payload.get('origin') or 'built-in'}"
    return CheckState.WARNING, f"{module} is not installed in {executable}"


def _probe_bionet_runtime(python_executable: str) -> tuple[CheckState, str]:
    executable = Path(python_executable).expanduser()
    if not executable.is_file():
        return CheckState.FAIL, f"Python executable not found: {executable}"
    result = probe_simulator_runtime(executable, timeout=20.0)
    if result.error:
        return CheckState.FAIL, f"BMTK BioNet runtime probe failed: {result.error}"
    if not result.has_bmtk:
        return CheckState.FAIL, f"BMTK is not installed in {executable}"
    if not result.bionet_ready:
        detail = result.bionet_error or "BioNet, NEURON, NumPy, or h5py could not be imported."
        return (
            CheckState.FAIL,
            f"BMTK {result.bmtk_version} is present, but BioNet is not runnable: {detail}",
        )
    return (
        CheckState.PASS,
        f"BMTK {result.bmtk_version} BioNet is ready with NEURON {result.neuron_version} "
        f"in {executable}",
    )


def _probe_neuron_runtime(python_executable: str) -> tuple[CheckState, str]:
    executable = external_runtime_launcher(python_executable)
    if not executable.is_file() or not os.access(executable, os.X_OK):
        return CheckState.FAIL, f"Python executable not found: {executable}"
    # Probe the selected scientific interpreter itself rather than accidentally
    # discovering a different user- or system-level NEURON installation.
    code = (
        "import json, neuron; "
        "print(json.dumps({'version': getattr(neuron, '__version__', 'unknown'), "
        "'origin': getattr(neuron, '__file__', 'unknown')}))"
    )
    environment = sanitized_external_environment(
        {
            "PATH": external_runtime_path(executable),
            "PYTHONNOUSERSITE": "1",
            "NEURON_MODULE_OPTIONS": "-nogui",
        }
    )
    try:
        completed = subprocess.run(
            [str(executable), "-c", code],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CheckState.FAIL, f"NEURON runtime probe failed: {exc}"
    if completed.returncode != 0:
        return CheckState.FAIL, (completed.stderr or completed.stdout).strip() or "NEURON import failed."
    try:
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return CheckState.FAIL, "Unexpected NEURON identity response."
    return CheckState.PASS, f"NEURON {payload.get('version')} at {payload.get('origin')}"
