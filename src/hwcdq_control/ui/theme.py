"""Qt-compatible visual tokens for the calibrated bench-meter interface."""

from __future__ import annotations

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication


COLORS = {
    "primary": "#00726B",
    "primary_deep": "#00544E",
    "background": "#FFFFFF",
    "surface": "#F0F5F4",
    "surface_strong": "#E1E8E7",
    "ink": "#101A1C",
    "muted": "#516164",
    "border": "#C4CCCE",
    "attention": "#C15800",
    "danger": "#BE241F",
}


def interface_font(size: int = 13, *, weight: QFont.Weight = QFont.Normal) -> QFont:
    """Return a native UI font with a predictable fallback."""

    font = QApplication.font()
    font.setPointSize(size)
    font.setWeight(weight)
    return font


def mono_font(size: int = 12) -> QFont:
    """Return the platform fixed-width font for UUIDs and protocol bytes."""

    font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
    font.setPointSize(size)
    font.setStyleHint(QFont.Monospace)
    return font


def stylesheet() -> str:
    """Application-local stylesheet; no palette or global state is mutated."""

    return f"""
        QMainWindow, QWidget {{
            background: {COLORS['background']};
            color: {COLORS['ink']};
        }}
        QWidget {{
            font-size: 13px;
        }}
        QFrame#instrumentHeader {{
            background: {COLORS['surface']};
            border-bottom: 1px solid {COLORS['border']};
        }}
        QFrame#instrumentHeader QLabel#productTitle,
        QFrame#instrumentHeader QLabel#productSubtitle {{
            background: transparent;
        }}
        QLabel#productTitle {{
            color: {COLORS['ink']};
            font-size: 18px;
            font-weight: 650;
        }}
        QLabel#productSubtitle, QLabel[muted="true"] {{
            color: {COLORS['muted']};
        }}
        QLabel[role="status"] {{
            background: {COLORS['surface_strong']};
            border: 1px solid {COLORS['border']};
            border-radius: 4px;
            padding: 5px 8px;
            font-weight: 600;
        }}
        QLabel[severity="ok"] {{
            color: {COLORS['primary_deep']};
            border-color: {COLORS['primary']};
            background: #E4F4F1;
        }}
        QLabel[severity="attention"] {{
            color: #713500;
            border-color: {COLORS['attention']};
            background: #FFF1E4;
        }}
        QLabel[severity="danger"] {{
            color: #7D1714;
            border-color: {COLORS['danger']};
            background: #FCE9E7;
        }}
        QLabel[severity="neutral"] {{
            color: {COLORS['muted']};
        }}
        QGroupBox {{
            border: 1px solid {COLORS['border']};
            border-radius: 5px;
            margin-top: 12px;
            padding-top: 13px;
            font-weight: 650;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 5px;
            color: {COLORS['ink']};
        }}
        QPushButton, QToolButton {{
            min-height: 30px;
            padding: 2px 12px;
            border: 1px solid {COLORS['border']};
            border-radius: 4px;
            background: {COLORS['background']};
            color: {COLORS['ink']};
        }}
        QPushButton#outputControlButton {{
            min-height: 32px;
            max-height: 32px;
        }}
        QPushButton:hover, QToolButton:hover {{
            background: {COLORS['surface']};
            border-color: #879497;
        }}
        QPushButton:pressed, QToolButton:pressed {{
            background: {COLORS['surface_strong']};
        }}
        QPushButton:focus, QToolButton:focus, QComboBox:focus,
        QDoubleSpinBox:focus, QTreeWidget:focus, QTableWidget:focus {{
            border: 2px solid {COLORS['primary']};
        }}
        QPushButton:disabled, QToolButton:disabled {{
            color: #879194;
            background: #F5F7F7;
            border-color: #D8DDDE;
        }}
        QPushButton[variant="primary"] {{
            background: {COLORS['primary']};
            color: white;
            border-color: {COLORS['primary_deep']};
            font-weight: 650;
        }}
        QPushButton[variant="primary"]:hover {{
            background: {COLORS['primary_deep']};
        }}
        QPushButton[variant="danger"] {{
            background: {COLORS['danger']};
            color: white;
            border-color: #8D1714;
            font-weight: 750;
            padding-left: 18px;
            padding-right: 18px;
        }}
        QPushButton[variant="attention"] {{
            color: #713500;
            border-color: {COLORS['attention']};
            background: #FFF5EC;
            font-weight: 650;
        }}
        QPushButton[variant="primary"]:disabled,
        QPushButton[variant="attention"]:disabled,
        QPushButton[variant="danger"]:disabled {{
            color: #879194;
            background: #F5F7F7;
            border-color: #D8DDDE;
        }}
        QComboBox, QDoubleSpinBox {{
            min-height: 30px;
            padding: 1px 8px;
            border: 1px solid {COLORS['border']};
            border-radius: 4px;
            background: white;
        }}
        QTabWidget::pane {{
            border: 1px solid {COLORS['border']};
            background: white;
        }}
        QTabBar::tab {{
            background: {COLORS['surface']};
            border: 1px solid {COLORS['border']};
            border-bottom: none;
            padding: 9px 16px;
            margin-right: 2px;
        }}
        QTabBar::tab:selected {{
            background: white;
            color: {COLORS['primary_deep']};
            border-top: 3px solid {COLORS['primary']};
            padding-top: 7px;
            font-weight: 650;
        }}
        QTreeWidget, QTableWidget, QPlainTextEdit {{
            alternate-background-color: {COLORS['surface']};
            background: white;
            border: 1px solid {COLORS['border']};
            gridline-color: {COLORS['surface_strong']};
            selection-background-color: #CDE8E4;
            selection-color: {COLORS['ink']};
        }}
        QHeaderView::section {{
            background: {COLORS['surface']};
            color: {COLORS['ink']};
            padding: 7px;
            border: none;
            border-right: 1px solid {COLORS['border']};
            border-bottom: 1px solid {COLORS['border']};
            font-weight: 650;
        }}
        QLabel[role="reading"] {{
            color: {COLORS['ink']};
            font-size: 25px;
            font-weight: 650;
        }}
        QLabel[role="readingUnit"] {{
            color: {COLORS['muted']};
            font-size: 13px;
        }}
        QLabel#interlockReason {{
            color: #713500;
            background: #FFF5EC;
            border: 1px solid {COLORS['attention']};
            border-radius: 4px;
            padding: 7px 9px;
        }}
        QLabel#modeBanner {{
            background: #E4F4F1;
            color: {COLORS['primary_deep']};
            border: 1px solid #A5CBC6;
            padding: 7px 10px;
            font-weight: 650;
        }}
        QLabel#modeBanner[simulation="true"] {{
            background: #FFF1E4;
            color: #713500;
            border-color: #D89154;
        }}
    """


__all__ = ["COLORS", "interface_font", "mono_font", "stylesheet"]
