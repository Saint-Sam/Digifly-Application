from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping

from PySide6.QtCore import QProcess, QProcessEnvironment, QSettings, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QFont, QIcon, QPixmap, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QDialog,
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
    QSlider,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from digifly_app import __version__
from digifly_app.core.jobs import JobStore
from digifly_app.core.circuit import CircuitSpec
from digifly_app.core.connectomes import NeuronRecord
from digifly_app.core.experiment import EXPERIMENT_BUILDER_WORKFLOW, ExperimentSpec
from digifly_app.core.models import (
    Artifact,
    CheckState,
    ExecutionPlan,
    PreflightReport,
    ResultRecord,
)
from digifly_app.core.morphology import Morphology, load_swc
from digifly_app.core.process_environment import (
    EXTERNAL_PYTHON_ENV_REMOVE,
    external_runtime_launcher,
)
from digifly_app.core.project import DigiflyProject
from digifly_app.core.resources import ResourceSnapshot, capture_resources
from digifly_app.core.results import load_escape_siz_result
from digifly_app.core.workspace import DigiflyWorkspace
from digifly_app.core.paths import bundled_arbor_python, package_root, resource_path
from digifly_app.core.resource_profile import ResourceKind, load_default_profile
from digifly_app.core.resource_profile import (
    default_profile_path,
    make_default_profile,
    update_runtime_bindings,
)
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
from digifly_app.engines.generic_experiment import load_generic_experiment_result
from .style import (
    DARK_THEME,
    LIGHT_THEME,
    normalize_theme,
    style_for_theme,
)
from .circuit_builder import CIRCUIT_BUILDER_WORKFLOW, CircuitBuilderPage
from .circuit_viewport import (
    CircuitViewport,
    DISPLAY_MODE_FULL_SKELETONS,
    DISPLAY_MODE_SOMA_POINTS,
)
from .data_library import DataLibraryPage
from .experiment_builder import ExperimentBuilderPage
from .runtime_setup import RuntimeSetupDialog
from .result_playback import (
    ActivityFlowTrack,
    active_flow_segments,
    active_spiking_somas,
    load_activity_flow_tracks,
    segment_distances_from_soma,
)
from .snapshot import save_image_with_dialog
from .voltage_plot import (
    MODE_2D,
    MODE_3D_STACK,
    VoltagePlotWidget,
    load_voltage_traces,
)
from .widgets import Card, CheckRow, EngineCard, StatusPill, clear_layout, make_label_copyable


ORGANIZATION_NAME = "Digifly"
APPLICATION_NAME = "Digifly Workstation"
LEGACY_APPLICATION_NAME = "Digifly App"


def _workspace_home() -> Path:
    """Keep projects and large simulation artifacts outside the app bundle."""
    return Path.home() / "Digifly Workstation Workspace"


def _valid_workspace_path(value: Any) -> bool:
    if value is None or not str(value).strip():
        return False
    path = Path(str(value)).expanduser()
    return path.is_dir() and (path / "README.md").is_file()


def _valid_output_path(value: Any) -> bool:
    if value is None or not str(value).strip():
        return False
    path = Path(str(value)).expanduser()
    if path.is_dir():
        return os.access(path, os.W_OK)
    return not path.exists() and path.parent.is_dir() and os.access(path.parent, os.W_OK)


def _valid_runtime_path(value: Any) -> bool:
    if value is None or not str(value).strip():
        return False
    path = Path(str(value)).expanduser()
    return path.is_file() and os.access(path, os.X_OK)


def _first_valid_path(
    *values: Any,
    validator: Any,
    preserve_final_symlink: bool = False,
) -> str | None:
    for value in values:
        if validator(value):
            path = Path(str(value)).expanduser()
            return str(
                external_runtime_launcher(path)
                if preserve_final_symlink
                else path.resolve()
            )
    return None


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


