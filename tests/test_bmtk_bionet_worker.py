from __future__ import annotations

import csv
import json
import math
import os
from pathlib import Path
import signal
import subprocess

import pytest

from digifly_app.core.circuit import CircuitSpec, ConnectomeRef
from digifly_app.core.experiment import (
    ConditionSpec,
    ExperimentSpec,
    RecordingSpec,
    StimulusSpec,
)
from digifly_app.workers import bmtk_bionet_worker as worker


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _swc(path: Path, offset: float = 0.0) -> Path:
    path.write_text(
        f"10 1 {offset} 0 0 5 -1\n"
        f"30 3 {offset} 0 30 1 20\n"
        f"20 1 {offset} 0 5 4 10\n"
        f"40 3 {offset} 0 60 0.8 30\n",
        encoding="utf-8",
    )
    return path


def _request_fixture(tmp_path: Path) -> tuple[Path, dict, dict, dict]:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    first = _swc(tmp_path / "cell-a.swc")
    second = _swc(tmp_path / "cell-b.swc", 10.0)
    circuit = CircuitSpec(
        connectome=ConnectomeRef("test:v1", "Test", str(tmp_path)),
        neuron_ids=("cell/a", "cell-b"),
    ).to_dict()
    experiment = ExperimentSpec(
        name="BMTK chemical smoke",
        engine="bmtk",
        duration_ms=5.0,
        integration_dt_ms=0.025,
        temperature_C=22.0,
        repetitions=1,
        workers=1,
        stimuli=(
            StimulusSpec(
                waveform="step",
                target_neuron_ids=("cell/a",),
                amplitude_nA=1.0,
                delay_ms=1.0,
                pulse_width_ms=2.0,
                pulse_count=1,
            ),
        ),
        conditions=(
            ConditionSpec(name="Chemical enabled", chemical_synapses_enabled=True),
            ConditionSpec(name="Chemical disabled", chemical_synapses_enabled=False),
        ),
        recording=RecordingSpec(
            target_neuron_ids=("cell/a", "cell-b"),
            target_region="soma",
            sample_dt_ms=0.05,
            make_plots=False,
        ),
    ).to_dict()
    chemical_edges = [
        {
            "contact_id": "chem/1",
            "source_edge_rowid": "1",
            "source_edge_id": "fixture",
            "pre_id": "cell/a",
            "post_id": "cell-b",
            "post_source_node_id": 30,
            "post_coordinate_um": [10.0, 0.0, 30.0],
            "weight_uS": 0.01,
            "delay_ms": 1.0,
            "tau1_ms": 0.5,
            "tau2_ms": 3.0,
            "reversal_mV": 0.0,
        }
    ]
    edge_manifest = {
        "schema_version": 2,
        "kind": "explicit_selected_subgraph",
        "neuron_ids": ["cell/a", "cell-b"],
        "sources": [],
        "chemical_synapse_policy": circuit["chemical_synapse_policy"],
        "gap_junction_policy": circuit["gap_junction_policy"],
        "chemical_edges": chemical_edges,
        "electrical_edges": [],
        "selected_contact_identity_sha256": worker._json_sha256(
            {"chemical": chemical_edges, "electrical": []}
        ),
    }
    edge_path = run_dir / "edge_manifest.json"
    _write_json(edge_path, edge_manifest)
    request = {
        "schema_version": 1,
        "created_at": "2026-09-09T00:00:00+00:00",
        "engine": "bmtk",
        "output_dir": str(run_dir.resolve()),
        "circuit": circuit,
        "experiment": experiment,
        "edge_manifest_path": str(edge_path.resolve()),
        "edge_manifest_sha256": worker._common._sha256(edge_path),
        "morphologies": {
            "cell/a": {
                "path": str(first.resolve()),
                "sha256": worker._common._sha256(first),
                "neuron_type": "A",
                "family": "test",
            },
            "cell-b": {
                "path": str(second.resolve()),
                "sha256": worker._common._sha256(second),
                "neuron_type": "B",
                "family": "test",
            },
        },
        "preflight": {"ok": True, "checks": []},
    }
    request_path = run_dir / "worker_request.json"
    _write_json(request_path, request)
    _write_json(run_dir / "circuit.json", circuit)
    _write_json(run_dir / "experiment.json", experiment)
    _write_json(
        run_dir / "run_manifest.json",
        {
            "schema_version": 1,
            "state": "queued",
            "engine": "bmtk",
            "summary_path": str(run_dir / "summary.json"),
        },
    )
    return request_path, request, edge_manifest, circuit


