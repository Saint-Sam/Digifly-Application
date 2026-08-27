"""Credential-safe neuPrint discovery, bounded selection, and SWC acquisition."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import ssl
import tempfile
import threading
import time
from typing import Any, Callable, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

from digifly_app import __version__

from .credentials import normalize_neuprint_token
from .data_library import (
    MANIFEST_FILENAME,
    MANIFEST_SCHEMA_VERSION,
    ManagedResource,
    register_managed_morphology,
    safe_component,
)
from .resource_profile import ResourceProfile
from .swc_quality import ADAPTIVE_RADIUS_RULE_ID, analyze_swc


DEFAULT_NEUPRINT_SERVER = "https://neuprint.janelia.org"
DEFAULT_SELECTION_LIMIT = 100
MAX_SELECTION_LIMIT = 500
DEFAULT_MAX_SKELETON_BYTES = 250 * 1024 * 1024
DEFAULT_MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_MAX_CONNECTIVITY_BYTES = 128 * 1024 * 1024
DEFAULT_MAX_CONNECTIVITY_ROWS = 250_000
NEUPRINT_CHECKPOINT_FILENAME = "digifly-neuprint-checkpoint.json"
SELECTION_MODES = {
    "body_ids",
    "type_exact",
    "type_regex",
    "instance_exact",
    "instance_regex",
    "roi",
}


class NeuPrintError(RuntimeError):
    """A user-facing neuPrint failure whose message contains no credentials."""


class NeuPrintNetworkError(NeuPrintError):
    pass


class AcquisitionCancelled(NeuPrintError):
    pass


@dataclass(frozen=True)
class NeuPrintDataset:
    name: str
    description: str = ""
    uuid: str = ""


@dataclass(frozen=True)
class NeuPrintNeuron:
    body_id: int
    neuron_type: str = ""
    instance: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "body_id": self.body_id,
            "type": self.neuron_type,
            "instance": self.instance,
        }


@dataclass(frozen=True)
class NeuPrintConnection:
    source_body_id: int
    target_body_id: int
    weight: int

    def to_row(self) -> tuple[int, int, int]:
        return (self.source_body_id, self.target_body_id, self.weight)


@dataclass(frozen=True)
class NeuPrintSelection:
    mode: str
    value: str
    limit: int = DEFAULT_SELECTION_LIMIT

    def __post_init__(self) -> None:
        if self.mode not in SELECTION_MODES:
            raise ValueError(f"Unsupported neuPrint selection mode: {self.mode}")
        if not 1 <= int(self.limit) <= MAX_SELECTION_LIMIT:
            raise ValueError(f"Selection limit must be between 1 and {MAX_SELECTION_LIMIT}")
        if not str(self.value).strip():
            raise ValueError("Enter a body ID or neuron selection value")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NeuPrintAcquisitionRequest:
    server: str
    dataset: str
    resource_id: str
    source_version: str
    selection: NeuPrintSelection
    neurons: tuple[NeuPrintNeuron, ...]
    token: str = field(repr=False, compare=False)
    credential_ref: str = ""
    max_skeleton_bytes: int = DEFAULT_MAX_SKELETON_BYTES
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES
    include_connectivity: bool = False
    max_connectivity_rows: int = DEFAULT_MAX_CONNECTIVITY_ROWS
    max_connectivity_bytes: int = DEFAULT_MAX_CONNECTIVITY_BYTES

    def __post_init__(self) -> None:
        normalize_server(self.server)
        safe_component(self.dataset, field="dataset")
        safe_component(self.resource_id, field="download name")
        safe_component(self.source_version, field="snapshot name")
        normalize_neuprint_token(self.token)
        if not self.neurons:
            raise ValueError("Preview and select at least one neuron before downloading")
        if len(self.neurons) > MAX_SELECTION_LIMIT:
            raise ValueError(f"A single acquisition is limited to {MAX_SELECTION_LIMIT} neurons")
        body_ids = [neuron.body_id for neuron in self.neurons]
        if len(body_ids) != len(set(body_ids)):
            raise ValueError("A neuPrint acquisition cannot contain duplicate body IDs")
        if len(self.neurons) > self.selection.limit:
            raise ValueError("The reviewed neuron set exceeds the bounded selection limit")
        if self.credential_ref and not re.fullmatch(
            r"keyring:org\.digifly\.workstation\.neuprint:[a-z0-9.-]+",
            self.credential_ref,
        ):
            raise ValueError("The neuPrint credential reference is not an opaque keyring ID")
        if self.max_skeleton_bytes < 1 or self.max_total_bytes < self.max_skeleton_bytes:
            raise ValueError("Invalid neuPrint acquisition byte limits")
        if not 1 <= int(self.max_connectivity_rows) <= DEFAULT_MAX_CONNECTIVITY_ROWS:
            raise ValueError(
                f"Connectivity rows must be between 1 and {DEFAULT_MAX_CONNECTIVITY_ROWS:,}"
            )
        if self.max_connectivity_bytes < 1:
            raise ValueError("Invalid neuPrint connectivity byte limit")


@dataclass(frozen=True)
class AcquisitionProgress:
    stage: str
    completed: int
    total: int
    transferred_bytes: int
    current: str = ""


class Transport(Protocol):
    def __call__(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        payload: bytes | None,
        timeout: float,
        cancel: threading.Event | None,
        max_bytes: int,
        on_bytes: Callable[[int], None] | None,
    ) -> bytes: ...


def normalize_server(server: str) -> str:
    text = str(server or "").strip().rstrip("/")
    if "://" not in text:
        text = f"https://{text}"
    parsed = urlsplit(text)
    if parsed.scheme.casefold() != "https":
        raise ValueError("neuPrint servers must use HTTPS")
    if not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Enter a valid neuPrint HTTPS server without embedded credentials")
    if parsed.query or parsed.fragment or (parsed.path and parsed.path != "/"):
        raise ValueError("Enter only the neuPrint server origin, without a path or query")
    host = parsed.hostname.casefold()
    port = f":{parsed.port}" if parsed.port else ""
    return f"https://{host}{port}"


def _default_transport(
    method: str,
    url: str,
    headers: Mapping[str, str],
    payload: bytes | None,
    timeout: float,
    cancel: threading.Event | None,
    max_bytes: int,
    on_bytes: Callable[[int], None] | None,
) -> bytes:
    request = Request(url, data=payload, headers=dict(headers), method=method)
    try:
        with urlopen(request, timeout=timeout, context=ssl.create_default_context()) as response:
            chunks: list[bytes] = []
            transferred = 0
            while True:
                if cancel is not None and cancel.is_set():
                    raise AcquisitionCancelled("The neuPrint download was cancelled")
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                transferred += len(chunk)
                if transferred > max_bytes:
                    raise NeuPrintError(
                        f"The neuPrint response exceeded the {max_bytes:,}-byte safety limit"
                    )
                chunks.append(chunk)
                if on_bytes is not None:
                    on_bytes(len(chunk))
            return b"".join(chunks)
    except HTTPError as exc:
        status = int(getattr(exc, "code", 0) or 0)
        if status in {401, 403}:
            detail = "neuPrint rejected the application token"
        elif status == 404:
            detail = "The requested neuPrint dataset or skeleton was not found"
        else:
            detail = f"neuPrint returned HTTP {status or 'error'}"
        raise NeuPrintNetworkError(detail) from None
    except (TimeoutError, URLError, OSError) as exc:
        raise NeuPrintNetworkError("Could not reach the neuPrint server securely") from None


class NeuPrintClient:
    """Minimal HTTPS client for the stable neuPrint HTTP endpoints Digifly needs."""

    def __init__(
        self,
        server: str,
        token: str,
        *,
        dataset: str = "",
        timeout: float = 45.0,
        transport: Transport | None = None,
    ):
        self.server = normalize_server(server)
        self.dataset = str(dataset or "").strip()
        self._token = normalize_neuprint_token(token)
        self.timeout = float(timeout)
        self._transport = transport or _default_transport

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, Any] | None = None,
        cancel: threading.Event | None = None,
        max_bytes: int = 32 * 1024 * 1024,
        on_bytes: Callable[[int], None] | None = None,
    ) -> bytes:
        if not path.startswith("/"):
            raise ValueError("neuPrint request paths must be absolute")
        encoded = (
            json.dumps(payload, separators=(",", ":")).encode("utf-8")
            if payload is not None
            else None
        )
        return self._transport(
            method,
            f"{self.server}{path}",
            {
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/json, text/plain",
                "Content-Type": "application/json",
                "User-Agent": f"Digifly-Workstation/{__version__}",
            },
            encoded,
            self.timeout,
            cancel,
            max_bytes,
            on_bytes,
        )

    def _json(self, method: str, path: str, *, payload: Mapping[str, Any] | None = None) -> Any:
        raw = self._request(method, path, payload=payload)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise NeuPrintError("neuPrint returned an unreadable response") from exc

    def validate_and_list_datasets(self) -> tuple[NeuPrintDataset, ...]:
        self._json("GET", "/profile")
        payload = self._json("GET", "/api/dbmeta/datasets")
        datasets: list[NeuPrintDataset] = []
        if isinstance(payload, Mapping):
            for name, details in payload.items():
                info = details if isinstance(details, Mapping) else {}
                datasets.append(
                    NeuPrintDataset(
                        str(name),
                        str(info.get("description") or info.get("tag") or ""),
                        str(info.get("uuid") or info.get("UUID") or ""),
                    )
                )
        elif isinstance(payload, list):
            for item in payload:
                if isinstance(item, str):
                    datasets.append(NeuPrintDataset(item))
                elif isinstance(item, Mapping) and item.get("name"):
                    datasets.append(
                        NeuPrintDataset(
                            str(item["name"]),
                            str(item.get("description") or ""),
                            str(item.get("uuid") or ""),
                        )
                    )
        if not datasets:
            raise NeuPrintError("The neuPrint server reported no available datasets")
        return tuple(sorted(datasets, key=lambda item: item.name.casefold()))

    def preview_neurons(self, selection: NeuPrintSelection) -> tuple[NeuPrintNeuron, ...]:
        if not self.dataset:
            raise ValueError("Choose a neuPrint dataset before previewing neurons")
        cypher = _selection_cypher(selection)
        payload = self._json(
            "POST",
            "/api/custom/custom",
            payload={"cypher": cypher, "dataset": self.dataset},
        )
        records = _parse_custom_rows(payload)
        return tuple(records[: selection.limit])

    def fetch_skeleton(
        self,
        body_id: int,
        *,
        cancel: threading.Event | None = None,
        max_bytes: int = DEFAULT_MAX_SKELETON_BYTES,
        on_bytes: Callable[[int], None] | None = None,
    ) -> bytes:
        if not self.dataset:
            raise ValueError("Choose a neuPrint dataset before downloading skeletons")
        # neuPrint's documented skeleton route uses the dataset's literal
        # ``name:version`` segment. Preserve the colon while still escaping
        # slashes and every other path-control character.
        dataset = quote(self.dataset, safe=":._-")
        body = int(body_id)
        return self._request(
            "GET",
            f"/api/skeletons/skeleton/{dataset}/{body}?format=swc",
            cancel=cancel,
            max_bytes=max_bytes,
            on_bytes=on_bytes,
        )

    def fetch_connectivity(
        self,
        body_ids: tuple[int, ...],
        *,
        cancel: threading.Event | None = None,
        max_rows: int = DEFAULT_MAX_CONNECTIVITY_ROWS,
        max_bytes: int = DEFAULT_MAX_CONNECTIVITY_BYTES,
        on_bytes: Callable[[int], None] | None = None,
    ) -> tuple[NeuPrintConnection, ...]:
        """Fetch a bounded directed edge table among the reviewed neurons."""

        if not self.dataset:
            raise ValueError("Choose a neuPrint dataset before downloading connectivity")
        ids = tuple(dict.fromkeys(int(value) for value in body_ids))
        if not ids or len(ids) > MAX_SELECTION_LIMIT:
            raise ValueError("Connectivity requires 1–500 reviewed neuron body IDs")
        limit = int(max_rows)
        if not 1 <= limit <= DEFAULT_MAX_CONNECTIVITY_ROWS:
            raise ValueError("Invalid neuPrint connectivity row limit")
        body_list = ",".join(str(value) for value in ids)
        cypher = (
            "MATCH (source:Neuron)-[edge:ConnectsTo]->(target:Neuron) "
            f"WHERE source.bodyId IN [{body_list}] AND target.bodyId IN [{body_list}] "
            "RETURN source.bodyId AS sourceBodyId, target.bodyId AS targetBodyId, "
            "edge.weight AS weight ORDER BY sourceBodyId, targetBodyId "
            f"LIMIT {limit + 1}"
        )
        raw = self._request(
            "POST",
            "/api/custom/custom",
            payload={"cypher": cypher, "dataset": self.dataset},
            cancel=cancel,
            max_bytes=max_bytes,
            on_bytes=on_bytes,
        )
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise NeuPrintError("neuPrint returned an unreadable connectivity table") from exc
        return _parse_connectivity_rows(payload, max_rows=limit)


def _cypher_string(value: str, *, field: str) -> str:
    text = str(value).strip()
    if not text or len(text) > 256 or any(character in text for character in "\r\n\x00"):
        raise ValueError(f"Invalid neuPrint {field}")
    return json.dumps(text, ensure_ascii=True)


def _selection_cypher(selection: NeuPrintSelection) -> str:
    limit = int(selection.limit)
    if selection.mode == "body_ids":
        parts = [
            part
            for part in re.split(r"[,;\s]+", selection.value.strip())
            if part
        ]
        if not parts or any(not re.fullmatch(r"\d+", part) for part in parts):
            raise ValueError("Enter one or more numeric neuPrint body IDs")
        values = [int(value) for value in parts]
        values = list(dict.fromkeys(values))
        if len(values) > limit:
            raise ValueError(f"The body-ID selection exceeds its {limit}-neuron limit")
        where = f"n.bodyId IN [{','.join(str(value) for value in values)}]"
    elif selection.mode in {"type_exact", "type_regex"}:
        operator = "=" if selection.mode.endswith("exact") else "=~"
        where = f"n.type {operator} {_cypher_string(selection.value, field='type')}"
    elif selection.mode in {"instance_exact", "instance_regex"}:
        operator = "=" if selection.mode.endswith("exact") else "=~"
        where = f"n.instance {operator} {_cypher_string(selection.value, field='instance')}"
    else:
        roi = str(selection.value).strip()
        if not roi or len(roi) > 128 or "`" in roi or any(c in roi for c in "\r\n\x00"):
            raise ValueError("Invalid neuPrint ROI name")
        where = f"coalesce(n.`{roi}`, false) = true"
    return (
        "MATCH (n:Neuron) "
        f"WHERE {where} "
        "RETURN n.bodyId AS bodyId, n.type AS type, n.instance AS instance "
        f"ORDER BY n.bodyId LIMIT {limit}"
    )


def _parse_custom_rows(payload: Any) -> list[NeuPrintNeuron]:
    if not isinstance(payload, Mapping):
        raise NeuPrintError("neuPrint returned an unexpected query response")
    columns = payload.get("columns")
    data = payload.get("data")
    if not isinstance(columns, list) or not isinstance(data, list):
        raise NeuPrintError("neuPrint returned an unexpected query table")
    names = [str(value) for value in columns]
    records: list[NeuPrintNeuron] = []
    for raw in data:
        if isinstance(raw, Mapping):
            row = raw
        elif isinstance(raw, list) and len(raw) == len(names):
            row = dict(zip(names, raw))
        else:
            continue
        try:
            body_id = int(row.get("bodyId"))
        except (TypeError, ValueError):
            continue
        records.append(
            NeuPrintNeuron(
                body_id,
                str(row.get("type") or ""),
                str(row.get("instance") or ""),
            )
        )
    return records


def _parse_connectivity_rows(
    payload: Any,
    *,
    max_rows: int,
) -> tuple[NeuPrintConnection, ...]:
    if not isinstance(payload, Mapping):
        raise NeuPrintError("neuPrint returned an unexpected connectivity response")
    columns = payload.get("columns")
    data = payload.get("data")
    if not isinstance(columns, list) or not isinstance(data, list):
        raise NeuPrintError("neuPrint returned an unexpected connectivity table")
    if len(data) > max_rows:
        raise NeuPrintError(
            f"The selected connectivity exceeds the {max_rows:,}-row safety limit; "
            "reduce the neuron selection"
        )
    names = [str(value) for value in columns]
    connections = []
    for raw in data:
        if isinstance(raw, Mapping):
            row = raw
        elif isinstance(raw, list) and len(raw) == len(names):
            row = dict(zip(names, raw))
        else:
            raise NeuPrintError("neuPrint returned a malformed connectivity row")
        try:
            source = int(row.get("sourceBodyId"))
            target = int(row.get("targetBodyId"))
            weight = int(row.get("weight"))
        except (TypeError, ValueError) as exc:
            raise NeuPrintError("neuPrint returned a malformed connectivity row") from exc
        if source < 0 or target < 0 or weight < 0:
            raise NeuPrintError("neuPrint returned a negative connectivity value")
        connections.append(NeuPrintConnection(source, target, weight))
    return tuple(connections)


def preview_destination(profile: ResourceProfile, request: NeuPrintAcquisitionRequest) -> Path:
    host = safe_component(urlsplit(normalize_server(request.server)).netloc, field="server")
    dataset = safe_component(request.dataset, field="dataset")
    resource = safe_component(request.resource_id, field="download name")
    version = safe_component(request.source_version, field="snapshot name")
    return profile.managed_data_root / "neuprint" / host / dataset / resource / version


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _file_component(value: str, fallback: str) -> str:
    result = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value).strip()).strip("._-")
    return (result or fallback)[:96]


def _family(neuron_type: str) -> str:
    upper = neuron_type.upper()
    return next((prefix for prefix in ("AN", "DN", "IN", "MN", "SN") if upper.startswith(prefix)), "UNKNOWN")


def _utc_text() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _request_descriptor(request: NeuPrintAcquisitionRequest) -> dict[str, Any]:
    """Return the credential-free identity persisted in resumable checkpoints."""

    return {
        "server": normalize_server(request.server),
        "dataset": request.dataset,
        "resource_id": safe_component(request.resource_id),
        "source_version": safe_component(request.source_version),
        "selection": request.selection.to_dict(),
        "neurons": [neuron.to_dict() for neuron in request.neurons],
        "include_connectivity": bool(request.include_connectivity),
        "max_connectivity_rows": int(request.max_connectivity_rows),
    }


def _request_digest(request: NeuPrintAcquisitionRequest) -> str:
    encoded = json.dumps(
        _request_descriptor(request),
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def neuprint_checkpoint_path(
    profile: ResourceProfile,
    request: NeuPrintAcquisitionRequest,
) -> Path:
    return (
        profile.managed_data_root
        / ".staging"
        / f"neuprint-{_request_digest(request)[:24]}"
    )


def discard_neuprint_checkpoint(
    profile: ResourceProfile,
    request: NeuPrintAcquisitionRequest,
) -> bool:
    """Permanently discard exactly one credential-free partial acquisition."""

    staging_path = profile.managed_data_root / ".staging"
    if staging_path.is_symlink():
        raise ValueError("Refusing to discard through a symbolic-link staging root")
    staging_root = staging_path.resolve()
    checkpoint_root = neuprint_checkpoint_path(profile, request)
    try:
        checkpoint_root.resolve().relative_to(staging_root)
    except ValueError as exc:
        raise ValueError("neuPrint checkpoint escapes the managed staging root") from exc
    if checkpoint_root.is_symlink():
        raise ValueError("Refusing to discard a symbolic-link checkpoint")
    if not checkpoint_root.exists():
        return False
    if not (checkpoint_root / NEUPRINT_CHECKPOINT_FILENAME).is_file():
        raise ValueError("The partial neuPrint folder has no valid checkpoint receipt")
    shutil.rmtree(checkpoint_root)
    return True


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_checkpoint(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _new_checkpoint(request: NeuPrintAcquisitionRequest) -> dict[str, Any]:
    now = _utc_text()
    return {
        "schema_version": 1,
        "request_sha256": _request_digest(request),
        "request": _request_descriptor(request),
        "created_at": now,
        "updated_at": now,
        "files": [],
    }


def _load_checkpoint(
    root: Path,
    request: NeuPrintAcquisitionRequest,
) -> dict[str, Any]:
    receipt = root / NEUPRINT_CHECKPOINT_FILENAME
    try:
        payload = json.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NeuPrintError(
            "The saved neuPrint checkpoint is unreadable; discard it before retrying"
        ) from exc
    if (
        not isinstance(payload, dict)
        or int(payload.get("schema_version", 0)) != 1
        or payload.get("request_sha256") != _request_digest(request)
        or payload.get("request") != _request_descriptor(request)
        or not isinstance(payload.get("files"), list)
    ):
        raise NeuPrintError(
            "The saved neuPrint checkpoint does not match this reviewed acquisition"
        )
    seen_paths: set[Path] = set()
    seen_bodies: set[int] = set()
    selected_bodies = {neuron.body_id for neuron in request.neurons}
    for record in payload["files"]:
        if not isinstance(record, dict):
            raise NeuPrintError("The saved neuPrint checkpoint has a malformed inventory")
        relative = Path(str(record.get("path") or ""))
        if not relative.parts or relative.is_absolute() or ".." in relative.parts:
            raise NeuPrintError("The saved neuPrint checkpoint contains an unsafe path")
        raw_candidate = root / relative
        cursor = root
        for part in relative.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise NeuPrintError("The saved neuPrint checkpoint contains a symbolic link")
        candidate = raw_candidate.resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError as exc:
            raise NeuPrintError("The saved neuPrint checkpoint escapes its staging folder") from exc
        if relative in seen_paths or not candidate.is_file() or candidate.is_symlink():
            raise NeuPrintError("The saved neuPrint checkpoint is incomplete or duplicated")
        seen_paths.add(relative)
        try:
            size = int(record.get("size_bytes", -1))
        except (TypeError, ValueError) as exc:
            raise NeuPrintError("The saved neuPrint checkpoint has an invalid file size") from exc
        if candidate.stat().st_size != size or _sha256_file(candidate) != record.get("sha256"):
            raise NeuPrintError("A saved neuPrint checkpoint file failed checksum verification")
        if "body_id" in record:
            try:
                body = int(record["body_id"])
            except (TypeError, ValueError) as exc:
                raise NeuPrintError("The saved neuPrint checkpoint has an invalid body ID") from exc
            if body not in selected_bodies or body in seen_bodies:
                raise NeuPrintError("The saved neuPrint checkpoint has an unexpected body ID")
            seen_bodies.add(body)
    return payload


def acquire_neuprint_bundle(
    profile: ResourceProfile,
    profile_path: str | Path,
    request: NeuPrintAcquisitionRequest,
    *,
    progress: Callable[[AcquisitionProgress], None] | None = None,
    cancel: threading.Event | None = None,
    client: NeuPrintClient | None = None,
) -> ManagedResource:
    """Resume/download a reviewed neuron set, audit it, and atomically register it."""

    cancel_event = cancel or threading.Event()
    destination = preview_destination(profile, request)
    if destination.exists():
        raise FileExistsError(f"A managed neuPrint download already exists at {destination}")
    managed_root = profile.managed_data_root
    managed_root.mkdir(parents=True, exist_ok=True)
    reserve = request.max_skeleton_bytes * len(request.neurons)
    if request.include_connectivity:
        reserve += request.max_connectivity_bytes
    required_free = min(request.max_total_bytes, reserve)
    available_free = shutil.disk_usage(managed_root).free
    if available_free < required_free:
        raise OSError(
            f"The managed library needs a {required_free:,}-byte safety reserve, "
            f"but only {available_free:,} bytes are free"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging_root = managed_root / ".staging"
    if staging_root.is_symlink():
        raise NeuPrintError("Refusing to acquire through a symbolic-link staging root")
    staging_root.mkdir(parents=True, exist_ok=True)
    staging = neuprint_checkpoint_path(profile, request)
    if staging.is_symlink():
        raise NeuPrintError("Refusing to use a symbolic-link neuPrint checkpoint")
    resumed = staging.exists()
    if resumed:
        if not staging.is_dir():
            raise NeuPrintError("The saved neuPrint checkpoint is not a directory")
        checkpoint = _load_checkpoint(staging, request)
    else:
        staging.mkdir()
        checkpoint = _new_checkpoint(request)
        _write_checkpoint(staging / NEUPRINT_CHECKPOINT_FILENAME, checkpoint)
    source_root = staging / "source"
    swc_root = source_root / "export_swc"
    connectivity_root = source_root / "connectivity"
    derived_root = staging / "derived"
    swc_root.mkdir(parents=True, exist_ok=True)
    derived_root.mkdir(exist_ok=True)
    provider = client or NeuPrintClient(
        request.server,
        request.token,
        dataset=request.dataset,
    )
    promoted = False
    inventory: list[dict[str, Any]] = [dict(item) for item in checkpoint["files"]]
    transferred = sum(int(record["size_bytes"]) for record in inventory)
    completed_bodies = {
        int(record["body_id"])
        for record in inventory
        if "body_id" in record
    }
    connectivity_record = next(
        (record for record in inventory if record.get("data_role") == "connectivity"),
        None,
    )
    review_count = sum(
        int(bool(record.get("swc_quality", {}).get("needs_review")))
        for record in inventory
        if isinstance(record.get("swc_quality"), Mapping)
    )
    structural_errors = sum(
        int(record.get("swc_quality", {}).get("error_count", 0))
        for record in inventory
        if isinstance(record.get("swc_quality"), Mapping)
    )
    total_tasks = len(request.neurons) + int(request.include_connectivity)

    def completed_tasks() -> int:
        return len(completed_bodies) + int(connectivity_record is not None)

    def emit(stage: str, completed: int, current: str = "") -> None:
        if progress is not None:
            try:
                progress(
                    AcquisitionProgress(
                        stage,
                        completed,
                        total_tasks,
                        transferred,
                        current,
                    )
                )
            except Exception:
                pass

    def add_bytes(count: int) -> None:
        nonlocal transferred
        transferred += int(count)
        if transferred > request.max_total_bytes:
            raise NeuPrintError(
                f"The download exceeded its {request.max_total_bytes:,}-byte safety limit"
            )

    def save_checkpoint() -> None:
        checkpoint["updated_at"] = _utc_text()
        checkpoint["files"] = inventory
        _write_checkpoint(staging / NEUPRINT_CHECKPOINT_FILENAME, checkpoint)

    try:
        emit(
            "Resuming saved checkpoint" if resumed else "Preparing resumable download",
            completed_tasks(),
        )
        for neuron in request.neurons:
            if neuron.body_id in completed_bodies:
                emit("Using verified checkpoint SWC", completed_tasks(), f"body {neuron.body_id}")
                continue
            if cancel_event.is_set():
                raise AcquisitionCancelled("The neuPrint download was cancelled")
            current = f"body {neuron.body_id}"
            emit("Downloading SWC", completed_tasks(), current)
            skeleton: bytes | None = None
            for attempt in range(3):
                try:
                    skeleton = provider.fetch_skeleton(
                        neuron.body_id,
                        cancel=cancel_event,
                        max_bytes=request.max_skeleton_bytes,
                        on_bytes=add_bytes,
                    )
                    break
                except NeuPrintNetworkError:
                    if attempt == 2:
                        raise
                    if cancel_event.wait(0.5 * (2**attempt)):
                        raise AcquisitionCancelled("The neuPrint download was cancelled")
            assert skeleton is not None
            try:
                skeleton.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise NeuPrintError(
                    f"neuPrint body {neuron.body_id} did not return text SWC data"
                ) from exc
            neuron_type = _file_component(neuron.neuron_type, "untyped")
            relative = (
                Path("source")
                / "export_swc"
                / _family(neuron.neuron_type)
                / neuron_type
                / str(neuron.body_id)
                / f"{neuron.body_id}_neuprint_raw.swc"
            )
            output = staging / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(skeleton)
            report = analyze_swc(
                output,
                dataset=request.dataset,
                body_id=neuron.body_id,
                provider="neuprint",
                instance=neuron.instance,
            )
            if report.node_count == 0:
                raise NeuPrintError(f"neuPrint body {neuron.body_id} returned no valid SWC nodes")
            record = {
                "path": relative.as_posix(),
                "size_bytes": len(skeleton),
                "sha256": _sha256_bytes(skeleton),
                "body_id": neuron.body_id,
                "type": neuron.neuron_type,
                "instance": neuron.instance,
                "swc_rows": report.node_count,
                "swc_quality": {
                    "needs_review": report.needs_review,
                    "finding_count": len(report.findings),
                    "error_count": len(report.errors),
                },
            }
            inventory.append(record)
            completed_bodies.add(neuron.body_id)
            review_count += int(report.needs_review)
            structural_errors += len(report.errors)
            save_checkpoint()
            emit("Audited and checkpointed SWC", completed_tasks(), current)

        if request.include_connectivity and connectivity_record is None:
            if cancel_event.is_set():
                raise AcquisitionCancelled("The neuPrint download was cancelled")
            emit("Downloading selected connectivity", completed_tasks())
            connections: tuple[NeuPrintConnection, ...] | None = None
            body_ids = tuple(neuron.body_id for neuron in request.neurons)
            for attempt in range(3):
                try:
                    connections = provider.fetch_connectivity(
                        body_ids,
                        cancel=cancel_event,
                        max_rows=request.max_connectivity_rows,
                        max_bytes=request.max_connectivity_bytes,
                        on_bytes=add_bytes,
                    )
                    break
                except NeuPrintNetworkError:
                    if attempt == 2:
                        raise
                    if cancel_event.wait(0.5 * (2**attempt)):
                        raise AcquisitionCancelled("The neuPrint download was cancelled")
            assert connections is not None
            table_buffer = io.StringIO(newline="")
            writer = csv.writer(table_buffer, lineterminator="\n")
            writer.writerow(("source_body_id", "target_body_id", "weight"))
            writer.writerows(connection.to_row() for connection in connections)
            table_bytes = table_buffer.getvalue().encode("utf-8")
            connectivity_root.mkdir(parents=True, exist_ok=True)
            relative = Path("source/connectivity/selected_connections.csv")
            (staging / relative).write_bytes(table_bytes)
            connectivity_record = {
                "path": relative.as_posix(),
                "size_bytes": len(table_bytes),
                "sha256": _sha256_bytes(table_bytes),
                "data_role": "connectivity",
                "row_count": len(connections),
            }
            inventory.append(connectivity_record)
            save_checkpoint()
            emit("Checkpointed connectivity table", completed_tasks())

        metadata = {
            "schema_version": 1,
            "server": normalize_server(request.server),
            "dataset": request.dataset,
            "selection": request.selection.to_dict(),
            "neurons": [neuron.to_dict() for neuron in request.neurons],
            "connectivity_included": bool(request.include_connectivity),
        }
        metadata_bytes = (json.dumps(metadata, indent=2, sort_keys=True) + "\n").encode("utf-8")
        metadata_path = source_root / "neurons.json"
        metadata_path.write_bytes(metadata_bytes)
        final_inventory = [*inventory, {
            "path": "source/neurons.json",
            "size_bytes": len(metadata_bytes),
            "sha256": _sha256_bytes(metadata_bytes),
        }]
        fetched_at = _utc_text()
        total_bytes = sum(int(record["size_bytes"]) for record in final_inventory)
        connectivity_payload = {
            "included": bool(request.include_connectivity),
            "scope": "selected_to_selected",
            "table_path": (
                "source/connectivity/selected_connections.csv"
                if request.include_connectivity
                else ""
            ),
            "row_count": (
                int(connectivity_record.get("row_count", 0))
                if connectivity_record is not None
                else 0
            ),
            "columns": ["source_body_id", "target_body_id", "weight"],
            "row_limit": int(request.max_connectivity_rows),
        }
        manifest_payload = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "provider": "neuprint",
            "resource_id": safe_component(request.resource_id),
            "dataset": request.dataset,
            "source_version": safe_component(request.source_version),
            "source_url": normalize_server(request.server),
            "server": normalize_server(request.server),
            "fetched_at": fetched_at,
            "selection": request.selection.to_dict(),
            "neurons": [neuron.to_dict() for neuron in request.neurons],
            "connectivity": connectivity_payload,
            "digifly_version": __version__,
            "derived_data_directory": "derived",
            "file_count": len(final_inventory),
            "total_bytes": total_bytes,
            "swc_count": len(request.neurons),
            "post_import_quality": {
                "rule": ADAPTIVE_RADIUS_RULE_ID,
                "audited_swcs": len(request.neurons),
                "needs_review": review_count,
                "structural_errors": structural_errors,
            },
            "files": final_inventory,
        }
        manifest_path = staging / MANIFEST_FILENAME
        manifest_path.write_text(
            json.dumps(manifest_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if cancel_event.is_set():
            raise AcquisitionCancelled("The neuPrint download was cancelled")
        emit("Promoting validated bundle", total_tasks)
        os.replace(staging, destination)
        promoted = True
        checkpoint_at_destination = destination / NEUPRINT_CHECKPOINT_FILENAME
        checkpoint_at_destination.unlink()
        resource = ManagedResource(
            "neuprint",
            safe_component(request.resource_id),
            safe_component(request.source_version),
            destination,
            destination / MANIFEST_FILENAME,
            len(final_inventory),
            total_bytes,
            len(request.neurons),
            fetched_at,
        )
        binding_id = register_managed_morphology(
            profile,
            resource,
            profile_path=profile_path,
            morphology_root=destination / "source" / "export_swc",
            data_root=(destination / "source" / "connectivity") if request.include_connectivity else None,
            data_label=f"{request.resource_id} selected connectivity",
            label=request.resource_id,
            dataset=request.dataset,
            metadata={
                "connectome_key": f"neuprint:{request.dataset}:{safe_component(request.resource_id)}",
                **({"credential_ref": request.credential_ref} if request.credential_ref else {}),
            },
            data_metadata={
                "data_role": "neuprint_connectivity",
                "connectivity_scope": "selected_to_selected",
            },
        )
        emit("Complete", total_tasks)
        return ManagedResource(
            **{**resource.__dict__, "registered_binding": binding_id}
        )
    except Exception:
        if promoted and destination.exists():
            checkpoint_at_destination = destination / NEUPRINT_CHECKPOINT_FILENAME
            if not checkpoint_at_destination.exists():
                _write_checkpoint(checkpoint_at_destination, checkpoint)
            os.replace(destination, staging)
            promoted = False
        raise
