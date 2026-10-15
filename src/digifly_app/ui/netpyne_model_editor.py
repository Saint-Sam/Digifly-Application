"""Editable, synthetic-model-only front end for the NetPyNE Workbench v1 schema.

This widget deliberately does not import SWCs or claim to edit the pinned PDCM
author model. The versioned document is the source of truth; the NetPyNE export
is a validated, derived settings snapshot and does not run a simulation.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QFormLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from digifly_app.core.netpyne_workbench import (
    CellRuleSpec,
    ConnectionSpec,
    ExternalResourceRef,
    NetPyNEExperimentSpec,
    NetPyNEWorkbenchSpec,
    NetStimSpec,
    PopulationSpec,
    SynapseSpec,
    load_workbench_spec,
    save_workbench_spec,
    synthetic_two_population_example,
    to_netpyne_payload,
)


@dataclass(frozen=True)
class _Column:
    key: str
    title: str
    default: str
    choices: tuple[str, ...] = ()


def _float(value: str, location: str) -> float:
    try:
        return float(value.strip())
    except ValueError as exc:
        raise ValueError(f"{location} must be a number") from exc


def _int(value: str, location: str) -> int:
    try:
        return int(value.strip())
    except ValueError as exc:
        raise ValueError(f"{location} must be a whole number") from exc


class _RecordTable(QWidget):
    """Small editable rule grid; semantic and reference checks live in core."""

    changed = Signal()

    def __init__(self, columns: tuple[_Column, ...], description: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.columns = columns
        self._loading = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        help_label = QLabel(description)
        help_label.setWordWrap(True)
        layout.addWidget(help_label)

        self.table = QTableWidget(0, len(columns), self)
        self.table.setHorizontalHeaderLabels([column.title for column in columns])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setMinimumSectionSize(90)
        self.table.itemChanged.connect(self._item_changed)

        actions = QHBoxLayout()
        self.add_button = QPushButton("+ Add rule")
        self.remove_button = QPushButton("Remove selected")
        self.add_button.clicked.connect(lambda: self.add_row())
        self.remove_button.clicked.connect(self.remove_selected)
        actions.addWidget(self.add_button)
        actions.addWidget(self.remove_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        layout.addWidget(self.table, 1)

    def _item_changed(self, _item: QTableWidgetItem) -> None:
        if not self._loading:
            self.changed.emit()

    def add_row(self, values: dict[str, Any] | None = None) -> None:
        values = values or {}
        previous = self._loading
        self._loading = True
        row = self.table.rowCount()
        self.table.insertRow(row)
        for index, column in enumerate(self.columns):
            value = values.get(column.key, column.default)
            if column.choices:
                combo = QComboBox(self.table)
                combo.addItems(column.choices)
                choice = str(value)
                combo.setCurrentIndex(max(0, combo.findText(choice)))
                combo.currentTextChanged.connect(lambda _text: self.changed.emit())
                self.table.setCellWidget(row, index, combo)
            else:
                item = QTableWidgetItem(str(value))
                item.setToolTip(column.title)
                self.table.setItem(row, index, item)
        self._loading = previous
        if not self._loading:
            self.changed.emit()

    def replace_rows(self, records: tuple[Any, ...]) -> None:
        self._loading = True
        self.table.setRowCount(0)
        for record in records:
            self.add_row({column.key: getattr(record, column.key) for column in self.columns})
        self._loading = False
        self.changed.emit()

    def remove_selected(self) -> None:
        rows = sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True)
        for row in rows:
            self.table.removeRow(row)
        if rows:
            self.changed.emit()

    def rows(self) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        for row in range(self.table.rowCount()):
            record: dict[str, str] = {}
            for index, column in enumerate(self.columns):
                widget = self.table.cellWidget(row, index)
                item = self.table.item(row, index)
                record[column.key] = (
                    widget.currentText() if isinstance(widget, QComboBox)
                    else item.text() if item is not None else ""
                )
            result.append(record)
        return result


class NetPyNEModelEditor(QWidget):
    """Embeddable v1 model editor with validated JSON and NetPyNE settings export.

    Public API: ``load_model``, ``model_spec``, ``load_file``, ``save_file``,
    and ``export_payload``. Those methods raise ``ValueError`` for invalid
    documents/drafts, so callers can decide how to show failures.
    """

    model_changed = Signal()
    validation_changed = Signal(bool, str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.current_path: Path | None = None
        self._external_resources: tuple[ExternalResourceRef, ...] = ()
        self._loading = False
        self.setObjectName("NetPyNEModelEditor")

        shell = QVBoxLayout(self)
        self.scope_banner = QLabel(
            "NetPyNE Workbench · schema v1 · synthetic single-soma HH models only. "
            "The starter E/I network is not the PDCM paper model. "
            "SWC, MOD and dataset imports are not available in this editor."
        )
        self.scope_banner.setObjectName("NetPyNEScopeBanner")
        self.scope_banner.setWordWrap(True)
        shell.addWidget(self.scope_banner)

        title_row = QHBoxLayout()
        title_row.addWidget(QLabel("Model name"))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Name this synthetic model")
        title_row.addWidget(self.name_edit, 1)
        shell.addLayout(title_row)

        self.sections = QTabWidget()
        self.sections.setObjectName("NetPyNEEditorSections")
        self.sections.tabBar().setObjectName("NetPyNESectionTabs")
        self.sections.setDocumentMode(True)
        self.cell_rules_table = _RecordTable(
            (
                _Column("label", "Cell rule", "NewCell"),
                _Column("diameter_um", "Soma diameter (µm)", "18.8"),
                _Column("length_um", "Soma length (µm)", "18.8"),
                _Column("axial_resistance_ohm_cm", "Ra (Ω·cm)", "123"),
                _Column("capacitance_uF_cm2", "Cm (µF/cm²)", "1"),
                _Column("gnabar_S_cm2", "HH gNa (S/cm²)", "0.12"),
                _Column("gkbar_S_cm2", "HH gK (S/cm²)", "0.036"),
                _Column("gl_S_cm2", "HH gLeak (S/cm²)", "0.003"),
                _Column("el_mV", "HH ELeak (mV)", "-70"),
            ),
            "Single-soma Hodgkin–Huxley cell rules. This does not import morphology or mechanisms.",
        )
        self.populations_table = _RecordTable(
            (
                _Column("label", "Population", "NewPop"),
                _Column("cell_rule", "Cell rule label", "NewCell"),
                _Column("count", "Cell count", "1"),
            ),
            "Each population names an existing cell rule. Counts are explicit, not a dataset import.",
        )
        self.synapses_table = _RecordTable(
            (
                _Column("label", "Synapse", "NewSynapse"),
                _Column("role", "Sign", "excitatory", ("excitatory", "inhibitory")),
                _Column("tau1_ms", "Rise τ (ms)", "0.1"),
                _Column("tau2_ms", "Decay τ (ms)", "5"),
                _Column("reversal_mV", "Reversal (mV)", "0"),
            ),
            "Exp2Syn mechanisms. The sign must agree with reversal potential; color or label alone does not set it.",
        )
        self.connections_table = _RecordTable(
            (
                _Column("label", "Connection rule", "NewConnection"),
                _Column("pre_population", "Pre population", "NewPop"),
                _Column("post_population", "Post population", "NewPop"),
                _Column("synapse", "Synapse", "NewSynapse"),
                _Column("mode", "Rule", "probability", ("probability", "convergence", "divergence")),
                _Column("value", "Rule value", "0.1"),
                _Column("weight_uS", "Weight (µS)", "0.01"),
                _Column("delay_ms", "Delay (ms)", "1"),
            ),
            "Choose probability, convergence or divergence for each directed population connection.",
        )
        self.stimuli_table = _RecordTable(
            (
                _Column("label", "NetStim", "NewDrive"),
                _Column("target_population", "Target population", "NewPop"),
                _Column("synapse", "Synapse", "NewSynapse"),
                _Column("rate_hz", "Rate (Hz)", "10"),
                _Column("noise", "Noise (0–1)", "1"),
                _Column("start_ms", "Start (ms)", "0"),
                _Column("weight_uS", "Weight (µS)", "0.01"),
                _Column("delay_ms", "Delay (ms)", "1"),
                _Column("number", "Max events", "1000000000"),
            ),
            "External NetStim drive. This is a generated spike source, not an imported recording.",
        )
        section_widgets = (
            ("Cell rules", self.cell_rules_table),
            ("Populations", self.populations_table),
            ("Synapses", self.synapses_table),
            ("Connectivity", self.connections_table),
            ("NetStim", self.stimuli_table),
        )
        for widget, label in (
            (self.cell_rules_table, "+ Add cell rule"),
            (self.populations_table, "+ Add population"),
            (self.synapses_table, "+ Add synapse"),
            (self.connections_table, "+ Add connection rule"),
            (self.stimuli_table, "+ Add NetStim"),
        ):
            widget.add_button.setText(label)
        for title, widget in section_widgets:
            self.sections.addTab(widget, title)
            widget.changed.connect(self._draft_changed)
        self.sections.addTab(self._experiment_page(), "Experiment / recording")

        work_area = QHBoxLayout()
        work_area.setSpacing(12)
        model_rail = QFrame()
        model_rail.setObjectName("NetPyNEModelRail")
        model_rail.setFixedWidth(190)
        rail_layout = QVBoxLayout(model_rail)
        rail_layout.setContentsMargins(10, 14, 10, 14)
        rail_layout.setSpacing(5)
        rail_heading = QLabel("MODEL SPECIFICATION")
        rail_heading.setObjectName("NetPyNERailHeading")
        rail_layout.addWidget(rail_heading)
        self.section_nav_buttons: list[QPushButton] = []
        for index, (title, _widget) in enumerate(section_widgets):
            button = QPushButton(title)
            button.setObjectName("NetPyNESectionNavButton")
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, i=index: self.sections.setCurrentIndex(i))
            rail_layout.addWidget(button)
            self.section_nav_buttons.append(button)
        rail_layout.addSpacing(12)
        run_heading = QLabel("SIMULATION")
        run_heading.setObjectName("NetPyNERailHeading")
        rail_layout.addWidget(run_heading)
        experiment_button = QPushButton("Experiment / recording")
        experiment_button.setObjectName("NetPyNESectionNavButton")
        experiment_button.setCheckable(True)
        experiment_button.clicked.connect(lambda: self.sections.setCurrentIndex(5))
        rail_layout.addWidget(experiment_button)
        self.section_nav_buttons.append(experiment_button)
        rail_layout.addStretch(1)
        self.sections.currentChanged.connect(self._sync_section_nav)
        self._sync_section_nav(self.sections.currentIndex())
        work_area.addWidget(model_rail)
        work_area.addWidget(self.sections, 1)
        shell.addLayout(work_area, 1)

        self.external_resources_label = QLabel()
        self.external_resources_label.setWordWrap(True)
        shell.addWidget(self.external_resources_label)
        self.validation_label = QLabel()
        self.validation_label.setObjectName("NetPyNEValidationStatus")
        self.validation_label.setWordWrap(True)
        shell.addWidget(self.validation_label)
        self.validation_details = QPlainTextEdit()
        self.validation_details.setObjectName("NetPyNEValidationDetails")
        self.validation_details.setReadOnly(True)
        self.validation_details.setMaximumHeight(80)
        shell.addWidget(self.validation_details)

        actions = QHBoxLayout()
        self.example_button = QPushButton("Load synthetic example")
        self.open_button = QPushButton("Open model JSON…")
        self.save_button = QPushButton("Save model JSON…")
        self.export_button = QPushButton("Export NetPyNE settings…")
        for button in (self.example_button, self.open_button, self.save_button, self.export_button):
            actions.addWidget(button)
        actions.addStretch(1)
        shell.addLayout(actions)

        self.example_button.clicked.connect(lambda: self.load_model(synthetic_two_population_example()))
        self.open_button.clicked.connect(self._choose_load)
        self.save_button.clicked.connect(self._choose_save)
        self.export_button.clicked.connect(self._choose_export)
        self.name_edit.textChanged.connect(self._draft_changed)
        self.load_model(synthetic_two_population_example())

    def _sync_section_nav(self, current_index: int) -> None:
        for index, button in enumerate(self.section_nav_buttons):
            button.setChecked(index == current_index)

    def _experiment_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        explanation = QLabel(
            "Simulation settings and recording outputs for this model. Spikes and soma voltage "
            "are the supported recording modes; select populations for voltage or leave All on."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        form = QFormLayout()
        specs = (
            ("duration_ms", "Duration (ms)", "100"),
            ("dt_ms", "Integration step (ms)", "0.025"),
            ("record_step_ms", "Voltage recording step (ms)", "0.1"),
            ("initial_voltage_mV", "Initial voltage (mV)", "-65"),
            ("spike_threshold_mV", "Spike threshold (mV)", "-20"),
            ("temperature_C", "Temperature (°C)", "34"),
            ("connection_seed", "Connection seed", "1"),
            ("stimulus_seed", "Stimulus seed", "1"),
            ("location_seed", "Location seed", "1"),
            ("cell_seed", "Cell seed", "1"),
        )
        self.experiment_edits: dict[str, QLineEdit] = {}
        for key, label, default in specs:
            edit = QLineEdit(default)
            edit.setObjectName(f"NetPyNE_{key}")
            edit.textChanged.connect(self._draft_changed)
            form.addRow(label, edit)
            self.experiment_edits[key] = edit
        layout.addLayout(form)
        self.record_spikes_check = QCheckBox("Record all spikes (raster)")
        self.record_voltage_check = QCheckBox("Record soma voltage")
        self.record_all_populations_check = QCheckBox("Record voltage from all populations")
        self.voltage_population_list = QListWidget()
        self.voltage_population_list.setObjectName("NetPyNEVoltagePopulations")
        self.voltage_population_list.setMaximumHeight(110)
        for check in (self.record_spikes_check, self.record_voltage_check, self.record_all_populations_check):
            layout.addWidget(check)
            check.toggled.connect(self._draft_changed)
        self.record_all_populations_check.toggled.connect(self._recording_controls_changed)
        self.record_voltage_check.toggled.connect(self._recording_controls_changed)
        layout.addWidget(self.voltage_population_list)
        self.voltage_population_list.itemChanged.connect(lambda _item: self._draft_changed())
        layout.addStretch(1)
        return page

    def _recording_controls_changed(self, _checked: bool) -> None:
        self.voltage_population_list.setEnabled(
            self.record_voltage_check.isChecked() and not self.record_all_populations_check.isChecked()
        )

    def _refresh_population_choices(self, *, selected: set[str] | None = None) -> None:
        if selected is None:
            selected = {
                self.voltage_population_list.item(index).text()
                for index in range(self.voltage_population_list.count())
                if self.voltage_population_list.item(index).checkState() == Qt.CheckState.Checked
            }
        labels = [row["label"].strip() for row in self.populations_table.rows() if row["label"].strip()]
        self.voltage_population_list.blockSignals(True)
        self.voltage_population_list.clear()
        for label in labels:
            item = QListWidgetItem(label)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if label in selected else Qt.CheckState.Unchecked)
            self.voltage_population_list.addItem(item)
        self.voltage_population_list.blockSignals(False)

    def _draft_changed(self, *_args: Any) -> None:
        if self._loading:
            return
        if self.sender() is self.populations_table:
            self._refresh_population_choices()
        self.model_changed.emit()
        self.refresh_validation()

    def _experiment_spec(self) -> NetPyNEExperimentSpec:
        edits = self.experiment_edits
        floats = (
            "duration_ms", "dt_ms", "record_step_ms", "initial_voltage_mV",
            "spike_threshold_mV", "temperature_C",
        )
        integers = ("connection_seed", "stimulus_seed", "location_seed", "cell_seed")
        values: dict[str, Any] = {key: _float(edits[key].text(), key) for key in floats}
        values.update({key: _int(edits[key].text(), key) for key in integers})
        values["record_spikes"] = self.record_spikes_check.isChecked()
        values["record_voltage"] = self.record_voltage_check.isChecked()
        if self.record_voltage_check.isChecked() and not self.record_all_populations_check.isChecked():
            selected = tuple(
                self.voltage_population_list.item(index).text()
                for index in range(self.voltage_population_list.count())
                if self.voltage_population_list.item(index).checkState() == Qt.CheckState.Checked
            )
            if not selected:
                raise ValueError("Select at least one voltage population or choose All populations")
            values["voltage_populations"] = selected
        else:
            values["voltage_populations"] = ()
        return NetPyNEExperimentSpec(**values)

    def _collect_model(self) -> NetPyNEWorkbenchSpec:
        cell_rules = tuple(
            CellRuleSpec(
                row["label"].strip(),
                *(_float(row[key], f"Cell rule {index + 1} {key}") for key in (
                    "diameter_um", "length_um", "axial_resistance_ohm_cm",
                    "capacitance_uF_cm2", "gnabar_S_cm2", "gkbar_S_cm2", "gl_S_cm2", "el_mV",
                )),
            )
            for index, row in enumerate(self.cell_rules_table.rows())
        )
        populations = tuple(
            PopulationSpec(row["label"].strip(), row["cell_rule"].strip(),
                           _int(row["count"], f"Population {index + 1} count"))
            for index, row in enumerate(self.populations_table.rows())
        )
        synapses = tuple(
            SynapseSpec(row["label"].strip(), row["role"],
                        _float(row["tau1_ms"], f"Synapse {index + 1} rise time"),
                        _float(row["tau2_ms"], f"Synapse {index + 1} decay time"),
                        _float(row["reversal_mV"], f"Synapse {index + 1} reversal"))
            for index, row in enumerate(self.synapses_table.rows())
        )
        connections: list[ConnectionSpec] = []
        for index, row in enumerate(self.connections_table.rows()):
            mode = row["mode"]
            value = (
                _float(row["value"], f"Connection {index + 1} probability")
                if mode == "probability" else _int(row["value"], f"Connection {index + 1} {mode}")
            )
            connections.append(ConnectionSpec(
                label=row["label"].strip(), pre_population=row["pre_population"].strip(),
                post_population=row["post_population"].strip(), synapse=row["synapse"].strip(),
                weight_uS=_float(row["weight_uS"], f"Connection {index + 1} weight"),
                delay_ms=_float(row["delay_ms"], f"Connection {index + 1} delay"),
                **{mode: value},
            ))
        stimuli = tuple(
            NetStimSpec(
                row["label"].strip(), row["target_population"].strip(), row["synapse"].strip(),
                _float(row["rate_hz"], f"NetStim {index + 1} rate"),
                _float(row["noise"], f"NetStim {index + 1} noise"),
                _float(row["start_ms"], f"NetStim {index + 1} start"),
                _float(row["weight_uS"], f"NetStim {index + 1} weight"),
                _float(row["delay_ms"], f"NetStim {index + 1} delay"),
                _int(row["number"], f"NetStim {index + 1} max events"),
            )
            for index, row in enumerate(self.stimuli_table.rows())
        )
        return NetPyNEWorkbenchSpec(
            name=self.name_edit.text().strip(), cell_rules=cell_rules,
            populations=populations, synapses=synapses, connections=tuple(connections),
            stimuli=stimuli, experiment=self._experiment_spec(),
            external_resources=self._external_resources,
        )

    def model_spec(self) -> NetPyNEWorkbenchSpec:
        """Return a valid workbench document or raise a concrete validation error."""
        model = self._collect_model()
        model.require_valid()
        return model

    def refresh_validation(self) -> bool:
        """Show all semantic errors, and gate save/export on the draft state."""
        errors: list[str] = []
        export_error = ""
        try:
            model = self._collect_model()
            errors = model.errors()
            if not errors:
                try:
                    model.require_exportable()
                except ValueError as exc:
                    export_error = str(exc)
        except ValueError as exc:
            errors = [str(exc)]
        valid = not errors
        self.save_button.setEnabled(valid)
        self.export_button.setEnabled(valid and not export_error)
        if errors:
            self.validation_label.setText(f"Needs attention: {len(errors)} validation issue(s)")
            self.validation_details.setPlainText("\n".join(f"• {error}" for error in errors))
        elif export_error:
            self.validation_label.setText("Model valid; NetPyNE export unavailable for external model inputs")
            self.validation_details.setPlainText(export_error)
        else:
            self.validation_label.setText("Model valid · ready to save or export settings")
            self.validation_details.setPlainText("No simulation runs when settings are exported.")
        self.validation_changed.emit(valid, self.validation_label.text())
        return valid

    def load_model(self, model: NetPyNEWorkbenchSpec) -> None:
        model.require_valid()
        self._loading = True
        self.current_path = None
        self.name_edit.setText(model.name)
        self.cell_rules_table.replace_rows(model.cell_rules)
        self.populations_table.replace_rows(model.populations)
        self.synapses_table.replace_rows(model.synapses)
        connection_rows: list[dict[str, Any]] = []
        for connection in model.connections:
            mode = next(
                key for key in ("probability", "convergence", "divergence")
                if getattr(connection, key) is not None
            )
            connection_rows.append({
                **{key: getattr(connection, key) for key in (
                    "label", "pre_population", "post_population", "synapse", "weight_uS", "delay_ms"
                )},
                "mode": mode, "value": getattr(connection, mode),
            })
        self.connections_table._loading = True
        self.connections_table.table.setRowCount(0)
        for row in connection_rows:
            self.connections_table.add_row(row)
        self.connections_table._loading = False
        self.stimuli_table.replace_rows(model.stimuli)
        self._external_resources = model.external_resources
        self.external_resources_label.setText(
            f"External references: {len(self._external_resources)}. "
            "References are preserved in model JSON, never imported here."
            if self._external_resources else "External references: none (no SWC or dataset is bundled)."
        )
        for key, edit in self.experiment_edits.items():
            edit.setText(str(getattr(model.experiment, key)))
        self.record_spikes_check.setChecked(model.experiment.record_spikes)
        self.record_voltage_check.setChecked(model.experiment.record_voltage)
        self.record_all_populations_check.setChecked(not model.experiment.voltage_populations)
        self._refresh_population_choices(selected=set(model.experiment.voltage_populations))
        self._recording_controls_changed(False)
        self._loading = False
        self.model_changed.emit()
        self.refresh_validation()

    def load_file(self, path: str | Path) -> NetPyNEWorkbenchSpec:
        """Load a strict versioned JSON document, leaving the UI untouched on failure."""
        model = load_workbench_spec(path)
        self.load_model(model)
        self.current_path = Path(path).resolve()
        return model

    def save_file(self, path: str | Path) -> Path:
        """Atomically save the editable workbench document to an explicit path."""
        destination = save_workbench_spec(path, self.model_spec())
        self.current_path = destination.resolve()
        return destination

    def export_payload(self) -> tuple[dict[str, Any], dict[str, Any]]:
        """Validate the complete draft before deriving NetPyNE network/sim settings."""
        return to_netpyne_payload(self.model_spec())

    def export_settings(self, path: str | Path) -> Path:
        """Atomically save a settings snapshot, not a simulation or editable model file."""
        network, simulation = self.export_payload()
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        document = {
            "format": "digifly-netpyne-settings-v1",
            "model_name": self.name_edit.text().strip(),
            "network": network,
            "simulation": simulation,
        }
        contents = json.dumps(document, indent=2, sort_keys=True, allow_nan=False) + "\n"
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=destination.parent,
                prefix=f".{destination.name}.", suffix=".tmp", delete=False,
            ) as stream:
                temporary = Path(stream.name)
                stream.write(contents)
            os.replace(temporary, destination)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return destination

    def _show_file_error(self, title: str, operation: Callable[[], Any]) -> None:
        try:
            operation()
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            QMessageBox.warning(self, title, str(exc))

    def _choose_load(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open NetPyNE model", "", "JSON files (*.json)")
        if path:
            self._show_file_error("Could not open model", lambda: self.load_file(path))

    def _choose_save(self) -> None:
        start = str(self.current_path) if self.current_path else "netpyne-model.json"
        path, _ = QFileDialog.getSaveFileName(self, "Save NetPyNE model", start, "JSON files (*.json)")
        if path:
            self._show_file_error("Could not save model", lambda: self.save_file(path))

    def _choose_export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export NetPyNE settings", "netpyne-settings.json", "JSON files (*.json)"
        )
        if path:
            self._show_file_error("Could not export settings", lambda: self.export_settings(path))
