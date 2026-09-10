from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess

import pytest

from digifly_app.core.circuit import CircuitSpec, ConnectomeRef
from digifly_app.core.connectomes import NeuronRecord
from digifly_app.core.experiment import (
    ConditionSpec,
    ExperimentSpec,
    RecordingSpec,
    StimulusSpec,
)
from digifly_app.core.models import CheckState, PreflightCheck, PreflightReport
from digifly_app.core.mechanisms import GapJunctionPolicy
from digifly_app.core.morphology import Morphology, load_swc
from digifly_app.core.process_environment import sanitized_external_environment
from digifly_app.engines import generic_experiment as generic
from digifly_app.workers.generic_experiment_worker import _normalize_swc


def _morphology(tmp_path: Path, neuron_id: str = "1") -> Morphology:
    path = tmp_path / f"{neuron_id}.swc"
    path.write_text(
        "1 1 0 0 0 5 -1\n"
        "3 3 0 0 10 2 2\n"
        "2 1 0 0 5 5 1\n"
        "4 3 0 0 50 1 3\n",
        encoding="utf-8",
    )
    return load_swc(
        NeuronRecord(
            neuron_id,
            "test",
            "synthetic",
            str(path),
            "test:v1",
        )
    )


def _morphology_hashes(
    morphologies: tuple[Morphology, ...] | list[Morphology],
) -> dict[str, str]:
    return {
        morphology.record.neuron_id: hashlib.sha256(
            Path(morphology.record.swc_path).read_bytes()
        ).hexdigest()
        for morphology in morphologies
    }


def _uniform_radius_morphology(
    tmp_path: Path,
    radius: float,
    *,
    neuron_id: str = "1",
) -> Morphology:
    path = tmp_path / f"radius-{neuron_id}.swc"
    path.write_text(
        f"1 1 0 0 0 {radius:.17g} -1\n"
        f"2 3 0 0 5 {radius:.17g} 1\n",
        encoding="utf-8",
    )
    return load_swc(
        NeuronRecord(
            neuron_id,
            "test",
            "synthetic",
            str(path),
            "test:v1",
        )
    )


def _experiment(engine: str = "arbor") -> ExperimentSpec:
    return ExperimentSpec(
        name=f"{engine.title()} executable smoke",
        engine=engine,
        duration_ms=5.0,
        integration_dt_ms=0.025,
        temperature_C=22.0,
        stimuli=(
            StimulusSpec(
                waveform="step",
                amplitude_nA=0.1,
                delay_ms=1.0,
                pulse_width_ms=2.0,
                pulse_count=1,
            ),
        ),
        conditions=(ConditionSpec(name="Control"),),
        recording=RecordingSpec(
            target_region="soma",
            sample_dt_ms=0.05,
            make_plots=False,
        ),
    )


def _passing_runtime(*_args, **_kwargs) -> PreflightCheck:
    return PreflightCheck(
        "runtime",
        "Runtime",
        CheckState.PASS,
        "Test runtime accepted.",
        blocking=True,
    )


def test_generic_plan_preserves_selected_virtualenv_launcher(tmp_path):
    base_python = tmp_path / "base" / "python3.12"
    base_python.parent.mkdir()
    base_python.symlink_to(Path(os.sys.executable))
    launcher = tmp_path / "bmtk-env" / "bin" / "python"
    launcher.parent.mkdir(parents=True)
    launcher.symlink_to(base_python)
    circuit = CircuitSpec(
        connectome=ConnectomeRef("test:v1", "Test", str(tmp_path)),
        neuron_ids=(),
    )
    adapter = generic.GenericExperimentAdapter({"bmtk": launcher})

    plan = adapter.plan(
        circuit,
        _experiment("bmtk"),
        output_root=tmp_path / "runs",
    )

    assert adapter.runtime_paths["bmtk"] == launcher.absolute()
    assert Path(plan.program) == launcher.absolute()
    assert Path(plan.program) != launcher.resolve()
    assert plan.environment["PATH"].split(os.pathsep, 1)[0] == str(launcher.parent)


def test_bmtk_runtime_probe_requires_one_isolated_bionet_environment(
    tmp_path, monkeypatch
):
    executable = tmp_path / "python"
    executable.write_text("", encoding="utf-8")
    captured = {}

    class Completed:
        returncode = 0
        stdout = json.dumps(
            {
                "python": str(executable),
                "version": "1.2.0",
                "neuron": "9.0.1",
                "h5py": "3.16.0",
                "numpy": "2.3.2",
                "matplotlib": "3.10.0",
                "origins": {},
            }
        )
        stderr = ""

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return Completed()

    monkeypatch.setenv("PYTHONPATH", "/untrusted/inherited/modules")
    monkeypatch.setenv("VIRTUAL_ENV", "/untrusted/inherited/venv")
    monkeypatch.setenv("CONDA_PREFIX", "/untrusted/inherited/conda")
    monkeypatch.setenv("DISPLAY", ":99")
    monkeypatch.setattr(generic.subprocess, "run", fake_run)

    check = generic._runtime_check("bmtk", executable, make_plots=True)

    assert check.state == CheckState.PASS
    assert "BMTK 1.2.0" in check.detail
    assert "NEURON 9.0.1" in check.detail
    assert "NumPy 2.3.2" in check.detail
    child_code = captured["command"][3]
    for module in ("bmtk", "bmtk.simulator.bionet", "neuron", "h5py", "numpy"):
        assert f"import_module('{module}')" in child_code
    assert "sys.base_prefix" in child_code
    assert "is_relative_to" in child_code
    environment = captured["kwargs"]["env"]
    assert environment["PYTHONNOUSERSITE"] == "1"
    assert environment["NEURON_MODULE_OPTIONS"] == "-nogui"
    assert "PYTHONPATH" not in environment
    assert "VIRTUAL_ENV" not in environment
    assert "CONDA_PREFIX" not in environment
    assert "DISPLAY" not in environment


