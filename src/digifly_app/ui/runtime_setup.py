from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QThread, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QCloseEvent, QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from digifly_app.core.runtime_discovery import (
    SimulatorRuntime,
    discover_simulator_runtimes,
)


NEURON_INSTALL_URL = "https://nrn.readthedocs.io/en/latest/index.html#installation"
ARBOR_INSTALL_URL = "https://docs.arbor-sim.org/en/latest/install/python.html"
BMTK_INSTALL_URL = "https://alleninstitute.github.io/bmtk/installation.html"
NETPYNE_INSTALL_URL = "https://doc.netpyne.org/install.html"


class _DiscoveryWorker(QObject):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, explicit: tuple[str, ...]):
        super().__init__()
        self.explicit = explicit

    @Slot()
    def run(self) -> None:
        try:
            results = discover_simulator_runtimes(explicit=self.explicit)
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.completed.emit(results)


class RuntimeSetupDialog(QDialog):
    runtimes_selected = Signal(str, str, str, str)

    def __init__(
        self,
        *,
        current_neuron: str = "",
        current_arbor: str = "",
        current_bmtk: str = "",
        current_netpyne: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Set up scientific runtimes")
        available = self.screen().availableGeometry()
        self.resize(min(960, int(available.width() * 0.9)), min(740, int(available.height() * 0.9)))
        self._thread: QThread | None = None
        self._worker: _DiscoveryWorker | None = None
        self._choice_available = False
        self._current_by_engine = dict(zip(
            ("neuron", "arbor", "bmtk", "netpyne"),
            (value.strip() for value in (current_neuron, current_arbor, current_bmtk, current_netpyne)),
        ))
        self._current = tuple(
            value
            for value in self._current_by_engine.values()
            if value
        )

        root = QVBoxLayout(self)
        # Long environment paths and guidance must not enlarge the dialog past
        # the screen. Choices scroll; final actions stay outside the viewport.
        self.content_scroll = QScrollArea()
        self.content_scroll.setWidgetResizable(True)
        self.content_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.content_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        content = QWidget()
        content.setMinimumWidth(0)
        content_layout = QVBoxLayout(content)
        self.content_scroll.setWidget(content)
        root.addWidget(self.content_scroll, 1)
        title = QLabel("Scientific runtime setup")
        title.setObjectName("PageTitle")
        title.setWordWrap(True)
        content_layout.addWidget(title)
        detail = QLabel(
            "Install the runtime you want in "
            "a compatible Python environment, or allow a read-only search for environments "
            "that already exist on this machine. BMTK BioNet needs BMTK, NEURON, NumPy, and h5py in "
            "the same environment. NetPyNE needs NEURON in its selected Python. Digifly checks each environment in a child process; it "
            "does not import simulators into the app process."
        )
        detail.setObjectName("Muted")
        detail.setWordWrap(True)
        content_layout.addWidget(detail)

        guidance = QGridLayout()
        neuron_docs = QPushButton("NEURON guide")
        neuron_docs.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(NEURON_INSTALL_URL))
        )
        guidance.addWidget(neuron_docs, 0, 0)
        arbor_docs = QPushButton("Arbor guide")
        arbor_docs.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(ARBOR_INSTALL_URL))
        )
        guidance.addWidget(arbor_docs, 0, 1)
        bmtk_docs = QPushButton("BioNet guide")
        bmtk_docs.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(BMTK_INSTALL_URL))
        )
        guidance.addWidget(bmtk_docs, 1, 0)
        netpyne_docs = QPushButton("NetPyNE guide")
        netpyne_docs.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(NETPYNE_INSTALL_URL))
        )
        guidance.addWidget(netpyne_docs, 1, 1)
        self.search_button = QPushButton("Allow read-only runtime search…")
        self.search_button.setProperty("primary", True)
        self.search_button.clicked.connect(self.request_search)
        guidance.addWidget(self.search_button, 2, 0, 1, 2)
        content_layout.addLayout(guidance)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        content_layout.addWidget(self.progress)
        self.status = QLabel("No search has been run.")
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        content_layout.addWidget(self.status)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ("Python interpreter", "Python", "NEURON", "Arbor", "BMTK / BioNet", "NetPyNE")
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setDefaultSectionSize(140)
        self.table.setColumnWidth(0, 300)
        self.table.setMinimumHeight(180)
        self.table.setTabKeyNavigation(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        content_layout.addWidget(self.table)

        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.neuron_combo = QComboBox()
        self.neuron_combo.setEnabled(False)
        form.addRow("Use for NEURON", self.neuron_combo)
        self.arbor_combo = QComboBox()
        self.arbor_combo.setEnabled(False)
        form.addRow("Use for Arbor", self.arbor_combo)
        self.bmtk_combo = QComboBox()
        self.bmtk_combo.setEnabled(False)
        form.addRow("Use for BMTK BioNet", self.bmtk_combo)
        self.netpyne_combo = QComboBox()
        self.netpyne_combo.setEnabled(False)
        form.addRow("Use for NetPyNE", self.netpyne_combo)
        for combo in (self.neuron_combo, self.arbor_combo, self.bmtk_combo, self.netpyne_combo):
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(20)
            combo.setMinimumWidth(0)
            # QFormLayout.ExpandingFieldsGrow collapses Ignored fields to zero
            # width. The minimum-content adjustment already bounds long paths;
            # Expanding gives the visible selector its available form space.
            combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            combo.currentIndexChanged.connect(lambda _index, selected=combo: selected.setToolTip(str(selected.currentData() or "")))
        content_layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.use_button = buttons.addButton(
            "Use selected runtimes", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.use_button.setEnabled(False)
        self.use_button.clicked.connect(self.use_selected)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.setTabOrder(self.search_button, self.neuron_combo)
        self.setTabOrder(self.neuron_combo, self.arbor_combo)
        self.setTabOrder(self.arbor_combo, self.bmtk_combo)
        self.setTabOrder(self.bmtk_combo, self.netpyne_combo)
        self.setTabOrder(self.netpyne_combo, self.use_button)
        self.netpyne_combo.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:
        # The scroll area's focus traversal can cycle back into its table
        # instead of crossing to the fixed footer. Make the final choice's
        # forward Tab explicit without changing combo selection/activation.
        if (watched is self.netpyne_combo and event.type() == QEvent.Type.KeyPress
                and event.key() == Qt.Key.Key_Tab
                and event.modifiers() == Qt.KeyboardModifier.NoModifier
                and self.use_button.isEnabled()):
            self.use_button.setFocus(Qt.FocusReason.TabFocusReason)
            return True
        return super().eventFilter(watched, event)

    @Slot()
    def request_search(self) -> None:
        if self._thread is not None:
            return
        answer = QMessageBox.question(
            self,
            "Allow runtime search?",
            "Digifly will read PATH plus common Conda, virtual-environment, and "
            "Digifly-Runtimes folders. It checks only immediate environment folders and "
            "runs each candidate Python briefly to read NEURON, Arbor, BMTK, and NetPyNE metadata. "
            "For BMTK it also verifies that BioNet, NEURON, NumPy, and h5py import together.\n\n"
            "It will not scan the whole disk, install software, import simulator modules "
            "into the app, or modify any environment.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            self.status.setText("Runtime search was not authorized; no locations were inspected.")
            return
        self.search_button.setEnabled(False)
        self.use_button.setEnabled(False)
        self._choice_available = False
        self.progress.setVisible(True)
        self.status.setText("Checking approved runtime locations…")
        thread = QThread(self)
        worker = _DiscoveryWorker(self._current)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self._discovery_completed)
        worker.failed.connect(self._discovery_failed)
        worker.completed.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(self._thread_finished)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._thread = thread
        self._worker = worker
        thread.start()

    @Slot(object)
    def _discovery_completed(self, results: tuple[SimulatorRuntime, ...]) -> None:
        self.table.setRowCount(0)
        self.neuron_combo.clear()
        self.arbor_combo.clear()
        self.bmtk_combo.clear()
        self.netpyne_combo.clear()
        for result in results:
            row = self.table.rowCount()
            self.table.insertRow(row)
            for column, text in enumerate(
                (
                    str(result.python),
                    result.python_version or "unknown",
                    result.neuron_version or "not installed",
                    result.arbor_version or "not installed",
                    (
                        f"{result.bmtk_version} · BioNet ready"
                        if result.bionet_ready
                        else f"{result.bmtk_version} · BioNet blocked: {result.bionet_error}"
                        if result.has_bmtk
                        else "not installed"
                    ),
                    (
                        f"{result.netpyne_version} · NEURON {result.neuron_version}"
                        if result.has_netpyne and result.has_neuron
                        else f"{result.netpyne_version} · NEURON missing"
                        if result.has_netpyne
                        else "not installed"
                    ),
                )
            ):
                self.table.setItem(row, column, QTableWidgetItem(text))
            if result.has_neuron:
                self.neuron_combo.addItem(
                    f"NEURON {result.neuron_version} · {result.python}", str(result.python)
                )
            if result.has_arbor:
                self.arbor_combo.addItem(
                    f"Arbor {result.arbor_version} · {result.python}", str(result.python)
                )
            if result.bionet_ready:
                self.bmtk_combo.addItem(
                    f"BMTK {result.bmtk_version} + BioNet · {result.python}",
                    str(result.python),
                )
            if result.has_netpyne and result.has_neuron:
                self.netpyne_combo.addItem(
                    f"NetPyNE {result.netpyne_version} + NEURON {result.neuron_version} · {result.python}",
                    str(result.python),
                )
        for engine, combo in (("neuron", self.neuron_combo), ("arbor", self.arbor_combo),
                              ("bmtk", self.bmtk_combo), ("netpyne", self.netpyne_combo)):
            current = self._current_by_engine[engine]
            index = combo.findData(str(Path(current).expanduser())) if current else -1
            if index >= 0:
                combo.setCurrentIndex(index)
            combo.setEnabled(combo.count() > 0)
            combo.setToolTip(str(combo.currentData() or ""))
            for index in range(combo.count()):
                combo.setItemData(index, str(combo.itemData(index)), Qt.ItemDataRole.ToolTipRole)
        self._choice_available = (
            self.neuron_combo.count() > 0
            or self.arbor_combo.count() > 0
            or self.bmtk_combo.count() > 0
            or self.netpyne_combo.count() > 0
        )
        if results:
            self.status.setText(
                f"Found {len(results)} Python environment(s) containing a supported simulator. "
                "BMTK requires BioNet with NEURON; NetPyNE is offered only alongside NEURON."
            )
        else:
            self.status.setText(
                "No NEURON, Arbor, BMTK, or NetPyNE Python package was found in the approved locations. "
                "Use the official installation guides, then search again."
            )

    @Slot(str)
    def _discovery_failed(self, detail: str) -> None:
        self.status.setText(f"Runtime search failed: {detail}")
        QMessageBox.critical(self, "Runtime search failed", detail)

    @Slot()
    def _thread_finished(self) -> None:
        self._thread = None
        self._worker = None
        self.progress.setVisible(False)
        self.search_button.setEnabled(True)
        self.use_button.setEnabled(self._choice_available)

    @Slot()
    def use_selected(self) -> None:
        neuron = str(self.neuron_combo.currentData() or "")
        arbor = str(self.arbor_combo.currentData() or "")
        bmtk = str(self.bmtk_combo.currentData() or "")
        netpyne = str(self.netpyne_combo.currentData() or "")
        self.runtimes_selected.emit(neuron, arbor, bmtk, netpyne)
        self.accept()

    def reject(self) -> None:
        if self._thread is not None:
            QMessageBox.information(
                self,
                "Runtime search in progress",
                "Wait for the read-only runtime checks to finish before closing this window.",
            )
            return
        super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._thread is not None:
            QMessageBox.information(
                self,
                "Runtime search in progress",
                "Wait for the read-only runtime checks to finish before closing this window.",
            )
            event.ignore()
            return
        super().closeEvent(event)
