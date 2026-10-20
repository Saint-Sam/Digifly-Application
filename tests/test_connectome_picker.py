import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import hashlib
import json

import pytest
from PySide6.QtWidgets import QApplication, QComboBox
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt
from digifly_app.ui.connectome_picker import CONNECTOMES, ConnectomePicker, picture_path
from digifly_app.ui.data_library import DataLibraryPage
from digifly_app.ui.neuprint_import import NeuPrintImportDialog
from digifly_app.core.neuprint import NeuPrintDataset
from digifly_app.core.resource_profile import make_standalone_profile


def test_catalogue_matches_public_snapshot_and_exact_original_pictures():
    receipt = json.loads((picture_path('male_cns').parent / 'connectome_pictures.json').read_text())
    assert set(k for item in CONNECTOMES for k in item.releases) == {
        dataset for record in receipt['pictures'].values() for dataset in record['datasets']}
    assert len(CONNECTOMES) == 5
    assert not receipt['credential_used']
    for item in CONNECTOMES:
        path = picture_path(item.key)
        record = receipt['pictures'][path.name]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == record['sha256']
        assert set(item.releases) == set(record['datasets'])
    with pytest.raises(ValueError):
        picture_path('../../other')


@pytest.mark.parametrize('width', [780, 1120, 1700])
def test_picker_images_navigation_buttons_releases_and_responsive_layout(width):
    app = QApplication.instance() or QApplication([])
    picker = ConnectomePicker()
    picker.resize(width, 900)
    picker.show()
    app.processEvents()
    try:
        assert picker.selected_dataset == ''
        assert all(not image.pixmap().isNull() for image in picker.pictures.values())
        picker.buttons['male_cns'].click()
        assert picker.selected_dataset == 'male-cns:v1.0'
        picker.select_release('male-cns:v0.9')
        assert picker.selected_dataset == 'male-cns:v0.9'
        picker.buttons['manc'].click()
        assert picker.selected_dataset == 'manc:v1.2.3'
        picker.buttons['male_cns'].click()
        assert picker.selected_dataset == 'male-cns:v0.9'
        with pytest.raises(ValueError):
            picker.select_release('manc:v1.2.1')
        assert not picker.findChildren(QComboBox)
        assert all(not button.isCheckable() for button in picker.buttons.values())
        rects = [card.geometry() for card in picker.cards.values()]
        assert all(not a.intersects(b) for i, a in enumerate(rects) for b in rects[i+1:])
        assert all(picker.rect().contains(rect) for rect in rects)
    finally:
        picker.close()


