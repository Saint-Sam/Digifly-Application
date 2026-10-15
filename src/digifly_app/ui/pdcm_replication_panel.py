"""User-facing independent PDCM execution, separate from the author reference.

Scientific code is launched in the selected external BioNet runtime. Saved
native and BioNet results are opened without rerunning their simulations.
"""

from __future__ import annotations

from pathlib import Path
from threading import Event
from datetime import datetime

from PySide6.QtCore import QThread, Signal, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from .widgets import Card


class BioNetReplicationWorker(QThread):
    succeeded = Signal(str)
    failed = Signal(str)
    progress = Signal(str)
    cancelled = Signal()

    def __init__(self, spec, output_root: Path, python: Path, parent=None):
        super().__init__(parent)
        self.spec, self.output_root, self.python = spec, output_root, python
        self._cancel = Event()

    def request_cancel(self):
        self._cancel.set()

    def run(self):
        if self.spec.scale == .05:
            from digifly_app.core.pdcm_bionet_5pct_app import prepare_run, run_prepared
        elif self.spec.scale == .02:
            from digifly_app.core.pdcm_bionet_scalable_app import prepare_run, run_prepared
        else:
            from digifly_app.core.pdcm_bionet_app import prepare_run, run_prepared
        try:
            self.progress.emit("Freezing the independent model and exact condition…")
            root = prepare_run(self.spec, output_root=self.output_root)
            result = run_prepared(
                root, python_executable=self.python,
                progress=self.progress.emit, cancel_requested=self._cancel.is_set,
            )
            if isinstance(result, dict) and result.get("raw_run_root"):
                from digifly_app.core.pdcm_campaign_results import create_bionet_results_view
                self.progress.emit("Opening full-duration paper-window metrics and raster in Results…")
                summary = create_bionet_results_view(
                    result["raw_run_root"], self.output_root / "replication_views",
                    analysis_root=result["analysis_root"], python_executable=self.python,
                )
            else:
                summary = result.get("summary_path", str(root / "summary.json")) if isinstance(result, dict) else str(result)
            self.succeeded.emit(str(summary))
        except InterruptedError:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(str(exc))


class BioNetSavedResultWorker(QThread):
    succeeded = Signal(str)
    failed = Signal(str)

    def __init__(self, source: Path, output_root: Path, python: Path, parent=None):
        super().__init__(parent)
        self.source, self.output_root, self.python = source, output_root, python

    def run(self):
        from digifly_app.core.pdcm_campaign_results import create_bionet_results_view
        try:
            summary = create_bionet_results_view(
                self.source, self.output_root / "replication_views", python_executable=self.python,
            )
            self.succeeded.emit(str(summary))
        except Exception as exc:
            self.failed.emit(str(exc))


class SavedFigureWorker(QThread):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, source: Path, destination: Path | None = None, parent=None):
        super().__init__(parent)
        self.source, self.destination = source, destination

    def run(self):
        from digifly_app.core.pdcm_figure_bundle import load_figure_bundle, export_figure_bundle
        try:
            source = export_figure_bundle(self.source, self.destination) if self.destination else self.source
            self.succeeded.emit(load_figure_bundle(source))
        except Exception as exc:
            self.failed.emit(str(exc))


