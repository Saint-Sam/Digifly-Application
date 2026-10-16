"""Create a private relocatable runtime from official Windows NEURON/Python files.

Runs only on disposable Windows CI. The upstream NSIS installer is extracted,
not executed: its registry/environment/association actions are never applied.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from digifly_app.core.windows_neuron_bundle import INSTALLER_SHA256, MANIFEST, RUNTIME_ID, file_hash, safe_member

NRN_URL = "https://github.com/neuronsimulator/nrn/releases/download/9.0.2/nrn-9.0.2.w64-mingw-py-310-311-312-313-314-setup.exe"
PY_URL = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip"
PY_SHA = "4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3"

# Exact wheels already delivered for RUNTIME_ID v1. A new transitive release
# must not silently change that installation identity or reject an old install.
LOCKED_WHEELS = {
    "contourpy-1.4.0-cp312-cp312-win_amd64.whl": "6e697d94e69f499ff6bebb899cae97a58d5d14f0e1fe9568b43a0248d2f9af8c",
    "cycler-0.12.1-py3-none-any.whl": "85cef7cff222d8644161529808465972e51340599459b8ac3ccbac5a854e0d30",
    "duckdb-1.5.2-cp312-cp312-win_amd64.whl": "8d599758b4e48bf12e18c9b960cf491d219f0c4972d19a45489c05cc5ab36f83",
    "fonttools-4.67.0-cp312-cp312-win_amd64.whl": "2bfab2f5d1d255dec82f4bd082a1c10e77df808e42210890f50a9c30bf91570e",
    "kiwisolver-1.5.1-cp312-cp312-win_amd64.whl": "06a6917674de9e0fe3f66f5430787f59a9f2ddb64af9b714eaec547e29ef5c19",
    "matplotlib-3.10.7-cp312-cp312-win_amd64.whl": "0d8c32b7ea6fb80b1aeff5a2ceb3fb9778e2759e899d9beff75584714afcc5ee",
    "numpy-2.2.6-cp312-cp312-win_amd64.whl": "c1f9540be57940698ed329904db803cf7a402f3fc200bfe599334c9bd84a40b2",
    "packaging-26.3-py3-none-any.whl": "d7193f7c8e4e93f444fde0262bf90af30e16fa0ad0ad44cb553c87339b23cd1c",
    "pillow-12.3.0-cp312-cp312-win_amd64.whl": "a2b55dd6b2a4c4b7d87ffa56bdb33fdc5fdb9a462173861a7bc097f17d91cb09",
    "pyparsing-3.3.3-py3-none-any.whl": "ece8c00a69cf01b45d0b1dedabb469c90d8caf996d4fda40f147627a122849a4",
    "python_dateutil-2.9.0.post0-py2.py3-none-any.whl": "a8b2bc7bffae282281c8140a97d3aa9c14da0b136dfe83f850eea9a5f7470427",
    "six-1.17.0-py2.py3-none-any.whl": "4721f391ed90541fddacab5acf947aa0d3dc7d27b2e1e8eda2be8970586c3274",
}


def validate_wheels(directory):
    files = {p.name: p for p in directory.iterdir()}
    if set(files) != set(LOCKED_WHEELS):
        raise ValueError("Private runtime wheel membership differs from its locked identity")
    for name, expected in LOCKED_WHEELS.items():
        if files[name].is_symlink() or file_hash(files[name]) != expected:
            raise ValueError("Private runtime wheel checksum differs: " + name)

BOOTSTRAP = '''# Digifly private binding; no system registry, global PATH or foreign packages.
import os
import ctypes
from pathlib import Path
_base = Path(__file__).resolve().parents[3]
_nrn = _base
os.environ['NEURONHOME'] = str(_nrn)
os.environ['NRNHOME'] = str(_nrn)
# CPython uses UCRT while the official MinGW NEURON DLLs can use MSVCRT.
# Synchronize these process-local CRT environments, not registry/global state.
for _crt_name in ('msvcrt', 'ucrtbase'):
    _putenv = ctypes.CDLL(_crt_name)._putenv_s
    _putenv.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
    _putenv.restype = ctypes.c_int
    for _key in ('NEURONHOME', 'NRNHOME'):
        if _putenv(_key.encode('ascii'), str(_nrn).replace('\\\\','/').encode('utf-8')):
            raise RuntimeError('Unable to bind private NEURON CRT environment')
os.environ.setdefault('NEURON_MODULE_OPTIONS', '-nogui')
_dll_handles = [os.add_dll_directory(str(p)) for p in (_nrn/'bin',_nrn/'lib/python/neuron') if p.is_dir()]
os.environ['PATH'] = str(_nrn/'bin') + os.pathsep + str(_nrn/'mingw/usr/bin') + os.pathsep + os.environ.get('PATH','')
'''


def fetch(url, destination, sha=None):
    req = urllib.request.Request(url, headers={"User-Agent": "Digifly-official-runtime-bundle"})
    with urllib.request.urlopen(req, timeout=120) as response, destination.open("xb") as stream:
        shutil.copyfileobj(response, stream)
    if sha and file_hash(destination) != sha:
        raise ValueError("Official source checksum mismatch: " + destination.name)


def extract_zip(archive, destination):
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            path = safe_member(info)
            if info.is_dir():
                continue
            target = destination.joinpath(*path.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as source, target.open("xb") as stream:
                shutil.copyfileobj(source, stream)


def build(destination, work):
    if sys.platform != "win32":
        raise ValueError("Build on the native disposable Windows runner")
    destination.mkdir(parents=True, exist_ok=False)
    work.mkdir(parents=True, exist_ok=False)
    installer = destination / "NEURON-9.0.2-official-setup.exe"
    fetch(NRN_URL, installer, INSTALLER_SHA256)
    python_zip = work / "python-embed.zip"
    fetch(PY_URL, python_zip, PY_SHA)
    sevenzip = shutil.which("7z") or r"C:\Program Files\7-Zip\7z.exe"
    unpacked = work / "official-extracted"
    subprocess.run([sevenzip, "x", str(installer), "-o" + str(unpacked), "-y"], check=True, capture_output=True, timeout=180)
    candidates = [p.parent.parent for p in unpacked.rglob("nrniv.exe") if p.parent.name.casefold() == "bin"]
    if len(candidates) != 1:
        raise ValueError("Official installer layout is ambiguous: " + str(candidates))
    payload = work / "payload"
    payload.mkdir()
    # NEURON's Windows core derives neuronhome from the executable's ../bin
    # layout (src/oc/mswinprt.cpp:setneuronhome). Retain that official layout.
    shutil.copytree(candidates[0], payload, dirs_exist_ok=True, ignore=shutil.ignore_patterns("$PLUGINSDIR"))
    py_root = payload / "bin"
    extract_zip(python_zip, py_root)
    site = py_root / "Lib/site-packages"
    site.mkdir(parents=True, exist_ok=True)
    wheels = work / "wheels"
    wheels.mkdir()
    requirements = ["==".join(name.split("-")[:2]) for name in sorted(LOCKED_WHEELS)]
    subprocess.run([sys.executable, "-m", "pip", "download", "--only-binary=:all:", "--no-deps", "--dest", str(wheels),
        *requirements], check=True, timeout=300)
    validate_wheels(wheels)
    wheel_records = []
    for wheel in sorted(wheels.glob("*.whl")):
        extract_zip(wheel, site)
        wheel_records.append({"name": wheel.name, "sha256": file_hash(wheel), "source": "https://pypi.org/"})
    (py_root / "python312._pth").write_text("python312.zip\n.\nLib/site-packages\n../lib/python\nimport site\n", "ascii")
    (site / "sitecustomize.py").write_text(BOOTSTRAP, "utf-8")
    init = payload / "lib/python/neuron/__init__.py"
    original = init.read_bytes()
    old = b'path = os.path.join(n.neuronhome().replace("/", "\\\\"), b)'
    new = b'path = os.path.join(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../..")), b)'
    if original.count(old) != 1:
        raise ValueError("Official Windows DLL lookup source differs; do not patch blindly")
    adapted = original.replace(old, new)
    init.write_bytes(adapted)
    (payload / "NEURON-PYTHON-PATH-ADAPTATION.json").write_text(json.dumps({
        "source": "https://github.com/neuronsimulator/nrn/blob/9.0.2/share/lib/python/neuron/__init__.py",
        "original_sha256": hashlib.sha256(original).hexdigest(), "adapted_sha256": hashlib.sha256(adapted).hexdigest(),
        "change": "One DLL lookup path in nrn_dll_sym_nt: use the loaded module's own official-layout root rather than Cygwin neuronhome, which ctypes cannot open as a native Windows path. sitecustomize also synchronizes process-local MSVCRT/UCRT NEURONHOME for HOC libraries. DLLs, mechanisms, equations, parameters and integration are unchanged.",
        "original_line": old.decode(), "adapted_line": new.decode(), "author": "Digifly Windows packaging adaptation,2026-10-15"
    }, indent=2) + "\n", "utf-8")
    fetch("https://raw.githubusercontent.com/neuronsimulator/nrn/9.0.2/Copyright", payload / "NEURON-Copyright.txt")
    notices = {"neuron": {"url": NRN_URL, "sha256": INSTALLER_SHA256, "license": "BSD-3-Clause with incorporated notices retained in official payload",
        "source": "https://github.com/neuronsimulator/nrn/tree/9.0.2"},
        "python": {"url": PY_URL, "sha256": PY_SHA, "license": "PSF and incorporated notices; LICENSE.txt retained",
        "source": "https://github.com/python/cpython/tree/v3.12.10"}, "wheels": wheel_records,
        "scope": "Private NEURON/CPython/NumPy/Matplotlib/DuckDB only, not BioNet/NetPyNE/Arbor or paper acceptance.7zip is a CI extraction tool and not redistributed. Upstream NSIS registry/shortcut/PATH sections never executed."}
    (payload / "UPSTREAM-SOURCES.json").write_text(json.dumps(notices, indent=2) + "\n", "utf-8")
    files = {p.relative_to(payload).as_posix(): {"sha256": file_hash(p), "bytes": p.stat().st_size}
             for p in sorted(payload.rglob("*")) if p.is_file()}
    archive = destination / "runtime.zip"
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for name in files:
            z.write(payload / name, name)
    manifest = {"schema": "digifly-offline-neuron-v1", "runtime_id": RUNTIME_ID,
        "neuron_installer_sha256": INSTALLER_SHA256, "upstream": notices,
        "uncompressed_bytes": sum(r["bytes"] for r in files.values()), "files": files,
        "artifacts": {p.name: {"sha256": file_hash(p), "bytes": p.stat().st_size} for p in (installer, archive)}}
    (destination / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", "utf-8")
    print(json.dumps({"files": len(files), "uncompressed_bytes": manifest["uncompressed_bytes"], "artifacts": manifest["artifacts"]}))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--destination", type=Path, required=True)
    p.add_argument("--work", type=Path, required=True)
    a = p.parse_args()
    build(a.destination, a.work)
