"""Credential-safe neuPrint discovery, bounded selection, and SWC acquisition."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
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


def acquire_neuprint_bundle(
    profile: ResourceProfile,
    profile_path: str | Path,
    request: NeuPrintAcquisitionRequest,
    *,
    progress: Callable[[AcquisitionProgress], None] | None = None,
    cancel: threading.Event | None = None,
    client: NeuPrintClient | None = None,
) -> ManagedResource:
    """Download a reviewed neuron set, audit it, then atomically register it."""

    cancel_event = cancel or threading.Event()
    destination = preview_destination(profile, request)
    if destination.exists():
        raise FileExistsError(f"A managed neuPrint download already exists at {destination}")
    managed_root = profile.managed_data_root
    managed_root.mkdir(parents=True, exist_ok=True)
    required_free = min(
        request.max_total_bytes,
        request.max_skeleton_bytes * len(request.neurons),
    )
    available_free = shutil.disk_usage(managed_root).free
    if available_free < required_free:
        raise OSError(
            f"The managed library needs a {required_free:,}-byte safety reserve, "
            f"but only {available_free:,} bytes are free"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging_root = managed_root / ".staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="neuprint-", dir=staging_root))
    provider = client or NeuPrintClient(
        request.server,
        request.token,
        dataset=request.dataset,
    )
    promoted = False
    transferred = 0
    inventory: list[dict[str, Any]] = []
    review_count = 0
    structural_errors = 0

    def emit(stage: str, completed: int, current: str = "") -> None:
        if progress is not None:
            try:
                progress(
                    AcquisitionProgress(
                        stage,
                        completed,
                        len(request.neurons),
                        transferred,
                        current,
                    )
                )
            except Exception:
                # UI/reporting failures must not corrupt an otherwise valid,
                # atomically promoted acquisition or its profile registration.
                pass

    def add_bytes(count: int) -> None:
        nonlocal transferred
        transferred += int(count)
        if transferred > request.max_total_bytes:
            raise NeuPrintError(
                f"The download exceeded its {request.max_total_bytes:,}-byte safety limit"
            )

    try:
        source_root = staging / "source"
        swc_root = source_root / "export_swc"
        derived_root = staging / "derived"
        swc_root.mkdir(parents=True)
        derived_root.mkdir()
        emit("Preparing staged download", 0)
        for index, neuron in enumerate(request.neurons, 1):
            if cancel_event.is_set():
                raise AcquisitionCancelled("The neuPrint download was cancelled")
            current = f"body {neuron.body_id}"
            emit("Downloading SWC", index - 1, current)
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
                raise NeuPrintError(f"neuPrint body {neuron.body_id} did not return text SWC data") from exc
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
            review_count += int(report.needs_review)
            structural_errors += len(report.errors)
            inventory.append(
                {
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
            )
            emit("Audited SWC", index, current)

        metadata = {
            "schema_version": 1,
            "server": normalize_server(request.server),
            "dataset": request.dataset,
            "selection": request.selection.to_dict(),
            "neurons": [neuron.to_dict() for neuron in request.neurons],
        }
        metadata_bytes = (json.dumps(metadata, indent=2, sort_keys=True) + "\n").encode("utf-8")
        metadata_path = source_root / "neurons.json"
        metadata_path.write_bytes(metadata_bytes)
        inventory.append(
            {
                "path": "source/neurons.json",
                "size_bytes": len(metadata_bytes),
                "sha256": _sha256_bytes(metadata_bytes),
            }
        )
        fetched_at = _utc_text()
        total_bytes = sum(int(record["size_bytes"]) for record in inventory)
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
            "digifly_version": __version__,
            "derived_data_directory": "derived",
            "file_count": len(inventory),
            "total_bytes": total_bytes,
            "swc_count": len(request.neurons),
            "post_import_quality": {
                "rule": ADAPTIVE_RADIUS_RULE_ID,
                "audited_swcs": len(request.neurons),
                "needs_review": review_count,
                "structural_errors": structural_errors,
            },
            "files": inventory,
        }
        manifest_path = staging / MANIFEST_FILENAME
        manifest_path.write_text(
            json.dumps(manifest_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if cancel_event.is_set():
            raise AcquisitionCancelled("The neuPrint download was cancelled")
        emit("Promoting validated bundle", len(request.neurons))
        os.replace(staging, destination)
        promoted = True
        resource = ManagedResource(
            "neuprint",
            safe_component(request.resource_id),
            safe_component(request.source_version),
            destination,
            destination / MANIFEST_FILENAME,
            len(inventory),
            total_bytes,
            len(request.neurons),
            fetched_at,
        )
        binding_id = register_managed_morphology(
            profile,
            resource,
            profile_path=profile_path,
            morphology_root=destination / "source" / "export_swc",
            label=request.resource_id,
            dataset=request.dataset,
            metadata={
                "connectome_key": f"neuprint:{request.dataset}:{safe_component(request.resource_id)}",
                **({"credential_ref": request.credential_ref} if request.credential_ref else {}),
            },
        )
        emit("Complete", len(request.neurons))
        return ManagedResource(
            **{**resource.__dict__, "registered_binding": binding_id}
        )
    except Exception:
        if promoted and destination.exists():
            os.replace(destination, staging)
            promoted = False
        raise
    finally:
        if not promoted and staging.exists():
            shutil.rmtree(staging)
