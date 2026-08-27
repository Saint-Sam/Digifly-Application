"""Guided neuPrint acquisition dialog for the managed Data Library."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import threading
from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QCloseEvent, QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from digifly_app.core.credentials import (
    CredentialStoreError,
    NEUPRINT_CREDENTIAL_ENV,
    NeuPrintCredentialStore,
    credential_reference,
    normalize_neuprint_token,
    token_from_environment,
)
from digifly_app.core.data_library import ManagedResource, safe_component
from digifly_app.core.neuprint import (
    DEFAULT_NEUPRINT_SERVER,
    MAX_SELECTION_LIMIT,
    AcquisitionCancelled,
    AcquisitionProgress,
    NeuPrintAcquisitionRequest,
    NeuPrintClient,
    NeuPrintDataset,
    NeuPrintNeuron,
    NeuPrintSelection,
    acquire_neuprint_bundle,
    normalize_server,
    preview_destination,
)
from digifly_app.core.resource_profile import ResourceProfile

from .widgets import Card


TOKEN_HELP_URL = "https://connectome-neuprint.github.io/neuprint-python/docs/quickstart.html"

SELECTION_OPTIONS = (
    ("Body IDs", "body_ids", "Example: 10000, 10002"),
    ("Exact neuron type", "type_exact", "Example: DNp01"),
    ("Neuron type regex", "type_regex", "Example: DNp.*"),
    ("Exact instance", "instance_exact", "Example: DNp01_R"),
    ("Instance regex", "instance_regex", "Example: DNp01_.*"),
    ("ROI membership", "roi", "Example: legNp(T1)(R)"),
)


class _CallWorker(QObject):
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


class NeuPrintImportDialog(QDialog):
    resource_imported = Signal(object)

    def __init__(
        self,
        profile: ResourceProfile,
        profile_path: Path,
        parent: QWidget | None = None,
        *,
        client_factory: Callable[..., NeuPrintClient] = NeuPrintClient,
        credential_store: NeuPrintCredentialStore | None = None,
    ):
        super().__init__(parent)
        self.profile = profile
        self.profile_path = Path(profile_path).expanduser().resolve()
        self.client_factory = client_factory
        self.credential_store = credential_store or NeuPrintCredentialStore()
        self._thread: QThread | None = None
        self._worker: _CallWorker | None = None
        self._success_handler: Callable[[Any], None] | None = None
        self._active_token = ""
        self._credential_ref = ""
        self._preview: tuple[NeuPrintNeuron, ...] = ()
        self._cancel_event = threading.Event()
        self._operation_kind = ""
        self._worker_progress.connect(self._show_progress)

        self.setWindowTitle("Download from neuPrint")
        self.resize(900, 760)
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 20)
        root.setSpacing(13)

        title = QLabel("Download neuron SWCs from neuPrint")
        title.setObjectName("PageTitle")
        detail = QLabel(
            "Connect with your personal application token, preview a bounded neuron set, "
            "review its exact destination, then download into the managed library."
        )
        detail.setObjectName("Muted")
        detail.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(detail)

        connection = Card()
        connection_form = QFormLayout(connection)
        connection_form.setContentsMargins(16, 15, 16, 15)
        connection_form.setHorizontalSpacing(16)
        connection_form.setVerticalSpacing(9)
        self.server_edit = QLineEdit(DEFAULT_NEUPRINT_SERVER)
        connection_form.addRow("neuPrint server", self.server_edit)
        token_row = QWidget()
        token_layout = QHBoxLayout(token_row)
        token_layout.setContentsMargins(0, 0, 0, 0)
        self.token_edit = QLineEdit()
        self.token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.token_edit.setPlaceholderText("Paste the token JSON or bare token")
        token_layout.addWidget(self.token_edit, 1)
        help_button = QPushButton("Token help")
        help_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(TOKEN_HELP_URL)))
        token_layout.addWidget(help_button)
        connection_form.addRow("Application token", token_row)
        token_options = QWidget()
        token_options_layout = QHBoxLayout(token_options)
        token_options_layout.setContentsMargins(0, 0, 0, 0)
        self.environment_token = QCheckBox(
            f"Use {NEUPRINT_CREDENTIAL_ENV} from this launch"
        )
        self.environment_token.setEnabled(bool(os.environ.get(NEUPRINT_CREDENTIAL_ENV, "").strip()))
        token_options_layout.addWidget(self.environment_token)
        self.remember_token = QCheckBox("Remember in the operating-system credential store")
        self.remember_token.setEnabled(self.credential_store.available)
        if not self.remember_token.isEnabled():
            self.remember_token.setToolTip(
                "Install a supported keyring backend; Digifly will not fall back to a plaintext token file."
            )
        token_options_layout.addWidget(self.remember_token)
        self.load_saved_button = QPushButton("Use saved token")
        self.load_saved_button.setEnabled(self.credential_store.available)
        self.load_saved_button.clicked.connect(self._load_saved_token)
        token_options_layout.addWidget(self.load_saved_button)
        token_options_layout.addStretch(1)
        connection_form.addRow("Token source", token_options)
        connection_actions = QWidget()
        connection_actions_layout = QHBoxLayout(connection_actions)
        connection_actions_layout.setContentsMargins(0, 0, 0, 0)
        self.connect_button = QPushButton("Connect and list datasets")
        self.connect_button.setProperty("primary", True)
        self.connect_button.clicked.connect(self.connect_server)
        connection_actions_layout.addWidget(self.connect_button)
        self.connection_status = QLabel("Not connected")
        self.connection_status.setObjectName("Muted")
        connection_actions_layout.addWidget(self.connection_status, 1)
        connection_form.addRow("", connection_actions)
        root.addWidget(connection)

        selection = Card()
        selection_form = QFormLayout(selection)
        selection_form.setContentsMargins(16, 15, 16, 15)
        selection_form.setHorizontalSpacing(16)
        selection_form.setVerticalSpacing(9)
        self.dataset_combo = QComboBox()
        self.dataset_combo.setEnabled(False)
        selection_form.addRow("Dataset and version", self.dataset_combo)
        selection_row = QWidget()
        selection_layout = QHBoxLayout(selection_row)
        selection_layout.setContentsMargins(0, 0, 0, 0)
        self.selection_mode = QComboBox()
        for label, key, example in SELECTION_OPTIONS:
            self.selection_mode.addItem(label, key)
            self.selection_mode.setItemData(self.selection_mode.count() - 1, example, Qt.ItemDataRole.ToolTipRole)
        selection_layout.addWidget(self.selection_mode)
        self.selection_edit = QLineEdit()
        self.selection_edit.setPlaceholderText(SELECTION_OPTIONS[0][2])
        selection_layout.addWidget(self.selection_edit, 1)
        self.limit_spin = QSpinBox()
        self.limit_spin.setRange(1, MAX_SELECTION_LIMIT)
        self.limit_spin.setValue(100)
        self.limit_spin.setToolTip("Hard cap applied to the server query and download")
        selection_layout.addWidget(QLabel("Limit"))
        selection_layout.addWidget(self.limit_spin)
        self.preview_button = QPushButton("Preview neurons")
        self.preview_button.setEnabled(False)
        self.preview_button.clicked.connect(self.preview_neurons)
        selection_layout.addWidget(self.preview_button)
        selection_form.addRow("Bounded selection", selection_row)
        self.preview_table = QTableWidget(0, 4)
        self.preview_table.setHorizontalHeaderLabels(("Download", "Body ID", "Type", "Instance"))
        self.preview_table.horizontalHeader().setStretchLastSection(True)
        self.preview_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.preview_table.itemChanged.connect(self._update_destination)
        selection_form.addRow("Preview", self.preview_table)
        root.addWidget(selection, 1)

        destination = Card()
        destination_form = QFormLayout(destination)
        destination_form.setContentsMargins(16, 15, 16, 15)
        destination_form.setHorizontalSpacing(16)
        destination_form.setVerticalSpacing(8)
        managed_root = QLabel(str(profile.managed_data_root))
        managed_root.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        managed_root.setWordWrap(True)
        destination_form.addRow("Managed library root", managed_root)
        naming_row = QWidget()
        naming_layout = QHBoxLayout(naming_row)
        naming_layout.setContentsMargins(0, 0, 0, 0)
        self.name_edit = QLineEdit("neuprint-selection")
        self.snapshot_edit = QLineEdit(datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
        naming_layout.addWidget(QLabel("Folder name"))
        naming_layout.addWidget(self.name_edit, 1)
        naming_layout.addWidget(QLabel("Snapshot"))
        naming_layout.addWidget(self.snapshot_edit, 1)
        destination_form.addRow("Download identity", naming_row)
        self.destination_label = QLabel("Preview neurons to calculate the exact destination")
        self.destination_label.setObjectName("Muted")
        self.destination_label.setWordWrap(True)
        self.destination_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        destination_form.addRow("Exact destination", self.destination_label)
        root.addWidget(destination)

        progress_row = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        progress_row.addWidget(self.progress, 1)
        self.cancel_button = QPushButton("Cancel download")
        self.cancel_button.setVisible(False)
        self.cancel_button.clicked.connect(self.cancel_download)
        progress_row.addWidget(self.cancel_button)
        root.addLayout(progress_row)
        self.status = QLabel(
            "Tokens are used only in memory unless you explicitly choose the operating-system credential store."
        )
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        actions = QHBoxLayout()
        actions.addStretch(1)
        self.download_button = QPushButton("Review and download")
        self.download_button.setProperty("primary", True)
        self.download_button.setEnabled(False)
        self.download_button.clicked.connect(self.start_download)
        actions.addWidget(self.download_button)
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.accept)
        actions.addWidget(self.close_button)
        root.addLayout(actions)

        self.server_edit.textChanged.connect(self._connection_invalidated)
        self.token_edit.textChanged.connect(self._connection_invalidated)
        self.environment_token.toggled.connect(self._connection_invalidated)
        self.dataset_combo.currentIndexChanged.connect(self._preview_invalidated)
        self.selection_mode.currentIndexChanged.connect(self._selection_mode_changed)
        self.selection_edit.textChanged.connect(self._preview_invalidated)
        self.limit_spin.valueChanged.connect(self._preview_invalidated)
        self.name_edit.textChanged.connect(self._update_destination)
        self.snapshot_edit.textChanged.connect(self._update_destination)

    @property
    def operation_in_progress(self) -> bool:
        return self._thread is not None

    def _redact(self, detail: str) -> str:
        return detail.replace(self._active_token, "[redacted]") if self._active_token else detail

    def _set_busy(self, busy: bool) -> None:
        self.connect_button.setEnabled(not busy)
        self.preview_button.setEnabled(not busy and bool(self._active_token) and self.dataset_combo.count() > 0)
        self.download_button.setEnabled(not busy and bool(self._selected_neurons()))
        self.close_button.setEnabled(not busy)

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
        self._set_busy(True)
        thread = QThread(self)
        worker = _CallWorker(operation)
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
        safe_detail = self._redact(detail)
        if isinstance(detail, str) and "cancel" in detail.casefold():
            self.status.setText("Download cancelled; the partial staging folder was removed.")
        else:
            self.status.setText(f"{self._operation_kind.capitalize()} failed: {safe_detail}")
            QMessageBox.critical(self, f"neuPrint {self._operation_kind} failed", safe_detail)

    @Slot()
    def _operation_finished(self) -> None:
        was_download = self._operation_kind == "download"
        self._thread = None
        self._worker = None
        self._success_handler = None
        self._operation_kind = ""
        self.cancel_button.setVisible(False)
        if not was_download:
            self.progress.setVisible(False)
        self._set_busy(False)

    def _connection_invalidated(self, *_args: Any) -> None:
        if self._thread is not None:
            return
        self._active_token = ""
        self._credential_ref = ""
        self.dataset_combo.clear()
        self.dataset_combo.setEnabled(False)
        self.preview_button.setEnabled(False)
        self._clear_preview()
        self.connection_status.setText("Connection details changed; reconnect to continue")

    def _preview_invalidated(self, *_args: Any) -> None:
        if self._thread is None:
            self._clear_preview()

    def _selection_mode_changed(self, index: int) -> None:
        self.selection_edit.setPlaceholderText(SELECTION_OPTIONS[index][2])
        self._preview_invalidated()

    def _clear_preview(self) -> None:
        self._preview = ()
        self.preview_table.setRowCount(0)
        self.download_button.setEnabled(False)
        self.destination_label.setText("Preview neurons to calculate the exact destination")

    def _token_for_connection(self) -> str:
        if self.environment_token.isChecked():
            token = token_from_environment()
            if token is None:
                raise ValueError(f"{NEUPRINT_CREDENTIAL_ENV} is not set for this app launch")
            return token
        if self._active_token and not self.token_edit.text().strip():
            return self._active_token
        return normalize_neuprint_token(self.token_edit.text())

    def _load_saved_token(self) -> None:
        try:
            server = normalize_server(self.server_edit.text())
            token = self.credential_store.get(server)
        except (CredentialStoreError, ValueError) as exc:
            QMessageBox.warning(self, "Saved neuPrint token unavailable", str(exc))
            return
        if not token:
            QMessageBox.information(self, "No saved neuPrint token", "No token is saved for this server.")
            return
        self._active_token = token
        self._credential_ref = credential_reference(server)
        self.token_edit.blockSignals(True)
        self.token_edit.clear()
        self.token_edit.blockSignals(False)
        self.connection_status.setText("Saved token loaded in memory; connect to validate it")

    @Slot()
    def connect_server(self) -> None:
        try:
            server = normalize_server(self.server_edit.text())
            token = self._token_for_connection()
        except ValueError as exc:
            QMessageBox.warning(self, "Connection details required", str(exc))
            return
        self._active_token = token
        self.connection_status.setText("Validating token and requesting datasets…")
        self.progress.setRange(0, 0)
        self.progress.setVisible(True)
        client = self.client_factory(server, token)
        self._start_operation(
            "connection",
            client.validate_and_list_datasets,
            self._connected,
        )

    def _connected(self, datasets: tuple[NeuPrintDataset, ...]) -> None:
        self.dataset_combo.blockSignals(True)
        self.dataset_combo.clear()
        for dataset in datasets:
            description = f" — {dataset.description}" if dataset.description else ""
            self.dataset_combo.addItem(f"{dataset.name}{description}", dataset.name)
        self.dataset_combo.blockSignals(False)
        self.dataset_combo.setEnabled(True)
        self.preview_button.setEnabled(True)
        self.connection_status.setText(f"Connected; {len(datasets)} dataset(s) available")
        self.status.setText("Choose the exact dataset and preview a bounded neuron selection.")
        self.token_edit.blockSignals(True)
        self.token_edit.clear()
        self.token_edit.blockSignals(False)
        if self.remember_token.isChecked():
            try:
                self._credential_ref = self.credential_store.set(
                    normalize_server(self.server_edit.text()),
                    self._active_token,
                )
            except CredentialStoreError as exc:
                QMessageBox.warning(self, "Token was not saved", str(exc))
                self._credential_ref = ""
        self._clear_preview()

    def _selection(self) -> NeuPrintSelection:
        return NeuPrintSelection(
            str(self.selection_mode.currentData()),
            self.selection_edit.text(),
            self.limit_spin.value(),
        )

    @Slot()
    def preview_neurons(self) -> None:
        try:
            dataset = str(self.dataset_combo.currentData() or "")
            selection = self._selection()
            if not dataset or not self._active_token:
                raise ValueError("Connect and choose a dataset first")
        except ValueError as exc:
            QMessageBox.warning(self, "Selection required", str(exc))
            return
        self.status.setText("Running a bounded, read-only neuron query…")
        self.progress.setRange(0, 0)
        self.progress.setVisible(True)
        client = self.client_factory(
            normalize_server(self.server_edit.text()),
            self._active_token,
            dataset=dataset,
        )
        self._start_operation(
            "preview",
            lambda: client.preview_neurons(selection),
            self._preview_ready,
        )

    def _preview_ready(self, neurons: tuple[NeuPrintNeuron, ...]) -> None:
        self._preview = tuple(neurons)
        self.preview_table.blockSignals(True)
        self.preview_table.setRowCount(0)
        for neuron in neurons:
            row = self.preview_table.rowCount()
            self.preview_table.insertRow(row)
            include = QTableWidgetItem("")
            include.setFlags(include.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            include.setCheckState(Qt.CheckState.Checked)
            include.setData(Qt.ItemDataRole.UserRole, neuron.body_id)
            self.preview_table.setItem(row, 0, include)
            self.preview_table.setItem(row, 1, QTableWidgetItem(str(neuron.body_id)))
            self.preview_table.setItem(row, 2, QTableWidgetItem(neuron.neuron_type or "—"))
            self.preview_table.setItem(row, 3, QTableWidgetItem(neuron.instance or "—"))
        self.preview_table.blockSignals(False)
        if neurons:
            if self.selection_mode.currentData() == "type_exact":
                try:
                    self.name_edit.setText(safe_component(self.selection_edit.text()))
                except ValueError:
                    pass
            self.status.setText(
                f"Previewed {len(neurons)} neuron(s). Uncheck any rows you do not want to download."
            )
        else:
            self.status.setText("No neurons matched this selection; no download is available.")
        self._update_destination()

    def _selected_neurons(self) -> tuple[NeuPrintNeuron, ...]:
        selected_ids = {
            int(self.preview_table.item(row, 0).data(Qt.ItemDataRole.UserRole))
            for row in range(self.preview_table.rowCount())
            if self.preview_table.item(row, 0).checkState() == Qt.CheckState.Checked
        }
        return tuple(neuron for neuron in self._preview if neuron.body_id in selected_ids)

    def _request(self) -> NeuPrintAcquisitionRequest:
        return NeuPrintAcquisitionRequest(
            server=normalize_server(self.server_edit.text()),
            dataset=str(self.dataset_combo.currentData() or ""),
            resource_id=safe_component(self.name_edit.text(), field="download name"),
            source_version=safe_component(self.snapshot_edit.text(), field="snapshot name"),
            selection=self._selection(),
            neurons=self._selected_neurons(),
            token=self._active_token,
            credential_ref=self._credential_ref,
        )

    def _update_destination(self, *_args: Any) -> None:
        try:
            request = self._request()
            destination = preview_destination(self.profile, request)
        except ValueError:
            self.destination_label.setText("Complete the preview and naming fields to see the destination")
            self.download_button.setEnabled(False)
            return
        self.destination_label.setText(str(destination))
        self.download_button.setEnabled(self._thread is None and bool(request.neurons))

    @Slot()
    def start_download(self) -> None:
        try:
            request = self._request()
            destination = preview_destination(self.profile, request)
        except ValueError as exc:
            QMessageBox.warning(self, "Download details are incomplete", str(exc))
            return
        count = len(request.neurons)
        large_note = "\n\nThis is a larger job; verify the limit and free disk space." if count > 100 else ""
        answer = QMessageBox.question(
            self,
            "Confirm neuPrint download",
            f"Download {count} reviewed SWC(s) from {request.dataset}?\n\n"
            f"Destination:\n{destination}\n\n"
            "The bundle will be staged, checksummed, audited, and registered only after it is complete."
            f"{large_note}",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._cancel_event.clear()
        self.progress.setRange(0, count)
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self.cancel_button.setVisible(True)
        self.status.setText("Starting staged neuPrint download…")
        client = self.client_factory(
            request.server,
            self._active_token,
            dataset=request.dataset,
        )
        self._start_operation(
            "download",
            lambda: acquire_neuprint_bundle(
                self.profile,
                self.profile_path,
                request,
                progress=self._progress_changed,
                cancel=self._cancel_event,
                client=client,
            ),
            self._download_complete,
        )

    def _progress_changed(self, progress: AcquisitionProgress) -> None:
        # Emitting a queued Qt signal is unnecessary here because setting widgets
        # from a worker is unsafe. Invoke the slot through the worker's signal path.
        self._worker_progress.emit(progress)

    _worker_progress = Signal(object)

    @Slot(object)
    def _show_progress(self, progress: AcquisitionProgress) -> None:
        self.progress.setRange(0, max(1, progress.total))
        self.progress.setValue(progress.completed)
        byte_text = _human_bytes(progress.transferred_bytes)
        current = f" · {progress.current}" if progress.current else ""
        self.status.setText(
            f"{progress.stage}: {progress.completed}/{progress.total} · {byte_text} transferred{current}"
        )

    @Slot()
    def cancel_download(self) -> None:
        self._cancel_event.set()
        self.cancel_button.setEnabled(False)
        self.status.setText("Cancelling after the current network read; partial staging will be removed…")

    def _download_complete(self, resource: ManagedResource) -> None:
        self.progress.setValue(self.progress.maximum())
        self.status.setText(
            f"Downloaded and registered {resource.swc_count} SWC(s) at {resource.root}. "
            "Opening the post-import quality review next."
        )
        self.resource_imported.emit(resource)
        self._preview = ()
        self.download_button.setEnabled(False)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._thread is not None:
            QMessageBox.warning(
                self,
                "neuPrint operation is still running",
                "Cancel an active download and wait for staging cleanup before closing this window.",
            )
            event.ignore()
            return
        self._active_token = ""
        self.token_edit.blockSignals(True)
        self.token_edit.clear()
        self.token_edit.blockSignals(False)
        super().closeEvent(event)

    def accept(self) -> None:
        if self._thread is not None:
            return
        self._active_token = ""
        self.token_edit.blockSignals(True)
        self.token_edit.clear()
        self.token_edit.blockSignals(False)
        super().accept()

    def reject(self) -> None:
        if self._thread is not None:
            QMessageBox.warning(
                self,
                "neuPrint operation is still running",
                "Cancel an active download and wait for staging cleanup before closing this window.",
            )
            return
        self._active_token = ""
        self.token_edit.blockSignals(True)
        self.token_edit.clear()
        self.token_edit.blockSignals(False)
        super().reject()


def _human_bytes(value: int) -> str:
    amount = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if amount < 1024 or unit == "GB":
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{value} B"
