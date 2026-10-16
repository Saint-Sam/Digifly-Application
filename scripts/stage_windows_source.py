"""Freeze portable application inputs, never the developer workspace or data."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tomllib
import re
from digifly_app.core.bundled_pair_graph import PAIR_PATH, PAIR_NOTICE, stage_pair_file, verify_pair_file


def inputs(root: Path) -> list[Path]:
    names = {".gitattributes", "main.py", "pyproject.toml", "README.md", "LICENSE", "THIRD_PARTY_NOTICES.md",
             "scripts/build_windows.ps1", "scripts/setup_dev.ps1", "scripts/stage_windows_source.py",
             "scripts/windows_package.py", "scripts/collect_windows_notices.py", "scripts/stage_pdcm_bionet_assets.py",
             "scripts/stage_pdcm_bionet_scalable_assets.py", "docs/WINDOWS_TESTER.md"}
    metadata = tomllib.loads((root / "pyproject.toml").read_text("utf-8"))
    for files in metadata["tool"]["setuptools"]["data-files"].values():
        names.update(files)
    for directory in ("src/digifly_app", "mechanisms", "presets", "schemas"):
        for path in (root / directory).rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix in {".py", ".json", ".mod", ".md", ".png", ".ico", ".icns"}:
                names.add(path.relative_to(root).as_posix())
    if (root / PAIR_PATH).exists():
        verify_pair_file(root / PAIR_PATH)
        names.update((PAIR_PATH, PAIR_NOTICE))
    return [root / name for name in sorted(names)]


def stage(source: Path, destination: Path, *, release_snapshot=False, pair_parquet: Path | None = None) -> None:
    source = source.resolve()
    if destination.exists() or destination.is_symlink():
        raise ValueError("Staging destination must be new; existing builds are preserved")
    paths = inputs(source)
    if release_snapshot:
        paths.extend(source / name for name in (
            ".github/workflows/windows-current-release.yml", "scripts/publish_windows_release.py",
            ".github/workflows/windows-neuron-bundle.yml", "scripts/build_windows_neuron_payload.py",
            "tests/test_windows_neuron_bundle.py", "scripts/augment_windows_notices.py", "scripts/collect_qtpdf_notices.py",
            "tests/test_generic_experiment.py",
            "tests/test_app_settings.py",
            "tests/test_swc_library.py", "tests/test_bundled_pair_graph.py", "tests/test_neuprint_acquisition.py",
            "tests/test_neuprint_ui.py", "tests/test_data_library.py", "tests/test_connectomes.py",
            "tests/test_windows_release.py", "tests/test_windows_package.py", "tests/test_package_foundation.py", "tests/test_pair_graph.py",
            "tests/test_stage_pdcm_bionet_assets.py", "scripts/build_macos.sh"))
    records = {}
    for path in paths:
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(source):
            raise ValueError(f"Missing or unsafe source: {path.relative_to(source)}")
        data = path.read_bytes()
        if path.relative_to(source).as_posix() == PAIR_PATH:
            verify_pair_file(path)
        elif len(data) > 2 * 1024 * 1024:
            raise ValueError(f"Unexpected large source: {path.relative_to(source)}")
        if path.suffix in {".py", ".json", ".ps1", ".md", ".yml", ".sh", ".toml"} and re.search(
                rb"(?:ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|-----BEGIN (?:OPENSSH |RSA |EC )?PRIVATE KEY-----|eyJ[A-Za-z0-9_-]{30,}\.[A-Za-z0-9_-]{30,}\.[A-Za-z0-9_-]{20,})", data):
            raise ValueError(f"Possible credential in release input: {path.relative_to(source)}")
        records[path.relative_to(source).as_posix()] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    destination.mkdir(parents=True)
    for path in paths:
        target = destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    if pair_parquet is not None:
        stage_pair_file(pair_parquet, destination)
        for name in (PAIR_PATH, PAIR_NOTICE):
            data = (destination / name).read_bytes()
            records[name] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    (destination / "windows_source_manifest.json").write_text(json.dumps({"schema": "digifly-windows-source-v1", "files": records}, indent=2) + "\n", "utf-8")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--destination", type=Path, required=True)
    p.add_argument("--release-snapshot", action="store_true")
    p.add_argument("--pair-parquet", type=Path, help="Include only the checksum-pinned user-selected MANC file")
    a = p.parse_args()
    stage(a.source, a.destination, release_snapshot=a.release_snapshot, pair_parquet=a.pair_parquet)
