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
    from PySide6.QtCore import Qt, QTimer, QPoint
    from digifly_app.core.app_settings import app_settings, SETTINGS_ROOT_ENV
    from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog, QMessageBox, QScrollArea
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
        if page.view_stack.currentWidget() is not page.picker_scroll or page.import_button.isVisible():
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
            if not page.import_button.isVisible() or not page.load_link_button.isVisible():
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
            if page.view_stack.currentWidget() is not page.picker_scroll or page.import_button.isVisible():
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
        QTest.mouseClick(page.swc_intake_button, Qt.MouseButton.LeftButton)
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
            path.write_bytes(swc)
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
            if page.linked_table.rowCount() != 1:
                raise ValueError('Exact release must show only its own linked library')
            page.linked_table.selectRow(0)
            if page.trash_button.isEnabled() or page.registration_button.isEnabled():
                raise ValueError('Linked folder acquired managed deletion controls')
            QTest.mouseClick(page.count_linked_button, Qt.MouseButton.LeftButton)
            wait_for(lambda: not page.import_in_progress, 'Read-only linked folder count did not finish')
            linked = page._linked[0]
            counts[linked.root] = page.linked_table.item(0, 3).text()
            shortcut = destination / ('library-links/' + ('male' if linked.root == male.resolve() else 'manc') + '.digifly-library.json')
            QFileDialog.getSaveFileName = lambda *a, _path=shortcut, **kw: (str(_path), '')
            page.scroll.ensureWidgetVisible(page.save_link_button)
            QTest.mouseClick(page.save_link_button, Qt.MouseButton.LeftButton)
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
                raise ValueError('Library shortcut did not request explicit read-only confirmation')
            modal.grab().save(str(destination / 'library-shortcut-review.png'))
            QTest.mouseClick(modal.button(QMessageBox.StandardButton.Yes), Qt.MouseButton.LeftButton)
        for shortcut in shortcuts:
            QFileDialog.getOpenFileName = lambda *a, _path=shortcut, **kw: (str(_path), '')
            page.scroll.ensureWidgetVisible(page.load_link_button)
            QTimer.singleShot(100, accept_link)
            QTest.mouseClick(page.load_link_button, Qt.MouseButton.LeftButton)
        if {link.root for link in page._all_linked} != {male.resolve(), manc.resolve()}:
            raise ValueError('Saved library shortcuts did not restore both exact external sources')
        if any(path.read_bytes() != before_linked[str(path)] for path in native_files):
            raise ValueError('Shortcut controls modified a source SWC')
        if (page.linked_table.geometry().intersects(page.open_linked_button.geometry())
                or (page.managed_card.isVisible() and page.table.geometry().intersects(page.inspect_button.geometry()))):
            raise ValueError('Data Library controls overlap a table at the native test window size')
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
        choose_release('manc:v1.2.1')
        record.update(connectome_exact_release_filter=True, connectome_empty_sections_hidden=True,
                      connectome_stale_selection_cleared=True, connectome_parquet_scoped=True,
                      connectome_scope_preserves_sources=True, connectome_unknown_not_guessed=True)
        record.update(linked_local_libraries_visible=True, linked_workspace_discovery=True,
            linked_register_control=True, linked_readonly_counts=True, linked_sources_unchanged=True,
            linked_no_managed_delete_controls=True,
            library_shortcut_save_open=True, library_shortcut_explicit_review=True,
            library_shortcut_restores_without_workspace=True, library_layout_no_overlap=True,
            linked_fixture_counts={'male-cns:v0.9': 2, 'manc:v1.2.1': 1})
        record.update(ok=True, body_ids=sorted(catalog.by_id), primary_bindings=1,
                      separate_downloads_same_library=True, local_intake=True,
                      combined_list_download=True, combined_list_terms=["DNp01", "TTMn", "PSI"],
                      production_query_builder_used=True, queries=queries,
                      combined_visible_neurons=sorted(builder.loaded_morphologies), library_root=str(resources[-1].root))
    except Exception:
        record["error"] = traceback.format_exc()
    finally:
        QMessageBox.question = saved_question
        QInputDialog.getItem = saved_item
        QInputDialog.getText = saved_text
        QFileDialog.getExistingDirectory = saved_directory
        QFileDialog.getOpenFileName, QFileDialog.getSaveFileName = saved_open, saved_save
        QMessageBox.warning, QMessageBox.critical = saved_warning, saved_critical
        (destination / "receipt.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        if window is not None:
            # An engineering test does not ask the human to save its fixture draft.
            window.deleteLater()
        if app is not None:
            app.processEvents()
    (destination / "receipt.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return 0 if record["ok"] else 1
