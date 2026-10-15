from pathlib import Path
import shutil

import pytest

from scripts.stage_pdcm_bionet_assets import stage_assets
from digifly_app.workers.pdcm_bionet_app_worker import SOURCE_PINS, SCAFFOLD

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/digifly_app/pdcm_bionet/primary_v3"


def test_actual_frozen_assets_stage_without_caches(tmp_path):
    package = tmp_path / "candidate/digifly_app"
    package.mkdir(parents=True)
    staged = stage_assets(SOURCE, package)
    expected = set(SOURCE_PINS) | set(SCAFFOLD)
    assert {p.relative_to(staged).as_posix() for p in staged.rglob("*") if p.is_file()} == expected
    assert all((staged / p).read_bytes() == (SOURCE / p).read_bytes() for p in expected)


@pytest.mark.parametrize("corruption", ["missing", "changed"])
def test_invalid_actual_source_fails_before_copy(tmp_path, corruption):
    source = tmp_path / "source"
    shutil.copytree(SOURCE, source)
    item = source / next(iter(SOURCE_PINS))
    if corruption == "missing":
        item.unlink()
    else:
        item.write_bytes(item.read_bytes() + b"\n# changed\n")
    package = tmp_path / "candidate/digifly_app"
    package.mkdir(parents=True)
    with pytest.raises((ValueError, FileNotFoundError)):
        stage_assets(source, package)
    assert not (package / "pdcm_bionet/primary_v3").exists()


def test_builders_stage_before_signing_and_archive():
    mac = (ROOT / "scripts/build_macos.sh").read_text()
    win = (ROOT / "scripts/build_windows.ps1").read_text()
    assert mac.index("scripts/stage_pdcm_bionet_assets.py") < mac.index("codesign --force")
    assert win.index("scripts\\stage_pdcm_bionet_assets.py") < win.index('windows_package.py" archive')
