import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("build_source_stage", Path(__file__).resolve().parents[1] / "scripts/stage_build_source_manifest.py")
stage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage)


def source(tmp_path):
    root = tmp_path / "source"
    for name in ("main.py", "pyproject.toml", "digifly_build.json", "scripts/build_macos.sh", "scripts/stage_build_source_manifest.py", "src/digifly_app/core/example.py"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("test staged input\n")
    return root


def test_exact_precompile_inputs_survive_attach(tmp_path):
    root = source(tmp_path)
    frozen = tmp_path / "freeze.json"
    bundle = tmp_path / "app"
    digest = stage.freeze(root, frozen, "20261005.6")
    stage.attach(root, frozen, bundle, digest)
    actual = bundle / "Contents/Resources/digifly_build_sources.json"
    assert actual.read_bytes() == frozen.read_bytes()
    receipt = json.loads(actual.read_text())
    assert receipt["build"] == "20261005.6"
    assert "src/digifly_app/core/example.py" in receipt["files"]
    assert str(tmp_path) not in actual.read_text()


def test_source_drift_blocks_attachment(tmp_path):
    root = source(tmp_path)
    frozen = tmp_path / "freeze.json"
    digest = stage.freeze(root, frozen, "20261005.6")
    (root / "src/digifly_app/core/example.py").write_text("changed\n")
    with pytest.raises(ValueError, match="drifted"):
        stage.attach(root, frozen, tmp_path / "app", digest)
    assert not (tmp_path / "app/Contents/Resources/digifly_build_sources.json").exists()


def test_symlink_and_overwrite_refused(tmp_path):
    root = source(tmp_path)
    frozen = tmp_path / "freeze.json"
    digest = stage.freeze(root, frozen, "20261005.6")
    with pytest.raises(ValueError, match="replace"):
        stage.freeze(root, frozen, "20261005.6")
    (root / "src/digifly_app/core/link.py").symlink_to(root / "main.py")
    with pytest.raises(ValueError, match="symbolic"):
        stage.inventory(root)


def test_dangling_output_link_cannot_create_outside_file(tmp_path):
    root = source(tmp_path)
    outside = tmp_path / "outside.json"
    frozen = tmp_path / "freeze.json"
    frozen.symlink_to(outside)
    with pytest.raises(ValueError, match="symbolic"):
        stage.freeze(root, frozen, "20261005.6")
    assert not outside.exists()


def test_bundle_resource_link_cannot_escape(tmp_path):
    root = source(tmp_path)
    frozen = tmp_path / "freeze.json"
    digest = stage.freeze(root, frozen, "20261005.6")
    outside = tmp_path / "outside"
    outside.mkdir()
    contents = tmp_path / "app/Contents"
    contents.mkdir(parents=True)
    (contents / "Resources").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic"):
        stage.attach(root, frozen, tmp_path / "app", digest)
    assert not (outside / "digifly_build_sources.json").exists()


def test_input_root_symlink_and_attach_overwrite_refused(tmp_path):
    root = source(tmp_path)
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic"):
        stage.inventory(alias)
    frozen = tmp_path / "freeze.json"
    digest = stage.freeze(root, frozen, "20261005.6")
    stage.attach(root, frozen, tmp_path / "app", digest)
    with pytest.raises(ValueError, match="replace"):
        stage.attach(root, frozen, tmp_path / "app", digest)


def test_refreshed_manifest_cannot_hide_source_drift(tmp_path):
    root = source(tmp_path)
    frozen = tmp_path / "freeze.json"
    digest = stage.freeze(root, frozen, "20261005.6")
    (root / "src/digifly_app/core/example.py").write_text("changed\n")
    forged = json.loads(frozen.read_text())
    forged["files"] = stage.inventory(root)
    frozen.write_text(json.dumps(forged))
    with pytest.raises(ValueError, match="identity changed"):
        stage.attach(root, frozen, tmp_path / "app", digest)


@pytest.mark.parametrize("change", ["add", "delete"])
def test_added_or_deleted_source_blocks_attachment(tmp_path, change):
    root = source(tmp_path)
    frozen = tmp_path / "freeze.json"
    digest = stage.freeze(root, frozen, "20261005.6")
    if change == "add":
        (root / "src/digifly_app/core/extra.py").write_text("new\n")
    else:
        (root / "src/digifly_app/core/example.py").unlink()
    with pytest.raises(ValueError):
        stage.attach(root, frozen, tmp_path / "app", digest)
