from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from digifly_app.core.circuit import CircuitSpec
from digifly_app.core.experiment import (
    ConditionSpec,
    ExperimentSpec,
    RecordingSpec,
    StimulusSpec,
)
from digifly_app.workers import generic_experiment_worker as worker


def _write_swc(path: Path, *, radius: float = 1.0) -> None:
    path.write_text(
        f"1 1 0 0 0 {radius:.17g} -1\n"
        f"2 1 0 0 5 {radius:.17g} 1\n",
        encoding="utf-8",
    )


def _single_cell_worker_request(
    tmp_path: Path,
    *,
    source_sha256: str | None,
) -> tuple[Path, Path]:
    neuron_id = "cell-1"
    source = tmp_path / "source.swc"
    _write_swc(source)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    edge_path = run_dir / "edge_manifest.json"
    edge_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "kind": "single_cell",
                "neuron_ids": [neuron_id],
                "chemical_edges": [],
                "electrical_edges": [],
            }
        ),
        encoding="utf-8",
    )
    experiment = ExperimentSpec(
        name="Worker source identity",
        engine="arbor",
        duration_ms=1.0,
        integration_dt_ms=0.025,
        stimuli=(
            StimulusSpec(
                waveform="step",
                target_neuron_ids=(neuron_id,),
                amplitude_nA=0.1,
                delay_ms=0.1,
                pulse_width_ms=0.2,
                pulse_count=1,
            ),
        ),
        conditions=(ConditionSpec(name="Control"),),
        recording=RecordingSpec(
            target_neuron_ids=(neuron_id,),
            target_region="soma",
            sample_dt_ms=0.05,
            make_plots=False,
        ),
    )
    request = {
        "schema_version": 1,
        "engine": "arbor",
        "output_dir": str(run_dir),
        "experiment": experiment.to_dict(),
        "circuit": CircuitSpec(neuron_ids=(neuron_id,)).to_dict(),
        "morphologies": {
            neuron_id: {
                "path": str(source),
                "sha256": source_sha256,
                "family": "test",
                "neuron_type": "test-cell",
            }
        },
        "edge_manifest_path": str(edge_path),
        "edge_manifest_sha256": hashlib.sha256(edge_path.read_bytes()).hexdigest(),
        "preflight": {
            "ok": True,
            "checks": [],
        },
    }
    request_path = run_dir / "worker_request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    (run_dir / "run_manifest.json").write_text("{}", encoding="utf-8")
    return request_path, source


@pytest.mark.parametrize(
    "neuron_id",
    ("../../escape", "/tmp/absolute", "..", ".", "cell/a", "cell\\a", "🪰"),
)
def test_neuron_path_component_is_bounded_deterministic_and_relative(neuron_id):
    first = worker._safe_neuron_path_component(neuron_id)
    second = worker._safe_neuron_path_component(neuron_id)

    assert first == second
    assert len(first) <= 102
    assert first not in {"", ".", ".."}
    assert not Path(first).is_absolute()
    assert Path(first).parent == Path(".")
    assert "/" not in first and "\\" not in first


def test_neuron_path_components_do_not_alias_after_slug_sanitization():
    assert worker._safe_neuron_path_component("cell/a") != (
        worker._safe_neuron_path_component("cell?a")
    )


@pytest.mark.parametrize("radius", (0.0, -0.25))
def test_normalization_fails_closed_on_nonpositive_radius(tmp_path, radius):
    source = tmp_path / "invalid-radius.swc"
    _write_swc(source, radius=radius)

    with pytest.raises(ValueError, match="Non-positive SWC radius"):
        worker._normalize_swc(source, tmp_path / "output")


def test_normalization_preserves_small_positive_radius_without_floor(tmp_path):
    source = tmp_path / "small-radius.swc"
    output = tmp_path / "output"
    output.mkdir()
    _write_swc(source, radius=0.0001)

    normalized, _mapping, _digest = worker._normalize_swc(source, output)
    rows = [
        line.split()
        for line in normalized.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]

    assert [float(row[5]) for row in rows] == [0.0001, 0.0001]


def test_single_cell_neuron_discretization_is_uncapped_and_odd():
    class Section:
        def __init__(self, length: float) -> None:
            self.L = length
            self.nseg = 7

    sections = [Section(40.0), Section(8_000.0)]

    total = worker._apply_neuron_discretization(sections)

    assert [section.nseg for section in sections] == [1, 201]
    assert total == 202


