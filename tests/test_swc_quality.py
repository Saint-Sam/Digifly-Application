from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest

from digifly_app.core.resource_profile import (
    AccessMode,
    ResourceBinding,
    ResourceKind,
    ResourceProfile,
)
from digifly_app.core.swc_quality import (
    ADAPTIVE_RADIUS_RULE_ID,
    MINIMUM_ADAPTIVE_REPLACEMENT_FACTOR,
    analyze_swc,
    heal_swc,
    record_review_decision,
    reviewed_hashes,
    scan_recent_imports,
)


def _write_adaptive_fixture(path: Path, *, raw_nm: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    coordinate_scale = 1000 if raw_nm else 1
    radius_scale = 1000 if raw_nm else 1
    rows = []
    for index in range(1, 13):
        radius_um = 0.01 if index == 6 else 1.0
        rows.append(
            f"{index} 2 {(index - 1) * coordinate_scale} 0 0 "
            f"{radius_um * radius_scale} {index - 1 if index > 1 else -1}\n"
        )
    path.write_text("# adaptive fixture\n" + "".join(rows), encoding="utf-8")
    return path


def _shape(path: Path) -> list[tuple[str, ...]]:
    return [
        tuple(field for index, field in enumerate(line.split()) if index != 5)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]


def test_adaptive_checker_uses_each_swc_scale_and_topology(tmp_path: Path) -> None:
    micrometre = _write_adaptive_fixture(tmp_path / "cell.swc")
    nanometre = _write_adaptive_fixture(tmp_path / "cell_neuprint_raw.swc", raw_nm=True)

    local_report = analyze_swc(micrometre)
    raw_report = analyze_swc(nanometre, provider="neuprint")

    for report in (local_report, raw_report):
        assert report.can_heal is True
        assert [finding.node_id for finding in report.findings] == [6]
        assert report.findings[0].rule_id == ADAPTIVE_RADIUS_RULE_ID
        assert report.findings[0].radius_um == pytest.approx(0.01)
        assert report.findings[0].suggested_radius_um == pytest.approx(1.0)
    assert local_report.source_unit == "um"
    assert raw_report.source_unit == "nm"
    assert (
        raw_report.findings[0].suggested_radius_um
        / raw_report.findings[0].radius_um
        >= MINIMUM_ADAPTIVE_REPLACEMENT_FACTOR
    )


def test_adaptive_checker_does_not_force_a_flag_for_mild_taper(tmp_path: Path) -> None:
    source = tmp_path / "mild-taper.swc"
    radii = (1.0, 1.0, 1.0, 0.6, 0.55, 0.5, 0.55, 0.6, 1.0, 1.0, 1.0, 1.0)
    source.write_text(
        "".join(
            f"{index} 2 {index - 1} 0 0 {radius} "
            f"{index - 1 if index > 1 else -1}\n"
            for index, radius in enumerate(radii, 1)
        ),
        encoding="utf-8",
    )

    report = analyze_swc(source)

    assert report.errors == ()
    assert report.findings == ()
    assert report.needs_review is False


def test_copy_heal_leaves_original_and_changes_radius_only(tmp_path: Path) -> None:
    source = _write_adaptive_fixture(tmp_path / "10002_neuprint_raw.swc", raw_nm=True)
    before_bytes = source.read_bytes()
    before_shape = _shape(source)
    report = analyze_swc(source, provider="neuprint")

    result = heal_swc(report, mode="copy")
    output = Path(result.output_path)

    assert output.name == "10002_neuprint_raw_radius_healed.swc"
    assert source.read_bytes() == before_bytes
    assert _shape(output) == before_shape
    assert result.changed_node_ids == (6,)
    assert result.backup_path == ""
    assert Path(result.report_path).is_file()
    assert result.before_sha256 == hashlib.sha256(before_bytes).hexdigest()
    assert result.after_sha256 == hashlib.sha256(output.read_bytes()).hexdigest()


def test_overwrite_heal_keeps_name_and_creates_recoverable_backup(tmp_path: Path) -> None:
    source = _write_adaptive_fixture(tmp_path / "cell.swc")
    before_bytes = source.read_bytes()
    before_shape = _shape(source)
    report = analyze_swc(source)

    result = heal_swc(report, mode="overwrite")
    backup = Path(result.backup_path)

    assert result.output_path == str(source.resolve())
    assert backup.parent.name == ".digifly-backups"
    assert backup.read_bytes() == before_bytes
    assert _shape(source) == before_shape
    assert source.read_bytes() != before_bytes
    provenance = json.loads(Path(result.report_path).read_text(encoding="utf-8"))
    assert provenance["verification"]["topology_and_geometry_unchanged"] is True
    assert provenance["verification"]["radius_diff_node_ids"] == [6]


def test_structural_error_blocks_automatic_healing(tmp_path: Path) -> None:
    source = tmp_path / "broken.swc"
    source.write_text("1 1 0 0 0 1 -1\n2 2 1 0 0 0 99\n", encoding="utf-8")
    report = analyze_swc(source)
    assert report.errors
    assert report.can_heal is False
    with pytest.raises(ValueError, match="no safe"):
        heal_swc(report)


def test_recent_scan_is_bounded_by_manifests_and_records_decisions(tmp_path: Path) -> None:
    workspace = tmp_path / "public"
    output = tmp_path / "runs"
    bundle = tmp_path / "managed" / "snapshot"
    workspace.mkdir()
    output.mkdir()
    source = _write_adaptive_fixture(
        bundle / "export_swc" / "DN" / "DNp01" / "42" / "42_neuprint_raw.swc",
        raw_nm=True,
    )
    relative = source.relative_to(bundle).as_posix()
    manifest = bundle / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "provider": "neuprint",
                "dataset": "test:v1",
                "fetched_at": "2026-08-27T12:00:00Z",
                "files": [
                    {
                        "path": relative,
                        "body_id": 42,
                        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    unrelated = _write_adaptive_fixture(tmp_path / "managed" / "not-in-manifest.swc")
    profile = ResourceProfile(
        profile_id="quality-test",
        resources=(
            ResourceBinding(
                "workspace",
                ResourceKind.DIGIFLY_WORKSPACE,
                str(workspace),
                AccessMode.READ_ONLY,
            ),
            ResourceBinding(
                "output",
                ResourceKind.OUTPUT_ROOT,
                str(output),
                AccessMode.READ_WRITE,
            ),
            ResourceBinding(
                "managed-data",
                ResourceKind.MANAGED_DATA_ROOT,
                str(tmp_path / "managed"),
                AccessMode.READ_WRITE,
            ),
            ResourceBinding(
                "recent-import",
                ResourceKind.MORPHOLOGY_SOURCE,
                str(bundle / "export_swc"),
                AccessMode.READ_ONLY,
                metadata={"manifest": str(manifest), "provider": "neuprint"},
                required=False,
            ),
        ),
    )

    batch = scan_recent_imports(
        profile,
        recent_days=30,
        now=datetime(2026, 8, 27, 13, tzinfo=timezone.utc),
    )

    assert [report.path for report in batch.reports] == [str(source.resolve())]
    assert str(unrelated.resolve()) not in [report.path for report in batch.reports]
    report = batch.reports[0]
    healing_result = heal_swc(report, mode="copy")
    decision = record_review_decision(
        report, action="copy", healing_result=healing_result
    )
    assert decision == bundle / "swc-quality-decisions.json"
    assert reviewed_hashes(report) == {report.sha256, healing_result.after_sha256}
