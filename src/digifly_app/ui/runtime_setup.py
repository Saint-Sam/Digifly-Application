from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QThread, QUrl, Signal, Slot
from PySide6.QtGui import QCloseEvent, QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from digifly_app.core.runtime_discovery import (
    SimulatorRuntime,
    discover_simulator_runtimes,
)


NEURON_INSTALL_URL = "https://nrn.readthedocs.io/en/latest/index.html#installation"
ARBOR_INSTALL_URL = "https://docs.arbor-sim.org/en/latest/install/python.html"
BMTK_INSTALL_URL = "https://alleninstitute.github.io/bmtk/installation.html"


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
    runtimes_selected = Signal(str, str, str)

    def __init__(
        self,
        *,
        current_neuron: str = "",
        current_arbor: str = "",
        current_bmtk: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Set up scientific runtimes")
        self.resize(1040, 650)
        self._thread: QThread | None = None
        self._worker: _DiscoveryWorker | None = None
        self._choice_available = False
        self._current = tuple(
            value
            for value in (current_neuron, current_arbor, current_bmtk)
            if value.strip()
        )

        root = QVBoxLayout(self)
        title = QLabel("Scientific runtime setup")
        title.setObjectName("PageTitle")
        root.addWidget(title)
        detail = QLabel(
            "Digifly Workstation does not bundle simulators. Install the runtime you want in "
            "a compatible Python environment, or allow a read-only search for environments "
            "that already exist on this machine. BMTK BioNet needs BMTK, NEURON, NumPy, and h5py in "
            "the same environment. Digifly verifies each environment in a child process; it "
            "does not import simulators into the app process."
        )
        detail.setObjectName("Muted")
        detail.setWordWrap(True)
        root.addWidget(detail)

        guidance = QHBoxLayout()
        neuron_docs = QPushButton("NEURON installation guide")
        neuron_docs.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(NEURON_INSTALL_URL))
        )
        guidance.addWidget(neuron_docs)
        arbor_docs = QPushButton("Arbor installation guide")
        arbor_docs.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(ARBOR_INSTALL_URL))
        )
        guidance.addWidget(arbor_docs)
        bmtk_docs = QPushButton("BMTK / BioNet installation guide")
        bmtk_docs.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(BMTK_INSTALL_URL))
        )
        guidance.addWidget(bmtk_docs)
        guidance.addStretch(1)
        self.search_button = QPushButton("Allow read-only runtime search…")
        self.search_button.setProperty("primary", True)
        self.search_button.clicked.connect(self.request_search)
        guidance.addWidget(self.search_button)
        root.addLayout(guidance)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        root.addWidget(self.progress)
        self.status = QLabel("No search has been run.")
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ("Python interpreter", "Python", "NEURON", "Arbor", "BMTK / BioNet")
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        root.addWidget(self.table, 1)

        form = QFormLayout()
        self.neuron_combo = QComboBox()
        self.neuron_combo.setEnabled(False)
        form.addRow("Use for NEURON", self.neuron_combo)
        self.arbor_combo = QComboBox()
        self.arbor_combo.setEnabled(False)
        form.addRow("Use for Arbor", self.arbor_combo)
        self.bmtk_combo = QComboBox()
        self.bmtk_combo.setEnabled(False)
        form.addRow("Use for BMTK BioNet", self.bmtk_combo)
        root.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.use_button = buttons.addButton(
            "Use selected runtimes", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.use_button.setEnabled(False)
        self.use_button.clicked.connect(self.use_selected)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    @Slot()
    def request_search(self) -> None:
        if self._thread is not None:
            return
        answer = QMessageBox.question(
            self,
            "Allow runtime search?",
            "Digifly will read PATH plus common Conda, virtual-environment, and "
            "Digifly-Runtimes folders. It checks only immediate environment folders and "
            "runs each candidate Python briefly to read NEURON, Arbor, and BMTK metadata. "
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
        self.neuron_combo.setEnabled(self.neuron_combo.count() > 0)
        self.arbor_combo.setEnabled(self.arbor_combo.count() > 0)
        self.bmtk_combo.setEnabled(self.bmtk_combo.count() > 0)
        self._choice_available = (
            self.neuron_combo.count() > 0
            or self.arbor_combo.count() > 0
            or self.bmtk_combo.count() > 0
        )
        if results:
            self.status.setText(
                f"Found {len(results)} Python environment(s) containing a supported simulator. "
                "Only interpreters where BioNet and NEURON import together are offered for BMTK."
            )
        else:
            self.status.setText(
                "No NEURON, Arbor, or BMTK Python package was found in the approved locations. "
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
        self.runtimes_selected.emit(neuron, arbor, bmtk)
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
