"""Consent-gated discovery and verification of external simulator interpreters."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Iterable

from .process_environment import (
    external_runtime_launcher,
    sanitized_external_environment,
)


@dataclass(frozen=True)
class SimulatorRuntime:
    python: Path
    python_version: str
    neuron_version: str = ""
    arbor_version: str = ""
    bmtk_version: str = ""
    neuron_origin: str = ""
    arbor_origin: str = ""
    bmtk_origin: str = ""
    bionet_ready: bool = False
    bionet_origin: str = ""
    bionet_error: str = ""
    error: str = ""

    @property
    def has_neuron(self) -> bool:
        return bool(self.neuron_version)

    @property
    def has_arbor(self) -> bool:
        return bool(self.arbor_version)

    @property
    def has_bmtk(self) -> bool:
        return bool(self.bmtk_version)


def _common_environment_roots() -> tuple[Path, ...]:
    home = Path.home()
    return (
        Path("/opt/anaconda3/envs"),
        Path("/opt/homebrew/Caskroom/miniconda/base/envs"),
        home / "anaconda3" / "envs",
        home / "miniconda3" / "envs",
        home / "mambaforge" / "envs",
        home / "micromamba" / "envs",
        home / ".conda" / "envs",
        home / ".virtualenvs",
        home / "Desktop" / "Digifly-Runtimes" / "envs",
    )


def runtime_candidates(
    *,
    explicit: Iterable[str | Path] = (),
    environment_roots: Iterable[str | Path] | None = None,
    include_path: bool = True,
    limit: int = 96,
) -> tuple[Path, ...]:
    """Enumerate one launcher per environment without dereferencing its symlink."""
    candidates: list[Path] = [Path(value).expanduser() for value in explicit if str(value).strip()]
    if include_path:
        for name in ("python3", "python"):
            located = shutil.which(name)
            if located:
                candidates.append(Path(located))
    candidates.extend(
        (
            Path("/opt/anaconda3/bin/python"),
            Path("/opt/homebrew/bin/python3"),
            Path("/usr/local/bin/python3"),
            Path("/usr/bin/python3"),
        )
    )
    roots = (
        tuple(Path(value).expanduser() for value in environment_roots)
        if environment_roots is not None
        else _common_environment_roots()
    )
    for root in roots:
        if not root.is_dir():
            continue
        try:
            environments = tuple(root.iterdir())
        except OSError:
            continue
        for environment in environments:
            if not environment.is_dir() or environment.is_symlink():
                continue
            candidates.extend(
                (
                    environment / "bin" / "python",
                    environment / "bin" / "python3",
                    environment / "Scripts" / "python.exe",
                )
            )
    launchers: list[Path] = []
    seen: set[Path] = set()
    seen_environment_dirs: set[Path] = set()
    for candidate in candidates:
        try:
            path = external_runtime_launcher(candidate)
        except OSError:
            continue
        if (
            path in seen
            or path.parent in seen_environment_dirs
            or not path.is_file()
            or not os.access(path, os.X_OK)
        ):
            continue
        if ".app/Contents/MacOS" in str(path):
            continue
        launchers.append(path)
        seen.add(path)
        seen_environment_dirs.add(path.parent)
        if len(launchers) >= limit:
            break
    return tuple(launchers)


def probe_simulator_runtime(python: str | Path, *, timeout: float = 8.0) -> SimulatorRuntime:
    executable = external_runtime_launcher(python)
    if not executable.is_file() or not os.access(executable, os.X_OK):
        return SimulatorRuntime(
            executable,
            "",
            error=f"Python runtime is missing or not executable: {executable}",
        )
    code = """
import importlib
import importlib.metadata as metadata
import importlib.util
import json
import platform
import pathlib
import sys

def package(module, distributions):
    spec = importlib.util.find_spec(module)
    if spec is None:
        return {'version': '', 'origin': ''}
    version = ''
    for distribution in distributions:
        try:
            version = metadata.version(distribution)
            break
        except metadata.PackageNotFoundError:
            pass
    return {'version': version or 'installed', 'origin': getattr(spec, 'origin', '') or ''}

def bionet_capability(bmtk):
    if not bmtk.get('version'):
        return {'ready': False, 'origin': '', 'error': ''}
    try:
        modules = {
            'bmtk': importlib.import_module('bmtk'),
            'bionet': importlib.import_module('bmtk.simulator.bionet'),
            'neuron': importlib.import_module('neuron'),
            'numpy': importlib.import_module('numpy'),
            'h5py': importlib.import_module('h5py'),
        }
        roots = {pathlib.Path(sys.prefix).resolve(), pathlib.Path(sys.base_prefix).resolve()}
        origins = {
            name: pathlib.Path(getattr(module, '__file__', '')).resolve()
            for name, module in modules.items()
        }
        outside = {
            name: str(origin)
            for name, origin in origins.items()
            if not any(origin.is_relative_to(root) for root in roots)
        }
        if outside:
            raise RuntimeError(
                f'dependencies outside selected interpreter roots '
                f'{sorted(map(str, roots))}: {outside}'
            )
    except Exception as exc:
        return {
            'ready': False,
            'origin': '',
            'error': f'{type(exc).__name__}: {exc}',
        }
    return {
        'ready': True,
        'origin': str(origins['bionet']),
        'error': '',
    }

bmtk = package('bmtk', ('bmtk',))

print(json.dumps({
    'python_version': platform.python_version(),
    'neuron': package('neuron', ('NEURON', 'neuron')),
    'arbor': package('arbor', ('arbor',)),
    'bmtk': bmtk,
    'bionet': bionet_capability(bmtk),
}))
"""
    environment = sanitized_external_environment(
        {
            "NEURON_MODULE_OPTIONS": "-nogui",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
        }
    )
    environment.pop("DISPLAY", None)
    try:
        completed = subprocess.run(
            [str(executable), "-c", code],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return SimulatorRuntime(executable, "", error=str(exc))
    if completed.returncode != 0:
        return SimulatorRuntime(
            executable,
            "",
            error=(completed.stderr or completed.stdout).strip() or "probe failed",
        )
    try:
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
        neuron = dict(payload.get("neuron") or {})
        arbor = dict(payload.get("arbor") or {})
        bmtk = dict(payload.get("bmtk") or {})
        bionet = dict(payload.get("bionet") or {})
        return SimulatorRuntime(
            python=executable,
            python_version=str(payload.get("python_version") or ""),
            neuron_version=str(neuron.get("version") or ""),
            arbor_version=str(arbor.get("version") or ""),
            bmtk_version=str(bmtk.get("version") or ""),
            neuron_origin=str(neuron.get("origin") or ""),
            arbor_origin=str(arbor.get("origin") or ""),
            bmtk_origin=str(bmtk.get("origin") or ""),
            bionet_ready=bool(bionet.get("ready", False)),
            bionet_origin=str(bionet.get("origin") or ""),
            bionet_error=str(bionet.get("error") or ""),
        )
    except (ValueError, IndexError, TypeError) as exc:
        return SimulatorRuntime(executable, "", error=f"unexpected probe output: {exc}")


def discover_simulator_runtimes(
    *,
    explicit: Iterable[str | Path] = (),
    environment_roots: Iterable[str | Path] | None = None,
    include_path: bool = True,
) -> tuple[SimulatorRuntime, ...]:
    results = (
        probe_simulator_runtime(path)
        for path in runtime_candidates(
            explicit=explicit,
            environment_roots=environment_roots,
            include_path=include_path,
        )
    )
    return tuple(
        result
        for result in results
        if result.has_neuron or result.has_arbor or result.has_bmtk
    )
