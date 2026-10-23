"""Exact-release virtual SWC sources; no moves, profile writes or downloads."""
from dataclasses import replace
from pathlib import Path

from .circuit import ConnectomeRef
from .linked_libraries import canonical_dataset, pair_binding_dataset
from .neuprint import DEFAULT_NEUPRINT_SERVER
from .resource_profile import ResourceKind
from .pair_graph import selected_dataset_graph, source_with_graph
from .saved_neurons import SavedSource, _server, profile_saved_sources


def source_folders(source):
    return tuple(SavedSource(Path(row['root']), row['dataset'], row['server'],
                             Path(row['manifest']) if row.get('manifest') else None)
                 for row in source.swc_sources)


def library_sources(profile, releases, legacy=()):
    """One choice per exact release/server, including not-yet-saved releases."""
    saved = (profile_saved_sources(profile) if profile else
             tuple(SavedSource(Path(s.root), canonical_dataset(s.dataset)) for s in legacy
                   if canonical_dataset(s.dataset)))
    output = []
    for dataset in releases:
        matching = [s for s in saved if canonical_dataset(s.dataset) == dataset]
        servers = {DEFAULT_NEUPRINT_SERVER, *(_server(s.server) for s in matching)}
        for server in sorted(servers):
            folders = [s for s in matching if _server(s.server) == server]
            rows = tuple({'root': str(s.root), 'dataset': dataset, 'server': server,
                          'manifest': str(s.manifest or '')} for s in folders)
            # Preserve the genuine native contact root; never borrow a different
            # release's contacts. Extra roots are morphology locations only.
            candidates = [s for s in legacy if canonical_dataset(s.dataset) == dataset
                          and any(Path(s.root).resolve() == f.root.resolve() for f in folders)]
            native = next((s for s in candidates if s.key == 'manc:v1.2.1:full-local'),
                          candidates[0] if candidates else None)
            root = native.root if native else str(folders[0].root) if folders else ''
            key = f'library:{dataset}' + (f'@{server}' if server != DEFAULT_NEUPRINT_SERVER else '')
            source = ConnectomeRef(key, dataset, root, dataset, swc_sources=rows, server=server)
            if profile:
                pairs = [b for b in profile.bindings(ResourceKind.DATA_SOURCE)
                         if b.metadata.get('format') == 'digifly-weighted-pair-graph-v1'
                         and b.metadata.get('server', DEFAULT_NEUPRINT_SERVER) == server
                         and pair_binding_dataset(b.metadata) == dataset]
                pair = selected_dataset_graph(pairs)
                if pair is not None:
                    source = source_with_graph(source, pair)
                elif len(pairs) > 1:
                    # Do not choose among independent connectivity tables.
                    source = replace(source, label=dataset + ' (choose connectivity in Data Library)')
            output.append(source)
    return tuple(output)
