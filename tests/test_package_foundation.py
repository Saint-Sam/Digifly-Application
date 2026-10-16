from __future__ import annotations


def test_extensionless_native_framework_is_not_mistaken_for_text_binding(tmp_path):
    from digifly_app.packaging.audit import audit_artifact
    app = tmp_path / "Tester.app"
    binary = app / "Contents/MacOS/QtTest"
    binary.parent.mkdir(parents=True)
    # A synthetic header exercises classification only, not real executable QA.
    binary.write_bytes(b"\xcf\xfa\xed\xfe" + b"upstream build source /Users/upstream/qt/source")
    assert not any(item.code == "developer_machine_path" for item in audit_artifact(app).issues)
    text = app / "Contents/MacOS/settings.json"
    text.write_text('{"workspace":"/Users/developer/private-data"}')
    assert any(item.code == "developer_machine_path" for item in audit_artifact(app).issues)

from io import BytesIO
from pathlib import Path
import hashlib
import tomllib
import tarfile
import zipfile

import pytest

from digifly_app.core.paths import resource_path, resource_root, worker_path
from digifly_app.packaging.audit import audit_artifact, PROJECT_LICENSE_SHA256


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _current_release_payload(*, archive_root: str, wheel: bool) -> dict[str, bytes]:
    sources = {
        "digifly_app/workers/pair_graph_import_worker.py": PROJECT_ROOT / "src/digifly_app/workers/pair_graph_import_worker.py",
        "digifly_app/workers/arbor_channel_translation.py": PROJECT_ROOT / "src/digifly_app/workers/arbor_channel_translation.py",
        "mechanisms/augustin_2019/nat.mod": PROJECT_ROOT / "mechanisms/augustin_2019/nat.mod",
        "mechanisms/augustin_2019/nap.mod": PROJECT_ROOT / "mechanisms/augustin_2019/nap.mod",
        "mechanisms/augustin_2019/k.mod": PROJECT_ROOT / "mechanisms/augustin_2019/k.mod",
        "LICENSE": PROJECT_ROOT / "LICENSE",
        "THIRD_PARTY_NOTICES.md": PROJECT_ROOT / "THIRD_PARTY_NOTICES.md",
        "digifly_app/workers/generic_experiment_worker.py": (
            PROJECT_ROOT / "src/digifly_app/workers/generic_experiment_worker.py"
        ),
        "digifly_app/workers/independent_trials.py": (
            PROJECT_ROOT / "src/digifly_app/workers/independent_trials.py"
        ),
        "digifly_app/workers/bmtk_bionet_worker.py": (
            PROJECT_ROOT / "src/digifly_app/workers/bmtk_bionet_worker.py"
        ),
        "digifly_app/workers/pdcm_netpyne_worker.py": (
            PROJECT_ROOT / "src/digifly_app/workers/pdcm_netpyne_worker.py"
        ),
        "digifly_app/workers/netpyne_workbench_worker.py": (
            PROJECT_ROOT / "src/digifly_app/workers/netpyne_workbench_worker.py"
        ),
        "digifly_app/workers/pdcm_netpyne_mpi_worker.py": (
            PROJECT_ROOT / "src/digifly_app/workers/pdcm_netpyne_mpi_worker.py"
        ),
        "scripts/run_pdcm_athene.sh": (
            PROJECT_ROOT / "scripts/run_pdcm_athene.sh"
        ),
        "scripts/install_pdcm_athene_layer.sh": (
            PROJECT_ROOT / "scripts/install_pdcm_athene_layer.sh"
        ),
        "scripts/probe_neuron_mpi.py": (
            PROJECT_ROOT / "scripts/probe_neuron_mpi.py"
        ),
        "docs/PDCM_ATHENE_RUNBOOK.md": (
            PROJECT_ROOT / "docs/PDCM_ATHENE_RUNBOOK.md"
        ),
        "docs/NETPYNE_WORKBENCH_PLAN.md": (
            PROJECT_ROOT / "docs/NETPYNE_WORKBENCH_PLAN.md"
        ),
        "mechanisms/neuron_gap_junctions/Gap.mod": (
            PROJECT_ROOT / "mechanisms/neuron_gap_junctions/Gap.mod"
        ),
        "mechanisms/neuron_gap_junctions/RectGap.mod": (
            PROJECT_ROOT / "mechanisms/neuron_gap_junctions/RectGap.mod"
        ),
        "mechanisms/neuron_gap_junctions/HeteroRectGap.mod": (
            PROJECT_ROOT / "mechanisms/neuron_gap_junctions/HeteroRectGap.mod"
        ),
        "mechanisms/neuron_gap_junctions/source_manifest.json": (
            PROJECT_ROOT / "mechanisms/neuron_gap_junctions/source_manifest.json"
        ),
        "scripts/build_neuron_gap_mechanisms.py": (
            PROJECT_ROOT / "scripts/build_neuron_gap_mechanisms.py"
        ),
    }
    payload: dict[str, bytes] = {}
    sources["digifly_app/workers/pdcm_bionet_app_worker.py"] = PROJECT_ROOT / "src/digifly_app/workers/pdcm_bionet_app_worker.py"
    for name in ("pdcm_bionet_scalable_app_worker.py", "pdcm_bionet_5pct_app_worker.py"):
        sources[f"digifly_app/workers/{name}"] = PROJECT_ROOT / "src/digifly_app/workers" / name
    sources["digifly_app/core/pdcm_bionet_5pct_app.py"] = PROJECT_ROOT / "src/digifly_app/core/pdcm_bionet_5pct_app.py"
    # Native releases expose this source as a standalone converter; wheels keep
    # the same data-file layout in addition to the ordinary core module.
    sources["digifly_app/core/pdcm_campaign_results.py"] = PROJECT_ROOT / "src/digifly_app/core/pdcm_campaign_results.py"
    for version in ("primary_v3", "primary_v4"):
        frozen = PROJECT_ROOT / "src/digifly_app/pdcm_bionet" / version
        for source in frozen.rglob("*"):
            if source.is_file() and "__pycache__" not in source.parts:
                sources[f"digifly_app/pdcm_bionet/{version}/{source.relative_to(frozen)}"] = source
    for relative, source in sources.items():
        if wheel and relative in {"LICENSE", "THIRD_PARTY_NOTICES.md"}:
            destination = f"{archive_root}.data/data/share/digifly-workstation/{relative}"
        elif wheel and relative.startswith(("docs/", "mechanisms/", "scripts/")):
            destination = (
                f"{archive_root}.data/data/share/digifly-workstation/{relative}"
            )
        elif wheel:
            destination = relative
        elif relative.startswith("digifly_app/"):
            destination = f"{archive_root}/src/{relative}"
        else:
            destination = f"{archive_root}/{relative}"
        payload[destination] = source.read_bytes()
    return payload