class _ResultDisclosure(Card):
    """Compact result section with a large, accessible disclosure target."""

    def __init__(
        self,
        title: str,
        key: str,
        *,
        expanded: bool = False,
        show_status: bool = False,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setProperty("resultDisclosure", True)
        shell = QVBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        header = QWidget()
        header.setObjectName("ResultDisclosureHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(8, 0, 12, 0)
        header_layout.setSpacing(8)
        self.toggle_button = QToolButton()
        self.toggle_button.setObjectName("ResultDisclosureToggle")
        self.toggle_button.setProperty("sectionKey", key)
        self.toggle_button.setText(title)
        self.toggle_button.setCheckable(True)
        self.toggle_button.setChecked(bool(expanded))
        self.toggle_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.toggle_button.setSizePolicy(
            QSizePolicy.Policy.Maximum,
            QSizePolicy.Policy.Fixed,
        )
        self.toggle_button.setAccessibleName(f"{title} results section")
        header_layout.addWidget(self.toggle_button)
        self.status_label = QLabel("not loaded")
        self.status_label.setObjectName("ArtifactSummary")
        self.status_label.setProperty("artifactState", "info")
        self.status_label.setVisible(show_status)
        header_layout.addWidget(self.status_label)
        header_layout.addStretch(1)
        shell.addWidget(header)

        self.body = QWidget()
        self.body.setObjectName("ResultDisclosureBody")
        self.content_layout = QVBoxLayout(self.body)
        self.content_layout.setContentsMargins(16, 14, 16, 16)
        self.content_layout.setSpacing(10)
        shell.addWidget(self.body)

        self.toggle_button.toggled.connect(self.set_expanded)
        self.set_expanded(bool(expanded))

    @property
    def is_expanded(self) -> bool:
        return self.toggle_button.isChecked()

    def set_expanded(self, expanded: bool) -> None:
        expanded = bool(expanded)
        if self.toggle_button.isChecked() != expanded:
            self.toggle_button.setChecked(expanded)
            return
        self.toggle_button.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        self.body.setVisible(expanded)
        action = "collapse" if expanded else "expand"
        state = "Expanded" if expanded else "Collapsed"
        self.toggle_button.setToolTip(f"Click to {action} {self.toggle_button.text()}")
        self.toggle_button.setAccessibleDescription(
            f"{state} results section. Activate to {action}."
        )

    def set_status(self, text: str, state: str) -> None:
        self.status_label.setText(text)
        self.status_label.setProperty("artifactState", state)
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)


class _FittedFigureLabel(QLabel):
    """Render a source image inside the available frame without clipping it."""

    def __init__(self, placeholder: str, parent: QWidget | None = None):
        super().__init__(placeholder, parent)
        self._source_pixmap: QPixmap | None = None
        self.setObjectName("Muted")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(300, 500)
        self.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Ignored,
        )

    def set_figure(self, pixmap: QPixmap) -> None:
        self._source_pixmap = QPixmap(pixmap)
        self.setText("")
        self._render_fitted()

    def clear_figure(self, placeholder: str) -> None:
        self._source_pixmap = None
        QLabel.setPixmap(self, QPixmap())
        self.setText(placeholder)

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        self._render_fitted()

    def _render_fitted(self) -> None:
        source = self._source_pixmap
        if source is None or source.isNull():
            return
        bounds = self.contentsRect().adjusted(16, 16, -16, -16).size()
        if bounds.width() <= 0 or bounds.height() <= 0:
            return
        # QLabel sizes are device-independent pixels.  Scaling the source to
        # that logical size discards half the available samples on a Retina
        # display and macOS then enlarges the reduced pixmap, softening text.
        # Render at the screen's physical-pixel density and attach that density
        # to the result so it retains the same logical fit without the blur.
        pixel_ratio = max(1.0, float(self.devicePixelRatioF()))
        physical_bounds = bounds * pixel_ratio
        target = source.size()
        target.scale(physical_bounds, Qt.AspectRatioMode.KeepAspectRatio)
        rendered = source.scaled(
            target,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        rendered.setDevicePixelRatio(pixel_ratio)
        QLabel.setPixmap(self, rendered)


class _FullResolutionFigureDialog(QDialog):
    """Show one source pixel per display pixel in a resizable viewer."""

    def __init__(
        self,
        pixmap: QPixmap,
        title: str,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(f"{title} · full resolution")
        self.resize(1280, 820)
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(8)
        detail = QLabel(
            f"Source {pixmap.width()} × {pixmap.height()} px · "
            "one source pixel per display pixel"
        )
        detail.setObjectName("Muted")
        root.addWidget(detail)
        scroll = QScrollArea()
        scroll.setWidgetResizable(False)
        label = QLabel()
        label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        rendered = QPixmap(pixmap)
        pixel_ratio = max(1.0, float(self.devicePixelRatioF()))
        rendered.setDevicePixelRatio(pixel_ratio)
        label.setPixmap(rendered)
        logical_size = rendered.deviceIndependentSize().toSize()
        label.resize(logical_size)
        label.setMinimumSize(logical_size)
        scroll.setWidget(label)
        root.addWidget(scroll, 1)


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
                "Link native source/data trees and isolated runtimes. Bundled Arbor is ready when included in this build.",
            )
        )

        workspace_card = Card()
        form = QFormLayout(workspace_card)
        form.setContentsMargins(18, 18, 18, 18)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(12)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.workspace_edit = QLineEdit("")
        self.workspace_edit.setPlaceholderText(
            "Optional — choose an existing Digifly Public source workspace"
        )
        self.output_edit = QLineEdit(str(_workspace_home() / "runs"))
        self.python_edit = QLineEdit("/opt/anaconda3/bin/python")
        included_arbor = bundled_arbor_python()
        self.arbor_python_edit = QLineEdit(str(included_arbor or "/opt/anaconda3/bin/python"))
        if included_arbor is not None:
            self.arbor_python_edit.setToolTip(
                "Bundled Arbor 0.12.2 — ready without a separate installation. "
                "You may still choose another Arbor Python."
            )
        self.bmtk_python_edit = QLineEdit("")
        self.bmtk_python_edit.setPlaceholderText(
            "Choose one Python containing BMTK, BioNet, NEURON, NumPy, and h5py"
        )
        form.addRow(
            "Legacy source workspace",
            _path_row(self.workspace_edit, self, "Choose an existing Digifly Public workspace"),
        )
        form.addRow("Workstation output root", _path_row(self.output_edit, self, "Choose output root"))
        form.addRow("NEURON Python", _path_row(self.python_edit, self, "Choose NEURON Python", file_mode=True))
        form.addRow(
            "Arbor Python",
            _path_row(self.arbor_python_edit, self, "Choose Arbor Python", file_mode=True),
        )
        form.addRow(
            "BMTK BioNet Python",
            _path_row(self.bmtk_python_edit, self, "Choose BMTK BioNet Python", file_mode=True),
        )
        runtime_setup = QPushButton("Find or install NEURON / Arbor / BMTK…")
        runtime_setup.clicked.connect(self.open_runtime_setup)
        form.addRow("Runtime setup", runtime_setup)
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
            value.setObjectName("MetricValue")
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

        for editor in (
            self.workspace_edit,
            self.output_edit,
            self.python_edit,
            self.arbor_python_edit,
            self.bmtk_python_edit,
        ):
            editor.textChanged.connect(self.settings_changed)

    def open_runtime_setup(self) -> None:
        dialog = RuntimeSetupDialog(
            current_neuron=self.python_edit.text(),
            current_arbor=self.arbor_python_edit.text(),
            current_bmtk=self.bmtk_python_edit.text(),
            parent=self,
        )
        dialog.runtimes_selected.connect(self._runtime_selected)
        dialog.exec()

    def _runtime_selected(
        self,
        neuron_python: str,
        arbor_python: str,
        bmtk_python: str = "",
    ) -> None:
        if neuron_python:
            self.python_edit.setText(neuron_python)
        if arbor_python:
            self.arbor_python_edit.setText(arbor_python)
        if bmtk_python:
            self.bmtk_python_edit.setText(bmtk_python)
        profile_path = default_profile_path()
        try:
            profile = load_default_profile()
            if profile is None:
                profile = make_default_profile(
                    workspace_root=self.workspace_edit.text().strip() or None,
                    output_root=self.output_edit.text(),
                    neuron_runtime=neuron_python or None,
                    arbor_runtime=arbor_python or None,
                    bmtk_runtime=bmtk_python or None,
                )
                updated = profile
            else:
                updated = update_runtime_bindings(
                    profile,
                    neuron_runtime=neuron_python or None,
                    arbor_runtime=arbor_python or None,
                    bmtk_runtime=bmtk_python or None,
                )
            destination = (
                profile_path.with_name("resources-v2.json")
                if profile_path.name == "resources-v1.json"
                else profile_path
            )
            updated.save(destination, replace=destination.exists())
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Could not save runtime choices", str(exc))
            return
        self.doctor_summary.setText(f"Saved verified runtimes to {destination}")
        self.refresh()

    def workspace(self) -> DigiflyWorkspace:
        return DigiflyWorkspace(self.workspace_edit.text())

    def refresh(self) -> None:
        self.doctor_button.setEnabled(False)
        self.doctor_summary.setText("Inspecting source trees and runtimes…")
        QApplication.processEvents()
        workspace = self.workspace()
        base = workspace.base_preflight()
        try:
            probes = workspace.probe_engines(
                self.python_edit.text(),
                self.arbor_python_edit.text(),
                self.bmtk_python_edit.text(),
            )
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
        note_title.setObjectName("WarningTitle")
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
            python_executable=self.overview.arbor_python_edit.text().strip(),
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

    def __init__(
        self,
        overview: OverviewPage,
        experiment: ExperimentBuilderPage,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.overview = overview
        self.experiment = experiment
        self._pixmap: QPixmap | None = None
        self._current_result_name = "digifly-result"
        self._activity_tracks: tuple[ActivityFlowTrack, ...] = ()
        self._activity_distances: dict[str, dict[int, float]] = {}
        self._activity_stimulus_ids: set[str] = set()
        self._activity_timer = QTimer(self)
        self._activity_timer.timeout.connect(self._advance_activity_playback)
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
                "Completed run summaries are opened read-only. Recipe-specific evidence checks remain available for compatible legacy results.",
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
        layout.addWidget(checks_card)

        self.artifact_section = _ResultDisclosure(
            "Artifacts",
            "artifacts",
            expanded=False,
            show_status=True,
        )
        artifact_layout = self.artifact_section.content_layout
        self.artifact_rows = QVBoxLayout()
        self.artifact_rows.addWidget(_muted_label("No artifacts loaded."))
        artifact_layout.addLayout(self.artifact_rows)
        layout.addWidget(self.artifact_section)

        preview_card = Card()
        preview_layout = QVBoxLayout(preview_card)
        preview_layout.setContentsMargins(16, 15, 16, 16)
        preview_header = QHBoxLayout()
        preview_title = QLabel("Run visualization")
        preview_title.setObjectName("SectionTitle")
        preview_header.addWidget(preview_title)
        preview_header.addStretch(1)
        preview_layout.addLayout(preview_header)

        self.preview_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.preview_splitter.setObjectName("ResultVisualizationSplitter")
        self.preview_splitter.setChildrenCollapsible(False)

        self.figure_panel = QWidget()
        figure_layout = QVBoxLayout(self.figure_panel)
        figure_layout.setContentsMargins(0, 0, 0, 0)
        figure_layout.setSpacing(7)
        figure_header = QHBoxLayout()
        figure_title = QLabel("Voltage traces")
        figure_title.setObjectName("Strong")
        figure_header.addWidget(figure_title)
        figure_header.addStretch(1)
        self.expand_trace_button = QPushButton("Expand traces")
        self.expand_trace_button.setObjectName("ExpandResultTracesButton")
        self.expand_trace_button.setCheckable(True)
        self.expand_trace_button.setToolTip(
            "Temporarily use the full Results width for the voltage plot"
        )
        self.expand_trace_button.toggled.connect(self._set_trace_view_expanded)
        figure_header.addWidget(self.expand_trace_button)
        self.image_info = QLabel("No figure loaded")
        self.image_info.setObjectName("Muted")
        figure_header.addWidget(self.image_info)
        self.full_resolution_button = QPushButton("Open source PNG")
        self.full_resolution_button.setEnabled(False)
        self.full_resolution_button.setToolTip(
            "Open the original plot at one source pixel per display pixel"
        )
        self.full_resolution_button.clicked.connect(self._open_full_resolution)
        figure_header.addWidget(self.full_resolution_button)
        self.save_trace_button = QPushButton("Save plot…")
        self.save_trace_button.setObjectName("SaveResultTraceButton")
        self.save_trace_button.setEnabled(False)
        self.save_trace_button.setToolTip(
            "Save the current 2-D or 3-D interactive plot as a high-resolution PNG"
        )
        self.save_trace_button.clicked.connect(self._save_voltage_trace)
        figure_header.addWidget(self.save_trace_button)
        figure_layout.addLayout(figure_header)
        voltage_mode_row = QHBoxLayout()
        voltage_mode_label = QLabel("View")
        voltage_mode_label.setObjectName("Muted")
        voltage_mode_row.addWidget(voltage_mode_label)
        self.voltage_2d_button = QPushButton("2D traces")
        self.voltage_2d_button.setObjectName("ViewModeButton")
        self.voltage_2d_button.setCheckable(True)
        self.voltage_2d_button.setChecked(True)
        self.voltage_2d_button.setEnabled(False)
        self.voltage_3d_button = QPushButton("3D trace stack")
        self.voltage_3d_button.setObjectName("ViewModeButton")
        self.voltage_3d_button.setCheckable(True)
        self.voltage_3d_button.setEnabled(False)
        self.voltage_morphology_button = QPushButton("Morphology voltage")
        self.voltage_morphology_button.setObjectName("ViewModeButton")
        self.voltage_morphology_button.setCheckable(True)
        self.voltage_morphology_button.setEnabled(False)
        self.voltage_morphology_button.setToolTip(
            "Requires actual compartment-voltage recordings; soma voltage is not spread across the morphology"
        )
        self.voltage_mode_group = QButtonGroup(self)
        self.voltage_mode_group.setExclusive(True)
        for button in (
            self.voltage_2d_button,
            self.voltage_3d_button,
            self.voltage_morphology_button,
        ):
            self.voltage_mode_group.addButton(button)
            voltage_mode_row.addWidget(button)
        voltage_mode_row.addStretch(1)
        self.voltage_2d_button.clicked.connect(
            lambda: self._set_voltage_plot_mode(MODE_2D)
        )
        self.voltage_3d_button.clicked.connect(
            lambda: self._set_voltage_plot_mode(MODE_3D_STACK)
        )
        figure_layout.addLayout(voltage_mode_row)
        self.voltage_plot = VoltagePlotWidget()
        self.voltage_plot.status_message.connect(self.status_message)
        self.voltage_plot.time_selected.connect(self._voltage_time_selected)
        self.image_scroll = QScrollArea()
        self.image_scroll.setObjectName("ImagePreviewScroll")
        self.image_scroll.setWidgetResizable(True)
        self.image_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.image_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.image_scroll.setMinimumSize(420, 520)
        self.image_label = _FittedFigureLabel(
            "Load a completed run to preview its PNG."
        )
        self.image_scroll.setWidget(self.image_label)
        self.figure_stack = QStackedWidget()
        self.figure_stack.setObjectName("VoltageFigureStack")
        self.figure_stack.addWidget(self.voltage_plot)
        self.figure_stack.addWidget(self.image_scroll)
        self.figure_stack.setCurrentWidget(self.image_scroll)
        figure_layout.addWidget(self.figure_stack, 1)
        self.preview_splitter.addWidget(self.figure_panel)

        self.circuit_panel = QWidget()
        circuit_layout = QVBoxLayout(self.circuit_panel)
        circuit_layout.setContentsMargins(0, 0, 0, 0)
        circuit_layout.setSpacing(7)
        circuit_header = QHBoxLayout()
        circuit_title = QLabel("Recorded circuit")
        circuit_title.setObjectName("Strong")
        circuit_header.addWidget(circuit_title)
        circuit_header.addStretch(1)
        self.result_soma_points_button = QPushButton("Soma points")
        self.result_soma_points_button.setObjectName("ResultCircuitModeButton")
        self.result_soma_points_button.setCheckable(True)
        self.result_soma_points_button.setEnabled(False)
        self.result_full_skeletons_button = QPushButton("Full skeletons")
        self.result_full_skeletons_button.setObjectName("ResultCircuitModeButton")
        self.result_full_skeletons_button.setCheckable(True)
        self.result_full_skeletons_button.setChecked(True)
        self.result_full_skeletons_button.setEnabled(False)
        self.result_circuit_mode_group = QButtonGroup(self)
        self.result_circuit_mode_group.setExclusive(True)
        for button in (
            self.result_soma_points_button,
            self.result_full_skeletons_button,
        ):
            self.result_circuit_mode_group.addButton(button)
            circuit_header.addWidget(button)
        self.result_soma_points_button.clicked.connect(
            lambda: self._set_result_circuit_mode(DISPLAY_MODE_SOMA_POINTS)
        )
        self.result_full_skeletons_button.clicked.connect(
            lambda: self._set_result_circuit_mode(DISPLAY_MODE_FULL_SKELETONS)
        )
        self.save_result_visualization_button = QPushButton("Save snapshot…")
        self.save_result_visualization_button.setObjectName(
            "SaveResultVisualizationButton"
        )
        self.save_result_visualization_button.setEnabled(False)
        self.save_result_visualization_button.setToolTip(
            "Save the current recorded-circuit camera and playback frame as a high-resolution PNG"
        )
        self.save_result_visualization_button.clicked.connect(
            self._save_result_visualization
        )
        circuit_header.addWidget(self.save_result_visualization_button)
        circuit_layout.addLayout(circuit_header)
        self.circuit_info = QLabel("No recorded morphology loaded")
        self.circuit_info.setObjectName("Muted")
        self.circuit_info.setWordWrap(True)
        circuit_layout.addWidget(self.circuit_info)
        self.circuit_identity = QLabel(
            "Run-packaged SWCs and their body IDs will appear here."
        )
        self.circuit_identity.setObjectName("Strong")
        self.circuit_identity.setWordWrap(True)
        self.circuit_identity.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.circuit_identity_scroll = QScrollArea()
        self.circuit_identity_scroll.setObjectName("ResultCircuitIdentityScroll")
        self.circuit_identity_scroll.setAccessibleName("Recorded neuron list")
        self.circuit_identity_scroll.setWidgetResizable(True)
        self.circuit_identity_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.circuit_identity_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.circuit_identity_scroll.setMinimumHeight(72)
        self.circuit_identity_scroll.setMaximumHeight(156)
        self.circuit_identity_scroll.setWidget(self.circuit_identity)
        circuit_layout.addWidget(self.circuit_identity_scroll)
        self.result_viewport = CircuitViewport(camera_only=True)
        self.result_viewport.setAccessibleName("Recorded circuit visualization")
        self.result_viewport.setMinimumSize(280, 430)
        self.result_viewport.set_display_mode(DISPLAY_MODE_FULL_SKELETONS)
        self.result_viewport.status_message.connect(self.status_message)
        self.activity_method = QLabel(
            "Inferred activity flow · soma spikes + SWC path distance"
        )
        self.activity_method.setObjectName("Muted")
        self.activity_method.setToolTip(
            "Results-only explanatory playback. The moving band is inferred from each "
            "recorded soma spike at 25 µm/ms along the SWC graph; it is not a direct "
            "measurement of voltage in every compartment."
        )
        self.activity_method.setWordWrap(True)
        circuit_layout.addWidget(self.activity_method)
        activity_controls = QHBoxLayout()
        self.activity_condition_combo = QComboBox()
        self.activity_condition_combo.setObjectName("ResultActivityCondition")
        self.activity_condition_combo.setAccessibleName(
            "Activity playback condition"
        )
        self.activity_condition_combo.setEnabled(False)
        self.activity_condition_combo.currentIndexChanged.connect(
            self._activity_track_changed
        )
        activity_controls.addWidget(self.activity_condition_combo)
        self.activity_play_button = QPushButton("▶ Play")
        self.activity_play_button.setObjectName("ResultActivityPlayButton")
        self.activity_play_button.setEnabled(False)
        self.activity_play_button.clicked.connect(self._toggle_activity_playback)
        activity_controls.addWidget(self.activity_play_button)
        self.activity_slider = QSlider(Qt.Orientation.Horizontal)
        self.activity_slider.setObjectName("ResultActivityTimeline")
        self.activity_slider.setAccessibleName("Activity playback simulation time")
        self.activity_slider.setRange(0, 0)
        self.activity_slider.setEnabled(False)
        self.activity_slider.valueChanged.connect(self._activity_frame_changed)
        activity_controls.addWidget(self.activity_slider, 1)
        self.activity_time_label = QLabel("— ms")
        self.activity_time_label.setObjectName("Strong")
        self.activity_time_label.setMinimumWidth(68)
        self.activity_time_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        activity_controls.addWidget(self.activity_time_label)
        circuit_layout.addLayout(activity_controls)
        circuit_layout.addWidget(self.result_viewport, 1)
        circuit_hint = QLabel(
            "Drag to rotate · Shift-drag or middle-drag to move · wheel to zoom · "
            "right-click a neuron to center · right-double-click to restore"
        )
        circuit_hint.setObjectName("Muted")
        circuit_hint.setWordWrap(True)
        circuit_layout.addWidget(circuit_hint)
        self.preview_splitter.addWidget(self.circuit_panel)
        self.preview_splitter.setStretchFactor(0, 7)
        self.preview_splitter.setStretchFactor(1, 4)
        self.preview_splitter.setSizes([820, 420])
        preview_layout.addWidget(self.preview_splitter)
        layout.addWidget(preview_card)
        layout.addStretch(1)
        root.addWidget(_scroll_page(content))
        experiment.result_ready.connect(self.display_result)

    def _set_trace_view_expanded(self, expanded: bool) -> None:
        self.circuit_panel.setVisible(not expanded)
        self.expand_trace_button.setText(
            "Restore split" if expanded else "Expand traces"
        )
        self.expand_trace_button.setToolTip(
            "Restore the side-by-side circuit visualization"
            if expanded
            else "Temporarily use the full Results width for the voltage plot"
        )
        if not expanded:
            self.preview_splitter.setSizes([820, 420])
        self.status_message.emit(
            "Voltage traces expanded to the full Results width"
            if expanded
            else "Voltage traces and recorded circuit restored side by side"
        )

    def load_latest(self) -> None:
        output_root = Path(self.overview.output_edit.text()).expanduser().resolve()
        candidates: list[Path] = []
        for job_dir in sorted((output_root / "jobs").glob("*"), reverse=True):
            status_path = job_dir / "status.json"
            plan_path = job_dir / "resolved_plan.json"
            if not status_path.is_file() or not plan_path.is_file():
                continue
            try:
                status = json.loads(status_path.read_text(encoding="utf-8"))
                plan = json.loads(plan_path.read_text(encoding="utf-8"))
                summary = Path(str(plan.get("expected_summary_path") or ""))
            except (OSError, ValueError, TypeError):
                continue
            if status.get("state") == "completed" and summary.is_file():
                candidates.append(summary)
        for run_dir in (output_root / "experiments").glob("*"):
            manifest_path = run_dir / "run_manifest.json"
            summary = run_dir / "summary.json"
            if not manifest_path.is_file() or not summary.is_file():
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                continue
            if manifest.get("state") == "completed":
                candidates.append(summary)
        if not candidates:
            QMessageBox.information(
                self,
                "No result found",
                "No completed job with an existing summary was found under the configured output root.",
            )
            return
        candidates.sort(key=lambda path: path.stat().st_mtime_ns, reverse=True)
        try:
            self.display_result(self._load_result(candidates[0]))
        except Exception as exc:
            QMessageBox.critical(self, "Could not load result", str(exc))

    def open_summary(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "Open run summary",
            str(Path(self.overview.output_edit.text()).expanduser()),
            "JSON files (*.json)",
        )
        if not selected:
            return
        try:
            result = self._load_result(Path(selected))
        except Exception as exc:
            QMessageBox.critical(self, "Invalid summary", str(exc))
            return
        self.display_result(result)

    @staticmethod
    def _load_result(path: Path) -> ResultRecord:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Run summary must contain a JSON object.")
        if payload.get("workflow") == EXPERIMENT_BUILDER_WORKFLOW:
            return load_generic_experiment_result(path)
        is_arbor = (
            payload.get("backend") == "arbor"
            or payload.get("recipe") == "ablation_notebook_arbor_comparison_v1"
        )
        return (
            load_arbor_ablation_result(path)
            if is_arbor
            else load_escape_siz_result(path)
        )

    def display_result(self, result: ResultRecord) -> None:
        self._current_result_name = Path(result.summary_path).expanduser().resolve().parent.name
        self._stop_activity_playback()
        self.result_status.setText(f"{result.status} · {result.completed_at}")
        clear_layout(self.result_checks)
        for check in result.checks:
            self.result_checks.addWidget(CheckRow(check))
        clear_layout(self.artifact_rows)
        failed_artifacts = sum(not artifact.exists for artifact in result.artifacts)
        if failed_artifacts:
            self.artifact_section.set_status(
                f"{failed_artifacts} failed",
                "warning",
            )
        elif result.artifacts:
            self.artifact_section.set_status("all pass", "pass")
        else:
            self.artifact_section.set_status("no artifacts", "info")
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
                self.image_label.set_figure(pixmap)
                self.image_info.setText(
                    f"Source PNG {pixmap.width()} × {pixmap.height()} px"
                )
                self.full_resolution_button.setEnabled(True)
            else:
                self._clear_figure_preview(
                    "The recorded PNG could not be decoded."
                )
        else:
            self._clear_figure_preview(
                "No existing PNG artifact was recorded in this summary."
            )
        self._display_recorded_circuit(result)
        self._configure_voltage_plot(result)
        self.status_message.emit(f"Loaded result: {Path(result.summary_path).name}")

    def _open_full_resolution(self) -> None:
        if self._pixmap is None or self._pixmap.isNull():
            return
        previous = getattr(self, "_figure_dialog", None)
        if previous is not None:
            previous.close()
        dialog = _FullResolutionFigureDialog(
            self._pixmap,
            "Digifly voltage traces",
            self,
        )
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog.destroyed.connect(lambda: setattr(self, "_figure_dialog", None))
        self._figure_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _save_voltage_trace(self) -> None:
        if self.voltage_plot.trace_count:
            image = self.voltage_plot.render_high_resolution()
            mode = "3d-trace-stack" if self.voltage_plot.mode == MODE_3D_STACK else "2d-traces"
        elif self._pixmap is not None and not self._pixmap.isNull():
            image = self._pixmap
            mode = "source-voltage-traces"
        else:
            return
        output = save_image_with_dialog(
            self,
            image,
            title="Save high-resolution voltage plot",
            default_name=f"{self._current_result_name}-{mode}.png",
        )
        if output is not None:
            self.status_message.emit(
                f"Saved {image.width()} × {image.height()} PNG: {output}"
            )

    def _configure_voltage_plot(self, result: ResultRecord) -> None:
        run_root = Path(result.summary_path).expanduser().resolve().parent
        neuron_labels = {
            neuron_id: f"{morphology.record.neuron_type} · body {neuron_id}"
            for neuron_id, morphology in self.result_viewport.morphologies.items()
        }
        traces = load_voltage_traces(
            run_root / "voltage_traces.csv",
            neuron_labels=neuron_labels,
        )
        self.voltage_plot.set_traces(traces)
        interactive = bool(traces)
        self.voltage_2d_button.setEnabled(interactive)
        self.voltage_3d_button.setEnabled(interactive)
        self.save_trace_button.setEnabled(
            interactive or bool(self._pixmap is not None and not self._pixmap.isNull())
        )
        if interactive:
            self.voltage_2d_button.setChecked(True)
            self.voltage_plot.set_mode(MODE_2D)
            self.figure_stack.setCurrentWidget(self.voltage_plot)
            self.image_info.setText(
                f"{self.voltage_plot.trace_count} traces · "
                f"{self.voltage_plot.sample_count:,} samples · interactive"
            )
        else:
            self.figure_stack.setCurrentWidget(self.image_scroll)

    def _set_voltage_plot_mode(self, mode: str) -> None:
        if not self.voltage_plot.trace_count:
            return
        self.voltage_plot.set_mode(mode)
        is_3d = mode == MODE_3D_STACK
        self.voltage_2d_button.setChecked(not is_3d)
        self.voltage_3d_button.setChecked(is_3d)
        self.status_message.emit(
            "Voltage plot switched to 3-D trace stack"
            if is_3d
            else "Voltage plot switched to interactive 2-D traces"
        )

    def _voltage_time_selected(self, time_ms: float) -> None:
        track = self._current_activity_track()
        if track is None or not track.frame_times_ms:
            return
        frame_index = min(
            range(len(track.frame_times_ms)),
            key=lambda index: abs(track.frame_times_ms[index] - float(time_ms)),
        )
        self.activity_slider.setValue(frame_index)

    def _save_result_visualization(self) -> None:
        self.result_viewport.save_high_resolution_snapshot(
            f"{self._current_result_name}-activity-flow.png"
        )

    @staticmethod
    def _read_result_object(path: Path) -> dict[str, Any]:
        if not path.is_file():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}
        return dict(payload) if isinstance(payload, dict) else {}

    @staticmethod
    def _recorded_neuron_id(
        artifact: Artifact,
        record_payloads: Mapping[str, Any],
    ) -> str:
        """Recover a biological ID without trusting a storage-directory name.

        New workers use filesystem-safe, collision-resistant morphology folder
        names.  Their artifact label remains bound to the exact biological ID
        in ``worker_request.json``; older runs used that ID as the folder name.
        """

        exact_label_matches = [
            str(neuron_id)
            for neuron_id in record_payloads
            if artifact.label == f"Normalized morphology · {neuron_id}"
        ]
        if len(exact_label_matches) == 1:
            return exact_label_matches[0]
        if len(record_payloads) == 1:
            return str(next(iter(record_payloads)))
        parent_name = Path(artifact.path).expanduser().resolve().parent.name.strip()
        if parent_name in record_payloads:
            return parent_name
        label_suffix = artifact.label.rsplit("·", 1)[-1].strip()
        if label_suffix in record_payloads:
            return label_suffix
        return parent_name or label_suffix

    def _display_recorded_circuit(self, result: ResultRecord) -> None:
        """Load the immutable, run-packaged SWCs associated with a result."""

        run_root = Path(result.summary_path).expanduser().resolve().parent
        request = self._read_result_object(run_root / "worker_request.json")
        circuit = self._read_result_object(run_root / "circuit.json")
        experiment = self._read_result_object(run_root / "experiment.json")

        raw_records = request.get("morphologies")
        record_payloads = dict(raw_records) if isinstance(raw_records, dict) else {}
        raw_connectome = circuit.get("connectome")
        connectome = dict(raw_connectome) if isinstance(raw_connectome, dict) else {}
        connectome_key = str(connectome.get("key") or connectome.get("dataset") or "")

        stimulus_ids: set[str] = set()
        raw_stimuli = experiment.get("stimuli")
        if isinstance(raw_stimuli, list):
            for stimulus in raw_stimuli:
                if not isinstance(stimulus, dict) or not stimulus.get("enabled", True):
                    continue
                raw_targets = stimulus.get("target_neuron_ids")
                if isinstance(raw_targets, list):
                    stimulus_ids.update(str(value) for value in raw_targets)

        morphology_artifacts = [
            artifact
            for artifact in result.artifacts
            if artifact.kind == "morphology"
            and artifact.exists
            and Path(artifact.path).suffix.casefold() == ".swc"
        ]
        morphologies: list[Morphology] = []
        failures: list[str] = []
        for artifact in morphology_artifacts:
            swc_path = Path(artifact.path).expanduser().resolve()
            neuron_id = self._recorded_neuron_id(artifact, record_payloads)
            raw_record = record_payloads.get(neuron_id)
            record_values = dict(raw_record) if isinstance(raw_record, dict) else {}
            neuron_type = str(record_values.get("neuron_type") or "Unknown")
            family = str(record_values.get("family") or "")
            if not family and len(neuron_type) >= 2:
                candidate = neuron_type[:2].upper()
                if candidate in {"AN", "DN", "IN", "MN", "SN"}:
                    family = candidate
            try:
                morphologies.append(
                    load_swc(
                        NeuronRecord(
                            neuron_id=neuron_id,
                            family=family,
                            neuron_type=neuron_type,
                            swc_path=str(swc_path),
                            connectome_key=connectome_key,
                        )
                    )
                )
            except (OSError, ValueError) as exc:
                failures.append(f"{neuron_id}: {exc}")

        if not morphologies:
            self._reset_activity_playback()
            self.result_viewport.clear()
            self.circuit_info.setText("No recorded morphology loaded")
            self.circuit_identity.setText(
                "This result does not include a readable run-packaged SWC."
                if morphology_artifacts
                else "This result does not include run-packaged SWC artifacts."
            )
            if failures:
                self.circuit_identity.setToolTip("\n".join(failures))
            else:
                self.circuit_identity.setToolTip("")
            return

        self.result_viewport.set_morphologies(morphologies)
        large_circuit = len(morphologies) > 64
        display_mode = (
            DISPLAY_MODE_SOMA_POINTS if large_circuit else DISPLAY_MODE_FULL_SKELETONS
        )
        self.result_viewport.set_display_mode(display_mode)
        self.result_soma_points_button.setEnabled(True)
        self.result_full_skeletons_button.setEnabled(True)
        self.result_soma_points_button.setChecked(
            display_mode == DISPLAY_MODE_SOMA_POINTS
        )
        self.result_full_skeletons_button.setChecked(
            display_mode == DISPLAY_MODE_FULL_SKELETONS
        )
        visible_stimulus_ids = stimulus_ids.intersection(
            morphology.record.neuron_id for morphology in morphologies
        )
        self.result_viewport.set_highlights(soma_ids=visible_stimulus_ids)
        self.save_result_visualization_button.setEnabled(True)
        segment_count = sum(len(morphology.segments) for morphology in morphologies)
        self.circuit_info.setText(
            f"{len(morphologies)} recorded neuron(s) · {segment_count:,} SWC segments · "
            "loaded from this saved run"
            + (" · soma-point view selected for responsiveness" if large_circuit else "")
        )
        identity_lines = []
        for morphology in morphologies:
            record = morphology.record
            role = "STIMULATED" if record.neuron_id in visible_stimulus_ids else "recorded"
            identity_lines.append(
                f"● {record.neuron_type} · body {record.neuron_id} · {role}"
            )
        self.circuit_identity.setText("\n".join(identity_lines))
        self.circuit_identity.setToolTip("\n".join(failures))
        self._configure_activity_playback(
            run_root,
            morphologies,
            visible_stimulus_ids,
        )

    def _set_result_circuit_mode(self, mode: str) -> None:
        if not self.result_viewport.neuron_count:
            return
        self.result_viewport.set_display_mode(mode)
        self.result_soma_points_button.setChecked(mode == DISPLAY_MODE_SOMA_POINTS)
        self.result_full_skeletons_button.setChecked(
            mode == DISPLAY_MODE_FULL_SKELETONS
        )
        label = "soma points" if mode == DISPLAY_MODE_SOMA_POINTS else "full skeletons"
        self._activity_frame_changed(self.activity_slider.value())
        self.status_message.emit(f"Recorded circuit view changed to {label}")

    def _configure_activity_playback(
        self,
        run_root: Path,
        morphologies: list[Morphology],
        stimulus_ids: set[str],
    ) -> None:
        self._stop_activity_playback()
        self._activity_tracks = load_activity_flow_tracks(run_root)
        self._activity_distances = {
            morphology.record.neuron_id: segment_distances_from_soma(morphology)
            for morphology in morphologies
        }
        self._activity_stimulus_ids = set(stimulus_ids)
        has_spikes = any(
            any(track.spikes_by_neuron.values()) for track in self._activity_tracks
        )
        enabled = bool(self._activity_tracks and has_spikes)
        self.activity_condition_combo.blockSignals(True)
        self.activity_condition_combo.clear()
        for index, track in enumerate(self._activity_tracks):
            self.activity_condition_combo.addItem(track.label, index)
        self.activity_condition_combo.blockSignals(False)
        self.activity_condition_combo.setEnabled(enabled)
        self.activity_play_button.setEnabled(enabled)
        self.activity_slider.setEnabled(enabled)
        if enabled:
            self.activity_method.setText(
                "Inferred activity flow · soma spikes + SWC path distance"
            )
            self._activity_track_changed(0)
        else:
            self.activity_slider.setRange(0, 0)
            self.activity_time_label.setText("— ms")
            self.result_viewport.clear_activity_flow()
            self.activity_method.setText(
                "Activity playback unavailable · no readable saved soma spikes"
            )

    def _reset_activity_playback(self) -> None:
        self._stop_activity_playback()
        self._activity_tracks = ()
        self._activity_distances = {}
        self._activity_stimulus_ids = set()
        self.activity_condition_combo.clear()
        self.activity_condition_combo.setEnabled(False)
        self.activity_play_button.setEnabled(False)
        self.activity_slider.setRange(0, 0)
        self.activity_slider.setEnabled(False)
        self.activity_time_label.setText("— ms")
        self.voltage_plot.set_time_cursor(None)
        self.activity_method.setText(
            "Activity playback unavailable · load a run with saved soma spikes"
        )
        self.save_result_visualization_button.setEnabled(False)
        self.result_soma_points_button.setEnabled(False)
        self.result_full_skeletons_button.setEnabled(False)

    def _current_activity_track(self) -> ActivityFlowTrack | None:
        index = self.activity_condition_combo.currentData()
        if not isinstance(index, int) or not (0 <= index < len(self._activity_tracks)):
            return None
        return self._activity_tracks[index]

    def _activity_track_changed(self, _index: int) -> None:
        self._stop_activity_playback()
        track = self._current_activity_track()
        if track is None:
            return
        self.activity_slider.blockSignals(True)
        self.activity_slider.setRange(0, len(track.frame_times_ms) - 1)
        self.activity_slider.setValue(0)
        self.activity_slider.blockSignals(False)
        self._activity_frame_changed(0)

    def _activity_frame_changed(self, frame_index: int) -> None:
        track = self._current_activity_track()
        if track is None or not track.frame_times_ms:
            return
        index = min(max(0, int(frame_index)), len(track.frame_times_ms) - 1)
        time_ms = track.frame_times_ms[index]
        self.activity_time_label.setText(f"{time_ms:.2f} ms")
        self.voltage_plot.set_time_cursor(time_ms)
        if self.result_viewport.display_mode == DISPLAY_MODE_SOMA_POINTS:
            self.result_viewport.set_activity_flow({})
            self.result_viewport.set_highlights(
                soma_ids=(
                    self._activity_stimulus_ids
                    | active_spiking_somas(track, time_ms)
                )
            )
            return
        self.result_viewport.set_highlights(soma_ids=self._activity_stimulus_ids)
        self.result_viewport.set_activity_flow(
            active_flow_segments(track, time_ms, self._activity_distances)
        )

    def _toggle_activity_playback(self) -> None:
        if self._activity_timer.isActive():
            self._stop_activity_playback()
            return
        track = self._current_activity_track()
        if track is None or len(track.frame_times_ms) < 2:
            return
        if self.activity_slider.value() >= self.activity_slider.maximum():
            self.activity_slider.setValue(0)
        interval_ms = max(20, round(10_000 / max(1, len(track.frame_times_ms) - 1)))
        self._activity_timer.start(interval_ms)
        self.activity_play_button.setText("❚❚ Pause")

    def _advance_activity_playback(self) -> None:
        next_frame = self.activity_slider.value() + 1
        if next_frame > self.activity_slider.maximum():
            self._stop_activity_playback()
            return
        self.activity_slider.setValue(next_frame)

    def _stop_activity_playback(self) -> None:
        self._activity_timer.stop()
        if hasattr(self, "activity_play_button"):
            self.activity_play_button.setText("▶ Play")

    def _clear_figure_preview(self, message: str) -> None:
        self._pixmap = None
        self.image_label.clear_figure(message)
        self.image_info.setText("No figure available")
        self.full_resolution_button.setEnabled(False)
        self.save_trace_button.setEnabled(False)
        self.voltage_plot.set_traces(())
        self.voltage_2d_button.setEnabled(False)
        self.voltage_3d_button.setEnabled(False)
        self.figure_stack.setCurrentWidget(self.image_scroll)


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
            probes = self.overview.workspace().probe_engines(
                self.overview.python_edit.text(),
                self.overview.arbor_python_edit.text(),
                self.overview.bmtk_python_edit.text(),
            )
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
        self.theme = normalize_theme(self.settings.value("theme", DARK_THEME))
        self._apply_theme(self.theme, persist=False)
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
        brand_row = QHBoxLayout()
        brand_row.setContentsMargins(0, 0, 0, 0)
        brand_row.setSpacing(8)
        brand = QLabel("Digifly")
        brand.setObjectName("Brand")
        brand_row.addWidget(brand)
        brand_row.addStretch(1)
        self.theme_toggle = QToolButton()
        self.theme_toggle.setObjectName("ThemeToggle")
        self.theme_toggle.setCheckable(True)
        self.theme_toggle.setChecked(self.theme == LIGHT_THEME)
        self.theme_toggle.setAccessibleName("Color theme")
        self._update_theme_toggle_text()
        self.theme_toggle.toggled.connect(self._theme_toggled)
        brand_row.addWidget(self.theme_toggle, alignment=Qt.AlignmentFlag.AlignVCenter)
        subbrand = QLabel("SCIENTIFIC WORKSTATION")
        subbrand.setObjectName("Eyebrow")
        sidebar_layout.addLayout(brand_row)
        sidebar_layout.addWidget(subbrand)
        sidebar_layout.addSpacing(23)
        self.nav_buttons: list[QPushButton] = []
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_group.idToggled.connect(self._navigation_toggled)
        for index, (label, icon) in enumerate(
            (
                ("Workspace", "⌂"),
                ("Data Library", "▤"),
                ("Circuit Builder", "⌁"),
                ("Experiment Builder", "◉"),
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
        self.data_library_page = DataLibraryPage()
        self.circuit_builder_page = CircuitBuilderPage(self.overview_page)
        self.experiment_page = ExperimentBuilderPage(
            output_root=self.overview_page.output_edit.text(),
            neuron_runtime=self.overview_page.python_edit.text(),
            arbor_runtime=self.overview_page.arbor_python_edit.text(),
            bmtk_runtime=self.overview_page.bmtk_python_edit.text(),
            digifly_public_root=self.overview_page.workspace_edit.text(),
        )
        self.overview_page.output_edit.textChanged.connect(
            self.experiment_page.set_output_root
        )
        self.overview_page.python_edit.textChanged.connect(
            self.experiment_page.set_neuron_runtime
        )
        self.overview_page.arbor_python_edit.textChanged.connect(
            self.experiment_page.set_arbor_runtime
        )
        self.overview_page.bmtk_python_edit.textChanged.connect(
            self.experiment_page.set_bmtk_runtime
        )
        self.overview_page.workspace_edit.textChanged.connect(
            self.experiment_page.set_digifly_public_root
        )
        self.results_page = ResultsPage(self.overview_page, self.experiment_page)
        self.engines_page = EnginesPage(self.overview_page)
        for page in (
            self.overview_page,
            self.data_library_page,
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
        self.data_library_page.status_message.connect(self.statusBar().showMessage)
        self.data_library_page.sources_changed.connect(
            self.circuit_builder_page.refresh_connectomes
        )
        self.circuit_builder_page.managed_data_changed.connect(
            self.data_library_page.refresh
        )
        self.circuit_builder_page.circuit_changed.connect(
            self._sync_experiment_circuit
        )
        self.data_library_page.quality_review_requested.connect(
            self.circuit_builder_page.review_recent_imports
        )
        self.experiment_page.status_message.connect(self.statusBar().showMessage)
        self.results_page.status_message.connect(self.statusBar().showMessage)
        self.experiment_page.result_ready.connect(
            lambda _result: self.show_page(self.pages.indexOf(self.results_page))
        )
        self._sync_experiment_circuit(self.circuit_builder_page.circuit_spec())
        self._last_editor_page: QWidget = self.circuit_builder_page
        self._project_workflow: str | None = None
        self._build_menu()
        self._restore_settings()
        for label in self.findChildren(QLabel):
            make_label_copyable(label)
        self.show_page(0)

    def _theme_toggled(self, use_light_theme: bool) -> None:
        self._apply_theme(LIGHT_THEME if use_light_theme else DARK_THEME)

    def _apply_theme(self, theme: object, *, persist: bool = True) -> None:
        self.theme = normalize_theme(theme)
        application = QApplication.instance()
        if application is not None:
            application.setProperty("digiflyTheme", self.theme)
            application.setStyleSheet(style_for_theme(self.theme))
        if hasattr(self, "theme_toggle"):
            checked = self.theme == LIGHT_THEME
            if self.theme_toggle.isChecked() != checked:
                self.theme_toggle.blockSignals(True)
                self.theme_toggle.setChecked(checked)
                self.theme_toggle.blockSignals(False)
            self._update_theme_toggle_text()
        for widget in self.findChildren(QWidget):
            if widget.objectName() in {"CircuitViewport", "StimulusPreview"}:
                widget.update()
        if persist:
            self.settings.setValue("theme", self.theme)
            self.settings.sync()

    def _update_theme_toggle_text(self) -> None:
        if self.theme == LIGHT_THEME:
            icon = "☀"
            tooltip = "Switch to dark theme"
            description = "Light theme active. Activate to switch to dark theme."
        else:
            icon = "☾"
            tooltip = "Switch to light theme"
            description = "Dark theme active. Activate to switch to light theme."
        self.theme_toggle.setText(icon)
        self.theme_toggle.setToolTip(tooltip)
        self.theme_toggle.setAccessibleDescription(description)

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
        if current is self.experiment_page:
            self._sync_experiment_circuit(self.circuit_builder_page.circuit_spec())
        if current is self.engines_page:
            self.engines_page.refresh()
        if current is self.data_library_page:
            self.data_library_page.refresh()

    def _sync_experiment_circuit(self, spec: CircuitSpec) -> None:
        self.experiment_page.set_circuit_snapshot(
            spec,
            self.circuit_builder_page.morphology_snapshot(),
        )

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
        self.experiment_page.reset()
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
            circuit_spec = CircuitSpec.from_dict(project.circuit or {})
            experiment_spec = ExperimentSpec.from_dict(project.experiment or {})
            self.circuit_builder_page.refresh_connectomes()
            self.circuit_builder_page.set_selected_engine_key(project.selected_engine)
            self.circuit_builder_page.set_circuit_spec(circuit_spec)
            if circuit_spec.neuron_ids:
                self.circuit_builder_page.load_saved_assets()
            self.experiment_page.set_config(experiment_spec)
            self._sync_experiment_circuit(circuit_spec)
            if project.selected_workflow == CIRCUIT_BUILDER_WORKFLOW:
                self._last_editor_page = self.circuit_builder_page
                target_page = self.circuit_builder_page
            elif project.selected_workflow == EXPERIMENT_BUILDER_WORKFLOW:
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
        try:
            circuit = self.circuit_builder_page.circuit_spec().to_dict()
            experiment = self.experiment_page.config().to_dict()
        except Exception as exc:
            QMessageBox.warning(self, "Invalid project", f"Fix the project controls before saving:\n{exc}")
            return
        if editor is self.circuit_builder_page:
            selected_workflow = CIRCUIT_BUILDER_WORKFLOW
        else:
            selected_workflow = EXPERIMENT_BUILDER_WORKFLOW
        selected_engine = self.circuit_builder_page.selected_engine_key()
        suggested_name = "experiment.digifly.json"
        destination = self.current_project_path
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
            circuit=circuit,
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
        saved_workspace = self.settings.value("workspace_root")
        saved_output = self.settings.value("output_root")
        saved_worker_python = self.settings.value("neuron_python")
        saved_arbor_python = self.settings.value("arbor_python")
        saved_bmtk_python = self.settings.value("bmtk_python")
        try:
            profile = load_default_profile()
        except (OSError, ValueError):
            profile = None
        profile_workspace: Path | None = None
        profile_output: Path | None = None
        profile_neuron: Path | None = None
        profile_arbor: Path | None = None
        profile_bmtk: Path | None = None
        if profile is not None:
            profile_workspace = profile.workspace_root
            profile_output = profile.output_root
            profile_neuron = profile.runtime_path(ResourceKind.NEURON_RUNTIME)
            profile_arbor = profile.runtime_path(ResourceKind.ARBOR_RUNTIME)
            profile_bmtk = profile.runtime_path(ResourceKind.BMTK_RUNTIME)

        workspace = _first_valid_path(
            saved_workspace,
            profile_workspace,
            validator=_valid_workspace_path,
        )
        output = _first_valid_path(
            saved_output,
            profile_output,
            validator=_valid_output_path,
        )
        # Runtime-setup choices are verified before they enter the versioned
        # machine profile. Prefer those bindings over stale GUI preferences.
        worker_python = _first_valid_path(
            profile_neuron,
            saved_worker_python,
            validator=_valid_runtime_path,
            preserve_final_symlink=True,
        )
        arbor_python = _first_valid_path(
            profile_arbor,
            saved_arbor_python,
            validator=_valid_runtime_path,
            preserve_final_symlink=True,
        )
        if not arbor_python:
            arbor_python = bundled_arbor_python()
        bmtk_python = _first_valid_path(
            profile_bmtk,
            saved_bmtk_python,
            validator=_valid_runtime_path,
            preserve_final_symlink=True,
        )
        # Import only read-only input/runtime bindings from the legacy app on
        # first launch. Workstation outputs deliberately remain in their new
        # default root so the two applications cannot overwrite each other's
        # jobs, caches, or results.
        if not workspace:
            workspace = _first_valid_path(
                self.legacy_settings.value("workspace_root"),
                validator=_valid_workspace_path,
            )
        if not worker_python:
            worker_python = _first_valid_path(
                self.legacy_settings.value("neuron_python"),
                validator=_valid_runtime_path,
                preserve_final_symlink=True,
            )
        if workspace:
            self.overview_page.workspace_edit.setText(str(workspace))
        if output:
            self.overview_page.output_edit.setText(str(output))
        if worker_python:
            self.overview_page.python_edit.setText(str(worker_python))
        if arbor_python:
            self.overview_page.arbor_python_edit.setText(str(arbor_python))
        if bmtk_python:
            self.overview_page.bmtk_python_edit.setText(str(bmtk_python))
        if (
            (saved_workspace and not _valid_workspace_path(saved_workspace))
            or (saved_output and not _valid_output_path(saved_output))
        ) and profile is not None:
            self.overview_page.doctor_summary.setText(
                "Recovered unavailable saved paths from the machine resource profile."
            )

    def closeEvent(self, event: Any) -> None:
        if self.data_library_page.import_in_progress:
            QMessageBox.warning(
                self,
                "A data import is still running",
                "The app will remain open until staging, validation, and registration finish. "
                "An incomplete bundle is never promoted into the Data Library.",
            )
            event.ignore()
            return
        active_process = getattr(self.experiment_page, "_process", None)
        if active_process is not None:
            QMessageBox.warning(
                self,
                "A scientific worker is still running",
                "The GUI will remain open while the worker is active. Return to Experiment Builder and use Stop safely "
                "so the cancellation request and partial-artifact state are recorded.",
            )
            event.ignore()
            return
        self.settings.setValue("workspace_root", self.overview_page.workspace_edit.text())
        self.settings.setValue("output_root", self.overview_page.output_edit.text())
        self.settings.setValue("neuron_python", self.overview_page.python_edit.text())
        self.settings.setValue("arbor_python", self.overview_page.arbor_python_edit.text())
        self.settings.setValue("bmtk_python", self.overview_page.bmtk_python_edit.text())
        super().closeEvent(event)


def launch(argv: list[str] | None = None) -> int:
    application = QApplication(argv or [])
    application.setApplicationName(APPLICATION_NAME)
    application.setOrganizationName(ORGANIZATION_NAME)
    application.setApplicationVersion(__version__)
    icon_path = package_root() / "assets" / "digifly_icon.png"
    if icon_path.is_file():
        application.setWindowIcon(QIcon(str(icon_path)))
    application.setStyle("Fusion")
    initial_theme = normalize_theme(
        QSettings(ORGANIZATION_NAME, APPLICATION_NAME).value("theme", DARK_THEME)
    )
    application.setProperty("digiflyTheme", initial_theme)
    application.setStyleSheet(style_for_theme(initial_theme))
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


def _reveal(raw: str) -> None:
    path = Path(raw).expanduser()
    target = path if path.exists() else path.parent
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))
