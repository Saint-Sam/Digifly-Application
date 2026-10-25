"""Shared Mac/Windows intake contract; fixtures are not real download acceptance."""
from dataclasses import replace
import json
from pathlib import Path

import pytest

from digifly_app.core.circuit import ConnectomeRef
from digifly_app.core.connectomes import ConnectomeCatalog
from digifly_app.core.data_library import import_local_source, list_managed_resources
from digifly_app.core.neuprint import (NeuPrintAcquisitionRequest, NeuPrintNeuron, NeuPrintSelection,
                                      _selection_cypher, acquire_neuprint_bundle)
from digifly_app.core.providers import profile_connectome_sources
from digifly_app.core.resource_profile import ResourceKind, ResourceProfile, make_standalone_profile
from digifly_app.core.swc_library import add_bundle_to_library, library_root, inspect_existing_swcs

SERVER = "https://neuprint.janelia.org"
DATASET = "manc:v1.2.1"
SWC = b"1 1 0 0 0 5 -1\n2 3 1 0 0 1 1\n"


class GrowingGraphClient:
    def __init__(self, rows):
        self.rows, self.queries, self.skeletons = rows, [], []

    def fetch_skeleton(self, body_id, **kwargs):
        self.skeletons.append(body_id)
        return SWC

    def fetch_connectivity_between(self, sources, targets, **kwargs):
        from digifly_app.core.neuprint import NeuPrintConnection
        self.queries.append((tuple(sources), tuple(targets)))
        return tuple(NeuPrintConnection(a, b, w) for (a, b), w in self.rows.items()
                     if a in sources and b in targets)


def grow_graph(profile, path, client, bodies, version, **changes):
    request = NeuPrintAcquisitionRequest(SERVER, 'male-cns:v1.0', 'selected', version,
        NeuPrintSelection('name_list', 'fixture'),
        tuple(NeuPrintNeuron(body, 'DNp01') for body in bodies), 'fixture-secret',
        include_connectivity=True, **changes)
    return acquire_neuprint_bundle(profile, path, request, client=client, add_to_library=True)


def graph_ref(path, dataset='male-cns:v1.0', server=SERVER):
    from digifly_app.core.circuit_library import library_sources
    return next(s for s in library_sources(ResourceProfile.load(path), (dataset,)) if s.server == server)


class LocatedGraphClient(GrowingGraphClient):
    def fetch_contact_sites_between(self,sources,targets,**kwargs):
        from digifly_app.core.neuprint_contacts import ChemicalContact, METHOD_SOURCE, THRESHOLD_SOURCE
        rows=[]
        for (a,b),w in self.rows.items():
            if a not in sources or b not in targets:continue
            for j in range(w):
                rows.append(ChemicalContact(a,b,a*100000+b*100+j,a*100000+b*100+j+50000,
                    .1,.2,.3,.4,.5,.6,.9,.8))
        return tuple(rows),{'schema':'digifly-neuprint-contact-method-v1',
            'coordinates_rounded':False,'confidence_threshold':.5,'roi_filter':None,
            'electrical_anatomy_inferred':False,'coordinate_units':'native_dataset_space',
            'confidence_comparison':'strict_greater_when_nonzero',
            'method_source':METHOD_SOURCE,'threshold_source':THRESHOLD_SOURCE}


def test_located_contacts_share_one_growing_file_and_frozen_revisions(state):
    import sqlite3
    from digifly_app.core.pair_graph import validated_manifest,sha
    profile,path=state
    c=LocatedGraphClient({(1,2):2,(2,1):1,(1,3):2,(3,2):1})
    first=grow_graph(profile,path,c,(1,2),'sites1',include_contact_sites=True)
    frozen=graph_ref(path); first_hash=sha(Path(frozen.pair_graph_index))
    a=validated_manifest(frozen)
    assert a['contact_rows']==3 and a['contact_covered_body_ids']==[1,2]
    assert a['contact_coordinates_in_file'] is True
    second=grow_graph(profile,path,c,(3,),'sites2',include_contact_sites=True)
    current=graph_ref(path); b=validated_manifest(current)
    assert first.root==second.root and b['contact_rows']==6
    assert b['contact_covered_body_ids']==[1,2,3]
    assert sha(Path(frozen.pair_graph_index))==first_hash
    grow_graph(profile,path,c,(1,2),'sites3',include_contact_sites=True)
    last=validated_manifest(graph_ref(path));assert last['contact_rows']==6
    with sqlite3.connect(second.root/'connectivity/connectivity.sqlite') as db:
        assert db.execute('SELECT COUNT(*) FROM pairs').fetchone()==(4,)
        assert db.execute('SELECT COUNT(*) FROM contacts').fetchone()==(6,)
        assert db.execute('SELECT pre_x,post_z FROM contacts LIMIT 1').fetchone()==(.1,.6)
    from digifly_app.core.pair_graph import selected_contact_sites
    assert len(selected_contact_sites(current, (1,2,3))[1]) == 6
    assert len(selected_contact_sites(frozen, (1,2))[1]) == 3
    with pytest.raises(ValueError, match='unknown'):
        selected_contact_sites(frozen, (1,3))


