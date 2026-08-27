from __future__ import annotations

import json
from pathlib import Path

import pytest

from digifly_app.core.data_library import import_local_source, list_managed_resources
from digifly_app.core.resource_management import (
    LibraryMoveCancelled,
    LibraryMovePreview,
    list_trash_entries,
    move_managed_library,
    preview_library_relink,
    purge_trashed_resource,
    register_managed_resource,
    relink_managed_library,
    restore_trashed_resource,
    trash_managed_resource,
    unregister_managed_resource,
)
from digifly_app.core.resource_profile import (
    AccessMode,
    ResourceBinding,
    ResourceKind,
    ResourceProfile,
    make_default_profile,
)


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


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    (source / "README.md").write_text("fixture\n", encoding="utf-8")
    (source / "cell.swc").write_text(
        "1 1 0 0 0 5 -1\n2 3 1 0 0 1 1\n", encoding="utf-8"
    )
    return source


def _import(tmp_path: Path):
    profile, profile_path = _profile(tmp_path)
    resource = import_local_source(
        profile,
        _source(tmp_path),
        provider="local",
        resource_id="lifecycle",
        source_version="v1",
        profile_path=profile_path,
    )
    return ResourceProfile.load(profile_path), profile_path, resource


def test_library_inventory_reports_registration_for_scanned_manifests(tmp_path: Path):
    profile, _profile_path, resource = _import(tmp_path)
    listed = list_managed_resources(profile)
    assert len(listed) == 1
    assert listed[0].is_registered
    assert listed[0].registered_bindings == (resource.registered_binding,)


def test_unregister_and_reregister_keep_managed_bytes_unchanged(tmp_path: Path):
    profile, profile_path, resource = _import(tmp_path)
    original = (resource.root / "source" / "cell.swc").read_bytes()

    removed = unregister_managed_resource(profile, resource, profile_path=profile_path)

    unregistered_profile = ResourceProfile.load(profile_path)
    listed = list_managed_resources(unregistered_profile)
    assert removed
    assert resource.root.is_dir()
    assert (resource.root / "source" / "cell.swc").read_bytes() == original
    assert not listed[0].is_registered

    added = register_managed_resource(
        unregistered_profile,
        listed[0],
        profile_path=profile_path,
    )
    restored = ResourceProfile.load(profile_path)
    assert added == tuple(
        binding.resource_id for binding in restored.bindings(ResourceKind.MORPHOLOGY_SOURCE)
    )
    assert list_managed_resources(restored)[0].is_registered


def test_reregister_supports_legacy_neuprint_export_swc_layout(tmp_path: Path):
    profile, profile_path = _profile(tmp_path)
    bundle = profile.managed_data_root / "neuprint" / "manc" / "snapshot"
    swc_root = bundle / "export_swc"
    swc_root.mkdir(parents=True)
    swc = swc_root / "42.swc"
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
    binding = ResourceBinding(
        "managed-neuprint-manc",
        ResourceKind.MORPHOLOGY_SOURCE,
        str(swc_root),
        AccessMode.READ_ONLY,
        "MANC",
        False,
        {"provider": "neuprint", "manifest": str(manifest)},
    )
    registered = ResourceProfile(
        profile.profile_id,
        (*profile.resources, binding),
        profile.label,
    )
    registered.save(profile_path, replace=True)
    resource = list_managed_resources(ResourceProfile.load(profile_path))[0]

    unregister_managed_resource(registered, resource, profile_path=profile_path)
    stored = ResourceProfile.load(profile_path)
    assert not list_managed_resources(stored)[0].is_registered
    register_managed_resource(
        stored,
        list_managed_resources(stored)[0],
        profile_path=profile_path,
    )

    restored = ResourceProfile.load(profile_path)
    restored_binding = restored.bindings(ResourceKind.MORPHOLOGY_SOURCE)[0]
    assert restored_binding.resolved_path == swc_root.resolve()
    assert swc.read_text(encoding="utf-8") == "1 1 0 0 0 1 -1\n"


