"""Preserve Qt's versioned full PDF/PDFium attribution pages as offline notices."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import urllib.request


PAGES = ("qtpdf-licensing", "qtpdf-3rdparty-abseil", "qtpdf-3rdparty-freetype",
         "qtpdf-3rdparty-pdfium", "qtpdf-3rdparty-the-chromium-project", "qtpdf-3rdparty-fast-float",
         "qtpdf-3rdparty-harfbuzz-ng", "qtpdf-3rdparty-icu", "qtpdf-3rdparty-libjpeg-turbo",
         "qtpdf-3rdparty-libpng", "qtpdf-3rdparty-zlib")


def collect(destination):
    destination.mkdir(parents=True, exist_ok=False)
    def download(page):
        url = "https://doc.qt.io/qt-6.10/" + page + ".html"
        req = urllib.request.Request(url, headers={"User-Agent": "Digifly-attribution-review"})
        with urllib.request.urlopen(req, timeout=30) as response:
            data = response.read()
        if b"Qt 6.10.3" not in data or b"Copyright" not in data or len(data) < 4000:
            raise ValueError("Unexpected/incomplete Qt notice page: " + page)
        name = page + ".html"
        (destination / name).write_bytes(data)
        return {"file": name, "source_url": url, "sha256": hashlib.sha256(data).hexdigest()}
    with ThreadPoolExecutor(max_workers=4) as pool:
        notices = list(pool.map(download, PAGES))
    result = {"schema": "digifly-extra-license-notices-v1", "component": "Qt PDF and incorporated PDFium dependencies",
              "version": "6.10.3", "license": "LGPL-3.0 and incorporated licenses reproduced in full",
              "scope": "Full offline copies of the exact Qt6.10.3 PDF licensing and all10third-party attribution pages linked by its licensing page. Includes copyrights/conditions, not a new PDF implementation or acceptance claim.",
              "notices": notices}
    (destination / "notices_manifest.json").write_text(json.dumps(result, indent=2) + "\n", "utf-8")
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("destination", type=Path)
    print(json.dumps(collect(p.parse_args().destination), indent=2))
