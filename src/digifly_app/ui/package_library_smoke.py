"""Explicit engineering-only packaged GUI exercise of shared SWC intake.

Uses tiny labelled fixtures and a fake neuPrint transport, never the user's
credential or a simulator. Not real-neuPrint acquisition/scientific acceptance.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import time
import traceback


def run_library_review(destination: Path, workspace: Path) -> int:
    """Open the real app with isolated settings/profile for manual GUI review.

    Unlike the fixture check, this uses the normal neuPrint dialog/client and
    performs no automatic query/download. Credentials stay on the normal app
    path. The human/authorized UI operator chooses each acquisition explicitly.
    """
    from digifly_app.core.app_settings import app_settings, SETTINGS_ROOT_ENV
    from PySide6.QtWidgets import QApplication
    from digifly_app.core.resource_profile import PROFILE_PATH_ENV, make_default_profile
    destination = destination.resolve()
    workspace = workspace.resolve()
    if not workspace.is_dir():
        raise ValueError("The selected read-only Digifly workspace is missing")
    destination.mkdir(parents=True, exist_ok=False)
    profile_path = destination / "config/resources.json"
    make_default_profile(workspace_root=workspace, output_root=destination / "runs",
                         managed_data_root=destination / "data").save(profile_path)
    os.environ[PROFILE_PATH_ENV] = str(profile_path)
    os.environ[SETTINGS_ROOT_ENV] = str(destination / "settings")
    app = QApplication.instance() or QApplication([])
    from digifly_app.ui.main_window import MainWindow, ORGANIZATION_NAME, APPLICATION_NAME
    settings = app_settings(ORGANIZATION_NAME, APPLICATION_NAME)
    settings.setValue("workspace_root", str(workspace))
    settings.setValue("output_root", str(destination / "runs"))
    window = MainWindow()
    window.pages.setCurrentWidget(window.data_library_page)
    window.show()
    return app.exec()


def run_library_smoke(destination: Path) -> int:
    from PySide6.QtCore import Qt
    from digifly_app.core.app_settings import app_settings, SETTINGS_ROOT_ENV
    from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog, QMessageBox
    from PySide6.QtTest import QTest
    from digifly_app.core.resource_profile import PROFILE_PATH_ENV, ResourceProfile, ResourceKind, make_standalone_profile
    from digifly_app.core.neuprint import NeuPrintDataset, NeuPrintNeuron
    from digifly_app.core.providers import profile_connectome_sources
    from digifly_app.core.connectomes import ConnectomeCatalog
    from digifly_app.ui.neuprint_import import NeuPrintImportDialog

    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=False)
    record = {"schema": "digifly-packaged-swc-library-check-v1", "ok": False,
              "scope": "actual packaged controls/worker/storage with tiny fixtures; fake neuPrint transport; no credential, network, simulator or scientific acceptance"}
    app = window = None
    saved_question = QMessageBox.question
    saved_item = QInputDialog.getItem
    saved_directory = QFileDialog.getExistingDirectory
    saved_warning, saved_critical = QMessageBox.warning, QMessageBox.critical
    record["dialogs"] = []
    def journal(stage):
        record["stage"] = stage
        (destination / "journal.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    try:
        profile_path = destination / "workspace/config/resources.json"
        profile = make_standalone_profile(workstation_root=destination / "workspace")
        profile.save(profile_path)
        os.environ[PROFILE_PATH_ENV] = str(profile_path)
        os.environ[SETTINGS_ROOT_ENV] = str(destination / "settings")
        app = QApplication.instance() or QApplication([])
        def capture_message(parent, title, text, *args, **kwargs):
            record["dialogs"].append({"title": title, "text": text})
            return QMessageBox.StandardButton.Ok
        QMessageBox.warning = QMessageBox.critical = capture_message
        from digifly_app.ui.main_window import MainWindow, ORGANIZATION_NAME, APPLICATION_NAME
        settings = app_settings(ORGANIZATION_NAME, APPLICATION_NAME)
        settings.setValue("output_root", str(destination / "workspace/runs"))
        settings.setValue("workspace_root", "")
        window = MainWindow()
        journal("main_window_created")
        window.show()
        page = window.data_library_page
        window.pages.setCurrentWidget(page)
        QTest.qWait(400)

        swc = b"1 1 0 0 0 5 -1\n2 3 1 0 0 1 1\n"
        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass
            def validate_and_list_datasets(self, **kwargs):
                return (NeuPrintDataset("manc:v1.2.1", "Engineering fixture", "fixture"),)
            def preview_neurons(self, selection, **kwargs):
                all_neurons = (NeuPrintNeuron(42, "DNp01"), NeuPrintNeuron(43, "TTMn"))
                return tuple(item for item in all_neurons if item.neuron_type in selection.value)
            def fetch_skeleton(self, body_id, **kwargs):
                return swc

        def wait_for(predicate, detail):
            deadline = time.monotonic() + 30
            while not predicate() and time.monotonic() < deadline:
                QTest.qWait(40)
                # QtTest's wait may retain the GIL; yield explicitly so Python
                # workers are not starved by a fast GUI-test polling loop.
                time.sleep(0.005)
            if not predicate():
                raise ValueError(detail)

        QMessageBox.question = lambda *args, **kwargs: QMessageBox.StandardButton.Yes
        resources = []
        class NoCredentialStore:
            available = False
        for name in ("DNp01", "TTMn"):
            journal("create_" + name)
            dialog = NeuPrintImportDialog(profile, profile_path, window, client_factory=FakeClient,
                                           credential_store=NoCredentialStore())
            dialog.resource_imported.connect(resources.append)
            dialog.show()
            dialog.token_edit.setText("engineering-fixture-not-a-credential")
            QTest.mouseClick(dialog.connect_button, Qt.MouseButton.LeftButton)
            journal("connect_" + name)
            wait_for(lambda: not dialog.operation_in_progress, "Connection worker did not finish")
            if not dialog.dataset_combo.currentData():
                raise ValueError("Dataset fixture did not become available: " + dialog.status.text())
            dialog.selection_mode.setCurrentIndex(dialog.selection_mode.findData("type_exact"))
            dialog.selection_edit.setText(name)
            dialog.connectivity_check.setChecked(False)
            QTest.mouseClick(dialog.preview_button, Qt.MouseButton.LeftButton)
            journal("preview_" + name)
            wait_for(lambda: not dialog.operation_in_progress, "Preview worker did not finish")
            if dialog.preview_table.rowCount() != 1:
                raise ValueError("Preview did not contain the intended neuron")
            before = len(resources)
            QTest.mouseClick(dialog.download_button, Qt.MouseButton.LeftButton)
            journal("download_" + name)
            wait_for(lambda: not dialog.operation_in_progress, "Download worker did not finish")
            if len(resources) != before + 1:
                raise ValueError("App-owned acquisition failed: " + dialog.status.text())
            if not dialog.open_library_button.isEnabled() or "export_swc" not in dialog.library_location.text():
                raise ValueError("The destination/reveal control is not available")
            dialog.grab().save(str(destination / f"download-{name}.png"))
            dialog.accept()
            journal("finished_" + name)
        if resources[0].root != resources[1].root or resources[-1].swc_count != 2:
            raise ValueError("Separate downloads did not join one library")

        # Exercise the actual local-import action and its threaded production
        # worker; only native file/confirmation prompts receive fixture answers.
        local_root = destination / "local-source"
        local_file = local_root / "AN/AN-example/44/44.swc"
        local_file.parent.mkdir(parents=True)
        local_file.write_bytes(swc)
        choices = iter(("manc:v1.2.1", "Folder (preserve family/type labels)"))
        QInputDialog.getItem = lambda *args, **kwargs: (next(choices), True)
        QFileDialog.getExistingDirectory = lambda *args, **kwargs: str(local_root)
        QTest.mouseClick(page.swc_intake_button, Qt.MouseButton.LeftButton)
        wait_for(lambda: not page.import_in_progress, "Local SWC intake worker did not finish")
        restored = ResourceProfile.load(profile_path)
        if len(restored.bindings(ResourceKind.MORPHOLOGY_SOURCE)) != 1:
            raise ValueError("More than one dataset library binding was registered")
        source, = profile_connectome_sources(restored)
        catalog = ConnectomeCatalog.scan(source)
        if set(catalog.by_id) != {"42", "43", "44"} or catalog.query("DNp01, TTMn").unmatched:
            raise ValueError("Combined loading lost a separately downloaded/imported neuron")
        window.circuit_builder_page.refresh_connectomes()
        builder = window.circuit_builder_page
        window.pages.setCurrentWidget(builder)
        builder.connectome_combo.setCurrentIndex(builder.connectome_combo.findData(source.key))
        builder.query_edit.setText("DNp01, TTMn")
        builder.assemble_circuit()
        QTest.qWait(300)
        if set(builder.loaded_morphologies) != {"42", "43"}:
            raise ValueError("Actual Circuit Builder did not visibly load the combined cell set")
        if not window.grab().save(str(destination / "combined-circuit.png")):
            raise ValueError("Could not capture the actual packaged Circuit Builder")
        record.update(ok=True, body_ids=sorted(catalog.by_id), primary_bindings=1,
                      separate_downloads_same_library=True, local_intake=True,
                      combined_visible_neurons=sorted(builder.loaded_morphologies), library_root=str(resources[-1].root))
    except Exception:
        record["error"] = traceback.format_exc()
    finally:
        QMessageBox.question = saved_question
        QInputDialog.getItem = saved_item
        QFileDialog.getExistingDirectory = saved_directory
        QMessageBox.warning, QMessageBox.critical = saved_warning, saved_critical
        (destination / "receipt.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        if window is not None:
            # An engineering test does not ask the human to save its fixture draft.
            window.deleteLater()
        if app is not None:
            app.processEvents()
    (destination / "receipt.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return 0 if record["ok"] else 1
