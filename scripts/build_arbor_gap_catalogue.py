#!/usr/bin/env python3
"""Build the app-owned Digifly Arbor gap-junction catalogue outside the repo."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = REPO_ROOT / "mechanisms" / "arbor_gap_junctions"
CATALOGUE_NAME = "digifly_gap"
DEFAULT_ARBOR_PYTHON = Path("/opt/anaconda3/bin/python")
SUPPORTED_ARBOR_VERSION = "0.12.2"
EXPECTED_SOURCE_MANIFEST_SHA256 = "0041ca45f31d112b8c4bc0677d2d57bf6003592cc88d2825ac883ba819308e76"
BUILD_TIMEOUT_SECONDS = 300
BUILD_METHOD = "modcc_generated_cpu_C_ABI_plugin_no_static_solver"
EXPECTED_SOURCES = {
    "gap": "2b88d95a628abd21be48d550a7f387a0cefe1b65a4c09e43d19d06b11701aec2",
    "rect_gap": "e2164233d1230be646e6e66a5307290083d2a3bc4fb23ec9062213bc0cd9558d",
    "hetero_rect_gap": "b4ded50b979589f218ad9a7366b994be3ed33bb5473eff5f908b1f5749a7ba3d",
}
EXPECTED_PARAMETERS = {
    "gap": ["g"],
    "rect_gap": ["gmax"],
    "hetero_rect_gap": sorted([
        "gmax_open", "gmax_closed", "orientation", "vhalf", "vslope",
        "empirical_residual_frac", "tau_open_ms", "tau_close_ms",
    ]),
}
BITCODE_MISMATCH = re.compile(
    r"Invalid bitcode version \(Producer: '([^']+)' Reader: '([^']+)'\)"
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build the app-owned Arbor gap-junction catalogue in an external build directory. "
            "No files in Digifly Public are modified."
        )
    )
    parser.add_argument(
        "--python",
        default=str(DEFAULT_ARBOR_PYTHON if DEFAULT_ARBOR_PYTHON.is_file() else Path(sys.executable)),
        help="Python interpreter containing Arbor (default: /opt/anaconda3/bin/python when present).",
    )
    parser.add_argument(
        "--output-dir",
        help="External build directory. If omitted, a unique system-temporary directory is created.",
    )
    parser.add_argument(
        "--keep-generated",
        action="store_true",
        help="Retain generated C++ under OUTPUT_DIR/generated (now always retained for provenance).",
    )
    return parser


def _resolve_build_tool(python: Path) -> tuple[str, str, Path]:
    probe = subprocess.run(
        [
            str(python),
            "-I",
            "-B",
            "-c",
            (
                "import arbor, pathlib; "
                "root=pathlib.Path(arbor.__file__).resolve().parent; "
                "print(arbor.__version__); print(root); print(root/'bin'/'modcc')"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if probe.returncode:
        raise RuntimeError(
            f"{python} cannot import Arbor:\n{probe.stdout}{probe.stderr}".strip()
        )
    lines = [line.strip() for line in probe.stdout.splitlines() if line.strip()]
    if len(lines) != 3:
        raise RuntimeError(f"Unexpected Arbor runtime probe output: {probe.stdout!r}")
    version, arbor_root, build_tool_text = lines
    build_tool = Path(build_tool_text)
    if not build_tool.is_file():
        raise RuntimeError(f"Arbor mechanism compiler is missing: {build_tool}")
    return version, arbor_root, build_tool


def _classify_failure(output: str) -> dict[str, Any]:
    mismatch = BITCODE_MISMATCH.search(output)
    if mismatch:
        return {
            "code": "apple_llvm_bitcode_version_mismatch",
            "wheel_producer": mismatch.group(1),
            "local_reader": mismatch.group(2),
            "remedy": (
                "Use an Arbor build and Apple compiler/linker from the same LLVM generation; "
                "do not alter the mechanism equations to bypass this toolchain gate."
            ),
        }
    return {"code": "catalogue_build_failed"}


def _external_output_dir(requested: str | None) -> Path:
    if requested:
        output = Path(requested).expanduser().resolve()
        try:
            output.relative_to(REPO_ROOT)
        except ValueError:
            pass
        else:
            raise RuntimeError("Catalogue build output must stay outside the Digifly Workstation repository.")
        output.mkdir(parents=True, exist_ok=True)
        if any(output.iterdir()):
            raise RuntimeError(f"Catalogue build directory must be empty: {output}")
        return output
    return Path(tempfile.mkdtemp(prefix="digifly_gap_catalogue_"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _python_launcher(value: str) -> Path:
    """Validate Python while preserving a virtual-environment launcher symlink."""

    python = Path(value).expanduser().absolute()
    if not python.is_file() or not os.access(python, os.X_OK):
        raise RuntimeError(
            f"Python interpreter does not exist or is not executable: {python}"
        )
    return python


def _verified_sources() -> dict[str, str]:
    """Freeze only the three equation-locked app ports, never arbitrary MOD input."""
    if _sha256(SOURCE_DIR / "source_manifest.json") != EXPECTED_SOURCE_MANIFEST_SHA256:
        raise RuntimeError("Gap source manifest hash mismatch")
    manifest = json.loads((SOURCE_DIR / "source_manifest.json").read_text())
    if manifest.get("catalogue_name") != CATALOGUE_NAME:
        raise RuntimeError("Gap source manifest catalogue identity changed")
    records = manifest.get("mechanisms") or {}
    if set(records) != set(EXPECTED_SOURCES):
        raise RuntimeError("Gap source manifest mechanism set changed")
    if {p.name for p in SOURCE_DIR.glob("*.mod")} != {f"{n}.mod" for n in EXPECTED_SOURCES}:
        raise RuntimeError("Gap source directory mechanism set changed")
    result = {}
    for name, digest in EXPECTED_SOURCES.items():
        record = records[name]
        if record.get("arbor_source") != f"{name}.mod" or record.get("arbor_source_sha256") != digest:
            raise RuntimeError(f"Gap source manifest identity changed: {name}")
        source = SOURCE_DIR / f"{name}.mod"
        if _sha256(source) != digest:
            raise RuntimeError(f"Gap source hash mismatch: {name}")
        result[str(source)] = digest
    result[str(SOURCE_DIR / "source_manifest.json")] = _sha256(SOURCE_DIR / "source_manifest.json")
    return result


def _assert_hashes(records: dict[str, str]) -> None:
    for path, digest in records.items():
        if not Path(path).is_file() or _sha256(Path(path)) != digest:
            raise RuntimeError(f"Build input changed during catalogue construction: {path}")


def _run_stage(command: list[str], *, output: Path, report: dict[str, Any],
               stage: str, deadline: float, max_timeout: int = BUILD_TIMEOUT_SECONDS) -> subprocess.CompletedProcess:
    remaining = min(max_timeout, deadline - time.monotonic())
    if remaining <= 0:
        raise RuntimeError("Catalogue build exceeded its 300-second total budget")
    record: dict[str, Any] = {"stage": stage, "command": command, "timeout_seconds": remaining}
    report.setdefault("commands", []).append(record)
    try:
        completed = subprocess.run(command, cwd=output, check=False,
                                   capture_output=True, text=True, timeout=remaining)
    except subprocess.TimeoutExpired as exc:
        record["timeout"] = True
        with (output / "build.log").open("a", encoding="utf-8") as log:
            log.write(f"\n[{stage}] timeout\n")
            for data in (exc.stdout, exc.stderr):
                if data:
                    log.write(data.decode(errors="replace") if isinstance(data, bytes) else data)
        raise RuntimeError(f"Catalogue {stage} timed out") from exc
    record["returncode"] = completed.returncode
    with (output / "build.log").open("a", encoding="utf-8") as log:
        log.write(f"\n[{stage}] {json.dumps(command)}\n{completed.stdout}{completed.stderr}")
    if completed.returncode:
        report["returncode"] = completed.returncode
        report["failure"] = {"stage": stage, **_classify_failure(completed.stdout + completed.stderr)}
        raise RuntimeError(f"Catalogue {stage} failed; see build.log")
    return completed


def _abi_load_command(python: Path, library: Path, version: str, root: Path) -> list[str]:
    # Actual runtime dlopen/ABI and schema checks; no simulation or solver linkage.
    code = """
