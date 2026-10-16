"""Offline installation confinement and provenance guards; no fake simulation pass."""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import zipfile
import pytest
from digifly_app.core import windows_neuron_bundle as bundle


def test_runtime_build_locks_complete_wheel_set_and_rejects_tamper(tmp_path, monkeypatch):
    import hashlib
    import importlib.util
    script = Path(__file__).resolve().parents[1] / "scripts/build_windows_neuron_payload.py"
    spec = importlib.util.spec_from_file_location("runtime_builder", script)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    assert len(builder.LOCKED_WHEELS) == 12
    assert "pillow-12.3.0-cp312-cp312-win_amd64.whl" in builder.LOCKED_WHEELS
    data = b"inert wheel guard fixture"
    monkeypatch.setattr(builder, "LOCKED_WHEELS", {"fixture.whl": hashlib.sha256(data).hexdigest()})
    (tmp_path / "fixture.whl").write_bytes(data)
    builder.validate_wheels(tmp_path)
    (tmp_path / "fixture.whl").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="checksum"):
        builder.validate_wheels(tmp_path)
    (tmp_path / "extra.whl").write_bytes(data)
    with pytest.raises(ValueError, match="membership"):
        builder.validate_wheels(tmp_path)


@pytest.mark.parametrize("name", ["../bad", "/bad", "C:/bad", "safe/../bad", "safe\\bad", "safe/ads:bad", "safe/CON.txt", "safe/foo.", "safe/foo "])
def test_archive_traversal_refused(name):
    with pytest.raises(ValueError, match="Unsafe"):
        bundle.safe_member(zipfile.ZipInfo(name))


def test_link_refused():
    info = zipfile.ZipInfo("link")
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with pytest.raises(ValueError, match="Unsafe"):
        bundle.safe_member(info)


def test_installed_mutation_is_not_silently_repaired(tmp_path):
    file = tmp_path / "python/python.exe"
    file.parent.mkdir()
    file.write_bytes(b"original-software-fixture")
    data = {"files": {"python/python.exe": {"sha256": bundle.file_hash(file), "bytes": file.stat().st_size}}}
    bundle.verify_installed(tmp_path, data)
    file.write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        bundle.verify_installed(tmp_path, data)
    assert file.read_bytes() == b"changed"


def test_wrong_upstream_version_refused_before_payload(tmp_path):
    (tmp_path / bundle.MANIFEST).write_text(json.dumps({"schema": "digifly-offline-neuron-v1", "runtime_id": "wrong"}))
    with pytest.raises(ValueError, match="identity"):
        bundle.validate_manifest(tmp_path)


def test_no_platform_install_on_mac(tmp_path):
    if bundle.os.name == "nt":
        pytest.skip("native Windows installation exercised by actual payload/UI CI, not this non-Windows negative case")
    with pytest.raises(ValueError, match="Windows"):
        bundle.install_included_neuron(directory=tmp_path, destination=tmp_path / "new")
    assert not (tmp_path / "new").exists()


def test_runtime_dialog_thread_teardown_in_bounded_child():
    """Transport/lifecycle only: fake installation/probes, no simulator or IO.

    A separate bounded process makes the previous native teardown deadlock or
    access violation a real test failure rather than hanging the test runner.
    Actual installation and Run remain separate, unmocked Windows UI gates.
    """
    code = r'''
from pathlib import Path
import sys
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtTest import QTest
from digifly_app.core.runtime_discovery import SimulatorRuntime
import digifly_app.ui.runtime_setup as ui
app = QApplication([])
ready = SimulatorRuntime(Path(sys.executable), '3.12.10', neuron_version='9.0.2')
ui.default_install_root = lambda: Path('inert-not-created')
ui.install_included_neuron = lambda: Path(sys.executable)
ui.probe_simulator_runtime = lambda *args, **kwargs: ready
ui.discover_simulator_runtimes = lambda **kwargs: (ready,)
QMessageBox.question = lambda *args, **kwargs: QMessageBox.StandardButton.Yes
failures = []
QMessageBox.critical = lambda *args, **kwargs: failures.append(str(args[1:])) or QMessageBox.StandardButton.Ok
for method in ('request_included_neuron', 'request_search'):
    for _ in range(10):
        dialog = ui.RuntimeSetupDialog()
        getattr(dialog, method)()
        for _ in range(200):
            if dialog._thread is None: break
            QTest.qWait(10)
        assert dialog._thread is None and dialog.use_button.isEnabled(), dialog.status.text()
        assert dialog._worker is None
        dialog.close()
assert not failures
def fail_install():
    raise ValueError('inert integrity failure')
ui.install_included_neuron = fail_install
dialog = ui.RuntimeSetupDialog()
dialog.request_included_neuron()
for _ in range(200):
    if dialog._thread is None: break
    QTest.qWait(10)
assert dialog._thread is None and not dialog.use_button.isEnabled()
assert len(failures) == 1 and 'inert integrity failure' in failures[0]
dialog.close()
print('20 successful and one refused inert transport/cleanup controls')
'''
    result = subprocess.run([sys.executable, "-c", code], capture_output=True,
        text=True, timeout=15, env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "20 successful and one refused" in result.stdout


def test_gui_wait_uses_live_event_loop_with_success_and_timeout():
    # No engine, install or filesystem mutation. Exercise actual timer dispatch
    # and the same bounded wait used by the compiled-app check.
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from digifly_app.ui.package_neuron_smoke import _wait_until
    app = QApplication.instance() or QApplication([])
    completed = []
    QTimer.singleShot(30, lambda: completed.append(True))
    assert _wait_until(lambda: bool(completed), 1)
    assert not _wait_until(lambda: False, 0.03)
