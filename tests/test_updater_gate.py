from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("native_updater_gate", Path(__file__).parents[1] / "scripts/check_packaged_updater.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def fixture(tmp_path):
    archive, exe = tmp_path / "update.zip", tmp_path / "app.exe"
    archive.write_bytes(b"fixture archive")
    exe.write_bytes(b"fixture executable")
    data = {"schema": "digifly-native-updater-gate-v1", "ok": True, "platform": gate.platform.system(),
            "archive_sha256": gate.file_hash(archive), "source_app_executable_sha256": gate.file_hash(exe),
            "original_app_unchanged": True, "unmarked_original_app_refused": True, "cases": {}}
    for name, status in (("install", "installed"), ("rollback", "rolled_back")):
        image = tmp_path / (name + "-window.png")
        image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"fixture" * 30)
        data["cases"][name] = {"status": status, "external_files_unchanged": True, "draft_unchanged": True,
            "exact_tree_and_retained_backup_verified": True, "screenshot_sha256": gate.file_hash(image),
            "startup": {"window_ready": True, "draft_restored": True, "executable_sha256": gate.file_hash(exe)},
            "expected_failure": "injected startup exit" if name == "rollback" else None}
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps(data))
    return receipt, archive, exe


def test_valid_case_metadata_is_accepted_but_not_a_substitute_for_native_run(tmp_path):
    assert gate.validate_evidence(*fixture(tmp_path))["ok"]


def test_new_cleanup_gate_rejects_historical_retention_only_evidence(tmp_path):
    receipt, archive, exe = fixture(tmp_path)
    with pytest.raises(ValueError, match="cleanup"):
        gate.validate_release_evidence(receipt, archive_sha256=gate.file_hash(archive),
            executable_sha256=gate.file_hash(exe), system=gate.platform.system(), require_cleanup=True)


def test_digest_gate_requires_actual_verifier_without_sidecar(tmp_path):
    receipt, archive, exe = fixture(tmp_path)
    data = json.loads(receipt.read_text())
    data['github_asset_digest_checked'] = True
    for name in data['cases']:
        folder = tmp_path / name / 'gui'
        folder.mkdir(parents=True)
        (folder / 'digest-download.json').write_text(json.dumps({
            'ok': True, 'archive_sha256': gate.file_hash(archive),
            'github_asset_digest': 'sha256:' + gate.file_hash(archive), 'actual_verifier_used': True,
            'legacy_checksum_fixture': name == 'legacy_install',
            'checksum_file_requested': name == 'legacy_install', 'checksum_file_created': name == 'legacy_install'}))
    receipt.write_text(json.dumps(data))
    kwargs = dict(archive_sha256=gate.file_hash(archive), executable_sha256=gate.file_hash(exe),
                  system=gate.platform.system(), require_asset_digest=True)
    assert gate.validate_release_evidence(receipt, **kwargs)['ok']
    path = tmp_path / 'install/gui/digest-download.json'
    failed = json.loads(path.read_text())
    failed['actual_verifier_used'] = False
    path.write_text(json.dumps(failed))
    with pytest.raises(ValueError, match='asset-digest'):
        gate.validate_release_evidence(receipt, **kwargs)


@pytest.mark.parametrize("change", ["archive", "executable", "screenshot", "rollback", "startup"])
def test_native_gate_rejects_stale_or_incomplete_evidence(tmp_path, change):
    receipt, archive, exe = fixture(tmp_path)
    if change == "archive": archive.write_bytes(b"changed archive")
    elif change == "executable": exe.write_bytes(b"changed exe")
    elif change == "screenshot": (tmp_path / "rollback-window.png").write_bytes(b"changed image")
    else:
        data = json.loads(receipt.read_text())
        if change == "rollback": data["cases"]["rollback"]["status"] = "not_installed"
        else: data["cases"]["install"]["startup"]["draft_restored"] = False
        receipt.write_text(json.dumps(data))
    with pytest.raises(ValueError): gate.validate_evidence(receipt, archive, exe)
