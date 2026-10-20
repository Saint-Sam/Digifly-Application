from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import replace
from pathlib import Path
import threading
import json
import uuid

from PySide6.QtCore import QObject, QThread, Signal, Slot, Qt, QPoint
from PySide6.QtGui import QDesktopServices, QAction
from PySide6.QtWidgets import (
    QFileDialog,
    QButtonGroup,
    QHBoxLayout,
    QGridLayout,
    QLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QMenu,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import QUrl
from digifly_app.core.linked_libraries import (
    LinkedLibrary, LinkedCount, list_linked_libraries, count_linked_swcs, path_key,
    LINK_SUFFIX, save_library_link, read_library_link, remember_library_link,
    canonical_dataset, pair_binding_dataset,
)

from digifly_app.core.data_library import (
    ManagedResource,
    import_local_source,
    list_managed_resources,
    preview_local_source,
    register_existing_morphology,
    safe_component,
)
from digifly_app.core.resource_profile import (
    ResourceKind,
    ResourceProfile,
    load_or_create_default_profile,
)
from digifly_app.core.swc_library import LIBRARY_SCHEMA, add_bundle_to_library, library_root
from digifly_app.core.bundled_pair_graph import included_pair_path, PAIR_DATASET
from digifly_app.core.circuit import ConnectomeRef
from digifly_app.core.neuprint import DEFAULT_NEUPRINT_SERVER
from digifly_app.core.resource_management import (
    LibraryMoveProgress,
    LibraryMoveResult,
    move_managed_library,
    preview_library_move,
    preview_library_relink,
    register_managed_resource,
    relink_managed_library,
    trash_managed_resource,
    unregister_managed_resource,
)

from .widgets import Card
from .connectome_picker import CONNECTOMES, ConnectomePicker
from .neuprint_import import NeuPrintImportDialog
from .modeldb_import import ModelDBImportDialog
from .pair_graph_import import PairGraphImportDialog
from .resource_management import ManifestDialog, TrashDialog


def _human_bytes(value: int) -> str:
    amount = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1024 or unit == "TB":
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{value} B"


class _ImportWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        profile: ResourceProfile,
        profile_path: Path,
        source: Path,
        resource_id: str,
        source_version: str,
        library_identity: tuple[str, str, dict | None] | None = None,
        existing_bundle: ManagedResource | None = None,
    ):
        super().__init__()
        self.profile = profile
        self.profile_path = profile_path
        self.source = source
        self.resource_id = resource_id
        self.source_version = source_version
        self.library_identity = library_identity
        self.existing_bundle = existing_bundle

    @Slot()
    def run(self) -> None:
        try:
            resource = self.existing_bundle or import_local_source(
                self.profile,
                self.source,
                provider="local",
                resource_id=self.resource_id,
                source_version=self.source_version,
                profile_path=None if self.library_identity else self.profile_path,
                label=self.source.name,
                dataset=self.library_identity[1] if self.library_identity else "",
            )
            if self.library_identity:
                server, dataset, convention = self.library_identity
                resource = add_bundle_to_library(self.profile, self.profile_path, resource,
                    server=server, dataset=dataset, coordinate_convention=convention,
                    retire_acquisition_binding=True)
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.completed.emit(resource)


class _LibraryMoveWorker(QObject):
    completed = Signal(object)
    failed = Signal(str)
    progress = Signal(object)

    def __init__(
        self,
        profile: ResourceProfile,
        profile_path: Path,
        target: Path,
    ):
        super().__init__()
        self.profile = profile
        self.profile_path = profile_path
        self.target = target
        self.cancel_event = threading.Event()

    @Slot()
    def run(self) -> None:
        try:
            result = move_managed_library(
                self.profile,
                self.target,
                profile_path=self.profile_path,
                progress=self.progress.emit,
                cancel=self.cancel_event,
            )
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.completed.emit(result)


class _LinkedCountWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, root: Path, parent=None):
        super().__init__(parent)
        self.root = root
        self.cancel_event = threading.Event()

    def run(self):
        try:
            self.completed.emit(count_linked_swcs(self.root, cancel=self.cancel_event))
        except (OSError, ValueError) as error:
            self.failed.emit(str(error))


