#!/usr/bin/env python3
"""Build the app-owned NEURON gap mechanisms in an external cache directory."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = REPO_ROOT / "mechanisms" / "neuron_gap_junctions"
SOURCE_MANIFEST = SOURCE_DIR / "source_manifest.json"
OUTPUT_MANIFEST_NAME = "mechanism_manifest.json"
BUILD_REPORT_NAME = "build_report.json"
EXPECTED_SOURCES = {
    "Gap": (
        "Gap.mod",
        "e3ab9d0a37811314d3baa8461050e6d65fbb9b8e163ff6618a6d9f2f2549c1f2",
    ),
    "RectGap": (
        "RectGap.mod",
        "d9fa308ff0433ad0eb424017fff4384d5c06915ca9a2cbe25485ad176db3a8d6",
    ),
    "HeteroRectGap": (
        "HeteroRectGap.mod",
        "22b505af799076bb8079f204d222adfa72432f8609bd89fd050f7ffb325c9c2f",
    ),
}

_EXTERNAL_ENV_REMOVE = {
    "PYTHONHOME",
    "PYTHONPATH",
    "PYTHONEXECUTABLE",
    "PYTHONSTARTUP",
    "PYTHONINSPECT",
    "PYTHONUSERBASE",
    "PYTHONPLATLIBDIR",
    "__PYVENV_LAUNCHER__",
    "VIRTUAL_ENV",
    "_PYTHON_SYSCONFIGDATA_NAME",
    "LD_LIBRARY_PATH",
    "LD_PRELOAD",
    "QT_PLUGIN_PATH",
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    "QML_IMPORT_PATH",
    "QML2_IMPORT_PATH",
    "NEURONHOME",
    "NRNHOME",
    "CORENRNHOME",
    "NRN_PYTHONEXE",
    "CORENRN_PYTHONEXE",
    "NRNBIN",
    "NRNIVMODL",
    "NMODLHOME",
    "NMODL_PYLIB",
}

_RUNTIME_PROBE = r"""
import json
from pathlib import Path
import sys
import neuron

package_root = Path(neuron.__file__).resolve().parent
runtime_data_root = (package_root / ".data").resolve()
version = str(getattr(neuron, "__version__", "")).strip()
if not version:
    from neuron import h
    version = str(h.nrnversion(0)).strip()
candidates = [
    runtime_data_root / "bin" / "nrnivmodl",
    Path(sys.executable).absolute().parent / "nrnivmodl",
]
print(json.dumps({
    "python_version": sys.version.split()[0],
    "neuron_version": version,
    "neuron_file": str(Path(neuron.__file__).resolve()),
    "runtime_data_root": str(runtime_data_root),
    "nrnivmodl_candidates": [str(path) for path in candidates],
}, sort_keys=True))
"""

_LOAD_PROBE = r"""
import json
from pathlib import Path
import sys
import neuron
from neuron import h

library = str(Path(sys.argv[1]).resolve())
h.nrn_load_dll(library)
required = ("Gap", "RectGap", "HeteroRectGap")
missing = [name for name in required if not hasattr(h, name)]
if missing:
    raise RuntimeError("compiled library is missing mechanisms: " + ", ".join(missing))
