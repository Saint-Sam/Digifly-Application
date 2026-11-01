"""Read-only directed pairs and actual contact sites, with separate coverage."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3

from .circuit import ConnectomeRef
from .resource_profile import ResourceKind, ResourceProfile


def selected_dataset_graph(bindings):
    """A declared active graph wins; ambiguous independent imports never do."""
    active = [b for b in bindings if b.metadata.get('active_for_dataset') is True]
    candidates = active or [b for b in bindings if b.metadata.get('active_for_dataset') is not False]
    return candidates[0] if len(candidates) == 1 else None


def with_selected_graph(profile, binding):
    from .linked_libraries import pair_binding_dataset
    dataset, server = pair_binding_dataset(binding.metadata), binding.metadata.get('server', 'https://neuprint.janelia.org')
    retained = []
    for old in profile.resources:
        if old.resource_id == binding.resource_id:
            continue
        if (old.kind == ResourceKind.DATA_SOURCE and old.metadata.get('format') == 'digifly-weighted-pair-graph-v1'
                and pair_binding_dataset(old.metadata) == dataset
                and old.metadata.get('server', 'https://neuprint.janelia.org') == server):
            old = replace(old, metadata={**old.metadata, 'active_for_dataset': False})
        retained.append(old)
    return replace(profile, resources=(*retained, replace(binding,
        metadata={**binding.metadata, 'active_for_dataset': True})))


def source_with_graph(source, binding):
    # Shared file is user-visible. Builders pin its internal immutable revision.
    return replace(source, pair_graph_path=str(binding.metadata.get('snapshot_path') or binding.path),
        pair_graph_index=str(binding.metadata.get('index', '')),
        pair_graph_sha256=str(binding.metadata.get('source_sha256', '')))


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def graph_manifest_metadata(source: ConnectomeRef) -> dict:
    """Cheap UI description, NOT byte validation or an execution-readiness check."""
    index = Path(source.pair_graph_index).expanduser().resolve()
    manifest = json.loads((index.parent / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != "digifly-weighted-pair-graph-v1" or manifest.get("index") != index.name:
        raise ValueError("Invalid pair-graph index manifest")
    if manifest.get('storage_schema') == 'digifly-selected-connectivity-v1':
        if (manifest.get('dataset') != source.dataset or
                manifest.get('server') != source.server.rstrip('/')):
            raise ValueError('Shared connectivity belongs to another server or connectome release')
    original = Path(source.pair_graph_path).expanduser().resolve()
    if str(original) != manifest['source_path'] or source.pair_graph_sha256 != manifest['source_sha256']:
        raise ValueError("Selected pair graph or lookup index has changed; reselect the exact file")
    return manifest


def validated_manifest(source: ConnectomeRef) -> dict:
    manifest=graph_manifest_metadata(source)
    original=Path(source.pair_graph_path).expanduser().resolve()
    index=Path(source.pair_graph_index).expanduser().resolve()
    if sha(original)!=source.pair_graph_sha256 or sha(index)!=manifest['index_sha256']:
        raise ValueError('Selected pair graph or lookup index has changed; reselect the exact file')
    return manifest


def pair_rows(source: ConnectomeRef, first: str, second: str) -> tuple[dict, ...]:
    manifest = validated_manifest(source)
    # Integer conversion preserves all signed 64-bit body IDs, rejects arbitrary SQL.
    a, b = int(first), int(second)
    if not all(-(2**63) <= value < 2**63 for value in (a, b)):
        raise ValueError("Pair body IDs must fit signed 64-bit integers")
    if 'covered_body_ids' in manifest and not {a, b}.issubset(manifest['covered_body_ids']):
        raise ValueError('Connectivity for these neurons has not been downloaded; it is unknown, not zero')
    path = Path(source.pair_graph_index).expanduser().resolve()
    with sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True) as db:
        db.row_factory = sqlite3.Row
        return tuple(dict(row) for row in db.execute(
            "SELECT * FROM pairs WHERE (preId=? AND postId=?) OR (preId=? AND postId=?) ORDER BY preId,postId", (a, b, b, a)))


def selected_subgraph(source: ConnectomeRef, neuron_ids) -> tuple[dict, tuple[dict, ...]]:
    """Read a bounded directed subgraph without changing counts or inferring gaps.

    Return the verified source manifest as well as rows so execution can pin
    both the original table and the lookup index without another full hash read.
    Unknown query coverage is a refusal, never an empty/no-connection result.
    """
    manifest = validated_manifest(source)
    selected = {int(value) for value in neuron_ids}
    if not selected or any(not -(2**63) <= value < 2**63 for value in selected):
        raise ValueError("Selected pair body IDs must fit signed 64-bit integers")
    if 'covered_body_ids' in manifest and not selected.issubset(manifest['covered_body_ids']):
        raise ValueError('Connectivity for these neurons has not been downloaded; it is unknown, not zero')
    path = Path(source.pair_graph_index).expanduser().resolve()
    rows = []
    with sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True) as db:
        db.row_factory = sqlite3.Row
        for pre in sorted(selected):
            for raw in db.execute("SELECT * FROM pairs WHERE preId=? ORDER BY postId", (pre,)):
                if raw["postId"] not in selected:
                    continue
                count = raw["total_weight"]
                if not isinstance(count, int) or count < 0:
                    raise ValueError("Aggregate connection counts must be nonnegative integers, not conductances")
                rows.append(dict(raw))
                if len(rows) > 250_000:
                    raise ValueError("Selected aggregate subgraph exceeds the 250,000-pair execution budget")
    return manifest, tuple(rows)


def selected_contact_sites(source: ConnectomeRef, neuron_ids) -> tuple[dict, tuple[dict, ...]]:
    """Read actual sites from the exact immutable cumulative graph, never a cache fallback."""
    from .neuprint_contacts import COLUMNS, _integer, _number, validate_method
    from .swc_units import neuprint_raw_convention
    if not source.pair_graph_index or not Path(source.pair_graph_index).is_file():
        raise ValueError('Actual chemical contact locations are unavailable: the selected graph has no checked location index')
    manifest, pairs = selected_subgraph(source, neuron_ids)
    selected = {int(value) for value in neuron_ids}
    if (manifest.get('storage_schema') != 'digifly-selected-connectivity-v1'
            or manifest.get('contact_storage_schema') != 'digifly-selected-contact-sites-v1'
            or manifest.get('contact_coordinates_in_file') is not True
            or not selected.issubset(manifest.get('contact_covered_body_ids', []))):
        raise ValueError('Actual chemical contact locations have not been downloaded for these neurons. '
                         'Refresh their connectivity in Data Library; counts alone are not locations')
    convention = neuprint_raw_convention(source.server.rstrip('/'), source.dataset)
    if convention is None or manifest.get('contact_coordinate_convention') != convention:
        raise ValueError('Chemical contact coordinate units are unavailable or differ from this exact release')
    methods = manifest.get('contact_methods') or []
    if not methods:
        raise ValueError('Chemical contact acquisition rules are missing')
    for method in methods:
        validate_method(method)
    counts = {(row['preId'], row['postId']): row['total_weight'] for row in pairs}
    actual, result, seen = {}, [], set()
    path = Path(source.pair_graph_index).expanduser().resolve()
    from contextlib import closing
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)) as db:
        if [row[1] for row in db.execute('PRAGMA table_info(contacts)')] != list(COLUMNS):
            raise ValueError('Chemical contact table schema is invalid')
        for pre in sorted(selected):
            for raw in db.execute('SELECT '+','.join(COLUMNS)+' FROM contacts WHERE source_body_id=? '
                    'ORDER BY target_body_id,pre_synapse_id,post_synapse_id', (pre,)):
                if raw[1] not in selected:
                    continue
                key = tuple(_integer(value) for value in raw[:4])
                if key in seen:
                    raise ValueError('Repeated chemical contact identity')
                seen.add(key)
                for value in raw[4:10]:
                    _number(value)
                if any(value is not None and not 0<=_number(value)<=1 for value in raw[10:]):
                    raise ValueError('Invalid saved chemical contact confidence')
                pair = key[:2]
                actual[pair] = actual.get(pair, 0)+1
                result.append(dict(zip(COLUMNS, raw)))
                if len(result)>250_000:
                    raise ValueError('Selected contact sites exceed the 250,000-site execution budget')
    if actual != {pair:count for pair,count in counts.items() if count}:
        raise ValueError('Actual chemical contact census differs from the saved pair counts')
    return manifest, tuple(result)


def attach_registered_pair_graph(source: ConnectomeRef, profile: ResourceProfile | None) -> ConnectomeRef:
    if profile is None:
        return source
    matches = [binding for binding in profile.bindings(ResourceKind.DATA_SOURCE)
               if binding.metadata.get("format") == "digifly-weighted-pair-graph-v1"
               and binding.metadata.get('active_for_dataset') is not False
               and binding.metadata.get("morphology_key") == source.key
               and Path(str(binding.metadata.get("morphology_root", ""))).expanduser().resolve() == Path(source.root).expanduser().resolve()]
    if len(matches) > 1:
        raise ValueError("Multiple pair graphs are registered for this morphology source; choose one explicitly")
    if not matches:
        return source
    binding = matches[0]
    return source_with_graph(source, binding)
