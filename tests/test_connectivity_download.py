"""Connectivity acquisition/reader engineering, not simulation validation."""
import base64
from dataclasses import replace
import hashlib
import io
import json
import os
from pathlib import Path
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
import pyarrow as pa
import pyarrow.feather as feather
import pyarrow.parquet as pq
from PySide6.QtCore import QProcess, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtTest import QTest
from digifly_app.core.connectivity_download import (EXPORTS, SERVER, export_for, checked_download,
    connectivity_root, run_download, prepare_attempt, checked_attempt, attempt_path, remove_cancelled_attempt)
from digifly_app.core.connectivity_conversion import convert_connectivity
from digifly_app.core.circuit import ConnectomeRef
from digifly_app.core.circuit_library import library_sources
from digifly_app.core.pair_graph import pair_rows
from digifly_app.core.resource_profile import make_standalone_profile, ResourceProfile
from digifly_app.ui.connectivity_download import ConnectivityDownloadDialog, graph_bindings
from digifly_app.ui.connectome_picker import CONNECTOMES


def table():
    return pa.table({'body_pre': pa.array([2**53 + 7, 2], type=pa.int64()),
                     'body_post': pa.array([2, 2**53 + 7], type=pa.int64()),
                     'weight': [1, 42], 'source_note': ['kept', None]})


@pytest.mark.parametrize('suffix', ['csv', 'feather', 'parquet'])
def test_conversion_preserves_ids_rows_weak_edges_extra_columns_and_original(tmp_path, suffix):
    source = tmp_path / ('source.' + suffix)
    if suffix == 'csv':
        source.write_text(f'bodyId_pre,bodyId_post,weight,source_note\n{2**53+7},2,1,kept\n2,{2**53+7},42,\n')
    elif suffix == 'feather':
        feather.write_feather(table(), source)
    else:
        pq.write_table(table(), source)
    before = source.read_bytes()
    result = convert_connectivity(source, tmp_path / 'ready', dataset='male-cns:v0.9', server=SERVER)
    assert result['rows'] == 2 and source.read_bytes() == before
    ref = ConnectomeRef('unit', 'unit', '', 'male-cns:v0.9', result['path'], result['index'], result['sha256'])
    rows = pair_rows(ref, str(2**53+7), '2')
    assert {r['total_weight'] for r in rows} == {1, 42}
    assert all(r['preType'] is None and r['preNt'] is None for r in rows)
    metadata = pq.read_schema(result['path']).metadata
    assert metadata[b'digifly.dataset'] == b'male-cns:v0.9'
    assert pq.read_table(result['path'])['source_note'].to_pylist() == ['kept', None]
    with pytest.raises(ValueError, match='different release'):
        convert_connectivity(Path(result['path']), tmp_path/'wrong-release', dataset='male-cns:v1.0', server=SERVER)


@pytest.mark.parametrize('data', [
    'preId,postId,total_weight\n1,2,3\n1,2,4\n',
    'preId,postId,total_weight\n1,2,-1\n',
    'preId,postId,total_weight\n1.0,2,1\n',
    'preId,postId,total_weight\n9223372036854775808,2,1\n',
])
def test_invalid_values_do_not_create_a_ready_index(tmp_path, data):
    source = tmp_path/'bad.csv'; source.write_text(data)
    with pytest.raises(Exception):
        convert_connectivity(source, tmp_path/'out', dataset='manc:v1.0')
    assert not (tmp_path/'out/index/manifest.json').exists()
    assert source.read_text() == data


def test_catalog_has_every_release_and_never_aliases_old_versions(tmp_path):
    assert set(EXPORTS) == {r for c in CONNECTOMES for r in c.releases}
    assert sum(e.available for e in EXPORTS.values()) == 5
    assert not export_for('manc:v1.2.3').available
    assert not export_for('hemibrain:v1.2.1').available
    roots = [connectivity_root(tmp_path, r) for r in EXPORTS]
    assert len(set(roots)) == len(roots)
    assert all(p.name == 'connectivity' and 'swc-libraries' in p.parts for p in roots)
    assert export_for('optic-lobe:v1.0.1').object_name.startswith('v1.0/optic-lobe-v1.0.1-')


