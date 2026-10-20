from dataclasses import replace
from pathlib import Path
import threading
import pytest
from digifly_app.core.linked_libraries import list_linked_libraries, count_linked_swcs
from digifly_app.core.resource_profile import make_default_profile, ResourceBinding, ResourceKind, AccessMode


def profile(tmp_path):
    workspace = tmp_path / 'Public'
    workspace.mkdir()
    return make_default_profile(workspace_root=workspace, output_root=tmp_path / 'out', managed_data_root=tmp_path / 'managed')


def swc(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('1 1 0 0 0 1 -1\n')
    return path


def test_discovery_uses_same_workspace_and_full_manc_sources_without_crawling(tmp_path, monkeypatch):
    p = profile(tmp_path)
    male = swc(p.workspace_root / 'Phase 1/male-cns_v0.9/export_swc/MN/TTMn/800146/800146.swc').parents[3]
    manc = tmp_path / 'full-manc'
    swc(manc / 'DN/DNp01/10000/10000_axodendro_with_synapses.swc')
    (manc / '.phase2_export_index.json').write_text('{}')
    (manc / 'edges').mkdir()
    (manc / 'edges/master_edges_cache.sqlite').touch()
    managed = swc(p.managed_data_root / 'neuprint/selection/export_swc/1.swc').parent
    p = replace(p, resources=(*p.resources,
        ResourceBinding('manc', ResourceKind.MORPHOLOGY_SOURCE, str(manc), AccessMode.READ_ONLY),
        ResourceBinding('duplicate', ResourceKind.MORPHOLOGY_SOURCE, str(male), AccessMode.READ_ONLY),
        ResourceBinding('download', ResourceKind.MORPHOLOGY_SOURCE, str(managed), AccessMode.READ_ONLY)))
    monkeypatch.setattr(Path, 'rglob', lambda *a, **k: (_ for _ in ()).throw(AssertionError('Discovery crawled a library')))
    found = list_linked_libraries(p)
    assert len(found) == 2
    assert {x.root for x in found} == {male, manc}
    assert any(x.key == 'manc:v1.2.1:full-local' for x in found)
    assert any(x.label == 'Male CNS v0.9' for x in found)


def test_missing_registered_folder_stays_visible_without_creating_it(tmp_path):
    p = profile(tmp_path)
    missing = tmp_path / 'offline-drive/male-cns'
    p = replace(p, resources=(*p.resources, ResourceBinding('missing', ResourceKind.MORPHOLOGY_SOURCE,
        str(missing), AccessMode.READ_ONLY, 'My library')))
    linked, = list_linked_libraries(p)
    assert linked.root == missing and not linked.available
    assert not missing.exists() and not p.managed_data_root.exists()


def test_count_is_read_only_regular_files_not_claimed_neurons(tmp_path):
    root = tmp_path / 'swcs'
    a, b = swc(root / 'AN/1.SWC'), swc(root / 'DN/2.swc')
    swc(root / '.history/old.swc')
    external = swc(tmp_path / 'outside/3.swc')
    (root / 'linked.swc').symlink_to(external)
    (root / 'linked-folder').symlink_to(external.parent, target_is_directory=True)
    before = {p: p.read_bytes() for p in (a, b, external)}
    result = count_linked_swcs(root)
    assert result.swc_files == 2 and result.swc_bytes == sum(len(before[p]) for p in (a, b))
    assert result.skipped_links == 2
    assert {p: p.read_bytes() for p in before} == before
    assert not list(root.glob('*.json'))


def test_count_cancel_and_unavailable_folder_do_not_report_partial_success(tmp_path):
    event = threading.Event()
    event.set()
    root = swc(tmp_path / 'data/1.swc').parent
    with pytest.raises(InterruptedError, match='cancelled'):
        count_linked_swcs(root, cancel=event)
    with pytest.raises(ValueError, match='unavailable'):
        count_linked_swcs(tmp_path / 'missing')


def test_unreadable_subtree_fails_count(tmp_path, monkeypatch):
    root = swc(tmp_path / 'data/1.swc').parent
    import digifly_app.core.linked_libraries as module
    def fail(*args, **kwargs):
        kwargs['onerror'](PermissionError('unreadable subtree'))
        return iter(())
    monkeypatch.setattr(module.os, 'walk', fail)
    with pytest.raises(PermissionError):
        count_linked_swcs(root)
