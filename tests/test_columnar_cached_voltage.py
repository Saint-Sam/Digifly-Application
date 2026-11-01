"""Dense saved tables keep every sample without eager per-trace objects."""
import csv
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')

import pytest
from PySide6.QtWidgets import QApplication

from digifly_app.core.result_voltage import load_voltage_table
from digifly_app.ui.voltage_plot import load_voltage_traces, prepare_trace_plot, VoltagePlotWidget
from digifly_app.ui.result_playback import load_morphology_voltage_tracks, measured_segment_voltages


def dense(tmp_path, count=1000):
    path = tmp_path/'voltage_traces.csv'
    with path.open('w',newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(('condition','repetition','neuron_id','compartment_id','time_ms','voltage_mV'))
        for condition in ('Control','No stimulus'):
            for edge in range(1,count+1):
                for clock in (0.,.02,.04):
                    writer.writerow((condition,1,'1',edge,clock,edge*.001+clock))
    table = load_voltage_table(path,cache_root=tmp_path/'cache')
    return path,table


def materialized(table):
    return getattr(table.series,'materialized_count',len(table.series))


def test_cached_reader_and_plot_indices_do_not_eagerly_materialize_rows(tmp_path):
    path,table = dense(tmp_path)
    assert table.sample_count == 6000 and len(table.series) == 2000
    assert materialized(table) <= 128
    traces = load_voltage_traces(path,table=table)
    prepared = prepare_trace_plot(traces)
    assert len(traces) == 2000 and prepared.sample_count == 6000
    assert prepared.traces is traces and len(prepared.keys) == 2000
    assert materialized(table) <= 128
    first,last = traces[0],traces[-1]
    assert tuple(first.times_ms) == (0.,.02,.04)
    assert tuple(first.voltages_mV) == pytest.approx((.001,.021,.041),abs=1e-12)
    assert tuple(last.voltages_mV) == pytest.approx((1.,1.02,1.04),abs=1e-12)
    assert first.voltages_mV.readonly


def test_measured_replay_uses_full_values_without_eager_series_objects(tmp_path):
    _,table = dense(tmp_path)
    tracks = load_morphology_voltage_tracks(tmp_path,(),table=table,
        edge_maps={'1':{edge:edge for edge in range(1,1001)}})
    assert len(tracks) == 2 and all(len(track.samples_by_segment)==1000 for track in tracks)
    for track in tracks:
        for clock in (0.,.01,.02,.04):
            values = measured_segment_voltages(track,clock)
            assert len(values['1']) == 1000
            assert all(abs(value-(edge*.001+clock)) < 1e-12 for edge,value in values['1'].items())
        assert measured_segment_voltages(track,-.01) == {}
        assert measured_segment_voltages(track,.05) == {}
    assert materialized(table) <= 128


def test_filter_paging_and_emphasis_keep_lazy_rows_bounded(tmp_path):
    app = QApplication.instance() or QApplication([])
    path,table = dense(tmp_path)
    traces = load_voltage_traces(path,table=table)
    widget = VoltagePlotWidget()
    widget.set_traces(traces,prepared=prepare_trace_plot(traces))
    assert widget.trace_count == 2000 and widget.sample_count == 6000
    assert len(widget.legend_buttons) == 60
    assert materialized(table) <= 512
    for _ in range(4):
        widget._change_legend_page(1)
        app.processEvents()
    widget.legend_filter.setText('body 1 · SWC 1, body 1 · SWC 500')
    app.processEvents()
    expected = {trace.key for trace in traces if any(
        query in trace.label.casefold() for query in ('body 1 · swc 1','body 1 · swc 500'))}
    assert {trace.key for trace in widget._legend_matches} == expected
    widget.show_all()
    widget.legend_filter.clear()
    app.processEvents()
    assert len(widget._legend_matches) == 2000 and len(widget.legend_buttons) == 60
    assert materialized(table) <= 512
    widget.close()


def test_compact_replay_matches_independent_csv_reader_with_mixed_clocks(tmp_path):
    from dataclasses import replace
    path = tmp_path/'voltage_traces.csv'
    rows = [
        ('Control',1,'1','10',0.,-65.),('Control',1,'1','10',.03,40.),
        ('Control',1,'1','10',.05,-72.),('Control',1,'1','10',.1,-65.),
        ('Control',1,'1','20',.02,-60.),('Control',1,'1','20',.04,-30.),
        ('Control',1,'1','20',.09,15.),('Control',1,'2','30',0.,-65.),
        ('Control',1,'2','30',.01,-62.),('Control',1,'2','30',.04,-80.),
        ('No stimulus',2,'1','10',.01,-50.),('No stimulus',2,'1','10',.02,-52.),
        ('No stimulus',2,'1','10',.08,-51.)]
    with path.open('w',newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(('condition','repetition','neuron_id','compartment_id','time_ms','voltage_mV'))
        writer.writerows(rows)
    bindings = {'1':{10:501,20:502},'2':{30:601}}
    reference = {}
    for trace in load_voltage_traces(path):
        reference.setdefault((trace.condition,trace.repetition),{})[
            (trace.neuron_id,bindings[trace.neuron_id][int(trace.compartment_id)])] = tuple(zip(trace.times_ms,trace.voltages_mV))
    table = load_voltage_table(path,cache_root=tmp_path/'cache')
    tracks = load_morphology_voltage_tracks(tmp_path,(),table=table,edge_maps=bindings)
    for track in tracks:
        legacy = replace(track,samples_by_segment=reference[(track.condition,track.repetition)])
        for clock in (-.01,0.,.005,.01,.015,.02,.025,.03,.04,.05,.06,.08,.09,.1,.11):
            actual = measured_segment_voltages(track,clock)
            expected = measured_segment_voltages(legacy,clock)
            assert set(actual) == set(expected)
            for neuron,values in expected.items():
                assert actual[neuron] == pytest.approx(values,rel=1e-12,abs=1e-12)


def test_lazy_equality_preserves_lossless_reopen_and_detects_changed_values(tmp_path):
    path,table = dense(tmp_path)
    first = load_voltage_traces(path,table=table)
    reopened_table = load_voltage_table(path,cache_root=tmp_path/'cache')
    reopened = load_voltage_traces(path,table=reopened_table)
    assert first == reopened and materialized(table) <= 128 and materialized(reopened_table) <= 128
    path.write_text(path.read_text().replace('0.001','0.002',1))
    changed_table = load_voltage_table(path,cache_root=tmp_path/'cache')
    changed = load_voltage_traces(path,table=changed_table)
    assert first != changed


def test_equal_payload_bytes_with_different_clock_blocks_are_not_equal_traces(tmp_path):
    from array import array
    from digifly_app.core.result_voltage import MappedSeries, VoltageTable
    flat = memoryview(array('d',[0.,.02,0.,.04,-65.,40.]).tobytes()).cast('d')
    stores = []
    for block in ((0,2),(16,2)):
        start,length = block
        store = MappedSeries(flat,(flat[start//8:start//8+length],),payload_sha256='same-checked-payload')
        store.clock_blocks = (block,)
        store.add(('Control',1,'1','2'),0,32,True,(-65.,40.))
        stores.append(store)
    traces = [load_voltage_traces(tmp_path/'unused.csv',table=VoltageTable(series=store,sample_count=2))
              for store in stores]
    assert traces[0] != traces[1]