@pytest.mark.parametrize('dataset', tuple(EXPORTS))
def test_open_folder_button_creates_and_opens_exact_release_without_download(tmp_path, monkeypatch, dataset):
    app = QApplication.instance() or QApplication([])
    profile = make_standalone_profile(workstation_root=tmp_path/'workspace')
    profile_path = profile.save(tmp_path/'resources.json')
    monkeypatch.setenv('DIGIFLY_WORKSTATION_PROFILE', str(profile_path))
    before = profile_path.read_bytes()
    root = connectivity_root(profile.managed_data_root, dataset)
    assert not root.exists()
    opened = []
    monkeypatch.setattr(QDesktopServices, 'openUrl', lambda url: opened.append(Path(url.toLocalFile())) or True)
    dialog = ConnectivityDownloadDialog(dataset)
    dialog.show()
    try:
        QTest.mouseClick(dialog.open_folder, Qt.MouseButton.LeftButton)
        assert root.is_dir(), 'An enabled folder button must work before the first download'
        assert opened == [root]
        assert 'Opening connectivity folder' in dialog.status.text()
        assert not tuple(root.iterdir())
        assert dialog.process.state() == QProcess.ProcessState.NotRunning
        assert profile_path.read_bytes() == before
        QTest.mouseClick(dialog.open_folder, Qt.MouseButton.LeftButton)
        assert opened == [root, root]
        assert not tuple(root.iterdir())
    finally:
        dialog.reject()


