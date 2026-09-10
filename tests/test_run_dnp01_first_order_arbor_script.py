from __future__ import annotations

from pathlib import Path

import pytest

from digifly_app.core.resource_profile import make_default_profile
from scripts import run_dnp01_first_order_arbor as stress_run


def _executable(path: Path) -> Path:
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_stress_run_discovers_machine_paths_from_resource_profile(tmp_path: Path):
    public_root = tmp_path / "legacy-workspace"
    metadata = public_root / "Phase 2" / "data" / "all_neurons_neuroncriteria_template.csv"
    metadata.parent.mkdir(parents=True)
    metadata.write_text("bodyId,type,prefix\n10000,DNp01,DN\n", encoding="utf-8")

    manc_root = tmp_path / "full-manc" / "export_swc"
    edge_db = manc_root / "edges" / "master_edges_cache.sqlite"
    edge_db.parent.mkdir(parents=True)
    edge_db.touch()
    (manc_root / "DN").mkdir()

    runtime = _executable(tmp_path / "arbor-python")
    output_root = tmp_path / "workstation" / "runs"
    managed_root = tmp_path / "workstation" / "data"
    profile = make_default_profile(
        workspace_root=public_root,
        output_root=output_root,
        managed_data_root=managed_root,
        arbor_runtime=runtime,
        morphology_sources=(("full-manc", manc_root, "MANC full"),),
    )
    profile_path = profile.save(tmp_path / "resources-v2.json")

    paths = stress_run._resolve_paths(
        stress_run._parse_args(("--profile", str(profile_path)))
    )

    assert paths.edge_root == manc_root.resolve()
    assert paths.morphology_root == manc_root.resolve()
    assert paths.metadata == metadata.resolve()
    assert paths.arbor_runtime == runtime.absolute()
    assert paths.output_root == output_root.resolve()
    assert paths.managed_data_root == managed_root.resolve()
    assert paths.digifly_public_root == public_root.resolve()
    assert paths.profile_path == profile_path.resolve()


def test_stress_run_cli_paths_override_profile_sources(tmp_path: Path):
    public_root = tmp_path / "legacy-workspace"
    public_root.mkdir()
    registered_root = tmp_path / "registered-manc"
    registered_edge_db = registered_root / "edges" / "master_edges_cache.sqlite"
    registered_edge_db.parent.mkdir(parents=True)
    registered_edge_db.touch()
    explicit_root = tmp_path / "chosen-manc"
    explicit_edge_db = explicit_root / "edges" / "master_edges_cache.sqlite"
    explicit_edge_db.parent.mkdir(parents=True)
    explicit_edge_db.touch()
    runtime = _executable(tmp_path / "arbor-python")
    profile = make_default_profile(
        workspace_root=public_root,
        output_root=tmp_path / "runs",
        arbor_runtime=runtime,
        morphology_sources=(("registered", registered_root, "Registered"),),
    )
    profile_path = profile.save(tmp_path / "resources-v2.json")

    paths = stress_run._resolve_paths(
        stress_run._parse_args(
            (
                "--profile",
                str(profile_path),
                "--edge-root",
                str(explicit_root),
                "--morphology-root",
                str(explicit_root),
            )
        )
    )

    assert paths.edge_root == explicit_root.resolve()
    assert paths.morphology_root == explicit_root.resolve()


def test_stress_run_reports_how_to_configure_a_missing_edge_archive(tmp_path: Path):
    public_root = tmp_path / "legacy-workspace"
    public_root.mkdir()
    runtime = _executable(tmp_path / "arbor-python")
    profile = make_default_profile(
        workspace_root=public_root,
        output_root=tmp_path / "runs",
        arbor_runtime=runtime,
    )
    profile_path = profile.save(tmp_path / "resources-v2.json")

    with pytest.raises(FileNotFoundError, match="--edge-root") as error:
        stress_run._resolve_paths(
            stress_run._parse_args(("--profile", str(profile_path)))
        )

    assert stress_run.EDGE_ROOT_ENV in str(error.value)