def test_single_cell_neuron_discretization_checks_aggregate_before_mutation():
    class Section:
        def __init__(self, length: float) -> None:
            self.L = length
            self.nseg = 7

    sections = [Section(40.0), Section(80.0)]

    with pytest.raises(RuntimeError, match="segment safety limit"):
        worker._apply_neuron_discretization(sections, segment_budget=3)

    assert [section.nseg for section in sections] == [7, 7]


@pytest.mark.parametrize("source_sha256", (None, "", "not-a-digest", "f" * 63))
def test_worker_rejects_missing_or_invalid_selected_source_hash(
    tmp_path, monkeypatch, source_sha256
):
    request_path, _source = _single_cell_worker_request(
        tmp_path,
        source_sha256=source_sha256,
    )
    monkeypatch.setattr(
        worker,
        "_arbor_trace",
        lambda *_args, **_kwargs: pytest.fail("simulation must not start"),
    )

    with pytest.raises(ValueError, match="SHA-256 is missing or invalid"):
        worker.run(request_path)

    assert not (request_path.parent / "summary.json").exists()


def test_worker_rejects_selected_source_changed_after_preflight(tmp_path, monkeypatch):
    request_path, source = _single_cell_worker_request(
        tmp_path,
        source_sha256="0" * 64,
    )
    monkeypatch.setattr(
        worker,
        "_arbor_trace",
        lambda *_args, **_kwargs: pytest.fail("simulation must not start"),
    )

    with pytest.raises(ValueError, match="checksum changed"):
        worker.run(request_path)

    assert source.is_file()
    assert not (request_path.parent / "summary.json").exists()


def test_run_confines_malicious_biological_id_and_preserves_identity(
    tmp_path, monkeypatch
):
    neuron_id = "../../escape"
    source = tmp_path / "source.swc"
    _write_swc(source)
    source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    edge_manifest = {
        "schema_version": 2,
        "kind": "single_cell",
        "neuron_ids": [neuron_id],
        "chemical_edges": [],
        "electrical_edges": [],
    }
    edge_path = run_dir / "edge_manifest.json"
    edge_path.write_text(json.dumps(edge_manifest), encoding="utf-8")
    edge_sha256 = hashlib.sha256(edge_path.read_bytes()).hexdigest()
    (run_dir / "run_manifest.json").write_text("{}", encoding="utf-8")

    experiment = ExperimentSpec(
        name="Path confinement",
        engine="arbor",
        duration_ms=1.0,
        integration_dt_ms=0.025,
        stimuli=(
            StimulusSpec(
                waveform="step",
                target_neuron_ids=(neuron_id,),
                amplitude_nA=0.1,
                delay_ms=0.1,
                pulse_width_ms=0.2,
                pulse_count=1,
            ),
        ),
        conditions=(ConditionSpec(name="Control"),),
        recording=RecordingSpec(
            target_neuron_ids=(neuron_id,),
            target_region="soma",
            sample_dt_ms=0.05,
            make_plots=False,
        ),
    ).to_dict()
    circuit = CircuitSpec(neuron_ids=(neuron_id,)).to_dict()
    request = {
        "schema_version": 1,
        "engine": "arbor",
        "output_dir": str(run_dir),
        "experiment": experiment,
        "circuit": circuit,
        "morphologies": {
            neuron_id: {
                "path": str(source),
                "sha256": source_sha256,
                "family": "test",
                "neuron_type": "malicious-looking-id",
            }
        },
        "edge_manifest_path": str(edge_path),
        "edge_manifest_sha256": edge_sha256,
    }
    request_path = run_dir / "worker_request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    monkeypatch.setattr(
        worker,
        "_arbor_trace",
        lambda *_args, **_kwargs: ([0.0, 0.05], [-65.0, -64.0], "test"),
    )

    summary_path = worker.run(request_path)

    assert summary_path == run_dir / "summary.json"
    assert not (tmp_path / "escape").exists()
    manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    path_component = manifest["morphologies"][neuron_id]["run_path_component"]
    assert path_component == worker._safe_neuron_path_component(neuron_id)
    assert (run_dir / "morphologies" / path_component / "normalized_input.swc").is_file()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    for artifact in summary["artifacts"]:
        (run_dir / artifact["path"]).resolve().relative_to(run_dir.resolve())
    with (run_dir / "voltage_traces.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert {row["neuron_id"] for row in rows} == {neuron_id}
    assert summary["metadata"]["Neuron ID"] == neuron_id
