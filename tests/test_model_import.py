from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
import tarfile
import threading
import zipfile

import pytest

from digifly_app.core.data_library import list_managed_resources
from digifly_app.core.model_import import (
    ArchiveLimits,
    ModelDBClient,
    ModelImportCancelled,
    ModelImportError,
    ModelImportRequest,
    import_model_source,
    inspect_model_source,
    modeldb_request,
)
from digifly_app.core.resource_profile import ResourceKind, ResourceProfile, make_default_profile
from digifly_app.core.swc_quality import scan_recent_imports


def _profile(tmp_path: Path) -> tuple[ResourceProfile, Path]:
    workspace = tmp_path / "Digifly Public"
    workspace.mkdir()
    (workspace / "README.md").write_text("Digifly Public\n", encoding="utf-8")
    profile = make_default_profile(
        workspace_root=workspace,
        output_root=tmp_path / "workstation" / "runs",
        managed_data_root=tmp_path / "workstation" / "data",
    )
    return profile, profile.save(tmp_path / "resources-v2.json")


def _model_folder(tmp_path: Path) -> Path:
    source = tmp_path / "model"
    source.mkdir()
    (source / "README.md").write_text("Run with NEURON.\n", encoding="utf-8")
    (source / "LICENSE").write_text("Example only.\n", encoding="utf-8")
    (source / "mosinit.hoc").write_text("load_file(\"nrngui.hoc\")\n", encoding="utf-8")
    (source / "channel.mod").write_text("NEURON { SUFFIX demo }\n", encoding="utf-8")
    (source / "cell.swc").write_text(
        "1 1 0 0 0 5 -1\n2 3 1 0 0 1 1\n", encoding="utf-8"
    )
    return source


def _zip_folder(source: Path, output: Path) -> Path:
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for item in source.iterdir():
            archive.write(item, f"upstream/{item.name}")
    return output


def test_folder_model_import_is_inert_registered_and_swc_audited(tmp_path: Path):
    profile, profile_path = _profile(tmp_path)
    source = _model_folder(tmp_path)
    inspection = inspect_model_source(source)

    assert inspection.simulators == ("NEURON",)
    assert inspection.mechanism_count == 1
    assert inspection.swc_count == 1
    resource = import_model_source(
        profile,
        profile_path,
        ModelImportRequest(source, "local-model", "giant-fiber", "v1"),
        inspection=inspection,
    )

    manifest = json.loads(resource.manifest.read_text(encoding="utf-8"))
    assert manifest["inert_code"] is True
    assert manifest["execution_performed"] is False
    assert manifest["inspection"]["mechanism_count"] == 1
    assert (resource.root / "source" / "channel.mod").read_bytes() == (
        source / "channel.mod"
    ).read_bytes()
    restored = ResourceProfile.load(profile_path)
    assert len(restored.bindings(ResourceKind.MODEL_SOURCE)) == 1
    assert len(restored.bindings(ResourceKind.MORPHOLOGY_SOURCE)) == 1
    assert len(scan_recent_imports(restored).reports) == 1
    assert list_managed_resources(restored)[0].resource_id == "giant-fiber"


def test_zip_model_preserves_original_and_uses_modeldb_destination(tmp_path: Path):
    profile, profile_path = _profile(tmp_path)
    archive = _zip_folder(_model_folder(tmp_path), tmp_path / "245415.zip")
    request = ModelImportRequest(
        archive,
        "modeldb",
        "245415",
        "v9",
        accession=245415,
        source_url="https://modeldb.science/245415",
    )

    resource = import_model_source(profile, profile_path, request)

    assert resource.root == profile.managed_data_root / "modeldb" / "245415" / "v9"
    assert (resource.root / "original" / "245415.zip").read_bytes() == archive.read_bytes()
    assert (resource.root / "source" / "upstream" / "README.md").is_file()


@pytest.mark.parametrize("unsafe", ["../escape.py", "/absolute.py", "C:\\escape.py"])
def test_zip_rejects_path_traversal_and_absolute_paths(tmp_path: Path, unsafe: str):
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr(unsafe, "do not extract")
    with pytest.raises(ModelImportError, match="unsafe|absolute"):
        inspect_model_source(archive)


def test_zip_rejects_symlinks(tmp_path: Path):
    archive = tmp_path / "symlink.zip"
    info = zipfile.ZipInfo("link")
    info.create_system = 3
    info.external_attr = 0o120777 << 16
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr(info, "../outside")
    with pytest.raises(ModelImportError, match="link or special"):
        inspect_model_source(archive)


