from __future__ import annotations

from types import SimpleNamespace

from digifly_app.workers.escape_siz_worker import _assert_output_binding, _bind_app_owned_outputs


def test_worker_rebinds_all_native_write_roots(tmp_path):
    native = SimpleNamespace(bothgf=SimpleNamespace())
    root = tmp_path / "escape_siz"
    _bind_app_owned_outputs(native, root)
    assert native.HERE == root
    assert native.OHMIC_GFC_SESSION_ROOT.is_relative_to(root)
    assert native.bothgf.PLOTS_ROOT.is_relative_to(root)
    planned = {
        "session_root": root / "cache",
        "run_root": root / "runs",
        "status_path": root / "status.json",
        "summary_path": root / "summary.json",
    }
    _assert_output_binding(planned, root)