def test_wrong_contact_census_never_changes_committed_graph_or_profile(state):
    from digifly_app.core.pair_graph import sha
    profile,path=state;c=LocatedGraphClient({(1,2):2})
    result=grow_graph(profile,path,c,(1,2),'good-sites',include_contact_sites=True)
    graph=result.root/'connectivity/connectivity.sqlite'
    before=sha(graph),path.read_bytes()
    class MissingContact(LocatedGraphClient):
        def fetch_contact_sites_between(self,*a,**kw):
            rows,method=super().fetch_contact_sites_between(*a,**kw)
            return rows[:-1],method
    with pytest.raises(ValueError,match='census differs'):
        grow_graph(profile,path,MissingContact(c.rows),(1,2),'bad-sites',include_contact_sites=True)
    assert (sha(graph),path.read_bytes())==before


def test_count_only_refresh_invalidates_affected_contact_coverage(state):
    from digifly_app.core.pair_graph import validated_manifest
    profile,path=state;c=LocatedGraphClient({(1,2):2})
    grow_graph(profile,path,c,(1,2),'sites-before-counts',include_contact_sites=True)
    grow_graph(profile,path,c,(1,),'counts-only-refresh')
    m=validated_manifest(graph_ref(path))
    assert m['contact_covered_body_ids']==[2] and m['contact_rows']==0
    from digifly_app.core.pair_graph import selected_contact_sites
    with pytest.raises(ValueError,match='have not been downloaded'):
        selected_contact_sites(graph_ref(path),(1,2))


def test_downloaded_real_sites_feed_chemical_model_and_explicit_gf_ttmn_gaps(state):
    from digifly_app.core.circuit import CircuitSpec
    from digifly_app.core.connectomes import ConnectomeCatalog
    from digifly_app.core.morphology import load_swc
    from digifly_app.core.mechanisms import GapJunctionPolicy
    from digifly_app.engines.generic_experiment import GenericExperimentAdapter
    from digifly_app.workers import generic_experiment_worker as common
    from digifly_app.workers import bmtk_bionet_worker as bionet
    profile,path=state
    request=NeuPrintAcquisitionRequest(SERVER,'male-cns:v1.0','located','one',
        NeuPrintSelection('name_list','DNp01, TTMn'),
        (NeuPrintNeuron(1,'DNp01'),NeuPrintNeuron(2,'TTMn')),'fixture-secret',
        include_connectivity=True,include_contact_sites=True)
    result=acquire_neuprint_bundle(profile,path,request,
        client=LocatedGraphClient({(1,2):2}),add_to_library=True)
    source=graph_ref(path)
    cells=tuple(load_swc(r) for r in ConnectomeCatalog.scan(source).records)
    circuit=CircuitSpec(connectome=source,neuron_ids=('1','2'),
        gap_junction_policy=GapJunctionPolicy(mode='ohmic',placement_policy='gf_ttmn_chemical_sites'))
    edges=GenericExperimentAdapter({})._edge_manifest(circuit,cells)
    assert len(edges['chemical_edges'])==2
    gaps=edges['electrical_edges'][0]['contacts']; assert len(gaps)==2
    for chem,gap in zip(edges['chemical_edges'],gaps):
        assert chem['located_contact']==gap['located_contact']
        assert chem['post_coordinate_um']==[.4*.008,.5*.008,.6*.008]
        assert gap['pre_coordinate_um']==[.1*.008,.2*.008,.3*.008]
        assert gap['modeled_electrical'] is True
        assert gap['placement_assumption']['biological_electrical_contact'] is False
        assert chem['post_source_edge_fraction']==gap['post_source_edge_fraction']
    from test_generic_experiment import _experiment
    common._validate_request_capabilities('neuron',circuit.to_dict(),_experiment('neuron').to_dict(),edges)
    bionet._validate_contacts(edges,circuit.neuron_ids)
    common._validate_edge_source_identity(edges)
    changed=json.loads(json.dumps(edges['chemical_edges'][0]))
    changed['post_coordinate_um'][0]+=1
    with pytest.raises(ValueError,match='coordinates were changed'):
        common._validate_located_contact(changed)


