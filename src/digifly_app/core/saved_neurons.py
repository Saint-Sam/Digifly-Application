"""Read-only, cancellable inventory for the Data Library folder browser.

Counts are saved body IDs per declared server/release, not SWC variants or a
claim that a connectome is complete. No source files or persistent index writes.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import threading
from urllib.parse import urlsplit

from .connectomes import _infer_id
from .data_library import load_managed_resource
from .linked_libraries import canonical_dataset, path_key
from .neuprint import DEFAULT_NEUPRINT_SERVER
from .neuron_labels import neuron_family
from .swc_library import FAMILIES, LIBRARY_SCHEMA

# Export auxiliaries are not current morphology libraries. In particular,
# by_id commonly contains links/copies and circuit/run folders contain outputs.
_AUXILIARIES = {'by_id', 'swc_circuits', 'edges', 'dnp01_edges', 'hemi_runs',
                '__pycache__', 'trash', 'backups'}


@dataclass(frozen=True)
class SavedSource:
    root: Path
    dataset: str
    server: str = DEFAULT_NEUPRINT_SERVER
    manifest: Path | None = None


@dataclass(frozen=True)
class SavedNeuron:
    body_id: str
    family: str
    neuron_type: str
    server: str
    paths: tuple[Path, ...]
    label_conflict: bool = False
    source_labels: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class SavedInventory:
    dataset: str
    neurons: tuple[SavedNeuron, ...]
    issues: tuple[str, ...] = ()
    unidentified_files: int = 0
    skipped_links: int = 0

    def family(self, family):
        return tuple(n for n in self.neurons if n.family == family)


def _server(value):
    parsed = urlsplit(str(value or DEFAULT_NEUPRINT_SERVER).rstrip('/'))
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError('Invalid saved library server')
    return f'{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path.rstrip("/")}'


def saved_sources(linked, resources, server_by_root=None):
    """Use exact filtered sources; old acquisitions are read in place.

    Local folders linked to an official catalogue release use that catalogue's
    server unless their resource manifest explicitly declares a different one.
    """
    sources = []
    for item in linked:
        sources.append(SavedSource(item.root, item.dataset,
            (server_by_root or {}).get(path_key(item.root)) or DEFAULT_NEUPRINT_SERVER))
    for item in resources:
        value = json.loads(item.manifest.read_text(encoding='utf-8'))
        if value.get('library_schema') == LIBRARY_SCHEMA or (item.root / 'export_swc').is_dir():
            root = item.root / 'export_swc'
        elif (item.root / 'source/export_swc').is_dir():
            root = item.root / 'source/export_swc'
        else:
            root = item.root / 'source'
        sources.append(SavedSource(root, value.get('dataset') or item.source_version,
                                   value.get('server') or DEFAULT_NEUPRINT_SERVER, item.manifest))
    return tuple(sources)


def profile_saved_sources(profile):
    """The same declared folders/downloads for both library and circuit views."""
    from .linked_libraries import list_linked_libraries
    from .data_library import list_managed_resources
    from .resource_profile import ResourceKind
    servers = {path_key(b.resolved_path): b.metadata['server'] for b in profile.resources
               if b.metadata.get('server') and b.kind in
               {ResourceKind.MORPHOLOGY_SOURCE, ResourceKind.CONNECTOME_SOURCE}}
    return saved_sources(list_linked_libraries(profile), list_managed_resources(profile), servers)


def _labels(path, root, body):
    parts = path.relative_to(root).parts
    index = next((i for i, part in enumerate(parts[:-1]) if part == body), -1)
    neuron_type = (parts[index - 1] if index > 0 else
                   parts[-2] if len(parts) > 1 and parts[-2].upper() not in FAMILIES else 'Unknown')
    # Explicit UNKNOWN is meaningful; never override it with a type prefix.
    explicit = next((part.upper() for part in parts[:index if index >= 0 else -1] if part.upper() in FAMILIES), '')
    family = explicit or next((f for f in FAMILIES[:-1] if neuron_type.upper().startswith(f)), 'UNKNOWN')
    return family, neuron_type


def scan_saved_neurons(sources, dataset, *, cancel: threading.Event | None = None):
    dataset = canonical_dataset(dataset)
    if not dataset:
        raise ValueError('Choose an exact connectome release')
    cancel = cancel or threading.Event()
    found, seen_files, seen_roots, issues = {}, set(), set(), []
    unidentified = skipped = 0

    def check():
        if cancel.is_set():
            raise InterruptedError('Saved-neuron scan cancelled')

    for source in sources:
        check()
        if canonical_dataset(source.dataset) != dataset:
            continue
        try:
            server = _server(source.server)
            root = source.root.expanduser().resolve()
            key = (server, path_key(root), str(source.manifest or ''))
            if key in seen_roots:
                continue
            seen_roots.add(key)
            if not root.is_dir():
                raise FileNotFoundError(f'Saved SWC folder is unavailable: {root}')
            # Only committed entries of the current dataset library count.
            committed = None
            if source.manifest:
                value = json.loads(source.manifest.read_text(encoding='utf-8'))
                if canonical_dataset(value.get('dataset') or source.dataset) != dataset:
                    raise ValueError('Saved manifest release changed during scan')
                if value.get('library_schema') == LIBRARY_SCHEMA:
                    load_managed_resource(source.manifest)
                    committed = {os.path.normcase(os.path.abspath(source.manifest.parent / row['path'])) for row in value['files']
                                 if str(row.get('path', '')).casefold().endswith('.swc')}
            pending = [root]
            observed = set()
            while pending:
                check()
                folder = pending.pop()
                try:
                    with os.scandir(folder) as entries:
                        for entry in entries:
                            check()
                            if entry.name.startswith('.') or entry.name.casefold() in _AUXILIARIES:
                                continue
                            if entry.is_symlink() or (hasattr(os.path, 'isjunction') and os.path.isjunction(entry.path)):
                                skipped += 1
                                continue
                            if entry.is_dir(follow_symlinks=False):
                                pending.append(Path(entry.path))
                            elif entry.is_file(follow_symlinks=False) and entry.name.casefold().endswith('.swc'):
                                path = Path(entry.path)
                                # root is resolved once and descendants are
                                # never followed through links. Resolving every
                                # file repeats millions of ancestor stat calls
                                # for the full libraries, with no added scope.
                                file_key = os.path.normcase(str(path))
                                if committed is not None and file_key not in committed:
                                    continue
                                observed.add(file_key)
                                if (server, file_key) in seen_files:
                                    continue
                                seen_files.add((server, file_key))
                                body = _infer_id(path.relative_to(root))
                                if body is None:
                                    unidentified += 1
                                    continue
                                family, label = _labels(path, root, body)
                                found.setdefault((server, str(int(body))), []).append((family, label, path))
                except InterruptedError:
                    raise
                except OSError as error:
                    issues.append(f'{folder}: {error}')
            if committed is not None and committed - observed:
                issues.append(f'{root}: {len(committed - observed):,} listed SWC file(s) missing or skipped')
        except InterruptedError:
            raise
        except (OSError, ValueError, KeyError, TypeError) as error:
            issues.append(str(error))
    neurons = []
    for (server, body), copies in found.items():
        # Merge annotations independently within this exact server/release/body.
        # UNKNOWN is an absent family, not a different neuron type. In particular
        # MN/TTMn and UNKNOWN/TTMn agree on the name and one supplies the family.
        # Never infer a family from that name, or hide genuine disagreements.
        labels = tuple(sorted({(family, label) for family, label, _ in copies}))
        types = {label for _, label in labels if label != 'Unknown'}
        families = {family for family, _ in labels if family != 'UNKNOWN'}
        type_conflict = len(types) > 1
        conflict = type_conflict or len(families) > 1
        label = next(iter(types)) if len(types) == 1 else ('Conflicting labels' if type_conflict else 'Unknown')
        family = next(iter(families)) if len(families) == 1 and not type_conflict else 'UNKNOWN'
        if not families and len(types) == 1:
            family = neuron_family(label, explicit_family='UNKNOWN')
        neurons.append(SavedNeuron(body, family, label, server,
                                   tuple(sorted({path for _, _, path in copies}, key=str)), conflict, labels))
    return SavedInventory(dataset, tuple(sorted(neurons, key=lambda n: (n.family, n.neuron_type.casefold(), int(n.body_id), n.server))),
                          tuple(issues), unidentified, skipped)
