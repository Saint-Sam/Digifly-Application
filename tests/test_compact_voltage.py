"""Lossless compact storage and finite-budget regressions; no simulation."""
from array import array
import csv
import io
import math
import pytest
from digifly_app.workers.result_storage import SampleSeries, VoltageRows
from digifly_app.ui.voltage_plot import load_voltage_traces, voltage_at


def test_compact_series_preserves_values_order_and_duplicate_refusal():
    series = SampleSeries()
    for t, v in ((2., .123456789012345), (0., -65.), (1., 42.)):
        series.append(t, v)
    series.finish(unique=True)
    assert tuple(series)==((0.,-65.),(1.,42.),(2.,.123456789012345))
    assert series[1:]==((1.,42.),(2.,.123456789012345))
    series.append(1.,-10.)
    with pytest.raises(ValueError,match='Duplicate'):
        series.finish(unique=True)


def test_compact_numeric_payload_is_bounded_without_decimation():
    series=SampleSeries()
    for i in range(100_000):series.append(i*.02,math.sin(i))
    assert len(series)==100_000
    assert series.times.itemsize==series.values.itemsize==8
    assert (len(series.times)+len(series.values))*8==1_600_000
    assert series[-1]==(99999*.02,math.sin(99999))


def test_lazy_rows_retain_every_trace_and_share_exact_clocks():
    rows=VoltageRows()
    for i in range(50):rows.add('Control',1,str(i),'2',[0.,.02,.04],[-65.,i+.125,-60.])
    assert len(rows)==150 and len(list(rows))==150
    assert rows.traces[0][4] is rows.traces[-1][4]
    assert len(list(rows.overview()))==24*3
    output=io.StringIO()
    writer=csv.DictWriter(output,fieldnames=next(iter(rows)).keys())
    writer.writeheader();writer.writerows(rows)
    assert len(list(csv.DictReader(io.StringIO(output.getvalue()))))==150
    with pytest.raises(ValueError,match='Incomplete'):
        rows.add('Control',1,'bad','2',[0.,.02],[-65.])


def test_real_voltage_reader_retains_unsorted_full_precision(tmp_path):
    p=tmp_path/'voltage.csv'
    p.write_text('condition,repetition,neuron_id,compartment_id,time_ms,voltage_mV\n'
                 'Control,1,1,2,.04,.123456789012345\nControl,1,1,2,0,-65\n'
                 'Control,1,1,2,.02,42\n')
    trace,=load_voltage_traces(p)
    assert isinstance(trace.times_ms,array) and isinstance(trace.voltages_mV,array)
    assert tuple(trace.times_ms)==(0.,.02,.04)
    assert trace.voltages_mV[-1]==.123456789012345
    assert voltage_at(trace,.01)==pytest.approx(-11.5)
