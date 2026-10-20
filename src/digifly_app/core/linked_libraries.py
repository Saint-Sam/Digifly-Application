"""Read-only discovery and explicit inventory of user-owned SWC libraries.

These entries are not managed bundles: discovery/counting never writes a
manifest, profile or source file, and grants no move/trash/delete capability.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import threading

from .connectomes import discover_connectomes
from .providers import profile_connectome_sources
from .resource_profile import ResourceKind, ResourceProfile


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
