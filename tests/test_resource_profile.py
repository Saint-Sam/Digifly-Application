from __future__ import annotations

import json
from pathlib import Path

import pytest

from digifly_app.core.connectomes import discover_connectomes
from digifly_app.core.providers import profile_connectome_sources
from digifly_app.core.resource_profile import (
    AccessMode,
    ResourceBinding,
    ResourceKind,
    ResourceProfile,
    make_default_profile,
    migrate_profile_file,
)
from digifly_app.core.workspace import DigiflyWorkspace
from digifly_app.resource_cli import main as resource_cli_main
from digifly_app.cli import main as doctor_cli_main


def _workspace(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "README.md").write_text("Digifly Public\n", encoding="utf-8")
    return root


def test_resource_profile_round_trip_and_fingerprint(tmp_path: Path):
    workspace = _workspace(tmp_path / "Digifly Public")
    output = tmp_path / "Workstation output"
    profile = make_default_profile(
        workspace_root=workspace,
        output_root=output,
        neuron_runtime=Path("/bin/sh"),
    )
    destination = profile.save(tmp_path / "resources.json")
    restored = ResourceProfile.load(destination)
    assert restored == profile
    assert restored.fingerprint == profile.fingerprint
    assert restored.workspace_root == workspace.resolve()
    assert restored.output_root == output.resolve()
    assert restored.managed_data_root == (tmp_path / "data").resolve()
    assert restored.validate().ok


def test_output_root_cannot_be_inside_a_read_only_dataset(tmp_path: Path):
    workspace = _workspace(tmp_path / "Digifly Public")
    profile = make_default_profile(
        workspace_root=workspace,
        output_root=workspace / "generated",
    )
    report = profile.validate()
    assert not report.ok
    assert any(check.resource_id == "output-boundary" for check in report.checks)


def test_binding_access_is_fixed_by_resource_kind(tmp_path: Path):
    with pytest.raises(ValueError, match="requires read_only access"):
        ResourceBinding(
            "dataset",
            ResourceKind.MORPHOLOGY_SOURCE,
            str(tmp_path),
            AccessMode.READ_WRITE,
        )


def test_profile_rejects_ambiguous_runtime_bindings(tmp_path: Path):
    workspace = _workspace(tmp_path / "Digifly Public")
    base = make_default_profile(workspace_root=workspace, output_root=tmp_path / "output")
    duplicate_runtimes = (
        *base.resources,
        ResourceBinding(
            "neuron-a",
            ResourceKind.NEURON_RUNTIME,
            "/bin/sh",
            AccessMode.EXECUTABLE,
        ),
        ResourceBinding(
            "neuron-b",
            ResourceKind.NEURON_RUNTIME,
            "/bin/sh",
            AccessMode.EXECUTABLE,
        ),
    )
    with pytest.raises(ValueError, match="at most one neuron_runtime"):
        ResourceProfile("ambiguous", duplicate_runtimes)


def test_profile_provider_registers_external_morphologies_without_copying(tmp_path: Path):
    workspace = _workspace(tmp_path / "Digifly Public")
    output = tmp_path / "output"
    external = tmp_path / "huge-external-morphologies"
    external.mkdir()
    profile = make_default_profile(
        workspace_root=workspace,
        output_root=output,
        morphology_sources=(("external-swcs", external, "External SWCs"),),
    )
    provider_sources = profile_connectome_sources(profile)
    assert len(provider_sources) == 1
    discovered = discover_connectomes(workspace, external_sources=provider_sources)
    assert discovered[0].key == "profile:external-swcs"
    assert Path(discovered[0].root) == external.resolve()


