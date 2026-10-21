import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import json
from pathlib import Path
import threading
import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from digifly_app.core.saved_neurons import SavedSource, SavedInventory, scan_saved_neurons
from digifly_app.core.swc_library import FAMILIES, LIBRARY_SCHEMA
from digifly_app.ui.saved_neurons import SavedNeuronBrowser

DATASET = 'male-cns:v0.9'


def swc(root, relative):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('1 1 0 0 0 1 -1\n')
    return path


def wait(predicate):
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(10)
        time.sleep(.005)
    assert predicate()


def test_unique_bodies_variants_overlapping_roots_and_other_releases(tmp_path):
    root = tmp_path / 'export_swc'
    a = swc(root, 'DN/DNp01/42/42_raw.swc')
    b = swc(root, 'DN/DNp01/42/42_healed.swc')
    swc(root, 'MN/TTMn/43/43.swc')
    swc(root, '.history/DN/DNp01/90/90.swc')
    swc(root, 'by_id/91/91.swc')
    swc(root, 'SWC_Circuits/92/92.swc')
    other = tmp_path / 'other'
    swc(other, 'DN/DNp01/99/99.swc')
    before = {str(p): p.read_bytes() for p in tmp_path.rglob('*.swc')}
    result = scan_saved_neurons((SavedSource(root, DATASET), SavedSource(root / 'DN', DATASET),
                                 SavedSource(root, DATASET), SavedSource(other, 'male-cns:v1.0')), DATASET)
    assert len(result.neurons) == 2 and not result.issues
    neuron = result.family('DN')[0]
    assert neuron.body_id == '42' and set(neuron.paths) == {a, b}
    assert {str(p): p.read_bytes() for p in tmp_path.rglob('*.swc')} == before


def test_server_identity_labels_unknown_unidentified_and_missing(tmp_path):
    root = tmp_path / 'root'
    swc(root, 'UNKNOWN/DNp01/42/42.swc')
    swc(root, 'PSI/43/43_neuprint_raw.swc')
    swc(root, 'unidentified.swc')
    alternate = tmp_path / 'alternate'
    swc(alternate, 'MN/TTMn/42/42.swc')
    result = scan_saved_neurons((SavedSource(root, DATASET), SavedSource(alternate, DATASET),
        SavedSource(alternate, DATASET, 'https://other.example'), SavedSource(tmp_path / 'missing', DATASET)), DATASET)
    assert len(result.neurons) == 3 and result.unidentified_files == 1 and len(result.issues) == 1
    assert result.family('MN')[0].server == 'https://other.example'
    conflict = next(n for n in result.neurons if n.label_conflict)
    assert conflict.family == 'UNKNOWN' and len(conflict.paths) == 2
    assert next(n for n in result.neurons if n.body_id == '43').neuron_type == 'PSI'
    single = scan_saved_neurons((SavedSource(root, DATASET),), DATASET)
    assert single.neurons[0].family == 'UNKNOWN'  # explicit UNKNOWN never guessed as DN


def test_no_follow_links_cancel_and_inaccessible_subtree(tmp_path, monkeypatch):
    root = tmp_path / 'root'
    swc(root, 'DN/DNp01/42/42.swc')
    outside = tmp_path / 'outside'
    file = swc(outside, '100.swc')
    if os.name != 'nt':
        (root / 'linked').symlink_to(outside, target_is_directory=True)
        (root / 'file.swc').symlink_to(file)
        result = scan_saved_neurons((SavedSource(root, DATASET),), DATASET)
        assert len(result.neurons) == 1 and result.skipped_links == 2
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(InterruptedError):
        scan_saved_neurons((SavedSource(root, DATASET),), DATASET, cancel=cancel)
    original = os.scandir
    def denied(path):
        if Path(path).name == 'DN':
            raise PermissionError('fixture denied')
        return original(path)
    monkeypatch.setattr(os, 'scandir', denied)
    result = scan_saved_neurons((SavedSource(root, DATASET),), DATASET)
    assert not result.neurons and 'fixture denied' in result.issues[0]


def test_committed_manifest_missing_files_uncommitted_and_unsafe(tmp_path):
    from digifly_app.core.resource_profile import make_standalone_profile
    # Reuse production intake instead of fabricating a supposedly valid manifest.
    profile = make_standalone_profile(workstation_root=tmp_path / 'workspace')
    profile_path = profile.save(tmp_path / 'profile.json')
    source = tmp_path / 'source'
    file = swc(source, 'DN/DNp01/42/42.swc')
    from digifly_app.core.data_library import import_local_source
    from digifly_app.core.swc_library import add_bundle_to_library
    resource = import_local_source(profile, source, provider='local', resource_id='test', source_version='fixture', dataset=DATASET)
    resource = add_bundle_to_library(profile, profile_path, resource, server='https://neuprint.janelia.org', dataset=DATASET,
        coordinate_convention={'units': 'um', 'coordinate_scale_to_um': 1.0, 'radius_scale_to_um': 1.0, 'basis': 'engineering fixture'})
    from digifly_app.core.saved_neurons import saved_sources
    sources = saved_sources((), (resource,))
    result = scan_saved_neurons(sources, DATASET)
    assert len(result.neurons) == 1 and not result.issues
    swc(sources[0].root, 'DN/DNp01/99/99.swc')
    assert len(scan_saved_neurons(sources, DATASET).neurons) == 1
    saved = result.neurons[0].paths[0]
    saved.rename(saved.with_suffix('.absent'))
    result = scan_saved_neurons(sources, DATASET)
    assert not result.neurons and 'listed SWC' in result.issues[0]


