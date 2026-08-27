from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import threading

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
from digifly_app.core.resource_management import (
    LibraryMoveProgress,
    LibraryMoveResult,
    move_managed_library,
    preview_library_move,
    preview_library_relink,
    register_managed_resource,
    relink_managed_library,
    trash_managed_resource,
    unregister_managed_resource,
)

from .widgets import Card
from .neuprint_import import NeuPrintImportDialog
from .modeldb_import import ModelDBImportDialog
from .resource_management import ManifestDialog, TrashDialog


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


class _LibraryMoveWorker(QObject):
    completed = Signal(object)
    failed = Signal(str)
    progress = Signal(object)

    def __init__(
        self,
        profile: ResourceProfile,
        profile_path: Path,
        target: Path,
    ):
        super().__init__()
        self.profile = profile
        self.profile_path = profile_path
        self.target = target
        self.cancel_event = threading.Event()

    @Slot()
    def run(self) -> None:
        try:
            result = move_managed_library(
                self.profile,
                self.target,
                profile_path=self.profile_path,
                progress=self.progress.emit,
                cancel=self.cancel_event,
            )
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.completed.emit(result)


class DataLibraryPage(QWidget):
    status_message = Signal(str)
    sources_changed = Signal()
    quality_review_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._thread: QThread | None = None
        self._worker: QObject | None = None
        self._worker_kind = ""
        self._review_after_import = False
        self._neuprint_dialog: NeuPrintImportDialog | None = None
        self._modeldb_dialog: ModelDBImportDialog | None = None
        self._resources: tuple[ManagedResource, ...] = ()

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
        progress_row = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        progress_row.addWidget(self.progress, 1)
        self.cancel_move_button = QPushButton("Cancel library move")
        self.cancel_move_button.setVisible(False)
        self.cancel_move_button.clicked.connect(self.cancel_library_move)
        progress_row.addWidget(self.cancel_move_button)
        action_layout.addLayout(progress_row)
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
        self.move_library_button = QPushButton("Move library…")
        self.move_library_button.clicked.connect(self.move_library)
        heading.addWidget(self.move_library_button)
        reveal = QPushButton("Reveal library")
        reveal.clicked.connect(self.reveal_library)
        heading.addWidget(reveal)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        heading.addWidget(refresh)
        library_layout.addLayout(heading)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            (
                "Provider",
                "Resource",
                "Version",
                "State",
                "Files",
                "SWCs",
                "Size",
                "Imported",
            )
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.itemSelectionChanged.connect(self._resource_selection_changed)
        library_layout.addWidget(self.table)
        resource_actions = QHBoxLayout()
        self.inspect_button = QPushButton("Inspect manifest")
        self.inspect_button.setEnabled(False)
        self.inspect_button.clicked.connect(self.inspect_selected)
        resource_actions.addWidget(self.inspect_button)
        self.reveal_selected_button = QPushButton("Reveal selected")
        self.reveal_selected_button.setEnabled(False)
        self.reveal_selected_button.clicked.connect(self.reveal_selected)
        resource_actions.addWidget(self.reveal_selected_button)
        self.registration_button = QPushButton("Register")
        self.registration_button.setEnabled(False)
        self.registration_button.clicked.connect(self.toggle_registration)
        resource_actions.addWidget(self.registration_button)
        self.trash_button = QPushButton("Move to Library Trash…")
        self.trash_button.setEnabled(False)
        self.trash_button.clicked.connect(self.trash_selected)
        resource_actions.addWidget(self.trash_button)
        resource_actions.addStretch(1)
        self.manage_trash_button = QPushButton("Manage Trash…")
        self.manage_trash_button.clicked.connect(self.manage_trash)
        resource_actions.addWidget(self.manage_trash_button)
        self.relink_button = QPushButton("Relink moved library…")
        self.relink_button.clicked.connect(self.relink_library)
        resource_actions.addWidget(self.relink_button)
        library_layout.addLayout(resource_actions)
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
        self._resources = ()
        try:
            profile, _ = self._profile()
            resources = list_managed_resources(profile)
        except (OSError, ValueError) as exc:
            self.root_label.setText(str(exc))
            self._resource_selection_changed()
            return
        self._resources = resources
        root_state = "" if profile.managed_data_root.is_dir() else " — missing; relink if moved"
        self.root_label.setText(f"{profile.managed_data_root}{root_state}")
        for resource in resources:
            row = self.table.rowCount()
            self.table.insertRow(row)
            values = (
                resource.provider,
                resource.resource_id,
                resource.source_version,
                "Registered" if resource.is_registered else "Stored only",
                f"{resource.file_count:,}",
                f"{resource.swc_count:,}",
                _human_bytes(resource.total_bytes),
                resource.imported_at,
            )
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))
        self._resource_selection_changed()
        self.status_message.emit(f"Data Library contains {len(resources)} managed resource(s)")

    def _selected_resource(self) -> ManagedResource | None:
        rows = self.table.selectionModel().selectedRows()
        if len(rows) != 1:
            return None
        row = rows[0].row()
        return self._resources[row] if 0 <= row < len(self._resources) else None

    @Slot()
    def _resource_selection_changed(self) -> None:
        resource = self._selected_resource()
        selected = resource is not None
        self.inspect_button.setEnabled(selected)
        self.reveal_selected_button.setEnabled(selected)
        self.registration_button.setEnabled(selected)
        self.trash_button.setEnabled(selected)
        self.registration_button.setText(
            "Unregister" if resource is not None and resource.is_registered else "Register"
        )

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
        self._worker_kind = "local_import"
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
        worker_kind = self._worker_kind
        self._thread = None
        self._worker = None
        self._worker_kind = ""
        self.progress.setVisible(False)
        self.progress.setRange(0, 0)
        self.cancel_move_button.setVisible(False)
        self.cancel_move_button.setEnabled(True)
        self.import_button.setEnabled(True)
        self.register_button.setEnabled(True)
        self.neuprint_button.setEnabled(True)
        self.modeldb_button.setEnabled(True)
        self.move_library_button.setEnabled(True)
        self.relink_button.setEnabled(True)
        if worker_kind == "local_import" and self._review_after_import:
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
        if not profile.managed_data_root.is_dir():
            QMessageBox.warning(
                self,
                "Data Library folder is missing",
                "The configured Data Library folder no longer exists. If you moved it, use "
                "Relink moved library and select the relocated root. Workstation will not recreate "
                "the old folder automatically.",
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(profile.managed_data_root)))

    @Slot()
    def inspect_selected(self) -> None:
        resource = self._selected_resource()
        if resource is None:
            return
        ManifestDialog(resource, self).exec()

    @Slot()
    def reveal_selected(self) -> None:
        resource = self._selected_resource()
        if resource is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(resource.root)))

    @Slot()
    def toggle_registration(self) -> None:
        resource = self._selected_resource()
        if resource is None:
            return
        try:
            profile, profile_path = self._profile()
            if resource.is_registered:
                answer = QMessageBox.question(
                    self,
                    "Unregister managed resource",
                    f"Unregister {resource.provider} / {resource.resource_id}?\n\n"
                    "The managed bundle and every file in it will remain unchanged. It can be "
                    "registered again from this table later.",
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
                changed = unregister_managed_resource(
                    profile,
                    resource,
                    profile_path=profile_path,
                )
                detail = f"Unregistered {len(changed)} profile binding(s); managed files were kept."
            else:
                changed = register_managed_resource(
                    profile,
                    resource,
                    profile_path=profile_path,
                )
                detail = f"Registered {len(changed)} read-only profile binding(s)."
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Could not update registration", str(exc))
            return
        self.action_status.setText(detail)
        self.refresh()
        self.sources_changed.emit()

    @Slot()
    def trash_selected(self) -> None:
        resource = self._selected_resource()
        if resource is None:
            return
        answer = QMessageBox.question(
            self,
            "Move managed resource to Library Trash",
            f"Move {resource.provider} / {resource.resource_id} / {resource.source_version} "
            "to the recoverable Data Library Trash?\n\n"
            f"Bundle:\n{resource.root}\n\n"
            "The resource will be unregistered and disappear from active providers. It is not "
            "permanently deleted and can be restored from Manage Trash.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            profile, profile_path = self._profile()
            entry = trash_managed_resource(
                profile,
                resource,
                profile_path=profile_path,
            )
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Could not move resource to Trash", str(exc))
            return
        self.action_status.setText(
            f"Moved {resource.resource_id} to recoverable Library Trash at {entry.trash_root}."
        )
        self.refresh()
        self.sources_changed.emit()

    @Slot()
    def manage_trash(self) -> None:
        try:
            profile, profile_path = self._profile()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Data Library is not configured", str(exc))
            return
        dialog = TrashDialog(profile, profile_path, self)
        dialog.resource_restored.connect(self._resource_restored)
        dialog.resource_purged.connect(self._trash_purged)
        dialog.exec()

    @Slot(str)
    def _trash_purged(self, detail: str) -> None:
        self.action_status.setText(detail)

    @Slot(object)
    def _resource_restored(self, resource: ManagedResource) -> None:
        self.action_status.setText(f"Restored managed resource to {resource.root}.")
        self.refresh()
        self.sources_changed.emit()

    @Slot()
    def move_library(self) -> None:
        if self._thread is not None:
            return
        try:
            profile, profile_path = self._profile()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Data Library is not configured", str(exc))
            return
        selected = QFileDialog.getExistingDirectory(
            self,
            "Choose the destination parent folder",
            str(profile.managed_data_root.parent),
        )
        if not selected:
            return
        parent = Path(selected).expanduser().resolve()
        default_name = profile.managed_data_root.name
        if (parent / default_name).exists():
            default_name = f"{default_name}-moved"
        folder_name, accepted = QInputDialog.getText(
            self,
            "New Data Library folder",
            "Folder name",
            text=default_name,
        )
        if not accepted:
            return
        folder_name = folder_name.strip()
        if (
            not folder_name
            or folder_name in {".", ".."}
            or Path(folder_name).name != folder_name
            or len(folder_name) > 128
        ):
            QMessageBox.warning(
                self,
                "Invalid destination folder",
                "Enter one ordinary folder name without path separators.",
            )
            return
        target = parent / folder_name
        try:
            preview = preview_library_move(profile, target)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Cannot move Data Library", str(exc))
            return
        method = (
            "The folder is on the same filesystem, so Workstation can use an atomic rename."
            if preview.same_filesystem
            else "The destination is on another filesystem. Workstation will copy and checksum "
            "every file, update the profile, and only then remove the source library."
        )
        answer = QMessageBox.question(
            self,
            "Move managed Data Library",
            f"Move the complete Data Library?\n\n"
            f"Current root:\n{preview.source_root}\n\n"
            f"New root:\n{preview.target_root}\n\n{method}",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.import_button.setEnabled(False)
        self.register_button.setEnabled(False)
        self.neuprint_button.setEnabled(False)
        self.modeldb_button.setEnabled(False)
        self.move_library_button.setEnabled(False)
        self.relink_button.setEnabled(False)
        self.progress.setRange(0, 0)
        self.progress.setVisible(True)
        self.cancel_move_button.setVisible(True)
        self.cancel_move_button.setEnabled(True)
        self.action_status.setText("Inventorying the managed Data Library…")
        thread = QThread(self)
        worker = _LibraryMoveWorker(profile, profile_path, preview.target_root)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._library_move_progress)
        worker.completed.connect(self._library_move_completed)
        worker.failed.connect(self._library_move_failed)
        worker.completed.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._worker_thread_finished)
        self._thread = thread
        self._worker = worker
        self._worker_kind = "library_move"
        thread.start()

    @Slot(object)
    def _library_move_progress(self, update: LibraryMoveProgress) -> None:
        if update.total_bytes > 0:
            self.progress.setRange(0, 1000)
            self.progress.setValue(
                min(1000, int(1000 * update.completed_bytes / update.total_bytes))
            )
        elif update.total_files > 0:
            self.progress.setRange(0, update.total_files)
            self.progress.setValue(update.completed_files)
        else:
            self.progress.setRange(0, 0)
        current = f" · {update.current}" if update.current else ""
        self.action_status.setText(
            f"{update.stage}: {update.completed_files:,}/{update.total_files:,} files · "
            f"{_human_bytes(update.completed_bytes)}/{_human_bytes(update.total_bytes)}{current}"
        )

    @Slot()
    def cancel_library_move(self) -> None:
        if isinstance(self._worker, _LibraryMoveWorker):
            self._worker.cancel_event.set()
            self.cancel_move_button.setEnabled(False)
            self.action_status.setText(
                "Cancelling after the current verified file; the original library remains active…"
            )

    @Slot(object)
    def _library_move_completed(self, result: LibraryMoveResult) -> None:
        cleanup = (
            " The verified destination is active, but the old duplicate could not be fully "
            f"removed and remains at {result.source_root}."
            if not result.source_removed
            else ""
        )
        self.action_status.setText(
            f"Moved {result.file_count:,} files ({_human_bytes(result.total_bytes)}) to "
            f"{result.target_root} using {result.mode.replace('_', ' ')}.{cleanup}"
        )
        self.refresh()
        self.sources_changed.emit()

    @Slot(str)
    def _library_move_failed(self, detail: str) -> None:
        if "cancel" in detail.casefold():
            self.action_status.setText("Data Library move cancelled; the original library remains active.")
            return
        self.action_status.setText(f"Data Library move failed: {detail}")
        QMessageBox.critical(self, "Data Library move failed", detail)

    @Slot()
    def relink_library(self) -> None:
        try:
            profile, profile_path = self._profile()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Data Library is not configured", str(exc))
            return
        start = profile.managed_data_root.parent
        selected = QFileDialog.getExistingDirectory(
            self,
            "Choose the relocated Data Library root",
            str(start if start.is_dir() else Path.home()),
        )
        if not selected:
            return
        try:
            preview = preview_library_relink(profile, selected)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Relocated library is incomplete", str(exc))
            return
        answer = QMessageBox.question(
            self,
            "Relink moved Data Library",
            f"Update the resource profile to use this relocated library?\n\n"
            f"Current root:\n{preview.current_root}\n\n"
            f"New root:\n{preview.new_root}\n\n"
            f"Validated {preview.managed_binding_count} managed binding(s) and "
            f"{preview.resource_count} discoverable bundle(s). No data will be copied or moved.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            relink_managed_library(profile, selected, profile_path=profile_path)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Could not relink Data Library", str(exc))
            return
        self.action_status.setText(f"Relinked the Data Library to {preview.new_root}.")
        self.refresh()
        self.sources_changed.emit()

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
