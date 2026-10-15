"""Explicit packaged-UI engineering check; never runs a simulator or a preset."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback


def run_package_smoke(destination: Path, reader: Path) -> int:
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    from digifly_app.core.resource_profile import PROFILE_PATH_ENV, make_standalone_profile, ResourceProfile, ResourceKind
    from digifly_app.core.pair_graph import pair_rows
    from digifly_app.ui.pair_graph_import import PairGraphImportDialog

    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=False)
    record = {"scope": "packaged UI and two-row Parquet software fixture; no simulator/preset/equivalence acceptance", "ok": False}
    try:
        if not reader.is_file():
            raise ValueError("Explicit external data reader is missing")
        profile_path = destination / "workspace/config/resources.json"
        make_standalone_profile(workstation_root=destination / "workspace").save(profile_path)
        os.environ[PROFILE_PATH_ENV] = str(profile_path)
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(destination / "settings"))
        app = QApplication.instance() or QApplication([])
        from digifly_app.ui.main_window import MainWindow, ORGANIZATION_NAME, APPLICATION_NAME
        settings = QSettings(ORGANIZATION_NAME, APPLICATION_NAME)
        settings.setValue("output_root", str(destination / "workspace/runs"))
        settings.setValue("workspace_root", "")
        window = MainWindow()
        window.show()
        QTest.qWait(1500)
        if not window.isVisible() or not window.grab().save(str(destination / "main-window.png")):
            raise ValueError("Actual main window did not render")
        source = destination / "pair fixture.parquet"
        create = """import duckdb, sys
db=duckdb.connect(':memory:')
db.execute('CREATE TABLE pairs(preId BIGINT, preType VARCHAR, preInstance VARCHAR, preNt VARCHAR, postId BIGINT, postType VARCHAR, postInstance VARCHAR, postNt VARCHAR, total_weight BIGINT)')
db.execute("INSERT INTO pairs VALUES (841369754323,'A','a','gaba',10110,'B','b',NULL,6),(10110,'B','b',NULL,841369754323,'A','a','gaba',42)")
db.execute("COPY pairs TO '"+sys.argv[1].replace("'","''")+"' (FORMAT PARQUET)")
"""
        subprocess.run([str(reader), "-I", "-c", create, str(source)], check=True, timeout=30, capture_output=True)
        original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        dialog = PairGraphImportDialog(None, str(reader), window)
        dialog.file_edit.setText(str(source))
        dialog.show()
        from PySide6.QtCore import Qt
        QTest.mouseClick(dialog.use_button, Qt.MouseButton.LeftButton)
        deadline = time.monotonic() + 40
        while dialog.registered_source is None and time.monotonic() < deadline:
            QTest.qWait(100)
        if dialog.registered_source is None:
            raise ValueError("Actual Parquet dialog failed: " + dialog.status.text())
        rows = pair_rows(dialog.registered_source, "841369754323", "10110")
        if len(rows) != 2 or sorted(row["total_weight"] for row in rows) != [6, 42]:
            raise ValueError("Packaged Parquet lookup lost 64-bit IDs/weights")
        if hashlib.sha256(source.read_bytes()).hexdigest() != original_hash:
            raise ValueError("Packaged importer modified source data")
        restored = ResourceProfile.load(profile_path)
        if len(restored.bindings(ResourceKind.DATA_SOURCE)) != 1 or dialog.registered_source.root:
            raise ValueError("Standalone registration did not persist without SWCs")
        dialog.grab().save(str(destination / "parquet-dialog.png"))
        record.update(ok=True, window_title=window.windowTitle(), rows=2, id_64bit=841369754323,
                      weights=[6, 42], no_swcs=True, profile_reloaded=True, source_unchanged=True)
        dialog.accept()
        window.close()
        app.processEvents()
    except Exception:
        record["error"] = traceback.format_exc()
    (destination / "receipt.json").write_text(json.dumps(record, indent=2) + "\n", "utf-8")
    return 0 if record["ok"] else 1