def _write_zip(path: Path, payload: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in payload.items():
            archive.writestr(name, data)


def _write_tar(path: Path, payload: dict[str, bytes]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for name, data in payload.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, BytesIO(data))


def test_declared_application_resources_resolve_from_the_active_installation():
    root = resource_root()
    assert (root / "docs" / "ARCHITECTURE.md").is_file()
    assert (root / "docs" / "BACKEND_CONTRACT_V1.md").is_file()
    assert resource_path("schemas", "digifly-project-v1.schema.json").is_file()
    assert resource_path("mechanisms", "augustin_2019", "nat.mod").is_file()
    assert resource_path(
        "mechanisms", "arbor_gap_junctions", "source_manifest.json"
    ).is_file()
    assert worker_path("escape_siz_worker.py").is_file()
    assert worker_path("generic_experiment_worker.py").is_file()
    assert worker_path("bmtk_bionet_worker.py").is_file()
    assert worker_path("pdcm_netpyne_worker.py").is_file()
    assert worker_path("netpyne_workbench_worker.py").is_file()
    assert worker_path("pdcm_netpyne_mpi_worker.py").is_file()


def test_macos_bundle_explicitly_includes_bmtk_bionet_worker():
    deploy_spec = (
        Path(__file__).resolve().parents[1] / "pysidedeploy.spec"
    ).read_text(encoding="utf-8")
    assert (
        "--include-data-file=src/digifly_app/workers/bmtk_bionet_worker.py="
        "digifly_app/workers/bmtk_bionet_worker.py"
    ) in deploy_spec
    assert (
        "--include-data-file=src/digifly_app/workers/pdcm_netpyne_worker.py="
        "digifly_app/workers/pdcm_netpyne_worker.py"
    ) in deploy_spec
    assert (
        "--include-data-file=src/digifly_app/workers/netpyne_workbench_worker.py="
        "digifly_app/workers/netpyne_workbench_worker.py"
    ) in deploy_spec
    assert (
        "--include-data-file=src/digifly_app/workers/pdcm_netpyne_mpi_worker.py="
        "digifly_app/workers/pdcm_netpyne_mpi_worker.py"
    ) in deploy_spec
    assert (
        "--include-data-file=scripts/install_pdcm_athene_layer.sh="
        "scripts/install_pdcm_athene_layer.sh"
    ) in deploy_spec
    assert (
        "--include-data-file=scripts/probe_neuron_mpi.py="
        "scripts/probe_neuron_mpi.py"
    ) in deploy_spec
    assert (
        "--include-data-file=docs/NETPYNE_WORKBENCH_PLAN.md="
        "docs/NETPYNE_WORKBENCH_PLAN.md"
    ) in deploy_spec


def test_macos_bundle_explicitly_includes_project_license():
    deploy_spec = (
        Path(__file__).resolve().parents[1] / "pysidedeploy.spec"
    ).read_text(encoding="utf-8")
    assert "--include-data-file=LICENSE=LICENSE" in deploy_spec
    assert "--include-data-file=THIRD_PARTY_NOTICES.md=THIRD_PARTY_NOTICES.md" in deploy_spec


def test_original_code_mit_license_and_metadata_match_release_pin():
    license_bytes = (PROJECT_ROOT / "LICENSE").read_bytes()
    assert hashlib.sha256(license_bytes).hexdigest() == PROJECT_LICENSE_SHA256
    assert license_bytes.startswith(b"MIT License\n\nCopyright (c) 2026 Juan Lopez\n")
    metadata = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    classifiers = metadata["project"]["classifiers"]
    assert "License :: OSI Approved :: MIT License" in classifiers
    assert "Private :: Do Not Upload" not in classifiers
    assert "License :: Other/Proprietary License" not in classifiers
    branding = (PROJECT_ROOT / "src/digifly_app/assets/BRANDING.md").read_text()
    assert "MIT License" in branding
    assert "private development license" not in branding


def test_release_audit_requires_licensing_scope_notice(tmp_path: Path):
    archive_root = "digifly_workstation-0.1.0"
    payload = _current_release_payload(archive_root=archive_root, wheel=True)
    del payload[f"{archive_root}.data/data/share/digifly-workstation/THIRD_PARTY_NOTICES.md"]
    artifact = tmp_path / f"{archive_root}-py3-none-any.whl"
    _write_zip(artifact, payload)
    report = audit_artifact(artifact)
    assert any(issue.code == "missing_release_component" and issue.path == "THIRD_PARTY_NOTICES.md"
               for issue in report.issues)


def test_modified_mit_holder_does_not_pass_release_audit(tmp_path: Path):
    archive_root = "digifly_workstation-0.1.0"
    payload = _current_release_payload(archive_root=archive_root, wheel=True)
    project_license = f"{archive_root}.data/data/share/digifly-workstation/LICENSE"
    payload[project_license] = payload[project_license].replace(b"Juan Lopez", b"Another Holder")
    artifact = tmp_path / f"{archive_root}-py3-none-any.whl"
    _write_zip(artifact, payload)
    report = audit_artifact(artifact)
    assert any(issue.code == "missing_release_component" and issue.path == "LICENSE"
               for issue in report.issues)


def test_macos_build_does_not_stage_scientific_outputs():
    build_script = (PROJECT_ROOT / "scripts/build_macos.sh").read_text(encoding="utf-8")
    assert "--exclude '/outputs/'" in build_script


def test_macos_build_stages_distinct_external_bionet_workers():
    build_script = (PROJECT_ROOT / "scripts/build_macos.sh").read_text(encoding="utf-8")
    for name in ("pdcm_bionet_scalable_app_worker.py", "pdcm_bionet_5pct_app_worker.py"):
        assert f'cp "$stage_root/src/digifly_app/workers/{name}"' in build_script
        assert f'"$stage_bundle/Contents/MacOS/digifly_app/workers/{name}"' in build_script
    assert 'cp "$stage_root/src/digifly_app/core/pdcm_bionet_5pct_app.py"' in build_script
    assert '"$stage_bundle/Contents/MacOS/digifly_app/core/pdcm_bionet_5pct_app.py"' in build_script


@pytest.mark.parametrize("missing", [
    "digifly_app/workers/pdcm_bionet_5pct_app_worker.py",
    "digifly_app/core/pdcm_bionet_5pct_app.py",
    "digifly_app/pdcm_bionet/primary_v4/scripts/run_pdcm_bionet_scalable_v4.py",
    "digifly_app/pdcm_bionet/primary_v4/src/digifly_app/pdcm_bionet/mod/DigiflyPDCMEvent.mod",
])
def test_release_audit_requires_5pct_worker_and_immutable_scalable_sources(tmp_path, missing):
    archive_root = "digifly_workstation-0.1.0"
    payload = _current_release_payload(archive_root=archive_root, wheel=True)
    del payload[missing]
    artifact = tmp_path / f"{archive_root}-py3-none-any.whl"
    _write_zip(artifact, payload)
    report = audit_artifact(artifact)
    assert any(issue.code == "missing_release_component" and issue.path == missing for issue in report.issues)


def test_release_audit_rejects_changed_immutable_scalable_mod(tmp_path):
    archive_root = "digifly_workstation-0.1.0"
    payload = _current_release_payload(archive_root=archive_root, wheel=True)
    source = "digifly_app/pdcm_bionet/primary_v4/src/digifly_app/pdcm_bionet/mod/DigiflyPDCMEvent.mod"
    payload[source] += b"\n: changed frozen source\n"
    artifact = tmp_path / f"{archive_root}-py3-none-any.whl"
    _write_zip(artifact, payload)
    report = audit_artifact(artifact)
    assert any(issue.code == "changed_scientific_source" and issue.path == source for issue in report.issues)


def test_release_metadata_retains_both_immutable_bionet_source_versions():
    metadata = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    package_data = metadata["tool"]["setuptools"]["package-data"]["digifly_app"]
    manifest = (PROJECT_ROOT / "MANIFEST.in").read_text()
    for version in ("primary_v3", "primary_v4"):
        assert f"pdcm_bionet/{version}/src/digifly_app/pdcm_bionet/mod/*.mod" in package_data
        assert f"recursive-include src/digifly_app/pdcm_bionet/{version} *.json" in manifest


def test_macos_bundle_uses_the_digifly_owned_icon():
    deploy_spec = (
        Path(__file__).resolve().parents[1] / "pysidedeploy.spec"
    ).read_text(encoding="utf-8")
    assert "icon = src/digifly_app/assets/digifly_icon.icns" in deploy_spec
    assert "src/digifly_app/assets/digifly_icon.png" in deploy_spec
    assert "pyside_icon.icns" not in deploy_spec


def test_macos_bundle_excludes_qt_virtual_keyboard():
    deploy_spec = (
        Path(__file__).resolve().parents[1] / "pysidedeploy.spec"
    ).read_text(encoding="utf-8")
    plugin_line = next(
        line for line in deploy_spec.splitlines() if line.startswith("plugins =")
    )
    assert "platforminputcontexts" not in plugin_line


def test_macos_build_audits_candidate_before_promoting_it():
    build_script = (
        Path(__file__).resolve().parents[1] / "scripts" / "build_macos.sh"
    ).read_text(encoding="utf-8")
    audit = '"$stage_python" -m digifly_app.packaging.audit "$candidate_bundle"'
    promote = 'mv "$candidate_bundle" "$release_bundle"'
    assert audit in build_script
    assert build_script.index(audit) < build_script.index(promote)


def test_macos_private_build_declares_and_archives_supported_target():
    root = Path(__file__).resolve().parents[1]
    build_script = (root / "scripts" / "build_macos.sh").read_text(encoding="utf-8")
    workflow = (root / ".github" / "workflows" / "macos-private-release.yml").read_text(
        encoding="utf-8"
    )
    assert 'minimum_macos="${DIGIFLY_MINIMUM_MACOS:-14.0}"' in build_script
    assert "MACOSX_DEPLOYMENT_TARGET" in build_script
    assert "LSMinimumSystemVersion" in build_script
    assert 'scripts/archive_macos_app.sh' in build_script
    assert "architecture: arm64" in workflow
    assert "architecture: x86_64" not in workflow
    assert 'DIGIFLY_MINIMUM_MACOS: "14.0"' in workflow


def test_developer_id_lane_signs_and_notarizes_before_promotion():
    build_script = (
        Path(__file__).resolve().parents[1] / "scripts" / "build_macos.sh"
    ).read_text(encoding="utf-8")
    sign = '"$project_dir/scripts/sign_macos_app.sh" "$candidate_bundle"'
    audit = '"$stage_python" -m digifly_app.packaging.audit "$candidate_bundle"'
    notarize = (
        '"$project_dir/scripts/notarize_macos_app.sh" '
        '"$candidate_bundle" "$release_zip"'
    )
    promote = 'mv "$candidate_bundle" "$release_bundle"'
    assert "--developer-id" in build_script
    assert build_script.index(sign) < build_script.index(audit)
    assert build_script.index(audit) < build_script.index(notarize)
    assert build_script.index(notarize) < build_script.index(promote)


def test_developer_id_signing_is_inside_out_and_not_deep():
    sign_script = (
        Path(__file__).resolve().parents[1] / "scripts" / "sign_macos_app.sh"
    ).read_text(encoding="utf-8")
    leaf_sign = '/usr/bin/codesign "${sign_args[@]}" "$code_file"'
    outer_sign = "# Sign the outer bundle last."
    assert "--timestamp" in sign_script
    assert "--options runtime" in sign_script
    assert "--force --deep" not in sign_script
    assert sign_script.index(leaf_sign) < sign_script.index(outer_sign)
    assert "--verify --deep --strict" in sign_script


def test_notarization_uses_keychain_profile_and_archives_after_stapling():
    notary_script = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "notarize_macos_app.sh"
    ).read_text(encoding="utf-8")
    staple = '/usr/bin/xcrun stapler staple "$app_bundle"'
    archive = (
        '/usr/bin/ditto -c -k --sequesterRsrc --keepParent '
        '"$app_bundle" "$candidate_release_zip"'
    )
    assert "--keychain-profile" in notary_script
    assert "--apple-id" not in notary_script
    assert "--password" not in notary_script
    assert notary_script.index(staple) < notary_script.index(archive)
    assert '/usr/bin/unzip -tq "$candidate_release_zip"' in notary_script
    assert notary_script.index(archive) < notary_script.index(
        '/bin/mv "$candidate_release_zip" "$release_zip"'
    )


