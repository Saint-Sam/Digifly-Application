"""Read-only manifest inspection and recoverable Data Library Trash UI."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from digifly_app.core.data_library import ManagedResource
from digifly_app.core.resource_management import (
    TrashEntry,
    list_trash_entries,
    purge_trashed_resource,
    restore_trashed_resource,
)
from digifly_app.core.resource_profile import ResourceProfile

from .widgets import Card


def _human_bytes(value: int) -> str:
    amount = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1024 or unit == "TB":
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{value} B"


class ManifestDialog(QDialog):
    def __init__(self, resource: ManagedResource, parent: QWidget | None = None):
        super().__init__(parent)
        self.resource = resource
        self.setWindowTitle(f"Managed resource manifest — {resource.resource_id}")
        self.resize(820, 680)
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 20)
        root.setSpacing(12)

        title = QLabel(f"{resource.provider} / {resource.resource_id}")
        title.setObjectName("PageTitle")
        root.addWidget(title)
        try:
            payload = json.loads(resource.manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            payload = {"manifest_error": str(exc)}

        summary = Card()
        form = QFormLayout(summary)
        form.setContentsMargins(16, 15, 16, 15)
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(7)
        fields = (
            ("Registration", "Registered" if resource.is_registered else "Stored only / unregistered"),
            ("Version", resource.source_version),
            ("Contents", f"{resource.file_count:,} file(s) · {resource.swc_count:,} SWC(s) · {_human_bytes(resource.total_bytes)}"),
            ("Imported", resource.imported_at or "Not recorded"),
            ("Bundle", str(resource.root)),
            ("Manifest", str(resource.manifest)),
            ("Source", str(payload.get("source_url") or payload.get("server") or "Not recorded")),
            ("Citation", str(payload.get("citation") or "Not recorded")),
            ("License", str(payload.get("license") or "Not recorded")),
            (
                "Execution policy",
                "Inert; no execution performed"
                if bool(payload.get("inert_code"))
                else "Scientific data resource",
            ),
        )
        for label, value in fields:
            widget = QLabel(value)
            widget.setWordWrap(True)
            widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            form.addRow(label, widget)
        quality = payload.get("post_import_quality")
        if isinstance(quality, dict):
            quality_label = QLabel(
                f"{int(quality.get('audited_swcs', 0)):,} audited · "
                f"{int(quality.get('needs_review', 0)):,} need review · "
                f"{int(quality.get('structural_errors', 0)):,} structural errors"
            )
            quality_label.setWordWrap(True)
            form.addRow("SWC quality", quality_label)
        root.addWidget(summary)

        files = payload.get("files")
        display_payload = dict(payload)
        if isinstance(files, list) and len(files) > 200:
            display_payload["files"] = files[:200]
            display_payload["_display_note"] = (
                f"Showing the first 200 of {len(files):,} inventory records. "
                f"The complete manifest remains at {resource.manifest}."
            )
        raw_label = QLabel("Provenance manifest (read-only inventory preview)")
        raw_label.setObjectName("Muted")
        root.addWidget(raw_label)
        self.raw = QPlainTextEdit()
        self.raw.setReadOnly(True)
        self.raw.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.raw.setPlainText(json.dumps(display_payload, indent=2, sort_keys=True))
        root.addWidget(self.raw, 1)

        actions = QHBoxLayout()
        reveal = QPushButton("Reveal bundle")
        reveal.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(resource.root)))
        )
        actions.addWidget(reveal)
        source_url = str(payload.get("source_url") or "")
        if source_url.startswith("https://"):
            upstream = QPushButton("Open upstream source")
            upstream.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(source_url)))
            actions.addWidget(upstream)
        actions.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        actions.addWidget(close)
        root.addLayout(actions)


class TrashDialog(QDialog):
    resource_restored = Signal(object)
    resource_purged = Signal(str)

    def __init__(
        self,
        profile: ResourceProfile,
        profile_path: Path,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.profile = profile
        self.profile_path = Path(profile_path).expanduser().resolve()
        self._entries: tuple[TrashEntry, ...] = ()
        self.setWindowTitle("Data Library Trash")
        self.resize(790, 460)
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 20)
        root.setSpacing(12)
        title = QLabel("Recoverable Data Library Trash")
        title.setObjectName("PageTitle")
        detail = QLabel(
            "Removing a managed resource moves its complete bundle here. Restore returns it "
            "to the exact original managed path and reinstates its previous profile bindings."
        )
        detail.setObjectName("Muted")
        detail.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(detail)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ("Provider", "Resource", "Version", "Moved", "Original location")
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        root.addWidget(self.table, 1)
        self.status = QLabel("Ready")
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        actions = QHBoxLayout()
        reveal = QPushButton("Reveal Trash")
        reveal.clicked.connect(self.reveal_trash)
        actions.addWidget(reveal)
        self.restore_button = QPushButton("Restore selected")
        self.restore_button.setProperty("primary", True)
        self.restore_button.setEnabled(False)
        self.restore_button.clicked.connect(self.restore_selected)
        actions.addWidget(self.restore_button)
        self.purge_button = QPushButton("Permanently delete selected…")
        self.purge_button.setEnabled(False)
        self.purge_button.clicked.connect(self.purge_selected)
        actions.addWidget(self.purge_button)
        actions.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        actions.addWidget(close)
        root.addLayout(actions)
        self.refresh()

    def _current_entry(self) -> TrashEntry | None:
        rows = self.table.selectionModel().selectedRows()
        if len(rows) != 1:
            return None
        row = rows[0].row()
        return self._entries[row] if 0 <= row < len(self._entries) else None

    @Slot()
    def _selection_changed(self) -> None:
        selected = self._current_entry() is not None
        self.restore_button.setEnabled(selected)
        self.purge_button.setEnabled(selected)

    def refresh(self) -> None:
        try:
            self.profile = ResourceProfile.load(self.profile_path)
            self._entries = list_trash_entries(self.profile)
        except (OSError, ValueError) as exc:
            self._entries = ()
            self.status.setText(str(exc))
        self.table.setRowCount(0)
        for entry in self._entries:
            row = self.table.rowCount()
            self.table.insertRow(row)
            values = (
                entry.provider,
                entry.resource_id,
                entry.source_version,
                entry.trashed_at,
                str(entry.original_root),
            )
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))
        if self._entries:
            self.status.setText(f"{len(self._entries)} recoverable resource(s)")
        else:
            self.status.setText("Data Library Trash is empty")
        self._selection_changed()

    @Slot()
    def restore_selected(self) -> None:
        entry = self._current_entry()
        if entry is None:
            return
        answer = QMessageBox.question(
            self,
            "Restore managed resource",
            f"Restore {entry.provider} / {entry.resource_id} / {entry.source_version}?\n\n"
            f"Original location:\n{entry.original_root}\n\n"
            "Its exact previous profile bindings will also be restored.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            resource = restore_trashed_resource(
                self.profile,
                entry,
                profile_path=self.profile_path,
            )
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Could not restore resource", str(exc))
            return
        self.resource_restored.emit(resource)
        self.refresh()

    @Slot()
    def purge_selected(self) -> None:
        entry = self._current_entry()
        if entry is None:
            return
        answer = QMessageBox.question(
            self,
            "Permanently delete managed resource",
            f"Permanently delete {entry.provider} / {entry.resource_id} / "
            f"{entry.source_version}?\n\n"
            f"Trash location:\n{entry.trash_root}\n\n"
            "This removes the complete bundle immediately. It cannot be restored from "
            "Digifly or the system Trash.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            file_count, total_bytes = purge_trashed_resource(self.profile, entry)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Could not permanently delete resource", str(exc))
            return
        detail = (
            f"Permanently deleted {entry.resource_id}: {file_count:,} file(s), "
            f"{_human_bytes(total_bytes)}."
        )
        self.resource_purged.emit(detail)
        self.refresh()
        self.status.setText(detail)

    @Slot()
    def reveal_trash(self) -> None:
        trash = self.profile.managed_data_root / ".trash"
        if not trash.is_dir():
            QMessageBox.information(
                self,
                "Data Library Trash is empty",
                "No recoverable Trash folder exists yet.",
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(trash)))
