"""Dark / light theme management.

A single stylesheet template (with ``string.Template`` tokens) plus a
matching :class:`QPalette` is applied to the whole application, so native
dialogs (file pickers) follow the theme too. The token names are shared
between both palettes so the QSS never needs to know which theme is active.
"""

from __future__ import annotations

import string

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

#: Colors per theme. Keys are the ``$token`` names used in the QSS below.
THEME_COLORS: dict[str, dict[str, str]] = {
    "dark": {
        "bg": "#191a1f",           # window background
        "surface": "#23242b",      # panels, inputs, list, log
        "surface_alt": "#2a2c34",  # hover / pressed variants
        "border": "#34363f",
        "text": "#e8e9ed",
        "text_dim": "#9aa0ab",
        "accent": "#4a90e2",
        "accent_hover": "#5d9de8",
        "accent_pressed": "#3d7fc9",
        "accent_text": "#ffffff",
        "success": "#4fc07a",
        "warning": "#e0a64c",
        "error": "#ef6b6b",
        "info": "#aab2c0",
        "log_bg": "#101114",
    },
    "light": {
        "bg": "#f2f3f7",
        "surface": "#ffffff",
        "surface_alt": "#e9ebf1",
        "border": "#d5d8e0",
        "text": "#1d2026",
        "text_dim": "#6b7280",
        "accent": "#3b6fe0",
        "accent_hover": "#2f5fd0",
        "accent_pressed": "#2a55b8",
        "accent_text": "#ffffff",
        "success": "#1c8a4d",
        "warning": "#b45309",
        "error": "#d92d20",
        "info": "#5b6472",
        "log_bg": "#fbfbfc",
    },
}

