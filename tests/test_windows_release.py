from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_windows_build_packages_and_asserts_simulator_workers() -> None:
    script = (ROOT / "scripts" / "build_windows.ps1").read_text("utf-8")
    assert 'Get-ChildItem "src\\digifly_app\\workers\\*.py"' in script
    for worker in (
        "generic_experiment_worker.py",
        "arbor_escape_siz_worker.py",
        "bmtk_bionet_worker.py",
        "escape_siz_worker.py",
    ):
        assert worker in script
    assert "Packaged simulator worker is missing" in script
