"""Provider adapters from external resource profiles to app-native catalogs."""

from __future__ import annotations

from pathlib import Path

from .circuit import ConnectomeRef
from .resource_profile import ResourceKind, ResourceProfile


def _looks_like_legacy_manc_export(root: Path) -> bool:
    """Recognize the full SWC archive produced by the earlier Digifly pipeline."""

    return (
        (root / ".phase2_export_index.json").is_file()
        and (root / "edges" / "master_edges_cache.sqlite").is_file()
        and (
            root
            / "DN"
            / "DNp01"
            / "10000"
            / "10000_axodendro_with_synapses.swc"
        ).is_file()
    )


def profile_connectome_sources(profile: ResourceProfile | None) -> tuple[ConnectomeRef, ...]:
    """Return explicitly registered morphology/connectome roots without scanning them."""

    if profile is None:
        return ()
    sources: list[ConnectomeRef] = []
    for binding in profile.resources:
        if binding.kind not in {
            ResourceKind.MORPHOLOGY_SOURCE,
            ResourceKind.CONNECTOME_SOURCE,
        }:
            continue
        root = binding.resolved_path
        if not root.is_dir():
            continue
        dataset = str(binding.metadata.get("dataset") or "external")
        key = str(binding.metadata.get("connectome_key") or f"profile:{binding.resource_id}")
        label = binding.label or binding.resource_id
        # Phase 2 of the original Digifly app registered its complete MANC
        # morphology tree as a generic "external-swcs" folder.  Recognize the
        # archive by its own index, edge cache, and canonical DNp01 body so the
        # Workstation does not present a 24 GB MANC source as an anonymous
        # external folder or confuse it with the small Phase 1 curated subset.
        if dataset == "external" and _looks_like_legacy_manc_export(root):
            dataset = "manc:v1.2.1"
            key = "manc:v1.2.1:full-local"
            label = "MANC v1.2.1 · full local SWCs"
        sources.append(
            ConnectomeRef(
                key=key,
                label=label,
                root=str(root),
                dataset=dataset,
            )
        )
    return tuple(sources)