def test_cumulative_two_batches_cross_edges_update_delete_and_frozen_circuit(state):
    from digifly_app.core.pair_graph import pair_rows, validated_manifest
    profile, path = state
    large_id = 2**53 + 7
    client = GrowingGraphClient({(1, 2): 4, (2, 1): 1, (2, large_id): 6,
                                (large_id, 1): 3, (large_id, 3): 2})
    first = grow_graph(profile, path, client, (1, 2), 'one')
    frozen = graph_ref(path)
    assert pair_rows(frozen, '1', '2')[0]['total_weight'] == 4
    original_swcs = {p: p.read_bytes() for p in first.root.rglob('*.swc')}
    second = grow_graph(profile, path, client, (large_id, 3), 'two')
    current = graph_ref(path)
    assert second.root == first.root
    canonical = second.root / 'connectivity/connectivity.sqlite'
    assert canonical.is_file()
    assert len(ResourceProfile.load(path).bindings(ResourceKind.DATA_SOURCE)) == 1
    assert pair_rows(current, '2', str(large_id))[0]['total_weight'] == 6
    assert pair_rows(current, str(large_id), '1')[0]['total_weight'] == 3
    assert validated_manifest(current)['covered_body_ids'] == [1, 2, 3, large_id]
    assert all(p.read_bytes() == raw for p, raw in original_swcs.items())
    client.rows[(1, 2)] = 9
    del client.rows[(2, 1)]
    grow_graph(profile, path, client, (1, 2), 'three')
    updated = graph_ref(path)
    assert [r['total_weight'] for r in pair_rows(updated, '1', '2')] == [9]
    assert pair_rows(updated, '2', str(large_id))[0]['total_weight'] == 6
    assert [r['total_weight'] for r in pair_rows(frozen, '1', '2')] == [4, 1]
    assert client.skeletons == [1, 2, large_id, 3]
    grow_graph(profile, path, client, (1, 2), 'four')
    assert [r['total_weight'] for r in pair_rows(graph_ref(path), '1', '2')] == [9]
    assert len(ResourceProfile.load(path).bindings(ResourceKind.DATA_SOURCE)) == 1


def test_cumulative_zero_edges_and_rollback_preserve_canonical_and_profile(state, monkeypatch):
    from digifly_app.core.pair_graph import pair_rows, validated_manifest
    profile, path = state
    first = grow_graph(profile, path, GrowingGraphClient({}), (1,), 'one')
    canonical = first.root / 'connectivity/connectivity.sqlite'
    assert validated_manifest(graph_ref(path))['rows'] == 0
    assert pair_rows(graph_ref(path), '1', '1') == ()
    before = canonical.read_bytes(), first.manifest.read_bytes(), path.read_bytes()
    def fail(*args, **kwargs):
        raise OSError('fixture profile failure')
    monkeypatch.setattr(ResourceProfile, 'save', fail)
    with pytest.raises(OSError, match='profile failure'):
        grow_graph(profile, path, GrowingGraphClient({(1, 2): 8}), (2,), 'two')
    assert (canonical.read_bytes(), first.manifest.read_bytes(), path.read_bytes()) == before
    assert not list((first.root / 'export_swc').rglob('2.swc'))


def test_cumulative_plan_rejects_changed_review_before_http_and_isolated_origin(state):
    from digifly_app.core.selected_connectivity import review_connectivity
    profile, path = state
    grow_graph(profile, path, GrowingGraphClient({}), (1,), 'one')
    plan = review_connectivity(profile, SERVER, 'male-cns:v1.0', (2,))
    assert plan['participants'] == [1, 2]
    assert review_connectivity(profile, SERVER, 'male-cns:v0.9', (2,))['participants'] == [2]
    assert review_connectivity(profile, 'https://other.example', 'male-cns:v1.0', (2,))['participants'] == [2]
    grow_graph(profile, path, GrowingGraphClient({}), (3,), 'intervening')
    client = GrowingGraphClient({})
    with pytest.raises(ValueError, match='changed after review'):
        grow_graph(profile, path, client, (2,), 'two', connectivity_review=plan)
    assert client.queries == [] and client.skeletons == []


def test_cumulative_cancel_during_fetch_never_changes_committed_files(state):
    import threading
    from digifly_app.core.neuprint import AcquisitionCancelled
    profile, path = state
    first = grow_graph(profile, path, GrowingGraphClient({(1, 1): 4}), (1,), 'one')
    canonical = first.root / 'connectivity/connectivity.sqlite'
    before = canonical.read_bytes(), first.manifest.read_bytes(), path.read_bytes()
    event = threading.Event()
    class CancelClient(GrowingGraphClient):
        def fetch_connectivity_between(self, *args, **kwargs):
            event.set()
            raise AcquisitionCancelled('fixture cancelled')
    request = NeuPrintAcquisitionRequest(SERVER, 'male-cns:v1.0', 'cancel', 'two',
        NeuPrintSelection('name_list', 'fixture'), (NeuPrintNeuron(2, 'TTMn'),), 'fixture-secret',
        include_connectivity=True)
    with pytest.raises(AcquisitionCancelled):
        acquire_neuprint_bundle(profile, path, request, client=CancelClient({}), cancel=event, add_to_library=True)
    assert (canonical.read_bytes(), first.manifest.read_bytes(), path.read_bytes()) == before
    assert not list((first.root / 'export_swc').rglob('2.swc'))


