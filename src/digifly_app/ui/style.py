from __future__ import annotations

from typing import Final


DARK_THEME: Final = "dark"
LIGHT_THEME: Final = "light"
DEFAULT_THEME: Final = DARK_THEME
THEMES: Final = (DARK_THEME, LIGHT_THEME)


THEME_PALETTES: dict[str, dict[str, str]] = {
    DARK_THEME: {
        "text": "#e7edf7",
        "root": "#0b1020",
        "sidebar": "#0e1528",
        "sidebar_border": "#24304a",
        "topbar": "#0b1020",
        "topbar_border": "#1c2941",
        "card": "#121b31",
        "card_border": "#253451",
        "inset": "#0d1528",
        "inset_border": "#22314d",
        "side_panel": "#0e172b",
        "section": "#101b31",
        "section_border": "#2a3b59",
        "section_header": "#14213a",
        "section_header_hover": "#1c2d4c",
        "section_header_checked": "#192a48",
        "section_body": "#0d172a",
        "strong": "#edf3ff",
        "brand": "#f5f8ff",
        "title": "#f7f9ff",
        "section_title": "#f2f6ff",
        "muted": "#93a4c3",
        "eyebrow": "#70a5ff",
        "accent_meta": "#7faaf0",
        "warning_text": "#f0c76d",
        "button": "#17243d",
        "button_border": "#36517b",
        "button_text": "#eaf1ff",
        "button_hover": "#203253",
        "button_hover_border": "#557bb7",
        "button_pressed": "#111d33",
        "button_disabled": "#11182a",
        "button_disabled_border": "#263149",
        "button_disabled_text": "#697995",
        "primary": "#2864d7",
        "primary_hover": "#3472e8",
        "primary_border": "#3b76e7",
        "danger": "#3a1c29",
        "danger_border": "#7d344e",
        "danger_text": "#ffb8ca",
        "nav_text": "#9aabc8",
        "nav_hover": "#151f35",
        "nav_selected": "#18335f",
        "nav_indicator": "#64a0ff",
        "view_button": "#0d172a",
        "view_border": "#30415f",
        "input": "#0b1325",
        "input_border": "#30415f",
        "focus": "#5791ef",
        "selection": "#2d67c8",
        "checkbox_text": "#cbd6e9",
        "scrollbar": "#0b1020",
        "scrollbar_handle": "#31415e",
        "table_header": "#17223a",
        "table_header_text": "#aebdd5",
        "progress": "#0c1425",
        "progress_border": "#263653",
        "progress_chunk": "#3c7be3",
        "menu_selected": "#233558",
        "tooltip": "#15223a",
        "tooltip_border": "#49658f",
        "theme_button": "#151f35",
        "theme_button_hover": "#213354",
        "theme_button_border": "#334766",
        "theme_button_text": "#ffd66b",
        "rubber_band": "#ffffff",
        "rubber_band_fill": "rgba(255,255,255,35)",
        "image_surface": "#080d19",
        "viewport_background": "#071021",
        "stimulus_surface": "#081326",
        "stimulus_border": "#2a3d5e",
        "stimulus_title": "#edf5ff",
        "stimulus_grid": "#203653",
        "stimulus_axis": "#6f87ad",
        "stimulus_label": "#8ea2c2",
        "stimulus_signal": "#4fd7ff",
        "stimulus_signal_fill": "#40cdff",
        "stimulus_status": "#6fcff0",
    },
    LIGHT_THEME: {
        "text": "#1b2a3d",
        "root": "#f4f7fb",
        "sidebar": "#e8eef7",
        "sidebar_border": "#c7d3e2",
        "topbar": "#ffffff",
        "topbar_border": "#d4deea",
        "card": "#ffffff",
        "card_border": "#cad6e4",
        "inset": "#f1f5fa",
        "inset_border": "#ccd7e4",
        "side_panel": "#eef3f9",
        "section": "#f7f9fc",
        "section_border": "#c7d4e4",
        "section_header": "#edf3fa",
        "section_header_hover": "#e2ebf6",
        "section_header_checked": "#dce8f7",
        "section_body": "#f8fafd",
        "strong": "#17283e",
        "brand": "#12243d",
        "title": "#142239",
        "section_title": "#1a2b42",
        "muted": "#61728a",
        "eyebrow": "#3169b3",
        "accent_meta": "#326bb7",
        "warning_text": "#8a5a0a",
        "button": "#f4f7fb",
        "button_border": "#aebed2",
        "button_text": "#20344f",
        "button_hover": "#e8eff8",
        "button_hover_border": "#7896bc",
        "button_pressed": "#dce6f2",
        "button_disabled": "#edf1f5",
        "button_disabled_border": "#d4dbe4",
        "button_disabled_text": "#8b98a8",
        "primary": "#2864c7",
        "primary_hover": "#1f57b4",
        "primary_border": "#2459ad",
        "danger": "#fff0f3",
        "danger_border": "#d691a4",
        "danger_text": "#9b3150",
        "nav_text": "#526781",
        "nav_hover": "#dde7f3",
        "nav_selected": "#d4e4f8",
        "nav_indicator": "#2864c7",
        "view_button": "#f4f7fb",
        "view_border": "#adbed2",
        "input": "#ffffff",
        "input_border": "#aebdd0",
        "focus": "#3474ca",
        "selection": "#3474ca",
        "checkbox_text": "#2c405b",
        "scrollbar": "#edf2f7",
        "scrollbar_handle": "#aebccd",
        "table_header": "#e8eef6",
        "table_header_text": "#41566f",
        "progress": "#e7edf4",
        "progress_border": "#c1cedd",
        "progress_chunk": "#3474ca",
        "menu_selected": "#d9e7f8",
        "tooltip": "#213653",
        "tooltip_border": "#607c9f",
        "theme_button": "#ffffff",
        "theme_button_hover": "#f6edcf",
        "theme_button_border": "#b7c5d6",
        "theme_button_text": "#b46b00",
        "rubber_band": "#245d9f",
        "rubber_band_fill": "rgba(52,116,202,35)",
        "image_surface": "#eef3f8",
        "viewport_background": "#e8eef6",
        "stimulus_surface": "#f7f9fc",
        "stimulus_border": "#b8c7da",
        "stimulus_title": "#172a43",
        "stimulus_grid": "#d8e1ec",
        "stimulus_axis": "#70839d",
        "stimulus_label": "#526984",
        "stimulus_signal": "#087ea4",
        "stimulus_signal_fill": "#20a9d6",
        "stimulus_status": "#176f8d",
    },
}


