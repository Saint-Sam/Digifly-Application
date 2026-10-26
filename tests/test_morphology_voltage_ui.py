from pathlib import Path
import json
import hashlib
from dataclasses import replace
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QSettings
from digifly_app.core.connectomes import NeuronRecord
from digifly_app.core.morphology import Morphology, SwcNode, SwcSegment
from digifly_app.ui.main_window import MainWindow
from digifly_app.ui.circuit_viewport import CircuitViewport
from digifly_app.ui.voltage_plot import MODE_2D


def morphology():
    record = NeuronRecord("a", "MN", "Example", "/tmp/a.swc", "custom")
    nodes = (SwcNode(1, 1, 0, 0, 0, 2, -1), SwcNode(2, 3, 10, 0, 0, 1, 1), SwcNode(3, 3, 20, 0, 0, 1, 2))
    edges = (SwcSegment(2, 1, (10, 0, 0), (0, 0, 0), 1, 3), SwcSegment(3, 2, (20, 0, 0), (10, 0, 0), 1, 3))
    return Morphology(record, nodes, edges, (0, 20, 0, 0, 0, 0))


def test_measured_overlay_preserves_identity_and_clears_without_spreading():
    app = QApplication.instance() or QApplication([])
    viewport = CircuitViewport(camera_only=True)
    try:
        viewport.set_morphologies([morphology()])
        viewport.set_measured_voltages({"a": {2: -60, 3: 20}})
        assert viewport.activity_segment_count == 2
        assert len(viewport._voltage_ranges) == 2
        assert viewport._voltage_ranges[0][2] != viewport._voltage_ranges[1][2]
        viewport.set_measured_voltages({"a": {2: -60}})
        assert viewport.activity_segment_count == 1
        with pytest.raises(ValueError):
            viewport.set_measured_voltages({"a": {999: -60}})
        with pytest.raises(ValueError):
            viewport.set_measured_voltages({"a": {2: float("nan")}})
        viewport.clear_activity_flow()
        assert viewport.activity_segment_count == 0
        assert not viewport._measured_voltage_mode
    finally:
        viewport.close()


def test_results_measured_playback_needs_no_spikes_and_switches_back(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    old_format = QSettings.defaultFormat()
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    monkeypatch.setenv("DIGIFLY_WORKSTATION_PROFILE", str(tmp_path/"absent.json"))
    monkeypatch.setenv("DIGIFLY_WORKSTATION_SETTINGS_ROOT", str(tmp_path/"settings"))
    folder = tmp_path / "morphologies/a"
    folder.mkdir(parents=True)
    swc = folder/"normalized_input.swc"
    swc.write_text("1 1 0 0 0 2 -1\n2 3 10 0 0 1 1\n3 3 20 0 0 1 2\n")
    (tmp_path/"run_manifest.json").write_text(json.dumps({"morphologies":{"a":{"simulation_swc_sha256":hashlib.sha256(swc.read_bytes()).hexdigest()}}}))
    recorded = replace(morphology(), record=replace(morphology().record, swc_path=str(swc)))
    (folder/"regional_map.json").write_text(json.dumps({"neuron_id":"a", "sections":[{"node_id":2,"source_node_id":20,"is_target_soma":True}, {"node_id":3,"source_node_id":30}]}))
    (folder/"morphology_node_map.csv").write_text("source_node_id,run_node_id\n10,1\n20,2\n30,3\n")
    (tmp_path/"voltage_traces.csv").write_text("condition,repetition,neuron_id,compartment_id,time_ms,voltage_mV\nControl,0,a,20,1,-60\nControl,0,a,20,2,-50\nControl,0,a,30,1,-45\nControl,0,a,30,2,-40\n")
    window = MainWindow()
    try:
        page = window.results_page
        page.result_viewport.set_morphologies([recorded])
        page._configure_activity_playback(tmp_path, [recorded], set())
        assert page.voltage_morphology_button.isEnabled()
        assert not page.activity_slider.isEnabled()  # inferred spikes absent
        # Exercise state-only activation as well as ordinary pointer clicks.
        page.voltage_morphology_button.setChecked(True)
        assert page.activity_slider.isEnabled()
        assert "Measured edge-center" in page.activity_method.text()
        assert page.result_viewport.activity_segment_count == 2
        page.activity_slider.setValue(1)
        assert page.activity_time_label.text() == "2.00 ms"
        # Actual trace data is available in ordinary Results; set minimal traces
        # here to exercise returning from the morphology mode.
        from digifly_app.ui.voltage_plot import load_voltage_traces
        page.voltage_plot.set_traces(load_voltage_traces(tmp_path/"voltage_traces.csv"))
        page.voltage_2d_button.setEnabled(True)
        page.voltage_2d_button.setChecked(True)
        assert not page._morphology_voltage_active
        assert page.voltage_plot.mode == MODE_2D
        assert page.result_viewport.activity_segment_count == 0
        page._reset_activity_playback()
        assert not page.voltage_morphology_button.isEnabled()
    finally:
        window.close()
        QSettings.setDefaultFormat(old_format)