@pytest.mark.parametrize('width', [840, 1120, 1700])
def test_page_inline_navigation_and_release_without_changing_profile(width, tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    p = make_standalone_profile(workstation_root=tmp_path / 'work')
    path = p.save(tmp_path / 'resources.json')
    monkeypatch.setattr(DataLibraryPage, '_profile', lambda self: (p, path))
    page = DataLibraryPage()
    page.resize(width, 760)
    page.show()
    app.processEvents()
    before = path.read_bytes()
    chosen = []
    def inspect_dialog(dialog):
        chosen.append(dialog._preferred_dataset)
        return 0
    monkeypatch.setattr(NeuPrintImportDialog, 'exec', inspect_dialog)
    try:
        assert page.view_stack.currentWidget() is page.picker_scroll
        assert page.connectome_picker.isVisible() and not page.add_data_button.isVisible()
        QTest.mouseClick(page.connectome_picker.buttons['male_cns'], Qt.MouseButton.LeftButton)
        app.processEvents()
        assert page.view_stack.currentWidget() is page.scroll
        assert page.add_data_button.isVisible() and not page.connectome_picker.isVisible()
        assert page.connectome_title.text() == 'Male CNS'
        assert QApplication.activeModalWidget() is None
        assert page.release_buttons['male-cns:v0.9'].isVisible()
        assert not page.release_buttons['manc:v1.2.1'].isVisible()
        QTest.mouseClick(page.release_buttons['male-cns:v0.9'], Qt.MouseButton.LeftButton)
        assert page.connectome_picker.selected_dataset == 'male-cns:v0.9'
        # Native accessibility activates a checkable button through its checked
        # state, not necessarily the clicked signal. Both input routes work.
        page.release_buttons['male-cns:v1.0'].setChecked(True)
        assert page.connectome_picker.selected_dataset == 'male-cns:v1.0'
        page.release_buttons['male-cns:v0.9'].setChecked(True)
        assert page.connectome_picker.selected_dataset == 'male-cns:v0.9'
        assert QApplication.activeModalWidget() is None
        QTest.mouseClick(page.back_to_connectomes, Qt.MouseButton.LeftButton)
        app.processEvents()
        assert page.view_stack.currentWidget() is page.picker_scroll
        assert not page.add_data_button.isVisible()
        QTest.mouseClick(page.connectome_picker.buttons['manc'], Qt.MouseButton.LeftButton)
        app.processEvents()
        QTest.mouseClick(page.release_buttons['manc:v1.2.1'], Qt.MouseButton.LeftButton)
        assert QApplication.activeModalWidget() is None
        page.scroll.ensureWidgetVisible(page.neuprint_button)
        app.processEvents()
        QTest.mouseClick(page.neuprint_button, Qt.MouseButton.LeftButton)
        assert chosen == ['manc:v1.2.1']
        assert path.read_bytes() == before and not p.managed_data_root.exists()
        assert not page.linked_card.isVisible() and not page.managed_card.isVisible()
        assert page.add_data_button.isVisible() and page.load_link_button in page.add_data_menu.actions()
    finally:
        page.close()


def test_empty_release_does_not_stretch_headings_in_a_tall_window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    profile = make_standalone_profile(workstation_root=tmp_path / 'work')
    path = profile.save(tmp_path / 'resources.json')
    monkeypatch.setattr(DataLibraryPage, '_profile', lambda self: (profile, path))
    page = DataLibraryPage()
    page.resize(1700, 1400)
    page.show()
    try:
        for dataset in ('manc:v1.2.3', 'manc:v1.2.1', 'male-cns:v1.0'):
            family = next(choice.key for choice in CONNECTOMES if dataset in choice.releases)
            page.connectome_picker.choose(family)
            page.release_buttons[dataset].setChecked(True)
            app.processEvents()
            for label in (page.connectome_title, page.release_detail, page.data_heading):
                natural = label.heightForWidth(label.width()) if label.hasHeightForWidth() else label.sizeHint().height()
                assert label.height() <= natural + 2
    finally:
        page.close()


def test_unavailable_preferred_release_does_not_silently_select_a_different_connectome(tmp_path):
    app = QApplication.instance() or QApplication([])
    p = make_standalone_profile(workstation_root=tmp_path / 'work')
    path = p.save(tmp_path / 'resources.json')
    dialog = NeuPrintImportDialog(p, path, preferred_dataset='male-cns:v0.9')
    try:
        dialog._active_token = 'engineering-not-a-credential'
        dialog._connected((NeuPrintDataset('manc:v1.2.1'),))
        dialog._set_busy(False)
        assert dialog.dataset_combo.currentIndex() == -1
        assert not dialog.preview_button.isEnabled()
        assert 'not available' in dialog.status.text()
        dialog.dataset_combo.setCurrentIndex(0)
        assert dialog.preview_button.isEnabled()
    finally:
        dialog.close()


def test_strict_release_identity_and_parquet_pin():
    from digifly_app.core.linked_libraries import canonical_dataset, pair_binding_dataset
    from digifly_app.core.bundled_pair_graph import PAIR_SHA256
    assert canonical_dataset('male-cns_v0.9') == 'male-cns:v0.9'
    assert canonical_dataset('manc_v1.2.1') == 'manc:v1.2.1'
    for unknown in ('MANC', 'phase2-local', 'manc:v1.2.1:full-local', '/manc:v1.2.1', 'v1.2.1'):
        assert canonical_dataset(unknown) == ''
    assert pair_binding_dataset({'source_sha256': PAIR_SHA256}) == 'manc:v1.2.1'
    assert pair_binding_dataset({'source_sha256': PAIR_SHA256, 'dataset': 'male-cns:v1.0'}) == 'manc:v1.2.1'
    assert pair_binding_dataset({'source_sha256': 'other', 'release': 'not_declared_in_file'}) == ''


def test_compact_menus_keep_routes_and_reject_stale_row_targets(tmp_path, monkeypatch):
    from digifly_app.core.data_library import import_local_source
    from digifly_app.core.resource_profile import ResourceProfile
    from PySide6.QtGui import QDesktopServices
    app = QApplication.instance() or QApplication([])
    profile = make_standalone_profile(workstation_root=tmp_path / 'work')
    path = profile.save(tmp_path / 'profile.json')
    roots = []
    for key in ('first', 'second'):
        source = tmp_path / key
        source.mkdir()
        (source / '1.swc').write_text('1 1 0 0 0 1 -1\n')
        item = import_local_source(ResourceProfile.load(path), source, provider='local',
            resource_id=key, source_version='snapshot', dataset='male-cns:v1.0', profile_path=path)
        roots.append(item.root)
    monkeypatch.setattr(DataLibraryPage, '_profile', lambda self: (ResourceProfile.load(path), path))
    opened = []
    monkeypatch.setattr(QDesktopServices, 'openUrl', lambda url: opened.append(str(Path(url.toLocalFile()))) or True)
    page = DataLibraryPage()
    page.resize(840, 760)
    page.show()
    page.connectome_picker.choose('male_cns')
    app.processEvents()
    try:
        assert page.neuprint_button.property('primary')
        assert page.swc_intake_button in page.add_data_menu.actions()
        assert page.load_link_button in page.add_data_menu.actions()
        assert page.import_button in page.add_data_menu.actions()
        assert page.modeldb_button in page.add_data_menu.actions()
        assert not page.pair_graph_button.isVisible()
        assert page.move_library_button in page.library_tools_menu.actions()
        assert page.relink_button in page.library_tools_menu.actions()
        assert page.manage_trash_button in page.library_tools_menu.actions()
        page.table.selectRow(0)
        holder = page.table.cellWidget(1, 8)
        menu = holder._library_menu
        QTest.mouseClick(holder._library_button, Qt.MouseButton.LeftButton)
        app.processEvents()
        target = next(action for action in menu.actions() if action.text() == page.reveal_selected_button.text())
        QTest.mouseClick(menu, Qt.MouseButton.LeftButton, pos=menu.actionGeometry(target).center())
        assert opened == [str(page._resources[1].root)]
        # Retain an old action across a release change. Its identity guard
        # refuses even a synthetic late activation before deferred destruction.
        before = path.read_bytes()
        page.release_buttons['male-cns:v0.9'].setChecked(True)
        target.trigger()
        assert len(opened) == 1 and path.read_bytes() == before
        page.connectome_picker.choose('manc')
        assert page.pair_graph_button.isVisible()
    finally:
        page.close()


def test_exact_scope_filters_rows_empty_cards_and_stale_actions(tmp_path, monkeypatch):
    from dataclasses import replace
    from digifly_app.core.resource_profile import ResourceBinding, ResourceKind, AccessMode, ResourceProfile
    from digifly_app.core.data_library import import_local_source
    from digifly_app.core.bundled_pair_graph import PAIR_SHA256
    app = QApplication.instance() or QApplication([])
    p = make_standalone_profile(workstation_root=tmp_path / 'work')
    bindings = []
    for key, dataset in (('male', 'male-cns_v0.9'), ('manc', 'manc_v1.2.1'), ('unknown', 'phase2-local')):
        folder = tmp_path / key
        folder.mkdir()
        (folder / '1.swc').write_text('1 1 0 0 0 1 -1\n')
        bindings.append(ResourceBinding(key, ResourceKind.MORPHOLOGY_SOURCE, str(folder),
            AccessMode.READ_ONLY, key, metadata={'dataset': dataset}))
    index = tmp_path / 'index'
    index.mkdir()
    (index / 'manifest.json').write_text(json.dumps({'rows': 2, 'represented_neurons': 3}))
    bindings.append(ResourceBinding('pairs', ResourceKind.DATA_SOURCE, str(tmp_path / 'unique_pairs_by_id.parquet'),
        AccessMode.READ_ONLY, metadata={'format':'digifly-weighted-pair-graph-v1', 'index':str(index/'pairs.sqlite'),
                                      'source_sha256': PAIR_SHA256, 'release':'not_declared_in_file'}))
    p = replace(p, resources=(*p.resources, *bindings))
    path = p.save(tmp_path / 'profile.json')
    for dataset in ('male-cns:v0.9', 'manc:v1.2.1'):
        source = tmp_path / ('download-' + dataset.replace(':','-'))
        source.mkdir()
        (source / '2.swc').write_text('1 1 0 0 0 1 -1\n')
        import_local_source(ResourceProfile.load(path), source, provider='local', resource_id=source.name,
                            source_version='snapshot', profile_path=path, dataset=dataset)
    monkeypatch.setattr(DataLibraryPage, '_profile', lambda self: (ResourceProfile.load(path), path))
    page = DataLibraryPage()
    page.resize(1120, 900)
    page.show()
    before = path.read_bytes()
    try:
        for family, dataset, roots in (('male_cns','male-cns:v0.9',{'male'}),
                ('male_cns','male-cns:v1.0',set()), ('manc','manc:v1.2.1',{'manc'}),
                ('manc','manc:v1.2.3',set()), ('manc','manc:v1.0',set())):
            page.connectome_picker.choose(family)
            page.release_buttons[dataset].setChecked(True)
            app.processEvents()
            assert {x.root.name for x in page._linked} == roots
            assert page.table.rowCount() == int(bool(roots))
            assert page.linked_card.isVisible() == bool(roots)
            assert page.managed_card.isVisible() == bool(roots)
            assert page.pair_graph_label.isVisible() == (dataset == 'manc:v1.2.1')
            assert not page.trash_button.isEnabled()
            if roots:
                page.table.selectRow(0)
                assert page.trash_button.isEnabled()
        assert path.read_bytes() == before
        assert len(page._all_linked) == 3
    finally:
        page.close()
