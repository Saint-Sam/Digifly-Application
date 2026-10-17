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
    assert all(p.suffix not in {".swc", ".parquet", ".sqlite", ".h5", ".pyc"}
               or p.relative_to(ROOT).as_posix() == "bundled_data/manc/unique_pairs_by_id.parquet" for p in paths)


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


@pytest.mark.parametrize("previous", ["cad2389f5c104b6e05b2914002a70e1478b8c9ca4552507e2ce455bbf240c851", "51b05776ce8cebab7db43564a123e334b8532727fd1a196e2a0a3ef4d11e473c", "f0cc3bcaa9c87d02c7fbb7b92efe6b11742a5cc0a2d4aeeb75a88f54df97a36f", "b998fcbdb5fe0dde0499fef7d49ec8c036b598682b72020c6d58a158db1b4924"])
def test_release_replacement_preserves_old_payload_and_mac_assets(tmp_path, monkeypatch, previous):
    import hashlib
    publisher = module("publish_windows_release")
    gate = module("windows_package")
    bundle = fixture_bundle(tmp_path)
    (bundle / "digifly_build.json").write_text(json.dumps({"source_commit": "fixture-source"}))
    (bundle / "THIRD_PARTY_COMPONENTS.json").write_text(json.dumps({"schema": "digifly-windows-notice-inventory-v1"}))
    notice = bundle / "mechanisms/augustin_2019/NOTICE.md"
    notice.parent.mkdir(parents=True, exist_ok=True)
    notice.write_bytes((ROOT / "mechanisms/augustin_2019/NOTICE.md").read_bytes())
    source = tmp_path / "source"
    source.mkdir()
    (source / "windows_source_manifest.json").write_text(json.dumps({"files": {}}))
    gate.seal(bundle, source, "fixture")
    archive = tmp_path / "Digifly-Workstation-fixture-Windows-x86_64.zip"
    gate.archive(bundle, archive)
    sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    native = tmp_path / "updater-evidence.json"
    exe_sha = hashlib.sha256((bundle / "Digifly Workstation.exe").read_bytes()).hexdigest()
    evidence = {"schema": "digifly-native-updater-gate-v1", "ok": True, "platform": "Windows",
        "archive_sha256": sha, "source_app_executable_sha256": exe_sha, "original_app_unchanged": True, "cases": {}}
    for name, status in (("install", "installed"), ("rollback", "rolled_back")):
        image = tmp_path / (name + "-window.png")
        image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"software fixture, not GUI acceptance" * 10)
        evidence["cases"][name] = {"status": status, "external_files_unchanged": True, "draft_unchanged": True,
            "exact_tree_and_retained_backup_verified": True, "screenshot_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
            "startup": {"window_ready": True, "draft_restored": True, "executable_sha256": exe_sha},
            "expected_failure": "fixture startup failure" if name == "rollback" else None}
    native.write_text(json.dumps(evidence))
    review = tmp_path / "review.json"
    review.write_text(json.dumps({"artifact_sha256": sha, "permission_evidence": "software fixture, not a licensing opinion",
        "technical_windows_package_passed": True, "ci_run_id": 1, "ci_source_commit": "fixture-source", "previous_alias_sha256": previous,
        "updater_native_evidence": str(native)}))
    assets = {
        publisher.OLD: {"id": 10, "name": publisher.OLD, "digest": "sha256:" + previous},
        publisher.OLD + ".sha256": {"id": 11, "name": publisher.OLD + ".sha256", "digest": "old-checksum-fixture"},
        "macOS-fixture.zip": {"id": 20, "name": "macOS-fixture.zip", "digest": "mac-fixture-unchanged"},
    }
    mutations = []
    def fake_gh(*args):
        if args[0] == "api":
            if "/actions/runs/" in args[1]:
                return json.dumps({"conclusion": "success", "head_sha": "fixture-source"})
            if "/releases/assets/" in args[1]:
                identifier = int(args[1].rsplit("/", 1)[-1])
                old_name, asset = next((key, value) for key, value in assets.items() if value["id"] == identifier)
                new_name = args[-1].removeprefix("name=")
                del assets[old_name]
                assets[new_name] = {**asset, "name": new_name}
                mutations.append(("rename", old_name, new_name))
                return json.dumps(assets[new_name])
            return json.dumps({"id": 1, "body": "Historical Mac notes", "assets": list(assets.values())})
        if args[:2] == ("release", "upload"):
            file = Path(args[3])
            assert file.name not in assets
            assets[file.name] = {"id": 100 + len(assets), "name": file.name,
                "digest": "sha256:" + hashlib.sha256(file.read_bytes()).hexdigest(), "browser_download_url": "https://example.invalid/fixture"}
            mutations.append(("upload", file.name))
            return ""
        assert args[:2] == ("release", "edit")
        return ""
    monkeypatch.setattr(publisher, "gh", fake_gh)
    original_review = review.read_text()
    missing = json.loads(original_review)
    del missing["updater_native_evidence"]
    review.write_text(json.dumps(missing))
    with pytest.raises(ValueError, match="Native updater"):
        publisher.publish(archive, review)
    assert mutations == []
    review.write_text(original_review)
    publisher.publish(archive, review)
    backup = publisher.KNOWN_ROLLBACKS[previous]
    assert assets[backup]["id"] == 10
    assert assets[backup]["digest"] == "sha256:" + previous
    assert assets[publisher.OLD]["digest"] == "sha256:" + sha
    assert assets["macOS-fixture.zip"] == {"id": 20, "name": "macOS-fixture.zip", "digest": "mac-fixture-unchanged"}
    assert mutations.index(("upload", archive.name)) < mutations.index(("rename", publisher.OLD, backup))