class DataLibraryPage(QWidget):
    status_message = Signal(str)
    sources_changed = Signal()
    quality_review_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._thread: QThread | None = None
        self._worker: QObject | None = None
        self._worker_kind = ""
        self._review_after_import = False
        self._neuprint_dialog: NeuPrintImportDialog | None = None
        self._modeldb_dialog: ModelDBImportDialog | None = None
        self._resources: tuple[ManagedResource, ...] = ()
        self._all_resources = ()
        self._all_linked = ()
        self._pair_graphs = ()
        self._linked: tuple[LinkedLibrary, ...] = ()
        self._linked_counts: dict[str, LinkedCount] = {}
        self._linked_counter: _LinkedCountWorker | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.view_stack = QStackedWidget()
        outer.addWidget(self.view_stack)
        self.picker_scroll = QScrollArea()
        self.picker_scroll.setWidgetResizable(True)
        self.picker_scroll.setObjectName('ConnectomeHomeScroll')
        picker_content = QWidget()
        picker_content.setMinimumWidth(760)
        picker_layout = QVBoxLayout(picker_content)
        picker_layout.setContentsMargins(30, 26, 30, 32)
        self.connectome_picker = ConnectomePicker()
        self.connectome_picker.dataset_selected.connect(self.open_connectome)
        picker_layout.addWidget(self.connectome_picker)
        picker_layout.addStretch()
        self.picker_scroll.setWidget(picker_content)
        self.view_stack.addWidget(self.picker_scroll)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setObjectName('DataLibraryScroll')
        content = QWidget()
        content.setMinimumWidth(640)
        content.setObjectName('CompactLibraryWorkspace')
        self.scroll.setWidget(content)
        self.view_stack.addWidget(self.scroll)
        root = QVBoxLayout(content)
        root.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        root.setContentsMargins(22, 20, 22, 24)
        root.setSpacing(12)
        # Hiding empty library cards must not stretch the remaining headings
        # across a tall window. Every release uses the same top-packed flow.
        root.setAlignment(Qt.AlignmentFlag.AlignTop)
        context = Card()
        context_layout = QVBoxLayout(context)
        context_layout.setContentsMargins(18, 18, 18, 18)
        header = QHBoxLayout()
        self.back_to_connectomes = QPushButton('← Connectomes')
        self.back_to_connectomes.clicked.connect(self.show_connectomes)
        header.addWidget(self.back_to_connectomes)
        self.connectome_title = QLabel()
        self.connectome_title.setObjectName('SectionTitle')
        header.addWidget(self.connectome_title, 0, Qt.AlignmentFlag.AlignVCenter)
        header.addStretch()
        context_layout.addLayout(header)
        release_row = QHBoxLayout()
        release_row.addWidget(QLabel('Release'))
        self.release_group = QButtonGroup(self)
        self.release_group.setExclusive(True)
        self.release_buttons = {}
        for choice in CONNECTOMES:
            for dataset in choice.releases:
                button = QPushButton(dataset.split(':')[-1])
                button.setObjectName('ConnectomeChoiceButton')
                button.setAccessibleName(f'Use {dataset} release')
                button.setCheckable(True)
                self.release_group.addButton(button)
                button.toggled.connect(lambda checked, value=dataset:
                                       self.select_connectome_release(value) if checked else None)
                release_row.addWidget(button)
                self.release_buttons[dataset] = button
                button.hide()
        release_row.addStretch()
        context_layout.addLayout(release_row)
        self.release_detail = QLabel()
        self.release_detail.setObjectName('Muted')
        self.release_detail.setWordWrap(True)
        context_layout.addWidget(self.release_detail)
        root.addWidget(context)
        self._row_menus = []
        self._view_generation = 0
        heading = QHBoxLayout()
        self.data_heading = QLabel('Your data')
        self.data_heading.setObjectName('SectionTitle')
        heading.addWidget(self.data_heading, 0, Qt.AlignmentFlag.AlignVCenter)
        heading.addStretch()
        self.refresh_button = QPushButton('Refresh')
        self.refresh_button.clicked.connect(self.refresh)
        heading.addWidget(self.refresh_button)
        root.addLayout(heading)
        actions = QWidget()
        action_layout = QVBoxLayout(actions)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(8)
        row = QHBoxLayout()
        self.register_button = QPushButton('Link SWC folder…')
        self.register_button.clicked.connect(self.register_existing_folder)
        self.neuprint_button = QPushButton("Download from neuPrint")
        self.neuprint_button.setProperty('primary', True)
        self.neuprint_button.clicked.connect(self.open_neuprint_import)
        row.addWidget(self.neuprint_button)
        row.addWidget(self.register_button)
        self.add_data_menu = QMenu(self)
        self.swc_intake_button = self._action('Import SWCs…', self.add_swcs_to_library, 'LibraryAddSwcsButton')
        self.load_link_button = self._action('Open library shortcut…', self.open_saved_link, 'OpenLibraryLinkButton')
        self.import_button = self._action('Other local files…', self.import_local_data)
        self.modeldb_button = self._action('Model / ModelDB…', self.open_modeldb_import)
        self.pair_graph_button = self._action('Connectivity Parquet…', self.use_pair_graph, 'LibraryConnectivityParquetButton')
        self.add_data_menu.addActions((self.swc_intake_button, self.load_link_button))
        self.add_data_menu.addSeparator()
        self.add_data_menu.addActions((self.import_button, self.modeldb_button, self.pair_graph_button))
        self.add_data_button = self._menu_button('Add data ▾', self.add_data_menu)
        row.addWidget(self.add_data_button)
        row.addStretch()
        action_layout.addLayout(row)
        hint = QLabel('Link keeps SWCs in their folder. Import copies SWCs into this release’s library.')
        hint.setObjectName('Muted')
        hint.setWordWrap(True)
        action_layout.addWidget(hint)
        self.consolidate_swcs_button = self._action('Add to dataset SWC library…', self.consolidate_selected_swcs, 'LibraryConsolidateSwcsButton')
        progress_row = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        progress_row.addWidget(self.progress, 1)
        self.cancel_move_button = QPushButton("Cancel library move")
        self.cancel_move_button.setVisible(False)
        self.cancel_move_button.clicked.connect(self.cancel_library_move)
        progress_row.addWidget(self.cancel_move_button)
        action_layout.addLayout(progress_row)
        self.action_status = QLabel('')
        self.action_status.setObjectName("Muted")
        self.action_status.setWordWrap(True)
        action_layout.addWidget(self.action_status)
        root.addWidget(actions)

        linked_card = self.linked_card = Card()
        linked_layout = QVBoxLayout(linked_card)
        linked_layout.addWidget(QLabel('Linked SWC folders · read-only'))
        self.linked_table = QTableWidget(0, 7)
        self.linked_table.setObjectName('LinkedLibrariesTable')
        self.linked_table.setHorizontalHeaderLabels(('Library', 'Dataset / release', 'Status', 'SWC files', 'SWC size', 'Folder', 'Actions'))
        self.linked_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.linked_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.linked_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.linked_table.horizontalHeader().setStretchLastSection(False)
        from PySide6.QtWidgets import QHeaderView
        self.linked_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.linked_table.setColumnHidden(1, True)
        for column, width in enumerate((210, 100, 110, 75, 75, 100, 150)):
            self.linked_table.setColumnWidth(column, width)
        self.linked_table.itemSelectionChanged.connect(self._linked_selection_changed)
        linked_layout.addWidget(self.linked_table)
        self.open_linked_button = self._action('Open folder', self.open_linked_folder)
        self.count_linked_button = self._action('Count SWCs (read-only)', self.count_selected_linked)
        self.cancel_count_button = QPushButton('Cancel SWC count')
        self.cancel_count_button.setEnabled(False)
        self.cancel_count_button.clicked.connect(self.cancel_linked_count)
        progress_row.addWidget(self.cancel_count_button)
        self.cancel_count_button.hide()
        self.save_link_button = self._action('Save library shortcut…', self.save_selected_link, 'SaveLibraryLinkButton')
        self.linked_detail = QLabel('Select a linked library to see its full path. Counts are read-only snapshots, not completeness checks.')
        self.linked_detail.setObjectName('LinkedLibraryDetails')
        self.linked_detail.setWordWrap(True)
        self.linked_detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        linked_layout.addWidget(self.linked_detail)
        root.addWidget(linked_card)

        library = self.managed_card = Card()
        library_layout = QVBoxLayout(library)
        library_layout.setContentsMargins(16, 15, 16, 15)
        heading = QHBoxLayout()
        heading.addWidget(QLabel('Downloaded and app-managed data'))
        heading.addStretch()
        reveal = QPushButton('Open folder')
        reveal.clicked.connect(self.reveal_library)
        heading.addWidget(reveal)
        library_layout.addLayout(heading)
        self.managed_heading_open = reveal
        self.root_label = QLabel("No resource profile configured")
        self.root_label.setObjectName("Muted")
        self.root_label.setWordWrap(True)
        library_layout.addWidget(self.root_label)
        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(
            (
                "Provider",
                "Resource",
                "Dataset / scope",
                "State",
                "Files",
                "SWCs",
                "Size",
                "Imported",
                'Actions',
            )
        )
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column, width in ((0, 80), (3, 95), (4, 55), (5, 55), (6, 75), (8, 100)):
            self.table.setColumnWidth(column, width)
        for column in (2, 7):
            self.table.setColumnHidden(column, True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.itemSelectionChanged.connect(self._resource_selection_changed)
        library_layout.addWidget(self.table)
        self.inspect_button = self._action('Download details…', self.inspect_selected)
        self.reveal_selected_button = self._action('Open selected folder', self.reveal_selected)
        self.registration_button = self._action('Manage registration…', self.toggle_registration)
        self.trash_button = self._action('Move to Library Trash…', self.trash_selected)
        root.addWidget(library)
        self.connectivity_card = Card()
        graph_layout = QVBoxLayout(self.connectivity_card)
        graph_layout.addWidget(QLabel('Connectivity'))
        self.pair_graph_label = QLabel()
        self.pair_graph_label.setObjectName('LibraryPairGraphStatus')
        self.pair_graph_label.setWordWrap(True)
        graph_layout.addWidget(self.pair_graph_label)
        root.addWidget(self.connectivity_card)
        self.library_tools_menu = QMenu(self)
        self.open_managed_action = self._action('Open managed library', self.reveal_library)
        self.move_library_button = self._action('Move managed library…', self.move_library)
        self.relink_button = self._action('Relink moved library…', self.relink_library)
        self.manage_trash_button = self._action('Manage Trash…', self.manage_trash)
        self.library_tools_menu.addActions((self.open_managed_action, self.move_library_button,
                                           self.relink_button, self.manage_trash_button))
        tools = QHBoxLayout()
        self.library_tools_button = self._menu_button('Library tools ▾', self.library_tools_menu)
        tools.addWidget(self.library_tools_button)
        tools.addStretch()
        root.addLayout(tools)
        root.addStretch(1)
        self.view_stack.setCurrentWidget(self.picker_scroll)
        self.refresh()

    def _action(self, text, callback, name=''):
        action = QAction(text, self)
        action.setObjectName(name)
        action.triggered.connect(callback)
        return action

    @staticmethod
    def _menu_button(text, menu):
        button = QPushButton(text)
        button.setAccessibleName(text.replace(' ▾', ' menu'))
        # A nonblocking popup supports mouse, keyboard and native accessibility
        # activation through the same ordinary push-button signal.
        button.clicked.connect(lambda: menu.popup(button.mapToGlobal(QPoint(0, button.height()))))
        button._library_menu = menu
        return button

    def _row_controls(self, item, *, linked):
        table = self.linked_table if linked else self.table
        items = self._linked if linked else self._resources
        column = 6 if linked else 8
        generation, dataset = self._view_generation, self._display_dataset
        identity = path_key(item.root)
        menu = QMenu(self)
        self._row_menus.append(menu)
        actions = ((self.count_linked_button, self.save_link_button) if linked else
                   (self.consolidate_swcs_button, self.reveal_selected_button,
                    self.inspect_button, self.registration_button, self.trash_button))

        def select_exact():
            # Never reuse a row number after refresh, release switching or a
            # disappearing resource. A stale popup cannot target another row.
            if generation != self._view_generation or dataset != self._display_dataset:
                return False
            visible = self._linked if linked else self._resources
            rows = [i for i, value in enumerate(visible) if path_key(value.root) == identity]
            if len(rows) != 1:
                return False
            table.selectRow(rows[0])
            return True

        for source_action in actions:
            action = menu.addAction(source_action.text())
            action.setObjectName(source_action.objectName())
            def invoke(checked=False, source=source_action):
                if select_exact() and source.isEnabled():
                    source.trigger()
            action.triggered.connect(invoke)
        def prepare():
            valid = select_exact()
            for action, source in zip(menu.actions(), actions):
                action.setText(source.text())
                action.setEnabled(valid and source.isEnabled())
        menu.aboutToShow.connect(prepare)
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(3, 3, 3, 3)
        row.setSpacing(4)
        if linked:
            open_button = QPushButton('Open folder')
            open_button.setEnabled(item.available)
            open_button.clicked.connect(lambda: self.open_linked_folder() if select_exact() else None)
            row.addWidget(open_button)
        button = self._menu_button('…' if linked else 'Actions ▾', menu)
        button.setAccessibleName(f'Actions for {item.label if linked else item.resource_id}')
        row.addWidget(button)
        holder._library_menu, holder._library_button = menu, button
        table.setCellWidget(items.index(item), column, holder)

    @staticmethod
    def _fit_rows(table):
        for row in range(table.rowCount()):
            table.setRowHeight(row, 44)
        table.setFixedHeight(min(300, 52 + table.rowCount() * 44))

    def open_connectome(self, dataset: str) -> None:
        choice = next(choice for choice in CONNECTOMES if dataset in choice.releases)
        self.connectome_title.setText(choice.name)
        for release, button in self.release_buttons.items():
            button.setVisible(release in choice.releases)
        self.select_connectome_release(dataset)
        self.view_stack.setCurrentWidget(self.scroll)
        self.scroll.verticalScrollBar().setValue(0)
        self.back_to_connectomes.setFocus()

    def select_connectome_release(self, dataset: str) -> None:
        self.connectome_picker.select_release(dataset)
        self.release_buttons[dataset].setChecked(True)
        self.release_detail.setText(f'{dataset} · Only this release’s data is shown. Other saved data is unchanged.')
        self._apply_dataset_filter()

    def show_connectomes(self) -> None:
        for menu in (self.add_data_menu, self.library_tools_menu, *self._row_menus):
            menu.close()
        self.view_stack.setCurrentWidget(self.picker_scroll)
        self.picker_scroll.verticalScrollBar().setValue(0)
        key = self.connectome_picker.selected_key
        if key:
            self.connectome_picker.buttons[key].setFocus()

    def _profile(self) -> tuple[ResourceProfile, Path]:
        return load_or_create_default_profile()

    def use_pair_graph(self) -> None:
        if self._thread is not None:
            return
        try:
            profile, _ = self._profile()
            bindings = [binding for binding in profile.bindings(ResourceKind.DATA_SOURCE)
                        if binding.metadata.get("format") == "digifly-weighted-pair-graph-v1"]
            reader = str(bindings[-1].metadata.get("reader_python", "")) if bindings else ""
            dataset = self.connectome_picker.selected_dataset
            if not dataset.startswith('manc:'):
                raise ValueError('Choose a MANC release before importing this connectivity format')
            dialog = PairGraphImportDialog(ConnectomeRef(dataset, dataset, '', dataset), reader, self)
            dialog.exec()
            if dialog.registered_source:
                self.refresh()
                self.sources_changed.emit()
                self.status_message.emit("MANC pair Parquet registered read-only; SWC bundle is separate and no simulation started")
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Cannot register connectivity file", str(exc))

    @Slot()
    def refresh(self) -> None:
        self.table.setRowCount(0)
        self._resources = ()
        try:
            profile, _ = self._profile()
            self._all_linked = list_linked_libraries(profile)
            resources = list_managed_resources(profile)
        except (OSError, ValueError) as exc:
            self.root_label.setText(str(exc))
            self._all_linked, self._all_resources, self._pair_graphs = (), (), ()
            self._apply_dataset_filter()
            self.action_status.setText(str(exc))
            self._resource_selection_changed()
            return
        self._all_resources = resources
        self._pair_graphs = tuple(binding for binding in profile.bindings(ResourceKind.DATA_SOURCE)
                       if binding.metadata.get("format") == "digifly-weighted-pair-graph-v1")
        root_state = "" if profile.managed_data_root.is_dir() else " — missing; relink if moved"
        self.root_label.setText(f"{profile.managed_data_root}{root_state}")
        self._apply_dataset_filter()

    def _apply_dataset_filter(self):
        self._view_generation += 1
        self.add_data_menu.close()
        self.library_tools_menu.close()
        for menu in self._row_menus:
            menu.close()
            menu.deleteLater()
        self._row_menus = []
        dataset = canonical_dataset(self.connectome_picker.selected_dataset)
        if dataset != getattr(self, '_display_dataset', ''):
            self.action_status.clear()
        self._display_dataset = dataset
        self._linked = ()
        self._resources = ()
        self.linked_table.clearSelection()
        self.table.clearSelection()
        self._linked = tuple(item for item in self._all_linked
                             if dataset and canonical_dataset(item.dataset) == dataset)
        self._resources = tuple(item for item in self._all_resources
                                if dataset and self._resource_dataset(item) == dataset)
        self._render_linked()
        self.linked_card.setVisible(bool(self._linked))
        self.managed_card.setVisible(bool(self._resources))
        self.table.setRowCount(0)
        self.pair_graph_button.setVisible(dataset.startswith('manc:'))
        self.pair_graph_button.setText('Use connectivity Parquet…')
        self.pair_graph_label.clear()
        matching_graphs = [binding for binding in self._pair_graphs
                           if dataset and pair_binding_dataset(binding.metadata) == dataset]
        if matching_graphs:
            graph_text = []
            for binding in matching_graphs:
                try:
                    import json
                    manifest = json.loads((Path(str(binding.metadata["index"])).parent / "manifest.json").read_text(encoding="utf-8"))
                    graph_text.append(f"{Path(binding.path).name}: {manifest['rows']:,} pairs / {manifest['represented_neurons']:,} IDs; SWCs separate; read-only")
                except (OSError, ValueError, KeyError):
                    graph_text.append(f"{Path(binding.path).name}: selected index unavailable; no fallback")
            self.pair_graph_label.setText("\n".join(graph_text))
        elif dataset == PAIR_DATASET:
            try:
                if included_pair_path():
                    self.pair_graph_button.setText('Use included MANC v1.2.1 Parquet…')
                    self.pair_graph_label.setText('Included MANC v1.2.1 pair table (release association supplied by you). SWCs are separate.')
            except (OSError, ValueError) as exc:
                self.pair_graph_label.setText(str(exc))
        self.pair_graph_label.setVisible(bool(self.pair_graph_label.text()))
        self.connectivity_card.setVisible(bool(self.pair_graph_label.text()))
        for resource in self._resources:
            row = self.table.rowCount()
            self.table.insertRow(row)
            values = (
                resource.provider,
                resource.resource_id,
                self._managed_dataset(resource),
                "Registered" if resource.is_registered else "Stored only",
                f"{resource.file_count:,}",
                f"{resource.swc_count:,}",
                _human_bytes(resource.total_bytes),
                resource.imported_at,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(f'{resource.resource_id}\n{resource.root}\n{self._managed_dataset(resource)}\nImported: {resource.imported_at}')
                self.table.setItem(row, column, item)
            self._row_controls(resource, linked=False)
        self._fit_rows(self.table)
        self._resource_selection_changed()
        self.status_message.emit(f"{dataset or 'Choose a connectome'}: {len(self._linked)} linked local libraries; {len(self._resources)} managed downloads/libraries")

    @staticmethod
    def _resource_dataset(resource):
        try:
            value = json.loads(resource.manifest.read_text(encoding='utf-8'))
            return canonical_dataset(value.get('dataset') or resource.source_version)
        except (OSError, ValueError):
            return canonical_dataset(resource.source_version)

    @staticmethod
    def _managed_dataset(resource):
        try:
            value = json.loads(resource.manifest.read_text(encoding='utf-8'))
            dataset = value.get('dataset') or resource.source_version
            return str(dataset) + (' · dataset library' if value.get('library_schema') else ' · subset/import')
        except (OSError, ValueError):
            return resource.source_version

    def _render_linked(self):
        previous = self._selected_linked()
        key = path_key(previous.root) if previous else None
        self.linked_table.setRowCount(0)
        for linked in self._linked:
            count = self._linked_counts.get(path_key(linked.root))
            values = (linked.label, linked.dataset, 'Linked · read-only' if linked.available else 'Missing folder',
                      f'{count.swc_files:,}' if count and linked.available else 'Not counted',
                      _human_bytes(count.swc_bytes) if count and linked.available else '—', str(linked.root))
            row = self.linked_table.rowCount()
            self.linked_table.insertRow(row)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(str(linked.root))
                self.linked_table.setItem(row, column, item)
            self._row_controls(linked, linked=True)
            if path_key(linked.root) == key:
                self.linked_table.selectRow(row)
        self._linked_selection_changed()
        self._fit_rows(self.linked_table)

    def _selected_linked(self):
        rows = self.linked_table.selectionModel().selectedRows()
        return self._linked[rows[0].row()] if len(rows) == 1 and rows[0].row() < len(self._linked) else None

    def _linked_selection_changed(self):
        linked = self._selected_linked()
        if linked is not None:
            self.table.clearSelection()
        available = linked is not None and linked.available
        self.open_linked_button.setEnabled(available)
        self.count_linked_button.setEnabled(available and not self.import_in_progress)
        self.save_link_button.setEnabled(available and not self.import_in_progress)
        self.load_link_button.setEnabled(not self.import_in_progress)
        if linked:
            self.linked_detail.setText(f'{linked.origin}: {linked.root}\nRead-only link. No source files are copied, moved, overwritten or deleted. '
                'Counts describe SWC files, not unique neurons or biological completeness. Recount after folder changes.')
        elif self._linked_counter is None:
            self.linked_detail.setText('Select a linked library to see its full path. Counts are read-only snapshots, not completeness checks.')

    def open_linked_folder(self):
        linked = self._selected_linked()
        if linked and linked.root.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(linked.root)))

    def save_selected_link(self):
        linked = self._selected_linked()
        if self.import_in_progress or linked is None or not linked.available:
            return
        try:
            profile, profile_path = self._profile()
            folder = profile_path.parent / 'library-links'
            folder.mkdir(parents=True, exist_ok=True)
            name = safe_component(linked.label)[:70] + '-' + uuid.uuid5(uuid.NAMESPACE_URL, path_key(linked.root)).hex[:8] + LINK_SUFFIX
            selected, _ = QFileDialog.getSaveFileName(self, 'Save SWC library shortcut', str(folder / name),
                                                      'Digifly library shortcut (*' + LINK_SUFFIX + ')')
            if not selected:
                return
            destination = save_library_link(linked, Path(selected))
            remember_library_link(profile, profile_path, linked)
            self.sources_changed.emit()
            self.refresh()
            self.action_status.setText(f'Saved library shortcut: {destination}\n'
                'This device remembers the folder after restart and app updates. The file contains only its location '
                'and dataset label, not SWCs. On another device, open the shortcut and locate the local folder if needed.')
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, 'Could not save library shortcut', str(error))

    def open_saved_link(self):
        if self.import_in_progress:
            return
        try:
            selected, _ = QFileDialog.getOpenFileName(self, 'Open SWC library shortcut', '',
                                                      'Digifly library shortcut (*' + LINK_SUFFIX + ')')
            if not selected:
                return
            linked = read_library_link(Path(selected))
            review = QMessageBox(self)
            review.setWindowTitle('Remember this SWC folder?')
            review.setTextFormat(Qt.TextFormat.PlainText)
            review.setText(f'{linked.label}\nDataset: {linked.dataset}\nFolder: {linked.root}\n\n'
                'Remember this read-only folder on this device? No SWCs are copied or changed. '
                + ('The folder is unavailable; choose its local location next.' if not linked.available else
                   'App updates retain this setting.'))
            review.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
            review.setDefaultButton(QMessageBox.StandardButton.Cancel)
            if review.exec() != QMessageBox.StandardButton.Yes:
                return
            if not linked.available:
                local = QFileDialog.getExistingDirectory(self, 'Locate this SWC library on this device')
                if not local:
                    return
                linked = replace(linked, root=Path(local).resolve(), available=True)
            profile, profile_path = self._profile()
            remember_library_link(profile, profile_path, linked)
            self.sources_changed.emit()
            self.refresh()
            self.action_status.setText(f'Library shortcut remembered on this device: {linked.root}\n'
                                      'The original shortcut and SWCs were not changed. No download is needed.')
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, 'Could not open library shortcut', str(error))

    def count_selected_linked(self):
        linked = self._selected_linked()
        if self.import_in_progress or linked is None or not linked.available:
            return
        worker = _LinkedCountWorker(linked.root, self)
        self._linked_counter = worker
        for button in (self.import_button, self.register_button, self.neuprint_button,
                       self.modeldb_button, self.swc_intake_button, self.consolidate_swcs_button,
                       self.move_library_button, self.relink_button):
            button.setEnabled(False)
        self.cancel_count_button.setEnabled(True)
        self.cancel_count_button.show()
        self.count_linked_button.setEnabled(False)
        self.save_link_button.setEnabled(False)
        self.load_link_button.setEnabled(False)
        self.linked_detail.setText(f'Counting regular SWC files without changing them: {linked.root}')
        worker.completed.connect(self._linked_count_complete)
        worker.failed.connect(self._linked_count_failed)
        worker.finished.connect(self._linked_count_finished)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def cancel_linked_count(self):
        if self._linked_counter:
            self._linked_counter.cancel_event.set()
            self.cancel_count_button.setEnabled(False)

    def _linked_count_complete(self, count):
        self._linked_counts[path_key(count.root)] = count
        self._render_linked()
        if any(path_key(item.root) == path_key(count.root) for item in self._linked):
            self.action_status.setText(f'Counted {count.swc_files:,} regular SWC files ({_human_bytes(count.swc_bytes)}); '
                                       f'{count.skipped_links} links skipped. Source files unchanged.')

    def _linked_count_failed(self, detail):
        self.action_status.setText(f'SWC count did not complete: {detail}')

    def _linked_count_finished(self):
        self._linked_counter = None
        for button in (self.import_button, self.register_button, self.neuprint_button,
                       self.modeldb_button, self.swc_intake_button, self.consolidate_swcs_button,
                       self.move_library_button, self.relink_button):
            button.setEnabled(True)
        self.cancel_count_button.setEnabled(False)
        self.cancel_count_button.hide()
        self._linked_selection_changed()

    def _selected_resource(self) -> ManagedResource | None:
        rows = self.table.selectionModel().selectedRows()
        if len(rows) != 1:
            return None
        row = rows[0].row()
        return self._resources[row] if 0 <= row < len(self._resources) else None

    @Slot()
    def _resource_selection_changed(self) -> None:
        resource = self._selected_resource()
        if resource is not None:
            self.linked_table.clearSelection()
        selected = resource is not None and not self.import_in_progress
        self.inspect_button.setEnabled(selected)
        self.reveal_selected_button.setEnabled(selected)
        self.registration_button.setEnabled(selected)
        self.trash_button.setEnabled(selected)
        self.consolidate_swcs_button.setEnabled(selected and not self.import_in_progress
            and resource.provider == 'neuprint' and bool(resource.swc_count))
        self.registration_button.setText(
            "Unregister" if resource is not None and resource.is_registered else "Register"
        )

    @Slot()
    def consolidate_selected_swcs(self) -> None:
        """Upgrade existing per-search downloads without another network request."""
        if self._thread is not None:
            return
        try:
            profile, profile_path = self._profile()
            resource = self._selected_resource()
            if resource is None or not resource.swc_count or resource.provider != "neuprint":
                raise ValueError("Select an existing neuPrint SWC download in the table first")
            payload = json.loads(resource.manifest.read_text(encoding="utf-8"))
            server, dataset = payload["server"], payload["dataset"]
            destination = library_root(profile, server, dataset) / "export_swc"
            answer = QMessageBox.question(self, "Consolidate this saved SWC download",
                f"Add {resource.swc_count:,} saved SWCs to the growing {dataset} library?\n\n{destination}\n\n"
                "No download or coordinate conversion is needed. The original bundle and source history remain. "
                "Its separate morphology-source entry is replaced by the dataset library only after intake succeeds; "
                "different existing SWC bytes stop intake without replacement.")
            if answer != QMessageBox.StandardButton.Yes:
                return
            self._start_local_import(profile, profile_path, resource.root, "saved-intake", "retained",
                (server, dataset, payload.get("swc_coordinate_convention")), resource)
        except (OSError, ValueError, KeyError) as exc:
            QMessageBox.warning(self, "Cannot consolidate saved SWCs", str(exc))

    @Slot()
    def add_swcs_to_library(self) -> None:
        """App-owned local intake, using the same library as neuPrint downloads."""
        if self._thread is not None:
            return
        try:
            profile, profile_path = self._profile()
            libraries = [item for item in list_managed_resources(profile)
                         if json.loads(item.manifest.read_text(encoding="utf-8")).get("library_schema") == LIBRARY_SCHEMA]
            labels = [json.loads(item.manifest.read_text(encoding="utf-8"))["dataset"] for item in libraries]
            if len(set(labels)) != len(labels):
                labels = [f"{label} — {json.loads(item.manifest.read_text(encoding='utf-8'))['server']}"
                          for label, item in zip(labels, libraries)]
            choice, ok = QInputDialog.getItem(self, "Dataset SWC library", "Destination dataset/version",
                                              [*labels, "New dataset library…"], 0, False)
            if not ok:
                return
            if choice == "New dataset library…":
                dataset, ok = QInputDialog.getText(self, "Exact dataset/version", "Dataset (for example manc:v1.2.1 or male-cns:v0.9)")
                if not ok:
                    return
                if ":v" not in dataset or not dataset.strip():
                    raise ValueError("Specify the exact dataset release, not a generic MANC/Male CNS label")
                dataset = dataset.strip()
                server = DEFAULT_NEUPRINT_SERVER
                # Never infer units from filenames. A raw v0.9 import must be
                # explicitly declared before it can share official downloads.
                convention = None
                if dataset == "male-cns:v0.9":
                    units, ok = QInputDialog.getItem(self, "SWC coordinates", "How are these SWC coordinates stored?",
                        ["Raw official Male CNS v0.9 (8 nm units)", "Other / not established"], 1, False)
                    if not ok:
                        return
                    if units.startswith("Raw official"):
                        convention = {"input_units": "8nm", "scale_to_um": 0.008,
                                      "frame": "male-cns-v0.9-native", "raw_bytes_preserved": True}
            else:
                payload = json.loads(libraries[labels.index(choice)].manifest.read_text(encoding="utf-8"))
                server, dataset = payload["server"], payload["dataset"]
                convention = payload.get("swc_coordinate_convention")
            mode, ok = QInputDialog.getItem(self, "Import SWCs", "Import source", ["SWC file", "Folder (preserve family/type labels)"], 0, False)
            if not ok:
                return
            if mode == "SWC file":
                selected, _ = QFileDialog.getOpenFileName(self, "Choose SWC (numeric body-ID filename)", "", "SWC files (*.swc)")
            else:
                selected = QFileDialog.getExistingDirectory(self, "Choose SWC export folder")
            if not selected:
                return
            source = Path(selected).resolve()
            preview = preview_local_source(source)
            if not preview.swc_count:
                raise ValueError("The selected input has no SWCs")
            destination = library_root(profile, server, dataset) / "export_swc"
            answer = QMessageBox.question(self, "Confirm dataset SWC intake",
                f"Add {preview.swc_count:,} SWCs to {dataset}?\n\n{destination}\n\n"
                "Confirm these neurons belong to this exact dataset/version and share its coordinate units. "
                "No scaling or topology repair is applied. Family/type labels come from the supplied folder; "
                "unlabelled files go in UNKNOWN. Source bytes and originals are preserved. "
                "Conflicting existing body IDs stop intake instead of replacing files.")
            if answer != QMessageBox.StandardButton.Yes:
                return
            identity = (server, dataset, convention)
            self._start_local_import(profile, profile_path, source, "swc-intake", "import-" + uuid.uuid4().hex, identity)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "SWC intake could not start", str(exc))

    @Slot()
    def import_local_data(self) -> None:
        if self._thread is not None:
            return
        try:
            profile, profile_path = self._profile()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Data Library is not configured", str(exc))
            return
        selected = QFileDialog.getExistingDirectory(self, "Choose local data folder")
        if not selected:
            return
        source = Path(selected).resolve()
        try:
            preview = preview_local_source(source)
            default_id = safe_component(source.name)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Cannot import folder", str(exc))
            return
        resource_id, accepted = QInputDialog.getText(
            self,
            "Managed resource identity",
            "Resource ID",
            text=default_id,
        )
        if not accepted:
            return
        try:
            resource_id = safe_component(resource_id)
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid resource ID", str(exc))
            return
        source_version = datetime.now(timezone.utc).strftime("import-%Y%m%d-%H%M%S")
        answer = QMessageBox.question(
            self,
            "Import local data",
            f"Copy {preview.file_count:,} file(s) ({_human_bytes(preview.total_bytes)}) "
            f"including {preview.swc_count:,} SWC(s) into the managed library?\n\n"
            "Source files will not be changed. The imported copy will be checksummed, "
            "manifested, and checked for SWC compartment-size anomalies before registration.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._start_local_import(profile, profile_path, source, resource_id, source_version)

    def _start_local_import(self, profile, profile_path, source, resource_id, source_version, library_identity=None, existing_bundle=None):
        self.import_button.setEnabled(False)
        self.swc_intake_button.setEnabled(False)
        self.consolidate_swcs_button.setEnabled(False)
        self.register_button.setEnabled(False)
        self.progress.setVisible(True)
        self.action_status.setText(f"Staging and validating {source}…")
        worker = _ImportWorker(profile, profile_path, source, resource_id, source_version, library_identity, existing_bundle)
        worker.setParent(self)
        worker.completed.connect(self._import_completed)
        worker.failed.connect(self._import_failed)
        worker.finished.connect(self._worker_thread_finished)
        worker.finished.connect(worker.deleteLater)
        self._thread = worker
        self._worker = worker
        self._worker_kind = "local_import"
        worker.start()

    @Slot(object)
    def _import_completed(self, resource: ManagedResource) -> None:
        self.action_status.setText(
            f"Imported {resource.file_count:,} files to {resource.root}. "
            f"Audited {resource.swc_count:,} SWC(s)."
        )
        self.refresh()
        self.sources_changed.emit()
        self._review_after_import = bool(resource.swc_count)

    @Slot(str)
    def _import_failed(self, detail: str) -> None:
        self.action_status.setText(f"Import failed: {detail}")
        QMessageBox.critical(self, "Data import failed", detail)

    @Slot()
    def _worker_thread_finished(self) -> None:
        worker_kind = self._worker_kind
        self._thread = None
        self._worker = None
        self._worker_kind = ""
        self.progress.setVisible(False)
        self.progress.setRange(0, 0)
        self.cancel_move_button.setVisible(False)
        self.cancel_move_button.setEnabled(True)
        self.import_button.setEnabled(True)
        self.swc_intake_button.setEnabled(True)
        self.consolidate_swcs_button.setEnabled(True)
        self.register_button.setEnabled(True)
        self.neuprint_button.setEnabled(True)
        self.modeldb_button.setEnabled(True)
        self.move_library_button.setEnabled(True)
        self.relink_button.setEnabled(True)
        if worker_kind == "local_import" and self._review_after_import:
            self._review_after_import = False
            self.quality_review_requested.emit()

    @property
    def import_in_progress(self) -> bool:
        return self._thread is not None or self._linked_counter is not None

    @Slot()
    def register_existing_folder(self) -> None:
        try:
            profile, profile_path = self._profile()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Data Library is not configured", str(exc))
            return
        selected = QFileDialog.getExistingDirectory(self, "Choose existing SWC folder")
        if not selected:
            return
        root = Path(selected).resolve()
        try:
            default_id = safe_component(root.name)
        except ValueError:
            default_id = "external-swcs"
        resource_id, accepted = QInputDialog.getText(
            self,
            "Register existing folder",
            "Resource ID",
            text=default_id,
        )
        if not accepted:
            return
        try:
            dataset = self.connectome_picker.selected_dataset
            if not dataset or QMessageBox.question(self, 'Link SWCs to this release?',
                    f'Link {root} read-only to {dataset}?\n\nConfirm that this folder belongs to this exact connectome and release. '
                    'No SWCs will be copied or changed.') != QMessageBox.StandardButton.Yes:
                return
            destination = register_existing_morphology(
                profile,
                root,
                resource_id=resource_id,
                profile_path=profile_path,
                label=root.name,
                dataset=dataset,
            )
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Could not register folder", str(exc))
            return
        self.action_status.setText(
            f"Registered {root} read-only in {destination}; no files were copied."
        )
        self.sources_changed.emit()
        self.refresh()

    @Slot()
    def reveal_library(self) -> None:
        try:
            profile, _ = self._profile()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Data Library is not configured", str(exc))
            return
        if not profile.managed_data_root.is_dir():
            QMessageBox.warning(
                self,
                "Data Library folder is missing",
                "The configured Data Library folder no longer exists. If you moved it, use "
                "Relink moved library and select the relocated root. Workstation will not recreate "
                "the old folder automatically.",
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(profile.managed_data_root)))

    @Slot()
    def inspect_selected(self) -> None:
        resource = self._selected_resource()
        if resource is None:
            return
        ManifestDialog(resource, self).exec()

    @Slot()
    def reveal_selected(self) -> None:
        resource = self._selected_resource()
        if resource is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(resource.root)))

    @Slot()
    def toggle_registration(self) -> None:
        resource = self._selected_resource()
        if resource is None:
            return
        try:
            profile, profile_path = self._profile()
            if resource.is_registered:
                answer = QMessageBox.question(
                    self,
                    "Unregister managed resource",
                    f"Unregister {resource.provider} / {resource.resource_id}?\n\n"
                    "The managed bundle and every file in it will remain unchanged. It can be "
                    "registered again from this table later.",
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
                changed = unregister_managed_resource(
                    profile,
                    resource,
                    profile_path=profile_path,
                )
                detail = f"Unregistered {len(changed)} profile binding(s); managed files were kept."
            else:
                changed = register_managed_resource(
                    profile,
                    resource,
                    profile_path=profile_path,
                )
                detail = f"Registered {len(changed)} read-only profile binding(s)."
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Could not update registration", str(exc))
            return
        self.action_status.setText(detail)
        self.refresh()
        self.sources_changed.emit()

    @Slot()
    def trash_selected(self) -> None:
        resource = self._selected_resource()
        if resource is None:
            return
        answer = QMessageBox.question(
            self,
            "Move managed resource to Library Trash",
            f"Move {resource.provider} / {resource.resource_id} / {resource.source_version} "
            "to the recoverable Data Library Trash?\n\n"
            f"Bundle:\n{resource.root}\n\n"
            "The resource will be unregistered and disappear from active providers. It is not "
            "permanently deleted and can be restored from Manage Trash.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            profile, profile_path = self._profile()
            entry = trash_managed_resource(
                profile,
                resource,
                profile_path=profile_path,
            )
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Could not move resource to Trash", str(exc))
            return
        self.action_status.setText(
            f"Moved {resource.resource_id} to recoverable Library Trash at {entry.trash_root}."
        )
        self.refresh()
        self.sources_changed.emit()

    @Slot()
    def manage_trash(self) -> None:
        try:
            profile, profile_path = self._profile()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Data Library is not configured", str(exc))
            return
        dialog = TrashDialog(profile, profile_path, self)
        dialog.resource_restored.connect(self._resource_restored)
        dialog.resource_purged.connect(self._trash_purged)
        dialog.exec()

    @Slot(str)
    def _trash_purged(self, detail: str) -> None:
        self.action_status.setText(detail)

    @Slot(object)
    def _resource_restored(self, resource: ManagedResource) -> None:
        self.action_status.setText(f"Restored managed resource to {resource.root}.")
        self.refresh()
        self.sources_changed.emit()

    @Slot()
    def move_library(self) -> None:
        if self._thread is not None:
            return
        try:
            profile, profile_path = self._profile()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Data Library is not configured", str(exc))
            return
        selected = QFileDialog.getExistingDirectory(
            self,
            "Choose the destination parent folder",
            str(profile.managed_data_root.parent),
        )
        if not selected:
            return
        parent = Path(selected).expanduser().resolve()
        default_name = profile.managed_data_root.name
        if (parent / default_name).exists():
            default_name = f"{default_name}-moved"
        folder_name, accepted = QInputDialog.getText(
            self,
            "New Data Library folder",
            "Folder name",
            text=default_name,
        )
        if not accepted:
            return
        folder_name = folder_name.strip()
        if (
            not folder_name
            or folder_name in {".", ".."}
            or Path(folder_name).name != folder_name
            or len(folder_name) > 128
        ):
            QMessageBox.warning(
                self,
                "Invalid destination folder",
                "Enter one ordinary folder name without path separators.",
            )
            return
        target = parent / folder_name
        try:
            preview = preview_library_move(profile, target)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Cannot move Data Library", str(exc))
            return
        method = (
            "The folder is on the same filesystem, so Workstation can use an atomic rename."
            if preview.same_filesystem
            else "The destination is on another filesystem. Workstation will copy and checksum "
            "every file, update the profile, and only then remove the source library."
        )
        answer = QMessageBox.question(
            self,
            "Move managed Data Library",
            f"Move the complete Data Library?\n\n"
            f"Current root:\n{preview.source_root}\n\n"
            f"New root:\n{preview.target_root}\n\n{method}",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.import_button.setEnabled(False)
        self.register_button.setEnabled(False)
        self.neuprint_button.setEnabled(False)
        self.modeldb_button.setEnabled(False)
        self.move_library_button.setEnabled(False)
        self.relink_button.setEnabled(False)
        self.progress.setRange(0, 0)
        self.progress.setVisible(True)
        self.cancel_move_button.setVisible(True)
        self.cancel_move_button.setEnabled(True)
        self.action_status.setText("Inventorying the managed Data Library…")
        thread = QThread(self)
        worker = _LibraryMoveWorker(profile, profile_path, preview.target_root)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._library_move_progress)
        worker.completed.connect(self._library_move_completed)
        worker.failed.connect(self._library_move_failed)
        worker.completed.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._worker_thread_finished)
        self._thread = thread
        self._worker = worker
        self._worker_kind = "library_move"
        thread.start()

    @Slot(object)
    def _library_move_progress(self, update: LibraryMoveProgress) -> None:
        if update.total_bytes > 0:
            self.progress.setRange(0, 1000)
            self.progress.setValue(
                min(1000, int(1000 * update.completed_bytes / update.total_bytes))
            )
        elif update.total_files > 0:
            self.progress.setRange(0, update.total_files)
            self.progress.setValue(update.completed_files)
        else:
            self.progress.setRange(0, 0)
        current = f" · {update.current}" if update.current else ""
        self.action_status.setText(
            f"{update.stage}: {update.completed_files:,}/{update.total_files:,} files · "
            f"{_human_bytes(update.completed_bytes)}/{_human_bytes(update.total_bytes)}{current}"
        )

    @Slot()
    def cancel_library_move(self) -> None:
        if isinstance(self._worker, _LibraryMoveWorker):
            self._worker.cancel_event.set()
            self.cancel_move_button.setEnabled(False)
            self.action_status.setText(
                "Cancelling after the current verified file; the original library remains active…"
            )

    @Slot(object)
    def _library_move_completed(self, result: LibraryMoveResult) -> None:
        cleanup = (
            " The verified destination is active, but the old duplicate could not be fully "
            f"removed and remains at {result.source_root}."
            if not result.source_removed
            else ""
        )
        self.action_status.setText(
            f"Moved {result.file_count:,} files ({_human_bytes(result.total_bytes)}) to "
            f"{result.target_root} using {result.mode.replace('_', ' ')}.{cleanup}"
        )
        self.refresh()
        self.sources_changed.emit()

    @Slot(str)
    def _library_move_failed(self, detail: str) -> None:
        if "cancel" in detail.casefold():
            self.action_status.setText("Data Library move cancelled; the original library remains active.")
            return
        self.action_status.setText(f"Data Library move failed: {detail}")
        QMessageBox.critical(self, "Data Library move failed", detail)

    @Slot()
    def relink_library(self) -> None:
        try:
            profile, profile_path = self._profile()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Data Library is not configured", str(exc))
            return
        start = profile.managed_data_root.parent
        selected = QFileDialog.getExistingDirectory(
            self,
            "Choose the relocated Data Library root",
            str(start if start.is_dir() else Path.home()),
        )
        if not selected:
            return
        try:
            preview = preview_library_relink(profile, selected)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Relocated library is incomplete", str(exc))
            return
        answer = QMessageBox.question(
            self,
            "Relink moved Data Library",
            f"Update the resource profile to use this relocated library?\n\n"
            f"Current root:\n{preview.current_root}\n\n"
            f"New root:\n{preview.new_root}\n\n"
            f"Validated {preview.managed_binding_count} managed binding(s) and "
            f"{preview.resource_count} discoverable bundle(s). No data will be copied or moved.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            relink_managed_library(profile, selected, profile_path=profile_path)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Could not relink Data Library", str(exc))
            return
        self.action_status.setText(f"Relinked the Data Library to {preview.new_root}.")
        self.refresh()
        self.sources_changed.emit()

    @Slot()
    def open_neuprint_import(self) -> None:
        try:
            profile, profile_path = self._profile()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Data Library is not configured", str(exc))
            return
        dialog = NeuPrintImportDialog(profile, profile_path, self,
                                     preferred_dataset=self.connectome_picker.selected_dataset)
        dialog.resource_imported.connect(self._neuprint_completed)
        self._neuprint_dialog = dialog
        dialog.exec()
        self._neuprint_dialog = None
        if self._review_after_import:
            self._review_after_import = False
            self.quality_review_requested.emit()

    @Slot(object)
    def _neuprint_completed(self, resource: ManagedResource) -> None:
        self.action_status.setText(
            f"Downloaded {resource.swc_count:,} neuPrint SWC(s) to {resource.root}."
        )
        self.refresh()
        self.sources_changed.emit()
        self._review_after_import = bool(resource.swc_count)

    @Slot()
    def open_modeldb_import(self) -> None:
        try:
            profile, profile_path = self._profile()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Data Library is not configured", str(exc))
            return
        dialog = ModelDBImportDialog(profile, profile_path, self)
        dialog.resource_imported.connect(self._modeldb_completed)
        self._modeldb_dialog = dialog
        dialog.exec()
        self._modeldb_dialog = None
        if self._review_after_import:
            self._review_after_import = False
            self.quality_review_requested.emit()

    @Slot(object)
    def _modeldb_completed(self, resource: ManagedResource) -> None:
        self.action_status.setText(
            f"Imported {resource.file_count:,} inert model file(s) to {resource.root}. "
            f"Audited {resource.swc_count:,} SWC(s)."
        )
        self.refresh()
        self.sources_changed.emit()
        self._review_after_import = bool(resource.swc_count)
