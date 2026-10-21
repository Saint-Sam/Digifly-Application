"""Inline family/type/neuron navigation; scans run off the GUI thread."""
from __future__ import annotations

from collections import Counter
import threading

from PySide6.QtCore import QThread, Signal, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                              QStackedWidget, QScrollArea, QVBoxLayout, QWidget, QSizePolicy)

from digifly_app.core.saved_neurons import SavedInventory, scan_saved_neurons
from digifly_app.core.swc_library import FAMILIES
from .widgets import Card


class _Scan(QThread):
    result = Signal(int, object)

    def __init__(self, sources, dataset, generation, parent):
        super().__init__(parent)
        self.sources, self.dataset, self.generation = sources, dataset, generation
        self.cancel = threading.Event()

    def run(self):
        try:
            result = scan_saved_neurons(self.sources, self.dataset, cancel=self.cancel)
        except InterruptedError:
            return
        except Exception as error:
            result = SavedInventory(self.dataset, (), (str(error),))
        self.result.emit(self.generation, result)


class _InlineStack(QStackedWidget):
    # Qt normally reserves the largest hidden page's height. A family grid
    # must not inherit the type table's height or stretch into empty space.
    def sizeHint(self):
        return self.currentWidget().sizeHint() if self.currentWidget() else super().sizeHint()

    def minimumSizeHint(self):
        return self.currentWidget().minimumSizeHint() if self.currentWidget() else super().minimumSizeHint()


