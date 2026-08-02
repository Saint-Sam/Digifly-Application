from __future__ import annotations

import json

import pytest

from digifly_app.core.project import DigiflyProject
from digifly_app.core.circuit import CircuitSpec, ConnectomeRef
from digifly_app.ui.circuit_builder import CIRCUIT_BUILDER_WORKFLOW


def test_project_round_trip(tmp_path):
    project = DigiflyProject(
        name="Escape SIZ",
        digifly_public_root="/data/Digifly Public",
        output_root="/runs/digifly",
        experiment={"preset": "latest_gfc2_pairwise", "nproc": 1},
    )
    path = project.save(tmp_path / "escape.digifly.json")
    loaded = DigiflyProject.load(path)
    assert loaded.name == "Escape SIZ"
    assert loaded.experiment["nproc"] == 1
    assert loaded.schema_version == 1


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
        experiment=spec.to_dict(),
    )
    loaded = DigiflyProject.load(project.save(tmp_path / "circuit.digifly.json"))
    restored = CircuitSpec.from_dict(loaded.experiment)
    assert loaded.selected_engine == "arbor"
    assert loaded.selected_workflow == CIRCUIT_BUILDER_WORKFLOW
    assert restored.neuron_ids == ("10000", "14662")
