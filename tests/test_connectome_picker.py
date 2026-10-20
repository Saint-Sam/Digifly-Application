import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import hashlib
import json

import pytest
from PySide6.QtWidgets import QApplication
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
def test_picker_images_exclusive_choice_releases_and_responsive_layout(width):
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
        picker.releases['male_cns'].setCurrentIndex(1)
        assert picker.selected_dataset == 'male-cns:v0.9'
        picker.buttons['manc'].click()
        assert picker.selected_dataset == 'manc:v1.2.3'
        assert sum(button.isChecked() for button in picker.buttons.values()) == 1
        rects = [card.geometry() for card in picker.cards.values()]
        assert all(not a.intersects(b) for i, a in enumerate(rects) for b in rects[i+1:])
        assert all(picker.rect().contains(rect) for rect in rects)
    finally:
        picker.close()


def test_page_places_chooser_first_and_passes_release_without_changing_profile(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    p = make_standalone_profile(workstation_root=tmp_path / 'work')
    path = p.save(tmp_path / 'resources.json')
    monkeypatch.setattr(DataLibraryPage, '_profile', lambda self: (p, path))
    page = DataLibraryPage()
    page.resize(1120, 760)
    page.show()
    app.processEvents()
    before = path.read_bytes()
    chosen = []
    def inspect_dialog(dialog):
        chosen.append(dialog._preferred_dataset)
        return 0
    monkeypatch.setattr(NeuPrintImportDialog, 'exec', inspect_dialog)
    try:
        page.connectome_picker.releases['manc'].setCurrentIndex(1)
        page.neuprint_button.click()
        assert chosen == ['manc:v1.2.1']
        picker_bottom = page.connectome_picker.mapTo(page.scroll.widget(), page.connectome_picker.rect().bottomLeft()).y()
        actions_top = page.import_button.mapTo(page.scroll.widget(), page.import_button.rect().topLeft()).y()
        assert picker_bottom < actions_top
        assert path.read_bytes() == before and not p.managed_data_root.exists()
        assert page.scroll.verticalScrollBar().maximum() > 0
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