def test_bmtk_runtime_probe_blocks_when_bionet_dependency_is_missing(
    tmp_path, monkeypatch
):
    executable = tmp_path / "python"
    executable.write_text("", encoding="utf-8")

    class Completed:
        returncode = 1
        stdout = ""
        stderr = "ModuleNotFoundError: No module named 'neuron'"

    monkeypatch.setattr(generic.subprocess, "run", lambda *_args, **_kwargs: Completed())

    check = generic._runtime_check("bmtk", executable, make_plots=False)

    assert check.state == CheckState.FAIL
    assert check.blocking
    assert "No module named 'neuron'" in check.detail


@pytest.mark.parametrize("engine", ("arbor", "neuron", "bmtk"))
def test_generic_adapter_requires_frozen_morphology_hash_for_every_engine(
    tmp_path, monkeypatch, engine
):
    morphology = _morphology(tmp_path)
    circuit = CircuitSpec(
        connectome=ConnectomeRef("test:v1", "Test", str(tmp_path)),
        neuron_ids=("1",),
        morphology_sha256={"1": "not-a-sha256"},
    )
    experiment = _experiment(engine)
    monkeypatch.setattr(generic, "_runtime_check", _passing_runtime)
    adapter = generic.GenericExperimentAdapter({engine: os.sys.executable})

    report = adapter.validate(
        circuit,
        experiment,
        (morphology,),
        output_root=tmp_path / "runs",
    )

    failure = next(
        check for check in report.checks if check.key == "morphology_identity"
    )
    assert failure.state == CheckState.FAIL
    assert failure.blocking
    assert "64 hexadecimal characters" in failure.detail
    assert "Reload each affected SWC in Circuit Builder" in failure.detail
    with pytest.raises(ValueError, match="64-character source_sha256"):
        adapter.request_payload(
            circuit,
            experiment,
            (morphology,),
            report,
            output_root=tmp_path / "runs",
        )


@pytest.mark.parametrize(
    ("radius", "expected_state"),
    ((0.0, CheckState.FAIL), (1.0e-12, CheckState.PASS)),
)
def test_generic_adapter_rejects_only_nonpositive_morphology_radii(
    tmp_path, monkeypatch, radius, expected_state
):
    morphology = _uniform_radius_morphology(tmp_path, radius)
    circuit = CircuitSpec(
        connectome=ConnectomeRef("test:v1", "Test", str(tmp_path)),
        neuron_ids=("1",),
        morphology_sha256=_morphology_hashes((morphology,)),
    )
    monkeypatch.setattr(generic, "_runtime_check", _passing_runtime)
    adapter = generic.GenericExperimentAdapter({"arbor": os.sys.executable})

    report = adapter.validate(
        circuit,
        _experiment(),
        (morphology,),
        output_root=tmp_path / "runs",
    )

    radius_check = next(
        check for check in report.checks if check.key == "morphology_radii"
    )
    assert radius_check.state == expected_state
    if expected_state == CheckState.FAIL:
        assert radius_check.blocking
        assert "SWC quality/healer" in radius_check.detail
        with pytest.raises(ValueError, match="non-positive SWC radius"):
            adapter.request_payload(
                circuit,
                _experiment(),
                (morphology,),
                report,
                output_root=tmp_path / "runs",
            )
    else:
        assert "tiny positive radii are preserved" in radius_check.detail


def test_generic_adapter_accepts_one_classic_hh_cell_and_writes_contract(
    tmp_path, monkeypatch
):
    morphology = _morphology(tmp_path)
    circuit = CircuitSpec(
        connectome=ConnectomeRef("test:v1", "Test", str(tmp_path)),
        neuron_ids=("1",),
        morphology_sha256={
            "1": hashlib.sha256(
                Path(morphology.record.swc_path).read_bytes()
            ).hexdigest()
        },
    )
    experiment = _experiment()
    monkeypatch.setattr(generic, "_runtime_check", _passing_runtime)
    adapter = generic.GenericExperimentAdapter({"arbor": os.sys.executable})
    output = tmp_path / "runs"
    report = adapter.validate(circuit, experiment, (morphology,), output_root=output)
    assert report.ok
    assert all(
        check.state != CheckState.FAIL or not check.blocking
        for check in report.checks
    )

    plan = adapter.plan(circuit, experiment, output_root=output)
    payload = adapter.request_payload(
        circuit,
        experiment,
        (morphology,),
        report,
        output_root=output,
    )
    request = adapter.write_request(plan.arguments[-1], payload)
    assert request.is_file()
    assert json.loads((request.parent / "experiment.json").read_text())["name"] == (
        experiment.name
    )
    assert json.loads((request.parent / "run_manifest.json").read_text())[
        "state"
    ] == "queued"
    assert plan.expected_summary_path == str(request.parent / "summary.json")


def test_generic_adapter_accepts_bmtk_chemical_lane_and_selects_bionet_worker(
    tmp_path, monkeypatch
):
    morphology = _morphology(tmp_path)
    circuit = CircuitSpec(
        connectome=ConnectomeRef("test:v1", "Test", str(tmp_path)),
        neuron_ids=("1",),
        morphology_sha256={
            "1": hashlib.sha256(
                Path(morphology.record.swc_path).read_bytes()
            ).hexdigest()
        },
    )
    experiment = _experiment("bmtk")
    monkeypatch.setattr(generic, "_runtime_check", _passing_runtime)
    adapter = generic.GenericExperimentAdapter({"bmtk": os.sys.executable})

    report = adapter.validate(
        circuit,
        experiment,
        (morphology,),
        output_root=tmp_path / "runs",
    )
    assert report.ok
    connectivity = next(
        check for check in report.checks if check.key == "bmtk_connectivity"
    )
    assert connectivity.state == CheckState.PASS

    plan = adapter.plan(circuit, experiment, output_root=tmp_path / "runs")
    assert adapter.bmtk_worker_path != adapter.worker_path
    assert plan.arguments[:2] == ("-B", str(adapter.bmtk_worker_path))
    assert Path(plan.arguments[1]).name == "bmtk_bionet_worker.py"
    assert plan.arguments[2] == "--request"
    assert plan.environment["NEURON_MODULE_OPTIONS"] == "-nogui"
    assert "PYTHONPATH" not in plan.environment
    assert "DISPLAY" not in plan.environment


