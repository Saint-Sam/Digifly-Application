from __future__ import annotations

import json
from pathlib import Path
import pytest

from digifly_app.core.circuit import CircuitSpec, ConnectomeRef
from digifly_app.core.connectomes import NeuronRecord
from digifly_app.core.mechanisms import membrane_profile
from digifly_app.core.morphology import load_custom_biophysics, load_swc, save_custom_morphology


def test_swc_load_and_non_destructive_custom_save(tmp_path):
    source = tmp_path / "10000.swc"
    source.write_text(
        "# source\n1 1 0 0 0 1 -1\n2 2 1 0 0 0.5 1\n3 3 1 2 0 0.25 2\n",
        encoding="utf-8",
    )
    record = NeuronRecord("10000", "DN", "DNp01", str(source), "manc")
    morphology = load_swc(record)
    assert len(morphology.nodes) == 3
    assert [segment.child_id for segment in morphology.segments] == [2, 3]

    circuit = CircuitSpec(connectome=ConnectomeRef("manc", "MANC", str(tmp_path)))
    circuit.apply_compartment_override("10000", (3,), {"branch_gnabar_s_cm2": 0.05})
    circuit.membrane = membrane_profile("phase2_para_shab")
    circuit.apply_compartment_mechanism_override("10000", (3,), circuit.membrane)
    bundle = save_custom_morphology(
        morphology,
        circuit,
        selected_engine="arbor",
        library_root=tmp_path / "library",
        label="test",
    )
    copied = bundle / "DN" / "DNp01" / "10000" / source.name
    assert copied.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")
    sidecar = json.loads((bundle / "digifly-biophysics.json").read_text(encoding="utf-8"))
    assert sidecar["schema_version"] == 2
    assert sidecar["compartment_overrides"]["3"]["branch_gnabar_s_cm2"] == 0.05
    assert sidecar["base_membrane"]["channels"]["para"]["suffix"] == "na16a"
    assert sidecar["compartment_mechanism_overrides"]["3"]["channels"]["shab"][
        "enabled"
    ]
    assert "gap_junction_policy" not in sidecar
    assert load_custom_biophysics(bundle, "10000", swc_path=copied) == sidecar
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["custom_swc"] == f"DN/DNp01/10000/{source.name}"
    assert not Path(manifest["custom_swc"]).is_absolute()
    assert len(manifest["source_sha256"]) == 64
    assert manifest["custom_sha256"] == manifest["source_sha256"]
    copied.write_text(copied.read_text(encoding="utf-8") + "# tampered\n", encoding="utf-8")
    with pytest.raises(ValueError, match="identity check failed"):
        load_custom_biophysics(bundle, "10000", swc_path=copied)
    assert source.exists()


@pytest.mark.parametrize(
    ("rows", "message"),
    (
        ("1 1 0 0 0 1 -1\n1 2 1 0 0 1 -1\n", "Duplicate"),
        ("1 1 0 0 0 1 -1\n2 2 1 0 0 1 99\n", "missing parent"),
        ("1 1 0 0 0 1 -1\n2 1 2 0 0 1 -1\n", "exactly one root"),
        ("1 1 0 0 0 1\n", "fewer than seven"),
        ("1 1 0 0 0 1 -2\n2 2 1 0 0 1 1\n", "invalid root sentinel"),
    ),
)
def test_swc_validation_rejects_ambiguous_topology(tmp_path, rows, message):
    source = tmp_path / "invalid.swc"
    source.write_text(rows, encoding="utf-8")
    record = NeuronRecord("1", "UNKNOWN", "Unknown", str(source), "test")
    with pytest.raises(ValueError, match=message):
        load_swc(record)
