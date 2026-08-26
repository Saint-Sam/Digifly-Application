from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import sqlite3

from digifly_app.core.circuit import ConnectomeRef
from digifly_app.core.connectomes import ConnectomeEdgeCatalog, pair_connection_summary
from digifly_app.core.workspace import DigiflyWorkspace


def _manc_fixture(tmp_path: Path) -> tuple[DigiflyWorkspace, ConnectomeRef, Path]:
    public = tmp_path / "Digifly Public"
    swc_root = public / "Phase 1" / "manc_v1.2.1" / "export_swc"
    edge_root = swc_root / "edges"
    edge_root.mkdir(parents=True)
    database = edge_root / "master_edges_cache.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE edges (pre_id INTEGER NOT NULL, post_id INTEGER NOT NULL, weight_uS REAL)"
        )
        connection.execute("CREATE INDEX idx_edges_pre_post ON edges(pre_id, post_id)")
        connection.executemany(
            "INSERT INTO edges(pre_id, post_id, weight_uS) VALUES (?, ?, ?)",
            [
                (100, 200, 0.1),
                (100, 200, 0.2),
                (200, 100, 0.3),
                (999, 100, 0.4),
            ],
        )
    return (
        DigiflyWorkspace(public),
        ConnectomeRef("manc:v1.2.1", "MANC v1.2.1", str(swc_root), "manc_v1.2.1"),
        database,
    )


def _gap_fixture(
    workspace: DigiflyWorkspace,
    *,
    rows: list[tuple[int, int]],
    selected_ids: tuple[int, ...] = (100, 200, 300),
) -> tuple[Path, Path]:
    bundle = (
        workspace.root
        / "Phase 2_Arbor_staging"
        / "Projects"
        / "Escape-SIZ"
        / "arbor_inputs"
        / "giant_fiber_ablation"
    )
    bundle.mkdir(parents=True)
    gap_csv = bundle / "gap_contacts_arbor.csv"
    with gap_csv.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=("pre_id", "post_id", "g_uS"))
        writer.writeheader()
        for pre_id, post_id in rows:
            writer.writerow({"pre_id": pre_id, "post_id": post_id, "g_uS": 0.001})
    digest = hashlib.sha256(gap_csv.read_bytes()).hexdigest()
    manifest = bundle / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "kind": "gap_contacts",
                        "path": gap_csv.name,
                        "row_count": len(rows),
                        "selected_neuron_ids": list(selected_ids),
                        "sha256": digest,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return gap_csv, manifest


def test_pair_inventory_reports_directional_chemical_and_scoped_gap_counts(tmp_path):
    workspace, source, database = _manc_fixture(tmp_path)
    gap_csv, manifest = _gap_fixture(
        workspace,
        rows=[(100, 200), (100, 200), (200, 100), (100, 300)],
    )
    original_inputs = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in (database, gap_csv, manifest)
    }

    summary = pair_connection_summary(workspace, source, 100, 200)

    assert summary.chemical.status == "present"
    assert summary.chemical.a_to_b_count == 2
    assert summary.chemical.b_to_a_count == 1
    assert summary.chemical.total_count == 3
    assert "A→B" in summary.chemical.status_text
    assert "indexed local" in summary.chemical.source_text
    assert "not a claim" in summary.chemical.scope_note

    assert summary.gap_junction.status == "present"
    assert summary.gap_junction.a_to_b_count == 2
    assert summary.gap_junction.b_to_a_count == 1
    assert summary.gap_junction.total_count == 3
    assert "3 gap-junction contacts" in summary.gap_junction.status_text
    assert "Escape-SIZ" in summary.gap_junction.source_text
    assert "3 selected neurons" in summary.gap_junction.scope_note

    for path, (contents, modified_ns) in original_inputs.items():
        assert path.read_bytes() == contents
        assert path.stat().st_mtime_ns == modified_ns
    assert not (database.parent / f"{database.name}-journal").exists()
    assert not (database.parent / f"{database.name}-wal").exists()


def test_loaded_sources_distinguish_known_zero_from_unavailable(tmp_path):
    workspace, source, _ = _manc_fixture(tmp_path)
    _gap_fixture(workspace, rows=[(100, 200)])
    catalog = ConnectomeEdgeCatalog(source, workspace)

    known_zero = catalog.pair_summary("100", "300")
    assert known_zero.chemical.available
    assert known_zero.chemical.known_zero
    assert known_zero.chemical.status == "known_zero"
    assert known_zero.gap_junction.available
    assert known_zero.gap_junction.known_zero
    assert known_zero.gap_junction.status == "known_zero"
    assert "scoped zero" in known_zero.chemical.status_text

    outside_gap_scope = catalog.pair_summary("100", "400")
    assert outside_gap_scope.chemical.known_zero
    assert not outside_gap_scope.gap_junction.available
    assert outside_gap_scope.gap_junction.status == "unavailable"
    assert not outside_gap_scope.gap_junction.known_zero
    assert "outside" in outside_gap_scope.gap_junction.status_text
    assert "unknown, not zero" in outside_gap_scope.gap_junction.status_text


def test_source_only_catalog_safely_derives_digifly_public_root(tmp_path):
    workspace, source, _ = _manc_fixture(tmp_path)
    gap_csv, _ = _gap_fixture(workspace, rows=[(100, 200)])

    summary = ConnectomeEdgeCatalog(source).pair_summary(100, 200)

    assert summary.chemical.available
    assert summary.gap_junction.available
    assert summary.gap_junction.source_path == str(gap_csv)


def test_custom_source_is_unavailable_without_scanning_arbitrary_edges(tmp_path):
    custom_root = tmp_path / "custom"
    arbitrary = custom_root / "edges" / "master_edges_cache.sqlite"
    arbitrary.parent.mkdir(parents=True)
    with sqlite3.connect(arbitrary) as connection:
        connection.execute("CREATE TABLE edges (pre_id INTEGER, post_id INTEGER)")
        connection.execute("INSERT INTO edges VALUES (100, 200)")
    source = ConnectomeRef("custom:test", "Custom", str(custom_root), "custom")

    summary = ConnectomeEdgeCatalog(source).pair_summary(100, 200)

    assert not summary.chemical.available
    assert summary.chemical.total_count == 0
    assert summary.chemical.status == "unavailable"
    assert "Only the indexed local MANC" in summary.chemical.status_text
    assert not summary.gap_junction.available
    assert "unknown, not zero" in summary.gap_junction.status_text


def test_manifest_mismatch_makes_gap_status_unavailable_not_zero(tmp_path):
    workspace, source, _ = _manc_fixture(tmp_path)
    gap_csv, _ = _gap_fixture(workspace, rows=[])
    gap_csv.write_text("pre_id,post_id,g_uS\n100,200,0.001\n", encoding="utf-8")

    summary = pair_connection_summary(workspace, source, 100, 200)

    assert not summary.gap_junction.available
    assert not summary.gap_junction.known_zero
    assert summary.gap_junction.status == "unavailable"
    assert "SHA-256" in summary.gap_junction.status_text


def test_pair_inventory_rejects_empty_or_duplicate_ids(tmp_path):
    workspace, source, _ = _manc_fixture(tmp_path)
    catalog = ConnectomeEdgeCatalog(source, workspace)

    for first, second in (("", "200"), ("100", ""), ("100", "100")):
        try:
            catalog.pair_summary(first, second)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid neuron pair was accepted")
