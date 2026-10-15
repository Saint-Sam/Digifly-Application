"""Read-only saved Figure 8 presentation and exclusive exports; no science runs.

Checks cover figures and their consumed analysis/review files. Recorded raw-run
lineage is retained, not re-audited. Partial coverage is never paper acceptance.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re

POPULATIONS = frozenset(("L2e", "L2i", "L4e", "L4i", "L5e", "L5i", "L6e", "L6i"))
SCALES = (1, 2, 5, 10, 20, 30, 40, 50, 60, 80, 100)
REQUIRED = frozenset((scale, drive) for scale in SCALES for drive in ("balanced_poisson", "balanced_dc")
                     if drive != "balanced_dc" or scale >= 10)
EXPORT_SCHEMA = "digifly-saved-figure-export-v1"
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_BUNDLE_BYTES = 128 * 1024 * 1024


def _hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(path):
    if Path(path).stat().st_size > MAX_FILE_BYTES:
        raise ValueError("Figure manifest exceeds bounded presentation size")
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Figure manifest must be a JSON object")
    return value


def _owned(root, relative):
    name = Path(relative)
    if name.is_absolute() or ".." in name.parts or not name.parts:
        raise ValueError("Figure output path must be owned and relative")
    path = root / name
    if any((root / Path(*name.parts[:i])).is_symlink() for i in range(1, len(name.parts) + 1)):
        raise ValueError("Figure output symlinks are not supported")
    if not path.is_file() or not path.resolve().is_relative_to(root):
        raise ValueError(f"Missing or outside figure artifact: {relative}")
    return path


def _checked(path, digest):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError(f"Missing, symlinked or oversized figure evidence: {path}")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) or _hash(path) != digest:
        raise ValueError(f"Figure evidence hash differs: {path}")
    return path


def _source_path(root, text):
    path = Path(text)
    if path.is_absolute():
        return path
    if ".." in path.parts:
        raise ValueError("Outside relative source path")
    candidates = [ancestor / path for ancestor in (root, *root.parents) if (ancestor / path).is_file()]
    if len(set(candidates)) != 1:
        raise ValueError(f"Cannot resolve unambiguous recorded source: {text}")
    return candidates[0]


def _grid(table):
    """Require full eight-population rows and honest empty unmeasured slots."""
    with Path(table).open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if not {"scale_percent", "drive", "population", "status", "paper_scope"}.issubset(reader.fieldnames or ()):
            raise ValueError("Figure source table lacks condition identities")
        rows = list(reader)
    if len(rows) != len(SCALES) * 2 * 8:
        raise ValueError("Figure source table must retain every measured/unmeasured slot")
    groups, seen = {}, set()
    identity = {"scale_percent", "drive", "population", "status", "paper_scope", "run_name", "seed"}
    for row in rows:
        key = (int(row["scale_percent"]), row["drive"])
        if key[0] not in SCALES or key[1] not in ("balanced_poisson", "balanced_dc") or row["population"] not in POPULATIONS:
            raise ValueError("Unknown Figure 8 condition or population")
        item = (*key, row["population"])
        if item in seen:
            raise ValueError("Duplicate Figure 8 population row")
        seen.add(item)
        status = row["status"]
        if status not in ("measured", "measured_silent", "missing", "not_simulated", "paper_excluded_unrun"):
            raise ValueError("Unsupported figure observation status")
        if (key in REQUIRED) != (row["paper_scope"] == "required"):
            raise ValueError("Figure paper-required scope differs")
        if key in groups and groups[key] != status:
            raise ValueError("Population statuses differ within a figure condition")
        groups[key] = status
        numeric = [v for k, v in row.items() if k not in identity and v not in (None, "")]
        if status not in ("measured", "measured_silent") and numeric:
            raise ValueError("Unmeasured condition has invented numeric values")
        if status in ("measured", "measured_silent"):
            rate = row.get("mean_rate_hz_60s", row.get("rate_hz_60s", ""))
            if not rate or not math.isfinite(float(rate)) or float(rate) < 0:
                raise ValueError("Measured condition lacks a finite nonnegative rate")
            if status == "measured_silent" and float(rate) != 0:
                raise ValueError("Silent condition has a nonzero rate")
            if any(not math.isfinite(float(v)) for v in numeric):
                raise ValueError("Nonfinite figure measurement")
    measured = {key for key, status in groups.items() if status in ("measured", "measured_silent")}
    return measured, measured & REQUIRED


def _condition_key(value):
    return (int(value["scale_percent"]), value["drive"])


def load_figure_bundle(manifest_path, *, requested_conditions=()):
    """Verify saved original/exported evidence and return a read-only view model."""
    path = Path(manifest_path).expanduser().absolute()
    if path.is_symlink() or not path.is_file():
        raise ValueError("Select an existing figure manifest, not a symlink")
    path = path.resolve()
    root = path.parent
    manifest = _json(path)
    if manifest.get("schema") == EXPORT_SCHEMA:
        original = _checked(manifest["source_manifest"], manifest["source_manifest_sha256"])
        if _json(original).get("schema") == EXPORT_SCHEMA:
            raise ValueError("Export receipts must refer directly to an original figure manifest")
        info = load_figure_bundle(original, requested_conditions=requested_conditions)
        if (manifest.get("scientific_acceptance") is not False
                or manifest.get("raw_runs_reaudited") is not False
                or manifest.get("simulation_or_compilation_performed") is not False):
            raise ValueError("Export receipt must retain scientific/reaudit caveats")
        for field in ("coverage", "verified_source_sha256", "recorded_raw_lineage_sha256", "caveat"):
            if manifest.get(field) != info[field]:
                raise ValueError(f"Export receipt differs from checked original {field}")
        inventory = manifest.get("files_sha256")
        expected = {**info["files_sha256"], "original_manifest.json": info["manifest_sha256"]}
        if inventory != expected:
            raise ValueError("Export inventory differs from checked original bundle")
        for name, digest in inventory.items():
            _checked(_owned(root, name), digest)
        return {**info, "manifest_path": str(path), "root": str(root), "exported": True}
    sources, recorded_raw = {}, {}
    if manifest.get("workflow") == "pdcm_figure8_partial_condition_audit_v2" and manifest.get("schema_version") == 2:
        files = manifest.get("output_sha256")
        table = "figure8_source_data.csv"
        kind = "Native NetPyNE Figure 8 · saved partial campaign"
        for condition in manifest.get("present_conditions", ()):
            if condition.get("duration_ms") != 60100 or condition.get("observation_status") not in ("measured", "measured_silent"):
                raise ValueError("Figure condition is not a measured full-duration result")
            analysis_inventory = condition.get("analysis_sha256") or {}
            if not {"analysis_manifest.json", "paper_metrics.csv"}.issubset(analysis_inventory):
                raise ValueError("Measured figure condition lacks bound paper analysis")
            for name, digest in analysis_inventory.items():
                source = _owned(Path(condition["analysis"]).resolve(), name)
                sources[str(source)] = digest
            for name, digest in (condition.get("source_sha256") or {}).items():
                recorded_raw[str(Path(condition["run"]) / name)] = digest
        declared_native = manifest.get("paper_required_available_count")
        declared_bionet = None
    elif manifest.get("schema") == "digifly-app-bionet-figure8-companion-v1":
        if manifest.get("scientific_acceptance") is not False or manifest.get("simulation_or_compilation_performed") is not False:
            raise ValueError("Companion must remain saved presentation, not scientific acceptance")
        files = manifest.get("files_sha256")
        table = "bionet_figure8_source_data.csv"
        kind = "BioNet Figure 8 companion · historical native context"
        for text, digest in (manifest.get("source_sha256") or {}).items():
            if Path(text).name == "spikes.h5":
                recorded_raw[text] = digest
            else:
                source = _source_path(root, text)
                sources[str(source)] = digest
        declared_native = manifest.get("native_measured_active_conditions")
        declared_bionet = manifest.get("bionet_measured_active_conditions")
    else:
        raise ValueError("Unsupported saved figure manifest")
    if not isinstance(files, dict) or not files or table not in files or "caption.txt" not in files or not sources:
        raise ValueError("Incomplete figure output/source inventory")
    if {"original_manifest.json", "export_receipt.json"} & set(files):
        raise ValueError("Figure output collides with reserved export evidence")
    if sum(_owned(root, name).stat().st_size for name in files) > MAX_BUNDLE_BYTES:
        raise ValueError("Saved figure bundle exceeds bounded presentation size")
    for name, digest in files.items():
        _checked(_owned(root, name), digest)
    for name, digest in sources.items():
        _checked(name, digest)
    measured, active = _grid(root / table)
    if declared_bionet is None:
        present = {_condition_key(c) for c in manifest["present_conditions"]}
        if present != measured or declared_native != len(active) or manifest.get("required_condition_count") != len(REQUIRED):
            raise ValueError("Native measured condition count differs")
        if {_condition_key(c) for c in manifest.get("missing_conditions", ())} != REQUIRED - active:
            raise ValueError("Native missing condition declaration differs")
        counts = f"Native {declared_native}/19 measured active conditions"
    else:
        _, native = _grid(_owned(root, "native_context_source_data.csv"))
        if declared_bionet != len(active) or declared_native != len(native) or manifest.get("paper_required_conditions") != len(REQUIRED):
            raise ValueError("Companion measured condition count differs")
        counts = f"BioNet {declared_bionet}/19 · historical native context {declared_native}/19"
    for requested in requested_conditions:
        key = _condition_key(requested) if isinstance(requested, dict) else tuple(requested)
        if key not in measured:
            raise ValueError(f"Requested condition is missing/unmeasured: {key}")
    previews = sorted(name for name in files if Path(name).suffix.lower() in (".pdf", ".png"))
    if not previews:
        raise ValueError("Figure bundle has no recorded PDF or PNG")
    return {"manifest_path": str(path), "manifest_sha256": _hash(path), "root": str(root),
            "title": kind, "coverage": counts, "files_sha256": files, "previews": previews,
            "verified_source_sha256": sources, "recorded_raw_lineage_sha256": recorded_raw,
            "raw_runs_reaudited": False, "scientific_acceptance": False, "exported": False,
            "missing_active_conditions": [dict(scale_percent=s, drive=d) for s, d in sorted(REQUIRED - active)],
            "caveat": "Partial measured data only; empty slots remain unmeasured. BioNet shares NEURON. No simulation, raw-run re-audit, equivalence or scientific acceptance. Same-machine checked export: reopening requires original analysis/source files; not a standalone reviewer package."}


def export_figure_bundle(manifest_path, destination, *, requested_conditions=()):
    """Copy checked presentation bytes to an exclusive new directory, not render."""
    info = load_figure_bundle(manifest_path, requested_conditions=requested_conditions)
    if info["exported"]:
        raise ValueError("Select the original checked figure manifest to create another export")
    destination = Path(destination).expanduser().absolute()
    if any(ancestor.is_symlink() for ancestor in (destination, *destination.parents)):
        raise ValueError("Export destination must not contain symlink ancestors")
    if destination.exists() or destination.is_symlink():
        raise ValueError("Export destination already exists; overwriting is forbidden")
    if destination.resolve().is_relative_to(Path(info["root"])):
        raise ValueError("Export must be outside the original figure bundle")
    destination.mkdir(parents=True, exist_ok=False)
    try:
        for name, digest in info["files_sha256"].items():
            source = _checked(_owned(Path(info["root"]), name), digest)
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open("rb") as reader, target.open("xb") as writer:
                for block in iter(lambda: reader.read(1024 * 1024), b""):
                    writer.write(block)
            _checked(target, digest)
        original = _checked(info["manifest_path"], info["manifest_sha256"])
        with (destination / "original_manifest.json").open("xb") as writer:
            writer.write(original.read_bytes())
        receipt = {"schema": EXPORT_SCHEMA, "source_manifest": str(original),
                   "source_manifest_sha256": info["manifest_sha256"],
                   "created_utc": datetime.now(timezone.utc).isoformat(),
                   "files_sha256": {**info["files_sha256"], "original_manifest.json": info["manifest_sha256"]},
                   "coverage": info["coverage"], "verified_source_sha256": info["verified_source_sha256"],
                   "recorded_raw_lineage_sha256": info["recorded_raw_lineage_sha256"],
                   "scientific_acceptance": False, "raw_runs_reaudited": False,
                   "simulation_or_compilation_performed": False, "caveat": info["caveat"]}
        path = destination / "export_receipt.json"
        with path.open("x", encoding="utf-8") as stream:
            json.dump(receipt, stream, indent=2, sort_keys=True)
            stream.write("\n")
        load_figure_bundle(path, requested_conditions=requested_conditions)
        return path
    except Exception:
        # Preserve a failed export for diagnosis; never remove or reuse it.
        raise
