"""Cached traces share data records without changing legacy sample behavior."""
from dataclasses import fields

from digifly_app.core.result_voltage import load_voltage_table
from digifly_app.ui.voltage_plot import VoltageTrace, load_voltage_traces, prepare_trace_plot
from test_result_voltage_store import recording


def test_cached_plot_reuses_each_series_instead_of_a_second_tracked_record(tmp_path):
    path = recording(tmp_path)
    table = load_voltage_table(path, cache_root=tmp_path/'cache')
    series = tuple(table.series.values())
    original_pairs = tuple(tuple(item) for item in series)
    reference = load_voltage_traces(path, neuron_labels={'1': 'reference cell'})
    actual = load_voltage_traces(path, table=table, neuron_labels={'1': 'reference cell'})
    assert len(actual) == len(series) == len(reference)
    for shared, stored, expected, pairs in zip(actual, series, reference, original_pairs):
        assert shared is stored
        for field in fields(VoltageTrace):
            value, other = getattr(shared, field.name), getattr(expected, field.name)
            if field.name in ('times_ms', 'voltages_mV'):
                assert tuple(value) == tuple(other)
            elif field.name == 'value_bounds':
                # The existing indexed reader already supplies checked bounds;
                # the ordinary CSV reference computes them while plotting.
                assert value == (min(expected.voltages_mV), max(expected.voltages_mV))
            else:
                assert value == other
        assert tuple(shared) == pairs
        assert shared.voltages_mV is stored.values and stored.values.readonly
    prepared = prepare_trace_plot(actual)
    assert prepared.traces is actual and prepared.sample_count == table.sample_count


def test_new_labels_cannot_mutate_an_already_published_shared_trace(tmp_path):
    path = recording(tmp_path)
    table = load_voltage_table(path, cache_root=tmp_path/'cache')
    first = load_voltage_traces(path, table=table, neuron_labels={'1': 'first label'})
    labels = tuple(trace.label for trace in first)
    second = load_voltage_traces(path, table=table, neuron_labels={'1': 'second label'})
    assert tuple(trace.label for trace in first) == labels
    assert any(a.label != b.label for a, b in zip(first, second))
    assert all(a.voltages_mV is b.voltages_mV for a, b in zip(first, second))
    repeated = load_voltage_traces(path, table=table, neuron_labels={'1': 'first label'})
    assert all(a is b for a, b in zip(first, repeated))


def test_shared_trace_metadata_uses_one_column_owner_not_per_trace_containers(tmp_path):
    path = recording(tmp_path)
    table = load_voltage_table(path, cache_root=tmp_path/'cache')
    traces = load_voltage_traces(path, table=table)
    for trace in traces:
        assert trace is table.series[(trace.condition, trace.repetition,
                                      trace.neuron_id, trace.compartment_id)]
        assert all(isinstance(column[trace._trace_index], (str, int))
                   for column in trace._trace_metadata.columns)
    assert len({id(trace._trace_metadata) for trace in traces}) == 1
    assert len(traces[0]._trace_metadata.columns) == 7
    assert all(len(column) == len(traces) for column in traces[0]._trace_metadata.columns)
