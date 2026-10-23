from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from digifly_app.core import updates


def test_update_cache_is_outside_workspace(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(updates.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    cache = updates.update_cache_root()
    assert "Caches" in cache.parts
    assert "Digifly Workstation Workspace" not in cache.parts


def test_new_release_discovery_and_download_use_github_digest_without_sidecar(monkeypatch, tmp_path):
    payload = b'checked package'
    item, _ = _release_asset('0.1.0-alpha.20261023.5')
    item['digest'] = 'sha256:' + hashlib.sha256(payload).hexdigest()
    draft, _ = _release_asset('9.9.9-alpha.20990101.1')
    monkeypatch.setattr(updates, '_read_json', lambda url: [
        {'draft': True, 'assets': [draft]}, {'draft': False, 'assets': [item]}])
    monkeypatch.setattr(updates, 'platform_asset_fragment', lambda: 'Windows-x86_64.zip')
    monkeypatch.setattr(updates, 'build_info', lambda: {'version': '0.1.0-alpha.20261023.4'})
    result = updates.check_for_update()
    assert result.available and result.asset.name == item['name'] and result.asset.checksum_url == ''
    monkeypatch.setattr(updates, 'update_cache_root', lambda: tmp_path)
    requested = []
    def download(url, destination, **kwargs):
        requested.append(url)
        destination.write_bytes(payload)
    monkeypatch.setattr(updates, '_download', download)
    assert updates.download_verified_update(result.asset).read_bytes() == payload
    assert requested == [item['browser_download_url']]
    assert not list(tmp_path.rglob('*.sha256'))


@pytest.mark.parametrize('digest', ['', 'sha1:' + '0' * 40, 'sha256:bad', 'sha256:' + 'A' * 64])
def test_missing_or_invalid_digest_cannot_enable_sidecar_free_update(monkeypatch, digest):
    item, _ = _release_asset('0.1.0-alpha.20261023.5')
    item['digest'] = digest
    monkeypatch.setattr(updates, '_read_json', lambda url: {'assets': [item]})
    monkeypatch.setattr(updates, 'platform_asset_fragment', lambda: 'Windows-x86_64.zip')
    monkeypatch.setattr(updates, 'build_info', lambda: {'version': '0.1.0-alpha.20261023.4'})
    assert not updates.check_for_update().available


def test_github_digest_mismatch_rejects_archive_without_sidecar(monkeypatch, tmp_path):
    monkeypatch.setattr(updates, 'update_cache_root', lambda: tmp_path)
    monkeypatch.setattr(updates, '_download', lambda url, destination, **kw: destination.write_bytes(b'changed'))
    asset = updates.UpdateAsset('app.zip', 'archive', '', '', digest='sha256:' + '0' * 64)
    with pytest.raises(ValueError, match='integrity'):
        updates.download_verified_update(asset)
    assert not list(tmp_path.rglob('*.partial')) and not list(tmp_path.rglob('*.zip'))


def test_verified_download_rejects_bad_checksum(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(updates, "update_cache_root", lambda: tmp_path)

    def fake_download(url: str, destination: Path, *, maximum: int) -> None:
        destination.write_text("0" * 64 if url.endswith("sha") else "payload")

    monkeypatch.setattr(updates, "_download", fake_download)
    asset = updates.UpdateAsset("app.zip", "archive", "sha", "2026-01-01T00:00:00Z")
    with pytest.raises(ValueError, match="integrity"):
        updates.download_verified_update(asset)
    assert not (tmp_path / "app.zip.partial").exists()


def test_verified_download_accepts_matching_checksum(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(updates, "update_cache_root", lambda: tmp_path)
    payload = b"safe update"

    def fake_download(url: str, destination: Path, *, maximum: int) -> None:
        if url == "sha":
            destination.write_text(hashlib.sha256(payload).hexdigest() + "  app.zip")
        else:
            destination.write_bytes(payload)

    monkeypatch.setattr(updates, "_download", fake_download)
    asset = updates.UpdateAsset("app.zip", "archive", "sha", "2026-01-01T00:00:00Z")
    assert updates.download_verified_update(asset).read_bytes() == payload


def _release_asset(version, timestamp="2026-10-16T23:00:00Z"):
    name = f"Digifly-Workstation-{version}-Windows-x86_64.zip"
    return ({"name": name, "browser_download_url": "https://github.com/example/" + name, "updated_at": timestamp},
            {"name": name + ".sha256", "browser_download_url": "https://github.com/example/" + name + ".sha256"})


@pytest.mark.parametrize("reverse", [False, True])
def test_selection_uses_newest_build_not_API_order_or_asset_edit_time(monkeypatch, reverse):
    assets = [*_release_asset("0.1.0-alpha.20261015.1", "2026-10-18T00:00:00Z"),
              *_release_asset("0.1.0-alpha.1", "2026-10-19T00:00:00Z"),
              *_release_asset("0.1.0-alpha.20261016.1")]
    if reverse:
        assets.reverse()
    monkeypatch.setattr(updates, "_read_json", lambda url: {"assets": assets})
    monkeypatch.setattr(updates, "platform_asset_fragment", lambda: "Windows-x86_64.zip")
    monkeypatch.setattr(updates, "build_info", lambda: {"version": "0.1.0-alpha.20261015.2"})
    result = updates.check_for_update()
    assert result.available and result.asset.version == "0.1.0-alpha.20261016.1"


@pytest.mark.parametrize("local", ["0.1.0-alpha.20261016.1", "0.1.0-alpha.20261017.1"])
def test_equal_or_newer_local_build_is_not_offered_an_update(monkeypatch, local):
    monkeypatch.setattr(updates, "_read_json", lambda url: {"assets": list(_release_asset("0.1.0-alpha.20261016.1"))})
    monkeypatch.setattr(updates, "platform_asset_fragment", lambda: "Windows-x86_64.zip")
    monkeypatch.setattr(updates, "build_info", lambda: {"version": local})
    assert not updates.check_for_update().available


def test_private_Mac_date_build_never_downgrades_to_legacy_public_asset(monkeypatch):
    assets = [{**item, "name": item["name"].replace("Windows-x86_64", "macOS-arm64")} for item in _release_asset("0.1.0-alpha.1")]
    monkeypatch.setattr(updates, "_read_json", lambda url: {"assets": assets})
    monkeypatch.setattr(updates, "platform_asset_fragment", lambda: "macOS-arm64.zip")
    monkeypatch.setattr(updates, "build_info", lambda: {"version": "0.1.0", "build": "20261016.4"})
    assert not updates.check_for_update().available


def test_missing_build_metadata_fails_closed_even_for_recent_asset(monkeypatch):
    monkeypatch.setattr(updates, "_read_json", lambda url: {"assets": list(_release_asset("0.1.0-alpha.1", "2099-01-01T00:00:00Z"))})
    monkeypatch.setattr(updates, "platform_asset_fragment", lambda: "Windows-x86_64.zip")
    monkeypatch.setattr(updates, "build_info", lambda: {})
    status = updates.check_for_update()
    assert not status.available and status.asset is None and "could not be verified" in status.reason


def test_mac_metadata_fallback_reads_loaded_app_not_source(tmp_path, monkeypatch):
    import plistlib
    from digifly_app.core import paths, update_installer
    app = tmp_path / "Current.app"
    (app / 'Contents').mkdir(parents=True)
    (app / 'Contents/Info.plist').write_bytes(plistlib.dumps({"CFBundleShortVersionString": "0.1.0", "CFBundleVersion": "20261019.1"}))
    monkeypatch.setattr(paths, "resource_path", lambda name: tmp_path / 'missing.json')
    monkeypatch.setattr(update_installer, 'installation_root', lambda: app)
    monkeypatch.setattr(updates.platform, 'system', lambda: 'Darwin')
    assert updates.build_info() == {"version": "0.1.0", "build": "20261019.1"}


@pytest.mark.parametrize("name", ["../app.zip", "/tmp/app.zip", "C:\\app.zip", "app:stream.zip"])
def test_unsafe_update_filename_refused_before_network(monkeypatch, name):
    monkeypatch.setattr(updates, "_download", lambda *args, **kwargs: pytest.fail("unsafe download attempted"))
    with pytest.raises(ValueError, match="unsafe filename"):
        updates.download_verified_update(updates.UpdateAsset(name, "archive", "sha", ""))


def test_update_checksum_must_name_selected_archive(monkeypatch, tmp_path):
    monkeypatch.setattr(updates, "update_cache_root", lambda: tmp_path)
    monkeypatch.setattr(updates, "_download", lambda url, destination, **kw: destination.write_text("0" * 64 + "  wrong.zip"))
    with pytest.raises(ValueError, match="identify"):
        updates.download_verified_update(updates.UpdateAsset("app.zip", "archive", "sha", ""))


@pytest.mark.parametrize("url", ["file:///tmp/app.zip", "http://github.com/Saint-Sam/Digifly-Application/releases/download/v/app.zip", "https://example.org/app.zip", "https://github.com/unrelated/project/releases/download/v/app.zip"])
def test_nonofficial_update_origin_is_rejected_before_open(monkeypatch, tmp_path, url):
    monkeypatch.setattr(updates, "urlopen", lambda *a, **kw: pytest.fail("untrusted origin opened"))
    with pytest.raises(ValueError, match="official"):
        updates._download(url, tmp_path / "app.zip", maximum=1024)
