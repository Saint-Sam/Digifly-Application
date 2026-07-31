from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from digifly_app.core.models import CheckState, EngineProbe, PreflightCheck
from .style import STATE_COLORS


class Card(QFrame):
    def __init__(self, parent: QWidget | None = None, *, inset: bool = False):
        super().__init__(parent)
        self.setObjectName("Inset" if inset else "Card")


class StatusPill(QLabel):
    def __init__(self, state: CheckState | str, text: str | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.set_state(state, text=text)

    def set_state(self, state: CheckState | str, *, text: str | None = None) -> None:
        key = state.value if isinstance(state, CheckState) else str(state)
        background, foreground, default_text = STATE_COLORS.get(key, STATE_COLORS["info"])
        self.setText(text or default_text)
        self.setStyleSheet(
            f"background:{background}; color:{foreground}; border:1px solid {foreground}55; "
            "border-radius:9px; padding:2px 8px; font-size:10px; font-weight:700;"
        )


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
        title.setStyleSheet("font-weight:650; color:#edf3ff;")
        detail = QLabel(check.detail)
        detail.setObjectName("Muted")
        detail.setWordWrap(True)
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
        layout.addWidget(summary)
        source = QLabel(f"Source: {probe.source_state.value}  ·  Runtime: {probe.runtime_state.value}")
        source.setStyleSheet("color:#7faaf0; font-size:11px; font-weight:600;")
        layout.addWidget(source)
        if probe.details:
            detail = QLabel("\n".join(probe.details))
            detail.setObjectName("Muted")
            detail.setWordWrap(True)
            detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
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
