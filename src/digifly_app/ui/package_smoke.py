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


def run_package_smoke(destination: Path, reader: Path, *, included: bool = False) -> int:
    from digifly_app.core.app_settings import app_settings, SETTINGS_ROOT_ENV
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    from digifly_app.core.resource_profile import PROFILE_PATH_ENV, make_standalone_profile, ResourceProfile, ResourceKind
    from digifly_app.core.pair_graph import pair_rows, validated_manifest
    from digifly_app.core.bundled_pair_graph import included_pair_path, PAIR_SHA256
    from digifly_app.ui.pair_graph_import import PairGraphImportDialog

    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=False)
    record = {"scope": "packaged UI and supplied all-row Parquet import" if included else "packaged UI and two-row Parquet software fixture",
              "no_simulator_preset_or_equivalence_acceptance": True, "ok": False}
    try:
        if not reader.is_file():
            raise ValueError("Explicit external data reader is missing")
        profile_path = destination / "workspace/config/resources.json"
        make_standalone_profile(workstation_root=destination / "workspace").save(profile_path)
        os.environ[PROFILE_PATH_ENV] = str(profile_path)
        os.environ[SETTINGS_ROOT_ENV] = str(destination / "settings")
        app = QApplication.instance() or QApplication([])
        from digifly_app.ui.main_window import MainWindow, ORGANIZATION_NAME, APPLICATION_NAME
        settings = app_settings(ORGANIZATION_NAME, APPLICATION_NAME)
        settings.setValue("output_root", str(destination / "workspace/runs"))
        settings.setValue("workspace_root", "")
        window = MainWindow()
        window.show()
        QTest.qWait(1500)
        if not window.isVisible() or not window.grab().save(str(destination / "main-window.png")):
            raise ValueError("Actual main window did not render")
        source = included_pair_path() if included else destination / "pair fixture.parquet"
        if source is None:
            raise ValueError("The exact included user-selected Parquet is absent")
        create = """import duckdb, sys
db=duckdb.connect(':memory:')
db.execute('CREATE TABLE pairs(preId BIGINT, preType VARCHAR, preInstance VARCHAR, preNt VARCHAR, postId BIGINT, postType VARCHAR, postInstance VARCHAR, postNt VARCHAR, total_weight BIGINT)')
db.execute("INSERT INTO pairs VALUES (841369754323,'A','a','gaba',10110,'B','b',NULL,6),(10110,'B','b',NULL,841369754323,'A','a','gaba',42)")
db.execute("COPY pairs TO '"+sys.argv[1].replace("'","''")+"' (FORMAT PARQUET)")
"""
        if not included:
            subprocess.run([str(reader), "-I", "-c", create, str(source)], check=True, timeout=30, capture_output=True)
        original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        dialog = PairGraphImportDialog(None, str(reader), window)
        if included:
            if Path(dialog.file_edit.text()).resolve() != source.resolve():
                raise ValueError("The actual import dialog did not preselect the included file")
        else:
            dialog.file_edit.setText(str(source))
        dialog.show()
        from PySide6.QtCore import Qt
        QTest.mouseClick(dialog.use_button, Qt.MouseButton.LeftButton)
        deadline = time.monotonic() + (120 if included else 40)
        while dialog.registered_source is None and time.monotonic() < deadline:
            QTest.qWait(100)
        if dialog.registered_source is None:
            raise ValueError("Actual Parquet dialog failed: " + dialog.status.text())
        manifest = validated_manifest(dialog.registered_source)
        if included:
            observed = {key: manifest[key] for key in ("rows", "represented_neurons", "total_weight", "min_weight", "max_weight")}
            if observed != {"rows": 1187262, "represented_neurons": 38309, "total_weight": 23323860, "min_weight": 6, "max_weight": 1080}:
                raise ValueError("Actual all-row import differs from the supplied file census")
            retained = Path(dialog.registered_source.pair_graph_path)
            if retained == source or not retained.is_relative_to(destination / "workspace") or hashlib.sha256(retained.read_bytes()).hexdigest() != PAIR_SHA256:
                raise ValueError("The included file was not durably retained outside the install")
            record.update(included_file_preselected=True, external_copy_sha256=PAIR_SHA256, census=observed,
                          data_reader=str(reader), index_sha256=manifest["index_sha256"])
        else:
            rows = pair_rows(dialog.registered_source, "841369754323", "10110")
            if len(rows) != 2 or sorted(row["total_weight"] for row in rows) != [6, 42]:
                raise ValueError("Packaged Parquet lookup lost 64-bit IDs/weights")
            record.update(rows=2, id_64bit=841369754323, weights=[6, 42])
        if hashlib.sha256(source.read_bytes()).hexdigest() != original_hash:
            raise ValueError("Packaged importer modified source data")
        restored = ResourceProfile.load(profile_path)
        if len(restored.bindings(ResourceKind.DATA_SOURCE)) != 1 or dialog.registered_source.root:
            raise ValueError("Standalone registration did not persist without SWCs")
        dialog.grab().save(str(destination / "parquet-dialog.png"))
        record.update(ok=True, window_title=window.windowTitle(), no_swcs=True, profile_reloaded=True, source_unchanged=True)
        dialog.accept()
        window.close()
        app.processEvents()
    except Exception:
        record["error"] = traceback.format_exc()
    (destination / "receipt.json").write_text(json.dumps(record, indent=2) + "\n", "utf-8")
    return 0 if record["ok"] else 1