def test_trash_and_restore_are_recoverable_and_preserve_profile_binding(tmp_path: Path):
    profile, profile_path, resource = _import(tmp_path)
    original_bytes = (resource.root / "source" / "cell.swc").read_bytes()
    original_binding_ids = {
        binding.resource_id for binding in profile.bindings(ResourceKind.MORPHOLOGY_SOURCE)
    }

    entry = trash_managed_resource(profile, resource, profile_path=profile_path)

    trashed_profile = ResourceProfile.load(profile_path)
    assert not resource.root.exists()
    assert entry.manifest.is_file()
    assert not trashed_profile.bindings(ResourceKind.MORPHOLOGY_SOURCE)
    assert list_managed_resources(trashed_profile) == ()
    assert list_trash_entries(trashed_profile)[0].resource_id == "lifecycle"

    restored_resource = restore_trashed_resource(
        trashed_profile,
        list_trash_entries(trashed_profile)[0],
        profile_path=profile_path,
    )

    restored_profile = ResourceProfile.load(profile_path)
    assert restored_resource.root == resource.root
    assert (resource.root / "source" / "cell.swc").read_bytes() == original_bytes
    assert {
        binding.resource_id
        for binding in restored_profile.bindings(ResourceKind.MORPHOLOGY_SOURCE)
    } == original_binding_ids
    assert not list_trash_entries(restored_profile)


def test_stored_only_resource_survives_trash_and_restores_unregistered(tmp_path: Path):
    profile, profile_path, resource = _import(tmp_path)
    unregister_managed_resource(profile, resource, profile_path=profile_path)
    stored_profile = ResourceProfile.load(profile_path)
    stored_resource = list_managed_resources(stored_profile)[0]
    assert not stored_resource.is_registered

    trash_managed_resource(stored_profile, stored_resource, profile_path=profile_path)
    trashed_profile = ResourceProfile.load(profile_path)
    restored = restore_trashed_resource(
        trashed_profile,
        list_trash_entries(trashed_profile)[0],
        profile_path=profile_path,
    )

    final = ResourceProfile.load(profile_path)
    assert restored.root.is_dir()
    assert not list_managed_resources(final)[0].is_registered
    assert final.bindings(ResourceKind.MORPHOLOGY_SOURCE) == ()


def test_trash_rolls_back_move_when_profile_save_fails(tmp_path: Path, monkeypatch):
    profile, profile_path, resource = _import(tmp_path)
    real_save = ResourceProfile.save

    def fail_save(self, path, *, replace=False):
        if Path(path).resolve() == profile_path.resolve():
            raise OSError("profile storage unavailable")
        return real_save(self, path, replace=replace)

    monkeypatch.setattr(ResourceProfile, "save", fail_save)
    with pytest.raises(OSError, match="profile storage"):
        trash_managed_resource(profile, resource, profile_path=profile_path)

    assert resource.root.is_dir()
    trash_root = profile.managed_data_root / ".trash"
    assert not trash_root.exists() or not any(trash_root.iterdir())
    assert ResourceProfile.load(profile_path).bindings(ResourceKind.MORPHOLOGY_SOURCE)


def test_relink_validates_user_moved_library_and_rewrites_managed_paths(tmp_path: Path):
    profile, profile_path, resource = _import(tmp_path)
    old_root = profile.managed_data_root
    relocated = tmp_path / "relocated-data-library"
    old_root.rename(relocated)

    preview = preview_library_relink(profile, relocated)
    assert preview.managed_binding_count == 1
    assert preview.resource_count == 1
    relink_managed_library(profile, relocated, profile_path=profile_path)

    restored = ResourceProfile.load(profile_path)
    assert restored.managed_data_root == relocated.resolve()
    binding = restored.bindings(ResourceKind.MORPHOLOGY_SOURCE)[0]
    assert binding.resolved_path == relocated / resource.root.relative_to(old_root) / "source"
    assert Path(str(binding.metadata["manifest"])).is_file()
    assert list_managed_resources(restored)[0].root == relocated / resource.root.relative_to(old_root)