def test_macos_build_restores_prior_release_after_interrupted_promotion():
    build_script = (
        Path(__file__).resolve().parents[1] / "scripts" / "build_macos.sh"
    ).read_text(encoding="utf-8")
    archive_prior = 'mv "$release_bundle" "$backup_bundle"'
    promote = 'mv "$candidate_bundle" "$release_bundle"'
    commit = "promotion_in_progress=0"
    assert "Restored the previous release after interrupted promotion." in build_script
    assert build_script.index(archive_prior) < build_script.index(promote)
    assert build_script.index(promote) < build_script.rindex(commit)


def test_developer_setup_does_not_require_a_machine_specific_python_path():
    setup_script = (
        Path(__file__).resolve().parents[1] / "scripts" / "setup_dev.sh"
    ).read_text(encoding="utf-8")
    assert 'bootstrap_python="${DIGIFLY_BOOTSTRAP_PYTHON:-}"' in setup_script
    assert "Python 3.11 or newer is required" in setup_script
    assert 'DIGIFLY_BOOTSTRAP_PYTHON:-/opt/anaconda3/bin/python' not in setup_script


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


def test_artifact_audit_rejects_stale_digifly_app_missing_runtime_bridges(
    tmp_path: Path,
):
    app = tmp_path / "Digifly Workstation.app"
    worker_dir = app / "Contents/MacOS/digifly_app/workers"
    worker_dir.mkdir(parents=True)
    (worker_dir / "generic_experiment_worker.py").write_text(
        "# stale worker-only bundle\n", encoding="utf-8"
    )

    report = audit_artifact(app)

    assert not report.ok
    missing = {issue.path for issue in report.issues if issue.code == "missing_release_component"}
    assert "digifly_app/workers/bmtk_bionet_worker.py" in missing
    assert "mechanisms/neuron_gap_junctions/Gap.mod" in missing
    assert "scripts/build_neuron_gap_mechanisms.py" in missing
    assert "scripts/install_pdcm_athene_layer.sh" in missing
    assert "scripts/probe_neuron_mpi.py" in missing
    assert "docs/NETPYNE_WORKBENCH_PLAN.md" in missing


