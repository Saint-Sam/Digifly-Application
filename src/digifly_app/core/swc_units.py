"""Explicit SWC source conventions; never infer units from geometric extent.

Male CNS v0.9 native SWCs use 8 nm units. The historical Phase 1 exporter
divided those values by 1000 instead of 125. Preserve its bytes and topology;
correct the uniform XYZ/radius scale on import. Recovered micrometer files
and managed raw downloads have distinct, source details-bound conventions.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


RULE_SCALES = {
    "identity_um": 1.0,
    "male_cns_v09_phase1_legacy": 8.0,
    "male_cns_v09_release_um": 1.0,
    "male_cns_v09_neuprint_raw": 0.008,
}


def _digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def source_coordinate_contract(path: Path) -> dict[str, Any]:
    path = path.resolve()
    # Custom saves retain original SWC bytes and their explicitly recorded unit
    # convention. Bind the contract to this exact copied file, not a label.
    for root in tuple(path.parents)[:8]:
        manifest_path = root / "manifest.json"
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            contract = manifest.get("coordinate_transform")
            if contract is not None:
                recorded = (root / str(manifest.get("custom_swc", ""))).resolve()
                if recorded != path or not recorded.is_relative_to(root):
                    raise ValueError("Custom SWC coordinate source details path mismatch")
                result = dict(contract)
                rule = result.get("rule")
                if (rule not in RULE_SCALES
                        or result.get("scale_to_um") != RULE_SCALES[rule]
                        or result.get("source_sha256") != _digest(path)
                        or manifest.get("custom_sha256") != result["source_sha256"]):
                    raise ValueError("Custom SWC coordinate source details identity check failed")
                return result
        resource_path = root / "digifly-resource.json"
        if resource_path.is_file():
            resource = json.loads(resource_path.read_text(encoding="utf-8"))
            if (resource.get("provider") == "neuprint"
                    and resource.get("dataset") == "male-cns:v0.9"
                    and resource.get("server") == "https://neuprint.janelia.org"):
                entry = next((item for item in resource.get("files", ())
                              if (root / str(item.get("path", ""))).resolve() == path), None)
                if entry is None or entry.get("sha256") != _digest(path):
                    raise ValueError("Managed Male CNS SWC coordinate source details checksum mismatch")
                return _contract("male_cns_v09_neuprint_raw", path)

    # Only the established version-specific Phase 1 export convention is
    # repaired. MANC, arbitrary/custom SWCs and other versions stay unchanged.
    export_root = next((root for root in path.parents
                        if root.name == "export_swc"
                        and root.parent.name == "male-cns_v0.9"), None)
    if export_root is not None:
        with path.open(encoding="utf-8") as handle:
            header = "".join(next(handle, "") for _ in range(8))
        if ("# Source: Janelia male-cns v0.9 public skeleton release" in header
                and "#x/y/z/rconvertedfrom8nmunitstomicrometers,scale0.008" in header.replace(" ", "")):
            return _contract("male_cns_v09_release_um", path)
        body = path.parent.name
        legacy_names = {f"{body}_axodendro_with_synapses.swc",
                        f"{body}_healed.swc", f"{body}_healed_final.swc"}
        if body.isdigit() and path.name in legacy_names and f"# bodyId {body}\n" in header:
            return _contract("male_cns_v09_phase1_legacy", path)
        raise ValueError(f"Unknown Male CNS v0.9 SWC unit source details: {path}")
    return _contract("identity_um", path)


def _contract(rule: str, path: Path) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "rule": rule,
        "scale_to_um": RULE_SCALES[rule],
        "source_sha256": _digest(path),
        "output_units": "um",
        "coordinate_frame": ("male-cns-v0.9-native" if rule.startswith("male_cns")
                             else "source-frame"),
        "geometry_policy": "Uniform XYZ and radius scaling; no translation, rotation, healing or links.",
    }
