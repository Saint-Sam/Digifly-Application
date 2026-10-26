from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from digifly_app.ui.voltage_plot import (
    MODE_3D_STACK,
    VoltagePlotWidget,
    VOLTAGE_AXIS_LABEL,
    load_voltage_traces,
    voltage_at,
)


def _write_voltage_csv(path: Path) -> Path:
    path.write_text(
        "condition,repetition,neuron_id,time_ms,voltage_mV\n"
        "Control,1,10000,0,-60\n"
        "Control,1,10000,1,20\n"
        "Control,1,10110,0,-65\n"
        "Control,1,10110,1,-45\n"
        "Gap disabled,1,10000,0,-62\n"
        "Gap disabled,1,10000,1,10\n",
        encoding="utf-8",
    )
    return path


def test_voltage_csv_loads_labeled_long_form_traces(tmp_path):
    traces = load_voltage_traces(
        _write_voltage_csv(tmp_path / "voltage_traces.csv"),
        neuron_labels={"10000": "DNp01 · body 10000", "10110": "TTMn · body 10110"},
    )
    assert len(traces) == 3
    assert traces[0].label == "Control · DNp01 · body 10000"
    assert traces[1].label == "Control · TTMn · body 10110"
    assert voltage_at(traces[0], 0.5) == pytest.approx(-20.0)


def test_regional_voltage_rows_are_separate_source_edge_traces(tmp_path):
    assert VOLTAGE_AXIS_LABEL == "membrane voltage (mV)"
    source=tmp_path/"voltage.csv"
    source.write_text("condition,repetition,neuron_id,compartment_id,time_ms,voltage_mV\nControl,1,1,3,0,-60\nControl,1,1,4,0,-65\nControl,1,1,3,1,20\nControl,1,1,4,1,-45\n")
    traces=load_voltage_traces(source)
    assert len(traces)==2 and len({t.key for t in traces})==2
    assert [t.compartment_id for t in traces]==["3","4"]
    assert all(len(t.times_ms)==2 and "SWC" in t.label for t in traces)
    assert voltage_at(traces[0],.5)==-20 and voltage_at(traces[1],.5)==-55


def test_clickable_legend_fades_filter_scales_plot_and_3d_renders(tmp_path):
    application = QApplication.instance() or QApplication([])
    traces = load_voltage_traces(_write_voltage_csv(tmp_path / "voltage_traces.csv"))
    widget = VoltagePlotWidget()
    try:
        widget.resize(800, 620)
        widget.set_traces(traces)
        first = traces[0]
        widget.legend_buttons[first.key].click()
        application.processEvents()
        assert first.key not in widget.canvas.active_keys
        assert widget.trace_count == 3
        assert "transparent" in widget.legend_buttons[first.key].accessibleDescription()

        # Accessible state actions must update the plotted emphasis too, not
        # merely the check mark (they need not emit the mouse-only clicked).
        widget.legend_buttons[first.key].setChecked(True)
        assert first.key in widget.canvas.active_keys
        widget.legend_buttons[first.key].setChecked(False)
        assert first.key not in widget.canvas.active_keys

        widget.legend_filter.setText("10110")
        application.processEvents()
        visible = [
            key for key, button in widget.legend_buttons.items() if not button.isHidden()
        ]
        assert visible == [traces[1].key]
        assert widget.canvas.visible_keys == {traces[1].key}
        assert widget.canvas._voltage_bounds == pytest.approx((-67.0, -43.0))
        assert widget.trace_count_label.text().startswith("1 of 3 plotted")
        widget.legend_filter.clear()
        assert widget.canvas.visible_keys == {trace.key for trace in traces}

        widget.set_mode(MODE_3D_STACK)
        widget.set_time_cursor(0.5)
        image = widget.render_high_resolution(width=1200)
        assert widget.mode == MODE_3D_STACK
        assert image.width() == 1200
        assert image.height() > 800
        assert not image.isNull()
    finally:
        widget.close()
        application.processEvents()


def test_playback_cursor_reuses_cached_trace_layer(tmp_path):
    application = QApplication.instance() or QApplication([])
    traces = load_voltage_traces(_write_voltage_csv(tmp_path / "voltage_traces.csv"))
    widget = VoltagePlotWidget()
    try:
        widget.resize(800, 620)
        widget.set_traces(traces)
        widget.canvas.grab()
        assert widget.canvas._static_layer is not None
        initial_cache_key = widget.canvas._static_layer.cacheKey()

        widget.set_time_cursor(0.5)
        widget.canvas.grab()
        assert widget.canvas._static_layer.cacheKey() == initial_cache_key

        widget.legend_buttons[traces[0].key].click()
        widget.canvas.grab()
        assert widget.canvas._static_layer.cacheKey() != initial_cache_key
    finally:
        widget.close()
        application.processEvents()
