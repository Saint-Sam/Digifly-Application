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
    "neuprint_8nm_raw": 0.008,
}

RAW_DATASETS = {"male-cns:v0.9", "male-cns:v1.0", "manc:v1.2.1"}
RAW_UNIT_REFERENCES = {
    "male-cns:v0.9": "https://github.com/flyconnectome/2025malecns/blob/main/README_RELEASE_BUCKET.md",
    "male-cns:v1.0": "https://male-cns.janelia.org/download/",
    "manc:v1.2.1": "https://github.com/natverse/malevnc/blob/master/R/xform.R",
}


def neuprint_raw_convention(server: str, dataset: str) -> dict | None:
    """Version/origin-specific raw voxel units, never an extent-based guess."""
    if server != "https://neuprint.janelia.org" or dataset not in RAW_DATASETS:
        return None
    return {"input_units": "8nm", "scale_to_um": 0.008,
            "frame": dataset.replace(":v", "-v") + "-native", "raw_bytes_preserved": True}


def _raw_acquisition_matches(root: Path, resource: dict, entry: dict) -> bool:
    for acquisition in resource.get("acquisitions", ()):
        receipt = Path(str(acquisition.get("manifest", "")))
        if not receipt.is_absolute():
            receipt = root / receipt
        if not receipt.is_file() or receipt.is_symlink() or not receipt.resolve().is_relative_to(root):
            continue
        if _digest(receipt) != acquisition.get("sha256"):
            raise ValueError("SWC unit evidence acquisition checksum mismatch")
        original = json.loads(receipt.read_text(encoding="utf-8"))
        if (original.get("dataset") != resource.get("dataset") or original.get("server") != resource.get("server")):
            continue
        if original.get("schema") == "digifly-dataset-download-v1" and original.get("status") == "stored":
            if any(row.get("action") in {"downloaded", "replaced"}
                   and row.get("sha256") == entry.get("sha256")
                   and str(row.get("body_id")) == str(entry.get("body_id"))
                   for row in original.get("actions", ())):
                return True
            continue  # Keeping a local micrometre file does not make it a raw API SWC.
        if original.get("provider") != "neuprint":
            continue
        if any(row.get("sha256") == entry.get("sha256")
               and str(row.get("body_id")) == str(entry.get("body_id"))
               and row.get("download_action") != "kept_existing"
               and str(row.get("path", "")).endswith("_neuprint_raw.swc") for row in original.get("files", ())):
            return True
    return False


def _digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def source_coordinate_contract(path: Path) -> dict[str, Any]:
    path = path.resolve()
    joined_path = path.with_suffix(path.suffix + ".quality.json")
    if joined_path.is_file():
        joined = json.loads(joined_path.read_text(encoding="utf-8"))
        if joined.get("schema") == "digifly-fragment-join-v1" or joined.get("operation") == "radius-only SWC healing":
            result = dict(joined.get("coordinate_transform") or {})
            if not result and joined.get("operation") == "radius-only SWC healing":
                result = None  # Historical untyped radius copies retain their prior path handling.
            elif ((joined.get("output_sha256") or joined.get("result", {}).get("after_sha256")) != _digest(path) or result.get("source_sha256") != _digest(path)
                    or result.get("rule") not in RULE_SCALES
                    or result.get("scale_to_um") != RULE_SCALES[result["rule"]]):
                raise ValueError("Joined-copy coordinate details do not match this exact SWC")
            if result is not None:
                return result
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
            expected = neuprint_raw_convention(resource.get("server", ""), resource.get("dataset", ""))
            if resource.get("provider") in {"neuprint", "swc-library"} and expected is not None:
                entry = next((item for item in resource.get("files", ())
                              if (root / str(item.get("path", ""))).resolve() == path), None)
                if entry is None:
                    continue  # Derived copies need their own unit record, not a label-based rescale.
                if entry.get("sha256") != _digest(path):
                    raise ValueError("Managed SWC coordinate source details checksum mismatch")
                declared = resource.get("swc_coordinate_convention")
                if declared not in (None, expected):
                    raise ValueError("Managed SWC coordinate convention is inconsistent with this raw source")
                if resource.get("provider") == "swc-library" and not _raw_acquisition_matches(root, resource, entry):
                    if declared is not None or entry.get("download_action"):
                        raise ValueError("Raw SWC unit evidence is unavailable; original acquisition records are required")
                    continue  # Unrelated locally imported micrometre SWCs are not raw API data.
                result = _contract("male_cns_v09_neuprint_raw" if resource["dataset"] == "male-cns:v0.9" else "neuprint_8nm_raw", path)
                result["coordinate_frame"] = expected["frame"]
                result["unit_evidence_url"] = RAW_UNIT_REFERENCES[resource["dataset"]]
                return result

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