def test_validated_request_accepts_strict_generic_contract(tmp_path):
    request_path, request, edge_manifest, _circuit = _request_fixture(tmp_path)
    loaded, output_dir, loaded_edges = worker._validated_request(request_path)
    assert loaded == json.loads(json.dumps(request))
    assert output_dir == request_path.parent
    assert loaded_edges == edge_manifest


def test_worker_rejects_morphology_hash_change(tmp_path):
    request_path, request, _edge_manifest, _circuit = _request_fixture(tmp_path)
    Path(request["morphologies"]["cell/a"]["path"]).write_text(
        "1 1 0 0 0 1 -1\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="identity check failed"):
        worker._validated_request(request_path)


def test_morphology_preparation_rechecks_the_exact_parsed_byte_stream(tmp_path):
    request_path, request, _edge_manifest, _circuit = _request_fixture(tmp_path)
    worker._validated_request(request_path)
    Path(request["morphologies"]["cell/a"]["path"]).write_text(
        "1 1 0 0 0 1 -1\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="Source SWC for cell/a checksum changed"):
        worker._prepare_morphologies(request, request_path.parent)


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("gap", "gap-junction policy"),
        ("native", "Native membrane mechanisms"),
        ("compartment", "Per-compartment"),
        ("workers", "exactly one worker"),
        ("sample_dt", "integer multiple"),
        ("duration_grid", "integer multiple"),
    ),
)
def test_capability_boundary_rejects_unsupported_semantics(
    tmp_path, mutation, message
):
    _path, request, edge_manifest, circuit = _request_fixture(tmp_path)
    experiment = request["experiment"]
    if mutation == "gap":
        circuit["gap_junction_policy"]["mode"] = "ohmic"
        edge_manifest["gap_junction_policy"] = circuit["gap_junction_policy"]
    elif mutation == "native":
        channel = next(iter(circuit["membrane"]["channels"].values()))
        channel["enabled"] = True
        channel["soma_gbar_s_cm2"] = 0.1
    elif mutation == "compartment":
        circuit["compartment_overrides"] = {"cell/a": {"20": {"cm_uF_cm2": 2.0}}}
    elif mutation == "workers":
        experiment["workers"] = 2
    elif mutation == "sample_dt":
        experiment["recording"]["sample_dt_ms"] = 0.06
    else:
        experiment["duration_ms"] = 5.01
    with pytest.raises(ValueError, match=message):
        worker._validate_capabilities(circuit, experiment, edge_manifest)


def test_section_plan_is_deterministic_and_maps_every_swc_node():
    rows = (
        (1, 1, 0.0, 0.0, 0.0, 5.0, -1),
        (2, 1, 0.0, 0.0, 5.0, 4.0, 1),
        (3, 3, 0.0, 0.0, 30.0, 1.0, 2),
        (4, 3, 0.0, 0.0, 60.0, 0.8, 3),
        (5, 3, 20.0, 0.0, 30.0, 0.8, 2),
    )
    first = worker._section_plan(rows, prefer_rostral_soma=False)
    second = worker._section_plan(rows, prefer_rostral_soma=False)
    assert first == second
    assert set(first["node_sites"]) == {1, 2, 3, 4, 5}
    target_site = first["node_sites"][first["target_node_id"]]
    assert target_site["section_id"] == 0
    assert target_site["section_pos"] == pytest.approx(0.5)
    assert first["sections"][0]["section_name"] == "soma[0]"
    assert first["sections"][0]["uses_centered_target_stub"] is True
    assert first["estimated_nseg"] >= len(first["sections"])