def test_resource_cli_creates_and_validates_profile(tmp_path: Path, capsys):
    workspace = _workspace(tmp_path / "Digifly Public")
    output = tmp_path / "runs"
    profile_path = tmp_path / "resources.json"
    assert resource_cli_main(
        [
            "init",
            "--profile",
            str(profile_path),
            "--workspace",
            str(workspace),
            "--output",
            str(output),
            "--neuron-python",
            "/bin/sh",
            "--json",
        ]
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["validation"]["ok"] is True
    assert resource_cli_main(
        ["validate", "--profile", str(profile_path), "--json"]
    ) == 0
    validated = json.loads(capsys.readouterr().out)
    assert validated["ok"] is True


def test_v1_profile_migrates_without_changing_existing_bindings(tmp_path: Path):
    workspace = _workspace(tmp_path / "Digifly Public")
    output = tmp_path / "runs"
    source = tmp_path / "resources-v1.json"
    source.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "profile_id": "legacy",
                "label": "Legacy profile",
                "resources": [
                    {
                        "resource_id": "digifly-public",
                        "kind": "digifly_workspace",
                        "path": str(workspace),
                        "access": "read_only",
                        "label": "Digifly Public",
                        "required": True,
                        "metadata": {},
                    },
                    {
                        "resource_id": "workstation-output",
                        "kind": "output_root",
                        "path": str(output),
                        "access": "read_write",
                        "label": "Output",
                        "required": True,
                        "metadata": {},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    profile = ResourceProfile.load(source)

    assert profile.schema_version == 2
    assert profile.output_root == output.resolve()
    assert profile.managed_data_root == (tmp_path / "data").resolve()
    assert [binding.resource_id for binding in profile.resources[:2]] == [
        "digifly-public",
        "workstation-output",
    ]
    destination = migrate_profile_file(source)
    assert destination.name == "resources-v2.json"
    assert source.is_file()
    assert json.loads(source.read_text(encoding="utf-8"))["schema_version"] == 1
    assert json.loads(destination.read_text(encoding="utf-8"))["schema_version"] == 2


def test_resource_cli_migrates_v1_profile(tmp_path: Path, capsys):
    workspace = _workspace(tmp_path / "Digifly Public")
    v2_profile = make_default_profile(workspace_root=workspace, output_root=tmp_path / "runs")
    payload = v2_profile.to_dict()
    payload["schema_version"] = 1
    payload["resources"] = [
        item for item in payload["resources"] if item["kind"] != "managed_data_root"
    ]
    source = tmp_path / "resources-v1.json"
    source.write_text(json.dumps(payload), encoding="utf-8")

    assert resource_cli_main(["migrate", "--profile", str(source), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert Path(result["profile"]).name == "resources-v2.json"
    assert result["validation"]["ok"] is True


def test_doctor_plan_refuses_an_invalid_profile_boundary(tmp_path: Path, capsys):
    workspace = _workspace(tmp_path / "Digifly Public")
    profile = make_default_profile(
        workspace_root=workspace,
        output_root=workspace / "unsafe-output",
    )
    profile_path = profile.save(tmp_path / "unsafe-resources.json")
    assert doctor_cli_main(["plan", "--profile", str(profile_path)]) == 2
    assert "Invalid resource profile [output-boundary]" in capsys.readouterr().err


def test_doctor_is_generic_and_requires_no_escape_siz_cache(
    tmp_path: Path, capsys, monkeypatch
):
    workspace = _workspace(tmp_path / "Digifly Public")
    profile = make_default_profile(
        workspace_root=workspace,
        output_root=tmp_path / "Workstation output",
    )
    profile_path = profile.save(tmp_path / "resources.json")
    monkeypatch.setattr(
        "digifly_app.cli._qt_probe",
        lambda: {"ok": True, "detail": "test Qt", "python": "/test/python"},
    )
    monkeypatch.setattr(DigiflyWorkspace, "probe_engines", lambda self, _python: ())

    assert doctor_cli_main(["doctor", "--profile", str(profile_path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["workspace_validation"]["ok"] is True
    assert payload["configured_runtime_validation"] == {"ok": True, "required": []}
    assert "escape_siz" not in payload
    assert "cache" not in json.dumps(payload).lower()
