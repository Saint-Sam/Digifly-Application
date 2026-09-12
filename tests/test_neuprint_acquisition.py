from __future__ import annotations

import json
from pathlib import Path
import threading

import pytest
import keyring

from digifly_app.core.credentials import (
    NeuPrintCredentialStore,
    credential_reference,
    normalize_neuprint_token,
)
from digifly_app.core.neuprint import (
    AcquisitionCancelled,
    NeuPrintAcquisitionRequest,
    NeuPrintClient,
    NeuPrintConnection,
    NeuPrintDataset,
    NeuPrintError,
    NeuPrintNetworkError,
    NeuPrintNeuron,
    NeuPrintSelection,
    acquire_neuprint_bundle,
    discard_neuprint_checkpoint,
    neuprint_checkpoint_path,
    preview_destination,
)
from digifly_app.core.data_library import list_managed_resources
from digifly_app.core.remote import RetryPolicy
from digifly_app.core.resource_profile import ResourceKind, ResourceProfile, make_default_profile
from digifly_app.core.swc_quality import scan_recent_imports


SECRET = "test-secret-that-must-never-be-persisted"
SWC = b"1 1 10000 20000 30000 500 -1\n2 3 11000 20000 30000 200 1\n"


def test_token_json_and_os_credential_adapter_keep_only_an_opaque_reference(monkeypatch):
    stored = {}

    class Backend:
        priority = 5

    monkeypatch.setattr(keyring, "get_keyring", lambda: Backend())
    monkeypatch.setattr(
        keyring,
        "set_password",
        lambda service, account, password: stored.__setitem__((service, account), password),
    )
    monkeypatch.setattr(
        keyring,
        "get_password",
        lambda service, account: stored.get((service, account)),
    )
    monkeypatch.setattr(
        keyring,
        "delete_password",
        lambda service, account: stored.pop((service, account), None),
    )
    store = NeuPrintCredentialStore()
    token_json = json.dumps({"token": SECRET})

    reference = store.set("https://neuprint.janelia.org", token_json)

    assert reference == credential_reference("https://neuprint.janelia.org")
    assert SECRET not in reference
    assert store.get("https://neuprint.janelia.org") == SECRET
    assert normalize_neuprint_token(token_json) == SECRET
    store.delete("https://neuprint.janelia.org")
    assert store.get("https://neuprint.janelia.org") is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (json.dumps({"dsg_token": SECRET}), SECRET),
        (f"dsg_token={SECRET}; Path=/; Secure", SECRET),
        (SECRET, SECRET),
    ],
)
def test_current_dataset_gateway_credential_formats(value, expected):
    assert normalize_neuprint_token(value) == expected


def _profile(tmp_path: Path):
    workspace = tmp_path / "Digifly Public"
    workspace.mkdir()
    (workspace / "README.md").write_text("Digifly Public\n", encoding="utf-8")
    profile = make_default_profile(
        workspace_root=workspace,
        output_root=tmp_path / "workstation" / "runs",
        managed_data_root=tmp_path / "workstation" / "data",
    )
    profile_path = profile.save(tmp_path / "resources-v2.json")
    return profile, profile_path


def _request(*, neurons: tuple[NeuPrintNeuron, ...] | None = None):
    return NeuPrintAcquisitionRequest(
        server="https://neuprint.janelia.org",
        dataset="manc:v1.2.1",
        resource_id="dnp01-sample",
        source_version="20260827T120000Z",
        selection=NeuPrintSelection("type_exact", "DNp01", 10),
        neurons=neurons or (NeuPrintNeuron(42, "DNp01", "DNp01_R"),),
        token=SECRET,
        credential_ref="keyring:org.digifly.workstation.neuprint:neuprint.janelia.org",
    )


def test_minimal_client_validates_lists_and_previews_without_exposing_token():
    calls = []

    def transport(method, url, headers, payload, timeout, cancel, max_bytes, on_bytes):
        calls.append((method, url, headers, payload))
        if url.endswith("/profile"):
            return b'{"user":"fixture"}'
        if url.endswith("/api/dbmeta/datasets"):
            return json.dumps(
                {"manc:v1.2.1": {"description": "MANC", "uuid": "fixture"}}
            ).encode()
        assert url.endswith("/api/custom/custom")
        return b'{"columns":["bodyId","type","instance"],"data":[[42,"DNp01","DNp01_R"]]}'

    client = NeuPrintClient(
        "neuprint.janelia.org",
        json.dumps({"token": SECRET}),
        transport=transport,
    )
    datasets = client.validate_and_list_datasets()
    assert datasets == (NeuPrintDataset("manc:v1.2.1", "MANC", "fixture"),)
    client.dataset = datasets[0].name
    neurons = client.preview_neurons(NeuPrintSelection("type_exact", "DNp01", 10))

    assert neurons == (NeuPrintNeuron(42, "DNp01", "DNp01_R"),)
    query = json.loads(calls[-1][3])["cypher"]
    assert 'n.type = "DNp01"' in query
    assert "LIMIT 10" in query
    assert all(call[2]["Authorization"] == f"Bearer {SECRET}" for call in calls)
    assert SECRET not in repr(client)


