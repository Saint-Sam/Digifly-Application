from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import zipfile

import pytest

from digifly_app.core import update_installer as installer


def fake_app(root, value=b"MZold"):
    root.mkdir(parents=True)
    (root / "Digifly Workstation.exe").write_bytes(value)
    (root / "digifly_build.json").write_text('{"version":"0.1.0"}')
    files = {p.name: {"sha256": installer.file_hash(p), "bytes": p.stat().st_size} for p in root.iterdir()}
    (root / installer.MARKER).write_text(json.dumps({"schema": "digifly-windows-package-v1", "files": files}))
    return root


def transaction(tmp_path, monkeypatch):
    monkeypatch.setattr(installer.platform, "system", lambda: "Windows")
    target = fake_app(tmp_path / "installed" / "Digifly")
    new = fake_app(tmp_path / "new", b"MZnew")
    archive = tmp_path / "update.zip"
    with zipfile.ZipFile(archive, "x") as bundle:
        for path in new.iterdir():
            bundle.write(path, path.name)
    archive.with_name("update.zip.sha256").write_text(installer.file_hash(archive) + "  update.zip")
    folder = tmp_path / "cache" / "transaction"
    folder.mkdir(parents=True)
    draft = folder / "draft.json"
    draft.write_text('{"name":"my unsaved draft"}')
    request = installer.prepare_install(archive, target, folder, draft)
    data = installer.read_request(request)
    monkeypatch.setattr(installer, "installation_root", lambda: Path(data["helper"]))
    class DoneParent:
        def __init__(self, pid): pass
        def exited(self): return True
        def close(self): pass
    monkeypatch.setattr(installer, "ParentWait", DoneParent)
    return request, data


@pytest.mark.parametrize("name", ["../escape", "/escape", "C:stream", "x\\escape", "a/./b", "a//b", "CON.txt", "x/foo."])
def test_unsafe_archive_path_refused(tmp_path, name):
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        entry = zipfile.ZipInfo(name)
        entry.filename = name  # deliberately retain a raw Windows backslash
        bundle.writestr(entry, "bad")
    with zipfile.ZipFile(archive) as bundle, pytest.raises(ValueError, match="Unsafe"):
        installer.checked_members(bundle, "Windows")


def test_archive_case_collision_and_symlink_escape_refused(tmp_path):
    for entries, message in [([("A", b"1"), ("a", b"2")], "colliding"), ([("App.app/link", b"../../outside")], "Escaping")]:
        archive = tmp_path / (message + ".zip")
        with zipfile.ZipFile(archive, "w") as bundle:
            for name, value in entries:
                info = zipfile.ZipInfo(name)
                if "link" in name:
                    info.create_system = 3
                    info.external_attr = (stat.S_IFLNK | 0o777) << 16
                bundle.writestr(info, value)
        with zipfile.ZipFile(archive) as bundle, pytest.raises(ValueError, match=message):
            installer.checked_members(bundle, "Darwin")


def test_prepare_leaves_current_app_and_external_data_untouched(tmp_path, monkeypatch):
    sentinel = tmp_path / "workspace.swc"
    sentinel.write_text("user data")
    request, data = transaction(tmp_path, monkeypatch)
    assert installer.tree_inventory(Path(data["target"])) == data["original"]
    assert not Path(data["backup"]).exists()
    assert sentinel.read_text() == "user data"
    assert installer.tree_inventory(Path(data["helper"])) == data["original"]


def test_candidate_tamper_after_staging_never_replaces_old_app(tmp_path, monkeypatch):
    request, data = transaction(tmp_path, monkeypatch)
    monkeypatch.setattr(installer.subprocess, "Popen", lambda *a, **kw: None)
    (Path(data["candidate"]) / "Digifly Workstation.exe").write_bytes(b"MZchanged")
    assert installer.install_transaction(request) == 1
    assert installer.tree_inventory(Path(data["target"])) == data["original"]
    assert not Path(data["backup"]).exists()
    assert json.loads((request.parent / "result.json").read_text())["status"] == "not_installed"


def test_draft_tamper_rejected(tmp_path, monkeypatch):
    request, data = transaction(tmp_path, monkeypatch)
    Path(data["draft"]).write_text("changed")
    with pytest.raises(ValueError, match="draft changed"):
        installer.read_request(request)


def test_success_requires_matching_real_startup_ack_and_retains_old_app(tmp_path, monkeypatch):
    request, data = transaction(tmp_path, monkeypatch)
    class Started:
        pid = 1234
        def __init__(self, *args, **kwargs):
            installer.write_new(request.parent / "started.json", {"token": data["token"], "pid": self.pid,
                "executable_sha256": data["candidate_inventory"]["Digifly Workstation.exe"]["sha256"],
                "draft_sha256": data["draft_sha256"], "window_ready": True, "draft_restored": True})
        def poll(self): return None
    monkeypatch.setattr(installer.subprocess, "Popen", Started)
    assert installer.install_transaction(request) == 0
    assert installer.tree_inventory(Path(data["backup"])) == data["original"]
    assert installer.tree_inventory(Path(data["target"])) == data["candidate_inventory"]
    assert json.loads((request.parent / "result.json").read_text())["status"] == "installed"


def test_early_candidate_exit_rolls_back_exact_old_tree_and_keeps_failed_candidate(tmp_path, monkeypatch):
    request, data = transaction(tmp_path, monkeypatch)
    calls = []
    class Exited:
        pid = 1234
        def __init__(self, arguments, **kwargs): calls.append(arguments)
        def poll(self): return 3
    monkeypatch.setattr(installer.subprocess, "Popen", Exited)
    assert installer.install_transaction(request) == 1
    assert installer.tree_inventory(Path(data["target"])) == data["original"]
    assert installer.tree_inventory(Path(data["failed"])) == data["candidate_inventory"]
    assert calls[-1][1] == "--update-rolled-back"
    assert json.loads((request.parent / "result.json").read_text())["status"] == "rolled_back"


def test_live_unacknowledged_candidate_is_never_killed_or_moved(tmp_path, monkeypatch):
    request, data = transaction(tmp_path, monkeypatch)
    class Running:
        pid = 1234
        def __init__(self, *args, **kwargs): pass
        def poll(self): return None
    monkeypatch.setattr(installer.subprocess, "Popen", Running)
    assert installer.install_transaction(request, startup_seconds=0) == 1
    assert installer.tree_inventory(Path(data["target"])) == data["candidate_inventory"]
    assert installer.tree_inventory(Path(data["backup"])) == data["original"]
    assert not Path(data["failed"]).exists()
    assert json.loads((request.parent / "result.json").read_text())["status"] == "recovery_required_live_candidate"


def test_multiple_transactions_do_not_share_lock(tmp_path, monkeypatch):
    request, data = transaction(tmp_path, monkeypatch)
    target = Path(data["target"])
    lock = target.with_name("." + target.name + ".update-lock")
    lock.write_text("other pending update")
    with pytest.raises(FileExistsError):
        installer.install_transaction(request)
    assert lock.read_text() == "other pending update"


def test_unknown_user_file_inside_windows_install_blocks_update(tmp_path):
    root = fake_app(tmp_path / "app")
    (root / "my-experiment.json").write_text("preserve me")
    with pytest.raises(ValueError, match="outside the app"):
        installer.validate_app(root, "Windows")


def test_receipt_publication_is_atomic_and_never_overwrites(tmp_path):
    path = tmp_path / "receipt.json"
    installer.write_new(path, {"good": True})
    with pytest.raises(FileExistsError):
        installer.write_new(path, {"good": False})
    assert json.loads(path.read_text()) == {"good": True}