class SavedNeuronBrowser(Card):
    PAGE_SIZE = 200
    inventory_changed = Signal()
    neuron_chosen = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.generation = 0
        self.inventory = None
        self._workers = set()
        self._selected_family = self._selected_type = ''
        self._page = 0
        self._rows = ()
        self.row_buttons = []
        self.row_values = ()
        self._row_generation = 0
        self._selected_row = -1
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        heading = QHBoxLayout()
        self.title = QLabel('Saved neurons')
        self.title.setObjectName('SectionTitle')
        heading.addWidget(self.title)
        heading.addStretch()
        self.cancel_button = QPushButton('Cancel scan')
        self.cancel_button.clicked.connect(self.cancel)
        self.cancel_button.hide()
        heading.addWidget(self.cancel_button)
        layout.addLayout(heading)
        self.status = QLabel('Choose a connectome release')
        self.status.setObjectName('Muted')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.stack = _InlineStack()
        self.stack.currentChanged.connect(lambda: self.stack.updateGeometry())
        layout.addWidget(self.stack)
        families = QWidget()
        grid = QGridLayout(families)
        grid.setContentsMargins(0, 8, 0, 0)
        grid.setSpacing(8)
        grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.family_buttons = {}
        for i, family in enumerate(FAMILIES):
            button = QPushButton(f'{family} · —')
            button.setMinimumHeight(48)
            button.clicked.connect(lambda checked=False, value=family: self.open_family(value))
            grid.addWidget(button, i // 3, i % 3)
            self.family_buttons[family] = button
        self.stack.addWidget(families)
        details = QWidget()
        detail = QVBoxLayout(details)
        detail.setContentsMargins(0, 8, 0, 0)
        navigation = QHBoxLayout()
        self.back = QPushButton('← Families')
        self.back.clicked.connect(self.go_back)
        navigation.addWidget(self.back)
        self.breadcrumb = QLabel()
        navigation.addWidget(self.breadcrumb)
        navigation.addStretch()
        detail.addLayout(navigation)
        self.search = QLineEdit()
        self.search.setPlaceholderText('Filter types…')
        self.search.textChanged.connect(self._filter_changed)
        detail.addWidget(self.search)
        self.rows_heading = QLabel('Neuron types')
        detail.addWidget(self.rows_heading)
        # Ordinary buttons avoid Cocoa's mutable table-cell accessibility
        # cache (actual21.2 crash retained in the local repair evidence).
        # Paginate so even a full connectome creates at most200 row widgets.
        self.row_list = QScrollArea()
        self.row_list.setWidgetResizable(True)
        self.row_list.setMinimumHeight(180)
        self.row_list.setMaximumHeight(320)
        self.row_host = QWidget()
        self.row_layout = QVBoxLayout(self.row_host)
        self.row_layout.setContentsMargins(4, 4, 4, 4)
        self.row_layout.setSpacing(4)
        self.row_list.setWidget(self.row_host)
        detail.addWidget(self.row_list)
        self.location = QLabel()
        self.location.setWordWrap(True)
        self.location.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        detail.addWidget(self.location)
        footer = QHBoxLayout()
        self.previous = QPushButton('Previous')
        self.previous.clicked.connect(lambda: self._paginate(-1))
        footer.addWidget(self.previous)
        self.page_label = QLabel()
        footer.addWidget(self.page_label)
        self.next = QPushButton('Next')
        self.next.clicked.connect(lambda: self._paginate(1))
        footer.addWidget(self.next)
        footer.addStretch()
        self.open_folder = QPushButton('Open neuron folder')
        self.open_folder.clicked.connect(self._open_folder)
        footer.addWidget(self.open_folder)
        self.choose_neuron = QPushButton('Add to selection')
        self.choose_neuron.clicked.connect(self._choose_neuron)
        self.choose_neuron.hide()
        self.choose_neuron.setEnabled(False)
        footer.addWidget(self.choose_neuron)
        detail.addLayout(footer)
        self.stack.addWidget(details)
        self._reset()

    def _reset(self):
        self.inventory = None
        self._selected_family = self._selected_type = ''
        self.stack.setCurrentIndex(0)
        self._clear_rows()
        self.location.clear()
        self.open_folder.setEnabled(False)
        self.choose_neuron.setEnabled(False)
        for family, button in self.family_buttons.items():
            button.setText(f'{family} · —')
            button.setAccessibleName(f'{family}, saved-neuron count not ready')
            button.setEnabled(False)

    def load(self, sources, dataset):
        self.cancel(quiet=True)
        self.generation += 1
        self._reset()
        if not dataset:
            self.status.setText('Choose a connectome release')
            return
        self.status.setText('Counting saved neurons… Files stay in their original folders.')
        self.cancel_button.show()
        worker = _Scan(sources, dataset, self.generation, self)
        self._workers.add(worker)
        worker.result.connect(self._received)
        def finished():
            self._workers.discard(worker)
            worker.deleteLater()
        worker.finished.connect(finished)
        worker.start()

    def cancel(self, *, quiet=False):
        for worker in self._workers:
            worker.cancel.set()
        self.generation += 1
        self.cancel_button.hide()
        if not quiet:
            self._reset()
            self.status.setText('Count cancelled. Refresh to count again; no files changed.')

    def shutdown(self):
        self.cancel(quiet=True)
        # Workers check cancellation between directory entries. Keep QThread
        # alive until it exits rather than destroying a running thread.
        for worker in tuple(self._workers):
            worker.wait()

    def _received(self, generation, inventory):
        if generation != self.generation:
            return
        self.inventory = inventory
        self.cancel_button.hide()
        qualifier = 'At least ' if inventory.issues else ''
        text = f'{qualifier}{len(inventory.neurons):,} saved neurons · {inventory.dataset}. Click a family to browse.'
        text += ' Counts combine matching saved folders; extra SWC copies count once.'
        if len({n.server for n in inventory.neurons}) > 1:
            text += ' Different servers are counted separately.'
        if inventory.issues:
            text += f' {len(inventory.issues)} folder issue(s); count may be incomplete.'
        if inventory.unidentified_files:
            text += f' {inventory.unidentified_files:,} SWC file(s) have no body ID and are not counted.'
        if inventory.skipped_links:
            text += f' {inventory.skipped_links:,} links skipped.'
        self.status.setText(text)
        self.status.setToolTip('\n'.join(inventory.issues))
        for family, button in self.family_buttons.items():
            count = len(inventory.family(family))
            button.setText(f'{family} · {count:,} {"neuron" if count == 1 else "neurons"}  ›')
            button.setAccessibleName(f'{family}, {count:,} saved neurons, open family')
            button.setEnabled(True)
        self.inventory_changed.emit()

    def open_family(self, family):
        if self.inventory is None or family not in FAMILIES:
            return
        self._selected_family, self._selected_type = family, ''
        self.search.clear()
        self.search.setPlaceholderText('Filter types…')
        self.stack.setCurrentIndex(1)
        self._filter_changed()
        self.back.setFocus()

    def go_back(self):
        if self._selected_type:
            self.open_family(self._selected_family)
        else:
            self.stack.setCurrentIndex(0)
            self.family_buttons[self._selected_family].setFocus()

    def _filter_changed(self):
        self._page = 0
        self._render()

    def _paginate(self, delta):
        self._page = max(0, self._page + delta)
        self._render()

    def _clear_rows(self):
        self.choose_neuron.setEnabled(False)
        self._row_generation += 1
        self._rows, self.row_values = (), ()
        self._selected_row = -1
        self.row_buttons = []
        while self.row_layout.count():
            item = self.row_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

    def _render(self):
        if self.inventory is None:
            return
        self.location.clear()
        self.open_folder.setEnabled(False)
        self.open_folder.setVisible(bool(self._selected_type))
        query = self.search.text().strip().casefold()
        values = self.inventory.family(self._selected_family)
        if self._selected_type:
            values = tuple(n for n in values if n.neuron_type == self._selected_type and
                           (not query or query in n.body_id or query in n.server.casefold()))
            rows = [(n.body_id, str(len(n.paths)), n) for n in values]
            self.rows_heading.setText('Neuron IDs · saved SWC copies')
            self.back.setText('← Types')
        else:
            counts = Counter(n.neuron_type for n in values)
            rows = [(label, f'{count:,}  ›', label) for label, count in sorted(counts.items(), key=lambda v: v[0].casefold())
                    if not query or query in label.casefold()]
            self.rows_heading.setText('Neuron types · saved neurons')
            self.back.setText('← Families')
        self.breadcrumb.setText(self._selected_family + (f' / {self._selected_type}' if self._selected_type else ''))
        self._page = min(self._page, max(0, (len(rows) - 1) // self.PAGE_SIZE))
        start = self._page * self.PAGE_SIZE
        self._clear_rows()
        self._rows = tuple(row[2] for row in rows[start:start + self.PAGE_SIZE])
        self.row_values = tuple((row[0], row[1]) for row in rows[start:start + self.PAGE_SIZE])
        generation = self._row_generation
        for i, row in enumerate(rows[start:start + self.PAGE_SIZE]):
            suffix = (f'{row[1]} saved SWC' + ('' if row[1] == '1' else 's') if self._selected_type else
                      f'{row[1].replace("  ›", "")} saved ' + ('neuron' if row[1] == '1  ›' else 'neurons') + '  ›')
            button = QPushButton(f'{row[0]}   ·   {suffix}')
            button.setAccessibleName(f'Neuron {row[0]}, {row[1]} saved SWC copies' if self._selected_type else
                                     f'Open type {row[0]}, {row[1].replace("  ›", "")} saved neurons')
            button.setStyleSheet('text-align: left;')
            button.setMinimumHeight(36)
            # Ordinary push buttons emit the same activation for a mouse,
            # keyboard and native accessibility; checkboxes may only toggle.
            button.setCheckable(False)
            if self._selected_type:
                button.setToolTip(row[2].server + '\n' + '\n'.join(map(str, row[2].paths)))
            button.clicked.connect(lambda checked=False, index=i, expected=generation: self._activate(index, expected))
            self.row_buttons.append(button)
            self.row_layout.addWidget(button)
        self.row_layout.addStretch()
        self.row_list.verticalScrollBar().setValue(0)
        self.page_label.setText(f'{start + 1 if rows else 0}–{min(start + self.PAGE_SIZE, len(rows))} of {len(rows):,}' if rows else 'No saved neurons here yet')
        self.previous.setEnabled(self._page > 0)
        self.next.setEnabled(start + self.PAGE_SIZE < len(rows))

    def _activate(self, row, generation):
        if generation != self._row_generation or self.inventory is None or not 0 <= row < len(self._rows):
            return
        if not self._selected_type:
            self._selected_type = self._rows[row]
            self.search.clear()
            self.search.setPlaceholderText('Filter neuron IDs…')
            self._filter_changed()
        else:
            neuron = self._rows[row]
            self._selected_row = row
            self.location.setText(neuron.server + '\n' + '\n'.join(map(str, neuron.paths)))
            self.open_folder.setEnabled(True)
            self.choose_neuron.setEnabled(True)

    def _choose_neuron(self):
        if self.inventory is not None and self._selected_type and 0 <= self._selected_row < len(self._rows):
            self.neuron_chosen.emit(self._rows[self._selected_row].body_id)

    def _open_folder(self):
        row = self._selected_row
        if not self._selected_type or self.inventory is None or not 0 <= row < len(self._rows):
            return
        # Recheck existence: no fallback to another release or managed root.
        path = self._rows[row].paths[0]
        if path.is_file() and not path.is_symlink():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))
        else:
            self.location.setText('Saved file is no longer available. Refresh to recount.')
