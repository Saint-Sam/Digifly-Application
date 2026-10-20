import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import time
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog, QMessageBox
from PySide6.QtTest import QTest
from digifly_app.core.resource_profile import make_default_profile, ResourceProfile
from digifly_app.ui.data_library import DataLibraryPage
from digifly_app.core.data_library import import_local_source


def test_actual_register_button_shows_external_folder_without_copy_or_trash(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    workspace = tmp_path / 'Public'
    workspace.mkdir()
    full = workspace / 'Phase 1/male-cns_v0.9/export_swc/MN/TTMn/800146'
    full.mkdir(parents=True)
    source = full / '800146.swc'
    source.write_text('1 1 0 0 0 1 -1\n')
    p = make_default_profile(workspace_root=workspace, output_root=tmp_path / 'runs', managed_data_root=tmp_path / 'managed')
    path = p.save(tmp_path / 'profile.json')
    monkeypatch.setattr(DataLibraryPage, '_profile', lambda self: (ResourceProfile.load(path), path))
    page = DataLibraryPage()
    page.connectome_picker.buttons['male_cns'].click()
    page.release_buttons['male-cns:v0.9'].setChecked(True)
    before = source.read_bytes()
    try:
        assert page.linked_table.rowCount() == 1 and page.table.rowCount() == 0
        other = tmp_path / 'external-swc'
        other.mkdir()
        (other / '2.swc').write_bytes(before)
        monkeypatch.setattr(QFileDialog, 'getExistingDirectory', lambda *a, **k: str(other))
        monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: ('another-library', True))
        monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.StandardButton.Yes)
        page.register_button.click()
        assert page.linked_table.rowCount() == 2
        assert page.table.rowCount() == 0
        assert not p.managed_data_root.exists()
        assert source.read_bytes() == before
        # Selecting a linked row must clear a previously selected managed
        # download, so its trash/unregister controls cannot be confused.
        imported = tmp_path / 'download'
        imported.mkdir()
        (imported / '3.swc').write_bytes(before)
        import_local_source(ResourceProfile.load(path), imported, provider='local',
                            resource_id='download', source_version='v1', profile_path=path, dataset='male-cns:v0.9')
        page.refresh()
        page.table.selectRow(0)
        assert page.trash_button.isEnabled()
        page.linked_table.selectRow(0)
        assert not page.trash_button.isEnabled() and not page.registration_button.isEnabled()
        assert str(page._linked[0].root) in page.linked_detail.text()
        assert page.count_linked_button.isEnabled()
        page.count_linked_button.click()
        deadline = time.monotonic() + 4
        while page.import_in_progress and time.monotonic() < deadline:
            QTest.qWait(10)
            time.sleep(.005)
        assert not page.import_in_progress
        assert page.linked_table.item(0, 3).text() == '1'
        assert 'Source files unchanged' in page.action_status.text()
        assert source.read_bytes() == before
    finally:
        page.close()
        app.processEvents()


def test_shortcut_buttons_save_restart_and_open_on_other_profile(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    workspace = tmp_path / 'Public'
    source = workspace / 'Phase 1/male-cns_v0.9/export_swc/MN/TTMn/1.swc'
    source.parent.mkdir(parents=True)
    source.write_text('1 1 0 0 0 1 -1\n')
    p = make_default_profile(workspace_root=workspace, output_root=tmp_path / 'runs', managed_data_root=tmp_path / 'data')
    path = p.save(tmp_path / 'config/resources.json')
    monkeypatch.setattr(DataLibraryPage, '_profile', lambda self: (ResourceProfile.load(path), path))
    link = tmp_path / 'config/library-links/male.digifly-library.json'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: (str(link), ''))
    page = DataLibraryPage()
    page.connectome_picker.buttons['male_cns'].click()
    page.release_buttons['male-cns:v0.9'].setChecked(True)
    page.resize(840, 650)
    page.show()
    app.processEvents()
    page.linked_table.selectRow(0)
    page.save_link_button.click()
    assert link.is_file() and 'remembers' in page.action_status.text()
    page.close()
    # A new page/profile after restart, without the original workspace, retains the source.
    from dataclasses import replace
    restored = ResourceProfile.load(path)
    from digifly_app.core.resource_profile import ResourceKind
    replace(restored, resources=tuple(b for b in restored.resources if b.kind != ResourceKind.DIGIFLY_WORKSPACE)).save(path, replace=True)
    page = DataLibraryPage()
    page.connectome_picker.buttons['male_cns'].click()
    page.release_buttons['male-cns:v0.9'].setChecked(True)
    assert page.linked_table.rowCount() == 1
    page.close()
    other = make_default_profile(workspace_root=None, output_root=tmp_path / 'other-runs', managed_data_root=tmp_path / 'other-data')
    path = other.save(tmp_path / 'other-config/resources.json')
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **k: (str(link), ''))
    monkeypatch.setattr(QMessageBox, 'exec', lambda self: QMessageBox.StandardButton.Cancel)
    page = DataLibraryPage()
    page.connectome_picker.buttons['male_cns'].click()
    page.release_buttons['male-cns:v0.9'].setChecked(True)
    before = path.read_bytes()
    page.load_link_button.click()
    assert path.read_bytes() == before
    monkeypatch.setattr(QMessageBox, 'exec', lambda self: QMessageBox.StandardButton.Yes)
    page.load_link_button.click()
    assert page.linked_table.rowCount() == 1 and not other.managed_data_root.exists()
    page.resize(840, 650)
    page.show()
    app.processEvents()
    assert page.scroll.verticalScrollBar().maximum() > 0
    assert page.linked_table.geometry().bottom() < page.open_linked_button.geometry().top()
    assert not page.managed_card.isVisible()
    assert source.read_text() == '1 1 0 0 0 1 -1\n'
    page.close()


def test_open_shortcut_can_locate_other_device_folder_without_rewriting_link(tmp_path, monkeypatch):
    from digifly_app.core.linked_libraries import LinkedLibrary, save_library_link
    app = QApplication.instance() or QApplication([])
    old = tmp_path / 'missing-on-this-device'
    local = tmp_path / 'my-external-swcs'
    local.mkdir()
    (local / '1.swc').write_text('1 1 0 0 0 1 -1\n')
    link = save_library_link(LinkedLibrary('male-cns:v0.9', 'Male CNS', 'male-cns:v0.9', old, 'external', False),
                             tmp_path / 'male.digifly-library.json')
    link_before = link.read_bytes()
    p = make_default_profile(workspace_root=None, output_root=tmp_path / 'runs', managed_data_root=tmp_path / 'data')
    path = p.save(tmp_path / 'config/resources.json')
    monkeypatch.setattr(DataLibraryPage, '_profile', lambda self: (ResourceProfile.load(path), path))
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **k: (str(link), ''))
    monkeypatch.setattr(QFileDialog, 'getExistingDirectory', lambda *a, **k: str(local))
    monkeypatch.setattr(QMessageBox, 'exec', lambda self: QMessageBox.StandardButton.Yes)
    page = DataLibraryPage()
    page.connectome_picker.buttons['male_cns'].click()
    page.release_buttons['male-cns:v0.9'].setChecked(True)
    try:
        page.load_link_button.click()
        assert page.linked_table.rowCount() == 1
        assert page._linked[0].root == local
        assert page._linked[0].dataset == 'male-cns:v0.9'
        assert link.read_bytes() == link_before and not old.exists()
        assert not p.managed_data_root.exists()
    finally:
        page.close()
        app.processEvents()
