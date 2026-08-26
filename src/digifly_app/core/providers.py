"""Provider adapters from external resource profiles to app-native catalogs."""

from __future__ import annotations

from pathlib import Path

from .circuit import ConnectomeRef
from .resource_profile import ResourceKind, ResourceProfile


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
        sources.append(
            ConnectomeRef(
                key=key,
                label=binding.label or binding.resource_id,
                root=str(root),
                dataset=dataset,
            )
        )
    return tuple(sources)
