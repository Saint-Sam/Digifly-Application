from __future__ import annotations

import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = REPO_ROOT / "scripts" / "build_arbor_gap_catalogue.py"


def _load_builder_module():
    spec = importlib.util.spec_from_file_location(
        "build_arbor_gap_catalogue", BUILD_SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_arbor_builder_preserves_selected_virtualenv_launcher(
    tmp_path: Path, monkeypatch
) -> None:
    builder = _load_builder_module()
    base_python = tmp_path / "base" / "python3.12"
    base_python.parent.mkdir()
    base_python.write_text("#!/bin/sh\n", encoding="utf-8")
    base_python.chmod(0o755)
    launcher = tmp_path / "arbor-env" / "bin" / "python"
    launcher.parent.mkdir(parents=True)
    launcher.symlink_to(base_python)
    arbor_root = tmp_path / "arbor-package"
    build_tool = arbor_root / "bin" / "arbor-build-catalogue"
    build_tool.parent.mkdir(parents=True)
    build_tool.write_text("#!/bin/sh\n", encoding="utf-8")
    captured = {}

    class Completed:
        returncode = 0
        stdout = f"0.12.2\n{arbor_root}\n{build_tool}\n"
        stderr = ""

    def fake_run(command, **kwargs):
        captured["command"] = command
        return Completed()

    monkeypatch.setattr(builder.subprocess, "run", fake_run)

    selected = builder._python_launcher(str(launcher))
    version, _, selected_build_tool = builder._resolve_build_tool(selected)

    assert selected == launcher.absolute()
    assert selected != launcher.resolve()
    assert captured["command"][0] == str(launcher.absolute())
    assert version == "0.12.2"
    assert selected_build_tool == build_tool