def test_neuprint_client_retries_only_transient_failures_and_honors_cancellation():
    transient_calls = 0

    def transient(method, url, headers, payload, timeout, cancel, max_bytes, on_bytes):
        nonlocal transient_calls
        transient_calls += 1
        if url.endswith("/profile") and transient_calls < 3:
            raise NeuPrintNetworkError("temporary", retryable=True)
        if url.endswith("/profile"):
            return b'{"user":"fixture"}'
        return b'{"manc:v1.2.1":{"description":"MANC"}}'

    client = NeuPrintClient(
        "https://neuprint.janelia.org",
        SECRET,
        transport=transient,
        retry_policy=RetryPolicy(max_attempts=3, initial_delay=0, max_delay=0),
    )
    assert client.validate_and_list_datasets()[0].name == "manc:v1.2.1"
    assert transient_calls == 4

    rejected_calls = 0

    def rejected(*_args):
        nonlocal rejected_calls
        rejected_calls += 1
        raise NeuPrintNetworkError("rejected", status=401, retryable=False)

    rejected_client = NeuPrintClient(
        "https://neuprint.janelia.org",
        SECRET,
        transport=rejected,
        retry_policy=RetryPolicy(max_attempts=3, initial_delay=0, max_delay=0),
    )
    with pytest.raises(NeuPrintNetworkError, match="rejected"):
        rejected_client.validate_and_list_datasets()
    assert rejected_calls == 1

    cancel = threading.Event()
    cancelled_calls = 0

    def cancelled_transport(*_args):
        nonlocal cancelled_calls
        cancelled_calls += 1
        cancel.set()
        raise NeuPrintNetworkError("temporary", retryable=True)

    cancelled_client = NeuPrintClient(
        "https://neuprint.janelia.org",
        SECRET,
        dataset="manc:v1.2.1",
        transport=cancelled_transport,
        retry_policy=RetryPolicy(max_attempts=3, initial_delay=10, max_delay=10),
    )
    with pytest.raises(AcquisitionCancelled):
        cancelled_client.fetch_skeleton(42, cancel=cancel)
    assert cancelled_calls == 1
    assert SECRET not in repr(_request())


def test_skeleton_route_preserves_dataset_version_separator_and_escapes_paths():
    captured = {}

    def transport(method, url, headers, payload, timeout, cancel, max_bytes, on_bytes):
        captured["url"] = url
        return SWC

    client = NeuPrintClient(
        "https://neuprint.janelia.org",
        SECRET,
        dataset="manc:v1.2.1",
        transport=transport,
    )
    assert client.fetch_skeleton(42) == SWC
    assert captured["url"].endswith(
        "/api/skeletons/skeleton/manc:v1.2.1/42?format=swc"
    )


def test_client_fetches_bounded_selected_connectivity_table():
    captured = {}

    def transport(method, url, headers, payload, timeout, cancel, max_bytes, on_bytes):
        captured.update(json.loads(payload))
        return (
            b'{"columns":["sourceBodyId","targetBodyId","weight"],'
            b'"data":[[42,43,17],[43,42,9]]}'
        )

    client = NeuPrintClient(
        "https://neuprint.janelia.org",
        SECRET,
        dataset="manc:v1.2.1",
        transport=transport,
    )
    result = client.fetch_connectivity((42, 43), max_rows=10)

    assert result == (
        NeuPrintConnection(42, 43, 17),
        NeuPrintConnection(43, 42, 9),
    )
    assert "source.bodyId IN [42,43]" in captured["cypher"]
    assert "target.bodyId IN [42,43]" in captured["cypher"]
    assert captured["cypher"].endswith("LIMIT 11")


def test_client_rejects_connectivity_larger_than_reviewed_row_limit():
    client = NeuPrintClient(
        "https://neuprint.janelia.org",
        SECRET,
        dataset="manc:v1.2.1",
        transport=lambda *args: (
            b'{"columns":["sourceBodyId","targetBodyId","weight"],'
            b'"data":[[42,43,1],[43,42,1]]}'
        ),
    )
    with pytest.raises(NeuPrintError, match="row safety limit"):
        client.fetch_connectivity((42, 43), max_rows=1)


