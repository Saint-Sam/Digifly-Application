from __future__ import annotations

from pathlib import Path

from digifly_app.core.paths import resource_path, resource_root, worker_path
from digifly_app.packaging.audit import audit_artifact


def test_declared_application_resources_resolve_from_the_active_installation():
    root = resource_root()
    assert (root / "docs" / "ARCHITECTURE.md").is_file()
    assert resource_path("schemas", "digifly-project-v1.schema.json").is_file()
    assert resource_path("mechanisms", "augustin_2019", "nat.mod").is_file()
    assert resource_path(
        "mechanisms", "arbor_gap_junctions", "source_manifest.json"
    ).is_file()
    assert worker_path("escape_siz_worker.py").is_file()


def test_resource_paths_reject_absolute_and_parent_traversal():
    for unsafe in (("/tmp/data",), ("..", "data")):
        try:
            resource_path(*unsafe)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Unsafe application resource path was accepted: {unsafe}")


def test_artifact_audit_accepts_code_and_rejects_scientific_data(tmp_path: Path):
    clean = tmp_path / "clean"
    clean.mkdir()
    (clean / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    assert audit_artifact(clean).ok

    contaminated = tmp_path / "contaminated"
    contaminated.mkdir()
    (contaminated / "manc.swc").write_text("1 1 0 0 0 1 -1\n", encoding="utf-8")
    report = audit_artifact(contaminated)
    assert not report.ok
    assert {issue.code for issue in report.issues} == {"scientific_dataset"}


def test_artifact_audit_rejects_developer_machine_paths(tmp_path: Path):
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    machine_path = "/" + "Users/example/Desktop/Digifly Public"
    (artifact / "settings.json").write_text(
        '{"workspace": "' + machine_path + '"}\n',
        encoding="utf-8",
    )
    report = audit_artifact(artifact)
    assert not report.ok
    assert any(issue.code == "developer_machine_path" for issue in report.issues)
