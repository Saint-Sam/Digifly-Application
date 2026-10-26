from pathlib import Path
import gzip
import json

import pytest

from digifly_app.workers.result_storage import (
    compact_outputs, compress_file, compress_native_voltage,
    open_result_text, read_result_json, result_path,
)


def test_large_new_run_is_lossless_and_updates_artifacts(tmp_path):
    (tmp_path / "run_manifest.json").write_text('{"state":"running"}')
    source = tmp_path / "condition_execution.json"
    original = json.dumps({"settings": [{"value": 1.23456789012345}] * 50000}).encode()
    source.write_bytes(original)
    artifacts = [{"kind": "document", "path": source.name}]
    receipt = compact_outputs(tmp_path, artifacts)
    assert not source.exists()
    assert gzip.decompress(result_path(source).read_bytes()) == original
    assert receipt["stored_bytes"] < receipt["original_bytes"] / 20
    assert artifacts[0]["path"] == "condition_execution.json.gz"
    assert read_result_json(source)["settings"][0]["value"] == 1.23456789012345


@pytest.mark.parametrize("state", ["completed", "failed", "cancelled"])
def test_preexisting_terminal_results_are_never_converted(tmp_path, state):
    (tmp_path / "run_manifest.json").write_text(json.dumps({"state": state}))
    source = tmp_path / "voltage_traces.csv"
    source.write_bytes(b"recorded\n" * 100000)
    before = source.read_bytes()
    with pytest.raises(ValueError, match="unpublished running"):
        compact_outputs(tmp_path, [])
    assert source.read_bytes() == before
    assert not source.with_suffix(".csv.gz").exists()


def test_dense_arbor_observations_keep_their_original_native_identity(tmp_path):
    import hashlib
    (tmp_path / 'run_manifest.json').write_text('{"state":"running"}')
    folder = tmp_path / 'arbor_native_reports'
    folder.mkdir()
    report = folder / 'condition-1-repetition-1.csv'
    native = b'neuron_id,compartment_id,native_row_index,time_ms,voltage_mV\n' + b'1,2,0,0,-65\n' * 60000
    report.write_bytes(native)
    receipt = folder / 'condition-1-repetition-1.json'
    receipt.write_text(json.dumps({'raw_report_path': report.name,
        'raw_report_sha256': hashlib.sha256(native).hexdigest()}))
    artifacts = [{'kind': 'table', 'path': report.relative_to(tmp_path).as_posix()}]
    compact_outputs(tmp_path, artifacts)
    assert artifacts[0]['path'].endswith('.csv.gz')
    record = read_result_json(receipt)
    with gzip.open(result_path(folder / record['raw_report_path']), 'rb') as stream:
        assert hashlib.sha256(stream.read()).hexdigest() == record['raw_report_sha256']
    assert gzip.decompress(result_path(report).read_bytes()) == native


def test_cancellation_retains_original_and_removes_only_owned_temporary(tmp_path):
    source = tmp_path / "voltage_traces.csv"
    source.write_bytes(b"recorded\n" * 100000)
    def cancelled():
        raise RuntimeError("cancelled")
    with pytest.raises(RuntimeError, match="cancelled"):
        compress_file(source, check_cancel=cancelled)
    assert source.is_file() and list(tmp_path.iterdir()) == [source]


def test_existing_compressed_file_is_not_replaced_or_silently_selected(tmp_path):
    source = tmp_path / "values.csv"
    source.write_text("new")
    other = tmp_path / "values.csv.gz"
    other.write_bytes(gzip.compress(b"previous"))
    with pytest.raises(ValueError, match="Refusing"):
        compress_file(source, minimum_bytes=0)
    with pytest.raises(ValueError, match="Ambiguous"):
        result_path(source)
    assert source.read_text() == "new" and gzip.decompress(other.read_bytes()) == b"previous"


def test_bad_compressed_recording_fails_instead_of_looking_empty(tmp_path):
    path = tmp_path / "values.csv.gz"
    path.write_bytes(gzip.compress(b"saved values")[:-5])
    with pytest.raises((EOFError, OSError)):
        with open_result_text(path) as stream:
            stream.read()


def test_standard_plot_overview_is_bounded_without_altering_trace_keys():
    from digifly_app.workers.generic_experiment_worker import _plot_trace_selection
    keys = [("Control", 1, str(cell), str(edge)) for cell in range(6) for edge in range(2000)]
    before = list(keys)
    selected = _plot_trace_selection(keys)
    assert len(selected) == len(set(selected)) == 24
    assert selected[0] == min(keys) and selected[-1] == max(keys)
    assert keys == before


def test_compression_preserves_disk_reserve_and_unsupported_filesystems(tmp_path, monkeypatch):
    from digifly_app.workers import result_storage as storage
    source = tmp_path / "values.csv"
    source.write_bytes(b"saved\n" * 100000)
    original = source.read_bytes()
    usage = storage.shutil.disk_usage(tmp_path)
    monkeypatch.setattr(storage.shutil, "disk_usage", lambda _: usage._replace(free=1024))
    assert compress_file(source) is None and source.read_bytes() == original
    monkeypatch.setattr(storage.shutil, "disk_usage", lambda _: usage._replace(free=10 * 1024**3))
    def unsupported(*args):
        raise OSError("filesystem has no hard links")
    monkeypatch.setattr(storage.os, "link", unsupported)
    assert compress_file(source) is None and source.read_bytes() == original
    assert list(tmp_path.iterdir()) == [source]


def test_native_report_retains_all_values_mapping_and_attributes(tmp_path):
    h5py = pytest.importorskip("h5py")
    import numpy as np
    path = tmp_path / "voltage.h5"
    values = np.repeat(np.arange(800, dtype="float64")[:, None], 128, axis=1)
    values[10, 20] = -1.2345678901234567
    with h5py.File(path, "w") as file:
        file.attrs["schema"] = "native"
        group = file.create_group("report/digifly")
        group.attrs["units"] = "mV"
        dataset = group.create_dataset("data", data=values)
        dataset.attrs["variable"] = "v"
        group.create_dataset("mapping/node_ids", data=[1, 2, 3])
    receipt = compress_native_voltage(path, h5py)
    assert receipt["all_native_values_preserved"]
    assert receipt["stored_bytes"] < receipt["original_bytes"] / 5
    with h5py.File(path) as file:
        assert file["report/digifly/data"][:].tobytes() == values.tobytes()
        assert file["report/digifly/data"].compression == "gzip"
        assert file.attrs["schema"] == "native"
        assert file["report/digifly"].attrs["units"] == "mV"
        assert file["report/digifly/data"].attrs["variable"] == "v"
        assert list(file["report/digifly/mapping/node_ids"][:]) == [1, 2, 3]
