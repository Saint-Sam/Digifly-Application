"""Adapter regression tests, not user experiment presets or simulator controls."""
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess

import pytest
from PySide6.QtWidgets import QApplication

from digifly_app.core.circuit import ConnectomeRef, CircuitSpec
from digifly_app.core.connectomes import ConnectomeEdgeCatalog
from digifly_app.core.pair_graph import pair_rows, validated_manifest
from digifly_app.core.resource_profile import make_standalone_profile, ResourceProfile, ResourceKind
from digifly_app.ui.pair_graph_import import PairGraphImportDialog
from digifly_app.ui import pair_graph_import as import_ui

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "src/digifly_app/workers/pair_graph_import_worker.py"


@pytest.fixture
def imported(tmp_path):
    reader = os.environ.get("DIGIFLY_TEST_PARQUET_PYTHON")
    if not reader:
        pytest.skip("Set an explicit duckdb data-reader Python for actual Parquet tests")
    source = tmp_path / "pair file.parquet"
    script = """import duckdb, sys
db=duckdb.connect(':memory:')
db.execute('CREATE TABLE pairs(preId BIGINT, preType VARCHAR, preInstance VARCHAR, preNt VARCHAR, postId BIGINT, postType VARCHAR, postInstance VARCHAR, postNt VARCHAR, total_weight BIGINT)')
db.execute("INSERT INTO pairs VALUES (841369754323,'A','a','gaba',10110,'B','b',NULL,6),(10110,'B','b',NULL,841369754323,'A','a','gaba',42)")
db.execute("COPY pairs TO '"+sys.argv[1].replace("'","''")+"' (FORMAT PARQUET)")
"""
    subprocess.run([reader, "-I", "-c", script, str(source)], check=True, capture_output=True, timeout=30)
    dest = tmp_path / "index"
    result = subprocess.run([reader, "-I", str(WORKER), "--source", str(source), "--destination", str(dest)], check=True, capture_output=True, text=True, timeout=30)
    manifest = json.loads(result.stdout)
    ref = ConnectomeRef("manc:unit-fixture", "Unit fixture", str(tmp_path / "SWCs not installed"), "manc:user-supplied",
                       str(source), str(dest / "pairs.sqlite"), manifest["source_sha256"])
    return ref, manifest, reader


def test_pair_index_works_without_any_swcs_and_preserves_64bit_weights(imported):
    ref, manifest, _ = imported
    assert manifest["rows"] == 2 and manifest["total_weight"] == 48
    assert not Path(ref.root).exists()
    rows = pair_rows(ref, "841369754323", "10110")
    assert {row["total_weight"] for row in rows} == {6, 42}
    assert max(row["preId"] for row in rows) == 841369754323
    assert next(row for row in rows if row["preId"] == 841369754323)["postNt"] is None
    assert validated_manifest(ref)["filtering"] == "none; all supplied rows preserved"


def test_changed_graph_never_falls_back_to_native_contact_cache(imported):
    ref, _, _ = imported
    source = Path(ref.pair_graph_path)
    source.write_bytes(source.read_bytes() + b"changed")
    summary = ConnectomeEdgeCatalog(ref).pair_summary("10110", "841369754323").chemical
    assert not summary.available
    assert "changed" in summary.unavailable_reason
    assert "cache" not in summary.source_label


def test_pair_weights_are_not_reported_as_contacts_or_executable_conductance(imported):
    ref, _, _ = imported
    catalog = ConnectomeEdgeCatalog(ref)
    summary = catalog.pair_summary("10110", "841369754323").chemical
    assert summary.a_to_b_weight == 42 and summary.b_to_a_weight == 6
    assert summary.total_count == 2
    assert "Aggregate total_weight" in summary.status_text
    with pytest.raises(ValueError, match="explicit aggregate-weight"):
        catalog.selected_chemical_contacts(("10110", "841369754323"))


def test_saved_binding_roundtrips_without_an_installed_morphology_bundle(imported):
    ref, _, _ = imported
    restored = CircuitSpec.from_dict(CircuitSpec(connectome=ref).to_dict()).connectome
    assert restored == ref
    assert pair_rows(restored, "10110", "12345") == ()


def test_index_cannot_be_overwritten(imported):
    ref, _, reader = imported
    result = subprocess.run([reader, "-I", str(WORKER), "--source", ref.pair_graph_path,
                             "--destination", str(Path(ref.pair_graph_index).parent)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 1
    assert "File exists" in result.stderr
    assert len(pair_rows(ref, "10110", "841369754323")) == 2


def test_no_swc_import_dialog_is_explicit():
    app = QApplication.instance() or QApplication([])
    dialog = PairGraphImportDialog(None, "")
    try:
        assert "no SWC bundle attached" in dialog.source.label
        assert dialog.source.root == ""
        assert dialog.use_button.text() == "Use this Parquet"
    finally:
        dialog.close()
        app.processEvents()


def test_actual_registration_preserves_existing_profile_without_swcs(imported, tmp_path, monkeypatch):
    ref, _, reader = imported
    app = QApplication.instance() or QApplication([])
    profile = make_standalone_profile(workstation_root=tmp_path / "Standalone Windows-like workspace")
    profile_path = profile.save(tmp_path / "existing-resources.json")
    monkeypatch.setattr(import_ui, "load_or_create_default_profile", lambda: (ResourceProfile.load(profile_path), profile_path))
    dialog = PairGraphImportDialog(None, reader)
    try:
        dialog.original_file = Path(ref.pair_graph_path)
        dialog._register(ref)
        restored = ResourceProfile.load(profile_path)
        assert restored.resources[:-1] == profile.resources
        assert restored.resources[-1].kind == ResourceKind.DATA_SOURCE
        assert restored.resources[-1].metadata["morphology_root"] == ""
        assert dialog.registered_source == ref
        assert "SWCs remain separate" in dialog.status.text()
    finally:
        dialog.close()
        app.processEvents()