def test_open_folder_reports_desktop_rejection_and_preserves_saved_files(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    profile = make_standalone_profile(workstation_root=tmp_path/'workspace')
    profile_path = profile.save(tmp_path/'resources.json')
    monkeypatch.setenv('DIGIFLY_WORKSTATION_PROFILE', str(profile_path))
    root = connectivity_root(profile.managed_data_root, 'male-cns:v1.0')
    root.mkdir(parents=True)
    saved = root/'keep.txt'; saved.write_bytes(b'existing user file')
    before = profile_path.read_bytes()
    monkeypatch.setattr(QDesktopServices, 'openUrl', lambda url: False)
    dialog = ConnectivityDownloadDialog('male-cns:v1.0')
    try:
        dialog.open_folder.click()
        assert 'Could not open connectivity folder' in dialog.status.text()
        assert str(root) in dialog.status.text()
        assert saved.read_bytes() == b'existing user file'
        assert profile_path.read_bytes() == before
    finally:
        dialog.reject()


def test_open_folder_reports_creation_error_without_launching(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    profile = make_standalone_profile(workstation_root=tmp_path/'workspace')
    profile_path = profile.save(tmp_path/'resources.json')
    monkeypatch.setenv('DIGIFLY_WORKSTATION_PROFILE', str(profile_path))
    root = connectivity_root(profile.managed_data_root, 'male-cns:v1.0')
    root.parent.mkdir(parents=True)
    root.write_bytes(b'not a directory')
    opened = []
    monkeypatch.setattr(QDesktopServices, 'openUrl', lambda url: opened.append(url) or True)
    dialog = ConnectivityDownloadDialog('male-cns:v1.0')
    try:
        dialog.open_folder.click()
        assert 'Could not open connectivity folder' in dialog.status.text()
        assert not opened and root.read_bytes() == b'not a directory'
    finally:
        dialog.reject()


def test_open_folder_refuses_symlink_destination(tmp_path, monkeypatch):
    if os.name == 'nt':
        pytest.skip('Unprivileged Windows runner cannot create a directory symlink')
    app = QApplication.instance() or QApplication([])
    profile = make_standalone_profile(workstation_root=tmp_path/'workspace')
    profile_path = profile.save(tmp_path/'resources.json')
    monkeypatch.setenv('DIGIFLY_WORKSTATION_PROFILE', str(profile_path))
    root = connectivity_root(profile.managed_data_root, 'male-cns:v1.0')
    root.parent.mkdir(parents=True)
    outside = tmp_path/'external'; outside.mkdir()
    root.symlink_to(outside, target_is_directory=True)
    opened = []
    monkeypatch.setattr(QDesktopServices, 'openUrl', lambda url: opened.append(url) or True)
    dialog = ConnectivityDownloadDialog('male-cns:v1.0')
    try:
        dialog.open_folder.click()
        assert 'Could not open connectivity folder' in dialog.status.text()
        assert not opened and not tuple(outside.iterdir())
    finally:
        dialog.reject()


@pytest.mark.parametrize('name', ['connectivity.parquet', 'download-old/raw.feather', 'old.csv', 'import-old/raw.arrow'])
def test_unregistered_saved_file_requires_default_no_before_transfer(tmp_path, monkeypatch, name):
    app = QApplication.instance() or QApplication([])
    profile = make_standalone_profile(workstation_root=tmp_path/'workspace')
    profile_path = profile.save(tmp_path/'resources.json')
    monkeypatch.setenv('DIGIFLY_WORKSTATION_PROFILE', str(profile_path))
    root = connectivity_root(profile.managed_data_root, 'male-cns:v1.0')
    old = root/name; old.parent.mkdir(parents=True); old.write_bytes(b'existing unregistered graph or partial')
    before = profile_path.read_bytes()
    questions, starts = [], []
    def answer(*args):
        questions.append(args)
        return QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox, 'question', answer)
    dialog = ConnectivityDownloadDialog('male-cns:v1.0')
    monkeypatch.setattr(dialog.process, 'start', lambda: starts.append(True))
    try:
        dialog.download.click()
        assert len(questions) == 1 and str(old) in questions[0][2]
        assert questions[0][-1] == QMessageBox.StandardButton.No
        assert not starts and dialog._request_file is None
        assert 'Kept existing' in dialog.status.text()
        assert profile_path.read_bytes() == before and old.read_bytes() == b'existing unregistered graph or partial'
    finally:
        dialog.reject()


class Response(io.BytesIO):
    def __init__(self, data, export):
        super().__init__(data)
        self.headers = {'Content-Length': str(len(data)), 'x-goog-generation': export.generation}
        self.export = export
    def geturl(self):
        return self.export.url


def test_download_checksum_size_generation_cancel_and_destination_guards(tmp_path):
    data = b'1,2,42\n'
    export = replace(export_for('manc:v1.0'), size=len(data), md5=base64.b64encode(hashlib.md5(data).digest()).decode())
    result = checked_download(export, tmp_path/'good.csv', opener=lambda *a, **k: Response(data, export))
    assert result['sha256'] == hashlib.sha256(data).hexdigest()
    with pytest.raises(ValueError, match='new file'):
        checked_download(export, tmp_path/'good.csv')
    with pytest.raises(ValueError, match='checksum'):
        checked_download(replace(export, md5='bad'), tmp_path/'bad.csv', opener=lambda *a, **k: Response(data, export))
    with pytest.raises(InterruptedError):
        checked_download(export, tmp_path/'cancel.csv', cancel=lambda: True, opener=lambda *a, **k: Response(data, export))
    assert (tmp_path/'good.csv').read_bytes() == data


def test_midway_cancel_removes_partial_and_restart_starts_clean(tmp_path, monkeypatch):
    import digifly_app.core.connectivity_download as download
    data = b'x' * (2 * 1024 * 1024)
    export = replace(export_for('manc:v1.0'), size=len(data), md5=base64.b64encode(hashlib.md5(data).digest()).decode())
    monkeypatch.setattr(download, 'export_for', lambda dataset: export)
    request = {'dataset': export.dataset, 'managed_root': str(tmp_path/'managed')}
    seen = []
    for _ in range(2):
        cancelled = []
        def after_block(*args):
            root = connectivity_root(request['managed_root'], export.dataset)
            generation, = root.glob('download-*')
            assert (generation / Path(export.object_name).name).stat().st_size > 0
            seen.append(generation)
            cancelled.append(True)
        with pytest.raises(InterruptedError):
            run_download(request, progress=after_block,
                cancel=lambda: bool(cancelled), opener=lambda *a, **k: Response(data, export))
        assert not tuple(connectivity_root(request['managed_root'], export.dataset).iterdir())
    root = connectivity_root(request['managed_root'], export.dataset)
    assert len(set(seen)) == 2 and not tuple(root.iterdir())


def wait_finished(dialog):
    deadline = time.monotonic() + 20
    while dialog.process.state() != QProcess.ProcessState.NotRunning and time.monotonic() < deadline:
        QTest.qWait(20)
    QApplication.processEvents()
    assert dialog.process.state() == QProcess.ProcessState.NotRunning, dialog.status.text()


def test_actual_child_reader_registration_replacement_and_release_handoff(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    profile_path = make_standalone_profile(workstation_root=tmp_path/'workspace').save(tmp_path/'resources.json')
    monkeypatch.setenv('DIGIFLY_WORKSTATION_PROFILE', str(profile_path))
    source = tmp_path/'small.feather'; feather.write_feather(table(), source)
    dialog = ConnectivityDownloadDialog('male-cns:v0.9')
    dialog._source = str(source)
    try:
        dialog.start('import'); wait_finished(dialog)
        assert 'Ready' in dialog.status.text(), dialog.status.text()
        assert json.loads(dialog._request_file.with_suffix('.result.json').read_text())['result']['rows'] == 2
        profile = ResourceProfile.load(profile_path)
        old, = graph_bindings(profile, 'male-cns:v0.9')
        raw = Path(old.path).read_bytes()
        sources = library_sources(profile, ('male-cns:v0.9','male-cns:v1.0'))
        assert sources[0].pair_graph_path == old.path and not sources[1].pair_graph_path
        monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.StandardButton.No)
        dialog.start('import')
        assert dialog.process.state() == QProcess.ProcessState.NotRunning
        assert 'Kept existing' in dialog.status.text()
        before = profile_path.read_bytes()
        monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.StandardButton.Yes)
        dialog.start('import'); wait_finished(dialog)
        assert 'Ready' in dialog.status.text(), dialog.status.text()
        new, = graph_bindings(ResourceProfile.load(profile_path), 'male-cns:v0.9')
        assert new.path != old.path and Path(old.path).read_bytes() == raw
        history = json.loads((Path(new.path).parent/'previous-selection.json').read_text())
        assert history['bindings'][0]['path'] == old.path
        assert before != profile_path.read_bytes()
    finally:
        dialog.reject()
    assert Path(old.path).read_bytes() == raw and Path(new.path).is_file()