def test_browser_real_clicks_counts_types_ids_paging_stale_and_cancel(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    root = tmp_path / 'export_swc'
    for body in range(1, 205):
        swc(root, f'DN/DNp01/{body}/{body}.swc')
    browser = SavedNeuronBrowser()
    browser.resize(780, 650)
    browser.show()
    try:
        browser.load((SavedSource(root, DATASET),), DATASET)
        wait(lambda: browser.inventory is not None)
        assert tuple(browser.family_buttons) == FAMILIES
        assert '204' in browser.family_buttons['DN'].text()
        assert all(not button.isCheckable() for button in browser.family_buttons.values())
        QTest.mouseClick(browser.family_buttons['DN'], Qt.MouseButton.LeftButton)
        assert browser.row_values[0][0] == 'DNp01'
        stale = browser.row_buttons[0]
        from PySide6.QtGui import QAccessible
        from PySide6.QtWidgets import QTableWidget
        assert not browser.findChildren(QTableWidget)
        assert QAccessible.queryAccessibleInterface(stale).text(QAccessible.Text.Name).startswith('Open type DNp01')
        QTest.mouseClick(browser.row_buttons[0], Qt.MouseButton.LeftButton)
        before = browser._selected_row
        stale.click()
        assert browser._selected_row == before  # retired row cannot target a new neuron
        assert len(browser.row_buttons) == 200 and browser.next.isEnabled()
        browser.next.click()
        assert len(browser.row_buttons) == 4 and browser.row_values[0][0] == '201'
        browser.search.setText('204')
        assert len(browser.row_buttons) == 1 and not browser.next.isEnabled()
        browser.back.click()
        browser.back.click()
        assert browser.stack.currentIndex() == 0
        QTest.mouseClick(browser.family_buttons['AN'], Qt.MouseButton.LeftButton)
        assert len(browser.row_buttons) == 0
        old = browser.inventory
        generation = browser.generation
        browser.load((), 'male-cns:v1.0')
        assert all('not ready' in b.accessibleName() for b in browser.family_buttons.values())
        browser._received(generation, old)
        wait(lambda: browser.inventory is not None)
        assert not browser.inventory.neurons and browser.inventory.dataset == 'male-cns:v1.0'
        browser.load((SavedSource(root, DATASET),), DATASET)
        browser.cancel_button.click()
        wait(lambda: not browser._workers)
        assert browser.inventory is None and 'cancelled' in browser.status.text()
    finally:
        browser.shutdown()
        browser.close()


def test_late_worker_cannot_restore_old_release_and_shutdown_waits(tmp_path, monkeypatch):
    from digifly_app.ui import saved_neurons as ui
    app = QApplication.instance() or QApplication([])
    started, release = threading.Event(), threading.Event()
    def slow(sources, dataset, **kwargs):
        if dataset == DATASET:
            started.set()
            release.wait(4)
        return SavedInventory(dataset, ())
    monkeypatch.setattr(ui, 'scan_saved_neurons', slow)
    browser = SavedNeuronBrowser()
    try:
        browser.load((), DATASET)
        wait(started.is_set)
        browser.load((), 'male-cns:v1.0')
        wait(lambda: browser.inventory is not None)
        release.set()
        wait(lambda: not browser._workers)
        assert browser.inventory.dataset == 'male-cns:v1.0'
        assert not browser.open_folder.isEnabled()
    finally:
        release.set()
        browser.shutdown()
        browser.close()


def test_junctions_skipped_and_explicit_linked_server_preserved(tmp_path, monkeypatch):
    from digifly_app.core.linked_libraries import LinkedLibrary, path_key
    from digifly_app.core.saved_neurons import saved_sources
    root = tmp_path / 'export_swc'
    swc(root, 'DN/DNp01/42/42.swc')
    swc(root, 'junction/99.swc')
    monkeypatch.setattr(os.path, 'isjunction', lambda p: Path(p).name == 'junction', raising=False)
    linked = LinkedLibrary('example', 'Example', DATASET, root, 'fixture', True)
    sources = saved_sources((linked,), (), {path_key(root): 'https://other.example'})
    result = scan_saved_neurons(sources, DATASET)
    assert result.skipped_links == 1 and len(result.neurons) == 1
    assert result.neurons[0].server == 'https://other.example'


def test_old_root_level_export_and_raw_healed_legacy_paths(tmp_path):
    from types import SimpleNamespace
    from digifly_app.core.saved_neurons import saved_sources
    root = tmp_path / 'legacy'
    swc(root, 'export_swc/DN/DNp01/42/42_raw.swc')
    swc(root, 'export_swc/DN/DNp01/42/42_healed.swc')
    manifest = root / 'manifest.json'
    manifest.write_text(json.dumps({'dataset': DATASET}))
    resource = SimpleNamespace(root=root, manifest=manifest, source_version=DATASET)
    result = scan_saved_neurons(saved_sources((), (resource,)), DATASET)
    assert not result.issues and len(result.neurons) == 1
    assert result.neurons[0].family == 'DN' and len(result.neurons[0].paths) == 2


def test_cancellation_inside_scan_never_returns_a_partial_count(tmp_path, monkeypatch):
    root = tmp_path / 'root'
    swc(root, 'DN/DNp01/42/42.swc')
    cancel = threading.Event()
    original = os.scandir
    def cancel_inside(path):
        result = original(path)
        cancel.set()
        return result
    monkeypatch.setattr(os, 'scandir', cancel_inside)
    with pytest.raises(InterruptedError):
        scan_saved_neurons((SavedSource(root, DATASET),), DATASET, cancel=cancel)
