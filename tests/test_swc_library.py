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
from digifly_app.core.swc_library import add_bundle_to_library, library_root, inspect_existing_swcs

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
    assert sum(item.provider == "neuprint" for item in list_managed_resources(restored)) == 0
    assert not (profile.managed_data_root / "neuprint").exists()


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


def _download(profile, path, *, body=42, kind="DNp01", version="one", data=SWC,
              review=None, replacements=(), fetched=None):
    class Client:
        def fetch_skeleton(self, body_id, **kwargs):
            if fetched is not None:
                fetched.append(body_id)
            return data
    request = NeuPrintAcquisitionRequest(SERVER, DATASET, "list-or-single", version,
        NeuPrintSelection("name_list", kind), (NeuPrintNeuron(body, kind),), "fixture-token",
        library_review=review, replacement_ids=replacements)
    return acquire_neuprint_bundle(profile, path, request, client=Client(), add_to_library=True)


def test_download_duplicate_keep_skips_http_and_replacement_keeps_backup(state):
    profile, path = state
    fetched = []
    first = _download(profile, path, fetched=fetched)
    existing = inspect_existing_swcs(profile, SERVER, DATASET, (42,))
    assert existing["42"]["path"] == "export_swc/DN/DNp01/42/42.swc"
    changed = SWC.replace(b"0 5", b"0 6")
    kept = _download(profile, path, version="two", data=changed, review=existing, fetched=fetched)
    assert fetched == [42]
    target = kept.root / existing["42"]["path"]
    assert target.read_bytes() == SWC
    replaced = _download(profile, path, version="three", data=changed,
                         review=existing, replacements=(42,), fetched=fetched)
    assert fetched == [42, 42] and target.read_bytes() == changed
    receipt = json.loads(Path(json.loads(replaced.manifest.read_text())["acquisitions"][-1]["manifest"]).read_text())
    assert receipt["actions"][0]["action"] == "replaced"
    assert (replaced.root / receipt["actions"][0]["backup"]).read_bytes() == SWC
    assert len(list((replaced.root / "export_swc").rglob("*.swc"))) == 1
    assert not (profile.managed_data_root / "neuprint").exists()
    assert not list((profile.managed_data_root / ".staging").rglob("*.swc"))


def test_existing_detection_checks_unindexed_folder_bytes_and_keeps_them(state):
    profile, path = state
    root = library_root(profile, SERVER, DATASET)
    target = root / "export_swc/MN/TTMn/43/43.swc"
    target.parent.mkdir(parents=True)
    target.write_bytes(SWC)
    review = inspect_existing_swcs(profile, SERVER, DATASET, (43,))
    fetched = []
    result = _download(profile, path, body=43, kind="TTMn", review=review, fetched=fetched)
    assert fetched == [] and target.read_bytes() == SWC
    assert json.loads(result.manifest.read_text())["files"][0]["path"] == "export_swc/MN/TTMn/43/43.swc"


def test_replacement_keeps_existing_classified_folder(state):
    profile, path = state
    root = library_root(profile, SERVER, DATASET)
    target = root / 'export_swc/MN/TTMn/43/43.swc'
    target.parent.mkdir(parents=True)
    target.write_bytes(SWC)
    review = inspect_existing_swcs(profile, SERVER, DATASET, (43,))
    changed = SWC.replace(b'0 5', b'0 6')
    result = _download(profile, path, body=43, kind='TTMn', review=review,
                       replacements=(43,), data=changed)
    assert target.read_bytes() == changed
    assert json.loads(result.manifest.read_text())['files'][0]['path'] == review['43']['path']


def test_stale_replacement_approval_refuses_before_fetch_or_overwrite(state):
    profile, path = state
    first = _download(profile, path)
    review = inspect_existing_swcs(profile, SERVER, DATASET, (42,))
    target = first.root / review["42"]["path"]
    changed = SWC.replace(b"0 5", b"0 7")
    target.write_bytes(changed)
    fetched = []
    with pytest.raises(ValueError, match="changed after review"):
        _download(profile, path, version="two", review=review, replacements=(42,), fetched=fetched)
    assert fetched == [] and target.read_bytes() == changed


def test_approved_replacement_rollback_restores_files_index_and_profile(state, monkeypatch):
    profile, path = state
    first = _download(profile, path)
    review = inspect_existing_swcs(profile, SERVER, DATASET, (42,))
    before_index, before_profile = first.manifest.read_bytes(), path.read_bytes()
    def fail(*args, **kwargs):
        raise OSError("fixture profile save failed")
    monkeypatch.setattr(ResourceProfile, "save", fail)
    with pytest.raises(OSError, match="profile save failed"):
        _download(profile, path, version="two", data=SWC.replace(b"0 5", b"0 6"),
                  review=review, replacements=(42,))
    assert (first.root / review["42"]["path"]).read_bytes() == SWC
    assert first.manifest.read_bytes() == before_index and path.read_bytes() == before_profile
    assert not (first.root / ".intake-lock").exists()
    failed, = first.root.glob(".history/*/failed-download.json")
    assert json.loads(failed.read_text())["status"] == "not_committed"