def test_cancel_never_registers_even_if_child_completed(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    profile_path = make_standalone_profile(workstation_root=tmp_path/'workspace').save(tmp_path/'resources.json')
    monkeypatch.setenv('DIGIFLY_WORKSTATION_PROFILE', str(profile_path))
    before = profile_path.read_bytes()
    source = tmp_path/'small.feather'; feather.write_feather(table(), source)
    dialog = ConnectivityDownloadDialog('optic-lobe:v1.1')
    dialog._source = str(source)
    dialog.start('import'); dialog.reject()
    assert profile_path.read_bytes() == before
    assert dialog._attempt is None
    assert not tuple(connectivity_root(ResourceProfile.load(profile_path).managed_data_root, dialog.dataset).iterdir())
    assert source.is_file()


def request_for(tmp_path, kind='import'):
    return {'kind': kind, 'dataset': 'male-cns:v1.0', 'server': SERVER, 'managed_root': str(tmp_path/'managed')}


def test_cleanup_removes_only_its_owned_attempt_and_all_conversion_scratch(tmp_path):
    first, other = (prepare_attempt(request_for(tmp_path, 'download')) for _ in range(2))
    path, sibling = checked_attempt(first), checked_attempt(other)
    imported = tmp_path/'original.csv'; imported.write_bytes(b'original user file')
    first['source'] = str(imported)
    (sibling/'kept.feather').write_bytes(b'earlier download')
    for name in ('raw.feather', 'ready/connectivity.parquet', 'ready/index/pairs.sqlite', 'ready/scratch/part.bin'):
        target = path/name; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(b'partial')
    remove_cancelled_attempt(first)
    remove_cancelled_attempt(first)  # Safe second finished/Cancel signal.
    assert not path.exists() and imported.read_bytes() == b'original user file'
    assert (sibling/'kept.feather').read_bytes() == b'earlier download'


def test_cleanup_keeps_owner_record_when_a_later_file_is_locked(tmp_path, monkeypatch):
    request = prepare_attempt(request_for(tmp_path))
    folder = checked_attempt(request)
    (folder/'first.bin').write_bytes(b'partial')
    (folder/'locked.bin').write_bytes(b'blocked partial')
    original = os.unlink
    def blocked(name, *args, **kwargs):
        if Path(name).name == 'locked.bin':
            raise PermissionError('file temporarily locked')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(os, 'unlink', blocked)
    with pytest.raises(PermissionError):
        remove_cancelled_attempt(request)
    assert (folder/'attempt.json').is_file(), 'A retry still needs the original ownership record'
    assert checked_attempt(request) == folder and (folder/'locked.bin').is_file()
    monkeypatch.setattr(os, 'unlink', original)
    remove_cancelled_attempt(request)
    assert not folder.exists()


def test_cleanup_restores_retry_record_if_empty_directory_removal_is_blocked(tmp_path, monkeypatch):
    request = prepare_attempt(request_for(tmp_path))
    folder = checked_attempt(request)
    original = Path.rmdir
    def blocked(path):
        if path == folder:
            raise PermissionError('directory temporarily locked')
        return original(path)
    monkeypatch.setattr(Path, 'rmdir', blocked)
    with pytest.raises(PermissionError):
        remove_cancelled_attempt(request)
    assert checked_attempt(request) == folder
    monkeypatch.setattr(Path, 'rmdir', original)
    remove_cancelled_attempt(request)
    assert not folder.exists()


@pytest.mark.parametrize('protection', ['owner', 'registered', 'selected', 'input', 'escape'])
def test_cleanup_refuses_unowned_completed_or_protected_targets(tmp_path, protection):
    request = prepare_attempt(request_for(tmp_path))
    path = checked_attempt(request)
    partial = path/'partial.parquet'; partial.write_bytes(b'keep until safe')
    protected = ()
    if protection == 'owner':
        (path/'attempt.json').write_text('{}')
    elif protection == 'registered':
        (path/'registered.json').write_text('{}')
    elif protection == 'selected':
        protected = (str(partial),)
    elif protection == 'input':
        request['source'] = str(partial)
    else:
        request['attempt_id'] = '../../external'
    with pytest.raises(ValueError):
        remove_cancelled_attempt(request, protected)
    assert partial.read_bytes() == b'keep until safe'


def test_cleanup_refuses_nested_links_before_deleting_anything(tmp_path):
    if os.name == 'nt':
        pytest.skip('Unprivileged Windows runner cannot create a directory symlink')
    request = prepare_attempt(request_for(tmp_path))
    path = checked_attempt(request)
    outside = tmp_path/'external'; outside.mkdir(); (outside/'keep').write_bytes(b'keep')
    (path/'partial').write_bytes(b'partial')
    (path/'linked').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match='link'):
        remove_cancelled_attempt(request)
    assert (outside/'keep').read_bytes() == b'keep' and (path/'partial').exists()


