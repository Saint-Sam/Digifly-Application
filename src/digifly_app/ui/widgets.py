from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from digifly_app.core.models import CheckState, EngineProbe, PreflightCheck
from .style import STATE_COLORS


class Card(QFrame):
    def __init__(self, parent: QWidget | None = None, *, inset: bool = False):
        super().__init__(parent)
        self.setObjectName("Inset" if inset else "Card")


class HelpButton(QToolButton):
    """Small, accessible button that opens contextual setting help."""

    help_requested = Signal(str, str)

    def __init__(
        self,
        setting_name: str,
        help_text: str,
        *,
        key: str = "",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setObjectName("HelpButton")
        self.setting_name = str(setting_name)
        self.help_key = str(key)
        self.help_text = str(help_text)
        self.setProperty("helpKey", self.help_key)
        self.setText("?")
        self.setFixedSize(18, 18)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Click for help")
        self.setAccessibleName(f"Help for {setting_name}")
        self.setAccessibleDescription(f"Click for help. {self.help_text}")
        self.help_popup: QMessageBox | None = None
        self.clicked.connect(self.show_help)

    def show_help(self) -> None:
        self.help_requested.emit(self.help_key, self.help_text)
        if self.help_popup is not None:
            self.help_popup.close()
        popup = QMessageBox(self)
        popup.setObjectName("SettingHelpPopover")
        popup.setWindowTitle(f"{self.setting_name} help")
        popup.setIcon(QMessageBox.Icon.Information)
        popup.setText(self.help_text)
        popup.setTextFormat(Qt.TextFormat.PlainText)
        popup.setStandardButtons(QMessageBox.StandardButton.Close)
        popup.setModal(False)
        popup.setAccessibleName(f"{self.accessibleName()} explanation")
        self.help_popup = popup
        popup.open()


class HelpLabel(QWidget):
    """Form label with a neighboring question-mark help button."""

    def __init__(
        self,
        text: str,
        help_text: str,
        *,
        key: str = "",
        buddy: QWidget | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setObjectName(f"HelpLabel_{key}" if key else "HelpLabel")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(5)
        self.text_label = QLabel(str(text))
        if buddy is not None:
            self.text_label.setBuddy(buddy)
        row.addWidget(self.text_label)
        self.help_button = HelpButton(text, help_text, key=key)
        row.addWidget(self.help_button, alignment=Qt.AlignmentFlag.AlignVCenter)


class CollapsibleSection(QWidget):
    """A compact, accessible disclosure section for dense control panels."""

    def __init__(
        self,
        title: str,
        key: str,
        *,
        expanded: bool = False,
        object_name_prefix: str = "Section",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.title = str(title)
        self.key = str(key)
        self.setObjectName(f"{object_name_prefix}_{self.key}")
        self.setProperty("collapsibleSection", True)

        shell = QVBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        self.toggle_button = QToolButton()
        self.toggle_button.setObjectName("CollapsibleSectionHeader")
        self.toggle_button.setProperty("sectionKey", self.key)
        self.toggle_button.setText(self.title)
        self.toggle_button.setCheckable(True)
        self.toggle_button.setChecked(bool(expanded))
        self.toggle_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.toggle_button.setAccessibleName(f"{self.title} settings")
        shell.addWidget(self.toggle_button)

        self.body = QWidget()
        self.body.setObjectName("CollapsibleSectionBody")
        self.content_layout = QVBoxLayout(self.body)
        self.content_layout.setContentsMargins(12, 10, 12, 12)
        self.content_layout.setSpacing(8)
        shell.addWidget(self.body)

        self.toggle_button.toggled.connect(self.set_expanded)
        self.set_expanded(bool(expanded))

    @property
    def is_expanded(self) -> bool:
        return self.toggle_button.isChecked()

    def set_expanded(self, expanded: bool) -> None:
        expanded = bool(expanded)
        if self.toggle_button.isChecked() != expanded:
            self.toggle_button.setChecked(expanded)
            return
        self.toggle_button.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        self.body.setVisible(expanded)
        action = "collapse" if expanded else "expand"
        state = "Expanded" if expanded else "Collapsed"
        self.toggle_button.setToolTip(f"Click to {action} {self.title}")
        self.toggle_button.setAccessibleDescription(
            f"{state} settings section. Activate to {action}."
        )


class StatusPill(QLabel):
    def __init__(self, state: CheckState | str, text: str | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.set_state(state, text=text)
        make_label_copyable(self)

    def set_state(self, state: CheckState | str, *, text: str | None = None) -> None:
        key = state.value if isinstance(state, CheckState) else str(state)
        background, foreground, default_text = STATE_COLORS.get(key, STATE_COLORS["info"])
        self.setText(text or default_text)
        self.setStyleSheet(
            f"background:{background}; color:{foreground}; border:1px solid {foreground}55; "
            "border-radius:9px; padding:2px 8px; font-size:10px; font-weight:700;"
        )


def make_label_copyable(label: QLabel) -> QLabel:
    """Enable native drag selection and right-click Copy without adding a tab stop."""

    label.setTextInteractionFlags(
        label.textInteractionFlags() | Qt.TextInteractionFlag.TextSelectableByMouse
    )
    label.setContextMenuPolicy(Qt.ContextMenuPolicy.DefaultContextMenu)
    return label


class CheckRow(Card):
    def __init__(self, check: PreflightCheck, parent: QWidget | None = None):
        super().__init__(parent, inset=True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(11)
        pill = StatusPill(check.state)
        pill.setFixedWidth(62)
        layout.addWidget(pill, alignment=Qt.AlignmentFlag.AlignTop)
        copy = QVBoxLayout()
        copy.setSpacing(3)
        title = QLabel(check.title)
        title.setObjectName("Strong")
        detail = QLabel(check.detail)
        detail.setObjectName("Muted")
        detail.setWordWrap(True)
        make_label_copyable(title)
        make_label_copyable(detail)
        copy.addWidget(title)
        copy.addWidget(detail)
        layout.addLayout(copy, 1)
        if check.path:
            button = QPushButton("Reveal")
            button.setFixedWidth(72)
            button.clicked.connect(lambda: self._open_path(check.path or ""))
            layout.addWidget(button, alignment=Qt.AlignmentFlag.AlignTop)

    @staticmethod
    def _open_path(raw: str) -> None:
        from PySide6.QtCore import QUrl

        path = Path(raw).expanduser()
        target = path if path.exists() else path.parent
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))


class EngineCard(Card):
    selected = Signal(str)

    def __init__(self, probe: EngineProbe, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 14, 15, 14)
        layout.setSpacing(8)
        top = QHBoxLayout()
        name = QLabel(probe.name)
        name.setObjectName("SectionTitle")
        make_label_copyable(name)
        top.addWidget(name, 1)
        combined = (
            CheckState.PASS
            if probe.source_state == CheckState.PASS and probe.runtime_state == CheckState.PASS
            else CheckState.WARNING
            if probe.source_state != CheckState.FAIL
            else CheckState.FAIL
        )
        top.addWidget(StatusPill(combined))
        layout.addLayout(top)
        summary = QLabel(probe.summary)
        summary.setObjectName("Muted")
        summary.setWordWrap(True)
        make_label_copyable(summary)
        layout.addWidget(summary)
        source = QLabel(f"Source: {probe.source_state.value}  ·  Runtime: {probe.runtime_state.value}")
        source.setObjectName("AccentMeta")
        make_label_copyable(source)
        layout.addWidget(source)
        if probe.details:
            detail = QLabel("\n".join(probe.details))
            detail.setObjectName("Muted")
            detail.setWordWrap(True)
            make_label_copyable(detail)
            layout.addWidget(detail)


def clear_layout(layout: QVBoxLayout | QHBoxLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        child_layout = item.layout()
        if widget is not None:
            widget.deleteLater()
        elif child_layout is not None:
            clear_layout(child_layout)  # type: ignore[arg-type]
