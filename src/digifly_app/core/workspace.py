from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Iterable

from .models import CheckState, EngineProbe, PreflightCheck, PreflightReport
from .process_environment import sanitized_external_environment


class DigiflyWorkspace:
    """Resolved native-file layout for a Digifly Public workspace."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()

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
        desktop = self.root.parent
        return (
            desktop / "VND 1.14 UIUC" / "VND r1.14_1.9.4a57-arm64.app",
            desktop / "VND 1.14 UIUC" / "VND r1.14_1.9.4a57.app",
        )

    @property
    def bmtk_python_candidates(self) -> tuple[Path, ...]:
        desktop = self.root.parent
        return (
            desktop / "Digifly-Runtimes" / "envs" / "digifly-bmtk-dpointnet-py311" / "bin" / "python",
            Path("/opt/anaconda3/envs/digifly-bmtk-dpointnet-py311/bin/python"),
        )

    def base_preflight(self) -> PreflightReport:
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
        checks.append(
            _path_check(
                "handoff",
                "Escape-SIZ handoff",
                self.escape_siz_handoff,
                expected="file",
                blocking=True,
            )
        )
        checks.append(
            _path_check(
                "plotting_contract",
                "Escape-SIZ plotting contract",
                self.plotting_contract,
                expected="file",
                blocking=True,
            )
        )
        return PreflightReport(tuple(checks))

    def probe_engines(self, python_executable: str) -> tuple[EngineProbe, ...]:
        neuron_source = self.phase2_neuron / "digifly" / "phase2"
        neuron_runtime = _probe_neuron_runtime(python_executable, self.phase2_neuron)
        neuron = EngineProbe(
            key="neuron",
            name="NEURON",
            source_state=CheckState.PASS if neuron_source.is_dir() else CheckState.FAIL,
            runtime_state=neuron_runtime[0],
            summary=(
                "Native Phase 2 source and runtime are available."
                if neuron_source.is_dir() and neuron_runtime[0] == CheckState.PASS
                else "NEURON needs source and runtime configuration."
            ),
            details=(str(neuron_source), neuron_runtime[1]),
        )

        arbor_source = self.phase2_arbor / "digifly" / "phase2"
        arbor_runtime = _probe_python_module(python_executable, "arbor")
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
        bmtk_python = next((path for path in self.bmtk_python_candidates if path.is_file()), None)
        bmtk_runtime = (
            _probe_python_module(str(bmtk_python), "bmtk", clean_environment=True)
            if bmtk_python
            else (CheckState.WARNING, "No isolated BMTK/DPointNet interpreter was detected.")
        )
        bmtk = EngineProbe(
            key="bmtk",
            name="BMTK / SONATA",
            source_state=CheckState.PASS if bmtk_source.is_dir() else CheckState.WARNING,
            runtime_state=bmtk_runtime[0],
            summary=(
                "Interoperability source is present; the selected runtime is optional and isolated."
                if bmtk_source.is_dir()
                else "BMTK interoperability source was not found."
            ),
            details=(str(bmtk_source), str(bmtk_python or "runtime not configured"), bmtk_runtime[1]),
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


def _probe_neuron_runtime(python_executable: str, phase2_root: Path) -> tuple[CheckState, str]:
    import os

    executable = Path(python_executable).expanduser()
    if not executable.is_file():
        return CheckState.FAIL, f"Python executable not found: {executable}"
    paths = [str(phase2_root)]
    bundled = Path("/Applications/NEURON/lib/python")
    if bundled.is_dir():
        paths.append(str(bundled))
    code = (
        "import json, neuron; "
        "print(json.dumps({'version': getattr(neuron, '__version__', 'unknown'), "
        "'origin': getattr(neuron, '__file__', 'unknown')}))"
    )
    environment = sanitized_external_environment(
        {
            "PYTHONPATH": os.pathsep.join(paths),
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
