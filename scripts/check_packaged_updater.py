"""Gate actual disposable packaged install/relaunch and startup-failure rollback.

Never uses an installed user app as a replacement target. The supplied app is
copied to fresh per-case directories and all external data sentinels are hashed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import time

from digifly_app.core.update_installer import executable_in, file_hash, tree_inventory


def validate_evidence(receipt_path: Path, archive: Path, executable: Path) -> dict:
    return validate_release_evidence(receipt_path, archive_sha256=file_hash(archive),
                                     executable_sha256=file_hash(executable), system=platform.system())


def validate_release_evidence(receipt_path: Path, *, archive_sha256: str, executable_sha256: str, system="Windows") -> dict:
    record = json.loads(receipt_path.read_text("utf-8"))
    if (record.get("schema") != "digifly-native-updater-gate-v1" or record.get("ok") is not True
            or record.get("platform") != system
            or record.get("archive_sha256") != archive_sha256
            or record.get("source_app_executable_sha256") != executable_sha256
            or record.get("original_app_unchanged") is not True):
        raise ValueError("Missing, unsuccessful or stale native updater evidence.")
    for name, status in (("install", "installed"), ("rollback", "rolled_back")):
        case = record["cases"][name]
        if (case.get("status") != status or any(case.get(key) is not True for key in
                ("external_files_unchanged", "draft_unchanged", "exact_tree_and_retained_backup_verified"))):
            raise ValueError("Native updater case did not pass all preservation checks.")
        screenshot = receipt_path.parent / (name + "-window.png")
        if (file_hash(screenshot) != case.get("screenshot_sha256") or screenshot.stat().st_size < 128
                or screenshot.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n"):
            raise ValueError("Native updater screenshot is missing or changed.")
        if name == "install":
            startup = case.get("startup") or {}
            if startup.get("window_ready") is not True or startup.get("draft_restored") is not True or startup.get("executable_sha256") != executable_sha256:
                raise ValueError("Native updater did not acknowledge the actual new window/draft.")
        elif not case.get("expected_failure"):
            raise ValueError("Rollback evidence lacks the injected startup failure.")
    return record


def check(application: Path, archive: Path, output: Path) -> dict:
    application, archive, output = application.resolve(), archive.resolve(), output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    system = platform.system()
    before = tree_inventory(application)
    record = {"schema": "digifly-native-updater-gate-v1", "ok": False,
              "platform": system, "archive_sha256": file_hash(archive),
              "source_app_executable_sha256": file_hash(executable_in(application, system)),
              "scope": "actual packaged app, offline verified-archive fixture, GUI update controls/workers, external copied-app helper, replacement and MainWindow/draft restart; explicit startup fault for rollback; not live GitHub transport, Windows11, SmartScreen or scientific acceptance", "cases": {}}
    for name, failure in (("install", False), ("rollback", True)):
        case = output / name
        case.mkdir()
        target = case / application.name
        if system == "Darwin":
            subprocess.run(["/usr/bin/ditto", str(application), str(target)], check=True)
        else:
            shutil.copytree(application, target)
        original_directory_id = target.stat().st_ino
        # Bounded external user-state controls, never real credentials/data.
        sentinels = case / "external-user-files"
        sentinels.mkdir()
        for filename in ("my-neuron.swc", "accepted-run.json", "private-runtime.txt", "original-project.digifly.json", "credential-placeholder.txt"):
            (sentinels / filename).write_text("keep unchanged: " + filename, "utf-8")
        user_before = tree_inventory(sentinels)
        args = [str(executable_in(target, system)), "--package-update-test", str(case / "gui"), "--update-archive", str(archive)]
        if failure:
            args.append("--fail-new-startup")
        with (case / "parent.log").open("xb") as log:
            process = subprocess.Popen(args, cwd=case, stdout=log, stderr=log)
        process.wait(timeout=270)
        if process.returncode:
            raise ValueError(f"Native updater parent failed: {case}")
        pointer = json.loads((case / "gui/transaction.json").read_text("utf-8"))
        request_path = Path(pointer["request"])
        transaction = json.loads(request_path.read_text("utf-8"))
        deadline = time.monotonic() + 150
        while not (request_path.parent / "result.json").exists() and time.monotonic() < deadline:
            time.sleep(0.3)
        result = json.loads((request_path.parent / "result.json").read_text("utf-8"))
        expected = "rolled_back" if failure else "installed"
        if result["status"] != expected:
            raise ValueError(f"Expected {expected}, observed {result}")
        deadline = time.monotonic() + 40
        while not (request_path.parent / "reopened-window.png").is_file() and time.monotonic() < deadline:
            time.sleep(0.3)
        if not (request_path.parent / "reopened-window.png").is_file():
            raise ValueError("The restarted actual MainWindow did not render.")
        after = tree_inventory(target)
        if after != (transaction["original"] if failure else transaction["candidate_inventory"]):
            raise ValueError("The installed/restored tree differs from its staged inventory.")
        retained = Path(transaction["failed"] if failure else transaction["backup"])
        if tree_inventory(retained) != (transaction["candidate_inventory"] if failure else transaction["original"]):
            raise ValueError("The preserved previous/failed app changed.")
        if (target.stat().st_ino == original_directory_id) != failure:
            raise ValueError("The actual installation directory was not replaced/restored as expected.")
        if not failure and retained.stat().st_ino != original_directory_id:
            raise ValueError("The retained backup is not the original installation directory.")
        if tree_inventory(sentinels) != user_before or file_hash(Path(transaction["draft"])) != transaction["draft_sha256"]:
            raise ValueError("External data or saved draft changed.")
        # Make compact evidence portable out of the native runner.
        shutil.copyfile(request_path.parent / "reopened-window.png", output / (name + "-window.png"))
        record["cases"][name] = {"status": result["status"], "external_files_unchanged": True,
                "draft_unchanged": True, "exact_tree_and_retained_backup_verified": True,
                "startup": result.get("startup"), "expected_failure": result.get("error"),
                "transaction": str(request_path), "screenshot": name + "-window.png"}
        record["cases"][name]["actual_directory_identity_transition_verified"] = True
        record["cases"][name]["screenshot_sha256"] = file_hash(output / (name + "-window.png"))
    if tree_inventory(application) != before:
        raise ValueError("The supplied original app was modified by the gate.")
    record["original_app_unchanged"] = record["ok"] = True
    (output / "receipt.json").write_text(json.dumps(record, indent=2) + "\n", "utf-8")
    validate_evidence(output / "receipt.json", archive, executable_in(application, system))
    return record


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--application", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    print(json.dumps(check(arguments.application, arguments.archive, arguments.output), indent=2))
