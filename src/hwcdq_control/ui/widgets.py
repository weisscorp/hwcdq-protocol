"""Small reusable widgets for exact readings and textual status."""

from __future__ import annotations

import math
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .theme import mono_font


class StatusBadge(QLabel):
    """A compact status which always carries symbol and text, never color only."""

    SYMBOLS = {
        "neutral": "○",
        "ok": "✓",
        "attention": "!",
        "danger": "×",
    }

    def __init__(self, title: str, *, object_name: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._title = title
        self.setObjectName(object_name)
        self.setProperty("role", "status")
        self.set_status("—", "neutral")

    def set_status(self, text: str, severity: str = "neutral") -> None:
        severity = severity if severity in self.SYMBOLS else "neutral"
        self.setText(f"{self.SYMBOLS[severity]} {self._title}: {text}")
        self.setProperty("severity", severity)
        self.style().unpolish(self)
        self.style().polish(self)


class ReadingWidget(QWidget):
    """An exact engineering value with a stable decimal and unit."""

    def __init__(
        self,
        label: str,
        unit: str,
        *,
        decimals: int = 2,
        object_name: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._decimals = decimals
        self._unit = unit
        self.setObjectName(object_name)

        title = QLabel(label)
        title.setProperty("muted", True)
        title.setObjectName(f"{object_name}Label")

        self.value_label = QLabel("—")
        self.value_label.setObjectName(f"{object_name}Value")
        self.value_label.setProperty("role", "reading")
        self.value_label.setFont(mono_font(21))
        self.value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.value_label.setMinimumWidth(105)

        unit_label = QLabel(unit)
        unit_label.setObjectName(f"{object_name}Unit")
        unit_label.setProperty("role", "readingUnit")
        unit_label.setMinimumWidth(28)

        value_row = QHBoxLayout()
        value_row.setContentsMargins(0, 0, 0, 0)
        value_row.setSpacing(6)
        value_row.addWidget(self.value_label, 1)
        value_row.addWidget(unit_label)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 7, 8, 7)
        layout.setSpacing(2)
        layout.addWidget(title)
        layout.addLayout(value_row)

    def set_value(self, value: Any) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            self.value_label.setText("—")
            self.value_label.setToolTip("Значение отсутствует")
            return
        numeric = float(value)
        if not math.isfinite(numeric):
            self.value_label.setText("—")
            self.value_label.setToolTip("Некорректное числовое значение")
            return
        self.value_label.setText(f"{numeric:.{self._decimals}f}")
        self.value_label.setToolTip(f"Точное значение: {numeric!r} {self._unit}")


__all__ = ["ReadingWidget", "StatusBadge"]
