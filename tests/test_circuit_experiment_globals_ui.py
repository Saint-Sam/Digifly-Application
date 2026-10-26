from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QLineEdit

from digifly_app.core.circuit import CircuitSpec, HodgkinHuxleySpec
from digifly_app.core.connectomes import NeuronRecord
from digifly_app.ui.circuit_builder import CircuitBuilderPage
from digifly_app.ui.experiment_builder import ExperimentBuilderPage


class _OverviewStub:
    def __init__(self, root: Path):
        self.workspace_edit = QLineEdit(str(root))


def test_explicit_global_apply_transfers_both_settings_without_load_side_effects(tmp_path):
    app = QApplication.instance() or QApplication([])
    circuit = CircuitBuilderPage(_OverviewStub(tmp_path))
    experiment = ExperimentBuilderPage(output_root=tmp_path)
    circuit.circuit_changed.connect(experiment.set_circuit_spec)
    circuit.experiment_globals_applied.connect(experiment.apply_circuit_experiment_globals)
    emitted = []
    circuit.experiment_globals_applied.connect(lambda voltage, temp: emitted.append((voltage, temp)))
    try:
        experiment.initial_voltage.setValue(-71.0)
        experiment.temperature.setValue(19.0)
        legacy = CircuitSpec(hh=HodgkinHuxleySpec(v_init_mV=-55.0, celsius_C=25.0))
        legacy.neuron_overrides["1"] = {"v_init_mV": -51.0, "celsius_C": 17.0}
        before = legacy.to_dict()
        experiment.set_circuit_spec(legacy)
        assert experiment.initial_voltage.value() == -71.0
        assert experiment.temperature.value() == 19.0
        assert legacy.to_dict() == before
        assert experiment.circuit_spec().neuron_overrides["1"] == before["neuron_overrides"]["1"]
        circuit.hh_editors["v_init_mV"].setValue(-63.0)
        circuit.hh_editors["celsius_C"].setValue(24.5)
        circuit._set_hh(circuit._read_hh())  # Rendering is not an explicit Apply action.
        assert emitted == []
        assert experiment.initial_voltage.value() == -71.0
        circuit.apply_cell_set_defaults()
        assert emitted == [(-63.0, 24.5)]
        assert experiment.config().initial_voltage_mV == -63.0
        assert experiment.config().temperature_C == 24.5
        assert circuit.spec.hh.v_init_mV == -63.0
        assert circuit.spec.hh.celsius_C == 24.5
        labels = [label.text() for label in circuit.findChildren(QLabel)]
        assert "Experiment-global initial voltage" in labels
        assert "Experiment-global temperature" in labels
        assert "loading a circuit" in experiment.global_settings_help.text()
        profile_index = circuit.channel_profile_combo.findData("escape_siz_para_hh_k")
        assert profile_index >= 0
        circuit.channel_profile_combo.setCurrentIndex(profile_index)
        experiment.set_circuit_spec(circuit.spec)
        assert emitted == [(-63.0, 24.5)]
        assert experiment.temperature.value() == 24.5
    finally:
        circuit.close(); experiment.close(); app.processEvents()


def test_regional_apply_omits_globals_and_mass_apply_transfers_them_once(tmp_path):
    app = QApplication.instance() or QApplication([])
    circuit = CircuitBuilderPage(_OverviewStub(tmp_path))
    emitted, messages = [], []
    circuit.experiment_globals_applied.connect(lambda voltage, temp: emitted.append((voltage, temp)))
    circuit.status_message.connect(messages.append)
    try:
        circuit.spec.neuron_ids = ("1", "2")
        circuit.loaded_records = {
            key: NeuronRecord(key, "control", "synthetic", str(tmp_path / f"{key}.swc"), "custom:control")
            for key in circuit.spec.neuron_ids
        }
        circuit.viewport.selected_neuron_id = "1"
        circuit.viewport.selected_neuron_ids = ["1"]
        circuit.viewport.selected_compartments = {2}
        circuit.hh_editors["v_init_mV"].setValue(-59.0)
        circuit.hh_editors["celsius_C"].setValue(23.0)
        circuit.hh_editors["cm_uF_cm2"].setValue(2.0)
        circuit.apply_neuron_override()
        circuit.apply_compartment_overrides()
        for values in (circuit.spec.neuron_overrides["1"], circuit.spec.compartment_overrides["1"]["2"]):
            assert "v_init_mV" not in values
            assert "celsius_C" not in values
            assert values["cm_uF_cm2"] == 2.0
        assert emitted == []
        assert "experiment-global voltage and temperature are unchanged" in messages[-1]
        circuit.mass_apply_to_loaded_neurons()
        assert emitted == [(-59.0, 23.0)]
        for key in ("1", "2"):
            assert "v_init_mV" not in circuit.spec.neuron_overrides[key]
            assert "celsius_C" not in circuit.spec.neuron_overrides[key]
            assert circuit.spec.neuron_overrides[key]["cm_uF_cm2"] == 2.0
        assert "sent global voltage and temperature" in messages[-1]
    finally:
        circuit.close(); app.processEvents()


def test_empty_mass_apply_does_not_transfer_globals(tmp_path):
    app = QApplication.instance() or QApplication([])
    circuit = CircuitBuilderPage(_OverviewStub(tmp_path))
    emitted = []
    circuit.experiment_globals_applied.connect(lambda *values: emitted.append(values))
    try:
        circuit.mass_apply_to_loaded_neurons()
        assert emitted == []
    finally:
        circuit.close(); app.processEvents()


@pytest.mark.parametrize("voltage,temp", [
    (float("nan"), 22.0), (-60.0, float("inf")), (True, 22.0),
    (-60.0, False), (-301.0, 22.0), (-60.0, 201.0), ("-60", 22.0),
])
def test_global_receiver_rejects_invalid_values_atomically(tmp_path, voltage, temp):
    app = QApplication.instance() or QApplication([])
    page = ExperimentBuilderPage(output_root=tmp_path)
    try:
        before = (page.initial_voltage.value(), page.temperature.value())
        with pytest.raises(ValueError, match="experiment-global"):
            page.apply_circuit_experiment_globals(voltage, temp)
        assert (page.initial_voltage.value(), page.temperature.value()) == before
    finally:
        page.close(); app.processEvents()
