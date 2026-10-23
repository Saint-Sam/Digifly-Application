from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from digifly_app.core import update_cleanup as cleanup
from digifly_app.core import update_installer as installer
from test_update_installer import transaction


def committed(tmp_path, monkeypatch):
    request, data = transaction(tmp_path, monkeypatch)
    # Production cache has exact install-/download- owned namespaces.
    old = request.parent
    moved = old.with_name("install-fixture")
    old.rename(moved)
    for key in ("helper", "draft"):
        data[key] = str(moved / Path(data[key]).name)
    draft = moved / "recovered-draft.digifly.json"
    Path(data["draft"]).rename(draft)
    data["draft"] = str(draft)
    request = moved / "request.json"
    request.write_text(json.dumps(data))
    target = Path(data["target"])
    target.rename(data["backup"])
    Path(data["candidate"]).rename(target)
    ack = {"token": data["token"], "pid": 321, "window_ready": True, "draft_restored": True,
           "draft_sha256": data["draft_sha256"],
           "executable_sha256": data["candidate_inventory"]["Digifly Workstation.exe"]["sha256"]}
    (moved / "result.json").write_text(json.dumps({"status": "installed", "target": str(target), "startup": ack}))
    (moved / "ready.json").write_text(json.dumps({"token": data["token"], "pid": 322}))
    monkeypatch.setattr(cleanup, "installation_root", lambda: target)
    monkeypatch.setattr(cleanup, "_exited", lambda pid: True)
    return request, data


def test_committed_cleanup_removes_only_checked_copies_preserves_current_draft(tmp_path, monkeypatch):
    request, data = committed(tmp_path, monkeypatch)
    external = tmp_path / "external"
    external.mkdir()
    for name in ("neuron.swc", "run.h5", "simulator.exe", "project.digifly.json"):
        (external / name).write_text("protected")
    before = installer.tree_inventory(external)
    result = cleanup.cleanup_completed_update(request, request.parent.parent, wait_seconds=0)
    assert result["ok"] and result["bytes_removed"] > 0
    assert not Path(data["helper"]).exists() and not Path(data["backup"]).exists()
    assert not Path(data["cleanup"]["stage"]).exists()
    assert Path(data["draft"]).is_file()
    assert installer.tree_inventory(external) == before
    assert installer.tree_inventory(Path(data["target"])) == data["candidate_inventory"]
    assert cleanup.cleanup_completed_update(request, request.parent.parent, wait_seconds=0)["removed"] == []


@pytest.mark.parametrize("case", ["failed", "live", "target_changed", "wrong_cache", "lock"])
def test_uncommitted_or_unsafe_cleanup_never_deletes(tmp_path, monkeypatch, case):
    request, data = committed(tmp_path, monkeypatch)
    cache = request.parent.parent
    if case == "failed":
        (request.parent / "result.json").write_text('{"status":"rolled_back"}')
    elif case == "live": monkeypatch.setattr(cleanup, "_exited", lambda pid: False)
    elif case == "target_changed": (Path(data["target"]) / "user-file.txt").write_text("keep")
    elif case == "wrong_cache": cache = tmp_path
    else: Path(data["target"]).with_name("." + Path(data["target"]).name + ".update-lock").touch()
    with pytest.raises(ValueError): cleanup.cleanup_completed_update(request, cache, wait_seconds=0)
    assert Path(data["helper"]).exists() and Path(data["backup"]).exists()


@pytest.mark.parametrize("case", ["changed", "added_swc", "symlink", "escape"])
def test_changed_or_redirected_backup_retained(tmp_path, monkeypatch, case):
    request, data = committed(tmp_path, monkeypatch)
    backup = Path(data["backup"])
    sentinel = tmp_path / "protected.swc"
    sentinel.write_text("unchanged")
    if case == "changed": (backup / "digifly_build.json").write_text("edited")
    elif case == "added_swc": (backup / "user.swc").write_text("never delete")
    elif case == "symlink":
        if os.name == "nt": pytest.skip("Windows junction case is covered separately")
        # Remove only fixture files, then replace this fixture backup with link.
        import shutil
        shutil.rmtree(backup)
        backup.symlink_to(sentinel.parent, target_is_directory=True)
    else:
        data["backup"] = str(sentinel.parent)
        request.write_text(json.dumps(data))
    result = cleanup.cleanup_completed_update(request, request.parent.parent, wait_seconds=0)
    assert not result["ok"] and result["kept"]
    assert backup.exists() and sentinel.read_text() == "unchanged"


def test_protected_files_inside_original_copy_block_recursive_removal(tmp_path):
    root = tmp_path / "owned-copy"
    root.mkdir()
    (root / "cell.swc").write_text("keep")
    with pytest.raises(ValueError, match="Protected"):
        cleanup._remove_tree(root, installer.tree_inventory(root))
    assert (root / "cell.swc").exists()


