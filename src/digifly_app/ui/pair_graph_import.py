"""Actual app-owned selection and indexing of a user's aggregate connectivity file."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import uuid

from PySide6.QtCore import QProcess, QProcessEnvironment, QTimer
from PySide6.QtWidgets import QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout

from digifly_app.core.circuit import ConnectomeRef
from digifly_app.core.paths import worker_path
from digifly_app.core.pair_graph import validated_manifest
from digifly_app.core.process_environment import external_runtime_launcher, sanitized_external_environment
from digifly_app.core.resource_profile import AccessMode, ResourceBinding, ResourceKind, load_or_create_default_profile


class PairGraphImportDialog(QDialog):
    def __init__(self, source: ConnectomeRef | None, reader_python: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Use MANC connectivity Parquet")
        self.resize(720, 310)
        self.source = source or ConnectomeRef("manc:pair-graph", "MANC pair graph — no SWC bundle attached", "", "manc:user-supplied")
        self.registered_source = None
        self.process = QProcess(self)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._failed_to_start)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._timeout)
        root = QVBoxLayout(self)
        intro = QLabel("Use your existing Parquet for MANC weighted-pair lookup. All supplied rows and annotations are preserved; SWCs stay separate and unchanged. No experiment or conductance model is selected.")
        intro.setWordWrap(True)
        root.addWidget(intro)
        form = QFormLayout()
        self.file_edit = QLineEdit()
        self.file_edit.setObjectName("PairGraphFile")
        self.file_edit.setPlaceholderText("Choose the existing Parquet file")
        browse = QPushButton("Choose Parquet…")
        browse.clicked.connect(self._choose_file)
        row = QHBoxLayout()
        row.addWidget(self.file_edit)
        row.addWidget(browse)
        form.addRow("Connectivity file", row)
        self.python_edit = QLineEdit(reader_python)
        self.python_edit.setObjectName("PairGraphReaderPython")
        python_browse = QPushButton("Choose Python…")
        python_browse.clicked.connect(self._choose_python)
        reader = QHBoxLayout()
        reader.addWidget(self.python_edit)
        reader.addWidget(python_browse)
        form.addRow("Data-reader Python", reader)
        form.addRow("Morphology", QLabel(self.source.label))
        root.addLayout(form)
        note = QLabel("The data reader needs duckdb, not NEURON. This creates a separate lookup index outside the app and registers the original file read-only. It does not copy morphologies, download data, change runtimes or interpret total_weight as µS.")
        note.setWordWrap(True)
        root.addWidget(note)
        self.status = QLabel("Choose the file to use.")
        self.status.setWordWrap(True)
        self.status.setObjectName("PairGraphImportStatus")
        root.addWidget(self.status)
        buttons = QHBoxLayout()
        self.use_button = QPushButton("Use this Parquet")
        self.use_button.clicked.connect(self._start)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.use_button)
        buttons.addWidget(self.cancel_button)
        root.addLayout(buttons)

    def _choose_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose connectivity Parquet", str(Path.home() / "Downloads"), "Parquet files (*.parquet)")
        if path:
            self.file_edit.setText(path)

    def _choose_python(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose existing data-reader Python")
        if path:
            self.python_edit.setText(path)

    def _start(self):
        if self.process.state() != QProcess.ProcessState.NotRunning:
            return
        try:
            source = Path(self.file_edit.text()).expanduser().resolve()
            if not source.is_file() or source.suffix.casefold() != ".parquet":
                raise ValueError("Choose an existing Parquet file")
            profile, _ = load_or_create_default_profile()
            self.original_file = source
            for binding in profile.bindings(ResourceKind.DATA_SOURCE):
                if binding.metadata.get("format") != "digifly-weighted-pair-graph-v1" or Path(binding.path).expanduser().resolve() != source:
                    continue
                cached = replace(self.source, pair_graph_path=str(source), pair_graph_index=str(binding.metadata["index"]),
                                 pair_graph_sha256=str(binding.metadata["source_sha256"]))
                try:
                    validated_manifest(cached)
                except (OSError, ValueError, KeyError):
                    continue
                if not self.python_edit.text().strip():
                    self.python_edit.setText(str(binding.metadata.get("reader_python", "")))
                self._register(cached)
                return
            python = external_runtime_launcher(self.python_edit.text())
            if not python.is_file():
                raise ValueError("Choose an existing data-reader Python")
            library = profile.binding(ResourceKind.MANAGED_DATA_ROOT)
            if library is None:
                raise ValueError("Configure the Data Library first")
            parent = library.resolved_path / "pair_graphs"
            parent.mkdir(parents=True, exist_ok=True)
            # A completed index can outlive a failed profile save or a cancelled
            # dialog. Reuse only exact source/index hashes, never a partial DB.
            for manifest_path in parent.glob("manc-pairs-*/manifest.json"):
                try:
                    prior = json.loads(manifest_path.read_text(encoding="utf-8"))
                    if prior.get("source_path") != str(source):
                        continue
                    cached = replace(self.source, pair_graph_path=str(source),
                        pair_graph_index=str(manifest_path.parent / "pairs.sqlite"), pair_graph_sha256=str(prior["source_sha256"]))
                    validated_manifest(cached)
                except (OSError, ValueError, KeyError):
                    continue
                self._register(cached)
                return
            self.destination = parent / ("manc-pairs-" + uuid.uuid4().hex)
            self.original_file = source
            self.process.setProgram(str(python))
            self.process.setArguments(["-I", str(worker_path("pair_graph_import_worker.py")), "--source", str(source), "--destination", str(self.destination)])
            environment = QProcessEnvironment()
            for key, value in sanitized_external_environment({}).items():
                environment.insert(key, value)
            self.process.setProcessEnvironment(environment)
            self.use_button.setEnabled(False)
            self.file_edit.setEnabled(False)
            self.python_edit.setEnabled(False)
            self.status.setText("Reading all supplied pairs and building the lookup index… No simulation is starting.")
            self.process.start()
            self.timer.start(120_000)
        except (OSError, ValueError) as exc:
            self.status.setText(str(exc))

    def _failed_to_start(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.timer.stop()
            self.status.setText("Data-reader Python could not start: " + self.process.errorString())
            self._unlock()

    def _unlock(self):
        self.use_button.setEnabled(True)
        self.file_edit.setEnabled(True)
        self.python_edit.setEnabled(True)

    def _timeout(self):
        self.process.kill()
        self.status.setText("Indexing exceeded the bounded 120-second limit; no source was modified or registered.")

    def _finished(self, code, exit_status):
        self.timer.stop()
        self._unlock()
        if code != 0 or exit_status != QProcess.ExitStatus.NormalExit:
            detail = bytes(self.process.readAllStandardError()).decode("utf-8", errors="replace")[-4000:]
            self.status.setText("Parquet was not registered: " + (detail or "data reader failed or was cancelled"))
            return
        try:
            result = json.loads(bytes(self.process.readAllStandardOutput()).decode("utf-8"))
            source = replace(self.source, pair_graph_path=str(self.original_file),
                pair_graph_index=str(self.destination / "pairs.sqlite"), pair_graph_sha256=result["source_sha256"])
            self._register(source)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.status.setText("Parquet registration failed: " + str(exc))

    def _register(self, source):
        verified = validated_manifest(source)
        profile, profile_path = load_or_create_default_profile()
        remaining = tuple(binding for binding in profile.resources if not (
            binding.kind == ResourceKind.DATA_SOURCE and binding.metadata.get("format") == "digifly-weighted-pair-graph-v1"
            and binding.metadata.get("morphology_key") == self.source.key
            and binding.metadata.get("morphology_root") == self.source.root))
        binding = ResourceBinding("manc-pair-graph-" + uuid.uuid4().hex, ResourceKind.DATA_SOURCE,
            source.pair_graph_path, AccessMode.READ_ONLY, label="MANC pair connectivity · " + self.original_file.name,
            metadata={"format": verified["schema"], "morphology_key": self.source.key, "morphology_root": self.source.root,
                      "index": source.pair_graph_index, "source_sha256": source.pair_graph_sha256,
                      "reader_python": self.python_edit.text(), "release": "not_declared_in_file"})
        # Retain every previous index directory; this only changes the selected binding.
        replace(profile, resources=(*remaining, binding)).save(profile_path, replace=True)
        self.registered_source = source
        self.status.setText(f"Using {self.original_file.name}: {verified['rows']:,} directed pairs, {verified['represented_neurons']:,} represented IDs. All supplied rows preserved. SWCs remain separate.")
        self.use_button.setText("Done")
        self.use_button.clicked.disconnect()
        self.use_button.clicked.connect(self.accept)
        self.file_edit.setEnabled(False)
        self.python_edit.setEnabled(False)
        self.cancel_button.setVisible(False)

    def reject(self):
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()
            self.process.waitForFinished(3000)
        super().reject()