def test_generic_adapter_blocks_bmtk_nonintegral_duration_sampling_grid(
    tmp_path, monkeypatch
):
    morphology = _morphology(tmp_path)
    circuit = CircuitSpec(
        connectome=ConnectomeRef("test:v1", "Test", str(tmp_path)),
        neuron_ids=("1",),
        morphology_sha256=_morphology_hashes((morphology,)),
    )
    experiment_payload = _experiment("bmtk").to_dict()
    experiment_payload["recording"]["sample_dt_ms"] = 0.075
    experiment = ExperimentSpec.from_dict(experiment_payload)
    monkeypatch.setattr(generic, "_runtime_check", _passing_runtime)

    report = generic.GenericExperimentAdapter(
        {"bmtk": os.sys.executable}
    ).validate(
        circuit,
        experiment,
        (morphology,),
        output_root=tmp_path / "runs",
    )

    failure = next(check for check in report.checks if check.key == "bmtk_sampling")
    assert failure.state == CheckState.FAIL
    assert failure.blocking
    assert "integer multiple" in failure.detail
    assert "exact divisor" in failure.detail


def test_generic_adapter_blocks_bmtk_gap_policy_without_approximating_it(
    tmp_path, monkeypatch
):
    morphology = _morphology(tmp_path)
    circuit = CircuitSpec(
        connectome=ConnectomeRef("test:v1", "Test", str(tmp_path)),
        neuron_ids=("1",),
        morphology_sha256={
            "1": hashlib.sha256(
                Path(morphology.record.swc_path).read_bytes()
            ).hexdigest()
        },
        gap_junction_policy=GapJunctionPolicy(mode="ohmic"),
    )
    monkeypatch.setattr(generic, "_runtime_check", _passing_runtime)
    adapter = generic.GenericExperimentAdapter({"bmtk": os.sys.executable})
    report = adapter.validate(
        circuit,
        _experiment("bmtk"),
        (morphology,),
        output_root=tmp_path / "runs",
    )
    failure = next(
        check for check in report.checks if check.key == "bmtk_connectivity"
    )
    assert failure.state == CheckState.FAIL
    assert failure.blocking
    assert "never silently dropped" in failure.detail
    assert not adapter.needs_gap_catalogue(circuit, _experiment("bmtk"))
    assert not any(
        check.key in {"arbor_gap_catalogue", "neuron_gap_mechanisms"}
        for check in report.checks
    )


def test_generic_adapter_pins_bmtk_to_one_isolated_worker(tmp_path, monkeypatch):
    morphology = _morphology(tmp_path)
    circuit = CircuitSpec(
        connectome=ConnectomeRef("test:v1", "Test", str(tmp_path)),
        neuron_ids=("1",),
        morphology_sha256={
            "1": hashlib.sha256(
                Path(morphology.record.swc_path).read_bytes()
            ).hexdigest()
        },
    )
    experiment = ExperimentSpec.from_dict(
        {**_experiment("bmtk").to_dict(), "workers": 2}
    )
    monkeypatch.setattr(generic, "_runtime_check", _passing_runtime)
    report = generic.GenericExperimentAdapter(
        {"bmtk": os.sys.executable}
    ).validate(
        circuit,
        experiment,
        (morphology,),
        output_root=tmp_path / "runs",
    )
    failure = next(check for check in report.checks if check.key == "parallelism")
    assert failure.state == CheckState.FAIL
    assert "BMTK" in failure.detail


def test_generic_adapter_blocks_unrepresented_multi_cell_connectivity(
    tmp_path, monkeypatch
):
    first = _morphology(tmp_path, "1")
    second = _morphology(tmp_path, "2")
    circuit = CircuitSpec(
        connectome=ConnectomeRef("test:v1", "Test", str(tmp_path)),
        neuron_ids=("1", "2"),
    )
    monkeypatch.setattr(generic, "_runtime_check", _passing_runtime)
    report = generic.GenericExperimentAdapter({"arbor": os.sys.executable}).validate(
        circuit,
        _experiment(),
        (first, second),
        output_root=tmp_path / "runs",
    )
    assert not report.ok
    failure = next(check for check in report.checks if check.key == "circuit_scope")
    assert failure.state == CheckState.FAIL
    assert "edge manifest" in failure.detail


