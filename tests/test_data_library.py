from __future__ import annotations

import json
from pathlib import Path

import pytest

from digifly_app.core.data_library import (
    import_local_source,
    list_managed_resources,
    preview_local_source,
    register_existing_morphology,
)
from digifly_app.core.resource_profile import ResourceKind, ResourceProfile, make_default_profile
from digifly_app.core.swc_quality import scan_recent_imports


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


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "downloaded"
    source.mkdir()
    (source / "notes.txt").write_text("source bytes stay unchanged\n", encoding="utf-8")
    (source / "42.swc").write_text(
        "1 1 0 0 0 5 -1\n2 3 1 0 0 1 1\n",
        encoding="utf-8",
    )
    return source


def test_local_import_stages_manifests_promotes_and_registers(tmp_path: Path):
    profile, profile_path = _profile(tmp_path)
    source = _source(tmp_path)

    resource = import_local_source(
        profile,
        source,
        provider="local",
        resource_id="example-neuron",
        source_version="v1",
        profile_path=profile_path,
        label="Example neuron",
    )

    assert resource.root.is_dir()
    assert resource.swc_count == 1
    assert resource.registered_binding
    assert (resource.root / "source" / "42.swc").read_bytes() == (source / "42.swc").read_bytes()
    manifest = json.loads(resource.manifest.read_text(encoding="utf-8"))
    assert manifest["provider"] == "local"
    assert manifest["file_count"] == 2
    assert {record["path"] for record in manifest["files"]} == {
        "source/42.swc",
        "source/notes.txt",
    }
    restored = ResourceProfile.load(profile_path)
    binding = restored.binding(ResourceKind.MORPHOLOGY_SOURCE)
    assert binding is not None
    assert binding.metadata["manifest"] == str(resource.manifest)
    assert len(scan_recent_imports(restored).reports) == 1
    assert not any(profile.managed_data_root.joinpath(".staging").iterdir())


def test_local_import_collision_does_not_replace_existing_resource(tmp_path: Path):
    profile, profile_path = _profile(tmp_path)
    source = _source(tmp_path)
    first = import_local_source(
        profile,
        source,
        provider="local",
        resource_id="collision",
        source_version="v1",
        profile_path=profile_path,
    )
    original_manifest = first.manifest.read_bytes()

    with pytest.raises(FileExistsError):
        import_local_source(
            ResourceProfile.load(profile_path),
            source,
            provider="local",
            resource_id="collision",
            source_version="v1",
            profile_path=profile_path,
        )

    assert first.manifest.read_bytes() == original_manifest


def test_local_import_rejects_symlink_before_creating_library_entry(tmp_path: Path):
    profile, profile_path = _profile(tmp_path)
    source = _source(tmp_path)
    (source / "escape").symlink_to(tmp_path)

    with pytest.raises(ValueError, match="Symbolic links"):
        import_local_source(
            profile,
            source,
            provider="local",
            resource_id="unsafe",
            source_version="v1",
            profile_path=profile_path,
        )

    assert not profile.managed_data_root.exists()


def test_inventory_verification_flags_changed_managed_bytes(tmp_path: Path):
    profile, profile_path = _profile(tmp_path)
    resource = import_local_source(
        profile,
        _source(tmp_path),
        provider="local",
        resource_id="verify",
        source_version="v1",
        profile_path=profile_path,
    )
    assert list_managed_resources(ResourceProfile.load(profile_path), verify=True)[0].integrity == "verified"
    (resource.root / "source" / "notes.txt").write_text("changed", encoding="utf-8")
    assert (
        list_managed_resources(ResourceProfile.load(profile_path), verify=True)[0].integrity
        == "checksum_mismatch"
    )


def test_preview_rejects_empty_folder(tmp_path: Path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="no files"):
        preview_local_source(empty)


def test_register_existing_folder_is_no_copy_and_writes_current_profile(tmp_path: Path):
    profile, profile_path = _profile(tmp_path)
    external = tmp_path / "user-owned"
    external.mkdir()
    source = external / "CELL.SWC"
    source.write_text("1 1 0 0 0 1 -1\n", encoding="utf-8")

    destination = register_existing_morphology(
        profile,
        external,
        resource_id="user-owned",
        profile_path=profile_path,
    )

    assert destination == profile_path
    restored = ResourceProfile.load(destination)
    registered = [
        binding
        for binding in restored.bindings(ResourceKind.MORPHOLOGY_SOURCE)
        if binding.resource_id == "external-user-owned"
    ]
    assert registered[0].resolved_path == external.resolve()
    assert source.read_text(encoding="utf-8") == "1 1 0 0 0 1 -1\n"
    assert not (profile.managed_data_root / "imports").exists()


def test_library_lists_existing_provider_manifest_inside_managed_root(tmp_path: Path):
    profile, profile_path = _profile(tmp_path)
    bundle = profile.managed_data_root / "neuprint" / "manc" / "snapshot"
    swc = bundle / "export_swc" / "42.swc"
    swc.parent.mkdir(parents=True)
    swc.write_text("1 1 0 0 0 1 -1\n", encoding="utf-8")
    manifest = bundle / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "provider": "neuprint",
                "dataset": "manc:v1",
                "fetched_at": "2026-08-27T12:00:00Z",
                "files": [{"path": "export_swc/42.swc", "bytes": swc.stat().st_size}],
            }
        ),
        encoding="utf-8",
    )
    payload = profile.to_dict()
    payload["resources"].append(
        {
            "resource_id": "neuprint-manc",
            "kind": "morphology_source",
            "path": str(swc.parent),
            "access": "read_only",
            "label": "MANC",
            "required": False,
            "metadata": {"provider": "neuprint", "manifest": str(manifest)},
        }
    )
    ResourceProfile.from_dict(payload).save(profile_path, replace=True)

    resources = list_managed_resources(ResourceProfile.load(profile_path))

    assert len(resources) == 1
    assert resources[0].resource_id == "neuprint-manc"
    assert resources[0].swc_count == 1
    assert resources[0].total_bytes == swc.stat().st_size