def test_cumulative_chunk_plan_covers_every_directed_pair_once_and_budgets():
    import threading
    from digifly_app.core.selected_connectivity import fetch_updates
    from digifly_app.core.neuprint import NeuPrintError, NeuPrintConnection
    focus, participants = [1, 502], list(range(1, 504))
    client = GrowingGraphClient({(1, 501): 8, (503, 502): 2, (502, 1): 3})
    plan = {'participants': participants, 'focus': focus}
    rows = fetch_updates(client, plan, cancel=threading.Event(), max_rows=10, max_bytes=1000, on_bytes=lambda n: None)
    assert len(rows) == 3
    pairs = [(a, b) for sources, targets in client.queries for a in sources for b in targets]
    expected = {(a, b) for a in participants for b in participants if a in focus or b in focus}
    assert len(pairs) == len(set(pairs)) and set(pairs) == expected
    with pytest.raises(NeuPrintError, match='total row limit'):
        fetch_updates(client, plan, cancel=threading.Event(), max_rows=1, max_bytes=1000, on_bytes=lambda n: None)
    class Bad:
        def fetch_connectivity_between(self, *args, **kwargs):
            return (NeuPrintConnection(999, 999, 1),)
    with pytest.raises(NeuPrintError, match='Unexpected'):
        fetch_updates(Bad(), plan, cancel=threading.Event(), max_rows=10, max_bytes=1000, on_bytes=lambda n: None)
    class TooManyBytes:
        def fetch_connectivity_between(self, *args, **kwargs):
            kwargs['on_bytes'](1001)
            return ()
    with pytest.raises(NeuPrintError, match='total byte limit'):
        fetch_updates(TooManyBytes(), plan, cancel=threading.Event(), max_rows=10, max_bytes=1000, on_bytes=lambda n: None)


def test_cumulative_unqueried_neuron_is_unknown_not_zero(state):
    from digifly_app.core.pair_graph import pair_rows
    from digifly_app.core.connectomes import ConnectomeEdgeCatalog
    profile, path = state
    grow_graph(profile, path, GrowingGraphClient({}), (1,), 'one')
    source = graph_ref(path)
    with pytest.raises(ValueError, match='unknown, not zero'):
        pair_rows(source, '1', '999')
    summary = ConnectomeEdgeCatalog(source)._chemical_summary('1', '999')
    assert not summary.available and 'unknown, not zero' in summary.unavailable_reason


def test_cumulative_limits_and_untracked_file_refuse_before_network(state):
    from digifly_app.core.selected_connectivity import review_connectivity
    profile, path = state
    root = library_root(profile, SERVER, 'male-cns:v1.0')
    canonical = root / 'connectivity/connectivity.sqlite'
    canonical.parent.mkdir(parents=True)
    canonical.write_bytes(b'untracked user data')
    client = GrowingGraphClient({})
    with pytest.raises(ValueError, match='untracked'):
        grow_graph(profile, path, client, (1,), 'one')
    assert not client.skeletons and canonical.read_bytes() == b'untracked user data'
    with pytest.raises(ValueError, match='5,000'):
        review_connectivity(profile, SERVER, 'male-cns:v0.9', range(1, 5002))


def test_cumulative_retains_independent_full_table_and_declares_current_selection(state):
    from digifly_app.core.resource_profile import ResourceBinding, AccessMode
    profile, path = state
    full = path.parent / 'full.parquet'
    full.write_bytes(b'fixture external full table unchanged')
    binding = ResourceBinding('full', ResourceKind.DATA_SOURCE, str(full), AccessMode.READ_ONLY,
        metadata={'format': 'digifly-weighted-pair-graph-v1', 'dataset': 'male-cns:v1.0',
                  'server': SERVER, 'index': str(path.parent / 'external-index/pairs.sqlite'), 'source_sha256': 'fixture'})
    replace(profile, resources=(*profile.resources, binding)).save(path, replace=True)
    grow_graph(profile, path, GrowingGraphClient({(1, 2): 1}), (1, 2), 'one')
    assert full.read_bytes() == b'fixture external full table unchanged'
    saved = ResourceProfile.load(path)
    assert len(saved.bindings(ResourceKind.DATA_SOURCE)) == 2
    assert next(b for b in saved.resources if b.resource_id == 'full').metadata['active_for_dataset'] is False
    assert graph_ref(path).pair_graph_path.endswith('connectivity.sqlite')


def test_cumulative_cancelled_revision_leaves_no_partial_database(state, monkeypatch):
    import threading
    from digifly_app.core import swc_library
    from digifly_app.core.neuprint import AcquisitionCancelled
    profile, path = state
    first = grow_graph(profile, path, GrowingGraphClient({}), (1,), 'one')
    canonical = first.root / 'connectivity/connectivity.sqlite'
    before = canonical.read_bytes(), first.manifest.read_bytes(), path.read_bytes()
    event = threading.Event()
    original = swc_library._write_json_atomic
    def stop(path, payload):
        original(path, payload)
        if path.name == 'manifest.json' and payload.get('storage_schema'):
            event.set()
    monkeypatch.setattr(swc_library, '_write_json_atomic', stop)
    request = NeuPrintAcquisitionRequest(SERVER, 'male-cns:v1.0', 'cancel', 'two',
        NeuPrintSelection('name_list', 'fixture'), (NeuPrintNeuron(2, 'TTMn'),), 'fixture-secret', include_connectivity=True)
    with pytest.raises(AcquisitionCancelled):
        acquire_neuprint_bundle(profile, path, request, client=GrowingGraphClient({(1, 2): 3}), cancel=event, add_to_library=True)
    assert (canonical.read_bytes(), first.manifest.read_bytes(), path.read_bytes()) == before
    failed, = first.root.glob('.history/*/failed-download.json')
    assert not (failed.parent / 'connectivity/connectivity.sqlite').exists()
    assert not (failed.parent / 'incoming-connectivity.sqlite').exists()


