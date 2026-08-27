"""Consent-gated discovery and verification of external simulator interpreters."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Iterable

from .process_environment import sanitized_external_environment


@dataclass(frozen=True)
class SimulatorRuntime:
    python: Path
    python_version: str
    neuron_version: str = ""
    arbor_version: str = ""
    neuron_origin: str = ""
    arbor_origin: str = ""
    error: str = ""

    @property
    def has_neuron(self) -> bool:
        return bool(self.neuron_version)

    @property
    def has_arbor(self) -> bool:
        return bool(self.arbor_version)


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
    """Enumerate only PATH, fixed interpreter paths, and one-level environment roots."""
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
    resolved: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            path = candidate.resolve()
        except OSError:
            continue
        if path in seen or not path.is_file() or not os.access(path, os.X_OK):
            continue
        if ".app/Contents/MacOS" in str(path):
            continue
        resolved.append(path)
        seen.add(path)
        if len(resolved) >= limit:
            break
    return tuple(resolved)


def probe_simulator_runtime(python: str | Path, *, timeout: float = 8.0) -> SimulatorRuntime:
    executable = Path(python).expanduser().resolve()
    code = """
import importlib.metadata as metadata
import importlib.util
import json
import platform

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

print(json.dumps({
    'python_version': platform.python_version(),
    'neuron': package('neuron', ('NEURON', 'neuron')),
    'arbor': package('arbor', ('arbor',)),
}))
"""
    environment = sanitized_external_environment({"PYTHONNOUSERSITE": "1"})
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
        return SimulatorRuntime(
            executable,
            str(payload.get("python_version") or ""),
            str(neuron.get("version") or ""),
            str(arbor.get("version") or ""),
            str(neuron.get("origin") or ""),
            str(arbor.get("origin") or ""),
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
    return tuple(result for result in results if result.has_neuron or result.has_arbor)
