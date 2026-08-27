"""Guided, inert ModelDB/archive/folder intake for the managed library."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import shutil
import tempfile
import threading
from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QCloseEvent, QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from digifly_app.core.data_library import ManagedResource, safe_component
from digifly_app.core.model_import import (
    ModelDBClient,
    ModelDBMetadata,
    ModelImportProgress,
    ModelImportRequest,
    ModelInspection,
    import_model_source,
    inspect_model_source,
    model_destination,
    modeldb_request,
)
from digifly_app.core.resource_profile import ResourceProfile

from .widgets import Card


MODELDB_HELP_URL = "https://modeldb.science/help"


def _human_bytes(value: int) -> str:
    amount = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1024 or unit == "TB":
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{value} B"


class _Worker(QObject):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, operation: Callable[[], Any]):
        super().__init__()
        self.operation = operation

    @Slot()
    def run(self) -> None:
        try:
            result = self.operation()
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.completed.emit(result)


class ModelDBImportDialog(QDialog):
    """Inspect first, show the exact destination, then import inert model code."""

    resource_imported = Signal(object)
    _worker_progress = Signal(object)

    def __init__(
        self,
        profile: ResourceProfile,
        profile_path: Path,
        parent: QWidget | None = None,
        *,
        client: ModelDBClient | None = None,
    ):
        super().__init__(parent)
        self.profile = profile
        self.profile_path = Path(profile_path).expanduser().resolve()
        self.client = client or ModelDBClient()
        self._thread: QThread | None = None
        self._worker: _Worker | None = None
        self._success_handler: Callable[[Any], None] | None = None
        self._operation_kind = ""
        self._cancel_event = threading.Event()
        self._inspection: ModelInspection | None = None
        self._metadata: ModelDBMetadata | None = None
        self._selected_source: Path | None = None
        self._download_temp: Path | None = None
        self._worker_progress.connect(self._show_progress)

        self.setWindowTitle("Import a computational model")
        self.resize(880, 730)
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 20)
        root.setSpacing(13)

        title = QLabel("Add a ModelDB or local computational model")
        title.setObjectName("PageTitle")
        detail = QLabel(
            "Choose an official ModelDB accession, a downloaded ZIP/TAR, or an unpacked "
            "folder. Digifly inspects the contents before copying them and never runs, imports, "
            "or compiles model code during intake."
        )
        detail.setObjectName("Muted")
        detail.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(detail)

        source_card = Card()
        source_form = QFormLayout(source_card)
        source_form.setContentsMargins(16, 15, 16, 15)
        source_form.setHorizontalSpacing(16)
        source_form.setVerticalSpacing(9)
        self.source_mode = QComboBox()
        self.source_mode.addItem("ModelDB accession (download online)", "modeldb")
        self.source_mode.addItem("Downloaded ZIP or TAR archive", "archive")
        self.source_mode.addItem("Unpacked local model folder", "folder")
        source_form.addRow("Source", self.source_mode)
        self.accession_widget = QWidget()
        accession_layout = QHBoxLayout(self.accession_widget)
        accession_layout.setContentsMargins(0, 0, 0, 0)
        self.accession_spin = QSpinBox()
        self.accession_spin.setRange(1, 2_000_000_000)
        self.accession_spin.setValue(245415)
        accession_layout.addWidget(self.accession_spin, 1)
        self.help_button = QPushButton("ModelDB help")
        self.help_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(MODELDB_HELP_URL))
        )
        accession_layout.addWidget(self.help_button)
        source_form.addRow("Accession", self.accession_widget)
        self.local_widget = QWidget()
        local_layout = QHBoxLayout(self.local_widget)
        local_layout.setContentsMargins(0, 0, 0, 0)
        self.source_edit = QLineEdit()
        self.source_edit.setReadOnly(True)
        self.source_edit.setPlaceholderText("Choose a source to inspect")
        local_layout.addWidget(self.source_edit, 1)
        self.choose_button = QPushButton("Choose…")
        self.choose_button.clicked.connect(self.choose_source)
        local_layout.addWidget(self.choose_button)
        source_form.addRow("Local source", self.local_widget)
        self.inspect_button = QPushButton("Look up, download, and inspect")
        self.inspect_button.setProperty("primary", True)
        self.inspect_button.clicked.connect(self.inspect_source)
        source_form.addRow("Preflight", self.inspect_button)
        root.addWidget(source_card)

        inspection_card = Card()
        inspection_form = QFormLayout(inspection_card)
        inspection_form.setContentsMargins(16, 15, 16, 15)
        inspection_form.setHorizontalSpacing(16)
        inspection_form.setVerticalSpacing(7)
        self.model_name = QLabel("Not inspected")
        self.model_name.setWordWrap(True)
        inspection_form.addRow("Model", self.model_name)
        self.contents = QLabel("—")
        self.contents.setWordWrap(True)
        inspection_form.addRow("Contents", self.contents)
        self.simulators = QLabel("—")
        self.simulators.setWordWrap(True)
        inspection_form.addRow("Detected/declared tools", self.simulators)
        self.entry_points = QLabel("—")
        self.entry_points.setWordWrap(True)
        inspection_form.addRow("Possible entry points", self.entry_points)
        self.citation = QLabel("—")
        self.citation.setWordWrap(True)
        inspection_form.addRow("Citation", self.citation)
        self.warnings = QLabel(
            "Code is always imported as inert, read-only data. Simulator setup and execution are separate, permissioned steps."
        )
        self.warnings.setObjectName("Muted")
        self.warnings.setWordWrap(True)
        inspection_form.addRow("Safety / notes", self.warnings)
        root.addWidget(inspection_card, 1)

        destination_card = Card()
        destination_form = QFormLayout(destination_card)
        destination_form.setContentsMargins(16, 15, 16, 15)
        destination_form.setHorizontalSpacing(16)
        destination_form.setVerticalSpacing(8)
        naming_widget = QWidget()
        naming_layout = QHBoxLayout(naming_widget)
        naming_layout.setContentsMargins(0, 0, 0, 0)
        self.name_edit = QLineEdit("model")
        self.version_edit = QLineEdit(
            datetime.now(timezone.utc).strftime("import-%Y%m%dT%H%M%SZ")
        )
        naming_layout.addWidget(QLabel("Folder"))
        naming_layout.addWidget(self.name_edit, 1)
        naming_layout.addWidget(QLabel("Version"))
        naming_layout.addWidget(self.version_edit, 1)
        destination_form.addRow("Managed identity", naming_widget)
        self.preserve_archive = QCheckBox("Keep a checksummed copy of the original archive")
        self.preserve_archive.setChecked(True)
        destination_form.addRow("Provenance", self.preserve_archive)
        self.destination_label = QLabel("Inspect a source to calculate the exact destination")
        self.destination_label.setObjectName("Muted")
        self.destination_label.setWordWrap(True)
        self.destination_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        destination_form.addRow("Exact destination", self.destination_label)
        root.addWidget(destination_card)

        progress_row = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        progress_row.addWidget(self.progress, 1)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setVisible(False)
        self.cancel_button.clicked.connect(self.cancel_operation)
        progress_row.addWidget(self.cancel_button)
        root.addLayout(progress_row)
        self.status = QLabel("Choose a source, then inspect it before importing.")
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        actions = QHBoxLayout()
        self.open_page_button = QPushButton("Open model page")
        self.open_page_button.setVisible(False)
        self.open_page_button.clicked.connect(self.open_model_page)
        actions.addWidget(self.open_page_button)
        actions.addStretch(1)
        self.import_button = QPushButton("Review and import")
        self.import_button.setProperty("primary", True)
        self.import_button.setEnabled(False)
        self.import_button.clicked.connect(self.start_import)
        actions.addWidget(self.import_button)
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.accept)
        actions.addWidget(self.close_button)
        root.addLayout(actions)

        self.source_mode.currentIndexChanged.connect(self._mode_changed)
        self.accession_spin.valueChanged.connect(self._invalidate)
        self.name_edit.textChanged.connect(self._update_destination)
        self.version_edit.textChanged.connect(self._update_destination)
        self._mode_changed()

    @property
    def operation_in_progress(self) -> bool:
        return self._thread is not None

    def _clear_download_temp(self) -> None:
        if self._download_temp is not None and self._download_temp.is_dir():
            shutil.rmtree(self._download_temp)
        self._download_temp = None

    def _invalidate(self, *_args: Any) -> None:
        if self._thread is not None:
            return
        self._inspection = None
        self._metadata = None
        self.import_button.setEnabled(False)
        self.open_page_button.setVisible(False)
        self.model_name.setText("Not inspected")
        self.contents.setText("—")
        self.simulators.setText("—")
        self.entry_points.setText("—")
        self.citation.setText("—")
        self.destination_label.setText("Inspect a source to calculate the exact destination")

    def _mode_changed(self, *_args: Any) -> None:
        if self._thread is not None:
            return
        self._clear_download_temp()
        mode = str(self.source_mode.currentData())
        self.accession_widget.setVisible(mode == "modeldb")
        self.local_widget.setVisible(mode != "modeldb")
        self.preserve_archive.setVisible(mode != "folder")
        self.inspect_button.setText(
            "Look up, download, and inspect" if mode == "modeldb" else "Inspect selected source"
        )
        self._selected_source = None
        self.source_edit.clear()
        self._invalidate()

    @Slot()
    def choose_source(self) -> None:
        mode = str(self.source_mode.currentData())
        if mode == "folder":
            selected = QFileDialog.getExistingDirectory(self, "Choose unpacked model folder")
        else:
            selected, _ = QFileDialog.getOpenFileName(
                self,
                "Choose downloaded model archive",
                "",
                "Model archives (*.zip *.tar *.tar.gz *.tgz *.tar.bz2 *.tbz2 *.tar.xz *.txz);;All files (*)",
            )
        if not selected:
            return
        self._selected_source = Path(selected).expanduser().resolve()
        self.source_edit.setText(str(self._selected_source))
        try:
            self.name_edit.setText(safe_component(self._selected_source.stem))
        except ValueError:
            self.name_edit.setText("local-model")
        self._invalidate()

    def _set_busy(self, busy: bool) -> None:
        self.source_mode.setEnabled(not busy)
        self.accession_spin.setEnabled(not busy)
        self.choose_button.setEnabled(not busy)
        self.inspect_button.setEnabled(not busy)
        self.import_button.setEnabled(not busy and self._inspection is not None)
        self.close_button.setEnabled(not busy)
        self.cancel_button.setVisible(busy)
        self.cancel_button.setEnabled(busy)

    def _start_operation(
        self,
        kind: str,
        operation: Callable[[], Any],
        success: Callable[[Any], None],
    ) -> None:
        if self._thread is not None:
            return
        self._operation_kind = kind
        self._success_handler = success
        self._cancel_event.clear()
        self._set_busy(True)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        thread = QThread(self)
        worker = _Worker(operation)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self._operation_completed)
        worker.failed.connect(self._operation_failed)
        worker.completed.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._operation_finished)
        self._thread = thread
        self._worker = worker
        thread.start()

    @Slot(object)
    def _operation_completed(self, result: Any) -> None:
        if self._success_handler is not None:
            self._success_handler(result)

    @Slot(str)
    def _operation_failed(self, detail: str) -> None:
        if "cancel" in detail.casefold():
            self.status.setText("Operation cancelled; partial files were removed.")
        else:
            self.status.setText(f"{self._operation_kind.capitalize()} failed: {detail}")
            QMessageBox.critical(self, f"Model {self._operation_kind} failed", detail)
        if self._operation_kind == "inspection" and self.source_mode.currentData() == "modeldb":
            self._clear_download_temp()

    @Slot()
    def _operation_finished(self) -> None:
        self._thread = None
        self._worker = None
        self._success_handler = None
        self._operation_kind = ""
        self.progress.setVisible(False)
        self.cancel_button.setVisible(False)
        self._set_busy(False)

    @Slot()
    def inspect_source(self) -> None:
        mode = str(self.source_mode.currentData())
        if mode == "modeldb":
            accession = self.accession_spin.value()
            self._clear_download_temp()
            temporary = Path(tempfile.mkdtemp(prefix=f"digifly-modeldb-{accession}-"))
            self._download_temp = temporary
            self.status.setText("Looking up official metadata and checking for a hosted archive…")

            def lookup_and_inspect() -> tuple[ModelDBMetadata, Path | None, ModelInspection | None]:
                metadata = self.client.lookup(accession)
                if not metadata.archive_available:
                    return metadata, None, None
                archive = self.client.download_archive(
                    metadata,
                    temporary / f"{accession}.zip",
                    progress=self._progress_changed,
                    cancel=self._cancel_event,
                )
                return metadata, archive, inspect_model_source(
                    archive, cancel=self._cancel_event
                )

            self._start_operation("inspection", lookup_and_inspect, self._online_inspected)
            return
        if self._selected_source is None:
            QMessageBox.warning(self, "Choose a model source", "Choose an archive or folder first.")
            return
        source = self._selected_source
        self.status.setText("Inspecting paths, file types, sizes, and archive safety…")
        self._start_operation(
            "inspection",
            lambda: inspect_model_source(source, cancel=self._cancel_event),
            self._local_inspected,
        )

    def _online_inspected(
        self,
        result: tuple[ModelDBMetadata, Path | None, ModelInspection | None],
    ) -> None:
        metadata, archive, inspection = result
        self._metadata = metadata
        self.open_page_button.setVisible(True)
        self.model_name.setText(metadata.name)
        self.citation.setText(metadata.citation or "No paper citation was present in API metadata")
        if inspection is None or archive is None:
            self._inspection = None
            self._selected_source = None
            self.contents.setText("No locally hosted ModelDB ZIP is available")
            self.simulators.setText(", ".join(metadata.simulators) or "Not declared")
            self.entry_points.setText("Not available until external code is downloaded")
            self.warnings.setText(
                "This record points to externally hosted code. Open the model page, follow its upstream code link, "
                "download it yourself, then switch Source to a local archive or folder. Digifly will not follow an arbitrary external download automatically."
            )
            self.import_button.setEnabled(False)
            self.status.setText("Metadata found, but no official hosted ZIP is available for automatic intake.")
            self._clear_download_temp()
            return
        self._selected_source = archive
        self.name_edit.setText(str(metadata.accession))
        self.version_edit.setText(metadata.version)
        self._apply_inspection(inspection, declared_simulators=metadata.simulators)

    def _local_inspected(self, inspection: ModelInspection) -> None:
        self._metadata = None
        self.model_name.setText(self._selected_source.name if self._selected_source else "Local model")
        self.citation.setText("Not provided; inspect the imported README and add provenance before publication")
        self._apply_inspection(inspection)

    def _apply_inspection(
        self,
        inspection: ModelInspection,
        *,
        declared_simulators: tuple[str, ...] = (),
    ) -> None:
        self._inspection = inspection
        tools = tuple(sorted(set(inspection.simulators).union(declared_simulators)))
        self.contents.setText(
            f"{inspection.file_count:,} file(s), {_human_bytes(inspection.total_bytes)} expanded · "
            f"{inspection.swc_count:,} SWC(s) · {inspection.mechanism_count:,} mechanism file(s)"
        )
        self.simulators.setText(", ".join(tools) or "No simulator signature detected")
        self.entry_points.setText(", ".join(inspection.entry_points[:8]) or "None detected")
        warning_text = " · ".join(inspection.warnings) if inspection.warnings else "No inspection warnings"
        self.warnings.setText(
            f"{warning_text}. Code remains inert and read-only; nothing was run or compiled."
        )
        self.status.setText("Inspection passed. Review the identity and exact destination before importing.")
        self._update_destination()

    def _request(self) -> ModelImportRequest:
        if self._inspection is None or self._selected_source is None:
            raise ValueError("Inspect a source before importing it")
        name = safe_component(self.name_edit.text(), field="model folder")
        version = safe_component(self.version_edit.text(), field="model version")
        preserve = self.preserve_archive.isChecked() and self._inspection.source_kind != "folder"
        if self._metadata is not None:
            return modeldb_request(
                self._selected_source,
                self._metadata,
                resource_id=name,
                source_version=version,
                preserve_archive=preserve,
            )
        return ModelImportRequest(
            self._selected_source,
            "local-model",
            name,
            version,
            label=self._selected_source.name,
            preserve_archive=preserve,
        )

    def _update_destination(self, *_args: Any) -> None:
        try:
            request = self._request()
        except ValueError:
            self.destination_label.setText("Complete inspection and naming to see the destination")
            self.import_button.setEnabled(False)
            return
        self.destination_label.setText(str(model_destination(self.profile, request)))
        self.import_button.setEnabled(self._thread is None)

    @Slot()
    def start_import(self) -> None:
        try:
            request = self._request()
            destination = model_destination(self.profile, request)
        except ValueError as exc:
            QMessageBox.warning(self, "Import details are incomplete", str(exc))
            return
        assert self._inspection is not None
        answer = QMessageBox.question(
            self,
            "Confirm inert model import",
            f"Import {self._inspection.file_count:,} inspected file(s)?\n\n"
            f"Destination:\n{destination}\n\n"
            "Digifly will copy/extract, checksum, SWC-audit, and register this model as read-only data. "
            "It will not run Python/MATLAB code, compile mechanisms, or launch a simulator.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.status.setText("Starting staged model import…")
        self.progress.setRange(0, max(1, self._inspection.file_count))
        self.progress.setValue(0)
        inspection = self._inspection
        self._start_operation(
            "import",
            lambda: import_model_source(
                self.profile,
                self.profile_path,
                request,
                inspection=inspection,
                progress=self._progress_changed,
                cancel=self._cancel_event,
            ),
            self._import_complete,
        )

    def _progress_changed(self, progress: ModelImportProgress) -> None:
        self._worker_progress.emit(progress)

    @Slot(object)
    def _show_progress(self, progress: ModelImportProgress) -> None:
        self.progress.setRange(0, max(1, progress.total))
        self.progress.setValue(progress.completed)
        bytes_text = f" · {_human_bytes(progress.transferred_bytes)}" if progress.transferred_bytes else ""
        current = f" · {progress.current}" if progress.current else ""
        self.status.setText(
            f"{progress.stage}: {progress.completed:,}/{progress.total:,}{bytes_text}{current}"
        )

    @Slot()
    def cancel_operation(self) -> None:
        self._cancel_event.set()
        self.cancel_button.setEnabled(False)
        self.status.setText("Cancelling; partial staged files will be removed…")

    def _import_complete(self, resource: ManagedResource) -> None:
        self.status.setText(
            f"Imported and registered {resource.file_count:,} inert model file(s) at {resource.root}."
        )
        self.resource_imported.emit(resource)
        self.import_button.setEnabled(False)
        self._clear_download_temp()

    @Slot()
    def open_model_page(self) -> None:
        if self._metadata is not None:
            QDesktopServices.openUrl(QUrl(self._metadata.model_url))

    def _may_close(self) -> bool:
        if self._thread is None:
            return True
        QMessageBox.warning(
            self,
            "Model operation is still running",
            "Cancel the active operation and wait for staged-file cleanup before closing this window.",
        )
        return False

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._may_close():
            event.ignore()
            return
        self._clear_download_temp()
        super().closeEvent(event)

    def accept(self) -> None:
        if not self._may_close():
            return
        self._clear_download_temp()
        super().accept()

    def reject(self) -> None:
        if not self._may_close():
            return
        self._clear_download_temp()
        super().reject()