class PDCMReplicationPanel(QWidget):
    result_ready = Signal(str)
    busy_changed = Signal(bool)

    def __init__(self, overview, parent=None):
        super().__init__(parent)
        self.overview = overview
        self.worker = None
        self.import_worker = None
        self.figure_worker = None
        self.figure_bundle = None
        self.runtime_busy = lambda: False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(14)
        card = Card()
        form = QFormLayout(card)
        form.setContentsMargins(18, 18, 18, 18)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        heading = QLabel("Independent PDCM · SONATA / BioNet")
        heading.setObjectName("SectionTitle")
        form.addRow(heading)
        note = QLabel(
            "Run the separately authored released-method point-neuron model in real BMTK BioNet. "
            "The source-bound local lanes are immutable 1% (767 cortical cells), Poisson or DC, "
            "and separately versioned scalable 2% (1,539 cells), Poisson only, "
            "plus a distinct 5% condition (3,854 cells), Poisson only, "
            "60,100 ms including 100 ms settling. This is a full-duration condition, not a capacity pilot. "
            "It uses its own wiring, initialization and stimulus streams—not an exported author graph. "
            "BioNet shares NEURON; a completed condition is not statistical or publication acceptance."
        )
        note.setWordWrap(True)
        note.setObjectName("Muted")
        form.addRow(note)
        self.runtime_edit = QLineEdit(overview.bmtk_python_edit.text())
        self.runtime_edit.setReadOnly(True)
        self.runtime_edit.setPlaceholderText("Choose BMTK BioNet Python in Workspace")
        overview.bmtk_python_edit.textChanged.connect(self.runtime_edit.setText)
        form.addRow("BioNet runtime", self.runtime_edit)
        self.name_edit = QLineEdit()
        self.name_edit.setObjectName("BioNetPDCMRunName")
        self.name_edit.setPlaceholderText("Unique full-duration replication condition name")
        form.addRow("Run name", self.name_edit)
        self.scale_combo = QComboBox()
        self.scale_combo.setObjectName("BioNetPDCMScale")
        self.scale_combo.addItem("1% · primary v3 · 767 cells", .01)
        self.scale_combo.addItem("2% · scalable v4 · 1,539 cells", .02)
        self.scale_combo.addItem("5% · separate app contract v1 · 3,854 cells", .05)
        self.scale_combo.setToolTip("Separate frozen implementations and resource guards; larger planned scales are not executable choices.")
        form.addRow("Model scale", self.scale_combo)
        self.drive_combo = QComboBox()
        self.drive_combo.setObjectName("BioNetPDCMDrive")
        self.drive_combo.addItem("Balanced Poisson", "balanced_poisson")
        self.drive_combo.addItem("Balanced DC", "balanced_dc")
        self.drive_combo.setToolTip("DC retains the own graph/initialization/clock and source-defined current; 1% silence is an outcome, not an execution failure.")
        form.addRow("External drive", self.drive_combo)
        self.network_seed = QSpinBox()
        self.network_seed.setRange(0, 2**31 - 1)
        self.network_seed.setValue(28)
        self.network_seed.setToolTip("Own wiring, positions, weights/delays and initialization realization")
        form.addRow("Network realization seed", self.network_seed)
        self.stimulus_seed = QSpinBox()
        self.stimulus_seed.setRange(0, 2**31 - 1)
        self.stimulus_seed.setValue(208)
        self.stimulus_seed.setToolTip("Own external Poisson event stream, separate from the network realization")
        form.addRow("Stimulus seed", self.stimulus_seed)
        self.limits = QLabel(
            "Measured prior full conditions: about 3 minutes, under 1.4 GiB peak RAM. "
            "This lane stops on failure, cancellation or its 2 GiB / 900 s resource guard; "
            "it never retries or submits cluster jobs. Larger scales are not silently enabled."
        )
        self.limits.setWordWrap(True)
        self.limits.setObjectName("Muted")
        form.addRow(self.limits)
        self.scale_combo.currentIndexChanged.connect(self._scale_changed)
        controls = QHBoxLayout()
        self.run_button = QPushButton("Run full BioNet replication condition")
        self.run_button.setProperty("netpynePrimary", True)
        self.run_button.clicked.connect(self.start_run)
        self.stop_button = QPushButton("Stop safely")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_run)
        controls.addWidget(self.run_button)
        controls.addWidget(self.stop_button)
        form.addRow(controls)
        self.status = QLabel("Not started · existing accepted runs are never rerun automatically")
        self.status.setWordWrap(True)
        self.status.setObjectName("Muted")
        form.addRow(self.status)
        layout.addWidget(card)

        saved = Card()
        saved_layout = QVBoxLayout(saved)
        saved_layout.setContentsMargins(18, 18, 18, 18)
        saved_title = QLabel("Continue from saved replication evidence")
        saved_title.setObjectName("SectionTitle")
        saved_layout.addWidget(saved_title)
        saved_note = QLabel(
            "Open a native NetPyNE summary or an independent BioNet run. BioNet creates a new, "
            "small Results sidecar with exact population counts/rates, a filterable spike raster "
            "and links to the original SONATA, source and execution evidence. Original runs remain read-only."
        )
        saved_note.setWordWrap(True)
        saved_note.setObjectName("Muted")
        saved_layout.addWidget(saved_note)
        row = QHBoxLayout()
        self.open_native_button = QPushButton("Open saved NetPyNE result…")
        self.open_native_button.clicked.connect(self.open_native)
        self.open_bionet_button = QPushButton("Open saved BioNet run…")
        self.open_bionet_button.clicked.connect(self.open_bionet)
        row.addWidget(self.open_native_button)
        row.addWidget(self.open_bionet_button)
        saved_layout.addLayout(row)
        layout.addWidget(saved)
        figures = Card()
        figure_layout = QVBoxLayout(figures)
        figure_layout.setContentsMargins(18, 18, 18, 18)
        title = QLabel("Saved paper figures · read-only preview and export")
        title.setObjectName("SectionTitle")
        figure_layout.addWidget(title)
        note = QLabel("Choose a checked native Figure 8 figure_manifest.json, BioNet companion manifest.json, or exported export_receipt.json. This opens saved PDF/PNG bytes without Run or a simulator runtime. Historical context and missing conditions remain explicit.")
        note.setWordWrap(True)
        note.setObjectName("Muted")
        figure_layout.addWidget(note)
        self.figure_manifest_edit = QLineEdit()
        self.figure_manifest_edit.setObjectName("PDCMSavedFigureManifest")
        self.figure_manifest_edit.setPlaceholderText("Existing figure manifest path (paste a path or Browse)")
        figure_layout.addWidget(self.figure_manifest_edit)
        buttons = QHBoxLayout()
        self.browse_figure_button = QPushButton("Browse figure manifest…")
        self.browse_figure_button.clicked.connect(self.browse_figure)
        self.check_figure_button = QPushButton("Check and reopen saved figures")
        self.check_figure_button.clicked.connect(self.check_figure)
        buttons.addWidget(self.browse_figure_button)
        buttons.addWidget(self.check_figure_button)
        figure_layout.addLayout(buttons)
        self.figure_status = QLabel("No figure bundle loaded · simulations and raw runs stay untouched")
        self.figure_status.setWordWrap(True)
        self.figure_status.setObjectName("Muted")
        figure_layout.addWidget(self.figure_status)
        self.figure_file_combo = QComboBox()
        self.figure_file_combo.setObjectName("PDCMSavedFigureFile")
        self.figure_file_combo.currentIndexChanged.connect(self._figure_preview)
        figure_layout.addWidget(self.figure_file_combo)
        self.figure_image = QLabel("Recorded PNG preview appears here")
        self.figure_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.figure_image.setMinimumHeight(100)
        self.figure_image.setMaximumHeight(500)
        self.figure_image.setWordWrap(True)
        figure_layout.addWidget(self.figure_image)
        actions = QHBoxLayout()
        self.open_figure_button = QPushButton("Open checked PDF / PNG")
        self.open_figure_button.clicked.connect(self.open_figure)
        self.export_figure_button = QPushButton("Export checked bundle to new folder…")
        self.export_figure_button.clicked.connect(self.export_figure)
        for button in (self.open_figure_button, self.export_figure_button):
            button.setEnabled(False)
            actions.addWidget(button)
        figure_layout.addLayout(actions)
        layout.addWidget(figures)
        layout.addStretch(1)

    @property
    def active(self):
        return any(worker is not None and worker.isRunning() for worker in (self.worker, self.import_worker, self.figure_worker))

    def browse_figure(self):
        selected, _ = QFileDialog.getOpenFileName(self, "Open checked saved figure manifest", self.overview.output_edit.text(), "Figure manifests (*.json)")
        if selected:
            self.figure_manifest_edit.setText(selected)
            self.check_figure()

    def _start_figure_read(self, source, destination=None):
        if self.active:
            return
        self.figure_worker = SavedFigureWorker(Path(source), destination, self)
        self.figure_worker.succeeded.connect(self._figure_ready)
        self.figure_worker.failed.connect(self._figure_failed)
        self.figure_worker.finished.connect(self._finished)
        self.figure_status.setText("Checking saved figure bytes and consumed analysis sources · no simulation, compilation or raw-run re-audit…")
        self._set_busy(True)
        self.figure_worker.start()

    def check_figure(self):
        source = self.figure_manifest_edit.text().strip()
        if not source:
            QMessageBox.warning(self, "Choose saved figures", "Choose or paste an existing checked figure manifest path.")
            return
        # Clear prior preview before validation so stale evidence is not shown.
        self.figure_bundle = None
        self.figure_file_combo.clear()
        self._figure_preview_text("")
        self.open_figure_button.setEnabled(False)
        self.export_figure_button.setEnabled(False)
        self._start_figure_read(source)

    def _figure_ready(self, info):
        self.figure_bundle = info
        self.figure_manifest_edit.setText(info["manifest_path"])
        self.figure_file_combo.clear()
        self.figure_file_combo.addItems(info["previews"])
        png = next((i for i, name in enumerate(info["previews"]) if name.endswith("_300dpi.png")),
                   next((i for i, name in enumerate(info["previews"]) if name.endswith(".png")), 0))
        self.figure_file_combo.setCurrentIndex(png)
        self._figure_preview()
        self.figure_status.setText(info["title"] + " · " + info["coverage"] + "\n" + info["caveat"])
        self.open_figure_button.setEnabled(True)
        self.export_figure_button.setEnabled(not info["exported"])

    def _figure_preview_text(self, text):
        self.figure_image.clear()
        # Release a previous image's fixed height for empty/error/PDF views.
        self.figure_image.setMinimumHeight(100)
        self.figure_image.setMaximumHeight(500)
        self.figure_image.setText(text)

    def _figure_preview(self):
        if not self.figure_bundle or not self.figure_file_combo.currentText():
            return
        name = self.figure_file_combo.currentText()
        if name.endswith(".png"):
            image = QPixmap(str(Path(self.figure_bundle["root"]) / name))
            if image.isNull():
                self._figure_preview_text("Recorded PNG could not be decoded; no replacement was generated.")
            else:
                preview = image.scaled(850, 480, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                self.figure_image.setPixmap(preview)
                # A QLabel can otherwise shrink below its pixmap and crop the
                # centre of a portrait page when this long panel is scrolled.
                self.figure_image.setFixedHeight(preview.height())
        else:
            self._figure_preview_text("Saved vector PDF · use Open checked PDF / PNG. No raster replacement or new analysis is generated.")

    def open_figure(self):
        if self.active or not self.figure_bundle:
            return
        from digifly_app.core.pdcm_figure_bundle import load_figure_bundle
        try:
            info = load_figure_bundle(self.figure_bundle["manifest_path"])
            name = self.figure_file_combo.currentText()
            if name not in info["previews"]:
                raise ValueError("Selected figure is not in the checked inventory")
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(info["root"]) / name))):
                raise ValueError("The system could not open the checked figure")
        except (OSError, ValueError) as exc:
            self._figure_failed(str(exc))

    def export_figure(self):
        if self.active or not self.figure_bundle:
            return
        default = Path(self.overview.output_edit.text()).expanduser() / ("saved-figure-export-" + datetime.now().strftime("%Y%m%dT%H%M%S"))
        destination, _ = QFileDialog.getSaveFileName(self, "Name a NEW figure export folder (existing paths are rejected)", str(default), "New folder name (*)")
        if destination:
            self._start_figure_read(self.figure_bundle["manifest_path"], Path(destination))

    def _figure_failed(self, message):
        self.figure_bundle = None
        self.figure_file_combo.clear()
        self._figure_preview_text("")
        self.figure_status.setText("Saved figure check/export failed · no simulation, overwrite or acceptance claim")
        self.open_figure_button.setEnabled(False)
        self.export_figure_button.setEnabled(False)
        QMessageBox.warning(self, "Saved figures could not be opened/exported", message)

    def _paths(self):
        python = self.runtime_edit.text().strip()
        output = self.overview.output_edit.text().strip()
        if not python or not Path(python).expanduser().is_file():
            raise ValueError("Choose an existing BMTK BioNet Python in Workspace first.")
        if not output:
            raise ValueError("Choose a Workstation output root first.")
        return Path(output).expanduser(), Path(python).expanduser()

    def _scale_changed(self):
        scale = self.scale_combo.currentData()
        scalable = scale in (.02, .05)
        for seed in (self.network_seed, self.stimulus_seed):
            seed.setEnabled(scale != .05)
        self.drive_combo.model().item(1).setEnabled(not scalable)
        if scale == .05:
            from digifly_app.workers.pdcm_bionet_5pct_app_worker import BOUNDS, MODEL_CONTRACT
            gib = 1024**3
            self.network_seed.setValue(MODEL_CONTRACT["network_seed"])
            self.stimulus_seed.setValue(MODEL_CONTRACT["stimulus_seed"])
            self.drive_combo.setCurrentIndex(0)
            self.limits.setText(
                "Distinct 5% condition: 3,854 cells with full Poisson inputs and clock. "
                f"Stops on failure or its {BOUNDS['rss_bytes'] / gib:g} GiB RAM / "
                f"{BOUNDS['timeout_seconds']:,} s / {BOUNDS['output_bytes'] / gib:g} GiB output guards. "
                f"Requires {(BOUNDS['rss_bytes'] + BOUNDS['memory_reserve_bytes']) / gib:g} GiB available RAM "
                f"and {(BOUNDS['output_bytes'] + BOUNDS['durable_copy_bytes'] + BOUNDS['disk_reserve_bytes']) / gib:g} GiB free disk before launch; "
                f"retains {BOUNDS['memory_reserve_bytes'] / gib:g} GiB RAM and "
                f"{BOUNDS['disk_reserve_bytes'] / gib:g} GiB disk reserves plus "
                f"{BOUNDS['durable_copy_bytes'] / gib:g} GiB for a full durable copy. "
                "Resources are checked again at Run; the forecast is not measured acceptance. No automatic retry or cluster submission."
            )
        elif scale == .02:
            self.drive_combo.setCurrentIndex(0)
            self.limits.setText("Separate 2% v4 condition: full external Poisson inputs and clock retained. Stops on failure or its 10 GiB RAM / 3,600 s / 4 GiB output guards. Requires 22 GiB available RAM and 20 GiB free disk before launch; retains 12 GiB RAM and 16 GiB disk reserves. Forecasts are not measured acceptance. No automatic retry or cluster submission.")
        else:
            self.limits.setText("Measured prior 1% conditions: about 3 minutes, under 1.4 GiB peak RAM. Immutable v3 stops on failure, cancellation or its 2 GiB / 900 s guard; no automatic retries or cluster jobs.")

    def start_run(self):
        if self.active:
            return
        if self.runtime_busy():
            QMessageBox.warning(self, "Scientific run already active", "Let the active app run finish or stop it safely before starting another.")
            return
        from digifly_app.core.pdcm_bionet_app import PDCMBioNetAppSpec
        from digifly_app.core.pdcm_bionet_scalable_app import PDCMBioNetScalableAppSpec
        from digifly_app.core.pdcm_bionet_5pct_app import PDCMBioNet5PctAppSpec
        try:
            output, python = self._paths()
            spec_types = {.01: PDCMBioNetAppSpec, .02: PDCMBioNetScalableAppSpec, .05: PDCMBioNet5PctAppSpec}
            spec_type = spec_types.get(self.scale_combo.currentData())
            if spec_type is None:
                raise ValueError("Choose an exact source-bound BioNet condition.")
            spec = spec_type(
                name=self.name_edit.text().strip(), network_seed=self.network_seed.value(),
                stimulus_seed=self.stimulus_seed.value(),
                drive=str(self.drive_combo.currentData()),
            )
            spec.validate()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Cannot start BioNet replication", str(exc))
            return
        self.worker = BioNetReplicationWorker(spec, output, python, self)
        self.worker.progress.connect(self.status.setText)
        self.worker.succeeded.connect(self._completed)
        self.worker.failed.connect(self._failed)
        self.worker.cancelled.connect(lambda: self.status.setText("Cancelled · partial scientific evidence preserved; no retry"))
        self.worker.finished.connect(self._finished)
        self._set_busy(True)
        self.worker.start()

    def stop_run(self):
        if self.worker is not None:
            self.status.setText("Stopping the owned worker and preserving its partial evidence…")
            self.stop_button.setEnabled(False)
            self.worker.request_cancel()

    def open_native(self):
        selected, _ = QFileDialog.getOpenFileName(
            self, "Open saved PDCM NetPyNE summary", self.overview.output_edit.text(), "JSON summaries (*.json)"
        )
        if selected:
            self.result_ready.emit(selected)

    def open_bionet(self):
        if self.active:
            return
        source = QFileDialog.getExistingDirectory(self, "Choose the completed BioNet run folder", self.overview.output_edit.text())
        if not source:
            return
        try:
            output, python = self._paths()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Cannot open BioNet evidence", str(exc))
            return
        self.import_worker = BioNetSavedResultWorker(Path(source), output, python, self)
        self.import_worker.succeeded.connect(self._completed)
        self.import_worker.failed.connect(self._failed)
        self.import_worker.finished.connect(self._finished)
        self.status.setText("Reading saved BioNet evidence · no simulation or scientific re-acceptance…")
        self._set_busy(True)
        self.import_worker.start()

    def _set_busy(self, busy):
        for widget in (self.run_button, self.open_bionet_button, self.open_native_button,
                       self.name_edit, self.scale_combo, self.drive_combo, self.network_seed, self.stimulus_seed,
                       self.figure_manifest_edit, self.browse_figure_button, self.check_figure_button):
            widget.setEnabled(not busy)
        self.open_figure_button.setEnabled(not busy and self.figure_bundle is not None)
        self.export_figure_button.setEnabled(not busy and self.figure_bundle is not None and not self.figure_bundle["exported"])
        self.stop_button.setEnabled(busy and self.worker is not None)
        if not busy:
            self._scale_changed()
        self.busy_changed.emit(busy)

    def _completed(self, summary):
        self.status.setText(f"Saved Results ready · {Path(summary).parent.name} · scientific acceptance remains separate")
        self.result_ready.emit(summary)

    def _failed(self, message):
        self.status.setText("Stopped on failure · existing and partial outputs retained; no automatic retry")
        QMessageBox.warning(self, "BioNet replication could not complete", message)

    def _finished(self):
        for attribute in ("worker", "import_worker", "figure_worker"):
            worker = getattr(self, attribute)
            if worker is not None and not worker.isRunning():
                worker.deleteLater()
                setattr(self, attribute, None)
        self._set_busy(False)
