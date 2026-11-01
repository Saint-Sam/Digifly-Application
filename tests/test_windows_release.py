from pathlib import Path
import importlib.util
import io
import re
import urllib.request


ROOT = Path(__file__).resolve().parents[1]


def test_windows_native_workflow_tests_are_in_frozen_snapshot(tmp_path) -> None:
    spec = importlib.util.spec_from_file_location("stage_windows_source_check", ROOT / "scripts/stage_windows_source.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    staged = tmp_path / "release-inputs"
    module.stage(ROOT, staged, release_snapshot=True)
    workflow = (staged / ".github/workflows/windows-neuron-bundle.yml").read_text("utf-8")
    test_paths = set(re.findall(r"tests/[A-Za-z0-9_]+\.py", workflow))
    assert len(test_paths) >= 30
    missing = sorted(name for name in test_paths if not (staged / name).is_file())
    assert not missing, f"Native Windows workflow tests absent from frozen input: {missing}"


def test_windows_build_packages_and_asserts_simulator_workers() -> None:
    script = (ROOT / "scripts" / "build_windows.ps1").read_text("utf-8")
    assert 'Get-ChildItem "src\\digifly_app\\workers\\*.py"' in script
    for worker in (
        "generic_experiment_worker.py",
        "arbor_escape_siz_worker.py",
        "bmtk_bionet_worker.py",
        "escape_siz_worker.py",
    ):
        assert worker in script
    assert "Packaged simulator worker is missing" in script


def test_notice_inventory_token_is_origin_path_and_redirect_scoped(monkeypatch) -> None:
    spec = importlib.util.spec_from_file_location("notice_fetch", ROOT / "scripts/collect_windows_notices.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv("GH_TOKEN", "test-only-short-lived-value")
    requests = []
    def open_fixture(request, **kwargs):
        requests.append(request)
        return io.BytesIO(b"notice")
    monkeypatch.setattr(module.urllib.request, "urlopen", open_fixture)
    allowed = "https://api.github.com/repos/qt/qtbase/git/trees/v6.10.3?recursive=1"
    assert module.fetch(allowed) == b"notice"
    request = requests[-1]
    assert request.get_header("Authorization") == "Bearer test-only-short-lived-value"
    redirected = urllib.request.HTTPRedirectHandler().redirect_request(
        request, None, 302, "Found", {}, "https://raw.githubusercontent.com/notice")
    assert redirected.get_header("Authorization") is None
    for url in (
        "https://raw.githubusercontent.com/qt/qtbase/v6.10.3/LICENSES/LGPL-3.0-only.txt",
        "https://api.github.com/repos/other/repo/git/trees/v6.10.3?recursive=1",
        "https://api.github.com.evil.test/repos/qt/qtbase/git/trees/v6.10.3?recursive=1",
        "http://api.github.com/repos/qt/qtbase/git/trees/v6.10.3?recursive=1",
        "https://api.github.com/repos/qt/qtbase/git/trees/v6.10.3?recursive=2",
    ):
        module.fetch(url)
        assert requests[-1].get_header("Authorization") is None
    monkeypatch.delenv("GH_TOKEN")
    assert module.fetch(allowed) == b"notice"
    assert requests[-1].get_header("Authorization") is None