#: Application-wide stylesheet. ``$token`` placeholders are substituted with
#: the active theme's colors. Braces are plain CSS so ``string.Template``
#: (which only substitutes ``$name``) is a safe formatter here.
_QSS_TEMPLATE = string.Template(
    """
QWidget {
    font-family: "Segoe UI", "SF Pro Text", "Ubuntu", "Noto Sans", "DejaVu Sans", sans-serif;
    font-size: 13px;
    color: $text;
}
QMainWindow, QDialog { background: $bg; }
QScrollArea { background: transparent; border: none; }
QWidget#scrollContent { background: $bg; }
QToolTip {
    background: $surface_alt;
    color: $text;
    border: 1px solid $border;
    border-radius: 4px;
    padding: 4px 8px;
}

/* --- Buttons ------------------------------------------------------- */
QPushButton {
    background: $surface;
    border: 1px solid $border;
    border-radius: 7px;
    padding: 7px 16px;
    color: $text;
}
QPushButton:hover { background: $surface_alt; }
QPushButton:pressed { background: $border; }
QPushButton:disabled { color: $text_dim; border-color: $border; background: $surface; }
QPushButton#primaryButton {
    background: $accent;
    color: $accent_text;
    border: 1px solid $accent;
    font-weight: 600;
}
QPushButton#primaryButton:hover { background: $accent_hover; border-color: $accent_hover; }
QPushButton#primaryButton:pressed { background: $accent_pressed; }
QPushButton#primaryButton:disabled {
    background: $surface_alt;
    color: $text_dim;
    border-color: $border;
}

/* --- Inputs -------------------------------------------------------- */
QComboBox, QLineEdit {
    background: $surface;
    border: 1px solid $border;
    border-radius: 7px;
    padding: 6px 10px;
    selection-background-color: $accent;
}
QComboBox:hover, QLineEdit:hover { border-color: $accent; }
QComboBox::drop-down { border: none; width: 26px; }
QComboBox::down-arrow {
    image: none;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-top: 6px solid $text_dim;
    margin-right: 8px;
}
QComboBox QAbstractItemView {
    background: $surface;
    border: 1px solid $border;
    selection-background-color: $accent;
    selection-color: $accent_text;
    outline: none;
}
QLineEdit:read-only { color: $text_dim; background: $surface; }

/* --- Slider -------------------------------------------------------- */
QSlider::groove:horizontal {
    height: 6px;
    border-radius: 3px;
    background: $border;
}
QSlider::sub-page:horizontal {
    background: $accent;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    width: 18px;
    height: 18px;
    margin: -6px 0;
    border-radius: 9px;
    background: $accent_text;
    border: 2px solid $accent;
}
QSlider::handle:horizontal:hover { background: #ffffff; }

/* --- Checkbox ------------------------------------------------------ */
QCheckBox { spacing: 8px; }
QCheckBox::indicator {
    width: 17px;
    height: 17px;
    border-radius: 5px;
    border: 1px solid $border;
    background: $surface;
}
QCheckBox::indicator:hover { border-color: $accent; }
QCheckBox::indicator:checked {
    background: $accent;
    border-color: $accent;
    image: none;
}

/* --- Group box ----------------------------------------------------- */
QGroupBox {
    border: 1px solid $border;
    border-radius: 9px;
    margin-top: 14px;
    padding: 12px 10px 10px 10px;
    background: transparent;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: $text_dim;
    font-weight: 600;
}

/* --- List / log ---------------------------------------------------- */
QListWidget, QPlainTextEdit {
    background: $surface;
    border: 1px solid $border;
    border-radius: 8px;
    padding: 4px;
}
QListWidget::item { padding: 4px 6px; border-radius: 5px; }
QListWidget::item:selected { background: $accent; color: $accent_text; }
QListWidget::item:hover:!selected { background: $surface_alt; }
QPlainTextEdit {
    font-family: "JetBrains Mono", "Consolas", "Menlo", "DejaVu Sans Mono", monospace;
    font-size: 12px;
    background: $log_bg;
    color: $text;
}

/* --- Progress bar -------------------------------------------------- */
QProgressBar {
    border: 1px solid $border;
    border-radius: 5px;
    background: $surface;
    text-align: center;
    color: $text;
    height: 14px;
    font-size: 10px;
}
QProgressBar::chunk {
    background: $accent;
    border-radius: 4px;
}

/* --- Splitter / scrollbars ----------------------------------------- */
QSplitter::handle { background: transparent; width: 8px; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical {
    background: $border; border-radius: 4px; min-height: 30px;
}
QScrollBar::handle:vertical:hover { background: $text_dim; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal {
    background: $border; border-radius: 4px; min-width: 30px;
}
QScrollBar::handle:horizontal:hover { background: $text_dim; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
"""
)


def _palette(dark: bool) -> QPalette:
    """Build a QPalette matching the active stylesheet colors."""
    c = THEME_COLORS["dark" if dark else "light"]
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(c["bg"]))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(c["text"]))
    palette.setColor(QPalette.ColorRole.Base, QColor(c["surface"]))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(c["surface_alt"]))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(c["surface_alt"]))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(c["text"]))
    palette.setColor(QPalette.ColorRole.Text, QColor(c["text"]))
    palette.setColor(QPalette.ColorRole.Button, QColor(c["surface"]))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(c["text"]))
    palette.setColor(QPalette.ColorRole.BrightText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.Link, QColor(c["accent"]))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(c["accent"]))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(c["accent_text"]))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(c["text_dim"]))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(c["text_dim"]))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(c["text_dim"]))
    return palette


def apply_theme(app: QApplication, dark: bool) -> None:
    """Apply (or re-apply) the chosen theme to the whole application."""
    colors = THEME_COLORS["dark" if dark else "light"]
    app.setStyle("Fusion")
    app.setPalette(_palette(dark))
    app.setStyleSheet(_QSS_TEMPLATE.substitute(colors))


def log_color(level: str, dark: bool) -> str:
    """Return the console text color for a log level in the active theme."""
    key = {
        "success": "success",
        "warning": "warning",
        "error": "error",
    }.get(level, "info")
    return THEME_COLORS["dark" if dark else "light"][key]