def test_cumulative_missing_coverage_after_swc_only_download_is_filled_next_time(state):
    from digifly_app.core.pair_graph import pair_rows, validated_manifest
    profile, path = state
    first = grow_graph(profile, path, GrowingGraphClient({}), (1,), 'one')
    no_graph = NeuPrintAcquisitionRequest(SERVER, 'male-cns:v1.0', 'swc-only', 'two',
        NeuPrintSelection('name_list', 'fixture'), (NeuPrintNeuron(2, 'TTMn'),), 'fixture-secret')
    acquire_neuprint_bundle(profile, path, no_graph, client=GrowingGraphClient({}), add_to_library=True)
    assert json.loads(first.manifest.read_text())['connectivity']['covered_body_ids'] == [1]
    with pytest.raises(ValueError, match='unknown'):
        pair_rows(graph_ref(path), '1', '2')
    grow_graph(profile, path, GrowingGraphClient({(1, 2): 7, (2, 3): 4}), (3,), 'three')
    assert validated_manifest(graph_ref(path))['covered_body_ids'] == [1, 2, 3]
    assert pair_rows(graph_ref(path), '1', '2')[0]['total_weight'] == 7


def test_cumulative_snapshot_cannot_be_relabelled_as_another_dataset(state):
    from digifly_app.core.pair_graph import validated_manifest
    profile, path = state
    grow_graph(profile, path, GrowingGraphClient({}), (1,), 'one')
    source = graph_ref(path)
    with pytest.raises(ValueError, match='another server or connectome'):
        validated_manifest(replace(source, dataset='male-cns:v0.9'))
    with pytest.raises(ValueError, match='another server or connectome'):
        validated_manifest(replace(source, server='https://other.example'))


@pytest.fixture
def state(tmp_path):
    profile = make_standalone_profile(workstation_root=tmp_path / "Workspace with spaces")
    return profile, profile.save(tmp_path / "profile.json")


def local(tmp_path, profile, name, body="42", kind="DNp01", data=SWC, dataset=DATASET):
    source = tmp_path / name / "DN" / kind / body
    source.mkdir(parents=True)
    (source / f"{body}.swc").write_bytes(data)
    return import_local_source(profile, tmp_path / name, provider="local", resource_id=name,
                               source_version="v1", dataset=dataset)


def test_separate_searches_and_local_import_share_one_source(state, tmp_path):
    profile, path = state

    class Client:
        def fetch_skeleton(self, body_id, **kwargs):
            return SWC

    first = NeuPrintAcquisitionRequest(SERVER, DATASET, "dnp01", "v1",
        NeuPrintSelection("type_exact", "DNp01"), (NeuPrintNeuron(42, "DNp01"),), "fixture-secret")
    one = acquire_neuprint_bundle(profile, path, first, client=Client(), add_to_library=True)
    second = replace(first, resource_id="ttmn", neurons=(NeuPrintNeuron(43, "TTMn"),),
                     selection=NeuPrintSelection("type_exact", "TTMn"))
    two = acquire_neuprint_bundle(profile, path, second, client=Client(), add_to_library=True)
    assert one.root == two.root
    assert two.swc_count == 2
    imported = local(tmp_path, profile, "local-third", "44", "DNp02")
    three = add_bundle_to_library(profile, path, imported, server=SERVER, dataset=DATASET)
    assert three.swc_count == 3 and three.root == one.root
    restored = ResourceProfile.load(path)
    assert len(restored.bindings(ResourceKind.MORPHOLOGY_SOURCE)) == 1
    source, = profile_connectome_sources(restored)
    catalog = ConnectomeCatalog.scan(source)
    assert not catalog.query("DNp01, TTMn").unmatched
    assert set(catalog.by_id) == {"42", "43", "44"}
    assert (three.root / "export_swc/DN/DNp01/42/42.swc").read_bytes() == SWC
    assert all((three.root / "export_swc" / family).is_dir() for family in ("AN", "DN", "IN", "MN", "SN", "UNKNOWN"))
    manifest = json.loads(three.manifest.read_text())
    assert len(manifest["acquisitions"]) == 3
    assert all(Path(item["manifest"]).is_file() for item in manifest["acquisitions"])
    assert "fixture-secret" not in three.manifest.read_text() + path.read_text()
    assert sum(item.provider == "neuprint" for item in list_managed_resources(restored)) == 0
    assert not (profile.managed_data_root / "neuprint").exists()


@pytest.mark.parametrize('dataset,bodies', [
    ('male-cns:v0.9', (800146, 804642)),
    ('male-cns:v1.0', (800146, 804642)),
    ('manc:v1.2.1', (10068, 10110)),
])
def test_ttmn_new_downloads_use_mn_family_in_each_exact_release(state, dataset, bodies):
    profile, path = state
    class Client:
        def fetch_skeleton(self, body_id, **kwargs):
            return SWC
    request = NeuPrintAcquisitionRequest(SERVER, dataset, 'ttmn', 'labels-test',
        NeuPrintSelection('type_exact', 'TTMn'), tuple(NeuPrintNeuron(b, 'TTMn') for b in bodies),
        'fixture-secret')
    saved = acquire_neuprint_bundle(profile, path, request, client=Client(), add_to_library=True)
    for body in bodies:
        assert (saved.root / f'export_swc/MN/TTMn/{body}/{body}.swc').read_bytes() == SWC
    assert not (saved.root / 'export_swc/UNKNOWN/TTMn').exists()


