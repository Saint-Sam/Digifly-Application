"""User-requested exact-release connectivity acquisition in an isolated process."""
from dataclasses import replace
import json
from pathlib import Path
import sys
import uuid

from PySide6.QtCore import QProcess, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QProgressBar, QMessageBox, QFileDialog

from digifly_app.core.connectivity_download import export_for, SERVER, connectivity_root, free_space_required
from digifly_app.core.linked_libraries import canonical_dataset, pair_binding_dataset
from digifly_app.core.pair_graph import validated_manifest, sha
from digifly_app.core.circuit import ConnectomeRef
from digifly_app.core.resource_profile import load_or_create_default_profile, ResourceBinding, ResourceKind, AccessMode


def graph_bindings(profile, dataset):
    return tuple(b for b in profile.bindings(ResourceKind.DATA_SOURCE)
        if b.metadata.get('format') == 'digifly-weighted-pair-graph-v1'
        and pair_binding_dataset(b.metadata) == dataset
        and b.metadata.get('server', SERVER) == SERVER)


def graph_snapshot(bindings):
    output = []
    for b in bindings:
        source = Path(b.path)
        index = Path(b.metadata.get('index', ''))
        # Approval binds to the existing files, not just labels or paths.
        output.append((b.resource_id, str(source), sha(source) if source.is_file() else 'missing',
                       str(index), sha(index) if index.is_file() else 'missing'))
    return tuple(output)