def test_only_exact_download_files_removed_and_unknown_files_retained(tmp_path, monkeypatch):
    request, data = committed(tmp_path, monkeypatch)
    folder = request.parent.parent / "download-known"
    folder.mkdir()
    archive, checksum = folder / "update.zip", folder / "update.zip.sha256"
    archive.write_bytes(b"update")
    checksum.write_text("checksum")
    (folder / "my-notes.txt").write_text("keep")
    data["cleanup"].update(archive=str(archive), archive_sha256=installer.file_hash(archive),
                           checksum_sha256=installer.file_hash(checksum))
    request.write_text(json.dumps(data))
    result = cleanup.cleanup_completed_update(request, request.parent.parent, wait_seconds=0)
    assert result["ok"] and not archive.exists() and not checksum.exists()
    assert (folder / "my-notes.txt").read_text() == "keep"


def test_digest_only_download_cleanup_needs_no_checksum_sidecar(tmp_path, monkeypatch):
    request, data = committed(tmp_path, monkeypatch)
    folder = request.parent.parent / 'download-digest-only'
    folder.mkdir()
    archive = folder / 'update.zip'
    archive.write_bytes(b'verified download')
    data['cleanup'].pop('checksum_sha256')
    data['cleanup'].update(archive=str(archive), archive_sha256=installer.file_hash(archive))
    request.write_text(json.dumps(data))
    result = cleanup.cleanup_completed_update(request, request.parent.parent, wait_seconds=0)
    assert result['ok'] and not archive.exists() and not folder.exists()


@pytest.mark.parametrize("edited", [False, True])
def test_superseded_unchanged_draft_removed_but_edited_draft_kept(tmp_path, monkeypatch, edited):
    request, data = committed(tmp_path, monkeypatch)
    old = request.parent.parent / "install-older"
    old.mkdir()
    previous = dict(data)
    previous["token"] = "a" * 32
    previous["helper"] = str(old / "helper")
    previous["backup"] = str(Path(data["target"]).with_name("Digifly.rollback-" + previous["token"]))
    previous["draft"] = str(old / "recovered-draft.digifly.json")
    Path(previous["draft"]).write_bytes(Path(data["draft"]).read_bytes())
    (old / "request.json").write_text(json.dumps(previous))
    result = json.loads((request.parent / "result.json").read_text())
    result["startup"]["token"] = previous["token"]
    (old / "result.json").write_text(json.dumps(result))
    (old / "ready.json").write_text(json.dumps({"token": previous["token"], "pid": 333}))
    if edited: Path(previous["draft"]).write_text("a saved edit")
    # Legacy journals without archive metadata are still safe for exact old
    # app-copy/draft removal, but cannot identify an arbitrary downloaded ZIP.
    previous.pop("cleanup")
    (old / "request.json").write_text(json.dumps(previous))
    outcome = cleanup.cleanup_completed_update(request, request.parent.parent, wait_seconds=0)
    assert Path(previous["draft"]).exists() is edited
    assert Path(data["draft"]).exists()
    assert outcome["ok"] is not edited


def test_legacy_upgrade_removes_only_archive_matching_exact_committed_tree(tmp_path, monkeypatch):
    import shutil
    request, data = committed(tmp_path, monkeypatch)
    archive = Path(data["cleanup"]["archive"])
    folder = request.parent.parent / "download-legacy"
    folder.mkdir()
    saved = folder / archive.name
    shutil.copyfile(archive, saved)
    shutil.copyfile(archive.with_name(archive.name + ".sha256"), saved.with_name(saved.name + ".sha256"))
    # A neighboring unrelated archive is never inferred to be obsolete.
    unknown = folder / "unknown.zip"
    unknown.write_bytes(b"unrelated")
    data.pop("cleanup")
    request.write_text(json.dumps(data))
    outcome = cleanup.cleanup_completed_update(request, request.parent.parent, wait_seconds=0)
    assert outcome["ok"] and not saved.exists()
    assert unknown.read_bytes() == b"unrelated"
    assert Path(data["draft"]).exists()


@pytest.mark.skipif(os.name != "nt", reason="Actual Windows junction")
def test_windows_junction_never_followed(tmp_path):
    import subprocess
    root, protected = tmp_path / "owned", tmp_path / "protected"
    root.mkdir(); protected.mkdir()
    (protected / "cell.swc").write_text("keep")
    subprocess.run(["cmd", "/c", "mklink", "/J", str(root / "junction"), str(protected)], check=True)
    with pytest.raises(ValueError, match="reparse"):
        cleanup._remove_tree(root, {})
    assert (protected / "cell.swc").read_text() == "keep"