class _FixtureClient:
    def __init__(
        self,
        data: bytes = SWC,
        connections: tuple[NeuPrintConnection, ...] = (),
    ):
        self.data = data
        self.connections = connections
        self.requested = []
        self.connectivity_requests = []

    def fetch_skeleton(self, body_id, *, cancel, max_bytes, on_bytes):
        self.requested.append(body_id)
        if cancel.is_set():
            raise AcquisitionCancelled("cancelled")
        on_bytes(len(self.data))
        return self.data

    def fetch_connectivity(self, body_ids, *, cancel, max_rows, max_bytes, on_bytes):
        self.connectivity_requests.append(tuple(body_ids))
        if cancel.is_set():
            raise AcquisitionCancelled("cancelled")
        on_bytes(32)
        return self.connections


def test_acquisition_stages_audits_promotes_registers_and_never_persists_token(tmp_path: Path):
    profile, profile_path = _profile(tmp_path)
    request = _request()
    progress = []
    resource = acquire_neuprint_bundle(
        profile,
        profile_path,
        request,
        client=_FixtureClient(),
        progress=progress.append,
    )

    assert resource.root == preview_destination(profile, request)
    assert resource.swc_count == 1
    assert resource.registered_binding
    swc = resource.root / "source/export_swc/DN/DNp01/42/42_neuprint_raw.swc"
    assert swc.read_bytes() == SWC
    manifest_text = resource.manifest.read_text(encoding="utf-8")
    manifest = json.loads(manifest_text)
    assert SECRET not in manifest_text
    assert manifest["selection"] == {"mode": "type_exact", "value": "DNp01", "limit": 10}
    assert manifest["post_import_quality"]["audited_swcs"] == 1
    assert manifest["files"][0]["body_id"] == 42
    restored = ResourceProfile.load(profile_path)
    profile_text = profile_path.read_text(encoding="utf-8")
    assert SECRET not in profile_text
    binding = next(
        binding
        for binding in restored.bindings(ResourceKind.MORPHOLOGY_SOURCE)
        if binding.resource_id == resource.registered_binding
    )
    assert binding.resolved_path == resource.root / "source/export_swc"
    assert binding.metadata["credential_ref"].startswith("keyring:")
    assert list_managed_resources(restored)[0].resource_id == "dnp01-sample"
    assert len(scan_recent_imports(restored).reports) == 1
    assert progress[-1].stage == "Complete"
    assert not any((profile.managed_data_root / ".staging").iterdir())


def test_cancelled_acquisition_keeps_credential_free_resumable_checkpoint(tmp_path: Path):
    profile, profile_path = _profile(tmp_path)
    request = _request()
    cancel = threading.Event()
    cancel.set()

    with pytest.raises(AcquisitionCancelled):
        acquire_neuprint_bundle(
            profile,
            profile_path,
            request,
            client=_FixtureClient(),
            cancel=cancel,
        )

    assert not preview_destination(profile, request).exists()
    checkpoint = neuprint_checkpoint_path(profile, request)
    assert checkpoint.is_dir()
    assert SECRET not in (checkpoint / "digifly-neuprint-checkpoint.json").read_text()
    assert ResourceProfile.load(profile_path).bindings(ResourceKind.MORPHOLOGY_SOURCE) == ()
    assert discard_neuprint_checkpoint(profile, request)
    assert not checkpoint.exists()


def test_acquisition_resumes_verified_swcs_without_redownloading_them(tmp_path: Path):
    profile, profile_path = _profile(tmp_path)
    neurons = (
        NeuPrintNeuron(42, "DNp01", "DNp01_R"),
        NeuPrintNeuron(43, "DNp01", "DNp01_L"),
    )
    request = _request(neurons=neurons)
    cancel = threading.Event()

    class InterruptingClient(_FixtureClient):
        def fetch_skeleton(self, body_id, *, cancel, max_bytes, on_bytes):
            if body_id == 43:
                raise AcquisitionCancelled("cancelled after first body")
            return super().fetch_skeleton(
                body_id,
                cancel=cancel,
                max_bytes=max_bytes,
                on_bytes=on_bytes,
            )

    first = InterruptingClient()
    with pytest.raises(AcquisitionCancelled):
        acquire_neuprint_bundle(
            profile,
            profile_path,
            request,
            client=first,
            cancel=cancel,
        )
    assert first.requested == [42]

    resumed = _FixtureClient()
    resource = acquire_neuprint_bundle(
        profile,
        profile_path,
        request,
        client=resumed,
    )

    assert resumed.requested == [43]
    assert resource.swc_count == 2
    assert not neuprint_checkpoint_path(profile, request).exists()