def test_generic_adapter_materializes_versioned_two_cell_gap_manifest(
    tmp_path, monkeypatch
):
    public_root = tmp_path / "Digifly Public"
    source_root = tmp_path / "full_manc"
    (source_root / "edges").mkdir(parents=True)
    (source_root / ".phase2_export_index.json").write_text("{}", encoding="utf-8")
    chemical_db = source_root / "edges" / "master_edges_cache.sqlite"
    with sqlite3.connect(chemical_db) as connection:
        connection.execute(
            "CREATE TABLE edges (pre_id INTEGER, post_id INTEGER, weight_uS REAL, "
            "delay_ms REAL, tau1_ms REAL, tau2_ms REAL, syn_e_rev_mV REAL, "
            "pre_x REAL, pre_y REAL, pre_z REAL, post_x REAL, post_y REAL, post_z REAL, "
            "syn_index INTEGER, pre_syn_index INTEGER, post_syn_index INTEGER, "
            "pre_match_um REAL, post_match_um REAL)"
        )
        connection.execute("CREATE INDEX idx_edges_pre_id ON edges(pre_id)")
        connection.execute("CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT)")
        connection.execute("INSERT INTO meta VALUES ('fixture', 'gap-only')")
    sentinel = (
        source_root
        / "DN"
        / "DNp01"
        / "10000"
        / "10000_axodendro_with_synapses.swc"
    )
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("sentinel", encoding="utf-8")
    first = _morphology(tmp_path, "10000")
    second = _morphology(tmp_path, "10110")
    gap_root = (
        public_root
        / "Phase 2_Arbor_staging"
        / "Projects"
        / "Escape-SIZ"
        / "arbor_inputs"
        / "giant_fiber_ablation"
    )
    gap_root.mkdir(parents=True)
    gap_table = gap_root / "gap_contacts_arbor.csv"
    gap_table.write_text(
        "source_edge_rowid,pre_id,post_id,g_uS,pre_x,pre_y,pre_z,post_x,post_y,post_z\n"
        "42,10000,10110,0.001,0,0,5,0,0,50\n",
        encoding="utf-8",
    )
    import hashlib

    digest = hashlib.sha256(gap_table.read_bytes()).hexdigest()
    (gap_root / "manifest.json").write_text(
        json.dumps(
            {
                "records": [
                    {
                        "kind": "gap_contacts",
                        "path": "gap_contacts_arbor.csv",
                        "selected_neuron_ids": [10000, 10110],
                        "row_count": 1,
                        "sha256": digest,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    circuit = CircuitSpec(
        connectome=ConnectomeRef(
            "manc:v1.2.1:full-local",
            "MANC full",
            str(source_root),
            "manc_v1.2.1",
        ),
        neuron_ids=("10000", "10110"),
        morphology_sha256=_morphology_hashes((first, second)),
        gap_junction_policy=GapJunctionPolicy(mode="ohmic"),
    )
    circuit.set_connection_class_enabled("10000", "10110", "gap_junction", True)
    circuit.set_connection_class_enabled("10000", "10110", "chemical", False)
    monkeypatch.setattr(generic, "_runtime_check", _passing_runtime)
    adapter = generic.GenericExperimentAdapter(
        {"arbor": os.sys.executable}, digifly_public_root=public_root
    )
    output = tmp_path / "runs"
    catalogue = adapter.gap_catalogue_path(output)
    catalogue.parent.mkdir(parents=True)
    catalogue.write_bytes(b"test catalogue")
    catalogue.with_name("catalogue_manifest.json").write_text("{}", encoding="utf-8")
    report = adapter.validate(
        circuit, _experiment(), (first, second), output_root=output
    )
    assert report.ok
    payload = adapter.request_payload(
        circuit, _experiment(), (first, second), report, output_root=output
    )
    edge_manifest = payload["edge_manifest"]
    assert edge_manifest["schema_version"] == 2
    assert edge_manifest["kind"] == "explicit_selected_subgraph"
    edge = edge_manifest["electrical_edges"][0]
    assert edge["contact_count"] == 1
    contact = edge["contacts"][0]
    assert contact["effective_g_uS"] == pytest.approx(0.001)
    assert contact["pre_source_node_id"] == 2
    assert contact["post_source_node_id"] == 4
    assert contact["pre_coordinate_um"] == [0.0, 0.0, 5.0]
    assert contact["post_coordinate_um"] == [0.0, 0.0, 50.0]
    assert contact["coordinate_sources"] == {"pre": "pre_xyz", "post": "post_xyz"}
    assert edge_manifest["chemical_edges"] == []


@pytest.mark.parametrize("engine", ("arbor", "bmtk"))
def test_generic_adapter_materializes_arbitrary_manc_chemical_subgraph(
    tmp_path, monkeypatch, engine
):
    source_root = tmp_path / "full_manc"
    (source_root / "edges").mkdir(parents=True)
    (source_root / ".phase2_export_index.json").write_text("{}", encoding="utf-8")
    sentinel = (
        source_root
        / "DN"
        / "DNp01"
        / "10000"
        / "10000_axodendro_with_synapses.swc"
    )
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("sentinel", encoding="utf-8")
    database = source_root / "edges" / "master_edges_cache.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE edges (pre_id INTEGER, post_id INTEGER, weight_uS REAL, "
            "delay_ms REAL, tau1_ms REAL, tau2_ms REAL, syn_e_rev_mV REAL, "
            "pre_x REAL, pre_y REAL, pre_z REAL, post_x REAL, post_y REAL, post_z REAL, "
            "syn_index INTEGER, pre_syn_index INTEGER, post_syn_index INTEGER, "
            "pre_match_um REAL, post_match_um REAL)"
        )
        connection.execute("CREATE INDEX idx_edges_pre_id ON edges(pre_id)")
        connection.execute("CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT)")
        connection.execute("INSERT INTO meta VALUES ('fixture', 'three-cell')")
        connection.executemany(
            "INSERT INTO edges (pre_id, post_id, weight_uS, post_x, post_y, post_z) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                (1, 2, 0.002, 0.0, 0.0, 10.0),
                (2, 3, 0.003, 0.0, 0.0, 50.0),
                (3, 1, 0.004, 0.0, 0.0, 5.0),
                (1, 99, 99.0, 0.0, 0.0, 10.0),
            ),
        )
    morphologies = tuple(_morphology(tmp_path, str(value)) for value in (1, 2, 3))
    circuit = CircuitSpec(
        connectome=ConnectomeRef(
            "manc:v1.2.1:full-local",
            "MANC full",
            str(source_root),
            "manc_v1.2.1",
        ),
        neuron_ids=("1", "2", "3"),
        morphology_sha256={
            morphology.record.neuron_id: hashlib.sha256(
                Path(morphology.record.swc_path).read_bytes()
            ).hexdigest()
            for morphology in morphologies
        },
    )
    circuit.set_connection_class_enabled("2", "3", "chemical", False)
    monkeypatch.setattr(generic, "_runtime_check", _passing_runtime)
    experiment = _experiment(engine)
    adapter = generic.GenericExperimentAdapter({engine: os.sys.executable})
    report = adapter.validate(
        circuit, experiment, morphologies, output_root=tmp_path / "runs"
    )
    assert report.ok
    payload = adapter.request_payload(
        circuit,
        experiment,
        morphologies,
        report,
        output_root=tmp_path / "runs",
    )
    manifest = payload["edge_manifest"]
    assert manifest["kind"] == "explicit_selected_subgraph"
    assert [(row["pre_id"], row["post_id"]) for row in manifest["chemical_edges"]] == [
        ("1", "2"),
        ("3", "1"),
    ]
    assert manifest["chemical_edges"][0]["weight_uS"] == pytest.approx(0.002)
    assert manifest["chemical_edges"][0]["delay_ms"] == pytest.approx(1.0)
    assert manifest["chemical_edges"][0]["parameter_sources"]["delay"] == (
        "circuit_policy"
    )


def test_standalone_worker_rechecks_fail_closed_capabilities():
    circuit = CircuitSpec(neuron_ids=("1",)).to_dict()
    experiment = _experiment().to_dict()
    generic_worker = Path(generic.__file__).parents[1] / "workers" / "generic_experiment_worker.py"
    namespace: dict[str, object] = {"__name__": "generic_experiment_worker_test"}
    exec(compile(generic_worker.read_text(encoding="utf-8"), generic_worker, "exec"), namespace)
    validate = namespace["_validate_request_capabilities"]

    validate("arbor", circuit, experiment)
    circuit["membrane"]["replace_builtin_hh"] = True
    with pytest.raises(ValueError, match="Native membrane mechanisms"):
        validate("arbor", circuit, experiment)


def test_standalone_worker_accepts_neuron_multi_cell_manifest_and_rejects_bad_endpoints():
    circuit = CircuitSpec(neuron_ids=("1", "2")).to_dict()
    experiment = _experiment("neuron").to_dict()
    worker = Path(generic.__file__).parents[1] / "workers" / "generic_experiment_worker.py"
    namespace: dict[str, object] = {"__name__": "generic_experiment_worker_test"}
    exec(compile(worker.read_text(encoding="utf-8"), worker, "exec"), namespace)
    validate = namespace["_validate_request_capabilities"]
    manifest = {
        "schema_version": 2,
        "kind": "explicit_selected_subgraph",
        "neuron_ids": ["1", "2"],
        "chemical_edges": [],
        "electrical_edges": [],
    }

    validate("neuron", circuit, experiment, manifest)
    manifest["gap_junction_policy"] = {"mode": "ohmic"}
    manifest["electrical_edges"] = [
        {
            "neuron_a": "1",
            "neuron_b": "2",
            "contact_count": 1,
            "contacts": [
                {
                    "contact_id": "gap-0000001",
                    "pre_id": "1",
                    "post_id": "2",
                    "pre_source_node_id": 2,
                    "post_source_node_id": 4,
                    "pre_coordinate_um": [0.0, 0.0, 5.0],
                    "post_coordinate_um": [0.0, 0.0, 50.0],
                    "coordinate_sources": {"pre": "pre_xyz", "post": "post_xyz"},
                    "effective_g_uS": -0.001,
                }
            ],
        }
    ]
    with pytest.raises(ValueError, match="invalid conductance"):
        validate("neuron", circuit, experiment, manifest)


def test_swc_normalization_preserves_topology_and_maps_ids(tmp_path):
    source = tmp_path / "out-of-order.swc"
    source.write_text(
        "1 1 0 0 0 5 -1\n"
        "2 3 0 0 20 1 4\n"
        "3 3 0 0 10 2 1\n"
        "4 3 0 0 15 1.5 3\n",
        encoding="utf-8",
    )
    normalized, mapping, digest = _normalize_swc(source, tmp_path)
    rows = [
        line.split()
        for line in normalized.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    assert all(int(row[6]) < int(row[0]) for row in rows if int(row[6]) >= 0)
    assert [float(row[4]) for row in rows] == [0.0, 10.0, 15.0, 20.0]
    assert "2,4" in mapping.read_text(encoding="utf-8")
    assert len(digest) == 64


def _write_generic_result_summary(
    run_dir: Path,
    artifact_path: str,
) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    summary = run_dir / "summary.json"
    summary.write_text(
        json.dumps(
            {
                "schema_version": generic.GENERIC_RESULT_SCHEMA_VERSION,
                "status": "complete",
                "title": "Confinement fixture",
                "artifacts": [
                    {
                        "kind": "table",
                        "path": artifact_path,
                        "label": "Fixture artifact",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return summary


def test_generic_result_loader_accepts_run_relative_artifact(tmp_path):
    run_dir = tmp_path / "run"
    artifact = run_dir / "tables" / "traces.csv"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("time_ms,voltage_mV\n", encoding="utf-8")
    summary = _write_generic_result_summary(run_dir, "tables/traces.csv")

    result = generic.load_generic_experiment_result(summary)

    assert result.artifacts[0].path == str(artifact.resolve())
    assert result.artifacts[0].exists


def test_generic_result_loader_rejects_absolute_artifact_even_inside_run(tmp_path):
    run_dir = tmp_path / "run"
    artifact = run_dir / "traces.csv"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("time_ms,voltage_mV\n", encoding="utf-8")
    summary = _write_generic_result_summary(run_dir, str(artifact))

    with pytest.raises(ValueError, match="run-relative, not absolute"):
        generic.load_generic_experiment_result(summary)


def test_generic_result_loader_rejects_parent_traversal(tmp_path):
    outside = tmp_path / "outside.csv"
    outside.write_text("private\n", encoding="utf-8")
    summary = _write_generic_result_summary(tmp_path / "run", "../outside.csv")

    with pytest.raises(ValueError, match=r"cannot contain '\.\.'"):
        generic.load_generic_experiment_result(summary)


def test_generic_result_loader_rejects_symlink_to_external_artifact(tmp_path):
    outside = tmp_path / "outside.csv"
    outside.write_text("private\n", encoding="utf-8")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    link = run_dir / "traces.csv"
    try:
        link.symlink_to(outside)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"File symlinks are unavailable: {exc}")
    summary = _write_generic_result_summary(run_dir, link.name)

    with pytest.raises(ValueError, match="resolves outside its run directory"):
        generic.load_generic_experiment_result(summary)


@pytest.mark.parametrize("engine,module", [("arbor", "arbor"), ("neuron", "neuron")])
def test_generic_worker_executes_installed_simulator(
    tmp_path, engine, module
):
    runtime = Path("/opt/anaconda3/bin/python3.12")
    if not runtime.is_file():
        pytest.skip("Local scientific runtime is not installed")
    probe_environment = sanitized_external_environment({})
    probe_environment.pop("DISPLAY", None)
    try:
        probe = subprocess.run(
            [str(runtime), "-B", "-c", f"import {module}"],
            env=probe_environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired:
        pytest.skip(f"{module} import exceeded the local runtime probe timeout")
    if probe.returncode:
        pytest.skip(f"{module} is unavailable in the local scientific runtime")

    morphology = _morphology(tmp_path)
    circuit = CircuitSpec(
        connectome=ConnectomeRef("test:v1", "Test", str(tmp_path)),
        neuron_ids=("1",),
        morphology_sha256=_morphology_hashes((morphology,)),
    )
    experiment = _experiment(engine)
    adapter = generic.GenericExperimentAdapter({engine: runtime})
    report = PreflightReport((_passing_runtime(),))
    output = tmp_path / "runs"
    plan = adapter.plan(circuit, experiment, output_root=output)
    payload = adapter.request_payload(
        circuit,
        experiment,
        (morphology,),
        report,
        output_root=output,
    )
    adapter.write_request(plan.arguments[-1], payload)
    environment = sanitized_external_environment(plan.environment)
    environment.pop("DISPLAY", None)
    completed = subprocess.run(
        [plan.program, *plan.arguments],
        cwd=plan.working_directory,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    result = generic.load_generic_experiment_result(plan.expected_summary_path)
    assert result.status == "complete"
    assert result.metadata["Simulator version"] != "unknown"
    assert result.metadata["Recorded samples"] > 0
    assert result.metadata["Morphology transform"] == (
        "parent_before_child_reindex_segment_tree_root_stub_v1"
        if engine == "arbor"
        else "parent_before_child_reindex_v1"
    )
    assert any(artifact.label == "Soma voltage traces" for artifact in result.artifacts)
    assert any(artifact.label == "Normalized morphology" for artifact in result.artifacts)


def test_generic_worker_executes_two_cell_chemical_neuron_network(tmp_path):
    runtime = Path("/opt/anaconda3/bin/python3.12")
    if not runtime.is_file():
        pytest.skip("Local scientific runtime is not installed")
    probe_environment = sanitized_external_environment(
        {"NEURON_MODULE_OPTIONS": "-nogui"}
    )
    probe_environment.pop("DISPLAY", None)
    probe = subprocess.run(
        [str(runtime), "-B", "-c", "import neuron"],
        env=probe_environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if probe.returncode:
        pytest.skip("NEURON is unavailable in the local scientific runtime")

    source_root = tmp_path / "full_manc"
    (source_root / "edges").mkdir(parents=True)
    (source_root / ".phase2_export_index.json").write_text("{}", encoding="utf-8")
    sentinel = (
        source_root
        / "DN"
        / "DNp01"
        / "10000"
        / "10000_axodendro_with_synapses.swc"
    )
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("sentinel", encoding="utf-8")
    database = source_root / "edges" / "master_edges_cache.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE edges (pre_id INTEGER, post_id INTEGER, weight_uS REAL, "
            "delay_ms REAL, tau1_ms REAL, tau2_ms REAL, syn_e_rev_mV REAL, "
            "pre_x REAL, pre_y REAL, pre_z REAL, post_x REAL, post_y REAL, post_z REAL, "
            "syn_index INTEGER, pre_syn_index INTEGER, post_syn_index INTEGER, "
            "pre_match_um REAL, post_match_um REAL)"
        )
        connection.execute("CREATE INDEX idx_edges_pre_id ON edges(pre_id)")
        connection.execute("CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT)")
        connection.execute("INSERT INTO meta VALUES ('fixture', 'neuron-chemical')")
        connection.execute(
            "INSERT INTO edges (pre_id, post_id, weight_uS, delay_ms, tau1_ms, "
            "tau2_ms, syn_e_rev_mV, post_x, post_y, post_z) "
            "VALUES (1, 2, 0.5, 0.5, 0.2, 2.0, 0.0, 0.0, 0.0, 10.0)"
        )
    morphologies = (_morphology(tmp_path, "1"), _morphology(tmp_path, "2"))
    circuit = CircuitSpec(
        connectome=ConnectomeRef(
            "manc:v1.2.1:full-local",
            "MANC full",
            str(source_root),
            "manc_v1.2.1",
        ),
        neuron_ids=("1", "2"),
        morphology_sha256=_morphology_hashes(morphologies),
    )
    experiment = ExperimentSpec(
        name="NEURON two-cell chemical smoke",
        engine="neuron",
        duration_ms=10.0,
        integration_dt_ms=0.025,
        temperature_C=22.0,
        workers=1,
        stimuli=(
            StimulusSpec(
                waveform="step",
                target_neuron_ids=("1",),
                amplitude_nA=5.0,
                delay_ms=1.0,
                pulse_width_ms=2.0,
                pulse_count=1,
            ),
        ),
        conditions=(
            ConditionSpec(name="Chemical enabled"),
            ConditionSpec(name="Chemical disabled", chemical_synapses_enabled=False),
        ),
        recording=RecordingSpec(
            target_neuron_ids=("1", "2"),
            target_region="soma",
            sample_dt_ms=0.05,
            make_plots=False,
        ),
    )
    adapter = generic.GenericExperimentAdapter({"neuron": runtime})
    output = tmp_path / "runs"
    report = adapter.validate(
        circuit,
        experiment,
        morphologies,
        output_root=output,
    )
    assert report.ok
    plan = adapter.plan(circuit, experiment, output_root=output)
    payload = adapter.request_payload(
        circuit,
        experiment,
        morphologies,
        report,
        output_root=output,
    )
    adapter.write_request(plan.arguments[-1], payload)
    environment = sanitized_external_environment(
        {**plan.environment, "NEURON_MODULE_OPTIONS": "-nogui"}
    )
    environment.pop("DISPLAY", None)
    completed = subprocess.run(
        [plan.program, *plan.arguments],
        cwd=plan.working_directory,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    rows = list(csv.DictReader((Path(plan.working_directory) / "voltage_traces.csv").open()))
    grouped: dict[tuple[str, str], list[float]] = {}
    for row in rows:
        grouped.setdefault((row["condition"], row["neuron_id"]), []).append(
            float(row["voltage_mV"])
        )
    assert set(grouped) == {
        ("Chemical enabled", "1"),
        ("Chemical enabled", "2"),
        ("Chemical disabled", "1"),
        ("Chemical disabled", "2"),
    }
    assert max(grouped[("Chemical enabled", "2")]) > (
        max(grouped[("Chemical disabled", "2")]) + 1.0
    )
    result = generic.load_generic_experiment_result(plan.expected_summary_path)
    assert result.metadata["Chemical contacts"] == 1
    assert result.metadata["Electrical contacts"] == 0


@pytest.mark.parametrize(
    "gap_mode", ("ohmic", "rectifying", "heterotypic_rectifying")
)
def test_generic_worker_executes_two_cell_neuron_gap_network(tmp_path, gap_mode):
    runtime = Path("/opt/anaconda3/bin/python3.12")
    if not runtime.is_file():
        pytest.skip("Local scientific runtime is not installed")
    probe_environment = sanitized_external_environment(
        {"NEURON_MODULE_OPTIONS": "-nogui"}
    )
    probe_environment.pop("DISPLAY", None)
    probe = subprocess.run(
        [str(runtime), "-B", "-c", "import neuron"],
        env=probe_environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if probe.returncode:
        pytest.skip("NEURON is unavailable in the local scientific runtime")

    source_root = tmp_path / "full_manc"
    public_root = tmp_path / "Digifly Public"
    (source_root / "edges").mkdir(parents=True)
    (source_root / ".phase2_export_index.json").write_text("{}", encoding="utf-8")
    sentinel = (
        source_root
        / "DN"
        / "DNp01"
        / "10000"
        / "10000_axodendro_with_synapses.swc"
    )
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("sentinel", encoding="utf-8")
    database = source_root / "edges" / "master_edges_cache.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE edges (pre_id INTEGER, post_id INTEGER, weight_uS REAL, "
            "delay_ms REAL, tau1_ms REAL, tau2_ms REAL, syn_e_rev_mV REAL, "
            "pre_x REAL, pre_y REAL, pre_z REAL, post_x REAL, post_y REAL, post_z REAL, "
            "syn_index INTEGER, pre_syn_index INTEGER, post_syn_index INTEGER, "
            "pre_match_um REAL, post_match_um REAL)"
        )
        connection.execute("CREATE INDEX idx_edges_pre_id ON edges(pre_id)")
        connection.execute("CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT)")
        connection.execute("INSERT INTO meta VALUES ('fixture', 'neuron-ohmic')")
    gap_root = (
        public_root
        / "Phase 2_Arbor_staging"
        / "Projects"
        / "Escape-SIZ"
        / "arbor_inputs"
        / "giant_fiber_ablation"
    )
    gap_root.mkdir(parents=True)
    gap_table = gap_root / "gap_contacts_arbor.csv"
    gap_table.write_text(
        "source_edge_rowid,pre_id,post_id,g_uS,pre_x,pre_y,pre_z,post_x,post_y,post_z\n"
        "1,1,2,0.1,0,0,5,0,0,50\n",
        encoding="utf-8",
    )
    import hashlib

    (gap_root / "manifest.json").write_text(
        json.dumps(
            {
                "records": [
                    {
                        "kind": "gap_contacts",
                        "path": "gap_contacts_arbor.csv",
                        "selected_neuron_ids": [1, 2],
                        "row_count": 1,
                        "sha256": hashlib.sha256(gap_table.read_bytes()).hexdigest(),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    morphologies = (_morphology(tmp_path, "1"), _morphology(tmp_path, "2"))
    circuit = CircuitSpec(
        connectome=ConnectomeRef(
            "manc:v1.2.1:full-local",
            "MANC full",
            str(source_root),
            "manc_v1.2.1",
        ),
        neuron_ids=("1", "2"),
        morphology_sha256=_morphology_hashes(morphologies),
        gap_junction_policy=GapJunctionPolicy(mode=gap_mode, g_uS=0.1),
    )
    circuit.set_connection_class_enabled("1", "2", "chemical", False)
    experiment = ExperimentSpec(
        name=f"NEURON two-cell {gap_mode} smoke",
        engine="neuron",
        duration_ms=10.0,
        integration_dt_ms=0.025,
        temperature_C=22.0,
        workers=1,
        stimuli=(
            StimulusSpec(
                waveform="step",
                target_neuron_ids=("1",),
                amplitude_nA=5.0,
                delay_ms=1.0,
                pulse_width_ms=2.0,
                pulse_count=1,
            ),
        ),
        conditions=(
            ConditionSpec(name="Gap enabled"),
            ConditionSpec(name="Gap disabled", gap_junctions_enabled=False),
        ),
        recording=RecordingSpec(
            target_neuron_ids=("1", "2"),
            target_region="soma",
            sample_dt_ms=0.05,
            make_plots=False,
        ),
    )
    adapter = generic.GenericExperimentAdapter(
        {"neuron": runtime}, digifly_public_root=public_root
    )
    output = tmp_path / "runs"
    mechanism_dir = adapter.ensure_gap_catalogue(
        circuit,
        experiment,
        output_root=output,
    )
    assert mechanism_dir is not None
    assert (mechanism_dir / "mechanism_manifest.json").is_file()
    report = adapter.validate(circuit, experiment, morphologies, output_root=output)
    assert report.ok
    plan = adapter.plan(circuit, experiment, output_root=output)
    payload = adapter.request_payload(
        circuit,
        experiment,
        morphologies,
        report,
        output_root=output,
    )
    assert len(payload["neuron_gap_manifest_sha256"]) == 64
    adapter.write_request(plan.arguments[-1], payload)
    environment = sanitized_external_environment(plan.environment)
    environment.pop("DISPLAY", None)
    completed = subprocess.run(
        [plan.program, *plan.arguments],
        cwd=plan.working_directory,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    rows = list(csv.DictReader((Path(plan.working_directory) / "voltage_traces.csv").open()))
    grouped: dict[tuple[str, str], list[float]] = {}
    for row in rows:
        grouped.setdefault((row["condition"], row["neuron_id"]), []).append(
            float(row["voltage_mV"])
        )
    assert set(grouped) == {
        ("Gap enabled", "1"),
        ("Gap enabled", "2"),
        ("Gap disabled", "1"),
        ("Gap disabled", "2"),
    }
    assert max(grouped[("Gap enabled", "2")]) > (
        max(grouped[("Gap disabled", "2")]) + 1.0
    )
    resolved = json.loads(
        (Path(plan.working_directory) / "resolved_edge_manifest.json").read_text()
    )
    contact = resolved["electrical_edges"][0]["contacts"][0]
    assert contact["pre_run_node_id"] == 2
    assert contact["post_run_node_id"] == 4
    result = generic.load_generic_experiment_result(plan.expected_summary_path)
    assert result.metadata["Electrical contacts"] == 1
    assert result.metadata["Gap-junction mode"] == gap_mode
    assert len(result.metadata["NEURON gap mechanism library SHA-256"]) == 64


def test_generic_worker_executes_three_cell_chemical_arbor_network(
    tmp_path, monkeypatch
):
    runtime = Path("/opt/anaconda3/bin/python3.12")
    if not runtime.is_file():
        pytest.skip("Local scientific runtime is not installed")
    probe = subprocess.run(
        [str(runtime), "-B", "-c", "import arbor"],
        env=sanitized_external_environment({}),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if probe.returncode:
        pytest.skip("Arbor is unavailable in the local scientific runtime")
    cached_catalogue = (
        Path.home()
        / "Digifly Workstation Workspace/runs/_runtime/arbor_catalogues/"
        "arbor-0.12.2-darwin-arm64-digifly-gap-v1/digifly_gap-catalogue.so"
    )
    if not cached_catalogue.is_file() or not cached_catalogue.with_name(
        "catalogue_manifest.json"
    ).is_file():
        pytest.skip("A locally ABI-qualified Arbor gap catalogue is not available")
    monkeypatch.setenv("DIGIFLY_ARBOR_GAP_CATALOGUE", str(cached_catalogue))

    source_root = tmp_path / "full_manc"
    public_root = tmp_path / "Digifly Public"
    (source_root / "edges").mkdir(parents=True)
    (source_root / ".phase2_export_index.json").write_text("{}", encoding="utf-8")
    sentinel = (
        source_root
        / "DN"
        / "DNp01"
        / "10000"
        / "10000_axodendro_with_synapses.swc"
    )
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("sentinel", encoding="utf-8")
    database = source_root / "edges" / "master_edges_cache.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE edges (pre_id INTEGER, post_id INTEGER, weight_uS REAL, "
            "delay_ms REAL, tau1_ms REAL, tau2_ms REAL, syn_e_rev_mV REAL, "
            "pre_x REAL, pre_y REAL, pre_z REAL, post_x REAL, post_y REAL, post_z REAL, "
            "syn_index INTEGER, pre_syn_index INTEGER, post_syn_index INTEGER, "
            "pre_match_um REAL, post_match_um REAL)"
        )
        connection.execute("CREATE INDEX idx_edges_pre_id ON edges(pre_id)")
        connection.execute("CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT)")
        connection.execute("INSERT INTO meta VALUES ('fixture', 'arbor-chemical')")
        connection.execute(
            "INSERT INTO edges (pre_id, post_id, weight_uS, delay_ms, tau1_ms, "
            "tau2_ms, syn_e_rev_mV, post_x, post_y, post_z) "
            "VALUES (1, 2, 0.01, 0.5, 0.2, 1.5, 0.0, 0.0, 0.0, 10.0)"
        )
        connection.execute(
            "INSERT INTO edges (pre_id, post_id, weight_uS, delay_ms, tau1_ms, "
            "tau2_ms, syn_e_rev_mV, post_x, post_y, post_z) "
            "VALUES (2, 3, 0.01, 0.5, 0.2, 1.5, 0.0, 0.0, 0.0, 10.0)"
        )
    gap_root = (
        public_root
        / "Phase 2_Arbor_staging"
        / "Projects"
        / "Escape-SIZ"
        / "arbor_inputs"
        / "giant_fiber_ablation"
    )
    gap_root.mkdir(parents=True)
    gap_table = gap_root / "gap_contacts_arbor.csv"
    gap_table.write_text(
        "source_edge_rowid,pre_id,post_id,g_uS,pre_x,pre_y,pre_z,post_x,post_y,post_z\n"
        "1,1,2,0.001,,,,0,0,10\n"
        "2,2,3,0.001,,,,0,0,10\n",
        encoding="utf-8",
    )
    import hashlib

    (gap_root / "manifest.json").write_text(
        json.dumps(
            {
                "records": [
                    {
                        "kind": "gap_contacts",
                        "path": "gap_contacts_arbor.csv",
                        "selected_neuron_ids": [1, 2, 3],
                        "row_count": 2,
                        "sha256": hashlib.sha256(gap_table.read_bytes()).hexdigest(),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    morphologies = tuple(_morphology(tmp_path, value) for value in ("1", "2", "3"))
    circuit = CircuitSpec(
        connectome=ConnectomeRef(
            "manc:v1.2.1:full-local",
            "MANC full",
            str(source_root),
            "manc_v1.2.1",
        ),
        neuron_ids=("1", "2", "3"),
        morphology_sha256=_morphology_hashes(morphologies),
        gap_junction_policy=GapJunctionPolicy(mode="ohmic"),
    )
    experiment = _experiment("arbor")
    adapter = generic.GenericExperimentAdapter(
        {"arbor": runtime}, digifly_public_root=public_root
    )
    output = tmp_path / "runs"
    adapter.ensure_gap_catalogue(circuit, experiment, output_root=output)
    plan = adapter.plan(circuit, experiment, output_root=output)
    payload = adapter.request_payload(
        circuit,
        experiment,
        morphologies,
        PreflightReport((_passing_runtime(),)),
        output_root=output,
    )
    adapter.write_request(plan.arguments[-1], payload)
    environment = sanitized_external_environment(plan.environment)
    environment.pop("DISPLAY", None)
    completed = subprocess.run(
        [plan.program, *plan.arguments],
        cwd=plan.working_directory,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    result = generic.load_generic_experiment_result(plan.expected_summary_path)
    assert result.status == "complete"
    assert result.metadata["Chemical contacts"] == 2
    assert result.metadata["Electrical contacts"] == 2
