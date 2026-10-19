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

from digifly_app.core.update_installer import DISPOSABLE_TEST_ENV, executable_in, file_hash, tree_inventory


def validate_evidence(receipt_path: Path, archive: Path, executable: Path) -> dict:
    return validate_release_evidence(receipt_path, archive_sha256=file_hash(archive),
                                     executable_sha256=file_hash(executable), system=platform.system())


def validate_release_evidence(receipt_path: Path, *, archive_sha256: str, executable_sha256: str, system="Windows", require_cleanup=False, require_metadata=False) -> dict:
    record = json.loads(receipt_path.read_text("utf-8"))
    if (record.get("schema") != "digifly-native-updater-gate-v1" or record.get("ok") is not True
            or record.get("platform") != system
            or record.get("archive_sha256") != archive_sha256
            or record.get("source_app_executable_sha256") != executable_sha256
            or record.get("original_app_unchanged") is not True
            or record.get("unmarked_original_app_refused") is not True):
        raise ValueError("Missing, unsuccessful or stale native updater evidence.")
    cases = [("install", "installed"), ("rollback", "rolled_back")]
    if record.get("legacy_request_checked"):
        cases.append(("legacy_install", "installed"))
    for name, status in cases:
        case = record["cases"][name]
        metadata_path = receipt_path.parent / name / "gui/metadata-safety.json"
        if record.get("metadata_safety_checked"):
            metadata = json.loads(metadata_path.read_text("utf-8"))
            if not all(metadata.get(key) is True for key in ("ok", "actual_build_reader_used", "historical_catalog_refused")):
                raise ValueError("Missing actual packaged metadata/downgrade check.")
        tree_check = "exact_tree_and_retained_backup_verified"
        if name != "rollback" and record.get("cleanup_policy") == "after_verified_startup":
            tree_check = "exact_tree_and_cleanup_verified"
        if (case.get("status") != status or any(case.get(key) is not True for key in
                ("external_files_unchanged", "draft_unchanged", tree_check))):
            raise ValueError("Native updater case did not pass all preservation checks.")
        screenshot = receipt_path.parent / (name + "-window.png")
        if (file_hash(screenshot) != case.get("screenshot_sha256") or screenshot.stat().st_size < 128
                or screenshot.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n"):
            raise ValueError("Native updater screenshot is missing or changed.")
        if name != "rollback":
            startup = case.get("startup") or {}
            if startup.get("window_ready") is not True or startup.get("draft_restored") is not True or startup.get("executable_sha256") != executable_sha256:
                raise ValueError("Native updater did not acknowledge the actual new window/draft.")
        elif not case.get("expected_failure"):
            raise ValueError("Rollback evidence lacks the injected startup failure.")
    if require_cleanup and (record.get("cleanup_policy") != "after_verified_startup"
            or not record.get("legacy_request_checked")
            or any(record["cases"][name].get("cleanup", {}).get("ok") is not True
                   for name in ("install", "legacy_install"))):
        raise ValueError("Missing native post-update cleanup evidence.")
    if require_metadata and not record.get("metadata_safety_checked"):
        raise ValueError("Missing actual packaged metadata/downgrade check.")
    return record


def check(application: Path, archive: Path, output: Path) -> dict:
    application, archive, output = application.resolve(), archive.resolve(), output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    system = platform.system()
    before = tree_inventory(application)
    record = {"schema": "digifly-native-updater-gate-v1", "ok": False,
              "cleanup_policy": "after_verified_startup",
              "legacy_request_checked": True,
              "metadata_safety_checked": True,
              "platform": system, "archive_sha256": file_hash(archive),
              "source_app_executable_sha256": file_hash(executable_in(application, system)),
              "scope": "actual packaged app, offline verified-archive fixture, GUI update controls/workers, external copied-app helper, replacement and MainWindow/draft restart; explicit startup fault for rollback; not live GitHub transport, Windows11, SmartScreen or scientific acceptance", "cases": {}}
    refusal = output / "unmarked-must-not-be-created"
    environment = {k: v for k, v in os.environ.items() if k != DISPOSABLE_TEST_ENV}
    with (output / "unmarked-refusal.log").open("xb") as log:
        refused = subprocess.run([str(executable_in(application, system)), "--package-update-test", str(refusal),
                                  "--update-archive", str(archive)], env=environment, cwd=output,
                                 stdout=log, stderr=log, timeout=30)
    if refused.returncode == 0 or refusal.exists() or tree_inventory(application) != before:
        raise ValueError("The engineering route did not refuse the unmarked original app.")
    record["unmarked_original_app_refused"] = True
    for name, failure in (("install", False), ("legacy_install", False), ("rollback", True)):
        case = output / name
        case.mkdir()
        target = case / application.name
        if system == "Darwin":
            subprocess.run(["/usr/bin/ditto", str(application), str(target)], check=True)
        else:
            shutil.copytree(application, target)
        original_directory_id = target.stat().st_ino
        disposable_marker = case / ".digifly-updater-disposable.json"
        with disposable_marker.open("x", encoding="utf-8") as handle:
            json.dump({"disposable": True, "target": str(target)}, handle)
        # Bounded external user-state controls, never real credentials/data.
        sentinels = case / "external-user-files"
        sentinels.mkdir()
        for filename in ("my-neuron.swc", "accepted-run.json", "private-runtime.txt", "original-project.digifly.json", "credential-placeholder.txt"):
            (sentinels / filename).write_text("keep unchanged: " + filename, "utf-8")
        user_before = tree_inventory(sentinels)
        args = [str(executable_in(target, system)), "--package-update-test", str(case / "gui"), "--update-archive", str(archive)]
        if failure:
            args.append("--fail-new-startup")
        if name == "legacy_install":
            args.append("--legacy-update-request")
        with (case / "parent.log").open("xb") as log:
            process = subprocess.Popen(args, cwd=case, stdout=log, stderr=log,
                                       env={**os.environ, DISPOSABLE_TEST_ENV: str(disposable_marker)})
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
        cleanup = None
        if failure:
            if tree_inventory(retained) != transaction["candidate_inventory"]:
                raise ValueError("The preserved failed app changed.")
            if (request_path.parent / "cleanup.json").exists():
                raise ValueError("A failed update must not start cleanup.")
        else:
            cleanup = json.loads((request_path.parent / "cleanup.json").read_text("utf-8"))
            if not cleanup.get("ok") or retained.exists() or Path(transaction["helper"]).exists():
                raise ValueError("Successful updater did not remove its exact obsolete app copies.")
            expected_owned = ([transaction["cleanup"]["archive"], transaction["cleanup"]["stage"]]
                              if "cleanup" in transaction else [str(p) for p in (case / "gui/cache").glob("download-*/*.zip")])
            if name == "legacy_install" and not any(Path(p).suffix == ".zip" for p in cleanup["removed"]):
                raise ValueError("Legacy updater archive was not independently identified and removed.")
            for owned in expected_owned:
                if Path(owned).exists():
                    raise ValueError("Successful updater retained its owned archive/extraction folder.")
        if (target.stat().st_ino == original_directory_id) != failure:
            raise ValueError("The actual installation directory was not replaced/restored as expected.")
        if tree_inventory(sentinels) != user_before or file_hash(Path(transaction["draft"])) != transaction["draft_sha256"]:
            raise ValueError("External data or saved draft changed.")
        # Make compact evidence portable out of the native runner.
        shutil.copyfile(request_path.parent / "reopened-window.png", output / (name + "-window.png"))
        record["cases"][name] = {"status": result["status"], "external_files_unchanged": True,
                "draft_unchanged": True, "exact_tree_and_retained_backup_verified": failure,
                "exact_tree_and_cleanup_verified": not failure, "cleanup": cleanup,
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
