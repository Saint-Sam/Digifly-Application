"""Post-import SWC radius auditing and conservative, provenance-rich repair.

The checker deliberately separates detection from mutation.  Scans are read-only;
healing happens only after an explicit copy or overwrite choice and is verified to
have changed radius values only.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import tempfile
from typing import Any, Iterable, Mapping

from .resource_profile import ResourceKind, ResourceProfile


QUALITY_SCHEMA_VERSION = 1
DEFAULT_RECENT_DAYS = 30
ADAPTIVE_RADIUS_RULE_ID = "per-swc-adaptive-radius-island-v1"


@dataclass(frozen=True)
class SwcNode:
    node_id: int
    swc_type: int
    x: float
    y: float
    z: float
    radius: float
    parent_id: int
    line_number: int


@dataclass(frozen=True)
class RadiusFinding:
    node_id: int
    line_number: int
    radius_um: float
    suggested_radius_um: float
    rule_id: str
    confidence: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SwcQualityReport:
    path: str
    sha256: str
    dataset: str
    body_id: str
    provider: str
    source_unit: str
    length_scale_to_um: float
    node_count: int
    findings: tuple[RadiusFinding, ...] = ()
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    manifest: str = ""
    instance: str = ""

    @property
    def needs_review(self) -> bool:
        return bool(self.findings or self.errors)

    @property
    def can_heal(self) -> bool:
        return bool(self.findings) and not self.errors

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["needs_review"] = self.needs_review
        payload["can_heal"] = self.can_heal
        payload["findings"] = [finding.to_dict() for finding in self.findings]
        return payload


@dataclass(frozen=True)
class ImportQualityBatch:
    generated_utc: str
    recent_days: int
    reports: tuple[SwcQualityReport, ...]
    skipped: tuple[str, ...] = ()

    @property
    def review_reports(self) -> tuple[SwcQualityReport, ...]:
        return tuple(report for report in self.reports if report.needs_review)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": QUALITY_SCHEMA_VERSION,
            "generated_utc": self.generated_utc,
            "recent_days": self.recent_days,
            "reports": [report.to_dict() for report in self.reports],
            "skipped": list(self.skipped),
        }


@dataclass(frozen=True)
class HealingResult:
    input_path: str
    output_path: str
    backup_path: str
    report_path: str
    mode: str
    changed_node_ids: tuple[int, ...]
    before_sha256: str
    after_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_text(moment: datetime | None = None) -> str:
    return (moment or _utc_now()).isoformat().replace("+00:00", "Z")


def _parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_swc(path: Path) -> tuple[list[str], tuple[SwcNode, ...], tuple[str, ...]]:
    lines = path.read_text(encoding="utf-8-sig").splitlines(keepends=True)
    nodes: list[SwcNode] = []
    errors: list[str] = []
    for line_number, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        fields = stripped.split()
        if len(fields) < 7:
            errors.append(f"line {line_number}: expected at least 7 SWC fields")
            continue
        try:
            node = SwcNode(
                node_id=int(fields[0]),
                swc_type=int(fields[1]),
                x=float(fields[2]),
                y=float(fields[3]),
                z=float(fields[4]),
                radius=float(fields[5]),
                parent_id=int(fields[6]),
                line_number=line_number,
            )
        except ValueError:
            errors.append(f"line {line_number}: invalid numeric SWC field")
            continue
        if not all(math.isfinite(value) for value in (node.x, node.y, node.z, node.radius)):
            errors.append(f"line {line_number}: non-finite coordinate or radius")
        nodes.append(node)
    if not nodes and not errors:
        errors.append("SWC contains no data rows")
    return lines, tuple(nodes), tuple(errors)


def _source_scale_to_um(
    nodes: Iterable[SwcNode], *, provider: str = "", path: Path | None = None
) -> tuple[float, str, str | None]:
    node_list = tuple(nodes)
    provider_key = provider.casefold().strip()
    name = path.name.casefold() if path is not None else ""
    if provider_key == "neuprint" and "raw" in name:
        return 0.001, "nm", None
    coordinates = [abs(value) for node in node_list for value in (node.x, node.y, node.z)]
    max_coordinate = max(coordinates, default=0.0)
    # Fly EM skeletons expressed in nanometres normally span tens of thousands
    # of units; micrometre SWCs are generally below 1,000.  Ambiguous files stay
    # in their native units and receive a warning instead of being rescaled.
    if max_coordinate >= 10_000.0:
        return 0.001, "nm", "units inferred as nanometres from coordinate extent"
    return 1.0, "um", None


def _topology_issues(nodes: tuple[SwcNode, ...]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    errors: list[str] = []
    warnings: list[str] = []
    by_id: dict[int, SwcNode] = {}
    duplicates: set[int] = set()
    for node in nodes:
        if node.node_id in by_id:
            duplicates.add(node.node_id)
        by_id[node.node_id] = node
        if node.radius <= 0:
            errors.append(f"node {node.node_id}: radius must be positive")
    if duplicates:
        errors.append("duplicate node IDs: " + ", ".join(str(value) for value in sorted(duplicates)))
    missing = sorted(
        {node.parent_id for node in nodes if node.parent_id >= 0 and node.parent_id not in by_id}
    )
    if missing:
        errors.append("missing parent node IDs: " + ", ".join(str(value) for value in missing[:20]))
    roots = [node.node_id for node in nodes if node.parent_id < 0]
    if not roots:
        errors.append("SWC has no root node")
    elif len(roots) > 1:
        warnings.append(f"SWC has {len(roots)} root nodes")

    if not duplicates and not missing:
        resolved: set[int] = set()
        for start in by_id:
            if start in resolved:
                continue
            chain: list[int] = []
            positions: dict[int, int] = {}
            current = start
            while current >= 0 and current in by_id and current not in resolved:
                if current in positions:
                    errors.append(f"cycle detected from node {start}")
                    break
                positions[current] = len(chain)
                chain.append(current)
                current = by_id[current].parent_id
            resolved.update(chain)
            if errors and errors[-1].startswith("cycle detected"):
                break
    return tuple(dict.fromkeys(errors)), tuple(warnings)


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("percentile requires at least one value")
    return ordered[round((len(ordered) - 1) * fraction)]


def _adaptive_radius_findings(
    nodes: tuple[SwcNode, ...], *, scale: float
) -> tuple[RadiusFinding, ...]:
    """Rank radius discontinuities against each SWC's own shape and scale.

    There is no universal fly-neuron radius in this rule.  For every compartment
    it estimates a local reference from a four-hop topology neighbourhood, scores
    the relative (log-ratio) drop, and reviews only the most extreme two percent
    for that individual SWC.  The proposed radius is a conservative lower-quartile
    value from larger nearby compartments, so the healer also adapts per neuron.
    """

    by_id = {node.node_id: node for node in nodes}
    adjacency: dict[int, set[int]] = {node.node_id: set() for node in nodes}
    for node in nodes:
        if node.parent_id in by_id:
            adjacency[node.node_id].add(node.parent_id)
            adjacency[node.parent_id].add(node.node_id)

    candidates: list[tuple[float, SwcNode, float]] = []
    for node in nodes:
        if node.parent_id < 0 or node.radius <= 0:
            continue
        seen = {node.node_id}
        frontier = {node.node_id}
        for _hop in range(4):
            frontier = {
                neighbour
                for current in frontier
                for neighbour in adjacency[current]
                if neighbour not in seen
            }
            seen.update(frontier)
        seen.discard(node.node_id)
        radii = [by_id[node_id].radius * scale for node_id in seen if by_id[node_id].radius > 0]
        if len(radii) < 3:
            continue
        radius_um = node.radius * scale
        upper_neighbours = [value for value in radii if value > radius_um]
        if len(upper_neighbours) < 3:
            continue
        local_reference = statistics.median(upper_neighbours)
        ratio = local_reference / radius_um
        if ratio >= 2.0:
            candidates.append((math.log(ratio), node, _percentile(upper_neighbours, 0.25)))

    if not candidates:
        return ()
    # A relative percentile makes a compact SWC and a giant, highly branched SWC
    # comparable without imposing the same radius or variance threshold on both.
    score_cutoff = _percentile([candidate[0] for candidate in candidates], 0.98)
    selected = [candidate for candidate in candidates if candidate[0] >= score_cutoff]
    findings = [
        RadiusFinding(
            node_id=node.node_id,
            line_number=node.line_number,
            radius_um=node.radius * scale,
            suggested_radius_um=max(suggested, node.radius * scale),
            rule_id=ADAPTIVE_RADIUS_RULE_ID,
            confidence="adaptive_review",
            reason=(
                f"one of this SWC's most extreme local radius drops "
                f"({math.exp(score):.1f}x below its larger nearby compartments)"
            ),
        )
        for score, node, suggested in selected
    ]
    return tuple(sorted(findings, key=lambda finding: finding.node_id))


def analyze_swc(
    path: str | Path,
    *,
    dataset: str = "",
    body_id: str | int = "",
    provider: str = "",
    manifest: str | Path = "",
    instance: str = "",
) -> SwcQualityReport:
    source = Path(path).expanduser().resolve()
    _lines, nodes, parse_errors = _parse_swc(source)
    scale, source_unit, unit_warning = _source_scale_to_um(nodes, provider=provider, path=source)
    topology_errors, topology_warnings = _topology_issues(nodes)
    findings = _adaptive_radius_findings(nodes, scale=scale)
    warnings = list(topology_warnings)
    if unit_warning:
        warnings.append(unit_warning)
    return SwcQualityReport(
        path=str(source),
        sha256=sha256_file(source),
        dataset=str(dataset),
        body_id=str(body_id),
        provider=str(provider),
        source_unit=source_unit,
        length_scale_to_um=scale,
        node_count=len(nodes),
        findings=findings,
        errors=parse_errors + topology_errors,
        warnings=tuple(warnings),
        manifest=str(Path(manifest).expanduser().resolve()) if manifest else "",
        instance=str(instance),
    )


def _manifest_swc_records(
    manifest_path: Path,
) -> tuple[Mapping[str, Any], tuple[Mapping[str, Any], ...]]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    records = payload.get("files")
    if not isinstance(records, list):
        raise ValueError("manifest has no files list")
    return payload, tuple(record for record in records if isinstance(record, Mapping))


def scan_recent_imports(
    profile: ResourceProfile,
    *,
    recent_days: int = DEFAULT_RECENT_DAYS,
    now: datetime | None = None,
) -> ImportQualityBatch:
    """Audit only manifest-declared SWCs from recent managed imports.

    This intentionally does not crawl arbitrary legacy morphology roots or large
    connectome datasets.  A provider manifest is the scope and provenance boundary.
    """

    if recent_days < 1:
        raise ValueError("recent_days must be at least 1")
    current = (now or _utc_now()).astimezone(timezone.utc)
    cutoff = current - timedelta(days=recent_days)
    reports: list[SwcQualityReport] = []
    skipped: list[str] = []
    seen_paths: set[Path] = set()
    bindings = profile.bindings(ResourceKind.MORPHOLOGY_SOURCE) + profile.bindings(
        ResourceKind.CONNECTOME_SOURCE
    )
    for binding in bindings:
        manifest_value = binding.metadata.get("manifest")
        if not manifest_value:
            continue
        manifest_path = Path(str(manifest_value)).expanduser().resolve()
        try:
            payload, file_records = _manifest_swc_records(manifest_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            skipped.append(f"{binding.resource_id}: could not read import manifest: {exc}")
            continue
        imported_at = _parse_utc(payload.get("fetched_at") or payload.get("imported_at"))
        if imported_at is None:
            skipped.append(f"{binding.resource_id}: manifest has no valid import timestamp")
            continue
        if imported_at < cutoff:
            continue
        provider = str(payload.get("provider") or binding.metadata.get("provider") or "")
        dataset = str(payload.get("dataset") or binding.metadata.get("dataset") or "")
        manifest_root = manifest_path.parent.resolve()
        for record in file_records:
            relative = record.get("path")
            if not isinstance(relative, str) or not relative.casefold().endswith(".swc"):
                continue
            candidate = (manifest_root / relative).resolve()
            try:
                candidate.relative_to(manifest_root)
            except ValueError:
                skipped.append(f"{binding.resource_id}: manifest path escapes its bundle: {relative}")
                continue
            if candidate in seen_paths:
                continue
            seen_paths.add(candidate)
            if not candidate.is_file():
                skipped.append(f"{binding.resource_id}: missing declared SWC: {relative}")
                continue
            report = analyze_swc(
                candidate,
                dataset=dataset,
                body_id=record.get("body_id", ""),
                provider=provider,
                manifest=manifest_path,
                instance=str(record.get("instance") or ""),
            )
            declared_hash = str(record.get("sha256") or "")
            if declared_hash and declared_hash != report.sha256:
                report = SwcQualityReport(
                    **{
                        **report.__dict__,
                        "warnings": report.warnings
                        + ("current file hash differs from the acquisition manifest",),
                    }
                )
            reports.append(report)
    return ImportQualityBatch(
        generated_utc=_utc_text(current),
        recent_days=recent_days,
        reports=tuple(reports),
        skipped=tuple(skipped),
    )


def _render_radius(radius_native: float) -> str:
    return f"{radius_native:.9g}"


def _replace_radii_only(
    lines: list[str], findings: Iterable[RadiusFinding], *, scale: float
) -> tuple[str, tuple[int, ...]]:
    by_line = {finding.line_number: finding for finding in findings}
    changed: list[int] = []
    rendered: list[str] = []
    for line_number, line in enumerate(lines, 1):
        finding = by_line.get(line_number)
        if finding is None:
            rendered.append(line)
            continue
        newline = "\n" if line.endswith("\n") else ""
        body = line[:-1] if newline else line
        fields = body.split()
        if len(fields) < 7 or int(fields[0]) != finding.node_id:
            raise ValueError(f"SWC changed after audit at line {line_number}")
        fields[5] = _render_radius(finding.suggested_radius_um / scale)
        rendered.append(" ".join(fields) + newline)
        changed.append(finding.node_id)
    if len(changed) != len(by_line):
        raise ValueError("not all audited SWC lines were available for repair")
    return "".join(rendered), tuple(changed)


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _unique_copy_path(source: Path) -> Path:
    preferred = source.with_name(f"{source.stem}_radius_healed{source.suffix}")
    if not preferred.exists():
        return preferred
    timestamp = _utc_now().strftime("%Y%m%dT%H%M%SZ")
    return source.with_name(f"{source.stem}_radius_healed_{timestamp}{source.suffix}")


def _verify_radius_only(before: tuple[SwcNode, ...], after: tuple[SwcNode, ...]) -> tuple[int, ...]:
    if len(before) != len(after):
        raise ValueError("healed SWC changed the number of nodes")
    changed: list[int] = []
    for old, new in zip(before, after):
        old_shape = (old.node_id, old.swc_type, old.x, old.y, old.z, old.parent_id)
        new_shape = (new.node_id, new.swc_type, new.x, new.y, new.z, new.parent_id)
        if old_shape != new_shape:
            raise ValueError(f"healed SWC changed non-radius data at node {old.node_id}")
        if old.radius != new.radius:
            changed.append(old.node_id)
    return tuple(changed)


def heal_swc(
    report: SwcQualityReport,
    *,
    mode: str = "copy",
) -> HealingResult:
    """Apply audited radius suggestions as a new copy or a backed-up overwrite."""

    if mode not in {"copy", "overwrite"}:
        raise ValueError("mode must be 'copy' or 'overwrite'")
    if not report.can_heal:
        raise ValueError("quality report has no safe radius-only repair")
    source = Path(report.path).resolve()
    if sha256_file(source) != report.sha256:
        raise ValueError("SWC changed after audit; run the checker again")
    lines, before_nodes, parse_errors = _parse_swc(source)
    if parse_errors:
        raise ValueError("SWC no longer parses cleanly")
    healed_text, intended_ids = _replace_radii_only(
        lines, report.findings, scale=report.length_scale_to_um
    )
    backup: Path | None = None
    if mode == "copy":
        output = _unique_copy_path(source)
        _atomic_write_text(output, healed_text)
    else:
        timestamp = _utc_now().strftime("%Y%m%dT%H%M%SZ")
        backup_dir = source.parent / ".digifly-backups"
        backup = backup_dir / f"{source.stem}.pre-radius-heal-{timestamp}{source.suffix}"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup.write_bytes(source.read_bytes())
        _atomic_write_text(source, healed_text)
        output = source

    _after_lines, after_nodes, after_errors = _parse_swc(output)
    if after_errors:
        raise ValueError("healed SWC failed post-write parsing")
    verified_ids = _verify_radius_only(before_nodes, after_nodes)
    if set(verified_ids) != set(intended_ids):
        raise ValueError("post-write verification found unexpected radius changes")
    after_hash = sha256_file(output)
    provenance_path = output.with_suffix(output.suffix + ".quality.json")
    result_without_report = {
        "input_path": str(source),
        "output_path": str(output),
        "backup_path": str(backup) if backup is not None else "",
        "mode": mode,
        "changed_node_ids": list(verified_ids),
        "before_sha256": report.sha256,
        "after_sha256": after_hash,
    }
    provenance = {
        "schema_version": QUALITY_SCHEMA_VERSION,
        "generated_utc": _utc_text(),
        "operation": "radius-only SWC healing",
        "audit": report.to_dict(),
        "result": result_without_report,
        "verification": {
            "node_count_unchanged": True,
            "topology_and_geometry_unchanged": True,
            "radius_diff_node_ids": list(verified_ids),
        },
    }
    _atomic_write_text(provenance_path, json.dumps(provenance, indent=2, sort_keys=True) + "\n")
    return HealingResult(
        input_path=str(source),
        output_path=str(output),
        backup_path=str(backup) if backup is not None else "",
        report_path=str(provenance_path),
        mode=mode,
        changed_node_ids=tuple(verified_ids),
        before_sha256=report.sha256,
        after_sha256=after_hash,
    )


def decision_path(report: SwcQualityReport) -> Path | None:
    return Path(report.manifest).parent / "swc-quality-decisions.json" if report.manifest else None


def reviewed_hashes(report: SwcQualityReport) -> set[str]:
    path = decision_path(report)
    if path is None or not path.is_file():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return set()
    records = payload.get("decisions", [])
    if not isinstance(records, list):
        return set()
    hashes: set[str] = set()
    for record in records:
        if not isinstance(record, Mapping):
            continue
        if record.get("source_sha256"):
            hashes.add(str(record["source_sha256"]))
        result = record.get("result")
        if isinstance(result, Mapping) and result.get("after_sha256"):
            hashes.add(str(result["after_sha256"]))
    return hashes


def record_review_decision(
    report: SwcQualityReport,
    *,
    action: str,
    healing_result: HealingResult | None = None,
) -> Path | None:
    """Remember an explicit UI choice so the same immutable import is not nagged twice."""

    if action not in {"keep", "copy", "overwrite"}:
        raise ValueError("unsupported SWC quality review action")
    path = decision_path(report)
    if path is None:
        return None
    payload: dict[str, Any] = {
        "schema_version": QUALITY_SCHEMA_VERSION,
        "decisions": [],
    }
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and isinstance(loaded.get("decisions"), list):
                payload = loaded
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    decisions = [
        record
        for record in payload["decisions"]
        if not (isinstance(record, Mapping) and record.get("source_sha256") == report.sha256)
    ]
    decisions.append(
        {
            "reviewed_utc": _utc_text(),
            "source_path": report.path,
            "source_sha256": report.sha256,
            "action": action,
            "finding_count": len(report.findings),
            "result": healing_result.to_dict() if healing_result is not None else None,
        }
    )
    payload["decisions"] = decisions
    _atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return path