def test_identical_intake_is_idempotent_conflicting_bytes_do_not_replace(state, tmp_path):
    profile, path = state
    original = local(tmp_path, profile, "original")
    resource = add_bundle_to_library(profile, path, original, server=SERVER, dataset=DATASET)
    repeated = add_bundle_to_library(profile, path, original, server=SERVER, dataset=DATASET)
    assert repeated.swc_count == 1
    assert len(json.loads(repeated.manifest.read_text())["acquisitions"]) == 1
    saved_manifest, saved_profile = resource.manifest.read_bytes(), path.read_bytes()
    conflict = local(tmp_path, profile, "conflict", data=SWC.replace(b"0 5", b"0 6"))
    with pytest.raises(ValueError, match="different SWC bytes"):
        add_bundle_to_library(profile, path, conflict, server=SERVER, dataset=DATASET)
    assert resource.manifest.read_bytes() == saved_manifest and path.read_bytes() == saved_profile
    assert conflict.manifest.is_file()


def _download(profile, path, *, body=42, kind="DNp01", version="one", data=SWC,
              review=None, replacements=(), fetched=None):
    class Client:
        def fetch_skeleton(self, body_id, **kwargs):
            if fetched is not None:
                fetched.append(body_id)
            return data
    request = NeuPrintAcquisitionRequest(SERVER, DATASET, "list-or-single", version,
        NeuPrintSelection("name_list", kind), (NeuPrintNeuron(body, kind),), "fixture-token",
        library_review=review, replacement_ids=replacements)
    return acquire_neuprint_bundle(profile, path, request, client=Client(), add_to_library=True)


def test_download_duplicate_keep_skips_http_and_replacement_keeps_backup(state):
    profile, path = state
    fetched = []
    first = _download(profile, path, fetched=fetched)
    existing = inspect_existing_swcs(profile, SERVER, DATASET, (42,))
    assert existing["42"]["path"] == "export_swc/DN/DNp01/42/42.swc"
    changed = SWC.replace(b"0 5", b"0 6")
    kept = _download(profile, path, version="two", data=changed, review=existing, fetched=fetched)
    assert fetched == [42]
    target = kept.root / existing["42"]["path"]
    assert target.read_bytes() == SWC
    replaced = _download(profile, path, version="three", data=changed,
                         review=existing, replacements=(42,), fetched=fetched)
    assert fetched == [42, 42] and target.read_bytes() == changed
    receipt = json.loads(Path(json.loads(replaced.manifest.read_text())["acquisitions"][-1]["manifest"]).read_text())
    assert receipt["actions"][0]["action"] == "replaced"
    assert (replaced.root / receipt["actions"][0]["backup"]).read_bytes() == SWC
    assert len(list((replaced.root / "export_swc").rglob("*.swc"))) == 1
    assert not (profile.managed_data_root / "neuprint").exists()
    assert not list((profile.managed_data_root / ".staging").rglob("*.swc"))


def test_existing_detection_checks_unindexed_folder_bytes_and_keeps_them(state):
    profile, path = state
    root = library_root(profile, SERVER, DATASET)
    target = root / "export_swc/MN/TTMn/43/43.swc"
    target.parent.mkdir(parents=True)
    target.write_bytes(SWC)
    review = inspect_existing_swcs(profile, SERVER, DATASET, (43,))
    fetched = []
    result = _download(profile, path, body=43, kind="TTMn", review=review, fetched=fetched)
    assert fetched == [] and target.read_bytes() == SWC
    assert json.loads(result.manifest.read_text())["files"][0]["path"] == "export_swc/MN/TTMn/43/43.swc"


def test_replacement_keeps_existing_classified_folder(state):
    profile, path = state
    root = library_root(profile, SERVER, DATASET)
    target = root / 'export_swc/MN/TTMn/43/43.swc'
    target.parent.mkdir(parents=True)
    target.write_bytes(SWC)
    review = inspect_existing_swcs(profile, SERVER, DATASET, (43,))
    changed = SWC.replace(b'0 5', b'0 6')
    result = _download(profile, path, body=43, kind='TTMn', review=review,
                       replacements=(43,), data=changed)
    assert target.read_bytes() == changed
    assert json.loads(result.manifest.read_text())['files'][0]['path'] == review['43']['path']


def test_stale_replacement_approval_refuses_before_fetch_or_overwrite(state):
    profile, path = state
    first = _download(profile, path)
    review = inspect_existing_swcs(profile, SERVER, DATASET, (42,))
    target = first.root / review["42"]["path"]
    changed = SWC.replace(b"0 5", b"0 7")
    target.write_bytes(changed)
    fetched = []
    with pytest.raises(ValueError, match="changed after review"):
        _download(profile, path, version="two", review=review, replacements=(42,), fetched=fetched)
    assert fetched == [] and target.read_bytes() == changed


