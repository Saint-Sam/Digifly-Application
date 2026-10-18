"""Remove only verified, completed updater artifacts; never sweep user folders.

The new GUI calls this after its startup acknowledgement. The external helper
must have exited first (Windows cannot delete its running EXE/DLLs). Failed or
live transactions are not cleanup candidates. One active recovery draft and
small transaction/cleanup records remain; superseded unchanged snapshots go.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import time

from .update_installer import (SCHEMA, ParentWait, executable_in, file_hash,
                               installation_root, tree_inventory, write_new)


def _plain_path(path: Path) -> None:
    if not path.is_absolute() or path != path.resolve():
        raise ValueError("Cleanup path is not an exact absolute path.")
    for item in (path, *path.parents):
        if item.is_symlink() or (item.exists() and getattr(item.lstat(), "st_file_attributes", 0) & 0x400):
            raise ValueError("Cleanup refuses links and junctions.")


def _exited(pid: int) -> bool:
    try:
        process = ParentWait(int(pid))
    except OSError:
        # OpenProcess on a process that already exited returns INVALID_PARAMETER.
        # ACCESS_DENIED is not evidence of exit and must keep the files intact.
        import ctypes
        if os.name == "nt" and ctypes.get_last_error() == 87:
            return True
        raise
    try:
        return process.exited()
    finally:
        process.close()


def _completed(path: Path, target: Path) -> tuple[dict, dict]:
    _plain_path(path)
    request = json.loads(path.read_text("utf-8"))
    token = request.get("token", "")
    if request.get("schema") != SCHEMA or not re.fullmatch("[a-f0-9]{32}", token):
        raise ValueError("Unknown updater record.")
    if Path(request["target"]) != target:
        raise ValueError("Updater record belongs to another installation.")
    result = json.loads((path.parent / "result.json").read_text("utf-8"))
    ack = result.get("startup") or {}
    key = executable_in(Path("."), request["system"]).as_posix()
    if (result.get("status") != "installed" or result.get("target") != str(target)
            or ack.get("token") != token or ack.get("window_ready") is not True
            or ack.get("draft_restored") is not True
            or ack.get("draft_sha256") != request["draft_sha256"]
            or ack.get("executable_sha256") != request["candidate_inventory"][key]["sha256"]):
        raise ValueError("Update has not committed a verified window and draft.")
    ready = json.loads((path.parent / "ready.json").read_text("utf-8"))
    if ready.get("token") != token or not _exited(ready["pid"]) or not _exited(request["parent_pid"]):
        raise ValueError("The updater or previous app is still running.")
    return request, ack


def _remove_tree(path: Path, expected: dict) -> int:
    _plain_path(path)
    if not path.exists():
        return 0
    # Reject Windows reparse-point directories even when they appeared after
    # staging. Internal macOS framework links are recorded and not traversed.
    for directory, folders, files in os.walk(path, followlinks=False):
        for name in (*folders, *files):
            entry = Path(directory) / name
            if getattr(entry.lstat(), "st_file_attributes", 0) & 0x400:
                raise ValueError("Cleanup refuses a reparse point inside an app copy.")
    if tree_inventory(path) != expected:
        raise ValueError("Files changed or were added; this app copy was kept.")
    if any(Path(name).suffix.lower() in {".swc", ".nwb", ".h5", ".hdf5"} for name in expected):
        raise ValueError("Protected morphology or run data found; this copy was kept.")
    amount = sum(item.get("bytes", 0) for item in expected.values())
    # Only this exact, checked updater-created tree is a recursive target.
    shutil.rmtree(path)
    return amount


def cleanup_completed_update(request_path: Path, cache_root: Path, *, wait_seconds=45) -> dict:
    """One bounded post-update pass. Never accepts a broad deletion target."""
    request_path, cache_root = request_path.absolute(), cache_root.absolute()
    _plain_path(cache_root)
    if (request_path.name != "request.json" or request_path.parent.parent != cache_root
            or not request_path.parent.name.startswith("install-")):
        raise ValueError("Cleanup requires an exact updater transaction in its cache.")
    target = installation_root()
    _plain_path(target)
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            current, _ = _completed(request_path, target)
            if target.with_name("." + target.name + ".update-lock").exists():
                raise ValueError("The installer is still finishing.")
            break
        except (FileNotFoundError, ValueError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.25)
    if tree_inventory(target) != current["candidate_inventory"]:
        raise ValueError("The installed app changed; cleanup was not started.")
    report = {"schema": "digifly-update-cleanup-v1", "removed": [], "kept": [], "bytes_removed": 0,
              "active_draft": current["draft"], "target": str(target)}
    # Only journaled successful updates for this exact installation. Never
    # infer ownership from age, a filename, Downloads, or the workspace root.
    paths = [request_path, *sorted(cache_root.glob("install-*/request.json"))]
    for path in dict.fromkeys(paths):
        try:
            item, ack = _completed(path, target)
            if path != request_path and not _exited(ack["pid"]):
                raise ValueError("An earlier updated app is still running.")
            token = item["token"]
            helper = path.parent / ("helper.app" if item["system"] == "Darwin" else "helper")
            backup = target.with_name(target.name + ".rollback-" + token)
            if Path(item["helper"]) != helper or Path(item["backup"]) != backup:
                raise ValueError("Updater artifact paths differ from their exact owned locations.")
            trees = [(backup, item["original"]), (helper, item["original"])]
            metadata = item.get("cleanup") or {}
            if metadata:
                stage = Path(metadata["stage"])
                candidate = Path(item["candidate"])
                if (stage.parent != target.parent or not stage.name.startswith(".digifly-update-")
                        or not candidate.is_relative_to(stage) or candidate == stage):
                    raise ValueError("Unexpected extraction folder; retained.")
                trees.append((stage, metadata["stage_remainder"]))
            for artifact, inventory in trees:
                try:
                    if artifact.exists() or artifact.is_symlink():
                        amount = _remove_tree(artifact, inventory)
                        report["bytes_removed"] += amount
                        report["removed"].append(str(artifact))
                except (OSError, ValueError) as exc:
                    report["kept"].append({"path": str(artifact), "reason": str(exc)})
            if metadata:
                archive = Path(metadata["archive"])
                # Never delete a manually selected ZIP outside updater cache.
                if archive.parent.parent == cache_root and archive.parent.name.startswith("download-"):
                    for artifact, expected_hash in ((archive, metadata["archive_sha256"]),
                            (archive.with_name(archive.name + ".sha256"), metadata["checksum_sha256"])):
                        _plain_path(artifact)
                        if artifact.exists():
                            if file_hash(artifact) != expected_hash:
                                report["kept"].append({"path": str(artifact), "reason": "Download changed; retained."})
                            else:
                                report["bytes_removed"] += artifact.stat().st_size
                                artifact.unlink()
                                report["removed"].append(str(artifact))
                    if archive.parent.exists() and not any(archive.parent.iterdir()):
                        archive.parent.rmdir()
            if path != request_path:
                draft = path.parent / "recovered-draft.digifly.json"
                if str(draft) == item["draft"] and draft.exists():
                    _plain_path(draft)
                    if file_hash(draft) == item["draft_sha256"]:
                        report["bytes_removed"] += draft.stat().st_size
                        draft.unlink()
                        report["removed"].append(str(draft))
                    else:
                        report["kept"].append({"path": str(draft), "reason": "Draft was edited; retained."})
        except (KeyError, OSError, ValueError) as exc:
            report["kept"].append({"path": str(path), "reason": str(exc)})
    report["ok"] = not report["kept"]
    # Receipts are tiny and make every deletion explainable. Keep previous
    # receipts intact; a later successful update can retry locked artifacts.
    receipt = request_path.parent / "cleanup.json"
    if not receipt.exists():
        write_new(receipt, report)
    return report
