"""Read-only aggregate pair lookup, kept separate from anatomical contacts."""
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


def validated_manifest(source: ConnectomeRef) -> dict:
    index = Path(source.pair_graph_index).expanduser().resolve()
    manifest = json.loads((index.parent / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != "digifly-weighted-pair-graph-v1" or manifest.get("index") != index.name:
        raise ValueError("Invalid pair-graph index manifest")
    if manifest.get('storage_schema') == 'digifly-selected-connectivity-v1':
        if (manifest.get('dataset') != source.dataset or
                manifest.get('server') != source.server.rstrip('/')):
            raise ValueError('Shared connectivity belongs to another server or connectome release')
    original = Path(source.pair_graph_path).expanduser().resolve()
    if (str(original) != manifest["source_path"] or source.pair_graph_sha256 != manifest["source_sha256"]
            or sha(original) != source.pair_graph_sha256 or sha(index) != manifest["index_sha256"]):
        raise ValueError("Selected pair graph or lookup index has changed; reselect the exact file")
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