@pytest.mark.parametrize('timeout', [False, True])
def test_cancel_stops_writer_before_cleanup_and_keeps_source_and_prior_graph(tmp_path, monkeypatch, timeout):
    import sys
    app = QApplication.instance() or QApplication([])
    profile_path = make_standalone_profile(workstation_root=tmp_path/'workspace').save(tmp_path/'resources.json')
    monkeypatch.setenv('DIGIFLY_WORKSTATION_PROFILE', str(profile_path))
    source = tmp_path/'original.feather'; feather.write_feather(table(), source)
    original = source.read_bytes(); before = profile_path.read_bytes()
    dialog = ConnectivityDownloadDialog('male-cns:v1.0'); dialog._source = str(source); dialog.show()
    original_start = dialog.process.start
    def start_writer():
        folder = checked_attempt(dialog._attempt)
        code = ('from pathlib import Path; import time; p=Path(' + repr(str(folder)) + '); '
                '(p/"raw.feather").write_bytes(b"partial"*1024); '
                '(p/"scratch").mkdir(); (p/"scratch/part.bin").write_bytes(b"scratch"); time.sleep(45)')
        dialog.process.setProgram(sys.executable); dialog.process.setArguments(['-c', code]); original_start()
    monkeypatch.setattr(dialog.process, 'start', start_writer)
    dialog.start('import')
    folder = checked_attempt(dialog._attempt)
    deadline = time.monotonic() + 5
    while not (folder/'scratch/part.bin').exists() and time.monotonic() < deadline:
        QTest.qWait(20)
    assert (folder/'raw.feather').stat().st_size > 0
    if timeout:
        dialog._timeout()
        assert dialog.isVisible() and 'safety limit' in dialog.status.text()
    else:
        dialog.close_button.click()
        assert not dialog.isVisible()
    assert dialog.process.state() == QProcess.ProcessState.NotRunning and not folder.exists()
    assert 'Unfinished files removed' in dialog.status.text()
    assert source.read_bytes() == original and profile_path.read_bytes() == before
    dialog.reject()


