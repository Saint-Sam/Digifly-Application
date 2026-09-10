from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from PySide6.QtCore import QProcess, QProcessEnvironment, QTimer, Qt, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from digifly_app.core.circuit import CircuitSpec
from digifly_app.core.experiment import (
    ConditionSpec,
    ExperimentSpec,
    RecordingSpec,
    StimulusSpec,
)
from digifly_app.core.jobs import JobStore
from digifly_app.core.models import CheckState
from digifly_app.core.morphology import Morphology, locate_soma
from digifly_app.core.process_environment import EXTERNAL_PYTHON_ENV_REMOVE
from digifly_app.engines.generic_experiment import (
    GenericExperimentAdapter,
    load_generic_experiment_result,
    update_run_manifest_state,
)
from .circuit_viewport import (
    DISPLAY_MODE_FULL_SKELETONS,
    CircuitViewport,
)
from .stimulus_preview import StimulusPreview
from .snapshot import save_image_with_dialog
from .widgets import (
    Card,
    CollapsibleSection,
    HelpButton,
    HelpLabel,
    StatusPill,
    make_label_copyable,
)


CANCEL_REQUEST_FILENAME = "cancel.requested"
CANCEL_MARKER_GRACE_MS = 500
CANCEL_FORCE_KILL_MS = 7_000


def _write_cancellation_request(run_directory: str | Path) -> Path:
    """Atomically publish a run-owned, credential-free cancellation marker."""

    root = Path(run_directory).expanduser().resolve()
    marker = root / CANCEL_REQUEST_FILENAME
    temporary = root / f".{CANCEL_REQUEST_FILENAME}.tmp"
    try:
        temporary.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "state": "cancel_requested",
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(marker)
    finally:
        if temporary.exists():
            temporary.unlink()
    return marker


EXPERIMENT_SETTING_HELP: dict[str, str] = {
    "experiment_name": (
        "A descriptive name saved with the experiment and used to identify its runs and results."
    ),
    "template": (
        "Choose a starting configuration. Blank keeps the generic defaults; templates populate app-owned controls without running a notebook."
    ),
    "engine": (
        "The simulator backend expected to execute this experiment after its execution adapter passes validation."
    ),
    "duration": "Total simulated biological time from start to finish, in milliseconds.",
    "integration_dt": (
        "The simulator integration time step. Smaller values resolve faster dynamics but require more computation."
    ),
    "initial_voltage": (
        "Starting membrane potential assigned before the simulation begins, in millivolts."
    ),
    "temperature": (
        "Model temperature in degrees Celsius; temperature-sensitive mechanisms may change their kinetics."
    ),
    "random_seed": (
        "Initializes pseudorandom simulator processes. Reusing the same seed with the same experiment reproduces the same random draws; deterministic models are unchanged."
    ),
    "repetitions": (
        "Number of times to repeat the complete experiment, useful for stochastic comparisons and replicates."
    ),
    "workers": (
        "Maximum worker threads requested from the execution backend for this experiment."
    ),
    "stimulus_targets": (
        "Neuron IDs or exact neuron types that receive this stimulus, separated by spaces or commas. Types such as DNp01 or TTMn resolve to every matching neuron in the loaded circuit. Leave blank to target every circuit neuron."
    ),
    "stimulus_region": (
        "Morphological region where the execution adapter will place the injected current."
    ),
    "waveform": "Temporal shape of the injected current: pulse train, single square step, or ramp.",
    "amplitude": "Magnitude of the injected current in nanoamperes.",
    "delay": "Time from simulation start until the first stimulus begins.",
    "pulse_width": "Duration of each pulse, square step, or ramp in milliseconds.",
    "frequency": (
        "Pulse-train rate in hertz. Higher values reduce the time between successive pulses."
    ),
    "pulse_count": (
        "Number of pulses scheduled in the train. The simulation duration must be long enough to contain them."
    ),
    "control_condition": (
        "Include an unmodified reference run for comparison with runtime manipulations."
    ),
    "manipulation_condition": (
        "Include a second run whose connectivity, neurons, stimulus, or mechanisms can be changed at runtime."
    ),
    "comparison_name": "Name used to identify the runtime manipulation condition in outputs.",
    "electrical_edges": (
        "Keep gap-junction or other electrical connections active in the manipulation condition."
    ),
    "chemical_edges": (
        "Keep chemical synaptic connections active in the manipulation condition."
    ),
    "disabled_neurons": (
        "Neuron IDs to disable only for the manipulation run, separated by spaces or commas."
    ),
    "stimulus_scale": (
        "Multiplier applied to the primary stimulus amplitude in the manipulation condition."
    ),
    "mechanism_scales": (
        "Optional JSON map of mechanism names to runtime multipliers, for example {\"para\": 0.5}."
    ),
    "recording_targets": (
        "Neuron IDs to record, separated by spaces or commas. Leave blank to record every circuit neuron."
    ),
    "recording_region": "Morphological region from which signals will be sampled.",
    "signals": "Choose which simulator signals and events are saved for analysis.",
    "record_voltage": "Record membrane voltage traces from the selected neurons and region.",
    "detect_spikes": "Detect and save spike-event times using the configured voltage threshold.",
    "sample_dt": "Time between recorded voltage samples, in milliseconds.",
    "spike_threshold": (
        "Membrane voltage crossing used to register a spike event, in millivolts."
    ),
    "make_plots": "Generate the standard Digifly plots after a successful experiment run.",
}


def _double_spin(
    minimum: float,
    maximum: float,
    value: float,
    *,
    decimals: int = 4,
    step: float = 0.1,
    suffix: str = "",
) -> QDoubleSpinBox:
    editor = QDoubleSpinBox()
    editor.setRange(minimum, maximum)
    editor.setDecimals(decimals)
    editor.setSingleStep(step)
    editor.setKeyboardTracking(False)
    editor.setValue(value)
    if suffix:
        editor.setSuffix(suffix)
    return editor


def _ids_text(values: tuple[str, ...]) -> str:
    return ", ".join(values)


def _parse_ids(value: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            token
            for token in value.replace(",", " ").replace(";", " ").split()
            if token
        )
    )


