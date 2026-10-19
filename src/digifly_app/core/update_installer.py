"""Explicit, user-level app replacement. No workspace, runtime or registry writes.

The running packaged app clones itself to an external transaction directory.
That clone runs this stdlib-only entrypoint, waits for the original process to
exit, renames the old app to a retained backup and starts the verified candidate.
A real MainWindow startup acknowledgement, not process creation, commits it.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import plistlib
import posixpath
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile

SCHEMA = "digifly-update-transaction-v1"
MARKER = "digifly_windows_package.json"
DISPOSABLE_TEST_ENV = "DIGIFLY_UPDATER_DISPOSABLE_MARKER"


def write_new(path: Path, value: dict) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".receipt-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)  # publish complete bytes, refuse an existing receipt
    finally:
        Path(temporary).unlink(missing_ok=True)


def file_hash(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def running_executable() -> Path:
    # Compiler/runtime values can retain an interpreter or pre-rename name.
    # Windows' loaded process module is the installation identity, not argv.
    if os.name == "nt":
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetModuleFileNameW.argtypes = [wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
        kernel.GetModuleFileNameW.restype = wintypes.DWORD
        buffer = ctypes.create_unicode_buffer(32768)
        size = kernel.GetModuleFileNameW(None, buffer, len(buffer))
        if not size or size >= len(buffer):
            raise OSError("Windows could not identify the running app executable.")
        return Path(buffer.value).resolve()
    if platform.system() == "Darwin":
        # Nuitka can retain Contents/MacOS/python in sys.executable on macOS
        # too. Ask dyld for the actual loaded main module, not argv or Python.
        get_path = ctypes.CDLL(None)._NSGetExecutablePath
        get_path.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        get_path.restype = ctypes.c_int
        size = ctypes.c_uint32(0)
        get_path(None, ctypes.byref(size))
        if not 0 < size.value <= 1024 * 1024:
            raise OSError("macOS could not identify the running app executable.")
        buffer = ctypes.create_string_buffer(size.value)
        if get_path(buffer, ctypes.byref(size)) != 0 or not buffer.value:
            raise OSError("macOS could not identify the running app executable.")
        return Path(os.fsdecode(buffer.value)).resolve()
    return Path(sys.executable).absolute()


def installation_root(executable: Path | None = None) -> Path:
    executable = (executable or running_executable()).absolute()
    if executable.is_symlink():
        raise ValueError("Run the installed app itself, not an executable symlink, to update.")
    if platform.system() == "Windows" and executable.name.casefold() == "digifly workstation.exe":
        root = executable.parent
        if (root / MARKER).is_file():
            return root.resolve()
    if platform.system() == "Darwin" and executable.parent.name == "MacOS":
        root = executable.parent.parent.parent
        if root.suffix == ".app" and (root / "Contents/Info.plist").is_file():
            return root.resolve()
    raise ValueError(f"Installation requires the packaged Digifly app, not a source launcher. Running executable: {executable}")


def executable_in(root: Path, system: str) -> Path:
    return root / ("Digifly Workstation.exe" if system == "Windows" else "Contents/MacOS/main")


def installed_build_info(root: Path, system: str) -> dict:
    """Read package identity without executing it or consulting upload times."""
    metadata = root / ("Contents/MacOS/digifly_build.json" if system == "Darwin" else "digifly_build.json")
    try:
        value = json.loads(metadata.read_text("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Invalid app build metadata.")
        return value
    except FileNotFoundError:
        if system != "Darwin":
            raise ValueError("App build metadata is missing.")
        info = plistlib.loads((root / "Contents/Info.plist").read_bytes())
        return {"version": info.get("CFBundleShortVersionString", ""), "build": info.get("CFBundleVersion", "")}


def require_newer_build(target: Path, candidate: Path, system: str) -> dict:
    from digifly_app.core.updates import _version_key

    old, new = installed_build_info(target, system), installed_build_info(candidate, system)
    old_key = _version_key(str(old.get("version", "")), str(old.get("build", "")))
    new_key = _version_key(str(new.get("version", "")), str(new.get("build", "")))
    if old_key is None or new_key is None:
        raise ValueError("App build numbers could not be verified; automatic replacement is disabled.")
    if new_key <= old_key:
        # Same-build fixtures are allowed only beside the exact disposable app
        # marker; production never uses a general environment bypass.
        if new_key == old_key and os.environ.get(DISPOSABLE_TEST_ENV):
            require_disposable_test()
        else:
            raise ValueError("Refusing an older or identical app build. Your current app and saved settings were not replaced.")
    return {"original": old, "candidate": new}


def require_disposable_test() -> None:
    """The auto-consenting engineering route must never target a normal app."""
    value = os.environ.get(DISPOSABLE_TEST_ENV, "")
    if not value:
        raise ValueError("The updater test requires an explicitly marked disposable app copy.")
    marker = Path(value).absolute()
    target = installation_root()
    if marker.name != ".digifly-updater-disposable.json" or marker.parent.resolve() != target.parent or marker.is_symlink():
        raise ValueError("The updater test marker is not beside its disposable app copy.")
    document = json.loads(marker.read_text("utf-8"))
    if document.get("disposable") is not True or Path(document.get("target", "")).resolve() != target:
        raise ValueError("The updater test marker identifies another app.")


def tree_inventory(root: Path) -> dict:
    records = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            if not path.resolve().is_relative_to(root.resolve()):
                raise ValueError("App contains an escaping symbolic link.")
            records[relative] = {"link": os.readlink(path)}
        elif path.is_file():
            records[relative] = {"sha256": file_hash(path), "bytes": path.stat().st_size,
                                 "executable": bool(path.stat().st_mode & stat.S_IXUSR)}
        elif not path.is_dir():
            raise ValueError("App contains an unsupported filesystem object.")
    return records


def validate_app(root: Path, system: str) -> dict:
    if system == "Windows":
        manifest = json.loads((root / MARKER).read_text("utf-8"))
        if manifest.get("schema") != "digifly-windows-package-v1":
            raise ValueError("This Windows app has no supported sealed package inventory.")
        records = tree_inventory(root)
        actual = {name: {"sha256": item["sha256"], "bytes": item["bytes"]}
                  for name, item in records.items() if name != MARKER and "sha256" in item}
        if actual != manifest["files"] or any("link" in item for item in records.values()):
            raise ValueError("Windows app files differ from the sealed inventory. Keep custom files outside the app folder.")
        executable = executable_in(root, system)
        with executable.open("rb") as handle:
            if handle.read(2) != b"MZ":
                raise ValueError("Candidate is not a native Windows executable.")
    elif system == "Darwin":
        info = plistlib.loads((root / "Contents/Info.plist").read_bytes())
        if info.get("CFBundleIdentifier") != "org.digifly.workstation" or info.get("CFBundleExecutable") != "main":
            raise ValueError("Candidate is not the Digifly Workstation app.")
        subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(root)],
                       check=True, capture_output=True, timeout=90)
        records = tree_inventory(root)
    else:
        raise ValueError("Automatic installation is supported only on Windows and macOS.")
    if not executable_in(root, system).is_file():
        raise ValueError("Candidate app executable is missing.")
    return records


def checked_members(archive: zipfile.ZipFile, system: str) -> list[zipfile.ZipInfo]:
    members, names, links = [], set(), set()
    total = 0
    for entry in archive.infolist():
        # On Windows ZipInfo.filename already normalizes backslashes. Inspect
        # the raw central-directory spelling as well, before accepting paths.
        name = entry.orig_filename.rstrip("/")
        path = PurePosixPath(name)
        if (not name or any(ord(ch) < 32 for ch in name) or path.is_absolute() or ".." in path.parts or "\\" in name
                or ":" in name or any(part in {"", "."} for part in name.split("/"))):
            raise ValueError("Unsafe path in update archive.")
        if system == "Windows" and any(part != part.rstrip(" .") or part.split(".")[0].upper() in
                {"CON", "PRN", "AUX", "NUL", *("COM" + str(i) for i in range(1, 10)), *("LPT" + str(i) for i in range(1, 10))}
                for part in path.parts):
            raise ValueError("Unsafe Windows device or normalized path in update archive.")
        if name.casefold() in names:
            raise ValueError("Duplicate or case-colliding path in update archive.")
        names.add(name.casefold())
        mode = entry.external_attr >> 16
        kind = stat.S_IFMT(mode)
        if kind not in (0, stat.S_IFREG, stat.S_IFDIR, stat.S_IFLNK):
            raise ValueError("Unsupported archive entry type.")
        if stat.S_ISLNK(mode):
            if system != "Darwin" or entry.file_size > 4096:
                raise ValueError("Symbolic links are not allowed in this update.")
            target = archive.read(entry).decode("utf-8")
            resolved = posixpath.normpath(posixpath.join(str(path.parent), target))
            if target.startswith("/") or "\\" in target or ":" in target or not resolved.startswith(path.parts[0] + "/"):
                raise ValueError("Escaping symbolic link in update archive.")
            links.add(name)
        total += entry.file_size
        if total > 8 * 1024**3 or len(names) > 30000:
            raise ValueError("Update archive exceeds expanded size limits.")
        members.append(entry)
    for entry in members:
        if any(str(parent) in links for parent in PurePosixPath(entry.filename.rstrip("/")).parents):
            raise ValueError("Archive writes through a symbolic link.")
    if system == "Darwin":
        roots = {PurePosixPath(e.filename).parts[0] for e in members if not e.filename.startswith("__MACOSX/")}
        if len(roots) != 1 or not next(iter(roots)).endswith(".app"):
            raise ValueError("A macOS update must contain exactly one app bundle.")
    return members


def prepare_install(archive: Path, target: Path, transaction: Path, draft: Path) -> Path:
    """Stage only; the live installation is not modified by preparation."""
    system = platform.system()
    target, transaction, archive, draft = (path.resolve() for path in (target, transaction, archive, draft))
    if transaction.is_relative_to(target) or archive.is_relative_to(target) or not draft.is_relative_to(transaction):
        raise ValueError("Update staging and draft must be outside the installed app.")
    if not draft.is_file() or not archive.is_file() or not target.is_dir():
        raise ValueError("App, verified archive or saved draft is missing.")
    if len(target.parts) < 3 or target.parent == Path.home() or target == Path.home():
        raise ValueError("Keep Digifly in its own extracted app folder. The updater will not replace your home, Desktop or Downloads folder.")
    # Check the checksum again after the UI/download-worker boundary.
    checksum = archive.with_name(archive.name + ".sha256").read_text("ascii").split()[0].lower()
    if file_hash(archive) != checksum:
        raise ValueError("The staged update failed its integrity check.")
    original = validate_app(target, system)
    if not original:
        raise ValueError("Refusing an ambiguous installation target.")
    transaction.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        members = checked_members(bundle, system)
        expanded = sum(item.file_size for item in members)
        old_size = sum(item.get("bytes", 0) for item in original.values())
        if shutil.disk_usage(target.parent).free < expanded + old_size + 256 * 1024**2:
            raise ValueError("Not enough free space to stage this update and keep the previous app.")
        if system == "Windows":
            marker = json.loads(bundle.read(MARKER))
            if {e.filename for e in members if not e.is_dir()} != {*marker["files"], MARKER}:
                raise ValueError("Windows ZIP membership differs from its sealed inventory.")
        stage = Path(tempfile.mkdtemp(prefix=".digifly-update-", dir=target.parent))
        extraction = stage / "payload"
        extraction.mkdir()
        if system == "Windows":
            bundle.extractall(extraction, members=members)
            candidate = extraction
        else:
            subprocess.run(["/usr/bin/ditto", "-x", "-k", str(archive), str(extraction)],
                           check=True, capture_output=True, timeout=180)
            candidate = next(p for p in extraction.iterdir() if p.suffix == ".app")
    candidate_inventory = validate_app(candidate, system)
    versions = require_newer_build(target, candidate, system)
    helper = transaction / ("helper.app" if system == "Darwin" else "helper")
    if system == "Darwin":
        subprocess.run(["/usr/bin/ditto", str(target), str(helper)], check=True, capture_output=True, timeout=180)
    else:
        shutil.copytree(target, helper, symlinks=True)
    if tree_inventory(helper) != original:
        raise ValueError("The external installer copy did not match the running app.")
    token = uuid.uuid4().hex
    candidate_prefix = candidate.relative_to(stage).as_posix() + "/"
    stage_remainder = {name: item for name, item in tree_inventory(stage).items()
                       if not name.startswith(candidate_prefix)}
    request = transaction / "request.json"
    write_new(request, {"schema": SCHEMA, "token": token, "system": system,
                       "parent_pid": os.getpid(), "target": str(target), "candidate": str(candidate),
                       "backup": str(target.with_name(target.name + ".rollback-" + token)),
                       "failed": str(target.with_name(target.name + ".failed-" + token)),
                       "helper": str(helper), "draft": str(draft),
                       "draft_sha256": file_hash(draft), "original": original,
                       "cleanup": {"archive": str(archive), "archive_sha256": file_hash(archive),
                                   "checksum_sha256": file_hash(archive.with_name(archive.name + ".sha256")),
                                   "stage": str(stage), "stage_remainder": stage_remainder},
                       "candidate_inventory": candidate_inventory, "build_check": versions})
    return request


def read_request(path: Path) -> dict:
    request = json.loads(path.read_text("utf-8"))
    if request.get("schema") != SCHEMA or len(request.get("token", "")) != 32:
        raise ValueError("Unsupported update request.")
    target = Path(request["target"])
    candidate = Path(request["candidate"])
    if (not target.is_absolute() or len(target.parts) < 3 or target == Path.home()
            or target.parent == Path.home() or candidate.is_relative_to(target)
            or not candidate.is_relative_to(target.parent) or not candidate.relative_to(target.parent).parts[0].startswith(".digifly-update-")
            or Path(request["helper"]).is_relative_to(target)
            or Path(request["backup"]) != target.with_name(target.name + ".rollback-" + request["token"])
            or Path(request["failed"]) != target.with_name(target.name + ".failed-" + request["token"])):
        raise ValueError("Unsafe update transaction paths.")
    for key in ("target", "candidate", "backup", "failed", "helper", "draft"):
        item = Path(request[key])
        if item.is_symlink() or any(parent.is_symlink() for parent in item.parents):
            raise ValueError("Update transaction paths cannot have symbolic-link ancestors.")
    if file_hash(Path(request["draft"])) != request["draft_sha256"]:
        raise ValueError("The saved update draft changed.")
    return request


class ParentWait:
    def __init__(self, pid: int):
        if pid < 1 or pid == os.getpid():
            raise ValueError("Invalid updater parent process.")
        self.pid, self.handle = pid, None
        if os.name == "nt":
            from ctypes import wintypes
            self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            self.kernel.OpenProcess.restype = wintypes.HANDLE
            self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            self.kernel.WaitForSingleObject.restype = wintypes.DWORD
            self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            self.handle = self.kernel.OpenProcess(0x100000, False, pid)  # SYNCHRONIZE only
            if not self.handle:
                raise OSError("Could not obtain a wait handle for the running app.")

    def exited(self) -> bool:
        if self.handle:
            value = self.kernel.WaitForSingleObject(self.handle, 0)
            if value == 0xFFFFFFFF:
                raise OSError("Could not check the original app process.")
            return value == 0
        try:
            os.kill(self.pid, 0)
            return False
        except ProcessLookupError:
            return True

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)


def start_helper(request_path: Path) -> subprocess.Popen:
    request = read_request(request_path)
    executable = executable_in(Path(request["helper"]), request["system"])
    log = (request_path.parent / "installer.log").open("xb")
    options = {"creationflags": 0x00000008 | 0x00000200} if os.name == "nt" else {"start_new_session": True}
    try:
        return subprocess.Popen([str(executable), "--install-update", str(request_path)],
                                cwd=request_path.parent, stdin=subprocess.DEVNULL,
                                stdout=log, stderr=log, **options)
    finally:
        log.close()


def install_transaction(request_path: Path, *, wait_seconds=120, startup_seconds=90) -> int:
    """External-helper entrypoint. Never kills an app or deletes a backup."""
    request = read_request(request_path)
    folder = request_path.parent
    target, candidate, backup, failed = (Path(request[k]) for k in ("target", "candidate", "backup", "failed"))
    if installation_root() != Path(request["helper"]):
        raise ValueError("The installer must run from its external app copy.")
    lock = target.with_name("." + target.name + ".update-lock")
    with lock.open("x") as handle:
        handle.write(str(request_path))
    parent = None
    moved_old = False
    parent_exited = False
    launched = None
    try:
        parent = ParentWait(int(request["parent_pid"]))
        write_new(folder / "ready.json", {"token": request["token"], "pid": os.getpid()})
        deadline = time.monotonic() + wait_seconds
        while not parent.exited():
            if (folder / "cancel").exists():
                write_new(folder / "result.json", {"status": "cancelled", "target_untouched": True})
                return 1
            if time.monotonic() > deadline:
                raise TimeoutError("Digifly did not close. Installation was not started.")
            time.sleep(0.2)
        parent_exited = True
        # Both trees are rechecked after the original has released its files.
        if tree_inventory(target) != request["original"] or validate_app(candidate, request["system"]) != request["candidate_inventory"]:
            raise ValueError("App files changed after update staging. Installation refused.")
        if backup.exists() or failed.exists():
            raise ValueError("The rollback destination already exists.")
        target.rename(backup)
        moved_old = True
        candidate.rename(target)
        with (folder / "relaunch.log").open("xb") as log:
            launched = subprocess.Popen([str(executable_in(target, request["system"])), "--finish-update", str(request_path)],
                                        cwd=folder, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        deadline = time.monotonic() + startup_seconds
        while time.monotonic() < deadline:
            ack = folder / "started.json"
            if ack.is_file():
                acknowledgement = json.loads(ack.read_text("utf-8"))
                expected_exe = executable_in(Path("."), request["system"]).as_posix()
                if (acknowledgement.get("token") == request["token"] and acknowledgement.get("pid") == launched.pid
                        and acknowledgement.get("executable_sha256") == request["candidate_inventory"][expected_exe]["sha256"]
                        and acknowledgement.get("draft_sha256") == request["draft_sha256"]
                        and acknowledgement.get("window_ready") is True and acknowledgement.get("draft_restored") is True):
                    write_new(folder / "result.json", {"status": "installed", "target": str(target),
                              "backup": str(backup), "startup": acknowledgement, "draft_sha256": request["draft_sha256"]})
                    return 0
            if launched.poll() is not None:
                raise RuntimeError("The new Digifly app exited before its window and saved draft were ready.")
            time.sleep(0.2)
        raise TimeoutError("The new app has not acknowledged startup. Its backup is retained.")
    except Exception as exc:
        status = "not_installed"
        if moved_old:
            if launched is not None and launched.poll() is None:
                # Do not move or kill a live GUI. Retain both trees for recovery.
                status = "recovery_required_live_candidate"
            else:
                if target.exists():
                    target.rename(failed)
                backup.rename(target)
                status = "rolled_back"
                with (folder / "rollback-relaunch.log").open("xb") as log:
                    subprocess.Popen([str(executable_in(target, request["system"])), "--update-rolled-back", str(request_path)],
                                     cwd=folder, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        elif parent_exited and executable_in(target, request["system"]).is_file():
            with (folder / "unchanged-relaunch.log").open("xb") as log:
                subprocess.Popen([str(executable_in(target, request["system"])), "--update-rolled-back", str(request_path)],
                                 cwd=folder, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        write_new(folder / "result.json", {"status": status, "error": str(exc), "target": str(target),
                                          "backup": str(backup), "failed_candidate": str(failed)})
        return 1
    finally:
        if parent:
            parent.close()
        lock.unlink(missing_ok=True)


def acknowledge_startup(request_path: Path, *, project_path: Path) -> None:
    request = read_request(request_path)
    if installation_root() != Path(request["target"]) or project_path.resolve() != Path(request["draft"]):
        raise ValueError("The updated app did not restore the saved update draft.")
    write_new(request_path.parent / "started.json", {"token": request["token"], "pid": os.getpid(),
              "executable_sha256": file_hash(running_executable()), "draft_sha256": file_hash(project_path),
              "window_ready": True, "draft_restored": True})