def test_stored_only_registration_rebases_after_library_relink(tmp_path: Path):
    profile, profile_path, resource = _import(tmp_path)
    unregister_managed_resource(profile, resource, profile_path=profile_path)
    stored = ResourceProfile.load(profile_path)
    old_root = stored.managed_data_root
    relocated = tmp_path / "relocated-stored-library"
    old_root.rename(relocated)

    relink_managed_library(stored, relocated, profile_path=profile_path)
    relinked = ResourceProfile.load(profile_path)
    stored_resource = list_managed_resources(relinked)[0]
    register_managed_resource(relinked, stored_resource, profile_path=profile_path)

    restored = ResourceProfile.load(profile_path)
    binding = restored.bindings(ResourceKind.MORPHOLOGY_SOURCE)[0]
    assert binding.resolved_path == relocated / resource.root.relative_to(old_root) / "source"
    assert list_managed_resources(restored)[0].is_registered


def test_relink_rejects_incomplete_relocated_library_without_changing_profile(tmp_path: Path):
    profile, profile_path, _resource = _import(tmp_path)
    incomplete = tmp_path / "incomplete-library"
    incomplete.mkdir()
    original_profile = profile_path.read_bytes()

    with pytest.raises(ValueError, match="missing"):
        relink_managed_library(profile, incomplete, profile_path=profile_path)

    assert profile_path.read_bytes() == original_profile


def test_trashed_resource_restores_after_whole_library_is_moved(tmp_path: Path):
    profile, profile_path, resource = _import(tmp_path)
    trash_managed_resource(profile, resource, profile_path=profile_path)
    trashed_profile = ResourceProfile.load(profile_path)
    relocated = tmp_path / "relocated-with-trash"
    trashed_profile.managed_data_root.rename(relocated)

    relink_managed_library(trashed_profile, relocated, profile_path=profile_path)
    relinked = ResourceProfile.load(profile_path)
    entry = list_trash_entries(relinked)[0]
    restored = restore_trashed_resource(relinked, entry, profile_path=profile_path)

    final = ResourceProfile.load(profile_path)
    assert restored.root.is_dir()
    assert restored.root.is_relative_to(relocated.resolve())
    assert list_managed_resources(final)[0].is_registered
    assert not list_trash_entries(final)


def test_in_app_library_move_uses_atomic_rename_and_relinks_profile(tmp_path: Path):
    profile, profile_path, resource = _import(tmp_path)
    source_root = profile.managed_data_root
    target = tmp_path / "moved-by-app"

    result = move_managed_library(
        profile,
        target,
        profile_path=profile_path,
    )

    restored = ResourceProfile.load(profile_path)
    assert result.mode == "atomic_rename"
    assert result.source_removed
    assert not source_root.exists()
    assert restored.managed_data_root == target.resolve()
    assert (target / resource.root.relative_to(source_root) / "source/cell.swc").is_file()
    assert list_managed_resources(restored)[0].is_registered


def test_atomic_library_move_rolls_back_when_profile_save_fails(
    tmp_path: Path,
    monkeypatch,
):
    profile, profile_path, _resource = _import(tmp_path)
    source_root = profile.managed_data_root
    target = tmp_path / "rolled-back-move"
    original_profile = profile_path.read_bytes()
    real_save = ResourceProfile.save

    def fail_save(self, path, *, replace=False):
        if Path(path).resolve() == profile_path.resolve():
            raise OSError("profile storage unavailable")
        return real_save(self, path, replace=replace)

    monkeypatch.setattr(ResourceProfile, "save", fail_save)
    with pytest.raises(OSError, match="profile storage"):
        move_managed_library(profile, target, profile_path=profile_path)

    assert source_root.is_dir()
    assert not target.exists()
    assert profile_path.read_bytes() == original_profile


