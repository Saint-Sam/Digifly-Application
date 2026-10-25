from __future__ import annotations

import hashlib
import json

import pytest

from digifly_app.core.circuit import CircuitSpec
from digifly_app.core.experiment import ExperimentSpec
from digifly_app.core.connectomes import NeuronRecord
from digifly_app.core.morphology import load_swc, save_custom_morphology
from digifly_app.workers import generic_experiment_worker as worker
from digifly_app.workers import bmtk_bionet_worker as bionet
from digifly_app.workers.parquet_edge_query_worker import _native_coordinate_um


def record(path):
    return NeuronRecord("10001", "DN", "DNp01", str(path), "male-cns:v0.9")


def legacy(tmp_path):
    path = tmp_path / "male-cns_v0.9/export_swc/DN/DNp01/10001/10001_axodendro_with_synapses.swc"
    path.parent.mkdir(parents=True)
    path.write_text("# bodyId 10001\n1 1 40 20 16 0.032 -1\n2 2 41 55 91 0.064 1\n")
    return path


def test_legacy_preview_and_worker_have_identical_micrometers(tmp_path):
    path = legacy(tmp_path)
    original = path.read_bytes()
    morphology = load_swc(record(path))
    assert morphology.bounds == (320, 328, 160, 440, 128, 728)
    assert morphology.nodes[0].radius == pytest.approx(0.256)
    output = tmp_path / "run"
    output.mkdir()
    normalized, node_map, _ = worker._normalize_swc(
        path, output, expected_source_sha256=hashlib.sha256(original).hexdigest(),
        coordinate_transform=morphology.coordinate_transform,
    )
    rows = worker._swc_rows(normalized)
    assert rows[0][2:6] == (320, 160, 128, 0.256)
    assert rows[1][2:6] == (328, 440, 728, 0.512)
    assert node_map.read_text().splitlines()[1:] == ["1,1", "2,2"]
    assert path.read_bytes() == original


def test_native_contact_points_share_skeleton_micrometers():
    assert _native_coordinate_um(40000) == 320
    assert _native_coordinate_um(86298) == pytest.approx(690.384)


def test_bionet_materialization_uses_same_coordinate_contract(tmp_path):
    path = legacy(tmp_path)
    morphology = load_swc(record(path))
    circuit = CircuitSpec(neuron_ids=("10001",)).to_dict()
    request = {"circuit": circuit, "experiment": ExperimentSpec().to_dict(), "morphologies": {"10001": {
        "path": str(path), "sha256": morphology.coordinate_transform["source_sha256"],
        "family": "DN", "coordinate_transform": morphology.coordinate_transform}}}
    output = tmp_path / "bionet"
    output.mkdir()
    cells, _, metadata = bionet._prepare_morphologies(request, output)
    assert worker._swc_rows(cells["10001"]["normalized_path"])[1][2:6] == (328, 440, 728, 0.512)
    assert metadata["10001"]["coordinate_transform"] == morphology.coordinate_transform


def test_old_legacy_worker_request_without_units_fails_closed(tmp_path):
    with pytest.raises(ValueError, match="explicit coordinate transform"):
        worker._normalize_swc(legacy(tmp_path), tmp_path)


def test_custom_save_reload_retains_units_without_double_conversion(tmp_path):
    path = legacy(tmp_path)
    first = load_swc(record(path))
    bundle = save_custom_morphology(first, CircuitSpec(), selected_engine="arbor",
                                    library_root=tmp_path / "library")
    manifest = json.loads((bundle / "manifest.json").read_text())
    copied = bundle / manifest["custom_swc"]
    second = load_swc(record(copied))
    assert second.nodes == first.nodes
    assert copied.read_bytes() == path.read_bytes()
    copied.write_text(copied.read_text() + "# changed\n")
    with pytest.raises(ValueError, match="identity check failed"):
        load_swc(record(copied))


def test_recovered_micrometers_are_not_scaled_again(tmp_path):
    path = legacy(tmp_path)
    path = path.with_name("10001_release_v09_um.swc")
    path.write_text("# Source: Janelia male-cns v0.9 public skeleton release\n"
                    "# x/y/z/r converted from8nm units to micrometers, scale0.008\n"
                    "1 2 320 160 128 .256 -1\n2 2 328 440 728 .512 1\n")
    morphology = load_swc(record(path))
    assert morphology.nodes[0].x == 320
    assert morphology.coordinate_transform["scale_to_um"] == 1


def test_managed_raw_has_manifest_bound_native_units(tmp_path):
    path = tmp_path / "bundle/source/export_swc/UNKNOWN/DNp01/10001/10001_neuprint_raw.swc"
    path.parent.mkdir(parents=True)
    path.write_text("1 2 40000 20000 16000 32 -1\n2 2 41000 55000 91000 64 1\n")
    root = tmp_path / "bundle"
    manifest = {"provider": "neuprint", "dataset": "male-cns:v0.9",
                "server": "https://neuprint.janelia.org",
                "files": [{"path": str(path.relative_to(root)),
                           "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}]}
    (root / "digifly-resource.json").write_text(json.dumps(manifest))
    assert load_swc(record(path)).bounds == (320, 328, 160, 440, 128, 728)
    path.write_text(path.read_text() + "# changed\n")
    with pytest.raises(ValueError, match="checksum mismatch"):
        load_swc(record(path))


def test_source_label_or_extent_cannot_trigger_scaling(tmp_path):
    path = tmp_path / "10001_axodendro_with_synapses.swc"
    path.write_text("# bodyId 10001\n1 2 40 20 16 .032 -1\n2 2 41 55 91 .064 1\n")
    assert load_swc(record(path)).nodes[0].x == 40
    manc = NeuronRecord("10001", "DN", "DNp01", str(path), "manc:v1.2.1")
    assert load_swc(manc).nodes[0].x == 40


def test_unknown_canonical_male_cns_units_fail_closed(tmp_path):
    path = legacy(tmp_path).with_name("10001_unknown.swc")
    path.write_text("1 2 1 2 3 1 -1\n2 2 2 3 4 1 1\n")
    with pytest.raises(ValueError, match="Unknown Male CNS"):
        load_swc(record(path))


@pytest.mark.parametrize("mutation", ({"scale_to_um": 1000}, {"rule": "guess"},
                                     {"source_sha256": "0" * 64}, {"output_units": "nm"}))
def test_worker_rejects_unsupported_or_stale_transform(tmp_path, mutation):
    path = legacy(tmp_path)
    transform = dict(load_swc(record(path)).coordinate_transform)
    transform.update(mutation)
    with pytest.raises(ValueError, match="coordinate transform"):
        worker._normalize_swc(path, tmp_path, coordinate_transform=transform)