import hashlib, importlib, json, pathlib, sys
import arbor
library, digest, version, root, expected = sys.argv[1:]
assert arbor.__version__ == version, 'Arbor version changed during build'
assert str(pathlib.Path(arbor.__file__).resolve().parent) == root, 'Arbor runtime path changed'
assert hashlib.sha256(pathlib.Path(library).read_bytes()).hexdigest() == digest, 'Catalogue hash changed'
catalogue = arbor.load_catalogue(library)
expected = json.loads(expected)
assert set(catalogue.keys()) == set(expected), 'Catalogue mechanism names differ'
actual = {}
for name, parameters in expected.items():
    info = catalogue[name]
    assert 'gap junction' in str(info.kind).lower(), 'Not a gap junction: ' + name
    assert sorted(info.parameters) == parameters, 'Catalogue parameters differ: ' + name
    actual[name] = {'kind': str(info.kind), 'parameters': sorted(info.parameters)}
properties = arbor.neuron_cable_properties()
properties.catalogue.extend(catalogue, 'digifly_')
assert all('digifly_' + name in properties.catalogue for name in expected), 'Catalogue extension failed'
module = pathlib.Path(importlib.import_module('arbor._arbor').__file__).resolve()
print(json.dumps({'arbor_version': arbor.__version__, 'arbor_root': root,
                 'runtime_module': str(module), 'runtime_module_sha256': hashlib.sha256(module.read_bytes()).hexdigest(),
                 'mechanisms': actual, 'extension_prefix': 'digifly_', 'simulation_started': False}))