@pytest.mark.parametrize("link_kind", [tarfile.SYMTYPE, tarfile.LNKTYPE])
def test_tar_rejects_symbolic_and_hard_links(tmp_path: Path, link_kind: bytes):
    archive = tmp_path / "links.tar"
    with tarfile.open(archive, "w") as output:
        info = tarfile.TarInfo("unsafe-link")
        info.type = link_kind
        info.linkname = "../outside"
        output.addfile(info)
    with pytest.raises(ModelImportError, match="link or special"):
        inspect_model_source(archive)


def test_archive_rejects_case_collisions(tmp_path: Path):
    archive = tmp_path / "collision.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("Run.py", "")
        output.writestr("run.py", "")
    with pytest.raises(ModelImportError, match="case-colliding"):
        inspect_model_source(archive)


def test_archive_rejects_excessive_compression_ratio(tmp_path: Path):
    archive = tmp_path / "compressed.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
        output.writestr("zeros.bin", b"0" * 10_000)
    with pytest.raises(ModelImportError, match="compression ratio"):
        inspect_model_source(
            archive,
            limits=ArchiveLimits(max_compression_ratio=2),
        )


def test_model_source_rejects_a_symlinked_root(tmp_path: Path):
    source = _model_folder(tmp_path)
    linked = tmp_path / "linked-model"
    linked.symlink_to(source, target_is_directory=True)
    with pytest.raises(ModelImportError, match="Symbolic links"):
        inspect_model_source(linked)


def test_cancelled_model_import_removes_partial_staging(tmp_path: Path):
    profile, profile_path = _profile(tmp_path)
    source = _model_folder(tmp_path)
    cancel = threading.Event()
    cancel.set()

    with pytest.raises(ModelImportCancelled):
        import_model_source(
            profile,
            profile_path,
            ModelImportRequest(source, "local-model", "cancelled", "v1"),
            cancel=cancel,
        )

    assert not (
        profile.managed_data_root / "models" / "local" / "cancelled" / "v1"
    ).exists()
    assert not any((profile.managed_data_root / ".staging").iterdir())


class _Response:
    def __init__(
        self,
        body: bytes = b"",
        headers: dict[str, str] | None = None,
        final_url: str = "",
    ):
        self._stream = BytesIO(body)
        self.headers = headers or {}
        self.final_url = final_url

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)

    def geturl(self) -> str:
        return self.final_url or "https://modeldb.science/fixture"


class _ModelDBClient(ModelDBClient):
    def __init__(self, metadata_body: bytes, archive_body: bytes):
        super().__init__()
        self.metadata_body = metadata_body
        self.archive_body = archive_body

    def _open(self, request):
        if request.full_url.endswith("/api/v1/models/245415"):
            return _Response(
                self.metadata_body,
                {"Content-Type": "application/json", "Content-Length": str(len(self.metadata_body))},
            )
        if request.get_method() == "HEAD":
            return _Response(
                headers={
                    "Content-Type": "application/zip",
                    "Content-Disposition": "attachment; filename=245415.zip",
                    "Content-Length": str(len(self.archive_body)),
                }
            )
        return _Response(
            self.archive_body,
            {"Content-Type": "application/zip", "Content-Length": str(len(self.archive_body))},
        )


def test_modeldb_lookup_and_download_use_official_metadata(tmp_path: Path):
    payload = json.dumps(
        {
            "id": 245415,
            "name": "Giant Fiber model",
            "ver_number": 9,
            "ver_date": "2019-04-05",
            "notes": {"value": "A model"},
            "modeling_application": {
                "value": [{"object_id": 1882, "object_name": "NEURON"}]
            },
            "model_paper": {
                "value": [{"object_id": 1, "object_name": "Augustin et al. (2019)"}]
            },
            "implemented_by": {
                "value": [{"object_id": 2, "object_name": "Zylbertal, Asaph"}]
            },
        }
    ).encode()
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("README", "model")
    client = _ModelDBClient(payload, buffer.getvalue())

    metadata = client.lookup(245415)
    downloaded = client.download_archive(metadata, tmp_path / "download.zip")

    assert metadata.version == "v9"
    assert metadata.simulators == ("NEURON",)
    assert metadata.citation == "Augustin et al. (2019)"
    assert metadata.archive_available
    assert downloaded.read_bytes() == buffer.getvalue()
    request = modeldb_request(downloaded, metadata)
    assert request.resource_id == "245415"
    assert request.metadata["simulators"] == ["NEURON"]


def test_modeldb_client_refuses_external_download_redirects():
    class RedirectingClient(ModelDBClient):
        def _open(self, _request):
            return _Response(
                headers={"Content-Type": "application/zip"},
                final_url="https://untrusted.example/model.zip",
            )

    with pytest.raises(ModelImportError, match="external host"):
        RedirectingClient()._archive_info(123)
