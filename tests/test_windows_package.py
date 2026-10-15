"""Release gates exercised with ordinary software fixtures, not experiments."""
import importlib.util
import json
from pathlib import Path
import struct
import pytest
from test_package_foundation import _current_release_payload

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def fixture_bundle(root):
    bundle = root / "package"
    for relative, data in _current_release_payload(archive_root="source", wheel=False).items():
        relative = relative.removeprefix("source/").removeprefix("src/")
        target = bundle / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    pe = bytearray(128)
    pe[:2] = b"MZ"
    struct.pack_into("<I", pe, 60, 64)
    pe[64:70] = b"PE\0\0\x64\x86"
    (bundle / "Digifly Workstation.exe").write_bytes(pe)
    return bundle


def test_windows_gate_accepts_complete_fixture_and_rejects_changed_payload(tmp_path):
    gate = module("windows_package")
    bundle = fixture_bundle(tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    (source / "windows_source_manifest.json").write_text(json.dumps({"files": {}}))
    assert gate.seal(bundle, source, "fixture")["ok"]
    archive = tmp_path / "Digifly-Workstation-fixture-Windows-x86_64.zip"
    assert gate.archive(bundle, archive)["ok"]
    (bundle / "LICENSE").write_text("broken license")
    with pytest.raises(ValueError, match="sealed inventory"):
        gate.check(bundle)


def test_windows_gate_rejects_dataset_even_with_declared_inventory(tmp_path):
    gate = module("windows_package")
    bundle = fixture_bundle(tmp_path)
    (bundle / "private.parquet").write_bytes(b"forbidden dataset fixture")
    source = tmp_path / "source"
    source.mkdir()
    (source / "windows_source_manifest.json").write_text(json.dumps({"files": {}}))
    with pytest.raises(ValueError, match="scientific_dataset"):
        gate.seal(bundle, source, "fixture")


def test_windows_source_selection_excludes_private_inputs_and_old_outputs():
    paths = module("stage_windows_source").inputs(ROOT)
    names = {p.relative_to(ROOT).as_posix() for p in paths}
    assert "src/digifly_app/workers/pair_graph_import_worker.py" in names
    assert "src/digifly_app/ui/package_smoke.py" in names
    assert ".gitattributes" in names
    assert "* -text" in (ROOT / ".gitattributes").read_text("utf-8")
    assert all(not name.startswith(("outputs/", "dist/", ".venv/", "sources/")) for name in names)
    assert all(p.suffix not in {".swc", ".parquet", ".sqlite", ".h5", ".pyc"} for p in paths)


def test_windows_build_has_no_docker_or_arbor_dependency_and_gates_before_zip():
    script = (ROOT / "scripts/build_windows.ps1").read_text("utf-8")
    assert "docker_python_launcher" not in script and "bundle_arbor" not in script
    assert "robocopy" not in script and "Remove-Item" not in script
    assert script.index('windows_package.py" seal') < script.index('windows_package.py" archive')
    assert "stage_pdcm_bionet_scalable_assets.py" in script


def test_publication_stops_before_network_without_redistribution_review(tmp_path, monkeypatch):
    publisher = module("publish_windows_release")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_REPOSITORY", "Saint-Sam/Digifly-Application")
    with pytest.raises(ValueError, match="Public replacement held"):
        publisher.publish(tmp_path / "no-artifact.zip")