@pytest.mark.parametrize("kind", ["wheel", "sdist"])
def test_artifact_audit_accepts_current_wheel_and_sdist_release_layouts(
    tmp_path: Path,
    kind: str,
):
    archive_root = "digifly_workstation-0.1.0"
    payload = _current_release_payload(archive_root=archive_root, wheel=kind == "wheel")
    if kind == "wheel":
        artifact = tmp_path / f"{archive_root}-py3-none-any.whl"
        _write_zip(artifact, payload)
    else:
        artifact = tmp_path / f"{archive_root}.tar.gz"
        _write_tar(artifact, payload)

    report = audit_artifact(artifact)

    assert report.ok, report.issues


def test_dependency_license_does_not_replace_project_license(tmp_path: Path):
    archive_root = "digifly_workstation-0.1.0"
    payload = _current_release_payload(archive_root=archive_root, wheel=True)
    project_license = (
        f"{archive_root}.data/data/share/digifly-workstation/LICENSE"
    )
    del payload[project_license]
    payload["dependency.dist-info/LICENSE"] = b"A third-party permissive license\n"
    artifact = tmp_path / f"{archive_root}-py3-none-any.whl"
    _write_zip(artifact, payload)

    report = audit_artifact(artifact)

    assert not report.ok
    assert any(
        issue.code == "missing_release_component" and issue.path == "LICENSE"
        for issue in report.issues
    )


