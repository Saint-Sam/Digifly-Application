from __future__ import annotations


APP_STYLE = r"""
QWidget {
    color: #e7edf7;
    font-family: "Inter", "SF Pro Display", "Helvetica Neue", sans-serif;
    font-size: 13px;
}
QMainWindow, QWidget#RootWindow {
    background: #0b1020;
}
QWidget#PageContent, QScrollArea > QWidget > QWidget, QAbstractScrollArea::viewport {
    background: #0b1020;
}
QFrame#Sidebar {
    background: #0e1528;
    border-right: 1px solid #24304a;
}
QFrame#Topbar {
    background: #0b1020;
    border-bottom: 1px solid #1c2941;
}
QFrame#Card {
    background: #121b31;
    border: 1px solid #253451;
    border-radius: 12px;
}
QFrame#Inset {
    background: #0d1528;
    border: 1px solid #22314d;
    border-radius: 8px;
}
QLabel#Brand {
    color: #f5f8ff;
    font-size: 22px;
    font-weight: 700;
}
QLabel#PageTitle {
    color: #f7f9ff;
    font-size: 27px;
    font-weight: 700;
}
QLabel#SectionTitle {
    color: #f2f6ff;
    font-size: 16px;
    font-weight: 650;
}
QLabel#Muted, QLabel[muted="true"] {
    color: #93a4c3;
}
QLabel#Eyebrow {
    color: #70a5ff;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1px;
}
QPushButton {
    min-height: 34px;
    padding: 0 15px;
    border: 1px solid #36517b;
    border-radius: 8px;
    background: #17243d;
    color: #eaf1ff;
    font-weight: 600;
}
QPushButton:hover {
    background: #203253;
    border-color: #557bb7;
}
QPushButton:pressed { background: #111d33; }
QPushButton:disabled {
    color: #697995;
    background: #11182a;
    border-color: #263149;
}
QPushButton[primary="true"] {
    color: white;
    background: #2864d7;
    border-color: #3b76e7;
}
QPushButton[primary="true"]:hover { background: #3472e8; }
QPushButton[danger="true"] {
    background: #3a1c29;
    border-color: #7d344e;
    color: #ffb8ca;
}
QPushButton#NavButton {
    min-height: 42px;
    padding: 0 14px;
    text-align: left;
    border: 0;
    background: transparent;
    color: #9aabc8;
}
QPushButton#NavButton:hover { background: #151f35; color: #edf3ff; }
QPushButton#NavButton:checked {
    background: #18335f;
    color: #f3f7ff;
    border-left: 3px solid #64a0ff;
}
QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTableWidget {
    background: #0b1325;
    border: 1px solid #30415f;
    border-radius: 7px;
    selection-background-color: #2d67c8;
    padding: 6px 9px;
}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border-color: #5791ef;
}
QComboBox::drop-down { border: 0; width: 24px; }
QCheckBox { spacing: 8px; color: #cbd6e9; }
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid #4b6188;
    border-radius: 4px;
    background: #0b1325;
}
QCheckBox::indicator:checked { background: #3275df; border-color: #6aa1f5; }
QScrollArea { border: 0; background: transparent; }
QScrollBar:vertical { background: #0b1020; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: #31415e; border-radius: 5px; min-height: 28px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QHeaderView::section {
    background: #17223a;
    border: 0;
    border-bottom: 1px solid #31415d;
    color: #aebdd5;
    padding: 8px;
    font-weight: 600;
}
QTableWidget { gridline-color: #253451; }
QProgressBar {
    background: #0c1425;
    border: 1px solid #263653;
    border-radius: 5px;
    min-height: 9px;
    max-height: 9px;
    text-align: center;
}
QProgressBar::chunk { background: #3c7be3; border-radius: 4px; }
QMenuBar { background: #0b1020; color: #d9e2f2; }
QMenuBar::item:selected, QMenu::item:selected { background: #233558; }
QMenu { background: #121b31; border: 1px solid #344564; }
QStatusBar { background: #0e1528; color: #93a4c3; border-top: 1px solid #24304a; }
QToolTip { background: #15223a; color: white; border: 1px solid #49658f; padding: 5px; }
"""


STATE_COLORS = {
    "pass": ("#173a2a", "#62d79b", "PASS"),
    "warning": ("#3c3018", "#f0c35e", "CHECK"),
    "fail": ("#401e2a", "#ff7e9e", "BLOCKED"),
    "info": ("#173153", "#72a8ff", "INFO"),
}
