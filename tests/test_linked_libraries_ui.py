import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import time
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog
from PySide6.QtTest import QTest
from digifly_app.core.resource_profile import make_default_profile, ResourceProfile
from digifly_app.ui.data_library import DataLibraryPage


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
    before = source.read_bytes()
    try:
        assert page.linked_table.rowCount() == 1 and page.table.rowCount() == 0
        other = tmp_path / 'external-swc'
        other.mkdir()
        (other / '2.swc').write_bytes(before)
        monkeypatch.setattr(QFileDialog, 'getExistingDirectory', lambda *a, **k: str(other))
        monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: ('another-library', True))
        page.register_button.click()
        assert page.linked_table.rowCount() == 2
        assert page.table.rowCount() == 0
        assert not p.managed_data_root.exists()
        assert source.read_bytes() == before
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