class ConnectivityDownloadDialog(QDialog):
    registered = Signal()

    def __init__(self, dataset, parent=None):
        super().__init__(parent)
        self.dataset = dataset
        self.export = export_for(dataset)
        self.setWindowTitle('Connectivity · ' + dataset)
        self.resize(680, 410)
        self.process = QProcess(self)
        self.process.readyReadStandardOutput.connect(self._progress)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._error)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._timeout)
        self.ready_result = None
        self._abandoned = False
        self._buffer = ''
        self._approved = None
        self._request_file = None
        self._source = ''
        profile, _ = load_or_create_default_profile()
        root = QVBoxLayout(self)
        title = QLabel(dataset + ' connectivity')
        title.setObjectName('SectionTitle')
        root.addWidget(title)
        self.description = QLabel(self.export.scope if self.export.available else self.export.unavailable)
        self.description.setWordWrap(True)
        root.addWidget(self.description)
        if self.export.available:
            label = QLabel(f'Official file: {self.export.size/1024**2:,.1f} MiB. Allow approximately {free_space_required(self.export)/1024**3:.1f} GiB free for download, conversion and lookup. No SWCs or simulator are downloaded.')
            label.setWordWrap(True)
            root.addWidget(label)
        self.destination = QLabel(str(connectivity_root(profile.managed_data_root, dataset)))
        self.destination.setWordWrap(True)
        root.addWidget(QLabel('Saved in this release’s connectivity folder:'))
        root.addWidget(self.destination)
        self.status = QLabel('Ready. Existing connectivity is kept until a new file is checked and you approve replacement.')
        self.status.setObjectName('ConnectivityDownloadStatus')
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        root.addWidget(self.progress)
        actions = QHBoxLayout()
        self.download = QPushButton('Download connectivity')
        self.download.setEnabled(self.export.available)
        self.download.clicked.connect(lambda: self.start('download'))
        self.import_file = QPushButton('Import matching file…')
        self.import_file.clicked.connect(self.choose_import)
        self.info = QPushButton('Official source')
        self.info.setEnabled(bool(self.export.info))
        self.info.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(self.export.info)))
        actions.addWidget(self.download)
        actions.addWidget(self.import_file)
        actions.addWidget(self.info)
        root.addLayout(actions)
        self.open_folder = QPushButton('Open connectivity folder')
        self.open_folder.clicked.connect(self.reveal)
        root.addWidget(self.open_folder)
        self.close_button = QPushButton('Close')
        self.close_button.clicked.connect(self.reject)
        root.addWidget(self.close_button)

    def choose_import(self):
        source, _ = QFileDialog.getOpenFileName(self, 'Import connectivity for ' + self.dataset, '',
            'Connectivity (*.parquet *.feather *.arrow *.csv)')
        if source:
            self._source = source
            self.start('import')

    def start(self, kind):
        if self.process.state() != QProcess.ProcessState.NotRunning:
            return
        try:
            if kind == 'download' and not self.export.available:
                raise ValueError(self.export.unavailable)
            profile, _ = load_or_create_default_profile()
            bindings = graph_bindings(profile, self.dataset)
            approved = graph_snapshot(bindings)
            if bindings:
                answer = QMessageBox.question(self, 'Replace selected connectivity?',
                    'Connectivity is already saved for ' + self.dataset + '.\n\n' +
                    '\n'.join(b.path for b in bindings) +
                    '\n\nDownload/import a new copy and use it instead? Existing files and indexes will remain recoverable. Nothing is replaced if the new copy fails or is cancelled.',
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No)
                if answer != QMessageBox.StandardButton.Yes:
                    self.status.setText('Kept existing connectivity. No download started.')
                    return
            self._approved = approved
            self._abandoned = False
            self.ready_result = None
            self._buffer = ''
            root = connectivity_root(profile.managed_data_root, self.dataset)
            root.mkdir(parents=True, exist_ok=True)
            self._request_file = root / ('request-' + uuid.uuid4().hex + '.json')
            request = {'kind': kind, 'dataset': self.dataset, 'server': SERVER,
                'managed_root': str(profile.managed_data_root), 'source': self._source}
            self._request_file.write_text(json.dumps(request) + '\n')
            self.process.setProgram(sys.executable)
            args = [] if '__compiled__' in globals() or getattr(sys, 'frozen', False) else ['-m', 'digifly_app']
            # Source entrypoint is digifly_app.app (package has no __main__).
            if args:
                args = ['-c', 'from digifly_app.app import main; raise SystemExit(main())']
            self.process.setArguments([*args, '--connectivity-task', str(self._request_file)])
            self.download.setEnabled(False)
            self.import_file.setEnabled(False)
            self.close_button.setText('Cancel')
            self.status.setText('Starting isolated data reader… Existing files remain unchanged.')
            self.process.start()
            self.timer.start(60 * 60 * 1000)
        except (OSError, ValueError) as exc:
            self.status.setText(str(exc))

    def _progress(self):
        self._buffer += bytes(self.process.readAllStandardOutput()).decode('utf-8', errors='replace')
        while '\n' in self._buffer:
            line, self._buffer = self._buffer.split('\n', 1)
            try:
                message = json.loads(line)
                if 'result' in message:
                    self.ready_result = message['result']
                elif 'phase' in message:
                    total, received = message['total'], message['received']
                    self.progress.setRange(0, 100 if total else 0)
                    if total:
                        self.progress.setValue(int(received * 100 / total))
                    self.status.setText(message['phase'] + (f' · {received/1024**2:.1f} / {total/1024**2:.1f} MiB' if total else '…'))
            except (ValueError, KeyError):
                self.status.setText('Unexpected data-reader output; registration will be refused.')

    def _finished(self, code, status):
        self.timer.stop()
        self._progress()
        self.close_button.setText('Close')
        self.download.setEnabled(self.export.available)
        self.import_file.setEnabled(True)
        if self._abandoned:
            return
        response_error = ''
        if self._request_file is not None:
            try:
                response = json.loads(self._request_file.with_suffix('.result.json').read_text(encoding='utf-8'))
                self.ready_result = response.get('result')
                response_error = response.get('error', '')
            except (OSError, ValueError):
                self.ready_result = None
        if code or status != QProcess.ExitStatus.NormalExit or not self.ready_result:
            detail = bytes(self.process.readAllStandardError()).decode('utf-8', errors='replace')[-2000:]
            self.status.setText('Connectivity not changed: ' + (response_error or detail or 'data reader failed'))
            return
        try:
            profile, path = load_or_create_default_profile()
            reviewed_profile = path.read_bytes()
            existing = graph_bindings(profile, self.dataset)
            if graph_snapshot(existing) != self._approved:
                raise ValueError('Existing connectivity changed after review; select again. New copy retained separately.')
            result = self.ready_result
            if result['dataset'] != self.dataset or result['server'] != SERVER:
                raise ValueError('Connectivity release/server differs; registration refused')
            source = ConnectomeRef(self.dataset, self.dataset, '', self.dataset,
                result['path'], result['index'], result['sha256'])
            manifest = validated_manifest(source)
            if manifest['dataset'] != self.dataset or manifest['server'] != SERVER:
                raise ValueError('Converted file has another release/server')
            # Replacing changes selection only. Previous raw, Parquet and index
            # generations stay in their folders; never delete a source file.
            remaining = tuple(b for b in profile.resources if b not in existing)
            binding = ResourceBinding('connectivity-' + uuid.uuid4().hex, ResourceKind.DATA_SOURCE,
                result['path'], AccessMode.READ_ONLY, label=self.dataset + ' connectivity',
                metadata={'format': manifest['schema'], 'dataset': self.dataset, 'server': SERVER,
                    'index': result['index'], 'source_sha256': result['sha256'],
                    'release': self.dataset, 'scope': result['scope'],
                    'download_details': result.get('details', ''), 'reader_python': '',
                    'built_in_reader': True, 'morphology_key': 'library:' + self.dataset, 'morphology_root': ''})
            if path.read_bytes() != reviewed_profile:
                raise ValueError('Library settings changed during validation; new copy retained separately')
            if existing:
                history = Path(result['path']).parent / 'previous-selection.json'
                with history.open('x') as stream:
                    json.dump({'dataset': self.dataset, 'bindings': [b.to_dict() for b in existing]}, stream, indent=2)
            replace(profile, resources=(*remaining, binding)).save(path, replace=True)
            self.progress.setRange(0, 100)
            self.progress.setValue(100)
            self.status.setText(f'Ready · {manifest["rows"]:,} directed pairs / {manifest["represented_neurons"]:,} represented IDs. Available in Circuit Builder. SWCs unchanged.\n' + result['scope'])
            self.registered.emit()
        except (OSError, ValueError, KeyError) as exc:
            self.status.setText('Connectivity not changed: ' + str(exc))

    def _error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.timer.stop()
            self.status.setText('Built-in data reader could not start: ' + self.process.errorString())
            self.download.setEnabled(self.export.available)
            self.import_file.setEnabled(True)
            self.close_button.setText('Close')

    def _timeout(self):
        self._abandoned = True
        self.process.kill()
        self.status.setText('Stopped at the one-hour safety limit; previous connectivity unchanged. Partial download retained separately.')

    def reveal(self):
        profile, _ = load_or_create_default_profile()
        root = connectivity_root(profile.managed_data_root, self.dataset)
        if root.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(root)))

    def reject(self):
        self._abandoned = True
        self.timer.stop()
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()
            self.process.waitForFinished(3000)
        super().reject()