def normalize_theme(theme: object) -> str:
    value = str(theme or "").strip().lower()
    return value if value in THEMES else DEFAULT_THEME


def theme_color(theme: object, key: str) -> str:
    return THEME_PALETTES[normalize_theme(theme)][key]


_STYLE_TEMPLATE = r"""
QWidget {
    color: @text@;
    font-family: "Inter", "SF Pro Display", "Helvetica Neue", sans-serif;
    font-size: 13px;
}
QMainWindow, QWidget#RootWindow { background: @root@; }
QDialog, QMessageBox { background: @root@; }
QMessageBox QLabel { color: @text@; background: transparent; }
QWidget#PageContent, QScrollArea > QWidget > QWidget, QAbstractScrollArea::viewport { background: @root@; }
QFrame#Sidebar { background: @sidebar@; border-right: 1px solid @sidebar_border@; }
QFrame#Topbar { background: @topbar@; border-bottom: 1px solid @topbar_border@; }
QFrame#Card { background: @card@; border: 1px solid @card_border@; border-radius: 12px; }
QFrame#Inset { background: @inset@; border: 1px solid @inset_border@; border-radius: 8px; }
QWidget#HHSidePanel { background: @side_panel@; border-left: 1px solid @card_border@; }
QWidget[collapsibleSection="true"] { background: @section@; border: 1px solid @section_border@; border-radius: 8px; }
QToolButton#CollapsibleSectionHeader {
    min-height: 38px; padding: 0 10px; border: 0; border-radius: 7px;
    background: @section_header@; color: @strong@; font-weight: 650; text-align: left;
}
QToolButton#CollapsibleSectionHeader:hover { background: @section_header_hover@; color: @title@; }
QToolButton#CollapsibleSectionHeader:checked {
    background: @section_header_checked@; color: @title@;
    border-bottom-left-radius: 0; border-bottom-right-radius: 0;
}
QToolButton#HelpButton {
    min-width: 18px; max-width: 18px; min-height: 18px; max-height: 18px; padding: 0;
    border: 1px solid @focus@; border-radius: 9px; background: @section_header@;
    color: @eyebrow@; font-size: 11px; font-weight: 750;
}
QToolButton#HelpButton:hover, QToolButton#HelpButton:focus {
    border-color: @eyebrow@; background: @section_header_hover@; color: @title@;
}
QToolButton#ThemeToggle {
    min-width: 30px; max-width: 30px; min-height: 30px; max-height: 30px; padding: 0;
    border: 1px solid @theme_button_border@; border-radius: 15px;
    background: @theme_button@; color: @theme_button_text@; font-size: 17px;
}
QToolButton#ThemeToggle:hover, QToolButton#ThemeToggle:focus {
    background: @theme_button_hover@; border-color: @eyebrow@;
}
QToolButton#ThemeToggle:pressed { background: @button_pressed@; }
QWidget#CollapsibleSectionBody { background: @section_body@; border-top: 1px solid @section_border@; }
QOpenGLWidget#CircuitViewport { background: @viewport_background@; border: 1px solid @section_border@; border-radius: 8px; }
QRubberBand { border: 1px solid @rubber_band@; background-color: @rubber_band_fill@; }
QLabel#Brand { color: @brand@; font-size: 22px; font-weight: 700; }
QLabel#PageTitle { color: @title@; font-size: 27px; font-weight: 700; }
QLabel#SectionTitle { color: @section_title@; font-size: 16px; font-weight: 650; }
QLabel#Strong, QLabel[strong="true"] { color: @strong@; font-weight: 650; }
QLabel#MetricValue { color: @strong@; font-size: 20px; font-weight: 700; }
QLabel#Muted, QLabel[muted="true"] { color: @muted@; }
QLabel#Eyebrow { color: @eyebrow@; font-size: 11px; font-weight: 700; letter-spacing: 1px; }
QLabel#AccentMeta { color: @accent_meta@; font-size: 11px; font-weight: 600; }
QLabel#WarningTitle { color: @warning_text@; font-weight: 650; }
QPushButton {
    min-height: 34px; padding: 0 15px; border: 1px solid @button_border@; border-radius: 8px;
    background: @button@; color: @button_text@; font-weight: 600;
}
QPushButton:hover { background: @button_hover@; border-color: @button_hover_border@; }
QPushButton:pressed { background: @button_pressed@; }
QPushButton:disabled { color: @button_disabled_text@; background: @button_disabled@; border-color: @button_disabled_border@; }
QPushButton[primary="true"] { color: white; background: @primary@; border-color: @primary_border@; }
QPushButton[primary="true"]:hover { background: @primary_hover@; }
QPushButton[danger="true"] { background: @danger@; border-color: @danger_border@; color: @danger_text@; }
QPushButton#NavButton {
    min-height: 42px; padding: 0 14px; text-align: left; border: 0;
    background: transparent; color: @nav_text@;
}
QPushButton#NavButton:hover { background: @nav_hover@; color: @strong@; }
QPushButton#NavButton:checked { background: @nav_selected@; color: @strong@; border-left: 3px solid @nav_indicator@; }
QPushButton#ViewModeButton {
    min-height: 30px; padding: 0 12px; color: @nav_text@;
    background: @view_button@; border-color: @view_border@;
}
QPushButton#ViewModeButton:hover { color: @strong@; background: @button_hover@; }
QPushButton#ViewModeButton:checked { color: white; background: @primary@; border-color: @eyebrow@; }
QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTableWidget {
    color: @text@; background: @input@; border: 1px solid @input_border@; border-radius: 7px;
    selection-background-color: @selection@; selection-color: white; padding: 6px 9px;
}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus { border-color: @focus@; }
QComboBox::drop-down { border: 0; width: 24px; }
QComboBox QAbstractItemView { color: @text@; background: @card@; selection-background-color: @selection@; }
QCheckBox { spacing: 8px; color: @checkbox_text@; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid @view_border@; border-radius: 4px; background: @input@; }
QCheckBox::indicator:checked { background: @primary@; border-color: @eyebrow@; }
QScrollArea { border: 0; background: transparent; }
QScrollArea#ImagePreviewScroll { background: @image_surface@; border: 1px solid @card_border@; border-radius: 8px; }
QScrollArea#ImagePreviewScroll > QWidget > QWidget { background: @image_surface@; }
QScrollBar:vertical { background: @scrollbar@; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: @scrollbar_handle@; border-radius: 5px; min-height: 28px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QHeaderView::section {
    background: @table_header@; border: 0; border-bottom: 1px solid @input_border@;
    color: @table_header_text@; padding: 8px; font-weight: 600;
}
QTableWidget { gridline-color: @card_border@; }
QProgressBar {
    background: @progress@; border: 1px solid @progress_border@; border-radius: 5px;
    min-height: 9px; max-height: 9px; text-align: center;
}
QProgressBar::chunk { background: @progress_chunk@; border-radius: 4px; }
QMenuBar { background: @topbar@; color: @text@; }
QMenuBar::item:selected, QMenu::item:selected { background: @menu_selected@; }
QMenu { color: @text@; background: @card@; border: 1px solid @card_border@; }
QStatusBar { background: @sidebar@; color: @muted@; border-top: 1px solid @sidebar_border@; }
QToolTip { background: @tooltip@; color: white; border: 1px solid @tooltip_border@; padding: 5px; }
"""


def style_for_theme(theme: object) -> str:
    palette = THEME_PALETTES[normalize_theme(theme)]
    style = _STYLE_TEMPLATE
    for key, value in palette.items():
        style = style.replace(f"@{key}@", value)
    return style


# Backwards-compatible default for callers that have not opted into switching.
APP_STYLE = style_for_theme(DEFAULT_THEME)


STATE_COLORS = {
    "pass": ("#173a2a", "#62d79b", "PASS"),
    "warning": ("#3c3018", "#f0c35e", "CHECK"),
    "fail": ("#401e2a", "#ff7e9e", "BLOCKED"),
    "info": ("#173153", "#72a8ff", "INFO"),
}
