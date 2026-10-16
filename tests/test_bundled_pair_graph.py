"""Exact user-selected graph exception; no blanket dataset packaging waiver."""
import json
import os
from pathlib import Path

import pytest

from digifly_app.core import bundled_pair_graph as graph
from digifly_app.core.resource_profile import make_standalone_profile
from digifly_app.packaging.audit import audit_artifact


@pytest.fixture
def supplied():
    value = os.environ.get("DIGIFLY_TEST_MANC_PARQUET")
    if not value:
        pytest.skip("Explicit exact user graph path required")
    return graph.verify_pair_file(Path(value))


def test_wrong_file_is_not_accepted_under_pinned_name(tmp_path):
    root = tmp_path / "artifact"
    target = root / graph.PAIR_PATH
    target.parent.mkdir(parents=True)
    target.write_bytes(b"not the user's graph")
    with pytest.raises(ValueError, match="wrong size"):
        graph.verify_pair_file(target)
    report = audit_artifact(root)
    assert not report.ok
    assert {item.code for item in report.issues} >= {"scientific_dataset", "invalid_bundled_pair_graph"}


def test_pinned_graph_is_bundled_with_notice_and_installed_externally(supplied, tmp_path, monkeypatch):
    bundle = tmp_path / "artifact"
    staged = graph.stage_pair_file(supplied, bundle)
    assert staged.read_bytes() == supplied.read_bytes()
    assert json.loads((bundle / graph.PAIR_NOTICE).read_text()) == graph.NOTICE
    assert audit_artifact(bundle).ok
    from digifly_app.core import paths
    monkeypatch.setattr(paths, "resource_path", lambda *parts: bundle.joinpath(*parts))
    assert graph.included_pair_path() == staged
    profile = make_standalone_profile(workstation_root=tmp_path / "workstation")
    external = graph.install_included_pair(profile, staged)
    assert external.is_relative_to(profile.managed_data_root)
    assert not external.is_relative_to(bundle)
    assert graph.install_included_pair(profile, staged) == external
    with pytest.raises(FileExistsError):
        graph.stage_pair_file(supplied, bundle)
    # No generic permission for another data export or a falsely relabelled file.
    (bundle / "other.parquet").write_bytes(b"private other data")
    assert not audit_artifact(bundle).ok


def test_missing_or_changed_notice_fails_packaging(supplied, tmp_path):
    graph.stage_pair_file(supplied, tmp_path)
    (tmp_path / graph.PAIR_NOTICE).write_text('{"dataset":"Male CNS 1.0"}')
    assert not audit_artifact(tmp_path).ok


def test_existing_external_copy_is_never_replaced(supplied, tmp_path):
    profile = make_standalone_profile(workstation_root=tmp_path / "workstation")
    target = graph.install_included_pair(profile, supplied)
    target.write_bytes(b"conflicting original")
    with pytest.raises(ValueError):
        graph.install_included_pair(profile, supplied)
    assert target.read_bytes() == b"conflicting original"