@pytest.mark.parametrize("include_neuron_notices", [False, True])
def test_notice_only_overlay_preserves_original_payload_and_rejects_changed_notice(tmp_path, include_neuron_notices):
    import hashlib
    import zipfile
    gate, overlay = module("windows_package"), module("augment_windows_notices")
    bundle = fixture_bundle(tmp_path)
    (bundle / "THIRD_PARTY_COMPONENTS.json").write_text(json.dumps({"components": []}))
    source = tmp_path / "source"
    source.mkdir()
    (source / "windows_source_manifest.json").write_text(json.dumps({"files": {}}))
    gate.seal(bundle, source, "fixture")
    base = tmp_path / "Digifly-Workstation-base-Windows-x86_64.zip"
    gate.archive(bundle, base)
    extra, additional = tmp_path / "extra", tmp_path / "additional"
    extra.mkdir()
    additional.mkdir()
    for name in overlay.SOURCES:
        (extra / name).write_text("test notice, not licensing evidence")
    data = b"software-fixture full notice"
    (additional / "notice.html").write_bytes(data)
    (additional / "notices_manifest.json").write_text(json.dumps({
        "schema": "digifly-extra-license-notices-v1", "component": "fixture", "version": "fixture",
        "license": "fixture", "scope": "software test only", "notices": [{"file": "notice.html",
        "sha256": hashlib.sha256(data).hexdigest(), "source_url": "https://example.invalid/fixture"}]}))
    final = tmp_path / "Digifly-Workstation-final-Windows-x86_64.zip"
    assert overlay.augment(base, extra, final, additional,
                           additional if include_neuron_notices else None)["audit"]["ok"]
    with zipfile.ZipFile(base) as old, zipfile.ZipFile(final) as new:
        assert ("licenses/NEURON-9.0.2-dependencies/notice.html" in new.namelist()) == include_neuron_notices
        for name in old.namelist():
            if name not in {"THIRD_PARTY_COMPONENTS.json", "digifly_windows_package.json"}:
                assert old.read(name) == new.read(name)
    with pytest.raises(ValueError, match="must not overwrite"):
        overlay.augment(base, extra, final, additional)
    (additional / "notice.html").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="checksum/collision"):
        overlay.augment(base, extra, tmp_path / "new.zip", additional)
