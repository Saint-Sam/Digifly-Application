from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from digifly_app.core.circuit import CircuitSpec
from digifly_app.core.experiment import ConditionSpec, ExperimentSpec
from digifly_app.core.models import CheckState, PreflightCheck, PreflightReport
from digifly_app.ui.experiment_builder import ExperimentBuilderPage


def test_changed_source_clears_geometry_and_downloaded_table_stays_explicit(tmp_path):
    from dataclasses import replace
    from digifly_app.core.circuit import ConnectomeRef
    from digifly_app.core.connectomes import NeuronRecord
    from digifly_app.core.morphology import load_swc
    app = QApplication.instance() or QApplication([])
    swc = tmp_path / "1.swc"
    swc.write_text("1 1 0 0 0 2 -1\n2 3 1 0 5 1 1\n")
    source = ConnectomeRef("library:male-cns:v0.9", "Male CNS", str(tmp_path), "male-cns:v0.9")
    circuit = CircuitSpec(connectome=source, neuron_ids=("1",))
    morphology = load_swc(NeuronRecord("1", "DN", "DNp01", str(swc), source.key))
    page = ExperimentBuilderPage(output_root=tmp_path)
    try:
        page.set_circuit_snapshot(circuit, (morphology,))
        assert page.target_region_viewport.roll_degrees == 180
        paired = replace(source, pair_graph_path=str(tmp_path / "downloaded.parquet"),
                         pair_graph_index=str(tmp_path / "pairs.sqlite"), pair_graph_sha256="a" * 64)
        page.set_circuit_spec(replace(circuit, connectome=paired))
        assert page._morphologies == (morphology,)
        assert page._circuit.connectome == paired
        assert "downloaded.parquet" in page.circuit_detail.text()
        assert "source details unavailable" in page.circuit_detail.text()
        assert "No other contact source" in page.circuit_detail.text()
        changed = replace(source, key="library:male-cns:v1.0", dataset="male-cns:v1.0")
        page.set_circuit_spec(replace(circuit, connectome=changed))
        assert not page._morphologies
        assert not page.target_region_viewport.morphologies
        # Identical files and body IDs cannot suppress a new source's camera.
        other = replace(morphology, record=replace(morphology.record, connectome_key="library:manc:v1.2.1"))
        page.set_circuit_snapshot(circuit, (morphology,))
        page.set_circuit_snapshot(replace(circuit, connectome=replace(source, key=other.record.connectome_key)), (other,))
        assert page.target_region_viewport.roll_degrees == 0
    finally:
        page.close()
        app.processEvents()


def test_control_connection_flags_survive_load_edit_and_roundtrip(tmp_path):
    app = QApplication.instance() or QApplication([])
    page = ExperimentBuilderPage(output_root=tmp_path)
    try:
        original = ExperimentSpec(conditions=(
            ConditionSpec(name="Control", gap_junctions_enabled=False,
                          chemical_synapses_enabled=False),
            ConditionSpec(name="Chemical on", gap_junctions_enabled=False,
                          chemical_synapses_enabled=True),
        ))
        page.set_config(original)
        assert page.config().conditions == original.conditions
        page.control_gap_enabled.setChecked(True)
        changed = page.config()
        assert changed.conditions[0].gap_junctions_enabled
        assert not changed.conditions[0].chemical_synapses_enabled
        assert changed.conditions[1] == original.conditions[1]
        page.set_config(ExperimentSpec.from_dict(changed.to_dict()))
        assert page.config().conditions == changed.conditions
        page.set_config(ExperimentSpec())
        assert page.control_gap_enabled.isChecked()
        assert page.control_chemical_enabled.isChecked()
        assert not list(tmp_path.iterdir())
    finally:
        page.close()
        app.processEvents()