def test_approved_replacement_rollback_restores_files_index_and_profile(state, monkeypatch):
    profile, path = state
    first = _download(profile, path)
    review = inspect_existing_swcs(profile, SERVER, DATASET, (42,))
    before_index, before_profile = first.manifest.read_bytes(), path.read_bytes()
    def fail(*args, **kwargs):
        raise OSError("fixture profile save failed")
    monkeypatch.setattr(ResourceProfile, "save", fail)
    with pytest.raises(OSError, match="profile save failed"):
        _download(profile, path, version="two", data=SWC.replace(b"0 5", b"0 6"),
                  review=review, replacements=(42,))
    assert (first.root / review["42"]["path"]).read_bytes() == SWC
    assert first.manifest.read_bytes() == before_index and path.read_bytes() == before_profile
    assert not (first.root / ".intake-lock").exists()
    failed, = first.root.glob(".history/*/failed-download.json")
    assert json.loads(failed.read_text())["status"] == "not_committed"


def test_file_changed_during_fetch_is_not_replaced(state):
    profile, path = state
    first = _download(profile, path)
    review = inspect_existing_swcs(profile, SERVER, DATASET, (42,))
    target = first.root / review['42']['path']
    changed = SWC.replace(b'0 5', b'0 9')
    class Client:
        def fetch_skeleton(self, *args, **kwargs):
            target.write_bytes(changed)
            return SWC
    request = NeuPrintAcquisitionRequest(SERVER, DATASET, 'race', 'one',
        NeuPrintSelection('name_list', 'DNp01'), (NeuPrintNeuron(42, 'DNp01'),), 'fixture',
        library_review=review, replacement_ids=(42,))
    with pytest.raises(ValueError, match='changed after review'):
        acquire_neuprint_bundle(profile, path, request, client=Client(), add_to_library=True)
    assert target.read_bytes() == changed


def test_recovery_preserves_external_changes_and_original_backup(state, monkeypatch):
    profile, path = state
    first = _download(profile, path)
    review = inspect_existing_swcs(profile, SERVER, DATASET, (42,))
    target = first.root / review['42']['path']
    external = SWC.replace(b'0 5', b'0 9')
    def fail(*args, **kwargs):
        target.write_bytes(external)
        raise OSError('fixture changed concurrently')
    monkeypatch.setattr(ResourceProfile, 'save', fail)
    with pytest.raises(ValueError, match='during recovery'):
        _download(profile, path, version='two', data=SWC.replace(b'0 5', b'0 6'),
                  review=review, replacements=(42,))
    assert target.read_bytes() == external
    backups = list(first.root.glob('.history/*/previous/**/*.swc'))
    assert len(backups) == 1 and backups[0].read_bytes() == SWC


def test_cancelled_library_download_resumes_after_reopening_without_new_names(state):
    import threading
    from digifly_app.core.neuprint import AcquisitionCancelled
    profile, path = state
    cancel = threading.Event()
    fetched = []
    class Client:
        def fetch_skeleton(self, body_id, **kwargs):
            fetched.append(body_id)
            if body_id == 42:
                cancel.set()
            return SWC
    request = NeuPrintAcquisitionRequest(SERVER, DATASET, 'original', 'one',
        NeuPrintSelection('name_list', 'DNp01, TTMn'),
        (NeuPrintNeuron(42, 'DNp01'), NeuPrintNeuron(43, 'TTMn')), 'fixture', library_review={})
    with pytest.raises(AcquisitionCancelled):
        acquire_neuprint_bundle(profile, path, request, client=Client(), cancel=cancel, add_to_library=True)
    cancel.clear()
    result = acquire_neuprint_bundle(profile, path, replace(request, resource_id='reopened', source_version='two'),
                                    client=Client(), add_to_library=True)
    assert fetched == [42, 43] and result.swc_count == 2
    assert not list((profile.managed_data_root / '.staging').rglob('*.swc'))


def test_duplicate_files_in_tree_do_not_choose_an_arbitrary_replacement(state):
    profile, path = state
    first = _download(profile, path)
    extra = first.root / "export_swc/UNKNOWN/Other/42/42.swc"
    extra.parent.mkdir(parents=True)
    extra.write_bytes(SWC)
    with pytest.raises(ValueError, match="multiple files"):
        inspect_existing_swcs(profile, SERVER, DATASET, (42,))


@pytest.mark.parametrize('relative', ['/export_swc/42.swc', '../export_swc/42.swc',
    'export_swc/../../42.swc', 'C:/export_swc/42.swc', 'export_swc\\DN\\42.swc'])
def test_reviewed_paths_keep_portable_grammar_and_reject_escape(state, relative):
    request = NeuPrintAcquisitionRequest(SERVER, DATASET, 'review', 'one',
        NeuPrintSelection('name_list', 'DNp01'), (NeuPrintNeuron(42, 'DNp01'),), 'fixture')
    with pytest.raises(ValueError, match='Invalid reviewed'):
        replace(request, library_review={'42': {'path': relative, 'sha256': 'a' * 64}})
    valid = replace(request, library_review={'42': {'path': 'export_swc/DN/DNp01/42/42.swc', 'sha256': 'a' * 64}})
    assert valid.library_review['42']['path'].startswith('export_swc/')