@pytest.mark.parametrize("alteration", ["missing", "changed"])
def test_release_audit_blocks_missing_or_changed_bionet_scientific_assets(tmp_path, alteration):
    root = "digifly_workstation-0.1.0"
    payload = _current_release_payload(archive_root=root, wheel=True)
    asset = "digifly_app/pdcm_bionet/primary_v3/scripts/build_pdcm_bionet_primary_v3.py"
    if alteration == "missing":
        del payload[asset]
    else:
        payload[asset] += b"\n# changed scientific model\n"
    artifact = tmp_path / f"{root}-py3-none-any.whl"
    _write_zip(artifact, payload)
    report = audit_artifact(artifact)
    assert not report.ok
    expected = "missing_release_component" if alteration == "missing" else "changed_scientific_source"
    assert any(issue.code == expected and issue.path == asset for issue in report.issues)


def test_truncated_project_license_does_not_pass_release_audit(tmp_path: Path):
    archive_root = "digifly_workstation-0.1.0"
    payload = _current_release_payload(archive_root=archive_root, wheel=True)
    project_license = (
        f"{archive_root}.data/data/share/digifly-workstation/LICENSE"
    )
    payload[project_license] = b"MIT License\n"
    artifact = tmp_path / f"{archive_root}-py3-none-any.whl"
    _write_zip(artifact, payload)

    report = audit_artifact(artifact)

    assert any(
        issue.code == "missing_release_component" and issue.path == "LICENSE"
        for issue in report.issues
    )


