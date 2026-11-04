from pathlib import Path
import pytest
from scripts.release_version import read, validate
from scripts.stage_windows_source import inputs
from digifly_app.core.updates import _version_key


@pytest.mark.parametrize("value", ["0.2.0", "0.2.1", "1.0.0", "10.12.3"])
def test_short_versions(value):
    assert validate(value) == value


@pytest.mark.parametrize("value", ["0.1.0-alpha.20261102.10", "20261103.6", "0.2", "01.2.0", "0.2.0/evil", "0.2.0\n"])
def test_dates_and_unsafe_names_refused(value):
    with pytest.raises(ValueError, match="short three-part"):
        validate(value)


def test_same_version_file_in_both_builders_and_normal_stage(tmp_path):
    (tmp_path / "VERSION").write_text("0.2.0\n")
    assert read(tmp_path) == "0.2.0"
    assert read(tmp_path, "0.2.1") == "0.2.1"
    root = Path(__file__).resolve().parents[1]
    declared = {path.relative_to(root).as_posix() for path in inputs(root)}
    assert {"VERSION", "scripts/release_version.py"} <= declared
    for file in ("scripts/build_macos.sh", "scripts/build_windows.ps1"):
        assert "release_version.py" in (root / file).read_text()


def test_legacy_updates_and_short_version_order():
    assert _version_key("0.2.0", "1") > _version_key("0.1.0", "20261103.6")
    assert _version_key("0.2.0", "1") > _version_key("0.1.0-alpha.20261102.10", "102")
    assert _version_key("0.2.1") > _version_key("0.2.0", "1")


def test_current_windows_workflow_reads_the_shared_version():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/windows-neuron-bundle.yml").read_text()
    assert "DIGIFLY_RELEASE_VERSION: 0." not in workflow
    assert "python scripts/release_version.py --source ." in workflow
    assert '"DIGIFLY_RELEASE_VERSION=$version"' in workflow
