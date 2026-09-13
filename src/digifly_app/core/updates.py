"""Release update discovery and integrity checks.

Updates are staged in the operating-system cache.  The Digifly workspace is
deliberately not accepted as an update destination, so runs and data libraries
cannot be replaced by this subsystem.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import platform
import tempfile
from typing import Any
from urllib.request import Request, urlopen


RELEASE_TAG = "v0.1.0-alpha.1"
RELEASE_API = (
    "https://api.github.com/repos/Saint-Sam/Digifly-Application/releases/tags/"
    + RELEASE_TAG
)
MAX_METADATA_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class UpdateAsset:
    name: str
    url: str
    checksum_url: str
    updated_at: str


@dataclass(frozen=True)
class UpdateStatus:
    available: bool
    asset: UpdateAsset | None
    reason: str


def build_info() -> dict[str, Any]:
    """Read immutable metadata embedded into a packaged build."""

    try:
        from digifly_app.core.paths import resource_path

        value = json.loads(resource_path("digifly_build.json").read_text("utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def platform_asset_fragment() -> str:
    system = platform.system()
    machine = platform.machine().lower()
    if system == "Windows":
        return "Windows-x86_64.zip"
    if system == "Darwin":
        architecture = "arm64" if machine in {"arm64", "aarch64"} else "x86_64"
        return f"macOS-{architecture}.zip"
    raise RuntimeError(f"Automatic updates are not yet packaged for {system}.")


def _read_json(url: str) -> dict[str, Any]:
    request = Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "Digifly-Workstation-Updater"})
    with urlopen(request, timeout=15) as response:
        payload = response.read(MAX_METADATA_BYTES + 1)
    if len(payload) > MAX_METADATA_BYTES:
        raise ValueError("The update response was unexpectedly large.")
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError("The update response was not an object.")
    return value


def check_for_update(*, release_api: str = RELEASE_API) -> UpdateStatus:
    release = _read_json(release_api)
    fragment = platform_asset_fragment()
    assets = release.get("assets", [])
    if not isinstance(assets, list):
        raise ValueError("The release asset list is invalid.")
    selected = next((item for item in assets if isinstance(item, dict) and str(item.get("name", "")).endswith(fragment)), None)
    if selected is None:
        return UpdateStatus(False, None, f"No {fragment} update is published yet.")
    checksum = next((item for item in assets if isinstance(item, dict) and item.get("name") == f"{selected.get('name')}.sha256"), None)
    if checksum is None:
        return UpdateStatus(False, None, "The update exists but its checksum is missing.")
    asset = UpdateAsset(
        name=str(selected["name"]),
        url=str(selected["browser_download_url"]),
        checksum_url=str(checksum["browser_download_url"]),
        updated_at=str(selected.get("updated_at", "")),
    )
    current = str(build_info().get("built_at", ""))
    available = not current or asset.updated_at > current
    return UpdateStatus(available, asset, "Update available." if available else "This build is current.")


def update_cache_root() -> Path:
    """Return a cache path that is never inside the user's Digifly workspace."""

    if platform.system() == "Windows":
        base = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir()))
    elif platform.system() == "Darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "Digifly Workstation" / "updates"


def _download(url: str, destination: Path, *, maximum: int) -> None:
    request = Request(url, headers={"User-Agent": "Digifly-Workstation-Updater"})
    with urlopen(request, timeout=60) as response, destination.open("wb") as output:
        total = 0
        while chunk := response.read(1024 * 1024):
            total += len(chunk)
            if total > maximum:
                raise ValueError("The update download exceeded its safety limit.")
            output.write(chunk)


def download_verified_update(asset: UpdateAsset) -> Path:
    """Download an update to cache and reject it unless SHA-256 matches."""

    root = update_cache_root()
    root.mkdir(parents=True, exist_ok=True)
    archive = root / asset.name
    checksum_file = root / f"{asset.name}.sha256"
    partial = root / f"{asset.name}.partial"
    _download(asset.checksum_url, checksum_file, maximum=4096)
    expected = checksum_file.read_text("ascii").split()[0].lower()
    if len(expected) != 64 or any(ch not in "0123456789abcdef" for ch in expected):
        raise ValueError("The published update checksum is invalid.")
    _download(asset.url, partial, maximum=4 * 1024 * 1024 * 1024)
    digest = hashlib.sha256()
    with partial.open("rb") as downloaded:
        while chunk := downloaded.read(1024 * 1024):
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual != expected:
        partial.unlink(missing_ok=True)
        raise ValueError("The downloaded update failed its integrity check.")
    partial.replace(archive)
    return archive
