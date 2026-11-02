"""Reveal must locate an artifact without launching the artifact itself."""
from pathlib import Path
from digifly_app.ui.main_window import _reveal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox


def test_result_reveal_opens_containing_folder_not_the_file(tmp_path,monkeypatch):
    file=tmp_path/'reader-test.txt';file.write_text('Software test only')
    opened=[]
    monkeypatch.setattr(QDesktopServices,'openUrl',lambda url:opened.append(url.toLocalFile()) or True)
    _reveal(str(file))
    assert [Path(path) for path in opened]==[tmp_path]


def test_result_folder_failure_is_reported_without_opening_the_file(tmp_path,monkeypatch):
    file=tmp_path/'result.py';file.write_text('# Must not be launched by Open folder\n')
    opened=[];warnings=[]
    monkeypatch.setattr(QDesktopServices,'openUrl',lambda url:opened.append(url.toLocalFile()) or False)
    monkeypatch.setattr(QMessageBox,'warning',lambda *a:warnings.append(a[2]))
    assert _reveal(str(file)) is False
    assert [Path(path) for path in opened]==[tmp_path] and len(warnings)==1


def test_unavailable_containing_folder_is_not_launched(tmp_path,monkeypatch):
    warnings=[]
    monkeypatch.setattr(QDesktopServices,'openUrl',lambda *a:(_ for _ in ()).throw(AssertionError('Missing folder launched')))
    monkeypatch.setattr(QMessageBox,'warning',lambda *a:warnings.append(a[2]))
    assert _reveal(str(tmp_path/'missing'/'result.txt')) is False
    assert warnings and 'unavailable' in warnings[0]


def test_result_reveal_keeps_existing_directory_target(tmp_path,monkeypatch):
    opened=[]
    monkeypatch.setattr(QDesktopServices,'openUrl',lambda url:opened.append(url.toLocalFile()) or True)
    _reveal(str(tmp_path))
    assert [Path(path) for path in opened]==[tmp_path]
