"""Consent-gated, offline, private NEURON installation; no registry/global PATH."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import uuid
import zipfile

from .paths import resource_path
from .process_environment import sanitized_external_environment

NEURON_VERSION = "9.0.2"
INSTALLER_SHA256 = "f5bb285c9efa330f4d1ae67885755443b363e829c50e9ffa15f6b9cba44114ce"
RUNTIME_ID = "neuron-9.0.2-cpython-3.12.10-win64-v1"
MANIFEST = "DIGIFLY_NEURON_RUNTIME.json"


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def bundle_directory() -> Path:
    return resource_path("installers", "neuron")


def included_neuron_available() -> bool:
    return os.name == "nt" and (bundle_directory() / MANIFEST).is_file()


def default_install_root() -> Path:
    base = os.environ.get("LOCALAPPDATA", "").strip()
    if not base:
        raise ValueError("Windows LOCALAPPDATA is unavailable; no installation attempted")
    return Path(base) / "Digifly" / "Runtimes" / RUNTIME_ID


def validate_manifest(directory: Path) -> dict:
    data = json.loads((directory / MANIFEST).read_text("utf-8"))
    if (data.get("schema") != "digifly-offline-neuron-v1" or data.get("runtime_id") != RUNTIME_ID
            or data.get("neuron_installer_sha256") != INSTALLER_SHA256):
        raise ValueError("Included NEURON has an unknown source/version identity")
    for name, record in data["artifacts"].items():
        if Path(name).name != name or not isinstance(record.get("bytes"), int):
            raise ValueError("Unsafe NEURON installation artifact")
        path = directory / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size != record["bytes"] or file_hash(path) != record["sha256"]:
            raise ValueError("Included NEURON installation file is missing or changed: " + name)
    if not {"runtime.zip", "NEURON-9.0.2-official-setup.exe"} <= set(data["artifacts"]):
        raise ValueError("Included official installer/private payload is missing")
    if data["artifacts"]["NEURON-9.0.2-official-setup.exe"]["sha256"] != INSTALLER_SHA256:
        raise ValueError("Official NEURON installer checksum differs")
    if (not 0 < data.get("uncompressed_bytes", 0) <= 4 * 1024**3
            or sum(r["bytes"] for r in data["files"].values()) != data["uncompressed_bytes"]
            or not {"bin/python.exe", "bin/python312._pth", "bin/Lib/site-packages/sitecustomize.py", "bin/nrniv.exe", "NEURON-PYTHON-PATH-ADAPTATION.json"} <= set(data["files"])):
        raise ValueError("Private runtime expansion size/required files differ")
    return data


def safe_member(info: zipfile.ZipInfo) -> PurePosixPath:
    # ZipInfo normalizes backslashes on Windows. Inspect the original spelling
    # too so a malformed member cannot evade the same cross-platform guard.
    original_name = info.orig_filename
    path = PurePosixPath(original_name)
    if (path.is_absolute() or not path.parts or ".." in path.parts or "\\" in original_name
            or any(":" in part or part.endswith((" ", "."))
                or part.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *("COM"+str(i) for i in range(1,10)), *("LPT"+str(i) for i in range(1,10))}
                for part in path.parts) or stat.S_ISLNK(info.external_attr >> 16)):
        raise ValueError("Unsafe private-runtime archive member: " + info.filename)
    return path


def verify_installed(root: Path, data: dict) -> None:
    for name, record in data["files"].items():
        safe_member(zipfile.ZipInfo(name))
        path = root.joinpath(*PurePosixPath(name).parts)
        if path.is_symlink() or not path.is_file() or path.stat().st_size != record["bytes"] or file_hash(path) != record["sha256"]:
            raise ValueError("Private runtime file changed: " + name)


HEALTH_CODE = r'''
import json, math, sys
try:
 import neuron
except Exception:
 import os, pathlib, ctypes
 hoc=sys.modules.get('neuron.hoc')
 print('DLL_BINDING_DIAGNOSTIC',json.dumps({'environment_home':os.environ.get('NEURONHOME'),
   'loaded_home':str(hoc.HocObject().neuronhome()) if hoc else None,
   'binary_names':[p.name for p in (pathlib.Path(sys.executable).parent.parent/'bin').glob('*.dll')]}))
 raise
from neuron import h
h.load_file('stdrun.hoc')
s=h.Section(name='digifly_install_health_check'); s.L=20; s.diam=20; s.insert('hh')
i=h.IClamp(s(0.5)); i.delay=2; i.dur=10; i.amp=0.1
t=h.Vector().record(h._ref_t); v=h.Vector().record(s(0.5)._ref_v)
h.dt=0.025; h.finitialize(-65); h.continuerun(20)
volts=list(v); times=list(t)
assert neuron.__version__ == '9.0.2'
assert len(volts)==801 and len(times)==801 and abs(times[-1]-20)<1e-8
assert all(math.isfinite(x) for x in volts) and max(volts)>0 and min(volts)>-120
print(json.dumps({'ok':True,'neuron':neuron.__version__,'python':sys.version.split()[0],
 'neuron_origin':neuron.__file__,'samples':len(volts),'duration_ms':times[-1],
 'min_mV':min(volts),'max_mV':max(volts),'scope':'built-in HH installation health check, not a user experiment or paper acceptance'}))
'''


def check_neuron_health(python: Path) -> dict:
    result = subprocess.run([str(python), "-I", "-B", "-c", HEALTH_CODE],
        capture_output=True, text=True, timeout=45,
        env=sanitized_external_environment({"NEURON_MODULE_OPTIONS": "-nogui"}))
    if result.returncode:
        raise ValueError("Private NEURON check failed: " + (result.stdout + result.stderr)[-5000:])
    data = json.loads(result.stdout.strip().splitlines()[-1])
    if data.get("ok") is not True or not Path(data["neuron_origin"]).resolve().is_relative_to(python.parent.parent.resolve()):
        raise ValueError("NEURON did not load from this private installation")
    return data


def install_included_neuron(*, directory: Path | None = None, destination: Path | None = None) -> Path:
    """Caller must obtain installation consent; never touches an existing runtime."""
    if os.name != "nt":
        raise ValueError("The included runtime is Windows x86-64 only")
    directory = directory or bundle_directory()
    destination = destination or default_install_root()
    data = validate_manifest(directory)
    if destination.is_symlink() or (hasattr(destination, "is_junction") and destination.is_junction()):
        raise ValueError("Private runtime destination must not be a link/junction")
    if destination.exists():
        verify_installed(destination, data)
        check_neuron_health(destination / "bin/python.exe")
        return destination / "bin/python.exe"
    if destination.resolve().is_relative_to(directory.resolve().parents[1]):
        raise ValueError("Install the runtime outside the read-only app folder")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(destination.parent).free < data["uncompressed_bytes"] * 2 + 256 * 1024**2:
        raise ValueError("Not enough free disk for a private NEURON installation")
    staging = destination.parent / (destination.name + ".installing-" + uuid.uuid4().hex)
    staging.mkdir()
    # Failed partial installs are retained for diagnosis, never recursively deleted.
    with zipfile.ZipFile(directory / "runtime.zip") as archive:
        names = [i.filename for i in archive.infolist() if not i.is_dir()]
        if len(names) != len(set(n.casefold() for n in names)) or set(names) != set(data["files"]):
            raise ValueError("Private runtime membership differs from its source inventory")
        for info in archive.infolist():
            path = safe_member(info)
            if info.is_dir():
                continue
            record = data["files"][info.filename]
            if info.file_size != record["bytes"]:
                raise ValueError("Private runtime expansion size differs")
            target = staging.joinpath(*path.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output)
    verify_installed(staging, data)
    # Validate at final location: embedded Python and NEURON must be relocatable.
    staging.rename(destination)
    python = destination / "bin/python.exe"
    health = check_neuron_health(python)
    (destination / "digifly-install-receipt.json").write_text(json.dumps({
        "runtime_id": RUNTIME_ID, "payload_sha256": data["artifacts"]["runtime.zip"]["sha256"],
        "health": health, "registry_changed": False, "global_path_changed": False,
        "original_installer_executed": False}, indent=2) + "\n", "utf-8")
    return python