def test_cancel_completion_race_removes_unselected_ready_output(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    profile_path = make_standalone_profile(workstation_root=tmp_path/'workspace').save(tmp_path/'resources.json')
    monkeypatch.setenv('DIGIFLY_WORKSTATION_PROFILE', str(profile_path))
    before = profile_path.read_bytes()
    dialog = ConnectivityDownloadDialog('male-cns:v1.0')
    request = prepare_attempt(request_for(tmp_path))
    folder = checked_attempt(request)
    source = tmp_path/'input.feather'; feather.write_feather(table(), source)
    dialog._attempt = request
    dialog.ready_result = convert_connectivity(source, folder/'ready', dataset=dialog.dataset, server=SERVER)
    dialog.reject()
    dialog._finished(0, QProcess.ExitStatus.NormalExit)
    assert not folder.exists() and profile_path.read_bytes() == before and source.is_file()


def test_conversion_explicitly_closes_the_index_handle(tmp_path, monkeypatch):
    import sqlite3
    from digifly_app.workers import pair_graph_import_worker as worker
    factory = sqlite3.connect
    closed = []
    class Connection(sqlite3.Connection):
        def close(self):
            closed.append(True)
            return super().close()
    monkeypatch.setattr(worker.sqlite3, 'connect', lambda *a, **k: factory(*a, **k, factory=Connection))
    source = tmp_path/'input.feather'; feather.write_feather(table(), source)
    convert_connectivity(source, tmp_path/'ready', dataset='male-cns:v1.0', server=SERVER)
    assert closed == [True], 'An ended SQLite transaction must also close the file handle'


def test_cancel_cannot_delete_files_until_worker_stopped(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    profile_path = make_standalone_profile(workstation_root=tmp_path/'workspace').save(tmp_path/'resources.json')
    monkeypatch.setenv('DIGIFLY_WORKSTATION_PROFILE', str(profile_path))
    dialog = ConnectivityDownloadDialog('male-cns:v1.0'); dialog.show()
    dialog._attempt = prepare_attempt(request_for(tmp_path)); folder = checked_attempt(dialog._attempt)
    (folder/'raw').write_bytes(b'active')
    monkeypatch.setattr(dialog.process, 'state', lambda: QProcess.ProcessState.Running)
    monkeypatch.setattr(dialog.process, 'kill', lambda: None)
    monkeypatch.setattr(dialog.process, 'waitForFinished', lambda ms: False)
    dialog.reject()
    assert folder.exists() and dialog.isVisible() and 'Stopping' in dialog.status.text()
    monkeypatch.setattr(dialog.process, 'state', lambda: QProcess.ProcessState.NotRunning)
    dialog._finished(1, QProcess.ExitStatus.CrashExit)
    assert not folder.exists() and not dialog.isVisible()


def test_cleanup_failure_is_visible_and_retry_preserves_original_input(tmp_path, monkeypatch):
    import digifly_app.ui.connectivity_download as ui
    app = QApplication.instance() or QApplication([])
    profile_path = make_standalone_profile(workstation_root=tmp_path/'workspace').save(tmp_path/'resources.json')
    monkeypatch.setenv('DIGIFLY_WORKSTATION_PROFILE', str(profile_path))
    dialog = ConnectivityDownloadDialog('male-cns:v1.0'); dialog.show()
    dialog._attempt = prepare_attempt(request_for(tmp_path)); folder = checked_attempt(dialog._attempt)
    real_remove = ui.remove_cancelled_attempt
    def refused(*args):
        raise PermissionError('file in use')
    monkeypatch.setattr(ui, 'remove_cancelled_attempt', refused)
    dialog.reject()
    assert folder.exists() and dialog.isVisible() and 'Could not remove unfinished' in dialog.status.text()
    assert dialog.close_button.text() == 'Retry cleanup' and not dialog.download.isEnabled()
    monkeypatch.setattr(ui, 'remove_cancelled_attempt', real_remove)
    dialog.close_button.click()
    assert not folder.exists() and not dialog.isVisible()


def test_low_disk_refuses_before_conversion_files(tmp_path, monkeypatch):
    import digifly_app.core.connectivity_conversion as conversion
    from types import SimpleNamespace
    source = tmp_path/'small.feather'; feather.write_feather(table(), source)
    monkeypatch.setattr(conversion.shutil, 'disk_usage', lambda p: SimpleNamespace(free=1))
    with pytest.raises(ValueError, match='free disk'):
        convert_connectivity(source, tmp_path/'out', dataset='manc:v1.0')
    assert not (tmp_path/'out').exists()


def test_lookup_scratch_is_inside_the_new_generation(tmp_path, monkeypatch):
    import digifly_app.core.connectivity_conversion as conversion
    original = conversion.build_index
    observed = []
    def build(source, destination, **kwargs):
        observed.append(kwargs['temp_directory'])
        return original(source, destination, **kwargs)
    monkeypatch.setattr(conversion, 'build_index', build)
    source = tmp_path/'small.feather'; feather.write_feather(table(), source)
    convert_connectivity(source, tmp_path/'new-generation', dataset='manc:v1.0')
    assert observed == [tmp_path/'new-generation/index-scratch']


def test_existing_bytes_changed_after_approval_refuses_registration(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    profile_path = make_standalone_profile(workstation_root=tmp_path/'workspace').save(tmp_path/'resources.json')
    monkeypatch.setenv('DIGIFLY_WORKSTATION_PROFILE', str(profile_path))
    source = tmp_path/'small.feather'; feather.write_feather(table(), source)
    dialog = ConnectivityDownloadDialog('male-cns:v0.9'); dialog._source = str(source)
    try:
        dialog.start('import'); wait_finished(dialog)
        old, = graph_bindings(ResourceProfile.load(profile_path), 'male-cns:v0.9')
        before = profile_path.read_bytes()
        monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.StandardButton.Yes)
        dialog.start('import')
        old_path = Path(old.path)
        with old_path.open('ab') as stream:
            stream.write(b'changed-after-review')
        wait_finished(dialog)
        assert 'changed after review' in dialog.status.text(), dialog.status.text()
        assert profile_path.read_bytes() == before
    finally:
        dialog.reject()
