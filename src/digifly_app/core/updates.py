"""Release update discovery and integrity checks.

Updates are staged in the operating-system cache.  The Digifly workspace is
deliberately not accepted as an update destination, so runs and data libraries
cannot be replaced by this subsystem.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import tempfile
from typing import Any
from urllib.request import Request, urlopen
from urllib.parse import urlsplit, unquote


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
    version: str = ""
    digest: str = ""


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
        if isinstance(value, dict) and _version_key(str(value.get("version", "")), str(value.get("build", ""))) is not None:
            return value
    except (OSError, ValueError, TypeError):
        pass
    # Older Mac packages accidentally omitted the JSON. Use the *loaded*
    # app's Info.plist, never the compiler's remembered Python executable.
    try:
        from digifly_app.core.update_installer import installation_root, installed_build_info

        return installed_build_info(installation_root(), platform.system())
    except (OSError, ValueError, TypeError, KeyError):
        return {}


def platform_asset_fragment() -> str:
    system = platform.system()
    machine = platform.machine().lower()
    if system == "Windows":
        if machine not in {"amd64", "x86_64"}:
            raise RuntimeError("Automatic Windows updates are qualified only for x86-64.")
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


def _version_key(value: str, build: str = "") -> tuple[int, ...] | None:
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:[.+-].*)?$", value)
    if not match:
        return None
    stamp = re.search(r"(?:^|[.+-])(20\d{6})\.(\d+)(?:$|[.+-])", value)
    if stamp is None:
        stamp = re.fullmatch(r"(20\d{6})\.(\d+)", build)
    return (*map(int, match.groups()), *(map(int, stamp.groups()) if stamp else (0, 0)))


def _asset_version(name: str, fragment: str) -> str | None:
    prefix, suffix = "Digifly-Workstation-", "-" + fragment
    if not name.startswith(prefix) or not name.endswith(suffix) or "rollback" in name.casefold():
        return None
    version = name[len(prefix):-len(suffix)]
    return version if _version_key(version) is not None else None


def _time_key(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return datetime.min.replace(tzinfo=timezone.utc)


def check_for_update(*, release_api: str = RELEASE_API) -> UpdateStatus:
    release = _read_json(release_api)
    fragment = platform_asset_fragment()
    assets = release.get("assets", [])
    if not isinstance(assets, list):
        raise ValueError("The release asset list is invalid.")
    candidates = [(item, _asset_version(str(item.get("name", "")), fragment))
                  for item in assets if isinstance(item, dict)]
    candidates = [(item, version) for item, version in candidates if version is not None]
    if not candidates:
        return UpdateStatus(False, None, f"No {fragment} update is published yet.")
    # Asset API order is not release order. In particular an old versioned ZIP
    # can precede the newly uploaded stable alias. Prefer the newest explicit
    # app/build version, never a renamed rollback or a later-edited old asset.
    selected, version = max(candidates, key=lambda pair: (_version_key(pair[1]), _time_key(pair[0].get("updated_at", ""))))
    checksum = next((item for item in assets if isinstance(item, dict) and item.get("name") == f"{selected.get('name')}.sha256"), None)
    if checksum is None:
        return UpdateStatus(False, None, "The update exists but its checksum is missing.")
    asset = UpdateAsset(
        name=str(selected["name"]),
        url=str(selected["browser_download_url"]),
        checksum_url=str(checksum["browser_download_url"]),
        updated_at=str(selected.get("updated_at", "")),
        version=version,
        digest=str(selected.get("digest") or ""),
    )
    current = build_info()
    local_key = _version_key(str(current.get("version", "")), str(current.get("build", "")))
    remote_key = _version_key(version)
    if local_key is None:
        return UpdateStatus(False, None, "This app's build number could not be verified. Automatic replacement is disabled; install the current verified download manually.")
    # Upload/edit times do not identify an app version. Never let a refreshed
    # historical ZIP or missing metadata trigger replacement of a newer app.
    available = remote_key > local_key
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
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.netloc != "github.com" or parsed.query or parsed.fragment
            or not unquote(parsed.path).startswith("/Saint-Sam/Digifly-Application/releases/download/")):
        raise ValueError("Update downloads must come from the official Digifly GitHub release.")
    request = Request(url, headers={"User-Agent": "Digifly-Workstation-Updater"})
    with urlopen(request, timeout=60) as response:
        final = urlsplit(response.url)
        if final.scheme != "https" or final.netloc not in {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}:
            raise ValueError("The update redirected outside GitHub's release storage.")
        _stream_download(response, destination, maximum=maximum)


def _stream_download(response, destination: Path, *, maximum: int) -> None:
    with destination.open("xb") as output:
        total = 0
        while chunk := response.read(1024 * 1024):
            total += len(chunk)
            if total > maximum:
                raise ValueError("The update download exceeded its safety limit.")
            output.write(chunk)


def download_verified_update(asset: UpdateAsset) -> Path:
    """Download an update to cache and reject it unless SHA-256 matches."""

    if (Path(asset.name).name != asset.name or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*\.zip", asset.name)
            or ".." in asset.name or ":" in asset.name):
        raise ValueError("The update asset has an unsafe filename.")
    cache = update_cache_root()
    cache.mkdir(parents=True, exist_ok=True)
    # A retry gets a fresh transaction; never overwrite an earlier verified
    # archive, another process's partial file or a saved diagnostic.
    root = Path(tempfile.mkdtemp(prefix="download-", dir=cache))
    archive = root / asset.name
    checksum_file = root / f"{asset.name}.sha256"
    partial = root / f"{asset.name}.partial"
    _download(asset.checksum_url, checksum_file, maximum=4096)
    fields = checksum_file.read_text("ascii").strip().split()
    if not fields or len(fields) > 2 or (len(fields) == 2 and fields[1].lstrip("*") != asset.name):
        raise ValueError("The published checksum does not identify this update.")
    expected = fields[0].lower()
    if len(expected) != 64 or any(ch not in "0123456789abcdef" for ch in expected):
        raise ValueError("The published update checksum is invalid.")
    if asset.digest and asset.digest != "sha256:" + expected:
        raise ValueError("The release asset digest and checksum disagree.")
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
