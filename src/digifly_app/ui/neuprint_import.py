"""Guided neuPrint acquisition dialog for the managed Data Library."""

from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import replace
import os
from pathlib import Path
import threading
from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QCloseEvent, QDesktopServices, QKeySequence, QShortcut
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
    QScrollArea,
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
    token_from_workspace,
    workspace_token_path,
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
    discard_neuprint_checkpoint,
    exact_selection_terms,
    name_selection_terms,
    neuron_matches_name,
    neuprint_checkpoint_path,
    matching_library_checkpoint,
    normalize_server,
    preview_destination,
)
from digifly_app.core.resource_profile import ResourceProfile
from digifly_app.core.swc_library import library_root, inspect_existing_swcs, _labels

from .widgets import Card


TOKEN_HELP_URL = "https://neuprint.janelia.org/account"

SELECTION_OPTIONS = (
    ("Neuron names / IDs (list)", "name_list", "DNp01, TTMn, PSI — comma or semicolon separated"),
    ("Body IDs", "body_ids", "Example: 10000, 10002"),
    ("Exact neuron types", "type_exact", "Example: DNp01, TTMn (combined search)"),
    ("Advanced: type regex", "type_regex", "One regex, e.g. DNp01|TTMn|PSI (not a comma list)"),
    ("Exact instance", "instance_exact", "Example: DNp01_R"),
    ("Advanced: instance regex", "instance_regex", "One regex, e.g. DNp01_.*|TTMn_.* (not a comma list)"),
    ("ROI membership", "roi", "Example: legNp(T1)(R)"),
)


class _CallWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, operation: Callable[[], Any], parent=None):
        super().__init__(parent)
        self.operation = operation

    @Slot()
    def run(self) -> None:
        try:
            result = self.operation()
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.completed.emit(result)


