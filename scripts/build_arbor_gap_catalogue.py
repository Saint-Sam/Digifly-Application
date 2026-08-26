#!/usr/bin/env python3
"""Build the app-owned Digifly Arbor gap-junction catalogue outside the repo."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = REPO_ROOT / "mechanisms" / "arbor_gap_junctions"
CATALOGUE_NAME = "digifly_gap"
DEFAULT_ARBOR_PYTHON = Path("/opt/anaconda3/bin/python")
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
        help="Keep generated C++ and CMake files under OUTPUT_DIR/generated.",
    )
    return parser


def _resolve_build_tool(python: Path) -> tuple[str, str, Path]:
    probe = subprocess.run(
        [
            str(python),
            "-B",
            "-c",
            (
                "import arbor, pathlib; "
                "root=pathlib.Path(arbor.__file__).resolve().parent; "
                "print(arbor.__version__); print(root); print(root/'bin'/'arbor-build-catalogue')"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
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
        raise RuntimeError(f"Arbor catalogue builder is missing: {build_tool}")
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


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report: dict[str, Any] = {
        "catalogue": CATALOGUE_NAME,
        "source_dir": str(SOURCE_DIR),
        "source_tree_policy": "app-owned Arbor ports; Digifly Public remains read-only",
    }
    output: Path | None = None
    try:
        python = Path(args.python).expanduser().resolve()
        if not python.is_file():
            raise RuntimeError(f"Python interpreter does not exist: {python}")
        version, arbor_root, build_tool = _resolve_build_tool(python)
        output = _external_output_dir(args.output_dir)
        report.update(
            {
                "arbor_version": version,
                "arbor_root": arbor_root,
                "build_tool": str(build_tool),
                "output_dir": str(output),
            }
        )
        command = [
            str(python),
            "-B",
            str(build_tool),
            CATALOGUE_NAME,
            str(SOURCE_DIR),
        ]
        if args.keep_generated:
            command.extend(("--debug", "generated"))
        completed = subprocess.run(
            command,
            cwd=output,
            check=False,
            capture_output=True,
            text=True,
        )
        build_output = completed.stdout + completed.stderr
        (output / "build.log").write_text(build_output, encoding="utf-8")
        report["command"] = command
        report["returncode"] = completed.returncode
        if completed.returncode:
            report["status"] = "blocked"
            report["failure"] = _classify_failure(build_output)
        else:
            candidates = sorted(output.rglob(f"{CATALOGUE_NAME}-catalogue.*"))
            report["status"] = "complete"
            report["catalogue_artifacts"] = [str(path.resolve()) for path in candidates]
    except Exception as exc:
        report["status"] = "failed"
        report["failure"] = {"code": "setup_failed", "detail": str(exc)}

    if output is not None:
        (output / "build_report.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
