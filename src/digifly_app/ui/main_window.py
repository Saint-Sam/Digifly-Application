from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Any

from PySide6.QtCore import QProcess, QProcessEnvironment, QSettings, Qt, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QFont, QPixmap, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from digifly_app import __version__
from digifly_app.core.jobs import JobStore
from digifly_app.core.circuit import CircuitSpec
from digifly_app.core.models import CheckState, ExecutionPlan, PreflightReport, ResultRecord
from digifly_app.core.process_environment import EXTERNAL_PYTHON_ENV_REMOVE
from digifly_app.core.project import DigiflyProject
from digifly_app.core.resources import ResourceSnapshot, capture_resources
from digifly_app.core.results import load_escape_siz_result
from digifly_app.core.workspace import DigiflyWorkspace
from digifly_app.core.paths import resource_path
from digifly_app.core.resource_profile import ResourceKind, load_default_profile
from digifly_app.engines.arbor_escape_siz import (
    ArborAblationComparisonConfig,
    ArborEscapeSizAdapter,
    load_arbor_ablation_result,
)
from digifly_app.engines.neuron_escape_siz import (
    EscapeSizConfig,
    NeuronEscapeSizAdapter,
    latest_gfc2_stimulus,
)
from .style import APP_STYLE
from .circuit_builder import CIRCUIT_BUILDER_WORKFLOW, CircuitBuilderPage
from .widgets import Card, CheckRow, EngineCard, StatusPill, clear_layout, make_label_copyable


ORGANIZATION_NAME = "Digifly"
APPLICATION_NAME = "Digifly Workstation"
LEGACY_APPLICATION_NAME = "Digifly App"


def _workspace_home() -> Path:
    """Keep projects and large simulation artifacts outside the app bundle."""
    return Path.home() / "Digifly Workstation Workspace"


def _scroll_page(content: QWidget) -> QScrollArea:
    content.setObjectName("PageContent")
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setWidget(content)
    return scroll


def _page_header(eyebrow: str, title: str, detail: str) -> QVBoxLayout:
    layout = QVBoxLayout()
    layout.setSpacing(5)
    eyebrow_label = QLabel(eyebrow.upper())
    eyebrow_label.setObjectName("Eyebrow")
    title_label = QLabel(title)
    title_label.setObjectName("PageTitle")
    detail_label = QLabel(detail)
    detail_label.setObjectName("Muted")
    detail_label.setWordWrap(True)
    layout.addWidget(eyebrow_label)
    layout.addWidget(title_label)
    layout.addWidget(detail_label)
    return layout


def _browse_directory(line_edit: QLineEdit, parent: QWidget, title: str) -> None:
    selected = QFileDialog.getExistingDirectory(parent, title, line_edit.text())
    if selected:
        line_edit.setText(selected)


def _path_row(line_edit: QLineEdit, parent: QWidget, title: str, *, file_mode: bool = False) -> QWidget:
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    layout.addWidget(line_edit, 1)
    browse = QPushButton("Browse…")
    if file_mode:
        browse.clicked.connect(lambda: _browse_file(line_edit, parent, title))
    else:
        browse.clicked.connect(lambda: _browse_directory(line_edit, parent, title))
    layout.addWidget(browse)
    return row


def _browse_file(line_edit: QLineEdit, parent: QWidget, title: str) -> None:
    selected, _ = QFileDialog.getOpenFileName(parent, title, line_edit.text())
    if selected:
        line_edit.setText(selected)