class ExistingNeuronDialog(QDialog):
    """Explicit per-neuron replacement choices; keeping files is the default."""
    def __init__(self, dataset: str, existing: dict[str, dict], neurons, parent=None):
        super().__init__(parent)
        self.setWindowTitle("These neurons are already downloaded")
        self.setObjectName("ExistingNeuronReview")
        self.resize(850, 440)
        self.replacement_ids = ()
        self._bodies = sorted(existing, key=int)
        layout = QVBoxLayout(self)
        label = QLabel(f"Found {len(existing)} existing neuron file(s) in {dataset}.\n"
            "Keep them, or check only the neurons you want to download again and replace. "
            "Replaced files are backed up first. Nothing is replaced without your choice.")
        label.setWordWrap(True)
        layout.addWidget(label)
        names = {str(neuron.body_id): neuron.neuron_type for neuron in neurons}
        self.table = QTableWidget(len(self._bodies), 4)
        self.table.setHorizontalHeaderLabels(("Replace", "Body ID", "Type", "Existing file"))
        self.table.horizontalHeader().setStretchLastSection(True)
        for row, body in enumerate(self._bodies):
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            check.setCheckState(Qt.CheckState.Unchecked)
            self.table.setItem(row, 0, check)
            for column, value in enumerate((body, names[body], existing[body]["path"]), 1):
                item = QTableWidgetItem(value)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                item.setToolTip(value)
                self.table.setItem(row, column, item)
        layout.addWidget(self.table)
        actions = QHBoxLayout()
        self.keep_button = QPushButton("Keep all existing files")
        self.keep_button.setDefault(True)
        self.keep_button.clicked.connect(self.accept)
        self.replace_button = QPushButton("Download and replace checked files")
        self.replace_button.setEnabled(False)
        self.replace_button.clicked.connect(self._replace_checked)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        for button in (self.keep_button, self.replace_button, self.cancel_button):
            actions.addWidget(button)
        layout.addLayout(actions)
        self.table.itemChanged.connect(lambda *_: self.replace_button.setEnabled(bool(self._checked())))

    def _checked(self):
        return tuple(int(body) for row, body in enumerate(self._bodies)
                     if self.table.item(row, 0).checkState() == Qt.CheckState.Checked)

    def _replace_checked(self):
        self.replacement_ids = self._checked()
        if self.replacement_ids:
            self.accept()


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
        preferred_dataset: str = "",
        initial_selection: NeuPrintSelection | None = None,
        initial_resource_id: str = "",
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
        self._token_source = "provided"
        self._preview: tuple[NeuPrintNeuron, ...] = ()
        self._cancel_event = threading.Event()
        self._operation_kind = ""
        self._after_operation = None
        self._reviewed_request = None
        self._preferred_dataset = str(preferred_dataset).strip()
        self._worker_progress.connect(self._show_progress)

        self.setWindowTitle("Download from neuPrint")
        self.resize(900, 760)
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        root = QVBoxLayout(content)
        scroll.setWidget(content)
        outer.addWidget(scroll)
        root.setContentsMargins(22, 20, 22, 20)
        root.setSpacing(13)

        title = QLabel("Download neuron data from neuPrint")
        title.setObjectName("PageTitle")
        detail = QLabel(
            "Connect with your personal neuPrint credential, preview a bounded neuron set, "
            "review its exact destination, then download resumable SWCs and optional "
            "selected connectivity into the managed library."
        )
        detail.setObjectName("Muted")
        detail.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(detail)
        library_card = Card()
        library_form = QFormLayout(library_card)
        library_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.library_location = QLabel("Choose a dataset to see its growing SWC folder")
        self.library_location.setWordWrap(True)
        self.library_location.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.library_location.setObjectName("NeuPrintSwcLibraryLocation")
        library_form.addRow("Dataset SWC folder", self.library_location)
        self.open_library_button = QPushButton("Open dataset SWC folder")
        self.open_library_button.setEnabled(False)
        self.open_library_button.clicked.connect(self.open_library_folder)
        library_form.addRow("", self.open_library_button)
        root.addWidget(library_card)

        connection = Card()
        connection_form = QFormLayout(connection)
        connection_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
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
        self.token_edit.setPlaceholderText("Paste a DatasetGateway API key or legacy token")
        token_layout.addWidget(self.token_edit, 1)
        help_button = QPushButton("Token help")
        help_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(TOKEN_HELP_URL)))
        token_layout.addWidget(help_button)
        connection_form.addRow("neuPrint credential", token_row)
        token_options = QWidget()
        token_options_layout = QVBoxLayout(token_options)
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
                "Install a supported keyring backend to save tokens. Existing workspace tokens can be read without copying them."
            )
        token_options_layout.addWidget(self.remember_token)
        self.load_saved_button = QPushButton("Use saved token")
        self.load_saved_button.setEnabled(self.credential_store.available)
        self.load_saved_button.clicked.connect(self._load_saved_token)
        token_options_layout.addWidget(self.load_saved_button)
        token_options_layout.addStretch(1)
        connection_form.addRow("Credential source", token_options)
        self.workspace_token = QCheckBox("Use workspace Phase 1/Neuprint Token.txt")
        self.workspace_token.setObjectName("WorkspaceNeuPrintToken")
        workspace_file = workspace_token_path(profile.workspace_root)
        file_available = workspace_file is not None and workspace_file.is_file()
        self.workspace_token.setEnabled(file_available)
        self.workspace_token.setChecked(file_available)
        self.workspace_token.setToolTip(
            str(workspace_file) if workspace_file else "Select a Digifly workspace first"
        )
        connection_form.addRow("Workspace credential file", self.workspace_token)
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
        selection_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        selection_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        selection_form.setContentsMargins(16, 15, 16, 15)
        selection_form.setHorizontalSpacing(16)
        selection_form.setVerticalSpacing(9)
        self.dataset_combo = QComboBox()
        self.dataset_combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.dataset_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.dataset_combo.setMinimumContentsLength(20)
        self.dataset_combo.setAccessibleName("NeuPrint dataset and version")
        self.dataset_combo.setToolTip("Exact NeuPrint dataset/version. Alt+D focuses this selector.")
        self.dataset_focus_shortcut = QShortcut(QKeySequence("Alt+D"), self)
        self.dataset_focus_shortcut.activated.connect(self.dataset_combo.setFocus)
        self.dataset_combo.setEnabled(False)
        selection_form.addRow("Dataset and version", self.dataset_combo)
        selection_row = QWidget()
        selection_layout = QHBoxLayout(selection_row)
        selection_layout.setContentsMargins(0, 0, 0, 0)
        self.selection_mode = QComboBox()
        self.selection_mode.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
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
        self.selection_help = QLabel()
        self.selection_help.setWordWrap(True)
        self.selection_help.setMinimumHeight(self.selection_help.fontMetrics().lineSpacing() * 4)
        self.selection_help.setObjectName("Muted")
        selection_form.addRow(self.selection_help)
        self._update_selection_help()
        self.preview_table = QTableWidget(0, 4)
        self.preview_table.setMinimumHeight(150)
        self.preview_table.setHorizontalHeaderLabels(("Download", "Body ID", "Type", "Instance"))
        self.preview_table.horizontalHeader().setStretchLastSection(True)
        self.preview_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.preview_table.itemChanged.connect(self._update_destination)
        selection_form.addRow(QLabel("Matching neurons — review before downloading"))
        selection_form.addRow(self.preview_table)
        root.addWidget(selection, 1)

        destination = Card()
        destination_form = QFormLayout(destination)
        destination_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        destination_form.setContentsMargins(16, 15, 16, 15)
        destination_form.setHorizontalSpacing(16)
        destination_form.setVerticalSpacing(8)
        managed_root = QLabel(str(profile.managed_data_root))
        managed_root.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        managed_root.setWordWrap(True)
        destination_form.addRow("Managed library root", managed_root)
        # These identifiers only bind partial-download receipts. They no longer
        # create or name visible SWC folders, so they are not user-facing fields.
        self.name_edit = QLineEdit("neuprint-selection", self)
        self.snapshot_edit = QLineEdit(datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"), self)
        self.name_edit.hide()
        self.snapshot_edit.hide()
        self.connectivity_check = QCheckBox(
            "Update this dataset's shared connectivity file"
        )
        self.connectivity_check.setChecked(True)
        self.connectivity_check.setToolTip(
            "Adds both directions between selected and earlier app-managed neurons. "
            "Counts replace earlier observations of the same directed pair; they are not added twice. "
            "Linked external libraries are not automatically queried."
        )
        destination_form.addRow("Connectivity table", self.connectivity_check)
        self.destination_label = QLabel("Preview neurons to calculate the exact destination")
        self.destination_label.setObjectName("Muted")
        self.destination_label.setWordWrap(True)
        self.destination_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        destination_form.addRow("Neuron files", self.destination_label)
        self.storage_help = QLabel("Every search uses the same dataset folder: family → type → body ID → SWC. "
            "Before downloading, the app checks this folder and asks which existing files you want to replace. "
            "Connectivity grows in one file alongside this dataset's SWCs. Internal history keeps saved experiments reproducible; "
            "no new search-named SWC folder is created.")
        self.storage_help.setWordWrap(True)
        destination_form.addRow("", self.storage_help)
        self.discard_checkpoint_button = QPushButton("Discard saved partial download…")
        self.discard_checkpoint_button.setVisible(False)
        self.discard_checkpoint_button.clicked.connect(self.discard_checkpoint)
        destination_form.addRow("Saved checkpoint", self.discard_checkpoint_button)
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

        if initial_selection is not None:
            selection_index = self.selection_mode.findData(initial_selection.mode)
            if selection_index >= 0:
                self.selection_mode.setCurrentIndex(selection_index)
                self.selection_edit.setPlaceholderText(SELECTION_OPTIONS[selection_index][2])
                self._update_selection_help()
            self.selection_edit.setText(initial_selection.value)
            self.limit_spin.setValue(initial_selection.limit)
        if initial_resource_id:
            try:
                self.name_edit.setText(
                    safe_component(initial_resource_id, field="download name")
                )
            except ValueError:
                pass

        self.server_edit.textChanged.connect(self._connection_invalidated)
        self.token_edit.textChanged.connect(self._connection_invalidated)
        self.environment_token.toggled.connect(self._connection_invalidated)
        self.workspace_token.toggled.connect(self._connection_invalidated)
        self.dataset_combo.currentIndexChanged.connect(self._preview_invalidated)
        self.selection_mode.currentIndexChanged.connect(self._selection_mode_changed)
        self.selection_edit.textChanged.connect(self._preview_invalidated)
        self.limit_spin.valueChanged.connect(self._preview_invalidated)
        self.name_edit.textChanged.connect(self._update_destination)
        self.snapshot_edit.textChanged.connect(self._update_destination)
        self.connectivity_check.toggled.connect(self._update_destination)

    @property
    def operation_in_progress(self) -> bool:
        return self._thread is not None

    def _redact(self, detail: str) -> str:
        return detail.replace(self._active_token, "[redacted]") if self._active_token else detail

    def _set_busy(self, busy: bool) -> None:
        # Keep the reviewed query/dataset identical to the acquisition manifest.
        # Previously edits during a worker could relabel an old preview as a new query.
        for control in (self.server_edit, self.token_edit, self.selection_mode,
                        self.selection_edit, self.limit_spin, self.name_edit,
                        self.snapshot_edit, self.connectivity_check):
            control.setEnabled(not busy)
        self.dataset_combo.setEnabled(not busy and bool(self._active_token) and self.dataset_combo.count() > 0)
        self.connect_button.setEnabled(not busy)
        self.preview_button.setEnabled(not busy and bool(self._active_token) and bool(self.dataset_combo.currentData()))
        self.download_button.setEnabled(not busy and bool(self._selected_neurons()))
        self.close_button.setEnabled(not busy)
        self.discard_checkpoint_button.setEnabled(
            not busy and self.discard_checkpoint_button.isVisible()
        )

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
        # A moved QObject could be destroyed concurrently by Python's final
        # reference and Qt's worker-thread deleteLater during finished handling.
        # Keep the operation on one parent-owned QThread instead.
        worker = _CallWorker(operation, self)
        worker.completed.connect(self._operation_completed)
        worker.failed.connect(self._operation_failed)
        worker.finished.connect(self._operation_finished)
        worker.finished.connect(worker.deleteLater)
        self._thread = worker
        self._worker = worker
        worker.start()

    @Slot(object)
    def _operation_completed(self, result: Any) -> None:
        if self._success_handler is not None:
            self._success_handler(result)

    @Slot(str)
    def _operation_failed(self, detail: str) -> None:
        safe_detail = self._redact(detail)
        if isinstance(detail, str) and "cancel" in detail.casefold():
            self.status.setText(
                "Download cancelled; verified completed files remain in a credential-free "
                "checkpoint and will resume on retry."
            )
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
        self.cancel_button.setEnabled(True)
        if not was_download:
            self.progress.setVisible(False)
        self._set_busy(False)
        continuation = self._after_operation
        self._after_operation = None
        if continuation is not None:
            continuation()

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
            self.preview_button.setEnabled(bool(self._active_token) and bool(self.dataset_combo.currentData()))

    def _selection_mode_changed(self, index: int) -> None:
        self.selection_edit.setPlaceholderText(SELECTION_OPTIONS[index][2])
        self._update_selection_help()
        self._preview_invalidated()

    def _update_selection_help(self) -> None:
        mode = self.selection_mode.currentData()
        if mode == "name_list":
            text = ("Fetch the combined matches for every name in your list. Names match type or instance "
                    "prefixes, ignoring case; numeric IDs match exactly. Review the rows before downloading. "
                    "The limit applies to the whole list, not each name.")
        elif mode in {"type_regex", "instance_regex"}:
            text = ("Advanced: this is one complete regular expression. Commas are literal, not list separators. "
                    "For DNp01, TTMn, PSI choose ‘Neuron names / IDs (list)’ instead.")
        else:
            text = ("Exact type/instance fields accept comma- or semicolon-separated names; body IDs must be numeric. "
                    "All matches stay within the selected dataset/version and the total limit.")
        self.selection_help.setText(text)

    def _clear_preview(self) -> None:
        self._reviewed_request = None
        self._preview = ()
        self.preview_table.setRowCount(0)
        self.download_button.setEnabled(False)
        self.destination_label.setText("Preview neurons to calculate the exact destination")
        self._update_destination()

    def _token_for_connection(self) -> str:
        if self.environment_token.isChecked():
            token = token_from_environment()
            if token is None:
                raise ValueError(f"{NEUPRINT_CREDENTIAL_ENV} is not set for this app launch")
            self._token_source = "environment"
            return token
        if self._active_token and not self.token_edit.text().strip():
            return self._active_token
        if not self.token_edit.text().strip() and self.workspace_token.isChecked():
            token = token_from_workspace(self.profile.workspace_root, self.server_edit.text())
            self._token_source = "workspace_file"
            return token
        self._token_source = "provided"
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
        self._token_source = "saved"
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
            self.dataset_combo.addItem(dataset.name, dataset.name)
            if dataset.description:
                self.dataset_combo.setItemData(self.dataset_combo.count() - 1,
                                              dataset.description, Qt.ItemDataRole.ToolTipRole)
        preferred_index = self.dataset_combo.findData(self._preferred_dataset)
        if preferred_index >= 0:
            self.dataset_combo.setCurrentIndex(preferred_index)
        elif self._preferred_dataset:
            # Never turn a chosen release into the server's first dataset.
            self.dataset_combo.setCurrentIndex(-1)
        self.dataset_combo.blockSignals(False)
        self.dataset_combo.setEnabled(True)
        self.preview_button.setEnabled(bool(self.dataset_combo.currentData()))
        self.connection_status.setText(f"Connected; {len(datasets)} dataset(s) available")
        self.status.setText("Choose the exact dataset and preview a bounded neuron selection.")
        if self._preferred_dataset and preferred_index < 0:
            self.status.setText(f'The selected release {self._preferred_dataset} is not available on this server. '
                                'Choose another release explicitly; no replacement was selected.')
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
            if self.selection_mode.currentData() in {"name_list", "type_exact"}:
                try:
                    self.name_edit.setText(safe_component(self.selection_edit.text()))
                except ValueError:
                    pass
            self.status.setText(
                f"Previewed {len(neurons)} neuron(s). Uncheck any rows you do not want to download."
            )
            if self.selection_mode.currentData() == "name_list":
                terms = name_selection_terms(self.selection_edit.text())
                counts = [(term, sum(neuron_matches_name(neuron, term) for neuron in neurons)) for term in terms]
                self.status.setText(self.status.text() + " Returned matches: " + "; ".join(
                    f"{term}: {count}" for term, count in counts) + ".")
                missing = [term for term, count in counts if not count]
                if missing:
                    self.status.setText(self.status.text() + " No returned match for: " + ", ".join(missing)
                                        + ". Check dataset/version, annotation and total limit.")
            elif self.selection_mode.currentData() in {"type_exact", "instance_exact"}:
                field = "neuron_type" if self.selection_mode.currentData() == "type_exact" else "instance"
                present = {getattr(neuron, field) for neuron in neurons}
                missing = [name for name in exact_selection_terms(self.selection_edit.text()) if name not in present]
                if missing:
                    self.status.setText(self.status.text() + " No returned match for: " + ", ".join(missing) + ". Check dataset/version, exact annotation and limit.")
            if len(neurons) >= self.limit_spin.value():
                self.status.setText(self.status.text() + " Query reached its limit; additional neurons may be omitted.")
        else:
            self.status.setText("No neurons matched this selection; no download is available. "
                                "For a list such as DNp01, TTMn, PSI use ‘Neuron names / IDs (list)’. "
                                "Check the selected dataset/version and its annotations.")
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
            acquisition_entrypoint="workstation_neuprint_dialog",
            credential_source=self._token_source,
            include_connectivity=self.connectivity_check.isChecked(),
        )

    def _update_destination(self, *_args: Any) -> None:
        dataset = str(self.dataset_combo.currentData() or "")
        try:
            swc_root = library_root(self.profile, normalize_server(self.server_edit.text()), dataset) / "export_swc"
            self.library_location.setText(f"{swc_root}\nAN / DN / IN / MN / SN / UNKNOWN → type → body ID")
            self.open_library_button.setEnabled(swc_root.is_dir())
        except ValueError:
            self.library_location.setText("Choose a dataset to see its growing SWC folder")
            self.open_library_button.setEnabled(False)
        try:
            request = self._request()
            destination = swc_root
        except ValueError:
            self.destination_label.setText("Preview and select neurons to see their dataset folders")
            self.discard_checkpoint_button.setVisible(False)
            self.download_button.setEnabled(False)
            return
        checkpoint_request = self._reviewed_request or request
        checkpoint = neuprint_checkpoint_path(self.profile, checkpoint_request)
        checkpoint_note = (
            "\nA saved partial download matches this request and will resume automatically."
            if (checkpoint / "digifly-neuprint-checkpoint.json").is_file()
            else ""
        )
        examples = []
        for neuron in request.neurons[:4]:
            family, kind = _labels(Path(), str(neuron.body_id), {"type": neuron.neuron_type})
            examples.append(f"{family}/{kind}/{neuron.body_id}/{neuron.body_id}.swc")
        extra = f"\n… and {len(request.neurons) - 4} more neuron files" if len(request.neurons) > 4 else ""
        self.destination_label.setText(f"{destination}\n" + "\n".join(examples) + extra + checkpoint_note)
        self.discard_checkpoint_button.setVisible(bool(checkpoint_note))
        self.discard_checkpoint_button.setEnabled(self._thread is None and bool(checkpoint_note))
        self.download_button.setEnabled(self._thread is None and bool(request.neurons))

    @Slot()
    def open_library_folder(self) -> None:
        try:
            path = library_root(self.profile, normalize_server(self.server_edit.text()),
                                str(self.dataset_combo.currentData() or "")) / "export_swc"
        except ValueError:
            return
        if path.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    @Slot()
    def discard_checkpoint(self) -> None:
        try:
            request = self._reviewed_request or self._request()
            checkpoint = neuprint_checkpoint_path(self.profile, request)
        except ValueError as exc:
            QMessageBox.warning(self, "Partial download is unavailable", str(exc))
            return
        answer = QMessageBox.question(
            self,
            "Permanently discard partial neuPrint download",
            f"Permanently delete the saved partial acquisition?\n\n{checkpoint}\n\n"
            "Completed SWCs and connectivity in this checkpoint cannot be restored after deletion.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            removed = discard_neuprint_checkpoint(self.profile, request)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Could not discard partial download", str(exc))
            return
        self.status.setText(
            "Saved partial download was permanently discarded."
            if removed
            else "No matching partial download remains."
        )
        self._update_destination()

    @Slot()
    def start_download(self) -> None:
        try:
            request = self._request()
        except ValueError as exc:
            QMessageBox.warning(self, "Download details are incomplete", str(exc))
            return
        self.status.setText("Checking this dataset's SWC folders for existing neurons…")
        def inspect():
            bodies = tuple(neuron.body_id for neuron in request.neurons)
            existing = inspect_existing_swcs(self.profile, request.server, request.dataset, bodies)
            from digifly_app.core.selected_connectivity import review_connectivity
            plan = review_connectivity(self.profile, request.server, request.dataset, bodies) if request.include_connectivity else None
            return existing, plan
        def ready(result):
            existing, plan = result
            reviewed = replace(request, connectivity_review=plan)
            self._after_operation = lambda: self._review_download(reviewed, existing)
        self._start_operation("existing-file check", inspect, ready)

    def _review_download(self, request, existing):
        replacements = ()
        if existing:
            review = ExistingNeuronDialog(request.dataset, existing, request.neurons, self)
            if review.exec() != QDialog.DialogCode.Accepted:
                self.status.setText("Download cancelled. Existing files were not changed.")
                return
            replacements = review.replacement_ids
        request = replace(request, library_review=existing, replacement_ids=replacements)
        try:
            request = matching_library_checkpoint(self.profile, request)
        except ValueError as exc:
            QMessageBox.warning(self, "Saved download needs review", str(exc))
            return
        self._reviewed_request = request
        count = len(request.neurons)
        connectivity_note = (
            f"\n\nShared connectivity: {len(request.connectivity_review['participants']):,} app-managed neurons, "
            f"refreshing {len(request.connectivity_review['focus']):,} selected/new endpoints in both directions. "
            "Earlier directed pairs are retained or refreshed, never counted twice. "
            "This is not the whole connectome or the contents of linked external libraries. "
            "Limit: 5,000 managed neurons / 250,000 pairs / 128 MiB per update.\n"
            f"{library_root(self.profile, request.server, request.dataset) / 'connectivity/connectivity.sqlite'}"
            if request.include_connectivity
            else ""
        )
        resume_note = (
            "\n\nA matching verified checkpoint exists; completed files will not be downloaded again."
            if (neuprint_checkpoint_path(self.profile, request) / "digifly-neuprint-checkpoint.json").is_file()
            else ""
        )
        large_note = "\n\nThis is a larger job; verify the limit and free disk space." if count > 100 else ""
        answer = QMessageBox.question(
            self,
            "Confirm neuPrint download",
            f"{request.dataset}: {count - len(existing)} new neuron(s), {len(replacements)} approved replacement(s), "
            f"{len(existing) - len(replacements)} existing file(s) kept without another download.\n\n"
            f"Neuron folder:\n{library_root(self.profile, request.server, request.dataset) / 'export_swc'}\n\n"
            "Each file follows family/type/body-ID/body-ID.swc. Previous files are backed up before an "
            "approved replacement. Different datasets and releases stay separate. "
            f"Download details are kept in the dataset history.{connectivity_note}{resume_note}{large_note}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._cancel_event.clear()
        self.progress.setRange(0, count)
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self.cancel_button.setVisible(True)
        self.cancel_button.setEnabled(True)
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
                add_to_library=True,
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
        self.status.setText(
            "Cancelling after the current network read; verified completed files will remain "
            "available for a later resume…"
        )

    def _download_complete(self, resource: ManagedResource) -> None:
        self.progress.setValue(self.progress.maximum())
        self.status.setText(
            f"Dataset library now contains {resource.swc_count} SWC(s). SWC folder: {resource.root / 'export_swc'}. "
            "Opening the post-import quality review next."
        )
        self.open_library_button.setEnabled((resource.root / "export_swc").is_dir())
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
