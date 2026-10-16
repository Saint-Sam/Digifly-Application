"""Shared Mac/Windows intake contract; fixtures are not real download acceptance."""
from dataclasses import replace
import json
from pathlib import Path

import pytest

from digifly_app.core.circuit import ConnectomeRef
from digifly_app.core.connectomes import ConnectomeCatalog
from digifly_app.core.data_library import import_local_source, list_managed_resources
from digifly_app.core.neuprint import (NeuPrintAcquisitionRequest, NeuPrintNeuron, NeuPrintSelection,
                                      _selection_cypher, acquire_neuprint_bundle)
from digifly_app.core.providers import profile_connectome_sources
from digifly_app.core.resource_profile import ResourceKind, ResourceProfile, make_standalone_profile
from digifly_app.core.swc_library import add_bundle_to_library, library_root

SERVER = "https://neuprint.janelia.org"
DATASET = "manc:v1.2.1"
SWC = b"1 1 0 0 0 5 -1\n2 3 1 0 0 1 1\n"


@pytest.fixture
def state(tmp_path):
    profile = make_standalone_profile(workstation_root=tmp_path / "Workspace with spaces")
    return profile, profile.save(tmp_path / "profile.json")


def local(tmp_path, profile, name, body="42", kind="DNp01", data=SWC, dataset=DATASET):
    source = tmp_path / name / "DN" / kind / body
    source.mkdir(parents=True)
    (source / f"{body}.swc").write_bytes(data)
    return import_local_source(profile, tmp_path / name, provider="local", resource_id=name,
                               source_version="v1", dataset=dataset)


def test_separate_searches_and_local_import_share_one_source(state, tmp_path):
    profile, path = state

    class Client:
        def fetch_skeleton(self, body_id, **kwargs):
            return SWC

    first = NeuPrintAcquisitionRequest(SERVER, DATASET, "dnp01", "v1",
        NeuPrintSelection("type_exact", "DNp01"), (NeuPrintNeuron(42, "DNp01"),), "fixture-secret")
    one = acquire_neuprint_bundle(profile, path, first, client=Client(), add_to_library=True)
    second = replace(first, resource_id="ttmn", neurons=(NeuPrintNeuron(43, "TTMn"),),
                     selection=NeuPrintSelection("type_exact", "TTMn"))
    two = acquire_neuprint_bundle(profile, path, second, client=Client(), add_to_library=True)
    assert one.root == two.root
    assert two.swc_count == 2
    imported = local(tmp_path, profile, "local-third", "44", "DNp02")
    three = add_bundle_to_library(profile, path, imported, server=SERVER, dataset=DATASET)
    assert three.swc_count == 3 and three.root == one.root
    restored = ResourceProfile.load(path)
    assert len(restored.bindings(ResourceKind.MORPHOLOGY_SOURCE)) == 1
    source, = profile_connectome_sources(restored)
    catalog = ConnectomeCatalog.scan(source)
    assert not catalog.query("DNp01, TTMn").unmatched
    assert set(catalog.by_id) == {"42", "43", "44"}
    assert (three.root / "export_swc/DN/DNp01/42/42.swc").read_bytes() == SWC
    assert all((three.root / "export_swc" / family).is_dir() for family in ("AN", "DN", "IN", "MN", "SN", "UNKNOWN"))
    manifest = json.loads(three.manifest.read_text())
    assert len(manifest["acquisitions"]) == 3
    assert all(Path(item["manifest"]).is_file() for item in manifest["acquisitions"])
    assert "fixture-secret" not in three.manifest.read_text() + path.read_text()
    assert sum(item.provider == "neuprint" for item in list_managed_resources(restored)) == 2


def test_identical_intake_is_idempotent_conflicting_bytes_do_not_replace(state, tmp_path):
    profile, path = state
    original = local(tmp_path, profile, "original")
    resource = add_bundle_to_library(profile, path, original, server=SERVER, dataset=DATASET)
    repeated = add_bundle_to_library(profile, path, original, server=SERVER, dataset=DATASET)
    assert repeated.swc_count == 1
    assert len(json.loads(repeated.manifest.read_text())["acquisitions"]) == 1
    saved_manifest, saved_profile = resource.manifest.read_bytes(), path.read_bytes()
    conflict = local(tmp_path, profile, "conflict", data=SWC.replace(b"0 5", b"0 6"))
    with pytest.raises(ValueError, match="different SWC bytes"):
        add_bundle_to_library(profile, path, conflict, server=SERVER, dataset=DATASET)
    assert resource.manifest.read_bytes() == saved_manifest and path.read_bytes() == saved_profile
    assert conflict.manifest.is_file()


