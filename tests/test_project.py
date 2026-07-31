from __future__ import annotations

import json

import pytest

from digifly_app.core.project import DigiflyProject


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