print(json.dumps({
    "library": library,
    "mechanisms": list(required),
    "neuron_version": str(getattr(neuron, "__version__", "")),
}, sort_keys=True))
"""


class BuildError(RuntimeError):
    """A classified build failure suitable for the machine-readable report."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compile the packaged Digifly NEURON gap mechanisms in an external "
            "cache and emit a hash-verifiable mechanism_manifest.json."
        )
    )
    parser.add_argument(
        "--python",
        required=True,
        help="External Python interpreter containing the target NEURON runtime.",
    )
    parser.add_argument(
        "--nrnivmodl",
        help=(
            "nrnivmodl executable to use. By default the helper selects the "
            "compiler installed beside the target NEURON Python package."
        ),
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="External cache directory to create or validate and reuse.",
    )
    return parser


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _sanitized_environment(
    overrides: Mapping[str, str] | None = None,
    *,
    inherited: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return a standalone external-runtime environment without app contamination."""
    environment = dict(os.environ if inherited is None else inherited)
    for key in tuple(environment):
        if (
            key in _EXTERNAL_ENV_REMOVE
            or key.startswith("DYLD_")
            or key.startswith("CONDA_")
        ):
            environment.pop(key, None)
    if path_value := environment.get("PATH"):
        environment["PATH"] = os.pathsep.join(
            entry
            for entry in path_value.split(os.pathsep)
            if entry
            and ".app/Contents/MacOS" not in entry
            and Path(entry) != Path("/Applications/NEURON/bin")
        )
    environment.update(
        {
            "LANG": "C",
            "LC_ALL": "C",
            "NEURON_MODULE_OPTIONS": "-nogui",
            "PYTHONNOUSERSITE": "1",
        }
    )
    if overrides:
        environment.update({str(key): str(value) for key, value in overrides.items()})
    return environment


def _resolve_executable(value: str, *, label: str) -> Path:
    # The launch path is part of a virtualenv/Conda interpreter's identity.
    # Keep the selected symlink intact rather than collapsing it to base Python.
    path = Path(value).expanduser().absolute()
    if not path.is_file():
        raise BuildError("missing_executable", f"{label} does not exist: {path}")
    if not os.access(path, os.X_OK):
        raise BuildError("missing_executable", f"{label} is not executable: {path}")
    return path


def _parse_json_line(output: str, *, label: str) -> dict[str, Any]:
    for line in reversed(output.splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise BuildError("runtime_probe_failed", f"{label} returned no JSON object: {output!r}")


def _probe_runtime(python: Path) -> tuple[dict[str, Any], dict[str, str]]:
    environment = _sanitized_environment(
        {"PATH": os.pathsep.join((str(python.parent), "/usr/bin", "/bin"))}
    )
    completed = subprocess.run(
        [str(python), "-I", "-B", "-c", _RUNTIME_PROBE],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    if completed.returncode:
        output = (completed.stdout + completed.stderr).strip()
        raise BuildError(
            "runtime_probe_failed",
            f"{python} cannot import its isolated NEURON runtime: {output}",
        )
    payload = _parse_json_line(completed.stdout, label="NEURON runtime probe")
    required = {
        "python_version",
        "neuron_version",
        "neuron_file",
        "runtime_data_root",
        "nrnivmodl_candidates",
    }
    missing = sorted(required.difference(payload))
    if missing:
        raise BuildError(
            "runtime_probe_failed",
            f"NEURON runtime probe omitted fields: {', '.join(missing)}",
        )
    if not str(payload["neuron_version"]).strip():
        raise BuildError("runtime_probe_failed", "NEURON reported an empty version.")
    return payload, environment


def _resolve_nrnivmodl(
    requested: str | None,
    *,
    runtime: Mapping[str, Any],
    probe_environment: Mapping[str, str],
) -> Path:
    if requested:
        return _resolve_executable(requested, label="nrnivmodl")
    for value in runtime["nrnivmodl_candidates"]:
        candidate = Path(str(value)).expanduser().resolve()
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    discovered = shutil.which("nrnivmodl", path=probe_environment.get("PATH"))
    if discovered:
        return _resolve_executable(discovered, label="nrnivmodl")
    raise BuildError(
        "missing_executable",
        "No nrnivmodl executable was found for the selected NEURON Python; "
        "pass --nrnivmodl explicitly.",
    )


def _verified_sources() -> dict[str, dict[str, str]]:
    try:
        frozen = json.loads(SOURCE_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BuildError("source_manifest_invalid", str(exc)) from exc
    if frozen.get("schema_version") != 1 or frozen.get("engine") != "neuron":
        raise BuildError(
            "source_manifest_invalid",
            "Packaged source_manifest.json has an unsupported schema or engine.",
        )
    records = frozen.get("mechanisms")
    if not isinstance(records, dict) or set(records) != set(EXPECTED_SOURCES):
        raise BuildError(
            "source_manifest_invalid",
            "Packaged source_manifest.json does not name the three frozen mechanisms.",
        )
    verified: dict[str, dict[str, str]] = {}
    for mechanism, (filename, expected_hash) in EXPECTED_SOURCES.items():
        record = records.get(mechanism)
        if not isinstance(record, dict):
            raise BuildError("source_manifest_invalid", f"Invalid record for {mechanism}.")
        if record.get("source_filename") != filename:
            raise BuildError(
                "source_manifest_invalid",
                f"Frozen filename mismatch for {mechanism}.",
            )
        if record.get("source_sha256") != expected_hash:
            raise BuildError(
                "source_manifest_invalid",
                f"Frozen hash mismatch for {mechanism}.",
            )
        source = SOURCE_DIR / filename
        if not source.is_file():
            raise BuildError("source_missing", f"Packaged source is missing: {source}")
        actual_hash = _sha256(source)
        if actual_hash != expected_hash:
            raise BuildError(
                "source_hash_mismatch",
                f"Packaged source hash mismatch for {filename}: {actual_hash}",
            )
        verified[mechanism] = {
            "source_filename": filename,
            "source_relpath": f"sources/{filename}",
            "source_sha256": expected_hash,
        }
    return verified


def _external_output_dir(value: str) -> Path:
    output = Path(value).expanduser().resolve()
    try:
        output.relative_to(REPO_ROOT)
    except ValueError:
        return output
    raise BuildError(
        "unsafe_output_dir",
        "NEURON mechanism build output must stay outside the Digifly Workstation repository.",
    )


def _safe_cached_path(output: Path, relpath: Any, *, label: str) -> Path:
    relative = Path(str(relpath))
    if relative.is_absolute() or ".." in relative.parts:
        raise BuildError("cache_invalid", f"Unsafe {label} in cache manifest: {relpath!r}")
    resolved = (output / relative).resolve()
    try:
        resolved.relative_to(output.resolve())
    except ValueError as exc:
        raise BuildError("cache_invalid", f"{label} escapes the cache: {relpath!r}") from exc
    if not resolved.is_file():
        raise BuildError("cache_invalid", f"Cached {label} is missing: {resolved}")
    return resolved


def _validate_cached_manifest(
    output: Path,
    *,
    neuron_version: str,
    expected_mechanisms: Mapping[str, Mapping[str, str]],
) -> tuple[dict[str, Any], Path]:
    manifest_path = output / OUTPUT_MANIFEST_NAME
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BuildError("cache_invalid", f"Cannot read {manifest_path}: {exc}") from exc
    top_level = {
        "schema_version",
        "engine",
        "neuron_version",
        "library_relpath",
        "library_sha256",
        "mechanisms",
    }
    if not isinstance(payload, dict) or set(payload) != top_level:
        raise BuildError("cache_invalid", "Cache manifest has an unexpected field set.")
    if payload["schema_version"] != 1 or payload["engine"] != "neuron":
        raise BuildError("cache_invalid", "Cache manifest has an unsupported schema or engine.")
    if payload["neuron_version"] != neuron_version:
        raise BuildError(
            "cache_runtime_mismatch",
            f"Cache uses NEURON {payload['neuron_version']}, selected runtime is {neuron_version}.",
        )
    records = payload.get("mechanisms")
    if not isinstance(records, dict) or records != expected_mechanisms:
        raise BuildError("cache_invalid", "Cache mechanism source contract does not match the app.")
    for record in records.values():
        source = _safe_cached_path(output, record["source_relpath"], label="source")
        if source.name != record["source_filename"]:
            raise BuildError("cache_invalid", f"Cached source filename mismatch: {source}")
        if _sha256(source) != record["source_sha256"]:
            raise BuildError("cache_invalid", f"Cached source hash mismatch: {source}")
    library = _safe_cached_path(output, payload["library_relpath"], label="library")
    if _sha256(library) != payload["library_sha256"]:
        raise BuildError("cache_invalid", f"Cached library hash mismatch: {library}")
    return payload, library


def _stage_sources(
    output: Path, mechanisms: Mapping[str, Mapping[str, str]]
) -> Path:
    sources = output / "sources"
    sources.mkdir()
    for record in mechanisms.values():
        source = SOURCE_DIR / record["source_filename"]
        destination = output / record["source_relpath"]
        shutil.copyfile(source, destination)
        if _sha256(destination) != record["source_sha256"]:
            raise BuildError(
                "source_hash_mismatch",
                f"Staged source hash mismatch: {destination}",
            )
    return sources


def _build_environment(
    *,
    python: Path,
    nrnivmodl: Path,
    runtime: Mapping[str, Any],
    output: Path,
) -> dict[str, str]:
    inherited_path = os.environ.get("PATH", "")
    path_entries = [str(nrnivmodl.parent), str(python.parent)]
    for entry in inherited_path.split(os.pathsep):
        if (
            entry
            and entry not in path_entries
            and ".app/Contents/MacOS" not in entry
            and (Path(entry) != Path("/Applications/NEURON/bin") or nrnivmodl.parent == Path(entry))
        ):
            path_entries.append(entry)
    runtime_data_root = Path(str(runtime["runtime_data_root"])).resolve()
    temporary = output / ".tmp"
    temporary.mkdir(exist_ok=True)
    return _sanitized_environment(
        {
            "PATH": os.pathsep.join(path_entries),
            "NRNIVMODL": str(nrnivmodl),
            "NRNHOME": str(runtime_data_root),
            "CORENRNHOME": str(runtime_data_root),
            "NEURONHOME": str(runtime_data_root / "share" / "nrn"),
            "NRN_PYTHONEXE": str(python),
            "CORENRN_PYTHONEXE": str(python),
            "TMPDIR": str(temporary),
        }
    )


def _find_library(output: Path) -> Path:
    names = {"libnrnmech.dylib", "libnrnmech.so", "nrnmech.dll"}
    candidates = [
        path
        for path in output.rglob("*")
        if path.name in names and path.is_file()
    ]
    if not candidates:
        raise BuildError(
            "library_missing",
            "nrnivmodl completed but produced no NEURON mechanism library.",
        )
    by_target: dict[Path, list[Path]] = {}
    for candidate in candidates:
        by_target.setdefault(candidate.resolve(), []).append(candidate)
    if len(by_target) != 1:
        rendered = ", ".join(str(path.relative_to(output)) for path in candidates)
        raise BuildError(
            "library_ambiguous",
            f"nrnivmodl produced multiple distinct mechanism libraries: {rendered}",
        )
    aliases = next(iter(by_target.values()))
    return min(
        aliases,
        key=lambda path: (".libs" in path.parts, len(path.relative_to(output).parts)),
    )


def _validate_library_load(
    *,
    python: Path,
    library: Path,
    environment: Mapping[str, str],
) -> dict[str, Any]:
    completed = subprocess.run(
        [str(python), "-I", "-B", "-c", _LOAD_PROBE, str(library)],
        check=False,
        capture_output=True,
        text=True,
        env=dict(environment),
    )
    if completed.returncode:
        output = (completed.stdout + completed.stderr).strip()
        raise BuildError(
            "library_load_failed",
            "The compiled mechanism library is incompatible with the selected "
            f"NEURON Python: {output}",
        )
    return _parse_json_line(completed.stdout, label="NEURON library load probe")


def _new_manifest(
    *,
    output: Path,
    library: Path,
    neuron_version: str,
    mechanisms: Mapping[str, Mapping[str, str]],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "engine": "neuron",
        "neuron_version": neuron_version,
        "library_relpath": library.relative_to(output).as_posix(),
        "library_sha256": _sha256(library),
        "mechanisms": {name: dict(record) for name, record in mechanisms.items()},
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report: dict[str, Any] = {
        "schema_version": 1,
        "engine": "neuron",
        "source_dir": str(SOURCE_DIR),
        "source_tree_policy": "app-owned packaged sources; Digifly Public remains read-only",
    }
    output: Path | None = None
    report_writable = False
    try:
        mechanisms = _verified_sources()
        python = _resolve_executable(args.python, label="NEURON Python")
        runtime, probe_environment = _probe_runtime(python)
        nrnivmodl = _resolve_nrnivmodl(
            args.nrnivmodl,
            runtime=runtime,
            probe_environment=probe_environment,
        )
        output = _external_output_dir(args.output_dir)
        report.update(
            {
                "output_dir": str(output),
                "python": str(python),
                "python_version": runtime["python_version"],
                "neuron_file": runtime["neuron_file"],
                "neuron_version": runtime["neuron_version"],
                "nrnivmodl": str(nrnivmodl),
                "nrnivmodl_sha256": _sha256(nrnivmodl),
                "source_manifest_sha256": _sha256(SOURCE_MANIFEST),
            }
        )

        if output.exists() and not output.is_dir():
            raise BuildError("output_not_directory", f"Output path is not a directory: {output}")
        if (output / OUTPUT_MANIFEST_NAME).is_file():
            cached_manifest, library = _validate_cached_manifest(
                output,
                neuron_version=str(runtime["neuron_version"]),
                expected_mechanisms=mechanisms,
            )
            environment = _build_environment(
                python=python,
                nrnivmodl=nrnivmodl,
                runtime=runtime,
                output=output,
            )
            load_probe = _validate_library_load(
                python=python,
                library=library,
                environment=environment,
            )
            report_writable = True
            report.update(
                {
                    "status": "cached",
                    "library": str(library),
                    "library_sha256": cached_manifest["library_sha256"],
                    "load_probe": load_probe,
                }
            )
        else:
            if output.exists() and any(output.iterdir()):
                raise BuildError(
                    "output_not_empty",
                    f"Output directory is non-empty and has no valid cache manifest: {output}",
                )
            output.mkdir(parents=True, exist_ok=True)
            report_writable = True
            sources = _stage_sources(output, mechanisms)
            environment = _build_environment(
                python=python,
                nrnivmodl=nrnivmodl,
                runtime=runtime,
                output=output,
            )
            command = [str(nrnivmodl), sources.name]
            completed = subprocess.run(
                command,
                cwd=output,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )
            build_output = completed.stdout + completed.stderr
            (output / "build.log").write_text(build_output, encoding="utf-8")
            report.update({"command": command, "returncode": completed.returncode})
            if completed.returncode:
                raise BuildError(
                    "mechanism_compile_failed",
                    f"nrnivmodl exited with status {completed.returncode}; see build.log.",
                )
            library = _find_library(output)
            load_probe = _validate_library_load(
                python=python,
                library=library,
                environment=environment,
            )
            manifest = _new_manifest(
                output=output,
                library=library,
                neuron_version=str(runtime["neuron_version"]),
                mechanisms=mechanisms,
            )
            _write_json(output / OUTPUT_MANIFEST_NAME, manifest)
            report.update(
                {
                    "status": "complete",
                    "library": str(library),
                    "library_sha256": manifest["library_sha256"],
                    "load_probe": load_probe,
                }
            )
    except BuildError as exc:
        report["status"] = "failed"
        report["failure"] = {"code": exc.code, "detail": str(exc)}
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
        report["status"] = "failed"
        report["failure"] = {"code": "unexpected_failure", "detail": str(exc)}

    if output is not None and report_writable:
        _write_json(output / BUILD_REPORT_NAME, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report.get("status") in {"complete", "cached"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
