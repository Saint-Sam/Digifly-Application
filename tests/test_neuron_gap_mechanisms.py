from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import tomllib

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
MECHANISM_ROOT = REPO_ROOT / "mechanisms" / "neuron_gap_junctions"
BUILD_SCRIPT = REPO_ROOT / "scripts" / "build_neuron_gap_mechanisms.py"
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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_builder_module():
    spec = importlib.util.spec_from_file_location("build_neuron_gap_mechanisms", BUILD_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


def _fake_tools(tmp_path: Path) -> tuple[Path, Path, Path]:
    runtime_root = tmp_path / "fake-neuron-runtime"
    runtime_root.mkdir()
    (tmp_path / "python3").symlink_to(Path(sys.executable).resolve())
    python = tmp_path / "fake-neuron-python"
    nrnivmodl = tmp_path / "fake-nrnivmodl"
    _make_executable(
        python,
        "#!/usr/bin/env python3\n"
        + textwrap.dedent(
            f"""
            import json
            import sys

            code = sys.argv[sys.argv.index("-c") + 1]
            if "nrnivmodl_candidates" in code:
                print(json.dumps({{
                    "python_version": "3.12.fake",
                    "neuron_version": "9.99.fake",
                    "neuron_file": {str(runtime_root / "neuron" / "__init__.py")!r},
                    "runtime_data_root": {str(runtime_root)!r},
                    "nrnivmodl_candidates": [],
                }}, sort_keys=True))
            elif "nrn_load_dll" in code:
                print(json.dumps({{
                    "library": sys.argv[-1],
                    "mechanisms": ["Gap", "RectGap", "HeteroRectGap"],
                    "neuron_version": "9.99.fake",
                }}, sort_keys=True))
            else:
                raise SystemExit("unexpected fake Python invocation")
            """
        ).lstrip(),
    )
    _make_executable(
        nrnivmodl,
        "#!/usr/bin/env python3\n"
        + textwrap.dedent(
            """
            import json
            import os
            from pathlib import Path
            import sys

            root = Path.cwd()
            if sys.argv[1:] != ["sources"]:
                raise SystemExit(f"unexpected arguments: {sys.argv[1:]}")
            count_path = root / "compile_count.txt"
            count = int(count_path.read_text()) + 1 if count_path.exists() else 1
            count_path.write_text(str(count), encoding="utf-8")
            environment = {
                key: os.environ.get(key)
                for key in (
                    "PYTHONHOME",
                    "PYTHONPATH",
                    "VIRTUAL_ENV",
                    "CONDA_PREFIX",
                    "DYLD_LIBRARY_PATH",
                    "QT_PLUGIN_PATH",
                    "NEURON_MODULE_OPTIONS",
                    "NRNHOME",
                    "NRN_PYTHONEXE",
                    "TMPDIR",
                    "PATH",
                )
            }
            (root / "compile_environment.json").write_text(
                json.dumps(environment, sort_keys=True), encoding="utf-8"
            )
            library = root / "fake_arch" / "libnrnmech.so"
            library.parent.mkdir()
            library.write_bytes(b"fake-neuron-gap-library-v1\\n")
            """
        ).lstrip(),
    )
    return python, nrnivmodl, runtime_root


def _run_builder(
    output: Path,
    *,
    python: Path,
    nrnivmodl: Path,
    environment: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-E",
            str(BUILD_SCRIPT),
            "--python",
            str(python),
            "--nrnivmodl",
            str(nrnivmodl),
            "--output-dir",
            str(output),
        ],
        cwd=REPO_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


def test_packaged_sources_and_frozen_manifest_match_phase2_bytes() -> None:
    frozen = json.loads((MECHANISM_ROOT / "source_manifest.json").read_text(encoding="utf-8"))
    assert frozen["schema_version"] == 1
    assert frozen["engine"] == "neuron"
    assert set(frozen["mechanisms"]) == set(EXPECTED_SOURCES)
    for mechanism, (filename, source_hash) in EXPECTED_SOURCES.items():
        assert frozen["mechanisms"][mechanism] == {
            "source_filename": filename,
            "source_sha256": source_hash,
        }
        assert _sha256(MECHANISM_ROOT / filename) == source_hash


def test_neuron_gap_resources_and_builder_are_in_the_distribution_contract() -> None:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    data_files = pyproject["tool"]["setuptools"]["data-files"]
    assert set(data_files["share/digifly-workstation/mechanisms/neuron_gap_junctions"]) == {
        "mechanisms/neuron_gap_junctions/Gap.mod",
        "mechanisms/neuron_gap_junctions/HeteroRectGap.mod",
        "mechanisms/neuron_gap_junctions/README.md",
        "mechanisms/neuron_gap_junctions/RectGap.mod",
        "mechanisms/neuron_gap_junctions/source_manifest.json",
    }
    assert "scripts/build_neuron_gap_mechanisms.py" in data_files[
        "share/digifly-workstation/scripts"
    ]
    deploy_spec = (REPO_ROOT / "pysidedeploy.spec").read_text(encoding="utf-8")
    assert (
        "--include-data-dir=mechanisms/neuron_gap_junctions="
        "mechanisms/neuron_gap_junctions"
    ) in deploy_spec
    assert (
        "--include-data-file=scripts/build_neuron_gap_mechanisms.py="
        "scripts/build_neuron_gap_mechanisms.py"
    ) in deploy_spec


def test_builder_sanitizes_embedded_app_and_conflicting_neuron_environment() -> None:
    builder = _load_builder_module()
    contaminated = {
        "PATH": os.pathsep.join(
            ("/Applications/Fake.app/Contents/MacOS", "/Applications/NEURON/bin", "/usr/bin")
        ),
        "PYTHONPATH": "/Applications/NEURON/lib/python",
        "PYTHONHOME": "/embedded/python",
        "CONDA_PREFIX": "/wrong/conda",
        "DYLD_LIBRARY_PATH": "/embedded/lib",
        "QT_PLUGIN_PATH": "/embedded/qt",
        "NRNHOME": "/wrong/neuron",
    }
    cleaned = builder._sanitized_environment(inherited=contaminated)
    for key in (
        "PYTHONPATH",
        "PYTHONHOME",
        "CONDA_PREFIX",
        "DYLD_LIBRARY_PATH",
        "QT_PLUGIN_PATH",
        "NRNHOME",
    ):
        assert key not in cleaned
    assert cleaned["PATH"] == "/usr/bin"
    assert cleaned["NEURON_MODULE_OPTIONS"] == "-nogui"
    assert cleaned["PYTHONNOUSERSITE"] == "1"


def test_builder_preserves_selected_virtualenv_python_launcher(tmp_path: Path) -> None:
    builder = _load_builder_module()
    base_python = tmp_path / "base" / "python3.12"
    base_python.parent.mkdir()
    base_python.symlink_to(Path(sys.executable))
    launcher = tmp_path / "neuron-env" / "bin" / "python"
    launcher.parent.mkdir(parents=True)
    launcher.symlink_to(base_python)

    selected = builder._resolve_executable(str(launcher), label="NEURON Python")

    assert selected == launcher.absolute()
    assert selected != launcher.resolve()
    assert "Path(sys.executable).absolute().parent" in builder._RUNTIME_PROBE


def test_builder_rejects_a_cache_inside_the_application_repository() -> None:
    builder = _load_builder_module()
    with pytest.raises(builder.BuildError, match="outside") as raised:
        builder._external_output_dir(str(REPO_ROOT / "local-neuron-cache"))
    assert raised.value.code == "unsafe_output_dir"


def test_external_build_emits_load_checked_manifest_and_reuses_cache(tmp_path: Path) -> None:
    base_python, nrnivmodl, runtime_root = _fake_tools(tmp_path)
    python = tmp_path / "neuron-env" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(base_python)
    output = tmp_path / "neuron-gap-cache"
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONPATH": "/Applications/NEURON/lib/python",
            "PYTHONHOME": "/embedded/python",
            "VIRTUAL_ENV": "/embedded/venv",
            "CONDA_PREFIX": "/wrong/conda",
            "DYLD_LIBRARY_PATH": "/embedded/lib",
            "QT_PLUGIN_PATH": "/embedded/qt",
            "NRNHOME": "/wrong/neuron",
            "PATH": os.pathsep.join(
                (
                    "/Applications/Fake.app/Contents/MacOS",
                    "/Applications/NEURON/bin",
                    environment.get("PATH", ""),
                )
            ),
        }
    )

    first = _run_builder(
        output,
        python=python,
        nrnivmodl=nrnivmodl,
        environment=environment,
    )
    assert first.returncode == 0, first.stdout + first.stderr
    report = json.loads(first.stdout)
    assert report["status"] == "complete"
    assert report["neuron_version"] == "9.99.fake"
    assert report["load_probe"]["mechanisms"] == [
        "Gap",
        "RectGap",
        "HeteroRectGap",
    ]

    manifest_path = output / "mechanism_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert set(manifest) == {
        "schema_version",
        "engine",
        "neuron_version",
        "library_relpath",
        "library_sha256",
        "mechanisms",
    }
    assert manifest["schema_version"] == 1
    assert manifest["engine"] == "neuron"
    assert manifest["neuron_version"] == "9.99.fake"
    library = output / manifest["library_relpath"]
    assert library.is_file()
    assert _sha256(library) == manifest["library_sha256"]
    for mechanism, (filename, source_hash) in EXPECTED_SOURCES.items():
        assert manifest["mechanisms"][mechanism] == {
            "source_filename": filename,
            "source_relpath": f"sources/{filename}",
            "source_sha256": source_hash,
        }
        staged = output / manifest["mechanisms"][mechanism]["source_relpath"]
        assert staged.read_bytes() == (MECHANISM_ROOT / filename).read_bytes()

    child_environment = json.loads(
        (output / "compile_environment.json").read_text(encoding="utf-8")
    )
    for key in (
        "PYTHONHOME",
        "PYTHONPATH",
        "VIRTUAL_ENV",
        "CONDA_PREFIX",
        "DYLD_LIBRARY_PATH",
        "QT_PLUGIN_PATH",
    ):
        assert child_environment[key] is None
    assert child_environment["NEURON_MODULE_OPTIONS"] == "-nogui"
    assert child_environment["NRNHOME"] == str(runtime_root)
    assert child_environment["NRN_PYTHONEXE"] == str(python)
    assert child_environment["TMPDIR"] == str(output / ".tmp")
    assert ".app/Contents/MacOS" not in child_environment["PATH"]
    assert "/Applications/NEURON/bin" not in child_environment["PATH"]
    assert (output / "compile_count.txt").read_text(encoding="utf-8") == "1"

    second = _run_builder(
        output,
        python=python,
        nrnivmodl=nrnivmodl,
        environment=environment,
    )
    assert second.returncode == 0, second.stdout + second.stderr
    assert json.loads(second.stdout)["status"] == "cached"
    assert (output / "compile_count.txt").read_text(encoding="utf-8") == "1"
    assert json.loads(manifest_path.read_text(encoding="utf-8")) == manifest


def test_cache_reuse_detects_staged_source_tampering(tmp_path: Path) -> None:
    python, nrnivmodl, _ = _fake_tools(tmp_path)
    output = tmp_path / "neuron-gap-cache"
    environment = os.environ.copy()
    first = _run_builder(
        output,
        python=python,
        nrnivmodl=nrnivmodl,
        environment=environment,
    )
    assert first.returncode == 0, first.stdout + first.stderr
    (output / "sources" / "Gap.mod").write_text("NEURON {}\n", encoding="utf-8")

    second = _run_builder(
        output,
        python=python,
        nrnivmodl=nrnivmodl,
        environment=environment,
    )
    assert second.returncode == 2
    failure = json.loads(second.stdout)
    assert failure["status"] == "failed"
    assert failure["failure"]["code"] == "cache_invalid"
    assert "source hash mismatch" in failure["failure"]["detail"]
