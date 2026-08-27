from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import QUrl

from digifly_app.core.data_library import (
    ManagedResource,
    import_local_source,
    list_managed_resources,
    preview_local_source,
    register_existing_morphology,
    safe_component,
)
from digifly_app.core.resource_profile import (
    ResourceProfile,
    default_profile_path,
    load_default_profile,
)

from .widgets import Card
from .neuprint_import import NeuPrintImportDialog
from .modeldb_import import ModelDBImportDialog


def _human_bytes(value: int) -> str:
    amount = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1024 or unit == "TB":
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{value} B"


class _ImportWorker(QObject):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        profile: ResourceProfile,
        profile_path: Path,
        source: Path,
        resource_id: str,
        source_version: str,
    ):
        super().__init__()
        self.profile = profile
        self.profile_path = profile_path
        self.source = source
        self.resource_id = resource_id
        self.source_version = source_version

    @Slot()
    def run(self) -> None:
        try:
            resource = import_local_source(
                self.profile,
                self.source,
                provider="local",
                resource_id=self.resource_id,
                source_version=self.source_version,
                profile_path=self.profile_path,
                label=self.source.name,
            )
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.completed.emit(resource)


class DataLibraryPage(QWidget):
    status_message = Signal(str)
    sources_changed = Signal()
    quality_review_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._thread: QThread | None = None
        self._worker: _ImportWorker | None = None
        self._review_after_import = False
        self._neuprint_dialog: NeuPrintImportDialog | None = None
        self._modeldb_dialog: ModelDBImportDialog | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(30, 26, 30, 32)
        root.setSpacing(16)
        eyebrow = QLabel("MANAGED DATA")
        eyebrow.setObjectName("Eyebrow")
        title = QLabel("Data Library")
        title.setObjectName("PageTitle")
        detail = QLabel(
            "Import scientific data into a versioned library outside the app package, "
            "or register an existing folder without copying it."
        )
        detail.setObjectName("Muted")
        detail.setWordWrap(True)
        root.addWidget(eyebrow)
        root.addWidget(title)
        root.addWidget(detail)

        actions = Card()
        action_layout = QVBoxLayout(actions)
        action_layout.setContentsMargins(16, 15, 16, 15)
        action_layout.setSpacing(10)
        row = QHBoxLayout()
        self.import_button = QPushButton("Import local data…")
        self.import_button.setProperty("primary", True)
        self.import_button.clicked.connect(self.import_local_data)
        row.addWidget(self.import_button)
        self.register_button = QPushButton("Register existing SWC folder…")
        self.register_button.clicked.connect(self.register_existing_folder)
        row.addWidget(self.register_button)
        self.neuprint_button = QPushButton("Download from neuPrint")
        self.neuprint_button.clicked.connect(self.open_neuprint_import)
        row.addWidget(self.neuprint_button)
        self.modeldb_button = QPushButton("Import model / ModelDB")
        self.modeldb_button.clicked.connect(self.open_modeldb_import)
        row.addWidget(self.modeldb_button)
        row.addStretch(1)
        action_layout.addLayout(row)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        action_layout.addWidget(self.progress)
        self.action_status = QLabel("Ready")
        self.action_status.setObjectName("Muted")
        self.action_status.setWordWrap(True)
        action_layout.addWidget(self.action_status)
        root.addWidget(actions)

        library = Card()
        library_layout = QVBoxLayout(library)
        library_layout.setContentsMargins(16, 15, 16, 15)
        heading = QHBoxLayout()
        self.root_label = QLabel("No resource profile configured")
        self.root_label.setObjectName("Muted")
        heading.addWidget(self.root_label, 1)
        reveal = QPushButton("Reveal library")
        reveal.clicked.connect(self.reveal_library)
        heading.addWidget(reveal)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        heading.addWidget(refresh)
        library_layout.addLayout(heading)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ("Provider", "Resource", "Version", "Files", "SWCs", "Size", "Imported")
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        library_layout.addWidget(self.table)
        root.addWidget(library, 1)
        self.refresh()

    def _profile(self) -> tuple[ResourceProfile, Path]:
        path = default_profile_path()
        profile = load_default_profile()
        if profile is None:
            raise ValueError(
                "No Workstation resource profile is configured. Create one from the Workspace setup first."
            )
        return profile, path

    @Slot()
    def refresh(self) -> None:
        self.table.setRowCount(0)
        try:
            profile, _ = self._profile()
            resources = list_managed_resources(profile)
        except (OSError, ValueError) as exc:
            self.root_label.setText(str(exc))
            return
        self.root_label.setText(str(profile.managed_data_root))
        for resource in resources:
            row = self.table.rowCount()
            self.table.insertRow(row)
            values = (
                resource.provider,
                resource.resource_id,
                resource.source_version,
                f"{resource.file_count:,}",
                f"{resource.swc_count:,}",
                _human_bytes(resource.total_bytes),
                resource.imported_at,
            )
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))
            self.table.item(row, 0).setData(256, str(resource.root))
        self.status_message.emit(f"Data Library contains {len(resources)} managed resource(s)")

    @Slot()
    def import_local_data(self) -> None:
        if self._thread is not None:
            return
        try:
            profile, profile_path = self._profile()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Data Library is not configured", str(exc))
            return
        selected = QFileDialog.getExistingDirectory(self, "Choose local data folder")
        if not selected:
            return
        source = Path(selected).resolve()
        try:
            preview = preview_local_source(source)
            default_id = safe_component(source.name)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Cannot import folder", str(exc))
            return
        resource_id, accepted = QInputDialog.getText(
            self,
            "Managed resource identity",
            "Resource ID",
            text=default_id,
        )
        if not accepted:
            return
        try:
            resource_id = safe_component(resource_id)
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid resource ID", str(exc))
            return
        source_version = datetime.now(timezone.utc).strftime("import-%Y%m%d-%H%M%S")
        answer = QMessageBox.question(
            self,
            "Import local data",
            f"Copy {preview.file_count:,} file(s) ({_human_bytes(preview.total_bytes)}) "
            f"including {preview.swc_count:,} SWC(s) into the managed library?\n\n"
            "Source files will not be changed. The imported copy will be checksummed, "
            "manifested, and checked for SWC compartment-size anomalies before registration.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.import_button.setEnabled(False)
        self.register_button.setEnabled(False)
        self.progress.setVisible(True)
        self.action_status.setText(f"Staging and validating {source}…")
        thread = QThread(self)
        worker = _ImportWorker(profile, profile_path, source, resource_id, source_version)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self._import_completed)
        worker.failed.connect(self._import_failed)
        worker.completed.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._worker_thread_finished)
        self._thread = thread
        self._worker = worker
        thread.start()

    @Slot(object)
    def _import_completed(self, resource: ManagedResource) -> None:
        self.action_status.setText(
            f"Imported {resource.file_count:,} files to {resource.root}. "
            f"Audited {resource.swc_count:,} SWC(s)."
        )
        self.refresh()
        self.sources_changed.emit()
        self._review_after_import = bool(resource.swc_count)

    @Slot(str)
    def _import_failed(self, detail: str) -> None:
        self.action_status.setText(f"Import failed: {detail}")
        QMessageBox.critical(self, "Data import failed", detail)

    @Slot()
    def _worker_thread_finished(self) -> None:
        self._thread = None
        self._worker = None
        self.progress.setVisible(False)
        self.import_button.setEnabled(True)
        self.register_button.setEnabled(True)
        if self._review_after_import:
            self._review_after_import = False
            self.quality_review_requested.emit()

    @property
    def import_in_progress(self) -> bool:
        return self._thread is not None

    @Slot()
    def register_existing_folder(self) -> None:
        try:
            profile, profile_path = self._profile()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Data Library is not configured", str(exc))
            return
        selected = QFileDialog.getExistingDirectory(self, "Choose existing SWC folder")
        if not selected:
            return
        root = Path(selected).resolve()
        try:
            default_id = safe_component(root.name)
        except ValueError:
            default_id = "external-swcs"
        resource_id, accepted = QInputDialog.getText(
            self,
            "Register existing folder",
            "Resource ID",
            text=default_id,
        )
        if not accepted:
            return
        try:
            destination = register_existing_morphology(
                profile,
                root,
                resource_id=resource_id,
                profile_path=profile_path,
                label=root.name,
            )
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Could not register folder", str(exc))
            return
        self.action_status.setText(
            f"Registered {root} read-only in {destination}; no files were copied."
        )
        self.sources_changed.emit()
        self.refresh()

    @Slot()
    def reveal_library(self) -> None:
        try:
            profile, _ = self._profile()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Data Library is not configured", str(exc))
            return
        profile.managed_data_root.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(profile.managed_data_root)))

    @Slot()
    def open_neuprint_import(self) -> None:
        try:
            profile, profile_path = self._profile()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Data Library is not configured", str(exc))
            return
        dialog = NeuPrintImportDialog(profile, profile_path, self)
        dialog.resource_imported.connect(self._neuprint_completed)
        self._neuprint_dialog = dialog
        dialog.exec()
        self._neuprint_dialog = None
        if self._review_after_import:
            self._review_after_import = False
            self.quality_review_requested.emit()

    @Slot(object)
    def _neuprint_completed(self, resource: ManagedResource) -> None:
        self.action_status.setText(
            f"Downloaded {resource.swc_count:,} neuPrint SWC(s) to {resource.root}."
        )
        self.refresh()
        self.sources_changed.emit()
        self._review_after_import = bool(resource.swc_count)

    @Slot()
    def open_modeldb_import(self) -> None:
        try:
            profile, profile_path = self._profile()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Data Library is not configured", str(exc))
            return
        dialog = ModelDBImportDialog(profile, profile_path, self)
        dialog.resource_imported.connect(self._modeldb_completed)
        self._modeldb_dialog = dialog
        dialog.exec()
        self._modeldb_dialog = None
        if self._review_after_import:
            self._review_after_import = False
            self.quality_review_requested.emit()

    @Slot(object)
    def _modeldb_completed(self, resource: ManagedResource) -> None:
        self.action_status.setText(
            f"Imported {resource.file_count:,} inert model file(s) to {resource.root}. "
            f"Audited {resource.swc_count:,} SWC(s)."
        )
        self.refresh()
        self.sources_changed.emit()
        self._review_after_import = bool(resource.swc_count)