def test_release_audit_rejects_qt_virtual_keyboard(tmp_path: Path):
    app = tmp_path / "Digifly Workstation.app"
    plugin = (
        app
        / "Contents/MacOS/PySide6/qt-plugins/platforminputcontexts"
        / "libqtvirtualkeyboardplugin.dylib"
    )
    plugin.parent.mkdir(parents=True)
    plugin.write_bytes(b"Mach-O fixture")

    report = audit_artifact(app)

    assert any(
        issue.code == "incompatible_distribution_component"
        and issue.path.endswith("libqtvirtualkeyboardplugin.dylib")
        for issue in report.issues
    )


@pytest.mark.parametrize("package", ["neuron", "bmtk", "arbor", "h5py", "numpy"])
def test_artifact_audit_rejects_obvious_vendored_simulator_packages(
    tmp_path: Path,
    package: str,
):
    artifact = tmp_path / f"artifact-{package}"
    package_dir = artifact / "site-packages" / package
    package_dir.mkdir(parents=True)
    (package_dir / "__init__.py").write_text("# vendored runtime\n", encoding="utf-8")

    report = audit_artifact(artifact)

    assert not report.ok
    assert any(issue.code == "bundled_simulator_runtime" for issue in report.issues)


def test_app_audit_allows_only_explicit_marked_bundled_arbor(tmp_path: Path, monkeypatch):
    app = tmp_path / "Digifly Workstation.app"
    runtime = app / "Contents/Resources/runtimes/arbor"
    package = runtime / "lib/python3.12/site-packages/arbor"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("# bundled Arbor\n", encoding="utf-8")
    (runtime / "DIGIFLY_RUNTIME.json").write_text(
        '{"component":"Arbor","version":"0.12.2",'
        '"policy":"temporary-bundled-runtime-v1"}\n',
        encoding="utf-8",
    )

    rejected = audit_artifact(app)
    assert any(issue.code == "bundled_simulator_runtime" for issue in rejected.issues)

    monkeypatch.setenv("DIGIFLY_ALLOW_BUNDLED_ARBOR", "1")
    allowed = audit_artifact(app)
    assert not any(issue.code == "bundled_simulator_runtime" for issue in allowed.issues)
    assert not any(issue.code == "invalid_bundled_arbor_runtime" for issue in allowed.issues)