def test_simulator_selector_is_keyboard_reachable_and_preserves_protocol(tmp_path):
    app = QApplication.instance() or QApplication([])
    page = ExperimentBuilderPage(output_root=tmp_path)
    try:
        page.show()
        app.processEvents()
        assert page.engine_combo.focusPolicy() == Qt.FocusPolicy.StrongFocus
        before = page.config().to_dict()
        page.name_edit.setFocus()
        visited = []
        for _ in range(8):
            QTest.keyClick(app.focusWidget(), Qt.Key.Key_Tab)
            visited.append(app.focusWidget())
            if app.focusWidget() == page.engine_combo:
                break
        assert page.template_combo in visited
        assert app.focusWidget() == page.engine_combo
        QTest.keyClick(page.engine_combo, Qt.Key.Key_Down)
        assert page.engine_combo.currentData() == "neuron"
        QTest.keyClick(page.engine_combo, Qt.Key.Key_Down)
        assert page.engine_combo.currentData() == "bmtk"
        after = page.config().to_dict()
        before.pop("engine"); after.pop("engine")
        assert before == after
    finally:
        page.close()
        app.processEvents()


def test_unbuilt_gap_cache_is_preparation_not_an_unsupported_setting(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    page = ExperimentBuilderPage(output_root=tmp_path)
    page.set_circuit_spec(CircuitSpec(neuron_ids=("1", "2")))
    prepare = PreflightCheck("neuron_gap_mechanisms", "App-owned NEURON gap mechanisms",
                             CheckState.FAIL, "Mechanisms are not built yet. Run will compile them once.", blocking=True)
    monkeypatch.setattr("digifly_app.ui.experiment_builder.GenericExperimentAdapter.validate",
                        lambda *a, **kw: PreflightReport((prepare,)))
    try:
        page.validate_draft()
        assert "Preparation required" in page.validation_state.text()
        assert "Preflight passed" not in page.validation_state.text()
        assert not list(tmp_path.iterdir())
        missing = PreflightCheck("runtime", "Runtime missing", CheckState.FAIL,
                                 "Choose an installed runtime.", blocking=True)
        monkeypatch.setattr("digifly_app.ui.experiment_builder.GenericExperimentAdapter.validate",
                            lambda *a, **kw: PreflightReport((prepare, missing)))
        page.validate_draft()
        assert "Cannot run · 1 issue(s)" in page.validation_state.text()
        assert "Runtime missing" in page.validation_state.text()
        assert not list(tmp_path.iterdir())
    finally:
        page.close()
        app.processEvents()


def test_validate_checks_execution_capabilities_without_creating_run(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    page = ExperimentBuilderPage(output_root=tmp_path)
    page.set_circuit_spec(CircuitSpec(neuron_ids=("10110", "10068")))
    checks = (
        PreflightCheck("mechanisms", "Membrane mechanism translation", CheckState.FAIL,
                       "Native para is not executable in this lane.", blocking=True),
        PreflightCheck("recording", "Recording translation", CheckState.FAIL,
                       "Choose Soma and enable membrane voltage.", blocking=True),
    )
    calls = []

    def validate(adapter, circuit, config, morphologies, *, output_root):
        calls.append((circuit, config, output_root))
        return PreflightReport(checks)

    monkeypatch.setattr("digifly_app.ui.experiment_builder.GenericExperimentAdapter.validate", validate)
    try:
        page.validate_draft()
        assert len(calls) == 1
        assert "Cannot run · 2 issue(s)" in page.validation_state.text()
        assert "para" in page.document_preview.toPlainText()
        assert "Choose Soma" in page.validation_state.toolTip()
        assert not list(tmp_path.iterdir())
        assert page._process is None

        monkeypatch.setattr("digifly_app.ui.experiment_builder.GenericExperimentAdapter.validate",
                            lambda *args, **kwargs: PreflightReport(()))
        page.validate_draft()
        assert "Preflight passed" in page.validation_state.text()
        assert not page.validation_state.toolTip()
        assert not list(tmp_path.iterdir())
    finally:
        page.close()
        app.processEvents()
