from __future__ import annotations

import json
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
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
from digifly_app.core.models import CheckState
from .stimulus_preview import StimulusPreview
from .widgets import Card, CollapsibleSection, StatusPill, make_label_copyable


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


class ExperimentBuilderPage(QWidget):
    """Build a run protocol around a CircuitSpec without importing a notebook."""

    status_message = Signal(str)
    experiment_changed = Signal(object)
    result_ready = Signal(object)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._circuit = CircuitSpec()
        self._restoring = False

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

        circuit_card = Card()
        circuit_layout = QVBoxLayout(circuit_card)
        circuit_layout.setContentsMargins(17, 14, 17, 15)
        circuit_layout.setSpacing(8)
        circuit_top = QHBoxLayout()
        circuit_top.addWidget(_section_title("Circuit input"))
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
        layout.addWidget(circuit_card)

        workspace = QGridLayout()
        workspace.setHorizontalSpacing(16)
        workspace.setVerticalSpacing(0)

        selector_panel = QWidget()
        selector_panel.setObjectName("ExperimentSelectorPanel")
        selector_panel.setMinimumWidth(400)
        selector_panel.setMaximumWidth(520)
        selector_layout = QVBoxLayout(selector_panel)
        selector_layout.setContentsMargins(0, 0, 0, 0)
        selector_layout.setSpacing(9)
        self.selector_sections: dict[str, CollapsibleSection] = {}

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
        self.template_combo.setToolTip(
            "Templates populate app-owned fields only. No notebook is imported or executed."
        )
        self.engine_combo = QComboBox()
        self.engine_combo.setObjectName("ExperimentEngine")
        self.engine_combo.addItem("Arbor", "arbor")
        self.engine_combo.addItem("NEURON", "neuron")
        self.engine_combo.addItem("BMTK / SONATA", "bmtk")
        identity_form.addRow("Name", self.name_edit)
        identity_form.addRow("Template", self.template_combo)
        identity_form.addRow("Execution engine", self.engine_combo)
        identity_layout.addLayout(identity_form)

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
        run_form.addRow("Simulation duration", self.duration)
        run_form.addRow("Integration step", self.integration_dt)
        run_form.addRow("Initial voltage", self.initial_voltage)
        run_form.addRow("Temperature", self.temperature)
        run_form.addRow("Random seed", self.seed)
        run_form.addRow("Repetitions", self.repetitions)
        run_form.addRow("Workers / threads", self.workers)
        run_layout.addLayout(run_form)

        stimulus_layout = add_selector("primary_stimulus", "Primary stimulus")
        stimulus_hint = QLabel(
            "Blank target IDs means every neuron in the circuit. The execution adapter will resolve simulator locations after validation."
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
        self.stimulus_targets.setPlaceholderText("all circuit neurons")
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
            self.amplitude,
            self.delay,
            self.pulse_width,
            self.frequency,
            self.pulse_count,
        ):
            live_control.setKeyboardTracking(True)
        stimulus_form.addRow("Target neuron IDs", self.stimulus_targets)
        stimulus_form.addRow("Target region", self.stimulus_region)
        stimulus_form.addRow("Waveform", self.waveform_combo)
        stimulus_form.addRow("Amplitude", self.amplitude)
        stimulus_form.addRow("Start delay", self.delay)
        stimulus_form.addRow("Pulse / step duration", self.pulse_width)
        stimulus_form.addRow("Frequency", self.frequency)
        stimulus_form.addRow("Pulse count", self.pulse_count)
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
        condition_layout.addWidget(self.control_enabled)
        self.manipulation_enabled = QCheckBox("Run comparison / manipulation condition")
        self.manipulation_enabled.setChecked(True)
        condition_layout.addWidget(self.manipulation_enabled)
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
        self.mechanism_scales.setToolTip(
            "Optional runtime mechanism-scale mapping. Keys must match mechanisms in the circuit design."
        )
        condition_form.addRow("Comparison name", self.manipulation_name)
        condition_form.addRow("Electrical edges", self.manip_gap_enabled)
        condition_form.addRow("Chemical edges", self.manip_chemical_enabled)
        condition_form.addRow("Disabled neurons", self.disabled_neurons)
        condition_form.addRow("Stimulus multiplier", self.stimulus_scale)
        condition_form.addRow("Mechanism scales (JSON)", self.mechanism_scales)
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
        self.recording_region.addItem("All compartments", "all")
        self.recording_region.addItem("Soma", "soma")
        self.recording_region.addItem("AIS", "ais")
        self.recording_region.addItem("Selected SWC compartments", "selected_compartments")
        self.record_voltage = QCheckBox("Membrane voltage")
        self.record_voltage.setChecked(True)
        self.detect_spikes = QCheckBox("Spike events")
        self.detect_spikes.setChecked(True)
        outputs = QWidget()
        output_row = QHBoxLayout(outputs)
        output_row.setContentsMargins(0, 0, 0, 0)
        output_row.addWidget(self.record_voltage)
        output_row.addWidget(self.detect_spikes)
        self.sample_dt = _double_spin(
            0.000001, 1_000_000.0, 0.05, decimals=6, step=0.01, suffix=" ms"
        )
        self.spike_threshold = _double_spin(
            -300.0, 300.0, -20.0, decimals=3, suffix=" mV"
        )
        self.make_plots = QCheckBox("Generate standard plots")
        self.make_plots.setChecked(True)
        recording_form.addRow("Target neuron IDs", self.recording_targets)
        recording_form.addRow("Target region", self.recording_region)
        recording_form.addRow("Signals", outputs)
        recording_form.addRow("Sample interval", self.sample_dt)
        recording_form.addRow("Spike threshold", self.spike_threshold)
        recording_form.addRow("Analysis", self.make_plots)
        recording_layout.addLayout(recording_form)
        selector_layout.addStretch(1)

        visual_panel = QWidget()
        visual_layout = QVBoxLayout(visual_panel)
        visual_layout.setContentsMargins(0, 0, 0, 0)
        visual_layout.setSpacing(14)

        stimulus_preview_card = Card()
        stimulus_preview_layout = QVBoxLayout(stimulus_preview_card)
        stimulus_preview_layout.setContentsMargins(17, 14, 17, 16)
        stimulus_preview_layout.setSpacing(9)
        stimulus_preview_layout.addWidget(_section_title("Live stimulus preview"))
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
        self.run_button.setEnabled(False)
        self.run_button.setToolTip(
            "A generic CircuitSpec execution adapter is required before this draft can launch."
        )
        action_row.addWidget(self.validate_button)
        action_row.addWidget(self.run_button)
        action_row.addStretch(1)
        self.validation_state = QLabel("Draft not validated")
        self.validation_state.setObjectName("Muted")
        self.validation_state.setWordWrap(True)
        action_layout.addLayout(action_row)
        action_layout.addWidget(self.validation_state)
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
            selector_panel,
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
        self.set_config(ExperimentSpec.pulse_train_comparison())
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
        self._circuit = CircuitSpec.from_dict(spec.to_dict())
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
        self._invalidate()

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
        stimulus = StimulusSpec(
            target_neuron_ids=_parse_ids(self.stimulus_targets.text()),
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
        self.set_config(ExperimentSpec.pulse_train_comparison())

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
            "Draft valid · generic execution adapter is the next backend milestone"
        )
        self.status_message.emit(self.validation_state.text())

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

    def _invalidate(self, *_args: Any) -> None:
        if self._restoring:
            return
        self._update_stimulus_preview()
        self.validation_state.setText("Controls changed · validate draft")
        self.document_preview.clear()
        try:
            self.experiment_changed.emit(self.config())
        except Exception:
            pass