def test_app_audit_rejects_unmarked_bundled_arbor_exception(tmp_path: Path, monkeypatch):
    app = tmp_path / "Digifly Workstation.app"
    package = app / "Contents/Resources/runtimes/arbor/lib/python3.12/site-packages/arbor"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("# bundled Arbor\n", encoding="utf-8")
    monkeypatch.setenv("DIGIFLY_ALLOW_BUNDLED_ARBOR", "1")

    report = audit_artifact(app)

    assert any(issue.code == "invalid_bundled_arbor_runtime" for issue in report.issues)


@pytest.mark.parametrize(
    "filename",
    ("libnrnmech.so", "nrnmech.dll", "digifly_gap-catalogue.dylib"),
)
def test_artifact_audit_rejects_compiled_mechanism_outputs(
    tmp_path: Path,
    filename: str,
):
    artifact = tmp_path / "compiled-mechanisms"
    library = artifact / "runtime-cache" / filename
    library.parent.mkdir(parents=True)
    library.write_bytes(b"compiled simulator mechanism")

    report = audit_artifact(artifact)

    assert not report.ok
    assert any(issue.code == "bundled_simulator_runtime" for issue in report.issues)


def test_artifact_audit_does_not_confuse_owned_bridge_names_with_runtimes(
    tmp_path: Path,
):
    artifact = tmp_path / "owned-bridges"
    workers = artifact / "digifly_app/workers"
    mechanisms = artifact / "mechanisms/neuron_gap_junctions"
    workers.mkdir(parents=True)
    mechanisms.mkdir(parents=True)
    (workers / "bmtk_bionet_worker.py").write_text("# adapter\n", encoding="utf-8")
    (workers / "arbor_gap_bridge.py").write_text("# adapter\n", encoding="utf-8")
    (mechanisms / "README.md").write_text("source resources\n", encoding="utf-8")

    report = audit_artifact(artifact)

    assert report.ok, report.issues
