"""Collect exact build-environment and Qt release license/copyright notices.

This preserves notices and source links, not legal certification. No private
configuration, credentials, scientific runtime or dataset is inspected/copied.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
from importlib import metadata
import json
from pathlib import Path, PurePosixPath
import sys
import urllib.request


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Digifly-Windows-license-notices"})
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read()


def collect(bundle):
    target = bundle / "licenses"
    if target.exists():
        raise ValueError("Notices destination must be new")
    target.mkdir()
    records = []
    def save(relative, data):
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return {"path": "licenses/" + relative, "sha256": hashlib.sha256(data).hexdigest()}

    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        python_license = Path(sys.base_prefix) / "LICENSE"
    if not python_license.is_file():
        raise ValueError("Python distribution license not found")
    records.append({"component": "CPython", "version": sys.version.split()[0],
                    "license": "PSF-2.0 and incorporated notices", "notices": [save("CPython-LICENSE.txt", python_license.read_bytes())],
                    "source": "https://github.com/python/cpython/tree/v" + sys.version.split()[0]})
    for dist in sorted(metadata.distributions(), key=lambda d: d.metadata.get("Name", "")):
        name = dist.metadata.get("Name", "unknown")
        if name == "digifly-workstation":
            continue
        notices = []
        for file in dist.files or ():
            base = PurePosixPath(str(file)).name.lower()
            if base.startswith(("license", "copying", "notice", "authors")):
                path = dist.locate_file(file)
                if path.is_file() and path.stat().st_size <= 1024 * 1024:
                    relative = str(file).replace("\\", "/").replace("../", "_/")
                    notices.append(save("python-packages/" + name + "-" + dist.version + "/" + relative, path.read_bytes()))
        records.append({"component": name, "version": dist.version,
                        "license": dist.metadata.get("License-Expression") or dist.metadata.get("License") or "see upstream metadata/notices",
                        "project_urls": dist.metadata.get_all("Project-URL", []), "notices": notices,
                        "scope": "Exact build environment; some entries are build/test-only, not an assertion that every distribution ships in the executable"})

    version = metadata.version("PySide6")
    if version != "6.10.3":
        raise ValueError("Qt notice source version needs review for this PySide build")
    downloads = []
    for repo in ("qt/qtbase", "qt/qtsvg", "qt/qtimageformats", "pyside/pyside-setup"):
        tree = json.loads(fetch(f"https://api.github.com/repos/{repo}/git/trees/v{version}?recursive=1"))
        if tree.get("truncated"):
            raise ValueError("Incomplete upstream notice inventory")
        for entry in tree["tree"]:
            path = entry["path"]
            base = PurePosixPath(path).name.lower()
            selected = path.startswith("LICENSES/") or ("3rdparty/" in path and (
                base == "qt_attribution.json" or base.startswith(("license", "copying", "notice"))))
            if entry["type"] == "blob" and selected and entry.get("size", 0) < 1024 * 1024:
                url = f"https://raw.githubusercontent.com/{repo}/v{version}/{path}"
                downloads.append((repo, path, url))
    def download(item):
        repo, path, url = item
        data = fetch(url)
        notice = save("Qt-" + version + "/" + repo + "/" + path, data)
        return {**notice, "source_url": url}
    with ThreadPoolExecutor(max_workers=6) as pool:
        qt_notices = list(pool.map(download, downloads))
    if not qt_notices:
        raise ValueError("Qt license and third-party notices are missing")
    records.append({"component": "Qt/PySide/Shiboken", "version": version,
                    "license_route": "LGPL-3.0 for the dynamically linked UI libraries; preserve alternative licenses and incorporated third-party notices",
                    "source": "https://doc.qt.io/qtforpython-6/licenses.html", "notices": qt_notices,
                    "replacement": "DLLs remain separate in the extracted app folder; modification/replacement and reverse engineering for debugging library modifications are not prohibited by Digifly",
                    "source_obtainment": "Exact v6.10.3 upstream source tags are linked in every collected notice"})
    result = {"schema": "digifly-windows-notice-inventory-v1", "components": records,
              "native_binaries": sorted(path.relative_to(bundle).as_posix() for path in bundle.rglob("*") if path.suffix.lower() in {".dll", ".pyd", ".exe"}),
              "augustin": {"license": "CC-BY-4.0", "article": "https://doi.org/10.1523/ENEURO.0423-18.2019",
                  "code_supplement": "10.1523/ENEURO.0423-18.2019.ed1", "notice": "mechanisms/augustin_2019/NOTICE.md"},
              "scope": "Exact runtime binary census, build dependency metadata, license texts and upstream Qt copyright/attribution records. This is not a legal opinion or native scientific acceptance."}
    (bundle / "THIRD_PARTY_COMPONENTS.json").write_text(json.dumps(result, indent=2) + "\n", "utf-8")
    return {"components": len(records), "qt_notices": len(qt_notices)}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bundle", type=Path, required=True)
    print(json.dumps(collect(p.parse_args().bundle)))
