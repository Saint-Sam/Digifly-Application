"""Explicit count-to-synapse modeling; never relabel counts as located contacts."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from digifly_app.core.circuit import CircuitSpec, ConnectomeRef
from digifly_app.core.connectomes import NeuronRecord
from digifly_app.core.mechanisms import ChemicalSynapsePolicy, GapJunctionPolicy
from digifly_app.core.morphology import load_swc
from digifly_app.engines.generic_experiment import GenericExperimentAdapter
from digifly_app.workers.generic_experiment_worker import _validate_edge_source_identity


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.fixture
def aggregate(tmp_path):
    graph = tmp_path / "connectivity.sqlite"
    with sqlite3.connect(graph) as db:
        db.execute("CREATE TABLE pairs (preId INTEGER, postId INTEGER, total_weight INTEGER, preNt TEXT, PRIMARY KEY(preId,postId))")
        db.executemany("INSERT INTO pairs VALUES (?,?,?,?)", [(1, 2, 4, "gaba"), (2, 1, 7, "acetylcholine")])
    manifest = {"schema": "digifly-weighted-pair-graph-v1", "index": graph.name,
                "source_path": str(graph), "source_sha256": digest(graph),
                "index_sha256": digest(graph), "storage_schema": "digifly-selected-connectivity-v1",
                "dataset": "male-cns:v1.0", "server": "https://neuprint.janelia.org",
                "covered_body_ids": [1, 2], "rows": 2, "represented_neurons": 2}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    ref = ConnectomeRef("library:male-cns:v1.0", "Male CNS", str(tmp_path),
                       "male-cns:v1.0", str(graph), str(graph), digest(graph),
                       server="https://neuprint.janelia.org")
    cells = []
    for body in ("1", "2"):
        swc = tmp_path / f"{body}.swc"
        swc.write_text("# units: um\n1 1 0 0 0 2 -1\n2 1 0 0 2 2 1\n3 2 0 0 10 1 2\n4 2 0 0 20 1 3\n")
        cells.append(load_swc(NeuronRecord(body, "DN", "fixture", str(swc), ref.key)))
    circuit = CircuitSpec(connectome=ref, neuron_ids=("1", "2"))
    return circuit, tuple(cells)


def explicit_policy(**kwargs):
    return ChemicalSynapsePolicy(placement_policy="aggregate_post_soma",
        weight_policy="count_times_default", aggregation_policy="one_exp2syn_per_pair", **kwargs)


def test_count_model_requires_explicit_opt_in(aggregate):
    circuit, cells = aggregate
    with pytest.raises(ValueError, match="contact locations have not been downloaded"):
        GenericExperimentAdapter({})._edge_manifest(circuit, cells)


def test_policy_roundtrip_and_incompatible_combinations_rejected():
    policy = explicit_policy(default_weight_uS=0.002, weight_scale=3)
    assert ChemicalSynapsePolicy.from_dict(policy.to_dict()) == policy
    with pytest.raises(ValueError):
        ChemicalSynapsePolicy(placement_policy="aggregate_post_soma")
    with pytest.raises(ValueError):
        ChemicalSynapsePolicy(weight_policy="count_times_default")


@pytest.mark.parametrize("placement", ["aggregate_post_soma", "aggregate_post_ais"])
def test_count_model_preserves_direction_counts_units_and_declared_assumptions(aggregate, placement):
    circuit, cells = aggregate
    circuit.chemical_synapse_policy = replace(explicit_policy(default_weight_uS=0.002,
        weight_scale=3, default_delay_ms=1.2, tau1_ms=0.3, tau2_ms=2, reversal_mV=-70),
        placement_policy=placement)
    edges = GenericExperimentAdapter({})._edge_manifest(circuit, cells)
    assert [(e["pre_id"], e["post_id"]) for e in edges["chemical_edges"]] == [("1", "2"), ("2", "1")]
    for edge, count in zip(edges["chemical_edges"], (4, 7)):
        assert edge["weight_uS"] == pytest.approx(count * 0.002 * 3)
        assert edge["source_connection_count"] == count
        assert edge["delay_ms"] == 1.2
        assert (edge["tau1_ms"], edge["tau2_ms"], edge["reversal_mV"]) == (0.3, 2, -70)
        assert edge["placement_assumption"]["biological_contact"] is False
        assert edge["placement_assumption"]["policy"] == placement
        assert edge["parameter_sources"]["weight"] == "declared_aggregate_policy"
        assert edge["parameter_sources"]["reversal"] == "circuit_policy"
        assert edge["post_source_node_id"] in {n.node_id for n in cells[0].nodes}
    assert edges["electrical_edges"] == []
    _validate_edge_source_identity(edges)


def test_per_pair_basis_does_not_multiply_by_count(aggregate):
    circuit, cells = aggregate
    circuit.chemical_synapse_policy = replace(explicit_policy(default_weight_uS=0.002), weight_policy="per_pair_default")
    edges = GenericExperimentAdapter({})._edge_manifest(circuit, cells)
    assert [e["weight_uS"] for e in edges["chemical_edges"]] == [0.002, 0.002]
    assert [e["source_connection_count"] for e in edges["chemical_edges"]] == [4, 7]


def test_no_implicit_electrical_contacts_from_counts(aggregate):
    circuit, cells = aggregate
    circuit.chemical_synapse_policy = explicit_policy()
    circuit.gap_junction_policy = GapJunctionPolicy(mode="ohmic")
    with pytest.raises(ValueError, match="electrical|gap|Gap"):
        GenericExperimentAdapter({})._edge_manifest(circuit, cells)


def test_unknown_coverage_is_not_zero_and_source_changes_fail_closed(aggregate):
    circuit, cells = aggregate
    circuit.chemical_synapse_policy = explicit_policy()
    adapter = GenericExperimentAdapter({})
    first = adapter._edge_manifest(circuit, cells)
    index = Path(circuit.connectome.pair_graph_index)
    manifest_path = index.parent / "manifest.json"
    payload = json.loads(manifest_path.read_text())
    payload["covered_body_ids"] = [1]
    manifest_path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="unknown|not been downloaded"):
        adapter._edge_manifest(circuit, cells)
    with pytest.raises(ValueError, match="changed"):
        _validate_edge_source_identity(first)


def test_pair_overrides_still_remove_the_declared_directional_edges(aggregate):
    circuit, cells = aggregate
    circuit.chemical_synapse_policy = explicit_policy()
    circuit.set_connection_class_enabled("1", "2", "chemical", False)
    manifest = GenericExperimentAdapter({})._edge_manifest(circuit, cells)
    assert manifest["chemical_edges"] == []


def test_single_cell_aggregate_autapse_cannot_silently_disappear(aggregate):
    circuit, cells = aggregate
    circuit.chemical_synapse_policy = explicit_policy()
    graph = Path(circuit.connectome.pair_graph_index)
    with sqlite3.connect(graph) as db:
        db.execute("INSERT INTO pairs VALUES (1,1,3,'gaba')")
    manifest_path = graph.parent / "manifest.json"
    info = json.loads(manifest_path.read_text())
    info.update(source_sha256=digest(graph), index_sha256=digest(graph))
    manifest_path.write_text(json.dumps(info))
    circuit.connectome = replace(circuit.connectome, pair_graph_sha256=digest(graph))
    circuit.neuron_ids = ("1",)
    with pytest.raises(ValueError, match="autapse"):
        GenericExperimentAdapter({})._edge_manifest(circuit, cells[:1])


@pytest.mark.parametrize("field,value", [("weight_uS", 123.0), ("source_connection_count", -1),
    ("post_source_edge_fraction", 1.0), ("placement_assumption", {"biological_contact": True})])
def test_workers_reject_altered_count_model(aggregate, field, value):
    from digifly_app.workers.generic_experiment_worker import _validate_aggregate_chemical_contact
    from digifly_app.workers.bmtk_bionet_worker import _validate_contacts
    from copy import deepcopy
    circuit, cells = aggregate
    circuit.chemical_synapse_policy = explicit_policy()
    manifest = GenericExperimentAdapter({})._edge_manifest(circuit, cells)
    edge = deepcopy(manifest["chemical_edges"][0])
    _validate_aggregate_chemical_contact(edge, circuit.chemical_synapse_policy.to_dict())
    edge[field] = value
    with pytest.raises(ValueError):
        _validate_aggregate_chemical_contact(edge, circuit.chemical_synapse_policy.to_dict())
    manifest["chemical_edges"][0] = edge
    with pytest.raises(ValueError):
        _validate_contacts(manifest, circuit.neuron_ids)


def test_schema_accepts_explicit_model_but_refuses_implicit_mixed_policy():
    import jsonschema
    schema = json.loads((Path(__file__).parents[1] / "schemas/digifly-circuit-v3.schema.json").read_text())
    circuit = CircuitSpec(chemical_synapse_policy=explicit_policy())
    jsonschema.validate(circuit.to_dict(), schema)
    malformed = circuit.to_dict()
    malformed["chemical_synapse_policy"]["weight_policy"] = "source_or_default_per_contact"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(malformed, schema)