def test_releases_origins_and_coordinate_units_are_not_merged(state, tmp_path):
    profile, path = state
    original = local(tmp_path, profile, "first")
    resource = add_bundle_to_library(profile, path, original, server=SERVER, dataset=DATASET)
    with pytest.raises(ValueError, match="coordinate convention"):
        add_bundle_to_library(profile, path, original, server=SERVER, dataset=DATASET,
                              coordinate_convention={"units": "other"})
    assert library_root(profile, SERVER, "manc:v1.0") != resource.root
    assert library_root(profile, "https://example.org", DATASET) != resource.root
    assert library_root(profile, SERVER, "a:b") != library_root(profile, SERVER, "a-b")


def test_symlink_destination_and_interrupted_lock_fail_closed(state, tmp_path):
    profile, path = state
    original = local(tmp_path, profile, "first")
    root = library_root(profile, SERVER, DATASET)
    root.parent.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    root.symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic"):
        add_bundle_to_library(profile, path, original, server=SERVER, dataset=DATASET)
    assert not list(elsewhere.iterdir())
    root.unlink()
    root.mkdir()
    (root / ".intake-lock").mkdir()
    with pytest.raises(ValueError, match="busy"):
        add_bundle_to_library(profile, path, original, server=SERVER, dataset=DATASET)


def test_profile_save_failure_rolls_back_additions(state, tmp_path, monkeypatch):
    profile, path = state
    original = local(tmp_path, profile, "first")
    resource = add_bundle_to_library(profile, path, original, server=SERVER, dataset=DATASET)
    extra = local(tmp_path, profile, "extra", body="45")
    previous = resource.manifest.read_bytes()
    def fail(*args, **kwargs):
        raise OSError("fixture save failure")
    monkeypatch.setattr(ResourceProfile, "save", fail)
    with pytest.raises(OSError, match="save failure"):
        add_bundle_to_library(profile, path, extra, server=SERVER, dataset=DATASET)
    assert previous == resource.manifest.read_bytes()
    assert not list((resource.root / "export_swc").rglob("45.swc"))
    assert not (resource.root / ".intake-lock").exists()


def test_uncommitted_crash_file_does_not_enter_visible_catalog(state, tmp_path):
    profile, path = state
    resource = add_bundle_to_library(profile, path, local(tmp_path, profile, "first"), server=SERVER, dataset=DATASET)
    orphan = resource.root / "export_swc/DN/DNp99/99/99.swc"
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(SWC)
    source = ConnectomeRef("test", "Test", str(resource.root / "export_swc"), DATASET)
    assert set(ConnectomeCatalog.scan(source).by_id) == {"42"}


def test_explicit_saved_bundle_consolidation_retires_only_its_old_source(state, tmp_path):
    from digifly_app.core.data_library import register_managed_morphology
    profile, path = state
    first = local(tmp_path, profile, "old-search")
    old_id = register_managed_morphology(profile, first, profile_path=path, dataset=DATASET)
    another = local(tmp_path, profile, "other-dataset", "90", dataset="male-cns:v0.9")
    current = ResourceProfile.load(path)
    other_id = register_managed_morphology(current, another, profile_path=path, dataset="male-cns:v0.9")
    library = add_bundle_to_library(profile, path, first, server=SERVER, dataset=DATASET,
                                    retire_acquisition_binding=True)
    ids = {binding.resource_id for binding in ResourceProfile.load(path).resources}
    assert old_id not in ids and other_id in ids and library.registered_binding in ids
    assert first.manifest.is_file() and another.manifest.is_file()


def test_older_neuprint_manifest_bodyid_and_bytes_fields_remain_supported(state, tmp_path):
    profile, path = state
    original = local(tmp_path, profile, "legacy")
    payload = json.loads(original.manifest.read_text())
    payload["neurons"] = [{"bodyId": 42, "type": "DNp01", "instance": "legacy"}]
    for row in payload["files"]:
        row["bytes"] = row.pop("size_bytes")
    original.manifest.write_text(json.dumps(payload))
    prior = original.manifest.read_bytes()
    result = add_bundle_to_library(profile, path, original, server=SERVER, dataset=DATASET)
    assert result.swc_count == 1 and result.integrity == "verified"
    assert (result.root / "export_swc/DN/DNp01/42/42.swc").read_bytes() == SWC
    assert original.manifest.read_bytes() == prior


@pytest.mark.parametrize("mode,field", [("type_exact", "type"), ("instance_exact", "instance")])
def test_combined_names_are_safe_union_not_one_literal(mode, field):
    query = _selection_cypher(NeuPrintSelection(mode, 'DNp01, TTMn; DNp01', 8))
    assert f'n.{field} IN ["DNp01","TTMn"]' in query
    assert "LIMIT 8" in query
    escaped = _selection_cypher(NeuPrintSelection(mode, 'DNp01, x" MATCH (z)', 8))
    assert 'x\\" MATCH (z)' in escaped
    with pytest.raises(ValueError, match="exceeds"):
        _selection_cypher(NeuPrintSelection(mode, "a,b,c", 2))