def test_file_changed_during_fetch_is_not_replaced(state):
    profile, path = state
    first = _download(profile, path)
    review = inspect_existing_swcs(profile, SERVER, DATASET, (42,))
    target = first.root / review['42']['path']
    changed = SWC.replace(b'0 5', b'0 9')
    class Client:
        def fetch_skeleton(self, *args, **kwargs):
            target.write_bytes(changed)
            return SWC
    request = NeuPrintAcquisitionRequest(SERVER, DATASET, 'race', 'one',
        NeuPrintSelection('name_list', 'DNp01'), (NeuPrintNeuron(42, 'DNp01'),), 'fixture',
        library_review=review, replacement_ids=(42,))
    with pytest.raises(ValueError, match='changed after review'):
        acquire_neuprint_bundle(profile, path, request, client=Client(), add_to_library=True)
    assert target.read_bytes() == changed


def test_recovery_preserves_external_changes_and_original_backup(state, monkeypatch):
    profile, path = state
    first = _download(profile, path)
    review = inspect_existing_swcs(profile, SERVER, DATASET, (42,))
    target = first.root / review['42']['path']
    external = SWC.replace(b'0 5', b'0 9')
    def fail(*args, **kwargs):
        target.write_bytes(external)
        raise OSError('fixture changed concurrently')
    monkeypatch.setattr(ResourceProfile, 'save', fail)
    with pytest.raises(ValueError, match='during recovery'):
        _download(profile, path, version='two', data=SWC.replace(b'0 5', b'0 6'),
                  review=review, replacements=(42,))
    assert target.read_bytes() == external
    backups = list(first.root.glob('.history/*/previous/**/*.swc'))
    assert len(backups) == 1 and backups[0].read_bytes() == SWC


def test_cancelled_library_download_resumes_after_reopening_without_new_names(state):
    import threading
    from digifly_app.core.neuprint import AcquisitionCancelled
    profile, path = state
    cancel = threading.Event()
    fetched = []
    class Client:
        def fetch_skeleton(self, body_id, **kwargs):
            fetched.append(body_id)
            if body_id == 42:
                cancel.set()
            return SWC
    request = NeuPrintAcquisitionRequest(SERVER, DATASET, 'original', 'one',
        NeuPrintSelection('name_list', 'DNp01, TTMn'),
        (NeuPrintNeuron(42, 'DNp01'), NeuPrintNeuron(43, 'TTMn')), 'fixture', library_review={})
    with pytest.raises(AcquisitionCancelled):
        acquire_neuprint_bundle(profile, path, request, client=Client(), cancel=cancel, add_to_library=True)
    cancel.clear()
    result = acquire_neuprint_bundle(profile, path, replace(request, resource_id='reopened', source_version='two'),
                                    client=Client(), add_to_library=True)
    assert fetched == [42, 43] and result.swc_count == 2
    assert not list((profile.managed_data_root / '.staging').rglob('*.swc'))


def test_duplicate_files_in_tree_do_not_choose_an_arbitrary_replacement(state):
    profile, path = state
    first = _download(profile, path)
    extra = first.root / "export_swc/UNKNOWN/Other/42/42.swc"
    extra.parent.mkdir(parents=True)
    extra.write_bytes(SWC)
    with pytest.raises(ValueError, match="multiple files"):
        inspect_existing_swcs(profile, SERVER, DATASET, (42,))


@pytest.mark.parametrize('relative', ['/export_swc/42.swc', '../export_swc/42.swc',
    'export_swc/../../42.swc', 'C:/export_swc/42.swc', 'export_swc\\DN\\42.swc'])
def test_reviewed_paths_keep_portable_grammar_and_reject_escape(state, relative):
    request = NeuPrintAcquisitionRequest(SERVER, DATASET, 'review', 'one',
        NeuPrintSelection('name_list', 'DNp01'), (NeuPrintNeuron(42, 'DNp01'),), 'fixture')
    with pytest.raises(ValueError, match='Invalid reviewed'):
        replace(request, library_review={'42': {'path': relative, 'sha256': 'a' * 64}})
    valid = replace(request, library_review={'42': {'path': 'export_swc/DN/DNp01/42/42.swc', 'sha256': 'a' * 64}})
    assert valid.library_review['42']['path'].startswith('export_swc/')


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
