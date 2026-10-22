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
from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtTest import QTest
from digifly_app.core.connectivity_download import EXPORTS, SERVER, export_for, checked_download, connectivity_root
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
