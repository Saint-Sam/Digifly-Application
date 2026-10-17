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
    monkeypatch.setattr(updates, "_read_json", lambda url: {"assets": list(_release_asset("0.1.0-alpha.1"))})
    monkeypatch.setattr(updates, "platform_asset_fragment", lambda: "Windows-x86_64.zip")
    monkeypatch.setattr(updates, "build_info", lambda: {"version": "0.1.0", "build": "20261016.4"})
    assert not updates.check_for_update().available


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
