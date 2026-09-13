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