def test_releases_origins_and_coordinate_units_are_not_merged(state, tmp_path):
    profile, path = state
    original = local(tmp_path, profile, "first")
    resource = add_bundle_to_library(profile, path, original, server=SERVER, dataset=DATASET)
    with pytest.raises(ValueError, match="coordinate convention"):
        add_bundle_to_library(profile, path, original, server=SERVER, dataset=DATASET,
                              coordinate_convention={"units": "other"})
    assert library_root(profile, SERVER, "manc:v1.0") != resource.root
    assert library_root(profile, "https://example.org", DATASET) != resource.root
    assert library_root(profile, SERVER, "a:b") != library_root(profile, SERVER, "a-b")


def test_symlink_destination_and_interrupted_lock_fail_closed(state, tmp_path):
    profile, path = state
    original = local(tmp_path, profile, "first")
    root = library_root(profile, SERVER, DATASET)
    root.parent.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    root.symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic"):
        add_bundle_to_library(profile, path, original, server=SERVER, dataset=DATASET)
    assert not list(elsewhere.iterdir())
    root.unlink()
    root.mkdir()
    (root / ".intake-lock").mkdir()
    with pytest.raises(ValueError, match="busy"):
        add_bundle_to_library(profile, path, original, server=SERVER, dataset=DATASET)


def test_profile_save_failure_rolls_back_additions(state, tmp_path, monkeypatch):
    profile, path = state
    original = local(tmp_path, profile, "first")
    resource = add_bundle_to_library(profile, path, original, server=SERVER, dataset=DATASET)
    extra = local(tmp_path, profile, "extra", body="45")
    previous = resource.manifest.read_bytes()
    def fail(*args, **kwargs):
        raise OSError("fixture save failure")
    monkeypatch.setattr(ResourceProfile, "save", fail)
    with pytest.raises(OSError, match="save failure"):
        add_bundle_to_library(profile, path, extra, server=SERVER, dataset=DATASET)
    assert previous == resource.manifest.read_bytes()
    assert not list((resource.root / "export_swc").rglob("45.swc"))
    assert not (resource.root / ".intake-lock").exists()


def test_uncommitted_crash_file_does_not_enter_visible_catalog(state, tmp_path):
    profile, path = state
    resource = add_bundle_to_library(profile, path, local(tmp_path, profile, "first"), server=SERVER, dataset=DATASET)
    orphan = resource.root / "export_swc/DN/DNp99/99/99.swc"
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(SWC)
    source = ConnectomeRef("test", "Test", str(resource.root / "export_swc"), DATASET)
    assert set(ConnectomeCatalog.scan(source).by_id) == {"42"}


def test_explicit_saved_bundle_consolidation_retires_only_its_old_source(state, tmp_path):
    from digifly_app.core.data_library import register_managed_morphology
    profile, path = state
    first = local(tmp_path, profile, "old-search")
    old_id = register_managed_morphology(profile, first, profile_path=path, dataset=DATASET)
    another = local(tmp_path, profile, "other-dataset", "90", dataset="male-cns:v0.9")
    current = ResourceProfile.load(path)
    other_id = register_managed_morphology(current, another, profile_path=path, dataset="male-cns:v0.9")
    library = add_bundle_to_library(profile, path, first, server=SERVER, dataset=DATASET,
                                    retire_acquisition_binding=True)
    ids = {binding.resource_id for binding in ResourceProfile.load(path).resources}
    assert old_id not in ids and other_id in ids and library.registered_binding in ids
    assert first.manifest.is_file() and another.manifest.is_file()


def test_older_neuprint_manifest_bodyid_and_bytes_fields_remain_supported(state, tmp_path):
    profile, path = state
    original = local(tmp_path, profile, "legacy")
    payload = json.loads(original.manifest.read_text())
    payload["neurons"] = [{"bodyId": 42, "type": "DNp01", "instance": "legacy"}]
    for row in payload["files"]:
        row["bytes"] = row.pop("size_bytes")
    original.manifest.write_text(json.dumps(payload))
    prior = original.manifest.read_bytes()
    result = add_bundle_to_library(profile, path, original, server=SERVER, dataset=DATASET)
    assert result.swc_count == 1 and result.integrity == "verified"
    assert (result.root / "export_swc/DN/DNp01/42/42.swc").read_bytes() == SWC
    assert original.manifest.read_bytes() == prior


@pytest.mark.parametrize("mode,field", [("type_exact", "type"), ("instance_exact", "instance")])
def test_combined_names_are_safe_union_not_one_literal(mode, field):
    query = _selection_cypher(NeuPrintSelection(mode, 'DNp01, TTMn; DNp01', 8))
    assert f'n.{field} IN ["DNp01","TTMn"]' in query
    assert "LIMIT 8" in query
    escaped = _selection_cypher(NeuPrintSelection(mode, 'DNp01, x" MATCH (z)', 8))
    assert 'x\\" MATCH (z)' in escaped
    with pytest.raises(ValueError, match="exceeds"):
        _selection_cypher(NeuPrintSelection(mode, "a,b,c", 2))
