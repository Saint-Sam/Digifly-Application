"""Read-only discovery and explicit inventory of user-owned SWC libraries.

These entries are not managed bundles: discovery/counting never writes a
manifest, profile or source file, and grants no move/trash/delete capability.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import threading
import hashlib
import json
import stat
import re

from .connectomes import discover_connectomes
from .providers import profile_connectome_sources
from .resource_profile import AccessMode, ResourceBinding, ResourceKind, ResourceProfile

LINK_SCHEMA = 'digifly-library-link-v1'
LINK_SUFFIX = '.digifly-library.json'


def canonical_dataset(value: str) -> str:
    """Normalize only explicit release IDs, never infer identity from a label/path."""
    value = str(value).strip().casefold()
    if value == 'mushroombody':
        return value
    match = re.fullmatch(r'(male-cns|manc|hemibrain|optic-lobe)[:_]v(\d+(?:\.\d+){1,2})', value)
    return f'{match[1]}:v{match[2]}' if match else ''


def pair_binding_dataset(metadata: dict) -> str:
    from .bundled_pair_graph import PAIR_DATASET, PAIR_SHA256
    if metadata.get('source_sha256') == PAIR_SHA256:
        return PAIR_DATASET
    explicit = canonical_dataset(metadata.get('dataset', ''))
    if explicit:
        return explicit
    return canonical_dataset(metadata.get('morphology_dataset', ''))


def save_library_link(library: LinkedLibrary, destination: Path) -> Path:
    """Save only a location/identity, never SWCs, credentials or executable actions."""
    destination = Path(destination).expanduser().absolute()
    if not destination.name.endswith(LINK_SUFFIX):
        raise ValueError('Library links must end in ' + LINK_SUFFIX)
    if destination.is_symlink() or destination.resolve().is_relative_to(library.root.resolve()):
        raise ValueError('Save the library link outside the SWC source folder')
    value = {'schema': LINK_SCHEMA, 'label': library.label, 'dataset': library.dataset,
             'source_key': library.key, 'folder': str(library.root.resolve()), 'access': 'read_only'}
    data = json.dumps(value, indent=2, ensure_ascii=False) + '\n'
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if destination.read_text(encoding='utf-8') == data:
            return destination
        raise FileExistsError('A different file already exists; choose a new link filename')
    with destination.open('x', encoding='utf-8') as handle:
        handle.write(data)
    return destination


def read_library_link(source: Path) -> LinkedLibrary:
    """A link is inert metadata. Opening one never changes settings by itself."""
    source = Path(source).expanduser().absolute()
    info = source.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
        raise ValueError('Choose a regular library-link file smaller than 64 KB')
    value = json.loads(source.read_text(encoding='utf-8'))
    expected = {'schema', 'label', 'dataset', 'source_key', 'folder', 'access'}
    if (not isinstance(value, dict) or set(value) != expected or value['schema'] != LINK_SCHEMA
            or value['access'] != 'read_only' or any(not isinstance(v, str) or not v.strip() or len(v) > 4096
                                                   for v in value.values())):
        raise ValueError('Invalid read-only SWC library link')
    root = Path(value['folder']).expanduser()
    raw = value['folder']
    if ('\x00' in raw or raw.startswith(('\\\\', '//'))
            or not (PurePosixPath(raw).is_absolute() or PureWindowsPath(raw).is_absolute())):
        raise ValueError('Library links require a local absolute folder path, not a URL or network share')
    native = root.is_absolute()
    return LinkedLibrary(value['source_key'], value['label'], value['dataset'], root.resolve() if native else root,
                         'Saved library link', native and root.is_dir())


def remember_library_link(profile: ResourceProfile, profile_path: Path, library: LinkedLibrary) -> Path:
    """Persist an explicit read-only binding outside the app, deduplicated by path."""
    if not library.root.is_absolute() or not library.root.is_dir():
        raise ValueError('The linked folder is unavailable; locate it on this device first')
    if library.root.resolve().is_relative_to(profile.managed_data_root.resolve()):
        raise ValueError('App-managed libraries already persist; choose an external SWC folder')
    if any(path_key(b.resolved_path) == path_key(library.root) for b in profile.resources
           if b.kind in {ResourceKind.MORPHOLOGY_SOURCE, ResourceKind.CONNECTOME_SOURCE}):
        return Path(profile_path)
    identifier = 'library-link-' + hashlib.sha256(path_key(library.root).encode()).hexdigest()[:20]
    binding = ResourceBinding(identifier, ResourceKind.MORPHOLOGY_SOURCE, str(library.root.resolve()),
        AccessMode.READ_ONLY, library.label, False, {'provider': 'local', 'managed': False,
        'dataset': library.dataset, 'connectome_key': library.key, 'saved_library_link': True})
    updated = ResourceProfile(profile.profile_id, (*profile.resources, binding), profile.label)
    return updated.save(profile_path, replace=Path(profile_path).exists())


@dataclass(frozen=True)
class LinkedLibrary:
    key: str
    label: str
    dataset: str
    root: Path
    origin: str
    available: bool


@dataclass(frozen=True)
class LinkedCount:
    root: Path
    swc_files: int
    swc_bytes: int
    skipped_links: int


def path_key(path: Path) -> str:
    return os.path.normcase(str(path.expanduser().resolve()))


def list_linked_libraries(profile: ResourceProfile) -> tuple[LinkedLibrary, ...]:
    """Use the same source discovery as Circuit Builder, without a tree scan."""
    bindings = tuple(b for b in profile.resources if b.kind in {
        ResourceKind.MORPHOLOGY_SOURCE, ResourceKind.CONNECTOME_SOURCE})
    managed = profile.managed_data_root.resolve()
    sources = discover_connectomes(profile.workspace_root or managed / '__no_workspace__',
                                   external_sources=profile_connectome_sources(profile))
    result: dict[str, LinkedLibrary] = {}
    registered = {path_key(b.resolved_path) for b in bindings}
    for source in sources:
        root = Path(source.root).resolve()
        if root.is_relative_to(managed):
            continue
        key = path_key(root)
        result.setdefault(key, LinkedLibrary(source.key, source.label, source.dataset,
            root, 'Registered folder' if key in registered else 'Workspace folder', root.is_dir()))
    # A disconnected drive must remain visible; the provider deliberately
    # excludes unavailable paths from runnable Circuit Builder choices.
    for binding in bindings:
        root = binding.resolved_path
        if root.is_relative_to(managed) or binding.metadata.get('managed'):
            continue
        key = path_key(root)
        result.setdefault(key, LinkedLibrary(binding.resource_id, binding.label or binding.resource_id,
            str(binding.metadata.get('dataset') or 'Not specified'), root, 'Registered folder', root.is_dir()))
    return tuple(sorted(result.values(), key=lambda item: (
        0 if item.key == 'manc:v1.2.1:full-local' else 1 if item.key == 'male-cns:v0.9' else 2,
        item.label.casefold(), str(item.root))))


def count_linked_swcs(root: Path, *, cancel: threading.Event | None = None) -> LinkedCount:
    """Count regular SWC files/bytes, not biological completeness or unique IDs."""
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError('Linked SWC folder is unavailable')
    count = size = skipped = 0
    def check_cancel():
        if cancel is not None and cancel.is_set():
            raise InterruptedError('SWC count cancelled; files were not changed')
    def failed(error):
        raise error  # Never present an inaccessible subtree as a complete count.
    for current, directories, files in os.walk(root, followlinks=False, onerror=failed):
        check_cancel()
        base = Path(current)
        for name in list(directories):
            path = base / name
            if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
                skipped += 1
                directories.remove(name)
            elif name in {'.history', '.git', '__pycache__'}:
                directories.remove(name)
        for name in files:
            check_cancel()
            if Path(name).suffix.casefold() != '.swc':
                continue
            path = base / name
            if path.is_symlink():
                skipped += 1
                continue
            if not path.is_file():
                raise ValueError('A listed SWC changed or is not a regular file')
            size += path.stat().st_size
            count += 1
    return LinkedCount(root, count, size, skipped)