class OverviewPage(QWidget):
    settings_changed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(30, 26, 30, 32)
        layout.setSpacing(20)
        layout.addLayout(
            _page_header(
                "Workspace",
                "Connect the Digifly ecosystem",
                "Link native source/data trees and isolated runtimes. Nothing is copied into this app repository.",
            )
        )

        workspace_card = Card()
        form = QFormLayout(workspace_card)
        form.setContentsMargins(18, 18, 18, 18)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(12)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.workspace_edit = QLineEdit(str(Path.home() / "Desktop" / "Digifly Public"))
        self.output_edit = QLineEdit(str(_workspace_home() / "runs"))
        self.python_edit = QLineEdit("/opt/anaconda3/bin/python")
        form.addRow("Digifly Public root", _path_row(self.workspace_edit, self, "Choose Digifly Public"))
        form.addRow("Workstation output root", _path_row(self.output_edit, self, "Choose output root"))
        form.addRow("NEURON Python", _path_row(self.python_edit, self, "Choose NEURON Python", file_mode=True))
        controls = QWidget()
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(0, 5, 0, 0)
        self.doctor_button = QPushButton("Run workspace doctor")
        self.doctor_button.setProperty("primary", True)
        self.doctor_button.clicked.connect(self.refresh)
        controls_layout.addWidget(self.doctor_button)
        self.doctor_summary = QLabel("Not checked yet")
        self.doctor_summary.setObjectName("Muted")
        controls_layout.addWidget(self.doctor_summary, 1)
        form.addRow("", controls)
        layout.addWidget(workspace_card)

        resource_row = QHBoxLayout()
        resource_row.setSpacing(12)
        self.resource_labels: dict[str, QLabel] = {}
        for key, title in (
            ("cpu", "CPU"),
            ("memory", "Available memory"),
            ("disk", "Free disk"),
            ("workers", "NEURON default"),
        ):
            card = Card()
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(15, 13, 15, 13)
            label = QLabel(title.upper())
            label.setObjectName("Eyebrow")
            value = QLabel("—")
            value.setStyleSheet("font-size:20px; font-weight:700; color:#eef4ff;")
            card_layout.addWidget(label)
            card_layout.addWidget(value)
            resource_row.addWidget(card, 1)
            self.resource_labels[key] = value
        layout.addLayout(resource_row)

        section = QHBoxLayout()
        title = QLabel("Engine readiness")
        title.setObjectName("SectionTitle")
        section.addWidget(title)
        section.addStretch()
        self.engine_hint = QLabel("Run the doctor to inspect all lanes")
        self.engine_hint.setObjectName("Muted")
        section.addWidget(self.engine_hint)
        layout.addLayout(section)
        self.engine_grid = QGridLayout()
        self.engine_grid.setSpacing(12)
        layout.addLayout(self.engine_grid)
        layout.addStretch(1)
        root_layout.addWidget(_scroll_page(content))

        for editor in (self.workspace_edit, self.output_edit, self.python_edit):
            editor.textChanged.connect(self.settings_changed)

    def workspace(self) -> DigiflyWorkspace:
        return DigiflyWorkspace(self.workspace_edit.text())

    def refresh(self) -> None:
        self.doctor_button.setEnabled(False)
        self.doctor_summary.setText("Inspecting source trees and runtimes…")
        QApplication.processEvents()
        workspace = self.workspace()
        base = workspace.base_preflight()
        try:
            probes = workspace.probe_engines(self.python_edit.text())
            resources = capture_resources(self.output_edit.text())
        except Exception as exc:  # GUI boundary: show diagnostic rather than crash.
            self.doctor_summary.setText(f"Doctor failed: {exc}")
            self.doctor_button.setEnabled(True)
            return
        self._set_resources(resources)
        clear_layout(self.engine_grid)  # type: ignore[arg-type]
        for index, probe in enumerate(probes):
            self.engine_grid.addWidget(EngineCard(probe), index // 2, index % 2)
        passing = sum(
            1
            for probe in probes
            if probe.source_state == CheckState.PASS and probe.runtime_state == CheckState.PASS
        )
        if base.ok:
            self.doctor_summary.setText(f"Workspace linked · {passing}/{len(probes)} engines fully available")
        else:
            self.doctor_summary.setText("Workspace markers are incomplete; review the configured root.")
        self.engine_hint.setText(f"Checked {len(probes)} integration lanes")
        self.doctor_button.setEnabled(True)

    def _set_resources(self, resources: ResourceSnapshot) -> None:
        self.resource_labels["cpu"].setText(f"{resources.logical_cores} cores")
        memory = f"{resources.available_memory_gb:.1f} GB" if resources.available_memory_gb is not None else "unknown"
        self.resource_labels["memory"].setText(memory)
        self.resource_labels["disk"].setText(f"{resources.disk_free_gb:.1f} GB")
        self.resource_labels["workers"].setText(f"{resources.neuron_worker_default} max")


class ExperimentPage(QWidget):
    result_ready = Signal(object)
    status_message = Signal(str)

    def __init__(self, overview: OverviewPage, parent: QWidget | None = None):
        super().__init__(parent)
        self.overview = overview
        self._report: PreflightReport | None = None
        self._plan: ExecutionPlan | None = None
        self._process: QProcess | None = None
        self._job_dir: Path | None = None
        self._job_store: JobStore | None = None
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(30, 26, 30, 32)
        layout.setSpacing(18)
        layout.addLayout(
            _page_header(
                "Experiment · 01",
                "Escape-SIZ guided run",
                "Run the exact NEURON reference or the app-owned Arbor HeteroRectGap equation port from one guided recipe.",
            )
        )

        banner = Card()
        banner_layout = QHBoxLayout(banner)
        banner_layout.setContentsMargins(16, 13, 16, 13)
        banner_layout.addWidget(StatusPill(CheckState.INFO, "MILESTONE 1"))
        copy = QLabel(
            "The Ablation notebook preset reproduces its current active GFC recipe. "
            "It uses one worker instead of the notebook's four-worker setting to avoid observed MPI segfaults; "
            "the scientific model is unchanged. Latest GFC2 and canonical dual-GF remain separate experiments."
        )
        copy.setWordWrap(True)
        copy.setObjectName("Muted")
        banner_layout.addWidget(copy, 1)
        layout.addWidget(banner)

        main_grid = QGridLayout()
        main_grid.setHorizontalSpacing(14)
        main_grid.setVerticalSpacing(14)
        config_card = Card()
        config_layout = QVBoxLayout(config_card)
        config_layout.setContentsMargins(18, 17, 18, 18)
        config_layout.setSpacing(14)
        title_row = QHBoxLayout()
        title = QLabel("Recipe controls")
        title.setObjectName("SectionTitle")
        title_row.addWidget(title)
        title_row.addStretch()
        title_row.addWidget(StatusPill(CheckState.WARNING, "CACHE-AWARE"))
        config_layout.addLayout(title_row)

        form = QFormLayout()
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(10)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.backend_combo = QComboBox()
        self.backend_combo.addItem("NEURON · exact notebook reference", "neuron")
        self.backend_combo.addItem("Arbor · fast custom-gap comparison", "arbor")
        self.backend_combo.setToolTip(
            "NEURON uses the original HeteroRectGap mechanism. Arbor uses the app-owned equation port with the "
            "same parameters; measured cross-backend agreement is reported by the equivalence audit."
        )
        self.preset_combo = QComboBox()
        self.preset_combo.addItem("Ablation notebook · active GFC experiment", "ablation_notebook_active")
        self.preset_combo.addItem("Latest GFC2 pairwise diagnostic", "latest_gfc2_pairwise")
        self.preset_combo.addItem("Canonical dual-GF comparison", "canonical_dual_gf")
        self.gj_combo = QComboBox()
        self.gj_combo.addItem("Heterotypic rectifying", "heterotypic_rectifying")
        self.gj_combo.addItem("Ohmic (legacy)", "ohmic")
        self.na_multiplier = _double_spin(0.01, 1000.0, 2.5, decimals=4)
        self.frequency = _double_spin(0.1, 10000.0, 100.0, suffix=" Hz")
        self.pulses = QSpinBox()
        self.pulses.setRange(1, 100000)
        self.pulses.setValue(10)
        self.nproc = QSpinBox()
        self.nproc.setRange(1, 64)
        self.nproc.setValue(1)
        self.amp_enabled = _double_spin(0, 1000, 1.0, suffix=" nA", decimals=8)
        self.amp_disabled = _double_spin(0, 1000, 0.46142578125, suffix=" nA", decimals=8)
        form.addRow("Execution engine", self.backend_combo)
        form.addRow("Versioned preset", self.preset_combo)
        form.addRow("Gap-junction model  · rebuild", self.gj_combo)
        form.addRow("Contact-site Na  · runtime", self.na_multiplier)
        form.addRow("Frequency  · runtime", self.frequency)
        form.addRow("Maximum pulses  · runtime", self.pulses)
        form.addRow("Gap-enabled fallback", self.amp_enabled)
        form.addRow("Gap-disabled fallback", self.amp_disabled)
        self.parallelism_label = QLabel("NEURON workers")
        form.addRow(self.parallelism_label, self.nproc)
        config_layout.addLayout(form)

        self.separate_gfs = QCheckBox("Remove direct GF↔GF chemical edges  · requires rebuild")
        self.separate_gfs.setChecked(True)
        self.gfc2_ohmic = QCheckBox("Add 55 pairwise GFC2 AIS ohmic junctions  · requires rebuild")
        self.gfc2_ohmic.setChecked(True)
        self.extra_heatmaps = QCheckBox("Generate extra target heatmaps  · analysis-only")
        self.postsynaptic_3d = QCheckBox("Generate Ablation notebook heatmap + postsynaptic 3D figure  · analysis-only")
        config_layout.addWidget(self.separate_gfs)
        config_layout.addWidget(self.gfc2_ohmic)
        config_layout.addWidget(self.extra_heatmaps)
        config_layout.addWidget(self.postsynaptic_3d)

        target_label = QLabel("Per-condition stimulus map (JSON)")
        target_label.setStyleSheet("font-weight:600;")
        config_layout.addWidget(target_label)
        self.stimulus_json = QPlainTextEdit()
        self.stimulus_json.setMinimumHeight(145)
        self.stimulus_json.setPlainText(json.dumps(latest_gfc2_stimulus(), indent=2))
        self.stimulus_json.setToolTip("Map gap_enabled and gap_disabled to neuron-ID → nA values.")
        config_layout.addWidget(self.stimulus_json)
        main_grid.addWidget(config_card, 0, 0)

        advanced_card = Card()
        advanced_layout = QVBoxLayout(advanced_card)
        advanced_layout.setContentsMargins(18, 17, 18, 18)
        advanced_layout.setSpacing(14)
        advanced_title = QLabel("Mechanism & safety")
        advanced_title.setObjectName("SectionTitle")
        advanced_layout.addWidget(advanced_title)
        advanced_form = QFormLayout()
        advanced_form.setHorizontalSpacing(18)
        advanced_form.setVerticalSpacing(10)
        self.closed_frac = _double_spin(0, 1, 0.0, decimals=4)
        self.vhalf = _double_spin(-200, 200, 0.0, suffix=" mV", decimals=3)
        self.vslope = _double_spin(0.001, 200, 5.0, suffix=" mV", decimals=3)
        self.residual = _double_spin(0, 1, 0.20, decimals=4)
        self.tau_open = _double_spin(0.001, 1000, 6.0, suffix=" ms", decimals=3)
        self.tau_close = _double_spin(0.001, 1000, 2.0, suffix=" ms", decimals=3)
        self.vmin = _double_spin(-200, 200, -80.0, suffix=" mV", decimals=1)
        self.vmax = _double_spin(-200, 300, 40.0, suffix=" mV", decimals=1)
        for label, control in (
            ("Closed conductance fraction", self.closed_frac),
            ("V½", self.vhalf),
            ("Voltage slope", self.vslope),
            ("Empirical residual", self.residual),
            ("Opening time", self.tau_open),
            ("Closing time", self.tau_close),
            ("Heatmap minimum", self.vmin),
            ("Heatmap maximum", self.vmax),
        ):
            advanced_form.addRow(label, control)
        advanced_layout.addLayout(advanced_form)

        safety_note = Card(inset=True)
        safety_layout = QVBoxLayout(safety_note)
        safety_layout.setContentsMargins(12, 11, 12, 11)
        note_title = QLabel("Why execution is locked by default")
        note_title.setStyleSheet("font-weight:650; color:#f0c76d;")
        note = QLabel(
            "The app-owned worker redirects cache, request, run, status, and plot writes into the app output root. "
            "A first run remains locked until you explicitly permit the expensive cache build."
        )
        note.setObjectName("Muted")
        note.setWordWrap(True)
        safety_layout.addWidget(note_title)
        safety_layout.addWidget(note)
        advanced_layout.addWidget(safety_note)
        self.allow_cache_build = QCheckBox("Permit a new cache build if no compatible cache exists")
        self.ack_legacy_writes = QCheckBox("Legacy source-tree writes are disabled by the app-owned worker")
        self.ack_legacy_writes.setChecked(True)
        self.ack_legacy_writes.setEnabled(False)
        self.force_restart = QCheckBox("Force cache service restart (advanced)")
        advanced_layout.addWidget(self.allow_cache_build)
        advanced_layout.addWidget(self.ack_legacy_writes)
        advanced_layout.addWidget(self.force_restart)
        advanced_layout.addStretch()
        main_grid.addWidget(advanced_card, 0, 1)
        main_grid.setColumnStretch(0, 3)
        main_grid.setColumnStretch(1, 2)
        layout.addLayout(main_grid)

        action_row = QHBoxLayout()
        self.validate_button = QPushButton("Validate & preview plan")
        self.validate_button.setProperty("primary", True)
        self.validate_button.clicked.connect(self.validate_and_plan)
        self.run_button = QPushButton("Run experiment")
        self.run_button.setEnabled(False)
        self.run_button.clicked.connect(self.run_experiment)
        self.cancel_button = QPushButton("Stop safely")
        self.cancel_button.setProperty("danger", True)
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_run)
        action_row.addWidget(self.validate_button)
        action_row.addWidget(self.run_button)
        action_row.addWidget(self.cancel_button)
        action_row.addStretch()
        self.plan_state = QLabel("Plan not validated")
        self.plan_state.setObjectName("Muted")
        action_row.addWidget(self.plan_state)
        layout.addLayout(action_row)

        review_grid = QGridLayout()
        review_grid.setSpacing(14)
        checks_card = Card()
        checks_layout = QVBoxLayout(checks_card)
        checks_layout.setContentsMargins(16, 15, 16, 16)
        checks_title = QLabel("Preflight")
        checks_title.setObjectName("SectionTitle")
        checks_layout.addWidget(checks_title)
        self.checks_container = QVBoxLayout()
        self.checks_container.setSpacing(8)
        placeholder = QLabel("Validate the recipe to see source, runtime, cache, contact-policy, and storage gates.")
        placeholder.setObjectName("Muted")
        placeholder.setWordWrap(True)
        self.checks_container.addWidget(placeholder)
        checks_layout.addLayout(self.checks_container)
        checks_layout.addStretch()
        review_grid.addWidget(checks_card, 0, 0)

        command_card = Card()
        command_layout = QVBoxLayout(command_card)
        command_layout.setContentsMargins(16, 15, 16, 16)
        command_title = QLabel("Resolved command")
        command_title.setObjectName("SectionTitle")
        command_layout.addWidget(command_title)
        self.command_preview = QPlainTextEdit()
        self.command_preview.setReadOnly(True)
        self.command_preview.setPlaceholderText("The exact argument array will appear here. No shell interpolation is used.")
        self.command_preview.setMinimumHeight(210)
        self.command_preview.setStyleSheet("font-family: 'SFMono-Regular', Menlo, monospace; font-size:11px;")
        command_layout.addWidget(self.command_preview)
        review_grid.addWidget(command_card, 0, 1)
        review_grid.setColumnStretch(0, 3)
        review_grid.setColumnStretch(1, 2)
        layout.addLayout(review_grid)

        log_card = Card()
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(16, 15, 16, 16)
        log_header = QHBoxLayout()
        log_title = QLabel("Run log")
        log_title.setObjectName("SectionTitle")
        log_header.addWidget(log_title)
        log_header.addStretch()
        self.log_state = StatusPill(CheckState.INFO, "IDLE")
        log_header.addWidget(self.log_state)
        log_layout.addLayout(log_header)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        log_layout.addWidget(self.progress)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        self.log.setMinimumHeight(190)
        self.log.setStyleSheet("font-family: 'SFMono-Regular', Menlo, monospace; font-size:11px;")
        log_layout.addWidget(self.log)
        layout.addWidget(log_card)
        layout.addStretch(1)
        root_layout.addWidget(_scroll_page(content))

        self.preset_combo.currentIndexChanged.connect(self._apply_preset)
        self.backend_combo.currentIndexChanged.connect(self._backend_changed)
        self.overview.settings_changed.connect(self._invalidate_plan)
        for signal_owner in (
            self.gj_combo,
            self.na_multiplier,
            self.frequency,
            self.pulses,
            self.nproc,
            self.amp_enabled,
            self.amp_disabled,
            self.separate_gfs,
            self.gfc2_ohmic,
            self.extra_heatmaps,
            self.postsynaptic_3d,
            self.closed_frac,
            self.vhalf,
            self.vslope,
            self.residual,
            self.tau_open,
            self.tau_close,
            self.vmin,
            self.vmax,
            self.allow_cache_build,
            self.ack_legacy_writes,
            self.force_restart,
            self.stimulus_json,
        ):
            _connect_change(signal_owner, self._invalidate_plan)
        self.set_config(EscapeSizConfig.ablation_notebook_active())

    def config(self) -> EscapeSizConfig:
        raw = self.stimulus_json.toPlainText().strip()
        stimulus = json.loads(raw) if raw else {}
        if not isinstance(stimulus, dict):
            raise ValueError("The stimulus map must be a JSON object.")
        return EscapeSizConfig(
            preset=str(self.preset_combo.currentData()),
            python_executable=self.overview.python_edit.text().strip(),
            contact_site_na_multiplier=self.na_multiplier.value(),
            gj_model=str(self.gj_combo.currentData()),
            hetero_g_closed_frac=self.closed_frac.value(),
            hetero_vhalf_mV=self.vhalf.value(),
            hetero_vslope_mV=self.vslope.value(),
            hetero_empirical_residual_frac=self.residual.value(),
            hetero_tau_open_ms=self.tau_open.value(),
            hetero_tau_close_ms=self.tau_close.value(),
            separate_gfs=self.separate_gfs.isChecked(),
            gfc2_ohmic=self.gfc2_ohmic.isChecked(),
            frequency_hz=self.frequency.value(),
            max_pulses=self.pulses.value(),
            gap_enabled_amp_nA=self.amp_enabled.value(),
            gap_disabled_amp_nA=self.amp_disabled.value(),
            stimulus_by_condition=stimulus,
            nproc=self.nproc.value(),
            force_restart_cache=self.force_restart.isChecked(),
            vmin_mV=self.vmin.value(),
            vmax_mV=self.vmax.value(),
            include_extra_target_heatmaps=self.extra_heatmaps.isChecked(),
            postsynaptic_only_3d_plots=self.postsynaptic_3d.isChecked(),
        )

    def arbor_config(self) -> ArborAblationComparisonConfig:
        neuron_config = self.config()
        amplitudes = {
            float(value)
            for condition in neuron_config.stimulus_by_condition.values()
            if isinstance(condition, dict)
            for value in condition.values()
        }
        if amplitudes != {0.9}:
            raise ValueError(
                "The Arbor comparison is locked to all 11 GFC2 cells at 0.9 nA in both conditions."
            )
        return ArborAblationComparisonConfig(
            python_executable=self.overview.python_edit.text().strip(),
            contact_site_na_multiplier=self.na_multiplier.value(),
            static_reverse_fraction=self.residual.value(),
            requested_hetero_g_closed_frac=self.closed_frac.value(),
            requested_hetero_vhalf_mV=self.vhalf.value(),
            requested_hetero_vslope_mV=self.vslope.value(),
            requested_empirical_residual_frac=self.residual.value(),
            requested_tau_open_ms=self.tau_open.value(),
            requested_tau_close_ms=self.tau_close.value(),
            frequency_hz=self.frequency.value(),
            max_pulses=self.pulses.value(),
            stimulus_amp_nA=0.9,
            threads=self.nproc.value(),
            vmin_mV=self.vmin.value(),
            vmax_mV=self.vmax.value(),
        )

    def active_config(self) -> EscapeSizConfig | ArborAblationComparisonConfig:
        return self.arbor_config() if self.backend_combo.currentData() == "arbor" else self.config()

    def set_config(self, config: EscapeSizConfig) -> None:
        index = self.preset_combo.findData(config.preset)
        if index >= 0:
            self.preset_combo.blockSignals(True)
            self.preset_combo.setCurrentIndex(index)
            self.preset_combo.blockSignals(False)
        is_ablation_notebook = config.preset == "ablation_notebook_active"
        if is_ablation_notebook:
            self.na_multiplier.setRange(2.0, 3.0)
        else:
            self.na_multiplier.setRange(0.01, 1000.0)
        self.na_multiplier.setToolTip(
            "Pinned to the notebook's 2–3× ChAT-informed prior."
            if is_ablation_notebook
            else "Contact-site sodium conductance multiplier."
        )
        self.gj_combo.setCurrentIndex(max(0, self.gj_combo.findData(config.gj_model)))
        self.na_multiplier.setValue(config.contact_site_na_multiplier)
        self.frequency.setValue(config.frequency_hz)
        self.pulses.setValue(config.max_pulses)
        self.nproc.setValue(config.nproc)
        self.amp_enabled.setValue(config.gap_enabled_amp_nA)
        self.amp_disabled.setValue(config.gap_disabled_amp_nA)
        self.separate_gfs.setChecked(config.separate_gfs)
        self.gfc2_ohmic.setChecked(config.gfc2_ohmic)
        self.extra_heatmaps.setChecked(config.include_extra_target_heatmaps)
        self.postsynaptic_3d.setChecked(config.postsynaptic_only_3d_plots)
        self.closed_frac.setValue(config.hetero_g_closed_frac)
        self.vhalf.setValue(config.hetero_vhalf_mV)
        self.vslope.setValue(config.hetero_vslope_mV)
        self.residual.setValue(config.hetero_empirical_residual_frac)
        self.tau_open.setValue(config.hetero_tau_open_ms)
        self.tau_close.setValue(config.hetero_tau_close_ms)
        self.vmin.setValue(config.vmin_mV)
        self.vmax.setValue(config.vmax_mV)
        self.force_restart.setChecked(config.force_restart_cache)
        self.stimulus_json.setPlainText(json.dumps(config.stimulus_by_condition, indent=2))
        self._update_parallelism_control()
        self._invalidate_plan()

    def validate_and_plan(self) -> None:
        self.validate_button.setEnabled(False)
        self.plan_state.setText("Running scientific preflight…")
        QApplication.processEvents()
        try:
            config = self.active_config()
            adapter = (
                ArborEscapeSizAdapter(self.overview.workspace())
                if self.backend_combo.currentData() == "arbor"
                else NeuronEscapeSizAdapter(self.overview.workspace())
            )
            report = adapter.validate(
                config,
                output_root=self.overview.output_edit.text(),
                allow_new_cache_build=self.allow_cache_build.isChecked(),
                legacy_write_acknowledged=self.ack_legacy_writes.isChecked(),
            )
            plan = adapter.plan(config, output_root=self.overview.output_edit.text())
        except Exception as exc:
            self._report = None
            self._plan = None
            self.run_button.setEnabled(False)
            self.plan_state.setText(f"Validation error: {exc}")
            self.command_preview.setPlainText("")
            clear_layout(self.checks_container)
            self.checks_container.addWidget(
                CheckRow(
                    _ad_hoc_check(CheckState.FAIL, "Could not create plan", str(exc), blocking=True)
                )
            )
            self.validate_button.setEnabled(True)
            return
        self._report = report
        self._plan = plan
        clear_layout(self.checks_container)
        for check in report.checks:
            self.checks_container.addWidget(CheckRow(check))
        self.command_preview.setPlainText(
            f"Working directory:\n{plan.working_directory}\n\n"
            f"Output behavior:\n{plan.output_behavior}\n\n"
            f"Command:\n{plan.display_command}\n\n"
            "Environment overrides:\n"
            + "\n".join(f"{key}={value}" for key, value in plan.environment.items())
        )
        self.run_button.setEnabled(report.ok and self._process is None)
        self.plan_state.setText(
            "Ready to run" if report.ok else f"{len(report.failures)} blocking check(s) remain"
        )
        self.validate_button.setEnabled(True)
        self.status_message.emit(self.plan_state.text())

    def run_experiment(self) -> None:
        if self._report is None or self._plan is None or not self._report.ok:
            QMessageBox.warning(self, "Preflight required", "Validate a launch-ready plan first.")
            return
        config = self.active_config()
        output_root = Path(self.overview.output_edit.text()).expanduser().resolve()
        (output_root / "_runtime" / "matplotlib").mkdir(parents=True, exist_ok=True)
        store = JobStore(output_root)
        job_dir = store.create(
            self._plan,
            self._report,
            {
                "recipe": config.to_dict(),
                "digifly_public_root": str(self.overview.workspace().root),
                "output_root": str(output_root),
            },
        )
        process = QProcess(self)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        environment = QProcessEnvironment.systemEnvironment()
        for key in tuple(environment.keys()):
            if (
                key in EXTERNAL_PYTHON_ENV_REMOVE
                or key.startswith("DYLD_")
                or key.startswith("CONDA_")
            ):
                environment.remove(key)
        for key, value in self._plan.environment.items():
            environment.insert(key, value)
        process.setProcessEnvironment(environment)
        process.setWorkingDirectory(self._plan.working_directory)
        process.readyReadStandardOutput.connect(self._read_process_output)
        process.finished.connect(self._process_finished)
        process.errorOccurred.connect(self._process_error)
        self._process = process
        self._job_dir = job_dir
        self._job_store = store
        store.update_status(job_dir, "running", pid=None)
        store.append_event(job_dir, "running", "Scientific worker started.")
        self.log.clear()
        self.log.appendPlainText(f"Job provenance: {job_dir}\n")
        self.log.appendPlainText(f"Starting: {self._plan.display_command}\n")
        self.log_state.set_state(CheckState.INFO, text="RUNNING")
        self.progress.setRange(0, 0)
        self.run_button.setEnabled(False)
        self.validate_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        process.start(self._plan.program, list(self._plan.arguments))
        if not process.waitForStarted(5000):
            self._process_error(process.error())
        else:
            store.update_status(job_dir, "running", pid=int(process.processId()))
        self.status_message.emit(f"Escape-SIZ {self._plan.engine} worker running · PID {process.processId()}")

    def cancel_run(self) -> None:
        if self._process is None:
            return
        choice = QMessageBox.question(
            self,
            "Stop the current run?",
            "Digifly Workstation will request a graceful process termination and preserve reusable cache files. "
            "It will not delete partial outputs.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if choice != QMessageBox.StandardButton.Yes:
            return
        if self._job_store and self._job_dir:
            self._job_store.append_event(self._job_dir, "cancel_requested", "User requested graceful termination.")
            self._job_store.update_status(self._job_dir, "cancelling")
        self.log.appendPlainText("\nCancellation requested; preserving cache and partial artifacts…")
        self._process.terminate()
        self.cancel_button.setEnabled(False)

    def _read_process_output(self) -> None:
        if self._process is None:
            return
        raw = bytes(self._process.readAllStandardOutput())
        text = raw.decode("utf-8", errors="replace")
        if text:
            self.log.moveCursor(QTextCursor.MoveOperation.End)
            self.log.insertPlainText(text)
            if self._job_dir:
                with (self._job_dir / "stdout.log").open("a", encoding="utf-8") as handle:
                    handle.write(text)

    def _process_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        crashed = exit_status == QProcess.ExitStatus.CrashExit
        state = "failed" if crashed or exit_code != 0 else "completed"
        if self._job_store and self._job_dir:
            self._job_store.update_status(
                self._job_dir,
                state,
                exit_code=exit_code,
                exit_status=exit_status.name,
            )
            self._job_store.append_event(
                self._job_dir,
                state,
                f"Scientific worker exited with code {exit_code}.",
            )
        self.progress.setRange(0, 100)
        self.progress.setValue(100 if state == "completed" else 0)
        self.log_state.set_state(
            CheckState.PASS if state == "completed" else CheckState.FAIL,
            text=state.upper(),
        )
        self.log.appendPlainText(f"\nWorker {state} with exit code {exit_code}.")
        self.status_message.emit(f"Escape-SIZ run {state}")
        expected = Path(self._plan.expected_summary_path) if self._plan and self._plan.expected_summary_path else None
        if state == "completed" and expected and expected.is_file():
            try:
                result = (
                    load_arbor_ablation_result(expected)
                    if self._plan and self._plan.engine == "arbor"
                    else load_escape_siz_result(expected)
                )
            except Exception as exc:
                self.log.appendPlainText(f"Result inspection failed: {exc}")
            else:
                self.result_ready.emit(result)
        self._process = None
        self.cancel_button.setEnabled(False)
        self.validate_button.setEnabled(True)
        self.run_button.setEnabled(bool(self._report and self._report.ok))

    def _process_error(self, error: QProcess.ProcessError) -> None:
        message = self._process.errorString() if self._process else str(error)
        self.log.appendPlainText(f"\nProcess error: {message}")
        self.log_state.set_state(CheckState.FAIL, text="ERROR")
        if self._job_store and self._job_dir:
            self._job_store.update_status(self._job_dir, "failed", error=message)
            self._job_store.append_event(self._job_dir, "error", message)
        self.status_message.emit(f"Worker error: {message}")

    def _apply_preset(self) -> None:
        if self.preset_combo.currentData() == "ablation_notebook_active":
            self.set_config(EscapeSizConfig.ablation_notebook_active())
        elif self.preset_combo.currentData() == "canonical_dual_gf":
            self.set_config(EscapeSizConfig.canonical_dual_gf())
        else:
            self.set_config(EscapeSizConfig())

    def _backend_changed(self) -> None:
        is_arbor = self.backend_combo.currentData() == "arbor"
        if is_arbor and self.preset_combo.currentData() != "ablation_notebook_active":
            self.set_config(EscapeSizConfig.ablation_notebook_active())
        self.preset_combo.setEnabled(not is_arbor)
        self.gj_combo.setEnabled(not is_arbor)
        self.separate_gfs.setEnabled(not is_arbor)
        self.gfc2_ohmic.setEnabled(not is_arbor)
        self.allow_cache_build.setEnabled(not is_arbor)
        self.force_restart.setEnabled(not is_arbor)
        if is_arbor:
            self.nproc.setValue(4)
            self.run_button.setText("Run Arbor custom-gap comparison")
        else:
            if self.preset_combo.currentData() == "ablation_notebook_active":
                self.nproc.setValue(1)
            self.run_button.setText("Run exact NEURON reference")
        self._update_parallelism_control()
        self._invalidate_plan()
        if is_arbor:
            self.plan_state.setText("Arbor custom HeteroRectGap · validate before comparison")

    def _update_parallelism_control(self) -> None:
        if self.backend_combo.currentData() == "arbor":
            label = "Arbor CPU threads"
            tooltip = "Number of CPU threads allocated to Arbor's vectorized simulation (1–64)."
            enabled = True
        else:
            label = "NEURON workers"
            is_ablation_notebook = self.preset_combo.currentData() == "ablation_notebook_active"
            tooltip = (
                "Pinned to one worker because historical multi-rank launches segfaulted; "
                "scientific parameters are unchanged."
                if is_ablation_notebook
                else "Number of external NEURON workers."
            )
            enabled = not is_ablation_notebook
        self.parallelism_label.setText(label)
        self.parallelism_label.setToolTip(tooltip)
        self.nproc.setToolTip(tooltip)
        self.nproc.setEnabled(enabled)

    def _invalidate_plan(self) -> None:
        if self._process is not None:
            return
        self._report = None
        self._plan = None
        self.run_button.setEnabled(False)
        self.plan_state.setText("Controls changed · revalidate")


class ResultsPage(QWidget):
    status_message = Signal(str)

    def __init__(self, overview: OverviewPage, experiment: ExperimentPage, parent: QWidget | None = None):
        super().__init__(parent)
        self.overview = overview
        self.experiment = experiment
        self._pixmap: QPixmap | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(30, 26, 30, 32)
        layout.setSpacing(18)
        layout.addLayout(
            _page_header(
                "Results",
                "Review scientific evidence",
                "Completed summaries are opened read-only and checked against the Escape-SIZ contact and plotting contract.",
            )
        )
        action_row = QHBoxLayout()
        latest = QPushButton("Load latest matching result")
        latest.setProperty("primary", True)
        latest.clicked.connect(self.load_latest)
        open_button = QPushButton("Open summary JSON…")
        open_button.clicked.connect(self.open_summary)
        action_row.addWidget(latest)
        action_row.addWidget(open_button)
        action_row.addStretch()
        self.result_status = QLabel("No result loaded")
        self.result_status.setObjectName("Muted")
        action_row.addWidget(self.result_status)
        layout.addLayout(action_row)

        summary_grid = QGridLayout()
        summary_grid.setSpacing(14)
        metadata_card = Card()
        metadata_layout = QVBoxLayout(metadata_card)
        metadata_layout.setContentsMargins(16, 15, 16, 16)
        metadata_title = QLabel("Run metadata")
        metadata_title.setObjectName("SectionTitle")
        metadata_layout.addWidget(metadata_title)
        self.metadata = QTableWidget(0, 2)
        self.metadata.setHorizontalHeaderLabels(("Field", "Value"))
        self.metadata.horizontalHeader().setStretchLastSection(True)
        self.metadata.verticalHeader().hide()
        self.metadata.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.metadata.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.metadata.setMinimumHeight(260)
        metadata_layout.addWidget(self.metadata)
        summary_grid.addWidget(metadata_card, 0, 0)

        checks_card = Card()
        checks_layout = QVBoxLayout(checks_card)
        checks_layout.setContentsMargins(16, 15, 16, 16)
        checks_title = QLabel("Interpretation gates")
        checks_title.setObjectName("SectionTitle")
        checks_layout.addWidget(checks_title)
        self.result_checks = QVBoxLayout()
        self.result_checks.addWidget(_muted_label("Load a summary to validate its evidence contract."))
        checks_layout.addLayout(self.result_checks)
        checks_layout.addStretch()
        summary_grid.addWidget(checks_card, 0, 1)
        summary_grid.setColumnStretch(0, 3)
        summary_grid.setColumnStretch(1, 2)
        layout.addLayout(summary_grid)

        artifact_card = Card()
        artifact_layout = QVBoxLayout(artifact_card)
        artifact_layout.setContentsMargins(16, 15, 16, 16)
        artifact_title = QLabel("Artifacts")
        artifact_title.setObjectName("SectionTitle")
        artifact_layout.addWidget(artifact_title)
        self.artifact_rows = QVBoxLayout()
        self.artifact_rows.addWidget(_muted_label("No artifacts loaded."))
        artifact_layout.addLayout(self.artifact_rows)
        layout.addWidget(artifact_card)

        preview_card = Card()
        preview_layout = QVBoxLayout(preview_card)
        preview_layout.setContentsMargins(16, 15, 16, 16)
        preview_title = QLabel("Primary figure preview")
        preview_title.setObjectName("SectionTitle")
        preview_layout.addWidget(preview_title)
        image_scroll = QScrollArea()
        image_scroll.setWidgetResizable(True)
        image_scroll.setMinimumHeight(520)
        image_scroll.setStyleSheet("background:#080d19; border:1px solid #23314a; border-radius:8px;")
        self.image_label = QLabel("Load a completed run to preview its PNG.")
        self.image_label.setObjectName("Muted")
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setMinimumSize(300, 480)
        image_scroll.setWidget(self.image_label)
        preview_layout.addWidget(image_scroll)
        layout.addWidget(preview_card)
        layout.addStretch(1)
        root.addWidget(_scroll_page(content))
        experiment.result_ready.connect(self.display_result)

    def load_latest(self) -> None:
        try:
            if self.experiment.backend_combo.currentData() == "arbor":
                result = ArborEscapeSizAdapter(self.overview.workspace()).latest_result(
                    self.experiment.arbor_config(),
                    output_root=self.overview.output_edit.text(),
                )
            else:
                result = NeuronEscapeSizAdapter(self.overview.workspace()).latest_result(
                    self.experiment.config(),
                    output_root=self.overview.output_edit.text(),
                )
        except Exception as exc:
            QMessageBox.critical(self, "Could not load result", str(exc))
            return
        if result is None:
            backend = "Arbor" if self.experiment.backend_combo.currentData() == "arbor" else "NEURON"
            QMessageBox.information(
                self,
                "No result found",
                f"No matching {backend} Escape-SIZ summary was found.",
            )
            return
        self.display_result(result)

    def open_summary(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "Open Escape-SIZ summary",
            str(Path(self.overview.output_edit.text()).expanduser()),
            "JSON files (*.json)",
        )
        if not selected:
            return
        try:
            payload = json.loads(Path(selected).read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("Escape-SIZ summary must contain a JSON object.")
            is_arbor = (
                payload.get("backend") == "arbor"
                or payload.get("recipe") == "ablation_notebook_arbor_comparison_v1"
            )
            result = (
                load_arbor_ablation_result(selected)
                if is_arbor
                else load_escape_siz_result(selected)
            )
        except Exception as exc:
            QMessageBox.critical(self, "Invalid summary", str(exc))
            return
        self.display_result(result)

    def display_result(self, result: ResultRecord) -> None:
        self.result_status.setText(f"{result.status} · {result.completed_at}")
        rows = [
            ("Summary", result.summary_path),
            ("Status", result.status),
            ("Completed", result.completed_at),
            *[(str(key), _display_value(value)) for key, value in result.metadata.items()],
        ]
        self.metadata.setRowCount(len(rows))
        for row, (key, value) in enumerate(rows):
            self.metadata.setItem(row, 0, QTableWidgetItem(key))
            self.metadata.setItem(row, 1, QTableWidgetItem(value))
        self.metadata.resizeRowsToContents()
        clear_layout(self.result_checks)
        for check in result.checks:
            self.result_checks.addWidget(CheckRow(check))
        clear_layout(self.artifact_rows)
        for artifact in result.artifacts:
            row = Card(inset=True)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(11, 8, 11, 8)
            row_layout.addWidget(StatusPill(CheckState.PASS if artifact.exists else CheckState.WARNING))
            label = QLabel(f"{artifact.label}  ·  {artifact.kind}\n{artifact.path}")
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            row_layout.addWidget(label, 1)
            reveal = QPushButton("Reveal")
            reveal.setEnabled(artifact.exists)
            reveal.clicked.connect(lambda _=False, path=artifact.path: _reveal(path))
            row_layout.addWidget(reveal)
            self.artifact_rows.addWidget(row)
        image = result.primary_image
        if image:
            pixmap = QPixmap(str(image))
            if not pixmap.isNull():
                self._pixmap = pixmap
                scaled = pixmap.scaledToWidth(1080, Qt.TransformationMode.SmoothTransformation)
                self.image_label.setPixmap(scaled)
                self.image_label.resize(scaled.size())
        else:
            self._pixmap = None
            self.image_label.setPixmap(QPixmap())
            self.image_label.setText("No existing PNG artifact was recorded in this summary.")
        self.status_message.emit(f"Loaded result: {Path(result.summary_path).name}")


class EnginesPage(QWidget):
    def __init__(self, overview: OverviewPage, parent: QWidget | None = None):
        super().__init__(parent)
        self.overview = overview
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(30, 26, 30, 32)
        layout.setSpacing(18)
        layout.addLayout(
            _page_header(
                "Adapters",
                "Independent scientific runtimes",
                "Each lane is probed independently; implemented simulation workers launch out of process. VND is detection/viewer-only and is never bundled.",
            )
        )
        refresh = QPushButton("Refresh engine profiles")
        refresh.setProperty("primary", True)
        refresh.clicked.connect(self.refresh)
        top = QHBoxLayout()
        top.addWidget(refresh)
        top.addStretch()
        layout.addLayout(top)
        self.cards = QVBoxLayout()
        self.cards.setSpacing(12)
        layout.addLayout(self.cards)
        note = Card()
        note_layout = QVBoxLayout(note)
        note_layout.setContentsMargins(17, 15, 17, 15)
        title = QLabel("Integration rules")
        title.setObjectName("SectionTitle")
        note_layout.addWidget(title)
        body = QLabel(
            "• NEURON and Arbor must not share an imported digifly.phase2 namespace in the UI process.\n"
            "• DPointNet, PointNet/NEST, and BioNet are distinct BMTK lanes.\n"
            "• A future VND handoff will receive prepared activity/SONATA assets; it is not a dynamics backend.\n"
            "• Phase 3/MuJoCo will be added as another worker profile using the same project and result contracts."
        )
        body.setObjectName("Muted")
        body.setWordWrap(True)
        note_layout.addWidget(body)
        layout.addWidget(note)
        layout.addStretch(1)
        root.addWidget(_scroll_page(content))

    def refresh(self) -> None:
        clear_layout(self.cards)
        QApplication.processEvents()
        try:
            probes = self.overview.workspace().probe_engines(self.overview.python_edit.text())
        except Exception as exc:
            self.cards.addWidget(_muted_label(f"Engine probe failed: {exc}"))
            return
        for probe in probes:
            self.cards.addWidget(EngineCard(probe))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APPLICATION_NAME)
        self.resize(1440, 940)
        self.setMinimumSize(1120, 760)
        self.settings = QSettings(ORGANIZATION_NAME, APPLICATION_NAME)
        self.legacy_settings = QSettings(ORGANIZATION_NAME, LEGACY_APPLICATION_NAME)
        self.current_project_path: Path | None = None
        root = QWidget()
        root.setObjectName("RootWindow")
        self.setCentralWidget(root)
        shell = QHBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(225)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(15, 20, 15, 15)
        sidebar_layout.setSpacing(7)
        brand = QLabel("Digifly")
        brand.setObjectName("Brand")
        subbrand = QLabel("SCIENTIFIC WORKSTATION")
        subbrand.setObjectName("Eyebrow")
        sidebar_layout.addWidget(brand)
        sidebar_layout.addWidget(subbrand)
        sidebar_layout.addSpacing(23)
        self.nav_buttons: list[QPushButton] = []
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_group.idToggled.connect(self._navigation_toggled)
        for index, (label, icon) in enumerate(
            (
                ("Workspace", "⌂"),
                ("Circuit Builder", "⌁"),
                ("Escape-SIZ", "◉"),
                ("Results", "▦"),
                ("Engines", "◇"),
            )
        ):
            button = QPushButton(f"{icon}   {label}")
            button.setObjectName("NavButton")
            button.setCheckable(True)
            self.nav_group.addButton(button, index)
            sidebar_layout.addWidget(button)
            self.nav_buttons.append(button)
        sidebar_layout.addStretch(1)
        boundary = Card(inset=True)
        boundary_layout = QVBoxLayout(boundary)
        boundary_layout.setContentsMargins(11, 10, 11, 10)
        boundary_layout.addWidget(StatusPill(CheckState.PASS, f"v{__version__}"))
        boundary_text = QLabel("Separate repository\nNative-file adapters")
        boundary_text.setObjectName("Muted")
        boundary_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        boundary_layout.addWidget(boundary_text)
        sidebar_layout.addWidget(boundary)
        shell.addWidget(sidebar)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        topbar = QFrame()
        topbar.setObjectName("Topbar")
        topbar_layout = QHBoxLayout(topbar)
        topbar_layout.setContentsMargins(22, 10, 22, 10)
        self.project_label = QLabel("Unsaved project")
        self.project_label.setStyleSheet("font-weight:650;")
        topbar_layout.addWidget(self.project_label)
        topbar_layout.addStretch()
        self.read_only_badge = StatusPill(CheckState.INFO, "DRY-RUN SAFE")
        topbar_layout.addWidget(self.read_only_badge)
        right.addWidget(topbar)
        self.pages = QStackedWidget()
        self.overview_page = OverviewPage()
        self.circuit_builder_page = CircuitBuilderPage(self.overview_page)
        self.experiment_page = ExperimentPage(self.overview_page)
        self.results_page = ResultsPage(self.overview_page, self.experiment_page)
        self.engines_page = EnginesPage(self.overview_page)
        for page in (
            self.overview_page,
            self.circuit_builder_page,
            self.experiment_page,
            self.results_page,
            self.engines_page,
        ):
            self.pages.addWidget(page)
        right.addWidget(self.pages, 1)
        shell.addLayout(right, 1)
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Ready")
        self.circuit_builder_page.status_message.connect(self.statusBar().showMessage)
        self.experiment_page.status_message.connect(self.statusBar().showMessage)
        self.results_page.status_message.connect(self.statusBar().showMessage)
        self.experiment_page.result_ready.connect(
            lambda _result: self.show_page(self.pages.indexOf(self.results_page))
        )
        self._last_editor_page: QWidget = self.circuit_builder_page
        self._project_workflow: str | None = None
        self._build_menu()
        self._restore_settings()
        for label in self.findChildren(QLabel):
            make_label_copyable(label)
        self.show_page(0)

    def _navigation_toggled(self, index: int, checked: bool) -> None:
        """Navigate for mouse, keyboard, and accessibility state changes."""
        if checked and self.pages.currentIndex() != index:
            self.show_page(index)

    def show_page(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        for button_index, button in enumerate(self.nav_buttons):
            button.setChecked(button_index == index)
        current = self.pages.widget(index)
        if current in (self.circuit_builder_page, self.experiment_page):
            self._last_editor_page = current
        if current is self.engines_page:
            self.engines_page.refresh()

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("File")
        new_action = QAction("New project", self)
        new_action.setShortcut("Ctrl+N")
        new_action.triggered.connect(self.new_project)
        open_action = QAction("Open project…", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self.open_project)
        save_action = QAction("Save project", self)
        save_action.setShortcut("Ctrl+S")
        save_action.triggered.connect(self.save_project)
        save_as_action = QAction("Save project as…", self)
        save_as_action.setShortcut("Ctrl+Shift+S")
        save_as_action.triggered.connect(lambda: self.save_project(save_as=True))
        file_menu.addActions((new_action, open_action, save_action, save_as_action))
        file_menu.addSeparator()
        quit_action = QAction("Quit", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)
        help_menu = self.menuBar().addMenu("Help")
        docs_action = QAction("Open architecture guide", self)
        docs_action.triggered.connect(self.open_architecture_guide)
        help_menu.addAction(docs_action)

    def open_architecture_guide(self) -> None:
        guide = resource_path("docs", "ARCHITECTURE.md")
        if guide.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(guide)))
            return
        QMessageBox.information(
            self,
            "Architecture guide unavailable",
            "The local architecture guide was not included in this build. See the repository's docs/ARCHITECTURE.md.",
        )

    def new_project(self) -> None:
        self.current_project_path = None
        self._project_workflow = None
        self.project_label.setText("Unsaved project")
        self.circuit_builder_page.reset()
        self.experiment_page.set_config(EscapeSizConfig.ablation_notebook_active())
        self._last_editor_page = self.circuit_builder_page
        self.show_page(0)

    def open_project(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "Open Digifly project",
            str(_workspace_home()),
            "Digifly projects (*.digifly.json);;JSON files (*.json)",
        )
        if not selected:
            return
        try:
            project = DigiflyProject.load(selected)
        except Exception as exc:
            QMessageBox.critical(self, "Could not open project", str(exc))
            return
        self.overview_page.workspace_edit.setText(project.digifly_public_root)
        self.overview_page.output_edit.setText(project.output_root)
        self.overview_page.python_edit.setText(project.python_executable)
        try:
            if project.selected_workflow == CIRCUIT_BUILDER_WORKFLOW:
                spec = CircuitSpec.from_dict(project.experiment)
                self.circuit_builder_page.refresh_connectomes()
                self.circuit_builder_page.set_selected_engine_key(project.selected_engine)
                self.circuit_builder_page.set_circuit_spec(spec)
                self.circuit_builder_page.load_saved_assets()
                self._last_editor_page = self.circuit_builder_page
                target_page = self.circuit_builder_page
            elif project.selected_workflow == "escape_siz_gfc_contact_na":
                self.experiment_page.set_config(EscapeSizConfig.from_dict(project.experiment))
                self._last_editor_page = self.experiment_page
                target_page = self.experiment_page
            else:
                raise ValueError(f"Unsupported project workflow: {project.selected_workflow}")
        except Exception as exc:
            # The new project has already changed workspace/editor fields.
            # Detach from any previously opened path so a later Ctrl-S cannot
            # overwrite that older project with this partial state.
            self.current_project_path = None
            self._project_workflow = None
            self.project_label.setText("Open failed · unsaved state")
            QMessageBox.critical(self, "Could not open project", str(exc))
            return
        self.current_project_path = Path(selected).resolve()
        self._project_workflow = project.selected_workflow
        self.project_label.setText(project.name)
        self.statusBar().showMessage(f"Opened {self.current_project_path}")
        self.show_page(self.pages.indexOf(target_page))

    def save_project(self, *, save_as: bool = False) -> None:
        current = self.pages.currentWidget()
        editor = current if current in (self.circuit_builder_page, self.experiment_page) else self._last_editor_page
        if editor is self.circuit_builder_page:
            selected_workflow = CIRCUIT_BUILDER_WORKFLOW
            selected_engine = self.circuit_builder_page.selected_engine_key()
            try:
                experiment = self.circuit_builder_page.circuit_spec().to_dict()
            except Exception as exc:
                QMessageBox.warning(self, "Invalid circuit design", str(exc))
                return
            suggested_name = "circuit.digifly.json"
        else:
            try:
                config = self.experiment_page.config()
            except Exception as exc:
                QMessageBox.warning(self, "Invalid controls", f"Fix the experiment controls before saving:\n{exc}")
                return
            selected_workflow = "escape_siz_gfc_contact_na"
            selected_engine = "neuron"
            experiment = config.to_dict()
            suggested_name = "escape-siz.digifly.json"
        destination = self.current_project_path
        if (
            destination is not None
            and not save_as
            and self._project_workflow is not None
            and self._project_workflow != selected_workflow
        ):
            QMessageBox.information(
                self,
                "Save as a new project",
                "This editor uses a different workflow from the opened project. Choose a new file so the original project is not converted or overwritten.",
            )
            self.save_project(save_as=True)
            return
        if save_as or destination is None:
            selected, _ = QFileDialog.getSaveFileName(
                self,
                "Save Digifly project",
                str(_workspace_home() / suggested_name),
                "Digifly projects (*.digifly.json)",
            )
            if not selected:
                return
            destination = Path(selected)
            if not str(destination).endswith(".digifly.json"):
                destination = Path(str(destination) + ".digifly.json")
        project = DigiflyProject(
            name=destination.name.removesuffix(".digifly.json"),
            digifly_public_root=self.overview_page.workspace_edit.text(),
            output_root=self.overview_page.output_edit.text(),
            python_executable=self.overview_page.python_edit.text(),
            selected_engine=selected_engine,
            selected_workflow=selected_workflow,
            experiment=experiment,
        )
        try:
            project.save(destination)
        except Exception as exc:
            QMessageBox.critical(self, "Could not save project", str(exc))
            return
        self.current_project_path = destination.resolve()
        self._project_workflow = selected_workflow
        self.project_label.setText(project.name)
        self.statusBar().showMessage(f"Saved {self.current_project_path}")

    def _restore_settings(self) -> None:
        workspace = self.settings.value("workspace_root")
        output = self.settings.value("output_root")
        worker_python = self.settings.value("neuron_python")
        try:
            profile = load_default_profile()
        except (OSError, ValueError):
            profile = None
        if profile is not None:
            if not workspace:
                workspace = str(profile.workspace_root)
            if not output:
                output = str(profile.output_root)
            if not worker_python:
                runtime = profile.runtime_path(ResourceKind.NEURON_RUNTIME)
                worker_python = str(runtime) if runtime is not None else None
        # Import only read-only input/runtime bindings from the legacy app on
        # first launch. Workstation outputs deliberately remain in their new
        # default root so the two applications cannot overwrite each other's
        # jobs, caches, or results.
        if not workspace:
            workspace = self.legacy_settings.value("workspace_root")
        if not worker_python:
            worker_python = self.legacy_settings.value("neuron_python")
        if workspace:
            self.overview_page.workspace_edit.setText(str(workspace))
        if output:
            self.overview_page.output_edit.setText(str(output))
        if worker_python:
            self.overview_page.python_edit.setText(str(worker_python))

    def closeEvent(self, event: Any) -> None:
        if self.experiment_page._process is not None:
            QMessageBox.warning(
                self,
                "A scientific worker is still running",
                "The GUI will remain open while the worker is active. Return to Escape-SIZ and use Stop safely "
                "so the cancellation request and partial-artifact state are recorded.",
            )
            event.ignore()
            return
        self.settings.setValue("workspace_root", self.overview_page.workspace_edit.text())
        self.settings.setValue("output_root", self.overview_page.output_edit.text())
        self.settings.setValue("neuron_python", self.overview_page.python_edit.text())
        super().closeEvent(event)


