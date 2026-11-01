from __future__ import annotations


def test_standalone_draft_with_no_public_checkout_roundtrips(tmp_path):
    from digifly_app.core.project import DigiflyProject
    project = DigiflyProject(name="standalone", digifly_public_root="", output_root=str(tmp_path / "runs"))
    assert DigiflyProject.load(project.save(tmp_path / "standalone.digifly.json")).digifly_public_root == ""


def test_failed_atomic_project_replace_preserves_existing_file(tmp_path,monkeypatch):
    from digifly_app.core import project as module
    destination=tmp_path/'original.digifly.json'
    destination.write_text('existing project bytes')
    def fail(*args):raise OSError('inert replacement failure')
    monkeypatch.setattr(module.os,'replace',fail)
    with pytest.raises(OSError,match='replacement failure'):
        module.DigiflyProject(name='new',digifly_public_root='',output_root='runs').save(destination)
    assert destination.read_text()=='existing project bytes'
    assert list(tmp_path.iterdir())==[destination]


def test_explicit_unconfigured_runtimes_are_not_replaced_by_old_defaults(tmp_path):
    from digifly_app.core.project import DigiflyProject
    project=DigiflyProject(name='unconfigured',digifly_public_root='',output_root='runs',
        python_executable='',arbor_python_executable='',bmtk_python_executable='',netpyne_python_executable='')
    saved=DigiflyProject.load(project.save(tmp_path/'unconfigured.json'))
    assert (saved.python_executable,saved.arbor_python_executable,saved.bmtk_python_executable,saved.netpyne_python_executable)==('','','','')
    old=DigiflyProject.from_dict({'schema_version':2,'name':'old','output_root':'runs'})
    assert old.arbor_python_executable is None and old.bmtk_python_executable is None

import json

import pytest

from digifly_app.core.project import DigiflyProject
from digifly_app.core.circuit import CircuitSpec, ConnectomeRef
from digifly_app.core.experiment import EXPERIMENT_BUILDER_WORKFLOW, ExperimentSpec
from digifly_app.ui.circuit_builder import CIRCUIT_BUILDER_WORKFLOW


def test_project_round_trip(tmp_path):
    experiment = ExperimentSpec.pulse_train_comparison()
    project = DigiflyProject(
        name="Pulse comparison",
        digifly_public_root="/data/Digifly Public",
        output_root="/runs/digifly",
        selected_workflow=EXPERIMENT_BUILDER_WORKFLOW,
        experiment=experiment.to_dict(),
    )
    path = project.save(tmp_path / "experiment.digifly.json")
    loaded = DigiflyProject.load(path)
    assert loaded.name == "Pulse comparison"
    assert ExperimentSpec.from_dict(loaded.experiment).workers == 4
    assert loaded.schema_version == 2


def test_version_one_escape_siz_project_migrates_to_experiment_builder():
    loaded = DigiflyProject.from_dict(
        {
            "schema_version": 1,
            "name": "Old Escape-SIZ",
            "digifly_public_root": "/data",
            "output_root": "/runs",
            "selected_engine": "neuron",
            "selected_workflow": "escape_siz_gfc_contact_na",
            "experiment": {
                "frequency_hz": 80.0,
                "max_pulses": 7,
                "gap_enabled_amp_nA": 1.2,
                "nproc": 3,
            },
        }
    )
    experiment = ExperimentSpec.from_dict(loaded.experiment)
    assert loaded.schema_version == 2
    assert loaded.selected_workflow == EXPERIMENT_BUILDER_WORKFLOW
    assert experiment.engine == "neuron"
    assert experiment.stimuli[0].frequency_hz == 80.0
    assert experiment.stimuli[0].pulse_count == 7
    assert experiment.stimuli[0].amplitude_nA == 1.2
    assert experiment.workers == 3


def test_project_rejects_unknown_schema():
    with pytest.raises(ValueError, match="Unsupported"):
        DigiflyProject.from_dict(
            {
                "schema_version": 99,
                "name": "future",
                "digifly_public_root": "/data",
                "output_root": "/runs",
            }
        )


def test_circuit_builder_project_uses_existing_experiment_envelope(tmp_path):
    spec = CircuitSpec(
        connectome=ConnectomeRef("manc-v1-2-1", "MANC v1.2.1", "/data/swc"),
        neuron_ids=("10000", "14662"),
    )
    project = DigiflyProject(
        name="Circuit",
        digifly_public_root="/data/Digifly Public",
        output_root="/runs",
        selected_engine="arbor",
        selected_workflow=CIRCUIT_BUILDER_WORKFLOW,
        circuit=spec.to_dict(),
        experiment=ExperimentSpec().to_dict(),
    )
    loaded = DigiflyProject.load(project.save(tmp_path / "circuit.digifly.json"))
    restored = CircuitSpec.from_dict(loaded.circuit)
    assert loaded.selected_engine == "arbor"
    assert loaded.selected_workflow == CIRCUIT_BUILDER_WORKFLOW
    assert restored.neuron_ids == ("10000", "14662")
