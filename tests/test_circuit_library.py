"""Connectome/release bridge, exact saved bytes, and compact native controls."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from dataclasses import replace
from pathlib import Path
import pytest
from PySide6.QtWidgets import QApplication, QLineEdit, QInputDialog
from PySide6.QtTest import QTest
from PySide6.QtCore import QPoint
from digifly_app.core.circuit import ConnectomeRef, CircuitSpec
from digifly_app.core.circuit_library import library_sources, source_folders
from digifly_app.core.connectomes import ConnectomeCatalog
from digifly_app.core.saved_neurons import scan_saved_neurons
from digifly_app.core.resource_profile import make_default_profile, ResourceBinding, ResourceKind, AccessMode
from digifly_app.ui.connectome_picker import CONNECTOMES

RELEASES = tuple(r for c in CONNECTOMES for r in c.releases)
SWC = ('# Source: Janelia male-cns v0.9 public skeleton release\n'
       '# x/y/z/r converted from8nm units to micrometers, scale0.008\n'
       '1 1 0 0 0 2 -1\n2 3 1 0 0 1 1\n')


def test_incomplete_graph_binding_does_not_hide_morphology_choices(tmp_path):
    profile, roots = fixture(tmp_path)
    bad = ResourceBinding('incomplete', ResourceKind.DATA_SOURCE, str(tmp_path / 'missing.parquet'),
        AccessMode.READ_ONLY, metadata={'format': 'digifly-weighted-pair-graph-v1', 'dataset': 'male-cns:v0.9'})
    choices = library_sources(replace(profile, resources=(*profile.resources, bad)), RELEASES)
    source = next(s for s in choices if s.dataset == 'male-cns:v0.9')
    assert source.swc_sources and source.pair_graph_path.endswith('missing.parquet')
    from digifly_app.core.connectomes import ConnectomeEdgeCatalog
    assert not ConnectomeEdgeCatalog(source)._chemical_summary('1', '2').available


def fixture(tmp_path):
    public = tmp_path / 'Public'
    roots = [public / 'Phase 1/male-cns_v0.9/export_swc', tmp_path / 'external']
    for root, body, kind in ((roots[0], '1', 'DNp01'), (roots[1], '2', 'TTMn')):
        path = root / f'DN/{kind}/{body}/{body}.swc'
        path.parent.mkdir(parents=True)
        path.write_text(SWC)
    profile = make_default_profile(workspace_root=public, output_root=tmp_path / 'runs', managed_data_root=tmp_path / 'managed')
    profile = replace(profile, resources=(*profile.resources, ResourceBinding('external-swcs',
        ResourceKind.MORPHOLOGY_SOURCE, str(roots[1]), AccessMode.READ_ONLY, metadata={'dataset': 'male-cns:v0.9'})))
    return profile, roots


def test_direct_library_handoff_initializes_exact_family(tmp_path, monkeypatch):
    from digifly_app.ui.data_library import DataLibraryPage
    profile, _ = fixture(tmp_path)
    path = profile.save(tmp_path / 'resources.json')
    monkeypatch.setenv('DIGIFLY_WORKSTATION_PROFILE', str(path))
    app = QApplication.instance() or QApplication([])
    page = DataLibraryPage()
    try:
        assert not page.connectome_picker.selected_key
        for choice in CONNECTOMES:
            for dataset in choice.releases:
                page.open_connectome(dataset)
                assert page.connectome_picker.selected_key == choice.key
                assert page.connectome_picker.selected_dataset == dataset
                assert page._display_dataset == dataset
                assert page.view_stack.currentWidget() is page.scroll
    finally:
        page.neuron_browser.shutdown()
        page.close()
        app.processEvents()


def test_union_exact_releases_pinned_roundtrip_and_conflicting_bytes(tmp_path):
    profile, roots = fixture(tmp_path)
    choices = library_sources(profile, RELEASES)
    assert {s.dataset for s in choices} == set(RELEASES)
    source = next(s for s in choices if s.dataset == 'male-cns:v0.9')
    before = {p: p.read_bytes() for root in roots for p in root.rglob('*.swc')}
    catalog = ConnectomeCatalog.scan(source)
    assert set(catalog.by_id) == {'1', '2'}
    assert not catalog.query('DNp01, TTMn').unmatched
    empty = next(s for s in choices if s.dataset == 'male-cns:v1.0')
    assert not ConnectomeCatalog.scan(empty).records
    duplicate = roots[1] / 'DN/DNp01/1/1.swc'
    duplicate.parent.mkdir(parents=True)
    duplicate.write_text(SWC.replace('1 0 0 1', '2 0 0 1'))
    catalog = ConnectomeCatalog.scan(source)
    with pytest.raises(ValueError, match='different SWC copies'):
        catalog.verify_selection(catalog.query('1').records)
    original = next(p for p in before if p.stem == '1')
    pinned = replace(source, swc_paths={'1': str(original)})
    restored = ConnectomeRef.from_dict(pinned.to_dict())
    assert restored == pinned
    assert ConnectomeCatalog.scan(restored).by_id['1'].swc_path == str(original)
    catalog = ConnectomeCatalog.scan(restored)
    catalog.verify_selection(catalog.query('1').records)
    assert all(p.read_bytes() == value for p, value in before.items())
    old = ConnectomeRef('legacy', 'Old exact source', str(roots[0]), 'male-cns_v0.9')
    assert ConnectomeRef.from_dict(old.to_dict()) == old
    assert ConnectomeCatalog.scan(old).by_id['1'].swc_path == str(original)


def test_distinct_servers_never_merge_body_ids(tmp_path):
    profile, roots = fixture(tmp_path)
    binding = profile.resources[-1]
    profile = replace(profile, resources=(*profile.resources[:-1], replace(binding,
        metadata={**binding.metadata, 'server': 'https://another.example'})))
    sources = [s for s in library_sources(profile, RELEASES) if s.dataset == 'male-cns:v0.9']
    assert len(sources) == 2
    assert {s.server: set(ConnectomeCatalog.scan(s).by_id) for s in sources} == {
        'https://neuprint.janelia.org': {'1'}, 'https://another.example': {'2'}}


def test_missing_family_ttmn_name_reaches_experiment_without_hiding_copy_choice(tmp_path):
    from digifly_app.core.saved_neurons import SavedSource
    from digifly_app.core.morphology import load_swc
    from digifly_app.ui.experiment_builder import ExperimentBuilderPage
    roots = (tmp_path / 'local', tmp_path / 'download')
    originals = {}
    for root, family in zip(roots, ('MN', 'UNKNOWN')):
        for body in ('800146', '804642'):
            path = root / f'{family}/TTMn/{body}/{body}.swc'
            path.parent.mkdir(parents=True)
            path.write_text(SWC if family == 'MN' else SWC.replace('1 0 0 1', '2 0 0 1'))
            originals[path] = path.read_bytes()
    source = ConnectomeRef('library:male-cns:v0.9', 'Male CNS', str(roots[0]),
                           'male-cns:v0.9', server='https://neuprint.janelia.org')
    inventory = scan_saved_neurons(tuple(SavedSource(r, source.dataset) for r in roots), source.dataset)
    catalog = ConnectomeCatalog.from_saved_inventory(source, inventory)
    for query in ('TTMn', 'type:ttmn', 'TTM*', 'family:MN', 'TTMn, 800146'):
        result = catalog.query(query)
        assert not result.unmatched and {r.neuron_id for r in result.records} == {'800146', '804642'}
        assert all((r.family, r.neuron_type) == ('MN', 'TTMn') for r in result.records)
    assert catalog.query('TTMn', limit=1).truncated
    with pytest.raises(ValueError, match='different SWC copies'):
        catalog.verify_selection(catalog.query('TTMn').records)
    pinned = replace(source, swc_paths={body: str(roots[0] / f'MN/TTMn/{body}/{body}.swc')
                                      for body in ('800146', '804642')})
    catalog = ConnectomeCatalog.from_saved_inventory(pinned, inventory)
    records = catalog.query('TTMn').records
    catalog.verify_selection(records)
    app = QApplication.instance() or QApplication([])
    page = ExperimentBuilderPage(output_root=tmp_path / 'runs')
    try:
        circuit = CircuitSpec(connectome=pinned, neuron_ids=tuple(r.neuron_id for r in records))
        page.set_circuit_snapshot(circuit, tuple(load_swc(r) for r in records))
        page.stimulus_targets.setText('ttmn')
        assert page._resolve_stimulus_targets(all_if_blank=False) == (('800146', '804642'), ())
        assert page._target_neuron_ids() == (('800146', '804642'), ())
        assert not (tmp_path / 'runs').exists()
    finally:
        page.close()
        app.processEvents()
    assert all(p.read_bytes() == data for p, data in originals.items())


def test_documented_manc_instance_aliases_are_release_and_server_bound(tmp_path):
    from digifly_app.core.saved_neurons import SavedSource
    from digifly_app.core.morphology import load_swc
    from digifly_app.ui.experiment_builder import ExperimentBuilderPage
    root = tmp_path / 'export_swc'
    for family, kind, body in (('MN', 'TTMn', '10068'),
                               ('UNKNOWN', 'MNwm34_PDMNp_L', '10068'),
                               ('UNKNOWN', 'MNwm34_PDMNp_R', '10110')):
        path = root / f'{family}/{kind}/{body}/{body}.swc'
        path.parent.mkdir(parents=True)
        path.write_text(SWC)
    source = ConnectomeRef('library:manc:v1.2.1', 'MANC', str(root), 'manc:v1.2.1',
                           server='https://neuprint.janelia.org')
    inventory = scan_saved_neurons((SavedSource(root, source.dataset),), source.dataset)
    catalog = ConnectomeCatalog.from_saved_inventory(source, inventory)
    assert {r.neuron_id for r in catalog.query('TTMn').records} == {'10068', '10110'}
    assert tuple(r.neuron_id for r in catalog.query('type:MNwm34_PDMNp_L').records) == ('10068',)
    assert tuple(r.neuron_id for r in catalog.query('MNwm34_PDMNp_R').records) == ('10110',)
    app = QApplication.instance() or QApplication([])
    page = ExperimentBuilderPage(output_root=tmp_path / 'runs')
    try:
        records = catalog.query('TTMn').records
        page.set_circuit_snapshot(CircuitSpec(connectome=source, neuron_ids=('10068', '10110')),
                                  tuple(load_swc(r) for r in records))
        for name, expected in (('TTMn', ('10068', '10110')), ('MNwm34_PDMNp_L', ('10068',))):
            page.stimulus_targets.setText(name)
            assert page._target_neuron_ids() == (expected, ())
    finally:
        page.close()
        app.processEvents()
    other = scan_saved_neurons((SavedSource(root, source.dataset, 'https://other.example'),), source.dataset)
    assert next(n for n in other.neurons if n.body_id == '10068').label_conflict
    assert next(n for n in other.neurons if n.body_id == '10110').neuron_type == 'MNwm34_PDMNp_R'
    other = scan_saved_neurons((SavedSource(root, 'manc:v1.2.3'),), 'manc:v1.2.3')
    assert next(n for n in other.neurons if n.body_id == '10068').label_conflict


def test_compact_controls_use_shared_inventory_and_browse_without_loading(tmp_path, monkeypatch):
    from digifly_app.ui.circuit_builder import CircuitBuilderPage
    profile, roots = fixture(tmp_path)
    profile_path = profile.save(tmp_path / 'resources.json')
    before_profile = profile_path.read_bytes()
    monkeypatch.setenv('DIGIFLY_WORKSTATION_PROFILE', str(profile_path))
    app = QApplication.instance() or QApplication([])
    class Overview:
        workspace_edit = QLineEdit(str(profile.workspace_root))
    page = CircuitBuilderPage(Overview())
    try:
        page.select_library_release('male-cns:v0.9')
        for _ in range(500):
            app.processEvents()
            if page.saved_browser.inventory is not None:
                break
            QTest.qWait(10)
        assert page.saved_browser.inventory is not None
        assert page.family_combo.currentData() == 'male_cns'
        assert page.release_combo.currentText() == 'v0.9'
        assert not page.more_options.is_expanded and page.connectome_combo.isHidden()
        page.browse_button.setChecked(True)
        browser = page.saved_browser
        page.resize(1120, 800)
        page.show()
        app.processEvents()
        boxes = [b.geometry() for b in browser.family_buttons.values()]
        assert all(browser.stack.currentWidget().rect().contains(r) for r in boxes)
        assert all(not a.intersects(b) for i, a in enumerate(boxes) for b in boxes[i+1:])
        # Each inline chooser belongs immediately below its own control row.
        # In particular Simulator must not reuse the Connectome position.
        for width in (1120, 1700):
            page.resize(width, 900)
            for button, combo, kind, following in (
                    (page.engine_button, page.engine_combo, 'simulator', browser),
                    (page.family_button, page.family_combo, 'connectome', page.source_summary),
                    (page.engine_button, page.engine_combo, 'simulator', browser),
                    (page.release_button, page.release_combo, 'release', page.source_summary)):
                button.click()
                app.processEvents()
                panel = page.choice_panel
                top = panel.mapTo(page, QPoint(0, 0)).y()
                assert panel.isVisible()
                assert top >= button.mapTo(page, QPoint(0, button.height())).y()
                assert top + panel.height() <= following.mapTo(page, QPoint(0, 0)).y()
                assert all(panel.rect().contains(b.geometry()) for b in page.choice_buttons)
            page.choice_buttons[-1].click()
            assert page.choice_panel.isHidden()
        browser.family_buttons['DN'].click()
        browser.row_buttons[0].click()
        browser.row_buttons[0].click()
        browser.choose_neuron.click()
        assert page.query_edit.text() == '1' and not page.loaded_records
        page.query_edit.setText('DNp01, TTMn')
        page.assemble_circuit()
        assert set(page.loaded_records) == {'1', '2'}, page.viewport_summary.text()
        saved = CircuitSpec.from_dict(page.circuit_spec().to_dict())
        assert saved.connectome.swc_paths == {k: r.swc_path for k, r in page.loaded_records.items()}
        # A subsequent query must reuse the worker's inventory, not recursively
        # scan a whole connectome on the UI thread after paths are pinned.
        monkeypatch.setattr(ConnectomeCatalog, 'scan', lambda *a, **k: pytest.fail('UI performed a recursive scan'))
        page.query_edit.setText('TTMn')
        page.assemble_circuit()
        assert set(page.loaded_records) == {'2'}
        page.set_circuit_spec(saved)
        page.load_saved_assets()
        assert set(page.loaded_records) == {'1', '2'}
        # A refresh invalidates pending choices; a delayed old action must not
        # select another connectome after the list has been rebuilt.
        page._open_inline_choices(page.family_combo, 'connectome')
        stale = page.choice_buttons[1]
        source_key = page._selected_source().key
        page.refresh_connectomes()
        stale.clicked.emit()
        assert page._selected_source().key == source_key
        # An explicit same-release choice leaves a legacy single-root view
        # and selects the combined library, rather than doing nothing.
        page.connectome_combo.setCurrentIndex(page.connectome_combo.findData('male-cns:v0.9'))
        assert not page._selected_source().server
        page._open_inline_choices(page.release_combo, 'release')
        page.choice_buttons[page.release_combo.currentIndex()].click()
        assert page._selected_source().server
        # A new release never reloads the previous cell set by accident.
        page.select_library_release('male-cns:v1.0')
        assert not page.loaded_records and not page.assemble_button.isEnabled()
        assert profile_path.read_bytes() == before_profile
    finally:
        page.close()
        app.processEvents()