"""
    return [str(python), "-I", "-B", "-c", code, str(library), _sha256(library),
            version, str(root), json.dumps(EXPECTED_PARAMETERS, sort_keys=True)]


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report: dict[str, Any] = {
        "catalogue": CATALOGUE_NAME,
        "source_dir": str(SOURCE_DIR),
        "source_tree_policy": "app-owned Arbor ports; Digifly Public remains read-only",
    }
    output: Path | None = None
    deadline = time.monotonic() + BUILD_TIMEOUT_SECONDS
    try:
        output = _external_output_dir(args.output_dir)
        python = _python_launcher(args.python)
        version, arbor_root, modcc = _resolve_build_tool(python)
        if version != SUPPORTED_ARBOR_VERSION:
            raise RuntimeError(f"Gap ports are tested against Arbor {SUPPORTED_ARBOR_VERSION}, not {version}")
        root = Path(arbor_root)
        compiler_text = shutil.which("c++")
        if not modcc.is_file() or compiler_text is None:
            raise RuntimeError("Arbor modcc or local C++ compiler is missing")
        compiler = Path(compiler_text)
        include = root / "include"
        if not (include / "arbor" / "mechanism_abi.h").is_file():
            raise RuntimeError("Selected Arbor runtime is missing mechanism ABI headers")
        sources = _verified_sources()
        inputs = {**sources, str(modcc): _sha256(modcc), str(compiler): _sha256(compiler),
                  str(python): _sha256(python),
                  **{str(p): _sha256(p) for p in include.rglob("*") if p.is_file()},
                  **{str(p): _sha256(p) for p in root.glob("_arbor*.so") if p.is_file()},
                  str(root / "__init__.py"): _sha256(root / "__init__.py"),
                  str(root / "VERSION"): _sha256(root / "VERSION")}
        report.update(
            {
                "arbor_version": version,
                "arbor_root": arbor_root,
                "build_tool": str(modcc),
                "output_dir": str(output),
                "build_method": BUILD_METHOD,
                "builder_sha256": _sha256(Path(__file__)),
                "inputs": inputs,
                "source_manifest_sha256": sources[str(SOURCE_DIR / "source_manifest.json")],
                "compiler_path": str(compiler),
                "compiler_sha256": inputs[str(compiler)],
                "modcc_path": str(modcc),
                "modcc_sha256": inputs[str(modcc)],
                "total_timeout_seconds": BUILD_TIMEOUT_SECONDS,
                "simulation_started": False,
            }
        )
        compiler_probe = _run_stage([str(compiler), "--version"], output=output, report=report,
                                    stage="compiler_identity", deadline=deadline, max_timeout=30)
        report["compiler_version"] = compiler_probe.stdout.strip()
        staged, generated = output / "sources", output / "generated"
        staged.mkdir()
        generated.mkdir()
        for name, digest in EXPECTED_SOURCES.items():
            data = (SOURCE_DIR / f"{name}.mod").read_bytes()
            if hashlib.sha256(data).hexdigest() != digest:
                raise RuntimeError(f"Gap source changed while staging: {name}")
            (staged / f"{name}.mod").write_bytes(data)
        command = [
            str(modcc), "-t", "cpu", "-N", "arb", "-c", CATALOGUE_NAME,
            "-o", str(generated), *map(str, sorted(staged.glob("*.mod"))),
        ]
        report["command"] = command
        _run_stage(command, output=output, report=report, stage="source_generation", deadline=deadline)
        generated_files = sorted(p for p in generated.rglob("*") if p.is_file())
        report["generated"] = {str(p.relative_to(output)): _sha256(p) for p in generated_files}
        cpp = sorted(generated.glob("*.cpp"))
        if {p.name for p in cpp} != {f"{CATALOGUE_NAME}_catalogue.cpp", *[f"{n}_cpu.cpp" for n in EXPECTED_SOURCES]}:
            raise RuntimeError("Unexpected generated CPU catalogue source set")
        catalogue = output / f"{CATALOGUE_NAME}-catalogue.so"
        # modcc's standalone CPU mechanism ABI uses headers, not Arbor's solver.
        # Linking wheel libarbor.a adds a second solver and incompatible LTO.
        link_command = [str(compiler), "-std=c++17", "-O2", "-shared", "-fPIC", "-DSTANDALONE=1",
                        "-I", str(include), *map(str, cpp), "-o", str(catalogue)]
        report["link_command"] = link_command
        _run_stage(link_command, output=output, report=report, stage="cpu_plugin_link", deadline=deadline)
        if not catalogue.is_file():
            raise RuntimeError("Compiler did not produce a catalogue library")
        _assert_hashes(inputs)
        _assert_hashes({str(output / p): digest for p, digest in report["generated"].items()})
        loaded = _run_stage(_abi_load_command(python, catalogue, version, root), output=output,
                            report=report, stage="runtime_abi_load", deadline=deadline, max_timeout=30)
        report["abi_validation"] = json.loads(loaded.stdout)
        loaded_runtime = report["abi_validation"]
        if (loaded_runtime.get("arbor_version") != version or loaded_runtime.get("arbor_root") != str(root)
                or inputs.get(loaded_runtime.get("runtime_module")) != loaded_runtime.get("runtime_module_sha256")
                or not loaded_runtime.get("runtime_module_sha256")):
            raise RuntimeError("ABI load used a different Arbor runtime binary")
        _assert_hashes(inputs)
        report["returncode"] = 0
        report["status"] = "complete"
        report["catalogue_artifacts"] = [str(catalogue)]
        report["catalogue_sha256"] = _sha256(catalogue)
        (output / "catalogue_manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "catalogue": CATALOGUE_NAME,
                        "arbor_version": version,
                        "catalogue_path": str(catalogue),
                        "catalogue_sha256": _sha256(catalogue),
                        "source_manifest_sha256": _sha256(SOURCE_DIR / "source_manifest.json"),
                        "build_method": BUILD_METHOD,
                        "provenance": report,
                        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
    except Exception as exc:
        report["status"] = "failed"
        report.setdefault("failure", {"code": "setup_failed"})["detail"] = str(exc)

    if output is not None:
        (output / "build_report.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