def test_morphology_preparation_and_contact_resolution_are_exact(tmp_path):
    request_path, request, edge_manifest, _circuit = _request_fixture(tmp_path)
    worker._validated_request(request_path)
    cells, artifacts, metadata = worker._prepare_morphologies(
        request, request_path.parent
    )
    resolved = worker._resolve_edge_manifest(edge_manifest, cells)
    contact = resolved["chemical_edges"][0]
    assert contact["post_run_node_id"] == 3
    site = cells["cell-b"]["section_plan"]["node_sites"][3]
    assert contact["bionet_section_id"] == site["section_id"]
    assert contact["bionet_section_pos"] == pytest.approx(site["section_pos"])
    assert all("cell/a" not in item["path"] for item in artifacts)
    assert metadata["cell/a"]["source_swc_sha256"]
    morphology_labels = {
        item["label"] for item in artifacts if item["kind"] == "morphology"
    }
    assert morphology_labels == {
        "Normalized morphology · cell/a",
        "Normalized morphology · cell-b",
    }
    with (request_path.parent / "sonata/node_id_crosswalk.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        crosswalk = list(csv.DictReader(handle))
    assert [item["neuron_id"] for item in crosswalk] == ["cell/a", "cell-b"]
    assert metadata["cell/a"]["diameter_policy"].endswith("no floor.")
    assert metadata["cell/a"]["target_soma_proxy_length_um"] == pytest.approx(
        worker.TARGET_SOMA_PROXY_LENGTH_UM
    )


def test_tiny_positive_swc_radius_is_not_floored():
    radius_um = 1.0e-9
    assert worker._swc_diameter(radius_um) == pytest.approx(2.0e-9)
    with pytest.raises(ValueError, match="must be positive"):
        worker._swc_diameter(0.0)


def test_identical_exp2syn_dynamics_share_one_model_file(tmp_path):
    model_root = tmp_path / "sonata/components/synaptic_models"
    model_root.mkdir(parents=True)
    contacts = [
        {
            "contact_id": "contact-1",
            "reversal_mV": 0.0,
            "tau1_ms": 0.5,
            "tau2_ms": 3.0,
        },
        {
            "contact_id": "contact-2",
            "reversal_mV": 0.0,
            "tau1_ms": 0.5,
            "tau2_ms": 3.0,
        },
    ]

    models = worker._write_synapse_models(
        tmp_path, {"chemical_edges": contacts}
    )

    assert models["contact-1"] == models["contact-2"]
    assert len(list(model_root.glob("*.json"))) == 1


def test_simulation_config_translates_pulses_targets_and_recording(tmp_path):
    request_path, request, edge_manifest, _circuit = _request_fixture(tmp_path)
    cells, _artifacts, _metadata = worker._prepare_morphologies(
        request, request_path.parent
    )
    resolved = worker._resolve_edge_manifest(edge_manifest, cells)
    worker._write_synapse_models(request_path.parent, resolved)
    circuit_config = {
        "components": {
            "morphologies_dir": str(request_path.parent / "morphologies"),
            "biophysical_neuron_models_dir": str(
                request_path.parent / "sonata/components/biophysical_models"
            ),
            "synaptic_models_dir": str(
                request_path.parent / "sonata/components/synaptic_models"
            ),
        },
        "networks": {"nodes": [], "edges": []},
    }
    experiment = request["experiment"]
    stimulus = dict(experiment["stimuli"][0])
    stimulus.update(
        {
            "waveform": "pulse_train",
            "pulse_count": 3,
            "frequency_hz": 100.0,
            "pulse_width_ms": 0.25,
        }
    )
    experiment["duration_ms"] = 30.0
    repetition_dir = request_path.parent / "rep"
    config = worker._simulation_config(
        output_dir=request_path.parent,
        repetition_dir=repetition_dir,
        circuit_config=circuit_config,
        cells=cells,
        experiment=experiment,
        stimulus=stimulus,
        condition={"name": "Scaled", "stimulus_scale": 0.5},
        repetition=2,
        chemical_spike_threshold_mV=0.0,
    )
    primary = config["inputs"]["primary_stimulus"]
    assert primary["amp"] == [0.5, 0.5, 0.5]
    assert primary["delay"] == [1.0, 11.0, 21.0]
    assert config["node_sets"]["stimulus_targets"]["node_id"] == [0]
    assert config["node_sets"]["recording_targets"]["node_id"] == [0, 1]
    assert config["reports"]["soma_voltage"]["dt"] == pytest.approx(0.05)


def test_native_soma_arrays_convert_to_canonical_long_rows():
    rows = worker._report_arrays_to_rows(
        [[-65.0, -64.0], [-60.0, -63.0], [-20.0, -62.0]],
        [0, 1],
        [0, 1, 2],
        [0.0, 0.15, 0.05],
        {0: "A", 1: "B"},
        integration_dt_ms=0.025,
        expected_duration_ms=0.15,
        expected_sample_dt_ms=0.05,
        condition="Control",
        repetition=1,
    )
    assert len(rows) == 6
    assert rows[0] == {
        "condition": "Control",
        "repetition": 1,
        "neuron_id": "A",
        "time_ms": 0.025,
        "voltage_mV": -65.0,
    }
    assert rows[-1]["time_ms"] == pytest.approx(0.125)


@pytest.mark.parametrize(
    ("time_spec", "sample_count", "message"),
    (
        ([0.025, 0.15, 0.05], 3, "start time"),
        ([0.0, 0.10, 0.05], 3, "end time"),
        ([0.0, 0.15, 0.025], 3, "dt does not match"),
        ([0.0, 0.15, 0.05], 2, "sample count"),
    ),
)
def test_native_soma_arrays_reject_wrong_or_truncated_time_grid(
    time_spec, sample_count, message
):
    data = [[-65.0] for _index in range(sample_count)]
    with pytest.raises((ValueError, RuntimeError), match=message):
        worker._report_arrays_to_rows(
            data,
            [0],
            [0, 1],
            time_spec,
            {0: "A"},
            integration_dt_ms=0.025,
            expected_duration_ms=0.15,
            expected_sample_dt_ms=0.05,
            condition="Control",
            repetition=1,
        )


def test_child_environment_removes_host_runtime_contamination(monkeypatch):
    contaminated = {
        "PYTHONHOME": "/bad/python",
        "PYTHONPATH": "/bad/modules",
        "VIRTUAL_ENV": "/bad/venv",
        "CONDA_PREFIX": "/bad/conda",
        "DYLD_LIBRARY_PATH": "/bad/dylib",
        "QT_PLUGIN_PATH": "/bad/qt",
        "QML_IMPORT_PATH": "/bad/qml",
        "NEURONHOME": "/bad/neuron",
        "NRN_NMODL_PATH": "/bad/nrn",
        "CORENRN_HOME": "/bad/coreneuron",
        "NMODLHOME": "/bad/nmodl",
        "NMODL_PYLIB": "/bad/nmodl-python",
        "DISPLAY": ":9",
        "PATH": "/usr/bin:/bin",
    }
    for key, value in contaminated.items():
        monkeypatch.setenv(key, value)
    environment = worker._child_environment()
    assert not (set(contaminated) - {"PATH"}) & set(environment)
    assert environment["PATH"].split(os.pathsep)[0] == str(
        Path(os.sys.executable).absolute().parent
    )
    assert environment["NEURON_MODULE_OPTIONS"] == "-nogui"
    assert environment["PYTHONNOUSERSITE"] == "1"


def test_child_cancellation_forwards_signal_preserves_log_and_marks_manifest(
    tmp_path, monkeypatch
):
    request_path, _request, _edges, _circuit = _request_fixture(tmp_path)
    repetition_dir = request_path.parent / "sonata/conditions/test/repetition-001"
    repetition_dir.mkdir(parents=True)
    config_path = repetition_dir / "simulation_config.json"
    _write_json(config_path, {"fixture": True})
    cancel_marker = request_path.parent / worker.CANCEL_REQUEST_FILENAME

    handlers: dict[int, object] = {}
    forwarded: list[tuple[int, bool]] = []
    popen_options: dict[str, object] = {}

    def fake_signal(signum, handler):
        key = int(signum)
        previous = handlers.get(key, signal.SIG_DFL)
        handlers[key] = handler
        return previous

    class FakePopen:
        def __init__(self, _command, *, stdout, **kwargs):
            self.pid = 4242
            self.returncode = None
            popen_options.update(kwargs)
            stdout.write("partial BioNet output before cancellation\n")
            stdout.flush()

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            assert timeout == pytest.approx(0.2)
            if self.returncode is None:
                handler = handlers[int(signal.SIGTERM)]
                assert callable(handler)
                handler(signal.SIGTERM, None)
            return self.returncode

        def kill(self):
            self.returncode = -int(signal.SIGKILL)

    def fake_forward(process, signum, *, force=False):
        forwarded.append((int(signum), bool(force)))
        process.returncode = -int(signum)

    monkeypatch.setattr(worker.signal, "signal", fake_signal)
    monkeypatch.setattr(worker.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(worker, "_signal_child_process", fake_forward)

    with pytest.raises(worker._WorkerCancelled) as raised:
        worker._run_child(
            config_path,
            repetition_dir,
            cancel_marker=cancel_marker,
        )
    assert raised.value.signum == int(signal.SIGTERM)
    assert forwarded == [(int(signal.SIGTERM), False)]
    if os.name != "nt":
        assert popen_options["start_new_session"] is True
    log_path = repetition_dir / "worker_subprocess.log"
    assert log_path.read_text(encoding="utf-8") == (
        "partial BioNet output before cancellation\n"
    )

    _write_json(
        request_path.parent / "summary.json",
        {"schema_version": 1, "status": "complete"},
    )
    worker._mark_failed(request_path, raised.value)
    manifest = json.loads((request_path.parent / "run_manifest.json").read_text())
    assert manifest["state"] == "cancelled"
    assert manifest["error_type"] == "_WorkerCancelled"
    assert manifest["preserved_child_logs"] == [
        str(log_path.relative_to(request_path.parent))
    ]
    assert not (request_path.parent / "summary.json").exists()
    assert (request_path.parent / "summary.cancelled.json").is_file()
    assert manifest["partial_summary_path"].endswith("summary.cancelled.json")


def test_process_lifetime_signal_before_child_unwinds_through_main(
    tmp_path, monkeypatch
):
    request_path, _request, _edges, _circuit = _request_fixture(tmp_path)
    handlers: dict[int, object] = {}

    def fake_signal(signum, handler):
        key = int(signum)
        previous = handlers.get(key, signal.SIG_DFL)
        handlers[key] = handler
        return previous

    def fake_internal_run(_request_path, *, cancellation):
        assert cancellation.active_child is None
        handler = handlers[int(signal.SIGTERM)]
        assert callable(handler)
        handler(signal.SIGTERM, None)
        cancellation.raise_if_requested()
        raise AssertionError("the cancellation checkpoint must unwind the worker")

    monkeypatch.setattr(worker.signal, "signal", fake_signal)
    monkeypatch.setattr(worker, "_run", fake_internal_run)

    assert worker.main(["--request", str(request_path)]) == 1
    manifest = json.loads((request_path.parent / "run_manifest.json").read_text())
    assert manifest["state"] == "cancelled"
    assert manifest["error_type"] == "_WorkerCancelled"
    assert not (request_path.parent / "summary.json").exists()


def test_process_lifetime_signal_after_child_quarantines_complete_summary(
    tmp_path, monkeypatch
):
    request_path, _request, _edges, _circuit = _request_fixture(tmp_path)
    handlers: dict[int, object] = {}

    def fake_signal(signum, handler):
        key = int(signum)
        previous = handlers.get(key, signal.SIG_DFL)
        handlers[key] = handler
        return previous

    class FinishedChild:
        def poll(self):
            return 0

    def fake_internal_run(_request_path, *, cancellation):
        child = FinishedChild()
        cancellation.attach_child(child)
        cancellation.detach_child(child)
        _write_json(
            request_path.parent / "summary.json",
            {"schema_version": 1, "status": "complete"},
        )
        handler = handlers[int(signal.SIGTERM)]
        assert callable(handler)
        handler(signal.SIGTERM, None)
        return request_path.parent / "summary.json"

    monkeypatch.setattr(worker.signal, "signal", fake_signal)
    monkeypatch.setattr(worker, "_run", fake_internal_run)

    assert worker.main(["--request", str(request_path)]) == 1
    manifest = json.loads((request_path.parent / "run_manifest.json").read_text())
    assert manifest["state"] == "cancelled"
    assert manifest["error_type"] == "_WorkerCancelled"
    assert not (request_path.parent / "summary.json").exists()
    assert (request_path.parent / "summary.cancelled.json").is_file()
    assert manifest["partial_summary_path"].endswith("summary.cancelled.json")


def test_real_two_cell_chemical_bionet_smoke_when_runtime_requested(tmp_path):
    executable = os.environ.get("DIGIFLY_TEST_BMTK_PYTHON", "").strip()
    if not executable:
        pytest.skip("Set DIGIFLY_TEST_BMTK_PYTHON to a same-interpreter BMTK+NEURON runtime")
    executable_path = Path(executable).expanduser().absolute()
    if not executable_path.is_file():
        pytest.skip(f"Requested BMTK test interpreter does not exist: {executable_path}")
    request_path, _request, _edges, _circuit = _request_fixture(tmp_path)
    environment = worker._child_environment()
    environment["PATH"] = os.pathsep.join(
        (str(executable_path.parent), environment.get("PATH", ""))
    )
    completed = subprocess.run(
        [
            str(executable_path),
            "-B",
            str(Path(worker.__file__).resolve()),
            "--request",
            str(request_path),
        ],
        cwd=str(request_path.parent),
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stdout
    summary = json.loads((request_path.parent / "summary.json").read_text())
    assert summary["engine"] == "bmtk"
    assert summary["status"] == "complete"
    voltage_path = request_path.parent / "voltage_traces.csv"
    assert voltage_path.is_file()
    assert (request_path.parent / "spikes.csv").is_file()
    rows = list(csv.DictReader(voltage_path.open(encoding="utf-8", newline="")))
    assert len(rows) == 400
    assert {row["neuron_id"] for row in rows} == {"cell/a", "cell-b"}
    assert {row["condition"] for row in rows} == {
        "Chemical enabled",
        "Chemical disabled",
    }
    assert all(
        math.isfinite(float(row["time_ms"]))
        and math.isfinite(float(row["voltage_mV"]))
        for row in rows
    )
    for condition in ("Chemical enabled", "Chemical disabled"):
        for neuron_id in ("cell/a", "cell-b"):
            times = [
                float(row["time_ms"])
                for row in rows
                if row["condition"] == condition and row["neuron_id"] == neuron_id
            ]
            assert len(times) == 100
            assert times[0] == pytest.approx(0.025)
            assert times[-1] == pytest.approx(4.975)
            assert all(
                later - earlier == pytest.approx(0.05)
                for earlier, later in zip(times, times[1:])
            )
    enabled_post = [
        float(row["voltage_mV"])
        for row in rows
        if row["condition"] == "Chemical enabled" and row["neuron_id"] == "cell-b"
    ]
    disabled_post = [
        float(row["voltage_mV"])
        for row in rows
        if row["condition"] == "Chemical disabled" and row["neuron_id"] == "cell-b"
    ]
    assert len(enabled_post) == len(disabled_post) == 100
    assert max(abs(on - off) for on, off in zip(enabled_post, disabled_post)) > 1.0

    artifacts = summary["artifacts"]
    assert any(
        item["kind"] == "sonata" and item["path"].endswith("digifly_nodes.h5")
        for item in artifacts
    )
    assert any(
        item["kind"] == "sonata" and item["path"].endswith("digifly_edges.h5")
        for item in artifacts
    )
    assert sum(item["label"].startswith("BioNet simulation config") for item in artifacts) == 2
    provenance_path = request_path.parent / "bmtk_bionet_provenance.json"
    assert provenance_path.is_file()
    provenance = json.loads(provenance_path.read_text())
    timing = provenance["canonical_voltage_timing"]
    assert timing["first_sample_offset_ms"] == pytest.approx(0.025)
    assert timing["native_mapping_time_semantics"] == "configured report window"
    assert timing["native_sonata_mapping_preserved"] is True
    assert timing["sampling_hook"] == "BioSimulator post_fadvance"
    assert timing["validated_native_mapping"] == {
        "expected_samples_per_target": 100,
        "sample_dt_ms": 0.05,
        "start_ms": 0.0,
        "stop_ms": 5.0,
    }
    assert provenance["morphology_policy"]["diameter_floor_um"] is None
    assert provenance["morphology_policy"]["source_diameter_rule"] == (
        "exact 2 × positive SWC radius"
    )
    resolved = json.loads(
        (request_path.parent / "resolved_edge_manifest.json").read_text()
    )
    contact = resolved["chemical_edges"][0]
    assert contact["post_run_node_id"] == 3
    assert isinstance(contact["bionet_section_id"], int)
    assert 0.0 <= contact["bionet_section_pos"] <= 1.0