def test_cross_filesystem_move_copies_verifies_relinks_then_removes_source(
    tmp_path: Path,
    monkeypatch,
):
    profile, profile_path, resource = _import(tmp_path)
    source_root = profile.managed_data_root
    target = tmp_path / "verified-copy-target"
    import digifly_app.core.resource_management as lifecycle

    monkeypatch.setattr(
        lifecycle,
        "preview_library_move",
        lambda _profile, _target: LibraryMovePreview(
            source_root.resolve(), target.resolve(), False
        ),
    )
    progress = []
    result = move_managed_library(
        profile,
        target,
        profile_path=profile_path,
        progress=progress.append,
    )

    restored = ResourceProfile.load(profile_path)
    assert result.mode == "verified_copy"
    assert result.source_removed
    assert not source_root.exists()
    assert (target / resource.root.relative_to(source_root) / "source/cell.swc").is_file()
    assert restored.managed_data_root == target.resolve()
    assert progress[-1].stage == "Complete"


def test_cancelled_cross_filesystem_move_keeps_source_and_profile(
    tmp_path: Path,
    monkeypatch,
):
    profile, profile_path, _resource = _import(tmp_path)
    source_root = profile.managed_data_root
    target = tmp_path / "cancelled-copy-target"
    original_profile = profile_path.read_bytes()
    import digifly_app.core.resource_management as lifecycle

    monkeypatch.setattr(
        lifecycle,
        "preview_library_move",
        lambda _profile, _target: LibraryMovePreview(
            source_root.resolve(), target.resolve(), False
        ),
    )
    cancel = __import__("threading").Event()
    cancel.set()
    with pytest.raises(LibraryMoveCancelled):
        move_managed_library(
            profile,
            target,
            profile_path=profile_path,
            cancel=cancel,
        )

    assert source_root.is_dir()
    assert not target.exists()
    assert profile_path.read_bytes() == original_profile


def test_failed_verified_copy_cleans_candidate_and_keeps_source(
    tmp_path: Path,
    monkeypatch,
):
    profile, profile_path, _resource = _import(tmp_path)
    source_root = profile.managed_data_root
    target = tmp_path / "failed-copy-target"
    import digifly_app.core.resource_management as lifecycle

    monkeypatch.setattr(
        lifecycle,
        "preview_library_move",
        lambda _profile, _target: LibraryMovePreview(
            source_root.resolve(), target.resolve(), False
        ),
    )
    monkeypatch.setattr(
        lifecycle,
        "_verified_copy",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("copy verification failed")),
    )
    with pytest.raises(OSError, match="verification"):
        move_managed_library(profile, target, profile_path=profile_path)

    assert source_root.is_dir()
    assert not target.exists()
    assert not tuple(tmp_path.glob(".failed-copy-target.digifly-copy-*"))


def test_in_app_move_allows_an_empty_managed_library(tmp_path: Path):
    profile, profile_path = _profile(tmp_path)
    profile.managed_data_root.mkdir(parents=True)
    target = tmp_path / "empty-library-moved"

    result = move_managed_library(profile, target, profile_path=profile_path)

    assert result.file_count == 0
    assert ResourceProfile.load(profile_path).managed_data_root == target.resolve()
    assert target.is_dir()


def test_permanent_trash_purge_deletes_only_selected_receipt(tmp_path: Path):
    profile, profile_path, resource = _import(tmp_path)
    entry = trash_managed_resource(profile, resource, profile_path=profile_path)
    trashed_profile = ResourceProfile.load(profile_path)

    file_count, total_bytes = purge_trashed_resource(trashed_profile, entry)

    assert file_count >= 3
    assert total_bytes > 0
    assert not entry.trash_root.exists()
    assert not list_trash_entries(trashed_profile)
    assert trashed_profile.managed_data_root.is_dir()


def test_permanent_purge_refuses_symbolic_link_trash_entry(tmp_path: Path):
    profile, profile_path, resource = _import(tmp_path)
    entry = trash_managed_resource(profile, resource, profile_path=profile_path)
    real_entry = entry.trash_root.with_name(f"{entry.trash_root.name}-real")
    entry.trash_root.rename(real_entry)
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "keep.txt"
    marker.write_text("keep\n", encoding="utf-8")
    entry.trash_root.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symbolic-link"):
        purge_trashed_resource(ResourceProfile.load(profile_path), entry)

    assert marker.read_text(encoding="utf-8") == "keep\n"