def launch(argv: list[str] | None = None) -> int:
    application = QApplication(argv or [])
    application.setApplicationName(APPLICATION_NAME)
    application.setOrganizationName(ORGANIZATION_NAME)
    application.setApplicationVersion(__version__)
    application.setStyle("Fusion")
    application.setStyleSheet(APP_STYLE)
    font = QFont()
    font.setPointSize(12)
    application.setFont(font)
    window = MainWindow()
    window.show()
    return application.exec()


def _double_spin(
    minimum: float,
    maximum: float,
    value: float,
    *,
    suffix: str = "",
    decimals: int = 3,
) -> QDoubleSpinBox:
    control = QDoubleSpinBox()
    control.setRange(minimum, maximum)
    control.setDecimals(decimals)
    control.setValue(value)
    control.setSuffix(suffix)
    control.setSingleStep(max(10 ** (-decimals), abs(value) / 20 if value else 0.1))
    return control


def _connect_change(widget: QWidget, callback: Any) -> None:
    if isinstance(widget, (QDoubleSpinBox, QSpinBox)):
        widget.valueChanged.connect(callback)
    elif isinstance(widget, QComboBox):
        widget.currentIndexChanged.connect(callback)
    elif isinstance(widget, QCheckBox):
        widget.toggled.connect(callback)
    elif isinstance(widget, QPlainTextEdit):
        widget.textChanged.connect(callback)


def _ad_hoc_check(state: CheckState, title: str, detail: str, *, blocking: bool = False):
    from digifly_app.core.models import PreflightCheck

    return PreflightCheck("ui", title, state, detail, blocking=blocking)


def _muted_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Muted")
    label.setWordWrap(True)
    return make_label_copyable(label)


def _display_value(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, sort_keys=True)
    return str(value)


def _reveal(raw: str) -> None:
    path = Path(raw).expanduser()
    target = path if path.exists() else path.parent
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))
