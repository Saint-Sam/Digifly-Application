from dataclasses import replace
import json

import pytest

from digifly_app.core.circuit import CircuitSpec
from digifly_app.core.connectomes import NeuronRecord
from digifly_app.core.morphology import load_swc, locate_soma
from digifly_app.core.swc_quality import analyze_swc
from digifly_app.core.swc_fragments import plan_fragment_join, save_fragment_join
from digifly_app.engines.generic_experiment import _nearest_node_id
from digifly_app.workers import generic_experiment_worker as worker


@pytest.fixture
def fragments(tmp_path):
    source = tmp_path / "1.swc"
    source.write_text("# units: um\n1 1 0 0 0 1 -1\n2 1 2 0 0 1 1\n3 2 4 0 0 0.9 2\n20 2 20 0 0 0.8 -1\n21 2 22 0 0 0.7 20\n")
    record = NeuronRecord("1", "MN", "fixture", str(source), "custom:fixture")
    return source, record


def test_adaptive_join_preserves_every_original_point_radius_and_real_edge(fragments):
    source, record = fragments
    original = source.read_bytes()
    before = load_swc(record, allow_forest_preview=True)
    plan = plan_fragment_join(analyze_swc(source))
    assert plan.added_compartments > 1
    for bridge in plan.bridges:
        assert bridge["maximum_compartment_length_um"] <= bridge["reference_maximum_length_um"]
        assert bridge["maximum_compartment_volume_bound_um3"] <= bridge["reference_maximum_volume_um3"]
    joined = save_fragment_join(plan)
    assert source.read_bytes() == original
    after = load_swc(replace(record, swc_path=str(joined)))
    by_id = {node.node_id: node for node in after.nodes}
    for old in before.nodes:
        new = by_id[old.node_id]
        assert (old.swc_type, old.x, old.y, old.z, old.radius) == (new.swc_type, new.x, new.y, new.z, new.radius)
        assert new.parent_id == plan.parent_changes.get(old.node_id, old.parent_id)
    assert sum(node.parent_id == -1 for node in after.nodes) == 1
    assert locate_soma(before) == locate_soma(after)
    # A point on the invented bridge must not become a new anatomical contact anchor.
    assert _nearest_node_id(after, (12, 0, 0)) in {node.node_id for node in before.nodes}
    note = json.loads(joined.with_suffix(".swc.quality.json").read_text())
    assert note["source_unchanged"] is True
    assert note["plan"]["biological_reconstruction"] is False
    assert note["output_sha256"] == after.coordinate_transform["source_sha256"]


def test_normalization_and_regional_sites_keep_fillers_out_of_soma_ais(fragments, tmp_path):
    source, _record = fragments
    original_rows = worker._swc_rows(source)
    joined = save_fragment_join(plan_fragment_join(analyze_swc(source)))
    joined_rows = worker._swc_rows(joined)
    for rostral in (True, False):
        assert worker._target_soma_node(original_rows, prefer_rostral=rostral) == worker._target_soma_node(joined_rows, prefer_rostral=rostral)
    data = worker._regional_cell_data(CircuitSpec(neuron_ids=("1",)).to_dict(), "1", {row[0]:row[0] for row in joined_rows})
    before = worker._regional_plan(original_rows, data)
    after = worker._regional_plan(joined_rows, data)
    for region in ("soma", "ais"):
        assert [s["node_id"] for s in worker._regional_sites(before, region, data)] == [s["node_id"] for s in worker._regional_sites(after, region, data)]
        assert all(not s["is_filler"] for s in worker._regional_sites(after, region, data))
    output = tmp_path / "normalized"
    output.mkdir()
    normalized, _mapping, _digest = worker._normalize_swc(joined, output)
    normalized_rows = worker._swc_rows(normalized)
    assert len(normalized_rows.filler_metadata["synthetic_node_ids"]) == len(joined_rows.filler_metadata["synthetic_node_ids"])
    assert normalized_rows.filler_metadata["normalized"] is True


def test_changed_source_and_existing_copy_are_never_overwritten(fragments):
    source, _record = fragments
    plan = plan_fragment_join(analyze_swc(source))
    joined = save_fragment_join(plan)
    kept = joined.read_bytes()
    with pytest.raises(FileExistsError):
        save_fragment_join(plan)
    assert joined.read_bytes() == kept
    source.write_text(source.read_text() + "# changed\n")
    with pytest.raises(ValueError, match="changed"):
        save_fragment_join(plan)


def test_invalid_or_unbounded_filler_is_refused_without_writing(fragments, monkeypatch):
    import digifly_app.core.swc_fragments as module
    source, _record = fragments
    monkeypatch.setattr(module, "MAX_FILLER_COMPARTMENTS", 1)
    with pytest.raises(ValueError, match="budget"):
        plan_fragment_join(analyze_swc(source))
    assert not source.with_name("1_fragment_healed.swc").exists()


def test_tampered_plan_and_existing_sidecar_refuse_without_overwriting(fragments):
    source, _record = fragments
    plan = plan_fragment_join(analyze_swc(source))
    with pytest.raises(ValueError, match="differs"):
        save_fragment_join(replace(plan, parent_changes={20: 1}))
    sidecar = source.with_name("1_fragment_healed.swc.quality.json")
    sidecar.write_text("unrelated earlier record")
    with pytest.raises(FileExistsError):
        save_fragment_join(plan)
    assert sidecar.read_text() == "unrelated earlier record"
    assert not source.with_name("1_fragment_healed.swc").exists()


def test_concurrent_copy_creation_preserves_other_writer_and_rolls_back_owned_sidecar(fragments, monkeypatch):
    import digifly_app.core.swc_fragments as module
    source, _record = fragments
    plan = plan_fragment_join(analyze_swc(source))
    publish = module._publish_new_text
    def concurrent(path, text):
        if path.suffix == ".swc":
            path.write_text("another writer's copy")
        publish(path, text)
    monkeypatch.setattr(module, "_publish_new_text", concurrent)
    with pytest.raises(FileExistsError):
        save_fragment_join(plan)
    assert source.with_name("1_fragment_healed.swc").read_text() == "another writer's copy"
    assert not source.with_name("1_fragment_healed.swc.quality.json").exists()
    assert not tuple(source.parent.glob(".fragment-check-*"))


def test_old_radius_review_does_not_suppress_fragment_choice(fragments):
    from digifly_app.core.swc_quality import fragments_reviewed, record_review_decision
    source, _record = fragments
    report = analyze_swc(source, manifest=source.parent / "manifest.json")
    assert report.component_count == 2 and report.needs_review
    record_review_decision(report, action="keep")
    assert not fragments_reviewed(report)
    record_review_decision(report, action="keep", fragments_reviewed=True)
    assert fragments_reviewed(report)


@pytest.mark.parametrize("rows,reason", [
    ("1 1 0 0 0 1 -1\n2 3 0 0 0 1 -1\n", "Coincident"),
    ("1 1 0 0 0 1 -1\n2 3 10 0 0 1 -1\n", "adjoining"),
    ("1 1 0 0 0 1 -1\n2 3 1 0 0 0 -1\n", "positive-radius"),
    ("1 1 0 0 0 1 -1\n2 3 1 0 0 1 3\n3 3 2 0 0 1 2\n", "acyclic"),
])
def test_unsupported_geometry_refuses_without_silent_alteration(tmp_path, rows, reason):
    source = tmp_path / "invalid.swc"
    source.write_text(rows)
    with pytest.raises(ValueError, match=reason):
        plan_fragment_join(analyze_swc(source))
    assert source.read_text() == rows
    assert list(tmp_path.iterdir()) == [source]