def _section_title(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("SectionTitle")
    return label


def _proximal_axon_segment_ids(morphology: Morphology) -> set[int]:
    """Return a small visual AIS proxy from the nearest type-2 SWC segments."""

    axonal = [segment for segment in morphology.segments if segment.swc_type == 2]
    if not axonal:
        return set()
    soma = locate_soma(morphology).point

    def distance_squared(segment: Any) -> float:
        return min(
            sum((value - origin) ** 2 for value, origin in zip(point, soma))
            for point in (segment.parent, segment.child)
        )

    axonal.sort(key=distance_squared)
    preview_count = max(1, min(16, (len(axonal) + 19) // 20))
    return {segment.child_id for segment in axonal[:preview_count]}


class ExperimentBuilderPage(QWidget):
    """Build a run protocol around a CircuitSpec without importing a notebook."""

    status_message = Signal(str)
    experiment_changed = Signal(object)
    result_ready = Signal(object)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        output_root: str | Path | None = None,
        neuron_runtime: str | Path | None = None,
        arbor_runtime: str | Path | None = None,
        bmtk_runtime: str | Path | None = None,
        digifly_public_root: str | Path | None = None,
    ):
        super().__init__(parent)
        self._configured_output_root = Path(
            output_root
            if output_root is not None
            else Path.home() / "Digifly Workstation Workspace" / "runs"
        ).expanduser()
        self._circuit = CircuitSpec()
        self._morphologies: tuple[Morphology, ...] = ()
        self._morphology_signature: tuple[tuple[str, str, int], ...] = ()
        self._restoring = False
        self._runtime_paths = {
            "neuron": str(neuron_runtime or ""),
            "arbor": str(arbor_runtime or ""),
            "bmtk": str(bmtk_runtime or ""),
        }
        self._digifly_public_root = str(digifly_public_root or "")
        self._process: QProcess | None = None
        self._job_dir: Path | None = None
        self._job_store: JobStore | None = None
        self._plan = None
        self._run_output_buffer = ""
        self._cancel_requested = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        content = QWidget()
        content.setObjectName("PageContent")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(30, 26, 30, 32)
        layout.setSpacing(16)

        eyebrow = QLabel("RUN PROTOCOL · NOTEBOOK-INDEPENDENT")
        eyebrow.setObjectName("Eyebrow")
        title = QLabel("Experiment Builder")
        title.setObjectName("PageTitle")
        detail = QLabel(
            "Take the immutable circuit assembled in Circuit Builder and define how it is stimulated, manipulated, recorded, and executed. Network construction remains upstream."
        )
        detail.setObjectName("Muted")
        detail.setWordWrap(True)
        layout.addWidget(eyebrow)
        layout.addWidget(title)
        layout.addWidget(detail)

        workspace = QGridLayout()
        workspace.setHorizontalSpacing(16)
        workspace.setVerticalSpacing(0)

        self.selector_panel = QWidget()
        self.selector_panel.setObjectName("ExperimentSelectorPanel")
        self.selector_panel.setMinimumWidth(400)
        self.selector_panel.setMaximumWidth(520)
        selector_layout = QVBoxLayout(self.selector_panel)
        selector_layout.setContentsMargins(0, 0, 0, 0)
        selector_layout.setSpacing(9)
        self.selector_sections: dict[str, CollapsibleSection] = {}
        self.help_buttons: dict[str, HelpButton] = {}
        self.help_labels: dict[str, HelpLabel] = {}

        def add_help_row(
            form: QFormLayout,
            key: str,
            label: str,
            field: QWidget,
        ) -> HelpLabel:
            help_text = EXPERIMENT_SETTING_HELP[key]
            help_label = HelpLabel(label, help_text, key=key, buddy=field)
            self.help_labels[key] = help_label
            self.help_buttons[key] = help_label.help_button
            form.addRow(help_label, field)
            return help_label

        def helped_control(control: QWidget, key: str) -> QWidget:
            help_text = EXPERIMENT_SETTING_HELP[key]
            setting_name = str(getattr(control, "text", lambda: key)())
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(5)
            row_layout.addWidget(control)
            help_button = HelpButton(setting_name, help_text, key=key)
            self.help_buttons[key] = help_button
            row_layout.addWidget(
                help_button,
                alignment=Qt.AlignmentFlag.AlignVCenter,
            )
            row_layout.addStretch(1)
            return row

        def add_selector(
            key: str, title: str, *, expanded: bool = False
        ) -> QVBoxLayout:
            section = CollapsibleSection(
                title,
                key,
                expanded=expanded,
                object_name_prefix="ExperimentSection",
            )
            self.selector_sections[key] = section
            selector_layout.addWidget(section)
            return section.content_layout

        identity_layout = add_selector(
            "experiment_identity", "Experiment identity", expanded=True
        )
        identity_form = QFormLayout()
        identity_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        identity_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        self.name_edit = QLineEdit()
        self.name_edit.setObjectName("ExperimentName")
        self.template_combo = QComboBox()
        self.template_combo.setObjectName("ExperimentTemplate")
        self.template_combo.addItem("Blank experiment", "blank")
        self.template_combo.addItem(
            "Pulse-train comparison · app-owned Escape-SIZ translation",
            "pulse_train_comparison",
        )
        self.engine_combo = QComboBox()
        self.engine_combo.setObjectName("ExperimentEngine")
        self.engine_combo.addItem("Arbor", "arbor")
        self.engine_combo.addItem("NEURON", "neuron")
        self.engine_combo.addItem("BMTK / SONATA", "bmtk")
        self.name_availability = QLabel()
        self.name_availability.setObjectName("ExperimentNameAvailability")
        self.name_availability.setAccessibleName("Experiment name availability")
        name_help_label = add_help_row(
            identity_form,
            "experiment_name",
            "Name",
            self.name_edit,
        )
        name_help_label.add_trailing_widget(self.name_availability)
        add_help_row(identity_form, "template", "Template", self.template_combo)
        add_help_row(identity_form, "engine", "Execution engine", self.engine_combo)
        identity_layout.addLayout(identity_form)

        circuit_layout = add_selector("circuit_input", "Circuit input")
        circuit_top = QHBoxLayout()
        circuit_top.addStretch(1)
        self.circuit_state = StatusPill(CheckState.WARNING, "NO CIRCUIT")
        circuit_top.addWidget(self.circuit_state)
        circuit_layout.addLayout(circuit_top)
        self.circuit_summary = QLabel(
            "Assemble and load neurons in Circuit Builder. This page will receive a read-only snapshot automatically."
        )
        self.circuit_summary.setObjectName("Muted")
        self.circuit_summary.setWordWrap(True)
        circuit_layout.addWidget(self.circuit_summary)
        self.circuit_detail = QLabel("No circuit snapshot attached")
        self.circuit_detail.setWordWrap(True)
        circuit_layout.addWidget(self.circuit_detail)

        stimulus_layout = add_selector("primary_stimulus", "Primary stimulus")
        run_layout = add_selector("simulation_compute", "Simulation & compute")
        run_form = QFormLayout()
        run_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        run_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        self.duration = _double_spin(0.01, 10_000_000.0, 110.0, suffix=" ms")
        self.integration_dt = _double_spin(
            0.000001, 1000.0, 0.01, decimals=6, step=0.001, suffix=" ms"
        )
        self.initial_voltage = _double_spin(
            -300.0, 300.0, -65.0, decimals=3, step=1.0, suffix=" mV"
        )
        self.temperature = _double_spin(-273.0, 200.0, 6.3, suffix=" °C")
        self.seed = QSpinBox()
        self.seed.setRange(0, 2_147_483_647)
        self.seed.setValue(1)
        self.repetitions = QSpinBox()
        self.repetitions.setRange(1, 1_000_000)
        self.repetitions.setValue(1)
        self.workers = QSpinBox()
        self.workers.setRange(1, 256)
        self.workers.setValue(1)
        add_help_row(run_form, "duration", "Simulation duration", self.duration)
        add_help_row(run_form, "integration_dt", "Integration step", self.integration_dt)
        add_help_row(run_form, "initial_voltage", "Initial voltage", self.initial_voltage)
        add_help_row(run_form, "temperature", "Temperature", self.temperature)
        add_help_row(run_form, "random_seed", "Random seed", self.seed)
        add_help_row(run_form, "repetitions", "Repetitions", self.repetitions)
        add_help_row(run_form, "workers", "Workers / threads", self.workers)
        run_layout.addLayout(run_form)

        stimulus_hint = QLabel(
            "Enter neuron IDs, exact neuron types such as DNp01 or TTMn, or leave blank for every circuit neuron. The execution adapter resolves simulator locations after validation."
        )
        stimulus_hint.setObjectName("Muted")
        stimulus_hint.setWordWrap(True)
        stimulus_layout.addWidget(stimulus_hint)
        stimulus_form = QFormLayout()
        stimulus_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        stimulus_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        self.stimulus_targets = QLineEdit()
        self.stimulus_targets.setPlaceholderText(
            "all circuit neurons · IDs or types (DNp01, TTMn)"
        )
        self.stimulus_region = QComboBox()
        self.stimulus_region.addItem("Soma", "soma")
        self.stimulus_region.addItem("AIS", "ais")
        self.stimulus_region.addItem("Selected SWC compartments", "selected_compartments")
        self.stimulus_region.addItem("All compartments", "all")
        self.waveform_combo = QComboBox()
        self.waveform_combo.addItem("Pulse train", "pulse_train")
        self.waveform_combo.addItem("Single step", "step")
        self.waveform_combo.addItem("Ramp", "ramp")
        self.amplitude = _double_spin(0.0, 100_000.0, 0.9, decimals=8, suffix=" nA")
        self.delay = _double_spin(0.0, 10_000_000.0, 5.0, suffix=" ms")
        self.pulse_width = _double_spin(
            0.000001, 10_000_000.0, 0.4, decimals=6, suffix=" ms"
        )
        self.frequency = _double_spin(0.000001, 1_000_000.0, 100.0, suffix=" Hz")
        self.pulse_count = QSpinBox()
        self.pulse_count.setRange(1, 1_000_000)
        self.pulse_count.setValue(10)
        for live_control in (
            self.duration,
            self.seed,
            self.amplitude,
            self.delay,
            self.pulse_width,
            self.frequency,
            self.pulse_count,
        ):
            live_control.setKeyboardTracking(True)
        add_help_row(
            stimulus_form,
            "stimulus_targets",
            "Target IDs or types",
            self.stimulus_targets,
        )
        add_help_row(stimulus_form, "stimulus_region", "Target region", self.stimulus_region)
        add_help_row(stimulus_form, "waveform", "Waveform", self.waveform_combo)
        add_help_row(stimulus_form, "amplitude", "Amplitude", self.amplitude)
        add_help_row(stimulus_form, "delay", "Start delay", self.delay)
        add_help_row(
            stimulus_form,
            "pulse_width",
            "Pulse / step duration",
            self.pulse_width,
        )
        add_help_row(stimulus_form, "frequency", "Frequency", self.frequency)
        add_help_row(stimulus_form, "pulse_count", "Pulse count", self.pulse_count)
        stimulus_layout.addLayout(stimulus_form)

        condition_layout = add_selector(
            "runtime_conditions", "Conditions & runtime manipulations"
        )
        condition_hint = QLabel(
            "Conditions change the run while preserving the Circuit Builder network definition."
        )
        condition_hint.setObjectName("Muted")
        condition_hint.setWordWrap(True)
        condition_layout.addWidget(condition_hint)
        self.control_enabled = QCheckBox("Run control condition")
        self.control_enabled.setChecked(True)
        condition_layout.addWidget(
            helped_control(self.control_enabled, "control_condition")
        )
        self.manipulation_enabled = QCheckBox("Run comparison / manipulation condition")
        self.manipulation_enabled.setChecked(True)
        condition_layout.addWidget(
            helped_control(self.manipulation_enabled, "manipulation_condition")
        )
        condition_form = QFormLayout()
        condition_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        condition_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        self.manipulation_name = QLineEdit("Gap junctions disabled")
        self.manip_gap_enabled = QCheckBox("Gap junctions enabled")
        self.manip_gap_enabled.setChecked(False)
        self.manip_chemical_enabled = QCheckBox("Chemical synapses enabled")
        self.manip_chemical_enabled.setChecked(True)
        self.disabled_neurons = QLineEdit()
        self.disabled_neurons.setPlaceholderText("optional neuron IDs to ablate")
        self.stimulus_scale = _double_spin(0.0, 1_000_000.0, 1.0, decimals=6)
        self.mechanism_scales = QLineEdit("{}")
        self.mechanism_scales.setPlaceholderText('{"para": 0.5}')
        add_help_row(
            condition_form,
            "comparison_name",
            "Comparison name",
            self.manipulation_name,
        )
        add_help_row(
            condition_form,
            "electrical_edges",
            "Electrical edges",
            self.manip_gap_enabled,
        )
        add_help_row(
            condition_form,
            "chemical_edges",
            "Chemical edges",
            self.manip_chemical_enabled,
        )
        add_help_row(
            condition_form,
            "disabled_neurons",
            "Disabled neurons",
            self.disabled_neurons,
        )
        add_help_row(
            condition_form,
            "stimulus_scale",
            "Stimulus multiplier",
            self.stimulus_scale,
        )
        add_help_row(
            condition_form,
            "mechanism_scales",
            "Mechanism scales (JSON)",
            self.mechanism_scales,
        )
        condition_layout.addLayout(condition_form)

        recording_layout = add_selector("recording_outputs", "Recording & outputs")
        recording_form = QFormLayout()
        recording_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        recording_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        self.recording_targets = QLineEdit()
        self.recording_targets.setPlaceholderText("all circuit neurons")
        self.recording_region = QComboBox()
        self.recording_region.addItem("Soma", "soma")
        self.recording_region.addItem("All compartments", "all")
        self.recording_region.addItem("AIS", "ais")
        self.recording_region.addItem("Selected SWC compartments", "selected_compartments")
        self.record_voltage = QCheckBox("Membrane voltage")
        self.record_voltage.setChecked(True)
        self.detect_spikes = QCheckBox("Spike events")
        self.detect_spikes.setChecked(True)
        outputs = QWidget()
        output_row = QHBoxLayout(outputs)
        output_row.setContentsMargins(0, 0, 0, 0)
        output_row.addWidget(helped_control(self.record_voltage, "record_voltage"))
        output_row.addWidget(helped_control(self.detect_spikes, "detect_spikes"))
        self.sample_dt = _double_spin(
            0.000001, 1_000_000.0, 0.05, decimals=6, step=0.01, suffix=" ms"
        )
        self.spike_threshold = _double_spin(
            -300.0, 300.0, -20.0, decimals=3, suffix=" mV"
        )
        self.make_plots = QCheckBox("Generate standard plots")
        self.make_plots.setChecked(True)
        add_help_row(
            recording_form,
            "recording_targets",
            "Target neuron IDs",
            self.recording_targets,
        )
        add_help_row(
            recording_form,
            "recording_region",
            "Target region",
            self.recording_region,
        )
        add_help_row(recording_form, "signals", "Signals", outputs)
        add_help_row(recording_form, "sample_dt", "Sample interval", self.sample_dt)
        add_help_row(
            recording_form,
            "spike_threshold",
            "Spike threshold",
            self.spike_threshold,
        )
        add_help_row(recording_form, "make_plots", "Analysis", self.make_plots)
        recording_layout.addLayout(recording_form)
        selector_layout.addStretch(1)

        visual_panel = QWidget()
        visual_layout = QVBoxLayout(visual_panel)
        visual_layout.setContentsMargins(0, 0, 0, 0)
        visual_layout.setSpacing(14)

        target_preview_card = Card()
        target_preview_layout = QVBoxLayout(target_preview_card)
        target_preview_layout.setContentsMargins(12, 12, 12, 12)
        target_preview_layout.setSpacing(7)
        target_preview_header = QHBoxLayout()
        target_preview_header.addWidget(
            _section_title("Primary stimulus · target view")
        )
        target_preview_header.addStretch(1)
        self.save_target_visualization_button = QPushButton("Save snapshot…")
        self.save_target_visualization_button.setObjectName(
            "SaveTargetVisualizationButton"
        )
        self.save_target_visualization_button.setToolTip(
            "Save the current target-region camera and highlight as a high-resolution PNG"
        )
        self.save_target_visualization_button.clicked.connect(
            lambda: self.target_region_viewport.save_high_resolution_snapshot(
                "digifly-primary-stimulus-target.png"
            )
        )
        target_preview_header.addWidget(self.save_target_visualization_button)
        target_preview_layout.addLayout(target_preview_header)
        self.target_region_visualization_label = QLabel(
            "Target region: Soma · load a circuit morphology to see the highlighted target."
        )
        self.target_region_visualization_label.setObjectName(
            "TargetRegionVisualizationLabel"
        )
        self.target_region_visualization_label.setWordWrap(True)
        target_preview_layout.addWidget(self.target_region_visualization_label)
        target_controls = QLabel(
            "Bright yellow marks the current target region. Controls match Circuit Builder: "
            "drag to rotate, Shift-drag or middle-drag to move, wheel to zoom, "
            "right-click to center/frame, and R to reset."
        )
        target_controls.setObjectName("Muted")
        target_controls.setWordWrap(True)
        target_preview_layout.addWidget(target_controls)
        self.target_region_viewport = CircuitViewport(camera_only=True)
        self.target_region_viewport.setObjectName("ExperimentTargetRegionViewport")
        self.target_region_viewport.setAccessibleName(
            "Primary stimulus target-region visualization"
        )
        self.target_region_viewport.set_display_mode(DISPLAY_MODE_FULL_SKELETONS)
        self.target_region_viewport.status_message.connect(self.status_message)
        target_preview_layout.addWidget(self.target_region_viewport)
        visual_layout.addWidget(target_preview_card)

        stimulus_preview_card = Card()
        stimulus_preview_layout = QVBoxLayout(stimulus_preview_card)
        stimulus_preview_layout.setContentsMargins(17, 14, 17, 16)
        stimulus_preview_layout.setSpacing(9)
        stimulus_preview_header = QHBoxLayout()
        stimulus_preview_header.addWidget(_section_title("Live stimulus preview"))
        stimulus_preview_header.addStretch(1)
        self.save_stimulus_visualization_button = QPushButton("Save snapshot…")
        self.save_stimulus_visualization_button.setObjectName(
            "SaveStimulusVisualizationButton"
        )
        self.save_stimulus_visualization_button.setToolTip(
            "Save the current stimulus waveform as a high-resolution PNG"
        )
        self.save_stimulus_visualization_button.clicked.connect(
            self._save_stimulus_visualization
        )
        stimulus_preview_header.addWidget(self.save_stimulus_visualization_button)
        stimulus_preview_layout.addLayout(stimulus_preview_header)
        stimulus_preview_hint = QLabel(
            "This simulator-independent trace redraws as the simulation window or primary stimulus controls change."
        )
        stimulus_preview_hint.setObjectName("Muted")
        stimulus_preview_hint.setWordWrap(True)
        stimulus_preview_layout.addWidget(stimulus_preview_hint)
        self.stimulus_preview = StimulusPreview()
        stimulus_preview_layout.addWidget(self.stimulus_preview)
        self.stimulus_preview_summary = QLabel()
        self.stimulus_preview_summary.setObjectName("StimulusPreviewSummary")
        self.stimulus_preview_summary.setWordWrap(True)
        stimulus_preview_layout.addWidget(self.stimulus_preview_summary)
        visual_layout.addWidget(stimulus_preview_card)

        action_card = Card()
        action_layout = QVBoxLayout(action_card)
        action_layout.setContentsMargins(17, 14, 17, 15)
        action_layout.setSpacing(9)
        action_row = QHBoxLayout()
        self.validate_button = QPushButton("Validate experiment draft")
        self.validate_button.setProperty("primary", True)
        self.validate_button.clicked.connect(self.validate_draft)
        self.run_button = QPushButton("Run experiment")
        self.run_button.setToolTip(
            "Preflight and launch this experiment in the selected external simulator"
        )
        self.run_button.clicked.connect(self.request_run)
        self.cancel_button = QPushButton("Stop safely")
        self.cancel_button.setProperty("danger", True)
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_run)
        action_row.addWidget(self.validate_button)
        action_row.addWidget(self.run_button)
        action_row.addWidget(self.cancel_button)
        action_row.addStretch(1)
        self.validation_state = QLabel("Draft not validated")
        self.validation_state.setObjectName("Muted")
        self.validation_state.setWordWrap(True)
        action_layout.addLayout(action_row)
        action_layout.addWidget(self.validation_state)
        self.run_progress = QProgressBar()
        self.run_progress.setRange(0, 100)
        self.run_progress.setValue(0)
        self.run_progress.setVisible(False)
        action_layout.addWidget(self.run_progress)
        self.run_log = QPlainTextEdit()
        self.run_log.setObjectName("ExperimentRunLog")
        self.run_log.setReadOnly(True)
        self.run_log.setMaximumBlockCount(5000)
        self.run_log.setMinimumHeight(130)
        self.run_log.setVisible(False)
        self.run_log.setStyleSheet(
            "font-family:'SFMono-Regular', Menlo, monospace; font-size:11px;"
        )
        action_layout.addWidget(self.run_log)
        visual_layout.addWidget(action_card)

        preview_card = Card()
        preview_layout = QVBoxLayout(preview_card)
        preview_layout.setContentsMargins(17, 14, 17, 16)
        preview_layout.addWidget(_section_title("Notebook-independent experiment document"))
        self.document_preview = QPlainTextEdit()
        self.document_preview.setObjectName("ExperimentDocumentPreview")
        self.document_preview.setReadOnly(True)
        self.document_preview.setMinimumHeight(210)
        self.document_preview.setPlaceholderText(
            "Validate to preview the exact app-owned experiment document."
        )
        self.document_preview.setStyleSheet(
            "font-family:'SFMono-Regular', Menlo, monospace; font-size:11px;"
        )
        preview_layout.addWidget(self.document_preview)
        visual_layout.addWidget(preview_card)
        visual_layout.addStretch(1)

        workspace.addWidget(
            self.selector_panel,
            0,
            0,
            alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
        )
        workspace.addWidget(visual_panel, 0, 1)
        workspace.setColumnStretch(0, 4)
        workspace.setColumnStretch(1, 6)
        layout.addLayout(workspace)
        layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        root.addWidget(scroll)

        self.template_combo.currentIndexChanged.connect(self._template_changed)
        for control in (
            self.name_edit,
            self.engine_combo,
            self.duration,
            self.integration_dt,
            self.initial_voltage,
            self.temperature,
            self.seed,
            self.repetitions,
            self.workers,
            self.stimulus_targets,
            self.stimulus_region,
            self.waveform_combo,
            self.amplitude,
            self.delay,
            self.pulse_width,
            self.frequency,
            self.pulse_count,
            self.control_enabled,
            self.manipulation_enabled,
            self.manipulation_name,
            self.manip_gap_enabled,
            self.manip_chemical_enabled,
            self.disabled_neurons,
            self.stimulus_scale,
            self.mechanism_scales,
            self.recording_targets,
            self.recording_region,
            self.record_voltage,
            self.detect_spikes,
            self.sample_dt,
            self.spike_threshold,
            self.make_plots,
        ):
            self._connect_change(control)
        self.name_edit.textChanged.connect(self._update_name_availability)
        self.set_config(ExperimentSpec())
        for label in self.findChildren(QLabel):
            make_label_copyable(label)

    def _connect_change(self, control: QWidget) -> None:
        for signal_name in (
            "textEdited",
            "valueChanged",
            "currentIndexChanged",
            "toggled",
        ):
            signal = getattr(control, signal_name, None)
            if signal is not None:
                signal.connect(self._invalidate)
                return

    def set_circuit_spec(self, spec: CircuitSpec) -> None:
        """Attach a circuit document, retaining geometry only for the same cell set."""

        existing_ids = tuple(item.record.neuron_id for item in self._morphologies)
        morphologies = self._morphologies if existing_ids == tuple(spec.neuron_ids) else ()
        self.set_circuit_snapshot(spec, morphologies)

    def set_circuit_snapshot(
        self,
        spec: CircuitSpec,
        morphologies: Iterable[Morphology] = (),
    ) -> None:
        """Attach a copied circuit spec plus immutable, presentation-only geometry."""

        self._circuit = CircuitSpec.from_dict(spec.to_dict())
        by_id = {item.record.neuron_id: item for item in morphologies}
        ordered = tuple(
            by_id[neuron_id]
            for neuron_id in self._circuit.neuron_ids
            if neuron_id in by_id
        )
        signature = tuple(
            (item.record.neuron_id, item.record.swc_path, len(item.segments))
            for item in ordered
        )
        if signature != self._morphology_signature:
            self._morphologies = ordered
            self._morphology_signature = signature
            self.target_region_viewport.set_morphologies(ordered)

        count = len(self._circuit.neuron_ids)
        if count:
            self.circuit_state.set_state(CheckState.PASS, text="CIRCUIT READY")
            source = self._circuit.connectome.label or self._circuit.connectome.key
            self.circuit_summary.setText(
                f"{count} neuron(s) from {source or 'the active circuit source'} are attached as a read-only experiment input."
            )
            overrides = len(self._circuit.neuron_overrides)
            compartments = sum(
                len(values) for values in self._circuit.compartment_overrides.values()
            )
            self.circuit_detail.setText(
                f"Neuron IDs: {', '.join(self._circuit.neuron_ids[:12])}"
                + (" …" if count > 12 else "")
                + f"\nNeuron overrides: {overrides} · compartment overrides: {compartments} · design schema: {self._circuit.schema_version}"
            )
        else:
            self.circuit_state.set_state(CheckState.WARNING, text="NO CIRCUIT")
            self.circuit_summary.setText(
                "Assemble and load neurons in Circuit Builder. This page will receive a read-only snapshot automatically."
            )
            self.circuit_detail.setText("No circuit snapshot attached")
        self._update_target_region_preview()
        self._invalidate()

    def _resolve_stimulus_targets(
        self, *, all_if_blank: bool
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Resolve explicit IDs and exact type names against the loaded circuit."""

        selectors = _parse_ids(self.stimulus_targets.text())
        circuit_ids = tuple(self._circuit.neuron_ids)
        if not selectors:
            return (circuit_ids if all_if_blank else ()), ()

        available_ids = set(circuit_ids)
        type_matches: dict[str, list[str]] = {}
        for morphology in self._morphologies:
            neuron_id = morphology.record.neuron_id
            neuron_type = morphology.record.neuron_type.strip().casefold()
            if neuron_id in available_ids and neuron_type:
                type_matches.setdefault(neuron_type, []).append(neuron_id)

        resolved: list[str] = []
        unmatched: list[str] = []
        for selector in selectors:
            matches = (
                (selector,)
                if selector in available_ids
                else tuple(type_matches.get(selector.casefold(), ()))
            )
            if not matches:
                unmatched.append(selector)
                continue
            for neuron_id in matches:
                if neuron_id not in resolved:
                    resolved.append(neuron_id)
        return tuple(resolved), tuple(unmatched)

    def _target_neuron_ids(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        resolved, unmatched = self._resolve_stimulus_targets(all_if_blank=True)
        loaded = {item.record.neuron_id for item in self._morphologies}
        target_ids = tuple(neuron_id for neuron_id in resolved if neuron_id in loaded)
        unavailable_geometry = tuple(
            neuron_id for neuron_id in resolved if neuron_id not in loaded
        )
        return target_ids, tuple((*unmatched, *unavailable_geometry))

    def _selected_compartment_targets(
        self, neuron_ids: Iterable[str]
    ) -> dict[str, set[int]]:
        selected: dict[str, set[int]] = {}
        for neuron_id in neuron_ids:
            raw_ids = set(self._circuit.compartment_overrides.get(neuron_id, {}))
            raw_ids.update(
                self._circuit.compartment_mechanism_overrides.get(neuron_id, {})
            )
            node_ids: set[int] = set()
            for raw_node_id in raw_ids:
                try:
                    node_ids.add(int(raw_node_id))
                except (TypeError, ValueError):
                    continue
            if node_ids:
                selected[neuron_id] = node_ids
        return selected

    def _update_target_region_preview(self) -> None:
        if not hasattr(self, "target_region_viewport"):
            return
        if not self._morphologies:
            self.target_region_viewport.clear_highlights()
            self.target_region_visualization_label.setText(
                f"Target region: {self.stimulus_region.currentText()} · "
                "load a circuit morphology to see the highlighted target."
            )
            return

        target_ids, missing_ids = self._target_neuron_ids()
        morphology_by_id = {
            item.record.neuron_id: item for item in self._morphologies
        }
        region = str(self.stimulus_region.currentData())
        missing_suffix = (
            f" · {len(missing_ids)} selector(s) did not match a loaded circuit morphology"
            if missing_ids
            else ""
        )
        if region == "all":
            self.target_region_viewport.set_highlights(neuron_ids=target_ids)
            segment_count = sum(
                len(morphology_by_id[neuron_id].segments)
                for neuron_id in target_ids
            )
            summary = (
                f"Target region: All compartments · {segment_count:,} SWC segment(s) "
                f"across {len(target_ids)} neuron(s) are brightened"
            )
        elif region == "soma":
            self.target_region_viewport.set_highlights(soma_ids=target_ids)
            summary = (
                f"Target region: Soma · {len(target_ids)} soma/pseudosoma marker(s) "
                "are brightened"
            )
        elif region == "ais":
            segment_ids = {
                neuron_id: _proximal_axon_segment_ids(morphology_by_id[neuron_id])
                for neuron_id in target_ids
            }
            self.target_region_viewport.set_highlights(segment_ids=segment_ids)
            summary = (
                f"Target region: AIS · {self.target_region_viewport.highlighted_segment_count} "
                "proximal axonal SWC segment(s) are brightened as a visual proxy; "
                "the execution adapter resolves the exact AIS"
            )
        else:
            segment_ids = self._selected_compartment_targets(target_ids)
            self.target_region_viewport.set_highlights(segment_ids=segment_ids)
            summary = (
                "Target region: Selected SWC compartments · "
                f"{self.target_region_viewport.highlighted_segment_count} applied Circuit Builder "
                "compartment(s) are brightened"
            )
        self.target_region_visualization_label.setText(summary + missing_suffix)

    def circuit_spec(self) -> CircuitSpec:
        return CircuitSpec.from_dict(self._circuit.to_dict())

    def config(self) -> ExperimentSpec:
        raw_scales = self.mechanism_scales.text().strip() or "{}"
        parsed_scales = json.loads(raw_scales)
        if not isinstance(parsed_scales, dict):
            raise ValueError("Mechanism scales must be a JSON object.")
        conditions: list[ConditionSpec] = []
        if self.control_enabled.isChecked():
            conditions.append(ConditionSpec(name="Control"))
        if self.manipulation_enabled.isChecked():
            conditions.append(
                ConditionSpec(
                    name=self.manipulation_name.text().strip(),
                    gap_junctions_enabled=self.manip_gap_enabled.isChecked(),
                    chemical_synapses_enabled=self.manip_chemical_enabled.isChecked(),
                    disabled_neuron_ids=_parse_ids(self.disabled_neurons.text()),
                    stimulus_scale=self.stimulus_scale.value(),
                    mechanism_scales={
                        str(key): float(value) for key, value in parsed_scales.items()
                    },
                )
            )
        resolved_targets, unmatched_targets = self._resolve_stimulus_targets(
            all_if_blank=True
        )
        stimulus = StimulusSpec(
            target_neuron_ids=tuple(
                dict.fromkeys((*resolved_targets, *unmatched_targets))
            ),
            target_region=str(self.stimulus_region.currentData()),
            waveform=str(self.waveform_combo.currentData()),
            amplitude_nA=self.amplitude.value(),
            delay_ms=self.delay.value(),
            pulse_width_ms=self.pulse_width.value(),
            frequency_hz=self.frequency.value(),
            pulse_count=self.pulse_count.value(),
        )
        recording = RecordingSpec(
            target_neuron_ids=_parse_ids(self.recording_targets.text()),
            target_region=str(self.recording_region.currentData()),
            record_voltage=self.record_voltage.isChecked(),
            detect_spikes=self.detect_spikes.isChecked(),
            sample_dt_ms=self.sample_dt.value(),
            spike_threshold_mV=self.spike_threshold.value(),
            make_plots=self.make_plots.isChecked(),
        )
        return ExperimentSpec(
            name=self.name_edit.text().strip(),
            template_key=str(self.template_combo.currentData()),
            engine=str(self.engine_combo.currentData()),
            duration_ms=self.duration.value(),
            integration_dt_ms=self.integration_dt.value(),
            initial_voltage_mV=self.initial_voltage.value(),
            temperature_C=self.temperature.value(),
            random_seed=self.seed.value(),
            repetitions=self.repetitions.value(),
            workers=self.workers.value(),
            stimuli=(stimulus,),
            conditions=tuple(conditions),
            recording=recording,
        )

    def set_config(self, config: ExperimentSpec) -> None:
        self._restoring = True
        try:
            self.name_edit.setText(config.name)
            self.template_combo.setCurrentIndex(
                max(0, self.template_combo.findData(config.template_key))
            )
            self.engine_combo.setCurrentIndex(max(0, self.engine_combo.findData(config.engine)))
            self.duration.setValue(config.duration_ms)
            self.integration_dt.setValue(config.integration_dt_ms)
            self.initial_voltage.setValue(config.initial_voltage_mV)
            self.temperature.setValue(config.temperature_C)
            self.seed.setValue(config.random_seed)
            self.repetitions.setValue(config.repetitions)
            self.workers.setValue(config.workers)
            stimulus = config.stimuli[0] if config.stimuli else StimulusSpec()
            self.stimulus_targets.setText(_ids_text(stimulus.target_neuron_ids))
            self.stimulus_region.setCurrentIndex(
                max(0, self.stimulus_region.findData(stimulus.target_region))
            )
            self.waveform_combo.setCurrentIndex(
                max(0, self.waveform_combo.findData(stimulus.waveform))
            )
            self.amplitude.setValue(stimulus.amplitude_nA)
            self.delay.setValue(stimulus.delay_ms)
            self.pulse_width.setValue(stimulus.pulse_width_ms)
            self.frequency.setValue(stimulus.frequency_hz)
            self.pulse_count.setValue(stimulus.pulse_count)
            control = next(
                (item for item in config.conditions if item.name.casefold() == "control"),
                None,
            )
            manipulation = next(
                (item for item in config.conditions if item is not control), None
            )
            self.control_enabled.setChecked(bool(control and control.enabled))
            self.manipulation_enabled.setChecked(bool(manipulation and manipulation.enabled))
            manipulation = manipulation or ConditionSpec(name="Comparison")
            self.manipulation_name.setText(manipulation.name)
            self.manip_gap_enabled.setChecked(manipulation.gap_junctions_enabled)
            self.manip_chemical_enabled.setChecked(manipulation.chemical_synapses_enabled)
            self.disabled_neurons.setText(_ids_text(manipulation.disabled_neuron_ids))
            self.stimulus_scale.setValue(manipulation.stimulus_scale)
            self.mechanism_scales.setText(
                json.dumps(manipulation.mechanism_scales, sort_keys=True)
            )
            recording = config.recording
            self.recording_targets.setText(_ids_text(recording.target_neuron_ids))
            self.recording_region.setCurrentIndex(
                max(0, self.recording_region.findData(recording.target_region))
            )
            self.record_voltage.setChecked(recording.record_voltage)
            self.detect_spikes.setChecked(recording.detect_spikes)
            self.sample_dt.setValue(recording.sample_dt_ms)
            self.spike_threshold.setValue(recording.spike_threshold_mV)
            self.make_plots.setChecked(recording.make_plots)
        finally:
            self._restoring = False
        self._invalidate()

    def reset(self) -> None:
        self.set_circuit_spec(CircuitSpec())
        self.set_config(ExperimentSpec())

    def validate_draft(self) -> None:
        try:
            config = self.config()
            errors = config.errors(self._circuit)
        except Exception as exc:
            errors = [str(exc)]
            config = None
        if config is not None:
            self.document_preview.setPlainText(
                json.dumps(
                    {
                        "circuit": self._circuit.to_dict(),
                        "experiment": config.to_dict(),
                    },
                    indent=2,
                )
            )
        if errors:
            self.validation_state.setText(f"{len(errors)} issue(s) · {errors[0]}")
            self.status_message.emit(self.validation_state.text())
            return
        self.validation_state.setText(
            "Draft valid · Run will preflight the selected simulator and launch app-owned output"
        )
        self.status_message.emit(self.validation_state.text())

    def request_run(self) -> None:
        """Preflight and launch the generic classic-HH worker."""

        if self._process is not None:
            QMessageBox.warning(
                self,
                "Experiment already running",
                "Stop or finish the current experiment before launching another.",
            )
            return

        try:
            config = self.config()
        except Exception as exc:
            self._show_not_ready(str(exc))
            return
        matches = self._update_name_availability()
        if matches:
            existing = matches[0]
            self.validation_state.setText(
                f"Name already used · rename {config.name!r} before running"
            )
            self.status_message.emit(self.validation_state.text())
            QMessageBox.warning(
                self,
                "Experiment name already used",
                f'A saved run named "{config.name.strip()}" already exists.\n\n'
                f"Existing run: {existing}\n\n"
                "Choose a different experiment name before running. No files were changed.",
            )
            return
        errors = config.errors(self._circuit)
        if errors:
            self._show_not_ready(errors[0])
            return
        adapter = GenericExperimentAdapter(
            self._runtime_paths,
            digifly_public_root=self._digifly_public_root,
        )
        self.validation_state.setText("Running simulator preflight…")
        self.status_message.emit(self.validation_state.text())
        try:
            if adapter.needs_gap_catalogue(self._circuit, config):
                mechanism_label = (
                    "Arbor gap mechanism catalogue"
                    if config.engine == "arbor"
                    else "NEURON gap mechanisms"
                )
                self.validation_state.setText(
                    f"Preparing the app-owned {mechanism_label}…"
                )
                self.status_message.emit(self.validation_state.text())
                adapter.ensure_gap_catalogue(
                    self._circuit,
                    config,
                    output_root=self._output_root(),
                )
            report = adapter.validate(
                self._circuit,
                config,
                self._morphologies,
                output_root=self._output_root(),
            )
        except Exception as exc:
            self._show_not_ready(f"Simulator preflight failed: {exc}")
            return
        if not report.ok:
            failures = [
                f"• {check.title}: {check.detail}"
                for check in report.checks
                if check.blocking and check.state == CheckState.FAIL
            ]
            detail = "\n".join(failures)
            self.validation_state.setText(
                f"Experiment blocked · {len(failures)} simulator preflight check(s) failed"
            )
            self.status_message.emit(self.validation_state.text())
            QMessageBox.warning(
                self,
                "Experiment cannot run yet",
                detail + "\n\nNo simulation started and no run files were created.",
            )
            return
        try:
            plan = adapter.plan(
                self._circuit,
                config,
                output_root=self._output_root(),
            )
            payload = adapter.request_payload(
                self._circuit,
                config,
                self._morphologies,
                report,
                output_root=self._output_root(),
            )
            request_path = Path(plan.arguments[-1])
            adapter.write_request(request_path, payload)
            (self._output_root() / "_runtime" / "matplotlib").mkdir(
                parents=True,
                exist_ok=True,
            )
            store = JobStore(self._output_root())
            job_dir = store.create(plan, report, payload)
        except FileExistsError as exc:
            self._update_name_availability()
            self._show_not_ready(
                f"The app-owned run folder is already reserved: {exc}"
            )
            return
        except Exception as exc:
            self._show_not_ready(f"Could not create the app-owned run: {exc}")
            return
        self._update_name_availability()
        self._start_process(plan, store, job_dir, report)

    def _start_process(
        self,
        plan: Any,
        store: JobStore,
        job_dir: Path,
        report: Any,
    ) -> None:
        process = QProcess(self)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        environment = QProcessEnvironment.systemEnvironment()
        for key in tuple(environment.keys()):
            if (
                key in EXTERNAL_PYTHON_ENV_REMOVE
                or key == "DISPLAY"
                or key.startswith("DYLD_")
                or key.startswith("CONDA_")
            ):
                environment.remove(key)
        for key, value in plan.environment.items():
            environment.insert(key, value)
        process.setProcessEnvironment(environment)
        process.setWorkingDirectory(plan.working_directory)
        process.readyReadStandardOutput.connect(self._read_process_output)
        process.finished.connect(self._process_finished)
        process.errorOccurred.connect(self._process_error)
        self._process = process
        self._plan = plan
        self._job_store = store
        self._job_dir = job_dir
        self._run_output_buffer = ""
        self._cancel_requested = False
        store.update_status(job_dir, "running", pid=None)
        store.append_event(job_dir, "running", "Generic scientific worker started.")

        self.run_log.clear()
        self.run_log.setVisible(True)
        self.run_log.appendPlainText(f"Job provenance: {job_dir}\n")
        for check in report.checks:
            self.run_log.appendPlainText(
                f"[{check.state.value.upper()}] {check.title} · {check.detail}"
            )
        self.run_log.appendPlainText(f"\nStarting: {plan.display_command}\n")
        self.run_progress.setVisible(True)
        self.run_progress.setRange(0, 0)
        self.run_button.setEnabled(False)
        self.run_button.setText("Experiment running…")
        self.validate_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.selector_panel.setEnabled(False)
        self.validation_state.setText(
            f"Running {plan.engine.upper()} experiment · starting worker"
        )
        process.start(plan.program, list(plan.arguments))
        if process.waitForStarted(5000):
            store.update_status(job_dir, "running", pid=int(process.processId()))
            self.validation_state.setText(
                f"Running {plan.engine.upper()} experiment · PID {process.processId()}"
            )
            self.status_message.emit(self.validation_state.text())

    def cancel_run(self) -> None:
        if self._process is None:
            return
        choice = QMessageBox.question(
            self,
            "Stop the current experiment?",
            "Digifly Workstation will request graceful termination and preserve the run documents, log, and partial artifacts.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if choice != QMessageBox.StandardButton.Yes:
            return
        process = self._process
        if self._job_store is not None and self._job_dir is not None:
            self._job_store.update_status(self._job_dir, "cancelling")
            self._job_store.append_event(
                self._job_dir,
                "cancel_requested",
                "User requested graceful termination.",
            )
        marker_written = False
        if self._plan is not None:
            try:
                marker = _write_cancellation_request(self._plan.working_directory)
            except OSError as exc:
                self.run_log.appendPlainText(
                    f"\nCould not publish the cancellation marker ({exc}); "
                    "falling back to direct termination."
                )
            else:
                marker_written = True
                self.run_log.appendPlainText(
                    f"\nCancellation marker published: {marker.name}"
                )
        self.run_log.appendPlainText(
            "\nCancellation requested; preserving provenance and partial artifacts…"
        )
        self.cancel_button.setEnabled(False)
        self._cancel_requested = True
        if marker_written:
            QTimer.singleShot(
                CANCEL_MARKER_GRACE_MS,
                lambda active=process: self._terminate_cancelled_process(active),
            )
        else:
            self._terminate_cancelled_process(process)

    def _terminate_cancelled_process(self, process: QProcess) -> None:
        if (
            self._process is not process
            or process.state() == QProcess.ProcessState.NotRunning
        ):
            return
        process.terminate()
        QTimer.singleShot(
            CANCEL_FORCE_KILL_MS,
            lambda active=process: self._kill_cancelled_process(active),
        )

    def _kill_cancelled_process(self, process: QProcess) -> None:
        if (
            self._process is process
            and process.state() != QProcess.ProcessState.NotRunning
        ):
            self.run_log.appendPlainText(
                "\nGraceful cancellation timed out; forcing worker shutdown."
            )
            process.kill()

    def _read_process_output(self) -> None:
        if self._process is None:
            return
        raw = bytes(self._process.readAllStandardOutput())
        text = raw.decode("utf-8", errors="replace")
        if not text:
            return
        self.run_log.moveCursor(QTextCursor.MoveOperation.End)
        self.run_log.insertPlainText(text)
        self.run_log.moveCursor(QTextCursor.MoveOperation.End)
        if self._job_dir is not None:
            with (self._job_dir / "stdout.log").open("a", encoding="utf-8") as handle:
                handle.write(text)

    def _process_finished(
        self,
        exit_code: int,
        exit_status: QProcess.ExitStatus,
    ) -> None:
        self._read_process_output()
        crashed = exit_status == QProcess.ExitStatus.CrashExit
        state = (
            "cancelled"
            if self._cancel_requested
            else "failed"
            if crashed or exit_code
            else "completed"
        )
        if self._job_store is not None and self._job_dir is not None:
            self._job_store.update_status(
                self._job_dir,
                state,
                exit_code=exit_code,
                exit_status=exit_status.name,
            )
            self._job_store.append_event(
                self._job_dir,
                state,
                f"Generic scientific worker exited with code {exit_code}.",
            )
        if state != "completed" and self._plan is not None:
            update_run_manifest_state(
                self._plan.working_directory,
                state,
                exit_code=exit_code,
                exit_status=exit_status.name,
            )
        self.run_progress.setRange(0, 100)
        self.run_progress.setValue(100 if state == "completed" else 0)
        self.run_log.appendPlainText(
            f"\nWorker {state} with exit code {exit_code}."
        )
        expected = (
            Path(self._plan.expected_summary_path)
            if self._plan is not None and self._plan.expected_summary_path
            else None
        )
        if state == "completed" and (expected is None or not expected.is_file()):
            state = "failed"
            message = "Worker exited successfully but produced no result summary."
            self.run_log.appendPlainText(message)
            if self._job_store is not None and self._job_dir is not None:
                self._job_store.update_status(
                    self._job_dir,
                    "failed",
                    error=message,
                )
            if self._plan is not None:
                update_run_manifest_state(
                    self._plan.working_directory,
                    "failed",
                    error=message,
                )
        if state == "completed" and expected is not None and expected.is_file():
            try:
                result = load_generic_experiment_result(expected)
            except Exception as exc:
                state = "failed"
                self.run_log.appendPlainText(f"Result inspection failed: {exc}")
                if self._job_store is not None and self._job_dir is not None:
                    self._job_store.update_status(
                        self._job_dir,
                        "failed",
                        error=f"Result inspection failed: {exc}",
                    )
                update_run_manifest_state(
                    self._plan.working_directory,
                    "failed",
                    error=f"Result inspection failed: {exc}",
                )
            else:
                self.result_ready.emit(result)
        self.validation_state.setText(
            "Experiment completed · opened in Results"
            if state == "completed"
            else "Experiment cancelled · partial artifacts and provenance were preserved"
            if state == "cancelled"
            else f"Experiment failed · inspect the preserved run log (exit {exit_code})"
        )
        self.status_message.emit(self.validation_state.text())
        self._finish_process_ui()

    def _process_error(self, error: QProcess.ProcessError) -> None:
        message = self._process.errorString() if self._process else str(error)
        self.run_log.appendPlainText(f"\nProcess error: {message}")
        if self._cancel_requested and error != QProcess.ProcessError.FailedToStart:
            if self._job_store is not None and self._job_dir is not None:
                self._job_store.append_event(
                    self._job_dir,
                    "cancellation_process_error",
                    message,
                )
            return
        if self._job_store is not None and self._job_dir is not None:
            self._job_store.update_status(self._job_dir, "failed", error=message)
            self._job_store.append_event(self._job_dir, "error", message)
        if self._plan is not None:
            update_run_manifest_state(
                self._plan.working_directory,
                "failed",
                error=message,
            )
        self.validation_state.setText(f"Experiment worker error · {message}")
        self.status_message.emit(self.validation_state.text())
        if error == QProcess.ProcessError.FailedToStart:
            self._finish_process_ui()

    def _finish_process_ui(self) -> None:
        self._process = None
        self.cancel_button.setEnabled(False)
        self.validate_button.setEnabled(True)
        self.run_button.setText("Run experiment")
        self.run_button.setEnabled(True)
        self.selector_panel.setEnabled(True)
        self._cancel_requested = False
        self._update_name_availability()

    def set_neuron_runtime(self, value: str | Path) -> None:
        self._runtime_paths["neuron"] = str(value)

    def set_arbor_runtime(self, value: str | Path) -> None:
        self._runtime_paths["arbor"] = str(value)

    def set_bmtk_runtime(self, value: str | Path) -> None:
        self._runtime_paths["bmtk"] = str(value)

    def set_digifly_public_root(self, value: str | Path) -> None:
        self._digifly_public_root = str(value)

    def set_output_root(self, value: str | Path) -> None:
        self._configured_output_root = Path(value).expanduser()
        self._update_name_availability()

    def _output_root(self) -> Path:
        return self._configured_output_root.resolve()

    def _update_name_availability(self, *_args: Any) -> tuple[Path, ...]:
        """Keep the name field aligned with the duplicate-name launch gate."""

        name = " ".join(self.name_edit.text().split())
        matches = (
            JobStore(self._output_root()).matching_experiment_runs(name)
            if name
            else ()
        )
        available = bool(name) and not matches
        state = "available" if available else "unavailable"
        text = "✓ Available" if available else "✕ Unavailable"
        if not name:
            detail = "Enter an experiment name before running."
        elif matches:
            detail = f"A saved run with this name already exists at {matches[0]}."
        else:
            detail = f"No saved run with this name exists under {self._output_root()}."

        self.name_availability.setText(text)
        self.name_availability.setProperty("availability", state)
        self.name_availability.setToolTip(detail)
        self.name_availability.setAccessibleDescription(detail)
        self.name_edit.setProperty("nameAvailability", state)
        self.name_edit.setToolTip(detail)
        self.name_edit.setAccessibleDescription(f"{text}. {detail}")
        for widget in (self.name_availability, self.name_edit):
            widget.style().unpolish(widget)
            widget.style().polish(widget)
            widget.update()
        return matches

    def _show_not_ready(self, detail: str) -> None:
        self.validation_state.setText(f"Experiment not ready · {detail}")
        self.status_message.emit(self.validation_state.text())
        QMessageBox.warning(
            self,
            "Experiment is not ready",
            f"{detail}\n\nNo simulation started and no files were created.",
        )

    def _template_changed(self) -> None:
        if self._restoring:
            return
        config = (
            ExperimentSpec.pulse_train_comparison()
            if self.template_combo.currentData() == "pulse_train_comparison"
            else ExperimentSpec()
        )
        self.set_config(config)

    def _update_stimulus_preview(self) -> None:
        self.stimulus_preview.set_protocol(
            duration_ms=self.duration.value(),
            random_seed=self.seed.value(),
            amplitude_nA=self.amplitude.value(),
            delay_ms=self.delay.value(),
            pulse_width_ms=self.pulse_width.value(),
            frequency_hz=self.frequency.value(),
            pulse_count=self.pulse_count.value(),
            waveform=str(self.waveform_combo.currentData()),
        )
        self.stimulus_preview_summary.setText(
            self.stimulus_preview.summary_text()
        )

    def _save_stimulus_visualization(self) -> None:
        image = self.stimulus_preview.render_high_resolution()
        output = save_image_with_dialog(
            self,
            image,
            title="Save high-resolution stimulus visualization",
            default_name="digifly-stimulus-preview.png",
        )
        if output is not None:
            self.status_message.emit(
                f"Saved {image.width()} × {image.height()} PNG: {output}"
            )

    def _invalidate(self, *_args: Any) -> None:
        if self._restoring:
            return
        self._update_stimulus_preview()
        self._update_target_region_preview()
        if self._process is not None:
            return
        self.validation_state.setText("Controls changed · validate draft")
        self.document_preview.clear()
        try:
            self.experiment_changed.emit(self.config())
        except Exception:
            pass
