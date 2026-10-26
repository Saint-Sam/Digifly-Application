"""Explicit engineering-only packaged GUI exercise of shared SWC intake.

Uses tiny labelled fixtures and a fake neuPrint transport, never the user's
credential or a simulator. Not real-neuPrint acquisition/scientific acceptance.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import time
import traceback
import faulthandler


def prepare_library_review(destination: Path, workspace: Path, existing_profile: Path | None = None):
    """Validate before writing; an explicit existing task profile is never reset."""
    from digifly_app.core.resource_profile import ResourceProfile, make_default_profile
    destination,workspace=destination.resolve(),workspace.resolve()
    if not workspace.is_dir():
        raise ValueError('The selected read-only Digifly workspace is missing')
    if destination.exists():
        raise FileExistsError('Manual app review needs a new evidence folder; do not relaunch an existing attempt')
    if existing_profile is not None:
        profile_path=existing_profile.resolve()
        profile=ResourceProfile.load(profile_path)
        if profile.workspace_root is None or profile.workspace_root.resolve()!=workspace:
            raise ValueError('The explicit review profile belongs to another workspace')
    else:
        profile_path=destination/'config/resources.json'
        profile=make_default_profile(workspace_root=workspace,output_root=destination/'runs',managed_data_root=destination/'data')
    destination.mkdir(parents=True,exist_ok=False)
    if existing_profile is None:
        profile.save(profile_path)
    return destination,workspace,profile_path,profile


def run_library_review(destination: Path, workspace: Path, existing_profile: Path | None = None) -> int:
    """Open the real app with isolated settings/profile for manual GUI review.

    Unlike the fixture check, this uses the normal neuPrint dialog/client and
    performs no automatic query/download. Credentials stay on the normal app
    path. The human/authorized UI operator chooses each acquisition explicitly.
    """
    from digifly_app.core.app_settings import app_settings, SETTINGS_ROOT_ENV
    from PySide6.QtWidgets import QApplication
    from digifly_app.core.resource_profile import PROFILE_PATH_ENV
    from PySide6.QtCore import QTimer
    destination,workspace,profile_path,profile=prepare_library_review(destination,workspace,existing_profile)
    os.environ[PROFILE_PATH_ENV] = str(profile_path)
    os.environ[SETTINGS_ROOT_ENV] = str(destination / "settings")
    app = QApplication.instance() or QApplication([])
    from digifly_app.ui.main_window import MainWindow, ORGANIZATION_NAME, APPLICATION_NAME
    settings = app_settings(ORGANIZATION_NAME, APPLICATION_NAME)
    settings.setValue("workspace_root", str(workspace))
    settings.setValue("output_root", str(profile.output_root))
    window = MainWindow()
    window.pages.setCurrentWidget(window.data_library_page)
    window.show()
    def ready():
        with (destination/'window-ready.json').open('x') as stream:
            json.dump({'window_visible':window.isVisible(),'pid':os.getpid(),
                'profile':str(profile_path),'workspace':str(workspace),
                'managed_data_root':str(profile.managed_data_root),
                'automatic_download_or_simulation':False},stream,indent=2)
    QTimer.singleShot(0,ready)
    return app.exec()


def run_library_smoke(destination: Path) -> int:
    from PySide6.QtCore import Qt, QTimer, QPoint
    from digifly_app.core.app_settings import app_settings, SETTINGS_ROOT_ENV
    from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog, QMessageBox, QScrollArea, QPushButton
    from PySide6.QtTest import QTest
    from digifly_app.core.resource_profile import PROFILE_PATH_ENV, ResourceProfile, ResourceKind, ResourceBinding, AccessMode, make_standalone_profile
    from digifly_app.core.neuprint import NeuPrintClient, NeuPrintDataset
    from digifly_app.core.providers import profile_connectome_sources
    from digifly_app.core.connectomes import ConnectomeCatalog
    from digifly_app.ui.neuprint_import import NeuPrintImportDialog, ExistingNeuronDialog

    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=False)
    record = {"schema": "digifly-packaged-swc-library-check-v1", "ok": False,
              "scope": "actual packaged controls/worker/storage with tiny fixtures; fake neuPrint transport; no credential, network, simulator or scientific acceptance"}
    stack_output = (destination / 'thread-stacks.txt').open('w')
    previous_fault_handler = faulthandler.is_enabled()
    faulthandler.enable(file=stack_output)
    faulthandler.dump_traceback_later(45, file=stack_output)
    app = window = None
    saved_question = QMessageBox.question
    saved_item = QInputDialog.getItem
    saved_text = QInputDialog.getText
    saved_directory = QFileDialog.getExistingDirectory
    saved_open, saved_save = QFileDialog.getOpenFileName, QFileDialog.getSaveFileName
    saved_warning, saved_critical = QMessageBox.warning, QMessageBox.critical
    record["dialogs"] = []
    def journal(stage):
        record["stage"] = stage
        (destination / "journal.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    try:
        profile_path = destination / "workspace/config/resources.json"
        profile = make_standalone_profile(workstation_root=destination / "workspace")
        # Existing profiles may contain every supported simulator, unlike the
        # old clean-install fixture which missed the reported NetPyNE failure.
        from dataclasses import replace
        runtimes = (ResourceKind.NEURON_RUNTIME, ResourceKind.ARBOR_RUNTIME,
                    ResourceKind.BMTK_RUNTIME, ResourceKind.NETPYNE_RUNTIME)
        profile = replace(profile, resources=tuple(r for r in profile.resources if r.kind not in runtimes) + tuple(
            ResourceBinding('saved-' + kind.value, kind, str(destination / 'existing-runtime' / kind.value),
                            AccessMode.EXECUTABLE, required=False) for kind in runtimes))
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
        # Test direct entry before the chooser is initialized, then with a
        # remembered different family. Card-first checks missed this failure.
        window.circuit_builder_page.select_library_release('manc:v1.2.1')
        window.pages.setCurrentWidget(window.circuit_builder_page)
        QTest.qWait(100)
        QTest.mouseClick(window.circuit_builder_page.library_button, Qt.MouseButton.LeftButton)
        if window.pages.currentWidget() is not page or not page.connectome_picker.selected_key:
            raise ValueError('Cold Circuit Builder to Data Library handoff failed')
        page.open_connectome('male-cns:v1.0')
        window.circuit_builder_page.select_library_release('manc:v1.2.1')
        window.pages.setCurrentWidget(window.circuit_builder_page)
        QTest.mouseClick(window.circuit_builder_page.library_button, Qt.MouseButton.LeftButton)
        if window.pages.currentWidget() is not page or page._display_dataset != 'manc:v1.2.1':
            raise ValueError('Cross-family Circuit Builder to Data Library handoff failed')
        page.show_connectomes()
        page.connectome_picker.selected_dataset = ''
        page.connectome_picker.selected_key = ''
        page.connectome_picker._selected_releases.clear()
        record['circuit_library_cold_handoff'] = True
        record['circuit_library_cross_family_handoff'] = True
        def click_menu(button, action, filename=None):
            # Native menu delivery requires the fixture window, not another
            # open app copy, to own focus. Keep the visible-menu assertion.
            window.raise_()
            window.activateWindow()
            QTest.qWait(60)
            # Older storage/registration controls remain under explicit details.
            if page.storage_details.isAncestorOf(button) and not page.storage_details_button.isChecked():
                QTest.mouseClick(page.storage_details_button, Qt.MouseButton.LeftButton)
            page.scroll.ensureWidgetVisible(button)
            QTest.qWait(50)
            menu = button._library_menu
            QTest.mouseClick(button, Qt.MouseButton.LeftButton)
            QTest.qWait(50)
            target = next(item for item in menu.actions() if item.text() == action.text())
            if not menu.isVisible() or not target.isEnabled():
                raise ValueError('The requested library menu action is not accessible: ' + action.text())
            if filename:
                menu.grab().save(str(destination / filename))
            QTest.mouseClick(menu, Qt.MouseButton.LeftButton, pos=menu.actionGeometry(target).center())
            app.processEvents()

        def click_linked(action, filename=None):
            holder = page.linked_table.cellWidget(0, 6)
            click_menu(holder._library_button, action, filename)
        window.pages.setCurrentWidget(page)
        QTest.qWait(400)
        page.refresh()
        if any(not ResourceProfile.load(profile_path).binding(kind) for kind in runtimes):
            raise ValueError("Saved simulator bindings were lost on Data Library refresh.")
        if any("ResourceKind" in item['text'] for item in record['dialogs']):
            raise ValueError("Existing simulator profile breaks Data Library.")
        record["existing_simulator_profile_loaded"] = [kind.value for kind in runtimes]

        # The home box and details are separate inline views. Family/release
        # choices never open a modal or mutate saved data/project settings.
        picker = page.connectome_picker
        from digifly_app.ui.connectome_picker import CONNECTOMES, picture_path
        def choose_release(dataset):
            if page.view_stack.currentWidget() is page.scroll:
                page.scroll.verticalScrollBar().setValue(0)
                QTest.mouseClick(page.back_to_connectomes, Qt.MouseButton.LeftButton)
            choice = next(c for c in CONNECTOMES if dataset in c.releases)
            QTest.mouseClick(picker.buttons[choice.key], Qt.MouseButton.LeftButton)
            QTest.mouseClick(page.release_buttons[dataset], Qt.MouseButton.LeftButton)
            QTest.qWait(60)
        before_choice = profile_path.read_bytes()
        if picker.selected_dataset or any(image.pixmap().isNull() for image in picker.pictures.values()):
            raise ValueError('Initial connectome choices or bundled pictures are missing')
        if any(button.isCheckable() for button in picker.buttons.values()):
            raise ValueError('Connectome navigation must use ordinary accessible push buttons')
        if page.view_stack.currentWidget() is not page.picker_scroll or page.add_data_button.isVisible():
            raise ValueError('Data Library home must show only the connectome chooser')
        layout_sizes = []
        for width in (1120, 1700):
            window.resize(width, 900)
            page.picker_scroll.verticalScrollBar().setValue(0)
            QTest.qWait(120)
            rects = [card.geometry() for card in picker.cards.values()]
            if any(a.intersects(b) for i, a in enumerate(rects) for b in rects[i+1:]):
                raise ValueError('Connectome cards overlap')
            if not window.grab().save(str(destination / f'connectome-choices-{width}.png')):
                raise ValueError('Connectome home view did not render')
            QTest.mouseClick(picker.buttons['male_cns'], Qt.MouseButton.LeftButton)
            QTest.qWait(80)
            if app.activeModalWidget() or page.view_stack.currentWidget() is not page.scroll or picker.isVisible():
                raise ValueError('Connectome choice must navigate inline without a window')
            if not page.add_data_button.isVisible() or page.load_link_button not in page.add_data_menu.actions():
                raise ValueError('Common import tools disappeared from an empty release')
            if page.linked_card.isVisible() or page.managed_card.isVisible():
                raise ValueError('Empty data sections must be hidden')
            QTest.mouseClick(page.release_buttons['male-cns:v0.9'], Qt.MouseButton.LeftButton)
            page.release_buttons['male-cns:v1.0'].setChecked(True)
            if picker.selected_dataset != 'male-cns:v1.0':
                raise ValueError('Accessible release activation did not update the selection')
            page.release_buttons['male-cns:v0.9'].setChecked(True)
            if app.activeModalWidget() or picker.selected_dataset != 'male-cns:v0.9':
                raise ValueError('Release selection must be inline and exact')
            if not window.grab().save(str(destination / f'connectome-details-{width}.png')):
                raise ValueError('Connectome details view did not render')
            QTest.mouseClick(page.back_to_connectomes, Qt.MouseButton.LeftButton)
            QTest.qWait(80)
            if page.view_stack.currentWidget() is not page.picker_scroll or page.add_data_button.isVisible():
                raise ValueError('Back did not restore the connectome-only home')
            layout_sizes.append(width)
        QTest.mouseClick(picker.buttons['manc'], Qt.MouseButton.LeftButton)
        QTest.qWait(80)
        if page.connectome_title.text() != 'MANC' or not page.release_buttons['manc:v1.2.1'].isVisible():
            raise ValueError('Back/switch did not open the next connectome')
        QTest.mouseClick(page.back_to_connectomes, Qt.MouseButton.LeftButton)
        QTest.mouseClick(picker.buttons['male_cns'], Qt.MouseButton.LeftButton)
        QTest.qWait(80)
        if picker.selected_dataset != 'male-cns:v0.9' or app.activeModalWidget():
            raise ValueError('Inline release was not retained across Back/switch')
        from digifly_app.ui import data_library as library_module
        saved_import_dialog = library_module.NeuPrintImportDialog
        class CatalogueOnlyDialog(saved_import_dialog):
            def __init__(self, *args, **kwargs):
                class NoStore:
                    available = False
                kwargs['credential_store'] = NoStore()
                super().__init__(*args, **kwargs)
                QTimer.singleShot(100, self.inspect_choice)
            def inspect_choice(self):
                if self._preferred_dataset != 'male-cns:v0.9':
                    self.reject()
                    return
                self._connected(tuple(NeuPrintDataset(release) for choice in CONNECTOMES for release in choice.releases))
                record['connectome_download_release_prefilled'] = self.dataset_combo.currentData() == 'male-cns:v0.9'
                self.grab().save(str(destination / 'connectome-download-release.png'))
                self.reject()
        library_module.NeuPrintImportDialog = CatalogueOnlyDialog
        try:
            page.scroll.ensureWidgetVisible(page.neuprint_button)
            QTest.mouseClick(page.neuprint_button, Qt.MouseButton.LeftButton)
        finally:
            library_module.NeuPrintImportDialog = saved_import_dialog
        if not record.get('connectome_download_release_prefilled') or profile_path.read_bytes() != before_choice:
            raise ValueError('Pictured choice changed saved data or failed to prefill the exact release')
        record.update(connectome_pictures_loaded=True, connectome_choices_first=True,
                      connectome_choice_preserves_data=True, connectome_layout_no_overlap=True,
                      connectome_home_only=True, connectome_inline_navigation=True,
                      connectome_back_navigation=True, connectome_release_inline_no_dialog=True,
                      connectome_existing_tools_preserved=True,
                      connectome_accessible_navigation=True,
                      connectome_choices=[choice.name for choice in CONNECTOMES],
                      connectome_releases=[r for choice in CONNECTOMES for r in choice.releases],
                      connectome_layout_sizes=layout_sizes,
                      connectome_picture_hashes={choice.key: __import__('hashlib').sha256(picture_path(choice.key).read_bytes()).hexdigest() for choice in CONNECTOMES})
        window.resize(1120, 760)

        swc = b"1 1 0 0 0 5 -1\n2 3 1 0 0 1 1\n"
        # Keep the production query builder and parser in this packaged UI
        # test. Only HTTP is replaced; the former fake preview bypassed both.
        queries = []
        skeleton_fetches = []
        active_swc = swc
        fixture_rows = ((42, "DNp01", "DNp01_R"), (43, "TTMn", "TTMn_R"), (45, "PSI", "PSI_L"))
        def transport(method, url, headers, payload, timeout, cancel, max_bytes, on_bytes):
            if "/api/skeletons/skeleton/" in url:
                skeleton_fetches.append(int(url.split("?")[0].rstrip("/").split("/")[-1]))
                return active_swc
            body = json.loads(payload)
            if body["dataset"] != "manc:v1.2.1":
                raise ValueError("The query switched datasets")
            query = body["cypher"]
            queries.append(query)
            if 'MATCH (m:Meta)' in query:
                return json.dumps({'columns':['threshold'],'data':[[.5]]}).encode()
            if 'id(pre)' in query:
                from digifly_app.core.neuprint_contacts import COLUMNS
                return json.dumps({'columns':list(COLUMNS),'data':[]}).encode()
            if 'ConnectsTo' in query:
                return json.dumps({'columns':['sourceBodyId','targetBodyId','weight'],'data':[]}).encode()
            literals = [json.loads(value) for value in re.findall(r'STARTS WITH ("(?:[^"\\]|\\.)*")', query)]
            if literals:
                rows = [row for row in fixture_rows if any(
                    row[1].lower().startswith(term) or row[2].lower().startswith(term) for term in literals)]
            else:
                rows = [row for row in fixture_rows if f'n.type = "{row[1]}"' in query]
            return json.dumps({"columns": ["bodyId", "type", "instance"], "data": rows}).encode()
        class FakeClient(NeuPrintClient):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs, transport=transport)
            def validate_and_list_datasets(self, **kwargs):
                return (NeuPrintDataset("manc:v1.2.1", "Engineering fixture", "fixture"),)

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
        review_observations = []
        def arm_review(action, filename):
            deadline = time.monotonic() + 15
            def answer():
                review = app.activeModalWidget()
                if not isinstance(review, ExistingNeuronDialog):
                    if time.monotonic() < deadline:
                        QTimer.singleShot(40, answer)
                    return
                unchecked = all(review.table.item(row, 0).checkState() == Qt.CheckState.Unchecked
                                for row in range(review.table.rowCount()))
                review_observations.append({"action": action, "default_keep": unchecked,
                    "replace_disabled_initially": not review.replace_button.isEnabled()})
                review.grab().save(str(destination / filename))
                if action == "replace":
                    item = review.table.item(0, 0)
                    QTest.mouseClick(review.table.viewport(), Qt.MouseButton.LeftButton,
                                     pos=review.table.visualItemRect(item).center())
                    if item.checkState() != Qt.CheckState.Checked:
                        # Native style checkboxes occupy the left of a cell.
                        QTest.mouseClick(review.table.viewport(), Qt.MouseButton.LeftButton,
                            pos=review.table.visualItemRect(item).topLeft() + QPoint(10, 12))
                    review.grab().save(str(destination / "replacement-checked.png"))
                    QTest.mouseClick(review.replace_button, Qt.MouseButton.LeftButton)
                else:
                    QTest.mouseClick(review.cancel_button if action == "cancel" else review.keep_button,
                                     Qt.MouseButton.LeftButton)
            QTimer.singleShot(40, answer)
        for name, action in (("DNp01", "new"), ("TTMn", "new"),
                             ("DNp01, TTMn, PSI", "keep"), ("DNp01", "cancel"), ("DNp01", "replace")):
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
            if name != "DNp01, TTMn, PSI":
                dialog.selection_mode.setCurrentIndex(dialog.selection_mode.findData("type_exact"))
            elif dialog.selection_mode.currentData() != "name_list":
                raise ValueError("Normal search did not default to a neuron list")
            dialog.selection_edit.setText(name)
            dialog.connectivity_check.setChecked(False)
            QTest.mouseClick(dialog.preview_button, Qt.MouseButton.LeftButton)
            journal("preview_" + name)
            wait_for(lambda: not dialog.operation_in_progress, "Preview worker did not finish")
            expected = 3 if name == "DNp01, TTMn, PSI" else 1
            if dialog.preview_table.rowCount() != expected:
                raise ValueError("Preview did not contain the intended neuron")
            if expected == 3 and not all(term + ": 1" in dialog.status.text() for term in ("DNp01", "TTMn", "PSI")):
                raise ValueError("Preview did not report all three requested names")
            if expected == 3:
                dialog.findChild(QScrollArea).ensureWidgetVisible(dialog.preview_table)
                QTest.qWait(120)
                if not dialog.grab().save(str(destination / "combined-list-preview.png")):
                    raise ValueError("Could not capture the combined query preview")
                record["combined_preview_status"] = dialog.status.text()
                record["combined_selection"] = dialog._request().selection.to_dict()
            before = len(resources)
            fetches_before = list(skeleton_fetches)
            if action != "new":
                arm_review(action, f"existing-{action}.png")
            if action == "replace":
                active_swc = swc.replace(b"0 5", b"0 6")
            QTest.mouseClick(dialog.download_button, Qt.MouseButton.LeftButton)
            journal("download_" + name)
            wait_for(lambda: not dialog.operation_in_progress, "Download worker did not finish")
            if action == "cancel":
                if len(resources) != before or skeleton_fetches != fetches_before:
                    raise ValueError("Cancel changed files or started a skeleton download")
                record["cancel_keeps_existing"] = True
                dialog.reject()
                continue
            if len(resources) != before + 1:
                raise ValueError("App-owned acquisition failed: " + dialog.status.text())
            if not dialog.open_library_button.isEnabled() or "export_swc" not in dialog.library_location.text():
                raise ValueError("The destination/reveal control is not available")
            if action == "keep":
                dialog.findChild(QScrollArea).ensureWidgetVisible(dialog.destination_label)
                QTest.qWait(100)
                dialog.grab().save(str(destination / "dataset-destination.png"))
            dialog.grab().save(str(destination / f"download-{name}.png"))
            if action == "keep":
                if skeleton_fetches[len(fetches_before):] != [45]:
                    raise ValueError("Keeping existing files downloaded them again")
                record["existing_keep_skips_network"] = True
            if action == "replace":
                current = resources[-1]
                metadata = json.loads(current.manifest.read_text(encoding="utf-8"))
                last = json.loads(Path(metadata["acquisitions"][-1]["manifest"]).read_text(encoding="utf-8"))
                change, = last["actions"]
                if skeleton_fetches[len(fetches_before):] != [42] or change["action"] != "replaced":
                    raise ValueError("Checked replacement did not download exactly the chosen neuron")
                if (current.root / change["path"]).read_bytes() != active_swc or (current.root / change["backup"]).read_bytes() != swc:
                    raise ValueError("New SWC or retained previous copy differs")
                record["replacement_backup_verified"] = True
            dialog.accept()
            journal("finished_" + name)
        if len({resource.root for resource in resources}) != 1 or resources[-1].swc_count != 3:
            raise ValueError("Separate downloads did not join one library")
        if not all(row["default_keep"] and row["replace_disabled_initially"] for row in review_observations):
            raise ValueError("Replacement was selected by default")
        if (profile.managed_data_root / "neuprint").exists() or list((profile.managed_data_root / ".staging").rglob("*.swc")):
            raise ValueError("New downloads left a search-named or staged SWC collection")
        record.update(canonical_dataset_destination=True, existing_tree_lookup=True,
                      replacement_requires_choice=True, no_search_named_swc_folders=True,
                      review_observations=review_observations, skeleton_fetches=skeleton_fetches)
        from PySide6.QtWidgets import QLabel
        unwanted = ''.join(map(chr, [112, 114, 111, 118, 101, 110, 97, 110, 99, 101]))
        if any(unwanted in label.text().casefold() for label in window.findChildren(QLabel)):
            raise ValueError("An app label still uses the removed wording")
        record["plain_ui_labels"] = True

        # Exercise the actual local-import action and its threaded production
        # worker; only native file/confirmation prompts receive fixture answers.
        local_root = destination / "local-source"
        local_file = local_root / "AN/AN-example/44/44.swc"
        local_file.parent.mkdir(parents=True)
        local_file.write_bytes(swc)
        choices = iter(("manc:v1.2.1", "Folder (preserve family/type labels)"))
        QInputDialog.getItem = lambda *args, **kwargs: (next(choices), True)
        QFileDialog.getExistingDirectory = lambda *args, **kwargs: str(local_root)
        click_menu(page.add_data_button, page.swc_intake_button, 'compact-add-data-menu.png')
        wait_for(lambda: not page.import_in_progress, "Local SWC intake worker did not finish")
        restored = ResourceProfile.load(profile_path)
        if any(restored.binding(kind) != profile.binding(kind) for kind in runtimes):
            raise ValueError("Data Library import changed saved simulator bindings.")
        if len(restored.bindings(ResourceKind.MORPHOLOGY_SOURCE)) != 1:
            raise ValueError("More than one dataset library binding was registered")
        source, = profile_connectome_sources(restored)
        catalog = ConnectomeCatalog.scan(source)
        if set(catalog.by_id) != {"42", "43", "44", "45"} or catalog.query("DNp01, TTMn, PSI").unmatched:
            raise ValueError("Combined loading lost a separately downloaded/imported neuron")
        window.circuit_builder_page.refresh_connectomes()
        builder = window.circuit_builder_page
        window.pages.setCurrentWidget(builder)
        builder.connectome_combo.setCurrentIndex(builder.connectome_combo.findData(source.key))
        builder.query_edit.setText("DNp01, TTMn, PSI")
        builder.assemble_circuit()
        QTest.qWait(300)
        if set(builder.loaded_morphologies) != {"42", "43", "45"}:
            raise ValueError("Actual Circuit Builder did not visibly load the combined cell set")
        if not window.grab().save(str(destination / "combined-circuit.png")):
            raise ValueError("Could not capture the actual packaged Circuit Builder")
        # The same compiled Data Library must expose existing full-folder
        # sources separately from managed acquisitions. Tiny fixtures exercise
        # discovery, actual Register controls and read-only background counts;
        # their file counts do not claim real connectome completeness.
        from dataclasses import replace
        public = destination / 'linked-fixture/Public'
        male = public / 'Phase 1/male-cns_v0.9/export_swc'
        manc = destination / 'linked-fixture/full-manc'
        native_files = (male / 'MN/TTMn/800146/800146.swc', male / 'IN/CT1/10009/10009.swc',
                        manc / 'DN/DNp01/10000/10000_axodendro_with_synapses.swc')
        for path in native_files:
            path.parent.mkdir(parents=True, exist_ok=True)
            header = (b'# Source: Janelia male-cns v0.9 public skeleton release\n'
                      b'# x/y/z/r converted from8nm units to micrometers, scale0.008\n') if path.is_relative_to(male) else b''
            path.write_bytes(header + swc)
        (manc / '.phase2_export_index.json').write_text('{}')
        (manc / 'edges').mkdir()
        (manc / 'edges/master_edges_cache.sqlite').touch()
        before_linked = {str(path): path.read_bytes() for path in native_files}
        linked_profile = ResourceProfile.load(profile_path)
        linked_profile = replace(linked_profile, resources=tuple(b for b in linked_profile.resources
            if b.kind != ResourceKind.DIGIFLY_WORKSPACE) + (ResourceBinding('linked-public',
            ResourceKind.DIGIFLY_WORKSPACE, str(public), AccessMode.READ_ONLY),))
        linked_profile.save(profile_path, replace=True)
        window.pages.setCurrentWidget(page)
        page.refresh()
        if page.linked_table.rowCount() != 1:
            raise ValueError('Workspace Male CNS folder is absent from Data Library')
        QFileDialog.getExistingDirectory = lambda *a, **kw: str(manc)
        QInputDialog.getText = lambda *a, **kw: ('full-manc-example', True)
        choose_release('manc:v1.2.1')
        QTest.mouseClick(page.register_button, Qt.MouseButton.LeftButton)
        if page.linked_table.rowCount() != 1 or len(page._all_linked) != 2:
            raise ValueError('Registered local MANC folder did not appear')
        counts = {}
        shortcuts = []
        for dataset in ('male-cns:v0.9', 'manc:v1.2.1'):
            choose_release(dataset)
            page.storage_details_button.setChecked(True)
            if page.linked_table.rowCount() != 1:
                raise ValueError('Exact release must show only its own linked library')
            page.linked_table.selectRow(0)
            if page.trash_button.isEnabled() or page.registration_button.isEnabled():
                raise ValueError('Linked folder acquired managed deletion controls')
            click_linked(page.count_linked_button, 'compact-linked-row-menu.png')
            wait_for(lambda: not page.import_in_progress, 'Read-only linked folder count did not finish')
            linked = page._linked[0]
            counts[linked.root] = page.linked_table.item(0, 3).text()
            shortcut = destination / ('library-links/' + ('male' if linked.root == male.resolve() else 'manc') + '.digifly-library.json')
            QFileDialog.getSaveFileName = lambda *a, _path=shortcut, **kw: (str(_path), '')
            click_linked(page.save_link_button)
            if not shortcut.is_file():
                raise ValueError('Save library shortcut did not save the selected release source')
            shortcuts.append(shortcut)
        if counts != {male.resolve(): '2', manc.resolve(): '1'}:
            raise ValueError('Linked library counts differ from actual fixture SWCs')
        if any(path.read_bytes() != before_linked[str(path)] for path in native_files):
            raise ValueError('A linked source file was modified')
        if any((root / 'digifly-resource.json').exists() for root in (male, manc)):
            raise ValueError('Registration wrote a managed manifest into a linked source')
        # Reopen both files through the actual app controls with workspace
        # discovery removed: this tests portable files, not cached table rows.
        current = ResourceProfile.load(profile_path)
        current = replace(current, resources=tuple(b for b in current.resources
            if b.kind != ResourceKind.DIGIFLY_WORKSPACE and b.resolved_path not in {male.resolve(), manc.resolve()}))
        current.save(profile_path, replace=True)
        page.refresh()
        if page.linked_table.rowCount() != 0:
            raise ValueError('Shortcut reload fixture retained its original external source bindings')
        def accept_link():
            modal = app.activeModalWidget()
            if not isinstance(modal, QMessageBox):
                if time.monotonic() < link_review_deadline:
                    QTimer.singleShot(40, accept_link)
                    return
                raise ValueError('Library shortcut did not request explicit read-only confirmation')
            modal.grab().save(str(destination / 'library-shortcut-review.png'))
            QTest.mouseClick(modal.button(QMessageBox.StandardButton.Yes), Qt.MouseButton.LeftButton)
        for shortcut in shortcuts:
            QFileDialog.getOpenFileName = lambda *a, _path=shortcut, **kw: (str(_path), '')
            link_review_deadline = time.monotonic() + 10
            QTimer.singleShot(250, accept_link)
            click_menu(page.add_data_button, page.load_link_button)
        if {link.root for link in page._all_linked} != {male.resolve(), manc.resolve()}:
            raise ValueError('Saved library shortcuts did not restore both exact external sources')
        if any(path.read_bytes() != before_linked[str(path)] for path in native_files):
            raise ValueError('Shortcut controls modified a source SWC')
        if not page.linked_table.rect().contains(page.linked_table.cellWidget(0, 6).geometry()):
            raise ValueError('Linked row actions escape the native table')
        page.linked_table.selectRow(0)
        page.scroll.ensureWidgetVisible(page.linked_detail)
        app.processEvents()
        if not window.grab().save(str(destination / 'linked-local-libraries.png')):
            raise ValueError('Linked library view did not render')
        # Use an explicit presentation fixture, not fabricated scientific data.
        scope_index = destination / 'scope-fixture-index'
        scope_index.mkdir()
        (scope_index / 'manifest.json').write_text(json.dumps({'rows': 2, 'represented_neurons': 3}))
        unknown_root = destination / 'unassigned-fixture'
        unknown_root.mkdir()
        (unknown_root / '3.swc').write_bytes(swc)
        scoped_profile = ResourceProfile.load(profile_path)
        scoped_profile = replace(scoped_profile, resources=(*scoped_profile.resources,
            ResourceBinding('scope-fixture', ResourceKind.DATA_SOURCE, str(destination / 'scope-fixture.parquet'),
                AccessMode.READ_ONLY, metadata={'format': 'digifly-weighted-pair-graph-v1',
                    'index': str(scope_index / 'pairs.sqlite'), 'dataset': 'manc:v1.2.1'}),
            ResourceBinding('unassigned-fixture', ResourceKind.MORPHOLOGY_SOURCE, str(unknown_root),
                AccessMode.READ_ONLY, metadata={'dataset': 'phase2-local'})))
        scoped_profile.save(profile_path, replace=True)
        scope_before = profile_path.read_bytes()
        page.refresh()
        expected_manc_count = sum(json.loads(x.manifest.read_text()).get('dataset') == 'manc:v1.2.1'
                                  for x in page._all_resources)
        if expected_manc_count < 1:
            raise ValueError('The scoped check lost the existing app-owned managed downloads')
        for dataset, linked_root, managed_count in (
                ('male-cns:v0.9', male.resolve(), 0), ('male-cns:v1.0', None, 0),
                ('manc:v1.2.3', None, 0), ('manc:v1.2.1', manc.resolve(), expected_manc_count),
                ('manc:v1.0', None, 0)):
            choose_release(dataset)
            if {x.root for x in page._linked} != ({linked_root} if linked_root else set()):
                raise ValueError('A library from a different connectome/release leaked into this view')
            if page.table.rowCount() != managed_count:
                raise ValueError('Managed downloads did not filter by exact release')
            if page.linked_card.isVisible() != bool(linked_root) or page.managed_card.isVisible() != bool(managed_count):
                raise ValueError('Empty/unrelated sections remained visible')
            if ('scope-fixture.parquet' in page.pair_graph_label.text()) != (dataset == 'manc:v1.2.1'):
                raise ValueError('Connectivity file crossed its exact release boundary')
            if page.trash_button.isEnabled() or page._selected_linked() is not None:
                raise ValueError('A stale row selection survived a release switch')
            window.grab().save(str(destination / ('scope-' + dataset.replace(':', '-').replace('.', '_') + '.png')))
            if page.table.rowCount():
                page.table.selectRow(0)
        if profile_path.read_bytes() != scope_before:
            raise ValueError('Filtering changed the saved profile')
        window.resize(1700, 1400)
        top_positions = {}
        for dataset in ('manc:v1.2.3', 'manc:v1.2.1', 'male-cns:v1.0'):
            choose_release(dataset)
            for label in (page.connectome_title, page.release_detail, page.data_heading):
                natural = label.heightForWidth(label.width()) if label.hasHeightForWidth() else label.sizeHint().height()
                if label.height() > natural + 2:
                    raise ValueError('Empty-release headings stretch apart in a tall native window')
            top_positions[dataset] = page.add_data_button.mapTo(page.scroll.widget(), QPoint()).y()
            window.grab().save(str(destination / ('tall-' + dataset.replace(':', '-').replace('.', '_') + '.png')))
        if top_positions['manc:v1.2.3'] != top_positions['manc:v1.2.1']:
            raise ValueError('MANC releases do not retain the same top action position')
        if profile_path.read_bytes() != scope_before:
            raise ValueError('Layout switching changed the saved profile')
        window.resize(1120, 760)
        choose_release('manc:v1.2.1')
        # Activate the new native menus, not a hidden legacy control or a
        # callback shortcut. All files here are isolated engineering fixtures.
        from PySide6.QtGui import QDesktopServices
        from digifly_app.core.data_library import import_local_source
        second = destination / 'compact-second-source'
        second.mkdir()
        (second / '88.swc').write_bytes(swc)
        second_resource = import_local_source(ResourceProfile.load(profile_path), second,
            provider='local', resource_id='compact-second', source_version='fixture',
            dataset='manc:v1.2.1', profile_path=profile_path)
        page.refresh()
        target_row = next(i for i, item in enumerate(page._resources) if item.root == second_resource.root)
        page.table.selectRow(0 if target_row != 0 else 1)
        urls = []
        original_open_url = QDesktopServices.openUrl
        original_directory = QFileDialog.getExistingDirectory
        original_open_file = QFileDialog.getOpenFileName
        original_question = QMessageBox.question
        dialogs_closed = []
        def close_fixture_dialog():
            modal = app.activeModalWidget()
            if modal is None:
                # Native menu focus/layout delivery can outlast the scheduled
                # opener delay. Wait for the actual modal, not a guessed clock.
                if time.monotonic() < close_fixture_deadline:
                    QTimer.singleShot(40, close_fixture_dialog)
                    return
                raise ValueError('Compact menu did not open its existing dialog')
            dialogs_closed.append(modal.windowTitle())
            modal.grab().save(str(destination / ('compact-dialog-' + str(len(dialogs_closed)) + '.png')))
            modal.reject()
        def managed_action(action, filename=None):
            row = next(i for i, item in enumerate(page._resources) if item.root == second_resource.root)
            holder = page.table.cellWidget(row, 8)
            click_menu(holder._library_button, action, filename)
        try:
            QDesktopServices.openUrl = lambda url: urls.append(str(Path(url.toLocalFile()))) or True
            managed_action(page.reveal_selected_button, 'compact-managed-row-menu.png')
            if urls != [str(second_resource.root)]:
                raise ValueError('Managed row menu opened a different selected row')
            close_fixture_deadline = time.monotonic() + 10
            QTimer.singleShot(300, close_fixture_dialog)
            managed_action(page.inspect_button)
            if not dialogs_closed:
                raise ValueError('Download details are no longer reachable')
            # Real registration changes affect only this disposable fixture.
            QMessageBox.question = lambda *a, **kw: QMessageBox.StandardButton.Yes
            managed_action(page.registration_button)
            if next(x for x in page._resources if x.root == second_resource.root).is_registered:
                raise ValueError('Row registration menu did not unregister the exact fixture')
            managed_action(page.registration_button)
            if not next(x for x in page._resources if x.root == second_resource.root).is_registered:
                raise ValueError('Row registration menu did not restore the fixture')
            trash_reviews = []
            def cancel_trash(parent, title, text, *a, **kw):
                trash_reviews.append(text)
                return QMessageBox.StandardButton.Cancel
            QMessageBox.question = cancel_trash
            managed_action(page.trash_button)
            if len(trash_reviews) != 1 or str(second_resource.root) not in trash_reviews[0]:
                raise ValueError('Trash review targeted the wrong row')
            if not (second_resource.root / 'digifly-resource.json').is_file():
                raise ValueError('Cancelled Trash action changed the fixture')
            holder = page.table.cellWidget(target_row, 8)
            stale = next(a for a in holder._library_menu.actions() if a.text() == page.reveal_selected_button.text())
            before_stale = list(urls)
            page.release_buttons['manc:v1.2.3'].setChecked(True)
            stale.trigger()
            if urls != before_stale or page._selected_resource() is not None:
                raise ValueError('A stale Compact row action survived a release switch')
            QFileDialog.getExistingDirectory = lambda *a, **kw: ''
            QFileDialog.getOpenFileName = lambda *a, **kw: ('', '')
            for action in (page.import_button, page.load_link_button):
                click_menu(page.add_data_button, action)
            for action in (page.move_library_button, page.relink_button):
                click_menu(page.library_tools_button, action, 'compact-library-tools-menu.png')
            for button, action in ((page.add_data_button, page.modeldb_button),
                                   (page.add_data_button, page.pair_graph_button),
                                   (page.library_tools_button, page.manage_trash_button)):
                close_fixture_deadline = time.monotonic() + 10
                QTimer.singleShot(300, close_fixture_dialog)
                click_menu(button, action)
            click_menu(page.library_tools_button, page.open_managed_action)
            if urls[-1] != str(ResourceProfile.load(profile_path).managed_data_root):
                raise ValueError('Library tools no longer opens the managed folder')
            if len(dialogs_closed) != 4:
                raise ValueError('A Compact menu lost an existing dialog route')
            for width in (1120, 1700):
                window.resize(width, 900)
                for choice in CONNECTOMES:
                    for release in choice.releases:
                        choose_release(release)
                        controls = (page.neuprint_button, page.register_button, page.add_data_button)
                        rects = [button.geometry() for button in controls]
                        if any(a.intersects(b) for i, a in enumerate(rects) for b in rects[i+1:]):
                            raise ValueError('Compact toolbar actions overlap')
                        if any(button.width() > 260 for button in controls):
                            raise ValueError('Compact actions stretch into full-width rows')
                choose_release('manc:v1.2.1')
                page.scroll.verticalScrollBar().setValue(0)
                QTest.qWait(80)
                window.grab().save(str(destination / f'compact-workspace-{width}.png'))
            if (second / '88.swc').read_bytes() != swc:
                raise ValueError('Compact menu tests changed their input SWC')
        finally:
            QDesktopServices.openUrl = original_open_url
            QFileDialog.getExistingDirectory = original_directory
            QFileDialog.getOpenFileName = original_open_file
            QMessageBox.question = original_question
        record.update(compact_library_layout=True, compact_library_menus=True,
                      compact_library_row_targeting=True, compact_library_stale_menu_rejected=True,
                      compact_library_dialog_routes=dialogs_closed, compact_library_widths=[1120, 1700])
        record.update(connectome_exact_release_filter=True, connectome_empty_sections_hidden=True,
                      connectome_stale_selection_cleared=True, connectome_parquet_scoped=True,
                      connectome_scope_preserves_sources=True, connectome_unknown_not_guessed=True,
                      connectome_consistent_compact_spacing=True, connectome_tall_action_top=top_positions)
        record.update(linked_local_libraries_visible=True, linked_workspace_discovery=True,
            linked_register_control=True, linked_readonly_counts=True, linked_sources_unchanged=True,
            linked_no_managed_delete_controls=True,
            library_shortcut_save_open=True, library_shortcut_explicit_review=True,
            library_shortcut_restores_without_workspace=True, library_layout_no_overlap=True,
            linked_fixture_counts={'male-cns:v0.9': 2, 'manc:v1.2.1': 1})
        # The default page now browses saved families, not acquisition batches.
        # Two variants of an already saved body must not add two neurons.
        journal('family_browser')
        duplicate_root = manc / 'DN/DNp01/42'
        duplicate_root.mkdir(parents=True)
        for name in ('42_raw.swc', '42_healed.swc'):
            (duplicate_root / name).write_bytes(swc)
        legacy = destination / 'legacy-family-fixture/PSI/802401'
        legacy.mkdir(parents=True)
        for name in ('802401_neuprint_raw.swc', '802401_neuprint_raw_radius_healed.swc'):
            (legacy / name).write_bytes(swc)
        import_local_source(ResourceProfile.load(profile_path), legacy.parents[1], provider='local',
            resource_id='legacy-family-fixture', source_version='fixture', dataset='male-cns:v1.0', profile_path=profile_path)
        before_browse = profile_path.read_bytes()
        page.refresh()
        page.storage_details_button.setChecked(False)
        browser = page.neuron_browser
        def counted(dataset):
            wait_for(lambda: browser.inventory is not None and browser.inventory.dataset == dataset,
                     'Saved family inventory did not finish for the chosen release')
        choose_release('manc:v1.2.1')
        counted('manc:v1.2.1')
        counts = {f: len(browser.inventory.family(f)) for f in browser.family_buttons}
        if counts != {'AN': 1, 'DN': 2, 'IN': 0, 'MN': 1, 'SN': 0, 'UNKNOWN': 2}:
            raise ValueError('Saved family count double-counted variants, history or matching sources: ' + str(counts))
        expected_dn = browser.inventory.family('DN')
        if len(next(n for n in expected_dn if n.body_id == '42').paths) != 3:
            raise ValueError('Saved browser lost duplicate-location information')
        if page.managed_card.isVisible() or page.linked_card.isVisible():
            raise ValueError('Download batches still replace the primary family browser')
        for width in (1120, 1700):
            window.resize(width, 900)
            page.scroll.ensureWidgetVisible(browser)
            QTest.qWait(100)
            rects = [b.geometry() for b in browser.family_buttons.values()]
            if any(a.intersects(b) for i, a in enumerate(rects) for b in rects[i+1:]):
                raise ValueError('Saved family folders overlap')
            if browser.stack.height() > 150 or browser.height() > 330:
                raise ValueError('Hidden type/ID pages stretched the compact family folder grid')
            window.grab().save(str(destination / f'family-folders-{width}.png'))
        QTest.mouseClick(browser.family_buttons['DN'], Qt.MouseButton.LeftButton)
        if browser.row_values[0] != ('DNp01', '2  ›'):
            raise ValueError('Family click did not open the saved neuron types')
        QTest.mouseClick(browser.row_buttons[0], Qt.MouseButton.LeftButton)
        if {row[0] for row in browser.row_values} != {'42', '10000'}:
            raise ValueError('Type click did not open its saved neurons')
        row = next(i for i, value in enumerate(browser.row_values) if value[0] == '42')
        if any(button.isCheckable() for button in browser.row_buttons):
            raise ValueError('Saved neuron rows must activate, not merely toggle a checkbox')
        QTest.mouseClick(browser.row_buttons[row], Qt.MouseButton.LeftButton)
        if not browser.open_folder.isEnabled() or '42' not in browser.location.text():
            raise ValueError('Saved-neuron location is unavailable')
        original_open_url = QDesktopServices.openUrl
        opened = []
        QDesktopServices.openUrl = lambda url: opened.append(str(Path(url.toLocalFile()))) or True
        try:
            QTest.mouseClick(browser.open_folder, Qt.MouseButton.LeftButton)
        finally:
            QDesktopServices.openUrl = original_open_url
        if opened != [str(next(n for n in expected_dn if n.body_id == '42').paths[0].parent)]:
            raise ValueError('Saved-neuron folder control targeted another neuron')
        page.scroll.ensureWidgetVisible(browser.row_list)
        QTest.qWait(80)
        window.grab().save(str(destination / 'family-neurons.png'))
        QTest.mouseClick(browser.back, Qt.MouseButton.LeftButton)
        QTest.mouseClick(browser.back, Qt.MouseButton.LeftButton)
        from PySide6.QtGui import QAccessible
        from PySide6.QtWidgets import QTableWidget
        if browser.findChildren(QTableWidget):
            raise ValueError('Saved browser retained the mutable accessibility table')
        for repeat in range(3):
            QTest.mouseClick(browser.family_buttons['DN'], Qt.MouseButton.LeftButton)
            accessible = QAccessible.queryAccessibleInterface(browser.row_buttons[0])
            if not accessible.text(QAccessible.Text.Name).startswith('Open type DNp01'):
                raise ValueError('Saved type row has no usable accessible button identity')
            QTest.mouseClick(browser.row_buttons[0], Qt.MouseButton.LeftButton)
            accessible = QAccessible.queryAccessibleInterface(browser.row_buttons[0])
            if not accessible.text(QAccessible.Text.Name).startswith('Neuron '):
                raise ValueError('Saved neuron row has no accessible button identity')
            QTest.mouseClick(browser.back, Qt.MouseButton.LeftButton)
            QTest.mouseClick(browser.back, Qt.MouseButton.LeftButton)
        QTest.mouseClick(browser.family_buttons['AN'], Qt.MouseButton.LeftButton)
        if len(browser.row_buttons) != 1 or browser.row_values[0][0] != 'AN-example':
            raise ValueError('Back/family navigation did not restore the right types')
        choose_release('male-cns:v1.0')
        counted('male-cns:v1.0')
        if len(browser.inventory.neurons) != 1 or browser.inventory.neurons[0].neuron_type != 'PSI':
            raise ValueError('Legacy raw/healed acquisition must count as one saved PSI neuron')
        window.grab().save(str(destination / 'family-legacy-male-v1.png'))
        choose_release('male-cns:v0.9')
        QTest.mouseClick(page.back_to_connectomes, Qt.MouseButton.LeftButton)
        QTest.mouseClick(picker.buttons['manc'], Qt.MouseButton.LeftButton)
        page.release_buttons['manc:v1.2.3'].setChecked(True)
        if browser.inventory is None and not all('not ready' in b.accessibleName() for b in browser.family_buttons.values()):
            raise ValueError('A pending count still announces another release’s old value')
        counted('manc:v1.2.3')
        if browser.inventory.neurons or browser.stack.currentIndex() != 0 or browser.open_folder.isEnabled():
            raise ValueError('Late saved-neuron results or actions leaked across a release switch')
        QTest.mouseClick(browser.family_buttons['DN'], Qt.MouseButton.LeftButton)
        if browser.row_buttons or 'No saved' not in browser.page_label.text():
            raise ValueError('An empty family cannot be browsed honestly')
        window.grab().save(str(destination / 'family-empty-release.png'))
        choose_release('manc:v1.2.1')
        counted('manc:v1.2.1')
        if profile_path.read_bytes() != before_browse or any(p.read_bytes() != swc for p in duplicate_root.glob('*.swc')):
            raise ValueError('Family browsing changed saved settings or SWCs')
        record.update(family_browser_counts=True, family_browser_unique_neurons=True,
            family_browser_drilldown=True, family_browser_release_isolation=True,
            family_browser_readonly=True, family_browser_legacy_variants=True,
            family_browser_default_folders=True, family_browser_pending_labels_cleared=True,
            family_browser_button_rows=True, family_browser_accessible_back=True,
            family_browser_plain_neuron_buttons=True, family_browser_widths=[1120, 1700],
            family_browser_fixture_counts=counts)
        journal('circuit_library_bridge')
        # Use actual page navigation and actual Load controls. No simulator or
        # live downloader is involved in this tiny engineering-only fixture.
        page.scroll.ensureWidgetVisible(page.circuit_button)
        QTest.qWait(50)
        QTest.mouseClick(page.circuit_button, Qt.MouseButton.LeftButton)
        builder = window.circuit_builder_page
        if window.pages.currentWidget() is not builder or builder._selected_source().dataset != 'manc:v1.2.1':
            raise ValueError('Data Library did not hand its exact release to Circuit Builder')
        wait_for(lambda: builder.saved_browser.inventory is not None,
                 'Circuit saved SWC union did not finish indexing')
        if {(n.server, n.body_id) for n in builder.saved_browser.inventory.neurons} != {
                (n.server, n.body_id) for n in browser.inventory.neurons}:
            raise ValueError('Circuit Builder and Data Library disagree on saved neurons')
        if builder.more_options.is_expanded or not builder.connectome_combo.isHidden():
            raise ValueError('Old source folder picker or dense options are still primary')
        for width in (1120, 1700):
            window.resize(width, 900)
            QTest.qWait(100)
            widgets = (builder.family_button, builder.release_button, builder.library_button,
                       builder.query_edit, builder.browse_button, builder.assemble_button)
            boxes = [(w, w.mapTo(window, QPoint(0, 0)), w.size()) for w in widgets]
            for w, point, size in boxes:
                if point.x() < 0 or point.x() + size.width() > window.width():
                    raise ValueError('Compact circuit control overflows the window: ' +
                        str(w.accessibleName() or w.objectName()) + f' x={point.x()} width={size.width()} window={window.width()}')
            window.grab().save(str(destination / f'circuit-library-compact-{width}.png'))
        QTest.mouseClick(builder.browse_button, Qt.MouseButton.LeftButton)
        cb = builder.saved_browser
        for width in (1120, 1700):
            window.resize(width, 900)
            QTest.qWait(100)
            parent_rect = cb.stack.currentWidget().rect()
            boxes = [button.geometry() for button in cb.family_buttons.values()]
            if any(not parent_rect.contains(box) for box in boxes) or any(
                    a.intersects(b) for i, a in enumerate(boxes) for b in boxes[i+1:]):
                raise ValueError('Expanded Circuit family folders overlap or escape their panel')
            for button in cb.family_buttons.values():
                builder.page_scroll.ensureWidgetVisible(button)
                QTest.qWait(20)
                point = button.mapTo(builder.page_scroll.viewport(), QPoint(0, 0))
                if not builder.page_scroll.viewport().rect().contains(point):
                    raise ValueError('A Circuit family folder cannot be scrolled into view')
            builder.page_scroll.ensureWidgetVisible(cb.family_buttons['DN'])
            window.grab().save(str(destination / f'circuit-library-browse-{width}.png'))
        record['circuit_library_expanded_browse_layout'] = True
        builder.page_scroll.ensureWidgetVisible(cb.family_buttons['DN'])
        QTest.mouseClick(cb.family_buttons['DN'], Qt.MouseButton.LeftButton)
        QTest.mouseClick(cb.row_buttons[0], Qt.MouseButton.LeftButton)
        QTest.mouseClick(cb.row_buttons[0], Qt.MouseButton.LeftButton)
        builder.query_edit.clear()
        builder.page_scroll.ensureWidgetVisible(cb.choose_neuron)
        QTest.mouseClick(cb.choose_neuron, Qt.MouseButton.LeftButton)
        if not builder.query_edit.text() or builder.loaded_records:
            raise ValueError('Saved-neuron selection must add a query without automatically loading')
        builder.page_scroll.ensureWidgetVisible(builder.browse_button)
        QTest.mouseClick(builder.browse_button, Qt.MouseButton.LeftButton)
        builder.page_scroll.verticalScrollBar().setValue(0)
        builder.query_edit.setText('DNp01, TTMn, PSI')
        QTest.mouseClick(builder.assemble_button, Qt.MouseButton.LeftButton)
        if set(builder.loaded_records) != {'42', '43', '45', '10000'}:
            raise ValueError('Connectome union Load lost a linked or downloaded neuron: ' + builder.viewport_summary.text())
        frozen = builder.circuit_spec().to_dict()
        if set(frozen['connectome']['swc_paths']) != set(builder.loaded_records):
            raise ValueError('The new circuit did not pin its selected physical SWCs')
        from digifly_app.core.circuit import CircuitSpec
        builder.set_circuit_spec(CircuitSpec.from_dict(frozen))
        builder.load_saved_assets()
        if set(builder.loaded_records) != {'42', '43', '45', '10000'}:
            raise ValueError('Saved virtual-library circuit could not reopen its exact assets')
        QTest.mouseClick(builder.library_button, Qt.MouseButton.LeftButton)
        if window.pages.currentWidget() is not page or page._display_dataset != 'manc:v1.2.1':
            raise ValueError('Circuit Builder did not return to its matching Data Library release')
        builder.select_library_release('manc:v1.2.3')
        wait_for(lambda: builder.saved_browser.inventory is not None and
                 builder.saved_browser.inventory.dataset == 'manc:v1.2.3', 'Empty circuit release did not finish')
        if builder.saved_browser.inventory.neurons or builder.assemble_button.isEnabled() or builder.loaded_records:
            raise ValueError('Circuit release switch retained stale neurons or enabled an empty load')
        if profile_path.read_bytes() != before_browse:
            raise ValueError('Circuit selection wrote the machine profile')
        window.pages.setCurrentWidget(builder)
        chooser_positions = []
        for width in (1120, 1700):
            window.resize(width, 900)
            builder.page_scroll.verticalScrollBar().setValue(0)
            for kind, control, following in (
                    ('simulator', builder.engine_button, builder.more_options),
                    ('connectome', builder.family_button, builder.source_summary),
                    ('simulator', builder.engine_button, builder.more_options),
                    ('release', builder.release_button, builder.source_summary)):
                builder.page_scroll.ensureWidgetVisible(control)
                QTest.mouseClick(control, Qt.MouseButton.LeftButton)
                QTest.qWait(50)
                panel = builder.choice_panel
                top = panel.mapTo(window, QPoint(0, 0)).y()
                control_bottom = control.mapTo(window, QPoint(0, control.height())).y()
                following_top = following.mapTo(window, QPoint(0, 0)).y()
                if (not panel.isVisible() or top < control_bottom or
                        top + panel.height() > following_top or
                        any(not panel.rect().contains(b.geometry()) for b in builder.choice_buttons)):
                    raise ValueError(f'{kind} choices are not contained below their own control at width{width}')
                chooser_positions.append(dict(width=width, kind=kind,
                    control_bottom=control_bottom, panel_top=top,
                    panel_bottom=top + panel.height(), following_top=following_top))
                window.grab().save(str(destination / f'circuit-library-{kind}-choices-{width}.png'))
            QTest.mouseClick(builder.choice_buttons[-1], Qt.MouseButton.LeftButton)
            if builder.choice_panel.isVisible():
                raise ValueError('Close choices did not hide the moved panel')
        record.update(circuit_library_chooser_anchors=True, circuit_library_chooser_positions=chooser_positions)
        QTest.mouseClick(builder.family_button, Qt.MouseButton.LeftButton)
        if not builder.choice_panel.isVisible() or len(builder.choice_buttons) != 7:
            raise ValueError('Compact connectome control does not open inline ordinary choices')
        QTest.mouseClick(builder.choice_buttons[0], Qt.MouseButton.LeftButton)
        if builder.family_combo.currentData() != 'male_cns' or builder.choice_panel.isVisible():
            raise ValueError('Inline connectome choice did not select and close')
        QTest.mouseClick(builder.release_button, Qt.MouseButton.LeftButton)
        QTest.mouseClick(builder.choice_buttons[1], Qt.MouseButton.LeftButton)
        if builder._selected_source().dataset != 'male-cns:v0.9':
            raise ValueError('Inline release choice did not change the exact release')
        QTest.mouseClick(builder.engine_button, Qt.MouseButton.LeftButton)
        QTest.mouseClick(builder.choice_buttons[1], Qt.MouseButton.LeftButton)
        if builder.selected_engine_key() != 'neuron':
            raise ValueError('Inline simulator choice did not apply')
        record.update(circuit_library_shared_choices=True, circuit_library_shared_inventory=True,
            circuit_library_union_load=True, circuit_library_saved_paths=True,
            circuit_library_inline_browse=True, circuit_library_exact_handoff=True,
            circuit_library_release_isolation=True, circuit_library_compact_layout=True,
            circuit_library_readonly=True, circuit_library_inline_choices=True,
            circuit_library_widths=[1120, 1700])
        journal('connectome_orientation_and_experiment_snapshot')
        from PySide6.QtCore import QPointF
        from math import isclose
        orientation_rows = []
        for dataset, expected_roll in (('male-cns:v0.9', 180.0), ('male-cns:v1.0', 180.0),
                                       ('manc:v1.2.1', 0.0)):
            builder.select_library_release(dataset)
            wait_for(lambda: builder.saved_browser.inventory is not None and
                     builder.saved_browser.inventory.dataset == dataset, 'Orientation inventory did not finish')
            body = builder.saved_browser.inventory.neurons[0].body_id
            builder.query_edit.setText(body)
            builder.page_scroll.ensureWidgetVisible(builder.assemble_button)
            QTest.qWait(50)
            QTest.mouseClick(builder.assemble_button, Qt.MouseButton.LeftButton)
            viewport = builder.viewport
            if (not builder._selected_source().key.startswith('library:') or
                    set(builder.loaded_morphologies) != {body} or viewport.roll_degrees != expected_roll):
                raise ValueError('Shared connectome lost its display orientation: ' + str({
                    'dataset': dataset, 'key': builder._selected_source().key, 'wanted': body,
                    'loaded': tuple(builder.loaded_morphologies), 'roll': viewport.roll_degrees,
                    'load_enabled': builder.assemble_button.isEnabled(), 'summary': builder.viewport_summary.text()}))
            builder.page_scroll.ensureWidgetVisible(builder.full_skeletons_button)
            QTest.mouseClick(builder.full_skeletons_button, Qt.MouseButton.LeftButton)
            geometry = viewport._vertex_data
            morphology = builder.loaded_morphologies[body]
            original_swc = Path(morphology.record.swc_path).read_bytes()
            segment = morphology.segments[0]
            midpoint = tuple((a + b) / 2 for a, b in zip(segment.parent, segment.child))
            projected = viewport._project(midpoint, viewport._mvp())
            if projected is None or viewport._pick(QPointF(*projected))[0] != body:
                raise ValueError('Displayed skeleton picking no longer matches geometry: ' + dataset)
            viewport.roll_degrees = 0.0
            zero_projection = viewport._project(midpoint, viewport._mvp())
            viewport.setFocus()
            QTest.keyClick(viewport, Qt.Key.Key_R)
            reset_projection = viewport._project(midpoint, viewport._mvp())
            if viewport.roll_degrees != expected_roll or geometry != viewport._vertex_data:
                raise ValueError('Reset did not preserve raw geometry and the release camera')
            if expected_roll and not (isclose(reset_projection[0], viewport.width() - zero_projection[0], abs_tol=.02)
                                     and isclose(reset_projection[1], viewport.height() - zero_projection[1], abs_tol=.02)):
                raise ValueError('Male CNS correction is not a camera-space half-turn')
            frozen_orientation = builder.circuit_spec().to_dict()
            builder.set_circuit_spec(CircuitSpec.from_dict(frozen_orientation))
            builder.load_saved_assets()
            experiment = window.experiment_page
            if (builder.viewport.roll_degrees != expected_roll or
                    experiment.target_region_viewport.roll_degrees != expected_roll or
                    experiment._circuit.connectome != builder.circuit_spec().connectome):
                raise ValueError('Saved circuit or Experiment Builder lost the exact source/camera')
            if Path(morphology.record.swc_path).read_bytes() != original_swc:
                raise ValueError('Orientation changed an SWC file')
            builder.page_scroll.ensureWidgetVisible(builder.viewport)
            QTest.qWait(80)
            filename = 'orientation-' + dataset.replace(':', '-') + '.png'
            if not window.grab().save(str(destination / filename)):
                raise ValueError('Could not save the rendered orientation control')
            orientation_rows.append({'dataset': dataset, 'key': morphology.record.connectome_key,
                'roll': expected_roll, 'pick_matches': True, 'reset_matches': True,
                'saved_reload_matches': True, 'experiment_camera_matches': True, 'swc_unchanged': True})
        record.update(connectome_orientation=True, connectome_orientation_rows=orientation_rows,
                      experiment_source_snapshot=True)
        journal('ttmn_saved_label_merge')
        # Reproduce the real old-download shape: the same name/body appears
        # under MN locally and UNKNOWN in an earlier download. Identical tiny
        # SWCs avoid a modal here; differing-copy refusal has its own regression.
        from digifly_app.core.saved_neurons import SavedSource, scan_saved_neurons
        unknown_ttmn = destination / 'ttmn-labels/old-download/export_swc'
        label_files = []
        for body in ('800146', '804642'):
            for root, family in ((male, 'MN'), (unknown_ttmn, 'UNKNOWN')):
                path = root / f'{family}/TTMn/{body}/{body}.swc'
                path.parent.mkdir(parents=True, exist_ok=True)
                if not path.exists():
                    path.write_bytes(native_files[0].read_bytes())
                label_files.append(path)
        original_labels = {p: p.read_bytes() for p in label_files}
        current_profile = ResourceProfile.load(profile_path)
        replace(current_profile, resources=(*current_profile.resources, ResourceBinding(
            'old-ttmn-labels', ResourceKind.MORPHOLOGY_SOURCE, str(unknown_ttmn), AccessMode.READ_ONLY,
            metadata={'dataset': 'male-cns:v0.9'}))).save(profile_path, replace=True)
        label_profile = profile_path.read_bytes()
        builder.refresh_connectomes()
        page.refresh()
        window.pages.setCurrentWidget(page)
        page.open_connectome('male-cns:v0.9')
        counted('male-cns:v0.9')
        ttmns = tuple(n for n in browser.inventory.family('MN') if n.neuron_type == 'TTMn')
        if {n.body_id for n in ttmns} != {'800146', '804642'} or any(n.label_conflict for n in ttmns):
            raise ValueError('A missing old-download family still hides the two TTMn names')
        if any(n.source_labels != (('MN', 'TTMn'), ('UNKNOWN', 'TTMn')) for n in ttmns):
            raise ValueError('TTMn merge discarded original saved labels')
        page.scroll.ensureWidgetVisible(browser.family_buttons['MN'])
        QTest.mouseClick(browser.family_buttons['MN'], Qt.MouseButton.LeftButton)
        row = next(i for i, value in enumerate(browser.row_values) if value[0] == 'TTMn')
        QTest.mouseClick(browser.row_buttons[row], Qt.MouseButton.LeftButton)
        if {row[0] for row in browser.row_values} != {'800146', '804642'}:
            raise ValueError('Data Library TTMn folder does not contain both saved bodies')
        QTest.mouseClick(browser.row_buttons[0], Qt.MouseButton.LeftButton)
        if any(label not in browser.location.text() for label in ('MN / TTMn', 'UNKNOWN / TTMn')):
            raise ValueError('Original TTMn annotations are unavailable in saved-neuron details')
        window.grab().save(str(destination / 'ttmn-library-labels.png'))
        page.scroll.ensureWidgetVisible(page.circuit_button)
        QTest.mouseClick(page.circuit_button, Qt.MouseButton.LeftButton)
        wait_for(lambda: builder.saved_browser.inventory is not None and
                 builder.saved_browser.inventory.dataset == 'male-cns:v0.9', 'TTMn circuit inventory did not finish')
        builder.query_edit.setText('TTMn')
        builder.page_scroll.ensureWidgetVisible(builder.assemble_button)
        QTest.mouseClick(builder.assemble_button, Qt.MouseButton.LeftButton)
        if (set(builder.loaded_records) != {'800146', '804642'} or
                any((r.family, r.neuron_type) != ('MN', 'TTMn') for r in builder.loaded_records.values())):
            raise ValueError('Circuit Builder name query did not load both correctly labelled TTMns')
        frozen_labels = builder.circuit_spec().to_dict()
        builder.set_circuit_spec(CircuitSpec.from_dict(frozen_labels))
        builder.load_saved_assets()
        experiment = window.experiment_page
        experiment.stimulus_targets.setText('TTMn')
        if set(experiment._target_neuron_ids()[0]) != {'800146', '804642'} or experiment._target_neuron_ids()[1]:
            raise ValueError('Experiment Builder TTMn named targets disagree with the saved circuit')
        builder.page_scroll.ensureWidgetVisible(builder.viewport)
        window.grab().save(str(destination / 'ttmn-circuit-labels.png'))
        if profile_path.read_bytes() != label_profile or any(p.read_bytes() != raw for p, raw in original_labels.items()):
            raise ValueError('TTMn lookup changed saved settings or original SWCs')
        genuine_root = destination / 'ttmn-labels/genuine-conflict'
        for relative in ('MN/TTMn/7/7.swc', 'UNKNOWN/AnotherType/7/7.swc'):
            path = genuine_root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(swc)
        conflict, = scan_saved_neurons((SavedSource(genuine_root, 'male-cns:v0.9'),), 'male-cns:v0.9').neurons
        if not conflict.label_conflict or conflict.neuron_type != 'Conflicting labels' or len(conflict.source_labels) != 2:
            raise ValueError('TTMn repair hid a genuine type disagreement')
        record.update(ttmn_label_merge=True, ttmn_named_circuit=True,
            ttmn_named_experiment=True, ttmn_labels_readonly=True, ttmn_label_conflicts_preserved=True,
            ttmn_label_bodies=['800146', '804642'])
        classification_rows = []
        for dataset, bodies in (('male-cns:v1.0', ('800146', '804642')),
                                ('manc:v1.2.1', ('10068', '10110'))):
            old_root = destination / 'ttmn-labels' / dataset.replace(':', '-') / 'export_swc'
            protected = {}
            for body in bodies:
                path = old_root / f'UNKNOWN/TTMn/{body}/{body}.swc'
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(swc)
                protected[path] = path.read_bytes()
            if dataset == 'manc:v1.2.1':
                # Real MANC10068 additionally has an old instance-named folder.
                # Its type is TTMn in the supplied official annotation table.
                for body, side in zip(bodies, ('L', 'R')):
                    path = old_root / f'UNKNOWN/MNwm34_PDMNp_{side}/{body}/{body}_healed.swc'
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(swc)
                    protected[path] = path.read_bytes()
            profile = ResourceProfile.load(profile_path)
            replace(profile, resources=(*profile.resources, ResourceBinding(
                'ttmn-' + dataset.replace(':', '-'), ResourceKind.MORPHOLOGY_SOURCE,
                str(old_root), AccessMode.READ_ONLY, metadata={'dataset': dataset}))).save(profile_path, replace=True)
            builder.refresh_connectomes()
            page.refresh()
            window.pages.setCurrentWidget(page)
            page.open_connectome(dataset)
            counted(dataset)
            expected = set(bodies) | ({'43'} if dataset.startswith('manc:') else set())
            if {n.body_id for n in browser.inventory.family('MN') if n.neuron_type == 'TTMn'} != expected:
                raise ValueError('Legacy TTMn is still UNKNOWN or crosses releases: ' + dataset)
            page.scroll.ensureWidgetVisible(page.circuit_button)
            QTest.mouseClick(page.circuit_button, Qt.MouseButton.LeftButton)
            wait_for(lambda: builder.saved_browser.inventory is not None and
                     builder.saved_browser.inventory.dataset == dataset, 'TTMn release inventory did not finish')
            builder.query_edit.setText('TTMn')
            builder.page_scroll.ensureWidgetVisible(builder.assemble_button)
            QTest.mouseClick(builder.assemble_button, Qt.MouseButton.LeftButton)
            experiment.stimulus_targets.setText('TTMn')
            if (set(builder.loaded_records) != expected or
                    any(r.family != 'MN' or r.neuron_type != 'TTMn' for r in builder.loaded_records.values()) or
                    set(experiment._target_neuron_ids()[0]) != expected or experiment._target_neuron_ids()[1]):
                raise ValueError('TTMn named Circuit/Experiment selection differs across releases: ' + dataset)
            if dataset == 'manc:v1.2.1':
                experiment.stimulus_targets.setText('MNwm34_PDMNp_L')
                if experiment._target_neuron_ids() != (('10068',), ()):
                    raise ValueError('MANC instance alias does not reach its own Experiment target')
                experiment.stimulus_targets.setText('TTMn')
            if any(p.read_bytes() != data for p, data in protected.items()) or (old_root / 'MN').exists():
                raise ValueError('Legacy classification moved or rewrote existing SWCs')
            classification_rows.append({'dataset': dataset, 'bodies': sorted(expected), 'type': 'TTMn', 'family': 'MN'})
            window.grab().save(str(destination / ('ttmn-' + dataset.replace(':', '-') + '.png')))
        if not (resources[-1].root / 'export_swc/MN/TTMn/43/43.swc').is_file():
            raise ValueError('The app-owned TTMn download did not use the MN storage folder')
        record.update(ttmn_motor_classification=True, ttmn_motor_classification_rows=classification_rows,
                      ttmn_motor_download_folder=True, ttmn_instance_aliases=True)
        journal('built_in_connectivity_reader')
        from digifly_app.core.connectivity_download import export_for, SERVER
        from digifly_app.ui.connectivity_download import ConnectivityDownloadDialog, graph_bindings
        from digifly_app.core.circuit_library import library_sources
        from PySide6.QtCore import QProcess
        import pyarrow as pa
        import pyarrow.feather as feather
        graph_fixture = destination / 'tiny-connectivity.feather'
        feather.write_feather(pa.table({'body_pre': pa.array([42, 2**53+7], type=pa.int64()),
            'body_post': pa.array([2**53+7, 42], type=pa.int64()), 'weight': [1, 9]}), graph_fixture)
        window.pages.setCurrentWidget(page)
        for family in CONNECTOMES:
            for release in family.releases:
                page.open_connectome(release)
                if not page.connectivity_card.isVisible() or page.connectivity_download_button.isEnabled() != export_for(release).available:
                    raise ValueError('Connectivity download availability leaked across releases: ' + release)
        page.open_connectome('male-cns:v1.0')
        window.pages.setCurrentWidget(page)
        for width in (1120, 1700):
            window.resize(width, 900)
            page.scroll.ensureWidgetVisible(page.connectivity_download_button)
            QTest.qWait(50)
            window.grab().save(str(destination / f'connectivity-card-{width}.png'))
        graph_checked = []
        def exercise_graph_dialog():
            dialog = page._connectivity_dialog
            try:
                if not isinstance(dialog, ConnectivityDownloadDialog):
                    raise ValueError('Connectivity card did not open the built-in reader')
                from PySide6.QtGui import QDesktopServices
                from PySide6.QtCore import QUrl
                from digifly_app.core.connectivity_download import connectivity_root
                native_open_url = QDesktopServices.openUrl
                opened = []
                def observe_open(url):
                    opened.append(url.toString())
                    return True
                before_folder = profile_path.read_bytes()
                folder = connectivity_root(ResourceProfile.load(profile_path).managed_data_root, dialog.dataset)
                if folder.exists():
                    raise ValueError('Folder regression must start before the first download')
                try:
                    QDesktopServices.openUrl = observe_open
                    QTest.mouseClick(dialog.open_folder, Qt.MouseButton.LeftButton)
                    if not folder.is_dir() or tuple(folder.iterdir()) or 'Opening connectivity folder' not in dialog.status.text():
                        raise ValueError('Open folder did not create and open the empty exact-release directory')
                    if opened != [QUrl.fromLocalFile(str(folder)).toString()] or profile_path.read_bytes() != before_folder:
                        raise ValueError('Folder opening changed bindings or requested the wrong path')
                    QTest.mouseClick(dialog.open_folder, Qt.MouseButton.LeftButton)
                    if len(opened) != 2 or tuple(folder.iterdir()):
                        raise ValueError('Opening an existing directory changed its files')
                    QDesktopServices.openUrl = lambda url: False
                    QTest.mouseClick(dialog.open_folder, Qt.MouseButton.LeftButton)
                    if 'Could not open connectivity folder' not in dialog.status.text() or str(folder) not in dialog.status.text():
                        raise ValueError('Desktop folder-launch refusal was silently ignored')
                    QDesktopServices.openUrl = observe_open
                    QTest.mouseClick(dialog.info, Qt.MouseButton.LeftButton)
                    if opened[-1] != dialog.export.info:
                        raise ValueError('Official source button requested the wrong URL')
                    # File/browser handlers above are recorded, not independent
                    # evidence of an external file-manager/browser window.
                    record.update(connectivity_folder_controls=True,
                        connectivity_all_dialog_buttons=True,
                        connectivity_folder_observation={'missing_created': True, 'existing_opened': True,
                            'launch_failure_visible': True, 'profile_unchanged': True,
                            'official_source_requested': True, 'external_launch_intercepted': True})
                    dialog.grab().save(str(destination / 'connectivity-folder-controls.png'))
                finally:
                    QDesktopServices.openUrl = native_open_url
                unregistered = folder / 'retained-unregistered.csv'
                unregistered.write_text('bodyId_pre,bodyId_post,weight\n1,2,3\n')
                kept_bytes = unregistered.read_bytes()
                QMessageBox.question = lambda *a, **k: QMessageBox.StandardButton.No
                QTest.mouseClick(dialog.download, Qt.MouseButton.LeftButton)
                if (dialog._request_file is not None or dialog.process.state() != QProcess.ProcessState.NotRunning
                        or 'Kept existing' not in dialog.status.text() or unregistered.read_bytes() != kept_bytes):
                    raise ValueError('An unregistered existing file did not prevent transfer before approval')
                record.update(connectivity_existing_disk_files_prompt=True)
                QMessageBox.question = lambda *a, **k: QMessageBox.StandardButton.Yes
                QFileDialog.getOpenFileName = lambda *a, **k: (str(graph_fixture), 'Connectivity')
                QTest.mouseClick(dialog.import_file, Qt.MouseButton.LeftButton)
                wait_for(lambda: dialog.process.state() == QProcess.ProcessState.NotRunning,
                         'Built-in connectivity child process did not finish')
                if 'Ready' not in dialog.status.text():
                    raise ValueError('Built-in reader did not register: ' + dialog.status.text())
                measured = ResourceProfile.load(profile_path)
                old, = graph_bindings(measured, 'male-cns:v1.0')
                old_bytes = Path(old.path).read_bytes()
                library = library_sources(measured, ('male-cns:v1.0', 'male-cns:v0.9'))
                if library[0].pair_graph_path != old.path or library[1].pair_graph_path:
                    raise ValueError('Connectivity did not attach to only its exact Circuit release')
                from digifly_app.core.pair_graph import pair_rows
                from digifly_app.core.circuit import ConnectomeRef
                source_ref = ConnectomeRef('unit', 'unit', '', 'male-cns:v1.0',
                    old.path, str(old.metadata['index']), old.metadata['source_sha256'])
                rows = pair_rows(source_ref, '42', str(2**53+7))
                if {r['total_weight'] for r in rows} != {1, 9}:
                    raise ValueError('Connectivity conversion lost exact 64-bit IDs or weak edges')
                builder.refresh_connectomes()
                builder.select_library_release('male-cns:v1.0')
                wait_for(lambda: builder.saved_browser.inventory is not None and
                    builder.saved_browser.inventory.dataset == 'male-cns:v1.0', 'Downloaded graph handoff did not finish')
                builder.query_edit.setText('802401')
                builder.assemble_circuit()
                attached = window.experiment_page._circuit.connectome
                if (attached.pair_graph_path != old.path or attached.pair_graph_index != old.metadata['index'] or
                        attached.pair_graph_sha256 != old.metadata['source_sha256'] or
                        Path(old.path).name not in window.experiment_page.circuit_detail.text() or
                        'choose an explicit' not in window.experiment_page.circuit_detail.text().casefold()):
                    raise ValueError('Experiment Builder lost the selected downloaded table or hid its execution boundary')
                record['experiment_downloaded_connectivity'] = True
                dialog.grab().save(str(destination / 'connectivity-ready.png'))
                QMessageBox.question = lambda *a, **k: QMessageBox.StandardButton.No
                QTest.mouseClick(dialog.download, Qt.MouseButton.LeftButton)
                if 'Kept existing' not in dialog.status.text() or dialog.process.state() != QProcess.ProcessState.NotRunning:
                    raise ValueError('Download button did not honor existing-connectivity refusal')
                QTest.mouseClick(dialog.import_file, Qt.MouseButton.LeftButton)
                if 'Kept existing' not in dialog.status.text() or dialog.process.state() != QProcess.ProcessState.NotRunning:
                    raise ValueError('Default Keep did not prevent connectivity replacement')
                QMessageBox.question = lambda *a, **k: QMessageBox.StandardButton.Yes
                QTest.mouseClick(dialog.import_file, Qt.MouseButton.LeftButton)
                wait_for(lambda: dialog.process.state() == QProcess.ProcessState.NotRunning,
                         'Reviewed connectivity replacement did not finish')
                current, = graph_bindings(ResourceProfile.load(profile_path), 'male-cns:v1.0')
                if current.path == old.path or Path(old.path).read_bytes() != old_bytes:
                    raise ValueError('Connectivity replacement overwrote its retained prior file')
                before_cancel = profile_path.read_bytes()
                current_bytes = Path(current.path).read_bytes()
                # A real compiled child writes a bounded file before Cancel.
                # This is conversion cancellation, not a live network transfer.
                from digifly_app.core.connectivity_download import checked_attempt
                cancel_fixture = destination / 'cancel-during-conversion.feather'
                feather.write_feather(pa.table({'body_pre': pa.array(range(1, 262145), type=pa.int64()),
                    'body_post': pa.array([262145] * 262144, type=pa.int64()),
                    'weight': pa.array([1] * 262144, type=pa.int64())}), cancel_fixture, chunksize=4096)
                from digifly_app.core.pair_graph import sha
                input_hash = sha(cancel_fixture)
                QFileDialog.getOpenFileName = lambda *a, **k: (str(cancel_fixture), 'Connectivity')
                QTest.mouseClick(dialog.import_file, Qt.MouseButton.LeftButton)
                cancelled_folder = checked_attempt(dialog._attempt)
                partial = cancelled_folder / 'ready/source-columns.parquet'
                wait_for(lambda: partial.is_file() and partial.stat().st_size > 0,
                    'Cancellation check did not observe actual worker output before stopping')
                if dialog.process.state() == QProcess.ProcessState.NotRunning:
                    raise ValueError('Cancellation must occur during work, not after registration')
                observed_partial_bytes = partial.stat().st_size
                QTest.mouseClick(dialog.close_button, Qt.MouseButton.LeftButton)
                if (profile_path.read_bytes() != before_cancel or dialog.isVisible() or cancelled_folder.exists()
                        or dialog.process.state() != QProcess.ProcessState.NotRunning):
                    raise ValueError('Cancel did not stop its child and remove only the unfinished attempt')
                if (sha(cancel_fixture) != input_hash or Path(old.path).read_bytes() != old_bytes
                        or Path(current.path).read_bytes() != current_bytes or unregistered.read_bytes() != kept_bytes):
                    raise ValueError('Cancellation modified an original input or an earlier saved graph')
                record.update(connectivity_cancel_removes_partials=True,
                    connectivity_cancel_stops_worker=True, connectivity_cancel_preserves_inputs=True,
                    connectivity_cancel_observation={'phase': 'actual_compiled_import_conversion',
                        'partial_bytes_observed': observed_partial_bytes, 'attempt_removed': True,
                        'worker_stopped': True, 'original_import_hash_preserved': True,
                        'previous_graphs_and_unregistered_file_preserved': True, 'profile_unchanged': True})
                graph_checked.append(True)
            except Exception:
                graph_checked.append(traceback.format_exc())
                dialog.reject()
        QTimer.singleShot(0, exercise_graph_dialog)
        QTest.mouseClick(page.connectivity_manage_button, Qt.MouseButton.LeftButton)
        if graph_checked != [True]:
            raise ValueError('Actual connectivity controls failed: ' + str(graph_checked))
        QFileDialog.getOpenFileName = saved_open
        record.update(connectivity_exact_release_catalog=True, connectivity_built_in_reader=True,
            connectivity_ready_handoff=True, connectivity_preserves_previous=True,
            connectivity_cancel_safe=True, connectivity_64bit_and_weak_edges=True)
        full_graph_spec = builder.circuit_spec().to_dict()
        journal('cumulative_selected_connectivity')
        growing_rows = ((51, 'DNp01', 'DNp01_R'), (52, 'TTMn', 'TTMn_R'), (53, 'PSI', 'PSI_L'),
                        (54, 'IN21A026', 'IN21A026_L'), (55, 'AN08B098', 'AN08B098_R'))
        growing_edges = {(51, 52): 3, (52, 53): 1, (53, 54): 7, (54, 51): 2, (55, 52): 4}
        growing_queries, growing_skeletons, growing_site_queries = [], [], []
        def growing_transport(method, url, headers, payload, timeout, cancel, max_bytes, on_bytes):
            if '/api/skeletons/skeleton/' in url:
                growing_skeletons.append(int(url.split('?')[0].split('/')[-1]))
                return swc
            body = json.loads(payload)
            if body['dataset'] != 'male-cns:v1.0':
                raise ValueError('Cumulative query switched release')
            query = body['cypher']
            if 'MATCH (m:Meta)' in query:
                return json.dumps({'columns':['threshold'],'data':[[.5]]}).encode()
            if 'id(pre)' in query:
                from digifly_app.core.neuprint_contacts import COLUMNS
                sources, targets = [set(map(int, group.split(','))) for group in re.findall(r'bodyId IN \[([^]]+)\]', query)]
                growing_site_queries.append({'sources':sorted(sources),'targets':sorted(targets)})
                rows = [[a,b,a*100000+b*100+j,a*100000+b*100+j+50000,.25,0,0,.75,0,0,.9,.8]
                        for (a,b),w in growing_edges.items() if a in sources and b in targets for j in range(w)]
                return json.dumps({'columns':list(COLUMNS),'data':rows}).encode()
            if 'ConnectsTo' in query:
                sources, targets = [set(map(int, group.split(','))) for group in re.findall(r'bodyId IN \[([^]]+)\]', query)]
                growing_queries.append({'sources': sorted(sources), 'targets': sorted(targets)})
                rows = [[a, b, w] for (a, b), w in growing_edges.items() if a in sources and b in targets]
                return json.dumps({'columns': ['sourceBodyId', 'targetBodyId', 'weight'], 'data': rows}).encode()
            terms = [json.loads(value) for value in re.findall(r'STARTS WITH ("(?:[^"\\]|\\.)*")', query)]
            rows = [row for row in growing_rows if any(row[1].lower().startswith(term) for term in terms)]
            return json.dumps({'columns': ['bodyId', 'type', 'instance'], 'data': rows}).encode()
        class GrowingClient(NeuPrintClient):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs, transport=growing_transport)
            def validate_and_list_datasets(self, **kwargs):
                return (NeuPrintDataset('male-cns:v1.0'),)
        from digifly_app.core.pair_graph import pair_rows, validated_manifest
        from digifly_app.core.circuit_library import library_sources
        cumulative_resources, frozen_graph = [], None
        for expression in ('DNp01, TTMn, PSI', 'IN21A026, AN08B098'):
            dialog = NeuPrintImportDialog(ResourceProfile.load(profile_path), profile_path, window,
                client_factory=GrowingClient, credential_store=NoCredentialStore())
            dialog.resource_imported.connect(cumulative_resources.append)
            dialog.show()
            dialog.token_edit.setText('engineering-fixture-not-a-credential')
            QTest.mouseClick(dialog.connect_button, Qt.MouseButton.LeftButton)
            wait_for(lambda: not dialog.operation_in_progress, 'Cumulative connect did not finish')
            dialog.selection_edit.setText(expression)
            QTest.mouseClick(dialog.preview_button, Qt.MouseButton.LeftButton)
            wait_for(lambda: not dialog.operation_in_progress, 'Cumulative preview did not finish')
            before_count = len(cumulative_resources)
            QTest.mouseClick(dialog.download_button, Qt.MouseButton.LeftButton)
            wait_for(lambda: not dialog.operation_in_progress, 'Cumulative download did not finish')
            if len(cumulative_resources) != before_count + 1:
                raise ValueError('Cumulative app download failed: ' + dialog.status.text())
            current_graph = library_sources(ResourceProfile.load(profile_path), ('male-cns:v1.0',))[0]
            if frozen_graph is None:
                frozen_graph = current_graph
            dialog.grab().save(str(destination / f'cumulative-download-{len(cumulative_resources)}.png'))
            dialog.accept()
        if len({r.root for r in cumulative_resources}) != 1 or growing_skeletons != [51, 52, 53, 54, 55]:
            raise ValueError('Successive app downloads did not share one neuron library')
        current_graph = library_sources(ResourceProfile.load(profile_path), ('male-cns:v1.0',))[0]
        if ([r['total_weight'] for r in pair_rows(current_graph, '53', '54')] != [7] or
                [r['total_weight'] for r in pair_rows(current_graph, '54', '51')] != [2] or
                validated_manifest(current_graph)['rows'] != 5 or
                validated_manifest(frozen_graph)['rows'] != 2):
            raise ValueError('Cross-batch graph, duplicate count or frozen previous revision differs')
        from digifly_app.core.pair_graph import selected_contact_sites
        if (len(selected_contact_sites(current_graph,(51,52,53,54,55))[1])!=17 or
                len(selected_contact_sites(frozen_graph,(51,52,53))[1])!=4 or
                growing_site_queries!=growing_queries):
            raise ValueError('Actual contact locations, cumulative census or frozen site revision differs')
        window.pages.setCurrentWidget(page)
        page.refresh()
        page.open_connectome('male-cns:v1.0')
        if '5 directed pairs / 5 checked neurons' not in page.pair_graph_label.text():
            raise ValueError('Data Library does not show the cumulative graph coverage')
        window._open_library_circuit('male-cns:v1.0')
        wait_for(lambda: builder.saved_browser.inventory is not None and
                 builder.saved_browser.inventory.dataset == 'male-cns:v1.0', 'Cumulative circuit index did not finish')
        builder.query_edit.setText('51, 52, 53, 54, 55')
        builder.assemble_circuit()
        wait_for(lambda: set(builder.loaded_records) == {'51','52','53','54','55'},
                 'Load did not finish indexing the newly added dataset folder')
        attached = window.experiment_page._circuit.connectome
        if (set(builder.loaded_records) != {'51','52','53','54','55'} or
                attached.pair_graph_sha256 != current_graph.pair_graph_sha256 or
                attached.pair_graph_path != current_graph.pair_graph_path or
                attached.pair_graph_index != current_graph.pair_graph_index):
            raise ValueError('Both builders did not use the same latest cumulative connectivity revision')
        if library_sources(ResourceProfile.load(profile_path), ('male-cns:v0.9',))[0].pair_graph_path:
            raise ValueError('Cumulative connectivity leaked into another release')
        if ('17 measured pre/post chemical sites' not in window.experiment_page.circuit_detail.text()
                or 'Choose an explicit count-to-strength' in window.experiment_page.circuit_detail.text()
                or '17 measured chemical sites' not in builder.pair_graph_note.text()):
            raise ValueError('A builder still describes acquired real sites as an aggregate-only count graph')
        window.grab().save(str(destination / 'cumulative-circuit-handoff.png'))
        record.update(cumulative_connectivity_two_downloads=True, cumulative_cross_batch_directions=True,
            cumulative_unique_pairs=True, cumulative_frozen_circuit=True,
            cumulative_builder_handoff=True, cumulative_release_isolation=True,
            cumulative_contact_locations=True,
            cumulative_query_rectangles=growing_queries,
            cumulative_shared_file=str(cumulative_resources[-1].root / 'connectivity/connectivity.sqlite'))
        journal('explicit_count_model_and_fragment_copy')
        from digifly_app.engines.generic_experiment import GenericExperimentAdapter
        located_spec = builder.circuit_spec()
        located_edges = GenericExperimentAdapter({})._edge_manifest(located_spec, tuple(builder.loaded_morphologies.values()))
        if (len(located_edges['chemical_edges']) != 17 or
                any(not row.get('located_contact') for row in located_edges['chemical_edges'])):
            raise ValueError('Builder did not materialize every acquired chemical location')
        builder.gap_mode_combo.setCurrentIndex(builder.gap_mode_combo.findData('ohmic'))
        builder.gap_placement_combo.setCurrentIndex(builder.gap_placement_combo.findData('gf_ttmn_chemical_sites'))
        apply_gap = builder.findChild(QPushButton, 'ApplyGapPolicyButton')
        gap_status=[]
        builder.status_message.connect(gap_status.append)
        QTest.mouseClick(apply_gap, Qt.MouseButton.LeftButton)
        if (not gap_status or 'modeled GF–TTMn' not in gap_status[-1]
                or 'chemical endpoints' not in gap_status[-1] or 'no measured electrical anatomy' not in gap_status[-1]
                or 'connectivity is not loaded' in gap_status[-1]):
            raise ValueError('Chemical-site gap status incorrectly describes the saved policy or data')
        located_spec = builder.circuit_spec()
        gap_edges = GenericExperimentAdapter({})._edge_manifest(located_spec, tuple(builder.loaded_morphologies.values()))
        gap_sites = [row for pair in gap_edges['electrical_edges'] for row in pair['contacts']]
        if (len(gap_sites) != 3 or any(row.get('modeled_electrical') is not True or
                row['placement_assumption'].get('biological_electrical_contact') is not False for row in gap_sites) or
                any(row['located_contact'] not in [c['located_contact'] for c in located_edges['chemical_edges']] for row in gap_sites)):
            raise ValueError('Modeled GF–TTMn gaps did not retain only the exact chemical sites')
        if window.experiment_page._circuit.gap_junction_policy.placement_policy != 'gf_ttmn_chemical_sites':
            raise ValueError('Applied chemical-site gap policy did not reach Experiment Builder')
        record.update(located_chemical_model_applied=True, modeled_gf_ttmn_chemical_sites=True,
                      chemical_site_gap_status=True)
        experiment_page = window.experiment_page
        original_experiment = experiment_page.config()
        previous_page_index = window.pages.currentIndex()
        experiment_nav = next(button for button in window.nav_buttons if 'Experiment Builder' in button.text())
        QTest.mouseClick(experiment_nav, Qt.MouseButton.LeftButton)
        app.processEvents()
        saved_job = experiment_page._output_root() / 'jobs' / 'readiness-history-only'
        saved_job.mkdir(parents=True, exist_ok=False)
        saved_job_request = saved_job / 'request.json'
        saved_job_request.write_text(json.dumps({'experiment': {'name': 'Already completed contact test'}}))
        saved_job_bytes = saved_job_request.read_bytes()
        experiment_page.name_edit.setText('  already   COMPLETED contact test  ')
        experiment_page.findChild(QScrollArea).ensureWidgetVisible(experiment_page.validate_button)
        QTest.qWait(80)
        QTest.mouseClick(experiment_page.validate_button, Qt.MouseButton.LeftButton)
        if ('Name already used' not in experiment_page.validation_state.text()
                or str(saved_job) not in experiment_page.validation_state.toolTip()
                or experiment_page._process is not None
                or saved_job_request.read_bytes() != saved_job_bytes):
            raise ValueError('Readiness disagrees with the saved-job duplicate-name launch guard')
        window.grab().save(str(destination / 'duplicate-name-readiness.png'))
        experiment_page.name_edit.setText('Fresh contact readiness check')
        if experiment_page._update_name_availability() or experiment_page.name_availability.text() != '✓ Available':
            raise ValueError('A new experiment name remains incorrectly unavailable')
        experiment_page.set_config(original_experiment)
        QTest.mouseClick(window.nav_buttons[previous_page_index], Qt.MouseButton.LeftButton)
        app.processEvents()
        record['job_history_duplicate_readiness'] = True
        builder.gap_mode_combo.setCurrentIndex(builder.gap_mode_combo.findData('none'))
        QTest.mouseClick(apply_gap, Qt.MouseButton.LeftButton)
        # Exercise the new controls on the graph acquired by the actual app
        # above. This is fixture engineering, not a numeric circuit experiment.
        builder.chemical_placement.setCurrentIndex(builder.chemical_placement.findData('aggregate_post_soma'))
        builder.chemical_default_weight.setValue(.002)
        apply_model = next(button for button in builder.findChildren(QPushButton)
                           if button.text() == 'Apply chemical connection model')
        QTest.mouseClick(apply_model, Qt.MouseButton.LeftButton)
        count_spec = builder.circuit_spec()
        if count_spec.chemical_synapse_policy.placement_policy != 'aggregate_post_soma':
            raise ValueError('Actual Apply did not select the explicit downloaded-count model')
        edge_model = GenericExperimentAdapter({})._edge_manifest(count_spec, tuple(builder.loaded_morphologies.values()))
        if (len(edge_model['chemical_edges']) != 5 or edge_model['electrical_edges']
                or any(row['weight_uS'] != row['source_connection_count'] * .002 for row in edge_model['chemical_edges'])):
            raise ValueError('Applied model lost directed source counts or inferred an electrical connection')
        model_path = destination / 'count-model-circuit.json'
        model_path.write_text(json.dumps(count_spec.to_dict(), indent=2) + '\n')
        builder.set_circuit_spec(CircuitSpec.from_dict(json.loads(model_path.read_text())))
        builder.load_saved_assets()
        if (builder._read_chemical_policy() != count_spec.chemical_synapse_policy or
                window.experiment_page._circuit.chemical_synapse_policy != count_spec.chemical_synapse_policy):
            raise ValueError('Saved/reopened count model did not reach Experiment Builder')
        window.grab().save(str(destination / 'count-model-circuit.png'))
        from digifly_app.core.swc_quality import analyze_swc
        from digifly_app.core.morphology import load_swc
        from digifly_app.core.connectomes import NeuronRecord
        from digifly_app.core.swc_library import add_bundle_to_library
        fragment_seed = destination / 'fragment-seed/MN/fragment/777/777.swc'
        fragment_seed.parent.mkdir(parents=True)
        fragment_seed.write_text('1 1 0 0 0 1 -1\n2 2 2 0 0 1 1\n20 2 8 0 0 0.8 -1\n21 2 10 0 0 0.8 20\n')
        fragment_resource = import_local_source(ResourceProfile.load(profile_path), destination / 'fragment-seed',
            provider='local', resource_id='fragment-fixture', source_version='fixture', dataset='male-cns:v1.0')
        fragment_resource = add_bundle_to_library(ResourceProfile.load(profile_path), profile_path, fragment_resource,
            server='https://fragments.example', dataset='male-cns:v1.0')
        fragment_source = next(item for item in library_sources(ResourceProfile.load(profile_path), ('male-cns:v1.0',))
                               if item.server == 'https://fragments.example')
        fragment = Path(ConnectomeCatalog.scan(fragment_source).by_id['777'].swc_path)
        original_fragment = fragment.read_bytes()
        report = analyze_swc(fragment)
        QMessageBox.question = saved_question
        fragment_dialog_checks = []
        def answer_fragment(accept):
            deadline = time.monotonic() + 4
            def answer():
                candidates = builder.findChildren(QMessageBox, 'FragmentJoinPreview')
                box = next((item for item in candidates if item.isVisible()), None)
                observation = {'accept': accept, 'candidates': [
                    {'title': item.windowTitle(), 'visible': item.isVisible()} for item in candidates]}
                (destination / 'fragment-dialog-state.json').write_text(json.dumps(observation, indent=2))
                if box is not None:
                    text = box.text()
                    if 'total volume' not in text or 'change electrical conduction' not in text:
                        fragment_dialog_checks.append(False)
                        box.reject()
                        return
                    box.grab().save(str(destination / f'fragment-preview-{accept}.png'))
                    fragment_dialog_checks.append(True)
                    QTest.mouseClick(box.button(QMessageBox.StandardButton.Yes if accept else QMessageBox.StandardButton.No), Qt.MouseButton.LeftButton)
                elif time.monotonic() < deadline:
                    QTimer.singleShot(30, answer)
                else:
                    fragment_dialog_checks.append(False)
                    for item in candidates:
                        item.reject()
            QTimer.singleShot(30, answer)
        answer_fragment(False)
        if builder._offer_fragment_join(report) != (True, '') or fragment.with_name('777_fragment_healed.swc').exists():
            raise ValueError('Declining the fragment preview wrote a joined morphology')
        answer_fragment(True)
        reviewed, joined = builder._offer_fragment_join(report)
        if fragment_dialog_checks != [True, True] or not reviewed or not joined or fragment.read_bytes() != original_fragment:
            raise ValueError('Accepted fragment preview did not preserve its original')
        joined_morph = load_swc(NeuronRecord('fixture', 'MN', 'fixture', joined, 'custom:fixture'))
        if sum(n.parent_id < 0 for n in joined_morph.nodes) != 1 or not joined_morph.filler_metadata:
            raise ValueError('Joined copy is not a tagged connected morphology')
        builder.set_circuit_spec(CircuitSpec(connectome=fragment_source, neuron_ids=('777',)))
        wait_for(lambda: builder.saved_browser.inventory is not None and
                 builder.saved_browser.inventory.dataset == 'male-cns:v1.0' and
                 any(str(path) == joined for neuron in builder.saved_browser.inventory.neurons
                     for path in neuron.paths), 'Managed joined-copy indexing did not finish')
        builder.load_saved_assets()
        if (builder.loaded_records['777'].swc_path != joined
                or not builder.loaded_morphologies['777'].filler_metadata
                or window.experiment_page._circuit.neuron_ids != ('777',)):
            raise ValueError('Managed joined copy did not reach both builders on the next Load')
        window.grab().save(str(destination / 'fragment-managed-builder-handoff.png'))
        # The actual failed MANC path: a reviewed derivative must not turn
        # its raw neuron into an ambiguous download/replacement target.
        fragment_copies = {p:p.read_bytes() for p in fragment.parent.glob('*.swc*')}
        fragment_transfers = []
        def fragment_transport(method,url,headers,payload,timeout,cancel,max_bytes,on_bytes):
            if '/api/skeletons/' in url:
                fragment_transfers.append(url)
                raise ValueError('Keeping a reviewed source attempted a new skeleton transfer')
            query=json.loads(payload)['cypher']
            if 'MATCH (m:Meta)' in query:
                data={'columns':['threshold'],'data':[[.5]]}
            elif 'id(pre)' in query:
                from digifly_app.core.neuprint_contacts import COLUMNS
                data={'columns':list(COLUMNS),'data':[]}
            elif 'ConnectsTo' in query:
                data={'columns':['sourceBodyId','targetBodyId','weight'],'data':[]}
            else:
                data={'columns':['bodyId','type','instance'],'data':[[777,'fragment','fragment']]}
            return json.dumps(data).encode()
        class FragmentClient(NeuPrintClient):
            def __init__(self,*args,**kwargs):
                super().__init__(*args,**kwargs,transport=fragment_transport)
            def validate_and_list_datasets(self,**kwargs):
                return (NeuPrintDataset('male-cns:v1.0'),)
        QMessageBox.question=lambda *a,**kw:QMessageBox.StandardButton.Yes
        dialog=NeuPrintImportDialog(ResourceProfile.load(profile_path),profile_path,window,
            client_factory=FragmentClient,credential_store=NoCredentialStore())
        fragment_downloads=[]; dialog.resource_imported.connect(fragment_downloads.append)
        dialog.show();dialog.server_edit.setText('https://fragments.example')
        dialog.token_edit.setText('engineering-fixture-not-a-credential')
        QTest.mouseClick(dialog.connect_button,Qt.MouseButton.LeftButton)
        wait_for(lambda:not dialog.operation_in_progress,'Joined-copy refresh connect did not finish')
        dialog.selection_edit.setText('777')
        QTest.mouseClick(dialog.preview_button,Qt.MouseButton.LeftButton)
        wait_for(lambda:not dialog.operation_in_progress,'Joined-copy refresh preview did not finish')
        arm_review('keep','fragment-contact-refresh-keep.png')
        QTest.mouseClick(dialog.download_button,Qt.MouseButton.LeftButton)
        wait_for(lambda:not dialog.operation_in_progress,'Joined-copy contact refresh did not finish')
        if (len(fragment_downloads)!=1 or fragment_transfers or
                any(p.read_bytes()!=data for p,data in fragment_copies.items())):
            raise ValueError('Contact refresh rejected or changed the reviewed raw/joined SWCs: '+dialog.status.text())
        dialog.accept()
        record['fragment_contact_refresh_keeps_copies']=True
        record.update(aggregate_chemical_model_applied=True, aggregate_chemical_saved_handoff=True,
            aggregate_model_directed_pairs=5, fragment_preview_decline_preserves_original=True,
            fragment_preview_accept_saves_connected_copy=True,
            fragment_original_geometry_preserved=True, fragment_managed_library_builder_handoff=True)
        builder.set_circuit_spec(CircuitSpec.from_dict(full_graph_spec))
        builder.load_saved_assets()
        record.update(ok=True, body_ids=sorted(catalog.by_id), primary_bindings=1,
                      separate_downloads_same_library=True, local_intake=True,
                      combined_list_download=True, combined_list_terms=["DNp01", "TTMn", "PSI"],
                      production_query_builder_used=True, queries=queries,
                      combined_visible_neurons=sorted(builder.loaded_morphologies), library_root=str(resources[-1].root))
    except Exception:
        record["error"] = traceback.format_exc()
    finally:
        faulthandler.cancel_dump_traceback_later()
        faulthandler.disable()
        stack_output.close()
        if previous_fault_handler:
            faulthandler.enable()
        QMessageBox.question = saved_question
        QInputDialog.getItem = saved_item
        QInputDialog.getText = saved_text
        QFileDialog.getExistingDirectory = saved_directory
        QFileDialog.getOpenFileName, QFileDialog.getSaveFileName = saved_open, saved_save
        QMessageBox.warning, QMessageBox.critical = saved_warning, saved_critical
        (destination / "receipt.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        if window is not None:
            window.data_library_page.neuron_browser.shutdown()
            window.circuit_builder_page.shutdown()
            # An engineering test does not ask the human to save its fixture draft.
            window.deleteLater()
        if app is not None:
            app.processEvents()
    (destination / "receipt.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return 0 if record["ok"] else 1