def test_checkpoint_operations_refuse_symbolic_link_staging_root(tmp_path: Path):
    profile, profile_path = _profile(tmp_path)
    outside = tmp_path / "outside-staging"
    outside.mkdir()
    staging = profile.managed_data_root / ".staging"
    staging.parent.mkdir(parents=True)
    staging.symlink_to(outside, target_is_directory=True)
    marker = outside / "keep.txt"
    marker.write_text("keep\n", encoding="utf-8")

    with pytest.raises(NeuPrintError, match="symbolic-link staging root"):
        acquire_neuprint_bundle(
            profile,
            profile_path,
            _request(),
            client=_FixtureClient(),
        )
    with pytest.raises(ValueError, match="symbolic-link staging root"):
        discard_neuprint_checkpoint(profile, _request())

    assert marker.read_text(encoding="utf-8") == "keep\n"


def test_connectivity_table_is_bounded_manifested_and_registered_as_data(tmp_path: Path):
    profile, profile_path = _profile(tmp_path)
    neurons = (
        NeuPrintNeuron(42, "DNp01", "DNp01_R"),
        NeuPrintNeuron(43, "DNp01", "DNp01_L"),
    )
    payload = _request(neurons=neurons).__dict__.copy()
    payload["include_connectivity"] = True
    request = NeuPrintAcquisitionRequest(**payload)
    client = _FixtureClient(
        connections=(NeuPrintConnection(42, 43, 17), NeuPrintConnection(43, 42, 9))
    )

    resource = acquire_neuprint_bundle(
        profile,
        profile_path,
        request,
        client=client,
    )

    table = resource.root / "source/connectivity/selected_connections.csv"
    assert table.read_text(encoding="utf-8") == (
        "source_body_id,target_body_id,weight\n42,43,17\n43,42,9\n"
    )
    manifest = json.loads(resource.manifest.read_text(encoding="utf-8"))
    assert manifest["connectivity"]["row_count"] == 2
    assert manifest["connectivity"]["scope"] == "selected_to_selected"
    restored = ResourceProfile.load(profile_path)
    data_binding = restored.bindings(ResourceKind.DATA_SOURCE)[0]
    assert data_binding.resolved_path == table.parent
    assert data_binding.metadata["data_role"] == "neuprint_connectivity"
    assert client.connectivity_requests == [(42, 43)]


def test_acquisition_collision_never_overwrites_completed_bundle(tmp_path: Path):
    profile, profile_path = _profile(tmp_path)
    request = _request()
    first = acquire_neuprint_bundle(profile, profile_path, request, client=_FixtureClient())
    original = first.manifest.read_bytes()

    with pytest.raises(FileExistsError):
        acquire_neuprint_bundle(
            ResourceProfile.load(profile_path),
            profile_path,
            request,
            client=_FixtureClient(),
        )

    assert first.manifest.read_bytes() == original


def test_acquisition_request_rejects_duplicates_and_nonopaque_credential_refs():
    with pytest.raises(ValueError, match="duplicate"):
        _request(
            neurons=(
                NeuPrintNeuron(42, "DNp01", "right"),
                NeuPrintNeuron(42, "DNp01", "right"),
            )
        )
    payload = _request().__dict__.copy()
    payload["credential_ref"] = SECRET
    with pytest.raises(ValueError, match="opaque keyring"):
        NeuPrintAcquisitionRequest(**payload)


@pytest.mark.parametrize(
    ("mode", "value", "expected"),
    [
        ("body_ids", "42, 43", "n.bodyId IN [42,43]"),
        ("type_regex", "DNp.*", 'n.type =~ "DNp.*"'),
        ("instance_exact", "DNp01_R", 'n.instance = "DNp01_R"'),
        ("roi", "legNp(T1)(R)", "n.`legNp(T1)(R)`"),
    ],
)
def test_bounded_selection_modes_generate_reviewable_queries(mode, value, expected):
    captured = {}

    def transport(method, url, headers, payload, timeout, cancel, max_bytes, on_bytes):
        captured.update(json.loads(payload))
        return b'{"columns":["bodyId","type","instance"],"data":[]}'

    client = NeuPrintClient(
        "https://neuprint.janelia.org",
        SECRET,
        dataset="manc:v1.2.1",
        transport=transport,
    )
    client.preview_neurons(NeuPrintSelection(mode, value, 5))
    assert expected in captured["cypher"]
    assert captured["cypher"].endswith("LIMIT 5")


def test_body_id_selection_rejects_mixed_free_text_instead_of_extracting_digits():
    client = NeuPrintClient(
        "https://neuprint.janelia.org",
        SECRET,
        dataset="manc:v1.2.1",
        transport=lambda *args: b"{}",
    )
    with pytest.raises(ValueError, match="numeric"):
        client.preview_neurons(NeuPrintSelection("body_ids", "body 42", 5))
