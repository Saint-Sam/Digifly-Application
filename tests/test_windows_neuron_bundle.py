"""Offline installation confinement and provenance guards; no fake simulation pass."""
import json
from pathlib import Path
import stat
import zipfile
import pytest
from digifly_app.core import windows_neuron_bundle as bundle


@pytest.mark.parametrize("name", ["../bad", "/bad", "C:/bad", "safe/../bad", "safe\\bad", "safe/ads:bad"])
def test_archive_traversal_refused(name):
    with pytest.raises(ValueError, match="Unsafe"):
        bundle.safe_member(zipfile.ZipInfo(name))


def test_link_refused():
    info = zipfile.ZipInfo("link")
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with pytest.raises(ValueError, match="Unsafe"):
        bundle.safe_member(info)


def test_installed_mutation_is_not_silently_repaired(tmp_path):
    file = tmp_path / "python/python.exe"
    file.parent.mkdir()
    file.write_bytes(b"original-software-fixture")
    data = {"files": {"python/python.exe": {"sha256": bundle.file_hash(file), "bytes": file.stat().st_size}}}
    bundle.verify_installed(tmp_path, data)
    file.write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        bundle.verify_installed(tmp_path, data)
    assert file.read_bytes() == b"changed"


def test_wrong_upstream_version_refused_before_payload(tmp_path):
    (tmp_path / bundle.MANIFEST).write_text(json.dumps({"schema": "digifly-offline-neuron-v1", "runtime_id": "wrong"}))
    with pytest.raises(ValueError, match="identity"):
        bundle.validate_manifest(tmp_path)


def test_no_platform_install_on_mac(tmp_path):
    if bundle.os.name == "nt":
        pytest.skip("native Windows installation exercised by actual payload/UI CI, not this non-Windows negative case")
    with pytest.raises(ValueError, match="Windows"):
        bundle.install_included_neuron(directory=tmp_path, destination=tmp_path / "new")
    assert not (tmp_path / "new").exists()
